from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote_plus

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


KEYS_URL = "https://www.gstatic.com/admob/reward/verifier-keys.json"


class SSVVerificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedReward:
    nonce: str
    transaction_id: str
    user_id: str | None
    ad_unit: str | None


class AdMobSSVVerifier:
    def __init__(self, *, expected_ad_unit: str) -> None:
        if not expected_ad_unit:
            raise ValueError("ADMOB_REWARDED_AD_UNIT_ID is required")
        self._expected_ad_unit = expected_ad_unit
        self._keys: dict[int, str] = {}
        self._keys_expire_at = 0.0
        self._lock = asyncio.Lock()

    async def _get_key(self, key_id: int) -> str:
        async with self._lock:
            if time.time() >= self._keys_expire_at:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(KEYS_URL)
                    response.raise_for_status()
                self._keys = {
                    int(item["keyId"]): item["pem"] for item in response.json().get("keys", [])
                }
                self._keys_expire_at = time.time() + 23 * 60 * 60
            try:
                return self._keys[key_id]
            except KeyError as error:
                raise SSVVerificationError("unknown AdMob SSV key") from error

    async def verify(self, raw_query: str) -> VerifiedReward:
        params = parse_qs(raw_query, keep_blank_values=True)
        try:
            signature_text = params["signature"][0]
            key_id = int(params["key_id"][0])
            transaction_id = params["transaction_id"][0]
            nonce = unquote_plus(params["custom_data"][0])
        except (KeyError, ValueError, IndexError) as error:
            raise SSVVerificationError("required SSV parameters are missing") from error

        marker = "&signature="
        marker_index = raw_query.rfind(marker)
        if marker_index < 0:
            raise SSVVerificationError("invalid signed SSV query")
        signed_content = raw_query[:marker_index].encode("utf-8")
        padding = "=" * (-len(signature_text) % 4)
        try:
            signature = base64.urlsafe_b64decode(signature_text + padding)
            pem = await self._get_key(key_id)
            public_key = serialization.load_pem_public_key(pem.encode("utf-8"))
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise SSVVerificationError("invalid SSV public key")
            public_key.verify(signature, signed_content, ec.ECDSA(hashes.SHA256()))
        except (ValueError, InvalidSignature) as error:
            raise SSVVerificationError("invalid AdMob SSV signature") from error

        ad_unit = params.get("ad_unit", [None])[0]
        if ad_unit != self._expected_ad_unit:
            raise SSVVerificationError("unexpected rewarded ad unit")
        return VerifiedReward(
            nonce=nonce,
            transaction_id=transaction_id,
            user_id=params.get("user_id", [None])[0],
            ad_unit=ad_unit,
        )
