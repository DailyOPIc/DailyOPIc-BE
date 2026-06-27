from pathlib import Path

from pydantic import ValidationError

from app.models.api import (
    BackgroundProfile,
    BackgroundSurvey,
    OPIcLevel,
    QuestionType,
    SurveyQuestionType,
)
from app.services.questions import (
    FallbackQuestionGenerator,
    QuestionPatternRepository,
    validate_mock_blueprint,
)


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
    assert questions[0].type is QuestionType.INTRODUCTION
    assert {item.combo_id for item in questions[1:4]} == {"survey-1"}
    assert {item.topic_id for item in questions[1:4]} == {"movies"}
    assert {item.topic_id for item in questions[4:7]} == {"music"}
    assert {item.topic_id for item in questions[7:10]} == {"cafes"}
    assert {item.combo_id for item in questions[10:12]} == {"roleplay"}
    assert questions[12].type is QuestionType.UNEXPECTED
    assert questions[13].type is QuestionType.COMPARISON
    assert questions[14].type is QuestionType.ADVANCED


def test_practice_set_contains_ten_numbered_questions() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    questions = FallbackQuestionGenerator(repository).practice(
        OPIcLevel.IM2, BackgroundProfile(housing="apartment")
    )
    assert [item.number for item in questions] == list(range(1, 11))
    assert all(item.type is QuestionType.PRACTICE for item in questions)
    assert all(item.question_type for item in questions)
    assert all(item.follow_up_prompt for item in questions)
    assert all(item.topic_id for item in questions)
    assert all(item.category for item in questions)
    assert all(item.estimated_level for item in questions)
    assert SurveyQuestionType.PROBLEM_SOLVING in {item.question_type for item in questions}


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
    assert [item.topic_id for item in first] == [item.topic_id for item in second]
    assert [item.question_type for item in first] == [item.question_type for item in second]


def test_background_survey_requires_three_multi_select_topics() -> None:
    try:
        BackgroundSurvey(status="student", residence="family", leisure=["movies"])
    except ValidationError as error:
        assert "at least 3 survey topics" in str(error)
    else:
        raise AssertionError("survey validation should reject too few topics")


def test_catalog_has_required_mock_schema() -> None:
    repository = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    assert len(repository.patterns) >= 80
    required = {
        "id",
        "topicId",
        "category",
        "difficulty",
        "questionType",
        "prompt",
        "followUpPrompt",
        "estimatedLevel",
        "tags",
    }
    assert all(required.issubset(item.keys()) for item in repository.patterns)
