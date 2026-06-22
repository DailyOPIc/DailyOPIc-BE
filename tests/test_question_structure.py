from pathlib import Path

from app.models.api import BackgroundProfile, OPIcLevel, QuestionType
from app.services.questions import (
    FallbackQuestionGenerator,
    QuestionPatternRepository,
    validate_mock_blueprint,
)


def test_mock_exam_matches_exact_blueprint() -> None:
    repository = QuestionPatternRepository(Path("../opic_mobile/questions.json"))
    questions = FallbackQuestionGenerator(repository).mock(
        OPIcLevel.IH,
        BackgroundProfile(interests=["music", "movies"], travel=["domestic"]),
    )

    validate_mock_blueprint(questions)
    assert len(questions) == 15
    assert questions[0].type is QuestionType.INTRODUCTION
    assert {item.combo_id for item in questions[1:4]} == {"survey-a"}
    assert {item.combo_id for item in questions[10:13]} == {"roleplay"}
    assert questions[13].type is QuestionType.COMPARISON
    assert questions[14].type is QuestionType.ADVANCED


def test_practice_set_contains_ten_numbered_questions() -> None:
    repository = QuestionPatternRepository(Path("../opic_mobile/questions.json"))
    questions = FallbackQuestionGenerator(repository).practice(
        OPIcLevel.IM2, BackgroundProfile(housing="apartment")
    )
    assert [item.number for item in questions] == list(range(1, 11))
    assert all(item.type is QuestionType.PRACTICE for item in questions)
