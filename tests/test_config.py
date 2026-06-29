import pytest
from pydantic import ValidationError

from app.config import Settings


def test_complete_mock_configuration_is_accepted() -> None:
    settings = Settings(
        mock_ai=True,
        openai_api_key="",
        firebase_project_id="dailyopic-test",
        admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
    )

    assert settings.mock_ai is True
    assert settings.openai_api_key is None


def test_real_ai_requires_openai_api_key() -> None:
    with pytest.raises(ValidationError):
        Settings(
            mock_ai=False,
            openai_api_key="",
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )


@pytest.mark.parametrize(
    "values",
    [
        {"firebase_project_id": ""},
        {"admob_rewarded_ad_unit_id": ""},
    ],
)
def test_required_external_configuration_is_enforced(values: dict[str, str]) -> None:
    base = {
        "mock_ai": True,
        "firebase_project_id": "dailyopic-test",
        "admob_rewarded_ad_unit_id": "ca-app-pub-5460686409666356/7091483531",
    }
    base.update(values)

    with pytest.raises(ValidationError):
        Settings(**base)
