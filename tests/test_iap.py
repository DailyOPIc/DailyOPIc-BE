"""IAP 구독/엔타이틀먼트 및 플랜 인지 사용량 테스트."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.services.admob import VerifiedReward


USER_ID = "22222222-2222-4222-8222-222222222222"
WEBHOOK_SECRET = "test-rc-secret"


def _headers(request_id: str | None = None) -> dict[str, str]:
    value = {
        "X-DailyOPIc-User-ID": USER_ID,
        "X-Firebase-AppCheck": "test-app-check-token",
    }
    if request_id:
        value["Idempotency-Key"] = request_id
    return value


def _future_ms(days: int = 30) -> int:
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp() * 1000)


def _purchase_event(
    plan_entitlement: str,
    *,
    event_id: str,
    event_type: str = "INITIAL_PURCHASE",
    expiration_ms: int | None = None,
) -> dict:
    return {
        "type": event_type,
        "id": event_id,
        "app_user_id": USER_ID,
        "entitlement_ids": [plan_entitlement],
        "product_id": f"opic_{plan_entitlement}_monthly",
        "period_type": "NORMAL",
        "expiration_at_ms": expiration_ms if expiration_ms is not None else _future_ms(),
        "store": "APP_STORE",
    }


def _post_webhook(client: TestClient, event: dict, *, secret: str = WEBHOOK_SECRET):
    return client.post(
        "/v1/iap/revenuecat-webhook",
        headers={"Authorization": secret},
        json={"event": event, "api_version": "1.0"},
    )


class _FakeSSVVerifier:
    def __init__(self, *, nonce: str) -> None:
        self._nonce = nonce

    async def verify(self, raw_query: str) -> VerifiedReward:
        return VerifiedReward(
            nonce=self._nonce,
            transaction_id=f"tx-{self._nonce}",
            user_id=USER_ID,
            ad_unit="ca-app-pub-5460686409666356/7091483531",
        )


def _verify_reward(client: TestClient, nonce: str) -> None:
    client.app.state.ssv_verifier = _FakeSSVVerifier(nonce=nonce)
    response = client.get(f"/v1/admob/ssv?custom_data={nonce}&fake=1")
    assert response.status_code == 200, response.text


# --- 웹훅 인증/검증 ---------------------------------------------------------


def test_webhook_rejects_missing_and_wrong_auth() -> None:
    with TestClient(app) as client:
        no_auth = client.post(
            "/v1/iap/revenuecat-webhook",
            json={"event": _purchase_event("plus", event_id="e-1")},
        )
        assert no_auth.status_code == 401

        wrong = _post_webhook(
            client, _purchase_event("plus", event_id="e-1"), secret="nope"
        )
        assert wrong.status_code == 401


def test_webhook_503_when_secret_not_configured() -> None:
    with TestClient(app) as client:
        client.app.state.settings.revenuecat_webhook_auth = None
        response = _post_webhook(client, _purchase_event("plus", event_id="e-x"))
        assert response.status_code == 503


# --- 엔타이틀먼트 → capabilities/usage 반영 --------------------------------


def test_purchase_sets_plan_and_capabilities_reflect_it() -> None:
    with TestClient(app) as client:
        # 기본은 무료.
        caps = client.get("/v1/capabilities", headers=_headers()).json()
        assert caps["plan"] == "free"
        assert caps["quotaPolicy"]["practiceDaily"] == 1
        assert caps["quotaPolicy"]["adsEnabled"] is True

        assert _post_webhook(
            client, _purchase_event("plus", event_id="p-1")
        ).status_code == 200

        caps = client.get("/v1/capabilities", headers=_headers()).json()
        assert caps["plan"] == "plus"
        assert caps["quotaPolicy"]["practiceDaily"] == 10
        assert caps["quotaPolicy"]["adsEnabled"] is False
        assert caps["quotaPolicy"]["analysisDepth"] == "detailed"

        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["freeRemaining"] == 10


def test_pro_purchase_unlocks_pro_features() -> None:
    with TestClient(app) as client:
        assert _post_webhook(
            client, _purchase_event("pro", event_id="pro-1")
        ).status_code == 200
        caps = client.get("/v1/capabilities", headers=_headers()).json()
        assert caps["plan"] == "pro"
        assert caps["quotaPolicy"]["practiceDaily"] == 20
        assert caps["quotaPolicy"]["reviewSet"] is True
        assert caps["quotaPolicy"]["weeklyReport"] is True
        assert caps["quotaPolicy"]["historyDays"] is None


def test_expiration_downgrades_to_free() -> None:
    with TestClient(app) as client:
        _post_webhook(client, _purchase_event("pro", event_id="pro-2"))
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "pro"

        expire = {
            "type": "EXPIRATION",
            "id": "exp-1",
            "app_user_id": USER_ID,
            "entitlement_ids": ["pro"],
            "product_id": "opic_pro_monthly",
        }
        assert _post_webhook(client, expire).status_code == 200
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "free"


def test_already_expired_timestamp_is_treated_as_free() -> None:
    with TestClient(app) as client:
        past_ms = int((datetime.now(UTC) - timedelta(days=1)).timestamp() * 1000)
        _post_webhook(
            client,
            _purchase_event("plus", event_id="stale-1", expiration_ms=past_ms),
        )
        # isActive=True로 저장되더라도 만료 시각이 과거면 free로 강등.
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "free"


def test_webhook_is_idempotent() -> None:
    with TestClient(app) as client:
        first = _post_webhook(client, _purchase_event("basic", event_id="dup-1"))
        assert first.status_code == 200
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "basic"

        # 동일 event id로 plus 부여 시도 → 멱등 처리로 무시되어야 함.
        dup = _post_webhook(
            client,
            _purchase_event("plus", event_id="dup-1"),
        )
        assert dup.status_code == 200
        assert dup.json()["status"] == "duplicate"
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "basic"


# --- 플랜 인지 사용량 ------------------------------------------------------


def _make_practice_form(client: TestClient) -> dict[str, str]:
    question_set = client.post(
        "/v1/question-sets/practice",
        headers=_headers(),
        json={"targetLevel": "IH", "background": {"interests": ["news"]}},
    ).json()
    return {
        "setId": question_set["setId"],
        "questionNumber": str(question_set["questions"][0]["number"]),
        "transcript": "I read several news sources every morning to compare perspectives.",
        "targetLevel": "IH",
    }


def test_basic_plan_gets_three_daily_practices() -> None:
    with TestClient(app) as client:
        _post_webhook(client, _purchase_event("basic", event_id="b-quota"))
        form = _make_practice_form(client)
        for _ in range(3):
            response = client.post(
                "/v1/evaluations/practice",
                headers=_headers(str(uuid.uuid4())),
                data=form,
            )
            assert response.status_code == 200, response.text
        blocked = client.post(
            "/v1/evaluations/practice",
            headers=_headers(str(uuid.uuid4())),
            data=form,
        )
        assert blocked.status_code == 402


def test_paid_plan_mock_reward_auto_verifies_without_ad() -> None:
    with TestClient(app) as client:
        _post_webhook(client, _purchase_event("plus", event_id="m-auto"))
        mock_set = client.post(
            "/v1/mock-exams",
            headers=_headers(),
            json={
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
            },
        ).json()
        reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_result", "sessionHash": mock_set["setHash"]},
        )
        assert reward.status_code == 200, reward.text
        # 유료 플랜은 광고 없이 즉시 verified.
        assert reward.json()["status"] == "verified"


def test_paid_plan_practice_ad_bonus_is_unavailable() -> None:
    with TestClient(app) as client:
        _post_webhook(client, _purchase_event("plus", event_id="no-ad"))
        response = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "practice_credits"},
        )
        assert response.status_code == 402
        assert response.json()["detail"]["code"] == "reward_not_available_for_plan"


# --- 취약점 복습 세트(Pro 게이트) -----------------------------------------


def _review_body() -> dict:
    return {
        "targetLevel": "IH",
        "background": {"interests": ["news"]},
        "focusDimension": "grammar",
    }


def test_review_set_requires_pro() -> None:
    with TestClient(app) as client:
        _post_webhook(client, _purchase_event("plus", event_id="rev-plus"))
        response = client.post(
            "/v1/question-sets/review",
            headers=_headers("review-key-1"),
            json=_review_body(),
        )
        assert response.status_code == 402
        assert response.json()["detail"]["code"] == "review_set_requires_pro"


def test_review_set_available_for_pro() -> None:
    with TestClient(app) as client:
        _post_webhook(client, _purchase_event("pro", event_id="rev-pro"))
        response = client.post(
            "/v1/question-sets/review",
            headers=_headers("review-key-2"),
            json=_review_body(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["number"] for item in body["questions"]] == list(range(2, 16))
