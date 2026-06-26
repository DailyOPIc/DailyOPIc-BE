import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services.auth import AuthService


@pytest.mark.asyncio
async def test_auth_disabled_uses_debug_user() -> None:
    service = AuthService(Settings(auth_disabled=True))

    user = await service.authenticate(None, None, "debug-user")

    assert user.uid == "debug-user"


@pytest.mark.asyncio
async def test_missing_firebase_id_token_returns_401() -> None:
    service = AuthService(
        Settings(
            auth_disabled=False,
            app_check_required=False,
            firebase_project_id="dailyopic-test",
        )
    )

    with pytest.raises(HTTPException) as error:
        await service.authenticate(None, None, None)

    assert error.value.status_code == 401
    assert error.value.detail["code"] == "missing_auth"


@pytest.mark.asyncio
async def test_missing_app_check_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AuthService(
        Settings(
            auth_disabled=False,
            app_check_required=True,
            firebase_project_id="dailyopic-test",
        )
    )
    monkeypatch.setattr("app.services.auth.auth.verify_id_token", lambda token: {"uid": "u1"})

    with pytest.raises(HTTPException) as error:
        await service.authenticate("Bearer firebase-token", None, None)

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "missing_app_check"


@pytest.mark.asyncio
async def test_invalid_app_check_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AuthService(
        Settings(
            auth_disabled=False,
            app_check_required=True,
            firebase_project_id="dailyopic-test",
        )
    )
    monkeypatch.setattr("app.services.auth.auth.verify_id_token", lambda token: {"uid": "u1"})

    def reject_app_check(token: str) -> None:
        raise ValueError("invalid")

    monkeypatch.setattr("app.services.auth.app_check.verify_token", reject_app_check)

    with pytest.raises(HTTPException) as error:
        await service.authenticate("Bearer firebase-token", "app-check-token", None)

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "invalid_app_check"
