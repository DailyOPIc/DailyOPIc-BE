from pathlib import Path

from app.models.api import RubricAssessment, RubricBand, RubricDimension
from app.services.ai import AIService, RUBRIC_SCORE_BY_BAND
from app.services.questions import QuestionPatternRepository


def test_rubric_band_compatibility_scores_are_fixed_and_never_one_point() -> None:
    assert RUBRIC_SCORE_BY_BAND == {
        RubricBand.FOUNDATION: 20,
        RubricBand.DEVELOPING: 40,
        RubricBand.FUNCTIONAL: 60,
        RubricBand.STRONG: 80,
        RubricBand.ADVANCED: 95,
    }
    service = AIService(
        api_key=None,
        model="fixture",
        mock=True,
        repository=QuestionPatternRepository(Path("app/data/question_patterns.json")),
    )
    rubrics = [
        RubricAssessment(
            dimension=dimension,
            band=RubricBand.FOUNDATION,
            evidence="fixture",
            nextAction="fixture",
        )
        for dimension in RubricDimension
    ]
    scores = service._scores_from_rubrics(rubrics)
    assert set(scores.model_dump().values()) == {20}
