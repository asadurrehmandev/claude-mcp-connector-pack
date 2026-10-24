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
_CAMPAIGN = re.compile(r"\bcmp_[a-z0-9_]+\b", re.IGNORECASE)
_TASK = re.compile(r"\btask_[a-z0-9_]+\b", re.IGNORECASE)
_CONTACT = re.compile(r"\bcon_[a-z0-9_]+\b", re.IGNORECASE)
_WORKSPACE = re.compile(
    r"(?i)\bworkspace_id(?:\s+is|\s*[:=])?\s+([a-z0-9]+(?:-[a-z0-9]+)+)"
)
_PAGE = re.compile(r"\bpage_[a-z0-9_]+\b", re.IGNORECASE)
_QUOTED = re.compile(r'"([^"]+)"')
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_HOMOGLYPHS = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0457": "i", "\u04cf": "l",
    "\u2010": "-", "\u2011": "-", "\u2013": "-", "\u2014": "-",
})
_INVISIBLE = dict.fromkeys(
    map(ord, "\u200b\u200c\u200d\ufeff\u2060\u00ad\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"),
    None,
)
# Hijack phrases are instructions. The same words inside quotes are data.
_HIJACK = re.compile(
    r"(?i)(ignore (all |any )?(previous|prior|above) instructions|"
    r"reveal (the )?(system prompt|api key|secret|token|signing secret)|"
    r"print (the )?(system prompt|hidden prompt|notion token|signing secret)|"
    r"(hidden prompt|text above (your|the) tools)|"
    r"developer mode|system override|decode and follow|"
    r"disable tenant|platform admin|forget the allowlist|dump every client)"
)
# A channel post is refused even when the secret sits in quotes.
# "Notify #client-sable saying \"the signing secret\"" must not send.
_EXFIL = re.compile(
    r"(?i)(oauth client secret|jwt private key|switchboard_signing_secret|"
    r"exfiltrate|send (me )?(all )?(secrets|tokens|api keys)|"
    r"\bsk-[a-z0-9-]{8,}|"
    r"(token|secret|api key|password|signing secret).{0,80}(slack|channel|#|notify|ping|drop|post)|"
    r"(slack|notify|post|ping|drop).{0,80}(token|secret|api key|password|signing secret|sk-))"
)
_CAST = (
    ("Mara", "Ellison"),
    ("Chris", "Adeyemi"),
    ("Helen", "Cho"),
    ("Marcus", "Bell"),
    ("Anita", "Deshmukh"),
    ("Luis", "Ortega"),
    ("Jonah", "Park"),
    ("Priya", "Shah"),
    ("Andre", "Molina"),
    ("Camille", "Berger"),
    ("Elena", "Voss"),
)
_CAMPAIGN_ALIASES = (
    ("gravel search", "cmp_halden_exact"),
    ("gravel exact", "cmp_halden_exact"),
    ("zero click", "cmp_halden_noclick"),
    ("performance max", "cmp_halden_pmax"),
    ("store visits", "cmp_sable_local"),
    ("weekly specials", "cmp_sable_meta"),
    ("new patients", "cmp_bright_search"),
    ("recall display", "cmp_bright_zero"),
)
_ROSTER = {
    "elena voss": "elena.voss@fernwood.studio",
    "jonah park": "jonah.park@fernwood.studio",
    "priya shah": "priya.shah@fernwood.studio",
    "andre molina": "andre.molina@fernwood.studio",
    "camille berger": "camille.berger@fernwood.studio",
    "elena": "elena.voss@fernwood.studio",
    "jonah": "jonah.park@fernwood.studio",
    "priya": "priya.shah@fernwood.studio",
    "andre": "andre.molina@fernwood.studio",
    "camille": "camille.berger@fernwood.studio",
}
_LEAD = re.compile(
    r"(?i)(\b(?:new lead|add (?:a )?lead|create (?:a )?contact|add (?:a )?contact|"
    r"new inquiry|new prospect|add a prospect|inbound lead|form fill|new person)\b|"
    r"\binbound\s*:)"
)


def _normalize(prompt: str) -> str:
    folded = unicodedata.normalize("NFKC", prompt).translate(_INVISIBLE).translate(_HOMOGLYPHS)
    for src, dst in (
        ("yestreday", "yesterday"),
        ("yesterdy", "yesterday"),
        ("yesteday", "yesterday"),
        ("lastweek", "last week"),
        ("lookup", "look up"),
    ):
        folded = re.sub(rf"(?i)\b{src}\b", dst, folded)
    return folded


def _unquoted(text: str) -> str:
    """Drop quoted spans so a data field is not treated as an instruction."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", " ", text)


def _refuses(text: str) -> bool:
    return bool(_EXFIL.search(text) or _HIJACK.search(_unquoted(text)))


def _without_emails(text: str) -> str:
    return _EMAIL.sub(" ", text)


def plan_calls(prompt: str) -> tuple[str, list[tuple[str, dict[str, Any]]], str]:
    raw = prompt.strip()
    text = _normalize(raw)
    parts = re.split(r"(?i)\s*,?\s+and also\s+|\s+and then\s+|\s*;\s*then\s+|\s+\bplus\b\s+", text, maxsplit=1)
    if len(parts) == 2 and all(part.strip() for part in parts):
        kind_a, steps_a, follow_a = _plan_clause(parts[0].strip(), parts[0].strip())
        kind_b, steps_b, follow_b = _plan_clause(parts[1].strip(), parts[1].strip())
        if steps_a and steps_b and kind_a != "refusal" and kind_b != "refusal":
            return "multi", steps_a + steps_b, follow_a or follow_b
        if steps_a:
            return kind_a, steps_a, follow_a
        if steps_b:
            return kind_b, steps_b, follow_b
    return _plan_clause(text, raw)


def _plan_clause(text: str, raw: str) -> tuple[str, list[tuple[str, dict[str, Any]]], str]:
    workspace_id = _workspace(text)

    def with_ws(arguments: dict[str, Any]) -> dict[str, Any]:
        if workspace_id:
            arguments["workspace_id"] = workspace_id
        return arguments

    if _refuses(text):
        return "refusal", [], ""

    note_contact = _CONTACT.search(text)
    if re.search(r"(?i)\b(log|add) a note\b", text) and note_contact:
        body = (
            _after(text, r"(?i)saying\s+")
            or _after(text, r"(?i)that says\s+")
            or _after(text, r"(?i)note on \S+\s*:\s*")
            or "Follow up"
        )
        body = re.split(r"(?i)\.\s+|\bno need\b|\bwithout\b", body)[0].strip().strip('"')
        return "note", [("crm_log_note", with_ws({
            "contact_id": note_contact.group(0).lower(),
            "body": body.strip().strip('"'),
            "confirm": _confirm(text),
        }))], ""

    lead_language = bool(_LEAD.search(text))
    channel_present = _CHANNEL.search(text)
    if (
        not lead_language
        and re.search(r"(?i)\b(slack|notify|ping|post|drop)\b", text)
        and (channel_present or re.search(r"(?i)\b(slack|notify|ping)\b", text))
    ):
        channel = _CHANNEL.search(text)
        saying = _quoted_or_after(text, r"(?i)saying\s+") or _quoted_or_after(text, r"(?i)message:\s*")
        return "slack", [("slack_notify", with_ws({
            "channel": channel.group(0) if channel else "",
            "text": saying or "Update from Fernwood.",
            "confirm": _confirm(text),
        }))], ""

    emails = _EMAIL.findall(text)
    # A contact card is a lead when it names a person and a task, even if nobody
    # wrote the word "add". "Create a task" stays a task, not a contact write.
    looks_like_lead = bool(_LEAD.search(text)) or (
        bool(emails)
        and re.search(r"(?i)\btask\b", text)
        and not re.search(r"(?i)\b(create|add|open) (a |the )?task\b", text)
        and _person_mentioned(text)
    )
    if emails and looks_like_lead and not re.search(r"(?i)\b(find|search|look up|who is)\b", text):
        lead_email = emails[0]
        assignee = emails[1] if len(emails) > 1 else ""
        name = _name(text) or "New lead"
        parts = name.split()
        first, last = parts[0], parts[-1] if len(parts) > 1 else "Lead"
        company = _company(text)
        title = _task_title(text) or f"Follow up with {name}"
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
            "deal_name": _deal_name(text),
            "amount_cents": int(amount.group(1)) if amount else 0,
            "stage": stage.group(1).lower() if stage else "",
            "confirm": _confirm(text),
        }))], ""

    task_id = _TASK.search(text)
    if task_id and re.search(r"(?i)\b(update|mark|set|move)\b", text):
        status = _status(text)
        return "task_update", [("projects_update_task", with_ws({
            "task_id": task_id.group(0).lower(),
            "status": status,
            "confirm": _confirm(text),
        }))], ""

    if re.search(r"(?i)\b(create|add|open) (a |the )?task\b", text):
        assignee = emails[0] if emails else _staff_email(text)
        title = _task_title(text) or "New task"
        return "task_create", [("projects_create_task", with_ws({
            "name": title,
            "assignee_email": assignee,
            "confirm": _confirm(text),
        }))], ""

    if re.search(r"(?i)who can i assign|list members|assignees|who is on the account", text):
        return "members", [("projects_list_members", with_ws({}))], ""

    if re.search(r"(?i)\b(open tasks|list tasks|task list|what(?:'s| is) open|on the board|open work|still open)\b", text):
        arguments: dict[str, Any] = {}
        if re.search(r"(?i)\bopen\b", text) and not re.search(r"(?i)whatever status", text):
            arguments["status"] = "open"
        staff = _staff_email(text)
        if staff:
            arguments["assignee_email"] = staff
        return "tasks", [("projects_list_tasks", with_ws(arguments))], ""

    if task_id and not re.search(r"(?i)\b(update|mark|set|move|create|add|open)\b", text):
        return "task_get", [("projects_get_task", with_ws({"task_id": task_id.group(0).lower()}))], ""

    if note_contact and re.search(r"(?i)\b(get|fetch|pull|open|show|read)\b", text):
        return "contact_get", [("crm_get_contact", with_ws({"contact_id": note_contact.group(0).lower()}))], ""

    page = _PAGE.search(text)
    if page and re.search(r"(?i)\b(fetch|get|pull|open|show|read|page)\b", text):
        return "docs", [("docs_fetch", with_ws({"page_id": page.group(0).lower()}))], ""

    if re.search(r"(?i)which channels|what channels|channels can", text):
        return "channels", [("slack_list_channels", with_ws({}))], ""

    if re.search(
        r"(?i)\b(docs|documentation|according to|cite|citation|policy|brand voice|what does our|from our notes|knowledge base|are we allowed|what are we allowed)\b",
        text,
    ):
        return "docs", [("docs_search", with_ws({"query": raw, "limit": 3}))], "docs_fetch"

    if re.search(
        r"(?i)\b(weekly|ads report|full report|account summary|readout|roll up|rollup|performance report|recap)\b|\broll\b.+\bup\b",
        text,
    ):
        parsed = _dates(text)
        if parsed is None:
            return "unknown", [], ""
        start, end = parsed
        platform = _platform(text)
        campaign = _campaign_id(text)
        metrics = {"start_date": start, "end_date": end, "platform": platform}
        if campaign:
            metrics["campaign_id"] = campaign
        return "report", [
            ("ads_list_campaigns", with_ws({"platform": platform})),
            ("ads_campaign_metrics", with_ws(dict(metrics))),
            ("ads_account_summary", with_ws({"start_date": start, "end_date": end, "platform": platform})),
        ], ""

    if re.search(r"(?i)\blist\b.+\bcampaigns?\b", text):
        return "campaigns", [("ads_list_campaigns", with_ws({"platform": _platform(text)}))], ""

    metric_cue = bool(
        re.search(r"(?i)\b(cpa|ctr|spend|impressions|conversions|clicks|campaign|numbers)\b", text)
        or _CAMPAIGN.search(text)
    )
    platform_cue = bool(re.search(r"(?i)\b(google|meta|facebook|instagram|every platform|all platforms)\b", text))
    date_cue = bool(
        _date_attempt(text)
        or _ISO.search(text)
        or _US_DATE.search(text)
        or re.search(r"(?i)\b(yesterday|last week|week ending|past week|this week)\b", text)
    )
    if metric_cue or (platform_cue and date_cue):
        parsed = _dates(text)
        if parsed is None:
            return "unknown", [], ""
        start, end = parsed
        platform = _platform(text)
        arguments = {"start_date": start, "end_date": end, "platform": platform}
        campaign = _campaign_id(text)
        if campaign:
            arguments["campaign_id"] = campaign
        return "metrics", [("ads_campaign_metrics", with_ws(arguments))], ""

    if re.search(r"(?i)\bdeals?\b", text) and re.search(
        r"(?i)\b(find|search|look up|list|show|where is|mentioning|any|named|pull|fetch|get)\b",
        text,
    ):
        return "deals", [("crm_search_deals", with_ws({"query": _query(text)}))], ""

    if re.search(r"(?i)\b(contact|contacts|crm|hubspot)\b", text) and (
        re.search(
            r"(?i)\b(find|search|look up|who is|who's|whos|named|do we have|pull|fetch|get|list|show)\b",
            text,
        )
        or _cast_name(_without_emails(text))
        or _flipped_name(text)
        or re.search(r"(?i)\bin the crm\b", text)
    ):
        return "contacts", [("crm_search_contacts", with_ws({"query": _query(text)}))], ""

    if re.search(r"(?i)\b(who is|who's|whos|do we have|look up)\b", text) and not re.search(r"(?i)\bdeals?\b", text):
        person = _cast_name(text) or _flipped_name(text)
        if person:
            return "contacts", [("crm_search_contacts", with_ws({"query": person}))], ""

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
    if kind == "multi":
        return "Did both parts. Each tool result is listed beside this reply."
    if kind == "channels":
        rows = steps[0]["result"]["data"]["channels"]
        return "Channels: " + ", ".join(row["name"] for row in rows)
    if kind == "task_get":
        row = steps[0]["result"]["data"]["task"]
        return f"{row['name']} ({row['status']}, {row['assignee_email']})."
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
    if re.search(
        r"(?i)("
        r"\b(no|not|without|skip|don't|dont|do not)\b.{0,40}\bconfirm\b|"
        r"\b(don't|dont|do not)\s+(write|save|send|apply|ask)\b|"
        r"\b(just save it|no preview|skip the preview|skip the dry run|"
        r"hold the write|preview only|dry-?run|show me the dry run|"
        r"did not say to apply|don't write yet|dont write yet)\b"
        r")",
        text,
    ):
        return False
    return bool(
        re.search(
            r"(?i)\bconfirm\b|\bgo ahead\b|\bapply the (write|change|preview|send)\b|"
            r"\byes,? apply\b|\bapply it\b|\bi confirm\b",
            text,
        )
    )


def _excluded(text: str, names: str) -> bool:
    return bool(
        re.search(
            rf"(?i)\b(skip|no|not|without|except|exclude|excluding|leave|drop|omit)\b.{{0,30}}\b(?:{names})\b"
            rf"|\b(?:{names})\b.{{0,20}}\b(off|out|excluded|skipped|aside)\b",
            text,
        )
    )


def _platform(text: str) -> str:
    skip_meta = _excluded(text, "meta|facebook|instagram") or bool(re.search(r"(?i)google only", text))
    skip_google = _excluded(text, "google") or bool(
        re.search(r"(?i)meta only|instagram only|facebook only", text)
    )
    if skip_meta and skip_google:
        return "all"
    if skip_meta:
        return "google"
    if skip_google:
        return "meta"
    google = bool(re.search(r"(?i)google", text))
    meta = bool(re.search(r"(?i)\bmeta\b|facebook|instagram", text))
    if google and not meta:
        return "google"
    if meta and not google:
        return "meta"
    return "all"


def _month_num(token: str) -> int:
    return _MONTH_NUM[token.lower()[:3]]


def _iso_from(year: int, month: int, day: int) -> str:
    from datetime import date

    return date(year, month, day).isoformat()


def _pair_dates(found: list[tuple[int, int, int | None]]) -> tuple[str, str] | None:
    concrete: list[tuple[int, int, int]] = []
    implied_year = DEMO_TODAY.year
    for year, month, day in found:
        concrete.append((year or implied_year, month, day))
    if len(concrete) >= 2:
        start, end = concrete[0], concrete[1]
        return _iso_from(*start), _iso_from(*end)
    if len(concrete) == 1:
        day = _iso_from(*concrete[0])
        return day, day
    return None


def _explicit_dates(text: str) -> tuple[str, str] | None:
    iso = _ISO.findall(text)
    if len(iso) >= 2:
        return iso[0], iso[1]
    if len(iso) == 1:
        return iso[0], iso[0]
    us = [(int(year), int(month), int(day)) for month, day, year in _US_DATE.findall(text)]
    if us:
        return _pair_dates(us)
    ordinal = re.search(
        rf"(?i)(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:through|thru|to|until|-)\s+(?:the\s+)?"
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+of\s+{_MONTH}(?:\s+(\d{{4}}))?",
        text,
    )
    if ordinal:
        year = int(ordinal.group(4)) if ordinal.group(4) else DEMO_TODAY.year
        month = _month_num(ordinal.group(3))
        return _iso_from(year, month, int(ordinal.group(1))), _iso_from(year, month, int(ordinal.group(2)))
    named: list[tuple[int | None, int, int]] = []
    for match in re.finditer(rf"(?i)\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?", text):
        year = int(match.group(3)) if match.group(3) else None
        named.append((year, _month_num(match.group(1)), int(match.group(2))))
    if not named:
        for match in re.finditer(rf"(?i)\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}(?:\s+(\d{{4}}))?", text):
            year = int(match.group(3)) if match.group(3) else None
            named.append((year, _month_num(match.group(2)), int(match.group(1))))
    shared_year = next((year for year, _, _ in named if year), None)
    normalized = [(shared_year if year is None else year, month, day) for year, month, day in named]
    return _pair_dates(normalized)


def _date_attempt(text: str) -> bool:
    return bool(
        re.search(
            rf"(?i)(\b{_MONTH}\b|\b\d{{1,2}}/\d{{1,2}}\b|\bmtd\b|month[\s-]to[\s-]date|"
            r"\d{{1,2}}(?:st|nd|rd|th)\b|\bthru\b|\bthrough\b|\buntil\b)",
            text,
        )
    )


def _dates(text: str) -> tuple[str, str] | None:
    explicit = _explicit_dates(text)
    if explicit:
        return explicit
    yesterday = DEMO_TODAY - timedelta(days=1)
    if re.search(r"(?i)\bmtd\b|month[\s-]to[\s-]date", text):
        start = yesterday.replace(day=1)
        return start.isoformat(), yesterday.isoformat()
    if re.search(r"(?i)yesterday", text) and not re.search(r"(?i)\bweek\b", text):
        day = yesterday.isoformat()
        return day, day
    if re.search(r"(?i)last week|week ending|past week|this past week|\bweekly\b", text) or not _date_attempt(text):
        end = yesterday
        start = end - timedelta(days=6)
        return start.isoformat(), end.isoformat()
    return None


def _query(text: str) -> str:
    text = _WORKSPACE.sub(" ", text)
    text = re.sub(r"\b(halden-bikes|sable-rye|brightline-dental)\b", " ", text)
    flipped = _flipped_name(text)
    if flipped:
        return flipped
    cast = _cast_name(text)
    if cast and re.search(r"(?i)\b(contact|who|crm|hubspot)\b", text):
        return cast
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
    words = re.sub(
        r"(?i)\b(find|search|look up|show|list|contacts?|deals?|the|a|an|for|in|me|any|where|is|sitting|mentioning|do|we|have|him|her|crm|hubspot|named|pull|fetch|get|please|but|set)\b",
        " ",
        text,
    )
    cleaned = " ".join(words.split())
    return cleaned or text


def _flipped_name(text: str) -> str:
    """Read LAST, FIRST. Skip commas that are just clause punctuation."""
    stop = {
        "in", "the", "and", "for", "task", "company", "com", "org", "net", "workspace",
        "stage", "with", "from", "at", "please", "give", "note", "it", "to", "send",
        "just", "show", "call", "is", "me", "can", "you", "of", "on", "do", "not",
        "write", "yet", "dry", "run", "preview", "confirm", "true", "false", "data",
        "says", "said", "field", "hidden", "but", "set", "a", "an",
    }
    known = {part.lower() for pair in _CAST for part in pair}
    best = ""
    best_score = 0
    for match in re.finditer(r"(?<!\.)\b([A-Za-z]{2,}),\s+([A-Za-z]{2,})\b", text):
        last, first = match.group(1), match.group(2)
        if first.lower() in stop or last.lower() in stop:
            continue
        score = 0
        if first[:1].isupper() and last[:1].isupper():
            score += 2
        if first.lower() in known or last.lower() in known:
            score += 2
        if score > best_score:
            best_score = score
            best = f"{first.title()} {last.title()}"
    return best


def _cast_name(text: str) -> str:
    lowered = text.lower()
    for first, last in _CAST:
        if re.search(rf"\b{re.escape(first.lower())}\b", lowered) and re.search(rf"\b{re.escape(last.lower())}\b", lowered):
            return f"{first} {last}"
    return ""


def _campaign_id(text: str) -> str:
    found = _CAMPAIGN.search(text)
    if found:
        return found.group(0).lower()
    lowered = text.lower()
    for phrase, campaign_id in _CAMPAIGN_ALIASES:
        if phrase in lowered:
            return campaign_id
    return ""


def _staff_email(text: str) -> str:
    for key, email in sorted(_ROSTER.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?i)\b{re.escape(key)}\b", text):
            return email
    return ""


def _person_mentioned(text: str) -> bool:
    return bool(
        _flipped_name(text)
        or _cast_name(_without_emails(text))
        or re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", text)
    )


def _name(text: str) -> str:
    """The person being filed, not the assignee whose address appears later."""
    named = re.search(
        r"(?i)\bnamed\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s+\S+@|\s+at\b|,|\s+task\b|$)",
        text,
    )
    if named:
        return " ".join(part.title() for part in named.group(1).split())
    possessive = re.search(r"(?i)\bname's\s+([A-Za-z]+\s+[A-Za-z]+)", text)
    if possessive:
        return " ".join(part.title() for part in possessive.group(1).split())
    flipped = _flipped_name(text)
    if flipped:
        return flipped
    head = text
    first_email = _EMAIL.search(text)
    if first_email:
        head = text[: first_email.start()]
    cast = _cast_name(_without_emails(head))
    if cast and cast.split()[0] not in {"Halden", "Sable", "Brightline", "Fernwood"}:
        return cast
    generic = re.search(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b", head)
    if generic and generic.group(1) not in {"Halden Bikes", "Fernwood Studio", "Pediatric Dentistry"}:
        return generic.group(1)
    tail = re.search(r"\b([A-Za-z]{2,})\s+([A-Za-z]{2,})\s*$", head.strip())
    if tail:
        blocked = {
            "for", "a", "an", "the", "at", "from", "to", "and", "or", "contact", "lead",
            "person", "new", "add", "create", "inbound", "please", "task", "company", "form", "fill",
        }
        first, last = tail.group(1), tail.group(2)
        if first.lower() not in blocked and last.lower() not in blocked:
            return f"{first.title()} {last.title()}"
    cast = _cast_name(_without_emails(text))
    if cast and cast.split()[0] not in {"Halden", "Sable", "Brightline", "Fernwood"}:
        return cast
    return ""


def _company(text: str) -> str:
    match = re.search(
        r"(?i)\b(?:at|company|from)\s+(.+?)(?:\.|,|\btask\b|\bassign\b|\bgive it\b|$)",
        text,
    )
    company = match.group(1).strip(" .'\"") if match else ""
    if company.lower().startswith("is "):
        company = company[3:].strip(" .'\"")
    return company


def _task_title(text: str) -> str:
    quoted = _quoted(text)
    if quoted:
        return quoted
    match = re.search(
        r"(?i)\btask(?:\s+is|\s+called)?\s+(.+?)(?:\s+and put|\s+for\s+\S+@|\s+assign\b|\s+give it\b|,|\.|$)",
        text,
    )
    if match:
        title = match.group(1).strip(" .")
        if title and title.lower() not in {"is", "called"}:
            return title
    return ""


def _deal_name(text: str) -> str:
    quoted = _quoted(text)
    if quoted:
        return quoted
    match = re.search(r"(?i)\bcalled\s+(.+?)(?:\s+for\s+\d|\s+stage\b|,|$)", text)
    return match.group(1).strip() if match else "New deal"


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
