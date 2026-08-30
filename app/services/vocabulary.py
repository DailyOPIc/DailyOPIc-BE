"""AI 맞춤 단어장(P14.2) — 스키마 · 프롬프트 · 중복 제거 · 구성 검증.

사용자 조작 1회 = 데일리 토큰 1개 = 30개(단어 10 / 자주 쓰는 표현 10 / 답변 패턴 10).
개수 · 구성 · 모델 · 재시도 상한은 **서버가 소유한다**. 클라이언트가 요청으로
바꿀 수 있는 것은 주제 · 목표 등급 · 제외 후보뿐이다.

제공자 호출을 여러 번 하더라도(부족분 보충) 그것은 내부 사정이지 과금 단위가
아니다. 과금 단위는 라우트가 잡는 데일리 토큰 1개뿐이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.api import (
    OPIcLevel,
    VocabularyGenerationPurpose,
    VocabularyItemType,
    VocabularyTopic,
    VocabularyUsageAssessment,
    VocabularyUsageRole,
)


SET_SIZE = 30
PER_TYPE = 10
# 쓰임새별 구성. 개수는 여기서만 정해진다 — 요청이 개수를 직접 정하지 못한다.
#   custom_set : 예전 계약 그대로 30개(10/10/10). 필드 없는 요청이 여기로 온다.
#   today_extra: 오늘의 단어 20개와 같은 크기의 20개(7/7/6). 시드 카탈로그의
#                실제 구성(단어 48 · 표현 45 · 패턴 35 = 128)을 20개로 줄인 비율이라
#                기본 20개와 추가 20개가 같은 종류로 느껴진다.
COMPOSITIONS: dict[VocabularyGenerationPurpose, dict[VocabularyItemType, int]] = {
    VocabularyGenerationPurpose.CUSTOM_SET: {
        VocabularyItemType.WORD: PER_TYPE,
        VocabularyItemType.PHRASE: PER_TYPE,
        VocabularyItemType.PATTERN: PER_TYPE,
    },
    VocabularyGenerationPurpose.TODAY_EXTRA: {
        VocabularyItemType.WORD: 7,
        VocabularyItemType.PHRASE: 7,
        VocabularyItemType.PATTERN: 6,
    },
}


def composition(
    purpose: VocabularyGenerationPurpose,
) -> dict[VocabularyItemType, int]:
    return COMPOSITIONS[purpose]


def set_size(purpose: VocabularyGenerationPurpose) -> int:
    return sum(COMPOSITIONS[purpose].values())
# 종류별 여유분. 중복·정규화 충돌을 걸러내고도 10개가 남게 한 번에 더 받는다.
SURPLUS_PER_TYPE = 4
# 제공자 호출 상한(최초 1회 + 보충 1회). 무한 재시도 루프를 만들지 않는다.
MAX_PROVIDER_ATTEMPTS = 2
# 프롬프트에 넣는 제외 목록 상한. 시드 카탈로그 전체를 프롬프트에 넣지 않는다.
MAX_PROMPT_EXCLUSIONS = 200

_WHITESPACE = re.compile(r"\s+")
_TRIVIAL_PUNCTUATION = re.compile(r"[.,!?;:\"'’“”()\[\]]")


def normalize_term(term: str) -> str:
    """중복 판정용 정규형: 대소문자 · 앞뒤 공백 · 중복 공백 · 사소한 문장부호 무시.

    의미 임베딩까지 가지 않는다(이 단계에서는 과한 설계다). 표기만 다른 같은
    표현을 걸러내는 것이 목적이다.
    """
    stripped = _TRIVIAL_PUNCTUATION.sub("", term)
    return _WHITESPACE.sub(" ", stripped).strip().lower()


class VocabularyDraft(BaseModel):
    """제공자가 채우는 슬롯. id · 주제 · 권장 등급 · source는 서버가 붙인다 —
    모델이 정할 수 있게 두면 잘못된 주제·등급이 저장될 수 있다."""

    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1, max_length=80)
    type: VocabularyItemType
    meaning_ko: str = Field(alias="meaningKo", min_length=1, max_length=120)
    example_en: str = Field(alias="exampleEn", min_length=8, max_length=240)
    example_ko: str = Field(alias="exampleKo", min_length=1, max_length=240)
    collocations: list[str] = Field(default_factory=list, max_length=3)
    usage_roles: list[VocabularyUsageRole] = Field(
        alias="usageRoles", min_length=1, max_length=3
    )


class VocabularyDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[VocabularyDraft]


@dataclass(slots=True)
class VocabularySelection:
    """종류별 정원이 찰 때까지 모으는 그릇. 중복은 여기서 걷어낸다."""

    excluded: set[str]
    #: 종류별 정원. 쓰임새가 정한 구성이 그대로 들어온다.
    limits: dict[VocabularyItemType, int]
    picked: dict[VocabularyItemType, list[VocabularyDraft]] = field(
        default_factory=lambda: {item: [] for item in VocabularyItemType}
    )

    @classmethod
    def create(
        cls,
        exclude_terms: list[str],
        limits: dict[VocabularyItemType, int] | None = None,
    ) -> "VocabularySelection":
        return cls(
            excluded={normalize_term(term) for term in exclude_terms} - {""},
            limits=dict(limits or COMPOSITIONS[VocabularyGenerationPurpose.CUSTOM_SET]),
        )

    def add(self, drafts: list[VocabularyDraft]) -> int:
        """쓸 만한 것만 담고 실제로 담긴 개수를 돌려준다."""
        accepted = 0
        for draft in drafts:
            key = normalize_term(draft.term)
            if not key or key in self.excluded:
                continue
            if not draft.meaning_ko.strip() or not draft.example_en.strip():
                continue
            bucket = self.picked[draft.type]
            if len(bucket) >= self.limits[draft.type]:
                continue
            self.excluded.add(key)
            bucket.append(draft)
            accepted += 1
        return accepted

    def needed(self) -> dict[VocabularyItemType, int]:
        return {
            item: self.limits[item] - len(bucket)
            for item, bucket in self.picked.items()
            if len(bucket) < self.limits[item]
        }

    @property
    def is_complete(self) -> bool:
        return not self.needed()

    def drafts(self) -> list[VocabularyDraft]:
        """단어 → 표현 → 패턴 순서로 평탄화."""
        return [draft for item in VocabularyItemType for draft in self.picked[item]]

    def picked_terms(self) -> list[str]:
        return [draft.term for draft in self.drafts()]


def instructions(target_level: OPIcLevel) -> str:
    return (
        "You write OPIc speaking vocabulary for Korean learners. "
        "Every item must be something a learner can actually say in an OPIc answer "
        f"at around the {target_level.value} level.\n"
        "- word: a single natural everyday word (e.g. crowded, cozy, memorable).\n"
        "- phrase: a short natural collocation or chunk "
        "(e.g. get crowded, spend time with, grab a cup of coffee).\n"
        "- pattern: a reusable answer frame with a blank part "
        "(e.g. one of my favorite places, what I like most about ... is ..., "
        "whenever I need a break, I ...).\n"
        "Rules: everyday spoken English only; no literary or academic vocabulary; "
        "no strange idioms; no textbook-only sentences; no heavy slang; "
        "nothing offensive. Each item needs a short natural Korean meaning and one "
        "natural English example sentence a learner could speak in an OPIc answer, "
        "plus its Korean translation. Do not repeat an item, and do not return "
        "singular/plural or punctuation variants of the same item. "
        "Do not return a list of isolated dictionary words: the phrases and patterns "
        "must be things that connect a real answer together."
    )


def input_text(
    *,
    topic: VocabularyTopic,
    target_level: OPIcLevel,
    needed: dict[VocabularyItemType, int],
    exclude_terms: list[str],
    limits: dict[VocabularyItemType, int] | None = None,
) -> str:
    caps = limits or COMPOSITIONS[VocabularyGenerationPurpose.CUSTOM_SET]
    requested = {
        item.value: min(count + SURPLUS_PER_TYPE, caps[item] + SURPLUS_PER_TYPE)
        for item, count in needed.items()
    }
    lines = [
        f"Topic: {topic.value} ({topic.label})",
        f"Target OPIc level: {target_level.value}",
        "Return exactly this many entries of each type: "
        + ", ".join(f"{key}={value}" for key, value in requested.items()),
        "usageRoles must come from the allowed enum values.",
    ]
    trimmed = exclude_terms[:MAX_PROMPT_EXCLUSIONS]
    if trimmed:
        lines.append(
            "Do not return any of these (already covered): " + ", ".join(trimmed)
        )
    return "\n".join(lines)


# --- MOCK_AI 전용 ------------------------------------------------------------
# 로컬/테스트에서만 쓰는 결정적 대체 데이터다(운영은 MOCK_AI=false 강제).
# 예문은 템플릿으로 만든다 — 사용자에게 나가지 않는 값이라 자연스러움보다
# "형식이 항상 유효하다"가 중요하다.

_MOCK_WORDS = [
    ("crowded", "붐비는"),
    ("cozy", "아늑한"),
    ("memorable", "기억에 남는"),
    ("relaxing", "편안한"),
    ("convenient", "편리한"),
    ("affordable", "가격이 적당한"),
    ("lively", "활기찬"),
    ("spacious", "널찍한"),
    ("refreshing", "상쾌한"),
    ("peaceful", "평화로운"),
    ("popular", "인기 있는"),
    ("comfortable", "편안한"),
    ("impressive", "인상적인"),
    ("familiar", "익숙한"),
]

_MOCK_PHRASES = [
    ("get crowded", "붐비게 되다"),
    ("spend time with", "~와 시간을 보내다"),
    ("grab a cup of coffee", "커피 한잔 하다"),
    ("hang out with", "~와 어울려 놀다"),
    ("take a break", "잠깐 쉬다"),
    ("look forward to", "~을 기대하다"),
    ("end up doing", "결국 ~하게 되다"),
    ("come up with", "~을 떠올리다"),
    ("get used to", "~에 익숙해지다"),
    ("keep in touch", "연락하며 지내다"),
    ("run into", "우연히 마주치다"),
    ("drop by", "잠깐 들르다"),
    ("work out", "운동하다"),
    ("pick up", "~을 사 오다"),
]

_MOCK_PATTERNS = [
    ("one of my favorite places", "내가 가장 좋아하는 장소 중 하나"),
    ("what I like most about ... is ...", "~에서 가장 마음에 드는 건 ~이다"),
    ("whenever I need a break, I ...", "쉬고 싶을 때마다 나는 ~한다"),
    ("if I had to choose, I would ...", "굳이 고르자면 나는 ~하겠다"),
    ("the best part is that ...", "가장 좋은 점은 ~라는 것이다"),
    ("I usually ... after work", "나는 보통 퇴근 후에 ~한다"),
    ("that's why I ...", "그래서 나는 ~한다"),
    ("not only ... but also ...", "~뿐만 아니라 ~도"),
    ("ever since I started ...", "~을 시작한 이후로"),
    ("compared to ..., it's ...", "~와 비교하면 그것은 ~하다"),
    ("I'm the kind of person who ...", "나는 ~하는 편인 사람이다"),
    ("every now and then, I ...", "가끔씩 나는 ~한다"),
    ("to be honest, I ...", "솔직히 말하면 나는 ~한다"),
    ("the thing I remember most is ...", "가장 기억에 남는 것은 ~이다"),
]

_MOCK_SOURCE = {
    VocabularyItemType.WORD: _MOCK_WORDS,
    VocabularyItemType.PHRASE: _MOCK_PHRASES,
    VocabularyItemType.PATTERN: _MOCK_PATTERNS,
}

_MOCK_EXAMPLE = {
    VocabularyItemType.WORD: "The place I usually go to is really {term}.",
    VocabularyItemType.PHRASE: "I often {term} when I have some free time.",
    VocabularyItemType.PATTERN: "{term} — that is how I would put it.",
}


def mock_drafts(
    *,
    topic: VocabularyTopic,
    needed: dict[VocabularyItemType, int],
) -> list[VocabularyDraft]:
    drafts: list[VocabularyDraft] = []
    for item, count in needed.items():
        pool = _MOCK_SOURCE[item]
        for term, meaning in pool[: min(count + SURPLUS_PER_TYPE, len(pool))]:
            drafts.append(
                VocabularyDraft(
                    term=term,
                    type=item,
                    meaningKo=meaning,
                    exampleEn=_MOCK_EXAMPLE[item].format(term=term),
                    exampleKo=f"{topic.label} 이야기를 할 때 이렇게 말할 수 있어요.",
                    collocations=[],
                    usageRoles=[VocabularyUsageRole.DESCRIPTION],
                )
            )
    return drafts


# --- AI 말하기 코치(P14.3) ----------------------------------------------------
# 단어를 배운 뒤 그 표현으로 직접 말한 답변을 코칭한다. 녹음 · 전사 · 저장된 결과
# 재열람은 공짜고, **새 코칭 분석 1회 = 데일리 토큰 1개**다(라우트가 잡는다).
#
# 데일리 분석과 달리 등급 · 점수 · 루브릭을 내지 않는다. 이 엔드포인트가 답하는
# 질문은 하나다: "내가 이 표현을 실제 OPIc 답변에서 제대로 쓴 걸까?"

# 제공자 호출 상한(최초 1회 + 형식 불량 재시도 1회). 내부 재시도가 몇 번이든
# 사용자 조작 1회는 여전히 토큰 1개다.
COACH_MAX_PROVIDER_ATTEMPTS = 2
# 함께 써볼 표현 개수. 여기서 30개짜리 단어장을 또 만들지 않는다.
COACH_MIN_RELATED = 2
COACH_MAX_RELATED = 4


class VocabularyCoachDraft(BaseModel):
    """제공자가 채우는 코칭 결과. id · 대상 표현 · 전사 · 시각은 서버가 붙인다.

    빈칸·공백만 있는 값을 통과시키지 않는다 — 형식이 깨진 출력을 사용자에게
    보여주느니 실패로 처리하고(라우트가) 환불한다.
    """

    model_config = ConfigDict(extra="forbid")

    usage_assessment: VocabularyUsageAssessment = Field(alias="usageAssessment")
    usage_feedback_ko: str = Field(alias="usageFeedbackKo", min_length=2, max_length=200)
    natural_correction_en: str = Field(
        alias="naturalCorrectionEn", min_length=4, max_length=300
    )
    natural_correction_ko: str = Field(
        alias="naturalCorrectionKo", min_length=1, max_length=300
    )
    expanded_answer_en: str = Field(alias="expandedAnswerEn", min_length=8, max_length=500)
    expanded_answer_ko: str = Field(alias="expandedAnswerKo", min_length=1, max_length=500)
    related_expressions: list[str] = Field(
        alias="relatedExpressions",
        min_length=COACH_MIN_RELATED,
        max_length=COACH_MAX_RELATED,
    )

    @field_validator(
        "usage_feedback_ko",
        "natural_correction_en",
        "natural_correction_ko",
        "expanded_answer_en",
        "expanded_answer_ko",
    )
    @classmethod
    def require_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("related_expressions")
    @classmethod
    def clean_related(cls, value: list[str]) -> list[str]:
        """공백·중복을 걷어낸 뒤에도 2개 이상 남아야 한다. 한 항목의 길이도 제한한다."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = _WHITESPACE.sub(" ", item).strip()
            key = text.lower()
            if not text or len(text) > 60 or key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        if len(cleaned) < COACH_MIN_RELATED:
            raise ValueError(
                f"needs at least {COACH_MIN_RELATED} usable related expressions"
            )
        return cleaned[:COACH_MAX_RELATED]


def coach_instructions(target_level: OPIcLevel) -> str:
    return (
        "You are an OPIc speaking coach for Korean learners. The learner just "
        "practised ONE target expression out loud and you are given the transcript "
        "of what they actually said. Coach that one expression — nothing else.\n"
        f"Aim the suggested English at around the {target_level.value} level: "
        "natural everyday spoken English, not literary or academic writing.\n"
        "Judge usageAssessment by MEANING AND CONTEXT, not string matching:\n"
        "- appropriate: the target expression is used, and it fits the context.\n"
        "- needsPolish: the target expression appears but the wording, position or "
        "context is awkward or unnatural.\n"
        "- notUsed: the learner did not actually use the target expression.\n"
        "Never say the expression was used well just because the characters appear "
        "in the transcript. If the learner copied an example sentence into a context "
        "where it does not fit, that is needsPolish, not appropriate.\n"
        "usageFeedbackKo: one or two short Korean sentences about how the learner "
        "used the target expression. Encouraging and concrete. No grammar lectures, "
        "no linguistic terminology, no scores, no OPIc grade, no pass/fail.\n"
        "naturalCorrectionEn: the learner's own idea rewritten as one or two natural "
        "spoken sentences that use the target expression well. Keep their content — "
        "do not invent a different story. If the expression was not used, show a "
        "natural way to say their idea WITH it. naturalCorrectionKo is its Korean "
        "translation.\n"
        "expandedAnswerEn: how they could keep going in an OPIc answer — the "
        "corrected sentence plus one more natural sentence (a reason, a detail or a "
        "feeling). expandedAnswerKo is its Korean translation.\n"
        f"relatedExpressions: {COACH_MIN_RELATED}-{COACH_MAX_RELATED} short English "
        "chunks that go naturally with this topic and expression (e.g. get crowded, "
        "especially on weekends, cozy atmosphere). Short chunks only, not full "
        "sentences and not a vocabulary list.\n"
        "Do not return a grade, a score, a rubric, a pronunciation or fluency "
        "judgement, or any comment about the learner's personality."
    )


def coach_input_text(
    *,
    term: str,
    item_type: VocabularyItemType,
    meaning_ko: str | None,
    topic: VocabularyTopic | None,
    transcript: str,
) -> str:
    lines = [
        f"Target expression ({item_type.value}): {term}",
    ]
    if meaning_ko:
        lines.append(f"Korean meaning of the target expression: {meaning_ko}")
    if topic is not None:
        lines.append(f"Topic being practised: {topic.value} ({topic.label})")
    lines.append(f"What the learner said: {transcript}")
    return "\n".join(lines)


def mock_coach_draft(
    *,
    term: str,
    item_type: VocabularyItemType,
    transcript: str,
) -> VocabularyCoachDraft:
    """MOCK_AI 전용 결정적 결과. 운영에서는 쓰이지 않는다(MOCK_AI=false 강제).

    표현이 전사에 들어 있는지만 본다 — 맥락 판단은 실제 모델의 몫이고, 여기서
    중요한 것은 "형식이 항상 유효하다"이다.
    """
    used = normalize_term(term) in normalize_term(transcript)
    assessment = (
        VocabularyUsageAssessment.APPROPRIATE if used else VocabularyUsageAssessment.NOT_USED
    )
    feedback = (
        f"{term}을(를) 문맥에 맞게 잘 사용했어요."
        if used
        else f"이번 답변에는 {term}이(가) 쓰이지 않았어요. 아래 문장처럼 넣어 보세요."
    )
    return VocabularyCoachDraft(
        usageAssessment=assessment,
        usageFeedbackKo=feedback,
        naturalCorrectionEn=f"My favorite place really {term} on weekends.",
        naturalCorrectionKo=f"주말에는 그곳이 정말 {term} 해요.",
        expandedAnswerEn=(
            f"My favorite place really {term} on weekends, "
            "but I still go there because it has a cozy atmosphere."
        ),
        expandedAnswerKo=(
            f"주말에는 그곳이 정말 {term} 하지만, 분위기가 아늑해서 그래도 자주 가요."
        ),
        relatedExpressions=["especially on weekends", "cozy atmosphere", "hang out with"],
    )
