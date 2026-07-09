"""SQLAlchemy(SQLite) 기반 StateStore 구현.

`FirestoreStateStore`와 동일한 동작/반환 계약(camelCase dict)을 유지하면서
저장소만 관계형 DB로 바꾼 구현. Firestore 트랜잭션의 원자적 read-modify-write는
프로세스 내 전역 락 + SQLAlchemy 세션 트랜잭션으로 대체한다.

문서 ID 규칙(dailyUsage/questionHistories의 sha256 문서 ID)은 Firestore와
동일하게 유지해 스키마 매핑의 대응 관계를 보존한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.api import DifficultyAdjustment, RewardPurpose
from app.models.db import (
    AdRewardIntent,
    AdRewardTransaction,
    AiRequest,
    Base,
    DailyUsage,
    QuestionHistory,
    QuestionSet,
    UserProfile,
)
from app.services.state import (
    AdjustmentAlreadyApplied,
    RequestAlreadyProcessing,
    Reservation,
    RewardNotVerified,
    StateStore,
    UsageLimitExceeded,
    _counts_toward_daily_reward_quota,
    _merge_question_history,
    _profile_from_value,
    _question_history_defaults,
    _reward_purpose_matches,
    _target_change_response,
    _usage_defaults,
)

T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(UTC)


class SqlAlchemyStateStore(StateStore):
    def __init__(self, url: str = "sqlite:///dailyopic.db") -> None:
        kwargs: dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            # 인메모리 DB는 모든 연결이 같은 DB를 보도록 StaticPool 사용.
            if ":memory:" in url or url == "sqlite://":
                kwargs["poolclass"] = StaticPool
        self._engine = create_engine(url, **kwargs)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        # Firestore 트랜잭션의 직렬성을 흉내내기 위한 프로세스 내 쓰기 락.
        self._lock = threading.Lock()

    # ---- infra helpers ---------------------------------------------------

    @contextmanager
    def _transaction(self):
        """전역 락 + 세션 트랜잭션. read-modify-write를 원자적으로 처리."""
        with self._lock:
            session = self._session_factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    @contextmanager
    def _read_session(self):
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    async def _run(self, func: Callable[[], T]) -> T:
        return await asyncio.to_thread(func)

    @staticmethod
    def _usage_id(uid: str, date_key: str) -> str:
        return hashlib.sha256(f"{uid}:{date_key}".encode()).hexdigest()

    @staticmethod
    def _question_history_id(uid: str, mode: str) -> str:
        return hashlib.sha256(f"{uid}:{mode}".encode()).hexdigest()

    def _get_or_create_usage(
        self, session: Session, uid: str, date_key: str
    ) -> DailyUsage:
        doc_id = self._usage_id(uid, date_key)
        usage = session.get(DailyUsage, doc_id)
        if usage is None:
            defaults = _usage_defaults()
            usage = DailyUsage(
                doc_id=doc_id,
                uid=uid,
                date_key=date_key,
                free_used=defaults["freeUsed"],
                bonus_remaining=defaults["bonusRemaining"],
                reward_count=defaults["rewardCount"],
                updated_at=_now(),
            )
            session.add(usage)
        return usage

    # ---- row -> camelCase dict mappers -----------------------------------

    @staticmethod
    def _profile_row_to_dict(row: UserProfile | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "uid": row.uid,
            "initialLevel": row.initial_level,
            "latestAdjustment": row.latest_adjustment,
            "effectiveLevel": row.effective_level,
            "effectiveLevelCode": row.effective_level_code,
            "targetLevel": row.target_level,
            "expectedTargetLevel": row.expected_target_level,
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }

    @staticmethod
    def _question_set_row_to_dict(row: QuestionSet) -> dict[str, Any]:
        return {
            "uid": row.uid,
            "setId": row.set_id,
            "mode": row.mode,
            "targetLevel": row.target_level,
            "expectedTargetLevel": row.expected_target_level,
            "initialLevel": row.initial_level,
            "adjustment": row.adjustment,
            "effectiveLevel": row.effective_level,
            "effectiveLevelCode": row.effective_level_code,
            "status": row.status,
            "frontQuestionCount": row.front_question_count,
            "background": row.background,
            "survey": row.survey,
            "questionHash": row.question_hash,
            "questions": row.questions,
            "source": row.source,
            "dateKey": row.date_key,
            "poolIndex": row.pool_index,
            "expiresAt": row.expires_at,
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }

    @staticmethod
    def _reward_row_to_dict(row: AdRewardIntent) -> dict[str, Any]:
        return {
            "nonce": row.nonce,
            "uid": row.uid,
            "purpose": row.purpose,
            "sessionHash": row.session_hash,
            "dateKey": row.date_key,
            "status": row.status,
            "consumed": row.consumed,
            "consumedAt": row.consumed_at,
            "consumedFor": row.consumed_for,
            "credited": row.credited,
            "transactionId": row.transaction_id,
            "expiresAt": row.expires_at,
            "createdAt": row.created_at,
        }

    # ---- question sets ---------------------------------------------------

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
        effective_level_code: str,
        status: str,
        front_question_count: int,
        background: dict[str, Any],
        survey: dict[str, Any] | None,
        question_hash: str,
        questions: list[dict[str, Any]],
        expires_at: datetime,
        source: str | None = None,
        date_key: str | None = None,
        pool_index: int | None = None,
    ) -> None:
        def run() -> None:
            now = _now()
            with self._transaction() as session:
                session.merge(
                    QuestionSet(
                        set_id=set_id,
                        uid=uid,
                        mode=mode,
                        status=status,
                        initial_level=initial_level,
                        adjustment=adjustment,
                        effective_level=effective_level,
                        effective_level_code=effective_level_code,
                        target_level=target_level,
                        expected_target_level=target_level,
                        front_question_count=front_question_count,
                        question_hash=question_hash,
                        questions=questions,
                        background=background,
                        survey=survey,
                        source=source,
                        date_key=date_key,
                        pool_index=pool_index,
                        expires_at=expires_at,
                        created_at=now,
                        updated_at=now,
                    )
                )

        await self._run(run)

    async def get_question_set(
        self, *, uid: str, set_id: str, mode: str
    ) -> dict[str, Any] | None:
        def run() -> dict[str, Any] | None:
            with self._read_session() as session:
                row = session.get(QuestionSet, set_id)
                if (
                    row is None
                    or row.uid != uid
                    or row.mode != mode
                    or row.expires_at < _now()
                ):
                    return None
                return self._question_set_row_to_dict(row)

        return await self._run(run)

    # ---- question histories ----------------------------------------------

    async def get_question_history(self, *, uid: str, mode: str) -> dict[str, list[str]]:
        def run() -> dict[str, list[str]]:
            with self._read_session() as session:
                row = session.get(QuestionHistory, self._question_history_id(uid, mode))
                if row is None:
                    return {**_question_history_defaults()}
                return {
                    "setHashes": list(row.set_hashes or []),
                    "topicIds": list(row.topic_ids or []),
                    "promptHashes": list(row.prompt_hashes or []),
                    "promptTexts": list(row.prompt_texts or []),
                }

        return await self._run(run)

    async def record_question_history(
        self,
        *,
        uid: str,
        mode: str,
        set_hash: str,
        questions: list[dict[str, Any]],
    ) -> None:
        def run() -> None:
            doc_id = self._question_history_id(uid, mode)
            with self._transaction() as session:
                row = session.get(QuestionHistory, doc_id)
                existing = (
                    {
                        "setHashes": row.set_hashes,
                        "topicIds": row.topic_ids,
                        "promptHashes": row.prompt_hashes,
                        "promptTexts": row.prompt_texts,
                    }
                    if row is not None
                    else None
                )
                updated = _merge_question_history(
                    existing, set_hash=set_hash, questions=questions
                )
                if row is None:
                    session.add(
                        QuestionHistory(
                            doc_id=doc_id,
                            uid=uid,
                            mode=mode,
                            set_hashes=updated["setHashes"],
                            topic_ids=updated["topicIds"],
                            prompt_hashes=updated["promptHashes"],
                            prompt_texts=updated["promptTexts"],
                            updated_at=_now(),
                        )
                    )
                else:
                    row.uid = uid
                    row.mode = mode
                    row.set_hashes = updated["setHashes"]
                    row.topic_ids = updated["topicIds"]
                    row.prompt_hashes = updated["promptHashes"]
                    row.prompt_texts = updated["promptTexts"]
                    row.updated_at = _now()

        await self._run(run)

    # ---- usage / profile -------------------------------------------------

    async def get_usage(self, uid: str, date_key: str) -> dict[str, int]:
        def run() -> dict[str, int]:
            with self._read_session() as session:
                row = session.get(DailyUsage, self._usage_id(uid, date_key))
                if row is None:
                    return _usage_defaults()
                return {
                    "freeUsed": row.free_used,
                    "bonusRemaining": row.bonus_remaining,
                    "rewardCount": row.reward_count,
                }

        return await self._run(run)

    async def get_target_level(self, uid: str) -> str | None:
        def run() -> str | None:
            with self._read_session() as session:
                profile = _profile_from_value(
                    self._profile_row_to_dict(session.get(UserProfile, uid))
                )
                return str(profile["targetLevel"]) if profile else None

        return await self._run(run)

    async def get_learning_profile(self, uid: str) -> dict[str, Any] | None:
        def run() -> dict[str, Any] | None:
            with self._read_session() as session:
                return _profile_from_value(
                    self._profile_row_to_dict(session.get(UserProfile, uid))
                )

        return await self._run(run)

    async def set_initial_level(
        self, *, uid: str, initial_level: int, reward_nonce: str | None
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            with self._transaction() as session:
                now = _now()
                profile_row = session.get(UserProfile, uid)
                previous = _profile_from_value(self._profile_row_to_dict(profile_row))
                reward_consumed = False
                if previous and previous["initialLevel"] != initial_level:
                    reward = (
                        session.get(AdRewardIntent, reward_nonce)
                        if reward_nonce
                        else None
                    )
                    if (
                        reward is None
                        or reward.uid != uid
                        or not _reward_purpose_matches(
                            reward.purpose, RewardPurpose.TARGET_LEVEL_CHANGE
                        )
                        or reward.status != "verified"
                        or reward.consumed
                        or reward.expires_at < now
                    ):
                        raise RewardNotVerified(
                            "verified target level change reward is required"
                        )
                    reward.consumed = True
                    reward.consumed_at = now
                    reward.consumed_for = "target_level_change"
                    reward_consumed = True

                created_at = (
                    profile_row.created_at if profile_row is not None else now
                )
                updated_profile = _profile_from_value(
                    {
                        "uid": uid,
                        "initialLevel": initial_level,
                        "latestAdjustment": DifficultyAdjustment.SAME.value,
                        "createdAt": created_at,
                        "updatedAt": now,
                    }
                )
                assert updated_profile is not None

                if profile_row is None:
                    session.add(
                        UserProfile(
                            uid=uid,
                            initial_level=updated_profile["initialLevel"],
                            latest_adjustment=updated_profile["latestAdjustment"],
                            effective_level=updated_profile["effectiveLevel"],
                            effective_level_code=updated_profile["effectiveLevelCode"],
                            target_level=updated_profile["targetLevel"],
                            expected_target_level=updated_profile["expectedTargetLevel"],
                            created_at=created_at,
                            updated_at=now,
                        )
                    )
                else:
                    profile_row.initial_level = updated_profile["initialLevel"]
                    profile_row.latest_adjustment = updated_profile["latestAdjustment"]
                    profile_row.effective_level = updated_profile["effectiveLevel"]
                    profile_row.effective_level_code = updated_profile[
                        "effectiveLevelCode"
                    ]
                    profile_row.target_level = updated_profile["targetLevel"]
                    profile_row.expected_target_level = updated_profile[
                        "expectedTargetLevel"
                    ]
                    profile_row.updated_at = now

                return _target_change_response(
                    profile=updated_profile,
                    previous=previous,
                    reward_consumed=reward_consumed,
                )

        return await self._run(run)

    async def set_target_level(
        self, *, uid: str, target_level: str, reward_nonce: str | None
    ) -> dict[str, Any]:
        from app.services.difficulty import initial_level_from_target

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
        effective_level_code: str,
        target_level: str,
        question_hash: str,
        questions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            with self._transaction() as session:
                now = _now()
                row = session.get(QuestionSet, set_id)
                if (
                    row is None
                    or row.uid != uid
                    or row.mode != mode
                    or row.expires_at < now
                ):
                    raise KeyError("question set not found")
                if row.status == "complete":
                    if row.adjustment == adjustment:
                        return self._question_set_row_to_dict(row)
                    raise AdjustmentAlreadyApplied(
                        "question set adjustment already applied"
                    )
                row.target_level = target_level
                row.expected_target_level = target_level
                row.adjustment = adjustment
                row.effective_level = effective_level
                row.effective_level_code = effective_level_code
                row.status = "complete"
                row.question_hash = question_hash
                row.questions = questions
                row.updated_at = now

                profile_row = session.get(UserProfile, uid)
                if profile_row is not None:
                    profile_row.latest_adjustment = adjustment
                    profile_row.effective_level = effective_level
                    profile_row.effective_level_code = effective_level_code
                    profile_row.expected_target_level = target_level
                    profile_row.target_level = target_level
                    profile_row.updated_at = now

                return self._question_set_row_to_dict(row)

        return await self._run(run)

    # ---- reservations ----------------------------------------------------

    async def reserve_practice(
        self, uid: str, date_key: str, request_id: str, free_limit: int
    ) -> Reservation:
        def run() -> Reservation:
            with self._transaction() as session:
                existing = session.get(AiRequest, request_id)
                if existing is not None:
                    if existing.uid != uid:
                        raise UsageLimitExceeded(
                            "idempotency key belongs to another user"
                        )
                    if existing.status == "completed":
                        return Reservation("cached", result=existing.result)
                    if existing.status == "processing":
                        raise RequestAlreadyProcessing("request is already processing")

                usage = self._get_or_create_usage(session, uid, date_key)
                if usage.free_used < free_limit:
                    usage.free_used += 1
                    source = "free"
                elif usage.bonus_remaining > 0:
                    usage.bonus_remaining -= 1
                    source = "bonus"
                else:
                    raise UsageLimitExceeded("daily practice quota exhausted")
                usage.updated_at = _now()

                session.merge(
                    AiRequest(
                        request_id=request_id,
                        uid=uid,
                        status="processing",
                        source=source,
                        usage_id=usage.doc_id,
                        created_at=_now(),
                    )
                )
                return Reservation("new", source=source)

        return await self._run(run)

    async def reserve_mock(
        self, uid: str, request_id: str, reward_nonce: str, session_hash: str
    ) -> Reservation:
        def run() -> Reservation:
            with self._transaction() as session:
                existing = session.get(AiRequest, request_id)
                if existing is not None:
                    if existing.uid != uid:
                        raise RewardNotVerified(
                            "idempotency key belongs to another user"
                        )
                    if existing.status == "completed":
                        return Reservation("cached", result=existing.result)
                    if existing.status == "processing":
                        raise RequestAlreadyProcessing("request is already processing")

                reward = session.get(AdRewardIntent, reward_nonce)
                if (
                    reward is None
                    or reward.uid != uid
                    or not _reward_purpose_matches(
                        reward.purpose, RewardPurpose.MOCK_RESULT
                    )
                    or reward.session_hash != session_hash
                    or reward.status != "verified"
                    or reward.consumed
                    or reward.expires_at < _now()
                ):
                    raise RewardNotVerified("verified mock reward is required")
                reward.consumed = True

                session.merge(
                    AiRequest(
                        request_id=request_id,
                        uid=uid,
                        status="processing",
                        source=f"mock:{reward_nonce}",
                        usage_id=None,
                        created_at=_now(),
                    )
                )
                return Reservation("new", source=f"mock:{reward_nonce}")

        return await self._run(run)

    async def finalize_request(
        self, request_id: str, result: dict[str, Any], ttl_hours: int
    ) -> None:
        def run() -> None:
            with self._transaction() as session:
                row = session.get(AiRequest, request_id)
                if row is None:
                    raise KeyError(request_id)
                row.status = "completed"
                row.result = result
                row.expires_at = _now() + timedelta(hours=ttl_hours)

        await self._run(run)

    async def fail_request(self, request_id: str) -> None:
        def run() -> None:
            with self._transaction() as session:
                row = session.get(AiRequest, request_id)
                if row is None or row.status != "processing":
                    return
                source = row.source
                if source in {"free", "bonus"} and row.usage_id is not None:
                    usage = session.get(DailyUsage, row.usage_id)
                    if usage is not None:
                        if source == "free":
                            usage.free_used = max(0, usage.free_used - 1)
                        else:
                            usage.bonus_remaining += 1
                        usage.updated_at = _now()
                elif isinstance(source, str) and source.startswith("mock:"):
                    reward = session.get(AdRewardIntent, source.split(":", 1)[1])
                    if reward is not None:
                        reward.consumed = False
                row.status = "failed"

        await self._run(run)

    # ---- rewards ---------------------------------------------------------

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
            with self._transaction() as session:
                usage = self._get_or_create_usage(session, uid, date_key)
                if _counts_toward_daily_reward_quota(purpose):
                    if usage.reward_count >= max_daily_reward_count:
                        raise UsageLimitExceeded("daily reward quota exhausted")
                    usage.reward_count += 1
                    usage.updated_at = _now()

                credited: bool | None = None
                if auto_verify and purpose is RewardPurpose.PRACTICE_CREDITS:
                    usage.bonus_remaining += practice_credit_amount
                    usage.updated_at = _now()
                    credited = True

                reward = AdRewardIntent(
                    nonce=nonce,
                    uid=uid,
                    purpose=purpose.value,
                    session_hash=session_hash,
                    date_key=date_key,
                    status="verified" if auto_verify else "pending",
                    consumed=False,
                    credited=credited,
                    expires_at=expires_at,
                    created_at=_now(),
                )
                session.add(reward)
                session.flush()
                return self._reward_row_to_dict(reward)

        return await self._run(run)

    async def get_reward_intent(self, nonce: str, uid: str) -> dict[str, Any] | None:
        def run() -> dict[str, Any] | None:
            with self._read_session() as session:
                reward = session.get(AdRewardIntent, nonce)
                if reward is None or reward.uid != uid:
                    return None
                return self._reward_row_to_dict(reward)

        return await self._run(run)

    async def verify_reward(
        self,
        *,
        nonce: str,
        transaction_id: str,
        practice_credit_amount: int,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            with self._transaction() as session:
                now = _now()
                if session.get(AdRewardTransaction, transaction_id) is not None:
                    raise RewardNotVerified("reward transaction already processed")
                reward = session.get(AdRewardIntent, nonce)
                if reward is None or reward.expires_at < now:
                    raise RewardNotVerified("reward intent missing or expired")

                session.add(
                    AdRewardTransaction(
                        transaction_id=transaction_id,
                        expires_at=now + timedelta(days=30),
                        created_at=now,
                    )
                )
                reward.status = "verified"
                reward.transaction_id = transaction_id
                if (
                    _reward_purpose_matches(reward.purpose, RewardPurpose.PRACTICE_CREDITS)
                    and not reward.credited
                ):
                    usage = self._get_or_create_usage(session, reward.uid, reward.date_key)
                    usage.bonus_remaining += practice_credit_amount
                    usage.updated_at = now
                    reward.credited = True
                return self._reward_row_to_dict(reward)

        return await self._run(run)
