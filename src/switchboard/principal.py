"""Who is calling, and which workspace they are allowed to see."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from switchboard.errors import SwitchboardError
from switchboard.settings import Settings

_override: ContextVar["Principal | None"] = ContextVar("switchboard_principal", default=None)


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    scopes: frozenset[str]
    subject: str
    client_id: str

    def allows(self, scope: str) -> bool:
        return scope in self.scopes


def set_principal(principal: Principal | None):
    return _override.set(principal)


def reset_principal(token) -> None:
    _override.reset(token)


def principal_from_access_token(token: AccessToken) -> Principal:
    tenant_id = str((token.claims or {}).get("tenant_id") or "")
    if not tenant_id:
        raise SwitchboardError("unauthenticated", "Token is missing tenant_id")
    return Principal(
        tenant_id=tenant_id,
        scopes=frozenset(token.scopes),
        subject=token.subject or "",
        client_id=token.client_id,
    )


def resolve_principal(settings: Settings) -> Principal:
    current = _override.get()
    if current is not None:
        return current
    token = get_access_token()
    if token is not None:
        return principal_from_access_token(token)
    if settings.allow_env_principal and settings.tenant_id:
        return Principal(
            tenant_id=settings.tenant_id,
            scopes=frozenset(settings.scopes),
            subject=settings.actor,
            client_id="stdio",
        )
    raise SwitchboardError("unauthenticated", "No workspace credential on this request.")
