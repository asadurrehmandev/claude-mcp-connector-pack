"""Bearer tokens and a small OAuth 2.1 issuer.

The resource server accepts HMAC tokens minted here (`sb1.`) and HS256 JWTs
signed with the same secret. The built-in issuer supports `client_credentials`
and authorization-code + PKCE (S256) for one pre-registered client. Demo mode
auto-approves that client: there is no end-user password check. Do not point
a production IdP at the demo secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from mcp.server.auth.provider import AccessToken

from switchboard.errors import SwitchboardError
from switchboard.settings import ALL_SCOPES, Settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def issue_token(
    settings: Settings,
    *,
    subject: str,
    tenant_id: str,
    scopes: list[str],
    client_id: str,
    ttl_seconds: int = 3600,
) -> str:
    unknown = sorted(set(scopes) - set(ALL_SCOPES))
    if unknown:
        raise SwitchboardError("invalid_scope", "Unknown scope requested.", {"scopes": unknown})
    now = int(time.time())
    payload = {
        "aud": settings.resource_url,
        "client_id": client_id,
        "exp": now + ttl_seconds,
        "iat": now,
        "iss": settings.issuer_url,
        "scope": " ".join(scopes),
        "sub": subject,
        "tenant_id": tenant_id,
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(settings.signing_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"sb1.{body}.{sig}"


def decode_token(token: str, settings: Settings) -> dict[str, Any] | None:
    if token.startswith("sb1."):
        return _decode_sb1(token, settings)
    if token.count(".") == 2 and token.startswith("eyJ"):
        return _decode_jwt(token, settings)
    return None


def _decode_sb1(token: str, settings: Settings) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    _, body, sig = parts
    expected = _b64(hmac.new(settings.signing_secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        claims = json.loads(_b64decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not _claims_ok(claims, settings):
        return None
    return claims


def _decode_jwt(token: str, settings: Settings) -> dict[str, Any] | None:
    header_b64, body_b64, sig_b64 = token.split(".")
    try:
        header = json.loads(_b64decode(header_b64))
        claims = json.loads(_b64decode(body_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if header.get("alg") != "HS256":
        return None
    signing_input = f"{header_b64}.{body_b64}".encode()
    expected = _b64(hmac.new(settings.signing_secret.encode(), signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig_b64):
        return None
    if not _claims_ok(claims, settings):
        return None
    return claims


def _claims_ok(claims: dict[str, Any], settings: Settings) -> bool:
    if claims.get("aud") != settings.resource_url:
        return False
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return False
    if not claims.get("tenant_id") or not isinstance(claims.get("scope"), str):
        return False
    return True


class SwitchboardTokenVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def verify_token(self, token: str) -> AccessToken | None:
        claims = decode_token(token, self.settings)
        if claims is None:
            return None
        scopes = [part for part in str(claims["scope"]).split(" ") if part]
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or "switchboard"),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.settings.resource_url,
            subject=str(claims.get("sub") or ""),
            claims={"iss": claims.get("iss") or self.settings.issuer_url, "tenant_id": claims["tenant_id"]},
        )


def pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64(digest)


class OAuthIssuer:
    """In-memory authorization server for the one configured client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._codes: dict[str, dict[str, Any]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.oauth_client_id and self.settings.oauth_client_secret)

    def authorization_server_metadata(self) -> dict[str, Any]:
        base = self.settings.issuer_url
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "client_credentials"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "scopes_supported": list(ALL_SCOPES),
        }

    def authorize(self, params: dict[str, str]) -> str:
        self._require_enabled()
        if params.get("response_type") != "code":
            raise SwitchboardError("invalid_request", "response_type must be code")
        if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
            raise SwitchboardError("invalid_request", "PKCE S256 code_challenge is required")
        if params.get("client_id") != self.settings.oauth_client_id:
            raise SwitchboardError("unauthorized_client", "Unknown client_id")
        redirect = params.get("redirect_uri", "")
        if redirect not in self.settings.oauth_redirect_uris:
            raise SwitchboardError("invalid_request", "redirect_uri is not registered for this client")
        tenant_id = params.get("tenant_id", "").strip()
        if not tenant_id:
            raise SwitchboardError("invalid_request", "tenant_id is required")
        scopes = self._scopes(params.get("scope", ""))
        resource = params.get("resource") or self.settings.resource_url
        if resource != self.settings.resource_url:
            raise SwitchboardError("invalid_target", "resource does not match this server")
        code = secrets.token_urlsafe(24)
        self._codes[code] = {
            "tenant_id": tenant_id,
            "scopes": scopes,
            "redirect_uri": redirect,
            "code_challenge": params["code_challenge"],
            "exp": int(time.time()) + 300,
            "client_id": params["client_id"],
        }
        state = params.get("state")
        query = {"code": code}
        if state:
            query["state"] = state
        return _join(redirect, query)

    def exchange(self, form: dict[str, str], basic: tuple[str, str] | None) -> dict[str, Any]:
        self._require_enabled()
        client_id, client_secret = self._client(form, basic)
        grant = form.get("grant_type", "")
        if grant == "client_credentials":
            tenant_id = form.get("tenant_id", "").strip()
            if not tenant_id:
                raise SwitchboardError("invalid_request", "tenant_id is required")
            scopes = self._scopes(form.get("scope", ""))
            subject = form.get("subject") or client_id
        elif grant == "authorization_code":
            code = form.get("code", "")
            record = self._codes.pop(code, None)
            if record is None or record["exp"] < int(time.time()):
                raise SwitchboardError("invalid_grant", "Authorization code is invalid or expired")
            if record["client_id"] != client_id or record["redirect_uri"] != form.get("redirect_uri"):
                raise SwitchboardError("invalid_grant", "Authorization code does not match this client")
            verifier = form.get("code_verifier", "")
            if pkce_s256(verifier) != record["code_challenge"]:
                raise SwitchboardError("invalid_grant", "PKCE verification failed")
            tenant_id = record["tenant_id"]
            scopes = record["scopes"]
            subject = form.get("subject") or client_id
        else:
            raise SwitchboardError("unsupported_grant_type", "Use authorization_code or client_credentials")
        if form.get("resource") and form["resource"] != self.settings.resource_url:
            raise SwitchboardError("invalid_target", "resource does not match this server")
        token = issue_token(
            self.settings,
            subject=subject,
            tenant_id=tenant_id,
            scopes=scopes,
            client_id=client_id,
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(scopes),
        }

    def _client(self, form: dict[str, str], basic: tuple[str, str] | None) -> tuple[str, str]:
        if basic is not None:
            client_id, client_secret = basic
        else:
            client_id = form.get("client_id", "")
            client_secret = form.get("client_secret", "")
        id_ok = hmac.compare_digest(client_id, self.settings.oauth_client_id)
        secret_ok = hmac.compare_digest(client_secret, self.settings.oauth_client_secret)
        if not (id_ok and secret_ok):
            raise SwitchboardError("invalid_client", "Client authentication failed")
        return client_id, client_secret

    def _scopes(self, raw: str) -> list[str]:
        scopes = [part for part in raw.split(" ") if part] or list(ALL_SCOPES)
        unknown = sorted(set(scopes) - set(ALL_SCOPES))
        if unknown:
            raise SwitchboardError("invalid_scope", "Unknown scope requested.", {"scopes": unknown})
        return scopes

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise SwitchboardError("not_configured", "This process has no OAuth client configured")


def _join(url: str, query: dict[str, str]) -> str:
    parsed = urlparse(url)
    extra = urlencode(query)
    joined = f"{parsed.query}&{extra}" if parsed.query else extra
    return urlunparse(parsed._replace(query=joined))
