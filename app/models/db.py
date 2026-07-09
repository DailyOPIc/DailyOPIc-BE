"""RDB(SQLAlchemy) 모델.

Firestore 컬렉션을 관계형 스키마로 매핑한 정의. `docs/domain-model.dbml`의
테이블/관계를 그대로 옮겼으며, 컬럼은 snake_case로 두고 StateStore 구현에서
API 계약(camelCase)으로 매핑한다.

주의: Firestore가 FK를 강제하지 않는 것과 동일한 의미론을 유지하기 위해
ForeignKey 컬럼은 스키마 문서화 용도로만 선언하고, SQLite FK 강제(PRAGMA
foreign_keys)는 켜지 않는다. (usage/request 문서가 프로필보다 먼저 생성되는
기존 흐름을 깨지 않기 위함)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    TypeDecorator,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UtcDateTime(TypeDecorator):
    """항상 tz-aware UTC datetime으로 주고받는 컬럼 타입.

    SQLite의 DateTime은 tzinfo를 버리기 때문에, 저장 시 UTC naive로 정규화하고
    조회 시 UTC tzinfo를 다시 붙인다. 이렇게 해야 `expires_at < now(UTC)` 같은
    비교가 naive/aware 혼용으로 깨지지 않고, Firestore 구현과 동일하게
    aware datetime을 반환한다.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class UserProfile(Base):
    """컬렉션: userProfiles (문서 ID = uid)."""

    __tablename__ = "user_profiles"

    uid: Mapped[str] = mapped_column(String, primary_key=True)
    initial_level: Mapped[int] = mapped_column(Integer, nullable=False)
    latest_adjustment: Mapped[str] = mapped_column(String, nullable=False, default="same")
    effective_level: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_level_code: Mapped[str] = mapped_column(String, nullable=False)
    target_level: Mapped[str] = mapped_column(String, nullable=False)
    expected_target_level: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class QuestionSet(Base):
    """컬렉션: questionSets (문서 ID = setId)."""

    __tablename__ = "question_sets"

    set_id: Mapped[str] = mapped_column(String, primary_key=True)
    uid: Mapped[str] = mapped_column(
        String, ForeignKey("user_profiles.uid"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    initial_level: Mapped[int] = mapped_column(Integer, nullable=False)
    adjustment: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_level: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_level_code: Mapped[str] = mapped_column(String, nullable=False)
    target_level: Mapped[str] = mapped_column(String, nullable=False)
    expected_target_level: Mapped[str] = mapped_column(String, nullable=False)
    front_question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    question_hash: Mapped[str] = mapped_column(String, nullable=False)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    background: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    survey: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    date_key: Mapped[str | None] = mapped_column(String, nullable=True)
    pool_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class QuestionHistory(Base):
    """컬렉션: questionHistories (문서 ID = sha256(uid:mode))."""

    __tablename__ = "question_histories"

    doc_id: Mapped[str] = mapped_column(String, primary_key=True)
    uid: Mapped[str] = mapped_column(
        String, ForeignKey("user_profiles.uid"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String, nullable=False)
    set_hashes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    topic_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    prompt_hashes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    prompt_texts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class DailyUsage(Base):
    """컬렉션: dailyUsage (문서 ID = sha256(uid:dateKey))."""

    __tablename__ = "daily_usage"

    doc_id: Mapped[str] = mapped_column(String, primary_key=True)
    uid: Mapped[str] = mapped_column(
        String, ForeignKey("user_profiles.uid"), nullable=False, index=True
    )
    date_key: Mapped[str] = mapped_column(String, nullable=False)
    free_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bonus_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reward_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AdRewardIntent(Base):
    """컬렉션: adRewardIntents 중 일반 리워드 문서 (문서 ID = nonce)."""

    __tablename__ = "ad_reward_intents"

    nonce: Mapped[str] = mapped_column(String, primary_key=True)
    uid: Mapped[str] = mapped_column(
        String, ForeignKey("user_profiles.uid"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    session_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    date_key: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    consumed_for: Mapped[str | None] = mapped_column(String, nullable=True)
    credited: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AdRewardTransaction(Base):
    """트랜잭션 replay 방지 레코드.

    Firestore에서는 같은 컬렉션에 `_tx_{hash}` 문서로 섞여 있던 것을 별도
    테이블로 분리(핸드오프에서 지적한 구조적 이슈 해소).
    """

    __tablename__ = "ad_reward_transactions"

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AiRequest(Base):
    """컬렉션: aiRequests (문서 ID = Idempotency-Key)."""

    __tablename__ = "ai_requests"

    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    uid: Mapped[str] = mapped_column(
        String, ForeignKey("user_profiles.uid"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    usage_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("daily_usage.doc_id"), nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
