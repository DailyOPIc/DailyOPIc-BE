"""리워드 한도 회귀 테스트.

실제 CS: "열심히 답변했는데 채점이 안돼요.. daily reward quota exhausted 라고만 떠요."

원인: 모의고사 게이트 3종(start/adjustment/result)이 카운터 하나를 같이 써서
하루 한도가 "완벽한 1회차"에 딱 맞았다. 포기 후 재시작으로 시작 광고를 한 번 더
보면 마지막 게이트인 결과(채점)에서 402가 떨어졌고, 그때는 이미 15문항을 다
답한 뒤였다. 여기서 그 길이 다시 열리지 않는지 고정한다.
"""

from __future__ import annotations

import re
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import plans
from app.models.api import RewardPurpose

from tests.test_api import (
    USER_ID,
    _grant_practice_token,
    _headers,
    _mock_session_payload,
    _verify_reward,
)


ENGLISH_ONLY = re.compile(r"^[\x00-\x7F]+$")
SESSION_HASH = "0" * 32


def _intent(client: TestClient, purpose: str, session_hash: str | None = None):
    body: dict[str, str] = {"purpose": purpose}
    if session_hash:
        body["sessionHash"] = session_hash
    return client.post("/v1/ad-rewards/intents", headers=_headers(), json=body)


def _earn(client: TestClient, purpose: str, session_hash: str | None = None) -> str:
    response = _intent(client, purpose, session_hash)
    assert response.status_code == 200, response.text
    nonce = response.json()["nonce"]
    _verify_reward(client, nonce)
    return nonce


def _usage_counters(client: TestClient) -> dict[str, int]:
    store = client.app.state.state_store
    (values,) = list(store._usage.values()) or [{}]
    return values


def test_reward_intent_within_quota_succeeds() -> None:
    with TestClient(app) as client:
        assert _intent(client, "practice_credits").status_code == 200


def test_reward_intent_above_quota_returns_stable_code_and_korean_message() -> None:
    with TestClient(app) as client:
        _earn(client, "practice_credits")  # 무료 플랜 데일리 광고 보너스는 하루 1회
        blocked = _intent(client, "practice_credits")
        assert blocked.status_code == 402
        detail = blocked.json()["detail"]
        assert detail["code"] == "reward_quota_exhausted"
        # 이미 배포된 앱은 message를 그대로 보여준다. 영어 예외 문구가 나가면 안 된다.
        assert not ENGLISH_ONLY.match(detail["message"])
        assert "daily reward quota exhausted" not in detail["message"]


def test_mock_result_gate_survives_a_restarted_session() -> None:
    """CS 재현 경로: 포기 → 재시작 → 조정 → 채점 게이트."""
    with TestClient(app) as client:
        first = client.post(
            "/v1/mock-exams/sessions",
            headers=_headers(str(uuid.uuid4())),
            json=_mock_session_payload(),
        ).json()
        nonce = _earn(client, "mock_start", first["sessionHash"])
        client.post(
            f"/v1/mock-exams/{first['sessionId']}/start",
            headers=_headers(str(uuid.uuid4())),
            json={"rewardNonce": nonce},
        )
        client.post(f"/v1/mock-exams/{first['sessionId']}/abandon", headers=_headers())

        second = client.post(
            "/v1/mock-exams/sessions",
            headers=_headers(str(uuid.uuid4())),
            json=_mock_session_payload(),
        ).json()
        nonce = _earn(client, "mock_start", second["sessionHash"])
        started = client.post(
            f"/v1/mock-exams/{second['sessionId']}/start",
            headers=_headers(str(uuid.uuid4())),
            json={"rewardNonce": nonce},
        ).json()
        nonce = _earn(client, "mock_adjustment", started["sessionHash"])
        adjusted = client.post(
            f"/v1/mock-exams/{second['sessionId']}/adjustment",
            headers=_headers(str(uuid.uuid4())),
            json={"adjustment": "same", "rewardNonce": nonce},
        ).json()

        # 15문항을 다 답한 시점. 채점 게이트가 여기서 막히면 답변이 버려진다.
        result_gate = _intent(client, "mock_result", adjusted["sessionHash"])
        assert result_gate.status_code == 200, result_gate.text


def test_reward_purposes_do_not_block_each_other() -> None:
    with TestClient(app) as client:
        _earn(client, "practice_credits")
        assert _intent(client, "practice_credits").status_code == 402
        # 다른 용도는 각자의 카운터를 쓴다.
        assert _intent(client, "practice_refresh").status_code == 200
        assert _intent(client, "mock_start", SESSION_HASH).status_code == 200
        assert _intent(client, "target_level_change").status_code == 200


def test_mock_gates_are_counted_separately() -> None:
    with TestClient(app) as client:
        limit = plans.reward_max_for("free", RewardPurpose.MOCK_START)
        for _ in range(limit):
            _earn(client, "mock_start", SESSION_HASH)
        assert _intent(client, "mock_start", SESSION_HASH).status_code == 402
        # 시작 게이트를 다 써도 채점 게이트는 별개로 남아 있어야 한다.
        assert _intent(client, "mock_result", SESSION_HASH).status_code == 200


def test_practice_evaluation_does_not_consume_reward_quota() -> None:
    with TestClient(app) as client:
        question_set = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json={"initialLevel": 4, "background": {"interests": ["news"]}},
        ).json()
        # P13: 분석도 데일리 토큰을 쓴다. 분석용 토큰을 광고로 확보한 뒤 스냅샷을
        # 찍는다 — 여기서 고정하려는 것은 "분석이 광고 리워드 한도를 먹지 않는다"이다.
        _grant_practice_token(client)
        before = dict(_usage_counters(client))
        form = {
            "setId": question_set["setId"],
            "questionNumber": str(question_set["questions"][0]["number"]),
            "transcript": "I read the news every morning to stay informed about the world.",
        }
        evaluated = client.post(
            "/v2/evaluations/practice",
            headers=_headers(str(uuid.uuid4())),
            data=form,
        )
        assert evaluated.status_code == 200, evaluated.text
        after = _usage_counters(client)
        for key, value in before.items():
            if key.endswith("RewardCount"):
                assert after[key] == value, key
        # 분석은 데일리 토큰만 쓴다. 다른 용도의 광고 한도는 그대로 남아 있다.
        assert _intent(client, "practice_refresh").status_code == 200


def test_evaluation_retry_does_not_consume_reward_quota() -> None:
    with TestClient(app) as client:
        question_set = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json={"initialLevel": 4, "background": {"interests": ["news"]}},
        ).json()
        form = {
            "setId": question_set["setId"],
            "questionNumber": str(question_set["questions"][0]["number"]),
            "transcript": "I read the news every morning to stay informed about the world.",
        }
        _grant_practice_token(client)  # 분석 1회분 토큰
        key = str(uuid.uuid4())
        first = client.post("/v2/evaluations/practice", headers=_headers(key), data=form)
        assert first.status_code == 200, first.text
        before = dict(_usage_counters(client))
        # 같은 Idempotency-Key 재시도 = 같은 결과, 추가 소모 없음.
        retried = client.post("/v2/evaluations/practice", headers=_headers(key), data=form)
        assert retried.status_code == 200, retried.text
        assert retried.json() == first.json()
        assert _usage_counters(client) == before


def test_verified_reward_is_counted_once_on_ssv_retry() -> None:
    with TestClient(app) as client:
        response = _intent(client, "practice_credits")
        nonce = response.json()["nonce"]
        _verify_reward(client, nonce)
        counted = _usage_counters(client)["practiceCreditRewardCount"]
        # AdMob이 같은 SSV를 재전송해도 한도는 한 번만 깎인다.
        _verify_reward(client, nonce)
        assert _usage_counters(client)["practiceCreditRewardCount"] == counted
        assert client.get("/v1/usage", headers=_headers()).json()["bonusRemaining"] == 1


def test_quota_resets_on_the_next_kst_day(monkeypatch) -> None:
    with TestClient(app) as client:
        _earn(client, "practice_credits")
        assert _intent(client, "practice_credits").status_code == 402
        # 날짜 키가 바뀌면(=KST 자정) 카운터도 새로 시작한다.
        monkeypatch.setattr("app.api.routes._date_key", lambda: "20991231")
        assert _intent(client, "practice_credits").status_code == 200
        store = client.app.state.state_store
        assert f"{USER_ID}:20991231" in store._usage
