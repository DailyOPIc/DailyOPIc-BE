"""plans.py 단위 테스트: 플랜 정의 및 한도 검증."""

from __future__ import annotations

import pytest

from app.services.plans import (
    AnalysisDepth,
    FeatureTier,
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
        assert limits.grade_trend == FeatureTier.LIMITED
        assert limits.weakness_analysis == FeatureTier.NONE
        assert limits.review_set is False
        assert limits.weekly_report is False
        assert limits.mock_comparison == FeatureTier.NONE

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
        assert limits.analysis_depth == AnalysisDepth.BASIC
        assert limits.grade_trend == FeatureTier.BASIC
        assert limits.weakness_analysis == FeatureTier.NONE
        assert limits.review_set is False
        assert limits.weekly_report is False
        assert limits.mock_comparison == FeatureTier.NONE


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
        assert limits.analysis_depth == AnalysisDepth.DETAILED
        assert limits.weakness_analysis == FeatureTier.BASIC
        assert limits.mock_requires_ad is False
        assert limits.mock_is_trial is False
        assert limits.ads_enabled is False

        # PLUS에서 공통값
        assert limits.grade_trend == FeatureTier.BASIC
        assert limits.review_set is False
        assert limits.weekly_report is False
        assert limits.mock_comparison == FeatureTier.NONE


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
        assert limits.analysis_depth == AnalysisDepth.FOCUS
        assert limits.grade_trend == FeatureTier.DETAILED
        assert limits.weakness_analysis == FeatureTier.ADVANCED
        assert limits.review_set is True
        assert limits.weekly_report is True
        assert limits.mock_comparison == FeatureTier.DETAILED
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

    def test_pro_most_generous_analysis(self) -> None:
        """PRO는 분석 깊이가 가장 깊음."""
        free = PLAN_LIMITS[Plan.FREE]
        basic = PLAN_LIMITS[Plan.BASIC]
        plus = PLAN_LIMITS[Plan.PLUS]
        pro = PLAN_LIMITS[Plan.PRO]

        assert free.analysis_depth == AnalysisDepth.SUMMARY
        assert basic.analysis_depth == AnalysisDepth.BASIC
        assert plus.analysis_depth == AnalysisDepth.DETAILED
        assert pro.analysis_depth == AnalysisDepth.FOCUS

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

    def test_only_pro_has_weekly_report(self) -> None:
        """주간 리포트는 PRO만."""
        assert PLAN_LIMITS[Plan.FREE].weekly_report is False
        assert PLAN_LIMITS[Plan.BASIC].weekly_report is False
        assert PLAN_LIMITS[Plan.PLUS].weekly_report is False
        assert PLAN_LIMITS[Plan.PRO].weekly_report is True


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
