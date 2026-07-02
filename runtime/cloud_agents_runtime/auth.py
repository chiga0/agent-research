from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from http import cookies
from typing import Any


SESSION_COOKIE = "cloud_agents_session"


@dataclass(frozen=True)
class AuthConfig:
    token: str | None = None
    protect_health: bool = False
    login_user: str | None = None
    login_password: str | None = None
    session_secret: str | None = None
    session_ttl_seconds: int = 12 * 60 * 60

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @property
    def login_enabled(self) -> bool:
        return bool(self.login_user and self.login_password and self.session_secret_value)

    @property
    def session_secret_value(self) -> str | None:
        return self.session_secret or self.token

    def is_public_path(self, path: str) -> bool:
        if path == "/health" and not self.protect_health:
            return True
        if path in {"/", "/ui", "/auth/session", "/auth/login", "/auth/logout"}:
            return True
        return path.startswith("/assets/")

    def login_matches(self, username: Any, password: Any) -> bool:
        if not self.login_enabled:
            return False
        return (
            isinstance(username, str)
            and isinstance(password, str)
            and hmac.compare_digest(username, self.login_user or "")
            and hmac.compare_digest(password, self.login_password or "")
        )

    def issue_session_cookie(
        self,
        username: str,
        *,
        cookie_path: str = "/",
        secure: bool = False,
    ) -> str:
        expires_at = int(time.time()) + self.session_ttl_seconds
        payload = {"u": username, "exp": expires_at}
        encoded_payload = _b64encode_json(payload)
        signature = _sign(encoded_payload, self.session_secret_value or "")
        return _cookie_header(
            f"{encoded_payload}.{signature}",
            max_age=self.session_ttl_seconds,
            path=cookie_path,
            secure=secure,
        )

    def clear_session_cookie(self, *, cookie_path: str = "/", secure: bool = False) -> str:
        return _cookie_header("", max_age=0, path=cookie_path, secure=secure)

    def session_identity(self, cookie_header: str | None) -> dict[str, Any] | None:
        if not self.login_enabled or not cookie_header:
            return None
        try:
            parsed = cookies.SimpleCookie(cookie_header)
        except cookies.CookieError:
            return None
        morsel = parsed.get(SESSION_COOKIE)
        if not morsel or "." not in morsel.value:
            return None
        encoded_payload, signature = morsel.value.rsplit(".", 1)
        expected = _sign(encoded_payload, self.session_secret_value or "")
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload = _b64decode_json(encoded_payload)
        except (ValueError, json.JSONDecodeError):
            return None
        username = payload.get("u")
        expires_at = payload.get("exp")
        if (
            not isinstance(username, str)
            or username != self.login_user
            or not isinstance(expires_at, int)
            or expires_at < int(time.time())
        ):
            return None
        return {
            "principal_id": username,
            "roles": ["owner"],
            "scopes": ["*:*"],
            "auth_type": "session",
        }

    def session_status(self, cookie_header: str | None) -> dict[str, Any]:
        identity = self.session_identity(cookie_header)
        principal = identity["principal_id"] if identity else None
        return {
            "authenticated": bool(identity) or not self.login_enabled,
            "login_required": self.login_enabled,
            "principal": (
                {
                    "id": principal or "local-dev",
                    "display_name": principal or "Local development",
                    "roles": ["owner"],
                }
                if identity or not self.login_enabled
                else None
            ),
        }


def is_authorized(config: AuthConfig, path: str, authorization: str | None) -> bool:
    if not config.enabled or config.is_public_path(path):
        return True
    expected = f"Bearer {config.token}"
    return bool(authorization) and hmac.compare_digest(authorization, expected)


def _cookie_header(value: str, *, max_age: int, path: str, secure: bool) -> str:
    cookie_path = path if path.startswith("/") else "/"
    parts = [
        f"{SESSION_COOKIE}={value}",
        f"Max-Age={max_age}",
        f"Path={cookie_path}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _b64encode_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode_json(value: str) -> dict[str, Any]:
    padded = value + ("=" * (-len(value) % 4))
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    payload = json.loads(decoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("session payload must be an object")
    return payload
