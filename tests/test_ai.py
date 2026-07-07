import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.api import (
    AudioMetrics,
    BackgroundProfile,
    ConfidenceBand,
    DifficultyAdjustment,
    EvaluationScores,
    GeneratedQuestion,
    OPIcLevel,
    PerQuestionFeedback,
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
from app.services.questions import FallbackQuestionGenerator


class FakeResponses:
    def __init__(self, outputs: list[list[GeneratedQuestion]]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        self.requests.append(kwargs)
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


class FakeTextResponses:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        self.requests.append(kwargs)
        return SimpleNamespace(
            id=f"resp-{self.calls}",
            output_text=self.outputs.pop(0),
            usage=SimpleNamespace(
                input_tokens=10,
                input_tokens_details=SimpleNamespace(cached_tokens=2),
                output_tokens=20,
                output_tokens_details=SimpleNamespace(reasoning_tokens=3),
                total_tokens=30,
            ),
        )


class FakeTextOpenAIClient:
    def __init__(self, outputs: list[str]) -> None:
        self.responses = FakeTextResponses(outputs)


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
        (1, QuestionType.INTRODUCTION, None, SurveyQuestionType.DESCRIPTION, f"{prefix}_intro"),
        (2, QuestionType.SURVEY, "daily-a", SurveyQuestionType.DESCRIPTION, f"{prefix}_topic_a"),
        (3, QuestionType.SURVEY, "daily-a", SurveyQuestionType.ROUTINE, f"{prefix}_topic_a"),
        (4, QuestionType.SURVEY, "daily-a", SurveyQuestionType.PAST_EXPERIENCE, f"{prefix}_topic_a"),
        (5, QuestionType.SURVEY, "daily-b", SurveyQuestionType.DESCRIPTION, f"{prefix}_topic_b"),
        (6, QuestionType.SURVEY, "daily-b", SurveyQuestionType.ROUTINE, f"{prefix}_topic_b"),
        (7, QuestionType.SURVEY, "daily-b", SurveyQuestionType.PAST_EXPERIENCE, f"{prefix}_topic_b"),
    ]
    return [
        GeneratedQuestion(
            number=number,
            type=broad_type,
            comboId=combo_id,
            topic=f"{prefix} topic {number}",
            prompt=(
                f"Describe {prefix} situation {number}. "
                f"Explain the background clearly. "
                f"Tell me why it matters to you."
            ),
            difficulty=OPIcLevel.IH,
            rubricFocus=["task fulfillment", "organization"],
            questionType=question_type,
            followUpPrompt=None,
            topicId=topic_id,
            category="survey" if broad_type is QuestionType.SURVEY else "introduction",
            estimatedLevel=OPIcLevel.IH,
        )
        for number, broad_type, combo_id, question_type, topic_id in sequence
    ]


def practice_tail_questions(prefix: str) -> list[GeneratedQuestion]:
    sequence = [
        (8, QuestionType.UNEXPECTED, None, SurveyQuestionType.PAST_EXPERIENCE, f"{prefix}_tail_a"),
        (9, QuestionType.UNEXPECTED, None, SurveyQuestionType.COMPARISON, f"{prefix}_tail_b"),
        (10, QuestionType.UNEXPECTED, None, SurveyQuestionType.OPINION, f"{prefix}_tail_c"),
    ]
    return [
        GeneratedQuestion(
            number=number,
            type=broad_type,
            comboId=combo_id,
            topic=f"{prefix} tail {number}",
            prompt=(
                f"Describe {prefix} tail situation {number}. "
                f"Explain the background clearly. "
                f"Tell me why it matters to you."
            ),
            difficulty=OPIcLevel.AL,
            rubricFocus=["task fulfillment", "organization"],
            questionType=question_type,
            followUpPrompt=None,
            topicId=topic_id,
            category="unexpected",
            estimatedLevel=OPIcLevel.AL,
        )
        for number, broad_type, combo_id, question_type, topic_id in sequence
    ]


def mock_front_generated_questions(prefix: str) -> list[GeneratedQuestion]:
    sequence = [
        (2, QuestionType.SURVEY, "survey-1", SurveyQuestionType.DESCRIPTION, f"{prefix}_topic_a"),
        (3, QuestionType.SURVEY, "survey-1", SurveyQuestionType.ROUTINE, f"{prefix}_topic_a"),
        (4, QuestionType.SURVEY, "survey-1", SurveyQuestionType.PAST_EXPERIENCE, f"{prefix}_topic_a"),
        (5, QuestionType.SURVEY, "survey-2", SurveyQuestionType.DESCRIPTION, f"{prefix}_topic_b"),
        (6, QuestionType.SURVEY, "survey-2", SurveyQuestionType.ROUTINE, f"{prefix}_topic_b"),
        (7, QuestionType.SURVEY, "survey-2", SurveyQuestionType.PAST_EXPERIENCE, f"{prefix}_topic_b"),
    ]
    return [
        GeneratedQuestion(
            number=number,
            type=broad_type,
            comboId=combo_id,
            topic=f"{prefix} mock topic {number}",
            prompt=(
                f"Describe {prefix} mock situation {number}. "
                f"Explain the background clearly. "
                f"Tell me why it matters to you."
            ),
            difficulty=OPIcLevel.AL,
            rubricFocus=["task fulfillment", "organization"],
            questionType=question_type,
            followUpPrompt=None,
            topicId=topic_id,
            category="survey",
            estimatedLevel=OPIcLevel.AL,
        )
        for number, broad_type, combo_id, question_type, topic_id in sequence
    ]


def mock_evaluation_json(per_question_count: int = 15) -> str:
    payload = {
        "predictedLevel": OPIcLevel.IM2.value,
        "confidence": ConfidenceBand.MEDIUM.value,
        "scores": EvaluationScores(
            taskFulfillment=70,
            grammar=68,
            vocabulary=72,
            discourse=66,
            fluency=69,
        ).model_dump(by_alias=True),
        "strengths": ["질문에 맞춰 답변을 이어 갔습니다."],
        "improvements": ["각 답변에 구체적인 예시를 더 연결하세요."],
        "targetGap": "목표 등급을 위해 답변 구조와 세부 묘사를 보강하세요.",
        "overallFeedback": "전체적으로 응답을 완주했으며, 문항별 핵심 근거를 더 구체화하면 좋습니다.",
        "perQuestion": [
            PerQuestionFeedback(
                number=number,
                feedback="핵심 답변 뒤에 이유와 예시를 보강하세요.",
                sampleAnswer="I would give a clear answer and add one specific personal example.",
            ).model_dump(by_alias=True)
            for number in range(1, per_question_count + 1)
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


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
async def test_mock_front_keeps_q1_fixed_and_generates_only_q2_to_q7() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key="test-key", model="test-model", mock=False, repository=repository)
    generated = mock_front_generated_questions("fresh")
    service._client = FakeOpenAIClient([generated])  # type: ignore[assignment]

    result = await service.generate_mock(6, BackgroundProfile(), stage="front")

    assert [item.number for item in result.questions] == list(range(1, 8))
    assert result.questions[0].prompt == "Introduce yourself."
    request = service._client.responses.requests[0]  # type: ignore[union-attr]
    input_text = json.loads(str(request["input"]))
    assert [item["number"] for item in input_text["blueprint"]] == list(range(2, 8))
    assert "Return exactly 6 questions" in str(request["instructions"])


@pytest.mark.asyncio
async def test_mock_tail_low_effective_level_does_not_require_forbidden_types() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key="test-key", model="test-model", mock=False, repository=repository)
    generated = FallbackQuestionGenerator(repository).mock_tail(
        effective_level=2,
        background=BackgroundProfile(),
    )
    service._client = FakeOpenAIClient([generated])  # type: ignore[assignment]

    result = await service.generate_mock(
        1,
        BackgroundProfile(),
        stage="tail",
        adjustment=DifficultyAdjustment.HARDER,
        effective_level=2,
    )

    forbidden = {
        SurveyQuestionType.COMPARISON,
        SurveyQuestionType.PROBLEM_SOLVING,
        SurveyQuestionType.OPINION,
        SurveyQuestionType.ROLEPLAY,
    }
    assert [item.number for item in result.questions] == list(range(8, 16))
    assert {item.question_type for item in result.questions}.isdisjoint(forbidden)
    request = service._client.responses.requests[0]  # type: ignore[union-attr]
    input_text = json.loads(str(request["input"]))
    assert input_text["effectiveLevel"] == 2
    assert {
        item["questionType"] for item in input_text["blueprint"]
    }.isdisjoint({value.value for value in forbidden})


@pytest.mark.asyncio
async def test_mock_evaluation_retries_when_per_question_validation_fails() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key="test-key", model="test-model", mock=False, repository=repository)
    questions = [
        *FallbackQuestionGenerator(repository).mock_front(6, BackgroundProfile()),
        *FallbackQuestionGenerator(repository).mock_tail(
            effective_level=6,
            background=BackgroundProfile(),
        ),
    ]
    service._client = FakeTextOpenAIClient(  # type: ignore[assignment]
        [mock_evaluation_json(per_question_count=14), mock_evaluation_json()]
    )

    result = await service.evaluate_mock(
        questions=questions,
        transcripts=[
            f"Answer {number} with some supporting detail." for number in range(1, 16)
        ],
        target=OPIcLevel.IH,
        metrics=[
            AudioMetrics(
                durationSeconds=30,
                speakingSeconds=25,
                silenceRatio=0.1,
                wordsPerMinute=100,
            )
            for _ in range(15)
        ],
    )

    assert [item.number for item in result.per_question] == list(range(1, 16))
    assert service._client.responses.calls == 2  # type: ignore[union-attr]
    retry_request = service._client.responses.requests[1]  # type: ignore[union-attr]
    assert "previous structured output failed backend validation" in str(
        retry_request["instructions"]
    )
    assert "perQuestion must contain ordered numbers 1 through 15" in str(
        retry_request["instructions"]
    )


@pytest.mark.asyncio
async def test_daily_pool_normalizes_model_metadata_to_blueprint() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(
        api_key="test-key",
        model="test-model",
        mock=False,
        repository=repository,
    )
    background = BackgroundProfile(interests=["cafes"])
    expected = FallbackQuestionGenerator(repository).daily_pool(1, background)
    generated = [
        item.model_copy(
            update={
                "type": QuestionType.SURVEY,
                "category": "model_selected",
            }
        )
        for item in expected
    ]
    service._client = FakeOpenAIClient([generated])  # type: ignore[assignment]

    result = await service.generate_daily_pool(1, background)

    assert service._client.responses.calls == 1  # type: ignore[union-attr]
    assert [item.number for item in result.questions] == list(range(2, 16))
    assert [item.type for item in result.questions] == [item.type for item in expected]
    assert [item.category for item in result.questions] == [
        item.category for item in expected
    ]


@pytest.mark.asyncio
async def test_daily_pool_rewrites_recent_topic_id_collisions() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(
        api_key="test-key",
        model="test-model",
        mock=False,
        repository=repository,
    )
    background = BackgroundProfile(interests=["cafes"])
    generated = FallbackQuestionGenerator(repository).daily_pool(3, background)
    history = {
        "setHashes": [],
        "topicIds": [str(generated[0].topic_id)],
        "promptHashes": [],
    }
    service._client = FakeOpenAIClient([generated])  # type: ignore[assignment]

    result = await service.generate_daily_pool(3, background, history=history)

    assert service._client.responses.calls == 1  # type: ignore[union-attr]
    assert result.questions[0].topic_id not in history["topicIds"]
    assert result.questions[0].topic_id.startswith(f"{generated[0].topic_id}_q2_")
    assert result.questions[0].prompt == generated[0].prompt
    assert result.questions[1].topic_id == generated[1].topic_id


@pytest.mark.asyncio
async def test_generation_retry_includes_previous_validation_errors() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(
        api_key="test-key",
        model="test-model",
        mock=False,
        repository=repository,
    )
    duplicate = practice_questions("duplicate")
    fresh = practice_questions("fresh")
    service._client = FakeOpenAIClient([duplicate, fresh])  # type: ignore[assignment]
    history = {"setHashes": [], "topicIds": ["duplicate_topic_a"], "promptHashes": []}

    await service.generate_practice(OPIcLevel.IH, BackgroundProfile(), history=history)

    second_request = service._client.responses.requests[1]  # type: ignore[union-attr]
    input_text = json.loads(str(second_request["input"]))
    assert input_text["previousValidationErrors"] == [
        "generated question set repeats recent topicIds"
    ]
    assert "Regenerate the full set" in input_text["retryInstructions"][0]


@pytest.mark.asyncio
async def test_real_ai_retries_when_recent_topic_is_reused() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key="test-key", model="test-model", mock=False, repository=repository)
    duplicate = practice_questions("duplicate")
    fresh = practice_questions("fresh")
    service._client = FakeOpenAIClient([duplicate, fresh])  # type: ignore[assignment]
    history = {"setHashes": [], "topicIds": ["duplicate_topic_a"], "promptHashes": []}

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
    history = {"setHashes": [], "topicIds": ["duplicate_topic_a"], "promptHashes": []}

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
