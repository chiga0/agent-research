from __future__ import annotations

import secrets
from typing import Any

from .events import utc_now
from .models import ApiToken, hash_token
from .store import RunStore


ROLE_DEFINITIONS = [
    {
        "id": "owner",
        "description": "Can administer runtime, profiles, missions, runs, and backups.",
        "permissions": [
            "runs:*",
            "missions:*",
            "profiles:*",
            "permissions:*",
            "executors:*",
            "workers:*",
            "cost:*",
            "ops:*",
            "access:*",
        ],
    },
    {
        "id": "operator",
        "description": "Can create and operate runs, missions, and human approvals.",
        "permissions": [
            "runs:create",
            "runs:read",
            "runs:cancel",
            "missions:create",
            "missions:read",
            "missions:cancel",
            "permissions:resolve",
            "executors:read",
            "workers:read",
            "artifacts:read",
        ],
    },
    {
        "id": "auditor",
        "description": "Can read events, artifacts, metrics, and audit bundles.",
        "permissions": [
            "runs:read",
            "missions:read",
            "events:read",
            "artifacts:read",
            "executors:read",
            "workers:read",
            "cost:read",
            "ops:read",
            "access:read",
        ],
    },
]


class AccessManager:
    def __init__(self, store: RunStore, default_principal: str = "single-tenant-operator"):
        self.store = store
        self.default_principal = default_principal
        self.store.ensure_access_project("default", "Default")

    def policy(self, headers: Any | None = None) -> dict[str, Any]:
        principal = self.principal_from_headers(headers)
        roles = ROLE_DEFINITIONS
        scopes = sorted({permission for role in roles for permission in role["permissions"]})
        projects = [project.to_dict() for project in self.store.list_access_projects()]
        tokens = [token.to_dict() for token in self.store.list_api_tokens()]
        return {
            "mode": "single-tenant-rbac-foundation",
            "current_principal": {
                "id": principal,
                "display_name": principal,
                "roles": ["owner"],
            },
            "roles": roles,
            "scopes": scopes,
            "projects": projects,
            "tokens": tokens,
            "audit": {
                "auth_boundary": "nginx basic auth plus runtime bearer token or API token",
                "token_storage": "api tokens are stored as sha256 hashes and shown once",
                "user_header": "x-remote-user or x-forwarded-user when configured",
                "status": "foundation only; external IAM can replace this manager",
                "generated_at": utc_now(),
            },
        }

    def principal_from_headers(self, headers: Any | None) -> str:
        if headers:
            principal = headers.get("x-remote-user") or headers.get("x-forwarded-user")
            if principal:
                return str(principal)
        return self.default_principal

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.store.create_access_project(payload).to_dict()

    def list_projects(self) -> dict[str, Any]:
        return {
            "projects": [project.to_dict() for project in self.store.list_access_projects()]
        }

    def create_token(
        self,
        payload: dict[str, Any],
        headers: Any | None = None,
    ) -> dict[str, Any]:
        plain_token = f"cat_{secrets.token_urlsafe(32)}"
        token = ApiToken.create(
            payload,
            plain_token=plain_token,
            default_principal=self.principal_from_headers(headers),
        )
        created = self.store.create_api_token(token).to_dict()
        return {**created, "token": plain_token}

    def list_tokens(self) -> dict[str, Any]:
        return {"tokens": [token.to_dict() for token in self.store.list_api_tokens()]}

    def revoke_token(self, token_id: str) -> dict[str, Any]:
        return self.store.revoke_api_token(token_id).to_dict()

    def authenticate_bearer(self, authorization: str | None) -> dict[str, Any] | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            return None
        matched = self.store.find_api_token_by_hash(hash_token(token))
        if matched is None:
            return None
        return {
            "principal_id": matched.principal_id,
            "token_id": matched.token_id,
            "project_id": matched.project_id,
            "scopes": matched.scopes,
        }
