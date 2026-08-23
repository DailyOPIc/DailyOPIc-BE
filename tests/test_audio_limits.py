"""업로드 크기/길이 상한 회귀 테스트.

실제 CS: "2분 정도밖에 말하지 않았는데 audio file is too large 오류가 뜬다."
앱스토어에 나가 있는 iOS는 AVAssetExportPresetAppleM4A로 약 256kbps m4a를
만든다(측정: 180초 → 5.43MiB). 상한이 4MiB였을 때 약 131초부터 정상 녹음이
거절됐다. 여기서는 그 지점이 다시 막히지 않는지, 그러면서도 진짜 초과
업로드와 초과 길이는 여전히 막히는지를 고정한다.
"""

from __future__ import annotations

import io
import json
import subprocess
import uuid

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.main import AUDIO_MAX_BYTES, AUDIO_MAX_SECONDS, app
from app.services.audio import (
    DURATION_TOLERANCE_SECONDS,
    AudioMetricsService,
    AudioValidationError,
)

from tests.test_api import _grant_practice_token, _headers, _verify_reward


OLD_MAX_BYTES = 4 * 1024 * 1024
# 앱스토어 클라이언트가 만드는 180초 파일의 실측 크기(≈256kbps mono AAC).
SHIPPED_CLIENT_180S_BYTES = int(5.43 * 1024 * 1024)
TRANSCRIPT = "I usually enjoy this activity because it helps me relax and learn."


def _upload(size: int, name: str = "answer.m4a") -> UploadFile:
    payload = b"\0" * size
    return UploadFile(file=io.BytesIO(payload), filename=name, size=size)


def _service() -> AudioMetricsService:
    return AudioMetricsService(max_bytes=AUDIO_MAX_BYTES, max_seconds=AUDIO_MAX_SECONDS)


def _fake_probe(duration: float):
    """ffprobe만 원하는 길이를 돌려주고 ffmpeg(silencedetect)는 조용히 통과."""

    def run(args, **kwargs):  # noqa: ANN001, ANN003
        stdout = f"{duration}\n" if args[0] == "ffprobe" else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return run


def test_new_limit_covers_shipped_client_full_length_answer() -> None:
    # 상한은 실측 180초 파일보다 커야 한다. 아니면 CS가 그대로 재현된다.
    assert AUDIO_MAX_BYTES > SHIPPED_CLIENT_180S_BYTES
    assert AUDIO_MAX_BYTES > OLD_MAX_BYTES


async def test_two_minute_recording_from_shipped_client_is_accepted() -> None:
    # 약 120초 = 3.6MiB지만, CS가 난 구간(4MiB 초과 ~ 180초)까지 함께 고정한다.
    metrics = await _service().analyze(_upload(SHIPPED_CLIENT_180S_BYTES), TRANSCRIPT)
    assert metrics.duration_seconds > 0


async def test_upload_above_new_limit_is_still_rejected() -> None:
    with pytest.raises(AudioValidationError, match="too large"):
        await _service().analyze(_upload(AUDIO_MAX_BYTES + 1), TRANSCRIPT)


async def test_empty_upload_is_still_rejected() -> None:
    with pytest.raises(AudioValidationError, match="empty"):
        await _service().analyze(_upload(0), TRANSCRIPT)


async def test_encoder_padding_at_max_duration_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 180.000초 원본이 AAC 패딩 때문에 180.053초로 찍혀도 정상 녹음이다.
    monkeypatch.setattr("app.services.audio.subprocess.run", _fake_probe(180.053333))
    metrics = await _service().analyze(_upload(1024), TRANSCRIPT)
    assert metrics.duration_seconds == pytest.approx(180.05)


async def test_recording_longer_than_limit_is_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    too_long = AUDIO_MAX_SECONDS + DURATION_TOLERANCE_SECONDS + 1
    monkeypatch.setattr("app.services.audio.subprocess.run", _fake_probe(too_long))
    with pytest.raises(AudioValidationError, match="180 seconds or shorter"):
        await _service().analyze(_upload(1024), TRANSCRIPT)


def _practice_form(client: TestClient) -> dict[str, str]:
    question_set = client.post(
        "/v1/question-sets/practice",
        headers=_headers(),
        json={"initialLevel": 4, "background": {"interests": ["news"]}},
    ).json()
    # P13: 분석도 데일리 토큰을 쓴다. 세트가 무료 토큰을 썼으니 분석용을 하나 더.
    _grant_practice_token(client)
    return {
        "setId": question_set["setId"],
        "questionNumber": str(question_set["questions"][0]["number"]),
        "transcript": TRANSCRIPT,
    }


def test_practice_accepts_audio_above_old_limit() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v2/evaluations/practice",
            headers=_headers(str(uuid.uuid4())),
            data=_practice_form(client),
            files={
                "audio": (
                    "answer.m4a",
                    b"\0" * SHIPPED_CLIENT_180S_BYTES,
                    "audio/mp4",
                )
            },
        )
        assert response.status_code == 200, response.text


def test_practice_rejects_oversized_audio_as_invalid_audio() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v2/evaluations/practice",
            headers=_headers(str(uuid.uuid4())),
            data=_practice_form(client),
            files={
                "audio": ("answer.m4a", b"\0" * (AUDIO_MAX_BYTES + 1), "audio/mp4")
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_audio"


def test_mock_accepts_answer_above_old_limit_within_aggregate_cap() -> None:
    # 모의고사도 같은 audio_service를 쓴다. 파일 하나가 구 4MiB를 넘어도
    # 합계 30MB 상한 안이면 통과해야 한다.
    payload = {
        "targetLevel": "IM2",
        "background": {"travel": ["domestic"]},
        "survey": {
            "status": "student",
            "residence": "family",
            "leisure": ["movies", "music", "cafes"],
            "hobbies": [],
            "sports": [],
            "travel": ["domestic_travel"],
        },
    }
    with TestClient(app) as client:
        session = client.post(
            "/v1/mock-exams/sessions",
            headers=_headers(str(uuid.uuid4())),
            json=payload,
        ).json()
        start_reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_start", "sessionHash": session["sessionHash"]},
        ).json()
        _verify_reward(client, start_reward["nonce"])
        session = client.post(
            f"/v1/mock-exams/{session['sessionId']}/start",
            headers=_headers(str(uuid.uuid4())),
            json={"rewardNonce": start_reward["nonce"]},
        ).json()
        adjustment_reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_adjustment", "sessionHash": session["sessionHash"]},
        ).json()
        _verify_reward(client, adjustment_reward["nonce"])
        session = client.post(
            f"/v1/mock-exams/{session['sessionId']}/adjustment",
            headers=_headers(str(uuid.uuid4())),
            json={"adjustment": "same", "rewardNonce": adjustment_reward["nonce"]},
        ).json()
        result_reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_result", "sessionHash": session["sessionHash"]},
        ).json()
        _verify_reward(client, result_reward["nonce"])
        manifest = {
            "setId": session["setId"],
            "rewardNonce": result_reward["nonce"],
            "answers": [
                {
                    "number": number,
                    "transcript": f"Complete answer {number} with a reason and example.",
                }
                for number in range(1, 16)
            ],
        }
        big = b"\0" * (OLD_MAX_BYTES + 1)
        files = [
            (
                "audioFiles",
                (
                    f"answer-{number}.m4a",
                    big if number == 1 else b"not-real-audio",
                    "audio/mp4",
                ),
            )
            for number in range(1, 16)
        ]
        evaluated = client.post(
            f"/v1/mock-exams/{session['sessionId']}/evaluate",
            headers=_headers(str(uuid.uuid4())),
            data={"manifest": json.dumps(manifest)},
            files=files,
        )
        assert evaluated.status_code == 200, evaluated.text


def test_mock_aggregate_cap_is_above_full_length_upload() -> None:
    from app.api.routes import MOCK_AUDIO_AGGREGATE_MAX_BYTES

    # 새 iOS는 180초 ≈ 1.1MiB로 인코딩한다. 15개 전부 최대 길이라도 합계 상한 안.
    assert 15 * 1.1 * 1024 * 1024 < MOCK_AUDIO_AGGREGATE_MAX_BYTES
