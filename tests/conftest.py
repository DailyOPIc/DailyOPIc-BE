import os

import pytest


os.environ.update(
    {
        "FIREBASE_PROJECT_ID": "dailyopic-test",
        "ADMOB_REWARDED_AD_UNIT_ID": "ca-app-pub-5460686409666356/7091483531",
        "MOCK_AI": "true",
        "FREE_PRACTICE_LIMIT": "3",
        "REWARD_PRACTICE_CREDITS": "1",
        "MAX_DAILY_REWARD_COUNT": "3",
        "REVENUECAT_WEBHOOK_AUTH": "test-rc-secret",
    }
)


@pytest.fixture(autouse=True)
def app_test_runtime(monkeypatch: pytest.MonkeyPatch):
    from app.config import get_settings
    from app.services.state import InMemoryStateStore

    get_settings.cache_clear()
    monkeypatch.setattr("app.main.FirestoreStateStore", lambda project_id: InMemoryStateStore())
    monkeypatch.setattr("app.services.auth.app_check.verify_token", lambda token: None)
    yield
    get_settings.cache_clear()
