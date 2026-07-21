import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.api import RewardPurpose
from app.services.state import (
    FirestoreStateStore,
    InvalidSessionTransition,
    RequestAlreadyProcessing,
    RewardNotVerified,
    UsageLimitExceeded,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="FIRESTORE_EMULATOR_HOST is required",
)


@pytest.mark.asyncio
async def test_firestore_question_set_is_bound_to_user_and_mode() -> None:
    uid = f"question-set-{uuid.uuid4()}"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))
    set_id = f"set-{uuid.uuid4()}"
    date_key = "20260622"

    await store.save_question_set(
        uid=uid,
        set_id=set_id,
        mode="daily",
        target_level="IH",
        initial_level=5,
        adjustment=None,
        effective_level=5,
        status="awaiting_adjustment",
        background={},
        survey=None,
        question_hash="hash-1",
        questions=[
            {
                "number": 2,
                "examSection": "survey",
                "comboId": "daily-a",
                "topic": "movies",
                "prompt": "Describe the movies you usually enjoy.",
                "difficulty": "IH",
                "rubricFocus": ["task fulfillment"],
                "questionStyle": "description",
                "followUpPrompt": None,
                "topicId": "movies",
                "category": "daily",
                "estimatedLevel": "IH",
            }
        ],
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        source="free",
        date_key=date_key,
    )

    saved = await store.get_question_set(uid=uid, set_id=set_id, mode="daily")

    assert saved is not None
    assert saved["questionHash"] == "hash-1"
    # 저장 스키마(변경 반영 후) 검증: 신 필드 존재, 구 필드 부재
    assert saved["mode"] == "daily"
    assert saved["date"] == date_key
    for legacy in ("expectedTargetLevel", "effectiveLevelCode", "frontQuestionCount", "poolIndex", "dateKey"):
        assert legacy not in saved
    question = saved["questions"][0]
    assert question["examSection"] == "survey"
    assert question["questionStyle"] == "description"
    assert "type" not in question
    assert "questionType" not in question
    assert await store.get_question_set(uid=f"{uid}-other", set_id=set_id, mode="daily") is None
    assert await store.get_question_set(uid=uid, set_id=set_id, mode="mock") is None


@pytest.mark.asyncio
async def test_firestore_legacy_question_set_is_normalized_on_read() -> None:
    uid = f"legacy-{uuid.uuid4()}"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))
    set_id = f"legacy-set-{uuid.uuid4()}"

    # 배포 전 스키마 문서를 client로 직접 저장 (mode=practice, type/questionType, dateKey)
    store._client.collection("questionSets").document(set_id).set(
        {
            "uid": uid,
            "setId": set_id,
            "mode": "practice",
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
    )

    # 구 mode=practice 문서를 mode=daily 요청으로 조회 (dual-read) + read-time 정규화
    record = await store.get_question_set(uid=uid, set_id=set_id, mode="daily")

    assert record is not None
    assert record["mode"] == "daily"
    assert record["date"] == "20260101"
    assert "dateKey" not in record
    question = record["questions"][0]
    assert question["examSection"] == "survey"
    assert question["questionStyle"] == "description"
    assert "type" not in question
    assert "questionType" not in question


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
        nonce = f"{uid}-reward-{index}"
        await store.create_reward_intent(
            nonce=nonce,
            uid=uid,
            purpose=RewardPurpose.PRACTICE_CREDITS,
            session_hash=None,
            date_key=date_key,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            auto_verify=False,
            practice_credit_amount=1,
            max_daily_reward_count=3,
        )
        await store.verify_reward(
            nonce=nonce,
            transaction_id=f"{uid}-transaction-{index}",
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
async def test_firestore_pending_reward_does_not_change_quota_and_ssv_is_idempotent() -> None:
    uid = f"verified-only-{uuid.uuid4()}"
    date_key = "20260622"
    nonce = f"{uid}-refresh"
    transaction_id = f"{uid}-transaction"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))

    await store.create_reward_intent(
        nonce=nonce,
        uid=uid,
        purpose=RewardPurpose.PRACTICE_REFRESH,
        session_hash=None,
        date_key=date_key,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        auto_verify=False,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )
    pending_usage = await store.get_usage(uid, date_key)
    assert pending_usage["practiceRefreshRewardCount"] == 0

    first = await store.verify_reward(
        nonce=nonce,
        transaction_id=transaction_id,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )
    duplicate = await store.verify_reward(
        nonce=nonce,
        transaction_id=transaction_id,
        practice_credit_amount=1,
        max_daily_reward_count=3,
    )

    assert first["status"] == "verified"
    assert duplicate["transactionId"] == transaction_id
    verified_usage = await store.get_usage(uid, date_key)
    assert verified_usage["practiceRefreshRewardCount"] == 1


@pytest.mark.asyncio
async def test_firestore_operation_lease_allows_only_one_parallel_owner() -> None:
    uid = f"operation-{uuid.uuid4()}"
    operation_id = str(uuid.uuid4())
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))

    async def reserve() -> str:
        try:
            result = await store.reserve_operation(
                uid=uid,
                operation="mock.adjustment",
                operation_id=operation_id,
                payload_hash="stable-payload",
            )
            return result.status
        except RequestAlreadyProcessing:
            return "processing"

    results = await asyncio.gather(*(reserve() for _ in range(20)))

    assert results.count("new") == 1
    assert results.count("processing") == 19
    operation = await store.get_operation(uid=uid, operation_id=operation_id)
    assert operation is not None
    assert operation["status"] == "processing"


@pytest.mark.asyncio
async def test_firestore_mock_session_stage_transition_is_atomic() -> None:
    uid = f"mock-session-{uuid.uuid4()}"
    session_id = f"session-{uuid.uuid4()}"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))

    created = await store.create_or_get_mock_session(
        uid=uid,
        session_id=session_id,
        session_hash="session-hash",
        date_key="20260622",
        initial_level=4,
        background={},
        survey=None,
        resets_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert created["stage"] == "awaiting_start_ad"

    transitioned = await store.transition_mock_session(
        uid=uid,
        session_id=session_id,
        expected_stages={"awaiting_start_ad"},
        stage="generating_front",
    )
    assert transitioned["stage"] == "generating_front"

    with pytest.raises(InvalidSessionTransition):
        await store.transition_mock_session(
            uid=uid,
            session_id=session_id,
            expected_stages={"awaiting_start_ad"},
            stage="generating_front",
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


@pytest.mark.asyncio
async def test_firestore_adjustment_removes_legacy_question_set_fields() -> None:
    uid = f"legacy-adj-{uuid.uuid4()}"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))
    set_id = f"legacy-adj-set-{uuid.uuid4()}"

    store._client.collection("questionSets").document(set_id).set(
        {
            "uid": uid,
            "setId": set_id,
            "mode": "mock",
            "targetLevel": "IM2",
            "expectedTargetLevel": "IM2",
            "initialLevel": 4,
            "adjustment": None,
            "effectiveLevel": 4,
            "effectiveLevelCode": "4-4",
            "status": "awaiting_adjustment",
            "frontQuestionCount": 7,
            "poolIndex": 0,
            "background": {},
            "survey": None,
            "questionHash": "legacy-hash",
            "questions": [],
            "source": "free",
            "dateKey": "20260101",
            "expiresAt": datetime.now(UTC) + timedelta(days=1),
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        }
    )

    await store.apply_question_set_adjustment(
        uid=uid,
        set_id=set_id,
        mode="mock",
        adjustment="same",
        effective_level=4,
        target_level="IM2",
        question_hash="new-hash",
        questions=[],
    )

    raw = store._client.collection("questionSets").document(set_id).get().to_dict()
    # 부분 업데이트 후에도 제거 대상 필드가 실제로 사라져야 한다
    for legacy in ("expectedTargetLevel", "effectiveLevelCode", "frontQuestionCount", "poolIndex", "dateKey"):
        assert legacy not in raw
    assert raw["questionHash"] == "new-hash"


@pytest.mark.asyncio
async def test_firestore_reserve_practice_removes_legacy_usage_date_key() -> None:
    uid = f"legacy-usage-{uuid.uuid4()}"
    date_key = "20260622"
    store = FirestoreStateStore(os.getenv("GCLOUD_PROJECT", "dailyopic-test"))
    usage_id = FirestoreStateStore._usage_id(uid, date_key)

    # 구 스키마 usage 문서(dateKey 보유)를 직접 저장
    store._client.collection("dailyUsage").document(usage_id).set(
        {"uid": uid, "dateKey": date_key, "freeUsed": 0, "bonusRemaining": 0, "rewardCount": 0}
    )

    await store.reserve_practice(uid, date_key, f"{uid}-req", 3)

    raw = store._client.collection("dailyUsage").document(usage_id).get().to_dict()
    assert raw["date"] == date_key
    assert "dateKey" not in raw
