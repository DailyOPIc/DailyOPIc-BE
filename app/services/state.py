from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import firebase_admin
from firebase_admin import firestore as admin_firestore
from google.cloud import firestore

from app.models.api import RewardPurpose


class UsageLimitExceeded(RuntimeError):
    pass


class RewardNotVerified(RuntimeError):
    pass


class RequestAlreadyProcessing(RuntimeError):
    pass


@dataclass(slots=True)
class Reservation:
    status: str
    source: str | None = None
    result: dict[str, Any] | None = None


class StateStore(ABC):
    @abstractmethod
    async def save_question_set(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        target_level: str,
        question_hash: str,
        questions: list[dict[str, Any]],
        expires_at: datetime,
    ) -> None: ...

    @abstractmethod
    async def get_question_set(
        self, *, uid: str, set_id: str, mode: str
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    async def get_usage(self, uid: str, date_key: str) -> dict[str, int]: ...

    @abstractmethod
    async def reserve_practice(
        self, uid: str, date_key: str, request_id: str, free_limit: int
    ) -> Reservation: ...

    @abstractmethod
    async def reserve_mock(
        self, uid: str, request_id: str, reward_nonce: str, session_hash: str
    ) -> Reservation: ...

    @abstractmethod
    async def finalize_request(
        self, request_id: str, result: dict[str, Any], ttl_hours: int
    ) -> None: ...

    @abstractmethod
    async def fail_request(self, request_id: str) -> None: ...

    @abstractmethod
    async def create_reward_intent(
        self,
        *,
        nonce: str,
        uid: str,
        purpose: RewardPurpose,
        session_hash: str | None,
        date_key: str,
        expires_at: datetime,
        auto_verify: bool,
        practice_credit_amount: int,
        max_daily_reward_count: int,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_reward_intent(self, nonce: str, uid: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def verify_reward(
        self,
        *,
        nonce: str,
        transaction_id: str,
        practice_credit_amount: int,
    ) -> dict[str, Any]: ...


def _usage_defaults() -> dict[str, int]:
    return {"freeUsed": 0, "bonusRemaining": 0, "rewardCount": 0}


class InMemoryStateStore(StateStore):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._usage: dict[str, dict[str, int]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._rewards: dict[str, dict[str, Any]] = {}
        self._transactions: set[str] = set()
        self._question_sets: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _usage_id(uid: str, date_key: str) -> str:
        return f"{uid}:{date_key}"

    async def save_question_set(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        target_level: str,
        question_hash: str,
        questions: list[dict[str, Any]],
        expires_at: datetime,
    ) -> None:
        async with self._lock:
            self._question_sets[set_id] = {
                "uid": uid,
                "setId": set_id,
                "mode": mode,
                "targetLevel": target_level,
                "questionHash": question_hash,
                "questions": deepcopy(questions),
                "expiresAt": expires_at,
                "createdAt": datetime.now(UTC),
            }

    async def get_question_set(
        self, *, uid: str, set_id: str, mode: str
    ) -> dict[str, Any] | None:
        async with self._lock:
            question_set = self._question_sets.get(set_id)
            if (
                not question_set
                or question_set["uid"] != uid
                or question_set["mode"] != mode
                or question_set["expiresAt"] < datetime.now(UTC)
            ):
                return None
            return deepcopy(question_set)

    async def get_usage(self, uid: str, date_key: str) -> dict[str, int]:
        async with self._lock:
            return deepcopy(self._usage.get(self._usage_id(uid, date_key), _usage_defaults()))

    async def reserve_practice(
        self, uid: str, date_key: str, request_id: str, free_limit: int
    ) -> Reservation:
        async with self._lock:
            existing = self._requests.get(request_id)
            if existing:
                if existing["uid"] != uid:
                    raise UsageLimitExceeded("idempotency key belongs to another user")
                if existing["status"] == "completed":
                    return Reservation("cached", result=deepcopy(existing["result"]))
                if existing["status"] == "processing":
                    raise RequestAlreadyProcessing("request is already processing")

            usage_id = self._usage_id(uid, date_key)
            usage = self._usage.setdefault(usage_id, _usage_defaults())
            if usage["freeUsed"] < free_limit:
                usage["freeUsed"] += 1
                source = "free"
            elif usage["bonusRemaining"] > 0:
                usage["bonusRemaining"] -= 1
                source = "bonus"
            else:
                raise UsageLimitExceeded("daily practice quota exhausted")

            self._requests[request_id] = {
                "uid": uid,
                "status": "processing",
                "source": source,
                "usageId": usage_id,
                "createdAt": datetime.now(UTC),
            }
            return Reservation("new", source=source)

    async def reserve_mock(
        self, uid: str, request_id: str, reward_nonce: str, session_hash: str
    ) -> Reservation:
        async with self._lock:
            existing = self._requests.get(request_id)
            if existing:
                if existing["uid"] != uid:
                    raise RewardNotVerified("idempotency key belongs to another user")
                if existing["status"] == "completed":
                    return Reservation("cached", result=deepcopy(existing["result"]))
                if existing["status"] == "processing":
                    raise RequestAlreadyProcessing("request is already processing")

            reward = self._rewards.get(reward_nonce)
            if (
                not reward
                or reward["uid"] != uid
                or reward["purpose"] != RewardPurpose.MOCK_RESULT
                or reward["sessionHash"] != session_hash
                or reward["status"] != "verified"
                or reward.get("consumed", False)
                or reward["expiresAt"] < datetime.now(UTC)
            ):
                raise RewardNotVerified("verified mock reward is required")
            reward["consumed"] = True
            self._requests[request_id] = {
                "uid": uid,
                "status": "processing",
                "source": f"mock:{reward_nonce}",
                "createdAt": datetime.now(UTC),
            }
            return Reservation("new", source=f"mock:{reward_nonce}")

    async def finalize_request(
        self, request_id: str, result: dict[str, Any], ttl_hours: int
    ) -> None:
        async with self._lock:
            request = self._requests[request_id]
            request.update(
                {
                    "status": "completed",
                    "result": deepcopy(result),
                    "expiresAt": datetime.now(UTC) + timedelta(hours=ttl_hours),
                }
            )

    async def fail_request(self, request_id: str) -> None:
        async with self._lock:
            request = self._requests.get(request_id)
            if not request or request["status"] != "processing":
                return
            source = request.get("source")
            if source == "free":
                self._usage[request["usageId"]]["freeUsed"] = max(
                    0, self._usage[request["usageId"]]["freeUsed"] - 1
                )
            elif source == "bonus":
                self._usage[request["usageId"]]["bonusRemaining"] += 1
            elif isinstance(source, str) and source.startswith("mock:"):
                nonce = source.split(":", 1)[1]
                if nonce in self._rewards:
                    self._rewards[nonce]["consumed"] = False
            request["status"] = "failed"

    async def create_reward_intent(
        self,
        *,
        nonce: str,
        uid: str,
        purpose: RewardPurpose,
        session_hash: str | None,
        date_key: str,
        expires_at: datetime,
        auto_verify: bool,
        practice_credit_amount: int,
        max_daily_reward_count: int,
    ) -> dict[str, Any]:
        async with self._lock:
            usage = self._usage.setdefault(self._usage_id(uid, date_key), _usage_defaults())
            if usage["rewardCount"] >= max_daily_reward_count:
                raise UsageLimitExceeded("daily reward quota exhausted")
            usage["rewardCount"] += 1
            reward = {
                "nonce": nonce,
                "uid": uid,
                "purpose": purpose,
                "sessionHash": session_hash,
                "dateKey": date_key,
                "status": "verified" if auto_verify else "pending",
                "consumed": False,
                "expiresAt": expires_at,
                "createdAt": datetime.now(UTC),
            }
            self._rewards[nonce] = reward
            if auto_verify and purpose is RewardPurpose.PRACTICE_CREDITS:
                usage["bonusRemaining"] += practice_credit_amount
                reward["credited"] = True
            return deepcopy(reward)

    async def get_reward_intent(self, nonce: str, uid: str) -> dict[str, Any] | None:
        async with self._lock:
            reward = self._rewards.get(nonce)
            return deepcopy(reward) if reward and reward["uid"] == uid else None

    async def verify_reward(
        self,
        *,
        nonce: str,
        transaction_id: str,
        practice_credit_amount: int,
    ) -> dict[str, Any]:
        async with self._lock:
            if transaction_id in self._transactions:
                raise RewardNotVerified("reward transaction already processed")
            reward = self._rewards.get(nonce)
            if not reward or reward["expiresAt"] < datetime.now(UTC):
                raise RewardNotVerified("reward intent missing or expired")
            self._transactions.add(transaction_id)
            reward["status"] = "verified"
            reward["transactionId"] = transaction_id
            if reward["purpose"] is RewardPurpose.PRACTICE_CREDITS and not reward.get(
                "credited", False
            ):
                usage = self._usage.setdefault(
                    self._usage_id(reward["uid"], reward["dateKey"]), _usage_defaults()
                )
                usage["bonusRemaining"] += practice_credit_amount
                reward["credited"] = True
            return deepcopy(reward)


class FirestoreStateStore(StateStore):
    def __init__(self, project_id: str | None = None) -> None:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={"projectId": project_id} if project_id else None)
        self._client = admin_firestore.client()

    @staticmethod
    def _usage_id(uid: str, date_key: str) -> str:
        return hashlib.sha256(f"{uid}:{date_key}".encode()).hexdigest()

    async def save_question_set(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        target_level: str,
        question_hash: str,
        questions: list[dict[str, Any]],
        expires_at: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._client.collection("questionSets").document(set_id).set,
            {
                "uid": uid,
                "setId": set_id,
                "mode": mode,
                "targetLevel": target_level,
                "questionHash": question_hash,
                "questions": questions,
                "expiresAt": expires_at,
                "createdAt": datetime.now(UTC),
            },
        )

    async def get_question_set(
        self, *, uid: str, set_id: str, mode: str
    ) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            snapshot = self._client.collection("questionSets").document(set_id).get()
            value = snapshot.to_dict() if snapshot.exists else None
            if (
                not value
                or value.get("uid") != uid
                or value.get("mode") != mode
                or value.get("expiresAt") < datetime.now(UTC)
            ):
                return None
            return value

        return await asyncio.to_thread(read)

    async def get_usage(self, uid: str, date_key: str) -> dict[str, int]:
        def read() -> dict[str, int]:
            snapshot = self._client.collection("dailyUsage").document(
                self._usage_id(uid, date_key)
            ).get()
            return {**_usage_defaults(), **(snapshot.to_dict() or {})}

        return await asyncio.to_thread(read)

    async def reserve_practice(
        self, uid: str, date_key: str, request_id: str, free_limit: int
    ) -> Reservation:
        def run() -> Reservation:
            transaction = self._client.transaction()
            usage_ref = self._client.collection("dailyUsage").document(
                self._usage_id(uid, date_key)
            )
            request_ref = self._client.collection("aiRequests").document(request_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> Reservation:
                existing = request_ref.get(transaction=transaction)
                if existing.exists:
                    data = existing.to_dict() or {}
                    if data.get("uid") != uid:
                        raise UsageLimitExceeded("idempotency key belongs to another user")
                    if data.get("status") == "completed":
                        return Reservation("cached", result=data.get("result"))
                    if data.get("status") == "processing":
                        raise RequestAlreadyProcessing("request is already processing")

                snapshot = usage_ref.get(transaction=transaction)
                usage = {**_usage_defaults(), **(snapshot.to_dict() or {})}
                if usage["freeUsed"] < free_limit:
                    usage["freeUsed"] += 1
                    source = "free"
                elif usage["bonusRemaining"] > 0:
                    usage["bonusRemaining"] -= 1
                    source = "bonus"
                else:
                    raise UsageLimitExceeded("daily practice quota exhausted")
                transaction.set(
                    usage_ref,
                    {**usage, "uid": uid, "dateKey": date_key, "updatedAt": datetime.now(UTC)},
                    merge=True,
                )
                transaction.set(
                    request_ref,
                    {
                        "uid": uid,
                        "status": "processing",
                        "source": source,
                        "usageId": usage_ref.id,
                        "createdAt": datetime.now(UTC),
                    },
                )
                return Reservation("new", source=source)

            return apply(transaction)

        return await asyncio.to_thread(run)

    async def reserve_mock(
        self, uid: str, request_id: str, reward_nonce: str, session_hash: str
    ) -> Reservation:
        def run() -> Reservation:
            transaction = self._client.transaction()
            reward_ref = self._client.collection("adRewardIntents").document(reward_nonce)
            request_ref = self._client.collection("aiRequests").document(request_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> Reservation:
                existing = request_ref.get(transaction=transaction)
                if existing.exists:
                    data = existing.to_dict() or {}
                    if data.get("uid") != uid:
                        raise RewardNotVerified("idempotency key belongs to another user")
                    if data.get("status") == "completed":
                        return Reservation("cached", result=data.get("result"))
                    if data.get("status") == "processing":
                        raise RequestAlreadyProcessing("request is already processing")

                snapshot = reward_ref.get(transaction=transaction)
                reward = snapshot.to_dict() or {}
                if (
                    not snapshot.exists
                    or reward.get("uid") != uid
                    or reward.get("purpose") != RewardPurpose.MOCK_RESULT.value
                    or reward.get("sessionHash") != session_hash
                    or reward.get("status") != "verified"
                    or reward.get("consumed", False)
                    or reward.get("expiresAt") < datetime.now(UTC)
                ):
                    raise RewardNotVerified("verified mock reward is required")
                transaction.update(reward_ref, {"consumed": True})
                transaction.set(
                    request_ref,
                    {
                        "uid": uid,
                        "status": "processing",
                        "source": f"mock:{reward_nonce}",
                        "createdAt": datetime.now(UTC),
                    },
                )
                return Reservation("new", source=f"mock:{reward_nonce}")

            return apply(transaction)

        return await asyncio.to_thread(run)

    async def finalize_request(
        self, request_id: str, result: dict[str, Any], ttl_hours: int
    ) -> None:
        await asyncio.to_thread(
            self._client.collection("aiRequests").document(request_id).update,
            {
                "status": "completed",
                "result": result,
                "expiresAt": datetime.now(UTC) + timedelta(hours=ttl_hours),
            },
        )

    async def fail_request(self, request_id: str) -> None:
        def run() -> None:
            transaction = self._client.transaction()
            request_ref = self._client.collection("aiRequests").document(request_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> None:
                snapshot = request_ref.get(transaction=transaction)
                data = snapshot.to_dict() or {}
                if not snapshot.exists or data.get("status") != "processing":
                    return
                source = data.get("source")
                if source in {"free", "bonus"}:
                    usage_ref = self._client.collection("dailyUsage").document(data["usageId"])
                    usage_snapshot = usage_ref.get(transaction=transaction)
                    usage = {**_usage_defaults(), **(usage_snapshot.to_dict() or {})}
                    if source == "free":
                        usage["freeUsed"] = max(0, usage["freeUsed"] - 1)
                    else:
                        usage["bonusRemaining"] += 1
                    transaction.set(usage_ref, usage, merge=True)
                elif isinstance(source, str) and source.startswith("mock:"):
                    reward_ref = self._client.collection("adRewardIntents").document(
                        source.split(":", 1)[1]
                    )
                    transaction.update(reward_ref, {"consumed": False})
                transaction.update(request_ref, {"status": "failed"})

            apply(transaction)

        await asyncio.to_thread(run)

    async def create_reward_intent(
        self,
        *,
        nonce: str,
        uid: str,
        purpose: RewardPurpose,
        session_hash: str | None,
        date_key: str,
        expires_at: datetime,
        auto_verify: bool,
        practice_credit_amount: int,
        max_daily_reward_count: int,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            transaction = self._client.transaction()
            reward_ref = self._client.collection("adRewardIntents").document(nonce)
            usage_ref = self._client.collection("dailyUsage").document(
                self._usage_id(uid, date_key)
            )

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> dict[str, Any]:
                usage_snapshot = usage_ref.get(transaction=transaction)
                usage = {**_usage_defaults(), **(usage_snapshot.to_dict() or {})}
                if usage["rewardCount"] >= max_daily_reward_count:
                    raise UsageLimitExceeded("daily reward quota exhausted")
                usage["rewardCount"] += 1
                reward = {
                    "nonce": nonce,
                    "uid": uid,
                    "purpose": purpose.value,
                    "sessionHash": session_hash,
                    "dateKey": date_key,
                    "status": "verified" if auto_verify else "pending",
                    "consumed": False,
                    "expiresAt": expires_at,
                    "createdAt": datetime.now(UTC),
                }
                if auto_verify and purpose is RewardPurpose.PRACTICE_CREDITS:
                    usage["bonusRemaining"] += practice_credit_amount
                    reward["credited"] = True
                transaction.set(
                    usage_ref,
                    {**usage, "uid": uid, "dateKey": date_key, "updatedAt": datetime.now(UTC)},
                    merge=True,
                )
                transaction.set(reward_ref, reward)
                return reward

            return apply(transaction)

        return await asyncio.to_thread(run)

    async def get_reward_intent(self, nonce: str, uid: str) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            snapshot = self._client.collection("adRewardIntents").document(nonce).get()
            value = snapshot.to_dict() if snapshot.exists else None
            return value if value and value.get("uid") == uid else None

        return await asyncio.to_thread(read)

    async def verify_reward(
        self,
        *,
        nonce: str,
        transaction_id: str,
        practice_credit_amount: int,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            transaction = self._client.transaction()
            reward_ref = self._client.collection("adRewardIntents").document(nonce)
            tx_ref = self._client.collection("adRewardIntents").document(
                f"_tx_{hashlib.sha256(transaction_id.encode()).hexdigest()}"
            )

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> dict[str, Any]:
                if tx_ref.get(transaction=transaction).exists:
                    raise RewardNotVerified("reward transaction already processed")
                snapshot = reward_ref.get(transaction=transaction)
                reward = snapshot.to_dict() or {}
                if not snapshot.exists or reward.get("expiresAt") < datetime.now(UTC):
                    raise RewardNotVerified("reward intent missing or expired")
                updates: dict[str, Any] = {
                    "status": "verified",
                    "transactionId": transaction_id,
                }
                usage_ref = None
                usage = None
                if (
                    reward.get("purpose") == RewardPurpose.PRACTICE_CREDITS.value
                    and not reward.get("credited", False)
                ):
                    usage_ref = self._client.collection("dailyUsage").document(
                        self._usage_id(reward["uid"], reward["dateKey"])
                    )
                    usage_snapshot = usage_ref.get(transaction=transaction)
                    usage = {**_usage_defaults(), **(usage_snapshot.to_dict() or {})}
                    usage["bonusRemaining"] += practice_credit_amount

                # Firestore transactions require every read to occur before the first write.
                transaction.set(
                    tx_ref,
                    {
                        "kind": "transaction",
                        "transactionId": transaction_id,
                        "expiresAt": datetime.now(UTC) + timedelta(days=30),
                    },
                )
                if usage_ref is not None and usage is not None:
                    transaction.set(usage_ref, usage, merge=True)
                    updates["credited"] = True
                transaction.update(reward_ref, updates)
                return {**reward, **updates}

            return apply(transaction)

        return await asyncio.to_thread(run)
