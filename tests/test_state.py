from datetime import UTC, datetime, timedelta

import pytest

from app.models.api import RewardPurpose
from app.services.state import InMemoryStateStore, RewardNotVerified, UsageLimitExceeded


@pytest.mark.asyncio
async def test_question_set_is_bound_to_user_and_mode() -> None:
    store = InMemoryStateStore()
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    questions = [{"number": 1, "prompt": "Please introduce yourself."}]

    await store.save_question_set(
        uid="u1",
        set_id="set-1",
        mode="practice",
        target_level="IH",
        question_hash="hash-1",
        questions=questions,
        expires_at=expires_at,
    )

    saved = await store.get_question_set(uid="u1", set_id="set-1", mode="practice")

    assert saved is not None
    assert saved["questionHash"] == "hash-1"
    assert await store.get_question_set(uid="u2", set_id="set-1", mode="practice") is None
    assert await store.get_question_set(uid="u1", set_id="set-1", mode="mock") is None


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
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        auto_verify=True,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )
    usage = await store.get_usage("u1", "20260622")
    assert usage["bonusRemaining"] == 1
    reservation = await store.reserve_practice("u1", "20260622", "request-5", 3)
    assert reservation.source == "bonus"
    usage = await store.get_usage("u1", "20260622")
    assert usage["bonusRemaining"] == 0


@pytest.mark.asyncio
async def test_mock_reward_is_bound_and_single_use() -> None:
    store = InMemoryStateStore()
    await store.create_reward_intent(
        nonce="mock-nonce-123456789",
        uid="u1",
        purpose=RewardPurpose.MOCK_RESULT,
        session_hash="hash-1",
        date_key="20260622",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        auto_verify=True,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )
    await store.reserve_mock("u1", "mock-request-1", "mock-nonce-123456789", "hash-1")
    with pytest.raises(RewardNotVerified):
        await store.reserve_mock("u1", "mock-request-2", "mock-nonce-123456789", "hash-1")


@pytest.mark.asyncio
async def test_target_level_change_requires_verified_reward_and_consumes_it() -> None:
    store = InMemoryStateStore()

    initial = await store.set_target_level(uid="u1", target_level="IH", reward_nonce=None)
    assert initial["changed"] is True
    assert initial["rewardConsumed"] is False
    assert await store.get_target_level("u1") == "IH"

    same = await store.set_target_level(uid="u1", target_level="IH", reward_nonce=None)
    assert same["changed"] is False
    assert same["rewardConsumed"] is False

    with pytest.raises(RewardNotVerified):
        await store.set_target_level(uid="u1", target_level="AL", reward_nonce=None)

    await store.create_reward_intent(
        nonce="target-level-nonce-123456",
        uid="u1",
        purpose=RewardPurpose.TARGET_LEVEL_CHANGE,
        session_hash=None,
        date_key="20260622",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        auto_verify=True,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )
    usage = await store.get_usage("u1", "20260622")
    assert usage["bonusRemaining"] == 0

    changed = await store.set_target_level(
        uid="u1", target_level="AL", reward_nonce="target-level-nonce-123456"
    )
    assert changed["previousTargetLevel"] == "IH"
    assert changed["targetLevel"] == "AL"
    assert changed["rewardConsumed"] is True

    with pytest.raises(RewardNotVerified):
        await store.set_target_level(
            uid="u1", target_level="IM3", reward_nonce="target-level-nonce-123456"
        )


@pytest.mark.asyncio
async def test_daily_reward_intents_are_limited() -> None:
    store = InMemoryStateStore()
    for index in range(3):
        await store.create_reward_intent(
            nonce=f"reward-nonce-{index}",
            uid="u1",
            purpose=RewardPurpose.PRACTICE_CREDITS,
            session_hash=None,
            date_key="20260622",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            auto_verify=False,
            practice_credit_amount=1,
            max_daily_reward_count=3,
        )

    with pytest.raises(UsageLimitExceeded):
        await store.create_reward_intent(
            nonce="reward-nonce-over-limit",
            uid="u1",
            purpose=RewardPurpose.PRACTICE_CREDITS,
            session_hash=None,
            date_key="20260622",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            auto_verify=False,
            practice_credit_amount=1,
            max_daily_reward_count=3,
        )


@pytest.mark.asyncio
async def test_target_level_change_intents_do_not_use_practice_reward_quota() -> None:
    store = InMemoryStateStore()
    for index in range(3):
        await store.create_reward_intent(
            nonce=f"reward-nonce-{index}",
            uid="u1",
            purpose=RewardPurpose.PRACTICE_CREDITS,
            session_hash=None,
            date_key="20260622",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            auto_verify=False,
            practice_credit_amount=1,
            max_daily_reward_count=3,
        )

    target_reward = await store.create_reward_intent(
        nonce="target-level-over-practice-quota",
        uid="u1",
        purpose=RewardPurpose.TARGET_LEVEL_CHANGE,
        session_hash=None,
        date_key="20260622",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        auto_verify=False,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )

    assert target_reward["purpose"] is RewardPurpose.TARGET_LEVEL_CHANGE
    usage = await store.get_usage("u1", "20260622")
    assert usage["rewardCount"] == 3
    assert usage["bonusRemaining"] == 0


@pytest.mark.asyncio
async def test_reward_transaction_replay_is_rejected() -> None:
    store = InMemoryStateStore()
    await store.create_reward_intent(
        nonce="reward-nonce-replay",
        uid="u1",
        purpose=RewardPurpose.PRACTICE_CREDITS,
        session_hash=None,
        date_key="20260622",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        auto_verify=False,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )

    await store.verify_reward(
        nonce="reward-nonce-replay",
        transaction_id="tx-1",
        practice_credit_amount=1,
    )
    with pytest.raises(RewardNotVerified):
        await store.verify_reward(
            nonce="reward-nonce-replay",
            transaction_id="tx-1",
            practice_credit_amount=1,
        )
