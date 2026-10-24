import json
import logging

import pytest

from switchboard.constants import HARDENING, INJECTION_DOC
from switchboard.errors import SwitchboardError
from switchboard.runtime import build_runtime
from switchboard.settings import Settings
from tests.conftest import principal


@pytest.mark.asyncio
async def test_every_read_tool_and_descriptions(runtime):
    who = principal()
    for spec in runtime.registry.tools.values():
        assert "untrusted data" in (spec.description + HARDENING)

    contacts = await runtime.gateway.invoke("crm_search_contacts", {"query": "Ellison"}, principal=who)
    assert contacts["data"]["contacts"][0]["id"] == "con_halden_mara"
    contact = await runtime.gateway.invoke("crm_get_contact", {"contact_id": "con_halden_mara"}, principal=who)
    assert contact["data"]["notes"]

    deals = await runtime.gateway.invoke("crm_search_deals", {"query": "gravel"}, principal=who)
    assert deals["data"]["deals"][0]["id"] == "deal_halden_gravel"
    deal = await runtime.gateway.invoke("crm_get_deal", {"deal_id": "deal_halden_gravel"}, principal=who)
    assert deal["data"]["deal"]["amount_cents"] == 4_800_000

    campaigns = await runtime.gateway.invoke("ads_list_campaigns", {"platform": "google"}, principal=who)
    assert any(row["id"] == "cmp_halden_exact" for row in campaigns["data"]["campaigns"])
    assert all(row["platform"] == "google" for row in campaigns["data"]["campaigns"])

    pages = await runtime.gateway.invoke(
        "docs_search",
        {"query": "pain-free Brightline claims"},
        principal=principal("brightline-dental"),
    )
    assert pages["data"]["pages"][0]["id"] == "page_bright_claims"
    page = await runtime.gateway.invoke(
        "docs_fetch",
        {"page_id": "page_bright_claims"},
        principal=principal("brightline-dental"),
    )
    assert page["data"]["citation"]["url"].endswith("/claims")
    assert page["data"]["citation"]["source"] == "notion"
    assert INJECTION_DOC in page["data"]["body"]
    assert page["untrusted"] is True

    tasks = await runtime.gateway.invoke("projects_list_tasks", {"status": "open"}, principal=who)
    assert {row["id"] for row in tasks["data"]["tasks"]} >= {"task_halden_landing"}
    task = await runtime.gateway.invoke("projects_get_task", {"task_id": "task_halden_landing"}, principal=who)
    assert task["data"]["task"]["assignee_email"].endswith("@northline.studio")
    members = await runtime.gateway.invoke("projects_list_members", {}, principal=who)
    assert any(row["email"] == "jonah.park@northline.studio" for row in members["data"]["members"])

    channels = await runtime.gateway.invoke("slack_list_channels", {}, principal=who)
    assert channels["data"]["channels"] == [{"name": "#client-halden"}]
    sable = await runtime.gateway.invoke("slack_list_channels", {}, principal=principal("sable-rye"))
    assert sable["data"]["channels"] == [{"name": "#client-sable"}]


@pytest.mark.asyncio
async def test_writes_require_confirm_and_valid_assignee(runtime):
    who = principal()
    before = runtime.store.revision
    dry_deal = await runtime.gateway.invoke(
        "crm_upsert_deal",
        {"deal_name": "Warehouse tour", "amount_cents": 90000, "stage": "lead", "confirm": False},
        principal=who,
    )
    assert dry_deal["dry_run"] is True
    assert runtime.store.revision == before
    deal = await runtime.gateway.invoke(
        "crm_upsert_deal",
        {
            "deal_name": "Warehouse tour",
            "amount_cents": 90000,
            "stage": "lead",
            "contact_id": "con_halden_mara",
            "confirm": True,
        },
        principal=who,
    )
    assert deal["data"]["deal"]["stage"] == "lead"

    dry_note = await runtime.gateway.invoke(
        "crm_log_note",
        {"contact_id": "con_halden_mara", "body": "Price stays $2,450.", "confirm": False},
        principal=who,
    )
    assert dry_note["dry_run"] is True
    note = await runtime.gateway.invoke(
        "crm_log_note",
        {"contact_id": "con_halden_mara", "body": "Price stays $2,450.", "confirm": True},
        principal=who,
    )
    assert note["data"]["note"]["body"] == "Price stays $2,450."

    with pytest.raises(SwitchboardError) as bad_assignee:
        await runtime.gateway.invoke(
            "projects_create_task",
            {"name": "Outside hire", "assignee_email": "stranger@example.com", "confirm": True},
            principal=who,
        )
    assert bad_assignee.value.code == "invalid_assignee"
    assert "Outside hire" not in {task.name for task in runtime.store.tasks.values()}

    created = await runtime.gateway.invoke(
        "projects_create_task",
        {
            "name": "Cut the warehouse film",
            "assignee_email": "Andre.Molina@northline.studio",
            "due_date": "2026-03-21",
            "confirm": True,
        },
        principal=who,
    )
    assert created["data"]["task"]["assignee_email"] == "andre.molina@northline.studio"
    task_id = created["data"]["task"]["id"]
    preview = await runtime.gateway.invoke(
        "projects_update_task",
        {"task_id": task_id, "status": "done", "confirm": False},
        principal=who,
    )
    assert preview["dry_run"] is True
    assert runtime.store.tasks[task_id].status == "open"
    updated = await runtime.gateway.invoke(
        "projects_update_task",
        {"task_id": task_id, "status": "done", "confirm": True},
        principal=who,
    )
    assert updated["data"]["task"]["status"] == "done"

    with pytest.raises(SwitchboardError) as channel:
        await runtime.gateway.invoke(
            "slack_notify",
            {"channel": "#general", "text": "hello", "confirm": True},
            principal=who,
        )
    assert channel.value.code == "channel_not_allowed"
    assert runtime.store.messages == []
    sent = await runtime.gateway.invoke(
        "slack_notify",
        {"channel": "#client-halden", "text": "Readout is in the doc.", "confirm": True},
        principal=who,
    )
    assert sent["data"]["message"]["channel"] == "#client-halden"
    assert len(runtime.store.messages) == 1
    dry_slack = await runtime.gateway.invoke(
        "slack_notify",
        {"channel": "#client-halden", "text": "not sent", "confirm": False},
        principal=who,
    )
    assert dry_slack["dry_run"] is True
    assert len(runtime.store.messages) == 1


@pytest.mark.asyncio
async def test_read_only_default_for_a_new_workspace(tmp_path):
    runtime = build_runtime(
        Settings(
            env="demo",
            audit_path=str(tmp_path / "audit.jsonl"),
            policy_path=str(tmp_path / "policy.json"),
            force_mock=True,
        )
    )
    who = principal("empty-co")
    with pytest.raises(SwitchboardError) as disabled:
        await runtime.gateway.invoke(
            "crm_upsert_contact",
            {"email": "a@b.co", "first_name": "A", "last_name": "B", "confirm": True},
            principal=who,
        )
    assert disabled.value.code == "tool_disabled"
    runtime.policy.set_enabled("empty-co", "crm_upsert_contact", True)
    preview = await runtime.gateway.invoke(
        "crm_upsert_contact",
        {"email": "a@b.co", "first_name": "A", "last_name": "B", "confirm": False},
        principal=who,
    )
    assert preview["dry_run"] is True
    assert "a@b.co" not in {row.email for row in runtime.store.contacts.values()}


def test_audit_redacts_logged_arguments(runtime):
    logger = logging.getLogger("switchboard")
    logger.info("provider key sk-test-SHOULD-NOT-LEAK and bearer sb1.abc.def")
    # The filter rewrites the record; a second handler observes the formatted line.
    lines = []

    class Grab(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    grab = Grab()
    logger.addHandler(grab)
    try:
        logger.info("email %s token %s", "mara.ellison@haldenbikes.com", "sk-test-SHOULD-NOT-LEAK")
    finally:
        logger.removeHandler(grab)
    assert lines
    assert "sk-test-SHOULD-NOT-LEAK" not in lines[0]
    assert "mara.ellison@haldenbikes.com" not in lines[0]


@pytest.mark.asyncio
async def test_audit_record_hides_note_body(runtime):
    await runtime.gateway.invoke(
        "crm_log_note",
        {
            "contact_id": "con_halden_mara",
            "body": "Reach mara.ellison@haldenbikes.com with sk-test-SHOULD-NOT-LEAK",
            "confirm": False,
        },
        principal=principal(),
    )
    blob = runtime.audit.dump_text()
    assert "sk-test-SHOULD-NOT-LEAK" not in blob
    assert "mara.ellison@haldenbikes.com" not in blob
    assert json.loads(blob)[-1]["result_summary"] == "dry_run"
