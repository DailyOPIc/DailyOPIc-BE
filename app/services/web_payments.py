"""웹 결제(토스페이먼츠 일반결제): 상품 목록, 지급·회수 규칙, 토스 API 클라이언트.

구독 자동갱신 없이 "30일 이용권"과 "토큰 팩"을 한 번씩 판다. 금액의 기준은 항상 서버다
(클라이언트가 보낸 금액은 주문 금액과 대조만 한다).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import SecretStr

from app.services.plans import Plan

TOSS_API = "https://api.tosspayments.com/v1"


@dataclass(frozen=True, slots=True)
class WebProduct:
    id: str
    name: str
    amount: int  # 원(부가세 포함)
    grant: dict[str, Any]  # {"type": "pass", "plan", "days"} | {"type": "tokens", "amount"}


# 가격·구성은 여기 한 곳에서만 바꾼다. 웹 요금제 화면(src/site.ts)의 표시 가격도 함께 맞춘다.
WEB_PRODUCTS: dict[str, WebProduct] = {
    product.id: product
    for product in (
        WebProduct("pass_basic_30d", "베이직 30일 이용권", 3900, {"type": "pass", "plan": "basic", "days": 30}),
        WebProduct("pass_plus_30d", "플러스 30일 이용권", 6900, {"type": "pass", "plan": "plus", "days": 30}),
        WebProduct("pass_pro_30d", "프로 30일 이용권", 9900, {"type": "pass", "plan": "pro", "days": 30}),
        WebProduct("tokens_10", "데일리 토큰 10개", 2900, {"type": "tokens", "amount": 10}),
        WebProduct("tokens_30", "데일리 토큰 30개", 6900, {"type": "tokens", "amount": 30}),
    )
}


def empty_wallet() -> dict[str, Any]:
    return {"webPass": None, "purchasedTokens": 0}


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def web_pass_plan(wallet: dict[str, Any] | None, now: datetime | None = None) -> Plan:
    """만료되지 않은 웹 이용권의 플랜. 없거나 만료면 free."""
    web_pass = (wallet or {}).get("webPass") or {}
    expires_at = _as_utc(web_pass.get("expiresAt"))
    if not expires_at or expires_at <= (now or datetime.now(UTC)):
        return Plan.FREE
    return Plan(web_pass.get("plan") or "free")


def apply_grant(wallet: dict[str, Any], grant: dict[str, Any], now: datetime) -> dict[str, Any]:
    """결제 1건을 지갑에 반영한 새 지갑을 돌려준다.

    같은 플랜 이용권이 남아 있으면 남은 기간 뒤에 이어 붙이고, 다른 플랜이면 지금부터 새로 시작한다.
    ponytail: 높은 플랜 사용 중 낮은 플랜을 사면 남은 기간이 사라진다. 화면에서 그런 구매를 막는다.
    """
    wallet = {**empty_wallet(), **wallet}
    if grant["type"] == "tokens":
        wallet["purchasedTokens"] = int(wallet.get("purchasedTokens") or 0) + int(grant["amount"])
        return wallet
    current = wallet.get("webPass") or {}
    current_expiry = _as_utc(current.get("expiresAt"))
    start = now
    if current.get("plan") == grant["plan"] and current_expiry and current_expiry > now:
        start = current_expiry
    wallet["webPass"] = {"plan": grant["plan"], "expiresAt": start + timedelta(days=int(grant["days"]))}
    return wallet


def revoke_grant(wallet: dict[str, Any], grant: dict[str, Any], now: datetime) -> dict[str, Any]:
    """환불된 결제 1건을 되돌린다(이미 쓴 토큰은 남은 만큼만 회수)."""
    wallet = {**empty_wallet(), **wallet}
    if grant["type"] == "tokens":
        wallet["purchasedTokens"] = max(0, int(wallet.get("purchasedTokens") or 0) - int(grant["amount"]))
        return wallet
    current = wallet.get("webPass") or {}
    expiry = _as_utc(current.get("expiresAt"))
    if current.get("plan") == grant["plan"] and expiry:
        shortened = expiry - timedelta(days=int(grant["days"]))
        wallet["webPass"] = {**current, "expiresAt": shortened} if shortened > now else None
    return wallet


class TossPaymentError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class TossClient:
    """토스페이먼츠 결제 승인·조회. 시크릿 키는 서버 환경변수로만 받는다."""

    def __init__(self, secret_key: SecretStr | None, http_client: httpx.AsyncClient) -> None:
        self._secret_key = secret_key
        self._http = http_client

    @property
    def configured(self) -> bool:
        return self._secret_key is not None

    def _headers(self) -> dict[str, str]:
        if self._secret_key is None:
            raise TossPaymentError("toss_not_configured", "결제가 아직 준비되지 않았어요.", 503)
        token = base64.b64encode(f"{self._secret_key.get_secret_value()}:".encode()).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    async def confirm(self, *, payment_key: str, order_id: str, amount: int) -> dict[str, Any]:
        response = await self._http.post(
            f"{TOSS_API}/payments/confirm",
            headers={**self._headers(), "Idempotency-Key": f"confirm-{order_id}"},
            json={"paymentKey": payment_key, "orderId": order_id, "amount": amount},
        )
        return self._parse(response)

    async def get_payment(self, payment_key: str) -> dict[str, Any]:
        response = await self._http.get(f"{TOSS_API}/payments/{payment_key}", headers=self._headers())
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        data = response.json() if response.content else {}
        if response.is_success:
            return data
        raise TossPaymentError(
            str(data.get("code") or "toss_error"),
            str(data.get("message") or "결제를 처리하지 못했어요."),
            response.status_code,
        )


def _self_check() -> None:
    now = datetime(2026, 11, 21, tzinfo=UTC)
    wallet = apply_grant(empty_wallet(), WEB_PRODUCTS["pass_plus_30d"].grant, now)
    assert web_pass_plan(wallet, now) is Plan.PLUS
    extended = apply_grant(wallet, WEB_PRODUCTS["pass_plus_30d"].grant, now)
    assert extended["webPass"]["expiresAt"] == now + timedelta(days=60), "same plan extends"
    switched = apply_grant(extended, WEB_PRODUCTS["pass_pro_30d"].grant, now)
    assert switched["webPass"] == {"plan": "pro", "expiresAt": now + timedelta(days=30)}
    assert web_pass_plan(switched, now + timedelta(days=31)) is Plan.FREE, "expires"
    revoked = revoke_grant(extended, WEB_PRODUCTS["pass_plus_30d"].grant, now)
    assert revoked["webPass"]["expiresAt"] == now + timedelta(days=30)
    assert revoke_grant(wallet, WEB_PRODUCTS["pass_plus_30d"].grant, now)["webPass"] is None
    tokens = apply_grant(empty_wallet(), WEB_PRODUCTS["tokens_10"].grant, now)
    assert tokens["purchasedTokens"] == 10
    assert revoke_grant({**tokens, "purchasedTokens": 4}, WEB_PRODUCTS["tokens_10"].grant, now)["purchasedTokens"] == 0


if __name__ == "__main__":
    _self_check()
    print("web_payments self-check ok")
