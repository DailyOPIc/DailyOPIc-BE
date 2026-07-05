from app.models.api import DifficultyAdjustment, OPIcLevel
from app.services.difficulty import (
    adjusted_level,
    effective_level_code,
    expected_target_level,
)


def test_adjustment_clamps_at_minimum_and_maximum_levels() -> None:
    assert adjusted_level(1, DifficultyAdjustment.EASIER) == 1
    assert effective_level_code(1, DifficultyAdjustment.EASIER) == "1-1"
    assert expected_target_level(adjusted_level(1, DifficultyAdjustment.EASIER)) is OPIcLevel.IL

    assert adjusted_level(6, DifficultyAdjustment.HARDER) == 6
    assert effective_level_code(6, DifficultyAdjustment.HARDER) == "6-6"
    assert expected_target_level(adjusted_level(6, DifficultyAdjustment.HARDER)) is OPIcLevel.AL
