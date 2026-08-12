"""답변 근거 충분도 판정과 추정 오디오 지표 구분."""

from __future__ import annotations

from app.models.api import AnswerQuality, AudioMetrics
from app.services import answer_quality


def _metrics(*, estimated: bool = False) -> AudioMetrics:
    return AudioMetrics(
        durationSeconds=42.0,
        speakingSeconds=38.0,
        silenceRatio=0.1,
        wordsPerMinute=95.0,
        isEstimated=estimated,
    )


def test_measured_metrics_default_to_not_estimated() -> None:
    assert _metrics().is_estimated is False


def test_no_gradable_word_is_insufficient() -> None:
    for transcript in ["", "   ", "음... 어..."]:
        assert (
            answer_quality.classify(
                transcript=transcript, metrics=_metrics(), level_was_clamped=False
            )
            is AnswerQuality.INSUFFICIENT
        )


def test_estimated_metrics_downgrade_to_low_evidence() -> None:
    assert (
        answer_quality.classify(
            transcript="I went to the park with my friends.",
            metrics=_metrics(estimated=True),
            level_was_clamped=False,
        )
        is AnswerQuality.LOW_EVIDENCE
    )


def test_clamped_level_is_not_presented_as_a_confident_grade() -> None:
    """IL 하한으로 끌어올린 등급은 '진짜 IL'과 구분한다."""
    assert (
        answer_quality.classify(
            transcript="I go park.",
            metrics=_metrics(),
            level_was_clamped=True,
        )
        is AnswerQuality.LOW_EVIDENCE
    )


def test_measured_full_answer_is_gradable() -> None:
    assert (
        answer_quality.classify(
            transcript="I usually visit the park on weekends because it is quiet.",
            metrics=_metrics(),
            level_was_clamped=False,
        )
        is AnswerQuality.GRADABLE
    )


def test_mock_needs_every_answer_empty_to_be_insufficient() -> None:
    metrics = [_metrics() for _ in range(3)]
    assert (
        answer_quality.classify_many(
            transcripts=["", "", ""], metrics=metrics, level_was_clamped=False
        )
        is AnswerQuality.INSUFFICIENT
    )
    assert (
        answer_quality.classify_many(
            transcripts=["", "I like it.", "It was fun."],
            metrics=metrics,
            level_was_clamped=False,
        )
        is AnswerQuality.LOW_EVIDENCE
    )
    assert (
        answer_quality.classify_many(
            transcripts=["I like it.", "It was fun.", "We stayed there."],
            metrics=metrics,
            level_was_clamped=False,
        )
        is AnswerQuality.GRADABLE
    )
