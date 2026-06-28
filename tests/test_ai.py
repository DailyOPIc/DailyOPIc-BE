from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.api import (
    AudioMetrics,
    BackgroundProfile,
    GeneratedQuestion,
    OPIcLevel,
    QuestionType,
    SurveyQuestionType,
)
from app.services.ai import (
    AIQuestionGenerationError,
    AIService,
    AIServiceConfigurationError,
    GeneratedQuestionsPayload,
    openai_strict_json_schema,
)
from app.services.questions import QuestionPatternRepository, prompt_hash, question_set_hash


class FakeResponses:
    def __init__(self, outputs: list[list[GeneratedQuestion]]) -> None:
        self.outputs = outputs
        self.calls = 0

    async def create(self, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls += 1
        questions = self.outputs.pop(0)
        return SimpleNamespace(
            id=f"resp-{self.calls}",
            output_text=GeneratedQuestionSetFixture(questions).json(),
            usage=SimpleNamespace(
                input_tokens=10,
                input_tokens_details=SimpleNamespace(cached_tokens=2),
                output_tokens=20,
                output_tokens_details=SimpleNamespace(reasoning_tokens=3),
                total_tokens=30,
            ),
        )


class FakeOpenAIClient:
    def __init__(self, outputs: list[list[GeneratedQuestion]]) -> None:
        self.responses = FakeResponses(outputs)


class GeneratedQuestionSetFixture:
    def __init__(self, questions: list[GeneratedQuestion]) -> None:
        self.questions = questions

    def json(self) -> str:
        return (
            '{"questions":'
            + "["
            + ",".join(question.model_dump_json(by_alias=True) for question in self.questions)
            + "]}"
        )


def practice_questions(prefix: str) -> list[GeneratedQuestion]:
    sequence = [
        SurveyQuestionType.DESCRIPTION,
        SurveyQuestionType.PAST_EXPERIENCE,
        SurveyQuestionType.COMPARISON,
        SurveyQuestionType.ROUTINE,
        SurveyQuestionType.DESCRIPTION,
        SurveyQuestionType.PROBLEM_SOLVING,
        SurveyQuestionType.PAST_EXPERIENCE,
        SurveyQuestionType.COMPARISON,
        SurveyQuestionType.ROLEPLAY,
        SurveyQuestionType.OPINION,
    ]
    return [
        GeneratedQuestion(
            number=index + 1,
            type=QuestionType.PRACTICE,
            comboId=None,
            topic=f"{prefix} topic {index}",
            prompt=f"Describe {prefix} situation {index} with a detailed personal example.",
            difficulty=OPIcLevel.IH,
            rubricFocus=["task fulfillment", "organization"],
            questionType=question_type,
            followUpPrompt=f"What changed after that {prefix} experience {index}?",
            topicId=f"{prefix}_topic_{index}",
            category="practice",
            estimatedLevel=OPIcLevel.IH,
        )
        for index, question_type in enumerate(sequence)
    ]


def test_openai_strict_schema_requires_nullable_question_fields() -> None:
    schema = openai_strict_json_schema(GeneratedQuestionsPayload.model_json_schema())
    question_schema = schema["$defs"]["GeneratedQuestion"]
    serialized = str(schema)

    assert "default" not in serialized
    assert "title" not in serialized
    assert "maxLength" not in serialized
    assert "minimum" not in serialized
    assert "maxItems" not in serialized
    assert question_schema["additionalProperties"] is False
    assert set(question_schema["required"]) == set(question_schema["properties"])
    assert "comboId" in question_schema["required"]
    assert "followUpPrompt" in question_schema["required"]
    assert "topicId" in question_schema["required"]


@pytest.mark.asyncio
async def test_target_level_does_not_anchor_fallback_grade() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key=None, model="test-model", mock=True, repository=repository)
    question = (await service.generate_practice(OPIcLevel.IM2, BackgroundProfile())).questions[0]
    transcript = (
        "I usually read the news in the morning because I want to understand current events. "
        "For example, last week I compared several articles and discussed them with my coworkers. "
        "This habit helps me notice different opinions and make better decisions."
    )
    metrics = AudioMetrics(
        durationSeconds=35,
        speakingSeconds=31,
        silenceRatio=0.11,
        wordsPerMinute=105,
    )
    low_target = await service.evaluate_practice(
        question=question,
        transcript=transcript,
        target=OPIcLevel.NM,
        metrics=metrics,
    )
    high_target = await service.evaluate_practice(
        question=question,
        transcript=transcript,
        target=OPIcLevel.AL,
        metrics=metrics,
    )
    assert low_target.predicted_level == high_target.predicted_level


def test_real_ai_requires_api_key() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    with pytest.raises(AIServiceConfigurationError):
        AIService(api_key=None, model="test-model", mock=False, repository=repository)


@pytest.mark.asyncio
async def test_real_ai_retries_when_recent_topic_is_reused() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key="test-key", model="test-model", mock=False, repository=repository)
    duplicate = practice_questions("duplicate")
    fresh = practice_questions("fresh")
    service._client = FakeOpenAIClient([duplicate, fresh])  # type: ignore[assignment]
    history = {"setHashes": [], "topicIds": ["duplicate_topic_0"], "promptHashes": []}

    result = await service.generate_practice(OPIcLevel.IH, BackgroundProfile(), history=history)

    assert result.fallback_used is False
    assert result.provider == "openai"
    assert result.openai_response_id == "resp-2"
    assert result.usage is not None
    assert result.usage.input_tokens == 10
    assert service._client.responses.calls == 2  # type: ignore[union-attr]
    assert {item.topic_id for item in result.questions}.isdisjoint(history["topicIds"])


@pytest.mark.asyncio
async def test_real_ai_fails_after_duplicate_retry_exhaustion() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key="test-key", model="test-model", mock=False, repository=repository)
    duplicate = practice_questions("duplicate")
    service._client = FakeOpenAIClient([duplicate, duplicate])  # type: ignore[assignment]
    history = {"setHashes": [], "topicIds": ["duplicate_topic_0"], "promptHashes": []}

    with pytest.raises(AIQuestionGenerationError):
        await service.generate_practice(OPIcLevel.IH, BackgroundProfile(), history=history)


@pytest.mark.asyncio
async def test_real_ai_rejects_recent_prompt_hash() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key="test-key", model="test-model", mock=False, repository=repository)
    duplicate = practice_questions("duplicate")
    service._client = FakeOpenAIClient([duplicate, duplicate])  # type: ignore[assignment]
    history = {
        "setHashes": [],
        "topicIds": [],
        "promptHashes": [prompt_hash(duplicate[0].prompt)],
    }

    with pytest.raises(AIQuestionGenerationError):
        await service.generate_practice(OPIcLevel.IH, BackgroundProfile(), history=history)


@pytest.mark.asyncio
async def test_real_ai_rejects_recent_set_hash() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key="test-key", model="test-model", mock=False, repository=repository)
    duplicate = practice_questions("duplicate")
    service._client = FakeOpenAIClient([duplicate, duplicate])  # type: ignore[assignment]
    serialized = [item.model_dump(by_alias=True, mode="json") for item in duplicate]
    history = {
        "setHashes": [question_set_hash(serialized)],
        "topicIds": [],
        "promptHashes": [],
    }

    with pytest.raises(AIQuestionGenerationError):
        await service.generate_practice(OPIcLevel.IH, BackgroundProfile(), history=history)
