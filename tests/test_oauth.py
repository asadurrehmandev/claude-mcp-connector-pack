"""Bearer rejection, refresh rotation, audience, and JWKS."""

import time

import pytest

from switchboard.auth import ACCESS_TTL, decode_token, issue_rs256, issue_token, jwks
from switchboard.errors import SwitchboardError
from switchboard.settings import Settings


def _settings() -> Settings:
    return Settings(env="demo", public_base_url="http://127.0.0.1:4317")


@pytest.mark.parametrize(
    "token",
    ["", "nope", "sb1.onlyone", "eyJhbGciOiJub25lIn0.eyJhdWQiOiJ4In0.sig", "Bearer"],
)
def test_missing_or_garbage_token_is_rejected(token):
    assert decode_token(token, _settings()) is None


def test_expired_hmac_token_is_rejected():
    settings = _settings()
    token = issue_token(
        settings,
        subject="elena.voss@northline.studio",
        tenant_id="halden-bikes",
        scopes=["crm.read"],
        client_id="northline-console",
        ttl_seconds=-30,
    )
    assert decode_token(token, settings) is None


def test_wrong_audience_is_rejected():
    settings = _settings()
    token = issue_token(
        settings,
        subject="elena.voss@northline.studio",
        tenant_id="halden-bikes",
        scopes=["crm.read"],
        client_id="northline-console",
        audience="https://other.example/mcp",
    )
    assert decode_token(token, settings) is None


def test_not_yet_valid_token_is_rejected():
    settings = _settings()
    token = issue_token(
        settings,
        subject="elena.voss@northline.studio",
        tenant_id="halden-bikes",
        scopes=["crm.read"],
        client_id="northline-console",
        nbf=int(time.time()) + 600,
    )
    assert decode_token(token, settings) is None


def test_rs256_roundtrip_and_wrong_audience_claim(runtime):
    token = issue_rs256(
        runtime.settings,
        subject="elena.voss@northline.studio",
        tenant_id="halden-bikes",
        scopes=["ads.read"],
        client_id="northline-console",
        ttl_seconds=ACCESS_TTL,
    )
    claims = decode_token(token, runtime.settings)
    assert claims is not None
    assert claims["aud"] == runtime.settings.resource_url
    header = token.split(".")[0]
    import base64
    import json

    pad = "=" * (-len(header) % 4)
    assert json.loads(base64.urlsafe_b64decode(header + pad))["alg"] == "RS256"
    document = jwks(runtime.settings)
    assert document["keys"][0]["kty"] == "RSA"
    assert document["keys"][0]["alg"] == "RS256"
    tampered = token[:-4] + ("A" if token[-4] != "A" else "B") + token[-3:]
    assert decode_token(tampered, runtime.settings) is None


def test_refresh_rotates_and_replay_fails(runtime):
    issuer = runtime.oauth
    first = issuer.exchange(
        {
            "grant_type": "client_credentials",
            "tenant_id": "halden-bikes",
            "scope": "crm.read",
            "client_id": runtime.settings.oauth_client_id,
            "client_secret": runtime.settings.oauth_client_secret,
        },
        None,
    )
    assert decode_token(first["access_token"], runtime.settings)["tenant_id"] == "halden-bikes"
    assert first["expires_in"] == ACCESS_TTL
    refreshed = issuer.exchange(
        {
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": runtime.settings.oauth_client_id,
            "client_secret": runtime.settings.oauth_client_secret,
        },
        None,
    )
    assert refreshed["refresh_token"] != first["refresh_token"]
    assert decode_token(refreshed["access_token"], runtime.settings)["scope"] == "crm.read"
    with pytest.raises(SwitchboardError) as replay:
        issuer.exchange(
            {
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": runtime.settings.oauth_client_id,
                "client_secret": runtime.settings.oauth_client_secret,
            },
            None,
        )
    assert replay.value.code == "invalid_grant"


def test_expired_refresh_token_is_rejected(runtime):
    issuer = runtime.oauth
    minted = issuer.exchange(
        {
            "grant_type": "client_credentials",
            "tenant_id": "sable-rye",
            "scope": "docs.read",
            "client_id": runtime.settings.oauth_client_id,
            "client_secret": runtime.settings.oauth_client_secret,
        },
        None,
    )
    issuer._refresh[minted["refresh_token"]]["exp"] = int(time.time()) - 5
    with pytest.raises(SwitchboardError) as caught:
        issuer.exchange(
            {
                "grant_type": "refresh_token",
                "refresh_token": minted["refresh_token"],
                "client_id": runtime.settings.oauth_client_id,
                "client_secret": runtime.settings.oauth_client_secret,
            },
            None,
        )
    assert caught.value.code == "invalid_grant"


def test_refresh_audience_must_match_this_server(runtime):
    issuer = runtime.oauth
    minted = issuer.exchange(
        {
            "grant_type": "client_credentials",
            "tenant_id": "halden-bikes",
            "scope": "crm.read",
            "client_id": runtime.settings.oauth_client_id,
            "client_secret": runtime.settings.oauth_client_secret,
        },
        None,
    )
    issuer._refresh[minted["refresh_token"]]["aud"] = "https://other.example/mcp"
    with pytest.raises(SwitchboardError) as caught:
        issuer.exchange(
            {
                "grant_type": "refresh_token",
                "refresh_token": minted["refresh_token"],
                "client_id": runtime.settings.oauth_client_id,
                "client_secret": runtime.settings.oauth_client_secret,
            },
            None,
        )
    assert caught.value.code == "invalid_target"


@pytest.mark.asyncio
async def test_jwks_and_rs256_access_token_on_http(runtime):
    import httpx

    transport = httpx.ASGITransport(app=runtime.app)
    async with runtime.app.router.lifespan_context(runtime.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            keys = await http.get("/.well-known/jwks.json")
            assert keys.status_code == 200
            assert keys.json()["keys"][0]["kid"]
            metadata = await http.get("/.well-known/oauth-authorization-server")
            assert "refresh_token" in metadata.json()["grant_types_supported"]
            issued = await http.post(
                "/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": runtime.settings.oauth_client_id,
                    "client_secret": runtime.settings.oauth_client_secret,
                    "tenant_id": "halden-bikes",
                    "scope": "crm.read",
                },
            )
            body = issued.json()
            assert body["refresh_token"].startswith("sbr.")
            authed = await http.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {body['access_token']}",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "switchboard-oauth", "version": "0.1.0"},
                    },
                },
            )
            assert authed.status_code == 200, authed.text[:400]
