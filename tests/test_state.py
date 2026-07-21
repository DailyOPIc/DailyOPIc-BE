from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter

from app.models.api import GeneratedQuestion, RewardPurpose
from app.services import state as state_module
from app.services.state import (
    FirestoreStateStore,
    InMemoryStateStore,
    InvalidSessionTransition,
    RewardNotVerified,
    UsageLimitExceeded,
)

_QUESTION_LIST = TypeAdapter(list[GeneratedQuestion])


def test_firestore_emulator_client_does_not_require_adc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    client = object()

    def make_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(state_module.firestore, "Client", make_client)
    monkeypatch.setattr(
        state_module.admin_firestore,
        "client",
        lambda: pytest.fail("Firebase Admin client must not be used for the emulator"),
    )

    store = FirestoreStateStore("dailyopic-test")

    assert store._client is client
    assert captured["project"] == "dailyopic-test"
    assert isinstance(captured["credentials"], state_module.AnonymousCredentials)


@pytest.mark.asyncio
async def test_firestore_contention_retry_starts_a_fresh_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            try:
                raise state_module.Aborted("Transaction lock timeout.")
            except state_module.Aborted as cause:
                raise ValueError("Failed to commit transaction") from cause
        return "reserved"

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(state_module.random, "uniform", lambda _start, _end: 0.0)
    monkeypatch.setattr(state_module.asyncio, "sleep", record_sleep)

    result = await state_module._run_with_firestore_contention_retry(operation)

    assert result == "reserved"
    assert calls == 3
    assert delays == pytest.approx([0.05, 0.1])


@pytest.mark.asyncio
async def test_firestore_contention_retry_does_not_retry_other_errors() -> None:
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("invalid payload")

    with pytest.raises(ValueError, match="invalid payload"):
        await state_module._run_with_firestore_contention_retry(operation)

    assert calls == 1


def test_firestore_emulator_client_does_not_require_adc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    client = object()

    def make_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(state_module.firestore, "Client", make_client)
    monkeypatch.setattr(
        state_module.admin_firestore,
        "client",
        lambda: pytest.fail("Firebase Admin client must not be used for the emulator"),
    )

    store = FirestoreStateStore("dailyopic-test")

    assert store._client is client
    assert captured["project"] == "dailyopic-test"
    assert isinstance(captured["credentials"], state_module.AnonymousCredentials)


@pytest.mark.asyncio
async def test_mock_session_is_unique_per_user_day_and_transitions_atomically() -> None:
    store = InMemoryStateStore()
    resets_at = datetime.now(UTC) + timedelta(hours=12)
    first = await store.create_or_get_mock_session(
        uid="u1",
        session_id="session-1",
        session_hash="hash-1",
        date_key="20260721",
        initial_level=4,
        background={},
        survey=None,
        resets_at=resets_at,
    )
    resumed = await store.create_or_get_mock_session(
        uid="u1",
        session_id="session-1",
        session_hash="hash-1",
        date_key="20260721",
        initial_level=6,
        background={"interests": ["changed"]},
        survey=None,
        resets_at=resets_at,
    )
    assert resumed == first
    generating = await store.transition_mock_session(
        uid="u1",
        session_id="session-1",
        expected_stages={"awaiting_start_ad"},
        stage="generating_front",
    )
    assert generating["stage"] == "generating_front"
    with pytest.raises(InvalidSessionTransition):
        await store.transition_mock_session(
            uid="u1",
            session_id="session-1",
            expected_stages={"awaiting_start_ad"},
            stage="generating_front",
        )


def _legacy_question_set(*, mode: str) -> dict:
    """배포 전 스키마의 questionSets 문서 (type/questionType/dateKey/제거된 필드 포함)."""
    return {
        "uid": "u1",
        "setId": "legacy-set",
        "mode": mode,
        "targetLevel": "IM1",
        "expectedTargetLevel": "IM1",
        "initialLevel": 3,
        "adjustment": None,
        "effectiveLevel": 3,
        "effectiveLevelCode": "3-3",
        "status": "complete",
        "frontQuestionCount": 0,
        "poolIndex": 0,
        "background": {},
        "survey": None,
        "questionHash": "legacy-hash",
        "questions": [
            {
                "number": 2,
                "type": "survey",
                "comboId": "daily-a",
                "topic": "movies",
                "prompt": "Describe the movies you usually enjoy.",
                "difficulty": "IM1",
                "rubricFocus": ["task fulfillment"],
                "questionType": "description",
                "followUpPrompt": None,
                "topicId": "movies",
                "category": "daily",
                "estimatedLevel": "IM1",
            }
        ],
        "source": "free",
        "dateKey": "20260101",
        "expiresAt": datetime.now(UTC) + timedelta(days=1),
        "createdAt": datetime.now(UTC),
        "updatedAt": datetime.now(UTC),
    }


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
        initial_level=5,
        adjustment=None,
        effective_level=5,
        status="awaiting_adjustment",
        background={},
        survey=None,
        question_hash="hash-1",
        questions=questions,
        expires_at=expires_at,
    )

    saved = await store.get_question_set(uid="u1", set_id="set-1", mode="practice")

    assert saved is not None
    assert saved["questionHash"] == "hash-1"
    assert (
        await store.get_question_set(uid="u2", set_id="set-1", mode="practice") is None
    )
    assert await store.get_question_set(uid="u1", set_id="set-1", mode="mock") is None


@pytest.mark.asyncio
async def test_question_history_records_recent_hashes_and_trims() -> None:
    store = InMemoryStateStore()

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
    assert len(history["promptHashes"]) == 80
    assert history["setHashes"][0] == "set-10"
    assert history["topicIds"][0] == "topic_10"
    assert history["setHashes"][-1] == "set-89"


@pytest.mark.asyncio
async def test_three_free_then_bonus_credits() -> None:
    store = InMemoryStateStore()
    for index in range(3):
        reservation = await store.reserve_practice(
            "u1", "20260622", f"request-{index}", 3
        )
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
        await store.reserve_mock(
            "u1", "mock-request-2", "mock-nonce-123456789", "hash-1"
        )


@pytest.mark.asyncio
async def test_target_level_change_requires_verified_reward_and_consumes_it() -> None:
    store = InMemoryStateStore()

    initial = await store.set_target_level(
        uid="u1", target_level="IH", reward_nonce=None
    )
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
async def test_target_level_is_derived_so_changed_tracks_level() -> None:
    # 리뷰 #5 확인: targetLevel은 독립 저장값이 아니라 beforeAdjust(레벨)에서 파생된다.
    # 따라서 같은 레벨로 매핑되는 하위 등급(NL/IL 모두 레벨 1)은 targetLevel이
    # 정규 등급(IL)으로 동일하게 나오고, changed 는 레벨 기준으로 일관되게 동작한다.
    store = InMemoryStateStore()

    first = await store.set_target_level(uid="u1", target_level="NL", reward_nonce=None)
    assert first["changed"] is True
    assert first["targetLevel"] == "IL"  # NL 요청이지만 레벨 1의 정규 등급으로 파생됨
    assert first["beforeAdjust"] == 1

    # 같은 레벨(1)로 매핑되는 다른 하위 등급 재요청 → 레벨 불변이라 changed=False
    same_level = await store.set_target_level(
        uid="u1", target_level="IL", reward_nonce=None
    )
    assert same_level["changed"] is False
    assert same_level["targetLevel"] == "IL"


@pytest.mark.asyncio
async def test_legacy_profile_document_preserves_chosen_level() -> None:
    store = InMemoryStateStore()
    # 구버전 문서: beforeAdjust/afterAdjust 이전 스키마 (initialLevel 사용).
    # targetLevel은 조정 후(harder) 레벨 기준으로 저장돼 있어 역산하면 6이 나오지만,
    # 사용자가 실제 고른 값은 initialLevel=5 이다.
    store._profiles["legacy-user"] = {
        "uid": "legacy-user",
        "initialLevel": 5,
        "latestAdjustment": "harder",
        "targetLevel": "AL",
        "effectiveLevel": 6,
        "effectiveLevelCode": "5-6",
    }

    profile = await store.get_learning_profile("legacy-user")

    assert profile is not None
    assert profile["beforeAdjust"] == 5
    assert profile["afterAdjust"] == 6
    assert profile["latestAdjustment"] == "harder"


@pytest.mark.asyncio
async def test_legacy_daily_question_set_is_normalized_on_read() -> None:
    store = InMemoryStateStore()
    # 배포 전 daily 세트: mode="practice", dateKey, 제거된 필드, 구 type/questionType
    store._question_sets["legacy-set"] = _legacy_question_set(mode="practice")

    record = await store.get_question_set(uid="u1", set_id="legacy-set", mode="daily")

    assert record is not None
    assert record["mode"] == "daily"
    assert record["date"] == "20260101"
    assert "dateKey" not in record
    for legacy in (
        "expectedTargetLevel",
        "effectiveLevelCode",
        "frontQuestionCount",
        "poolIndex",
    ):
        assert legacy not in record
    question = record["questions"][0]
    assert question["examSection"] == "survey"
    assert question["questionStyle"] == "description"
    assert "type" not in question
    assert "questionType" not in question
    # 정규화 후 현재 DTO 검증을 통과해야 한다
    _QUESTION_LIST.validate_python(record["questions"])


@pytest.mark.asyncio
async def test_legacy_mock_question_set_is_normalized_on_read() -> None:
    store = InMemoryStateStore()
    # 배포 전 mock 세트: mode="mock"(불변)이라 조회는 되고 구 필드로 검증이 깨지던 케이스
    store._question_sets["legacy-set"] = _legacy_question_set(mode="mock")

    record = await store.get_question_set(uid="u1", set_id="legacy-set", mode="mock")

    assert record is not None
    assert record["mode"] == "mock"
    question = record["questions"][0]
    assert question["examSection"] == "survey"
    assert question["questionStyle"] == "description"
    assert "type" not in question
    assert "questionType" not in question
    _QUESTION_LIST.validate_python(record["questions"])


@pytest.mark.asyncio
async def test_reward_intents_do_not_consume_verified_quota() -> None:
    store = InMemoryStateStore()
    for index in range(4):
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

    usage = await store.get_usage("u1", "20260622")
    assert usage["rewardCount"] == 0

    for index in range(3):
        await store.verify_reward(
            nonce=f"reward-nonce-{index}",
            transaction_id=f"tx-{index}",
            practice_credit_amount=1,
            max_daily_reward_count=3,
        )
    with pytest.raises(UsageLimitExceeded):
        await store.verify_reward(
            nonce="reward-nonce-3",
            transaction_id="tx-3",
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
            auto_verify=True,
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
    assert usage["bonusRemaining"] == 3


@pytest.mark.asyncio
async def test_reward_transaction_replay_is_idempotent() -> None:
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
        max_daily_reward_count=3,
    )
    replay = await store.verify_reward(
        nonce="reward-nonce-replay",
        transaction_id="tx-1",
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )
    assert replay["status"] == "verified"
    usage = await store.get_usage("u1", "20260622")
    assert usage["rewardCount"] == 1
    assert usage["bonusRemaining"] == 1
