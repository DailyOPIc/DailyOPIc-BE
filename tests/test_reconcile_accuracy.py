"""_reconcile_with_rubrics 정확도 테스트

주의: 이 테스트는 실제 AI API 를 호출하지 않는다.
      _reconcile_with_rubrics 는 순수 함수이며, 루브릭 밴드(fixture) 를
      입력받아 등급 상한을 계산한다. 질문/답변은 "왜 이 밴드인지"의
      맥락 설명이고, 판정 근거는 오픽 공식 레벨 기준이다.

테스트 구성: expected 레벨 6개 × 10개 케이스 = 60개
  IL  : 전항목 FOUNDATION 또는 predicted=IL 로 보정 없음
  IM1 : predicted=IM1 + cap >= IM1 (IM1 은 cap 후보가 아님)
  IM2 : predicted=IM2 + cap >= IM2 (IM2 는 cap 후보가 아님)
  IM3 : max<STRONG 또는 TASK/DISCOURSE<FUNCTIONAL 으로 cap=IM3
  IH  : 전항목 STRONG + AL 예측 → IH, 또는 predicted=IH 로 보정 없음
  AL  : 전항목 STRONG+, ADVANCED 항목 존재 → AL 유지

오픽 공식 등급 기준:
  IL  : 기본 문장으로 일상적 주제 소통
  IM1 : 문장 조합 시도, 오류 빈번
  IM2 : 친숙한 주제에서 어느 정도 정확하게 소통
  IM3 : 친숙한 주제 효과적, 복잡 상황에서 한계
  IH  : 예상치 못한 복잡한 상황에서도 설명·해결 가능
  AL  : 다양한 주제에서 일관되게 정확·유창·구조적
"""
from __future__ import annotations

import dataclasses

import pytest

from app.models.api import OPIcLevel, RubricAssessment, RubricBand, RubricDimension
from app.services.ai import _reconcile_with_rubrics


# ── 헬퍼 ──────────────────────────────────────────────────────────────────

def _rubrics(band: RubricBand, **overrides: RubricBand) -> list[RubricAssessment]:
    """5개 항목에 같은 밴드를 주고, 필요한 항목만 덮어쓴다.

    overrides 키는 RubricDimension.value 문자열:
      taskFulfillment / grammar / vocabulary / discourse / fluency
    """
    return [
        RubricAssessment(
            dimension=dimension,
            band=overrides.get(dimension.value, band),
            evidence="평가 근거",
            nextAction="개선 방향",
        )
        for dimension in RubricDimension
    ]


# ── Fixture 타입 ───────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class GradingCase:
    id: str
    question: str           # 문제 상황
    answer: str             # 사용자 답변 (밴드 선택의 맥락)
    rubrics: list[RubricAssessment]
    predicted: OPIcLevel    # AI 가 내놓은 등급 (보정 전)
    expected: OPIcLevel     # 오픽 공식 기준 기반 예상 최종 등급
    criterion: str          # 적용된 오픽 공식 기준 근거


# ══════════════════════════════════════════════════════════════════════════
# expected = IL  (10개)
# 조건: 전항목 FOUNDATION → cap=IL  /  predicted=IL 이면 보정 없음
# ══════════════════════════════════════════════════════════════════════════
IL_CASES: list[GradingCase] = [
    GradingCase(
        id="IL-01 전항목 FOUNDATION + 예측 IH → IL",
        question="Describe your daily routine in detail.",
        answer="I work. Home. Eat. Sleep.",
        rubrics=_rubrics(RubricBand.FOUNDATION),
        predicted=OPIcLevel.IH,
        expected=OPIcLevel.IL,
        criterion="전항목 FOUNDATION → max==FOUNDATION → cap=IL",
    ),
    GradingCase(
        id="IL-02 전항목 FOUNDATION + 예측 AL → IL",
        question="How has globalization affected culture?",
        answer="Good. Bad. Change.",
        rubrics=_rubrics(RubricBand.FOUNDATION),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IL,
        criterion="전항목 FOUNDATION → cap=IL. AL 예측도 IL 로 하향",
    ),
    GradingCase(
        id="IL-03 전항목 FOUNDATION + 예측 IM3 → IL",
        question="Tell me about a trip you enjoyed.",
        answer="Trip. Fun. Mountain.",
        rubrics=_rubrics(RubricBand.FOUNDATION),
        predicted=OPIcLevel.IM3,
        expected=OPIcLevel.IL,
        criterion="전항목 FOUNDATION → cap=IL",
    ),
    GradingCase(
        id="IL-04 전항목 FOUNDATION + 예측 IM2 → IL",
        question="What kind of food do you like?",
        answer="Korean food. Good.",
        rubrics=_rubrics(RubricBand.FOUNDATION),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IL,
        criterion="전항목 FOUNDATION → cap=IL",
    ),
    GradingCase(
        id="IL-05 전항목 FOUNDATION + 예측 IM1 → IL",
        question="What do you do on weekends?",
        answer="Sleep. Rest.",
        rubrics=_rubrics(RubricBand.FOUNDATION),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IL,
        criterion="전항목 FOUNDATION → cap=IL. IM1 도 IL 로 하향",
    ),
    GradingCase(
        id="IL-06 전항목 FOUNDATION + 예측 IL → IL (보정 없음)",
        question="Introduce yourself.",
        answer="Name Kim.",
        rubrics=_rubrics(RubricBand.FOUNDATION),
        predicted=OPIcLevel.IL,
        expected=OPIcLevel.IL,
        criterion="전항목 FOUNDATION → cap=IL, 예측=IL → 동일, 보정 없음",
    ),
    GradingCase(
        id="IL-07 예측 IL + 전항목 DEVELOPING → IL (보정 없음)",
        question="Describe your home.",
        answer="My house is small. Two rooms.",
        rubrics=_rubrics(RubricBand.DEVELOPING),
        predicted=OPIcLevel.IL,
        expected=OPIcLevel.IL,
        criterion="보정은 상한만 적용. cap=IM3 >= IL → 보정 없음",
    ),
    GradingCase(
        id="IL-08 예측 IL + 전항목 FUNCTIONAL → IL (보정 없음)",
        question="What music do you enjoy?",
        answer="I like pop music. It is fun and energetic.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IL,
        expected=OPIcLevel.IL,
        criterion="cap=IM3 >= IL → 보정 없음. IL 은 어떤 밴드도 올리지 않음",
    ),
    GradingCase(
        id="IL-09 예측 IL + 전항목 STRONG → IL (보정 없음)",
        question="Explain your usual morning routine.",
        answer="I wake up at seven, eat breakfast, and commute to work by subway.",
        rubrics=_rubrics(RubricBand.STRONG),
        predicted=OPIcLevel.IL,
        expected=OPIcLevel.IL,
        criterion="cap=IH >= IL → 보정 없음. IL 예측은 올라가지 않음",
    ),
    GradingCase(
        id="IL-10 예측 IL + 전항목 ADVANCED → IL (보정 없음)",
        question="Discuss the role of education in society.",
        answer=(
            "Education fundamentally shapes individuals and societies by cultivating "
            "critical thinking, social mobility, and cultural transmission."
        ),
        rubrics=_rubrics(RubricBand.ADVANCED),
        predicted=OPIcLevel.IL,
        expected=OPIcLevel.IL,
        criterion="cap=AL >= IL → 보정 없음. IL 예측은 절대 올라가지 않음",
    ),
]


# ══════════════════════════════════════════════════════════════════════════
# expected = IM1  (10개)
# IM1 은 cap 후보가 아니므로 전부 보정 없음 케이스.
# 단, 전항목 FOUNDATION 이면 cap=IL 로 내려가므로 IM1 케이스가 되지 않음.
# ══════════════════════════════════════════════════════════════════════════
IM1_CASES: list[GradingCase] = [
    GradingCase(
        id="IM1-01 예측 IM1 + 전항목 DEVELOPING → IM1 (보정 없음)",
        question="What do you usually do after work?",
        answer="I go home. I watch TV. Sometimes I cook simple food.",
        rubrics=_rubrics(RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="cap=IM3, IM1 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM1-02 예측 IM1 + 전항목 FUNCTIONAL → IM1 (보정 없음)",
        question="What kind of movies do you like?",
        answer="I like action movies. They are exciting. I watch them on weekends.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="cap=IM3, IM1 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM1-03 예측 IM1 + 전항목 STRONG → IM1 (보정 없음)",
        question="Describe a place you enjoy visiting.",
        answer="I enjoy visiting the park near my home. It is peaceful and quiet.",
        rubrics=_rubrics(RubricBand.STRONG),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="cap=IH, IM1 <= IH → 보정 없음",
    ),
    GradingCase(
        id="IM1-04 예측 IM1 + GRAMMAR DEVELOPING 나머지 FUNCTIONAL → IM1",
        question="Tell me about your daily commute.",
        answer="I take subway to work. It take about thirty minute. Sometimes crowded.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL, grammar=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="max<STRONG → cap=IM3, IM1 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM1-05 예측 IM1 + TASK DEVELOPING 나머지 FUNCTIONAL → IM1",
        question="Roleplay: Call a friend and make plans for the weekend.",
        answer="Hello friend. Weekend plan? Maybe movie? OK bye.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL, taskFulfillment=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="TASK<FUNCTIONAL → cap=IM3, IM1 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM1-06 예측 IM1 + DISCOURSE DEVELOPING 나머지 STRONG → IM1",
        question="Compare two restaurants you have visited.",
        answer="Restaurant A is cheap. Restaurant B is expensive. Both have good food. I prefer A.",
        rubrics=_rubrics(RubricBand.STRONG, discourse=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="DISCOURSE<FUNCTIONAL → cap=IM3, IM1 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM1-07 예측 IM1 + GRAMMAR FUNCTIONAL 나머지 STRONG → IM1",
        question="Explain what you do to stay healthy.",
        answer="I exercise three times a week and try to eat balanced meals regularly.",
        rubrics=_rubrics(RubricBand.STRONG, grammar=RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="min<STRONG → cap=IH, IM1 <= IH → 보정 없음",
    ),
    GradingCase(
        id="IM1-08 예측 IM1 + GRAMMAR ADVANCED 나머지 STRONG → IM1",
        question="Describe a memorable childhood experience.",
        answer="When I was young, I traveled abroad for the first time with my family.",
        rubrics=_rubrics(RubricBand.STRONG, grammar=RubricBand.ADVANCED),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="min=STRONG, max=ADVANCED → cap=AL, IM1 <= AL → 보정 없음",
    ),
    GradingCase(
        id="IM1-09 예측 IM1 + TASK FOUNDATION 나머지 DEVELOPING → IM1",
        question="Roleplay: You need to cancel a reservation. Call the hotel.",
        answer="Hello hotel. Cancel room. Bye.",
        rubrics=_rubrics(RubricBand.DEVELOPING, taskFulfillment=RubricBand.FOUNDATION),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="max=DEVELOPING (not FOUNDATION) → cap=IM3, IM1 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM1-10 예측 IM1 + VOCABULARY ADVANCED 나머지 STRONG → IM1",
        question="What are your hobbies?",
        answer="I enjoy hiking and photography, which allow me to appreciate nature.",
        rubrics=_rubrics(RubricBand.STRONG, vocabulary=RubricBand.ADVANCED),
        predicted=OPIcLevel.IM1,
        expected=OPIcLevel.IM1,
        criterion="min=STRONG, max=ADVANCED → cap=AL, IM1 <= AL → 보정 없음",
    ),
]


# ══════════════════════════════════════════════════════════════════════════
# expected = IM2  (10개)
# IM2 도 cap 후보가 아니므로 전부 보정 없음 케이스.
# ══════════════════════════════════════════════════════════════════════════
IM2_CASES: list[GradingCase] = [
    GradingCase(
        id="IM2-01 예측 IM2 + 전항목 DEVELOPING → IM2 (보정 없음)",
        question="Tell me about your favorite season and why you like it.",
        answer="I like spring. Flowers bloom and weather is nice. I can go outside more often.",
        rubrics=_rubrics(RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="cap=IM3, IM2 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM2-02 예측 IM2 + 전항목 FUNCTIONAL → IM2 (보정 없음)",
        question="What kind of music do you enjoy?",
        answer="I enjoy listening to K-pop music because the melodies are catchy and uplifting.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="cap=IM3, IM2 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM2-03 예측 IM2 + 전항목 STRONG → IM2 (보정 없음)",
        question="Describe your neighborhood and what you like about it.",
        answer="I live in a quiet residential area with good public transport and local markets.",
        rubrics=_rubrics(RubricBand.STRONG),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="cap=IH, IM2 <= IH → 보정 없음",
    ),
    GradingCase(
        id="IM2-04 예측 IM2 + GRAMMAR DEVELOPING 나머지 FUNCTIONAL → IM2",
        question="What do you usually cook at home?",
        answer="I usually cook Korean food like kimchi jjigae. Is not too difficult and taste good.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL, grammar=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="max<STRONG → cap=IM3, IM2 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM2-05 예측 IM2 + TASK DEVELOPING 나머지 FUNCTIONAL → IM2",
        question="Roleplay: You want to join a gym. Ask about membership options.",
        answer="Hello. Gym price? How much? Month fee? OK I think about it.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL, taskFulfillment=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="TASK<FUNCTIONAL → cap=IM3, IM2 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM2-06 예측 IM2 + DISCOURSE DEVELOPING 나머지 STRONG → IM2",
        question="Compare two cities you have lived in or visited.",
        answer="Seoul is big. Busan has ocean. Seoul more busy. Busan more relax. Both are nice city.",
        rubrics=_rubrics(RubricBand.STRONG, discourse=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="DISCOURSE<FUNCTIONAL → cap=IM3, IM2 <= IM3 → 보정 없음",
    ),
    GradingCase(
        id="IM2-07 예측 IM2 + GRAMMAR FUNCTIONAL 나머지 STRONG → IM2",
        question="How do you usually spend your vacation?",
        answer="I usually travel domestically during vacations and explore local food and culture.",
        rubrics=_rubrics(RubricBand.STRONG, grammar=RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="min<STRONG → cap=IH, IM2 <= IH → 보정 없음",
    ),
    GradingCase(
        id="IM2-08 예측 IM2 + GRAMMAR ADVANCED 나머지 STRONG → IM2",
        question="Describe a book or movie that influenced you.",
        answer="A book that deeply influenced me was about resilience in the face of adversity.",
        rubrics=_rubrics(RubricBand.STRONG, grammar=RubricBand.ADVANCED),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="min=STRONG, max=ADVANCED → cap=AL, IM2 <= AL → 보정 없음",
    ),
    GradingCase(
        id="IM2-09 예측 IM2 + TASK FUNCTIONAL 나머지 STRONG → IM2",
        question="Explain how you manage work and personal life.",
        answer="I set clear priorities and schedule personal time to avoid burnout.",
        rubrics=_rubrics(RubricBand.STRONG, taskFulfillment=RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="min<STRONG → cap=IH, IM2 <= IH → 보정 없음",
    ),
    GradingCase(
        id="IM2-10 예측 IM2 + TASK FOUNDATION 나머지 DEVELOPING → IM2",
        question="Roleplay: You need directions to the train station.",
        answer="Excuse me. Train station? Where? Which way?",
        rubrics=_rubrics(RubricBand.DEVELOPING, taskFulfillment=RubricBand.FOUNDATION),
        predicted=OPIcLevel.IM2,
        expected=OPIcLevel.IM2,
        criterion="max=DEVELOPING → cap=IM3, IM2 <= IM3 → 보정 없음",
    ),
]


# ══════════════════════════════════════════════════════════════════════════
# expected = IM3  (10개)
# 조건: max<STRONG, TASK<FUNCTIONAL, DISCOURSE<FUNCTIONAL 으로 cap=IM3
# ══════════════════════════════════════════════════════════════════════════
IM3_CASES: list[GradingCase] = [
    GradingCase(
        id="IM3-01 전항목 DEVELOPING + 예측 IH → IM3",
        question="Tell me about a memorable trip you took.",
        answer="I go trip last year. Very fun. I see mountain. Good weather. I enjoy very much.",
        rubrics=_rubrics(RubricBand.DEVELOPING),
        predicted=OPIcLevel.IH,
        expected=OPIcLevel.IM3,
        criterion="전항목 DEVELOPING → max<STRONG → IM3 상한. IH=복잡상황 해결 불가",
    ),
    GradingCase(
        id="IM3-02 전항목 DEVELOPING + 예측 AL → IM3",
        question="Analyze the impact of social media on society.",
        answer="Social media is popular now. People use it every day. Good and bad. I like it.",
        rubrics=_rubrics(RubricBand.DEVELOPING),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IM3,
        criterion="전항목 DEVELOPING → max<STRONG → IM3 상한",
    ),
    GradingCase(
        id="IM3-03 전항목 FUNCTIONAL + 예측 IH → IM3",
        question="Compare your lifestyle now with five years ago.",
        answer="Five years ago I was student. Now I work. Life is more busy but I earn money and have more freedom.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IH,
        expected=OPIcLevel.IM3,
        criterion="전항목 FUNCTIONAL → max<STRONG → IM3 상한",
    ),
    GradingCase(
        id="IM3-04 전항목 FUNCTIONAL + 예측 AL → IM3",
        question="Discuss the pros and cons of living in a big city.",
        answer="Big city has many jobs and entertainment. But it is expensive and crowded. I prefer it over rural.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IM3,
        criterion="전항목 FUNCTIONAL → max<STRONG → IM3 상한",
    ),
    GradingCase(
        id="IM3-05 TASK DEVELOPING 나머지 STRONG + 예측 IH → IM3",
        question="Roleplay: There is a problem with your internet service. Call to resolve it.",
        answer="Hello. Internet problem. Not work. Fix please.",
        rubrics=_rubrics(RubricBand.STRONG, taskFulfillment=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IH,
        expected=OPIcLevel.IM3,
        criterion="TASK_FULFILLMENT<FUNCTIONAL → 과제 수행 미달 → IM3 상한",
    ),
    GradingCase(
        id="IM3-06 TASK DEVELOPING 나머지 STRONG + 예측 AL → IM3",
        question="Roleplay: Negotiate a salary increase with your manager.",
        answer="Boss, more money please. I work hard. Raise salary.",
        rubrics=_rubrics(RubricBand.STRONG, taskFulfillment=RubricBand.DEVELOPING),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IM3,
        criterion="TASK_FULFILLMENT<FUNCTIONAL → cap=IM3",
    ),
    GradingCase(
        id="IM3-07 DISCOURSE DEVELOPING 나머지 STRONG + 예측 IH → IM3",
        question="Explain the advantages of learning a second language.",
        answer="Second language good. Job chance more. Friend more. Travel easy. Culture understand.",
        rubrics=_rubrics(RubricBand.STRONG, discourse=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IH,
        expected=OPIcLevel.IM3,
        criterion="DISCOURSE<FUNCTIONAL → 담화구성 미달 → IM3 상한",
    ),
    GradingCase(
        id="IM3-08 DISCOURSE DEVELOPING 나머지 STRONG + 예측 AL → IM3",
        question="Analyze how remote work changed company culture.",
        answer="Remote work different. Team less meet. Communication hard. But save time. Flexible.",
        rubrics=_rubrics(RubricBand.STRONG, discourse=RubricBand.DEVELOPING),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IM3,
        criterion="DISCOURSE<FUNCTIONAL → cap=IM3",
    ),
    GradingCase(
        id="IM3-09 예측 IM3 + 전항목 FUNCTIONAL → IM3 (보정 없음)",
        question="What are some challenges you face at your workplace?",
        answer="I sometimes struggle with tight deadlines and communication gaps between departments.",
        rubrics=_rubrics(RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IM3,
        expected=OPIcLevel.IM3,
        criterion="cap=IM3, 예측=IM3 → 동일, 보정 없음",
    ),
    GradingCase(
        id="IM3-10 예측 IM3 + TASK DEVELOPING 나머지 STRONG → IM3 (보정 없음)",
        question="Roleplay: You arrive at the wrong hotel. Explain and ask for help.",
        answer="Hello. Wrong hotel. My reservation is other place. Help me please.",
        rubrics=_rubrics(RubricBand.STRONG, taskFulfillment=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IM3,
        expected=OPIcLevel.IM3,
        criterion="cap=IM3, 예측=IM3 → 동일, 보정 없음",
    ),
]


# ══════════════════════════════════════════════════════════════════════════
# expected = IH  (10개)
# 조건: 전항목 STRONG + 예측 AL → IH  /  예측 IH + cap>=IH → 보정 없음
# ══════════════════════════════════════════════════════════════════════════
IH_CASES: list[GradingCase] = [
    GradingCase(
        id="IH-01 전항목 STRONG + 예측 AL → IH",
        question="How has technology transformed modern workplaces?",
        answer=(
            "Technology has fundamentally reshaped workplaces by enabling remote collaboration, "
            "automating repetitive tasks, and accelerating decision-making through data analytics. "
            "Companies that adapt effectively gain competitive advantages, though the transition "
            "requires significant investment in employee training and infrastructure."
        ),
        rubrics=_rubrics(RubricBand.STRONG),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IH,
        criterion="전항목 STRONG, ADVANCED 없음 → max<ADVANCED → cap=IH. AL=일관된 고급 통제",
    ),
    GradingCase(
        id="IH-02 전항목 STRONG + 예측 IH → IH (보정 없음)",
        question="Describe a challenge you overcame at work.",
        answer=(
            "When our project deadline was moved up, I reorganized the team's tasks, "
            "communicated priorities clearly, and worked extra hours to deliver on time. "
            "The experience strengthened my project management skills."
        ),
        rubrics=_rubrics(RubricBand.STRONG),
        predicted=OPIcLevel.IH,
        expected=OPIcLevel.IH,
        criterion="전항목 STRONG → cap=IH, 예측=IH → 보정 없음",
    ),
    GradingCase(
        id="IH-03 GRAMMAR FUNCTIONAL 나머지 STRONG + 예측 AL → IH",
        question="Discuss the ethical implications of AI in healthcare.",
        answer=(
            "AI in healthcare offer enormous potential for early diagnosis and personalized treatment. "
            "However, accountability for errors remain unclear, and data privacy concerns must be addressed. "
            "Strong regulatory frameworks should evolve alongside these capabilities."
        ),
        rubrics=_rubrics(RubricBand.STRONG, grammar=RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IH,
        criterion="GRAMMAR FUNCTIONAL → min<STRONG → cap=IH",
    ),
    GradingCase(
        id="IH-04 GRAMMAR FUNCTIONAL 나머지 STRONG + 예측 IH → IH (보정 없음)",
        question="Explain advantages and disadvantages of working from home.",
        answer=(
            "Working from home save commute time and allow flexible scheduling. "
            "However, it reduce team cohesion and blur work-life boundaries. "
            "Effectiveness depend on individual discipline and job requirements."
        ),
        rubrics=_rubrics(RubricBand.STRONG, grammar=RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.IH,
        expected=OPIcLevel.IH,
        criterion="min<STRONG → cap=IH, 예측=IH → 보정 없음",
    ),
    GradingCase(
        id="IH-05 VOCABULARY DEVELOPING 나머지 STRONG + 예측 AL → IH",
        question="Analyze the long-term effects of urbanization.",
        answer=(
            "City growth make more job and good thing. People move to city for better life. "
            "But traffic is bad and house price go up a lot. Environment also have big problem."
        ),
        rubrics=_rubrics(RubricBand.STRONG, vocabulary=RubricBand.DEVELOPING),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IH,
        criterion="VOCABULARY DEVELOPING → min<STRONG → cap=IH",
    ),
    GradingCase(
        id="IH-06 VOCABULARY DEVELOPING 나머지 STRONG + 예측 IH → IH (보정 없음)",
        question="Compare two different career paths you considered.",
        answer=(
            "I thought about becoming a teacher or an engineer. Teaching let you help student. "
            "Engineering give more money but more stress. I choose engineering for stability."
        ),
        rubrics=_rubrics(RubricBand.STRONG, vocabulary=RubricBand.DEVELOPING),
        predicted=OPIcLevel.IH,
        expected=OPIcLevel.IH,
        criterion="min<STRONG → cap=IH, 예측=IH → 보정 없음",
    ),
    GradingCase(
        id="IH-07 FLUENCY DEVELOPING 나머지 STRONG + 예측 AL → IH",
        question="Discuss how climate change affects daily life.",
        answer=(
            "Climate... change is... um... affecting many aspects of... daily life today. "
            "Extreme weather events are... becoming more common and unpredictable globally."
        ),
        rubrics=_rubrics(RubricBand.STRONG, fluency=RubricBand.DEVELOPING),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IH,
        criterion="FLUENCY DEVELOPING → min<STRONG → cap=IH",
    ),
    GradingCase(
        id="IH-08 TASK FUNCTIONAL 나머지 STRONG + 예측 AL → IH",
        question="Roleplay: Negotiate a contract with a new vendor.",
        answer=(
            "I would like to discuss the terms. The price seems reasonable, "
            "but I need better delivery schedule and warranty terms for our agreement."
        ),
        rubrics=_rubrics(RubricBand.STRONG, taskFulfillment=RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IH,
        criterion="TASK=FUNCTIONAL → min<STRONG → cap=IH",
    ),
    GradingCase(
        id="IH-09 GRAMMAR DEVELOPING 나머지 STRONG + 예측 AL → IH",
        question="How do cultural differences affect international business?",
        answer=(
            "Cultural difference make communication hard in global business. "
            "Every country have different way to negotiate and build relationship. "
            "Company must train their employee to understand local custom."
        ),
        rubrics=_rubrics(RubricBand.STRONG, grammar=RubricBand.DEVELOPING),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IH,
        criterion="GRAMMAR DEVELOPING → min<STRONG → cap=IH",
    ),
    GradingCase(
        id="IH-10 DISCOURSE FUNCTIONAL 나머지 STRONG + 예측 AL → IH",
        question="Explain why lifelong learning is important in today's world.",
        answer=(
            "Lifelong learning helps people stay relevant in rapidly changing industries. "
            "New skills allow career transitions. Continuous learning also improves adaptability. "
            "It provides intellectual satisfaction throughout life."
        ),
        rubrics=_rubrics(RubricBand.STRONG, discourse=RubricBand.FUNCTIONAL),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.IH,
        criterion="DISCOURSE=FUNCTIONAL → min<STRONG → cap=IH",
    ),
]


# ══════════════════════════════════════════════════════════════════════════
# expected = AL  (10개)
# 조건: 전항목 STRONG 이상, 최소 하나 ADVANCED → cap=AL 유지
# ══════════════════════════════════════════════════════════════════════════
AL_CASES: list[GradingCase] = [
    GradingCase(
        id="AL-01 전항목 STRONG + GRAMMAR ADVANCED + 예측 AL → AL",
        question="Analyze how globalization has reshaped cultural identity.",
        answer=(
            "Globalization presents a fundamental paradox: while enabling unprecedented "
            "cross-cultural exchange, it simultaneously risks homogenizing distinct identities. "
            "This tension demands nuanced policies that celebrate diversity while fostering "
            "mutual understanding across linguistic and geographic boundaries."
        ),
        rubrics=_rubrics(RubricBand.STRONG, grammar=RubricBand.ADVANCED),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → 규칙 5 미적용 → cap=AL 유지",
    ),
    GradingCase(
        id="AL-02 전항목 STRONG + DISCOURSE ADVANCED + 예측 AL → AL",
        question="Discuss the long-term societal impact of artificial intelligence.",
        answer=(
            "Artificial intelligence is poised to fundamentally restructure labor markets, "
            "governance structures, and interpersonal relationships over the coming decades. "
            "While AI democratizes access to expertise, it simultaneously concentrates power "
            "in the hands of those who control the underlying algorithms and data infrastructure."
        ),
        rubrics=_rubrics(RubricBand.STRONG, discourse=RubricBand.ADVANCED),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → cap=AL 유지",
    ),
    GradingCase(
        id="AL-03 전항목 STRONG + TASK ADVANCED + 예측 AL → AL",
        question="Roleplay: Mediate a dispute between two departments in your company.",
        answer=(
            "I understand both teams have legitimate concerns. Marketing needs faster turnaround "
            "times, while development requires adequate testing periods to ensure quality. "
            "I propose a phased release schedule that accommodates both constraints, with "
            "clear milestones and joint accountability metrics to track progress."
        ),
        rubrics=_rubrics(RubricBand.STRONG, taskFulfillment=RubricBand.ADVANCED),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → cap=AL 유지",
    ),
    GradingCase(
        id="AL-04 전항목 STRONG + VOCABULARY ADVANCED + 예측 AL → AL",
        question="Compare the philosophical underpinnings of Eastern and Western education.",
        answer=(
            "Eastern educational philosophy traditionally emphasizes collective harmony, "
            "rote memorization, and respect for authority, while Western approaches privilege "
            "critical inquiry, individualism, and creative problem-solving. Neither paradigm "
            "is universally superior; the optimal approach synthesizes both traditions "
            "contextually to cultivate well-rounded, adaptable graduates."
        ),
        rubrics=_rubrics(RubricBand.STRONG, vocabulary=RubricBand.ADVANCED),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → cap=AL 유지",
    ),
    GradingCase(
        id="AL-05 전항목 STRONG + FLUENCY ADVANCED + 예측 AL → AL",
        question="Discuss the ethical dimensions of genetic engineering in medicine.",
        answer=(
            "Genetic engineering in medicine occupies a complex ethical landscape where "
            "therapeutic potential intersects with profound questions about human identity "
            "and equity. While somatic gene therapy holds promise for hereditary diseases, "
            "germline modifications raise concerns about consent, irreversibility, "
            "and exacerbating genetic inequality across socioeconomic strata."
        ),
        rubrics=_rubrics(RubricBand.STRONG, fluency=RubricBand.ADVANCED),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → cap=AL 유지",
    ),
    GradingCase(
        id="AL-06 전항목 ADVANCED + 예측 AL → AL",
        question="Analyze the relationship between economic inequality and social mobility.",
        answer=(
            "Economic inequality and social mobility exist in an inverse relationship that "
            "undermines democratic ideals when wealth concentration becomes self-perpetuating. "
            "Structural barriers including differential educational quality, social capital "
            "networks, and intergenerational wealth transfer create systemic disadvantages "
            "that cannot be overcome through individual effort alone."
        ),
        rubrics=_rubrics(RubricBand.ADVANCED),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="전항목 ADVANCED → min=ADVANCED, max=ADVANCED → cap=AL 유지",
    ),
    GradingCase(
        id="AL-07 GRAMMAR+DISCOURSE ADVANCED 나머지 STRONG + 예측 AL → AL",
        question="Evaluate the effectiveness of international climate agreements.",
        answer=(
            "International climate agreements like the Paris Accord represent necessary "
            "but insufficient responses to the climate crisis. While establishing shared "
            "targets creates political accountability, the absence of enforcement mechanisms "
            "allows high-emission nations to prioritize short-term economic interests "
            "over long-term planetary stability."
        ),
        rubrics=_rubrics(
            RubricBand.STRONG,
            grammar=RubricBand.ADVANCED,
            discourse=RubricBand.ADVANCED,
        ),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → cap=AL 유지",
    ),
    GradingCase(
        id="AL-08 TASK+VOCABULARY ADVANCED 나머지 STRONG + 예측 AL → AL",
        question="Discuss how urbanization affects mental health and community cohesion.",
        answer=(
            "Rapid urbanization creates paradoxical effects on mental well-being: while "
            "cities offer economic opportunity and cultural stimulation, they simultaneously "
            "generate social fragmentation, noise pollution, and reduced access to natural "
            "environments. Longitudinal studies consistently correlate high urban density "
            "with elevated rates of anxiety and depression."
        ),
        rubrics=_rubrics(
            RubricBand.STRONG,
            taskFulfillment=RubricBand.ADVANCED,
            vocabulary=RubricBand.ADVANCED,
        ),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → cap=AL 유지",
    ),
    GradingCase(
        id="AL-09 FLUENCY+GRAMMAR ADVANCED 나머지 STRONG + 예측 AL → AL",
        question="Analyze the geopolitical implications of technological decoupling.",
        answer=(
            "Technological decoupling between major powers represents a structural shift "
            "in the global order, fragmenting previously integrated supply chains and "
            "creating competing technological ecosystems. This bifurcation compels smaller "
            "nations to navigate alignment choices with significant economic and security "
            "implications, effectively reordering the architecture of global trade."
        ),
        rubrics=_rubrics(
            RubricBand.STRONG,
            fluency=RubricBand.ADVANCED,
            grammar=RubricBand.ADVANCED,
        ),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → cap=AL 유지",
    ),
    GradingCase(
        id="AL-10 TASK+DISCOURSE+VOCABULARY ADVANCED 나머지 STRONG + 예측 AL → AL",
        question="Synthesize the relationship between democracy and economic development.",
        answer=(
            "The relationship between democracy and economic development resists simple "
            "causal characterization. While democratic institutions foster innovation "
            "through protected property rights and rule of law, authoritarian systems "
            "have demonstrated capacity for rapid capital accumulation and infrastructure "
            "development. The critical variable appears to be institutional quality "
            "and consistency rather than regime type per se."
        ),
        rubrics=_rubrics(
            RubricBand.STRONG,
            taskFulfillment=RubricBand.ADVANCED,
            discourse=RubricBand.ADVANCED,
            vocabulary=RubricBand.ADVANCED,
        ),
        predicted=OPIcLevel.AL,
        expected=OPIcLevel.AL,
        criterion="min=STRONG, max=ADVANCED → cap=AL 유지",
    ),
]


# ── 전체 케이스 ─────────────────────────────────────────────────────────────

CASES: list[GradingCase] = (
    IL_CASES + IM1_CASES + IM2_CASES + IM3_CASES + IH_CASES + AL_CASES
)


# ── 개별 케이스 테스트 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_reconcile_matches_opic_criterion(case: GradingCase) -> None:
    """각 케이스의 보정 결과가 오픽 공식 기준과 일치해야 한다."""
    actual, _ = _reconcile_with_rubrics(case.predicted, case.rubrics)
    assert actual is case.expected, (
        f"\n질문  : {case.question}"
        f"\n답변  : {case.answer[:120]}"
        f"\n기준  : {case.criterion}"
        f"\n예측→기대→실제 : {case.predicted.value} → {case.expected.value} → {actual.value}"
    )


# ── 정답률 집계 ────────────────────────────────────────────────────────────

def test_accuracy_summary() -> None:
    """레벨별·전체 정답률을 출력하고 100% 를 요구한다."""
    by_level: dict[str, tuple[int, int]] = {}  # level → (correct, total)
    wrong: list[str] = []

    for case in CASES:
        actual, _ = _reconcile_with_rubrics(case.predicted, case.rubrics)
        label = case.expected.value
        correct_count, total_count = by_level.get(label, (0, 0))
        if actual is case.expected:
            by_level[label] = (correct_count + 1, total_count + 1)
        else:
            by_level[label] = (correct_count, total_count + 1)
            wrong.append(
                f"  [{case.id}]"
                f"  예측={case.predicted.value}"
                f"  기대={case.expected.value}"
                f"  실제={actual.value}"
                f"\n    → {case.criterion}"
            )

    total_correct = sum(c for c, _ in by_level.values())
    total_all = sum(t for _, t in by_level.values())

    lines = [f"\n{'─'*64}", "레벨별 정답률:"]
    for level_val in ["IL", "IM1", "IM2", "IM3", "IH", "AL"]:
        c, t = by_level.get(level_val, (0, 0))
        bar = "█" * c + "░" * (t - c)
        lines.append(f"  {level_val:4s} [{bar}] {c}/{t}")
    lines.append(f"\n전체 정답률: {total_correct}/{total_all} = {total_correct/total_all:.0%}")
    if wrong:
        lines.append("\n오답:")
        lines.extend(wrong)
    lines.append(f"{'─'*64}")

    print("\n".join(lines))
    assert not wrong, f"\n오답 {len(wrong)}건 발생\n" + "\n".join(wrong)
