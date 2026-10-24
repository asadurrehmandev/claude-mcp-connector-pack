"""Connector backends. Mock implementations read the tenant-scoped store.

Live clients are in live.py. Handlers depend only on these method names.
"""

from __future__ import annotations

from typing import Any

from switchboard.models import MetricDay
from switchboard.store import Store


def _contact(row) -> dict[str, Any]:
    return row.model_dump()


class MockCrm:
    def __init__(self, store: Store):
        self.store = store

    async def search_contacts(self, tenant_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        needle = query.lower()
        hits = []
        for row in self.store.contacts_for(tenant_id):
            hay = " ".join([row.email, row.first_name, row.last_name, row.company]).lower()
            if needle in hay:
                hits.append(_contact(row))
        return hits[:limit]

    async def get_contact(self, tenant_id: str, contact_id: str) -> dict[str, Any] | None:
        row = self.store.contact(tenant_id, contact_id)
        return None if row is None else _contact(row)

    async def find_contact_by_email(self, tenant_id: str, email: str) -> dict[str, Any] | None:
        for row in self.store.contacts_for(tenant_id):
            if row.email.lower() == email.lower():
                return _contact(row)
        return None

    async def save_contact(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        contact_id = payload.get("id") or self.store.next_id("con")
        from switchboard.models import Contact

        row = Contact(
            id=contact_id,
            tenant_id=tenant_id,
            email=payload["email"],
            first_name=payload["first_name"],
            last_name=payload["last_name"],
            company=payload.get("company", ""),
            phone=payload.get("phone", ""),
        )
        self.store.contacts[row.id] = row
        self.store.bump()
        return _contact(row)

    async def search_deals(self, tenant_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        needle = query.lower()
        hits = []
        for row in self.store.deals_for(tenant_id):
            if needle in row.name.lower() or needle in row.stage:
                hits.append(row.model_dump())
        return hits[:limit]

    async def get_deal(self, tenant_id: str, deal_id: str) -> dict[str, Any] | None:
        row = self.store.deal(tenant_id, deal_id)
        return None if row is None else row.model_dump()

    async def save_deal(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from switchboard.models import Deal

        deal_id = payload.get("id") or self.store.next_id("deal")
        row = Deal(
            id=deal_id,
            tenant_id=tenant_id,
            name=payload["name"],
            amount_cents=int(payload["amount_cents"]),
            stage=payload["stage"],
            contact_id=payload.get("contact_id", ""),
        )
        self.store.deals[row.id] = row
        self.store.bump()
        return row.model_dump()

    async def log_note(self, tenant_id: str, contact_id: str, body: str) -> dict[str, Any]:
        from switchboard.models import Note

        row = Note(
            id=self.store.next_id("note"),
            tenant_id=tenant_id,
            contact_id=contact_id,
            body=body,
            created_at="2026-03-16T12:00:00Z",
        )
        self.store.notes[row.id] = row
        self.store.bump()
        return row.model_dump()

    async def notes_for(self, tenant_id: str, contact_id: str) -> list[dict[str, Any]]:
        return [row.model_dump() for row in self.store.notes_for(tenant_id, contact_id)]


class MockAds:
    def __init__(self, store: Store):
        self.store = store

    async def list_campaigns(self, tenant_id: str, platform: str) -> list[dict[str, Any]]:
        return [row.model_dump() for row in self.store.campaigns_for(tenant_id, platform)]

    async def daily_rows(
        self,
        tenant_id: str,
        start: str,
        end: str,
        platform: str,
        campaign_id: str,
    ) -> list[MetricDay]:
        return self.store.metrics_between(tenant_id, start, end, platform, campaign_id)

    async def get_campaign(self, tenant_id: str, campaign_id: str) -> dict[str, Any] | None:
        row = self.store.campaign(tenant_id, campaign_id)
        return None if row is None else row.model_dump()


class MockDocs:
    def __init__(self, store: Store):
        self.store = store

    async def search(self, tenant_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        tokens = [part.lower() for part in query.split() if part.strip()]
        scored: list[tuple[int, str, str, dict[str, Any]]] = []
        for page in self.store.pages_for(tenant_id):
            title = page.title.lower()
            body = page.body.lower()
            score = 0
            for token in tokens:
                if token in title:
                    score += 3
                if token in body:
                    score += 1
            if score <= 0:
                continue
            scored.append(
                (
                    -score,
                    page.title,
                    page.id,
                    {
                        "id": page.id,
                        "title": page.title,
                        "url": page.url,
                        "score": score,
                        "snippet": page.body[:180],
                    },
                )
            )
        scored.sort()
        return [item[3] for item in scored[:limit]]

    async def fetch(self, tenant_id: str, page_id: str) -> dict[str, Any] | None:
        page = self.store.page(tenant_id, page_id)
        if page is None:
            return None
        return page.model_dump()


class MockProjects:
    def __init__(self, store: Store):
        self.store = store

    async def list_tasks(self, tenant_id: str, status: str, assignee: str, limit: int) -> list[dict[str, Any]]:
        return [row.model_dump() for row in self.store.tasks_for(tenant_id, status, assignee)[:limit]]

    async def get_task(self, tenant_id: str, task_id: str) -> dict[str, Any] | None:
        row = self.store.task(tenant_id, task_id)
        return None if row is None else row.model_dump()

    async def list_members(self, tenant_id: str) -> list[dict[str, Any]]:
        return [row.model_dump() for row in self.store.members_for(tenant_id)]

    async def create_task(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from switchboard.models import Task

        row = Task(
            id=self.store.next_id("task"),
            tenant_id=tenant_id,
            name=payload["name"],
            status="open",
            assignee_email=payload["assignee_email"],
            due_date=payload.get("due_date", ""),
            notes=payload.get("notes", ""),
        )
        self.store.tasks[row.id] = row
        self.store.bump()
        return row.model_dump()

    async def update_task(self, tenant_id: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        row = self.store.task(tenant_id, task_id)
        if row is None:
            return None
        data = row.model_dump()
        for key in ("name", "status", "assignee_email", "due_date", "notes"):
            if payload.get(key):
                data[key] = payload[key]
        from switchboard.models import Task

        updated = Task(**data)
        self.store.tasks[updated.id] = updated
        self.store.bump()
        return updated.model_dump()


class MockSlack:
    def __init__(self, store: Store):
        self.store = store

    async def list_channels(self, tenant_id: str) -> list[dict[str, str]]:
        return [{"name": row.name} for row in self.store.channels_for(tenant_id)]

    async def post(self, tenant_id: str, channel: str, text: str) -> dict[str, str]:
        from switchboard.models import SlackMessage

        message = SlackMessage(tenant_id=tenant_id, channel=channel, text=text, ts="1710000000.0001")
        self.store.messages.append(message)
        self.store.bump()
        return {"channel": channel, "ts": message.ts}
