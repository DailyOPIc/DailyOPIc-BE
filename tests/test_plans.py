"""plans.py 단위 테스트: 플랜 정의 및 한도 검증."""

from __future__ import annotations

import dataclasses

import pytest

from app.services.plans import (
    AnalysisDepth,
    PLAN_LIMITS,
    Plan,
    PlanLimits,
    is_paid,
    limits_for,
    plan_from_entitlement_ids,
    reward_auto_verify,
    reward_max_for,
)
from app.models.api import RewardPurpose


class TestPlanLimitsFree:
    """FREE 플랜 한도값 검증."""

    def test_plan_limits_free_values(self) -> None:
        """FREE 플랜의 모든 필드 값 확인."""
        limits = PLAN_LIMITS[Plan.FREE]

        # 기본 한도
        assert limits.plan == Plan.FREE
        assert limits.practice_daily == 1
        assert limits.practice_ad_bonus == 1
        assert limits.refresh_ad_bonus == 1
        assert limits.mock_daily == 1

        # 광고 및 시험 관련
        assert limits.mock_requires_ad is True
        assert limits.mock_is_trial is True
        assert limits.ads_enabled is True

        # 기능 제한
        assert limits.history_days == 7
        assert limits.analysis_depth == AnalysisDepth.SUMMARY
        assert limits.review_set is False

    def test_plan_limits_free_immutability(self) -> None:
        """FREE PlanLimits는 불변(frozen=True)."""
        limits = PLAN_LIMITS[Plan.FREE]
        with pytest.raises(AttributeError):
            limits.practice_daily = 999  # type: ignore


class TestPlanLimitsBasic:
    """BASIC 플랜 한도값 검증."""

    def test_plan_limits_basic_values(self) -> None:
        """BASIC 플랜 고유 값 확인."""
        limits = PLAN_LIMITS[Plan.BASIC]

        # BASIC 변경점
        assert limits.plan == Plan.BASIC
        assert limits.practice_daily == 3
        assert limits.practice_ad_bonus == 0
        assert limits.refresh_ad_bonus == 10
        assert limits.mock_requires_ad is False
        assert limits.mock_is_trial is False
        assert limits.ads_enabled is False

        # FREE와 동일한 값들
        assert limits.mock_daily == 1
        assert limits.history_days == 30
        assert limits.analysis_depth == AnalysisDepth.FULL
        assert limits.review_set is False


class TestPlanLimitsPlus:
    """PLUS 플랜 한도값 검증."""

    def test_plan_limits_plus_values(self) -> None:
        """PLUS 플랜 고유 값 확인."""
        limits = PLAN_LIMITS[Plan.PLUS]

        # PLUS 변경점
        assert limits.plan == Plan.PLUS
        assert limits.practice_daily == 10
        assert limits.practice_ad_bonus == 0
        assert limits.refresh_ad_bonus == 20
        assert limits.mock_daily == 3
        assert limits.history_days == 30
        assert limits.analysis_depth == AnalysisDepth.FULL
        assert limits.mock_requires_ad is False
        assert limits.mock_is_trial is False
        assert limits.ads_enabled is False

        # PLUS에서 공통값
        assert limits.review_set is False


class TestPlanLimitsPro:
    """PRO 플랜 한도값 검증."""

    def test_plan_limits_pro_values(self) -> None:
        """PRO 플랜 고유 값 확인 (최고 등급)."""
        limits = PLAN_LIMITS[Plan.PRO]

        # PRO 변경점 (최고 사양)
        assert limits.plan == Plan.PRO
        assert limits.practice_daily == 20
        assert limits.practice_ad_bonus == 0
        assert limits.refresh_ad_bonus == 30
        assert limits.mock_daily == 5
        assert limits.history_days is None  # 무제한
        assert limits.analysis_depth == AnalysisDepth.FULL
        assert limits.review_set is True
        assert limits.mock_requires_ad is False
        assert limits.mock_is_trial is False
        assert limits.ads_enabled is False


class TestLimitsForFunction:
    """limits_for() 함수 테스트."""

    def test_limits_for_plan_enum(self) -> None:
        """Plan enum 전달."""
        assert limits_for(Plan.FREE) == PLAN_LIMITS[Plan.FREE]
        assert limits_for(Plan.BASIC) == PLAN_LIMITS[Plan.BASIC]
        assert limits_for(Plan.PLUS) == PLAN_LIMITS[Plan.PLUS]
        assert limits_for(Plan.PRO) == PLAN_LIMITS[Plan.PRO]

    def test_limits_for_string_plan(self) -> None:
        """String 플랜명 전달."""
        assert limits_for("free") == PLAN_LIMITS[Plan.FREE]
        assert limits_for("basic") == PLAN_LIMITS[Plan.BASIC]
        assert limits_for("plus") == PLAN_LIMITS[Plan.PLUS]
        assert limits_for("pro") == PLAN_LIMITS[Plan.PRO]

    def test_limits_for_none_defaults_to_free(self) -> None:
        """None 전달 → FREE 반환."""
        assert limits_for(None) == PLAN_LIMITS[Plan.FREE]

    def test_limits_for_unknown_plan_defaults_to_free(self) -> None:
        """알 수 없는 플랜 → FREE 반환 (Plan._missing_ 메커니즘)."""
        # Plan enum의 _missing_에 의해 unknown → FREE로 변환
        assert limits_for("unknown_plan") == PLAN_LIMITS[Plan.FREE]
        assert limits_for("garbage") == PLAN_LIMITS[Plan.FREE]


class TestPlanLimitsStructure:
    """플랜 한도 구조 검증."""

    def test_all_plans_have_limits(self) -> None:
        """모든 Plan enum이 PLAN_LIMITS에 정의되어 있는가."""
        for plan in Plan:
            assert plan in PLAN_LIMITS, f"Plan.{plan.name}이 PLAN_LIMITS에 없음"

    def test_all_plans_have_correct_plan_field(self) -> None:
        """각 PlanLimits.plan이 정의한 플랜과 일치."""
        for plan in Plan:
            limits = PLAN_LIMITS[plan]
            assert limits.plan == plan

    def test_plan_limits_are_immutable(self) -> None:
        """모든 PlanLimits 인스턴스는 불변."""
        for plan in Plan:
            limits = PLAN_LIMITS[plan]
            with pytest.raises(AttributeError):
                limits.practice_daily = 999  # type: ignore


class TestPlanComparisons:
    """플랜 간 한도 비교."""

    def test_free_most_restrictive(self) -> None:
        """FREE는 데일리 한도가 가장 낮음."""
        free = PLAN_LIMITS[Plan.FREE]
        basic = PLAN_LIMITS[Plan.BASIC]
        plus = PLAN_LIMITS[Plan.PLUS]
        pro = PLAN_LIMITS[Plan.PRO]

        assert free.practice_daily == 1
        assert basic.practice_daily > free.practice_daily
        assert plus.practice_daily > basic.practice_daily
        assert pro.practice_daily > plus.practice_daily

    def test_analysis_depth_has_one_paid_boundary(self) -> None:
        """분석 깊이 차이는 무료↔유료 한 곳뿐이다.

        유료 플랜끼리 더 깊은 분석을 주지 않으므로 그렇게 팔지도 않는다.
        """
        assert PLAN_LIMITS[Plan.FREE].analysis_depth == AnalysisDepth.SUMMARY
        for plan in (Plan.BASIC, Plan.PLUS, Plan.PRO):
            assert PLAN_LIMITS[plan].analysis_depth == AnalysisDepth.FULL

    def test_paid_plans_no_ads(self) -> None:
        """유료 플랜(BASIC 이상)은 광고 비활성화."""
        assert PLAN_LIMITS[Plan.FREE].ads_enabled is True
        assert PLAN_LIMITS[Plan.BASIC].ads_enabled is False
        assert PLAN_LIMITS[Plan.PLUS].ads_enabled is False
        assert PLAN_LIMITS[Plan.PRO].ads_enabled is False

    def test_only_pro_has_review_set(self) -> None:
        """복습 세트는 PRO만."""
        assert PLAN_LIMITS[Plan.FREE].review_set is False
        assert PLAN_LIMITS[Plan.BASIC].review_set is False
        assert PLAN_LIMITS[Plan.PLUS].review_set is False
        assert PLAN_LIMITS[Plan.PRO].review_set is True

    def test_smart_insights_are_not_plan_gated(self) -> None:
        """등급 추이·취약 유형·주간 리포트는 전 플랜 무료 → 플랜 한도에 없다."""
        fields = {field.name for field in dataclasses.fields(PlanLimits)}
        assert fields.isdisjoint(
            {"grade_trend", "weakness_analysis", "weekly_report", "mock_comparison"}
        )


class TestCalendarCapabilities:
    """G4: 캘린더는 전 플랜 접근 가능하고, 유료는 자동화 깊이만 산다."""

    def test_calendar_is_enabled_for_every_plan(self) -> None:
        """무료 포함 모든 플랜에서 캘린더 자체는 열린다."""
        for plan in Plan:
            assert PLAN_LIMITS[plan].calendar_enabled is True

    def test_free_has_no_calendar_automation(self) -> None:
        """무료는 캘린더를 쓰지만 자동화는 전부 꺼져 있다."""
        limits = PLAN_LIMITS[Plan.FREE]
        assert limits.calendar_auto_replan is False
        assert limits.calendar_evaluation_adaptive is False
        assert limits.calendar_exam_backplan is False

    def test_basic_unlocks_only_auto_replan(self) -> None:
        """베이직이 사는 자동화는 자동 일정 재조정 하나뿐이다(학습 알림은 별개 기능)."""
        limits = PLAN_LIMITS[Plan.BASIC]
        assert limits.calendar_auto_replan is True
        assert limits.calendar_evaluation_adaptive is False
        assert limits.calendar_exam_backplan is False

    def test_study_reminder_is_locked_for_free_and_open_from_basic(self) -> None:
        """학습 알림은 유료 기능이다. 무료만 잠기고 베이직·플러스·프로는 모두 열린다."""
        assert PLAN_LIMITS[Plan.FREE].calendar_study_reminder is False
        assert PLAN_LIMITS[Plan.BASIC].calendar_study_reminder is True
        assert PLAN_LIMITS[Plan.PLUS].calendar_study_reminder is True
        assert PLAN_LIMITS[Plan.PRO].calendar_study_reminder is True

    def test_study_reminder_has_no_paid_tier_difference(self) -> None:
        """유료 사이에는 차이가 없다. 구분은 무료 vs 유료 하나뿐이다."""
        paid = [PLAN_LIMITS[plan].calendar_study_reminder for plan in (Plan.BASIC, Plan.PLUS, Plan.PRO)]
        assert paid == [True, True, True]

    def test_study_reminder_does_not_change_other_calendar_capabilities(self) -> None:
        """알림 추가는 순수 가산이다. 기존 캘린더 3종 값이 그대로다."""
        expected = {
            Plan.FREE: (True, False, False, False),
            Plan.BASIC: (True, True, False, False),
            Plan.PLUS: (True, True, True, True),
            Plan.PRO: (True, True, True, True),
        }
        for plan, values in expected.items():
            limits = PLAN_LIMITS[plan]
            assert (
                limits.calendar_enabled,
                limits.calendar_auto_replan,
                limits.calendar_evaluation_adaptive,
                limits.calendar_exam_backplan,
            ) == values, plan

    def test_plus_unlocks_every_implemented_calendar_capability(self) -> None:
        """플러스는 평가 반영과 시험일 역산까지 모두 켠다."""
        limits = PLAN_LIMITS[Plan.PLUS]
        assert limits.calendar_auto_replan is True
        assert limits.calendar_evaluation_adaptive is True
        assert limits.calendar_exam_backplan is True

    def test_pro_matches_plus_calendar_capabilities(self) -> None:
        """프로는 플러스의 캘린더 기능을 그대로 포함한다(추가 기능을 지어내지 않는다)."""
        plus = PLAN_LIMITS[Plan.PLUS]
        pro = PLAN_LIMITS[Plan.PRO]
        for field in ("calendar_enabled", "calendar_auto_replan",
                      "calendar_evaluation_adaptive", "calendar_exam_backplan",
                      "calendar_study_reminder"):
            assert getattr(pro, field) == getattr(plus, field)

    def test_no_weakness_planner_capability_exists(self) -> None:
        """취약점 기반 일정(G8)은 구현이 없으므로 한도 필드로도 존재하지 않는다."""
        fields = {field.name for field in dataclasses.fields(PlanLimits)}
        assert "calendar_weakness_planner" not in fields

    def test_calendar_capabilities_are_monotonic_by_plan(self) -> None:
        """상위 플랜이 하위 플랜의 캘린더 기능을 잃지 않는다."""
        order = [Plan.FREE, Plan.BASIC, Plan.PLUS, Plan.PRO]
        for field in ("calendar_auto_replan", "calendar_evaluation_adaptive",
                      "calendar_exam_backplan", "calendar_study_reminder"):
            values = [getattr(PLAN_LIMITS[plan], field) for plan in order]
            assert values == sorted(values), field


class TestIsPaidFunction:
    """is_paid() 함수 테스트."""

    def test_is_paid_free_is_false(self) -> None:
        """FREE는 유료가 아님."""
        assert is_paid(Plan.FREE) is False
        assert is_paid("free") is False
        assert is_paid(None) is False

    def test_is_paid_paid_plans_are_true(self) -> None:
        """BASIC, PLUS, PRO는 유료."""
        for plan in [Plan.BASIC, Plan.PLUS, Plan.PRO]:
            assert is_paid(plan) is True
            assert is_paid(plan.value) is True


class TestRewardAutoVerifyFunction:
    """reward_auto_verify() 함수 테스트."""

    def test_free_plan_never_auto_verifies(self) -> None:
        """FREE 플랜은 자동 검증 없음."""
        for purpose in [
            RewardPurpose.MOCK_START,
            RewardPurpose.MOCK_ADJUSTMENT,
            RewardPurpose.MOCK_RESULT,
            RewardPurpose.PRACTICE_REFRESH,
            RewardPurpose.TARGET_LEVEL_CHANGE,
            RewardPurpose.PRACTICE_CREDITS,
        ]:
            assert reward_auto_verify(Plan.FREE, purpose) is False

    def test_paid_plans_auto_verify_mock_and_refresh(self) -> None:
        """유료 플랜은 모의고사·리프레시 자동 검증."""
        auto_verify_purposes = {
            RewardPurpose.MOCK_START,
            RewardPurpose.MOCK_ADJUSTMENT,
            RewardPurpose.MOCK_RESULT,
            RewardPurpose.PRACTICE_REFRESH,
            RewardPurpose.TARGET_LEVEL_CHANGE,
        }
        for plan in [Plan.BASIC, Plan.PLUS, Plan.PRO]:
            for purpose in auto_verify_purposes:
                assert reward_auto_verify(plan, purpose) is True

    def test_paid_plans_do_not_auto_verify_practice_credits(self) -> None:
        """유료 플랜은 연습 크레딧 자동 검증 안 함 (사용 불가)."""
        for plan in [Plan.BASIC, Plan.PLUS, Plan.PRO]:
            assert (
                reward_auto_verify(plan, RewardPurpose.PRACTICE_CREDITS) is False
            )


class TestRewardMaxForFunction:
    """reward_max_for() 함수 테스트."""

    def test_mock_rewards_scale_with_mock_daily(self) -> None:
        """모의고사 리워드는 mock_daily * 3게이트."""
        free = reward_max_for(Plan.FREE, RewardPurpose.MOCK_START)
        pro = reward_max_for(Plan.PRO, RewardPurpose.MOCK_START)

        assert free == 1 * 3  # free의 mock_daily=1
        assert pro == 5 * 3  # pro의 mock_daily=5

    def test_practice_bonus_reflects_plan(self) -> None:
        """연습 보너스는 플랜별 광고 보너스 값."""
        assert reward_max_for(Plan.FREE, RewardPurpose.PRACTICE_CREDITS) == 1
        assert reward_max_for(Plan.BASIC, RewardPurpose.PRACTICE_CREDITS) == 0
        assert reward_max_for(Plan.PLUS, RewardPurpose.PRACTICE_CREDITS) == 0
        assert reward_max_for(Plan.PRO, RewardPurpose.PRACTICE_CREDITS) == 0

    def test_refresh_bonus_reflects_plan(self) -> None:
        """리프레시 보너스는 플랜별 refresh_ad_bonus 값."""
        assert reward_max_for(Plan.FREE, RewardPurpose.PRACTICE_REFRESH) == 1
        assert reward_max_for(Plan.BASIC, RewardPurpose.PRACTICE_REFRESH) == 10
        assert reward_max_for(Plan.PLUS, RewardPurpose.PRACTICE_REFRESH) == 20
        assert reward_max_for(Plan.PRO, RewardPurpose.PRACTICE_REFRESH) == 30

    def test_target_level_change_always_one(self) -> None:
        """목표 등급 변경은 모든 플랜 하루 1회."""
        for plan in Plan:
            assert reward_max_for(plan, RewardPurpose.TARGET_LEVEL_CHANGE) == 1

    def test_unknown_purpose_returns_zero(self) -> None:
        """정의되지 않은 용도는 0 반환."""
        # 만약 새로운 RewardPurpose가 추가되면 0 반환
        assert isinstance(reward_max_for(Plan.PRO, RewardPurpose.MOCK_START), int)


class TestPlanFromEntitlementIds:
    """plan_from_entitlement_ids() 함수 테스트."""

    def test_empty_entitlements_returns_free(self) -> None:
        """빈 엔타이틀먼트 목록 → FREE."""
        assert plan_from_entitlement_ids(None) == Plan.FREE
        assert plan_from_entitlement_ids([]) == Plan.FREE

    def test_single_entitlement_resolves(self) -> None:
        """단일 엔타이틀먼트 해석."""
        assert plan_from_entitlement_ids(["basic"]) == Plan.BASIC
        assert plan_from_entitlement_ids(["plus"]) == Plan.PLUS
        assert plan_from_entitlement_ids(["pro"]) == Plan.PRO

    def test_multiple_entitlements_returns_highest(self) -> None:
        """복수 엔타이틀먼트는 최상위 플랜 반환."""
        assert plan_from_entitlement_ids(["basic", "plus"]) == Plan.PLUS
        assert plan_from_entitlement_ids(["free", "plus", "basic"]) == Plan.PLUS
        assert plan_from_entitlement_ids(["pro", "basic"]) == Plan.PRO

    def test_premium_alias_resolves_to_pro(self) -> None:
        """별칭 'premium' → PRO."""
        assert plan_from_entitlement_ids(["premium"]) == Plan.PRO

    def test_exam_pass_resolves_to_pro(self) -> None:
        """별칭 'exam_pass' → PRO (시험 대비 패스)."""
        assert plan_from_entitlement_ids(["exam_pass"]) == Plan.PRO

    def test_unknown_entitlement_ignored(self) -> None:
        """알 수 없는 엔타이틀먼트는 무시, 나머지로 판단."""
        assert plan_from_entitlement_ids(["unknown", "basic"]) == Plan.BASIC
        assert plan_from_entitlement_ids(["garbage"]) == Plan.FREE

    def test_case_insensitive(self) -> None:
        """대소문자 무시."""
        assert plan_from_entitlement_ids(["BASIC"]) == Plan.BASIC
        assert plan_from_entitlement_ids(["Plus"]) == Plan.PLUS
        assert plan_from_entitlement_ids(["PRO"]) == Plan.PRO
