import pytest

from app.services.admob import AdMobSSVVerifier, SSVVerificationError


@pytest.mark.asyncio
async def test_ssv_parses_required_callback_fields_when_signature_is_not_required() -> None:
    verifier = AdMobSSVVerifier(
        required=False,
        expected_ad_unit="ca-app-pub-5460686409666356/7091483531",
    )

    reward = await verifier.verify(
        "ad_unit=ca-app-pub-5460686409666356/7091483531"
        "&custom_data=reward-nonce"
        "&transaction_id=tx-1"
        "&key_id=123"
        "&signature=fake"
        "&user_id=u1"
    )

    assert reward.nonce == "reward-nonce"
    assert reward.transaction_id == "tx-1"
    assert reward.user_id == "u1"
    assert reward.ad_unit == "ca-app-pub-5460686409666356/7091483531"


@pytest.mark.asyncio
async def test_ssv_rejects_unexpected_rewarded_ad_unit() -> None:
    verifier = AdMobSSVVerifier(
        required=False,
        expected_ad_unit="ca-app-pub-5460686409666356/7091483531",
    )

    with pytest.raises(SSVVerificationError):
        await verifier.verify(
            "ad_unit=ca-app-pub-example/wrong"
            "&custom_data=reward-nonce"
            "&transaction_id=tx-1"
            "&key_id=123"
            "&signature=fake"
            "&user_id=u1"
        )


def test_ssv_requires_rewarded_unit_when_enabled() -> None:
    with pytest.raises(ValueError):
        AdMobSSVVerifier(required=True, expected_ad_unit=None)
