from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.api import (
    AudioMetrics,
    BackgroundProfile,
    BackgroundSurvey,
    ConfidenceBand,
    DifficultyAdjustment,
    EvaluationScores,
    GeneratedQuestion,
    MockEvaluation,
    OPIcLevel,
    PerQuestionFeedback,
    PracticeEvaluation,
    SurveyQuestionType,
)
from app.services.questions import (
    FallbackQuestionGenerator,
    QuestionPatternRepository,
    prompt_hash,
    question_set_hash,
    validate_practice_blueprint,
    validate_mock_blueprint,
)
from app.services.difficulty import expected_target_level
from app.services.difficulty import initial_level_from_target

PROMPT_VERSION = "opic-rubric-2026-06-22-v1"
DISCLAIMER = "이 결과는 학습용 AI 예상치이며 실제 OPIc 공식 등급과 다를 수 있습니다."
LEVELS = list(OPIcLevel)
logger = logging.getLogger(__name__)

BriefKorean = Annotated[str, Field(min_length=1, max_length=140)]
ShortKorean = Annotated[str, Field(min_length=1, max_length=260)]


class AIServiceError(RuntimeError):
    pass


class AIServiceConfigurationError(AIServiceError):
    pass


class AIServiceUnavailable(AIServiceError):
    pass


class AIQuestionGenerationError(AIServiceError):
    pass


class GeneratedQuestionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[GeneratedQuestion]


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


class AIPracticeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    predicted_level: OPIcLevel = Field(alias="predictedLevel")
    confidence: ConfidenceBand
    scores: EvaluationScores
    strengths: list[BriefKorean] = Field(min_length=1, max_length=3)
    improvements: list[BriefKorean] = Field(min_length=1, max_length=3)
    corrected_answer: str = Field(alias="correctedAnswer", min_length=1, max_length=900)
    target_gap: ShortKorean = Field(alias="targetGap")
    sample_answer: str = Field(alias="sampleAnswer", min_length=1, max_length=900)


class AIMockResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    predicted_level: OPIcLevel = Field(alias="predictedLevel")
    confidence: ConfidenceBand
    scores: EvaluationScores
    strengths: list[BriefKorean] = Field(min_length=1, max_length=4)
    improvements: list[BriefKorean] = Field(min_length=1, max_length=4)
    target_gap: ShortKorean = Field(alias="targetGap")
    overall_feedback: str = Field(alias="overallFeedback", min_length=1, max_length=450)
    per_question: list[PerQuestionFeedback] = Field(alias="perQuestion")

    @field_validator("per_question")
    @classmethod
    def validate_all_questions(
        cls, value: list[PerQuestionFeedback]
    ) -> list[PerQuestionFeedback]:
        if [item.number for item in value] != list(range(1, 16)):
            raise ValueError("perQuestion must contain ordered numbers 1 through 15")
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

    async def _structured(
        self, *, instructions: str, input_text: str, schema: type[BaseModel]
    ) -> StructuredAIResult:
        if not self._client:
            raise AIServiceConfigurationError("OpenAI client is not configured")
        try:
            response = await self._client.responses.create(
                model=self.model,
                store=False,
                reasoning={"effort": "low"},
                instructions=instructions,
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
                raise ValueError("OpenAI returned no structured output")
            return StructuredAIResult(
                payload=schema.model_validate_json(response.output_text),
                response_id=getattr(response, "id", None),
                usage=usage,
            )
        except AIServiceError:
            raise
        except Exception as error:
            logger.exception(
                "OpenAI structured request failed. model=%s schema=%s",
                self.model,
                schema.__name__,
            )
            raise AIServiceUnavailable(
                "AI service is temporarily unavailable"
            ) from error

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
                questions=base_questions,
                fallback_used=True,
                provider="catalog",
            )

        payload = self._question_generation_payload(
            mode="mock",
            stage=stage,
            initial_level=initial_level,
            effective_level=level,
            adjustment=adjustment,
            background=background,
            blueprint=base_questions,
            history=history,
            survey=survey,
        )
        return await self._generate_questions_with_openai(
            mode="mock",
            stage=stage,
            simulation_level=level,
            blueprint=base_questions,
            payload=payload,
            history=history,
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
            "type": question.type.value,
            "comboId": question.combo_id,
            "questionType": (
                question.question_type.value if question.question_type else None
            ),
            "difficulty": question.difficulty.value,
            "estimatedLevel": (
                question.estimated_level.value
                if question.estimated_level
                else question.difficulty.value
            ),
            "rubricFocus": question.rubric_focus,
        }

    @staticmethod
    def _exam_plan(*, mode: str, stage: str) -> list[str]:
        if mode == "practice" and stage == "front":
            return [
                "Q1 self introduction",
                "Q2-Q4 topic A combo: description -> routine/reason -> experience",
                "Q5-Q7 topic B combo: description -> routine/reason -> experience",
            ]
        if mode == "practice":
            return ["Q8-Q10 adjusted unexpected or experience questions"]
        if stage == "front":
            return [
                "Q1 self introduction",
                "Q2-Q4 topic A combo",
                "Q5-Q7 topic B combo",
            ]
        return [
            "Q8-Q10 topic C combo",
            "Q11-Q12 roleplay",
            "Q13-Q15 unexpected, comparison, advanced",
        ]

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
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                result = await self._structured(
                    instructions=self._question_generation_instructions(mode),
                    input_text=json.dumps(
                        {**payload, "attempt": attempt},
                        ensure_ascii=False,
                    ),
                    schema=GeneratedQuestionsPayload,
                )
                generated = result.payload
                assert isinstance(generated, GeneratedQuestionsPayload)
                questions = generated.questions
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
                    openai_response_id=result.response_id,
                    usage=result.usage,
                )
            except Exception as error:
                last_error = error
                logger.exception(
                    "AI question generation attempt failed. mode=%s model=%s attempt=%s",
                    mode,
                    self.model,
                    attempt,
                )
        raise AIQuestionGenerationError(
            f"AI question generation failed after 2 attempts for mode={mode}"
        ) from last_error

    @staticmethod
    def _question_generation_instructions(mode: str) -> str:
        base = (
            "You create OPIc-style speaking test questions. "
            "Output must strictly match the provided JSON schema. "
            "Follow the blueprint exactly for number, type, comboId, questionType, difficulty, and estimatedLevel. "
            "Do not invent or change blueprint metadata. "
            "Only make topic, topicId, prompt, followUpPrompt, and rubricFocus fresh and natural. "
            "topic must be a short label under 40 characters, such as 'Self Introduction' or 'Coffee Shops'. "
            "topic must never be a full question sentence. "
            "topicId must be fresh lowercase snake_case and not in forbidden.topicIds. "
            "Questions in the same combo must share the same topicId. "
            "Do not copy official OPIc wording or catalog examples. "
            "Do not reuse forbidden prompts or topicIds. "
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

        return (
            base
            + "Create a brand-new 15-question OPIc mock exam stage. "
            + "Keep the flow close to an actual OPIc exam: self introduction, survey-based combos, difficulty adjustment, roleplay, and unexpected topics."
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
        if mode == "practice":
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
            raise ValueError("generated question count does not match blueprint")

        for expected, actual in zip(blueprint, questions):
            if actual.number != expected.number:
                raise ValueError("generated question number does not match blueprint")
            if actual.type != expected.type:
                raise ValueError("generated question type does not match blueprint")
            if actual.combo_id != expected.combo_id:
                raise ValueError("generated question comboId does not match blueprint")
            if actual.question_type != expected.question_type:
                raise ValueError(
                    "generated question questionType does not match blueprint"
                )
            if actual.difficulty != expected.difficulty:
                raise ValueError(
                    "generated question difficulty does not match blueprint"
                )
            if actual.estimated_level != expected.estimated_level:
                raise ValueError(
                    "generated question estimatedLevel does not match blueprint"
                )
            if not actual.topic_id:
                raise ValueError("generated question topicId is required")

    @classmethod
    def _validate_level_rules(
        cls, simulation_level: int, questions: list[GeneratedQuestion]
    ) -> None:
        forbidden: set[SurveyQuestionType] = set()

        if simulation_level <= 1:
            forbidden = {
                SurveyQuestionType.COMPARISON,
                SurveyQuestionType.PROBLEM_SOLVING,
                SurveyQuestionType.OPINION,
                SurveyQuestionType.ROLEPLAY,
            }
        elif simulation_level == 2:
            forbidden = {
                SurveyQuestionType.COMPARISON,
                SurveyQuestionType.PROBLEM_SOLVING,
                SurveyQuestionType.OPINION,
                SurveyQuestionType.ROLEPLAY,
            }
        elif simulation_level == 3:
            forbidden = {
                SurveyQuestionType.PROBLEM_SOLVING,
                SurveyQuestionType.OPINION,
            }

        for item in questions:
            if item.question_type in forbidden:
                raise ValueError("generated question type is too difficult for level")

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
                scores=scores,
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
                    "Act as a conservative OPIc practice evaluator. Grade the answer independently of the target level. "
                    "Use targetLevel only for targetGap and sampleAnswer. Do not claim phoneme-level pronunciation analysis. "
                    "Give concise actionable feedback in Korean. Return at most three short strengths and improvements. "
                    "correctedAnswer and sampleAnswer must be English and no longer than five sentences each."
                ),
                input_text=json.dumps(payload, ensure_ascii=False),
                schema=AIPracticeResult,
            )
            result = structured.payload
        assert isinstance(result, AIPracticeResult)
        return PracticeEvaluation(
            **result.model_dump(by_alias=True),
            audioMetrics=metrics,
            disclaimer=DISCLAIMER,
            modelVersion=self.model,
            promptVersion=PROMPT_VERSION,
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
                scores=scores,
                strengths=["15개 문항을 끝까지 완주해 답변 표본을 확보했습니다."],
                improvements=[
                    "답변마다 주장, 구체적 사례, 마무리의 세 단계 구조를 유지하세요."
                ],
                targetGap=f"목표 {target.value}에 도달하려면 답변 간 시제와 연결어의 일관성을 높이세요.",
                overallFeedback="전체 답변에서 반복되는 강점과 개선점을 우선순위대로 연습하세요.",
                perQuestion=[
                    PerQuestionFeedback(
                        number=index + 1,
                        feedback="핵심 답변 뒤에 이유와 구체적인 예시를 보강하세요.",
                        sampleAnswer=(
                            f"Regarding {questions[index].topic}, I can explain my main point clearly, "
                            "support it with a personal example, and describe the result in detail."
                        ),
                    )
                    for index in range(15)
                ],
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
                    "Evaluate this complete 15-answer OPIc-style mock exam conservatively and holistically. "
                    "Determine predictedLevel before considering the target level. Use audio metrics only for delivery and fluency, "
                    "not pronunciation. Return compact Korean feedback. Each perQuestion feedback must be one short sentence, "
                    "and each English sampleAnswer must be one or two sentences."
                ),
                input_text=json.dumps(payload, ensure_ascii=False),
                schema=AIMockResult,
            )
            result = structured.payload
        assert isinstance(result, AIMockResult)
        return MockEvaluation(
            **result.model_dump(by_alias=True),
            disclaimer=DISCLAIMER,
            modelVersion=self.model,
            promptVersion=PROMPT_VERSION,
        )
