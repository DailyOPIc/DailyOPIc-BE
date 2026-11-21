"""오픽게임 하루 문제 세트.

게임(타자 · 뜻 맞추기 · 받아쓰기 · 문장 조립 · 빈칸)이 쓰는 표현 · 문장을 매일 AI로 새로 만든다.
하루 SETS_PER_DAY 세트를 한 번만 만들어 모든 사용자가 같이 쓰므로(사용자별 생성 없음)
무료 사용자도 토큰 없이 매번 다른 문제를 받는다. 웹은 접속 · 판마다 다음 세트로 돌린다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SETS_PER_DAY = 5
EXPRESSIONS_PER_SET = 12
SENTENCES_PER_SET = 8

# 웹 src/data/expressions.ts ExprGroup과 같은 값(화면의 묶음 이름표가 이 값을 쓴다)
ExprGroup = Literal["start", "stall", "link", "example", "time", "feeling", "close"]

# 세트마다 다른 장면을 줘서 다섯 세트가 서로 겹치지 않게 한다.
SET_THEMES = [
    "daily routine, home and neighborhood",
    "hobbies, movies, music and free time",
    "travel, vacations and memorable experiences",
    "school, work and plans for the future",
    "comparing past and present, opinions and changes",
]


class GameExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    en: str = Field(min_length=2, max_length=60)
    ko: str = Field(min_length=1, max_length=40)
    group: ExprGroup
    tip: str | None = Field(default=None, max_length=60)


class GameSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    en: str = Field(min_length=10, max_length=120)
    ko: str = Field(min_length=4, max_length=80)
    blank: str = Field(min_length=2, max_length=40)
    options: list[str] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def blank_is_in_sentence(self) -> "GameSentence":
        if self.blank not in self.en:
            raise ValueError("blank must appear verbatim in en")
        if self.blank in self.options or len(set(self.options)) != 3:
            raise ValueError("options must be 3 distinct wrong answers")
        return self


class GameSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expressions: list[GameExpression] = Field(min_length=EXPRESSIONS_PER_SET, max_length=EXPRESSIONS_PER_SET)
    sentences: list[GameSentence] = Field(min_length=SENTENCES_PER_SET, max_length=SENTENCES_PER_SET)


INSTRUCTIONS = (
    "You create word-game content for Korean learners preparing for the OPIc English speaking test. "
    f"Return exactly {EXPRESSIONS_PER_SET} expressions and {SENTENCES_PER_SET} sentences.\n"
    "Expressions: short spoken fillers, connectors and answer starters a learner can drop into any OPIc answer "
    "(e.g. 'To be honest,', 'Let me think.', 'On top of that,', 'Long story short,'). "
    "Each has a natural Korean meaning (ko) and a group: start (opening), stall (buying time), link (connecting), "
    "example (giving examples), time (time/frequency), feeling (opinion/feeling), close (wrapping up). "
    "Use a mix of groups. Keep each under 6 words. Optional tip: one short Korean usage hint.\n"
    "Sentences: natural first-person OPIc answer sentences (8-16 words) with a Korean translation. "
    "blank = a connector or filler that appears verbatim in the sentence (copy it exactly, including any comma). "
    "options = 3 plausible but wrong connectors of the same kind.\n"
    "Everyday spoken English only. No duplicates. Nothing offensive."
)


def input_text(theme: str, avoid: list[str]) -> str:
    avoid_line = ", ".join(avoid[:60]) or "(none)"
    return (
        f"Theme for the example sentences: {theme}.\n"
        f"Do not reuse these expressions: {avoid_line}"
    )


def mock_set(index: int) -> GameSet:
    """MOCK_AI(테스트 · 로컬)용 고정 세트. 세트 번호가 문장에 들어가 서로 구별된다."""
    groups: list[ExprGroup] = ["start", "stall", "link", "example", "time", "feeling", "close"]
    return GameSet(
        expressions=[
            GameExpression(en=f"Mock phrase {index}-{n}", ko=f"연습 표현 {n}", group=groups[n % len(groups)])
            for n in range(EXPRESSIONS_PER_SET)
        ],
        sentences=[
            GameSentence(
                en=f"To be honest, set {index} sentence {n} is about my day.",
                ko="솔직히 말하면 제 하루 이야기예요.",
                blank="To be honest,",
                options=["In the end,", "For example,", "As a result,"],
            )
            for n in range(SENTENCES_PER_SET)
        ],
    )
