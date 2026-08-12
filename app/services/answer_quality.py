"""답변 근거 충분도(A/B/C) 판정 — 유일한 정책 위치.

`docs/DESIGN_DECISIONS.md` T4는 A/B/C 구조만 승인하고 단어 수·WPM·무음 비율의
경계값은 "실제 데이터를 본 뒤 확정"으로 남겨 두었다. 그래서 여기에는 **임의로
만들어낸 임계값을 두지 않는다.** 지금 객관적으로 방어 가능한 사실만 사용한다.

- C(insufficient): 채점할 영어 단어가 하나도 없다. 등급을 말할 근거가 없다.
- B(low_evidence): 전달 지표가 측정값이 아니라 추정값이거나(`isEstimated`),
  모델이 IL 미만으로 판단한 것을 `_clamp_min_level`이 IL로 올린 경우.
  후자는 "진짜 IL"과 "IL이라고 말할 근거가 없음"을 구분하기 위한 것이다.
- A(gradable): 그 외.

아직 미확정: 경계 단어 수·발화 시간, 높은 무음 비율 기준(T4의 B 조건 일부).
확정되면 이 파일에만 추가한다.
"""

from __future__ import annotations

import re

from app.models.api import AnswerQuality, AudioMetrics

_WORD = re.compile(r"\b[A-Za-z']+\b")


def word_count(transcript: str) -> int:
    """채점 가능한 영어 단어 수(audio.py의 계산과 동일한 정의)."""
    return len(_WORD.findall(transcript))


def classify(
    *, transcript: str, metrics: AudioMetrics, level_was_clamped: bool
) -> AnswerQuality:
    if word_count(transcript) == 0:
        return AnswerQuality.INSUFFICIENT
    if metrics.is_estimated or level_was_clamped:
        return AnswerQuality.LOW_EVIDENCE
    return AnswerQuality.GRADABLE


def classify_many(
    *,
    transcripts: list[str],
    metrics: list[AudioMetrics],
    level_was_clamped: bool,
) -> AnswerQuality:
    """모의고사(15문항)는 한 문항이 비어도 나머지로 평가가 가능하므로,
    전부 비었을 때만 C로 본다. 한 문항이라도 비었거나 추정 지표면 B."""
    empty = [word_count(transcript) == 0 for transcript in transcripts]
    if empty and all(empty):
        return AnswerQuality.INSUFFICIENT
    if any(empty) or any(metric.is_estimated for metric in metrics) or level_was_clamped:
        return AnswerQuality.LOW_EVIDENCE
    return AnswerQuality.GRADABLE
