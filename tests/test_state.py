import pytest

from app.models.api import RewardPurpose
from app.services.state import InMemoryStateStore, RewardNotVerified, UsageLimitExceeded


@pytest.mark.asyncio
async def test_three_free_then_bonus_credits() -> None:
    store = InMemoryStateStore()
    for index in range(3):
        reservation = await store.reserve_practice("u1", "20260622", f"request-{index}", 3)
        assert reservation.source == "free"

    with pytest.raises(UsageLimitExceeded):
        await store.reserve_practice("u1", "20260622", "request-4", 3)

    await store.create_reward_intent(
        nonce="reward-nonce-123456",
        uid="u1",
        purpose=RewardPurpose.PRACTICE_CREDITS,
        session_hash=None,
        date_key="20260622",
        expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC)
        + __import__("datetime").timedelta(minutes=30),
        auto_verify=True,
        practice_credit_amount=3,
    )
    usage = await store.get_usage("u1", "20260622")
    assert usage["bonusRemaining"] == 3
    reservation = await store.reserve_practice("u1", "20260622", "request-5", 3)
    assert reservation.source == "bonus"


@pytest.mark.asyncio
async def test_mock_reward_is_bound_and_single_use() -> None:
    from datetime import UTC, datetime, timedelta

    store = InMemoryStateStore()
    await store.create_reward_intent(
        nonce="mock-nonce-123456789",
        uid="u1",
        purpose=RewardPurpose.MOCK_RESULT,
        session_hash="hash-1",
        date_key="20260622",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        auto_verify=True,
        practice_credit_amount=3,
    )
    await store.reserve_mock("u1", "mock-request-1", "mock-nonce-123456789", "hash-1")
    with pytest.raises(RewardNotVerified):
        await store.reserve_mock("u1", "mock-request-2", "mock-nonce-123456789", "hash-1")
