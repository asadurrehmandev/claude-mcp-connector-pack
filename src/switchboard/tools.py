"""Tool handlers. Writes mutate a backend only when confirm is true."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable

from switchboard.ads_math import anomaly_flags, previous_window, sum_totals
from switchboard.constants import STAGES, TASK_STATUSES
from switchboard.errors import SwitchboardError

Handler = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class ToolSpec:
    name: str
    description: str
    scope: str
    write: bool
    destructive: bool
    handler: Handler


@dataclass
class CallCtx:
    tenant_id: str
    actor: str
    crm: Any
    ads: Any
    docs: Any
    projects: Any
    slack: Any


class Registry:
    def __init__(self) -> None:
        self.tools: dict[str, ToolSpec] = {}

    def tool(self, name: str, description: str, scope: str, *, write: bool = False, destructive: bool = False):
        def decorate(fn: Handler) -> Handler:
            self.tools[name] = ToolSpec(name, description, scope, write, destructive, fn)
            return fn

        return decorate


registry = Registry()


def _limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 25:
        raise SwitchboardError("validation", "limit must be an integer from 1 to 25")
    return value


def _date(value: str, field: str) -> date:
    # Calendar dates only. A timestamp or offset would shift the reporting day.
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise SwitchboardError(
            "validation",
            f"{field} must be a calendar date YYYY-MM-DD, not a timestamp or zoned datetime",
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SwitchboardError("validation", f"{field} must be YYYY-MM-DD") from exc


def _platform(value: str) -> str:
    if value not in {"google", "meta", "all"}:
        raise SwitchboardError("validation", "platform must be google, meta, or all")
    return value


def _member_emails(members: list[dict[str, Any]]) -> dict[str, str]:
    return {row["email"].lower(): row["email"] for row in members}


async def _require_assignee(ctx: CallCtx, email: str) -> str:
    email = email.strip()
    if not email or "@" not in email:
        raise SwitchboardError("validation", "assignee_email must be a workspace member's email")
    members = await ctx.projects.list_members(ctx.tenant_id)
    found = _member_emails(members).get(email.lower())
    if found is None:
        raise SwitchboardError(
            "invalid_assignee",
            "That email is not a member of this workspace.",
            {"assignee_email": email},
        )
    return found


@registry.tool(
    "crm_search_contacts",
    "Search contacts in the authenticated workspace by name, email, or company.",
    "crm.read",
)
async def crm_search_contacts(ctx: CallCtx, query: str = "", limit: int = 10) -> dict[str, Any]:
    if not str(query).strip():
        raise SwitchboardError("validation", "query is required")
    rows = await ctx.crm.search_contacts(ctx.tenant_id, query.strip(), _limit(limit))
    return {"contacts": rows}


@registry.tool("crm_get_contact", "Fetch one contact and the notes stored on it.", "crm.read")
async def crm_get_contact(ctx: CallCtx, contact_id: str = "") -> dict[str, Any]:
    if not contact_id:
        raise SwitchboardError("validation", "contact_id is required")
    row = await ctx.crm.get_contact(ctx.tenant_id, contact_id)
    if row is None:
        raise SwitchboardError("not_found", "No contact with that id is in this workspace.")
    notes = await ctx.crm.notes_for(ctx.tenant_id, contact_id)
    return {"contact": row, "notes": notes}


@registry.tool(
    "crm_upsert_contact",
    "Create or update a contact. Matching email updates the existing record. confirm must be true to write.",
    "crm.write",
    write=True,
)
async def crm_upsert_contact(
    ctx: CallCtx,
    email: str = "",
    first_name: str = "",
    last_name: str = "",
    company: str = "",
    phone: str = "",
    contact_id: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    email = email.strip()
    if "@" not in email or not first_name.strip() or not last_name.strip():
        raise SwitchboardError("validation", "email, first_name, and last_name are required")
    existing = None
    if contact_id:
        existing = await ctx.crm.get_contact(ctx.tenant_id, contact_id)
        if existing is None:
            raise SwitchboardError("not_found", "No contact with that id is in this workspace.")
    else:
        existing = await ctx.crm.find_contact_by_email(ctx.tenant_id, email)
    action = "update" if existing else "create"
    preview = {
        "action": action,
        "id": existing["id"] if existing else "",
        "email": email,
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "company": company.strip(),
        "phone": phone.strip(),
    }
    if confirm is not True:
        return {"dry_run": True, "would_change": preview}
    saved = await ctx.crm.save_contact(
        ctx.tenant_id,
        {**preview, "id": existing["id"] if existing else ""},
    )
    return {"dry_run": False, "contact": saved}


@registry.tool("crm_search_deals", "Search deals in the authenticated workspace by name or stage.", "crm.read")
async def crm_search_deals(ctx: CallCtx, query: str = "", limit: int = 10) -> dict[str, Any]:
    if not str(query).strip():
        raise SwitchboardError("validation", "query is required")
    rows = await ctx.crm.search_deals(ctx.tenant_id, query.strip(), _limit(limit))
    return {"deals": rows}


@registry.tool("crm_get_deal", "Fetch one deal by id.", "crm.read")
async def crm_get_deal(ctx: CallCtx, deal_id: str = "") -> dict[str, Any]:
    if not deal_id:
        raise SwitchboardError("validation", "deal_id is required")
    row = await ctx.crm.get_deal(ctx.tenant_id, deal_id)
    if row is None:
        raise SwitchboardError("not_found", "No deal with that id is in this workspace.")
    return {"deal": row}


@registry.tool(
    "crm_upsert_deal",
    "Create or update a deal. amount_cents is an integer. confirm must be true to write.",
    "crm.write",
    write=True,
)
async def crm_upsert_deal(
    ctx: CallCtx,
    deal_name: str = "",
    amount_cents: int = 0,
    stage: str = "",
    contact_id: str = "",
    deal_id: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    if not deal_name.strip():
        raise SwitchboardError("validation", "deal_name is required")
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents < 0:
        raise SwitchboardError("validation", "amount_cents must be a non-negative integer")
    if stage not in STAGES:
        raise SwitchboardError("validation", "stage must be one of: " + ", ".join(STAGES))
    if contact_id:
        contact = await ctx.crm.get_contact(ctx.tenant_id, contact_id)
        if contact is None:
            raise SwitchboardError("not_found", "contact_id is not in this workspace.")
    existing = None
    if deal_id:
        existing = await ctx.crm.get_deal(ctx.tenant_id, deal_id)
        if existing is None:
            raise SwitchboardError("not_found", "No deal with that id is in this workspace.")
    preview = {
        "action": "update" if existing else "create",
        "id": existing["id"] if existing else "",
        "name": deal_name.strip(),
        "amount_cents": amount_cents,
        "stage": stage,
        "contact_id": contact_id,
    }
    if confirm is not True:
        return {"dry_run": True, "would_change": preview}
    saved = await ctx.crm.save_deal(ctx.tenant_id, preview)
    return {"dry_run": False, "deal": saved}


@registry.tool(
    "crm_log_note",
    "Attach a note to a contact. The note text is stored as data. confirm must be true to write.",
    "crm.write",
    write=True,
)
async def crm_log_note(ctx: CallCtx, contact_id: str = "", body: str = "", confirm: bool = False) -> dict[str, Any]:
    if not contact_id:
        raise SwitchboardError("validation", "contact_id is required")
    text = body.strip()
    if not text or len(text) > 4000:
        raise SwitchboardError("validation", "body must be between 1 and 4000 characters")
    contact = await ctx.crm.get_contact(ctx.tenant_id, contact_id)
    if contact is None:
        raise SwitchboardError("not_found", "No contact with that id is in this workspace.")
    preview = {"contact_id": contact_id, "body": text}
    if confirm is not True:
        return {"dry_run": True, "would_change": preview}
    note = await ctx.crm.log_note(ctx.tenant_id, contact_id, text)
    return {"dry_run": False, "note": note}


@registry.tool("ads_list_campaigns", "List campaigns for the workspace. platform is google, meta, or all.", "ads.read")
async def ads_list_campaigns(ctx: CallCtx, platform: str = "all") -> dict[str, Any]:
    rows = await ctx.ads.list_campaigns(ctx.tenant_id, _platform(platform))
    return {"campaigns": rows}


@registry.tool(
    "ads_campaign_metrics",
    "Sum spend, clicks, impressions, and conversions for an inclusive date range, then compute CTR, CPA, and anomaly flags against the previous window of equal length.",
    "ads.read",
)
async def ads_campaign_metrics(
    ctx: CallCtx,
    start_date: str = "",
    end_date: str = "",
    platform: str = "all",
    campaign_id: str = "",
) -> dict[str, Any]:
    return await _metrics(ctx, start_date, end_date, platform, campaign_id)


@registry.tool(
    "ads_account_summary",
    "Account-level spend, CTR, CPA, and anomaly flags for an inclusive date range. Same math as ads_campaign_metrics across every campaign.",
    "ads.read",
)
async def ads_account_summary(
    ctx: CallCtx,
    start_date: str = "",
    end_date: str = "",
    platform: str = "all",
) -> dict[str, Any]:
    return await _metrics(ctx, start_date, end_date, platform, "")


async def _metrics(ctx: CallCtx, start_date: str, end_date: str, platform: str, campaign_id: str) -> dict[str, Any]:
    start = _date(start_date, "start_date")
    end = _date(end_date, "end_date")
    if end < start:
        raise SwitchboardError("validation", "end_date is before start_date")
    if (end - start).days > 366:
        raise SwitchboardError("validation", "date range cannot exceed 366 days")
    chosen = _platform(platform)
    if campaign_id:
        campaign = await ctx.ads.get_campaign(ctx.tenant_id, campaign_id)
        if campaign is None:
            raise SwitchboardError("not_found", "No campaign with that id is in this workspace.")
    prev_start, prev_end = previous_window(start, end)
    current_rows = await ctx.ads.daily_rows(ctx.tenant_id, start.isoformat(), end.isoformat(), chosen, campaign_id)
    previous_rows = await ctx.ads.daily_rows(
        ctx.tenant_id, prev_start.isoformat(), prev_end.isoformat(), chosen, campaign_id
    )
    current = sum_totals(current_rows)
    previous = sum_totals(previous_rows)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "previous_start_date": prev_start.isoformat(),
        "previous_end_date": prev_end.isoformat(),
        "platform": chosen,
        "campaign_id": campaign_id,
        "current": current.as_dict(),
        "previous": previous.as_dict(),
        "flags": anomaly_flags(current, previous),
        "row_count": len(current_rows),
    }


@registry.tool("docs_search", "Search the workspace knowledge pages. Returns titles, urls, and snippets.", "docs.read")
async def docs_search(ctx: CallCtx, query: str = "", limit: int = 5) -> dict[str, Any]:
    if not str(query).strip():
        raise SwitchboardError("validation", "query is required")
    pages = await ctx.docs.search(ctx.tenant_id, query.strip(), _limit(limit))
    return {"pages": pages}


@registry.tool(
    "docs_fetch",
    "Fetch one knowledge page and a citation (title, url, page id, source).",
    "docs.read",
)
async def docs_fetch(ctx: CallCtx, page_id: str = "") -> dict[str, Any]:
    if not page_id:
        raise SwitchboardError("validation", "page_id is required")
    page = await ctx.docs.fetch(ctx.tenant_id, page_id)
    if page is None:
        raise SwitchboardError("not_found", "No page with that id is in this workspace.")
    return {
        "page_id": page["id"],
        "title": page["title"],
        "body": page["body"],
        "excerpt": page["body"][:500],
        "citation": {
            "title": page["title"],
            "url": page["url"],
            "page_id": page["id"],
            "source": "notion",
        },
    }


@registry.tool("projects_list_tasks", "List tasks in the workspace. status may be empty or a known status.", "projects.read")
async def projects_list_tasks(
    ctx: CallCtx,
    status: str = "",
    assignee_email: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    if status and status not in TASK_STATUSES:
        raise SwitchboardError("validation", "status must be one of: " + ", ".join(TASK_STATUSES))
    rows = await ctx.projects.list_tasks(ctx.tenant_id, status, assignee_email.strip(), _limit(limit))
    return {"tasks": rows}


@registry.tool("projects_get_task", "Fetch one task by id.", "projects.read")
async def projects_get_task(ctx: CallCtx, task_id: str = "") -> dict[str, Any]:
    if not task_id:
        raise SwitchboardError("validation", "task_id is required")
    row = await ctx.projects.get_task(ctx.tenant_id, task_id)
    if row is None:
        raise SwitchboardError("not_found", "No task with that id is in this workspace.")
    return {"task": row}


@registry.tool("projects_list_members", "List people who can be assigned work in this workspace.", "projects.read")
async def projects_list_members(ctx: CallCtx) -> dict[str, Any]:
    return {"members": await ctx.projects.list_members(ctx.tenant_id)}


@registry.tool(
    "projects_create_task",
    "Create a task. assignee_email must already be a member of this workspace. confirm must be true to write.",
    "projects.write",
    write=True,
)
async def projects_create_task(
    ctx: CallCtx,
    name: str = "",
    assignee_email: str = "",
    due_date: str = "",
    notes: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    title = name.strip()
    if not title:
        raise SwitchboardError("validation", "name is required")
    if due_date:
        _date(due_date, "due_date")
    assignee = await _require_assignee(ctx, assignee_email)
    preview = {
        "name": title,
        "assignee_email": assignee,
        "due_date": due_date,
        "notes": notes.strip(),
        "status": "open",
    }
    if confirm is not True:
        return {"dry_run": True, "would_change": preview}
    task = await ctx.projects.create_task(ctx.tenant_id, preview)
    return {"dry_run": False, "task": task}


@registry.tool(
    "projects_update_task",
    "Update a task's name, status, or assignee. The assignee must be a member. confirm must be true to write.",
    "projects.write",
    write=True,
    destructive=True,
)
async def projects_update_task(
    ctx: CallCtx,
    task_id: str = "",
    name: str = "",
    status: str = "",
    assignee_email: str = "",
    due_date: str = "",
    notes: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    if not task_id:
        raise SwitchboardError("validation", "task_id is required")
    existing = await ctx.projects.get_task(ctx.tenant_id, task_id)
    if existing is None:
        raise SwitchboardError("not_found", "No task with that id is in this workspace.")
    if status and status not in TASK_STATUSES:
        raise SwitchboardError("validation", "status must be one of: " + ", ".join(TASK_STATUSES))
    if due_date:
        _date(due_date, "due_date")
    if not any([name.strip(), status, assignee_email.strip(), due_date, notes.strip()]):
        raise SwitchboardError("validation", "Provide at least one field to change")
    assignee = existing["assignee_email"]
    if assignee_email.strip():
        assignee = await _require_assignee(ctx, assignee_email)
    preview = {
        "id": task_id,
        "name": name.strip() or existing["name"],
        "status": status or existing["status"],
        "assignee_email": assignee,
        "due_date": due_date or existing.get("due_date", ""),
        "notes": notes.strip() or existing.get("notes", ""),
    }
    if confirm is not True:
        return {"dry_run": True, "would_change": preview}
    updated = await ctx.projects.update_task(ctx.tenant_id, task_id, preview)
    return {"dry_run": False, "task": updated}


@registry.tool("slack_list_channels", "List the Slack channels this workspace is allowed to post to.", "slack.read")
async def slack_list_channels(ctx: CallCtx) -> dict[str, Any]:
    return {"channels": await ctx.slack.list_channels(ctx.tenant_id)}


@registry.tool(
    "slack_notify",
    "Post a message to an allowlisted channel. Channels outside the allowlist are rejected even when confirm is true.",
    "slack.write",
    write=True,
)
async def slack_notify(ctx: CallCtx, channel: str = "", text: str = "", confirm: bool = False) -> dict[str, Any]:
    channel = channel.strip()
    text = text.strip()
    if not channel.startswith("#") or not text:
        raise SwitchboardError("validation", "channel must start with # and text is required")
    if len(text) > 3000:
        raise SwitchboardError("validation", "text must be 3000 characters or fewer")
    allowed = {row["name"] for row in await ctx.slack.list_channels(ctx.tenant_id)}
    if channel not in allowed:
        raise SwitchboardError(
            "channel_not_allowed",
            "That channel is not on the workspace allowlist.",
            {"allowed": sorted(allowed)},
        )
    preview = {"channel": channel, "text": text}
    if confirm is not True:
        return {"dry_run": True, "would_change": preview}
    sent = await ctx.slack.post(ctx.tenant_id, channel, text)
    return {"dry_run": False, "message": sent}
