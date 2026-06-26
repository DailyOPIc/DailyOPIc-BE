import pytest
from pydantic import ValidationError

from app.config import Settings


def test_unsafe_production_configuration_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_complete_production_configuration_is_accepted() -> None:
    settings = Settings(
        environment="production",
        auth_disabled=False,
        app_check_required=True,
        firestore_enabled=True,
        mock_ai=False,
        openai_api_key="test-openai-key",
        token_signing_secret="prod_signing_value_2026_entropy_X9Z7Q4M2",
        admob_ssv_required=False,
        admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        debug_reward_auto_verify=False,
        firebase_project_id="opicmobile-45cd5",
    )
    assert settings.is_production


@pytest.mark.parametrize(
    "override",
    [
        {"auth_disabled": True},
        {"app_check_required": False},
        {"firestore_enabled": False},
        {"mock_ai": True},
        {"openai_api_key": ""},
        {"debug_reward_auto_verify": True},
        {"token_signing_secret": "replace-with-a-long-random-secret"},
        {"token_signing_secret": "short-value"},
    ],
)
def test_unsafe_production_overrides_are_rejected(override: dict[str, object]) -> None:
    values: dict[str, object] = {
        "environment": "production",
        "auth_disabled": False,
        "app_check_required": True,
        "firestore_enabled": True,
        "mock_ai": False,
        "openai_api_key": "test-openai-key",
        "token_signing_secret": "prod_signing_value_2026_entropy_X9Z7Q4M2",
        "admob_ssv_required": False,
        "admob_rewarded_ad_unit_id": "ca-app-pub-5460686409666356/7091483531",
        "debug_reward_auto_verify": False,
        "firebase_project_id": "opicmobile-45cd5",
    }
    values.update(override)
    with pytest.raises(ValidationError):
        Settings(**values)


def test_real_ai_requires_openai_api_key_in_any_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(mock_ai=False, openai_api_key="")
