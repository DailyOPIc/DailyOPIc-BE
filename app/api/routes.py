from __future__ import annotations

import asyncio
import hashlib
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
    GeneratedQuestion,
    MockEvaluation,
    MockEvaluationManifest,
    MockExamRequest,
    PracticeEvaluation,
    PracticeSetRequest,
    QuestionSetResponse,
    RewardIntentRequest,
    RewardIntentResponse,
    RewardPurpose,
    TargetLevelRequest,
    TargetLevelResponse,
    UsageResponse,
)
from app.services.admob import SSVVerificationError
from app.services.ai import AIQuestionGenerationError, AIServiceError
from app.services.audio import AudioValidationError
from app.services.auth import CurrentUser, current_user
from app.services.questions import prompt_hash, question_set_hash
from app.services.state import (
    RequestAlreadyProcessing,
    RewardNotVerified,
    UsageLimitExceeded,
)


logger = logging.getLogger(__name__)
router = APIRouter()
KST = ZoneInfo("Asia/Seoul")
QUESTION_LIST = TypeAdapter(list[GeneratedQuestion])
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def _date_key() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


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


async def _ensure_target_level(
    request: Request, user: CurrentUser, target_level: str
) -> None:
    current = await request.app.state.state_store.get_target_level(user.uid)
    if current is None:
        try:
            await request.app.state.state_store.set_target_level(
                uid=user.uid,
                target_level=target_level,
                reward_nonce=None,
            )
        except RewardNotVerified as error:
            _target_level_change_required(str(error))
        return
    if current != target_level:
        _target_level_change_required(
            "목표 등급을 변경하려면 보상형 광고를 끝까지 시청해야 합니다."
        )


@router.get("/health")
async def health(request: Request) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "mockAI": request.app.state.settings.mock_ai,
    }


async def _create_question_set(
    request: Request,
    user: CurrentUser,
    payload: PracticeSetRequest,
    *,
    mode: str,
) -> QuestionSetResponse:
    await _ensure_target_level(request, user, payload.target_level.value)
    uid_hash = _uid_hash(user.uid)
    history = await request.app.state.state_store.get_question_history(
        uid=user.uid,
        mode=mode,
    )
    logger.info(
        "question generation requested mode=%s uidHash=%s targetLevel=%s mockAI=%s "
        "model=%s recentSetCount=%s recentTopicCount=%s recentPromptCount=%s",
        mode,
        uid_hash,
        payload.target_level.value,
        request.app.state.settings.mock_ai,
        request.app.state.ai_service.model,
        len(history.get("setHashes", [])),
        len(history.get("topicIds", [])),
        len(history.get("promptHashes", [])),
    )
    try:
        if mode == "mock":
            generation = await request.app.state.ai_service.generate_mock(
                payload.target_level,
                payload.background,
                getattr(payload, "survey", None),
                history=history,
            )
        else:
            generation = await request.app.state.ai_service.generate_practice(
                payload.target_level,
                payload.background,
                history=history,
            )
    except AIQuestionGenerationError as error:
        logger.exception(
            "question generation failed mode=%s uidHash=%s targetLevel=%s model=%s",
            mode,
            uid_hash,
            payload.target_level.value,
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
    set_id = str(uuid.uuid4())
    serialized = [item.model_dump(by_alias=True, mode="json") for item in questions]
    set_hash = question_set_hash(serialized)
    await request.app.state.state_store.save_question_set(
        uid=user.uid,
        set_id=set_id,
        mode=mode,
        target_level=payload.target_level.value,
        question_hash=set_hash,
        questions=serialized,
        expires_at=datetime.now(UTC)
        + timedelta(seconds=86_400 if mode == "practice" else 7 * 86_400),
    )
    await request.app.state.state_store.record_question_history(
        uid=user.uid,
        mode=mode,
        set_hash=set_hash,
        questions=serialized,
    )
    topic_ids = [str(item.get("topicId") or "") for item in serialized]
    prompt_hashes = [prompt_hash(str(item.get("prompt") or ""))[:16] for item in serialized]
    usage = generation.usage
    logger.info(
        "question generation succeeded mode=%s uidHash=%s targetLevel=%s provider=%s "
        "model=%s openaiResponseId=%s fallbackUsed=%s setHash=%s topicIds=%s "
        "promptHashes=%s inputTokens=%s cachedInputTokens=%s outputTokens=%s "
        "reasoningTokens=%s totalTokens=%s",
        mode,
        uid_hash,
        payload.target_level.value,
        generation.provider,
        request.app.state.ai_service.model,
        generation.openai_response_id,
        generation.fallback_used,
        set_hash,
        topic_ids,
        prompt_hashes,
        usage.input_tokens if usage else None,
        usage.cached_input_tokens if usage else None,
        usage.output_tokens if usage else None,
        usage.reasoning_tokens if usage else None,
        usage.total_tokens if usage else None,
    )
    return QuestionSetResponse(
        setId=set_id,
        setHash=set_hash,
        questions=questions,
        modelVersion=request.app.state.ai_service.model,
        generatedAt=datetime.now(UTC),
        fallbackUsed=generation.fallback_used,
    )


@router.put("/v1/users/me/target-level", response_model=TargetLevelResponse)
async def update_target_level(
    payload: TargetLevelRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> TargetLevelResponse:
    try:
        result = await request.app.state.state_store.set_target_level(
            uid=user.uid,
            target_level=payload.target_level.value,
            reward_nonce=payload.reward_nonce,
        )
    except RewardNotVerified as error:
        _target_level_change_required(str(error))
    return TargetLevelResponse(
        targetLevel=result["targetLevel"],
        previousTargetLevel=result["previousTargetLevel"],
        changed=result["changed"],
        rewardConsumed=result["rewardConsumed"],
    )


@router.post("/v1/question-sets/practice", response_model=QuestionSetResponse)
async def create_practice_set(
    payload: PracticeSetRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> QuestionSetResponse:
    return await _create_question_set(request, user, payload, mode="practice")


@router.post("/v1/mock-exams", response_model=QuestionSetResponse)
async def create_mock_exam(
    payload: MockExamRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> QuestionSetResponse:
    return await _create_question_set(request, user, payload, mode="mock")


@router.get("/v1/usage", response_model=UsageResponse)
async def usage(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> UsageResponse:
    settings = request.app.state.settings
    date_key = _date_key()
    value = await request.app.state.state_store.get_usage(user.uid, date_key)
    return UsageResponse(
        date=date_key,
        freeRemaining=max(0, settings.free_practice_limit - int(value.get("freeUsed", 0))),
        bonusRemaining=max(0, int(value.get("bonusRemaining", 0))),
    )


@router.post("/v1/ad-rewards/intents", response_model=RewardIntentResponse)
async def create_reward_intent(
    payload: RewardIntentRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> RewardIntentResponse:
    settings = request.app.state.settings
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
            auto_verify=False,
            practice_credit_amount=settings.reward_practice_credits,
            max_daily_reward_count=settings.max_daily_reward_count,
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
        await request.app.state.state_store.verify_reward(
            nonce=verified.nonce,
            transaction_id=verified.transaction_id,
            practice_credit_amount=request.app.state.settings.reward_practice_credits,
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
    except (SSVVerificationError, RewardNotVerified) as error:
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
async def evaluate_practice(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    set_id: Annotated[str, Form(alias="setId")],
    question_number: Annotated[int, Form(alias="questionNumber")],
    transcript: Annotated[str, Form(min_length=1, max_length=12_000)],
    target_level: Annotated[str, Form(alias="targetLevel")],
    audio: Annotated[UploadFile | None, File()] = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PracticeEvaluation:
    request_id = _request_id(idempotency_key)
    try:
        target = request.app.state.level_adapter.validate_python(target_level)
        question_set = await request.app.state.state_store.get_question_set(
            uid=user.uid, set_id=set_id, mode="practice"
        )
        if not question_set or question_set.get("targetLevel") != target.value:
            raise ValueError("question set not found")
        questions = QUESTION_LIST.validate_python(question_set["questions"])
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_set", "message": str(error)},
        ) from error
    if question_number < 1 or question_number > len(questions):
        raise HTTPException(status_code=422, detail={"code": "invalid_question_number"})

    settings = request.app.state.settings
    try:
        reservation = await request.app.state.state_store.reserve_practice(
            user.uid, _date_key(), request_id, settings.free_practice_limit
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
            question=questions[question_number - 1],
            transcript=transcript.strip(),
            target=target,
            metrics=metrics,
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
        if not question_set or question_set.get("targetLevel") != manifest.target_level.value:
            raise ValueError("question set not found")
        questions = QUESTION_LIST.validate_python(question_set["questions"])
        question_hash = str(question_set["questionHash"])
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_set", "message": str(error)},
        ) from error
    if len(audio_files) != 15:
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_audio", "message": "All 15 audio files are required."},
        )
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
        number: item
        for item in audio_files
        if (number := _audio_number(item)) is not None and 1 <= number <= 15
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
        result = await request.app.state.ai_service.evaluate_mock(
            questions=questions,
            transcripts=[item.transcript for item in manifest.answers],
            target=manifest.target_level,
            metrics=list(metrics),
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
