"""HTTP clients for the real vendor APIs. Tests drive these with httpx.MockTransport.

Tenant isolation: each call uses the customer, list, page, or channel mapped to
that workspace. A missing map raises not_configured instead of querying a
shared account.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

import httpx

from switchboard.errors import SwitchboardError
from switchboard.models import MetricDay
from switchboard.settings import Settings

HUBSPOT = "https://api.hubapi.com"
GOOGLE_ADS = "https://googleads.googleapis.com/v18"
META = "https://graph.facebook.com/v21.0"
NOTION = "https://api.notion.com/v1"
CLICKUP = "https://api.clickup.com/api/v2"
SLACK = "https://slack.com/api"
NOTION_VERSION = "2022-06-28"
_META_CONVERSIONS = ("purchase", "lead", "complete_registration")


def _cents_from_micros(micros: int) -> int:
    return int((Decimal(micros) / Decimal(10_000)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def _cents_from_dollars(amount: str) -> int:
    return int((Decimal(amount) * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def google_rows_from_payload(payload: Any, tenant_id: str) -> list[MetricDay]:
    batches = payload if isinstance(payload, list) else [payload]
    rows: list[MetricDay] = []
    for batch in batches:
        for item in batch.get("results", []):
            metrics = item.get("metrics", {})
            actions = Decimal(str(metrics.get("conversions", 0) or 0))
            rows.append(
                MetricDay(
                    tenant_id=tenant_id,
                    campaign_id=f"gads_{item['campaign']['id']}",
                    date=item["segments"]["date"],
                    impressions=int(metrics.get("impressions", 0)),
                    clicks=int(metrics.get("clicks", 0)),
                    spend_cents=_cents_from_micros(int(metrics.get("costMicros", 0))),
                    conversions=int(actions.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)),
                )
            )
    return rows


def meta_rows_from_payload(payload: dict[str, Any], tenant_id: str) -> list[MetricDay]:
    rows: list[MetricDay] = []
    for item in payload.get("data", []):
        conversions = 0
        for action in item.get("actions", []):
            if action.get("action_type") in _META_CONVERSIONS:
                conversions += int(Decimal(str(action.get("value", "0"))).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
        rows.append(
            MetricDay(
                tenant_id=tenant_id,
                campaign_id=f"meta_{item['campaign_id']}",
                date=item["date_start"],
                impressions=int(item.get("impressions", 0)),
                clicks=int(item.get("clicks", 0)),
                spend_cents=_cents_from_dollars(str(item.get("spend", "0"))),
                conversions=conversions,
            )
        )
    return rows


class LiveCrm:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.hubspot_access_token}"}

    async def search_contacts(self, tenant_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "switchboard_tenant", "operator": "EQ", "value": tenant_id},
                    ]
                }
            ],
            "query": query,
            "limit": limit,
            "properties": ["email", "firstname", "lastname", "company", "phone", "switchboard_tenant"],
        }
        response = await self.client.post(f"{HUBSPOT}/crm/v3/objects/contacts/search", headers=self._headers(), json=body)
        _raise_for_status(response, "hubspot")
        return [_hubspot_contact(row) for row in response.json().get("results", []) if row.get("properties", {}).get("switchboard_tenant") == tenant_id]

    async def get_contact(self, tenant_id: str, contact_id: str) -> dict[str, Any] | None:
        response = await self.client.get(
            f"{HUBSPOT}/crm/v3/objects/contacts/{contact_id}",
            headers=self._headers(),
            params={"properties": "email,firstname,lastname,company,phone,switchboard_tenant"},
        )
        if response.status_code == 404:
            return None
        _raise_for_status(response, "hubspot")
        row = _hubspot_contact(response.json())
        if row.get("tenant_id") != tenant_id:
            return None
        return row

    async def find_contact_by_email(self, tenant_id: str, email: str) -> dict[str, Any] | None:
        rows = await self.search_contacts(tenant_id, email, 10)
        for row in rows:
            if row["email"].lower() == email.lower():
                return row
        return None

    async def save_contact(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        properties = {
            "email": payload["email"],
            "firstname": payload["first_name"],
            "lastname": payload["last_name"],
            "company": payload.get("company", ""),
            "phone": payload.get("phone", ""),
            "switchboard_tenant": tenant_id,
        }
        if payload.get("id"):
            response = await self.client.patch(
                f"{HUBSPOT}/crm/v3/objects/contacts/{payload['id']}",
                headers=self._headers(),
                json={"properties": properties},
            )
        else:
            response = await self.client.post(
                f"{HUBSPOT}/crm/v3/objects/contacts",
                headers=self._headers(),
                json={"properties": properties},
            )
        _raise_for_status(response, "hubspot")
        return _hubspot_contact(response.json())

    async def search_deals(self, tenant_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "switchboard_tenant", "operator": "EQ", "value": tenant_id}]}],
            "query": query,
            "limit": limit,
            "properties": ["dealname", "amount", "dealstage", "switchboard_tenant", "switchboard_contact_id"],
        }
        response = await self.client.post(f"{HUBSPOT}/crm/v3/objects/deals/search", headers=self._headers(), json=body)
        _raise_for_status(response, "hubspot")
        return [_hubspot_deal(row) for row in response.json().get("results", [])]

    async def get_deal(self, tenant_id: str, deal_id: str) -> dict[str, Any] | None:
        response = await self.client.get(
            f"{HUBSPOT}/crm/v3/objects/deals/{deal_id}",
            headers=self._headers(),
            params={"properties": "dealname,amount,dealstage,switchboard_tenant,switchboard_contact_id"},
        )
        if response.status_code == 404:
            return None
        _raise_for_status(response, "hubspot")
        row = _hubspot_deal(response.json())
        return None if row.get("tenant_id") != tenant_id else row

    async def save_deal(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        properties = {
            "dealname": payload["name"],
            "amount": f"{Decimal(payload['amount_cents']) / Decimal(100):.2f}",
            "dealstage": payload["stage"],
            "switchboard_tenant": tenant_id,
            "switchboard_contact_id": payload.get("contact_id", ""),
        }
        if payload.get("id"):
            response = await self.client.patch(
                f"{HUBSPOT}/crm/v3/objects/deals/{payload['id']}",
                headers=self._headers(),
                json={"properties": properties},
            )
        else:
            response = await self.client.post(
                f"{HUBSPOT}/crm/v3/objects/deals",
                headers=self._headers(),
                json={"properties": properties},
            )
        _raise_for_status(response, "hubspot")
        return _hubspot_deal(response.json())

    async def log_note(self, tenant_id: str, contact_id: str, body: str) -> dict[str, Any]:
        response = await self.client.post(
            f"{HUBSPOT}/crm/v3/objects/notes",
            headers=self._headers(),
            json={
                "properties": {
                    "hs_note_body": body,
                    "switchboard_tenant": tenant_id,
                },
                "associations": [
                    {
                        "to": {"id": contact_id},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                    }
                ],
            },
        )
        _raise_for_status(response, "hubspot")
        data = response.json()
        return {"id": data.get("id", ""), "tenant_id": tenant_id, "contact_id": contact_id, "body": body}

    async def notes_for(self, tenant_id: str, contact_id: str) -> list[dict[str, Any]]:
        return []


class LiveAds:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def list_campaigns(self, tenant_id: str, platform: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if platform in {"google", "all"} and tenant_id in self.settings.google_ads_customer_map:
            customer = self.settings.google_ads_customer_map[tenant_id]
            query = "SELECT campaign.id, campaign.name, campaign.status FROM campaign ORDER BY campaign.id"
            payload = await self._google(customer, query)
            for batch in payload if isinstance(payload, list) else [payload]:
                for item in batch.get("results", []):
                    rows.append(
                        {
                            "id": f"gads_{item['campaign']['id']}",
                            "tenant_id": tenant_id,
                            "platform": "google",
                            "name": item["campaign"]["name"],
                            "status": str(item["campaign"].get("status", "")).lower(),
                        }
                    )
        if platform in {"meta", "all"} and tenant_id in self.settings.meta_account_map:
            account = self.settings.meta_account_map[tenant_id]
            response = await self.client.get(
                f"{META}/{account}/campaigns",
                params={"fields": "id,name,status", "access_token": self.settings.meta_access_token},
            )
            _raise_for_status(response, "meta")
            for item in response.json().get("data", []):
                rows.append(
                    {
                        "id": f"meta_{item['id']}",
                        "tenant_id": tenant_id,
                        "platform": "meta",
                        "name": item["name"],
                        "status": str(item.get("status", "")).lower(),
                    }
                )
        if not rows and platform == "all":
            self._require_any(tenant_id)
        return rows

    async def daily_rows(
        self,
        tenant_id: str,
        start: str,
        end: str,
        platform: str,
        campaign_id: str,
    ) -> list[MetricDay]:
        rows: list[MetricDay] = []
        if platform in {"google", "all"}:
            if tenant_id not in self.settings.google_ads_customer_map:
                if platform == "google":
                    raise SwitchboardError("not_configured", "No Google Ads customer is mapped to this workspace.")
            else:
                customer = self.settings.google_ads_customer_map[tenant_id]
                where = f"segments.date BETWEEN '{start}' AND '{end}'"
                if campaign_id.startswith("gads_"):
                    where += f" AND campaign.id = {campaign_id.removeprefix('gads_')}"
                query = (
                    "SELECT campaign.id, campaign.name, segments.date, metrics.impressions, "
                    "metrics.clicks, metrics.cost_micros, metrics.conversions "
                    f"FROM campaign WHERE {where}"
                )
                rows.extend(google_rows_from_payload(await self._google(customer, query), tenant_id))
        if platform in {"meta", "all"}:
            if tenant_id not in self.settings.meta_account_map:
                if platform == "meta":
                    raise SwitchboardError("not_configured", "No Meta ad account is mapped to this workspace.")
            else:
                account = self.settings.meta_account_map[tenant_id]
                params = {
                    "level": "campaign",
                    "time_increment": 1,
                    "time_range": f'{{"since":"{start}","until":"{end}"}}',
                    "fields": "campaign_id,campaign_name,impressions,clicks,spend,actions,date_start",
                    "access_token": self.settings.meta_access_token,
                }
                response = await self.client.get(f"{META}/{account}/insights", params=params)
                _raise_for_status(response, "meta")
                parsed = meta_rows_from_payload(response.json(), tenant_id)
                if campaign_id:
                    parsed = [row for row in parsed if row.campaign_id == campaign_id]
                rows.extend(parsed)
        if platform == "all" and tenant_id not in self.settings.google_ads_customer_map and tenant_id not in self.settings.meta_account_map:
            self._require_any(tenant_id)
        return rows

    async def get_campaign(self, tenant_id: str, campaign_id: str) -> dict[str, Any] | None:
        platform = "google" if campaign_id.startswith("gads_") else "meta" if campaign_id.startswith("meta_") else "all"
        for row in await self.list_campaigns(tenant_id, platform):
            if row["id"] == campaign_id:
                return row
        return None

    async def _google(self, customer_id: str, query: str) -> Any:
        response = await self.client.post(
            f"{GOOGLE_ADS}/customers/{customer_id}/googleAds:search",
            headers={
                "Authorization": f"Bearer {self.settings.google_ads_access_token}",
                "developer-token": self.settings.google_ads_developer_token,
            },
            json={"query": query},
        )
        _raise_for_status(response, "google_ads")
        return response.json()

    def _require_any(self, tenant_id: str) -> None:
        raise SwitchboardError("not_configured", "No ads account is mapped to this workspace.", {"tenant_id": tenant_id})


class LiveDocs:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.notion_token}",
            "Notion-Version": NOTION_VERSION,
        }

    def _allowed(self, tenant_id: str) -> set[str]:
        pages = self.settings.notion_page_map.get(tenant_id) or []
        if not pages:
            raise SwitchboardError("not_configured", "No Notion pages are mapped to this workspace.")
        return set(pages)

    async def search(self, tenant_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        allowed = self._allowed(tenant_id)
        response = await self.client.post(
            f"{NOTION}/search",
            headers=self._headers(),
            json={"query": query, "page_size": limit, "filter": {"property": "object", "value": "page"}},
        )
        _raise_for_status(response, "notion")
        hits = []
        for page in response.json().get("results", []):
            if page.get("id") not in allowed:
                continue
            title = _notion_title(page)
            hits.append(
                {
                    "id": page["id"],
                    "title": title,
                    "url": page.get("url", ""),
                    "score": 1,
                    "snippet": title,
                }
            )
        return hits[:limit]

    async def fetch(self, tenant_id: str, page_id: str) -> dict[str, Any] | None:
        if page_id not in self._allowed(tenant_id):
            return None
        page = await self.client.get(f"{NOTION}/pages/{page_id}", headers=self._headers())
        if page.status_code == 404:
            return None
        _raise_for_status(page, "notion")
        blocks = await self.client.get(f"{NOTION}/blocks/{page_id}/children", headers=self._headers())
        _raise_for_status(blocks, "notion")
        body = _notion_plain(blocks.json())
        data = page.json()
        return {"id": page_id, "tenant_id": tenant_id, "title": _notion_title(data), "url": data.get("url", ""), "body": body}


class LiveProjects:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def _list_id(self, tenant_id: str) -> str:
        list_id = self.settings.clickup_list_map.get(tenant_id, "")
        if not list_id:
            raise SwitchboardError("not_configured", "No ClickUp list is mapped to this workspace.")
        return list_id

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.settings.clickup_token}

    async def list_members(self, tenant_id: str) -> list[dict[str, Any]]:
        list_id = self._list_id(tenant_id)
        response = await self.client.get(f"{CLICKUP}/list/{list_id}/member", headers=self._headers())
        _raise_for_status(response, "clickup")
        members = []
        for row in response.json().get("members", []):
            members.append(
                {
                    "id": row.get("id", ""),
                    "tenant_id": tenant_id,
                    "email": row.get("email", ""),
                    "name": row.get("username", ""),
                    "role": "member",
                }
            )
        return members

    async def list_tasks(self, tenant_id: str, status: str, assignee: str, limit: int) -> list[dict[str, Any]]:
        list_id = self._list_id(tenant_id)
        response = await self.client.get(f"{CLICKUP}/list/{list_id}/task", headers=self._headers())
        _raise_for_status(response, "clickup")
        rows = [_clickup_task(item, tenant_id) for item in response.json().get("tasks", [])]
        if status:
            rows = [row for row in rows if row["status"] == status]
        if assignee:
            rows = [row for row in rows if row["assignee_email"].lower() == assignee.lower()]
        return rows[:limit]

    async def get_task(self, tenant_id: str, task_id: str) -> dict[str, Any] | None:
        self._list_id(tenant_id)
        response = await self.client.get(f"{CLICKUP}/task/{task_id}", headers=self._headers())
        if response.status_code == 404:
            return None
        _raise_for_status(response, "clickup")
        row = _clickup_task(response.json(), tenant_id)
        expected = self.settings.clickup_list_map[tenant_id]
        if str(response.json().get("list", {}).get("id", "")) != str(expected):
            return None
        return row

    async def create_task(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        list_id = self._list_id(tenant_id)
        assignee_ids = [
            member["id"]
            for member in await self.list_members(tenant_id)
            if member.get("id") and member["email"].lower() == payload["assignee_email"].lower()
        ]
        response = await self.client.post(
            f"{CLICKUP}/list/{list_id}/task",
            headers=self._headers(),
            json={
                "name": payload["name"],
                "description": payload.get("notes", ""),
                "status": "open",
                "due_date": payload.get("due_date") or None,
                "assignees": assignee_ids,
            },
        )
        _raise_for_status(response, "clickup")
        task = _clickup_task(response.json(), tenant_id)
        task["assignee_email"] = payload["assignee_email"]
        return task

    async def update_task(self, tenant_id: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        existing = await self.get_task(tenant_id, task_id)
        if existing is None:
            return None
        body: dict[str, Any] = {}
        if payload.get("name"):
            body["name"] = payload["name"]
        if payload.get("status"):
            body["status"] = payload["status"]
        if payload.get("notes"):
            body["description"] = payload["notes"]
        response = await self.client.put(f"{CLICKUP}/task/{task_id}", headers=self._headers(), json=body)
        _raise_for_status(response, "clickup")
        updated = _clickup_task(response.json(), tenant_id)
        if payload.get("assignee_email"):
            updated["assignee_email"] = payload["assignee_email"]
        return updated


class LiveSlack:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def list_channels(self, tenant_id: str) -> list[dict[str, str]]:
        names = self.settings.slack_allowlist.get(tenant_id) or []
        if not names:
            raise SwitchboardError("not_configured", "No Slack channels are allowlisted for this workspace.")
        return [{"name": name} for name in names]

    async def post(self, tenant_id: str, channel: str, text: str) -> dict[str, str]:
        allowed = {row["name"] for row in await self.list_channels(tenant_id)}
        if channel not in allowed:
            raise SwitchboardError("channel_not_allowed", "That channel is not on the workspace allowlist.")
        response = await self.client.post(
            f"{SLACK}/chat.postMessage",
            headers={"Authorization": f"Bearer {self.settings.slack_bot_token}"},
            json={"channel": channel, "text": text},
        )
        _raise_for_status(response, "slack")
        data = response.json()
        if not data.get("ok"):
            raise SwitchboardError("upstream", "Slack refused the message.", {"slack_error": data.get("error", "")})
        return {"channel": channel, "ts": str(data.get("ts", ""))}


def _raise_for_status(response: httpx.Response, connector: str) -> None:
    if response.status_code >= 400:
        raise SwitchboardError(
            "upstream",
            f"{connector} returned HTTP {response.status_code}.",
            {"connector": connector, "status": response.status_code},
        )


def _hubspot_contact(row: dict[str, Any]) -> dict[str, Any]:
    props = row.get("properties", {})
    return {
        "id": row.get("id", ""),
        "tenant_id": props.get("switchboard_tenant", ""),
        "email": props.get("email", ""),
        "first_name": props.get("firstname", ""),
        "last_name": props.get("lastname", ""),
        "company": props.get("company", "") or "",
        "phone": props.get("phone", "") or "",
    }


def _hubspot_deal(row: dict[str, Any]) -> dict[str, Any]:
    props = row.get("properties", {})
    amount = props.get("amount") or "0"
    cents = int((Decimal(str(amount)) * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    return {
        "id": row.get("id", ""),
        "tenant_id": props.get("switchboard_tenant", ""),
        "name": props.get("dealname", ""),
        "amount_cents": cents,
        "stage": props.get("dealstage", ""),
        "contact_id": props.get("switchboard_contact_id", "") or "",
    }


def _notion_title(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    for value in props.values():
        if isinstance(value, dict) and value.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in value.get("title", []))
    return page.get("id", "Untitled")


def _notion_plain(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for block in payload.get("results", []):
        data = block.get(block.get("type", ""), {})
        if isinstance(data, dict) and "rich_text" in data:
            lines.append("".join(part.get("plain_text", "") for part in data["rich_text"]))
    return "\n".join(line for line in lines if line)


_CLICKUP_STATUS = {
    "open": "open",
    "to do": "open",
    "in progress": "in_progress",
    "review": "review",
    "done": "done",
    "closed": "done",
}


def _clickup_task(item: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    status_name = str(item.get("status", {}).get("status", "open")).lower()
    assignees = item.get("assignees") or []
    email = ""
    if assignees:
        email = assignees[0].get("email", "") or ""
    return {
        "id": item.get("id", ""),
        "tenant_id": tenant_id,
        "name": item.get("name", ""),
        "status": _CLICKUP_STATUS.get(status_name, "open"),
        "assignee_email": email,
        "due_date": "",
        "notes": item.get("description") or "",
    }
