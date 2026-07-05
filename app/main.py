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
    yield


app = FastAPI(
    title="DailyOPIc API",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(router)
