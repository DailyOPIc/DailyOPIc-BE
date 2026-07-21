from __future__ import annotations

import json
import logging
import asyncio
import random
import re
from dataclasses import dataclass
from typing import Annotated, Any

from openai import AsyncOpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    create_model,
    field_validator,
)

from app.models.api import (
    AudioMetrics,
    BackgroundProfile,
    BackgroundSurvey,
    ConfidenceBand,
    DifficultyAdjustment,
    EvaluationScores,
    ExamSection,
    GeneratedQuestion,
    MockEvaluation,
    OPIcLevel,
    PerQuestionFeedback,
    PracticeEvaluation,
    QuestionStyle,
    RubricAssessment,
    RubricBand,
    RubricDimension,
)
from app.services.questions import (
    FallbackQuestionGenerator,
    QuestionPatternRepository,
    prompt_hash,
    question_set_hash,
    validate_daily_pool,
    validate_mock_blueprint,
    validate_practice_blueprint,
)
from app.services.difficulty import adjusted_level, expected_target_level
from app.services.difficulty import initial_level_from_target

PROMPT_VERSION = "opic-rubric-band-2026-07-21-v2"
QUESTION_SCHEMA_VERSION = "question-content-slots-v2"
SCORE_SCALE_VERSION = "rubric-band-v1"
DISCLAIMER = "이 결과는 학습용 AI 예상치이며 실제 OPIc 공식 등급과 다를 수 있습니다."
LEVELS = list(OPIcLevel)
RUBRIC_SCORE_BY_BAND = {
    RubricBand.FOUNDATION: 20,
    RubricBand.DEVELOPING: 40,
    RubricBand.FUNCTIONAL: 60,
    RubricBand.STRONG: 80,
    RubricBand.ADVANCED: 95,
}
logger = logging.getLogger(__name__)

BriefKorean = Annotated[str, Field(min_length=1, max_length=140)]
ShortKorean = Annotated[str, Field(min_length=1, max_length=260)]


class AIServiceError(RuntimeError):
    pass


class AIServiceConfigurationError(AIServiceError):
    pass


class AIServiceUnavailable(AIServiceError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class AIQuestionGenerationError(AIServiceError):
    pass


class GeneratedQuestionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[GeneratedQuestion]


class GeneratedQuestionContent(BaseModel):
    """Creative fields the model is allowed to author for a blueprint slot."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=8, max_length=700)
    follow_up_prompt: str | None = Field(
        default=None,
        alias="followUpPrompt",
        max_length=500,
    )
    rubric_focus: list[str] = Field(alias="rubricFocus", min_length=1, max_length=6)


@dataclass(slots=True)
class AIUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(slots=True)
class StructuredAIResult:
    payload: BaseModel
    response_id: str | None
    usage: AIUsage


@dataclass(slots=True)
class QuestionGenerationResult:
    questions: list[GeneratedQuestion]
    fallback_used: bool
    provider: str
    openai_response_id: str | None = None
    usage: AIUsage | None = None
    fallback_reason: str | None = None
    fallback_question_numbers: tuple[int, ...] = ()
    retry_count: int = 0
    prompt_version: str = PROMPT_VERSION
    schema_version: str = QUESTION_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class QuestionValidationIssue:
    question_number: int
    field: str
    expected: str | None
    actual: str | None
    category: str

    def log_value(self) -> dict[str, object]:
        return {
            "questionNumber": self.question_number,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "category": self.category,
        }


class AIPracticeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    predicted_level: OPIcLevel = Field(alias="predictedLevel")
    confidence: ConfidenceBand
    rubrics: list[RubricAssessment]
    strengths: list[BriefKorean] = Field(min_length=1, max_length=3)
    improvements: list[BriefKorean] = Field(min_length=1, max_length=3)
    corrected_answer: str | None = Field(
        default=None, alias="correctedAnswer", max_length=900
    )
    target_gap: ShortKorean | None = Field(default=None, alias="targetGap")
    sample_answer: str | None = Field(
        default=None, alias="sampleAnswer", max_length=900
    )

    @field_validator("rubrics")
    @classmethod
    def validate_rubrics(
        cls, value: list[RubricAssessment]
    ) -> list[RubricAssessment]:
        if [item.dimension for item in value] != list(RubricDimension):
            raise ValueError("rubrics must contain all five dimensions in order")
        return value


class AIMockResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    predicted_level: OPIcLevel = Field(alias="predictedLevel")
    confidence: ConfidenceBand
    rubrics: list[RubricAssessment]
    strengths: list[BriefKorean] = Field(min_length=1, max_length=4)
    improvements: list[BriefKorean] = Field(min_length=1, max_length=4)
    target_gap: ShortKorean = Field(alias="targetGap")
    overall_feedback: str = Field(alias="overallFeedback", min_length=1, max_length=450)

    @field_validator("rubrics")
    @classmethod
    def validate_rubrics(
        cls, value: list[RubricAssessment]
    ) -> list[RubricAssessment]:
        if [item.dimension for item in value] != list(RubricDimension):
            raise ValueError("rubrics must contain all five dimensions in order")
        return value


def openai_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert Pydantic JSON schema to OpenAI strict structured-output schema."""

    def convert(value: Any) -> Any:
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value

        result: dict[str, Any] = {}
        unsupported_keywords = {
            "default",
            "title",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minLength",
            "maxLength",
            "pattern",
            "format",
            "minItems",
            "maxItems",
            "uniqueItems",
            "minProperties",
            "maxProperties",
        }
        for key, item in value.items():
            if key in unsupported_keywords:
                continue
            result[key] = convert(item)

        properties = result.get("properties")
        if isinstance(properties, dict):
            result["additionalProperties"] = False
            result["required"] = list(properties.keys())
        return result

    converted = convert(schema)
    assert isinstance(converted, dict)
    return converted


class AIService:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        mock: bool,
        repository: QuestionPatternRepository,
    ) -> None:
        self.model = model
        self._mock = mock
        if not mock and not api_key:
            raise AIServiceConfigurationError(
                "OPENAI_API_KEY is required when MOCK_AI is false"
            )
        self._client = AsyncOpenAI(api_key=api_key) if not mock and api_key else None
        self._repository = repository
        self._fallback = FallbackQuestionGenerator(repository)

    @staticmethod
    def _validation_error_messages(error: ValidationError) -> list[str]:
        messages: list[str] = []
        for item in error.errors(include_url=False):
            location = ".".join(str(part) for part in item.get("loc", ()))
            messages.append(
                f"{location or '__root__'}: {item.get('msg', 'validation error')}"
            )
        return messages[:8]

    async def _structured(
        self,
        *,
        instructions: str,
        input_text: str,
        schema: type[BaseModel],
        max_attempts: int = 1,
    ) -> StructuredAIResult:
        if not self._client:
            raise AIServiceConfigurationError("OpenAI client is not configured")
        validation_errors: list[str] = []
        try:
            for attempt in range(1, max_attempts + 1):
                attempt_instructions = instructions
                if validation_errors:
                    attempt_instructions = (
                        f"{instructions}\n\n"
                        "The previous structured output failed backend validation. "
                        "Return a complete replacement JSON object only, and fix these errors: "
                        f"{'; '.join(validation_errors)}"
                    )
                response = await self._client.responses.create(
                    model=self.model,
                    store=False,
                    reasoning={"effort": "low"},
                    instructions=attempt_instructions,
                    input=input_text,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": schema.__name__,
                            "strict": True,
                            "schema": openai_strict_json_schema(schema.model_json_schema()),
                        }
                    },
                )
                usage = self._log_usage(response, schema.__name__)
                if not response.output_text:
                    refusal = self._response_refusal(response)
                    if refusal:
                        raise AIServiceUnavailable("AI service refused question generation")
                    raise AIServiceUnavailable("OpenAI returned no structured output")
                try:
                    return StructuredAIResult(
                        payload=schema.model_validate_json(response.output_text),
                        response_id=getattr(response, "id", None),
                        usage=usage,
                    )
                except ValidationError as error:
                    if attempt >= max_attempts:
                        raise
                    validation_errors = self._validation_error_messages(error)
                    logger.warning(
                        "OpenAI structured output failed validation; retrying. "
                        "model=%s schema=%s attempt=%s errors=%s",
                        self.model,
                        schema.__name__,
                        attempt,
                        validation_errors,
                    )
            raise ValueError("OpenAI structured request exhausted attempts")
        except AIServiceError:
            raise
        except Exception as error:
            logger.exception(
                "OpenAI structured request failed. model=%s schema=%s",
                self.model,
                schema.__name__,
            )
            status_code = getattr(error, "status_code", None)
            if status_code == 401:
                raise AIServiceConfigurationError(
                    "OpenAI authentication failed"
                ) from error
            retry_after: float | None = None
            response = getattr(error, "response", None)
            headers = getattr(response, "headers", None)
            if headers:
                try:
                    retry_after = float(headers.get("retry-after"))
                except (TypeError, ValueError):
                    retry_after = None
            raise AIServiceUnavailable(
                "AI service is temporarily unavailable",
                status_code=status_code,
                retry_after=retry_after,
            ) from error

    @staticmethod
    def _response_refusal(response: Any) -> str | None:
        for output in getattr(response, "output", ()) or ():
            for content in getattr(output, "content", ()) or ():
                refusal = getattr(content, "refusal", None)
                if refusal:
                    return str(refusal)
        return None

    def _log_usage(self, response: Any, schema_name: str) -> AIUsage:
        usage = getattr(response, "usage", None)
        if usage is None:
            return AIUsage()
        input_tokens = self._usage_metric(usage, "input_tokens")
        output_tokens = self._usage_metric(usage, "output_tokens")
        total_tokens = self._usage_metric(usage, "total_tokens")
        cached_tokens = self._usage_metric(
            usage, "input_tokens_details", "cached_tokens"
        )
        reasoning_tokens = self._usage_metric(
            usage, "output_tokens_details", "reasoning_tokens"
        )
        response_id = getattr(response, "id", None)
        logger.info(
            "OpenAI usage recorded. model=%s schema=%s openaiResponseId=%s inputTokens=%s "
            "cachedInputTokens=%s outputTokens=%s reasoningTokens=%s totalTokens=%s",
            self.model,
            schema_name,
            response_id,
            input_tokens,
            cached_tokens,
            output_tokens,
            reasoning_tokens,
            total_tokens,
        )
        return AIUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _usage_metric(value: Any, *path: str) -> int | None:
        current = value
        for key in path:
            if hasattr(current, "model_dump"):
                current = current.model_dump()
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = getattr(current, key, None)
            if current is None:
                return None
        return int(current) if isinstance(current, (int, float)) else None

    async def generate_practice(
        self,
        initial_level: int | OPIcLevel,
        background: BackgroundProfile,
        *,
        stage: str = "front",
        adjustment: DifficultyAdjustment | None = None,
        effective_level: int | None = None,
        history: dict[str, list[str]] | None = None,
    ) -> QuestionGenerationResult:
        if isinstance(initial_level, OPIcLevel):
            initial_level = initial_level_from_target(initial_level) or 4
        level = (
            effective_level if stage == "tail" and effective_level else initial_level
        )
        base_questions = (
            self._fallback.practice_tail(effective_level=level, background=background)
            if stage == "tail"
            else self._fallback.practice_front(initial_level, background)
        )
        if self._mock:
            return QuestionGenerationResult(
                questions=base_questions,
                fallback_used=True,
                provider="catalog",
            )

        payload = self._question_generation_payload(
            mode="practice",
            stage=stage,
            initial_level=initial_level,
            effective_level=level,
            adjustment=adjustment,
            background=background,
            blueprint=base_questions,
            history=history,
        )
        return await self._generate_questions_with_openai(
            mode="practice",
            stage=stage,
            simulation_level=level,
            blueprint=base_questions,
            payload=payload,
            history=history,
        )

    async def generate_daily_pool(
        self,
        initial_level: int | OPIcLevel,
        background: BackgroundProfile,
        survey: BackgroundSurvey | None = None,
        *,
        adjustment: DifficultyAdjustment | None = None,
        history: dict[str, list[str]] | None = None,
    ) -> QuestionGenerationResult:
        if isinstance(initial_level, OPIcLevel):
            initial_level = initial_level_from_target(initial_level) or 4
        level = adjusted_level(initial_level, adjustment)
        base_questions = self._fallback.daily_pool(
            initial_level,
            background,
            survey=survey,
            adjustment=adjustment,
        )
        if self._mock:
            return QuestionGenerationResult(
                questions=base_questions,
                fallback_used=True,
                provider="catalog",
            )

        payload = self._question_generation_payload(
            mode="daily",
            stage="pool",
            initial_level=initial_level,
            effective_level=level,
            adjustment=adjustment,
            background=background,
            blueprint=base_questions,
            history=history,
            survey=survey,
        )
        return await self._generate_questions_with_openai(
            mode="daily",
            stage="pool",
            simulation_level=level,
            blueprint=base_questions,
            payload=payload,
            history=history,
        )

    async def generate_mock(
        self,
        initial_level: int | OPIcLevel,
        background: BackgroundProfile,
        survey: BackgroundSurvey | None = None,
        *,
        stage: str = "front",
        adjustment: DifficultyAdjustment | None = None,
        effective_level: int | None = None,
        history: dict[str, list[str]] | None = None,
    ) -> QuestionGenerationResult:
        if isinstance(initial_level, OPIcLevel):
            initial_level = initial_level_from_target(initial_level) or 4
        level = (
            effective_level if stage == "tail" and effective_level else initial_level
        )
        base_questions = (
            self._fallback.mock_tail(
                effective_level=level,
                background=background,
                survey=survey,
            )
            if stage == "tail"
            else self._fallback.mock_front(initial_level, background, survey=survey)
        )
        if self._mock:
            return QuestionGenerationResult(
                questions=self._with_fixed_intro(base_questions),
                fallback_used=True,
                provider="catalog",
            )
        fixed_intro: GeneratedQuestion | None = None
        generation_blueprint = base_questions
        if stage == "front":
            fixed_intro = self._with_fixed_intro([base_questions[0]])[0]
            generation_blueprint = base_questions[1:]

        payload = self._question_generation_payload(
            mode="mock",
            stage=stage,
            initial_level=initial_level,
            effective_level=level,
            adjustment=adjustment,
            background=background,
            blueprint=generation_blueprint,
            history=history,
            survey=survey,
        )
        result = await self._generate_questions_with_openai(
            mode="mock",
            stage=stage,
            simulation_level=level,
            blueprint=generation_blueprint,
            payload=payload,
            history=history,
        )
        questions = (
            [fixed_intro, *result.questions] if fixed_intro is not None else result.questions
        )
        return QuestionGenerationResult(
            questions=questions,
            fallback_used=result.fallback_used,
            provider=result.provider,
            openai_response_id=result.openai_response_id,
            usage=result.usage,
            fallback_reason=result.fallback_reason,
            fallback_question_numbers=result.fallback_question_numbers,
            retry_count=result.retry_count,
            prompt_version=result.prompt_version,
            schema_version=result.schema_version,
        )

    def _question_generation_payload(
        self,
        *,
        mode: str,
        stage: str,
        initial_level: int,
        effective_level: int,
        adjustment: DifficultyAdjustment | None,
        background: BackgroundProfile,
        blueprint: list[GeneratedQuestion],
        history: dict[str, list[str]] | None,
        survey: BackgroundSurvey | None = None,
    ) -> dict[str, Any]:
        return {
            "mode": mode,
            "stage": stage,
            "initialLevel": initial_level,
            "adjustment": adjustment.value if adjustment else None,
            "effectiveLevel": effective_level,
            "expectedTargetLevel": expected_target_level(effective_level).value,
            "background": background.model_dump(mode="json"),
            "backgroundSurvey": survey.model_dump(mode="json") if survey else None,
            "blueprint": [self._blueprint_item(item) for item in blueprint],
            "examPlan": self._exam_plan(mode=mode, stage=stage),
            "forbidden": {
                "setHashes": (history or {}).get("setHashes", []),
                "topicIds": (history or {}).get("topicIds", []),
                "promptHashes": (history or {}).get("promptHashes", []),
                "promptTexts": (history or {}).get("promptTexts", [])[-30:],
            },
            "constraints": [
                "Generate entirely new OPIc interviewer-style simulation questions.",
                "Do not copy official OPIc wording or the catalog examples.",
                "Use fresh lowercase snake_case topicId values not listed in forbidden.topicIds.",
                "Make prompt and followUpPrompt specific, natural, and different from prior sets.",
                "Preserve combo flow and make the difficulty visibly match effectiveLevel.",
                "Do not add fields outside the JSON schema.",
            ],
        }

    @staticmethod
    def _blueprint_item(question: GeneratedQuestion) -> dict[str, Any]:
        return {
            "number": question.number,
            "examSection": question.exam_section.value,
            "comboId": question.combo_id,
            "questionStyle": (
                question.question_style.value if question.question_style else None
            ),
            "difficulty": question.difficulty.value,
            "estimatedLevel": (
                question.estimated_level.value
                if question.estimated_level
                else question.difficulty.value
            ),
            "category": question.category,
            "rubricFocus": question.rubric_focus,
        }

    @staticmethod
    def _with_fixed_intro(questions: list[GeneratedQuestion]) -> list[GeneratedQuestion]:
        if not questions or questions[0].number != 1:
            return questions
        return [
            questions[0].model_copy(
                update={
                    "prompt": "Introduce yourself.",
                    "follow_up_prompt": None,
                    "topic": "self introduction",
                    "topic_id": "self_introduction",
                }
            ),
            *questions[1:],
        ]

    @staticmethod
    def _exam_plan(*, mode: str, stage: str) -> list[str]:
        if mode == "daily":
            return [
                "Q2-Q15 randomized Daily practice prompts only",
                "No Q1, no introduction, and no self-introduction",
                "Copy each blueprint examSection and questionStyle exactly; comboId remains null",
            ]
        if mode == "mock" and stage == "front":
            return [
                "Q1 is fixed server-side as exactly 'Introduce yourself.' and must not be returned",
                "Return exactly Q2-Q7 only",
                "Q2-Q4 topic A combo",
                "Q5-Q7 topic B combo",
            ]
        if mode == "mock":
            return [
                "Return exactly Q8-Q15 only",
                "Q8-Q10 topic C combo",
                "Q11-Q12 roleplay",
                "Q13-Q15 unexpected, comparison, advanced",
            ]
        if mode == "practice" and stage == "front":
            return [
                "Q1 self introduction",
                "Q2-Q4 topic A combo: description -> routine/reason -> experience",
                "Q5-Q7 topic B combo: description -> routine/reason -> experience",
            ]
        if mode == "practice":
            return ["Q8-Q10 adjusted unexpected or experience questions"]
        return []

    async def _generate_questions_with_openai(
        self,
        *,
        mode: str,
        stage: str,
        simulation_level: int,
        blueprint: list[GeneratedQuestion],
        payload: dict[str, Any],
        history: dict[str, list[str]] | None,
    ) -> QuestionGenerationResult:
        max_attempts = 3
        by_number = {question.number: question for question in blueprint}
        generated_by_number: dict[int, GeneratedQuestion] = {}
        pending_numbers = set(by_number)
        last_issues: list[QuestionValidationIssue] = []
        last_error: Exception | None = None
        response_id: str | None = None
        usage: AIUsage | None = None

        for attempt in range(1, max_attempts + 1):
            requested_blueprint = [
                question
                for question in blueprint
                if question.number in pending_numbers
            ]
            schema = self._question_content_schema(requested_blueprint)
            attempt_payload = {
                **payload,
                "attempt": attempt,
                "schemaVersion": QUESTION_SCHEMA_VERSION,
                "blueprint": [
                    self._blueprint_item(item) for item in requested_blueprint
                ],
                "fixedQuestions": [
                    {
                        "number": number,
                        "promptHash": prompt_hash(question.prompt),
                    }
                    for number, question in sorted(generated_by_number.items())
                    if number not in pending_numbers
                ],
            }
            if last_issues:
                attempt_payload["previousValidationErrors"] = [
                    issue.log_value() for issue in last_issues
                ]
                attempt_payload["retryInstructions"] = [
                    "Return only the requested failed slots.",
                    "Keep fixed questions unchanged and avoid their prompt meanings.",
                    "Correct every listed validation issue.",
                ]

            try:
                result = await self._structured(
                    instructions=self._question_generation_instructions(mode, stage),
                    input_text=json.dumps(attempt_payload, ensure_ascii=False),
                    schema=schema,
                )
                response_id = result.response_id
                usage = self._merge_usage(usage, result.usage)
                generated_by_number.update(
                    self._merge_question_content(
                        requested_blueprint,
                        result.payload,
                    )
                )
                questions = [generated_by_number[item.number] for item in blueprint]
                questions = self._normalize_server_topic_ids(questions, history)
                generated_by_number = {item.number: item for item in questions}
                last_issues = self._collect_question_validation_issues(
                    mode=mode,
                    stage=stage,
                    simulation_level=simulation_level,
                    blueprint=blueprint,
                    questions=questions,
                    history=history,
                )
                if not last_issues:
                    self._validate_generated_questions(
                        mode=mode,
                        stage=stage,
                        simulation_level=simulation_level,
                        blueprint=blueprint,
                        questions=questions,
                        history=history,
                    )
                    return QuestionGenerationResult(
                        questions=questions,
                        fallback_used=False,
                        provider="openai",
                        openai_response_id=response_id,
                        usage=usage,
                        retry_count=attempt - 1,
                    )
                pending_numbers = {
                    issue.question_number for issue in last_issues
                }
                logger.warning(
                    "AI question content failed validation; retrying failed slots. "
                    "mode=%s stage=%s model=%s attempt=%s issues=%s",
                    mode,
                    stage,
                    self.model,
                    attempt,
                    [item.log_value() for item in last_issues],
                )
            except AIServiceConfigurationError:
                raise
            except Exception as error:
                last_error = error
                pending_numbers = set(by_number)
                last_issues = [
                    QuestionValidationIssue(
                        question_number=item.number,
                        field="provider",
                        expected="valid structured content",
                        actual=type(error).__name__,
                        category="provider_error",
                    )
                    for item in blueprint
                ]
                logger.exception(
                    "AI question generation attempt failed. "
                    "mode=%s stage=%s model=%s attempt=%s",
                    mode,
                    stage,
                    self.model,
                    attempt,
                )
                if attempt < max_attempts:
                    retry_after = getattr(error, "retry_after", None)
                    delay = (
                        min(8.0, max(0.0, float(retry_after)))
                        if retry_after is not None
                        else min(4.0, (2 ** (attempt - 1)) + random.uniform(0.0, 0.35))
                    )
                    await asyncio.sleep(delay)

        fallback_numbers = tuple(sorted(pending_numbers or by_number.keys()))
        for number in fallback_numbers:
            generated_by_number[number] = by_number[number]
        questions = [generated_by_number.get(item.number, item) for item in blueprint]
        questions = self._normalize_server_topic_ids(questions, history)
        try:
            # Catalog content is trusted for structure and level. Recent-content history is
            # intentionally excluded so a provider outage cannot turn fallback into a 503.
            self._validate_generated_questions(
                mode=mode,
                stage=stage,
                simulation_level=simulation_level,
                blueprint=blueprint,
                questions=questions,
                history=None,
            )
        except Exception as error:
            raise AIQuestionGenerationError(
                f"validated catalog fallback failed for mode={mode} stage={stage}"
            ) from error

        fallback_reason = (
            "provider_error"
            if last_error is not None
            else "validation_exhausted"
        )
        logger.error(
            "AI question generation used validated catalog fallback. "
            "mode=%s stage=%s model=%s reason=%s questions=%s retryCount=%s",
            mode,
            stage,
            self.model,
            fallback_reason,
            fallback_numbers,
            max_attempts - 1,
        )
        return QuestionGenerationResult(
            questions=questions,
            fallback_used=True,
            provider=(
                "mixed"
                if generated_by_number and len(fallback_numbers) < len(blueprint)
                else "catalog"
            ),
            openai_response_id=response_id,
            usage=usage,
            fallback_reason=fallback_reason,
            fallback_question_numbers=fallback_numbers,
            retry_count=max_attempts - 1,
        )

    @staticmethod
    def _question_content_schema(
        blueprint: list[GeneratedQuestion],
    ) -> type[BaseModel]:
        numbers = "_".join(str(item.number) for item in blueprint)
        fields = {
            f"q{item.number:02d}": (GeneratedQuestionContent, ...)
            for item in blueprint
        }
        return create_model(
            f"QuestionContents_{numbers}",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )

    @staticmethod
    def _merge_question_content(
        blueprint: list[GeneratedQuestion], payload: BaseModel
    ) -> dict[int, GeneratedQuestion]:
        merged: dict[int, GeneratedQuestion] = {}
        for expected in blueprint:
            content = getattr(payload, f"q{expected.number:02d}")
            assert isinstance(content, GeneratedQuestionContent)
            rubric_focus: list[str] = []
            for raw_value in content.rubric_focus:
                value = raw_value.strip()
                if value and value not in rubric_focus:
                    rubric_focus.append(value)
            follow_up = (
                content.follow_up_prompt.strip()
                if content.follow_up_prompt and content.follow_up_prompt.strip()
                else None
            )
            merged[expected.number] = expected.model_copy(
                update={
                    "prompt": content.prompt.strip(),
                    "follow_up_prompt": follow_up,
                    "rubric_focus": rubric_focus or expected.rubric_focus,
                }
            )
        return merged

    @staticmethod
    def _merge_usage(current: AIUsage | None, new: AIUsage) -> AIUsage:
        if current is None:
            return new

        def total(left: int | None, right: int | None) -> int | None:
            if left is None and right is None:
                return None
            return (left or 0) + (right or 0)

        return AIUsage(
            input_tokens=total(current.input_tokens, new.input_tokens),
            cached_input_tokens=total(
                current.cached_input_tokens, new.cached_input_tokens
            ),
            output_tokens=total(current.output_tokens, new.output_tokens),
            reasoning_tokens=total(
                current.reasoning_tokens, new.reasoning_tokens
            ),
            total_tokens=total(current.total_tokens, new.total_tokens),
        )

    def _normalize_server_topic_ids(
        self,
        questions: list[GeneratedQuestion],
        history: dict[str, list[str]] | None,
    ) -> list[GeneratedQuestion]:
        blocked = set((history or {}).get("topicIds", []))
        used: set[str] = set()
        group_ids: dict[str, str] = {}
        normalized: list[GeneratedQuestion] = []

        for question in questions:
            group_key = question.combo_id or f"q{question.number}"
            if group_key in group_ids:
                topic_id = group_ids[group_key]
            else:
                base = self._snake_case_topic_id(
                    question.topic_id or question.topic or "opic_topic"
                )
                topic_id = base
                if topic_id in blocked or topic_id in used:
                    suffix = f"_{prompt_hash(question.prompt)[:8]}"
                    topic_id = f"{base[: max(1, 80 - len(suffix))]}{suffix}"
                    counter = 2
                    while topic_id in blocked or topic_id in used:
                        counter_suffix = f"{suffix}_{counter}"
                        topic_id = (
                            f"{base[: max(1, 80 - len(counter_suffix))]}"
                            f"{counter_suffix}"
                        )
                        counter += 1
                group_ids[group_key] = topic_id
                used.add(topic_id)
            normalized.append(
                question.model_copy(update={"topic_id": topic_id})
                if question.topic_id != topic_id
                else question
            )
        return normalized

    def _collect_question_validation_issues(
        self,
        *,
        mode: str,
        stage: str,
        simulation_level: int,
        blueprint: list[GeneratedQuestion],
        questions: list[GeneratedQuestion],
        history: dict[str, list[str]] | None,
    ) -> list[QuestionValidationIssue]:
        issues: list[QuestionValidationIssue] = []
        actual_by_number = {item.number: item for item in questions}
        metadata_fields = (
            "exam_section",
            "combo_id",
            "question_style",
            "difficulty",
            "estimated_level",
            "category",
        )

        for expected in blueprint:
            actual = actual_by_number.get(expected.number)
            if actual is None:
                issues.append(
                    QuestionValidationIssue(
                        expected.number,
                        "slot",
                        "present",
                        "missing",
                        "schema",
                    )
                )
                continue
            for field in metadata_fields:
                expected_value = getattr(expected, field)
                actual_value = getattr(actual, field)
                if actual_value != expected_value:
                    issues.append(
                        QuestionValidationIssue(
                            expected.number,
                            field,
                            str(expected_value),
                            str(actual_value),
                            "blueprint",
                        )
                    )
            if not actual.topic_id:
                issues.append(
                    QuestionValidationIssue(
                        expected.number,
                        "topicId",
                        "non-empty",
                        None,
                        "content",
                    )
                )
            if len(actual.topic) > 80 or actual.topic.endswith("?") or len(actual.topic.split()) > 8:
                issues.append(
                    QuestionValidationIssue(
                        expected.number,
                        "topic",
                        "short label",
                        actual.topic[:100],
                        "content",
                    )
                )
            try:
                self._validate_level_rules(simulation_level, [actual])
            except ValueError as error:
                issues.append(
                    QuestionValidationIssue(
                        expected.number,
                        "prompt",
                        f"level {simulation_level} rules",
                        str(error),
                        "difficulty",
                    )
                )

        recent_topic_ids = set((history or {}).get("topicIds", []))
        recent_prompt_hashes = set((history or {}).get("promptHashes", []))
        seen_prompt_hashes: dict[str, int] = {}
        for question in questions:
            if question.topic_id in recent_topic_ids:
                issues.append(
                    QuestionValidationIssue(
                        question.number,
                        "topicId",
                        "not recently used",
                        question.topic_id,
                        "uniqueness",
                    )
                )
            current_prompt_hash = prompt_hash(question.prompt)
            if current_prompt_hash in recent_prompt_hashes:
                issues.append(
                    QuestionValidationIssue(
                        question.number,
                        "prompt",
                        "not recently used",
                        current_prompt_hash,
                        "uniqueness",
                    )
                )
            previous_number = seen_prompt_hashes.get(current_prompt_hash)
            if previous_number is not None:
                for number in (previous_number, question.number):
                    issues.append(
                        QuestionValidationIssue(
                            number,
                            "prompt",
                            "unique within set",
                            current_prompt_hash,
                            "uniqueness",
                        )
                    )
            seen_prompt_hashes[current_prompt_hash] = question.number

        serialized = [
            item.model_dump(by_alias=True, mode="json") for item in questions
        ]
        if question_set_hash(serialized) in set((history or {}).get("setHashes", [])):
            issues.extend(
                QuestionValidationIssue(
                    item.number,
                    "setHash",
                    "not recently used",
                    question_set_hash(serialized),
                    "uniqueness",
                )
                for item in questions
            )

        # Preserve the established whole-set structural validators. If an invariant
        # is not attributable to one slot, repair every slot rather than guessing.
        if not issues:
            try:
                self._validate_generated_questions(
                    mode=mode,
                    stage=stage,
                    simulation_level=simulation_level,
                    blueprint=blueprint,
                    questions=questions,
                    history=history,
                )
            except ValueError as error:
                issues.extend(
                    QuestionValidationIssue(
                        item.number,
                        "questionSet",
                        "valid",
                        str(error),
                        "set_validation",
                    )
                    for item in questions
                )

        unique: dict[tuple[int, str, str], QuestionValidationIssue] = {}
        for issue in issues:
            unique[(issue.question_number, issue.field, issue.category)] = issue
        return list(unique.values())

    def _normalize_daily_topic_ids(
        self,
        questions: list[GeneratedQuestion],
        history: dict[str, list[str]] | None,
    ) -> list[GeneratedQuestion]:
        recent_topic_ids = set((history or {}).get("topicIds", []))
        used_topic_ids: set[str] = set()
        normalized: list[GeneratedQuestion] = []
        rewrites: list[str] = []

        for question in questions:
            original = (question.topic_id or "").strip()
            topic_id = self._daily_unique_topic_id(
                question=question,
                original=original,
                blocked={*recent_topic_ids, *used_topic_ids},
            )
            used_topic_ids.add(topic_id)
            if topic_id != original:
                rewrites.append(
                    f"Q{question.number}: {original or '<empty>'}->{topic_id}"
                )
                normalized.append(question.model_copy(update={"topic_id": topic_id}))
            else:
                normalized.append(question)

        if rewrites:
            logger.warning(
                "AI daily question topicIds were normalized to avoid recent collisions. "
                "model=%s rewrites=%s",
                self.model,
                rewrites[:20],
            )
        return normalized

    @classmethod
    def _daily_unique_topic_id(
        cls,
        *,
        question: GeneratedQuestion,
        original: str,
        blocked: set[str],
    ) -> str:
        base = cls._snake_case_topic_id(original or question.topic or "daily_topic")
        if original == base and base not in blocked:
            return base

        suffix = f"_q{question.number}_{prompt_hash(question.prompt)[:8]}"
        trimmed_base = base[: max(1, 80 - len(suffix))].rstrip("_")
        candidate = f"{trimmed_base}{suffix}"
        counter = 2
        while candidate in blocked:
            counter_suffix = f"{suffix}_{counter}"
            trimmed_base = base[: max(1, 80 - len(counter_suffix))].rstrip("_")
            candidate = f"{trimmed_base}{counter_suffix}"
            counter += 1
        return candidate

    @staticmethod
    def _snake_case_topic_id(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        return slug or "daily_topic"

    def _normalize_daily_metadata_to_blueprint(
        self,
        blueprint: list[GeneratedQuestion],
        questions: list[GeneratedQuestion],
    ) -> list[GeneratedQuestion]:
        if len(blueprint) != len(questions):
            return questions
        if any(
            actual.number != expected.number
            for expected, actual in zip(blueprint, questions)
        ):
            return questions
        if any(
            actual.exam_section is ExamSection.INTRODUCTION
            and expected.exam_section is not ExamSection.INTRODUCTION
            for expected, actual in zip(blueprint, questions)
        ):
            return questions

        normalized: list[GeneratedQuestion] = []
        mismatches: list[str] = []
        metadata_fields = [
            "exam_section",
            "combo_id",
            "question_style",
            "difficulty",
            "estimated_level",
            "category",
        ]

        for expected, actual in zip(blueprint, questions):
            update: dict[str, Any] = {}
            for field in metadata_fields:
                expected_value = getattr(expected, field)
                actual_value = getattr(actual, field)
                if actual_value != expected_value:
                    update[field] = expected_value
                    mismatches.append(
                        f"Q{expected.number}.{field}: "
                        f"{actual_value!s}->{expected_value!s}"
                    )
            normalized.append(actual.model_copy(update=update) if update else actual)

        if mismatches:
            logger.warning(
                "AI daily question metadata differed from blueprint and was normalized. "
                "model=%s mismatches=%s",
                self.model,
                mismatches[:20],
            )
        return normalized

    @staticmethod
    def _question_generation_instructions(mode: str, stage: str) -> str:
        base = (
            "You create OPIc-style speaking test questions. "
            "Output must strictly match the provided JSON schema. "
            "Each qNN property corresponds to the blueprint question with that number. "
            "The server owns every blueprint metadata field, so never return number, examSection, comboId, topic, topicId, "
            "questionStyle, difficulty, estimatedLevel, or category. "
            "Only author prompt, followUpPrompt, and rubricFocus for each requested slot. "
            "Do not copy official OPIc wording or catalog examples. "
            "Do not reuse forbidden prompt meanings or any fixedQuestions. "
            "\n\n"
            "Sentence count rules by effectiveLevel: "
            "Level 1: prompt must be exactly 1 sentence. "
            "Level 2: prompt must be 1 or 2 sentences. "
            "Level 3: prompt must be exactly 2 sentences. "
            "Level 4: prompt must be 2 or 3 sentences. "
            "Level 5: prompt must be at least 3 sentences. "
            "Level 6: prompt must be 3 or 4 sentences. "
            "\n\n"
            "Difficulty rules: "
            "Level 1 questions must be very short, concrete, and descriptive. "
            "Level 2 may include simple reasons. "
            "Level 3 may include simple past experiences. "
            "Level 4 may include comparison or change. "
            "Level 5 may include experience, comparison, roleplay, and problem solving. "
            "Level 6 may include abstract opinions, social impact, advantages and disadvantages, and hypothetical situations. "
        )

        if mode == "practice":
            return (
                base
                + "Create a brand-new Daily OPIc practice stage. "
                + "Keep the flow close to an actual OPIc interview: self introduction, topic combo, topic combo, then adjusted or unexpected questions."
            )

        if mode == "daily":
            return (
                base
                + "Create a brand-new randomized Daily OPIc question pool. "
                + "Generate only questions numbered Q2 through Q15. "
                + "Never include self-introduction, 'Introduce yourself', warm-up, or hint-like follow-up content. "
                + "The questions are independent practice prompts, not a mock exam combo sequence. "
                + "Daily independence does not change the metadata: keep examSection exactly as the blueprint says, "
                + "including roleplay, comparison, and advanced. "
                + "Use varied survey-based and unexpected topics, and make each prompt clearly different from forbidden.promptTexts."
            )

        if stage == "front":
            return (
                base
                + "Create only the generated front section of a brand-new OPIc mock exam. "
                + "Return exactly 6 questions: Q2 through Q7. "
                + "Do not include Q1; the server supplies Q1 as exactly 'Introduce yourself.'. "
                + "Q2-Q4 must be one survey-based combo and Q5-Q7 must be another survey-based combo."
            )

        return (
            base
            + "Create only the generated tail section of a brand-new OPIc mock exam. "
            + "Return exactly 8 questions: Q8 through Q15. "
            + "Q8-Q10 must be the final survey-based combo, Q11-Q12 roleplay, and Q13-Q15 unexpected/comparison/advanced prompts."
        )

    def _validate_generated_questions(
        self,
        *,
        mode: str,
        stage: str,
        simulation_level: int,
        blueprint: list[GeneratedQuestion],
        questions: list[GeneratedQuestion],
        history: dict[str, list[str]] | None,
    ) -> None:
        self._validate_against_blueprint(blueprint, questions)
        if mode == "daily":
            validate_daily_pool(questions)
        elif mode == "practice":
            validate_practice_blueprint(questions)
        elif len(questions) == 15:
            validate_mock_blueprint(questions)
        self._validate_level_rules(simulation_level, questions)
        self._validate_question_uniqueness(
            mode=mode, questions=questions, history=history
        )

        for question in questions:
            if len(question.topic) > 80:
                raise ValueError("topic too long")

            if question.topic.endswith("?"):
                raise ValueError("topic must be label, not question")

            if len(question.topic.split()) > 8:
                raise ValueError("topic must be short label")

    @staticmethod
    def _validate_against_blueprint(
        blueprint: list[GeneratedQuestion], questions: list[GeneratedQuestion]
    ) -> None:
        if len(blueprint) != len(questions):
            raise ValueError(
                "generated question count does not match blueprint: "
                f"expected={len(blueprint)} actual={len(questions)}"
            )

        for expected, actual in zip(blueprint, questions):
            if actual.number != expected.number:
                raise ValueError(
                    "generated question number does not match blueprint: "
                    f"question={expected.number} expected={expected.number} actual={actual.number}"
                )
            if actual.exam_section != expected.exam_section:
                raise ValueError(
                    "generated question examSection does not match blueprint: "
                    f"question={expected.number} expected={expected.exam_section.value} "
                    f"actual={actual.exam_section.value}"
                )
            if actual.combo_id != expected.combo_id:
                raise ValueError(
                    "generated question comboId does not match blueprint: "
                    f"question={expected.number} expected={expected.combo_id} actual={actual.combo_id}"
                )
            if actual.question_style != expected.question_style:
                raise ValueError(
                    "generated question questionStyle does not match blueprint: "
                    f"question={expected.number} expected={expected.question_style} "
                    f"actual={actual.question_style}"
                )
            if actual.difficulty != expected.difficulty:
                raise ValueError(
                    "generated question difficulty does not match blueprint: "
                    f"question={expected.number} expected={expected.difficulty.value} "
                    f"actual={actual.difficulty.value}"
                )
            if actual.estimated_level != expected.estimated_level:
                raise ValueError(
                    "generated question estimatedLevel does not match blueprint: "
                    f"question={expected.number} expected={expected.estimated_level} "
                    f"actual={actual.estimated_level}"
                )
            if actual.category != expected.category:
                raise ValueError(
                    "generated question category does not match blueprint: "
                    f"question={expected.number} expected={expected.category} actual={actual.category}"
                )
            if not actual.topic_id:
                raise ValueError(
                    "generated question topicId is required: "
                    f"question={expected.number}"
                )

    @classmethod
    def _validate_level_rules(
        cls, simulation_level: int, questions: list[GeneratedQuestion]
    ) -> None:
        forbidden: set[QuestionStyle] = set()

        if simulation_level <= 1:
            forbidden = {
                QuestionStyle.COMPARISON,
                QuestionStyle.PROBLEM_SOLVING,
                QuestionStyle.OPINION,
                QuestionStyle.ROLEPLAY,
            }
        elif simulation_level == 2:
            forbidden = {
                QuestionStyle.COMPARISON,
                QuestionStyle.PROBLEM_SOLVING,
                QuestionStyle.OPINION,
                QuestionStyle.ROLEPLAY,
            }
        elif simulation_level == 3:
            forbidden = {
                QuestionStyle.PROBLEM_SOLVING,
                QuestionStyle.OPINION,
            }

        for item in questions:
            if item.question_style in forbidden:
                raise ValueError("generated question type is too difficult for level")

            if item.exam_section is ExamSection.INTRODUCTION:
                continue

            sentence_count = cls._sentence_count(item.prompt)

            if simulation_level <= 1 and sentence_count > 2:
                raise ValueError("level 1 prompts must be one or two sentences")
            if simulation_level == 2 and not 1 <= sentence_count <= 2:
                raise ValueError("level 2 prompts must be one or two sentences")
            if simulation_level == 3 and not 1 <= sentence_count <= 3:
                raise ValueError("level 3 prompts must be one to three sentences")
            if simulation_level == 4 and not 2 <= sentence_count <= 3:
                raise ValueError("level 4 prompts must be two or three sentences")
            if simulation_level == 5 and sentence_count < 2:
                raise ValueError("level 5 prompts must be at least two sentences")
            if simulation_level >= 6 and not 2 <= sentence_count <= 4:
                raise ValueError("level 6 prompts must be two to four sentences")

    @staticmethod
    def _sentence_count(prompt: str) -> int:
        return len([item for item in re.split(r"[.!?]+", prompt) if item.strip()])

    @staticmethod
    def _validate_question_uniqueness(
        *,
        mode: str,
        questions: list[GeneratedQuestion],
        history: dict[str, list[str]] | None,
    ) -> None:
        serialized = [item.model_dump(by_alias=True, mode="json") for item in questions]
        set_hash = question_set_hash(serialized)
        prompt_hashes = [prompt_hash(item.prompt) for item in questions]
        topic_ids = [item.topic_id for item in questions if item.topic_id]
        recent_set_hashes = set((history or {}).get("setHashes", []))
        recent_topic_ids = set((history or {}).get("topicIds", []))
        recent_prompt_hashes = set((history or {}).get("promptHashes", []))
        if set_hash in recent_set_hashes:
            raise ValueError("generated question set repeats a recent setHash")
        if set(topic_ids).intersection(recent_topic_ids):
            raise ValueError("generated question set repeats recent topicIds")
        if set(prompt_hashes).intersection(recent_prompt_hashes):
            raise ValueError("generated question set repeats recent promptHashes")
        if len(prompt_hashes) != len(set(prompt_hashes)):
            raise ValueError("generated question set contains duplicate prompts")

    @staticmethod
    def _fallback_score(
        transcript: str, metrics: AudioMetrics
    ) -> tuple[OPIcLevel, EvaluationScores]:
        words = re.findall(r"\b[A-Za-z']+\b", transcript.lower())
        sentences = [item for item in re.split(r"[.!?\n]+", transcript) if item.strip()]
        word_count = len(words)
        average_sentence = word_count / max(1, len(sentences))
        filler_count = sum(
            transcript.lower().count(filler)
            for filler in [" um ", " uh ", " you know ", " i mean "]
        )
        repetition = 0.0
        if words:
            repetition = max(words.count(word) for word in set(words)) / len(words)
        base = (
            min(1.0, word_count / 220) * 0.48 + min(1.0, average_sentence / 18) * 0.32
        )
        delivery = max(0.0, 1 - metrics.silence_ratio) * 0.20
        total = max(
            0.0,
            min(
                1.0,
                base + delivery - filler_count * 0.01 - max(0, repetition - 0.2) * 0.2,
            ),
        )
        thresholds = [
            (0.78, OPIcLevel.AL),
            (0.70, OPIcLevel.IH),
            (0.64, OPIcLevel.IM3),
            (0.57, OPIcLevel.IM2),
            (0.49, OPIcLevel.IM1),
            (0.40, OPIcLevel.IL),
            (0.31, OPIcLevel.NH),
            (0.22, OPIcLevel.NM),
        ]
        level = next(
            (level for minimum, level in thresholds if total >= minimum), OPIcLevel.NL
        )
        scores = EvaluationScores(
            taskFulfillment=round(min(100, 25 + word_count * 0.5)),
            grammar=round(min(100, 30 + average_sentence * 3)),
            vocabulary=round(min(100, 25 + len(set(words)) * 0.6)),
            discourse=round(min(100, 25 + len(sentences) * 8)),
            fluency=round(min(100, max(10, 100 - metrics.silence_ratio * 70))),
        )
        return level, scores

    @staticmethod
    def _scores_from_rubrics(
        rubrics: list[RubricAssessment],
    ) -> EvaluationScores:
        values = {
            item.dimension: RUBRIC_SCORE_BY_BAND[item.band] for item in rubrics
        }
        return EvaluationScores(
            taskFulfillment=values[RubricDimension.TASK_FULFILLMENT],
            grammar=values[RubricDimension.GRAMMAR],
            vocabulary=values[RubricDimension.VOCABULARY],
            discourse=values[RubricDimension.DISCOURSE],
            fluency=values[RubricDimension.FLUENCY],
        )

    @staticmethod
    def _rubrics_from_scores(scores: EvaluationScores) -> list[RubricAssessment]:
        values = {
            RubricDimension.TASK_FULFILLMENT: scores.task_fulfillment,
            RubricDimension.GRAMMAR: scores.grammar,
            RubricDimension.VOCABULARY: scores.vocabulary,
            RubricDimension.DISCOURSE: scores.discourse,
            RubricDimension.FLUENCY: scores.fluency,
        }

        def band(score: int) -> RubricBand:
            if score >= 90:
                return RubricBand.ADVANCED
            if score >= 75:
                return RubricBand.STRONG
            if score >= 55:
                return RubricBand.FUNCTIONAL
            if score >= 35:
                return RubricBand.DEVELOPING
            return RubricBand.FOUNDATION

        return [
            RubricAssessment(
                dimension=dimension,
                band=band(score),
                evidence="답변 길이와 전달 지표를 기준으로 한 개발용 평가입니다.",
                nextAction="구체적인 이유와 예시를 연결해 답변을 확장하세요.",
            )
            for dimension, score in values.items()
        ]

    async def evaluate_practice(
        self,
        *,
        question: GeneratedQuestion,
        transcript: str,
        target: OPIcLevel,
        metrics: AudioMetrics,
    ) -> PracticeEvaluation:
        if self._mock:
            level, scores = self._fallback_score(transcript, metrics)
            result = AIPracticeResult(
                predictedLevel=level,
                confidence=ConfidenceBand.MEDIUM,
                rubrics=self._rubrics_from_scores(scores),
                strengths=["질문에 맞춰 영어로 답변을 완성했습니다."],
                improvements=["구체적인 이유와 예시를 한두 문장 더 연결해 보세요."],
                correctedAnswer=transcript.strip()[:900],
                targetGap=f"현재 예상 {level.value}에서 목표 {target.value}에 맞는 세부 묘사를 보강하세요.",
                sampleAnswer=(
                    f"For this {question.topic} question, I would begin with a clear answer, "
                    "add a specific personal example, and finish by explaining why it matters to me."
                ),
            )
        else:
            payload = {
                "question": question.model_dump(by_alias=True, mode="json"),
                "transcript": transcript,
                "deliveryMetrics": metrics.model_dump(by_alias=True, mode="json"),
                "targetLevelForFeedbackOnly": target.value,
            }
            structured = await self._structured(
                instructions=(
                    "Evaluate this OPIc-style practice answer using anchored, evidence-based bands. "
                    "Grade independently of targetLevel; use targetLevel only for targetGap and sampleAnswer. "
                    "Return rubrics exactly in this order: taskFulfillment, grammar, vocabulary, discourse, fluency. "
                    "Use foundation when meaning is mostly unavailable or the task is not addressed; developing when short, "
                    "frequent errors or fragments limit detail; functional when the main task is understandable with connected support; "
                    "strong when detail, control, and organization are consistently effective; advanced only for sustained, precise, "
                    "well-organized language. Cite transcript or delivery evidence briefly in Korean and give one next action per rubric. "
                    "Use delivery metrics only for fluency, never claim phoneme-level pronunciation analysis. "
                    "Return at most three concise strengths and improvements. correctedAnswer and sampleAnswer must be English "
                    "and no longer than five sentences; use null only when a useful optional detail cannot be produced."
                ),
                input_text=json.dumps(payload, ensure_ascii=False),
                schema=AIPracticeResult,
                max_attempts=2,
            )
            result = structured.payload
        assert isinstance(result, AIPracticeResult)
        warnings = [
            name
            for name, value in {
                "correctedAnswer": result.corrected_answer,
                "targetGap": result.target_gap,
                "sampleAnswer": result.sample_answer,
            }.items()
            if not value
        ]
        return PracticeEvaluation(
            **result.model_dump(by_alias=True),
            scores=self._scores_from_rubrics(result.rubrics),
            audioMetrics=metrics,
            disclaimer=DISCLAIMER,
            modelVersion=self.model,
            promptVersion=PROMPT_VERSION,
            resultStatus="partial" if warnings else "complete",
            warnings=warnings,
            scoreScaleVersion=SCORE_SCALE_VERSION,
        )

    async def evaluate_mock(
        self,
        *,
        questions: list[GeneratedQuestion],
        transcripts: list[str],
        target: OPIcLevel,
        metrics: list[AudioMetrics],
    ) -> MockEvaluation:
        if self._mock:
            combined = " ".join(transcripts)
            aggregate = AudioMetrics(
                durationSeconds=sum(item.duration_seconds for item in metrics),
                speakingSeconds=sum(item.speaking_seconds for item in metrics),
                silenceRatio=(
                    sum(item.silence_ratio for item in metrics) / len(metrics)
                ),
                wordsPerMinute=(
                    sum(item.words_per_minute for item in metrics) / len(metrics)
                ),
            )
            level, scores = self._fallback_score(combined, aggregate)
            result = AIMockResult(
                predictedLevel=level,
                confidence=ConfidenceBand.MEDIUM,
                rubrics=self._rubrics_from_scores(scores),
                strengths=["15개 문항을 끝까지 완주해 답변 표본을 확보했습니다."],
                improvements=[
                    "답변마다 주장, 구체적 사례, 마무리의 세 단계 구조를 유지하세요."
                ],
                targetGap=f"목표 {target.value}에 도달하려면 답변 간 시제와 연결어의 일관성을 높이세요.",
                overallFeedback="전체 답변에서 반복되는 강점과 개선점을 우선순위대로 연습하세요.",
            )
        else:
            payload = {
                "answers": [
                    {
                        "question": questions[index].model_dump(
                            by_alias=True, mode="json"
                        ),
                        "transcript": transcripts[index],
                        "deliveryMetrics": metrics[index].model_dump(
                            by_alias=True, mode="json"
                        ),
                    }
                    for index in range(15)
                ],
                "targetLevelForFeedbackOnly": target.value,
            }
            structured = await self._structured(
                instructions=(
                    "Evaluate all 15 answers holistically with anchored, evidence-based bands. Determine predictedLevel before "
                    "considering targetLevel and use targetLevel only for targetGap. Return rubrics exactly in this order: "
                    "taskFulfillment, grammar, vocabulary, discourse, fluency. Use foundation when meaning is mostly unavailable "
                    "or tasks are not addressed; developing when frequent limitations prevent detail; functional when most tasks are "
                    "understandable with connected support; strong when detailed control and organization are consistent; advanced "
                    "only for sustained, precise, flexible language. Use audio metrics only for fluency and delivery, not pronunciation. "
                    "Return compact Korean strengths, improvements, targetGap, and overallFeedback. Do not return per-question "
                    "feedback or sample answers."
                ),
                input_text=json.dumps(payload, ensure_ascii=False),
                schema=AIMockResult,
                max_attempts=2,
            )
            result = structured.payload
        assert isinstance(result, AIMockResult)
        return MockEvaluation(
            **result.model_dump(by_alias=True),
            scores=self._scores_from_rubrics(result.rubrics),
            perQuestion=[],
            disclaimer=DISCLAIMER,
            modelVersion=self.model,
            promptVersion=PROMPT_VERSION,
            resultStatus="complete",
            warnings=[],
            scoreScaleVersion=SCORE_SCALE_VERSION,
        )
