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
        openai_api_key="test-secret",
        token_signing_secret="a-secure-production-signing-secret",
        admob_ssv_required=True,
        admob_rewarded_ad_unit_id="ca-app-pub-example/rewarded",
        debug_reward_auto_verify=False,
    )
    assert settings.is_production
