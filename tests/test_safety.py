import json
import logging

import pytest

from switchboard.auth import OAuthIssuer, decode_token, issue_token, pkce_s256
from switchboard.constants import INJECTION_NOTE, RESULT_NOTICE
from switchboard.errors import SwitchboardError
from switchboard.ratelimit import RateLimiter
from switchboard.redact import redact_text
from switchboard.runtime import build_runtime
from switchboard.settings import ALL_SCOPES, Settings
from tests.conftest import principal


def test_redact_secrets_and_pii():
    raw = (
        "Bearer sb1.aaaa.bbbb email mara.ellison@haldenbikes.com "
        "phone 612-555-0148 sk-live-abcdefghij ssn 123-45-6789 password=hunter2"
    )
    cleaned = redact_text(raw)
    assert "sb1.aaaa.bbbb" not in cleaned
    assert "mara.ellison@haldenbikes.com" not in cleaned
    assert "612-555-0148" not in cleaned
    assert "sk-live-abcdefghij" not in cleaned
    assert "123-45-6789" not in cleaned
    assert "hunter2" not in cleaned
    assert "m***@haldenbikes.com" in cleaned


def test_token_roundtrip_and_rejection(runtime):
    token = issue_token(
        runtime.settings,
        subject="elena.voss@northline.studio",
        tenant_id="halden-bikes",
        scopes=["crm.read"],
        client_id="northline-console",
        ttl_seconds=60,
    )
    claims = decode_token(token, runtime.settings)
    assert claims is not None
    assert claims["tenant_id"] == "halden-bikes"
    assert decode_token(token + "x", runtime.settings) is None
    expired = issue_token(
        runtime.settings,
        subject="elena",
        tenant_id="halden-bikes",
        scopes=["crm.read"],
        client_id="northline-console",
        ttl_seconds=-10,
    )
    assert decode_token(expired, runtime.settings) is None


def test_pkce_exchange_and_bad_redirect(runtime):
    verifier = "b" * 50
    issuer = OAuthIssuer(runtime.settings)
    with pytest.raises(SwitchboardError) as denied:
        issuer.authorize(
            {
                "response_type": "code",
                "client_id": runtime.settings.oauth_client_id,
                "redirect_uri": "https://evil.example/steal",
                "code_challenge": pkce_s256(verifier),
                "code_challenge_method": "S256",
                "tenant_id": "halden-bikes",
            }
        )
    assert denied.value.code == "invalid_request"
    redirect = issuer.authorize(
        {
            "response_type": "code",
            "client_id": runtime.settings.oauth_client_id,
            "redirect_uri": runtime.settings.oauth_redirect_uris[0],
            "code_challenge": pkce_s256(verifier),
            "code_challenge_method": "S256",
            "tenant_id": "sable-rye",
            "scope": "docs.read",
        }
    )
    code = redirect.split("code=")[1].split("&")[0]
    token = issuer.exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": runtime.settings.oauth_redirect_uris[0],
            "client_id": runtime.settings.oauth_client_id,
            "client_secret": runtime.settings.oauth_client_secret,
        },
        None,
    )
    claims = decode_token(token["access_token"], runtime.settings)
    assert claims["tenant_id"] == "sable-rye"
    assert claims["scope"] == "docs.read"
    minted = issuer.exchange(
        {
            "grant_type": "client_credentials",
            "tenant_id": "brightline-dental",
            "scope": "ads.read",
            "client_id": runtime.settings.oauth_client_id,
            "client_secret": runtime.settings.oauth_client_secret,
        },
        None,
    )
    assert decode_token(minted["access_token"], runtime.settings)["tenant_id"] == "brightline-dental"


def test_rate_limit_blocks_with_retry():
    clock = {"now": 0.0}
    limiter = RateLimiter(2, 10, clock=lambda: clock["now"])
    assert limiter.check("halden-bikes", "crm_search_contacts") is None
    assert limiter.check("halden-bikes", "crm_search_contacts") is None
    retry = limiter.check("halden-bikes", "crm_search_contacts")
    assert retry is not None and retry > 0
    clock["now"] = 10
    assert limiter.check("halden-bikes", "crm_search_contacts") is None


@pytest.mark.asyncio
async def test_scope_rate_confirm_tenant_and_injection(runtime):
    who = principal()
    denied = principal(scopes=["ads.read"])
    with pytest.raises(SwitchboardError) as scope:
        await runtime.gateway.invoke("crm_search_contacts", {"query": "Mara"}, principal=denied)
    assert scope.value.code == "scope_denied"

    limited = build_runtime(
        Settings(
            env="demo",
            audit_path=str(runtime.settings.audit_path) + ".2",
            policy_path=str(runtime.settings.policy_path) + ".2",
            force_mock=True,
            rate_limit=1,
            rate_window_seconds=60,
        )
    )
    await limited.gateway.invoke("ads_list_campaigns", {"platform": "all"}, principal=who)
    with pytest.raises(SwitchboardError) as limited_error:
        await limited.gateway.invoke("ads_list_campaigns", {"platform": "all"}, principal=who)
    assert limited_error.value.code == "rate_limited"

    before = runtime.store.revision
    preview = await runtime.gateway.invoke(
        "crm_upsert_contact",
        {"email": "new.person@haldenbikes.com", "first_name": "New", "last_name": "Person", "confirm": False},
        principal=who,
    )
    assert preview["dry_run"] is True
    assert runtime.store.revision == before
    saved = await runtime.gateway.invoke(
        "crm_upsert_contact",
        {"email": "new.person@haldenbikes.com", "first_name": "New", "last_name": "Person", "confirm": True},
        principal=who,
    )
    assert saved["dry_run"] is False
    assert saved["data"]["contact"]["email"] == "new.person@haldenbikes.com"

    with pytest.raises(SwitchboardError) as isolated:
        await runtime.gateway.invoke(
            "crm_get_contact",
            {"contact_id": "con_halden_mara", "workspace_id": "sable-rye"},
            principal=who,
        )
    assert isolated.value.code == "tenant_isolation"
    with pytest.raises(SwitchboardError) as missing:
        await runtime.gateway.invoke("crm_get_contact", {"contact_id": "con_halden_mara"}, principal=principal("sable-rye"))
    assert missing.value.code == "not_found"
    foreign = await runtime.gateway.invoke("crm_search_contacts", {"query": "Mara"}, principal=principal("sable-rye"))
    assert foreign["data"]["contacts"] == []

    revision_after_write = runtime.store.revision
    fetched = await runtime.gateway.invoke("crm_get_contact", {"contact_id": "con_halden_mara"}, principal=who)
    assert fetched["untrusted"] is True
    assert fetched["notice"] == RESULT_NOTICE
    assert INJECTION_NOTE in json.dumps(fetched["data"])
    assert INJECTION_NOTE not in fetched["notice"]
    assert runtime.store.revision == revision_after_write
