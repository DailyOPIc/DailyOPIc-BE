import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.api import (
    BackgroundProfile,
    BackgroundSurvey,
    OPIcLevel,
    ExamSection,
    QuestionStyle,
)
from app.services.questions import (
    FallbackQuestionGenerator,
    QuestionPatternRepository,
    validate_mock_blueprint,
)

CATALOG = Path("app/data/question_patterns.json")
BACKGROUND = BackgroundProfile(interests=["movies", "music"])


@pytest.fixture
def generator() -> FallbackQuestionGenerator:
    return FallbackQuestionGenerator(QuestionPatternRepository(CATALOG))


def test_mock_exam_matches_exact_blueprint() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    survey = BackgroundSurvey(
        status="student",
        residence="family",
        leisure=["movies", "music", "cafes"],
        hobbies=["it"],
        sports=[],
        travel=["domestic_travel"],
    )
    questions = FallbackQuestionGenerator(repository).mock(
        OPIcLevel.IH,
        BackgroundProfile(interests=["music", "movies"], travel=["domestic"]),
        survey=survey,
    )

    validate_mock_blueprint(questions)
    assert len(questions) == 15
    assert questions[0].exam_section is ExamSection.INTRODUCTION
    assert {item.combo_id for item in questions[1:4]} == {"survey-1"}
    assert {item.topic_id for item in questions[1:4]} == {"movies"}
    assert {item.topic_id for item in questions[4:7]} == {"music"}
    assert {item.topic_id for item in questions[7:10]} == {"cafes"}
    assert {item.combo_id for item in questions[10:12]} == {"roleplay"}
    assert questions[12].exam_section is ExamSection.UNEXPECTED
    assert questions[13].exam_section is ExamSection.COMPARISON
    assert questions[14].exam_section is ExamSection.ADVANCED


def test_practice_set_contains_ten_numbered_questions() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    questions = FallbackQuestionGenerator(repository).practice(
        OPIcLevel.IM2, BackgroundProfile(housing="apartment")
    )
    assert [item.number for item in questions] == list(range(1, 11))
    assert questions[0].exam_section is ExamSection.INTRODUCTION
    assert all(item.exam_section is ExamSection.SURVEY for item in questions[1:7])
    assert all(item.exam_section is ExamSection.UNEXPECTED for item in questions[7:10])
    assert {item.combo_id for item in questions[1:4]} == {"daily-a"}
    assert {item.combo_id for item in questions[4:7]} == {"daily-b"}
    assert all(item.question_style for item in questions)
    assert all(item.topic_id for item in questions)
    assert all(item.category for item in questions)
    assert all(item.estimated_level for item in questions)
    assert QuestionStyle.COMPARISON in {item.question_style for item in questions}


def test_practice_set_uses_target_level_instead_of_background_profile() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    generator = FallbackQuestionGenerator(repository)
    first = generator.practice(
        OPIcLevel.IH,
        BackgroundProfile(interests=["music"], sports=["gym"], travel=["domestic"]),
    )
    second = generator.practice(
        OPIcLevel.IH,
        BackgroundProfile(interests=["gaming"], sports=["swimming"], travel=["overseas"]),
    )
    assert [item.question_style for item in first] == [item.question_style for item in second]
    assert {item.combo_id for item in first[1:4]} == {"daily-a"}
    assert {item.combo_id for item in second[1:4]} == {"daily-a"}
    assert first[1].difficulty == second[1].difficulty == OPIcLevel.IH


def test_background_survey_requires_three_multi_select_topics() -> None:
    try:
        BackgroundSurvey(status="student", residence="family", leisure=["movies"])
    except ValidationError as error:
        assert "at least 3 survey topics" in str(error)
    else:
        raise AssertionError("survey validation should reject too few topics")


def _fallback_sets(
    generator: FallbackQuestionGenerator, level: int
) -> dict[str, list]:
    """OpenAI 실패 시 서버가 대신 내려보내는 세 경로."""
    return {
        "mock": generator.mock_front(level, BACKGROUND)
        + generator.mock_tail(effective_level=level, background=BACKGROUND),
        "practice": generator.practice_front(level, BACKGROUND)
        + generator.practice_tail(effective_level=level, background=BACKGROUND),
        "daily": generator.daily_pool(level, BACKGROUND),
    }


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_fallback_prompts_are_unique_for_every_level(
    generator: FallbackQuestionGenerator, level: int
) -> None:
    """폴백 문항에 중복 프롬프트가 있으면 AIService 의 유일성 검증에 걸려
    503 ai_unavailable 이 된다. 즉 사용자가 문항을 아예 받지 못한다."""
    for name, questions in _fallback_sets(generator, level).items():
        prompts = [item.prompt for item in questions]
        duplicated = sorted({item for item in prompts if prompts.count(item) > 1})
        assert not duplicated, f"{name} level={level} 중복 프롬프트={duplicated}"


# 수정 대상이 아닌 레벨의 문구를 고정한다. 레벨 3·4 의 DESCRIPTION 분기를 추가하면서
# 다른 레벨 문구가 함께 바뀌면 이 테스트가 먼저 실패해야 한다.
UNTOUCHED_PRACTICE_FRONT_PROMPTS = {
    # 의문문 부호 수정(작업 5)으로 갱신했다. 문장 구성은 그대로다.
    2: [
        "Introduce yourself.",
        "Tell me about movies. Why do you like it?",
        "What do you usually do when you spend time with movies? Give one simple reason.",
        "Tell me about a simple experience related to movies. Why do you remember it?",
        "Tell me about music. Why do you like it?",
        "What do you usually do when you spend time with music? Give one simple reason.",
        "Tell me about a simple experience related to music. Why do you remember it?",
    ],
    5: [
        "Introduce yourself.",
        "Describe the key features of movies. Explain what makes them distinctive. Tell me why they matter to you.",
        "Explain your usual routine involving movies. Describe how you organize it. Tell me why that routine works well for you.",
        "Describe a detailed experience related to movies. Explain the background and the result. Tell me how that experience changed your thinking.",
        "Describe the key features of music. Explain what makes them distinctive. Tell me why they matter to you.",
        "Explain your usual routine involving music. Describe how you organize it. Tell me why that routine works well for you.",
        "Describe a detailed experience related to music. Explain the background and the result. Tell me how that experience changed your thinking.",
    ],
    6: [
        "Introduce yourself.",
        "Describe the most important features of movies. Explain how different people experience it. Analyze why those features matter in daily life.",
        "Explain how people usually engage with movies. Describe how that routine has evolved. Analyze what could change it in the future.",
        "Discuss a complex experience related to movies. Explain how the situation developed. Analyze what it shows about people's choices or values.",
        "Describe the most important features of music. Explain how different people experience it. Analyze why those features matter in daily life.",
        "Explain how people usually engage with music. Describe how that routine has evolved. Analyze what could change it in the future.",
        "Discuss a complex experience related to music. Explain how the situation developed. Analyze what it shows about people's choices or values.",
    ],
}


@pytest.mark.parametrize("level", sorted(UNTOUCHED_PRACTICE_FRONT_PROMPTS))
def test_untouched_levels_keep_existing_prompts(
    generator: FallbackQuestionGenerator, level: int
) -> None:
    questions = generator.practice_front(level, BACKGROUND)
    assert [
        item.prompt for item in questions
    ] == UNTOUCHED_PRACTICE_FRONT_PROMPTS[level]


def test_catalog_has_required_mock_schema() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    assert len(repository.patterns) >= 80
    required = {
        "id",
        "topicId",
        "category",
        "difficulty",
        "questionStyle",
        "prompt",
        "followUpPrompt",
        "estimatedLevel",
        "tags",
    }
    assert all(required.issubset(item.keys()) for item in repository.patterns)


def _sentence_count(prompt: str) -> int:
    """AIService._sentence_count 와 동일한 정의."""
    return len([item for item in re.split(r"[.!?]+", prompt) if item.strip()])


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_mock_exam_keeps_roleplay_section_at_low_levels(
    generator: FallbackQuestionGenerator, level: int
) -> None:
    """실제 OPIc 은 자기평가 레벨과 무관하게 Q11·Q12 를 롤플레이로 낸다.
    초급이라고 섹션을 빼면 사용자가 롤플레이를 연습할 기회가 없다."""
    questions = generator.mock_front(level, BACKGROUND) + generator.mock_tail(
        effective_level=level, background=BACKGROUND
    )
    validate_mock_blueprint(questions)
    assert questions[10].exam_section is ExamSection.ROLEPLAY
    assert questions[11].exam_section is ExamSection.ROLEPLAY


@pytest.mark.parametrize("level", [1, 2])
def test_low_level_mock_has_a_real_roleplay_prompt(
    generator: FallbackQuestionGenerator, level: int
) -> None:
    """섹션만 롤플레이로 두고 내용이 묘사 문항이면 연습 효과가 없다.
    난이도는 문장 길이로만 낮춘다."""
    questions = generator.mock_tail(effective_level=level, background=BACKGROUND)
    roleplay = [
        item for item in questions if item.question_style is QuestionStyle.ROLEPLAY
    ]
    assert roleplay, f"level={level} 롤플레이 스타일 문항이 없다"
    for item in roleplay:
        assert _sentence_count(item.prompt) <= 2, f"level={level} 롤플레이 문장 과다"


# 스타일 강등이 사라졌으므로 "강등되지 않은 스타일만 검사" 하던 예전 테스트는
# tests/test_question_set_integration.py 의
# test_every_style_is_reachable_and_has_its_own_prompt 로 대체했다.
# 이제 7개 스타일 전부가 검사 대상이다.
