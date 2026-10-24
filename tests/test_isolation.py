"""Client A cannot read client B through tools or rendered prompts."""

import json

import pytest

from switchboard.errors import SwitchboardError
from tests.conftest import principal

FOREIGN_IDS = [
    ("crm_get_contact", {"contact_id": "con_sable_helen"}),
    ("crm_get_contact", {"contact_id": "con_bright_anita"}),
    ("crm_get_deal", {"deal_id": "deal_sable_catering"}),
    ("crm_get_deal", {"deal_id": "deal_bright_recall"}),
    ("docs_fetch", {"page_id": "page_sable_voice"}),
    ("docs_fetch", {"page_id": "page_bright_claims"}),
    ("projects_get_task", {"task_id": "task_sable_photo"}),
    ("projects_get_task", {"task_id": "task_bright_copy"}),
    (
        "ads_campaign_metrics",
        {
            "start_date": "2026-03-09",
            "end_date": "2026-03-15",
            "platform": "google",
            "campaign_id": "cmp_sable_local",
        },
    ),
    (
        "ads_campaign_metrics",
        {
            "start_date": "2026-03-10",
            "end_date": "2026-03-10",
            "platform": "google",
            "campaign_id": "cmp_bright_zero",
        },
    ),
]

SEARCHES = [
    ("crm_search_contacts", {"query": "Helen Cho"}, "helen.cho@sableandrye.com"),
    ("crm_search_contacts", {"query": "Anita Deshmukh"}, "anita.deshmukh@brightlinedental.com"),
    ("crm_search_deals", {"query": "catering"}, "deal_sable_catering"),
    ("crm_search_deals", {"query": "recall"}, "deal_bright_recall"),
    ("docs_search", {"query": "Bay View"}, "page_sable_voice"),
    ("docs_search", {"query": "pain-free"}, "page_bright_claims"),
    ("projects_list_tasks", {}, "task_sable_photo"),
    ("ads_list_campaigns", {"platform": "all"}, "cmp_sable_local"),
]


@pytest.mark.parametrize(("name", "args"), FOREIGN_IDS)
async def test_foreign_id_is_not_found(runtime, name, args):
    with pytest.raises(SwitchboardError) as caught:
        await runtime.gateway.invoke(name, args, principal=principal("halden-bikes"))
    assert caught.value.code == "not_found"


@pytest.mark.parametrize(("name", "args", "needle"), SEARCHES)
async def test_search_does_not_return_another_clients_rows(runtime, name, args, needle):
    result = await runtime.gateway.invoke(name, args, principal=principal("halden-bikes"))
    blob = json.dumps(result["data"])
    assert needle not in blob


async def test_prompts_do_not_embed_another_clients_records(runtime):
    from mcp.client.client import Client

    from switchboard.principal import reset_principal, set_principal

    token = set_principal(principal("halden-bikes"))
    try:
        async with Client(runtime.server, mode="legacy") as client:
            weekly = await client.get_prompt(
                "weekly_client_ads_report",
                {"client_name": "Halden Bikes", "start_date": "2026-03-09", "end_date": "2026-03-15"},
            )
            lead = await client.get_prompt(
                "new_lead_to_crm_and_task",
                {
                    "lead_name": "Chris Adeyemi",
                    "email": "chris.adeyemi@haldenbikes.com",
                    "company": "Halden Bikes",
                    "task_title": "Send the fit guide",
                    "assignee_email": "jonah.park@fernwood.studio",
                },
            )
            docs = await client.get_prompt("answer_from_docs", {"question": "Can we name the Harlow price?"})
    finally:
        reset_principal(token)
    blob = json.dumps(
        [
            weekly.messages[0].content.text,
            lead.messages[0].content.text,
            docs.messages[0].content.text,
        ]
    )
    assert "helen.cho@sableandrye.com" not in blob
    assert "page_bright_claims" not in blob
    assert "pain-free" not in blob or "Can we name the Harlow price?" in blob
    assert "confirm false" in lead.messages[0].content.text
