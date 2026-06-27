from __future__ import annotations

import asyncio
import json
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
    UsageResponse,
)
from app.services.admob import SSVVerificationError
from app.services.ai import AIServiceError
from app.services.audio import AudioValidationError
from app.services.auth import CurrentUser, current_user
from app.services.state import (
    RequestAlreadyProcessing,
    RewardNotVerified,
    UsageLimitExceeded,
)
from app.services.tokens import TokenError


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


def _parse_questions(raw: str) -> list[GeneratedQuestion]:
    try:
        return QUESTION_LIST.validate_json(raw)
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_questions", "message": str(error)},
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


@router.get("/health")
async def health(request: Request) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "environment": request.app.state.settings.environment,
        "mockAI": request.app.state.settings.mock_ai,
    }


async def _create_question_set(
    request: Request,
    user: CurrentUser,
    payload: PracticeSetRequest,
    *,
    mode: str,
) -> QuestionSetResponse:
    if mode == "mock":
        questions, fallback = await request.app.state.ai_service.generate_mock(
            payload.target_level, payload.background, getattr(payload, "survey", None)
        )
    else:
        questions, fallback = await request.app.state.ai_service.generate_practice(
            payload.target_level, payload.background
        )
    set_id = str(uuid.uuid4())
    serialized = [item.model_dump(by_alias=True, mode="json") for item in questions]
    set_hash = request.app.state.token_service.question_hash(serialized)
    token = request.app.state.token_service.issue(
        uid=user.uid,
        set_id=set_id,
        mode=mode,
        target_level=payload.target_level.value,
        questions=serialized,
        ttl_seconds=86_400 if mode == "practice" else 7 * 86_400,
    )
    return QuestionSetResponse(
        setId=set_id,
        setToken=token,
        setHash=set_hash,
        questions=questions,
        modelVersion=request.app.state.ai_service.model,
        generatedAt=datetime.now(UTC),
        fallbackUsed=fallback,
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
    auto_verify = settings.debug_reward_auto_verify and not settings.is_production
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


@router.post("/v1/ad-rewards/{nonce}/client-complete", response_model=RewardIntentResponse)
async def complete_reward_from_client(
    nonce: str,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> RewardIntentResponse:
    settings = request.app.state.settings
    if settings.admob_ssv_required:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ssv_required",
                "message": "Reward must be verified by AdMob server-side verification.",
            },
        )

    reward = await request.app.state.state_store.get_reward_intent(nonce, user.uid)
    if not reward:
        raise HTTPException(status_code=404, detail={"code": "reward_not_found"})
    if reward["status"] == "verified":
        return _reward_response(reward, user.uid)
    try:
        verified = await request.app.state.state_store.verify_reward(
            nonce=nonce,
            transaction_id=f"client:{nonce}",
            practice_credit_amount=settings.reward_practice_credits,
        )
    except RewardNotVerified as error:
        raise HTTPException(status_code=400, detail={"code": "reward_not_verified"}) from error
    return _reward_response(verified, user.uid)


@router.get("/v1/admob/ssv", response_class=PlainTextResponse)
async def admob_ssv(request: Request) -> PlainTextResponse:
    try:
        verified = await request.app.state.ssv_verifier.verify(request.url.query)
        if not verified.user_id:
            raise RewardNotVerified("SSV user_id is required")
        reward = await request.app.state.state_store.get_reward_intent(
            verified.nonce, verified.user_id
        )
        if not reward:
            raise RewardNotVerified("SSV user_id does not match the reward intent")
        await request.app.state.state_store.verify_reward(
            nonce=verified.nonce,
            transaction_id=verified.transaction_id,
            practice_credit_amount=request.app.state.settings.reward_practice_credits,
        )
    except (SSVVerificationError, RewardNotVerified) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return PlainTextResponse("OK")


@router.post("/v1/evaluations/practice", response_model=PracticeEvaluation)
async def evaluate_practice(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    question_set_json: Annotated[str, Form(alias="questionSet")],
    question_number: Annotated[int, Form(alias="questionNumber")],
    transcript: Annotated[str, Form(min_length=1, max_length=12_000)],
    target_level: Annotated[str, Form(alias="targetLevel")],
    set_token: Annotated[str, Form(alias="setToken")],
    audio: Annotated[UploadFile | None, File()] = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PracticeEvaluation:
    request_id = _request_id(idempotency_key)
    questions = _parse_questions(question_set_json)
    if question_number < 1 or question_number > len(questions):
        raise HTTPException(status_code=422, detail={"code": "invalid_question_number"})
    serialized = [item.model_dump(by_alias=True, mode="json") for item in questions]
    try:
        request.app.state.token_service.verify(
            set_token, uid=user.uid, mode="practice", questions=serialized
        )
        target = request.app.state.level_adapter.validate_python(target_level)
    except (TokenError, ValidationError) as error:
        raise HTTPException(status_code=401, detail={"code": "invalid_set", "message": str(error)}) from error

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
            request_id, serialized_result, settings.request_result_ttl_hours
        )
        return result
    except AudioValidationError as error:
        await request.app.state.state_store.fail_request(request_id)
        raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": str(error)}) from error
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
        raise HTTPException(status_code=422, detail={"code": "invalid_manifest", "message": str(error)}) from error
    serialized = [item.model_dump(by_alias=True, mode="json") for item in manifest.questions]
    try:
        token_payload = request.app.state.token_service.verify(
            manifest.set_token, uid=user.uid, mode="mock", questions=serialized
        )
    except TokenError as error:
        raise HTTPException(status_code=401, detail={"code": "invalid_set", "message": str(error)}) from error

    settings = request.app.state.settings
    if settings.is_production and len(audio_files) != 15:
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_audio", "message": "All 15 audio files are required."},
        )
    try:
        reservation = await request.app.state.state_store.reserve_mock(
            user.uid,
            request_id,
            manifest.reward_nonce,
            str(token_payload["questionHash"]),
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
            questions=manifest.questions,
            transcripts=[item.transcript for item in manifest.answers],
            target=manifest.target_level,
            metrics=list(metrics),
        )
        serialized_result = result.model_dump(by_alias=True, mode="json")
        await request.app.state.state_store.finalize_request(
            request_id, serialized_result, settings.request_result_ttl_hours
        )
        return result
    except AudioValidationError as error:
        await request.app.state.state_store.fail_request(request_id)
        raise HTTPException(status_code=422, detail={"code": "invalid_audio", "message": str(error)}) from error
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
