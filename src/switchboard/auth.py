"""Bearer tokens and a small OAuth 2.1 issuer.

The resource server accepts HMAC tokens minted here (`sb1.`), HS256 JWTs signed
with the shared secret, and RS256 JWTs whose public key is on the JWKS
document. The built-in issuer supports `client_credentials`, authorization-code
+ PKCE (S256), and rotating refresh tokens for one pre-registered client.
Demo mode auto-approves that client: there is no end-user password check.
Do not point a production IdP at the demo secret.
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

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from mcp.server.auth.provider import AccessToken

from switchboard.errors import SwitchboardError
from switchboard.settings import ALL_SCOPES, Settings

ACCESS_TTL = 3600
REFRESH_TTL = 14 * 24 * 3600
_RSA: dict[str, Any] = {}


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
    ttl_seconds: int = ACCESS_TTL,
    audience: str | None = None,
    nbf: int | None = None,
) -> str:
    unknown = sorted(set(scopes) - set(ALL_SCOPES))
    if unknown:
        raise SwitchboardError("invalid_scope", "Unknown scope requested.", {"scopes": unknown})
    now = int(time.time())
    payload = {
        "aud": audience or settings.resource_url,
        "client_id": client_id,
        "exp": now + ttl_seconds,
        "iat": now,
        "nbf": now if nbf is None else nbf,
        "iss": settings.issuer_url,
        "scope": " ".join(scopes),
        "sub": subject,
        "tenant_id": tenant_id,
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(settings.signing_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"sb1.{body}.{sig}"


def decode_token(token: str, settings: Settings) -> dict[str, Any] | None:
    if not token or token.count(".") < 2:
        return None
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
    alg = header.get("alg")
    signing_input = f"{header_b64}.{body_b64}".encode()
    try:
        signature = _b64decode(sig_b64)
    except (ValueError, TypeError):
        return None
    if alg == "HS256":
        expected = hmac.new(settings.signing_secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            return None
    elif alg == "RS256":
        if header.get("kid") != _kid(settings):
            return None
        try:
            _public_key(settings).verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except Exception:
            return None
    else:
        return None
    if not _claims_ok(claims, settings):
        return None
    return claims


def _claims_ok(claims: dict[str, Any], settings: Settings) -> bool:
    aud = claims.get("aud")
    if isinstance(aud, list):
        aud_ok = settings.resource_url in aud
    else:
        aud_ok = aud == settings.resource_url
    if not aud_ok:
        return False
    now = int(time.time())
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp <= now:
        return False
    nbf = claims.get("nbf")
    if nbf is not None and (not isinstance(nbf, int) or nbf > now):
        return False
    if not claims.get("tenant_id") or not isinstance(claims.get("scope"), str):
        return False
    return True


def _rsa_key(settings: Settings):
    pem = settings.jwt_private_key_pem.strip()
    # One generated key per process when no PEM is configured. A PEM is keyed by its text.
    cache_key = pem or "generated"
    cached = _RSA.get(cache_key)
    if cached is None:
        if pem:
            cached = serialization.load_pem_private_key(pem.encode(), password=None)
        else:
            cached = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _RSA[cache_key] = cached
    return cached


def _public_key(settings: Settings):
    return _rsa_key(settings).public_key()


def _kid(settings: Settings) -> str:
    public = _public_key(settings).public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return _b64(hashlib.sha256(public).digest()[:8])


def jwks(settings: Settings) -> dict[str, Any]:
    numbers = _public_key(settings).public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": _kid(settings),
                "n": _int_b64(numbers.n),
                "e": _int_b64(numbers.e),
            }
        ]
    }


def _int_b64(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return _b64(value.to_bytes(length, "big"))


def issue_rs256(
    settings: Settings,
    *,
    subject: str,
    tenant_id: str,
    scopes: list[str],
    client_id: str,
    ttl_seconds: int = ACCESS_TTL,
) -> str:
    unknown = sorted(set(scopes) - set(ALL_SCOPES))
    if unknown:
        raise SwitchboardError("invalid_scope", "Unknown scope requested.", {"scopes": unknown})
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": _kid(settings)}
    payload = {
        "aud": settings.resource_url,
        "client_id": client_id,
        "exp": now + ttl_seconds,
        "iat": now,
        "nbf": now,
        "iss": settings.issuer_url,
        "scope": " ".join(scopes),
        "sub": subject,
        "tenant_id": tenant_id,
    }
    header_b64 = _b64(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    body_b64 = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{header_b64}.{body_b64}".encode()
    signature = _rsa_key(settings).sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_b64}.{body_b64}.{_b64(signature)}"


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
    """In-memory authorization server for the one configured client.

    Refresh tokens are opaque, stored here, and rotated on every use. They do
    not survive a process restart.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._codes: dict[str, dict[str, Any]] = {}
        self._refresh: dict[str, dict[str, Any]] = {}

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
            "grant_types_supported": ["authorization_code", "client_credentials", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "scopes_supported": list(ALL_SCOPES),
            "jwks_uri": f"{base}/.well-known/jwks.json",
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
        elif grant == "refresh_token":
            presented = form.get("refresh_token", "")
            record = self._refresh.pop(presented, None)
            now = int(time.time())
            if record is None or record["exp"] <= now:
                raise SwitchboardError("invalid_grant", "Refresh token is invalid or expired")
            if record["client_id"] != client_id:
                raise SwitchboardError("invalid_grant", "Refresh token does not match this client")
            if record["aud"] != self.settings.resource_url:
                raise SwitchboardError("invalid_target", "Refresh token audience does not match this server")
            tenant_id = record["tenant_id"]
            scopes = record["scopes"]
            subject = record["subject"]
        else:
            raise SwitchboardError(
                "unsupported_grant_type",
                "Use authorization_code, client_credentials, or refresh_token",
            )
        if form.get("resource") and form["resource"] != self.settings.resource_url:
            raise SwitchboardError("invalid_target", "resource does not match this server")
        return self._pair(subject=subject, tenant_id=tenant_id, scopes=scopes, client_id=client_id)

    def _pair(self, *, subject: str, tenant_id: str, scopes: list[str], client_id: str) -> dict[str, Any]:
        access = issue_rs256(
            self.settings,
            subject=subject,
            tenant_id=tenant_id,
            scopes=scopes,
            client_id=client_id,
            ttl_seconds=ACCESS_TTL,
        )
        refresh = "sbr." + secrets.token_urlsafe(32)
        now = int(time.time())
        self._refresh[refresh] = {
            "subject": subject,
            "tenant_id": tenant_id,
            "scopes": scopes,
            "client_id": client_id,
            "aud": self.settings.resource_url,
            "exp": now + REFRESH_TTL,
        }
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL,
            "refresh_token": refresh,
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
