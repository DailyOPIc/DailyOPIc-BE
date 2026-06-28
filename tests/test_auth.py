import pytest
from fastapi import HTTPException

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
