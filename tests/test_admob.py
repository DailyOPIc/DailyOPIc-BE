import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services.admob import AdMobSSVVerifier, SSVVerificationError


AD_UNIT = "ca-app-pub-5460686409666356/7091483531"


def _private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _public_pem(private_key: ec.EllipticCurvePrivateKey) -> str:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def _signed_query(private_key: ec.EllipticCurvePrivateKey, payload: str) -> str:
    signature = private_key.sign(payload.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{payload}&signature={encoded}"


@pytest.mark.asyncio
async def test_ssv_parses_and_verifies_required_callback_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = _private_key()
    verifier = AdMobSSVVerifier(expected_ad_unit=AD_UNIT)

    async def get_key(key_id: int) -> str:
        return _public_pem(private_key)

    monkeypatch.setattr(verifier, "_get_key", get_key)
    query = _signed_query(
        private_key,
        f"ad_unit={AD_UNIT}&custom_data=reward-nonce&transaction_id=tx-1&key_id=123&user_id=u1",
    )

    reward = await verifier.verify(query)

    assert reward.nonce == "reward-nonce"
    assert reward.transaction_id == "tx-1"
    assert reward.user_id == "u1"
    assert reward.ad_unit == AD_UNIT


@pytest.mark.asyncio
async def test_ssv_rejects_unexpected_rewarded_ad_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = _private_key()
    verifier = AdMobSSVVerifier(expected_ad_unit=AD_UNIT)

    async def get_key(key_id: int) -> str:
        return _public_pem(private_key)

    monkeypatch.setattr(verifier, "_get_key", get_key)
    query = _signed_query(
        private_key,
        "ad_unit=ca-app-pub-example/wrong"
        "&custom_data=reward-nonce"
        "&transaction_id=tx-1"
        "&key_id=123"
        "&user_id=u1",
    )

    with pytest.raises(SSVVerificationError):
        await verifier.verify(query)


@pytest.mark.asyncio
async def test_ssv_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = AdMobSSVVerifier(expected_ad_unit=AD_UNIT)
    private_key = _private_key()
    other_key = _private_key()

    async def get_key(key_id: int) -> str:
        return _public_pem(other_key)

    monkeypatch.setattr(verifier, "_get_key", get_key)
    query = _signed_query(
        private_key,
        f"ad_unit={AD_UNIT}&custom_data=reward-nonce&transaction_id=tx-1&key_id=123&user_id=u1",
    )

    with pytest.raises(SSVVerificationError):
        await verifier.verify(query)


def test_ssv_requires_rewarded_unit() -> None:
    with pytest.raises(ValueError):
        AdMobSSVVerifier(expected_ad_unit="")
