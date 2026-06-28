import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.api import RewardPurpose
from app.services.state import FirestoreStateStore, RewardNotVerified, UsageLimitExceeded


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="FIRESTORE_EMULATOR_HOST is required",
)


@pytest.mark.asyncio
async def test_firestore_question_set_is_bound_to_user_and_mode() -> None:
    uid = f"question-set-{uuid.uuid4()}"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))
    set_id = f"set-{uuid.uuid4()}"

    await store.save_question_set(
        uid=uid,
        set_id=set_id,
        mode="practice",
        target_level="IH",
        question_hash="hash-1",
        questions=[{"number": 1, "prompt": "Please introduce yourself."}],
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )

    saved = await store.get_question_set(uid=uid, set_id=set_id, mode="practice")

    assert saved is not None
    assert saved["questionHash"] == "hash-1"
    assert await store.get_question_set(uid=f"{uid}-other", set_id=set_id, mode="practice") is None
    assert await store.get_question_set(uid=uid, set_id=set_id, mode="mock") is None


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
            practice_credit_amount=1,
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
            practice_credit_amount=1,
            max_daily_reward_count=3,
        )


@pytest.mark.asyncio
async def test_firestore_target_level_change_consumes_verified_reward() -> None:
    uid = f"target-level-{uuid.uuid4()}"
    date_key = "20260622"
    nonce = f"{uid}-target-change"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))

    initial = await store.set_target_level(uid=uid, target_level="IH", reward_nonce=None)
    assert initial["targetLevel"] == "IH"
    assert initial["rewardConsumed"] is False

    with pytest.raises(RewardNotVerified):
        await store.set_target_level(uid=uid, target_level="AL", reward_nonce=None)

    await store.create_reward_intent(
        nonce=nonce,
        uid=uid,
        purpose=RewardPurpose.TARGET_LEVEL_CHANGE,
        session_hash=None,
        date_key=date_key,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        auto_verify=True,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )
    usage = await store.get_usage(uid, date_key)
    assert usage["bonusRemaining"] == 0

    changed = await store.set_target_level(
        uid=uid, target_level="AL", reward_nonce=nonce
    )
    assert changed["previousTargetLevel"] == "IH"
    assert changed["targetLevel"] == "AL"
    assert changed["rewardConsumed"] is True

    with pytest.raises(RewardNotVerified):
        await store.set_target_level(uid=uid, target_level="IM3", reward_nonce=nonce)
