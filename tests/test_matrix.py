"""One happy path, one scope denial, and one invalid call per tool, plus dry-runs."""

import pytest

from switchboard.constants import RESULT_NOTICE
from switchboard.errors import SwitchboardError
from tests.conftest import principal

HAPPY = {
    "crm_search_contacts": {"query": "Mara"},
    "crm_get_contact": {"contact_id": "con_halden_mara"},
    "crm_upsert_contact": {
        "email": "new.person@haldenbikes.com",
        "first_name": "New",
        "last_name": "Person",
        "company": "Halden Bikes",
        "confirm": True,
    },
    "crm_search_deals": {"query": "gravel"},
    "crm_get_deal": {"deal_id": "deal_halden_gravel"},
    "crm_upsert_deal": {"deal_name": "Popup extension", "amount_cents": 10000, "stage": "lead", "confirm": True},
    "crm_log_note": {"contact_id": "con_halden_mara", "body": "Called about the Harlow.", "confirm": True},
    "ads_list_campaigns": {"platform": "google"},
    "ads_campaign_metrics": {
        "start_date": "2026-03-02",
        "end_date": "2026-03-08",
        "platform": "google",
        "campaign_id": "cmp_halden_exact",
    },
    "ads_account_summary": {"start_date": "2026-03-09", "end_date": "2026-03-15", "platform": "all"},
    "docs_search": {"query": "brand voice"},
    "docs_fetch": {"page_id": "page_halden_voice"},
    "projects_list_tasks": {"status": "open"},
    "projects_get_task": {"task_id": "task_halden_landing"},
    "projects_list_members": {},
    "projects_create_task": {
        "name": "Call Mara",
        "assignee_email": "jonah.park@northline.studio",
        "confirm": True,
    },
    "projects_update_task": {"task_id": "task_halden_landing", "status": "review", "confirm": True},
    "slack_list_channels": {},
    "slack_notify": {"channel": "#client-halden", "text": "Readout is drafted.", "confirm": True},
}

INVALID = {
    "crm_search_contacts": {"query": "   "},
    "crm_get_contact": {"contact_id": ""},
    "crm_upsert_contact": {"email": "not-an-email", "first_name": "", "last_name": "", "confirm": True},
    "crm_search_deals": {"query": ""},
    "crm_get_deal": {"deal_id": ""},
    "crm_upsert_deal": {"deal_name": "", "amount_cents": -1, "stage": "nope", "confirm": True},
    "crm_log_note": {"contact_id": "", "body": "", "confirm": True},
    "ads_list_campaigns": {"platform": "tiktok"},
    "ads_campaign_metrics": {"start_date": "2026-03-08T23:00:00-05:00", "end_date": "2026-03-08"},
    "ads_account_summary": {"start_date": "2026-03-15", "end_date": "2026-03-01"},
    "docs_search": {"query": ""},
    "docs_fetch": {"page_id": ""},
    "projects_list_tasks": {"status": "archived"},
    "projects_get_task": {"task_id": ""},
    "projects_list_members": {"bogus": True},
    "projects_create_task": {"name": "", "assignee_email": "nobody@example.com", "confirm": True},
    "projects_update_task": {"task_id": "", "status": "done", "confirm": True},
    "slack_list_channels": {"bogus": True},
    "slack_notify": {"channel": "client-halden", "text": "", "confirm": True},
}

READS = [name for name, args in HAPPY.items() if not args.get("confirm")]
WRITES = [name for name, args in HAPPY.items() if args.get("confirm")]


@pytest.mark.parametrize("name", list(HAPPY))
async def test_happy_path(runtime, name):
    before = runtime.store.revision
    result = await runtime.gateway.invoke(name, HAPPY[name], principal=principal())
    assert result["notice"] == RESULT_NOTICE
    assert result["dry_run"] is False
    assert result["untrusted"] is True
    assert result["data"]
    if name in WRITES:
        assert runtime.store.revision > before
    else:
        assert runtime.store.revision == before


@pytest.mark.parametrize("name", list(HAPPY))
async def test_scope_denied(runtime, name):
    required = runtime.registry.tools[name].scope
    other = "ads.read" if required != "ads.read" else "crm.read"
    with pytest.raises(SwitchboardError) as caught:
        await runtime.gateway.invoke(name, HAPPY[name], principal=principal(scopes=[other]))
    assert caught.value.code == "scope_denied"
    assert caught.value.details["required"] == required


@pytest.mark.parametrize("name", list(INVALID))
async def test_missing_or_invalid_argument(runtime, name):
    with pytest.raises(SwitchboardError) as caught:
        await runtime.gateway.invoke(name, INVALID[name], principal=principal())
    assert caught.value.code == "validation"


@pytest.mark.parametrize("name", WRITES)
async def test_confirm_false_is_a_dry_run(runtime, name):
    args = dict(HAPPY[name])
    args["confirm"] = False
    before = runtime.store.revision
    messages = len(runtime.store.messages)
    result = await runtime.gateway.invoke(name, args, principal=principal())
    assert result["dry_run"] is True
    assert result["data"]["dry_run"] is True
    assert "would_change" in result["data"]
    assert runtime.store.revision == before
    assert len(runtime.store.messages) == messages


@pytest.mark.parametrize("name", READS)
async def test_read_tool_rejects_a_confirm_flag(runtime, name):
    args = dict(HAPPY[name])
    args["confirm"] = True
    with pytest.raises(SwitchboardError) as caught:
        await runtime.gateway.invoke(name, args, principal=principal())
    assert caught.value.code == "validation"


@pytest.mark.parametrize("name", list(HAPPY))
async def test_other_workspace_id_is_rejected(runtime, name):
    args = dict(HAPPY[name])
    args["workspace_id"] = "sable-rye"
    before = runtime.store.revision
    with pytest.raises(SwitchboardError) as caught:
        await runtime.gateway.invoke(name, args, principal=principal("halden-bikes"))
    assert caught.value.code == "tenant_isolation"
    assert runtime.store.revision == before
