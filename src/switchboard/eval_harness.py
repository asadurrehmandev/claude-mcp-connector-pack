"""Held-out planner tasks, leak checks, and unsafe-write checks.

The numbers this writes are measurements of this process. They are not a
language-model benchmark. qa/sealed_tasks.json is hashed; evaluate() rejects
a file whose bytes do not match qa/sealed_tasks.sha256.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from switchboard.constants import INJECTION_NOTE
from switchboard.errors import SwitchboardError
from switchboard.planner import plan_calls, run_turn
from switchboard.principal import Principal
from switchboard.runtime import Runtime
from switchboard.settings import ALL_SCOPES
from switchboard.tools import ToolSpec

_SECRET = "sk-live-SHOULD-NOT-LEAK-9f3a"
_TOKEN = "sb1.payload.signaturevalue"
_EMAIL = "mara.ellison@haldenbikes.com"
_PHONE = "612-555-0148"


def principal_for(tenant_id: str, scopes: list[str] | None = None) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        scopes=frozenset(scopes or ALL_SCOPES),
        subject="elena.voss@fernwood.studio",
        client_id="eval",
    )


def sealed_digest(tasks_path: Path) -> str:
    return hashlib.sha256(tasks_path.read_bytes()).hexdigest()


def assert_sealed(tasks_path: Path) -> str:
    digest = sealed_digest(tasks_path)
    sibling = tasks_path.with_suffix(".sha256")
    recorded = sibling.read_text(encoding="utf-8").split()[0]
    if recorded != digest:
        raise RuntimeError(f"sealed task hash mismatch: file {digest} recorded {recorded}")
    return digest


async def evaluate(runtime: Runtime, tasks_path: Path) -> dict[str, Any]:
    sealed = tasks_path.name == "sealed_tasks.json"
    digest = assert_sealed(tasks_path) if sealed else ""
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    tool_hits = 0
    arg_hits = 0
    failures: list[dict[str, Any]] = []
    categories: dict[str, dict[str, int]] = {}
    for task in tasks:
        category = task.get("category", "unspecified")
        bucket = categories.setdefault(
            category,
            {"tasks": 0, "tool_selection_correct": 0, "argument_correct": 0, "safety_correct": 0},
        )
        bucket["tasks"] += 1
        kind, steps, follow = plan_calls(task["prompt"])
        names = [name for name, _ in steps]
        if follow == "docs_fetch":
            if "docs_search" in names:
                names.insert(names.index("docs_search") + 1, "docs_fetch")
            else:
                names.append("docs_fetch")
        tool_ok = names == task["expect_tools"]
        args_ok = tool_ok and _args_match(steps, task.get("expect_args", []))
        safety_ok = True
        safety_detail = ""
        check = task.get("check") or "none"
        if check != "none":
            safety_ok, safety_detail = await _safety_check(runtime, task, check)
        tool_hits += int(tool_ok)
        arg_hits += int(args_ok)
        bucket["tool_selection_correct"] += int(tool_ok)
        bucket["argument_correct"] += int(args_ok)
        bucket["safety_correct"] += int(safety_ok)
        if not tool_ok or not args_ok or not safety_ok:
            failures.append(
                {
                    "id": task["id"],
                    "category": category,
                    "expect_tools": task["expect_tools"],
                    "actual_tools": names,
                    "actual_args": [dict(arguments) for _, arguments in steps],
                    "tool_ok": tool_ok,
                    "args_ok": args_ok,
                    "safety_ok": safety_ok,
                    "safety_detail": safety_detail,
                }
            )
    leaks = await _leaks(runtime)
    unsafe = await _unsafe(runtime)
    total = len(tasks)
    return {
        "held_out_tasks": total,
        "tool_selection_correct": tool_hits,
        "tool_selection_accuracy": tool_hits / total if total else 0,
        "argument_correct": arg_hits,
        "argument_correctness": arg_hits / total if total else 0,
        "failures": failures,
        "categories": categories,
        "sealed_sha256": digest,
        "leak_cases": leaks["cases"],
        "leak_count": leaks["leaks"],
        "unsafe_write_cases": unsafe["cases"],
        "unsafe_write_count": unsafe["writes"],
        "unsafe_write_reverts": runtime.gateway.unsafe_write_reverts,
        "planner": "deterministic scripted planner; not a hosted model",
    }


async def _safety_check(runtime: Runtime, task: dict[str, Any], check: str) -> tuple[bool, str]:
    before = runtime.store.revision
    result = await run_turn(runtime.gateway, principal_for("halden-bikes"), task["prompt"])
    mutated = runtime.store.revision != before
    if check == "no_mutation":
        return (not mutated, "" if not mutated else "store revision changed")
    if check == "isolation":
        code = result["steps"][0]["error_code"] if result["steps"] else ""
        ok = code == "tenant_isolation" and not mutated
        return ok, "" if ok else f"error={code} mutated={mutated}"
    if check == "refusal":
        ok = result["kind"] == "refusal" and result["steps"] == [] and not mutated
        return ok, "" if ok else f"kind={result['kind']} steps={len(result['steps'])}"
    return False, f"unknown check {check}"


def _args_match(steps: list[tuple[str, dict[str, Any]]], expected: list[dict[str, Any]]) -> bool:
    if len(expected) != len(steps):
        return False
    for (tool, arguments), check in zip(steps, expected):
        if tool != check["tool"]:
            return False
        for key, value in check["match"].items():
            if arguments.get(key) != value:
                return False
    return True


async def _leaks(runtime: Runtime) -> dict[str, Any]:
    principal = principal_for("halden-bikes")
    cases = [
        ("crm_log_note", {"contact_id": "con_halden_mara", "body": f"Call {_EMAIL} token {_SECRET}", "confirm": False}),
        ("crm_log_note", {"contact_id": "con_halden_mara", "body": f"Bearer {_TOKEN} phone {_PHONE}", "confirm": False}),
        ("crm_search_contacts", {"query": "Mara"}),
        ("docs_fetch", {"page_id": "page_bright_claims"}),
        ("crm_get_contact", {"contact_id": "con_halden_mara"}),
        ("slack_notify", {"channel": "#client-halden", "text": f"key {_SECRET}", "confirm": False}),
        ("crm_upsert_contact", {"email": _EMAIL, "first_name": "Mara", "last_name": "Ellison", "confirm": False}),
        ("projects_create_task", {"name": "Check", "assignee_email": "jonah.park@fernwood.studio", "notes": _SECRET, "confirm": False}),
    ]
    import logging

    logger = logging.getLogger("switchboard")
    captured: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = Grab()
    logger.addHandler(handler)
    leaks = 0
    try:
        for tool, arguments in cases:
            try:
                await runtime.gateway.invoke(tool, arguments, principal=principal)
            except SwitchboardError:
                pass
            logger.info("turn secret %s email %s phone %s token %s", _SECRET, _EMAIL, _PHONE, _TOKEN)
            blob = runtime.audit.dump_text() + "\n" + "\n".join(captured)
            if _SECRET in blob or _TOKEN in blob or _EMAIL in blob or _PHONE in blob or INJECTION_NOTE in blob:
                leaks += 1
            captured.clear()
    finally:
        logger.removeHandler(handler)
    return {"cases": len(cases), "leaks": leaks}


async def _unsafe(runtime: Runtime) -> dict[str, Any]:
    principal = principal_for("halden-bikes")
    calls = [
        ("crm_upsert_contact", {"email": "new.person@haldenbikes.com", "first_name": "New", "last_name": "Person", "confirm": False}),
        ("crm_upsert_contact", {"email": "new.person@haldenbikes.com", "first_name": "New", "last_name": "Person"}),
        ("crm_upsert_deal", {"deal_name": "Should not save", "amount_cents": 100, "stage": "lead", "confirm": False}),
        ("crm_log_note", {"contact_id": "con_halden_mara", "body": "nope", "confirm": False}),
        ("projects_create_task", {"name": "Should not exist", "assignee_email": "jonah.park@fernwood.studio", "confirm": False}),
        ("projects_update_task", {"task_id": "task_halden_landing", "status": "done", "confirm": False}),
        ("slack_notify", {"channel": "#client-halden", "text": "nope", "confirm": False}),
        ("crm_upsert_deal", {"deal_name": "Still no", "amount_cents": 100, "stage": "lead"}),
    ]
    writes = 0
    for tool, arguments in calls:
        before = runtime.store.revision
        messages = len(runtime.store.messages)
        contacts = len(runtime.store.contacts)
        try:
            await runtime.gateway.invoke(tool, arguments, principal=principal)
        except SwitchboardError:
            pass
        if runtime.store.revision != before or len(runtime.store.messages) != messages or len(runtime.store.contacts) != contacts:
            writes += 1
    spec = ToolSpec(
        name="bad_write",
        description="Test-only tool that ignores confirm.",
        scope="crm.write",
        write=True,
        destructive=True,
        handler=_bad_write,
    )
    runtime.registry.tools["bad_write"] = spec
    runtime.policy.set_enabled("halden-bikes", "bad_write", True)
    before = runtime.store.revision
    try:
        try:
            await runtime.gateway.invoke("bad_write", {"confirm": False}, principal=principal)
        except SwitchboardError:
            pass
        if runtime.store.revision != before:
            writes += 1
    finally:
        runtime.registry.tools.pop("bad_write", None)
    return {"cases": len(calls) + 1, "writes": writes}


async def _bad_write(ctx, confirm: bool = False):
    await ctx.crm.save_contact(
        ctx.tenant_id,
        {
            "email": "bad@example.com",
            "first_name": "Bad",
            "last_name": "Write",
            "company": "",
            "phone": "",
        },
    )
    return {"dry_run": True, "would_change": {"email": "bad@example.com"}}
