from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic import TypeAdapter

from app.api.routes import router
from app.config import get_settings
from app.models.api import OPIcLevel
from app.services.admob import AdMobSSVVerifier
from app.services.ai import AIService
from app.services.audio import AudioMetricsService
from app.services.auth import AuthService
from app.services.questions import QuestionPatternRepository
from app.services.state import FirestoreStateStore
from app.services.telemetry import RequestTimer, emit, stable_hash
from app.services.rate_limit import (
    RateLimitExceeded,
    SlidingWindowRateLimiter,
    request_identity,
)


QUESTION_PATTERN_FILE = Path("app/data/question_patterns.json")
REQUEST_RESULT_TTL_HOURS = 24
AUDIO_MAX_SECONDS = 180
AUDIO_MAX_BYTES = 4 * 1024 * 1024
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "DailyOPIc AI settings loaded. mockAI=%s openaiModel=%s openaiKeyPresent=%s",
        settings.mock_ai,
        settings.openai_model,
        bool(settings.openai_api_key),
    )
    repository = QuestionPatternRepository(QUESTION_PATTERN_FILE)
    app.state.settings = settings
    app.state.request_result_ttl_hours = REQUEST_RESULT_TTL_HOURS
    app.state.auth_service = AuthService(settings)
    app.state.state_store = FirestoreStateStore(settings.firebase_project_id)
    app.state.audio_service = AudioMetricsService(
        max_bytes=AUDIO_MAX_BYTES,
        max_seconds=AUDIO_MAX_SECONDS,
    )
    app.state.ai_service = AIService(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        mock=settings.mock_ai,
        repository=repository,
    )
    app.state.ssv_verifier = AdMobSSVVerifier(
        expected_ad_unit=settings.admob_rewarded_ad_unit_id,
    )
    app.state.level_adapter = TypeAdapter(OPIcLevel)
    app.state.rate_limiter = SlidingWindowRateLimiter()
    yield


app = FastAPI(
    title="DailyOPIc API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_telemetry(request, call_next):
    timer = RequestTimer()
    request_id = request.headers.get("Idempotency-Key")
    try:
        response = await call_next(request)
    except Exception:
        emit(
            "request_failed",
            method=request.method,
            path=request.url.path,
            operationIdHash=stable_hash(request_id),
            latencyMs=timer.latency_ms,
        )
        raise
    emit(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        operationIdHash=stable_hash(request_id),
        latencyMs=timer.latency_ms,
    )
    return response


@app.middleware("http")
async def endpoint_rate_limit(request, call_next):
    if request.url.path in {"/health", "/v1/admob/ssv"}:
        return await call_next(request)
    settings = get_settings()
    identity = request_identity(
        request.headers.get("Authorization"),
        request.headers.get("X-Firebase-AppCheck"),
        request.client.host if request.client else None,
    )
    is_mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    is_ai = is_mutation and any(
        segment in request.url.path
        for segment in ("question-sets", "mock-exams", "evaluations")
    )
    limit = (
        settings.ai_rate_limit_per_minute
        if is_ai
        else settings.mutation_rate_limit_per_minute
        if is_mutation
        else settings.read_rate_limit_per_minute
    )
    try:
        await request.app.state.rate_limiter.check(
            f"{identity}:{'ai' if is_ai else request.method}",
            limit=limit,
        )
    except RateLimitExceeded as error:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(error.retry_after)},
            content={
                "detail": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please retry later.",
                    "retryable": True,
                    "retryAfterSeconds": error.retry_after,
                }
            },
        )
    return await call_next(request)


app.include_router(router)
