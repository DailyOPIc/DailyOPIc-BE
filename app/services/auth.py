from __future__ import annotations

from dataclasses import dataclass
import logging
import uuid

import firebase_admin
from fastapi import Header, HTTPException, Request, status
from firebase_admin import app_check

from app.config import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    uid: str


class AuthService:
    def __init__(self, settings: Settings) -> None:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})

    async def authenticate(
        self,
        user_id: str | None,
        app_check_token: str | None,
    ) -> CurrentUser:
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "missing_user_id",
                    "message": "X-DailyOPIc-User-ID header is required.",
                },
            )
        try:
            parsed_user_id = str(uuid.UUID(user_id.strip()))
        except (ValueError, AttributeError) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "invalid_user_id",
                    "message": "X-DailyOPIc-User-ID must be a valid UUID.",
                },
            ) from error

        if not app_check_token:
            logger.warning("Firebase App Check failed: missing token")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "missing_app_check", "message": "App Check token is required."},
            )
        try:
            app_check.verify_token(app_check_token)
        except Exception as error:
            logger.warning("Firebase App Check failed: invalid token")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "invalid_app_check", "message": "App Check token is invalid."},
            ) from error
        logger.info("Firebase App Check verified")
        return CurrentUser(uid=parsed_user_id)


async def current_user(
    request: Request,
    user_id: str | None = Header(default=None, alias="X-DailyOPIc-User-ID"),
    app_check_token: str | None = Header(default=None, alias="X-Firebase-AppCheck"),
) -> CurrentUser:
    service: AuthService = request.app.state.auth_service
    return await service.authenticate(user_id, app_check_token)
