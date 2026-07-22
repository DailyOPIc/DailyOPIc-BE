"""구독 플랜(엔타이틀먼트) 정의와 플랜별 한도.

IAP BM의 단일 진실 소스. 서버는 이 매핑을 근거로 사용량을 강제하고,
클라이언트는 /v1/capabilities 로 플랜별 정책을 받아 UI를 게이팅한다.

플랜 4단계: free / basic / plus / pro
- basic = "가성비 히어로"(₩2,900): 광고 제거 + 매일 모의고사 + 데일리 3회
- 시험 대비 패스는 기간 내 pro 동급으로 취급(entitlement.plan == pro)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.api import RewardPurpose


class Plan(StrEnum):
    FREE = "free"
    BASIC = "basic"
    PLUS = "plus"
    PRO = "pro"

    @classmethod
    def _missing_(cls, value: object) -> "Plan":
        # 알 수 없는/누락 값은 항상 무료로 안전하게 강등.
        return cls.FREE


class AnalysisDepth(StrEnum):
    SUMMARY = "summary"  # 예상 등급 + 요약(교정/모범답안 없음) — 무료
    BASIC = "basic"  # 기본 분석 — 베이직
    DETAILED = "detailed"  # 상세(풀 루브릭 + 교정/모범답안) — 플러스
    FOCUS = "focus"  # 집중(상세 + 답변별 코칭) — 프로


class FeatureTier(StrEnum):
    NONE = "none"
    LIMITED = "limited"
    BASIC = "basic"
    DETAILED = "detailed"
    ADVANCED = "advanced"


@dataclass(frozen=True, slots=True)
class PlanLimits:
    plan: Plan
    practice_daily: int  # 하루 데일리 학습(평가) 무료 한도
    practice_ad_bonus: int  # 광고로 얻는 추가 데일리(무료 전용)
    refresh_ad_bonus: int  # 광고로 얻는 문제 리프레시 횟수
    mock_daily: int  # 하루 모의고사 횟수
    mock_requires_ad: bool  # 모의고사 광고 게이트 필요 여부(무료만 True)
    history_days: int | None  # 학습 기록 열람 범위(None = 전체)
    analysis_depth: AnalysisDepth
    grade_trend: FeatureTier  # 예상 등급 추이
    weakness_analysis: FeatureTier  # 취약 유형 분석
    review_set: bool  # 취약점 복습 세트 자동 생성
    weekly_report: bool  # 학습 리포트(주간)
    mock_comparison: FeatureTier  # 모의고사 비교(v2 준비값)
    ads_enabled: bool  # 배너/리워드 광고 노출 여부


_MOCK_REWARD_GATES = 3  # start / adjustment / result

PLAN_LIMITS: dict[Plan, PlanLimits] = {
    Plan.FREE: PlanLimits(
        plan=Plan.FREE,
        practice_daily=1,
        practice_ad_bonus=1,
        refresh_ad_bonus=1,
        mock_daily=1,
        mock_requires_ad=True,
        history_days=7,
        analysis_depth=AnalysisDepth.SUMMARY,
        grade_trend=FeatureTier.LIMITED,
        weakness_analysis=FeatureTier.NONE,
        review_set=False,
        weekly_report=False,
        mock_comparison=FeatureTier.NONE,
        ads_enabled=True,
    ),
    Plan.BASIC: PlanLimits(
        plan=Plan.BASIC,
        practice_daily=3,
        practice_ad_bonus=0,
        refresh_ad_bonus=10,
        mock_daily=1,
        mock_requires_ad=False,
        history_days=30,
        analysis_depth=AnalysisDepth.BASIC,
        grade_trend=FeatureTier.BASIC,
        weakness_analysis=FeatureTier.NONE,
        review_set=False,
        weekly_report=False,
        mock_comparison=FeatureTier.NONE,
        ads_enabled=False,
    ),
    Plan.PLUS: PlanLimits(
        plan=Plan.PLUS,
        practice_daily=10,
        practice_ad_bonus=0,
        refresh_ad_bonus=20,
        mock_daily=1,
        mock_requires_ad=False,
        history_days=30,
        analysis_depth=AnalysisDepth.DETAILED,
        grade_trend=FeatureTier.BASIC,
        weakness_analysis=FeatureTier.BASIC,
        review_set=False,
        weekly_report=False,
        mock_comparison=FeatureTier.NONE,
        ads_enabled=False,
    ),
    Plan.PRO: PlanLimits(
        plan=Plan.PRO,
        practice_daily=20,
        practice_ad_bonus=0,
        refresh_ad_bonus=30,
        mock_daily=1,
        mock_requires_ad=False,
        history_days=None,
        analysis_depth=AnalysisDepth.FOCUS,
        grade_trend=FeatureTier.DETAILED,
        weakness_analysis=FeatureTier.ADVANCED,
        review_set=True,
        weekly_report=True,
        mock_comparison=FeatureTier.DETAILED,
        ads_enabled=False,
    ),
}


def limits_for(plan: Plan | str | None) -> PlanLimits:
    return PLAN_LIMITS[Plan(plan) if plan is not None else Plan.FREE]


# RevenueCat 대시보드의 엔타이틀먼트 식별자 → 플랜. 동명 매핑을 기본으로 하되
# 하위 호환용 별칭을 포함한다. 시험 대비 패스는 pro 엔타이틀먼트에 연결.
_ENTITLEMENT_TO_PLAN: dict[str, Plan] = {
    "basic": Plan.BASIC,
    "plus": Plan.PLUS,
    "pro": Plan.PRO,
    "premium": Plan.PRO,
    "exam_pass": Plan.PRO,
}

_PLAN_RANK = {Plan.FREE: 0, Plan.BASIC: 1, Plan.PLUS: 2, Plan.PRO: 3}


def plan_from_entitlement_ids(entitlement_ids: list[str] | None) -> Plan:
    """활성 엔타이틀먼트 목록 중 가장 높은 등급의 플랜을 반환."""
    best = Plan.FREE
    for raw in entitlement_ids or []:
        candidate = _ENTITLEMENT_TO_PLAN.get(str(raw).strip().lower())
        if candidate and _PLAN_RANK[candidate] > _PLAN_RANK[best]:
            best = candidate
    return best


def is_paid(plan: Plan | str | None) -> bool:
    resolved = Plan(plan) if plan is not None else Plan.FREE
    return resolved is not Plan.FREE


def reward_auto_verify(plan: Plan | str | None, purpose: RewardPurpose) -> bool:
    """유료 플랜은 모의고사 광고 게이트를 광고 없이 즉시 충족(auto-verify)."""
    resolved = Plan(plan) if plan is not None else Plan.FREE
    if resolved is Plan.FREE:
        return False
    # 유료 플랜: 모의고사 게이트 + 문제 리프레시를 광고 없이 즉시 통과.
    return purpose in {
        RewardPurpose.MOCK_START,
        RewardPurpose.MOCK_ADJUSTMENT,
        RewardPurpose.MOCK_RESULT,
        RewardPurpose.PRACTICE_REFRESH,
    }


def reward_max_for(plan: Plan | str | None, purpose: RewardPurpose) -> int:
    """플랜·용도별 하루 리워드 상한.

    - 모의고사 게이트: 하루 1회 모의고사에 필요한 3게이트(무료는 광고, 유료는 auto-verify).
    - 데일리 광고 보너스/리프레시: 무료만 허용, 유료는 0(광고 없음).
    - 목표 등급 변경: 카운트 대상 아님(무제한 취급, 상한만 넉넉히).
    """
    resolved = Plan(plan) if plan is not None else Plan.FREE
    limits = PLAN_LIMITS[resolved]
    if purpose in {
        RewardPurpose.MOCK_START,
        RewardPurpose.MOCK_ADJUSTMENT,
        RewardPurpose.MOCK_RESULT,
    }:
        return _MOCK_REWARD_GATES * limits.mock_daily
    if purpose is RewardPurpose.PRACTICE_CREDITS:
        return limits.practice_ad_bonus
    if purpose is RewardPurpose.PRACTICE_REFRESH:
        return limits.refresh_ad_bonus
    if purpose is RewardPurpose.TARGET_LEVEL_CHANGE:
        return 99
    return 0
