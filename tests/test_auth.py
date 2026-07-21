import pytest
from fastapi import HTTPException
from firebase_admin import auth as firebase_auth

from app.config import Settings
from app.services.auth import AuthService


USER_ID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.asyncio
async def test_valid_uuid_and_app_check_returns_current_user() -> None:
    service = AuthService(
        Settings(
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )

    user = await service.authenticate(USER_ID, "app-check-token")

    assert user.uid == USER_ID


@pytest.mark.asyncio
async def test_missing_user_id_returns_401() -> None:
    service = AuthService(
        Settings(
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )

    with pytest.raises(HTTPException) as error:
        await service.authenticate(None, "app-check-token")

    assert error.value.status_code == 401
    assert error.value.detail["code"] == "missing_user_id"


@pytest.mark.asyncio
async def test_invalid_user_id_returns_401() -> None:
    service = AuthService(
        Settings(
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )

    with pytest.raises(HTTPException) as error:
        await service.authenticate("not-a-uuid", "app-check-token")

    assert error.value.status_code == 401
    assert error.value.detail["code"] == "invalid_user_id"


@pytest.mark.asyncio
async def test_missing_app_check_returns_403() -> None:
    service = AuthService(
        Settings(
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )

    with pytest.raises(HTTPException) as error:
        await service.authenticate(USER_ID, None)

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "missing_app_check"


@pytest.mark.asyncio
async def test_invalid_app_check_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AuthService(
        Settings(
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )

    def reject_app_check(token: str) -> None:
        raise ValueError("invalid")

    monkeypatch.setattr("app.services.auth.app_check.verify_token", reject_app_check)

    with pytest.raises(HTTPException) as error:
        await service.authenticate(USER_ID, "app-check-token")

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "invalid_app_check"


@pytest.mark.asyncio
async def test_production_requires_firebase_id_token() -> None:
    service = AuthService(
        Settings(
            app_env="production",
            mock_ai=False,
            openai_api_key="test-key",
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )

    with pytest.raises(HTTPException) as error:
        await service.authenticate(USER_ID, "app-check-token")

    assert error.value.status_code == 401
    assert error.value.detail["code"] == "missing_id_token"


@pytest.mark.asyncio
async def test_firebase_uid_is_source_of_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AuthService(
        Settings(
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )
    monkeypatch.setattr(
        "app.services.auth.auth.verify_id_token",
        lambda token, check_revoked: {"uid": "firebase-user-123"},
    )

    user = await service.authenticate(USER_ID, "app-check-token", "Bearer valid-token")

    assert user.uid == "firebase-user-123"
    assert user.legacy_install_id == USER_ID


@pytest.mark.asyncio
async def test_auth_permission_failure_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AuthService(
        Settings(
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )

    def reject_token(token: str, check_revoked: bool) -> None:
        raise firebase_auth.InsufficientPermissionError(
            "missing firebaseauth.users.get",
            cause=None,
            http_response=None,
        )

    monkeypatch.setattr("app.services.auth.auth.verify_id_token", reject_token)

    with pytest.raises(HTTPException) as error:
        await service.authenticate(USER_ID, "app-check-token", "Bearer valid-token")

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "auth_verification_unavailable"


@pytest.mark.asyncio
async def test_expired_id_token_returns_refreshable_code(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AuthService(
        Settings(
            firebase_project_id="dailyopic-test",
            admob_rewarded_ad_unit_id="ca-app-pub-5460686409666356/7091483531",
        )
    )

    def reject_token(token: str, check_revoked: bool) -> None:
        raise firebase_auth.ExpiredIdTokenError("expired", cause=None)

    monkeypatch.setattr("app.services.auth.auth.verify_id_token", reject_token)

    with pytest.raises(HTTPException) as error:
        await service.authenticate(USER_ID, "app-check-token", "Bearer expired-token")

    assert error.value.status_code == 401
    assert error.value.detail["code"] == "expired_id_token"
