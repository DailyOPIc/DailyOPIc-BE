from __future__ import annotations

from contextlib import asynccontextmanager

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
from app.services.state import FirestoreStateStore, InMemoryStateStore
from app.services.tokens import SignedSetTokenService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    repository = QuestionPatternRepository(settings.question_patterns_path)
    app.state.settings = settings
    app.state.auth_service = AuthService(settings)
    app.state.state_store = (
        FirestoreStateStore(settings.firebase_project_id)
        if settings.firestore_enabled
        else InMemoryStateStore()
    )
    app.state.token_service = SignedSetTokenService(settings.token_signing_secret)
    app.state.audio_service = AudioMetricsService(
        max_bytes=settings.audio_max_bytes,
        max_seconds=settings.audio_max_seconds,
    )
    app.state.ai_service = AIService(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        mock=settings.mock_ai,
        repository=repository,
    )
    app.state.ssv_verifier = AdMobSSVVerifier(
        required=settings.admob_ssv_required,
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
