import json

import httpx
import pytest

from switchboard.live import LiveCrm, google_rows_from_payload, meta_rows_from_payload
from switchboard.settings import Settings
from tests.conftest import principal


def test_google_and_meta_parsers_feed_the_same_row_shape():
    google = google_rows_from_payload(
        [
            {
                "results": [
                    {
                        "campaign": {"id": "99", "name": "Brand"},
                        "segments": {"date": "2026-03-02"},
                        "metrics": {
                            "impressions": "1000",
                            "clicks": "20",
                            "costMicros": "50000000",
                            "conversions": 1,
                        },
                    }
                ]
            }
        ],
        "halden-bikes",
    )
    assert google[0].spend_cents == 5000
    assert google[0].campaign_id == "gads_99"
    meta = meta_rows_from_payload(
        {
            "data": [
                {
                    "campaign_id": "55",
                    "campaign_name": "Prospecting",
                    "date_start": "2026-03-02",
                    "impressions": "1000",
                    "clicks": "20",
                    "spend": "12.50",
                    "actions": [{"action_type": "purchase", "value": "2"}],
                }
            ]
        },
        "halden-bikes",
    )
    assert meta[0].spend_cents == 1250
    assert meta[0].conversions == 2
    assert meta[0].campaign_id == "meta_55"


@pytest.mark.asyncio
async def test_hubspot_search_sends_the_tenant_filter():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "1",
                        "properties": {
                            "email": "mara.ellison@haldenbikes.com",
                            "firstname": "Mara",
                            "lastname": "Ellison",
                            "company": "Halden Bikes",
                            "phone": "",
                            "switchboard_tenant": "halden-bikes",
                        },
                    },
                    {
                        "id": "2",
                        "properties": {
                            "email": "other@example.com",
                            "firstname": "Other",
                            "lastname": "Person",
                            "switchboard_tenant": "sable-rye",
                        },
                    },
                ]
            },
        )

    settings = Settings(hubspot_access_token="hubspot-test-token", force_mock=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        crm = LiveCrm(settings, client)
        rows = await crm.search_contacts("halden-bikes", "Mara", 10)
    assert seen["url"].endswith("/crm/v3/objects/contacts/search")
    assert seen["auth"] == "Bearer hubspot-test-token"
    filters = seen["body"]["filterGroups"][0]["filters"]
    assert {"propertyName": "switchboard_tenant", "operator": "EQ", "value": "halden-bikes"} in filters
    assert [row["id"] for row in rows] == ["1"]


@pytest.mark.asyncio
async def test_mcp_conformance_and_http_auth(runtime):
    from mcp.client.client import Client

    from switchboard.auth import issue_token
    from switchboard.principal import reset_principal, set_principal

    token = set_principal(principal())
    try:
        async with Client(runtime.server, mode="legacy") as client:
            tools = await client.list_tools()
            templates = await client.list_resource_templates()
            prompts = await client.list_prompts()
            result = await client.call_tool("crm_search_contacts", {"query": "Mara"})
            prompt = await client.get_prompt(
                "answer_from_docs",
                {"question": "Can we say pain-free?"},
            )
    finally:
        reset_principal(token)

    names = {tool.name for tool in tools.tools}
    assert names == set(runtime.registry.tools)
    assert len(names) >= 19
    assert all("untrusted data" in (tool.description or "") for tool in tools.tools)
    assert len(templates.resource_templates) >= 5
    assert {item.name for item in prompts.prompts} == {
        "weekly_client_ads_report",
        "new_lead_to_crm_and_task",
        "answer_from_docs",
    }
    assert result.is_error is False
    body = json.loads(result.content[0].text)
    assert body["data"]["contacts"][0]["last_name"] == "Ellison"
    assert prompt.messages

    transport = httpx.ASGITransport(app=runtime.app)
    async with runtime.app.router.lifespan_context(runtime.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            missing = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            assert missing.status_code == 401
            bad = await http.post(
                "/mcp",
                headers={"Authorization": "Bearer not-a-token"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert bad.status_code == 401
            metadata = await http.get("/.well-known/oauth-protected-resource/mcp")
            assert metadata.status_code == 200
            assert "authorization_servers" in metadata.json()
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
            assert issued.status_code == 200
            access = issued.json()["access_token"]
            bearer = issue_token(
                runtime.settings,
                subject="elena.voss@northline.studio",
                tenant_id="halden-bikes",
                scopes=["crm.read", "ads.read"],
                client_id="northline-console",
            )
            assert access
            health = await http.get("/health")
            assert health.json()["status"] == "ok"
            authed = await http.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {bearer}",
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
                        "clientInfo": {"name": "switchboard-conformance", "version": "0.1.0"},
                    },
                },
            )
            assert authed.status_code == 200, authed.text[:500]
            console = await http.get("/console/workspaces")
            assert console.status_code == 200
            assert len(console.json()["workspaces"]) == 3
