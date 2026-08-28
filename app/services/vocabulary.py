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

from pydantic import BaseModel, ConfigDict, Field

from app.models.api import (
    OPIcLevel,
    VocabularyItemType,
    VocabularyTopic,
    VocabularyUsageRole,
)


SET_SIZE = 30
PER_TYPE = 10
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
    """종류별로 10개가 찰 때까지 모으는 그릇. 중복은 여기서 걷어낸다."""

    excluded: set[str]
    picked: dict[VocabularyItemType, list[VocabularyDraft]] = field(
        default_factory=lambda: {item: [] for item in VocabularyItemType}
    )

    @classmethod
    def create(cls, exclude_terms: list[str]) -> "VocabularySelection":
        return cls(excluded={normalize_term(term) for term in exclude_terms} - {""})

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
            if len(bucket) >= PER_TYPE:
                continue
            self.excluded.add(key)
            bucket.append(draft)
            accepted += 1
        return accepted

    def needed(self) -> dict[VocabularyItemType, int]:
        return {
            item: PER_TYPE - len(bucket)
            for item, bucket in self.picked.items()
            if len(bucket) < PER_TYPE
        }

    @property
    def is_complete(self) -> bool:
        return not self.needed()

    def drafts(self) -> list[VocabularyDraft]:
        """단어 10 → 표현 10 → 패턴 10 순서로 평탄화."""
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
) -> str:
    requested = {
        item.value: min(count + SURPLUS_PER_TYPE, PER_TYPE + SURPLUS_PER_TYPE)
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
