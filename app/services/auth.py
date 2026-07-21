from __future__ import annotations

from dataclasses import dataclass
import logging
import uuid

import firebase_admin
from fastapi import Header, HTTPException, Request, status
from firebase_admin import app_check, auth

from app.config import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    uid: str
    legacy_install_id: str | None = None


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})

    async def authenticate(
        self,
        user_id: str | None,
        app_check_token: str | None,
        authorization: str | None = None,
    ) -> CurrentUser:
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

        legacy_install_id: str | None = None
        if user_id:
            try:
                legacy_install_id = str(uuid.UUID(user_id.strip()))
            except (ValueError, AttributeError) as error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "invalid_user_id",
                        "message": "X-DailyOPIc-User-ID must be a valid UUID.",
                    },
                ) from error

        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if token:
            try:
                decoded = auth.verify_id_token(token, check_revoked=True)
                firebase_uid = str(decoded.get("uid") or decoded.get("sub") or "").strip()
                if not firebase_uid:
                    raise ValueError("Firebase token does not contain a UID")
                return CurrentUser(
                    uid=firebase_uid,
                    legacy_install_id=legacy_install_id,
                )
            except auth.InsufficientPermissionError as error:
                logger.exception(
                    "Firebase Authentication verification unavailable: runtime service "
                    "account requires firebaseauth.users.get"
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "auth_verification_unavailable",
                        "message": "Authentication verification is temporarily unavailable.",
                    },
                ) from error
            except auth.CertificateFetchError as error:
                logger.exception("Firebase Authentication verification unavailable: certificate fetch failed")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "auth_verification_unavailable",
                        "message": "Authentication verification is temporarily unavailable.",
                    },
                ) from error
            except auth.RevokedIdTokenError as error:
                logger.warning("Firebase Authentication failed: revoked ID token")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "revoked_id_token",
                        "message": "Firebase ID token has been revoked.",
                    },
                ) from error
            except auth.UserDisabledError as error:
                logger.warning("Firebase Authentication failed: disabled user")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "disabled_firebase_user",
                        "message": "Firebase user is disabled.",
                    },
                ) from error
            except auth.ExpiredIdTokenError as error:
                logger.warning("Firebase Authentication failed: expired ID token")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "expired_id_token",
                        "message": "Firebase ID token is expired.",
                    },
                ) from error
            except (auth.InvalidIdTokenError, ValueError) as error:
                logger.warning("Firebase Authentication failed: invalid ID token")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "invalid_id_token",
                        "message": "Firebase ID token is invalid.",
                    },
                ) from error
            except Exception as error:
                logger.exception(
                    "Firebase Authentication verification unavailable: unexpected Admin SDK error"
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "auth_verification_unavailable",
                        "message": "Authentication verification is temporarily unavailable.",
                    },
                ) from error

        if self._settings.app_env == "production":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "missing_id_token",
                    "message": "Bearer Firebase ID token is required.",
                },
            )
        if not legacy_install_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "missing_user_id",
                    "message": "A Firebase ID token or legacy installation ID is required.",
                },
            )
        logger.warning("Legacy installation UUID authentication accepted outside production")
        return CurrentUser(uid=legacy_install_id, legacy_install_id=legacy_install_id)


async def current_user(
    request: Request,
    user_id: str | None = Header(default=None, alias="X-DailyOPIc-User-ID"),
    app_check_token: str | None = Header(default=None, alias="X-Firebase-AppCheck"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> CurrentUser:
    service: AuthService = request.app.state.auth_service
    return await service.authenticate(user_id, app_check_token, authorization)
