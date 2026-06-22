from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.api import (
    AudioMetrics,
    BackgroundProfile,
    ConfidenceBand,
    EvaluationScores,
    GeneratedQuestion,
    MockEvaluation,
    OPIcLevel,
    PerQuestionFeedback,
    PracticeEvaluation,
    QuestionType,
)
from app.services.questions import (
    FallbackQuestionGenerator,
    QuestionPatternRepository,
    validate_mock_blueprint,
)


PROMPT_VERSION = "opic-rubric-2026-06-22-v1"
DISCLAIMER = "이 결과는 학습용 AI 예상치이며 실제 OPIc 공식 등급과 다를 수 있습니다."
LEVELS = list(OPIcLevel)


class GeneratedQuestionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[GeneratedQuestion]


class AIPracticeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    predicted_level: OPIcLevel = Field(alias="predictedLevel")
    confidence: ConfidenceBand
    scores: EvaluationScores
    strengths: list[str] = Field(min_length=1, max_length=5)
    improvements: list[str] = Field(min_length=1, max_length=5)
    corrected_answer: str = Field(alias="correctedAnswer")
    target_gap: str = Field(alias="targetGap")
    sample_answer: str = Field(alias="sampleAnswer")


class AIMockResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    predicted_level: OPIcLevel = Field(alias="predictedLevel")
    confidence: ConfidenceBand
    scores: EvaluationScores
    strengths: list[str] = Field(min_length=1, max_length=6)
    improvements: list[str] = Field(min_length=1, max_length=6)
    target_gap: str = Field(alias="targetGap")
    overall_feedback: str = Field(alias="overallFeedback")
    per_question: list[PerQuestionFeedback] = Field(alias="perQuestion")

    @field_validator("per_question")
    @classmethod
    def validate_all_questions(
        cls, value: list[PerQuestionFeedback]
    ) -> list[PerQuestionFeedback]:
        if [item.number for item in value] != list(range(1, 16)):
            raise ValueError("perQuestion must contain ordered numbers 1 through 15")
        return value


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
        self._mock = mock or not api_key
        self._client = AsyncOpenAI(api_key=api_key) if api_key else None
        self._repository = repository
        self._fallback = FallbackQuestionGenerator(repository)

    async def _structured(
        self, *, instructions: str, input_text: str, schema: type[BaseModel]
    ) -> BaseModel:
        if not self._client:
            raise RuntimeError("OpenAI client is not configured")
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
                    "schema": schema.model_json_schema(),
                }
            },
        )
        if not response.output_text:
            raise RuntimeError("OpenAI returned no structured output")
        return schema.model_validate_json(response.output_text)

    async def generate_practice(
        self, target: OPIcLevel, background: BackgroundProfile
    ) -> tuple[list[GeneratedQuestion], bool]:
        if self._mock:
            return self._fallback.practice(target, background), True
        references = self._repository.references(
            target_level=target, background=background, limit=20
        )
        prompt = {
            "targetLevel": target.value,
            "backgroundSurvey": background.model_dump(mode="json"),
            "referencePatterns": references,
            "count": 10,
        }
        try:
            output = await self._structured(
                instructions=(
                    "Create ten original OPIc-style English speaking practice questions. "
                    "Use references only as style patterns; do not copy them verbatim. "
                    "Return numbers 1-10, type=practice, no comboId, target difficulty, and concise rubricFocus."
                ),
                input_text=json.dumps(prompt, ensure_ascii=False),
                schema=GeneratedQuestionsPayload,
            )
            questions = output.questions  # type: ignore[attr-defined]
            if [item.number for item in questions] != list(range(1, 11)):
                raise ValueError("practice question numbering is invalid")
            if any(item.type is not QuestionType.PRACTICE for item in questions):
                raise ValueError("practice question type is invalid")
            return questions, False
        except Exception:
            return self._fallback.practice(target, background), True

    async def generate_mock(
        self, target: OPIcLevel, background: BackgroundProfile
    ) -> tuple[list[GeneratedQuestion], bool]:
        if self._mock:
            return self._fallback.mock(target, background), True
        references = self._repository.references(
            target_level=target, background=background, limit=24
        )
        input_payload = {
            "targetLevel": target.value,
            "backgroundSurvey": background.model_dump(mode="json"),
            "referencePatterns": references,
            "blueprint": {
                "1": "introduction",
                "2-4": "survey comboId survey-a",
                "5-7": "survey comboId survey-b",
                "8-10": "unexpected comboId unexpected",
                "11-13": "roleplay comboId roleplay",
                "14": "comparison",
                "15": "advanced",
            },
        }
        for _ in range(2):
            try:
                output = await self._structured(
                    instructions=(
                        "Create one coherent 15-question OPIc-style mock exam in English. "
                        "Respect the exact numbered blueprint and combo IDs. Use reference questions only "
                        "as non-verbatim style anchors. Adjust response complexity to targetLevel."
                    ),
                    input_text=json.dumps(input_payload, ensure_ascii=False),
                    schema=GeneratedQuestionsPayload,
                )
                questions = output.questions  # type: ignore[attr-defined]
                validate_mock_blueprint(questions)
                return questions, False
            except Exception:
                continue
        return self._fallback.mock(target, background), True

    @staticmethod
    def _fallback_score(transcript: str, metrics: AudioMetrics) -> tuple[OPIcLevel, EvaluationScores]:
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
        base = min(1.0, word_count / 220) * 0.48 + min(1.0, average_sentence / 18) * 0.32
        delivery = max(0.0, 1 - metrics.silence_ratio) * 0.20
        total = max(0.0, min(1.0, base + delivery - filler_count * 0.01 - max(0, repetition - 0.2) * 0.2))
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
        level = next((level for minimum, level in thresholds if total >= minimum), OPIcLevel.NL)
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
                correctedAnswer=transcript.strip(),
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
            result = await self._structured(
                instructions=(
                    "Act as a conservative OPIc practice evaluator. Grade the answer independently of the target level. "
                    "Use targetLevel only for targetGap and sampleAnswer. Do not claim phoneme-level pronunciation analysis. "
                    "Give concise actionable feedback in Korean; correctedAnswer and sampleAnswer must be English."
                ),
                input_text=json.dumps(payload, ensure_ascii=False),
                schema=AIPracticeResult,
            )
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
                silenceRatio=(sum(item.silence_ratio for item in metrics) / len(metrics)),
                wordsPerMinute=(sum(item.words_per_minute for item in metrics) / len(metrics)),
            )
            level, scores = self._fallback_score(combined, aggregate)
            result = AIMockResult(
                predictedLevel=level,
                confidence=ConfidenceBand.MEDIUM,
                scores=scores,
                strengths=["15개 문항을 끝까지 완주해 답변 표본을 확보했습니다."],
                improvements=["답변마다 주장, 구체적 사례, 마무리의 세 단계 구조를 유지하세요."],
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
                        "question": questions[index].model_dump(by_alias=True, mode="json"),
                        "transcript": transcripts[index],
                        "deliveryMetrics": metrics[index].model_dump(by_alias=True, mode="json"),
                    }
                    for index in range(15)
                ],
                "targetLevelForFeedbackOnly": target.value,
            }
            result = await self._structured(
                instructions=(
                    "Evaluate this complete 15-answer OPIc-style mock exam conservatively and holistically. "
                    "Determine predictedLevel before considering the target level. Use audio metrics only for delivery and fluency, "
                    "not pronunciation. Return concise Korean feedback and one natural English target-level sample per question."
                ),
                input_text=json.dumps(payload, ensure_ascii=False),
                schema=AIMockResult,
            )
        assert isinstance(result, AIMockResult)
        return MockEvaluation(
            **result.model_dump(by_alias=True),
            disclaimer=DISCLAIMER,
            modelVersion=self.model,
            promptVersion=PROMPT_VERSION,
        )
