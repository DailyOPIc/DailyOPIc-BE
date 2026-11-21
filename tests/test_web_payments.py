"""웹 결제(토스 일반결제): 주문 → 승인 → 지급, 멱등, 환불 회수, 보유 토큰 사용."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.state import InMemoryStateStore, UsageLimitExceeded
from app.services.web_payments import TossPaymentError

USER_ID = "88888888-8888-4888-8888-888888888888"
OTHER_ID = "99999999-9999-4999-8999-999999999999"


def _headers(uid: str = USER_ID) -> dict[str, str]:
    return {"X-DailyOPIc-User-ID": uid, "X-Firebase-AppCheck": "test-app-check-token"}


class _FakeToss:
    configured = True

    def __init__(self, *, already_processed: bool = False, status: str = "DONE") -> None:
        self.confirms: list[dict[str, Any]] = []
        self.payments: dict[str, dict[str, Any]] = {}
        self.already_processed = already_processed
        self.status = status

    async def confirm(self, *, payment_key: str, order_id: str, amount: int) -> dict[str, Any]:
        self.confirms.append({"paymentKey": payment_key, "orderId": order_id, "amount": amount})
        payment = {"paymentKey": payment_key, "orderId": order_id, "totalAmount": amount, "status": self.status}
        self.payments[payment_key] = payment
        if self.already_processed:
            raise TossPaymentError("ALREADY_PROCESSED_PAYMENT", "이미 처리된 결제", 400)
        return payment

    async def get_payment(self, payment_key: str) -> dict[str, Any]:
        return self.payments[payment_key]


@pytest.fixture(autouse=True)
def _token_packs_on(monkeypatch: pytest.MonkeyPatch):
    from app.config import get_settings

    monkeypatch.setenv("WEB_TOKEN_PACKS_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _buy(client: TestClient, product_id: str, uid: str = USER_ID) -> tuple[str, int]:
    order = client.post("/v1/web-payments/orders", json={"productId": product_id}, headers=_headers(uid))
    assert order.status_code == 200, order.text
    body = order.json()
    return body["orderId"], body["amount"]


def _confirm(client: TestClient, order_id: str, amount: int, uid: str = USER_ID, key: str = "pk_1"):
    return client.post(
        "/v1/web-payments/confirm",
        json={"paymentKey": key, "orderId": order_id, "amount": amount},
        headers=_headers(uid),
    )


def test_pass_purchase_grants_plan_once() -> None:
    with TestClient(app) as client:
        toss = client.app.state.toss = _FakeToss()
        order_id, amount = _buy(client, "pass_plus_30d")
        assert amount == 6900

        first = _confirm(client, order_id, amount)
        assert first.status_code == 200, first.text
        assert first.json()["plan"] == "plus"
        assert first.json()["webPass"]["plan"] == "plus"
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "plus"

        # 새로고침 등으로 다시 승인 요청해도 토스를 다시 부르지 않고 한 번만 지급
        again = _confirm(client, order_id, amount)
        assert again.status_code == 200
        assert len(toss.confirms) == 1
        assert again.json()["webPass"] == first.json()["webPass"]


def test_confirm_rejects_tampered_amount_and_other_users_order() -> None:
    with TestClient(app) as client:
        client.app.state.toss = _FakeToss()
        order_id, amount = _buy(client, "pass_pro_30d")
        assert _confirm(client, order_id, 100).status_code == 400
        assert _confirm(client, order_id, amount, uid=OTHER_ID).status_code == 404
        assert client.post("/v1/web-payments/orders", json={"productId": "nope"}, headers=_headers()).status_code == 404


def test_already_processed_payment_is_checked_and_granted() -> None:
    with TestClient(app) as client:
        client.app.state.toss = _FakeToss(already_processed=True)
        order_id, amount = _buy(client, "tokens_10")
        assert amount == 2900
        response = _confirm(client, order_id, amount)
        assert response.status_code == 200, response.text
        assert response.json()["purchasedTokens"] == 10


def test_unfinished_payment_is_not_granted() -> None:
    with TestClient(app) as client:
        client.app.state.toss = _FakeToss(status="WAITING_FOR_DEPOSIT")
        order_id, amount = _buy(client, "pass_basic_30d")
        assert _confirm(client, order_id, amount).status_code == 400
        assert client.get("/v1/web-payments/wallet", headers=_headers()).json()["plan"] == "free"


def test_refund_webhook_revokes_pass() -> None:
    with TestClient(app) as client:
        toss = client.app.state.toss = _FakeToss()
        order_id, amount = _buy(client, "pass_basic_30d")
        assert _confirm(client, order_id, amount, key="pk_refund").status_code == 200
        toss.payments["pk_refund"]["status"] = "CANCELED"

        # 본문은 믿지 않고 토스에 다시 조회한다
        hook = client.post("/v1/web-payments/toss-webhook", json={"data": {"paymentKey": "pk_refund", "status": "DONE"}})
        assert hook.json() == {"status": "revoked"}
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "free"
        assert client.post("/v1/web-payments/toss-webhook", json={"data": {"paymentKey": "pk_refund"}}).json() == {
            "status": "unchanged"
        }


def test_orders_need_toss_configured() -> None:
    with TestClient(app) as client:
        client.app.state.toss = type("Unconfigured", (), {"configured": False})()
        response = client.post("/v1/web-payments/orders", json={"productId": "tokens_10"}, headers=_headers())
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_purchased_tokens_are_used_after_daily_quota_and_refunded_on_failure() -> None:
    store = InMemoryStateStore()
    await store.create_web_order(
        {"orderId": "web_t1", "uid": USER_ID, "productId": "tokens_10", "amount": 2900,
         "grant": {"type": "tokens", "amount": 1}, "status": "pending"}
    )
    await store.complete_web_order("web_t1", payment_key="pk")

    first = await store.reserve_practice(USER_ID, "2026-11-21", "req-free", free_limit=1)
    assert first.source == "free"
    second = await store.reserve_practice(USER_ID, "2026-11-21", "req-bought", free_limit=1)
    assert second.source == "purchased"
    assert (await store.get_wallet(USER_ID))["purchasedTokens"] == 0
    with pytest.raises(UsageLimitExceeded):
        await store.reserve_practice(USER_ID, "2026-11-21", "req-none", free_limit=1)

    await store.fail_request("req-bought")
    assert (await store.get_wallet(USER_ID))["purchasedTokens"] == 1


def test_token_packs_are_hidden_until_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("WEB_TOKEN_PACKS_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as client:
        client.app.state.toss = _FakeToss()
        response = client.post("/v1/web-payments/orders", json={"productId": "tokens_10"}, headers=_headers())
        assert response.status_code == 404
        assert _buy(client, "pass_basic_30d")[1] == 3900


def test_allowlist_limits_orders_to_listed_verified_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("WEB_PAYMENTS_ALLOWLIST", "Review@Example.com")
    get_settings.cache_clear()
    tokens = {
        "token-review": {"uid": "uid-review", "email": "review@example.com", "email_verified": True},
        "token-other": {"uid": "uid-other", "email": "other@example.com", "email_verified": True},
        "token-unverified": {"uid": "uid-x", "email": "review@example.com", "email_verified": False},
    }
    monkeypatch.setattr("app.services.auth.auth.verify_id_token", lambda token, check_revoked=True: tokens[token])
    with TestClient(app) as client:
        client.app.state.toss = _FakeToss()

        def order(token: str):
            return client.post(
                "/v1/web-payments/orders",
                json={"productId": "pass_basic_30d"},
                headers={"X-Firebase-AppCheck": "test-app-check-token", "Authorization": f"Bearer {token}"},
            )

        assert order("token-review").status_code == 200
        assert order("token-other").status_code == 403
        assert order("token-unverified").status_code == 403
        # 익명·레거시 사용자(이메일 없음)도 막힌다
        assert client.post("/v1/web-payments/orders", json={"productId": "pass_basic_30d"}, headers=_headers()).status_code == 403


# --- 보유 토큰은 웹 요청에서만 쓴다 ---------------------------------------------


@pytest.mark.asyncio
async def test_purchased_tokens_are_not_spent_when_not_allowed() -> None:
    store = InMemoryStateStore()
    await store.create_web_order(
        {"orderId": "web_t2", "uid": USER_ID, "productId": "tokens_10", "amount": 2900,
         "grant": {"type": "tokens", "amount": 3}, "status": "pending"}
    )
    await store.complete_web_order("web_t2", payment_key="pk")
    with pytest.raises(UsageLimitExceeded):
        await store.reserve_practice(USER_ID, "2026-11-21", "req-ios", free_limit=0, allow_purchased=False)
    assert (await store.get_wallet(USER_ID))["purchasedTokens"] == 3
    web = await store.reserve_practice(USER_ID, "2026-11-21", "req-web", free_limit=0, allow_purchased=True)
    assert web.source == "purchased"


@pytest.mark.parametrize(
    ("app_id", "visible"),
    [("1:992368032252:web:abc", 5), ("1:992368032252:ios:abc", 0), (None, 0)],
)
def test_usage_shows_purchased_tokens_only_to_web(monkeypatch: pytest.MonkeyPatch, app_id, visible) -> None:
    monkeypatch.setattr(
        "app.services.auth.app_check.verify_token", lambda token: {"app_id": app_id} if app_id else None
    )
    with TestClient(app) as client:
        store = client.app.state.state_store
        import asyncio

        asyncio.run(store.create_web_order(
            {"orderId": "web_vis", "uid": USER_ID, "productId": "tokens_10", "amount": 2900,
             "grant": {"type": "tokens", "amount": 5}, "status": "pending"}
        ))
        asyncio.run(store.complete_web_order("web_vis", payment_key="pk"))
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["purchasedTokens"] == visible


@pytest.mark.parametrize(("app_id", "status"), [("1:1:web:x", "verified"), ("1:1:ios:x", "pending")])
def test_mock_gates_pass_without_ads_only_on_web_for_free_users(monkeypatch: pytest.MonkeyPatch, app_id, status) -> None:
    monkeypatch.setattr("app.services.auth.app_check.verify_token", lambda token: {"app_id": app_id})
    with TestClient(app) as client:
        intent = client.post(
            "/v1/ad-rewards/intents",
            json={"purpose": "mock_start", "sessionHash": "h" * 64},
            headers=_headers(),
        )
        assert intent.status_code == 200, intent.text
        assert intent.json()["status"] == status
