from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


class TokenError(ValueError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class SignedSetTokenService:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    @staticmethod
    def question_hash(questions: list[dict[str, Any]]) -> str:
        canonical = json.dumps(
            questions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def issue(
        self,
        *,
        uid: str,
        set_id: str,
        mode: str,
        target_level: str,
        questions: list[dict[str, Any]],
        ttl_seconds: int = 86_400,
    ) -> str:
        payload = {
            "uid": uid,
            "setId": set_id,
            "mode": mode,
            "targetLevel": target_level,
            "questionHash": self.question_hash(questions),
            "exp": int(time.time()) + ttl_seconds,
        }
        body = _encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = _encode(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def verify(
        self, token: str, *, uid: str, mode: str, questions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            body, signature = token.split(".", maxsplit=1)
        except ValueError as error:
            raise TokenError("invalid set token") from error

        expected = _encode(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise TokenError("invalid set token signature")

        try:
            payload = json.loads(_decode(body))
        except (ValueError, json.JSONDecodeError) as error:
            raise TokenError("invalid set token payload") from error

        if payload.get("exp", 0) < int(time.time()):
            raise TokenError("set token expired")
        if payload.get("uid") != uid or payload.get("mode") != mode:
            raise TokenError("set token does not belong to this request")
        if payload.get("questionHash") != self.question_hash(questions):
            raise TokenError("question set was modified")
        return payload
