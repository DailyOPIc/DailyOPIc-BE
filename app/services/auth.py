from __future__ import annotations

from dataclasses import dataclass

import firebase_admin
from fastapi import Header, HTTPException, Request, status
from firebase_admin import app_check, auth

from app.config import Settings


@dataclass(frozen=True, slots=True)
class CurrentUser:
    uid: str


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if not settings.auth_disabled and not firebase_admin._apps:
            firebase_admin.initialize_app(
                options={"projectId": settings.firebase_project_id}
                if settings.firebase_project_id
                else None
            )

    async def authenticate(
        self,
        authorization: str | None,
        app_check_token: str | None,
        debug_uid: str | None,
    ) -> CurrentUser:
        if self._settings.auth_disabled:
            return CurrentUser(uid=debug_uid or "dailyopic-demo-user")

        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "missing_auth", "message": "Firebase ID token is required."},
            )
        try:
            decoded = auth.verify_id_token(authorization.removeprefix("Bearer ").strip())
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_auth", "message": "Firebase ID token is invalid."},
            ) from error

        if self._settings.app_check_required:
            if not app_check_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "missing_app_check", "message": "App Check token is required."},
                )
            try:
                app_check.verify_token(app_check_token)
            except Exception as error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "invalid_app_check", "message": "App Check token is invalid."},
                ) from error
        return CurrentUser(uid=str(decoded["uid"]))


async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    app_check_token: str | None = Header(default=None, alias="X-Firebase-AppCheck"),
    debug_uid: str | None = Header(default=None, alias="X-Debug-User-ID"),
) -> CurrentUser:
    service: AuthService = request.app.state.auth_service
    return await service.authenticate(authorization, app_check_token, debug_uid)
