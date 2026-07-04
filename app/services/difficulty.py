from __future__ import annotations

from app.models.api import DifficultyAdjustment, OPIcLevel


LEVEL_TO_TARGET = {
    1: OPIcLevel.IL,
    2: OPIcLevel.IM1,
    3: OPIcLevel.IM1,
    4: OPIcLevel.IM2,
    5: OPIcLevel.IH,
    6: OPIcLevel.AL,
}
TARGET_TO_INITIAL = {
    OPIcLevel.NL: 1,
    OPIcLevel.NM: 1,
    OPIcLevel.NH: 1,
    OPIcLevel.IL: 1,
    OPIcLevel.IM1: 3,
    OPIcLevel.IM2: 4,
    OPIcLevel.IM3: 4,
    OPIcLevel.IH: 5,
    OPIcLevel.AL: 6,
}


def clamp_initial_level(value: int) -> int:
    return min(6, max(1, value))


def initial_level_from_target(target_level: str | OPIcLevel | None) -> int | None:
    if not target_level:
        return None
    try:
        return TARGET_TO_INITIAL[OPIcLevel(str(target_level))]
    except (KeyError, ValueError):
        return None


def expected_target_level(level: int) -> OPIcLevel:
    return LEVEL_TO_TARGET[clamp_initial_level(level)]


def adjustment_delta(adjustment: DifficultyAdjustment | str | None) -> int:
    value = adjustment.value if isinstance(adjustment, DifficultyAdjustment) else adjustment
    if value == DifficultyAdjustment.EASIER.value:
        return -1
    if value == DifficultyAdjustment.HARDER.value:
        return 1
    return 0


def adjusted_level(initial_level: int, adjustment: DifficultyAdjustment | str | None) -> int:
    return clamp_initial_level(initial_level + adjustment_delta(adjustment))


def effective_level_code(
    initial_level: int, adjustment: DifficultyAdjustment | str | None
) -> str:
    return f"{clamp_initial_level(initial_level)}-{adjusted_level(initial_level, adjustment)}"
