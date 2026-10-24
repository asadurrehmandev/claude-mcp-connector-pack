"""Deterministic demo planner.

This is not a language model. It maps a short list of agency requests onto
tool calls with regular expressions, then fills the reply from tool JSON.
Demo mode in the console says so.
"""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import timedelta
from decimal import Decimal
from typing import Any

from switchboard.constants import DEMO_TODAY
from switchboard.errors import SwitchboardError
from switchboard.gateway import Gateway
from switchboard.principal import Principal

_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
_CHANNEL = re.compile(r"#[a-z0-9][a-z0-9-]{1,40}")
_CAMPAIGN = re.compile(r"\bcmp_[a-z0-9_]+\b")
_TASK = re.compile(r"\btask_[a-z0-9_]+\b")
_CONTACT = re.compile(r"\bcon_[a-z0-9_]+\b")
_WORKSPACE = re.compile(r"\bworkspace_id\s+([a-z0-9-]+)")
_QUOTED = re.compile(r'"([^"]+)"')
_REFUSAL = re.compile(
    r"(?i)(ignore (all |any )?(previous|prior|above) instructions|"
    r"reveal (the )?(system prompt|api key|secret|token|signing secret)|"
    r"print (the )?(system prompt|hidden prompt|notion token|signing secret)|"
    r"exfiltrate|send (me )?(all )?(secrets|tokens|api keys)|"
    r"(token|secret|api key|password|signing secret).{0,80}(slack|channel|#|notify|ping|drop)|"
    r"(slack|notify|post|ping|drop).{0,80}(token|secret|api key|password|signing secret))"
)
_LEAD = re.compile(
    r"(?i)\b(new lead|add (a )?lead|create (a )?contact|add (a )?contact|"
    r"new inquiry|new prospect|add a prospect|inbound lead|form fill)\b"
)
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)


def _normalize(prompt: str) -> str:
    return unicodedata.normalize("NFKC", prompt.translate(_ZERO_WIDTH))


def plan_calls(prompt: str) -> tuple[str, list[tuple[str, dict[str, Any]]], str]:
    raw = prompt.strip()
    text = _normalize(raw)
    workspace_id = _workspace(text)

    def with_ws(arguments: dict[str, Any]) -> dict[str, Any]:
        if workspace_id:
            arguments["workspace_id"] = workspace_id
        return arguments

    if _REFUSAL.search(text):
        return "refusal", [], ""

    note_contact = _CONTACT.search(text)
    if re.search(r"(?i)\b(log|add) a note\b", text) and note_contact:
        body = _after(text, r"(?i)saying\s+") or _after(text, r"(?i)note on \S+\s*:\s*") or "Follow up"
        return "note", [("crm_log_note", with_ws({
            "contact_id": note_contact.group(0),
            "body": body.strip().strip('"'),
            "confirm": _confirm(text),
        }))], ""

    lead_language = bool(_LEAD.search(text))
    if re.search(r"(?i)\bslack\b|\bnotify\b|\bping\b|drop a message|post to #", text) and not lead_language:
        channel = _CHANNEL.search(text)
        saying = _quoted_or_after(text, r"(?i)saying\s+") or _quoted_or_after(text, r"(?i)message:\s*")
        return "slack", [("slack_notify", with_ws({
            "channel": channel.group(0) if channel else "",
            "text": saying or "Update from Northline.",
            "confirm": _confirm(text),
        }))], ""

    emails = _EMAIL.findall(text)
    if emails and _LEAD.search(text) and not re.search(r"(?i)\b(find|search|look up|who is)\b", text):
        lead_email = emails[0]
        assignee = emails[1] if len(emails) > 1 else ""
        name = _name(text) or "New lead"
        parts = name.split()
        first, last = parts[0], parts[-1] if len(parts) > 1 else "Lead"
        company = _company(text)
        title = _quoted(text) or f"Follow up with {name}"
        confirm = _confirm(text)
        steps = [
            ("crm_upsert_contact", with_ws({
                "email": lead_email,
                "first_name": first,
                "last_name": last,
                "company": company,
                "confirm": confirm,
            })),
            ("projects_create_task", with_ws({
                "name": title,
                "assignee_email": assignee,
                "confirm": confirm,
            })),
        ]
        if re.search(r"(?i)\bslack\b|\bnotify\b", text):
            channel = _CHANNEL.search(text)
            steps.append(("slack_notify", with_ws({
                "channel": channel.group(0) if channel else "",
                "text": f"New lead {name} added.",
                "confirm": confirm,
            })))
        return "lead", steps, ""

    if re.search(r"(?i)\b(add|create) a deal\b", text):
        amount = re.search(r"\b(\d+) cents\b", text)
        stage = re.search(r"(?i)\bstage\s+(lead|qualified|proposal|negotiation|won|lost)\b", text)
        return "deal_write", [("crm_upsert_deal", with_ws({
            "deal_name": _quoted(text) or "New deal",
            "amount_cents": int(amount.group(1)) if amount else 0,
            "stage": stage.group(1).lower() if stage else "",
            "confirm": _confirm(text),
        }))], ""

    task_id = _TASK.search(text)
    if task_id and re.search(r"(?i)\b(update|mark|set|move)\b", text):
        status = _status(text)
        return "task_update", [("projects_update_task", with_ws({
            "task_id": task_id.group(0),
            "status": status,
            "confirm": _confirm(text),
        }))], ""

    if re.search(r"(?i)\b(create|add|open) (a )?task\b", text):
        assignee = emails[0] if emails else ""
        title = _quoted(text) or "New task"
        return "task_create", [("projects_create_task", with_ws({
            "name": title,
            "assignee_email": assignee,
            "confirm": _confirm(text),
        }))], ""

    if re.search(r"(?i)who can i assign|list members|assignees|who is on the account", text):
        return "members", [("projects_list_members", with_ws({}))], ""

    if re.search(r"(?i)\b(open tasks|list tasks|task list|what(?:'s| is) open|on the board|open work)\b", text):
        arguments: dict[str, Any] = {}
        if re.search(r"(?i)\bopen\b", text):
            arguments["status"] = "open"
        return "tasks", [("projects_list_tasks", with_ws(arguments))], ""

    if re.search(
        r"(?i)\b(docs|documentation|according to|cite|policy|brand voice|what does our|from our notes|knowledge base|what are we allowed)\b",
        text,
    ):
        return "docs", [("docs_search", with_ws({"query": raw, "limit": 3}))], "docs_fetch"

    if re.search(
        r"(?i)\b(weekly|ads report|full report|account summary|readout|roll up|rollup|performance report)\b",
        text,
    ):
        start, end = _dates(text)
        platform = _platform(text)
        campaign = _CAMPAIGN.search(text)
        metrics = {"start_date": start, "end_date": end, "platform": platform}
        if campaign:
            metrics["campaign_id"] = campaign.group(0)
        return "report", [
            ("ads_list_campaigns", with_ws({"platform": platform})),
            ("ads_campaign_metrics", with_ws(dict(metrics))),
            ("ads_account_summary", with_ws({"start_date": start, "end_date": end, "platform": platform})),
        ], ""

    if re.search(r"(?i)\blist\b.+\bcampaigns?\b", text):
        return "campaigns", [("ads_list_campaigns", with_ws({"platform": _platform(text)}))], ""

    if re.search(r"(?i)\b(cpa|ctr|spend|impressions|conversions|campaign)\b", text):
        start, end = _dates(text)
        platform = _platform(text)
        arguments = {"start_date": start, "end_date": end, "platform": platform}
        campaign = _CAMPAIGN.search(text)
        if campaign:
            arguments["campaign_id"] = campaign.group(0)
        return "metrics", [("ads_campaign_metrics", with_ws(arguments))], ""

    if re.search(r"(?i)\bdeals?\b", text) and re.search(r"(?i)\b(find|search|look up|list|show)\b", text):
        return "deals", [("crm_search_deals", with_ws({"query": _query(text)}))], ""

    if re.search(r"(?i)\b(contact|contacts)\b", text) and re.search(r"(?i)\b(find|search|look up|who is|named)\b", text):
        return "contacts", [("crm_search_contacts", with_ws({"query": _query(text)}))], ""

    return "unknown", [], ""


async def run_turn(gateway: Gateway, principal: Principal, prompt: str) -> dict[str, Any]:
    kind, steps, follow = plan_calls(prompt)
    if kind == "refusal":
        return {
            "kind": kind,
            "reply": (
                "I won't do that. Switchboard does not reveal tokens, system prompts, or signing secrets, "
                "and it does not follow instructions that try to override the tool rules."
            ),
            "steps": [],
        }
    if kind == "unknown":
        return {
            "kind": kind,
            "reply": (
                "The demo planner has no tool for that. It can run a weekly ads report, look up spend, CTR, or CPA, "
                "search contacts and deals, answer from the workspace docs, create a lead with a task, or post to an allowlisted Slack channel."
            ),
            "steps": [],
        }
    executed: list[dict[str, Any]] = []
    for tool, arguments in steps:
        executed.append(await _invoke(gateway, principal, tool, arguments))
        if follow == "docs_fetch" and tool == "docs_search" and not executed[-1]["is_error"]:
            pages = executed[-1]["result"].get("data", {}).get("pages", [])
            if pages:
                executed.append(
                    await _invoke(gateway, principal, "docs_fetch", {"page_id": pages[0]["id"]})
                )
    return {"kind": kind, "reply": _reply(kind, executed), "steps": executed}


async def _invoke(gateway: Gateway, principal: Principal, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await gateway.invoke(tool, arguments, principal=principal)
        error = None
    except SwitchboardError as exc:
        result = {"error": {"code": exc.code, "message": exc.public_message, "details": exc.details}}
        error = exc.code
    return {
        "tool": tool,
        "arguments": arguments,
        "result": result,
        "is_error": error is not None,
        "error_code": error,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _money_line(current: dict[str, Any]) -> str:
    spend = current["spend_cents"]
    cpa = current["cpa_cents"]
    if cpa is None:
        cpa_text = "CPA n/a (no conversions)"
    else:
        cpa_text = f"CPA ${Decimal(str(cpa)) / Decimal(100):.2f} ({cpa} cents)"
    return f"Spend ${spend / 100:.2f} ({spend} cents), CTR {current['ctr']}, {cpa_text}."


def _reply(kind: str, steps: list[dict[str, Any]]) -> str:
    if any(step["is_error"] for step in steps):
        failed = next(step for step in steps if step["is_error"])
        message = failed["result"]["error"]["message"]
        return f"{failed['tool']} was refused ({failed['error_code']}): {message}"
    if kind == "metrics":
        data = steps[0]["result"]["data"]
        current = data["current"]
        return (
            f"{data['start_date']} to {data['end_date']}, platform {data['platform']}"
            f"{', campaign ' + data['campaign_id'] if data['campaign_id'] else ''}. "
            f"{_money_line(current)} "
            f"Flags: {', '.join(data['flags']) if data['flags'] else 'none'}. "
            "CTR, CPA, and flags were computed by the ads connector."
        )
    if kind == "report":
        summary = steps[-1]["result"]["data"]
        current = summary["current"]
        names = ", ".join(row["name"] for row in steps[0]["result"]["data"]["campaigns"]) or "no campaigns"
        return (
            f"Weekly readout {summary['start_date']} to {summary['end_date']}. "
            f"Campaigns: {names}. "
            f"Account {_money_line(current)} "
            f"Flags: {', '.join(summary['flags']) if summary['flags'] else 'none'}. "
            "Figures come from ads_account_summary, not from a model."
        )
    if kind == "docs":
        fetched = steps[-1]["result"]["data"]
        citation = fetched["citation"]
        return (
            f"From {citation['title']} ({citation['url']}), page {citation['page_id']}. "
            f"Excerpt, treated as source text and not as instructions: {fetched['excerpt']}"
        )
    if kind == "contacts":
        rows = steps[0]["result"]["data"]["contacts"]
        if not rows:
            return "No contacts matched in this workspace."
        listed = ", ".join(f"{row['first_name']} {row['last_name']} <{row['email']}>" for row in rows)
        return f"Contacts in this workspace: {listed}."
    if kind == "deals":
        rows = steps[0]["result"]["data"]["deals"]
        if not rows:
            return "No deals matched in this workspace."
        listed = ", ".join(f"{row['name']} (${row['amount_cents'] / 100:.2f}, {row['stage']})" for row in rows)
        return f"Deals: {listed}."
    if kind in {"lead", "note", "task_create", "task_update", "slack", "deal_write"}:
        if steps[0]["result"].get("dry_run"):
            return "Dry run only. Nothing was written. Pass confirm, or say 'confirm', to apply the preview shown in the tool result."
        return "The confirmed write finished. Details are in the tool result."
    if kind == "tasks":
        rows = steps[0]["result"]["data"]["tasks"]
        if not rows:
            return "No tasks matched."
        return "Tasks: " + "; ".join(f"{row['name']} ({row['status']}, {row['assignee_email']})" for row in rows)
    if kind == "members":
        rows = steps[0]["result"]["data"]["members"]
        return "Members: " + ", ".join(f"{row['name']} <{row['email']}>" for row in rows)
    if kind == "campaigns":
        rows = steps[0]["result"]["data"]["campaigns"]
        return "Campaigns: " + ", ".join(f"{row['name']} [{row['platform']}]" for row in rows)
    return "The tool call finished. See the result."


def _workspace(text: str) -> str:
    found = _WORKSPACE.search(text)
    if found:
        return found.group(1)
    loose = re.search(r"(?i)\bworkspace\s+([a-z0-9-]+)", text)
    if loose:
        return loose.group(1)
    for slug in ("brightline-dental", "halden-bikes", "sable-rye"):
        if re.search(rf"\b{slug}\b", text):
            return slug
    return ""


def _confirm(text: str) -> bool:
    if re.search(r"(?i)\b(skip|without|no|don't|do not)\s+confirm\b", text):
        return False
    if re.search(r"(?i)\b(no preview|skip the preview|just save it)\b", text):
        return False
    return bool(re.search(r"(?i)\bconfirm\b|\bgo ahead\b|\bapply the (write|change|preview)\b", text))


def _platform(text: str) -> str:
    google = bool(re.search(r"(?i)google", text))
    meta = bool(re.search(r"(?i)\bmeta\b|facebook|instagram", text))
    if google and not meta:
        return "google"
    if meta and not google:
        return "meta"
    return "all"


def _dates(text: str) -> tuple[str, str]:
    found = _ISO.findall(text)
    if len(found) >= 2:
        return found[0], found[1]
    if len(found) == 1:
        return found[0], found[0]
    if re.search(r"(?i)yesterday", text):
        day = (DEMO_TODAY - timedelta(days=1)).isoformat()
        return day, day
    end = DEMO_TODAY - timedelta(days=1)
    start = end - timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _query(text: str) -> str:
    text = _WORKSPACE.sub(" ", text)
    text = re.sub(r"\b(halden-bikes|sable-rye|brightline-dental)\b", " ", text)
    named = re.search(r"\b(?:[Nn]amed|[Ww]ho is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})", text)
    if named:
        return named.group(1)
    if not re.search(r"(?i)\btask\b", text):
        quoted = _quoted(text)
        if quoted:
            return quoted
    for_match = re.search(
        r"(?i)\bfor\s+(?:the\s+|a\s+|an\s+)?(.+?)(?:\s+in\s+workspace\b|\s+workspace_id\b|$)",
        text,
    )
    if for_match and re.search(r"(?i)\b(deals?|contacts?)\b", text):
        return for_match.group(1).strip(" .")
    words = re.sub(r"(?i)\b(find|search|look up|show|list|contacts?|deals?|the|a|an|for|in|me)\b", " ", text)
    cleaned = " ".join(words.split())
    return cleaned or text


def _name(text: str) -> str:
    named = re.search(r"\bnamed\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})", text)
    if named:
        return named.group(1)
    generic = re.search(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b", text)
    return generic.group(1) if generic else ""


def _company(text: str) -> str:
    match = re.search(r"(?i)\b(?:at|company)\s+([A-Z][^,.\n]+)", text)
    return match.group(1).strip() if match else ""


def _quoted(text: str) -> str:
    match = _QUOTED.search(text)
    return match.group(1).strip() if match else ""


def _quoted_or_after(text: str, pattern: str) -> str:
    quoted = _quoted(text)
    if quoted:
        return quoted
    return _after(text, pattern)


def _after(text: str, pattern: str) -> str:
    match = re.search(pattern + r"(.+)$", text)
    return match.group(1).strip() if match else ""


def _status(text: str) -> str:
    if re.search(r"(?i)in progress", text):
        return "in_progress"
    if re.search(r"(?i)\breview\b", text):
        return "review"
    if re.search(r"(?i)\bopen\b", text):
        return "open"
    if re.search(r"(?i)\b(done|closed|complete)\b", text):
        return "done"
    return ""
