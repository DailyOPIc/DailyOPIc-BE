from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import PlainTextResponse
from pydantic import TypeAdapter, ValidationError

from app.models.api import (
    BackgroundProfile,
    BackgroundSurvey,
    CapabilitiesResponse,
    CapabilityQuotaPolicy,
    DifficultyAdjustment,
    GeneratedQuestion,
    MockEvaluation,
    MockEvaluationManifest,
    MockExamRequest,
    MockSessionAdjustmentRequest,
    MockSessionResponse,
    MockSessionRewardRequest,
    MockSessionStage,
    OperationResponse,
    PracticeEvaluation,
    PracticeRefreshRequest,
    PracticeSetRequest,
    QuestionSetAdjustmentRequest,
    QuestionSetResponse,
    QuestionSetStatus,
    RevenueCatWebhook,
    RewardIntentRequest,
    RewardIntentResponse,
    RewardPurpose,
    TargetLevelRequest,
    TargetLevelResponse,
    UsageResponse,
)
from app.services.admob import SSVVerificationError
from app.services.ai import (
    AIQuestionGenerationError,
    AIServiceError,
    QuestionGenerationResult,
)
from app.services.audio import AudioValidationError
from app.services.auth import CurrentUser, current_user
from app.services.difficulty import (
    adjusted_level,
    effective_level_code,
    expected_target_level,
    initial_level_from_target,
)
from app.services.questions import prompt_hash, question_set_hash
from app.services import plans
from app.services.plans import Plan
from app.services.state import (
    AdjustmentAlreadyApplied,
    IdempotencyConflict,
    InvalidSessionTransition,
    RequestAlreadyProcessing,
    RewardNotVerified,
    UsageLimitExceeded,
    resolve_plan,
)


logger = logging.getLogger(__name__)
router = APIRouter()
KST = ZoneInfo("Asia/Seoul")
QUESTION_LIST = TypeAdapter(list[GeneratedQuestion])
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
MOCK_AUDIO_AGGREGATE_MAX_BYTES = 30 * 1024 * 1024


async def _current_plan(request: Request, uid: str) -> Plan:
    """엔타이틀먼트를 조회해 현재 유효 플랜을 반환(만료/미존재 → free)."""
    entitlement = await request.app.state.state_store.get_entitlement(uid)
    return Plan(resolve_plan(entitlement))


def _quota_policy_for(plan: Plan) -> CapabilityQuotaPolicy:
    limits = plans.limits_for(plan)
    return CapabilityQuotaPolicy(
        dailyAnalysisFree=limits.practice_daily,
        dailyRefreshRewards=plans.reward_max_for(plan, RewardPurpose.PRACTICE_REFRESH),
        mockSessionsPerDay=limits.mock_daily,
        mockRewardGates=3 if limits.mock_requires_ad else 0,
        practiceDaily=limits.practice_daily,
        practiceAdBonus=limits.practice_ad_bonus,
        historyDays=limits.history_days,
        analysisDepth=str(limits.analysis_depth),
        gradeTrend=str(limits.grade_trend),
        weaknessAnalysis=str(limits.weakness_analysis),
        reviewSet=limits.review_set,
        weeklyReport=limits.weekly_report,
        mockComparison=str(limits.mock_comparison),
        adsEnabled=limits.ads_enabled,
    )


@router.get("/v1/capabilities", response_model=CapabilitiesResponse)
async def capabilities(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CapabilitiesResponse:
    settings = request.app.state.settings
    plan = await _current_plan(request, user.uid)
    return CapabilitiesResponse(
        minimumSupportedAppVersion=settings.minimum_supported_app_version,
        questionGenerationV2=settings.question_generation_v2_enabled,
        mockSessionV2=settings.mock_session_v2_enabled,
        evaluationRubricV2=settings.evaluation_rubric_v2_enabled,
        practiceRefresh=settings.practice_refresh_enabled,
        guideSchemaVersion=settings.guide_schema_version,
        plan=str(plan),
        quotaPolicy=_quota_policy_for(plan),
    )


def _date_key() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def _next_reset() -> datetime:
    return (datetime.now(KST) + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _request_id(value: str | None) -> str:
    if not value or not REQUEST_ID_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_idempotency_key",
                "message": "A valid Idempotency-Key header is required.",
            },
        )
    return value


def _uid_hash(uid: str) -> str:
    return hashlib.sha256(uid.encode()).hexdigest()[:12]


def _daily_free_set_id(uid: str, date_key: str) -> str:
    return hashlib.sha256(f"{uid}:practice:{date_key}:free".encode()).hexdigest()


def _stable_json(value: dict[str, object] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_hash(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


async def _reserve_operation(
    request: Request,
    user: CurrentUser,
    *,
    operation: str,
    operation_id: str,
    payload: object,
):
    try:
        return await request.app.state.state_store.reserve_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            payload_hash=_payload_hash(payload),
        )
    except IdempotencyConflict as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_conflict",
                "message": str(error),
                "operationId": operation_id,
            },
        ) from error
    except RequestAlreadyProcessing as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "request_processing",
                "message": str(error),
                "operationId": operation_id,
                "retryable": True,
            },
            headers={"Retry-After": "2"},
        ) from error


def _reward_response(reward: dict[str, object], user_uid: str) -> RewardIntentResponse:
    purpose = reward["purpose"]
    if isinstance(purpose, str):
        purpose = RewardPurpose(purpose)
    return RewardIntentResponse(
        nonce=str(reward["nonce"]),
        purpose=purpose,
        status=str(reward["status"]),
        userIdentifier=user_uid,
        customData=str(reward["nonce"]),
        expiresAt=reward["expiresAt"],
    )


def _target_level_change_required(message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={"code": "target_level_change_reward_required", "message": message},
    )


def _request_initial_level(payload: PracticeSetRequest | TargetLevelRequest) -> int:
    if payload.initial_level is not None:
        return payload.initial_level
    value = initial_level_from_target(payload.target_level)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_initial_level", "message": "initialLevel is required"},
        )
    return value


def _question_set_response(
    *,
    set_id: str,
    set_hash: str,
    questions: list[GeneratedQuestion],
    model_version: str,
    fallback_used: bool,
    initial_level: int,
    adjustment: DifficultyAdjustment | None,
    effective_level: int,
    status_value: QuestionSetStatus,
    generation_metadata: dict[str, object] | None = None,
) -> QuestionSetResponse:
    metadata = generation_metadata or {}
    return QuestionSetResponse(
        setId=set_id,
        setHash=set_hash,
        questions=questions,
        modelVersion=str(metadata.get("modelVersion") or model_version),
        generatedAt=metadata.get("generatedAt") or datetime.now(UTC),
        fallbackUsed=bool(metadata.get("fallbackUsed", fallback_used)),
        initialLevel=initial_level,
        adjustment=adjustment,
        effectiveLevel=effective_level,
        effectiveLevelCode=effective_level_code(
            initial_level, adjustment or DifficultyAdjustment.SAME
        ),
        expectedTargetLevel=expected_target_level(effective_level),
        status=status_value,
        requiresAdjustmentAfter=7
        if status_value is QuestionSetStatus.AWAITING_ADJUSTMENT
        else None,
        isComplete=status_value is QuestionSetStatus.COMPLETE,
        provider=str(metadata.get("provider") or "openai"),
        fallbackReason=metadata.get("fallbackReason"),
        fallbackQuestionNumbers=list(metadata.get("fallbackQuestionNumbers") or []),
        retryCount=int(metadata.get("retryCount") or 0),
        promptVersion=metadata.get("promptVersion"),
        schemaVersion=metadata.get("schemaVersion"),
        serverDateKey=str(metadata.get("serverDateKey") or _date_key()),
    )


async def _mock_session_response(
    request: Request,
    user: CurrentUser,
    record: dict[str, object],
) -> MockSessionResponse:
    question_set = None
    set_id = record.get("setId")
    if set_id:
        stored = await request.app.state.state_store.get_question_set(
            uid=user.uid,
            set_id=str(set_id),
            mode="mock",
        )
        if stored:
            question_set = _question_set_response_from_record(
                stored,
                model_version=request.app.state.ai_service.model,
            )
    return MockSessionResponse(
        sessionId=str(record["sessionId"]),
        sessionHash=str(record["sessionHash"]),
        serverDateKey=str(record["date"]),
        stage=str(record["stage"]),
        resetsAt=record["resetsAt"],
        setId=str(set_id) if set_id else None,
        setHash=str(record.get("setHash")) if record.get("setHash") else None,
        adjustment=record.get("adjustment"),
        questionSet=question_set,
    )


def _generation_metadata(
    generation: QuestionGenerationResult,
    *,
    model_version: str,
) -> dict[str, object]:
    return {
        "modelVersion": model_version,
        "generatedAt": datetime.now(UTC),
        "fallbackUsed": generation.fallback_used,
        "provider": generation.provider,
        "fallbackReason": generation.fallback_reason,
        "fallbackQuestionNumbers": list(generation.fallback_question_numbers),
        "retryCount": generation.retry_count,
        "promptVersion": generation.prompt_version,
        "schemaVersion": generation.schema_version,
        "serverDateKey": _date_key(),
    }


def _question_set_response_from_record(
    record: dict[str, object],
    *,
    model_version: str,
) -> QuestionSetResponse:
    questions = QUESTION_LIST.validate_python(record["questions"])
    adjustment_value = record.get("adjustment")
    adjustment = DifficultyAdjustment(str(adjustment_value)) if adjustment_value else None
    status_value = QuestionSetStatus(str(record.get("status") or "complete"))
    return _question_set_response(
        set_id=str(record["setId"]),
        set_hash=str(record["questionHash"]),
        questions=questions,
        model_version=str(record.get("modelVersion") or model_version),
        fallback_used=bool(record.get("fallbackUsed", False)),
        initial_level=int(record["initialLevel"]),
        adjustment=adjustment,
        effective_level=int(record["effectiveLevel"]),
        status_value=status_value,
        generation_metadata=record,
    )


async def _ensure_initial_level(
    request: Request, user: CurrentUser, initial_level: int
) -> None:
    profile = await request.app.state.state_store.get_learning_profile(user.uid)
    if profile is None:
        try:
            await request.app.state.state_store.set_initial_level(
                uid=user.uid,
                initial_level=initial_level,
                reward_nonce=None,
            )
        except RewardNotVerified as error:
            _target_level_change_required(str(error))
        return
    if int(profile["beforeAdjust"]) != initial_level:
        _target_level_change_required(
            "Self Assessment 단계를 변경하려면 보상형 광고를 끝까지 시청해야 합니다."
        )


def _daily_record_matches(
    record: dict[str, object],
    *,
    initial_level: int,
    background: dict[str, object],
    survey: dict[str, object] | None,
    date_key: str,
) -> bool:
    return (
        record.get("source") == "free"
        and record.get("date") == date_key
        and int(record.get("initialLevel") or 0) == initial_level
        and _stable_json(record.get("background")) == _stable_json(background)
        and _stable_json(record.get("survey")) == _stable_json(survey)
    )


async def _create_daily_pool(
    request: Request,
    user: CurrentUser,
    payload: PracticeSetRequest,
    *,
    adjustment: DifficultyAdjustment,
    source: str,
    date_key: str,
    set_id: str | None = None,
    focus_dimension: str | None = None,
) -> QuestionSetResponse:
    initial_level = _request_initial_level(payload)
    effective_level = adjusted_level(initial_level, adjustment)
    expected_level = expected_target_level(effective_level)
    await _ensure_initial_level(request, user, initial_level)
    uid_hash = _uid_hash(user.uid)
    history = await request.app.state.state_store.get_question_history(
        uid=user.uid,
        mode="daily",
    )
    logger.info(
        "question generation requested mode=daily kind=daily_pool uidHash=%s "
        "initialLevel=%s adjustment=%s effectiveLevelCode=%s expectedTargetLevel=%s "
        "source=%s mockAI=%s model=%s recentSetCount=%s recentTopicCount=%s "
        "recentPromptCount=%s",
        uid_hash,
        initial_level,
        adjustment.value,
        effective_level_code(initial_level, adjustment),
        expected_level.value,
        source,
        request.app.state.settings.mock_ai,
        request.app.state.ai_service.model,
        len(history.get("setHashes", [])),
        len(history.get("topicIds", [])),
        len(history.get("promptHashes", [])),
    )
    try:
        generation = await request.app.state.ai_service.generate_daily_pool(
            initial_level,
            payload.background,
            payload.survey,
            adjustment=adjustment,
            history=history,
            focus_dimension=focus_dimension,
        )
    except AIQuestionGenerationError as error:
        logger.exception(
            "question generation failed mode=daily kind=daily_pool uidHash=%s "
            "initialLevel=%s adjustment=%s model=%s",
            uid_hash,
            initial_level,
            adjustment.value,
            request.app.state.ai_service.model,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ai_question_generation_failed",
                "message": "AI 질문 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
            },
        ) from error

    questions = generation.questions
    saved_set_id = set_id or str(uuid.uuid4())
    serialized = [item.model_dump(by_alias=True, mode="json") for item in questions]
    set_hash = question_set_hash(serialized)
    generation_metadata = _generation_metadata(
        generation,
        model_version=request.app.state.ai_service.model,
    )
    await request.app.state.state_store.save_question_set(
        uid=user.uid,
        set_id=saved_set_id,
        mode="daily",
        target_level=expected_level.value,
        initial_level=initial_level,
        adjustment=adjustment.value,
        effective_level=effective_level,
        status=QuestionSetStatus.COMPLETE.value,
        background=payload.background.model_dump(mode="json"),
        survey=payload.survey.model_dump(mode="json") if payload.survey else None,
        question_hash=set_hash,
        questions=serialized,
        expires_at=datetime.now(UTC) + timedelta(days=2),
        source=source,
        date_key=date_key,
        generation_metadata=generation_metadata,
    )
    await request.app.state.state_store.record_question_history(
        uid=user.uid,
        mode="daily",
        set_hash=set_hash,
        questions=serialized,
    )
    prompt_hashes = [prompt_hash(str(item.get("prompt") or ""))[:16] for item in serialized]
    usage = generation.usage
    logger.info(
        "question generation succeeded mode=daily kind=daily_pool uidHash=%s "
        "initialLevel=%s adjustment=%s effectiveLevelCode=%s expectedTargetLevel=%s "
        "provider=%s model=%s openaiResponseId=%s fallbackUsed=%s setHash=%s "
        "source=%s promptHashes=%s inputTokens=%s cachedInputTokens=%s "
        "outputTokens=%s reasoningTokens=%s totalTokens=%s",
        uid_hash,
        initial_level,
        adjustment.value,
        effective_level_code(initial_level, adjustment),
        expected_level.value,
        generation.provider,
        request.app.state.ai_service.model,
        generation.openai_response_id,
        generation.fallback_used,
        set_hash,
        source,
        prompt_hashes,
        usage.input_tokens if usage else None,
        usage.cached_input_tokens if usage else None,
        usage.output_tokens if usage else None,
        usage.reasoning_tokens if usage else None,
        usage.total_tokens if usage else None,
    )
    return _question_set_response(
        set_id=saved_set_id,
        set_hash=set_hash,
        questions=questions,
        model_version=request.app.state.ai_service.model,
        fallback_used=generation.fallback_used,
        initial_level=initial_level,
        adjustment=adjustment,
        effective_level=effective_level,
        status_value=QuestionSetStatus.COMPLETE,
        generation_metadata=generation_metadata,
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/operations/{operation_id}", response_model=OperationResponse)
async def operation_status(
    operation_id: str,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> OperationResponse:
    if not REQUEST_ID_PATTERN.fullmatch(operation_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_operation_id"},
        )
    record = await request.app.state.state_store.get_operation(
        uid=user.uid,
        operation_id=operation_id,
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "operation_not_found"},
        )
    operation_state = str(record.get("status") or "processing")
    return OperationResponse(
        operationId=operation_id,
        operation=str(record.get("operation") or "unknown"),
        status=operation_state,
        result=record.get("result"),
        retryable=operation_state in {"processing", "recoverable_failed"},
        updatedAt=record.get("updatedAt"),
    )


async def _create_question_set(
    request: Request,
    user: CurrentUser,
    payload: PracticeSetRequest,
    *,
    mode: str,
    set_id: str | None = None,
    date_key: str | None = None,
) -> QuestionSetResponse:
    initial_level = _request_initial_level(payload)
    effective_level = initial_level
    expected_level = expected_target_level(effective_level)
    await _ensure_initial_level(request, user, initial_level)
    uid_hash = _uid_hash(user.uid)
    history = await request.app.state.state_store.get_question_history(
        uid=user.uid,
        mode=mode,
    )
    logger.info(
        "question generation requested mode=%s stage=front uidHash=%s initialLevel=%s "
        "adjustment=%s effectiveLevelCode=%s expectedTargetLevel=%s mockAI=%s "
        "model=%s recentSetCount=%s recentTopicCount=%s recentPromptCount=%s",
        mode,
        uid_hash,
        initial_level,
        None,
        effective_level_code(initial_level, DifficultyAdjustment.SAME),
        expected_level.value,
        request.app.state.settings.mock_ai,
        request.app.state.ai_service.model,
        len(history.get("setHashes", [])),
        len(history.get("topicIds", [])),
        len(history.get("promptHashes", [])),
    )
    try:
        if mode == "mock":
            generation = await request.app.state.ai_service.generate_mock(
                initial_level,
                payload.background,
                getattr(payload, "survey", None),
                stage="front",
                history=history,
            )
        else:
            generation = await request.app.state.ai_service.generate_practice(
                initial_level,
                payload.background,
                stage="front",
                history=history,
            )
    except AIQuestionGenerationError as error:
        logger.exception(
            "question generation failed mode=%s stage=front uidHash=%s initialLevel=%s model=%s",
            mode,
            uid_hash,
            initial_level,
            request.app.state.ai_service.model,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ai_question_generation_failed",
                "message": "AI 질문 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
            },
        ) from error
    questions = generation.questions
    set_id = set_id or str(uuid.uuid4())
    serialized = [item.model_dump(by_alias=True, mode="json") for item in questions]
    set_hash = question_set_hash(serialized)
    generation_metadata = _generation_metadata(
        generation,
        model_version=request.app.state.ai_service.model,
    )
    await request.app.state.state_store.save_question_set(
        uid=user.uid,
        set_id=set_id,
        mode=mode,
        target_level=expected_level.value,
        initial_level=initial_level,
        adjustment=None,
        effective_level=effective_level,
        status=QuestionSetStatus.AWAITING_ADJUSTMENT.value,
        background=payload.background.model_dump(mode="json"),
        survey=getattr(payload, "survey", None).model_dump(mode="json")
        if getattr(payload, "survey", None)
        else None,
        question_hash=set_hash,
        questions=serialized,
        expires_at=datetime.now(UTC)
        + timedelta(seconds=86_400 if mode == "practice" else 7 * 86_400),
        source="daily" if date_key else None,
        date_key=date_key,
        generation_metadata=generation_metadata,
    )
    await request.app.state.state_store.record_question_history(
        uid=user.uid,
        mode=mode,
        set_hash=set_hash,
        questions=serialized,
    )
    prompt_hashes = [prompt_hash(str(item.get("prompt") or ""))[:16] for item in serialized]
    usage = generation.usage
    logger.info(
        "question generation succeeded mode=%s stage=front uidHash=%s initialLevel=%s "
        "adjustment=%s effectiveLevelCode=%s expectedTargetLevel=%s provider=%s "
        "model=%s openaiResponseId=%s fallbackUsed=%s setHash=%s "
        "promptHashes=%s inputTokens=%s cachedInputTokens=%s outputTokens=%s "
        "reasoningTokens=%s totalTokens=%s",
        mode,
        uid_hash,
        initial_level,
        None,
        effective_level_code(initial_level, DifficultyAdjustment.SAME),
        expected_level.value,
        generation.provider,
        request.app.state.ai_service.model,
        generation.openai_response_id,
        generation.fallback_used,
        set_hash,
        prompt_hashes,
        usage.input_tokens if usage else None,
        usage.cached_input_tokens if usage else None,
        usage.output_tokens if usage else None,
        usage.reasoning_tokens if usage else None,
        usage.total_tokens if usage else None,
    )
    return _question_set_response(
        set_id=set_id,
        set_hash=set_hash,
        questions=questions,
        model_version=request.app.state.ai_service.model,
        fallback_used=generation.fallback_used,
        initial_level=initial_level,
        adjustment=None,
        effective_level=effective_level,
        status_value=QuestionSetStatus.AWAITING_ADJUSTMENT,
        generation_metadata=generation_metadata,
    )


@router.put("/v1/users/me/target-level", response_model=TargetLevelResponse)
async def update_target_level(
    payload: TargetLevelRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> TargetLevelResponse:
    initial_level = _request_initial_level(payload)
    try:
        result = await request.app.state.state_store.set_initial_level(
            uid=user.uid,
            initial_level=initial_level,
            reward_nonce=payload.reward_nonce,
        )
    except RewardNotVerified as error:
        _target_level_change_required(str(error))
    return TargetLevelResponse(
        targetLevel=result["targetLevel"],
        previousTargetLevel=result["previousTargetLevel"],
        beforeAdjust=result["beforeAdjust"],
        previousBeforeAdjust=result["previousBeforeAdjust"],
        afterAdjust=result["afterAdjust"],
        changed=result["changed"],
        rewardConsumed=result["rewardConsumed"],
    )


@router.post("/v1/question-sets/practice", response_model=QuestionSetResponse)
async def create_practice_set(
    payload: PracticeSetRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> QuestionSetResponse:
    date_key = _date_key()
    initial_level = _request_initial_level(payload)
    free_set_id = _daily_free_set_id(user.uid, date_key)
    existing = await request.app.state.state_store.get_question_set(
        uid=user.uid,
        set_id=free_set_id,
        mode="daily",
    )
    background = payload.background.model_dump(mode="json")
    survey = payload.survey.model_dump(mode="json") if payload.survey else None
    if existing:
        return _question_set_response_from_record(
            existing,
            model_version=request.app.state.ai_service.model,
        )
    operation = "daily_free_generation"
    operation_id = f"daily-{date_key}"
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload={"date": date_key},
    )
    if reservation.status == "cached" and reservation.result:
        return QuestionSetResponse.model_validate(reservation.result)
    try:
        response = await _create_daily_pool(
            request,
            user,
            payload,
            adjustment=DifficultyAdjustment.SAME,
            source="free",
            date_key=date_key,
            set_id=free_set_id,
        )
        await request.app.state.state_store.complete_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            result=response.model_dump(by_alias=True, mode="json"),
            ttl_hours=request.app.state.request_result_ttl_hours,
        )
        return response
    except Exception:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise


@router.post("/v1/question-sets/review", response_model=QuestionSetResponse)
async def create_review_set(
    payload: PracticeSetRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> QuestionSetResponse:
    """취약점 복습 세트(Pro 전용): 지정 루브릭 차원에 편향된 연습 세트를 즉시 생성.

    기존 daily 생성 경로를 재사용(mode=daily)하므로 응답 문항은 일반 연습 평가
    (/v2/evaluations/practice)로 그대로 채점된다. 결제 유도는 402로 반환.
    """
    plan = await _current_plan(request, user.uid)
    if not plans.limits_for(plan).review_set:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "review_set_requires_pro",
                "message": "취약점 복습 세트는 프로 플랜 전용입니다.",
            },
        )
    if payload.focus_dimension is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "focus_dimension_required"},
        )
    operation_id = _request_id(idempotency_key)
    operation = "review_set_generation"
    date_key = _date_key()
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload=payload.model_dump(by_alias=True, mode="json"),
    )
    if reservation.status == "cached" and reservation.result:
        return QuestionSetResponse.model_validate(reservation.result)
    set_id = hashlib.sha256(
        f"{user.uid}:review:{operation_id}".encode()
    ).hexdigest()
    try:
        response = await _create_daily_pool(
            request,
            user,
            payload,
            adjustment=DifficultyAdjustment.SAME,
            source="review",
            date_key=date_key,
            set_id=set_id,
            focus_dimension=str(payload.focus_dimension),
        )
        await request.app.state.state_store.complete_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            result=response.model_dump(by_alias=True, mode="json"),
            ttl_hours=request.app.state.request_result_ttl_hours,
        )
        return response
    except Exception:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise


@router.post("/v1/question-sets/practice/refresh", response_model=QuestionSetResponse)
async def refresh_practice_set(
    payload: PracticeRefreshRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> QuestionSetResponse:
    operation_id = _request_id(idempotency_key)
    operation = "daily_refresh_generation"
    date_key = _date_key()
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload=payload.model_dump(by_alias=True, mode="json"),
    )
    if reservation.status == "cached" and reservation.result:
        return QuestionSetResponse.model_validate(reservation.result)
    request_id = hashlib.sha256(
        f"{user.uid}:{operation}:{operation_id}".encode()
    ).hexdigest()
    try:
        await request.app.state.state_store.reserve_mock(
            user.uid,
            request_id,
            payload.reward_nonce,
            None,
            RewardPurpose.PRACTICE_REFRESH,
        )
    except RewardNotVerified as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "practice_refresh_reward_required", "message": str(error)},
        ) from error
    except RequestAlreadyProcessing as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "request_processing", "operationId": operation_id},
            headers={"Retry-After": "2"},
        ) from error
    try:
        response = await _create_daily_pool(
            request,
            user,
            payload,
            adjustment=payload.adjustment,
            source="token",
            date_key=date_key,
        )
        await request.app.state.state_store.finalize_request(
            request_id,
            {"setId": response.set_id, "setHash": response.set_hash},
            request.app.state.request_result_ttl_hours,
        )
        await request.app.state.state_store.complete_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            result=response.model_dump(by_alias=True, mode="json"),
            ttl_hours=request.app.state.request_result_ttl_hours,
        )
        return response
    except Exception:
        await request.app.state.state_store.fail_request(request_id)
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise


@router.post("/v1/mock-exams", response_model=QuestionSetResponse)
async def create_mock_exam(
    payload: MockExamRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> QuestionSetResponse:
    date_key = _date_key()
    set_id = hashlib.sha256(f"{user.uid}:mock:{date_key}".encode()).hexdigest()
    existing = await request.app.state.state_store.get_question_set(
        uid=user.uid,
        set_id=set_id,
        mode="mock",
    )
    if existing:
        return _question_set_response_from_record(
            existing,
            model_version=request.app.state.ai_service.model,
        )
    operation = "mock_daily_generation"
    operation_id = f"mock-{date_key}"
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload={"date": date_key},
    )
    if reservation.status == "cached" and reservation.result:
        return QuestionSetResponse.model_validate(reservation.result)
    try:
        response = await _create_question_set(
            request,
            user,
            payload,
            mode="mock",
            set_id=set_id,
            date_key=date_key,
        )
        await request.app.state.state_store.complete_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            result=response.model_dump(by_alias=True, mode="json"),
            ttl_hours=request.app.state.request_result_ttl_hours,
        )
        return response
    except Exception:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise


@router.post("/v1/mock-exams/sessions", response_model=MockSessionResponse)
async def create_mock_session(
    payload: MockExamRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MockSessionResponse:
    if not request.app.state.settings.mock_session_v2_enabled:
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    operation_id = _request_id(idempotency_key)
    date_key = _date_key()
    initial_level = _request_initial_level(payload)
    reservation = await _reserve_operation(
        request,
        user,
        operation="mock_session_create",
        operation_id=operation_id,
        payload={"date": date_key, "initialLevel": initial_level},
    )
    if reservation.status == "cached" and reservation.result:
        return MockSessionResponse.model_validate(reservation.result)
    session_id = hashlib.sha256(f"{user.uid}:mock-session:{date_key}".encode()).hexdigest()
    session_hash = hashlib.sha256(f"{session_id}:reward-gates".encode()).hexdigest()
    record = await request.app.state.state_store.create_or_get_mock_session(
        uid=user.uid,
        session_id=session_id,
        session_hash=session_hash,
        date_key=date_key,
        initial_level=initial_level,
        background=payload.background.model_dump(mode="json"),
        survey=payload.survey.model_dump(mode="json") if payload.survey else None,
        resets_at=_next_reset(),
    )
    response = await _mock_session_response(request, user, record)
    await request.app.state.state_store.complete_operation(
        uid=user.uid,
        operation="mock_session_create",
        operation_id=operation_id,
        result=response.model_dump(by_alias=True, mode="json"),
        ttl_hours=request.app.state.request_result_ttl_hours,
    )
    return response


@router.get("/v1/mock-exams/current", response_model=MockSessionResponse)
async def current_mock_session(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> MockSessionResponse:
    record = await request.app.state.state_store.get_mock_session(
        uid=user.uid,
        date_key=_date_key(),
    )
    if not record:
        raise HTTPException(status_code=404, detail={"code": "mock_session_not_found"})
    return await _mock_session_response(request, user, record)


@router.post("/v1/mock-exams/{session_id}/start", response_model=MockSessionResponse)
async def start_mock_session(
    session_id: str,
    payload: MockSessionRewardRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MockSessionResponse:
    operation_id = _request_id(idempotency_key)
    record = await request.app.state.state_store.get_mock_session(
        uid=user.uid,
        session_id=session_id,
    )
    if not record:
        raise HTTPException(status_code=404, detail={"code": "mock_session_not_found"})
    if record.get("date") != _date_key():
        raise HTTPException(status_code=409, detail={"code": "mock_session_expired"})
    if record.get("stage") not in {
        MockSessionStage.AWAITING_START_AD.value,
        MockSessionStage.GENERATING_FRONT.value,
    }:
        return await _mock_session_response(request, user, record)
    operation = "mock_session_start"
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload={"sessionId": session_id, "rewardNonce": payload.reward_nonce},
    )
    if reservation.status == "cached" and reservation.result:
        return MockSessionResponse.model_validate(reservation.result)
    try:
        record = await request.app.state.state_store.transition_mock_session(
            uid=user.uid,
            session_id=session_id,
            expected_stages={MockSessionStage.AWAITING_START_AD.value},
            stage=MockSessionStage.GENERATING_FRONT.value,
        )
    except InvalidSessionTransition:
        current = await request.app.state.state_store.get_mock_session(
            uid=user.uid, session_id=session_id
        )
        if current and current.get("stage") not in {
            MockSessionStage.AWAITING_START_AD.value,
            MockSessionStage.GENERATING_FRONT.value,
        }:
            response = await _mock_session_response(request, user, current)
            await request.app.state.state_store.complete_operation(
                uid=user.uid,
                operation=operation,
                operation_id=operation_id,
                result=response.model_dump(by_alias=True, mode="json"),
                ttl_hours=request.app.state.request_result_ttl_hours,
            )
            return response
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "mock_session_processing", "operationId": operation_id},
            headers={"Retry-After": "2"},
        )
    reward_request_id = hashlib.sha256(
        f"{user.uid}:{session_id}:start".encode()
    ).hexdigest()
    try:
        await request.app.state.state_store.reserve_mock(
            user.uid,
            reward_request_id,
            payload.reward_nonce,
            str(record["sessionHash"]),
            RewardPurpose.MOCK_START,
        )
        mock_payload = MockExamRequest(
            initialLevel=int(record["initialLevel"]),
            background=BackgroundProfile.model_validate(record.get("background") or {}),
            survey=(
                BackgroundSurvey.model_validate(record["survey"])
                if record.get("survey")
                else None
            ),
        )
        set_id = hashlib.sha256(f"{session_id}:questions".encode()).hexdigest()
        question_set = await _create_question_set(
            request,
            user,
            mock_payload,
            mode="mock",
            set_id=set_id,
            date_key=str(record["date"]),
        )
        record = await request.app.state.state_store.transition_mock_session(
            uid=user.uid,
            session_id=session_id,
            expected_stages={MockSessionStage.GENERATING_FRONT.value},
            stage=MockSessionStage.ANSWERING_FRONT.value,
            updates={
                "setId": question_set.set_id,
                "setHash": question_set.set_hash,
                "startRewardNonce": payload.reward_nonce,
            },
        )
        response = await _mock_session_response(request, user, record)
        await request.app.state.state_store.finalize_request(
            reward_request_id,
            {"sessionId": session_id, "stage": record["stage"]},
            request.app.state.request_result_ttl_hours,
        )
        await request.app.state.state_store.complete_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            result=response.model_dump(by_alias=True, mode="json"),
            ttl_hours=request.app.state.request_result_ttl_hours,
        )
        return response
    except RewardNotVerified as error:
        await request.app.state.state_store.transition_mock_session(
            uid=user.uid,
            session_id=session_id,
            expected_stages={MockSessionStage.GENERATING_FRONT.value},
            stage=MockSessionStage.AWAITING_START_AD.value,
        )
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=402,
            detail={"code": "mock_start_reward_required", "message": str(error)},
        ) from error
    except Exception:
        await request.app.state.state_store.fail_request(reward_request_id)
        try:
            await request.app.state.state_store.transition_mock_session(
                uid=user.uid,
                session_id=session_id,
                expected_stages={MockSessionStage.GENERATING_FRONT.value},
                stage=MockSessionStage.AWAITING_START_AD.value,
            )
        except (KeyError, InvalidSessionTransition):
            pass
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise


@router.post(
    "/v1/mock-exams/{session_id}/adjustment",
    response_model=MockSessionResponse,
)
async def adjust_mock_session(
    session_id: str,
    payload: MockSessionAdjustmentRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MockSessionResponse:
    operation_id = _request_id(idempotency_key)
    record = await request.app.state.state_store.get_mock_session(
        uid=user.uid, session_id=session_id
    )
    if not record:
        raise HTTPException(status_code=404, detail={"code": "mock_session_not_found"})
    if record.get("stage") in {
        MockSessionStage.ANSWERING_TAIL.value,
        MockSessionStage.AWAITING_RESULT_AD.value,
        MockSessionStage.EVALUATING.value,
        MockSessionStage.COMPLETED.value,
    }:
        if record.get("adjustment") != payload.adjustment.value:
            raise HTTPException(
                status_code=409,
                detail={"code": "adjustment_already_applied"},
            )
        return await _mock_session_response(request, user, record)
    operation = "mock_session_adjustment"
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload=payload.model_dump(by_alias=True, mode="json"),
    )
    if reservation.status == "cached" and reservation.result:
        return MockSessionResponse.model_validate(reservation.result)
    try:
        record = await request.app.state.state_store.transition_mock_session(
            uid=user.uid,
            session_id=session_id,
            expected_stages={
                MockSessionStage.ANSWERING_FRONT.value,
                MockSessionStage.AWAITING_ADJUSTMENT_AD.value,
            },
            stage=MockSessionStage.GENERATING_TAIL.value,
        )
    except InvalidSessionTransition as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_mock_session_stage", "message": str(error)},
        ) from error
    reward_request_id = hashlib.sha256(
        f"{user.uid}:{session_id}:adjustment".encode()
    ).hexdigest()
    try:
        await request.app.state.state_store.reserve_mock(
            user.uid,
            reward_request_id,
            payload.reward_nonce,
            str(record["sessionHash"]),
            RewardPurpose.MOCK_ADJUSTMENT,
        )
        await apply_question_set_adjustment(
            str(record["setId"]),
            QuestionSetAdjustmentRequest(adjustment=payload.adjustment),
            request,
            user,
            f"session-adjust-{session_id}",
        )
        stored_set = await request.app.state.state_store.get_question_set(
            uid=user.uid,
            set_id=str(record["setId"]),
            mode="mock",
        )
        if not stored_set:
            raise KeyError("mock question set not found")
        record = await request.app.state.state_store.transition_mock_session(
            uid=user.uid,
            session_id=session_id,
            expected_stages={MockSessionStage.GENERATING_TAIL.value},
            stage=MockSessionStage.ANSWERING_TAIL.value,
            updates={
                "setHash": stored_set["questionHash"],
                "adjustment": payload.adjustment.value,
                "adjustmentRewardNonce": payload.reward_nonce,
            },
        )
        response = await _mock_session_response(request, user, record)
        await request.app.state.state_store.finalize_request(
            reward_request_id,
            {"sessionId": session_id, "stage": record["stage"]},
            request.app.state.request_result_ttl_hours,
        )
        await request.app.state.state_store.complete_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            result=response.model_dump(by_alias=True, mode="json"),
            ttl_hours=request.app.state.request_result_ttl_hours,
        )
        return response
    except RewardNotVerified as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=402,
            detail={"code": "mock_adjustment_reward_required", "message": str(error)},
        ) from error
    except Exception:
        await request.app.state.state_store.fail_request(reward_request_id)
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise
    finally:
        current = await request.app.state.state_store.get_mock_session(
            uid=user.uid, session_id=session_id
        )
        if current and current.get("stage") == MockSessionStage.GENERATING_TAIL.value:
            try:
                await request.app.state.state_store.transition_mock_session(
                    uid=user.uid,
                    session_id=session_id,
                    expected_stages={MockSessionStage.GENERATING_TAIL.value},
                    stage=MockSessionStage.AWAITING_ADJUSTMENT_AD.value,
                )
            except InvalidSessionTransition:
                pass


@router.post("/v1/question-sets/{set_id}/adjustment", response_model=QuestionSetResponse)
async def apply_question_set_adjustment(
    set_id: str,
    payload: QuestionSetAdjustmentRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> QuestionSetResponse:
    _request_id(idempotency_key)
    mode = "daily"
    record = await request.app.state.state_store.get_question_set(
        uid=user.uid, set_id=set_id, mode=mode
    )
    if record is None:
        mode = "mock"
        record = await request.app.state.state_store.get_question_set(
            uid=user.uid, set_id=set_id, mode=mode
        )
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "question_set_not_found"})
    current_status = str(record.get("status") or "complete")
    current_adjustment = record.get("adjustment")
    if current_status == QuestionSetStatus.COMPLETE.value:
        if current_adjustment == payload.adjustment.value:
            return _question_set_response_from_record(
                record,
                model_version=request.app.state.ai_service.model,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "question_set_complete",
                "message": "This question set is complete and cannot be adjusted again.",
            },
        )

    operation = "question_set_adjustment"
    operation_id = f"adjust-{set_id}"
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload={"setId": set_id, "adjustment": payload.adjustment.value},
    )
    if reservation.status == "cached" and reservation.result:
        return QuestionSetResponse.model_validate(reservation.result)

    initial_level = int(record["initialLevel"])
    effective_level = adjusted_level(initial_level, payload.adjustment)
    code = effective_level_code(initial_level, payload.adjustment)
    expected_level = expected_target_level(effective_level)
    background = BackgroundProfile.model_validate(record.get("background") or {})
    survey = (
        BackgroundSurvey.model_validate(record["survey"])
        if record.get("survey")
        else None
    )
    history = await request.app.state.state_store.get_question_history(
        uid=user.uid,
        mode=mode,
    )
    uid_hash = _uid_hash(user.uid)
    logger.info(
        "question generation requested mode=%s stage=tail uidHash=%s initialLevel=%s "
        "adjustment=%s effectiveLevelCode=%s expectedTargetLevel=%s mockAI=%s model=%s",
        mode,
        uid_hash,
        initial_level,
        payload.adjustment.value,
        code,
        expected_level.value,
        request.app.state.settings.mock_ai,
        request.app.state.ai_service.model,
    )
    try:
        if mode == "mock":
            generation = await request.app.state.ai_service.generate_mock(
                initial_level,
                background,
                survey,
                stage="tail",
                adjustment=payload.adjustment,
                effective_level=effective_level,
                history=history,
            )
        else:
            generation = await request.app.state.ai_service.generate_practice(
                initial_level,
                background,
                stage="tail",
                adjustment=payload.adjustment,
                effective_level=effective_level,
                history=history,
            )
    except AIQuestionGenerationError as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        logger.exception(
            "question generation failed mode=%s stage=tail uidHash=%s initialLevel=%s "
            "adjustment=%s model=%s",
            mode,
            uid_hash,
            initial_level,
            payload.adjustment.value,
            request.app.state.ai_service.model,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ai_question_generation_failed",
                "message": "AI 질문 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
            },
        ) from error

    existing_questions = QUESTION_LIST.validate_python(record["questions"])
    questions = [*existing_questions, *generation.questions]
    serialized = [item.model_dump(by_alias=True, mode="json") for item in questions]
    set_hash = question_set_hash(serialized)
    generation_metadata = _generation_metadata(
        generation,
        model_version=request.app.state.ai_service.model,
    )
    if record.get("fallbackUsed"):
        generation_metadata["fallbackUsed"] = True
        generation_metadata["provider"] = (
            "catalog" if generation.provider == "catalog" else "mixed"
        )
        generation_metadata["fallbackQuestionNumbers"] = sorted(
            {
                *[int(value) for value in record.get("fallbackQuestionNumbers", [])],
                *generation.fallback_question_numbers,
            }
        )
    try:
        stored_record = await request.app.state.state_store.apply_question_set_adjustment(
            uid=user.uid,
            set_id=set_id,
            mode=mode,
            adjustment=payload.adjustment.value,
            effective_level=effective_level,
            target_level=expected_level.value,
            question_hash=set_hash,
            questions=serialized,
            generation_metadata=generation_metadata,
        )
    except AdjustmentAlreadyApplied as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "adjustment_already_applied", "message": str(error)},
        ) from error
    except KeyError as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=404,
            detail={"code": "question_set_not_found"},
        ) from error
    canonical_questions = QUESTION_LIST.validate_python(stored_record["questions"])
    canonical_serialized = [
        item.model_dump(by_alias=True, mode="json") for item in canonical_questions
    ]
    canonical_set_hash = str(stored_record["questionHash"])
    await request.app.state.state_store.record_question_history(
        uid=user.uid,
        mode=mode,
        set_hash=canonical_set_hash,
        questions=canonical_serialized,
    )
    prompt_hashes = [prompt_hash(str(item.get("prompt") or ""))[:16] for item in serialized]
    usage = generation.usage
    logger.info(
        "question generation succeeded mode=%s stage=tail uidHash=%s initialLevel=%s "
        "adjustment=%s effectiveLevelCode=%s expectedTargetLevel=%s provider=%s "
        "model=%s openaiResponseId=%s fallbackUsed=%s setHash=%s "
        "promptHashes=%s inputTokens=%s cachedInputTokens=%s outputTokens=%s "
        "reasoningTokens=%s totalTokens=%s",
        mode,
        uid_hash,
        initial_level,
        payload.adjustment.value,
        code,
        expected_level.value,
        generation.provider,
        request.app.state.ai_service.model,
        generation.openai_response_id,
        generation.fallback_used,
        set_hash,
        prompt_hashes,
        usage.input_tokens if usage else None,
        usage.cached_input_tokens if usage else None,
        usage.output_tokens if usage else None,
        usage.reasoning_tokens if usage else None,
        usage.total_tokens if usage else None,
    )
    response = _question_set_response_from_record(
        stored_record,
        model_version=request.app.state.ai_service.model,
    )
    await request.app.state.state_store.complete_operation(
        uid=user.uid,
        operation=operation,
        operation_id=operation_id,
        result=response.model_dump(by_alias=True, mode="json"),
        ttl_hours=request.app.state.request_result_ttl_hours,
    )
    return response


@router.get("/v1/usage", response_model=UsageResponse)
async def usage(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> UsageResponse:
    date_key = _date_key()
    plan = await _current_plan(request, user.uid)
    limits = plans.limits_for(plan)
    value = await request.app.state.state_store.get_usage(user.uid, date_key)
    free_remaining = max(
        0, limits.practice_daily - int(value.get("freeUsed", 0))
    )
    bonus_remaining = max(0, int(value.get("bonusRemaining", 0)))
    refresh_max = plans.reward_max_for(plan, RewardPurpose.PRACTICE_REFRESH)
    resets_at = _next_reset()
    mock_session = await request.app.state.state_store.get_mock_session(
        uid=user.uid,
        date_key=date_key,
    )
    return UsageResponse(
        date=date_key,
        freeRemaining=free_remaining,
        bonusRemaining=bonus_remaining,
        serverDateKey=date_key,
        resetsAt=resets_at,
        dailyAnalysisFreeRemaining=free_remaining,
        dailyAnalysisRewardRemaining=bonus_remaining,
        dailyRefreshRemaining=max(
            0,
            refresh_max - int(value.get("practiceRefreshRewardCount", 0)),
        ),
        mockAvailable=mock_session is None,
        mockSessionStage=(str(mock_session["stage"]) if mock_session else None),
    )


@router.post("/v1/ad-rewards/intents", response_model=RewardIntentResponse)
async def create_reward_intent(
    payload: RewardIntentRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> RewardIntentResponse:
    settings = request.app.state.settings
    plan = await _current_plan(request, user.uid)
    # 유료 플랜은 모의고사 광고 게이트를 광고 없이 즉시 충족(auto-verify).
    auto_verify = plans.reward_auto_verify(plan, payload.purpose)
    max_daily_reward_count = plans.reward_max_for(plan, payload.purpose)
    if max_daily_reward_count <= 0:
        # 유료 플랜은 데일리/리프레시 광고 보너스를 사용하지 않음 → 결제 유도.
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "reward_not_available_for_plan",
                "message": "This reward is not available on your current plan.",
            },
        )
    nonce = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    try:
        reward = await request.app.state.state_store.create_reward_intent(
            nonce=nonce,
            uid=user.uid,
            purpose=payload.purpose,
            session_hash=payload.session_hash,
            date_key=_date_key(),
            expires_at=expires_at,
            auto_verify=auto_verify,
            practice_credit_amount=settings.reward_practice_credits,
            max_daily_reward_count=max_daily_reward_count,
        )
    except UsageLimitExceeded as error:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "reward_quota_exhausted", "message": str(error)},
        ) from error
    return RewardIntentResponse(
        nonce=nonce,
        purpose=payload.purpose,
        status=str(reward["status"]),
        userIdentifier=user.uid,
        customData=nonce,
        expiresAt=expires_at,
    )


_DEACTIVATING_EVENTS = {
    "CANCELLATION",
    "EXPIRATION",
    "SUBSCRIPTION_PAUSED",
    "REFUND",
    "BILLING_ISSUE",
}


@router.post("/v1/iap/revenuecat-webhook")
async def revenuecat_webhook(
    payload: RevenueCatWebhook,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    """RevenueCat 서버-서버 웹훅. App Check/Firebase Auth 대신 공유 시크릿으로 검증.

    엔타이틀먼트를 Firestore userProfiles 에 반영(서버 권위). 이벤트는 멱등 처리.
    """
    settings = request.app.state.settings
    expected = settings.revenuecat_webhook_auth
    if not expected:
        logger.error("RevenueCat webhook received but shared secret is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "webhook_not_configured"},
        )
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_webhook_auth"},
        )

    event = payload.event
    uid = (event.app_user_id or event.original_app_user_id or "").strip()
    if not uid:
        raise HTTPException(
            status_code=422, detail={"code": "missing_app_user_id"}
        )

    event_id = event.id or _payload_hash(payload.model_dump(mode="json"))
    is_new = await request.app.state.state_store.record_iap_event(event_id, uid)
    if not is_new:
        return {"status": "duplicate"}

    event_type = event.type.upper()
    if event_type in _DEACTIVATING_EVENTS:
        plan = Plan.FREE
        is_active = False
    else:
        entitlement_ids = event.entitlement_ids or (
            [event.entitlement_id] if event.entitlement_id else None
        )
        plan = plans.plan_from_entitlement_ids(entitlement_ids)
        if plan is Plan.FREE:
            logger.warning(
                "RevenueCat webhook: unmapped entitlements uid=%s ids=%s product=%s",
                _uid_hash(uid),
                entitlement_ids,
                event.product_id,
            )
        is_active = plan is not Plan.FREE

    entitlement = {
        "plan": str(plan),
        "isActive": is_active,
        "source": "revenuecat",
        "productId": event.product_id,
        "periodType": event.period_type,
        "expiresAt": event.expiration_at_ms,
        "store": event.store,
        "lastEventType": event_type,
        "updatedAt": datetime.now(UTC),
    }
    await request.app.state.state_store.set_entitlement(uid, entitlement=entitlement)
    logger.info(
        "RevenueCat webhook processed uid=%s type=%s plan=%s",
        _uid_hash(uid),
        event_type,
        plan,
    )
    return {"status": "ok", "plan": str(plan)}


@router.get("/v1/ad-rewards/{nonce}", response_model=RewardIntentResponse)
async def reward_status(
    nonce: str,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> RewardIntentResponse:
    reward = await request.app.state.state_store.get_reward_intent(nonce, user.uid)
    if not reward:
        raise HTTPException(status_code=404, detail={"code": "reward_not_found"})
    return _reward_response(reward, user.uid)


@router.get("/v1/admob/ssv", response_class=PlainTextResponse)
async def admob_ssv(request: Request) -> PlainTextResponse:
    query_keys = sorted(request.query_params.keys())
    client_host = request.client.host if request.client else "unknown"
    if not request.query_params.get("custom_data"):
        logger.info(
            "[SSV] AdMob URL verification request detected. "
            "No custom_data present. client=%s keys=%s",
            client_host,
            query_keys,
        )
        return PlainTextResponse("OK")

    logger.info("[SSV] callback received client=%s keys=%s", client_host, query_keys)
    try:
        verified = await request.app.state.ssv_verifier.verify(request.url.query)
        logger.info(
            "[SSV] nonce=%s transactionId=%s user=%s adUnit=%s",
            verified.nonce,
            verified.transaction_id,
            verified.user_id,
            verified.ad_unit,
        )
        if not verified.user_id:
            logger.warning("[SSV] missing parameter name=user_id nonce=%s", verified.nonce)
            raise RewardNotVerified("SSV user_id is required")
        reward = await request.app.state.state_store.get_reward_intent(
            verified.nonce, verified.user_id
        )
        if not reward:
            logger.warning(
                "[SSV] nonce not found nonce=%s user=%s",
                verified.nonce,
                verified.user_id,
            )
            raise RewardNotVerified("SSV user_id does not match the reward intent")
        reward_plan = await _current_plan(request, verified.user_id)
        reward_purpose = RewardPurpose(reward.get("purpose"))
        await request.app.state.state_store.verify_reward(
            nonce=verified.nonce,
            transaction_id=verified.transaction_id,
            practice_credit_amount=request.app.state.settings.reward_practice_credits,
            max_daily_reward_count=plans.reward_max_for(reward_plan, reward_purpose),
        )
        logger.info("[SSV] reward verified nonce=%s", verified.nonce)
        logger.info("[SSV] reward completed nonce=%s", verified.nonce)
        logger.info(
            "admob ssv verified nonce=%s transactionId=%s user=%s purpose=%s",
            verified.nonce,
            verified.transaction_id,
            verified.user_id,
            reward.get("purpose"),
        )
    except (SSVVerificationError, RewardNotVerified, UsageLimitExceeded) as error:
        error_text = str(error)
        if "required SSV parameters are missing" in error_text:
            logger.warning("[SSV] missing parameter client=%s keys=%s", client_host, query_keys)
        elif "signature" in error_text:
            logger.warning("[SSV] invalid signature client=%s keys=%s", client_host, query_keys)
        elif "does not match" in error_text or "missing or expired" in error_text:
            logger.warning("[SSV] nonce not found client=%s keys=%s", client_host, query_keys)
        logger.warning(
            "admob ssv verification failed client=%s keys=%s error=%s",
            client_host,
            query_keys,
            error,
        )
        raise HTTPException(status_code=400, detail=str(error)) from error
    return PlainTextResponse("OK")


@router.post("/v1/evaluations/practice", response_model=PracticeEvaluation)
@router.post("/v2/evaluations/practice", response_model=PracticeEvaluation)
async def evaluate_practice(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    set_id: Annotated[str, Form(alias="setId")],
    question_number: Annotated[int, Form(alias="questionNumber")],
    transcript: Annotated[str, Form(min_length=1, max_length=12_000)],
    target_level: Annotated[str | None, Form(alias="targetLevel")] = None,
    audio: Annotated[UploadFile | None, File()] = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PracticeEvaluation:
    request_id = _request_id(idempotency_key)
    try:
        question_set = await request.app.state.state_store.get_question_set(
            uid=user.uid, set_id=set_id, mode="daily"
        )
        if not question_set:
            raise ValueError("question set not found")
        target = request.app.state.level_adapter.validate_python(
            question_set.get("targetLevel")
        )
        questions = QUESTION_LIST.validate_python(question_set["questions"])
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_set", "message": str(error)},
        ) from error
    question = next((item for item in questions if item.number == question_number), None)
    if question is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_question_number"})

    plan = await _current_plan(request, user.uid)
    limits = plans.limits_for(plan)
    try:
        reservation = await request.app.state.state_store.reserve_practice(
            user.uid, _date_key(), request_id, limits.practice_daily
        )
    except UsageLimitExceeded as error:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "practice_quota_exhausted", "message": str(error)},
        ) from error
    except RequestAlreadyProcessing as error:
        raise HTTPException(status_code=409, detail={"code": "request_processing"}) from error

    if reservation.status == "cached" and reservation.result:
        return PracticeEvaluation.model_validate(reservation.result)

    try:
        metrics = await request.app.state.audio_service.analyze(audio, transcript)
        result = await request.app.state.ai_service.evaluate_practice(
            question=question,
            transcript=transcript.strip(),
            target=target,
            metrics=metrics,
            depth=limits.analysis_depth,
        )
        serialized_result = result.model_dump(by_alias=True, mode="json")
        await request.app.state.state_store.finalize_request(
            request_id, serialized_result, request.app.state.request_result_ttl_hours
        )
        return result
    except AudioValidationError as error:
        await request.app.state.state_store.fail_request(request_id)
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_audio", "message": str(error)},
        ) from error
    except AIServiceError as error:
        await request.app.state.state_store.fail_request(request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ai_unavailable",
                "message": "AI feedback is temporarily unavailable. Please try again.",
            },
        ) from error
    except Exception:
        await request.app.state.state_store.fail_request(request_id)
        raise


def _audio_number(upload: UploadFile) -> int | None:
    match = re.search(r"(?:answer[-_])?(\d{1,2})", upload.filename or "")
    return int(match.group(1)) if match else None


def _validate_mock_audio_files(audio_files: list[UploadFile]) -> list[int]:
    if len(audio_files) != 15:
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_audio", "message": "All 15 audio files are required."},
        )
    audio_numbers = [_audio_number(item) for item in audio_files]
    if sorted(number for number in audio_numbers if number is not None) != list(
        range(1, 16)
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_audio_manifest",
                "message": "Audio files must contain each answer number 1 through 15 exactly once.",
            },
        )
    aggregate_size = sum(int(item.size or 0) for item in audio_files)
    if aggregate_size > MOCK_AUDIO_AGGREGATE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "audio_payload_too_large",
                "message": "Combined mock audio exceeds the 30 MB limit.",
            },
        )
    return [int(number) for number in audio_numbers]


@router.post("/v1/mock-exams/{session_id}/evaluate", response_model=MockEvaluation)
async def evaluate_mock_session(
    session_id: str,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    manifest_json: Annotated[str, Form(alias="manifest")],
    audio_files: Annotated[list[UploadFile], File(alias="audioFiles")] = [],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MockEvaluation:
    operation_id = _request_id(idempotency_key)
    try:
        manifest = MockEvaluationManifest.model_validate_json(manifest_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_manifest", "message": str(error)},
        ) from error
    session = await request.app.state.state_store.get_mock_session(
        uid=user.uid,
        session_id=session_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail={"code": "mock_session_not_found"})
    if manifest.set_id != session.get("setId"):
        raise HTTPException(status_code=409, detail={"code": "mock_session_set_mismatch"})
    audio_numbers = _validate_mock_audio_files(audio_files)
    operation = "mock_session_evaluation"
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload={
            "sessionId": session_id,
            "setId": manifest.set_id,
            "rewardNonce": manifest.reward_nonce,
            "answers": [item.model_dump(mode="json") for item in manifest.answers],
        },
    )
    if reservation.status == "cached" and reservation.result:
        return MockEvaluation.model_validate(reservation.result)
    if session.get("stage") not in {
        MockSessionStage.ANSWERING_TAIL.value,
        MockSessionStage.AWAITING_RESULT_AD.value,
    }:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_mock_session_stage", "stage": session.get("stage")},
        )
    try:
        question_set = await request.app.state.state_store.get_question_set(
            uid=user.uid,
            set_id=manifest.set_id,
            mode="mock",
        )
        if not question_set or question_set.get("status") != "complete":
            raise ValueError("complete question set not found")
        target = request.app.state.level_adapter.validate_python(
            question_set.get("targetLevel")
        )
        questions = QUESTION_LIST.validate_python(question_set["questions"])
    except (ValueError, ValidationError) as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_set", "message": str(error)},
        ) from error
    try:
        await request.app.state.state_store.transition_mock_session(
            uid=user.uid,
            session_id=session_id,
            expected_stages={
                MockSessionStage.ANSWERING_TAIL.value,
                MockSessionStage.AWAITING_RESULT_AD.value,
            },
            stage=MockSessionStage.EVALUATING.value,
        )
    except InvalidSessionTransition as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "mock_session_processing", "message": str(error)},
        ) from error
    reward_request_id = hashlib.sha256(
        f"{user.uid}:{session_id}:result".encode()
    ).hexdigest()
    try:
        await request.app.state.state_store.reserve_mock(
            user.uid,
            reward_request_id,
            manifest.reward_nonce,
            str(session["sessionHash"]),
            RewardPurpose.MOCK_RESULT,
        )
        files_by_number = {
            number: item for number, item in zip(audio_numbers, audio_files)
        }
        metrics = await asyncio.gather(
            *[
                request.app.state.audio_service.analyze(
                    files_by_number[answer.number], answer.transcript
                )
                for answer in manifest.answers
            ]
        )
        mock_plan = await _current_plan(request, user.uid)
        result = await request.app.state.ai_service.evaluate_mock(
            questions=questions,
            transcripts=[item.transcript for item in manifest.answers],
            target=target,
            metrics=list(metrics),
            depth=plans.limits_for(mock_plan).analysis_depth,
        )
        serialized_result = result.model_dump(by_alias=True, mode="json")
        await request.app.state.state_store.finalize_request(
            reward_request_id,
            serialized_result,
            request.app.state.request_result_ttl_hours,
        )
        await request.app.state.state_store.transition_mock_session(
            uid=user.uid,
            session_id=session_id,
            expected_stages={MockSessionStage.EVALUATING.value},
            stage=MockSessionStage.COMPLETED.value,
            updates={
                "resultRewardNonce": manifest.reward_nonce,
                "resultOperationId": operation_id,
                "completedAt": datetime.now(UTC),
            },
        )
        await request.app.state.state_store.complete_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            result=serialized_result,
            ttl_hours=request.app.state.request_result_ttl_hours,
        )
        return result
    except RewardNotVerified as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=402,
            detail={"code": "mock_result_reward_required", "message": str(error)},
        ) from error
    except AudioValidationError as error:
        await request.app.state.state_store.fail_request(reward_request_id)
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_audio", "message": str(error)},
        ) from error
    except AIServiceError as error:
        await request.app.state.state_store.fail_request(reward_request_id)
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ai_unavailable",
                "message": "AI feedback is temporarily unavailable. Please try again.",
                "operationId": operation_id,
                "retryable": True,
            },
        ) from error
    except Exception:
        await request.app.state.state_store.fail_request(reward_request_id)
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=True,
        )
        raise
    finally:
        current = await request.app.state.state_store.get_mock_session(
            uid=user.uid, session_id=session_id
        )
        if current and current.get("stage") == MockSessionStage.EVALUATING.value:
            try:
                await request.app.state.state_store.transition_mock_session(
                    uid=user.uid,
                    session_id=session_id,
                    expected_stages={MockSessionStage.EVALUATING.value},
                    stage=MockSessionStage.AWAITING_RESULT_AD.value,
                )
            except InvalidSessionTransition:
                pass


@router.post("/v1/evaluations/mock", response_model=MockEvaluation)
async def evaluate_mock(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    manifest_json: Annotated[str, Form(alias="manifest")],
    audio_files: Annotated[list[UploadFile], File(alias="audioFiles")] = [],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MockEvaluation:
    request_id = _request_id(idempotency_key)
    try:
        manifest = MockEvaluationManifest.model_validate_json(manifest_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_manifest", "message": str(error)},
        ) from error
    try:
        question_set = await request.app.state.state_store.get_question_set(
            uid=user.uid, set_id=manifest.set_id, mode="mock"
        )
        if not question_set:
            raise ValueError("question set not found")
        target = request.app.state.level_adapter.validate_python(
            question_set.get("targetLevel")
        )
        questions = QUESTION_LIST.validate_python(question_set["questions"])
        question_hash = str(question_set["questionHash"])
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_set", "message": str(error)},
        ) from error
    audio_numbers = _validate_mock_audio_files(audio_files)
    try:
        reservation = await request.app.state.state_store.reserve_mock(
            user.uid,
            request_id,
            manifest.reward_nonce,
            question_hash,
        )
    except RewardNotVerified as error:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "mock_reward_required", "message": str(error)},
        ) from error
    except RequestAlreadyProcessing as error:
        raise HTTPException(status_code=409, detail={"code": "request_processing"}) from error
    if reservation.status == "cached" and reservation.result:
        return MockEvaluation.model_validate(reservation.result)

    files_by_number = {
        int(number): item for number, item in zip(audio_numbers, audio_files)
    }
    try:
        metrics = await asyncio.gather(
            *[
                request.app.state.audio_service.analyze(
                    files_by_number.get(answer.number), answer.transcript
                )
                for answer in manifest.answers
            ]
        )
        mock_plan = await _current_plan(request, user.uid)
        result = await request.app.state.ai_service.evaluate_mock(
            questions=questions,
            transcripts=[item.transcript for item in manifest.answers],
            target=target,
            metrics=list(metrics),
            depth=plans.limits_for(mock_plan).analysis_depth,
        )
        serialized_result = result.model_dump(by_alias=True, mode="json")
        await request.app.state.state_store.finalize_request(
            request_id, serialized_result, request.app.state.request_result_ttl_hours
        )
        return result
    except AudioValidationError as error:
        await request.app.state.state_store.fail_request(request_id)
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_audio", "message": str(error)},
        ) from error
    except AIServiceError as error:
        await request.app.state.state_store.fail_request(request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ai_unavailable",
                "message": "AI feedback is temporarily unavailable. Please try again.",
            },
        ) from error
    except Exception:
        await request.app.state.state_store.fail_request(request_id)
        raise
