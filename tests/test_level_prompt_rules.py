"""레벨별 프롬프트 문장 수 규칙.

`_validate_level_rules`(검증기)와 `_question_generation_instructions`(모델 지시문)이
같은 범위를 말해야 한다. 두 곳에 숫자를 따로 적으면 어긋난다.

먼저 현재 검증기 동작을 표로 고정하고(리팩토링 전후 동일해야 함), 그 다음 지시문이
같은 표에서 생성되는지 확인한다.
"""

from __future__ import annotations

import pytest

from app.models.api import ExamSection, GeneratedQuestion, OPIcLevel, QuestionStyle
from app.services.ai import LEVEL_PROMPT_SENTENCES, AIService

# level -> (최소 문장, 최대 문장 또는 None)
# 리팩토링 전 `_validate_level_rules` 의 분기를 그대로 옮긴 값이다.
EXPECTED_RANGES: dict[int, tuple[int, int | None]] = {
    1: (1, 2),
    2: (1, 2),
    3: (1, 3),
    4: (2, 3),
    5: (2, None),
    6: (2, 4),
}


def _question(sentences: int) -> GeneratedQuestion:
    prompt = " ".join(f"Tell me about topic number {index}." for index in range(sentences))
    return GeneratedQuestion(
        number=2,
        examSection=ExamSection.SURVEY,
        comboId="survey-1",
        topic="movies",
        prompt=prompt,
        difficulty=OPIcLevel.IM2,
        rubricFocus=["task fulfillment"],
        questionStyle=QuestionStyle.DESCRIPTION,
        followUpPrompt=None,
        topicId="movies",
        category="survey",
        estimatedLevel=OPIcLevel.IM2,
    )


@pytest.mark.parametrize("level", sorted(EXPECTED_RANGES))
def test_sentence_count_inside_range_is_accepted(level: int) -> None:
    minimum, maximum = EXPECTED_RANGES[level]
    upper = maximum if maximum is not None else minimum + 3
    for sentences in range(minimum, upper + 1):
        AIService._validate_level_rules(level, [_question(sentences)])


@pytest.mark.parametrize("level", sorted(EXPECTED_RANGES))
def test_sentence_count_below_minimum_is_rejected(level: int) -> None:
    minimum, _ = EXPECTED_RANGES[level]
    if minimum <= 1:
        pytest.skip("최소가 1문장이면 미달 케이스를 만들 수 없다")
    with pytest.raises(ValueError):
        AIService._validate_level_rules(level, [_question(minimum - 1)])


@pytest.mark.parametrize("level", sorted(EXPECTED_RANGES))
def test_sentence_count_above_maximum_is_rejected(level: int) -> None:
    _, maximum = EXPECTED_RANGES[level]
    if maximum is None:
        pytest.skip("상한이 없는 레벨")
    with pytest.raises(ValueError):
        AIService._validate_level_rules(level, [_question(maximum + 1)])


def test_introduction_is_exempt_from_sentence_rules() -> None:
    """Q1 자기소개는 서버가 고정하므로 문장 수 규칙에서 제외된다."""
    question = _question(5).model_copy(
        update={"exam_section": ExamSection.INTRODUCTION, "combo_id": None}
    )
    AIService._validate_level_rules(1, [question])


@pytest.mark.parametrize("mode,stage", [("practice", "front"), ("mock", "front"), ("mock", "tail"), ("daily", "pool")])
def test_instructions_state_the_same_sentence_range_as_the_validator(
    mode: str, stage: str
) -> None:
    """지시문의 문장 수 안내가 검증기 범위와 달라지면, 모델이 지시를 따랐는데도
    검증에 걸리거나 그 반대가 된다. 숫자는 표 하나에서만 나와야 한다."""
    instructions = AIService._question_generation_instructions(mode, stage)
    for level, (minimum, maximum) in LEVEL_PROMPT_SENTENCES.items():
        if maximum is None:
            phrase = f"Level {level}: at least {minimum} sentences."
        elif minimum == maximum:
            phrase = f"Level {level}: exactly {minimum} sentence."
        else:
            phrase = f"Level {level}: {minimum} to {maximum} sentences."
        assert phrase in instructions, f"지시문에 누락/불일치: {phrase!r}"


# 레벨이 문항 유형을 제한한다고 읽히는 문구. 블루프린트가 모든 레벨에 모든 유형을
# 보내므로, 이런 문구가 남아 있으면 모델이 지시와 블루프린트 사이에서 충돌한다.
_TYPE_RESTRICTING_PHRASES = [
    "must be very short, concrete, and descriptive",
    "may include simple reasons",
    "may include simple past experiences",
    "may include comparison or change",
    "may include experience, comparison, roleplay, and problem solving",
    "may include abstract opinions",
]


@pytest.mark.parametrize("mode,stage", [("practice", "front"), ("mock", "front"), ("mock", "tail"), ("daily", "pool")])
def test_instructions_do_not_restrict_question_types_by_level(
    mode: str, stage: str
) -> None:
    instructions = AIService._question_generation_instructions(mode, stage)
    leaked = [phrase for phrase in _TYPE_RESTRICTING_PHRASES if phrase in instructions]
    assert not leaked, f"레벨이 유형을 제한하는 문구가 남아 있다: {leaked}"


@pytest.mark.parametrize("mode,stage", [("practice", "front"), ("mock", "front"), ("mock", "tail"), ("daily", "pool")])
def test_instructions_tell_the_model_to_follow_the_blueprint_style(
    mode: str, stage: str
) -> None:
    """유형은 블루프린트가 정하고 모델은 그 유형의 문항을 쓴다는 지시가 있어야 한다."""
    instructions = AIService._question_generation_instructions(mode, stage)
    assert "questionStyle" in instructions
    assert "blueprint" in instructions
    assert "language demand" in instructions, "난이도가 문장·표현 수준으로 조절된다는 안내 누락"
