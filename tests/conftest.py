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
        "STATE_BACKEND": "sqlite",
        # 파일이 아닌 인메모리 SQLite → 테스트마다 새 DB로 격리 보장
        "SQLITE_URL": "sqlite://",
    }
)


@pytest.fixture(autouse=True)
def app_test_runtime(monkeypatch: pytest.MonkeyPatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("app.services.auth.app_check.verify_token", lambda token: None)
    yield
    get_settings.cache_clear()
