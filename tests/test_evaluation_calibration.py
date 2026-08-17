from pathlib import Path

from app.models.api import OPIcLevel, RubricAssessment, RubricBand, RubricDimension
from app.services.ai import AIService, RUBRIC_SCORE_BY_BAND, _reconcile_with_rubrics
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


def _rubrics(band: RubricBand, **overrides: RubricBand) -> list[RubricAssessment]:
    """5개 항목에 같은 밴드를 주고, 필요한 항목만 덮어쓴다."""
    return [
        RubricAssessment(
            dimension=dimension,
            band=overrides.get(dimension.value, band),
            evidence="근거",
            nextAction="액션",
        )
        for dimension in RubricDimension
    ]


def test_level_is_capped_when_no_band_reaches_strong() -> None:
    """공식 기준의 IH 는 '예측 못한 복잡한 상황을 설명하고 문제를 해결'이다.
    어느 항목도 strong 에 못 미치면 IH·AL 을 주장할 근거가 없다."""
    level, reconciled = _reconcile_with_rubrics(
        OPIcLevel.IH, _rubrics(RubricBand.DEVELOPING)
    )
    assert level is OPIcLevel.IM3
    assert reconciled is True


def test_consistent_level_is_left_alone() -> None:
    level, reconciled = _reconcile_with_rubrics(
        OPIcLevel.IM1, _rubrics(RubricBand.DEVELOPING)
    )
    assert level is OPIcLevel.IM1
    assert reconciled is False


def test_all_foundation_cannot_exceed_the_floor() -> None:
    level, reconciled = _reconcile_with_rubrics(
        OPIcLevel.IM2, _rubrics(RubricBand.FOUNDATION)
    )
    assert level is OPIcLevel.IL
    assert reconciled is True


def test_advanced_requires_every_band_strong_or_above() -> None:
    """공식 기준의 AL 은 시제·형용사·접속사·문단을 '일관되게' 관리하는 수준이다.
    한 항목이라도 strong 미만이면 AL 이 될 수 없다."""
    level, reconciled = _reconcile_with_rubrics(
        OPIcLevel.AL, _rubrics(RubricBand.STRONG, grammar=RubricBand.FUNCTIONAL)
    )
    assert level is OPIcLevel.IH
    assert reconciled is True


def test_advanced_survives_when_every_band_is_high() -> None:
    level, reconciled = _reconcile_with_rubrics(
        OPIcLevel.AL, _rubrics(RubricBand.STRONG, grammar=RubricBand.ADVANCED)
    )
    assert level is OPIcLevel.AL
    assert reconciled is False


def test_ih_requires_task_fulfillment_and_discourse() -> None:
    """사건을 설명하고 문제를 해결한다는 판정은 과제수행과 구성이 받쳐줘야 한다."""
    level, reconciled = _reconcile_with_rubrics(
        OPIcLevel.IH, _rubrics(RubricBand.STRONG, discourse=RubricBand.DEVELOPING)
    )
    assert level is OPIcLevel.IM3
    assert reconciled is True


def test_reconcile_never_raises_the_level() -> None:
    """보정은 상한만 적용한다. 등급을 올리는 방향으로는 절대 움직이지 않는다."""
    for band in RubricBand:
        level, reconciled = _reconcile_with_rubrics(OPIcLevel.IL, _rubrics(band))
        assert level is OPIcLevel.IL
        assert reconciled is False
