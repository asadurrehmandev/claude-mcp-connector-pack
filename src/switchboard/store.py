"""In-memory workspace data. Every read and write is filtered by tenant id."""

from __future__ import annotations

import copy
from typing import Any

from switchboard.models import (
    Campaign,
    Channel,
    Contact,
    Deal,
    Member,
    MetricDay,
    Note,
    Page,
    Seed,
    SlackMessage,
    Task,
    Workspace,
)


class Store:
    def __init__(self, seed: Seed):
        self.workspaces = {item.id: item for item in seed.workspaces}
        self.contacts = {item.id: item for item in seed.contacts}
        self.deals = {item.id: item for item in seed.deals}
        self.notes = {item.id: item for item in seed.notes}
        self.campaigns = {item.id: item for item in seed.campaigns}
        self.metrics = list(seed.metrics)
        self.pages = {item.id: item for item in seed.pages}
        self.members = list(seed.members)
        self.tasks = {item.id: item for item in seed.tasks}
        self.channels = list(seed.channels)
        self.messages: list[SlackMessage] = []
        self.revision = 0
        self._seq = 5000

    def bump(self) -> None:
        self.revision += 1

    def next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "contacts": copy.deepcopy(self.contacts),
            "deals": copy.deepcopy(self.deals),
            "notes": copy.deepcopy(self.notes),
            "tasks": copy.deepcopy(self.tasks),
            "messages": copy.deepcopy(self.messages),
            "revision": self.revision,
            "seq": self._seq,
        }

    def restore(self, snap: dict[str, Any]) -> None:
        self.contacts = snap["contacts"]
        self.deals = snap["deals"]
        self.notes = snap["notes"]
        self.tasks = snap["tasks"]
        self.messages = snap["messages"]
        self.revision = snap["revision"]
        self._seq = snap["seq"]

    def workspace(self, tenant_id: str) -> Workspace | None:
        return self.workspaces.get(tenant_id)

    def contacts_for(self, tenant_id: str) -> list[Contact]:
        return sorted(
            (row for row in self.contacts.values() if row.tenant_id == tenant_id),
            key=lambda row: (row.last_name, row.first_name, row.id),
        )

    def contact(self, tenant_id: str, contact_id: str) -> Contact | None:
        row = self.contacts.get(contact_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    def deals_for(self, tenant_id: str) -> list[Deal]:
        return sorted(
            (row for row in self.deals.values() if row.tenant_id == tenant_id),
            key=lambda row: row.name,
        )

    def deal(self, tenant_id: str, deal_id: str) -> Deal | None:
        row = self.deals.get(deal_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    def notes_for(self, tenant_id: str, contact_id: str) -> list[Note]:
        return [row for row in self.notes.values() if row.tenant_id == tenant_id and row.contact_id == contact_id]

    def campaigns_for(self, tenant_id: str, platform: str) -> list[Campaign]:
        rows = [row for row in self.campaigns.values() if row.tenant_id == tenant_id]
        if platform != "all":
            rows = [row for row in rows if row.platform == platform]
        return sorted(rows, key=lambda row: row.id)

    def campaign(self, tenant_id: str, campaign_id: str) -> Campaign | None:
        row = self.campaigns.get(campaign_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    def metrics_between(
        self,
        tenant_id: str,
        start: str,
        end: str,
        platform: str,
        campaign_id: str,
    ) -> list[MetricDay]:
        allowed = {row.id for row in self.campaigns_for(tenant_id, platform)}
        if campaign_id:
            allowed = {campaign_id} & allowed
        return [
            row
            for row in self.metrics
            if row.tenant_id == tenant_id and row.campaign_id in allowed and start <= row.date <= end
        ]

    def pages_for(self, tenant_id: str) -> list[Page]:
        return sorted((row for row in self.pages.values() if row.tenant_id == tenant_id), key=lambda row: row.id)

    def page(self, tenant_id: str, page_id: str) -> Page | None:
        row = self.pages.get(page_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    def members_for(self, tenant_id: str) -> list[Member]:
        return sorted(
            (row for row in self.members if row.tenant_id == tenant_id),
            key=lambda row: row.email,
        )

    def tasks_for(self, tenant_id: str, status: str, assignee: str) -> list[Task]:
        rows = [row for row in self.tasks.values() if row.tenant_id == tenant_id]
        if status:
            rows = [row for row in rows if row.status == status]
        if assignee:
            rows = [row for row in rows if row.assignee_email.lower() == assignee.lower()]
        return sorted(rows, key=lambda row: row.id)

    def task(self, tenant_id: str, task_id: str) -> Task | None:
        row = self.tasks.get(task_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    def channels_for(self, tenant_id: str) -> list[Channel]:
        return sorted((row for row in self.channels if row.tenant_id == tenant_id), key=lambda row: row.name)
