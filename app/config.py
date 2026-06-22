from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    auth_disabled: bool = True
    app_check_required: bool = False
    mock_ai: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini-2026-03-17"
    token_signing_secret: str = Field(
        default="dailyopic-development-only-secret-change-me",
        min_length=24,
    )
    firebase_project_id: str | None = None
    firestore_enabled: bool = False
    admob_ssv_required: bool = False
    admob_rewarded_ad_unit_id: str | None = None
    debug_reward_auto_verify: bool = True
    question_patterns_path: Path = Path("../opic_mobile/questions.json")
    free_practice_limit: int = 3
    reward_practice_credits: int = 3
    request_result_ttl_hours: int = 24
    audio_max_seconds: int = 180
    audio_max_bytes: int = 4 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if not self.is_production:
            return self
        invalid: list[str] = []
        if self.auth_disabled:
            invalid.append("AUTH_DISABLED must be false")
        if not self.app_check_required:
            invalid.append("APP_CHECK_REQUIRED must be true")
        if not self.firestore_enabled:
            invalid.append("FIRESTORE_ENABLED must be true")
        if self.mock_ai or not self.openai_api_key:
            invalid.append("MOCK_AI must be false and OPENAI_API_KEY must be set")
        if "development-only" in self.token_signing_secret:
            invalid.append("TOKEN_SIGNING_SECRET must be replaced")
        if not self.admob_ssv_required or not self.admob_rewarded_ad_unit_id:
            invalid.append("AdMob SSV and the production rewarded unit must be configured")
        if self.debug_reward_auto_verify:
            invalid.append("DEBUG_REWARD_AUTO_VERIFY must be false")
        if invalid:
            raise ValueError("Unsafe production configuration: " + "; ".join(invalid))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
