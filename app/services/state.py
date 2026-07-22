from __future__ import annotations

import asyncio
import hashlib
import os
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import firebase_admin
from firebase_admin import firestore as admin_firestore
from google.api_core.exceptions import Aborted, AlreadyExists
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore

from app.models.api import DifficultyAdjustment, RewardPurpose
from app.services.difficulty import (
    adjusted_level,
    expected_target_level,
    initial_level_from_target,
)
from app.services.questions import prompt_hash

_T = TypeVar("_T")
_FIRESTORE_CONTENTION_ATTEMPTS = 5
_FIRESTORE_CONTENTION_BASE_DELAY_SECONDS = 0.05


def _is_firestore_contention(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, Aborted):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False


async def _run_with_firestore_contention_retry(
    operation: Callable[[], _T],
    *,
    attempts: int = _FIRESTORE_CONTENTION_ATTEMPTS,
) -> _T:
    for attempt in range(attempts):
        try:
            return await asyncio.to_thread(operation)
        except (Aborted, ValueError) as error:
            if not _is_firestore_contention(error) or attempt == attempts - 1:
                raise
            base_delay = _FIRESTORE_CONTENTION_BASE_DELAY_SECONDS * (2**attempt)
            await asyncio.sleep(base_delay + random.uniform(0, base_delay))
    raise RuntimeError("unreachable Firestore retry state")


@dataclass(slots=True)
class _KeyedLockEntry:
    lock: asyncio.Lock
    users: int = 0


class _KeyedLockPool:
    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._entries: dict[str, _KeyedLockEntry] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = _KeyedLockEntry(lock=asyncio.Lock())
                self._entries[key] = entry
            entry.users += 1

        try:
            async with entry.lock:
                yield
        finally:
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(key) is entry:
                    self._entries.pop(key, None)


class UsageLimitExceeded(RuntimeError):
    pass


class RewardNotVerified(RuntimeError):
    pass


class RequestAlreadyProcessing(RuntimeError):
    pass


class AdjustmentAlreadyApplied(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass


class InvalidSessionTransition(RuntimeError):
    pass


@dataclass(slots=True)
class Reservation:
    status: str
    source: str | None = None
    result: dict[str, Any] | None = None


class StateStore(ABC):
    @abstractmethod
    async def create_or_get_mock_session(
        self,
        *,
        uid: str,
        session_id: str,
        session_hash: str,
        date_key: str,
        initial_level: int,
        background: dict[str, Any],
        survey: dict[str, Any] | None,
        resets_at: datetime,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_mock_session(
        self,
        *,
        uid: str,
        session_id: str | None = None,
        date_key: str | None = None,
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    async def transition_mock_session(
        self,
        *,
        uid: str,
        session_id: str,
        expected_stages: set[str],
        stage: str,
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def reserve_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        payload_hash: str,
    ) -> Reservation: ...

    @abstractmethod
    async def complete_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        result: dict[str, Any],
        ttl_hours: int,
    ) -> None: ...

    @abstractmethod
    async def fail_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        retryable: bool,
    ) -> None: ...

    @abstractmethod
    async def get_operation(
        self,
        *,
        uid: str,
        operation_id: str,
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    async def save_question_set(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        target_level: str,
        initial_level: int,
        adjustment: str | None,
        effective_level: int,
        status: str,
        background: dict[str, Any],
        survey: dict[str, Any] | None,
        question_hash: str,
        questions: list[dict[str, Any]],
        expires_at: datetime,
        source: str | None = None,
        date_key: str | None = None,
        generation_metadata: dict[str, Any] | None = None,
    ) -> None: ...

    @abstractmethod
    async def get_question_set(
        self, *, uid: str, set_id: str, mode: str
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    async def get_question_history(
        self, *, uid: str, mode: str
    ) -> dict[str, list[str]]: ...

    @abstractmethod
    async def record_question_history(
        self,
        *,
        uid: str,
        mode: str,
        set_hash: str,
        questions: list[dict[str, Any]],
    ) -> None: ...

    @abstractmethod
    async def get_usage(self, uid: str, date_key: str) -> dict[str, int]: ...

    @abstractmethod
    async def get_target_level(self, uid: str) -> str | None: ...

    @abstractmethod
    async def get_learning_profile(self, uid: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def set_initial_level(
        self, *, uid: str, initial_level: int, reward_nonce: str | None
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def apply_question_set_adjustment(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        adjustment: str,
        effective_level: int,
        target_level: str,
        question_hash: str,
        questions: list[dict[str, Any]],
        generation_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def reserve_practice(
        self, uid: str, date_key: str, request_id: str, free_limit: int
    ) -> Reservation: ...

    @abstractmethod
    async def reserve_mock(
        self,
        uid: str,
        request_id: str,
        reward_nonce: str,
        session_hash: str | None,
        purpose: RewardPurpose = RewardPurpose.MOCK_RESULT,
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
    async def get_reward_intent(
        self, nonce: str, uid: str
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    async def verify_reward(
        self,
        *,
        nonce: str,
        transaction_id: str,
        practice_credit_amount: int,
        max_daily_reward_count: int,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_entitlement(self, uid: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def set_entitlement(
        self, uid: str, *, entitlement: dict[str, Any]
    ) -> None: ...

    @abstractmethod
    async def record_iap_event(self, event_id: str, uid: str) -> bool:
        """웹훅 이벤트 멱등 기록. 신규면 True, 이미 처리된 이벤트면 False."""
        ...


_DAILY_MODE_ALIASES = frozenset({"daily", "practice"})
_LEGACY_QUESTION_SET_FIELDS = (
    "expectedTargetLevel",
    "effectiveLevelCode",
    "frontQuestionCount",
    "poolIndex",
)


def _mode_matches(stored: object, requested: str) -> bool:
    """배포 전 저장된 문서 호환: daily 요청은 구 practice 값도 매칭."""
    if stored == requested:
        return True
    return requested == "daily" and stored in _DAILY_MODE_ALIASES


def _normalize_legacy_question(question: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(question)
    if "examSection" not in normalized and "type" in normalized:
        normalized["examSection"] = normalized["type"]
    normalized.pop("type", None)
    if "questionStyle" not in normalized and "questionType" in normalized:
        normalized["questionStyle"] = normalized["questionType"]
    normalized.pop("questionType", None)
    return normalized


def _normalize_legacy_question_set(record: dict[str, Any]) -> dict[str, Any]:
    """구 스키마 questionSets 문서를 현재 필드로 정규화 (read-time, 멱등)."""
    normalized = dict(record)
    if normalized.get("mode") == "practice":
        normalized["mode"] = "daily"
    if "date" not in normalized and "dateKey" in normalized:
        normalized["date"] = normalized["dateKey"]
    normalized.pop("dateKey", None)
    for legacy in _LEGACY_QUESTION_SET_FIELDS:
        normalized.pop(legacy, None)
    questions = normalized.get("questions")
    if isinstance(questions, list):
        normalized["questions"] = [
            _normalize_legacy_question(item) if isinstance(item, dict) else item
            for item in questions
        ]
    return normalized


def _usage_defaults() -> dict[str, int]:
    return {
        "freeUsed": 0,
        "bonusRemaining": 0,
        "rewardCount": 0,
        "practiceCreditRewardCount": 0,
        "practiceRefreshRewardCount": 0,
        "mockRewardCount": 0,
    }


def _question_history_defaults() -> dict[str, list[str]]:
    return {"setHashes": [], "topicIds": [], "promptHashes": [], "promptTexts": []}


def _trim_recent(values: list[str], limit: int = 80) -> list[str]:
    result: list[str] = []
    for value in values:
        if not value:
            continue
        if value in result:
            result.remove(value)
        result.append(value)
    return result[-limit:]


def _merge_question_history(
    existing: dict[str, Any] | None,
    *,
    set_hash: str,
    questions: list[dict[str, Any]],
) -> dict[str, list[str]]:
    history = {**_question_history_defaults(), **(existing or {})}
    topic_ids = [
        str(question.get("topicId") or "").strip()
        for question in questions
        if str(question.get("topicId") or "").strip()
    ]
    prompt_hashes = [
        prompt_hash(str(question.get("prompt") or ""))
        for question in questions
        if str(question.get("prompt") or "").strip()
    ]
    prompt_texts = [
        str(question.get("prompt") or "").strip()
        for question in questions
        if str(question.get("prompt") or "").strip()
    ]
    return {
        "setHashes": _trim_recent([*history["setHashes"], set_hash]),
        "topicIds": _trim_recent([*history["topicIds"], *topic_ids]),
        "promptHashes": _trim_recent([*history["promptHashes"], *prompt_hashes]),
        "promptTexts": _trim_recent(
            [*history.get("promptTexts", []), *prompt_texts], 40
        ),
    }


def _reward_purpose_matches(value: object, purpose: RewardPurpose) -> bool:
    return value == purpose or value == purpose.value


def _counts_toward_daily_reward_quota(purpose: RewardPurpose) -> bool:
    return purpose is not RewardPurpose.TARGET_LEVEL_CHANGE


def _reward_count_key(purpose: RewardPurpose) -> str | None:
    if purpose is RewardPurpose.PRACTICE_CREDITS:
        return "practiceCreditRewardCount"
    if purpose is RewardPurpose.PRACTICE_REFRESH:
        return "practiceRefreshRewardCount"
    if purpose in {
        RewardPurpose.MOCK_START,
        RewardPurpose.MOCK_ADJUSTMENT,
        RewardPurpose.MOCK_RESULT,
    }:
        return "mockRewardCount"
    return None


def _profile_from_value(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not profile:
        return None
    before_adjust = profile.get("beforeAdjust")
    if before_adjust is None:
        before_adjust = profile.get(
            "initialLevel"
        )  # 레거시 문서: 사용자가 고른 원본 값
    if before_adjust is None:
        before_adjust = initial_level_from_target(profile.get("targetLevel"))
    if before_adjust is None:
        return None
    before_adjust = int(before_adjust)
    latest_adjustment = str(
        profile.get("latestAdjustment") or DifficultyAdjustment.SAME.value
    )
    after_adjust = adjusted_level(before_adjust, latest_adjustment)
    target_level = str(
        profile.get("targetLevel") or expected_target_level(after_adjust).value
    )
    return {
        **profile,
        "beforeAdjust": before_adjust,
        "latestAdjustment": latest_adjustment,
        "afterAdjust": after_adjust,
        "targetLevel": target_level,
    }


def _coerce_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        # RevenueCat는 밀리초 epoch. 초 단위도 방어적으로 지원.
        seconds = value / 1000 if value > 1_000_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def resolve_plan(entitlement: dict[str, Any] | None) -> str:
    """저장된 엔타이틀먼트에서 현재 유효 플랜 문자열을 계산.

    비활성/만료/미존재는 모두 'free'로 강등한다. 서버 권위의 최종 판단 지점.
    """
    if not entitlement or not entitlement.get("isActive", False):
        return "free"
    expires_at = _coerce_datetime(entitlement.get("expiresAt"))
    if expires_at is not None and expires_at < datetime.now(UTC):
        return "free"
    return str(entitlement.get("plan") or "free")


def _target_change_response(
    *,
    profile: dict[str, Any],
    previous: dict[str, Any] | None,
    reward_consumed: bool,
) -> dict[str, Any]:
    return {
        "targetLevel": profile["targetLevel"],
        "previousTargetLevel": previous["targetLevel"] if previous else None,
        "beforeAdjust": profile["beforeAdjust"],
        "previousBeforeAdjust": previous["beforeAdjust"] if previous else None,
        "latestAdjustment": profile["latestAdjustment"],
        "afterAdjust": profile["afterAdjust"],
        "changed": previous is None
        or previous["beforeAdjust"] != profile["beforeAdjust"],
        "rewardConsumed": reward_consumed,
    }


class InMemoryStateStore(StateStore):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._usage: dict[str, dict[str, int]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._rewards: dict[str, dict[str, Any]] = {}
        self._transactions: dict[str, str] = {}
        self._question_sets: dict[str, dict[str, Any]] = {}
        self._question_histories: dict[str, dict[str, list[str]]] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._operations: dict[str, dict[str, Any]] = {}
        self._mock_sessions: dict[str, dict[str, Any]] = {}
        self._entitlements: dict[str, dict[str, Any]] = {}
        self._iap_events: dict[str, str] = {}

    async def get_entitlement(self, uid: str) -> dict[str, Any] | None:
        async with self._lock:
            entitlement = self._entitlements.get(uid)
            return deepcopy(entitlement) if entitlement else None

    async def set_entitlement(self, uid: str, *, entitlement: dict[str, Any]) -> None:
        async with self._lock:
            self._entitlements[uid] = {**deepcopy(entitlement), "uid": uid}

    async def record_iap_event(self, event_id: str, uid: str) -> bool:
        async with self._lock:
            if event_id in self._iap_events:
                return False
            self._iap_events[event_id] = uid
            return True

    async def create_or_get_mock_session(
        self,
        *,
        uid: str,
        session_id: str,
        session_hash: str,
        date_key: str,
        initial_level: int,
        background: dict[str, Any],
        survey: dict[str, Any] | None,
        resets_at: datetime,
    ) -> dict[str, Any]:
        async with self._lock:
            existing = self._mock_sessions.get(session_id)
            if existing:
                if existing["uid"] != uid:
                    raise KeyError("mock session not found")
                return deepcopy(existing)
            now = datetime.now(UTC)
            value = {
                "uid": uid,
                "sessionId": session_id,
                "sessionHash": session_hash,
                "date": date_key,
                "stage": "awaiting_start_ad",
                "initialLevel": initial_level,
                "background": deepcopy(background),
                "survey": deepcopy(survey),
                "resetsAt": resets_at,
                "createdAt": now,
                "updatedAt": now,
            }
            self._mock_sessions[session_id] = value
            return deepcopy(value)

    async def get_mock_session(
        self,
        *,
        uid: str,
        session_id: str | None = None,
        date_key: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._lock:
            matches = [
                value
                for value in self._mock_sessions.values()
                if value.get("uid") == uid
                and (session_id is None or value.get("sessionId") == session_id)
                and (date_key is None or value.get("date") == date_key)
            ]
            return deepcopy(matches[0]) if matches else None

    async def transition_mock_session(
        self,
        *,
        uid: str,
        session_id: str,
        expected_stages: set[str],
        stage: str,
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            value = self._mock_sessions.get(session_id)
            if not value or value.get("uid") != uid:
                raise KeyError("mock session not found")
            if value.get("stage") not in expected_stages:
                raise InvalidSessionTransition(
                    f"expected {sorted(expected_stages)}, got {value.get('stage')}"
                )
            value.update(deepcopy(updates or {}))
            value["stage"] = stage
            value["updatedAt"] = datetime.now(UTC)
            return deepcopy(value)

    @staticmethod
    def _usage_id(uid: str, date_key: str) -> str:
        return f"{uid}:{date_key}"

    @staticmethod
    def _question_history_id(uid: str, mode: str) -> str:
        return f"{uid}:{mode}"

    @staticmethod
    def _operation_id(uid: str, operation: str, operation_id: str) -> str:
        return hashlib.sha256(f"{uid}:{operation}:{operation_id}".encode()).hexdigest()

    async def reserve_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        payload_hash: str,
    ) -> Reservation:
        async with self._lock:
            key = self._operation_id(uid, operation, operation_id)
            existing = self._operations.get(key)
            if existing:
                if existing.get("payloadHash") != payload_hash:
                    raise IdempotencyConflict("idempotency key payload does not match")
                if existing.get("status") == "completed":
                    return Reservation(
                        "cached", result=deepcopy(existing.get("result"))
                    )
                if existing.get("status") == "processing":
                    raise RequestAlreadyProcessing("operation is already processing")
            self._operations[key] = {
                "uid": uid,
                "operation": operation,
                "operationId": operation_id,
                "payloadHash": payload_hash,
                "status": "processing",
                "createdAt": datetime.now(UTC),
                "updatedAt": datetime.now(UTC),
            }
            return Reservation("new")

    async def complete_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        result: dict[str, Any],
        ttl_hours: int,
    ) -> None:
        async with self._lock:
            key = self._operation_id(uid, operation, operation_id)
            record = self._operations[key]
            record.update(
                {
                    "status": "completed",
                    "result": deepcopy(result),
                    "updatedAt": datetime.now(UTC),
                    "expiresAt": datetime.now(UTC) + timedelta(hours=ttl_hours),
                }
            )

    async def fail_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        retryable: bool,
    ) -> None:
        async with self._lock:
            key = self._operation_id(uid, operation, operation_id)
            record = self._operations.get(key)
            if record and record.get("status") == "processing":
                record.update(
                    {
                        "status": (
                            "recoverable_failed" if retryable else "terminal_failed"
                        ),
                        "updatedAt": datetime.now(UTC),
                    }
                )

    async def get_operation(
        self,
        *,
        uid: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        async with self._lock:
            matches = [
                record
                for record in self._operations.values()
                if record.get("uid") == uid
                and record.get("operationId") == operation_id
            ]
            return deepcopy(matches[0]) if matches else None

    async def save_question_set(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        target_level: str,
        initial_level: int,
        adjustment: str | None,
        effective_level: int,
        status: str,
        background: dict[str, Any],
        survey: dict[str, Any] | None,
        question_hash: str,
        questions: list[dict[str, Any]],
        expires_at: datetime,
        source: str | None = None,
        date_key: str | None = None,
        generation_metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            self._question_sets[set_id] = {
                "uid": uid,
                "setId": set_id,
                "mode": mode,
                "targetLevel": target_level,
                "initialLevel": initial_level,
                "adjustment": adjustment,
                "effectiveLevel": effective_level,
                "status": status,
                "background": deepcopy(background),
                "survey": deepcopy(survey),
                "questionHash": question_hash,
                "questions": deepcopy(questions),
                "source": source,
                "date": date_key,
                "expiresAt": expires_at,
                "createdAt": datetime.now(UTC),
                "updatedAt": datetime.now(UTC),
                **deepcopy(generation_metadata or {}),
            }

    async def get_question_set(
        self, *, uid: str, set_id: str, mode: str
    ) -> dict[str, Any] | None:
        async with self._lock:
            question_set = self._question_sets.get(set_id)
            if (
                not question_set
                or question_set["uid"] != uid
                or not _mode_matches(question_set["mode"], mode)
                or question_set["expiresAt"] < datetime.now(UTC)
            ):
                return None
            return _normalize_legacy_question_set(deepcopy(question_set))

    async def get_question_history(
        self, *, uid: str, mode: str
    ) -> dict[str, list[str]]:
        async with self._lock:
            history_id = self._question_history_id(uid, mode)
            return deepcopy(
                {
                    **_question_history_defaults(),
                    **self._question_histories.get(history_id, {}),
                }
            )

    async def record_question_history(
        self,
        *,
        uid: str,
        mode: str,
        set_hash: str,
        questions: list[dict[str, Any]],
    ) -> None:
        async with self._lock:
            history_id = self._question_history_id(uid, mode)
            self._question_histories[history_id] = _merge_question_history(
                self._question_histories.get(history_id),
                set_hash=set_hash,
                questions=questions,
            )

    async def get_usage(self, uid: str, date_key: str) -> dict[str, int]:
        async with self._lock:
            return deepcopy(
                self._usage.get(self._usage_id(uid, date_key), _usage_defaults())
            )

    async def get_target_level(self, uid: str) -> str | None:
        async with self._lock:
            profile = _profile_from_value(self._profiles.get(uid))
            return str(profile["targetLevel"]) if profile else None

    async def get_learning_profile(self, uid: str) -> dict[str, Any] | None:
        async with self._lock:
            return deepcopy(_profile_from_value(self._profiles.get(uid)))

    async def set_initial_level(
        self, *, uid: str, initial_level: int, reward_nonce: str | None
    ) -> dict[str, Any]:
        async with self._lock:
            now = datetime.now(UTC)
            previous = _profile_from_value(self._profiles.get(uid))
            reward_consumed = False
            if previous and previous["beforeAdjust"] != initial_level:
                reward = self._rewards.get(reward_nonce or "")
                if (
                    not reward
                    or reward["uid"] != uid
                    or not _reward_purpose_matches(
                        reward["purpose"], RewardPurpose.TARGET_LEVEL_CHANGE
                    )
                    or reward["status"] != "verified"
                    or reward.get("consumed", False)
                    or reward["expiresAt"] < now
                ):
                    raise RewardNotVerified(
                        "verified target level change reward is required"
                    )
                reward["consumed"] = True
                reward["consumedAt"] = now
                reward["consumedFor"] = "target_level_change"
                reward_consumed = True
            created_at = (
                previous["createdAt"] if previous and previous.get("createdAt") else now
            )
            profile = _profile_from_value(
                {
                    "uid": uid,
                    "beforeAdjust": initial_level,
                    "latestAdjustment": DifficultyAdjustment.SAME.value,
                    "createdAt": created_at,
                    "updatedAt": now,
                }
            )
            assert profile is not None
            self._profiles[uid] = {
                "uid": uid,
                "targetLevel": profile["targetLevel"],
                "beforeAdjust": profile["beforeAdjust"],
                "latestAdjustment": profile["latestAdjustment"],
                "afterAdjust": profile["afterAdjust"],
                "createdAt": created_at,
                "updatedAt": now,
            }
            return _target_change_response(
                profile=profile,
                previous=previous,
                reward_consumed=reward_consumed,
            )

    async def set_target_level(
        self, *, uid: str, target_level: str, reward_nonce: str | None
    ) -> dict[str, Any]:
        initial_level = initial_level_from_target(target_level)
        if initial_level is None:
            raise ValueError("invalid target level")
        return await self.set_initial_level(
            uid=uid,
            initial_level=initial_level,
            reward_nonce=reward_nonce,
        )

    async def apply_question_set_adjustment(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        adjustment: str,
        effective_level: int,
        target_level: str,
        question_hash: str,
        questions: list[dict[str, Any]],
        generation_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            question_set = self._question_sets.get(set_id)
            if (
                not question_set
                or question_set["uid"] != uid
                or not _mode_matches(question_set["mode"], mode)
                or question_set["expiresAt"] < datetime.now(UTC)
            ):
                raise KeyError("question set not found")
            if question_set.get("status") == "complete":
                if question_set.get("adjustment") == adjustment:
                    return deepcopy(question_set)
                raise AdjustmentAlreadyApplied(
                    "question set adjustment already applied"
                )
            question_set.update(
                {
                    "targetLevel": target_level,
                    "adjustment": adjustment,
                    "effectiveLevel": effective_level,
                    "status": "complete",
                    "questionHash": question_hash,
                    "questions": deepcopy(questions),
                    "updatedAt": datetime.now(UTC),
                    **deepcopy(generation_metadata or {}),
                }
            )
            profile = _profile_from_value(self._profiles.get(uid))
            if profile:
                self._profiles[uid] = {
                    **self._profiles[uid],
                    "latestAdjustment": adjustment,
                    "afterAdjust": effective_level,
                    "targetLevel": target_level,
                    "updatedAt": datetime.now(UTC),
                }
            return deepcopy(question_set)

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
            usage["date"] = date_key
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
        self,
        uid: str,
        request_id: str,
        reward_nonce: str,
        session_hash: str | None,
        purpose: RewardPurpose = RewardPurpose.MOCK_RESULT,
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
                or not _reward_purpose_matches(reward["purpose"], purpose)
                or (session_hash is not None and reward["sessionHash"] != session_hash)
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
            usage = self._usage.setdefault(
                self._usage_id(uid, date_key), _usage_defaults()
            )
            usage["date"] = date_key
            count_key = _reward_count_key(purpose)
            if count_key and usage[count_key] >= max_daily_reward_count:
                raise UsageLimitExceeded("daily reward quota exhausted")
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
            if auto_verify and count_key:
                if usage[count_key] >= max_daily_reward_count:
                    raise UsageLimitExceeded("daily reward quota exhausted")
                usage[count_key] += 1
                usage["rewardCount"] += 1
                reward["quotaCounted"] = True
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
        max_daily_reward_count: int,
    ) -> dict[str, Any]:
        async with self._lock:
            existing_nonce = self._transactions.get(transaction_id)
            if existing_nonce:
                if existing_nonce == nonce and nonce in self._rewards:
                    return deepcopy(self._rewards[nonce])
                raise RewardNotVerified("reward transaction already processed")
            reward = self._rewards.get(nonce)
            if not reward or reward["expiresAt"] < datetime.now(UTC):
                raise RewardNotVerified("reward intent missing or expired")
            if reward.get("status") == "verified":
                if reward.get("transactionId") == transaction_id:
                    return deepcopy(reward)
                raise RewardNotVerified("reward intent already verified")
            usage = self._usage.setdefault(
                self._usage_id(reward["uid"], reward["dateKey"]), _usage_defaults()
            )
            purpose = RewardPurpose(reward["purpose"])
            count_key = _reward_count_key(purpose)
            if count_key and not reward.get("quotaCounted", False):
                if usage[count_key] >= max_daily_reward_count:
                    raise UsageLimitExceeded("daily reward quota exhausted")
                usage[count_key] += 1
                usage["rewardCount"] += 1
                reward["quotaCounted"] = True
            self._transactions[transaction_id] = nonce
            reward["status"] = "verified"
            reward["transactionId"] = transaction_id
            if reward["purpose"] is RewardPurpose.PRACTICE_CREDITS and not reward.get(
                "credited", False
            ):
                usage["bonusRemaining"] += practice_credit_amount
                reward["credited"] = True
            return deepcopy(reward)


class FirestoreStateStore(StateStore):
    def __init__(self, project_id: str | None = None) -> None:
        self._transaction_locks = _KeyedLockPool()
        # The Firestore emulator is intentionally unauthenticated.  Firebase Admin's
        # client still resolves Application Default Credentials before connecting,
        # which breaks clean CI runners even when FIRESTORE_EMULATOR_HOST is set.
        # Use an explicit anonymous Google Cloud client only for the emulator; the
        # production path below continues to require Firebase Admin/ADC.
        if os.getenv("FIRESTORE_EMULATOR_HOST"):
            emulator_project = project_id or os.getenv("GCLOUD_PROJECT")
            if not emulator_project:
                raise ValueError(
                    "project_id or GCLOUD_PROJECT is required for the Firestore emulator"
                )
            self._client = firestore.Client(
                project=emulator_project,
                credentials=AnonymousCredentials(),
            )
            return
        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                options={"projectId": project_id} if project_id else None
            )
        self._client = admin_firestore.client()

    @staticmethod
    def _usage_id(uid: str, date_key: str) -> str:
        return hashlib.sha256(f"{uid}:{date_key}".encode()).hexdigest()

    @staticmethod
    def _question_history_id(uid: str, mode: str) -> str:
        return hashlib.sha256(f"{uid}:{mode}".encode()).hexdigest()

    @staticmethod
    def _operation_id(uid: str, operation: str, operation_id: str) -> str:
        return hashlib.sha256(f"{uid}:{operation}:{operation_id}".encode()).hexdigest()

    async def create_or_get_mock_session(
        self,
        *,
        uid: str,
        session_id: str,
        session_hash: str,
        date_key: str,
        initial_level: int,
        background: dict[str, Any],
        survey: dict[str, Any] | None,
        resets_at: datetime,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            transaction = self._client.transaction(max_attempts=20)
            ref = self._client.collection("mockSessions").document(session_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> dict[str, Any]:
                snapshot = ref.get(transaction=transaction)
                existing = snapshot.to_dict() or {}
                if snapshot.exists:
                    if existing.get("uid") != uid:
                        raise KeyError("mock session not found")
                    return existing
                now = datetime.now(UTC)
                value = {
                    "uid": uid,
                    "sessionId": session_id,
                    "sessionHash": session_hash,
                    "date": date_key,
                    "stage": "awaiting_start_ad",
                    "initialLevel": initial_level,
                    "background": background,
                    "survey": survey,
                    "resetsAt": resets_at,
                    "createdAt": now,
                    "updatedAt": now,
                }
                transaction.set(ref, value)
                return value

            return apply(transaction)

        return await asyncio.to_thread(run)

    async def get_mock_session(
        self,
        *,
        uid: str,
        session_id: str | None = None,
        date_key: str | None = None,
    ) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            if session_id:
                snapshot = (
                    self._client.collection("mockSessions").document(session_id).get()
                )
                value = snapshot.to_dict() if snapshot.exists else None
                return value if value and value.get("uid") == uid else None
            query = self._client.collection("mockSessions").where("uid", "==", uid)
            if date_key:
                query = query.where("date", "==", date_key)
            snapshot = next(iter(query.limit(1).stream()), None)
            return snapshot.to_dict() if snapshot else None

        return await asyncio.to_thread(read)

    async def transition_mock_session(
        self,
        *,
        uid: str,
        session_id: str,
        expected_stages: set[str],
        stage: str,
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            transaction = self._client.transaction(max_attempts=20)
            ref = self._client.collection("mockSessions").document(session_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> dict[str, Any]:
                snapshot = ref.get(transaction=transaction)
                value = snapshot.to_dict() or {}
                if not snapshot.exists or value.get("uid") != uid:
                    raise KeyError("mock session not found")
                if value.get("stage") not in expected_stages:
                    raise InvalidSessionTransition(
                        f"expected {sorted(expected_stages)}, got {value.get('stage')}"
                    )
                changed = {
                    **(updates or {}),
                    "stage": stage,
                    "updatedAt": datetime.now(UTC),
                }
                transaction.update(ref, changed)
                return {**value, **changed}

            return apply(transaction)

        return await asyncio.to_thread(run)

    async def reserve_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        payload_hash: str,
    ) -> Reservation:
        ref = self._client.collection("operationRequests").document(
            self._operation_id(uid, operation, operation_id)
        )
        now = datetime.now(UTC)
        initial_record = {
            "uid": uid,
            "operation": operation,
            "operationId": operation_id,
            "payloadHash": payload_hash,
            "status": "processing",
            "createdAt": now,
            "updatedAt": now,
        }

        def create() -> Reservation:
            ref.create(initial_record)
            return Reservation("new")

        try:
            return await _run_with_firestore_contention_retry(create)
        except AlreadyExists:
            pass

        def run() -> Reservation:
            transaction = self._client.transaction(max_attempts=5)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> Reservation:
                snapshot = ref.get(transaction=transaction)
                existing = snapshot.to_dict() or {}
                if snapshot.exists:
                    if existing.get("payloadHash") != payload_hash:
                        raise IdempotencyConflict(
                            "idempotency key payload does not match"
                        )
                    if existing.get("status") == "completed":
                        return Reservation("cached", result=existing.get("result"))
                    if existing.get("status") == "processing":
                        raise RequestAlreadyProcessing(
                            "operation is already processing"
                        )
                now = datetime.now(UTC)
                transaction.set(
                    ref,
                    {
                        "uid": uid,
                        "operation": operation,
                        "operationId": operation_id,
                        "payloadHash": payload_hash,
                        "status": "processing",
                        "createdAt": existing.get("createdAt", now),
                        "updatedAt": now,
                    },
                )
                return Reservation("new")

            return apply(transaction)

        lock_key = f"operation:{ref.id}"
        async with self._transaction_locks.hold(lock_key):
            snapshot = await asyncio.to_thread(ref.get)
            existing = snapshot.to_dict() or {}
            if snapshot.exists:
                if existing.get("payloadHash") != payload_hash:
                    raise IdempotencyConflict(
                        "idempotency key payload does not match"
                    )
                if existing.get("status") == "completed":
                    return Reservation("cached", result=existing.get("result"))
                if existing.get("status") == "processing":
                    raise RequestAlreadyProcessing("operation is already processing")
            return await _run_with_firestore_contention_retry(run)

    async def complete_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        result: dict[str, Any],
        ttl_hours: int,
    ) -> None:
        await asyncio.to_thread(
            self._client.collection("operationRequests")
            .document(self._operation_id(uid, operation, operation_id))
            .update,
            {
                "status": "completed",
                "result": result,
                "updatedAt": datetime.now(UTC),
                "expiresAt": datetime.now(UTC) + timedelta(hours=ttl_hours),
            },
        )

    async def fail_operation(
        self,
        *,
        uid: str,
        operation: str,
        operation_id: str,
        retryable: bool,
    ) -> None:
        await asyncio.to_thread(
            self._client.collection("operationRequests")
            .document(self._operation_id(uid, operation, operation_id))
            .update,
            {
                "status": "recoverable_failed" if retryable else "terminal_failed",
                "updatedAt": datetime.now(UTC),
            },
        )

    async def get_operation(
        self,
        *,
        uid: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            snapshots = (
                self._client.collection("operationRequests")
                .where("uid", "==", uid)
                .where("operationId", "==", operation_id)
                .limit(1)
                .stream()
            )
            snapshot = next(iter(snapshots), None)
            return snapshot.to_dict() if snapshot else None

        return await asyncio.to_thread(read)

    async def save_question_set(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        target_level: str,
        initial_level: int,
        adjustment: str | None,
        effective_level: int,
        status: str,
        background: dict[str, Any],
        survey: dict[str, Any] | None,
        question_hash: str,
        questions: list[dict[str, Any]],
        expires_at: datetime,
        source: str | None = None,
        date_key: str | None = None,
        generation_metadata: dict[str, Any] | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._client.collection("questionSets").document(set_id).set,
            {
                "uid": uid,
                "setId": set_id,
                "mode": mode,
                "targetLevel": target_level,
                "initialLevel": initial_level,
                "adjustment": adjustment,
                "effectiveLevel": effective_level,
                "status": status,
                "background": background,
                "survey": survey,
                "questionHash": question_hash,
                "questions": questions,
                "source": source,
                "date": date_key,
                "expiresAt": expires_at,
                "createdAt": datetime.now(UTC),
                "updatedAt": datetime.now(UTC),
                **(generation_metadata or {}),
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
                or not _mode_matches(value.get("mode"), mode)
                or value.get("expiresAt") < datetime.now(UTC)
            ):
                return None
            return _normalize_legacy_question_set(value)

        return await asyncio.to_thread(read)

    async def get_question_history(
        self, *, uid: str, mode: str
    ) -> dict[str, list[str]]:
        def read() -> dict[str, list[str]]:
            snapshot = (
                self._client.collection("questionHistories")
                .document(self._question_history_id(uid, mode))
                .get()
            )
            return {
                **_question_history_defaults(),
                **(snapshot.to_dict() or {}),
            }

        return await asyncio.to_thread(read)

    async def record_question_history(
        self,
        *,
        uid: str,
        mode: str,
        set_hash: str,
        questions: list[dict[str, Any]],
    ) -> None:
        def write() -> None:
            ref = self._client.collection("questionHistories").document(
                self._question_history_id(uid, mode)
            )
            snapshot = ref.get()
            updated = _merge_question_history(
                snapshot.to_dict() if snapshot.exists else None,
                set_hash=set_hash,
                questions=questions,
            )
            ref.set(
                {**updated, "uid": uid, "mode": mode, "updatedAt": datetime.now(UTC)}
            )

        await asyncio.to_thread(write)

    async def get_usage(self, uid: str, date_key: str) -> dict[str, int]:
        def read() -> dict[str, int]:
            snapshot = (
                self._client.collection("dailyUsage")
                .document(self._usage_id(uid, date_key))
                .get()
            )
            return {**_usage_defaults(), **(snapshot.to_dict() or {})}

        return await asyncio.to_thread(read)

    async def get_target_level(self, uid: str) -> str | None:
        def read() -> str | None:
            snapshot = self._client.collection("userProfiles").document(uid).get()
            value = _profile_from_value(snapshot.to_dict() if snapshot.exists else None)
            return (
                str(value["targetLevel"])
                if value and value.get("targetLevel")
                else None
            )

        return await asyncio.to_thread(read)

    async def get_learning_profile(self, uid: str) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            snapshot = self._client.collection("userProfiles").document(uid).get()
            return _profile_from_value(snapshot.to_dict() if snapshot.exists else None)

        return await asyncio.to_thread(read)

    async def get_entitlement(self, uid: str) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            snapshot = self._client.collection("userProfiles").document(uid).get()
            data = snapshot.to_dict() if snapshot.exists else None
            entitlement = (data or {}).get("entitlement")
            return dict(entitlement) if entitlement else None

        return await asyncio.to_thread(read)

    async def set_entitlement(self, uid: str, *, entitlement: dict[str, Any]) -> None:
        def write() -> None:
            self._client.collection("userProfiles").document(uid).set(
                {
                    "uid": uid,
                    "entitlement": entitlement,
                    "updatedAt": datetime.now(UTC),
                },
                merge=True,
            )

        await asyncio.to_thread(write)

    async def record_iap_event(self, event_id: str, uid: str) -> bool:
        def run() -> bool:
            transaction = self._client.transaction(max_attempts=5)
            event_ref = self._client.collection("iapEvents").document(event_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> bool:
                snapshot = event_ref.get(transaction=transaction)
                if snapshot.exists:
                    return False
                transaction.set(
                    event_ref,
                    {"uid": uid, "createdAt": datetime.now(UTC)},
                )
                return True

            return apply(transaction)

        return await _run_with_firestore_contention_retry(run)

    async def set_initial_level(
        self, *, uid: str, initial_level: int, reward_nonce: str | None
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            transaction = self._client.transaction(max_attempts=20)
            profile_ref = self._client.collection("userProfiles").document(uid)
            reward_ref = (
                self._client.collection("adRewardIntents").document(reward_nonce)
                if reward_nonce
                else None
            )

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> dict[str, Any]:
                now = datetime.now(UTC)
                profile_snapshot = profile_ref.get(transaction=transaction)
                profile = profile_snapshot.to_dict() if profile_snapshot.exists else {}
                previous = _profile_from_value(profile)
                reward_consumed = False
                if previous and previous["beforeAdjust"] != initial_level:
                    if reward_ref is None:
                        raise RewardNotVerified(
                            "verified target level change reward is required"
                        )
                    reward_snapshot = reward_ref.get(transaction=transaction)
                    reward = reward_snapshot.to_dict() or {}
                    if (
                        not reward_snapshot.exists
                        or reward.get("uid") != uid
                        or not _reward_purpose_matches(
                            reward.get("purpose"), RewardPurpose.TARGET_LEVEL_CHANGE
                        )
                        or reward.get("status") != "verified"
                        or reward.get("consumed", False)
                        or not reward.get("expiresAt")
                        or reward.get("expiresAt") < now
                    ):
                        raise RewardNotVerified(
                            "verified target level change reward is required"
                        )
                    reward_consumed = True
                updated_profile = _profile_from_value(
                    {
                        "uid": uid,
                        "beforeAdjust": initial_level,
                        "latestAdjustment": DifficultyAdjustment.SAME.value,
                        "createdAt": profile.get("createdAt", now),
                        "updatedAt": now,
                    }
                )
                assert updated_profile is not None

                transaction.set(
                    profile_ref,
                    {
                        "uid": uid,
                        "targetLevel": updated_profile["targetLevel"],
                        "beforeAdjust": updated_profile["beforeAdjust"],
                        "latestAdjustment": updated_profile["latestAdjustment"],
                        "afterAdjust": updated_profile["afterAdjust"],
                        "createdAt": profile.get("createdAt", now),
                        "updatedAt": now,
                    },
                    merge=True,
                )
                if reward_ref is not None and reward_consumed:
                    transaction.update(
                        reward_ref,
                        {
                            "consumed": True,
                            "consumedAt": now,
                            "consumedFor": "target_level_change",
                        },
                    )
                return _target_change_response(
                    profile=updated_profile,
                    previous=previous,
                    reward_consumed=reward_consumed,
                )

            return apply(transaction)

        return await asyncio.to_thread(run)

    async def set_target_level(
        self, *, uid: str, target_level: str, reward_nonce: str | None
    ) -> dict[str, Any]:
        initial_level = initial_level_from_target(target_level)
        if initial_level is None:
            raise ValueError("invalid target level")
        return await self.set_initial_level(
            uid=uid,
            initial_level=initial_level,
            reward_nonce=reward_nonce,
        )

    async def apply_question_set_adjustment(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        adjustment: str,
        effective_level: int,
        target_level: str,
        question_hash: str,
        questions: list[dict[str, Any]],
        generation_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            transaction = self._client.transaction(max_attempts=20)
            set_ref = self._client.collection("questionSets").document(set_id)
            profile_ref = self._client.collection("userProfiles").document(uid)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> dict[str, Any]:
                now = datetime.now(UTC)
                snapshot = set_ref.get(transaction=transaction)
                question_set = snapshot.to_dict() if snapshot.exists else None
                if (
                    not question_set
                    or question_set.get("uid") != uid
                    or not _mode_matches(question_set.get("mode"), mode)
                    or question_set.get("expiresAt") < now
                ):
                    raise KeyError("question set not found")
                if question_set.get("status") == "complete":
                    if question_set.get("adjustment") == adjustment:
                        return question_set
                    raise AdjustmentAlreadyApplied(
                        "question set adjustment already applied"
                    )
                updates = {
                    "targetLevel": target_level,
                    "adjustment": adjustment,
                    "effectiveLevel": effective_level,
                    "status": "complete",
                    "questionHash": question_hash,
                    "questions": questions,
                    "updatedAt": now,
                    **(generation_metadata or {}),
                    # 구 문서를 부분 업데이트할 때 제거된 저장 필드가 남지 않도록 명시 삭제
                    "dateKey": firestore.DELETE_FIELD,
                    **{
                        field: firestore.DELETE_FIELD
                        for field in _LEGACY_QUESTION_SET_FIELDS
                    },
                }
                transaction.update(set_ref, updates)
                transaction.set(
                    profile_ref,
                    {
                        "latestAdjustment": adjustment,
                        "afterAdjust": effective_level,
                        "targetLevel": target_level,
                        "updatedAt": now,
                    },
                    merge=True,
                )
                return {**question_set, **updates}

            return apply(transaction)

        return await asyncio.to_thread(run)

    async def reserve_practice(
        self, uid: str, date_key: str, request_id: str, free_limit: int
    ) -> Reservation:
        usage_id = self._usage_id(uid, date_key)

        def run() -> Reservation:
            transaction = self._client.transaction(max_attempts=5)
            usage_ref = self._client.collection("dailyUsage").document(
                usage_id
            )
            request_ref = self._client.collection("aiRequests").document(request_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> Reservation:
                existing = request_ref.get(transaction=transaction)
                if existing.exists:
                    data = existing.to_dict() or {}
                    if data.get("uid") != uid:
                        raise UsageLimitExceeded(
                            "idempotency key belongs to another user"
                        )
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
                    {
                        **usage,
                        "uid": uid,
                        "date": date_key,
                        "dateKey": firestore.DELETE_FIELD,  # 구 필드 제거
                        "updatedAt": datetime.now(UTC),
                    },
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

        async with self._transaction_locks.hold(f"practice:{usage_id}"):
            return await _run_with_firestore_contention_retry(run)

    async def reserve_mock(
        self,
        uid: str,
        request_id: str,
        reward_nonce: str,
        session_hash: str | None,
        purpose: RewardPurpose = RewardPurpose.MOCK_RESULT,
    ) -> Reservation:
        def run() -> Reservation:
            transaction = self._client.transaction(max_attempts=20)
            reward_ref = self._client.collection("adRewardIntents").document(
                reward_nonce
            )
            request_ref = self._client.collection("aiRequests").document(request_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> Reservation:
                existing = request_ref.get(transaction=transaction)
                if existing.exists:
                    data = existing.to_dict() or {}
                    if data.get("uid") != uid:
                        raise RewardNotVerified(
                            "idempotency key belongs to another user"
                        )
                    if data.get("status") == "completed":
                        return Reservation("cached", result=data.get("result"))
                    if data.get("status") == "processing":
                        raise RequestAlreadyProcessing("request is already processing")

                snapshot = reward_ref.get(transaction=transaction)
                reward = snapshot.to_dict() or {}
                if (
                    not snapshot.exists
                    or reward.get("uid") != uid
                    or reward.get("purpose") != purpose.value
                    or (
                        session_hash is not None
                        and reward.get("sessionHash") != session_hash
                    )
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
            transaction = self._client.transaction(max_attempts=20)
            request_ref = self._client.collection("aiRequests").document(request_id)

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> None:
                snapshot = request_ref.get(transaction=transaction)
                data = snapshot.to_dict() or {}
                if not snapshot.exists or data.get("status") != "processing":
                    return
                source = data.get("source")
                if source in {"free", "bonus"}:
                    usage_ref = self._client.collection("dailyUsage").document(
                        data["usageId"]
                    )
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
            transaction = self._client.transaction(max_attempts=20)
            reward_ref = self._client.collection("adRewardIntents").document(nonce)
            usage_ref = self._client.collection("dailyUsage").document(
                self._usage_id(uid, date_key)
            )

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> dict[str, Any]:
                usage_snapshot = usage_ref.get(transaction=transaction)
                usage = {**_usage_defaults(), **(usage_snapshot.to_dict() or {})}
                count_key = _reward_count_key(purpose)
                if count_key and usage[count_key] >= max_daily_reward_count:
                    raise UsageLimitExceeded("daily reward quota exhausted")
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
                if auto_verify and count_key:
                    if usage[count_key] >= max_daily_reward_count:
                        raise UsageLimitExceeded("daily reward quota exhausted")
                    usage[count_key] += 1
                    usage["rewardCount"] += 1
                    reward["quotaCounted"] = True
                transaction.set(
                    usage_ref,
                    {
                        **usage,
                        "uid": uid,
                        "date": date_key,
                        "dateKey": firestore.DELETE_FIELD,  # 구 필드 제거
                        "updatedAt": datetime.now(UTC),
                    },
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
        max_daily_reward_count: int,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            transaction = self._client.transaction(max_attempts=20)
            reward_ref = self._client.collection("adRewardIntents").document(nonce)
            tx_ref = self._client.collection("adRewardIntents").document(
                f"_tx_{hashlib.sha256(transaction_id.encode()).hexdigest()}"
            )

            @firestore.transactional
            def apply(transaction: firestore.Transaction) -> dict[str, Any]:
                tx_snapshot = tx_ref.get(transaction=transaction)
                snapshot = reward_ref.get(transaction=transaction)
                reward = snapshot.to_dict() or {}
                if tx_snapshot.exists:
                    transaction_record = tx_snapshot.to_dict() or {}
                    if transaction_record.get("nonce") == nonce and snapshot.exists:
                        return reward
                    raise RewardNotVerified("reward transaction already processed")
                if not snapshot.exists or reward.get("expiresAt") < datetime.now(UTC):
                    raise RewardNotVerified("reward intent missing or expired")
                if reward.get("status") == "verified":
                    if reward.get("transactionId") == transaction_id:
                        return reward
                    raise RewardNotVerified("reward intent already verified")
                updates: dict[str, Any] = {
                    "status": "verified",
                    "transactionId": transaction_id,
                }
                usage_ref = self._client.collection("dailyUsage").document(
                    self._usage_id(reward["uid"], reward["dateKey"])
                )
                usage_snapshot = usage_ref.get(transaction=transaction)
                usage = {**_usage_defaults(), **(usage_snapshot.to_dict() or {})}
                purpose = RewardPurpose(str(reward.get("purpose")))
                count_key = _reward_count_key(purpose)
                if count_key and not reward.get("quotaCounted", False):
                    if usage[count_key] >= max_daily_reward_count:
                        raise UsageLimitExceeded("daily reward quota exhausted")
                    usage[count_key] += 1
                    usage["rewardCount"] += 1
                    updates["quotaCounted"] = True
                if purpose is RewardPurpose.PRACTICE_CREDITS and not reward.get(
                    "credited", False
                ):
                    usage["bonusRemaining"] += practice_credit_amount

                # Firestore transactions require every read to occur before the first write.
                transaction.set(
                    tx_ref,
                    {
                        "kind": "transaction",
                        "nonce": nonce,
                        "transactionId": transaction_id,
                        "expiresAt": datetime.now(UTC) + timedelta(days=30),
                    },
                )
                transaction.set(usage_ref, usage, merge=True)
                if purpose is RewardPurpose.PRACTICE_CREDITS and not reward.get(
                    "credited", False
                ):
                    updates["credited"] = True
                transaction.update(reward_ref, updates)
                return {**reward, **updates}

            return apply(transaction)

        return await asyncio.to_thread(run)
