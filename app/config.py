from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production"] = "development"
    mock_ai: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini-2026-03-17"
    firebase_project_id: str = Field(min_length=1)
    admob_rewarded_ad_unit_id: str = Field(min_length=1)
    free_practice_limit: int = 3
    reward_practice_credits: int = 1
    max_daily_reward_count: int = 3
    minimum_supported_app_version: str = "1.0.0"
    guide_schema_version: int = 2
    question_generation_v2_enabled: bool = True
    evaluation_rubric_v2_enabled: bool = True
    mock_session_v2_enabled: bool = True
    practice_refresh_enabled: bool = True
    read_rate_limit_per_minute: int = 120
    mutation_rate_limit_per_minute: int = 30
    ai_rate_limit_per_minute: int = 12

    @model_validator(mode="after")
    def validate_required_settings(self) -> "Settings":
        self.openai_api_key = self._clean(self.openai_api_key)
        self.firebase_project_id = self._clean_required(
            self.firebase_project_id, "FIREBASE_PROJECT_ID"
        )
        self.admob_rewarded_ad_unit_id = self._clean_required(
            self.admob_rewarded_ad_unit_id, "ADMOB_REWARDED_AD_UNIT_ID"
        )

        if not self.mock_ai and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY must be set when MOCK_AI is false")
        if self.app_env == "production" and self.mock_ai:
            raise ValueError("MOCK_AI must be false in production")
        if (
            self.app_env == "production"
            and self.admob_rewarded_ad_unit_id
            == "ca-app-pub-3940256099942544/5224354917"
        ):
            raise ValueError("Google's sample rewarded ad unit cannot be used in production")
        return self

    @staticmethod
    def _clean(value: str | None) -> str | None:
        cleaned = value.strip() if value else ""
        return cleaned or None

    @classmethod
    def _clean_required(cls, value: str | None, name: str) -> str:
        cleaned = cls._clean(value)
        if not cleaned:
            raise ValueError(f"{name} must be set")
        return cleaned


@lru_cache
def get_settings() -> Settings:
    return Settings()
