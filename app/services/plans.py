"""구독 플랜(엔타이틀먼트) 정의와 플랜별 한도.

IAP BM의 단일 진실 소스. 서버는 이 매핑을 근거로 사용량을 강제하고,
클라이언트는 /v1/capabilities 로 플랜별 정책을 받아 UI를 게이팅한다.

플랜 4단계: free / basic / plus / pro
- basic = "가성비 히어로"(₩2,900): 광고 제거 + 결과 전체 해제 + 데일리 3회
- 분석 내용의 차이는 무료↔유료 한 곳뿐이다(AnalysisDepth 참고).
  유료 플랜끼리의 차이는 하루 횟수 · 기록 보관 기간 · 복습 세트뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    """AI가 실제로 다르게 만들어내는 깊이는 두 단계뿐이다.

    이전에는 summary/basic/detailed/focus 네 값이 있었지만 `ai.py`는
    summary·basic을 똑같이 축약하고 detailed·focus를 똑같이 전체 생성해서,
    베이직↔플러스↔프로의 "더 깊은 분석" 차이가 실제로는 존재하지 않았다.
    있지도 않은 차이를 이름으로만 유지하지 않는다.
    """

    SUMMARY = "summary"  # 예상 등급 + 강점/개선점 + 5영역 피드백 — 무료
    FULL = "full"  # 위 전부 + 교정 답안 · 모범 답안 · 목표 갭 — 유료 전체


@dataclass(frozen=True, slots=True)
class PlanLimits:
    plan: Plan
    practice_daily: int  # 하루 데일리 학습(평가) 무료 한도
    practice_ad_bonus: int  # 광고로 얻는 추가 데일리(무료 전용)
    refresh_ad_bonus: int  # 광고로 얻는 문제 리프레시 횟수
    mock_daily: int  # 하루 모의고사 횟수
    mock_requires_ad: bool  # 모의고사 광고 게이트 필요 여부(무료만 True)
    mock_is_trial: bool  # True면 mock_daily가 '평생 1회 체험'을 의미(무료)
    history_days: int | None  # 학습 기록 열람 범위(None = 전체)
    analysis_depth: AnalysisDepth
    review_set: bool  # 취약점 복습 세트 자동 생성(routes.py에서 실제 강제)
    ads_enabled: bool  # 배너/리워드 광고 노출 여부
    # 삭제한 필드: grade_trend / weakness_analysis / weekly_report /
    # mock_comparison. 스마트 인사이트 3종은 앱이 로컬 기록으로 계산해 전 플랜
    # 무료로 제공하고, 모의고사 비교는 서버·앱 어디에도 구현이 없었다.
    # 강제하지 않는 플랜 게이트를 capabilities로 내려보내면 그 자체가 거짓 약속이다.


# 모의고사 게이트(start/adjustment/result)는 각각 따로 센다. 한도는 "게이트 하나를
# 하루에 몇 번까지 통과할 수 있나"이다. 회차 수와 똑같이 잡으면 포기 후 재시작이나
# 광고 재시청에 여유가 0이 되고, 마지막 게이트인 채점에서 막혀 15문항을 다 답한
# 사용자가 결과를 못 본다. 회차당 3번까지 허용한다.
_MOCK_GATE_ATTEMPTS = 3

# 공통 디폴트값 (FREE 플랜 기준)
_DEFAULT_LIMITS = PlanLimits(
    plan=Plan.FREE,
    practice_daily=1,
    practice_ad_bonus=1,
    refresh_ad_bonus=1,
    mock_daily=1,
    mock_requires_ad=True,
    mock_is_trial=True,
    history_days=7,
    analysis_depth=AnalysisDepth.SUMMARY,
    review_set=False,
    ads_enabled=True,
)

# 플랜별 변경점만 정의
_PLAN_OVERRIDES: dict[Plan, dict] = {
    Plan.FREE: {},  # 디폴트 사용
    Plan.BASIC: {
        "practice_daily": 3,
        "practice_ad_bonus": 0,
        "refresh_ad_bonus": 10,
        "mock_requires_ad": False,
        "mock_is_trial": False,
        "history_days": 30,
        "analysis_depth": AnalysisDepth.FULL,
        "ads_enabled": False,
    },
    Plan.PLUS: {
        "practice_daily": 10,
        "practice_ad_bonus": 0,
        "refresh_ad_bonus": 20,
        "mock_daily": 3,
        "mock_requires_ad": False,
        "mock_is_trial": False,
        "history_days": 30,
        "analysis_depth": AnalysisDepth.FULL,
        "ads_enabled": False,
    },
    Plan.PRO: {
        "practice_daily": 20,
        "practice_ad_bonus": 0,
        "refresh_ad_bonus": 30,
        "mock_daily": 5,
        "mock_requires_ad": False,
        "mock_is_trial": False,
        "history_days": None,
        "analysis_depth": AnalysisDepth.FULL,
        "review_set": True,
        "ads_enabled": False,
    },
}

# 플랜별 한도 생성
PLAN_LIMITS: dict[Plan, PlanLimits] = {}
for plan in Plan:
    overrides = _PLAN_OVERRIDES.get(plan, {})
    PLAN_LIMITS[plan] = replace(_DEFAULT_LIMITS, plan=plan, **overrides)


def limits_for(plan: Plan | str | None) -> PlanLimits:
    return PLAN_LIMITS[Plan(plan) if plan is not None else Plan.FREE]


# RevenueCat 대시보드의 엔타이틀먼트 식별자 → 플랜.
# 현재 판매 중인 상품은 basic / plus / pro 세 개뿐이다.
# premium·exam_pass는 과거 명칭이며 지금은 발급되지 않는다. 그래도 남겨 둔다:
# 아직 만료되지 않은 구독자가 있다면 매핑을 지우는 순간 무료로 강등되기 때문이다.
# 새 상품을 이 별칭으로 만들지 않는다.
_LEGACY_ENTITLEMENT_ALIASES: dict[str, Plan] = {
    "premium": Plan.PRO,
    "exam_pass": Plan.PRO,
}

_ENTITLEMENT_TO_PLAN: dict[str, Plan] = {
    "basic": Plan.BASIC,
    "plus": Plan.PLUS,
    "pro": Plan.PRO,
    **_LEGACY_ENTITLEMENT_ALIASES,
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
    # 유료 플랜: 모의고사 게이트 + 문제 리프레시 + 난이도(목표 등급) 변경을
    # 광고 없이 즉시 통과.
    return purpose in {
        RewardPurpose.MOCK_START,
        RewardPurpose.MOCK_ADJUSTMENT,
        RewardPurpose.MOCK_RESULT,
        RewardPurpose.PRACTICE_REFRESH,
        RewardPurpose.TARGET_LEVEL_CHANGE,
    }


def reward_max_for(plan: Plan | str | None, purpose: RewardPurpose) -> int:
    """플랜·용도별 하루 리워드 상한.

    - 모의고사 게이트: 게이트별로 회차당 3번까지(무료는 광고, 유료는 auto-verify).
      실제 응시 횟수는 완료된 회차 수(mock_daily)로 따로 강제한다.
    - 데일리 광고 보너스/리프레시: 무료만 허용, 유료는 0(광고 없음).
    - 목표 등급(난이도) 변경: 전 플랜 하루 1회(무료 광고, 유료 auto-verify).
    """
    resolved = Plan(plan) if plan is not None else Plan.FREE
    limits = PLAN_LIMITS[resolved]
    if purpose in {
        RewardPurpose.MOCK_START,
        RewardPurpose.MOCK_ADJUSTMENT,
        RewardPurpose.MOCK_RESULT,
    }:
        return _MOCK_GATE_ATTEMPTS * limits.mock_daily
    if purpose is RewardPurpose.PRACTICE_CREDITS:
        return limits.practice_ad_bonus
    if purpose is RewardPurpose.PRACTICE_REFRESH:
        return limits.refresh_ad_bonus
    if purpose is RewardPurpose.TARGET_LEVEL_CHANGE:
        return 1
    return 0
