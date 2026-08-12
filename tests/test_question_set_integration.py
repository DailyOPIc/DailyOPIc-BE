"""질문 셋 통합 테스트.

레벨 × 모드 × 배경 조합으로 50개 이상의 질문 셋을 만들고, 실제 서비스 경로가
쓰는 검증을 전부 통과하는지 확인한다.

`AIService` 의 폴백 경로를 타므로 `_validate_generated_questions`(블루프린트 대조,
시험 구조, 레벨 규칙, 프롬프트 유일성)가 모두 실행된다. 단위 테스트가 함수 하나를
보는 것과 달리, 여기서는 "사용자가 받게 되는 셋"을 통째로 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.api import (
    BackgroundProfile,
    DifficultyAdjustment,
    ExamSection,
    GeneratedQuestion,
    QuestionStyle,
)
from app.services.ai import AIQuestionGenerationError, AIService
from app.services.questions import (
    FallbackQuestionGenerator,
    QuestionPatternRepository,
    validate_daily_pool,
    validate_mock_blueprint,
    validate_practice_blueprint,
)

CATALOG = Path("app/data/question_patterns.json")
LEVELS = [1, 2, 3, 4, 5, 6]

BACKGROUNDS = {
    "student": BackgroundProfile(
        student_status="student",
        housing="family",
        interests=["movies", "music"],
        sports=["gym"],
        travel=["domestic_travel"],
    ),
    "worker": BackgroundProfile(
        occupation="office_worker",
        housing="apartment",
        interests=["cafes", "reading"],
        sports=[],
        travel=["overseas_travel"],
    ),
}


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """폴백 경로는 프로바이더 재시도 백오프로 매 호출 수 초를 쓴다.
    여기서 보려는 것은 검증 로직이므로 대기만 제거한다."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.ai.asyncio.sleep", _instant)


class _BrokenResponses:
    """OpenAI 장애 재현. 호출하면 항상 실패한다."""

    async def create(self, **kwargs: object) -> object:
        raise RuntimeError("provider down")


class _BrokenClient:
    def __init__(self) -> None:
        self.responses = _BrokenResponses()


def _service(repository: QuestionPatternRepository) -> AIService:
    service = AIService(
        api_key="test-key", model="test-model", mock=False, repository=repository
    )
    service._client = _BrokenClient()  # type: ignore[assignment]
    return service


def _sentence_count(prompt: str) -> int:
    return len([item for item in re.split(r"[.!?]+", prompt) if item.strip()])


# (mode, stage, 기대 문항 번호) — 서비스가 실제로 호출하는 단위와 같다.
CASES = [
    ("practice", "front", list(range(1, 8))),
    ("practice", "tail", list(range(8, 11))),
    ("mock", "front", list(range(1, 8))),
    ("mock", "tail", list(range(8, 16))),
    ("daily", "pool", list(range(2, 16))),
]


async def _generate(
    service: AIService, mode: str, stage: str, level: int, background: BackgroundProfile
) -> list[GeneratedQuestion]:
    if mode == "practice":
        result = await service.generate_practice(
            level, background, stage=stage, effective_level=level
        )
    elif mode == "mock":
        result = await service.generate_mock(
            level, background, stage=stage, effective_level=level
        )
    else:
        result = await service.generate_daily_pool(level, background)
    assert result.fallback_used is True
    assert result.provider == "catalog"
    return result.questions


@pytest.mark.parametrize("background_name", sorted(BACKGROUNDS))
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("mode,stage,expected_numbers", CASES)
async def test_generated_set_passes_every_service_validation(
    background_name: str,
    level: int,
    mode: str,
    stage: str,
    expected_numbers: list[int],
) -> None:
    """폴백 경로가 만든 셋이 서비스 검증을 통과하고 구조가 기대와 맞는지 본다."""
    repository = QuestionPatternRepository(CATALOG)
    background = BACKGROUNDS[background_name]
    try:
        questions = await _generate(
            _service(repository), mode, stage, level, background
        )
    except AIQuestionGenerationError as error:
        raise AssertionError(
            f"{mode}/{stage} level={level} background={background_name} 폴백 실패: {error}"
        ) from error

    assert [item.number for item in questions] == expected_numbers

    prompts = [item.prompt for item in questions]
    duplicated = sorted({item for item in prompts if prompts.count(item) > 1})
    assert not duplicated, f"중복 프롬프트={duplicated}"

    for item in questions:
        assert item.prompt.strip(), "빈 프롬프트"
        assert item.topic_id, "topicId 누락"
        assert item.question_style is not None, "questionStyle 누락"
        assert item.difficulty is not None
        assert item.estimated_level is not None
        if item.exam_section is not ExamSection.INTRODUCTION:
            assert _sentence_count(item.prompt) >= 1


@pytest.mark.parametrize("background_name", sorted(BACKGROUNDS))
@pytest.mark.parametrize("level", LEVELS)
async def test_full_mock_exam_matches_official_structure(
    background_name: str, level: int
) -> None:
    """15문항을 합쳐 실제 시험 구조 검증을 통과해야 한다.

    단계별(6문항/8문항) 검증만 돌리면 Q11·Q12 롤플레이, Q13~Q15 구성 규칙이
    확인되지 않는다.
    """
    repository = QuestionPatternRepository(CATALOG)
    background = BACKGROUNDS[background_name]
    service = _service(repository)
    front = await _generate(service, "mock", "front", level, background)
    tail = await _generate(_service(repository), "mock", "tail", level, background)
    questions = front + tail

    validate_mock_blueprint(questions)

    prompts = [item.prompt for item in questions]
    duplicated = sorted({item for item in prompts if prompts.count(item) > 1})
    assert not duplicated, f"level={level} 15문항 중복={duplicated}"

    roleplay = [
        item for item in questions if item.exam_section is ExamSection.ROLEPLAY
    ]
    assert len(roleplay) == 2, "실제 시험은 롤플레이 2문항"
    assert any(
        item.question_style is QuestionStyle.ROLEPLAY for item in roleplay
    ), f"level={level} 롤플레이 섹션에 롤플레이 스타일 문항이 없다"


@pytest.mark.parametrize("adjustment", list(DifficultyAdjustment))
@pytest.mark.parametrize("level", LEVELS)
async def test_daily_pool_survives_every_adjustment(
    level: int, adjustment: DifficultyAdjustment
) -> None:
    """난이도 조정(easier/same/harder) 후에도 셋이 유효해야 한다."""
    repository = QuestionPatternRepository(CATALOG)
    questions = FallbackQuestionGenerator(repository).daily_pool(
        level, BACKGROUNDS["student"], adjustment=adjustment
    )
    validate_daily_pool(questions)
    prompts = [item.prompt for item in questions]
    assert len(prompts) == len(set(prompts))


async def test_practice_full_set_matches_blueprint() -> None:
    """front + tail 을 합친 10문항이 연습 구조 검증을 통과해야 한다."""
    repository = QuestionPatternRepository(CATALOG)
    for level in LEVELS:
        front = await _generate(
            _service(repository), "practice", "front", level, BACKGROUNDS["student"]
        )
        tail = await _generate(
            _service(repository), "practice", "tail", level, BACKGROUNDS["student"]
        )
        validate_practice_blueprint(front + tail)
