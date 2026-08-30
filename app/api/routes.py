from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import uuid
from collections import Counter
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
    OPIcLevel,
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
    StudyPlanRequest,
    StudyPlanResponse,
    StudyPlanSettings,
    TargetLevelRequest,
    TargetLevelResponse,
    UsageResponse,
    VocabularyEntry,
    VocabularyGeneratedSet,
    VocabularyGenerationPurpose,
    VocabularyGenerationRequest,
    VocabularyItemType,
    VocabularySpeakingCoachRequest,
    VocabularySpeakingCoachResult,
    VocabularyTopic,
)
from app.services.admob import SSVVerificationError
from app.services.ai import (
    AIQuestionGenerationError,
    AIVocabularyCoachError,
    AIVocabularyGenerationError,
    AIServiceError,
    QuestionGenerationResult,
    VocabularyGenerationResult,
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
from app.services import plans, vocabulary
from app.services.plans import Plan
from app.services.revenuecat import RevenueCatAPIError
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
# 학습 계획 설정 스키마 버전. 서버가 매긴다 — 클라이언트가 보내면 자기 문서의
# 해석 방식을 스스로 정하게 된다.
STUDY_PLAN_SCHEMA_VERSION = 1


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
        reviewSet=limits.review_set,
        adsEnabled=limits.ads_enabled,
        calendarEnabled=limits.calendar_enabled,
        calendarAutoReplan=limits.calendar_auto_replan,
        calendarEvaluationAdaptive=limits.calendar_evaluation_adaptive,
        calendarExamBackplan=limits.calendar_exam_backplan,
        calendarStudyReminder=limits.calendar_study_reminder,
        calendarEventReminder=limits.calendar_event_reminder,
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


def _daily_free_set_id(uid: str, date_key: str, level: int) -> str:
    # 난이도(level)를 키에 포함해, 목표 등급을 바꾸면 새 레벨의 세트가 생성되도록 한다.
    return hashlib.sha256(
        f"{uid}:practice:{date_key}:free:{level}".encode()
    ).hexdigest()


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


def _study_plan_settings(stored: dict[str, object]) -> StudyPlanSettings:
    return StudyPlanSettings.model_validate(stored)


@router.get("/v1/users/me/study-plan", response_model=StudyPlanResponse)
async def read_study_plan(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> StudyPlanResponse:
    """캘린더를 쓰지 않는 기존 사용자는 설정 문서가 없다. 그건 오류가 아니라
    `configured=false`다."""
    stored = await request.app.state.state_store.get_study_plan(user.uid)
    if not stored:
        return StudyPlanResponse(configured=False)
    try:
        settings = _study_plan_settings(stored)
    except ValidationError:
        # 스키마가 바뀌어 읽을 수 없는 문서는 지우지 않고 미설정으로 되돌린다.
        # 사용자는 다시 설정하면 되고, 원본은 조사할 수 있게 남는다.
        logger.warning("unreadable study plan uidHash=%s", _uid_hash(user.uid))
        return StudyPlanResponse(configured=False)
    return StudyPlanResponse(configured=True, studyPlan=settings)


@router.put("/v1/users/me/study-plan", response_model=StudyPlanResponse)
async def update_study_plan(
    payload: StudyPlanRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> StudyPlanResponse:
    """생성과 교체를 구분하지 않는다. 설정은 통째로 덮어쓰는 값 하나다."""
    stored = await request.app.state.state_store.set_study_plan(
        user.uid,
        study_plan={
            "schemaVersion": STUDY_PLAN_SCHEMA_VERSION,
            **payload.model_dump(by_alias=True, mode="json"),
        },
    )
    return StudyPlanResponse(configured=True, studyPlan=_study_plan_settings(stored))


@router.post("/v1/question-sets/practice", response_model=QuestionSetResponse)
async def create_practice_set(
    payload: PracticeSetRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> QuestionSetResponse:
    date_key = _date_key()
    initial_level = _request_initial_level(payload)
    # 난이도별 세트 id/operation → 목표 등급 변경 시 이전 문제 대신 새 레벨 문제 생성.
    free_set_id = _daily_free_set_id(user.uid, date_key, initial_level)
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
    operation_id = f"daily-{date_key}-{initial_level}"
    reservation = await _reserve_operation(
        request,
        user,
        operation=operation,
        operation_id=operation_id,
        payload={"date": date_key},
    )
    if reservation.status == "cached" and reservation.result:
        return QuestionSetResponse.model_validate(reservation.result)
    # 토큰 모델: 하루 첫 데일리 세트 생성 시 토큰 1개 소모(세트당 소모).
    plan = await _current_plan(request, user.uid)
    limits = plans.limits_for(plan)
    token_request_id = hashlib.sha256(
        f"{user.uid}:daily_token:{date_key}".encode()
    ).hexdigest()
    try:
        await request.app.state.state_store.reserve_practice(
            user.uid, date_key, token_request_id, limits.practice_daily
        )
    except UsageLimitExceeded as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "practice_quota_exhausted", "message": str(error)},
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
            adjustment=DifficultyAdjustment.SAME,
            source="free",
            date_key=date_key,
            set_id=free_set_id,
        )
        await request.app.state.state_store.finalize_request(
            token_request_id,
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
        await request.app.state.state_store.fail_request(token_request_id)
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
    # 토큰 모델(P13): 복습 세트도 mode=daily 새 문제 세트다. 새 세트 = 토큰 1개라는
    # 기존 규칙을 그대로 적용한다(프로 전용 게이트는 접근권이지 사용량 계량이 아니다).
    limits = plans.limits_for(plan)
    token_request_id = hashlib.sha256(
        f"{user.uid}:{operation}:{operation_id}".encode()
    ).hexdigest()
    try:
        await request.app.state.state_store.reserve_practice(
            user.uid, date_key, token_request_id, limits.practice_daily
        )
    except UsageLimitExceeded as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "practice_quota_exhausted", "message": str(error)},
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
            adjustment=DifficultyAdjustment.SAME,
            source="review",
            date_key=date_key,
            set_id=set_id,
            focus_dimension=str(payload.focus_dimension),
        )
        await request.app.state.state_store.finalize_request(
            token_request_id,
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
        await request.app.state.state_store.fail_request(token_request_id)
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
    # 토큰 모델: 새 문제 세트(리프레시)를 받을 때 토큰 1개 소모(광고/리워드 아님).
    plan = await _current_plan(request, user.uid)
    limits = plans.limits_for(plan)
    try:
        await request.app.state.state_store.reserve_practice(
            user.uid, date_key, request_id, limits.practice_daily
        )
    except UsageLimitExceeded as error:
        await request.app.state.state_store.fail_operation(
            uid=user.uid,
            operation=operation,
            operation_id=operation_id,
            retryable=False,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "practice_quota_exhausted", "message": str(error)},
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


def _vocabulary_set_response(
    *,
    set_id: str,
    topic: VocabularyTopic,
    target_level: OPIcLevel,
    purpose: VocabularyGenerationPurpose,
    generation: VocabularyGenerationResult,
) -> VocabularyGeneratedSet:
    """초안에 서버가 소유하는 값(id · 주제 · 권장 등급 · 출처)을 붙여 완성한다.

    항목 id는 `ai-` 접두사를 붙여 번들 시드 id와 절대 겹치지 않게 한다.
    구성(쓰임새가 정한 개수)은 여기서 마지막으로 한 번 더 확인한다 — 형식이 깨진
    제공자 출력을 저장하느니 실패로 처리한다. 남는 것을 잘라내 개수를 맞추지
    않는다: 계약이 20이면 제공자 단계에서 이미 20이어야 한다.
    """
    entries = [
        VocabularyEntry(
            id=f"ai-{set_id}-{index:02d}",
            term=draft.term.strip(),
            type=draft.type,
            meaningKo=draft.meaning_ko.strip(),
            exampleEn=draft.example_en.strip(),
            exampleKo=draft.example_ko.strip(),
            collocations=[item.strip() for item in draft.collocations if item.strip()],
            topics=[topic],
            usageRoles=draft.usage_roles,
            recommendedLevels=[target_level],
            source="ai",
        )
        for index, draft in enumerate(generation.drafts)
    ]
    counts = Counter(entry.type for entry in entries)
    unique_terms = {entry.term.strip().lower() for entry in entries}
    expected = vocabulary.composition(purpose)
    size = vocabulary.set_size(purpose)
    if (
        len(entries) != size
        or len(unique_terms) != size
        or any(counts[item] != expected[item] for item in VocabularyItemType)
    ):
        raise AIVocabularyGenerationError(
            f"generated set failed composition validation: {dict(counts)}"
        )
    return VocabularyGeneratedSet(
        setId=set_id,
        topic=topic,
        targetLevel=target_level,
        createdAt=datetime.now(UTC),
        entries=entries,
        source="ai",
    )


@router.post("/v1/vocabulary/generate", response_model=VocabularyGeneratedSet)
async def generate_vocabulary_set(
    payload: VocabularyGenerationRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> VocabularyGeneratedSet:
    """AI 단어 1세트. 개수 · 구성은 요청의 쓰임새(`purpose`)로 서버가 고른다:
    없거나 `custom_set`이면 예전 그대로 30개(10/10/10), `today_extra`면 20개(7/7/6).

    토큰 모델(P13 그대로): 사용자 조작 1회 = 데일리 토큰 1개. 새 지갑도, 단어장
    전용 화폐도 만들지 않는다 — 세트 생성 · AI 분석과 같은 `reserve_practice`다.

    과금 단위는 HTTP 요청이 아니라 Idempotency-Key가 가리키는 "조작 1회"다.
      - 같은 키 재전송(앱 재시도 · 응답 유실) → 저장된 결과를 그대로 돌려주고 추가 차감 없음
      - 제공자 실패로 쓸 만한 결과가 없음 → fail_request가 정확히 한 번 환불
      - 사용자가 새 세트를 또 만들면 앱이 새 키를 보내고 그때 새로 1개 나간다
    개수 · 구성 · 모델 · 내부 보충 횟수는 서버가 정한다. 어느 쓰임새든 값은
    데일리 토큰 1개로 같다 — 개수가 다르다고 과금이 달라지지 않는다.
    """
    request_id = _request_id(idempotency_key)
    token_request_id = hashlib.sha256(
        f"{user.uid}:vocabulary_generation:{request_id}".encode()
    ).hexdigest()
    plan = await _current_plan(request, user.uid)
    limits = plans.limits_for(plan)
    date_key = _date_key()
    # 토큰 확보가 제공자 호출보다 **먼저**다. 잔액이 0이면 AI를 부르지 않는다.
    try:
        reservation = await request.app.state.state_store.reserve_practice(
            user.uid, date_key, token_request_id, limits.practice_daily
        )
    except UsageLimitExceeded as error:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "practice_quota_exhausted", "message": str(error)},
        ) from error
    except RequestAlreadyProcessing as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "request_processing", "retryable": True},
            headers={"Retry-After": "2"},
        ) from error

    if reservation.status == "cached" and reservation.result:
        # 같은 조작의 재생. 제공자를 다시 부르지도, 토큰을 다시 받지도 않는다.
        return VocabularyGeneratedSet.model_validate(reservation.result)

    try:
        generation = await request.app.state.ai_service.generate_vocabulary(
            topic=payload.topic,
            target_level=payload.target_level,
            exclude_terms=payload.exclude_terms,
            purpose=payload.purpose,
        )
        response = _vocabulary_set_response(
            set_id=hashlib.sha256(
                f"vocabulary-set:{user.uid}:{request_id}".encode()
            ).hexdigest()[:24],
            topic=payload.topic,
            target_level=payload.target_level,
            purpose=payload.purpose,
            generation=generation,
        )
        await request.app.state.state_store.finalize_request(
            token_request_id,
            response.model_dump(by_alias=True, mode="json"),
            request.app.state.request_result_ttl_hours,
        )
        return response
    except AIServiceError as error:
        await request.app.state.state_store.fail_request(token_request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ai_unavailable",
                "message": "AI 단어 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
            },
        ) from error
    except Exception:
        await request.app.state.state_store.fail_request(token_request_id)
        raise


@router.post("/v1/vocabulary/coach", response_model=VocabularySpeakingCoachResult)
async def coach_vocabulary_speaking(
    payload: VocabularySpeakingCoachRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> VocabularySpeakingCoachResult:
    """단어장 AI 말하기 코치 1회(P14.3).

    무료: 단어 학습 · 녹음 · 다시 녹음 · 기기 전사(STT) · 이미 받은 코칭 다시 보기.
    과금: **새 코칭 분석 1회 = 데일리 토큰 1개.** 새 지갑도, 단어장 전용 화폐도,
    두 번째 쿼터 체계도 만들지 않는다 — 세트 생성 · 데일리 분석과 같은
    `reserve_practice`다.

    과금 단위는 HTTP 요청이 아니라 Idempotency-Key가 가리키는 "조작 1회"다.
      - 같은 키 재전송(응답 유실 · 앱 재시도) → 저장된 결과를 그대로 돌려주고 추가 차감 없음
      - 잔액 0 → 제공자를 부르기 **전에** 402
      - 쓸 만한 결과가 없음 → fail_request가 정확히 한 번 환불
      - 사용자가 "다시 분석"을 누르면 앱이 새 키를 보내고 그때 새로 1개 나간다
    모델 · 프롬프트 · 출력 스키마 · 내부 재시도 상한 · 비용(1)은 서버가 정한다.
    """
    request_id = _request_id(idempotency_key)
    token_request_id = hashlib.sha256(
        f"{user.uid}:vocabulary_coach:{request_id}".encode()
    ).hexdigest()
    plan = await _current_plan(request, user.uid)
    limits = plans.limits_for(plan)
    date_key = _date_key()
    # 토큰 확보가 제공자 호출보다 **먼저**다. 잔액이 0이면 AI를 부르지 않는다.
    try:
        reservation = await request.app.state.state_store.reserve_practice(
            user.uid, date_key, token_request_id, limits.practice_daily
        )
    except UsageLimitExceeded as error:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "practice_quota_exhausted", "message": str(error)},
        ) from error
    except RequestAlreadyProcessing as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "request_processing", "retryable": True},
            headers={"Retry-After": "2"},
        ) from error

    if reservation.status == "cached" and reservation.result:
        # 같은 조작의 재생. 제공자를 다시 부르지도, 토큰을 다시 받지도 않는다.
        return VocabularySpeakingCoachResult.model_validate(reservation.result)

    try:
        coaching = await request.app.state.ai_service.coach_vocabulary(
            term=payload.term,
            item_type=payload.type,
            meaning_ko=payload.meaning_ko,
            topic=payload.topic,
            # 목표 등급을 안 보내는 클라이언트(구버전)도 코칭은 성립해야 한다.
            target_level=payload.target_level or OPIcLevel.IM2,
            transcript=payload.transcript,
        )
        draft = coaching.draft
        response = VocabularySpeakingCoachResult(
            resultId=hashlib.sha256(
                f"vocabulary-coach:{user.uid}:{request_id}".encode()
            ).hexdigest()[:24],
            entryId=payload.entry_id,
            targetTerm=payload.term,
            transcript=payload.transcript,
            usageAssessment=draft.usage_assessment,
            usageFeedbackKo=draft.usage_feedback_ko,
            naturalCorrectionEn=draft.natural_correction_en,
            naturalCorrectionKo=draft.natural_correction_ko,
            expandedAnswerEn=draft.expanded_answer_en,
            expandedAnswerKo=draft.expanded_answer_ko,
            relatedExpressions=draft.related_expressions,
            createdAt=datetime.now(UTC),
        )
        await request.app.state.state_store.finalize_request(
            token_request_id,
            response.model_dump(by_alias=True, mode="json"),
            request.app.state.request_result_ttl_hours,
        )
        return response
    except AIServiceError as error:
        await request.app.state.state_store.fail_request(token_request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ai_unavailable",
                "message": "AI 코칭에 실패했습니다. 잠시 후 다시 시도해 주세요.",
            },
        ) from error
    except Exception:
        await request.app.state.state_store.fail_request(token_request_id)
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
    # 진행 중 세션이 있으면 이어서, 없으면 한도 미만일 때만 새 회차 생성.
    # 무료는 평생 1회 체험(전체 기간), 유료는 하루 N회.
    limits = plans.limits_for(await _current_plan(request, user.uid))
    active = await request.app.state.state_store.get_active_mock_session(
        uid=user.uid, date_key=date_key
    )
    if active is not None:
        record = active
    else:
        if limits.mock_is_trial:
            completed = await request.app.state.state_store.count_completed_mock_sessions(
                uid=user.uid
            )
            limit = 1
        else:
            completed = await request.app.state.state_store.count_completed_mock_sessions(
                uid=user.uid, date_key=date_key
            )
            limit = limits.mock_daily
        if completed >= limit:
            await request.app.state.state_store.fail_operation(
                uid=user.uid,
                operation="mock_session_create",
                operation_id=operation_id,
                retryable=False,
            )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "mock_limit_reached",
                    "message": (
                        "무료 체험 모의고사를 이미 사용했어요. 업그레이드하면 매일 응시할 수 있어요."
                        if limits.mock_is_trial
                        else "오늘 사용할 수 있는 모의고사를 모두 사용했어요."
                    ),
                },
            )
        # 회차 인덱스는 "오늘 닫힌 문서 수"(채점 완료 + 포기)로 잡는다. 포기한 회차는
        # 한도를 소진하지 않지만 문서는 남아 있으므로, 인덱스에서 빼면 새 회차가
        # 포기한 회차와 같은 sessionId로 충돌해 create_or_get이 그 회차를 그대로
        # 돌려준다(= 포기한 회차 부활). 포기 이력이 없으면 예전 값과 동일하다.
        attempt = await request.app.state.state_store.count_completed_mock_sessions(
            uid=user.uid, date_key=date_key, include_abandoned=True
        )
        session_id = hashlib.sha256(
            f"{user.uid}:mock-session:{date_key}:{attempt}".encode()
        ).hexdigest()
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
    record = await request.app.state.state_store.get_active_mock_session(
        uid=user.uid,
        date_key=_date_key(),
    )
    if not record:
        raise HTTPException(status_code=404, detail={"code": "mock_session_not_found"})
    return await _mock_session_response(request, user, record)


@router.post("/v1/mock-exams/{session_id}/abandon", response_model=MockSessionResponse)
async def abandon_mock_session(
    session_id: str,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> MockSessionResponse:
    # 사용자가 회차를 포기했을 때 서버 쪽 소유권을 닫는다. 새 stage를 만들지 않고
    # completed로 마감하는 이유는 get_active_mock_session 규칙과 구버전 클라이언트의
    # stage 디코딩을 그대로 두기 위해서다. 대신 abandonedAt을 남겨
    # count_completed_mock_sessions가 이 회차를 한도에서 제외한다(= 같은 날 다시 응시
    # 가능). 이미 소비한 리워드는 되돌리지 않는다.
    record = await request.app.state.state_store.get_mock_session(
        uid=user.uid,
        session_id=session_id,
    )
    if not record:
        raise HTTPException(status_code=404, detail={"code": "mock_session_not_found"})
    if record.get("stage") != MockSessionStage.COMPLETED.value:
        try:
            record = await request.app.state.state_store.transition_mock_session(
                uid=user.uid,
                session_id=session_id,
                expected_stages={
                    stage.value
                    for stage in MockSessionStage
                    if stage is not MockSessionStage.COMPLETED
                },
                stage=MockSessionStage.COMPLETED.value,
                updates={"abandonedAt": datetime.now(UTC)},
            )
        except InvalidSessionTransition:
            # 그 사이 다른 요청이 마감했으면 그것이 최종 상태다.
            record = await request.app.state.state_store.get_mock_session(
                uid=user.uid,
                session_id=session_id,
            )
            if not record:
                raise HTTPException(
                    status_code=404, detail={"code": "mock_session_not_found"}
                ) from None
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
    active_mock = await request.app.state.state_store.get_active_mock_session(
        uid=user.uid, date_key=date_key
    )
    if limits.mock_is_trial:
        # 무료: 평생 최초 1회 체험(전체 기간 완료 수 기준).
        completed_all_time = (
            await request.app.state.state_store.count_completed_mock_sessions(
                uid=user.uid
            )
        )
        mock_remaining = max(0, 1 - completed_all_time)
    else:
        completed_today = (
            await request.app.state.state_store.count_completed_mock_sessions(
                uid=user.uid, date_key=date_key
            )
        )
        mock_remaining = max(0, limits.mock_daily - completed_today)
    # 진행 중 세션이 있거나, 남은 횟수가 있으면 응시 가능.
    mock_available = active_mock is not None or mock_remaining > 0
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
        mockAvailable=mock_available,
        mockRemaining=mock_remaining,
        mockSessionStage=(str(active_mock["stage"]) if active_mock else None),
    )


# 리워드 한도 초과 메시지. 코드(reward_quota_exhausted)가 계약이고 메시지는 표시용인데,
# 이미 배포된 앱은 서버 message를 그대로 보여준다. 그래서 영어 예외 문구
# ("daily reward quota exhausted") 대신 용도별로 다음 행동이 보이는 문장을 내려준다.
_REWARD_QUOTA_MESSAGES: dict[RewardPurpose, str] = {
    RewardPurpose.PRACTICE_CREDITS: "오늘 받을 수 있는 추가 학습 보상을 모두 사용했어요. 내일 다시 받을 수 있어요.",
    RewardPurpose.PRACTICE_REFRESH: "오늘 문제 새로고침 기회를 모두 사용했어요. 내일 다시 받을 수 있어요.",
    RewardPurpose.TARGET_LEVEL_CHANGE: "난이도 변경은 하루 한 번만 가능해요. 내일 다시 시도해 주세요.",
    RewardPurpose.MOCK_START: "오늘 모의고사 시작 기회를 모두 사용했어요. 내일 다시 시작할 수 있어요.",
    RewardPurpose.MOCK_ADJUSTMENT: "오늘 난이도 조정 기회를 모두 사용했어요. 조정 없이 계속 진행할 수 있어요.",
    RewardPurpose.MOCK_RESULT: "오늘 결과 확인 기회를 모두 사용했어요. 내일 다시 확인할 수 있어요.",
}
_REWARD_QUOTA_DEFAULT_MESSAGE = "오늘 받을 수 있는 보상을 모두 사용했어요. 내일 다시 시도해 주세요."


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
            detail={
                "code": "reward_quota_exhausted",
                "message": _REWARD_QUOTA_MESSAGES.get(
                    payload.purpose, _REWARD_QUOTA_DEFAULT_MESSAGE
                ),
            },
        ) from error
    return RewardIntentResponse(
        nonce=nonce,
        purpose=payload.purpose,
        status=str(reward["status"]),
        userIdentifier=user.uid,
        customData=nonce,
        expiresAt=expires_at,
    )


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
    if await request.app.state.state_store.is_iap_event_completed(event_id):
        return {"status": "duplicate"}

    event_type = event.type.upper()
    try:
        customer_info = await request.app.state.revenuecat.get_customer_info(uid)
    except RevenueCatAPIError as error:
        logger.error(
            "RevenueCat customer sync failed uidHash=%s eventIdHash=%s "
            "code=%s upstreamStatus=%s",
            _uid_hash(uid),
            _uid_hash(event_id),
            error.code,
            error.upstream_status,
        )
        raise HTTPException(
            status_code=error.gateway_status,
            detail={"code": "revenuecat_sync_failed"},
        ) from error

    active_entitlement_ids = customer_info.active_entitlement_ids
    plan = plans.plan_from_entitlement_ids(active_entitlement_ids)
    selected_entitlement_ids = (
        [
            identifier
            for identifier in active_entitlement_ids
            if plans.plan_from_entitlement_ids([identifier]) is plan
        ]
        if plan is not Plan.FREE
        else []
    )
    expires_at = customer_info.effective_expiration_for(selected_entitlement_ids)

    entitlement = {
        "plan": str(plan),
        "isActive": plan is not Plan.FREE,
        "source": "revenuecat",
        "activeEntitlementIds": active_entitlement_ids,
        "expiresAt": expires_at,
        "revenueCatRequestDate": customer_info.request_date,
        "lastEventType": event_type,
        "updatedAt": datetime.now(UTC),
    }
    completed = await request.app.state.state_store.complete_iap_sync(
        event_id=event_id,
        uid=uid,
        entitlement=entitlement,
    )
    if not completed:
        return {"status": "duplicate"}
    logger.info(
        "RevenueCat webhook processed uidHash=%s eventIdHash=%s type=%s plan=%s",
        _uid_hash(uid),
        _uid_hash(event_id),
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

    # 오디오 검증은 과금 앞에 둔다. 형식이 틀린 요청은 AI를 부르지 않으므로 토큰을
    # 예약했다가 되돌릴 이유가 없다(§ "검증 실패 → 차감 없음"을 문자 그대로 지킨다).
    try:
        metrics = await request.app.state.audio_service.analyze(audio, transcript)
    except AudioValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_audio", "message": str(error)},
        ) from error

    # 토큰 모델(P13): 사용자가 시작한 Daily AI 작업은 무료가 아니다. AI 분석 1회 = 토큰 1개.
    # 새 세트 획득과 같은 데일리 토큰 지갑(reserve_practice)을 쓴다 — 별도 지갑을 만들지 않는다.
    #
    # 과금 단위는 HTTP 요청이 아니라 "사용자 조작 1회"이고, 그 정체성이 Idempotency-Key다.
    #   - 같은 조작 재전송 → cached 결과 반환, 추가 차감 없음
    #   - 서버/제공자 실패 → fail_request가 정확히 한 번 환불
    #   - 사용자가 "다시 분석"을 의도적으로 누르면 앱이 새 키를 보내고 그때 새로 1개 나간다
    date_key = _date_key()
    try:
        reservation = await request.app.state.state_store.reserve_practice(
            user.uid, date_key, request_id, limits.practice_daily
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
    except AIServiceError as error:
        # 쓸 만한 결과를 못 준 실패다 → 예약한 토큰을 정확히 한 번 되돌린다.
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
