from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr


REVENUECAT_API_BASE_URL = "https://api.revenuecat.com"


class RevenueCatAPIError(RuntimeError):
    """Safe-to-log RevenueCat API failure metadata without response or identity data."""

    def __init__(
        self,
        code: str,
        *,
        gateway_status: int,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.gateway_status = gateway_status
        self.upstream_status = upstream_status


@dataclass(frozen=True, slots=True)
class RevenueCatCustomerInfo:
    request_date: datetime
    active_entitlements: dict[str, datetime | None]

    @property
    def active_entitlement_ids(self) -> list[str]:
        return sorted(self.active_entitlements)

    def effective_expiration_for(self, entitlement_ids: list[str]) -> datetime | None:
        expirations = [
            self.active_entitlements[identifier]
            for identifier in entitlement_ids
            if identifier in self.active_entitlements
        ]
        if not expirations or any(value is None for value in expirations):
            return None
        return max(value for value in expirations if value is not None)


class RevenueCatClient:
    def __init__(
        self,
        *,
        secret_api_key: SecretStr | None,
        http_client: httpx.AsyncClient,
        base_url: str = REVENUECAT_API_BASE_URL,
    ) -> None:
        self._secret_api_key = secret_api_key
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")

    async def get_customer_info(self, app_user_id: str) -> RevenueCatCustomerInfo:
        if self._secret_api_key is None:
            raise RevenueCatAPIError("not_configured", gateway_status=503)

        encoded_user_id = quote(app_user_id, safe="")
        try:
            response = await self._http_client.get(
                f"{self._base_url}/v1/subscribers/{encoded_user_id}",
                headers={
                    "Accept": "application/json",
                    "Authorization": (
                        f"Bearer {self._secret_api_key.get_secret_value()}"
                    ),
                },
            )
        except httpx.TimeoutException as error:
            raise RevenueCatAPIError("timeout", gateway_status=503) from error
        except httpx.RequestError as error:
            raise RevenueCatAPIError("network_error", gateway_status=503) from error

        if response.status_code == 429 or response.status_code >= 500:
            raise RevenueCatAPIError(
                "upstream_unavailable",
                gateway_status=503,
                upstream_status=response.status_code,
            )
        if response.status_code in {401, 403}:
            raise RevenueCatAPIError(
                "upstream_auth_error",
                gateway_status=502,
                upstream_status=response.status_code,
            )
        if response.status_code == 404:
            raise RevenueCatAPIError(
                "subscriber_not_found",
                gateway_status=502,
                upstream_status=response.status_code,
            )
        if not 200 <= response.status_code < 300:
            raise RevenueCatAPIError(
                "upstream_http_error",
                gateway_status=502,
                upstream_status=response.status_code,
            )

        try:
            payload = response.json()
            return self._parse_customer_info(payload)
        except (TypeError, ValueError, KeyError) as error:
            raise RevenueCatAPIError("invalid_response", gateway_status=502) from error

    @classmethod
    def _parse_customer_info(cls, payload: Any) -> RevenueCatCustomerInfo:
        if not isinstance(payload, dict):
            raise TypeError("response must be an object")
        request_date = cls._parse_datetime(payload["request_date"])
        subscriber = payload["subscriber"]
        if not isinstance(subscriber, dict):
            raise TypeError("subscriber must be an object")
        entitlements = subscriber["entitlements"]
        if not isinstance(entitlements, dict):
            raise TypeError("entitlements must be an object")

        active: dict[str, datetime | None] = {}
        for identifier, raw in entitlements.items():
            if not isinstance(identifier, str) or not isinstance(raw, dict):
                raise TypeError("invalid entitlement entry")
            if "expires_date" not in raw:
                raise KeyError("expires_date")
            expires_at = cls._parse_optional_datetime(raw["expires_date"])
            grace_expires_at = cls._parse_optional_datetime(
                raw.get("grace_period_expires_date")
            )

            if expires_at is None:
                active[identifier] = None
                continue

            effective_expiration = max(
                value
                for value in (expires_at, grace_expires_at)
                if value is not None
            )
            if effective_expiration > request_date:
                active[identifier] = effective_expiration

        return RevenueCatCustomerInfo(
            request_date=request_date,
            active_entitlements=active,
        )

    @staticmethod
    def _parse_optional_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        return RevenueCatClient._parse_datetime(value)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if not isinstance(value, str):
            raise TypeError("date must be a string")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("date must include a timezone")
        return parsed.astimezone(UTC)
