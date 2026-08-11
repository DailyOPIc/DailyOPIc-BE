"""IAP 구독/엔타이틀먼트 및 플랜 인지 사용량 테스트."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import app
from app.services.admob import VerifiedReward
from app.services.revenuecat import (
    RevenueCatAPIError,
    RevenueCatClient,
    RevenueCatCustomerInfo,
)


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


class _FakeRevenueCatClient:
    def __init__(
        self,
        customer_info: RevenueCatCustomerInfo | None = None,
        error: RevenueCatAPIError | None = None,
    ) -> None:
        self.customer_info = customer_info
        self.error = error
        self.calls: list[str] = []

    async def get_customer_info(self, app_user_id: str) -> RevenueCatCustomerInfo:
        self.calls.append(app_user_id)
        if self.error is not None:
            raise self.error
        assert self.customer_info is not None
        return self.customer_info


def _customer_info(
    entitlement_ids: list[str],
    *,
    request_date: datetime | None = None,
    expires_at: datetime | None = None,
) -> RevenueCatCustomerInfo:
    requested_at = request_date or datetime.now(UTC)
    expiration = expires_at or requested_at + timedelta(days=30)
    return RevenueCatCustomerInfo(
        request_date=requested_at,
        active_entitlements={identifier: expiration for identifier in entitlement_ids},
    )


def _default_customer_info(event: dict[str, Any]) -> RevenueCatCustomerInfo:
    now = datetime.now(UTC)
    if str(event.get("type") or "").upper() in {"EXPIRATION", "REFUND"}:
        return _customer_info([], request_date=now)
    expiration_ms = event.get("expiration_at_ms")
    if expiration_ms is not None:
        expiration = datetime.fromtimestamp(expiration_ms / 1000, tz=UTC)
        if expiration <= now:
            return _customer_info([], request_date=now)
    else:
        expiration = now + timedelta(days=30)
    return _customer_info(
        list(event.get("entitlement_ids") or []),
        request_date=now,
        expires_at=expiration,
    )


def _post_webhook(
    client: TestClient,
    event: dict,
    *,
    secret: str = WEBHOOK_SECRET,
    customer_info: RevenueCatCustomerInfo | None = None,
    revenuecat: _FakeRevenueCatClient | None = None,
):
    client.app.state.revenuecat = revenuecat or _FakeRevenueCatClient(
        customer_info or _default_customer_info(event)
    )
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
        assert caps["quotaPolicy"]["analysisDepth"] == "full"

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
        assert caps["quotaPolicy"]["historyDays"] is None


@pytest.mark.parametrize(
    ("active_ids", "expected_plan"),
    [
        (["basic", "pro"], "pro"),
        (["basic", "plus"], "plus"),
        (["basic"], "basic"),
        (["pro"], "pro"),
        ([], "free"),
    ],
)
def test_webhook_uses_highest_active_customer_info_entitlement(
    active_ids: list[str], expected_plan: str
) -> None:
    with TestClient(app) as client:
        event = _purchase_event("basic", event_id=f"highest-{expected_plan}")
        response = _post_webhook(
            client,
            event,
            customer_info=_customer_info(active_ids),
        )
        assert response.status_code == 200
        assert response.json()["plan"] == expected_plan
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == expected_plan


def test_basic_renewal_does_not_overwrite_active_pro() -> None:
    with TestClient(app) as client:
        grant = _purchase_event(
            "pro", event_id="grant-with-basic", event_type="NON_RENEWING_PURCHASE"
        )
        _post_webhook(
            client,
            grant,
            customer_info=_customer_info(["basic", "pro"]),
        )

        renewal = _purchase_event(
            "basic", event_id="basic-renewal", event_type="RENEWAL"
        )
        response = _post_webhook(
            client,
            renewal,
            customer_info=_customer_info(["basic", "pro"]),
        )

        assert response.json()["plan"] == "pro"
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "pro"


def test_pro_expiration_falls_back_to_active_basic() -> None:
    with TestClient(app) as client:
        _post_webhook(
            client,
            _purchase_event("pro", event_id="pro-before-expiration"),
            customer_info=_customer_info(["basic", "pro"]),
        )
        expiration = {
            "type": "EXPIRATION",
            "id": "pro-expiration-with-basic",
            "app_user_id": USER_ID,
            "entitlement_ids": ["pro"],
        }
        response = _post_webhook(
            client,
            expiration,
            customer_info=_customer_info(["basic"]),
        )

        assert response.json()["plan"] == "basic"
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "basic"


def test_basic_expiration_keeps_active_pro() -> None:
    with TestClient(app) as client:
        _post_webhook(
            client,
            _purchase_event("pro", event_id="pro-with-basic"),
            customer_info=_customer_info(["basic", "pro"]),
        )
        expiration = {
            "type": "EXPIRATION",
            "id": "basic-expiration-with-pro",
            "app_user_id": USER_ID,
            "entitlement_ids": ["basic"],
        }
        response = _post_webhook(
            client,
            expiration,
            customer_info=_customer_info(["pro"]),
        )

        assert response.json()["plan"] == "pro"
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "pro"


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


def test_promotional_grant_activates_pro_until_expiration() -> None:
    with TestClient(app) as client:
        grant = _purchase_event(
            "pro",
            event_id="promo-pro-1",
            event_type="NON_RENEWING_PURCHASE",
        )
        grant["store"] = "PROMOTIONAL"
        grant["period_type"] = "PROMOTIONAL"

        response = _post_webhook(client, grant)
        assert response.status_code == 200
        assert response.json()["plan"] == "pro"
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "pro"

        expiration = {
            "type": "EXPIRATION",
            "id": "promo-pro-1-expiration",
            "app_user_id": USER_ID,
            "entitlement_ids": ["pro"],
            "product_id": "rc_promo_pro",
            "store": "PROMOTIONAL",
            "period_type": "PROMOTIONAL",
        }
        assert _post_webhook(client, expiration).status_code == 200
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "free"


def test_cancellation_keeps_access_until_expiry() -> None:
    """자동갱신 해지(CANCELLATION)는 즉시 강등하지 않고 만료일까지 권한 유지."""
    with TestClient(app) as client:
        _post_webhook(client, _purchase_event("pro", event_id="pro-c1"))
        cancel = {
            "type": "CANCELLATION",
            "id": "cancel-1",
            "app_user_id": USER_ID,
            "entitlement_ids": ["pro"],
            "product_id": "opic_pro_monthly",
            "expiration_at_ms": _future_ms(),  # 아직 미래 → 접근 유지
        }
        assert _post_webhook(client, cancel).status_code == 200
        caps = client.get("/v1/capabilities", headers=_headers()).json()
        assert caps["plan"] == "pro"

        # 만료 이벤트가 오면 그때 free로 강등.
        expire = {
            "type": "EXPIRATION",
            "id": "cancel-1-exp",
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
        revenuecat = _FakeRevenueCatClient(_customer_info(["basic"]))
        first = _post_webhook(
            client,
            _purchase_event("basic", event_id="dup-1"),
            revenuecat=revenuecat,
        )
        assert first.status_code == 200
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "basic"

        # 동일 event id로 plus 부여 시도 → 멱등 처리로 무시되어야 함.
        dup = _post_webhook(
            client,
            _purchase_event("plus", event_id="dup-1"),
            revenuecat=revenuecat,
        )
        assert dup.status_code == 200
        assert dup.json()["status"] == "duplicate"
        assert revenuecat.calls == [USER_ID]
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "basic"


@pytest.mark.parametrize(
    ("code", "gateway_status", "upstream_status"),
    [
        ("timeout", 503, None),
        ("upstream_auth_error", 502, 401),
        ("upstream_auth_error", 502, 403),
        ("subscriber_not_found", 502, 404),
        ("upstream_unavailable", 503, 429),
        ("upstream_unavailable", 503, 500),
        ("invalid_response", 502, None),
    ],
)
def test_revenuecat_api_failure_preserves_existing_plan_and_remains_retryable(
    code: str, gateway_status: int, upstream_status: int | None
) -> None:
    with TestClient(app) as client:
        _post_webhook(
            client,
            _purchase_event("pro", event_id=f"existing-{code}-{upstream_status}"),
        )
        event_id = f"failed-{code}-{upstream_status}"
        failure = _FakeRevenueCatClient(
            error=RevenueCatAPIError(
                code,
                gateway_status=gateway_status,
                upstream_status=upstream_status,
            )
        )

        response = _post_webhook(
            client,
            _purchase_event("basic", event_id=event_id),
            revenuecat=failure,
        )

        assert response.status_code == gateway_status
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "pro"
        assert client.app.state.state_store._iap_events.get(event_id) is None


def test_missing_revenuecat_api_key_preserves_existing_plan() -> None:
    with TestClient(app) as client:
        unconfigured_revenuecat = client.app.state.revenuecat
        _post_webhook(
            client,
            _purchase_event("pro", event_id="existing-before-missing-key"),
        )
        client.app.state.revenuecat = unconfigured_revenuecat
        event = _purchase_event("basic", event_id="missing-api-key")

        response = client.post(
            "/v1/iap/revenuecat-webhook",
            headers={"Authorization": WEBHOOK_SECRET},
            json={"event": event, "api_version": "1.0"},
        )

        assert response.status_code == 503
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "pro"
        assert client.app.state.state_store._iap_events.get("missing-api-key") is None


def test_state_save_failure_allows_same_webhook_to_retry() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        store = client.app.state.state_store
        original = store.complete_iap_sync
        attempts = 0

        async def fail_once(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("simulated state failure")
            return await original(**kwargs)

        store.complete_iap_sync = fail_once
        revenuecat = _FakeRevenueCatClient(_customer_info(["basic"]))
        event = _purchase_event("basic", event_id="retry-after-state-failure")

        first = _post_webhook(client, event, revenuecat=revenuecat)
        assert first.status_code == 500
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "free"

        second = _post_webhook(client, event, revenuecat=revenuecat)
        assert second.status_code == 200
        assert second.json()["plan"] == "basic"
        assert revenuecat.calls == [USER_ID, USER_ID]


@pytest.mark.parametrize(
    ("event_type", "active_ids", "expected_plan"),
    [
        ("CANCELLATION", ["basic", "pro"], "pro"),
        ("REFUND", ["basic"], "basic"),
        ("EXPIRATION", ["pro"], "pro"),
    ],
)
def test_lifecycle_event_always_uses_full_customer_info(
    event_type: str, active_ids: list[str], expected_plan: str
) -> None:
    with TestClient(app) as client:
        event = {
            "type": event_type,
            "id": f"lifecycle-{event_type.lower()}",
            "app_user_id": USER_ID,
            "entitlement_ids": [],
        }
        response = _post_webhook(
            client,
            event,
            customer_info=_customer_info(active_ids),
        )

        assert response.status_code == 200
        assert response.json()["plan"] == expected_plan


def test_older_customer_info_cannot_overwrite_newer_plan() -> None:
    with TestClient(app) as client:
        now = datetime.now(UTC)
        _post_webhook(
            client,
            _purchase_event("pro", event_id="newer-snapshot"),
            customer_info=_customer_info(["pro"], request_date=now),
        )
        stale = _post_webhook(
            client,
            _purchase_event("basic", event_id="older-snapshot"),
            customer_info=_customer_info(
                ["basic"], request_date=now - timedelta(minutes=1)
            ),
        )

        assert stale.status_code == 200
        assert client.get("/v1/capabilities", headers=_headers()).json()["plan"] == "pro"


@pytest.mark.asyncio
async def test_revenuecat_client_url_encodes_user_id_and_parses_active_entitlements() -> None:
    seen_path: bytes | None = None
    request_date = datetime(2026, 8, 1, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_path
        seen_path = request.url.raw_path
        return httpx.Response(
            200,
            json={
                "request_date": request_date.isoformat(),
                "subscriber": {
                    "entitlements": {
                        "basic": {
                            "expires_date": (request_date - timedelta(days=1)).isoformat(),
                            "grace_period_expires_date": (
                                request_date + timedelta(days=1)
                            ).isoformat(),
                        },
                        "pro": {
                            "expires_date": (request_date + timedelta(days=2)).isoformat(),
                            "grace_period_expires_date": None,
                        },
                        "expired": {
                            "expires_date": (request_date - timedelta(days=2)).isoformat(),
                            "grace_period_expires_date": None,
                        },
                    }
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        revenuecat = RevenueCatClient(
            secret_api_key=SecretStr("placeholder"),
            http_client=http_client,
        )
        info = await revenuecat.get_customer_info("user/+?# %")

    assert seen_path == b"/v1/subscribers/user%2F%2B%3F%23%20%25"
    assert info.active_entitlement_ids == ["basic", "pro"]
    assert info.active_entitlements["basic"] == request_date + timedelta(days=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_gateway"),
    [(401, 502), (403, 502), (404, 502), (429, 503), (500, 503)],
)
async def test_revenuecat_client_maps_http_errors(
    status_code: int, expected_gateway: int
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code, json={"error": "omitted"})
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        revenuecat = RevenueCatClient(
            secret_api_key=SecretStr("placeholder"),
            http_client=http_client,
        )
        with pytest.raises(RevenueCatAPIError) as captured:
            await revenuecat.get_customer_info(USER_ID)

    assert captured.value.gateway_status == expected_gateway
    assert captured.value.upstream_status == status_code


@pytest.mark.asyncio
async def test_revenuecat_client_rejects_invalid_json() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"{not-json")
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        revenuecat = RevenueCatClient(
            secret_api_key=SecretStr("placeholder"),
            http_client=http_client,
        )
        with pytest.raises(RevenueCatAPIError) as captured:
            await revenuecat.get_customer_info(USER_ID)

    assert captured.value.code == "invalid_response"
    assert captured.value.gateway_status == 502


@pytest.mark.asyncio
async def test_revenuecat_client_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        revenuecat = RevenueCatClient(
            secret_api_key=SecretStr("placeholder"),
            http_client=http_client,
        )
        with pytest.raises(RevenueCatAPIError) as captured:
            await revenuecat.get_customer_info(USER_ID)

    assert captured.value.code == "timeout"
    assert captured.value.gateway_status == 503


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


def test_basic_plan_gets_three_daily_sets() -> None:
    """토큰 모델: 토큰 = 하루 새 문제 세트 수. 베이직은 하루 3세트."""
    refresh_payload = {
        "targetLevel": "IH",
        "background": {"interests": ["news"]},
        "adjustment": "same",
    }
    with TestClient(app) as client:
        _post_webhook(client, _purchase_event("basic", event_id="b-quota"))
        # 초기 데일리 세트 = 토큰 1개.
        form = _make_practice_form(client)
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["freeRemaining"] == 2

        # 세트 내 평가는 무제한(토큰 미소모).
        for _ in range(3):
            response = client.post(
                "/v1/evaluations/practice",
                headers=_headers(str(uuid.uuid4())),
                data=form,
            )
            assert response.status_code == 200, response.text
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["freeRemaining"] == 2  # 평가는 토큰을 쓰지 않음

        # 리프레시 2회 = 토큰 2개(총 3세트 소진).
        for index in range(2):
            refreshed = client.post(
                "/v1/question-sets/practice/refresh",
                headers={**_headers(), "Idempotency-Key": f"basic-refresh-{index}"},
                json=refresh_payload,
            )
            assert refreshed.status_code == 200, refreshed.text
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["freeRemaining"] == 0

        # 4번째 세트는 토큰 소진으로 402.
        blocked = client.post(
            "/v1/question-sets/practice/refresh",
            headers={**_headers(), "Idempotency-Key": "basic-refresh-blocked"},
            json=refresh_payload,
        )
        assert blocked.status_code == 402
        assert blocked.json()["detail"]["code"] == "practice_quota_exhausted"


def test_target_level_change_limited_to_once_per_day() -> None:
    with TestClient(app) as client:
        # 최초 설정은 리워드 불필요.
        first = client.put(
            "/v1/users/me/target-level", headers=_headers(), json={"targetLevel": "IH"}
        )
        assert first.status_code == 200, first.text

        # 변경은 리워드 필요.
        blocked = client.put(
            "/v1/users/me/target-level", headers=_headers(), json={"targetLevel": "AL"}
        )
        assert blocked.status_code == 402

        # 1회차: 리워드 발급·검증 후 변경 성공.
        reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "target_level_change"},
        )
        assert reward.status_code == 200, reward.text
        _verify_reward(client, reward.json()["nonce"])
        changed = client.put(
            "/v1/users/me/target-level",
            headers=_headers(),
            json={"targetLevel": "AL", "rewardNonce": reward.json()["nonce"]},
        )
        assert changed.status_code == 200, changed.text

        # 같은 날 2회차: 난이도 변경 리워드는 하루 1회 → 402.
        second = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "target_level_change"},
        )
        assert second.status_code == 402
        assert second.json()["detail"]["code"] == "reward_quota_exhausted"


def test_mock_remaining_reflects_plan() -> None:
    with TestClient(app) as client:
        # 무료: 하루 1회.
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["mockRemaining"] == 1
        assert usage["mockAvailable"] is True
        # 플러스: 하루 3회.
        _post_webhook(client, _purchase_event("plus", event_id="mock-rem"))
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["mockRemaining"] == 3
        assert usage["mockAvailable"] is True


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
