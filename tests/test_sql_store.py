import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.models.api import RewardPurpose
from app.services.sql_store import SqlAlchemyStateStore
from app.services.state import RewardNotVerified, UsageLimitExceeded


@pytest.fixture
def store(tmp_path) -> SqlAlchemyStateStore:
    return SqlAlchemyStateStore(f"sqlite:///{tmp_path/'test.db'}")


@pytest.mark.asyncio
async def test_question_set_is_bound_to_user_and_mode(store: SqlAlchemyStateStore) -> None:
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    questions = [{"number": 1, "prompt": "Please introduce yourself."}]

    await store.save_question_set(
        uid="u1",
        set_id="set-1",
        mode="practice",
        target_level="IH",
        initial_level=5,
        adjustment=None,
        effective_level=5,
        effective_level_code="5-5",
        status="awaiting_adjustment",
        front_question_count=7,
        background={},
        survey=None,
        question_hash="hash-1",
        questions=questions,
        expires_at=expires_at,
    )

    saved = await store.get_question_set(uid="u1", set_id="set-1", mode="practice")

    assert saved is not None
    assert saved["questionHash"] == "hash-1"
    assert saved["questions"] == questions
    assert await store.get_question_set(uid="u2", set_id="set-1", mode="practice") is None
    assert await store.get_question_set(uid="u1", set_id="set-1", mode="mock") is None


@pytest.mark.asyncio
async def test_expired_question_set_is_hidden(store: SqlAlchemyStateStore) -> None:
    await store.save_question_set(
        uid="u1",
        set_id="expired",
        mode="mock",
        target_level="IH",
        initial_level=5,
        adjustment=None,
        effective_level=5,
        effective_level_code="5-5",
        status="complete",
        front_question_count=7,
        background={},
        survey=None,
        question_hash="hash-x",
        questions=[],
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert await store.get_question_set(uid="u1", set_id="expired", mode="mock") is None


@pytest.mark.asyncio
async def test_question_history_records_recent_hashes_and_trims(
    store: SqlAlchemyStateStore,
) -> None:
    for index in range(90):
        await store.record_question_history(
            uid="u1",
            mode="practice",
            set_hash=f"set-{index}",
            questions=[
                {
                    "topicId": f"topic_{index}",
                    "prompt": f"Describe a unique situation number {index}.",
                }
            ],
        )

    history = await store.get_question_history(uid="u1", mode="practice")

    assert len(history["setHashes"]) == 80
    assert len(history["topicIds"]) == 80
    assert history["setHashes"][0] == "set-10"
    assert history["topicIds"][0] == "topic_10"
    assert history["setHashes"][-1] == "set-89"


@pytest.mark.asyncio
async def test_three_free_then_bonus_credits(store: SqlAlchemyStateStore) -> None:
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
async def test_failed_request_rolls_back_usage(store: SqlAlchemyStateStore) -> None:
    await store.reserve_practice("u1", "20260622", "req-fail", 3)
    assert (await store.get_usage("u1", "20260622"))["freeUsed"] == 1
    await store.fail_request("req-fail")
    assert (await store.get_usage("u1", "20260622"))["freeUsed"] == 0


@pytest.mark.asyncio
async def test_reserve_practice_is_idempotent(store: SqlAlchemyStateStore) -> None:
    await store.reserve_practice("u1", "20260622", "same-key", 3)
    await store.finalize_request("same-key", {"ok": True}, ttl_hours=24)
    cached = await store.reserve_practice("u1", "20260622", "same-key", 3)
    assert cached.status == "cached"
    assert cached.result == {"ok": True}
    # 캐시 반환은 쿼터를 추가로 소모하지 않는다.
    assert (await store.get_usage("u1", "20260622"))["freeUsed"] == 1


@pytest.mark.asyncio
async def test_mock_reward_is_bound_and_single_use(store: SqlAlchemyStateStore) -> None:
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
async def test_target_level_change_requires_verified_reward_and_consumes_it(
    store: SqlAlchemyStateStore,
) -> None:
    initial = await store.set_target_level(uid="u1", target_level="IH", reward_nonce=None)
    assert initial["changed"] is True
    assert initial["rewardConsumed"] is False
    assert await store.get_target_level("u1") == "IH"

    same = await store.set_target_level(uid="u1", target_level="IH", reward_nonce=None)
    assert same["changed"] is False

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
async def test_daily_reward_intents_are_limited(store: SqlAlchemyStateStore) -> None:
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
async def test_reward_transaction_replay_is_rejected(store: SqlAlchemyStateStore) -> None:
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
    verified = await store.verify_reward(
        nonce="reward-nonce-replay", transaction_id="tx-1", practice_credit_amount=1
    )
    assert verified["status"] == "verified"
    assert verified["credited"] is True
    assert (await store.get_usage("u1", "20260622"))["bonusRemaining"] == 1

    with pytest.raises(RewardNotVerified):
        await store.verify_reward(
            nonce="reward-nonce-replay", transaction_id="tx-1", practice_credit_amount=1
        )


@pytest.mark.asyncio
async def test_parallel_free_reserves_respect_limit(store: SqlAlchemyStateStore) -> None:
    async def reserve(index: int) -> bool:
        try:
            await store.reserve_practice("u1", "20260622", f"req-{index}", 3)
            return True
        except UsageLimitExceeded:
            return False

    results = await asyncio.gather(*(reserve(i) for i in range(10)))
    assert sum(results) == 3
    assert (await store.get_usage("u1", "20260622"))["freeUsed"] == 3
