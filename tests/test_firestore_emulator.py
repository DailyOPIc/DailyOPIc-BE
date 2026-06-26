import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.api import RewardPurpose
from app.services.state import FirestoreStateStore, UsageLimitExceeded


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="FIRESTORE_EMULATOR_HOST is required",
)


@pytest.mark.asyncio
async def test_firestore_transaction_allows_exactly_three_parallel_free_uses() -> None:
    uid = f"emulator-{uuid.uuid4()}"
    date_key = "20260622"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))

    async def reserve(index: int) -> bool:
        try:
            await store.reserve_practice(uid, date_key, f"{uid}-request-{index}", 3)
            return True
        except UsageLimitExceeded:
            return False

    results = await asyncio.gather(*(reserve(index) for index in range(10)))
    assert sum(results) == 3
    usage = await store.get_usage(uid, date_key)
    assert usage["freeUsed"] == 3


@pytest.mark.asyncio
async def test_firestore_reward_intents_respect_daily_limit() -> None:
    uid = f"reward-emulator-{uuid.uuid4()}"
    date_key = "20260622"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))

    for index in range(3):
        await store.create_reward_intent(
            nonce=f"{uid}-reward-{index}",
            uid=uid,
            purpose=RewardPurpose.PRACTICE_CREDITS,
            session_hash=None,
            date_key=date_key,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            auto_verify=False,
            practice_credit_amount=3,
            max_daily_reward_count=3,
        )

    with pytest.raises(UsageLimitExceeded):
        await store.create_reward_intent(
            nonce=f"{uid}-reward-over-limit",
            uid=uid,
            purpose=RewardPurpose.PRACTICE_CREDITS,
            session_hash=None,
            date_key=date_key,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            auto_verify=False,
            practice_credit_amount=3,
            max_daily_reward_count=3,
        )
