"""Wire settings, mock or live backends, the gateway, and the MCP app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from switchboard.apis import MockAds, MockCrm, MockDocs, MockProjects, MockSlack
from switchboard.audit import AuditLog
from switchboard.auth import OAuthIssuer, SwitchboardTokenVerifier
from switchboard.gateway import Gateway
from switchboard.live import LiveAds, LiveCrm, LiveDocs, LiveProjects, LiveSlack
from switchboard.policy import PolicyStore
from switchboard.ratelimit import RateLimiter
from switchboard.redact import install_redacting_logger
from switchboard.seed import load_seed
from switchboard.settings import Settings
from switchboard.store import Store
from switchboard.tools import Registry, registry


@dataclass
class Runtime:
    settings: Settings
    store: Store
    audit: AuditLog
    limiter: RateLimiter
    policy: PolicyStore
    oauth: OAuthIssuer
    verifier: SwitchboardTokenVerifier
    registry: Registry
    apis: dict[str, Any]
    gateway: Gateway
    http: httpx.AsyncClient
    server: Any = None
    app: Any = None

    def connector_status(self) -> list[dict[str, str]]:
        return [
            {"id": "hubspot", "label": "HubSpot CRM", "mode": self.settings.connector_mode("hubspot"), "env": "HUBSPOT_ACCESS_TOKEN"},
            {
                "id": "google_ads",
                "label": "Google Ads",
                "mode": self.settings.connector_mode("google_ads"),
                "env": "GOOGLE_ADS_ACCESS_TOKEN, GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CUSTOMER_MAP",
            },
            {
                "id": "meta_ads",
                "label": "Meta Ads",
                "mode": self.settings.connector_mode("meta_ads"),
                "env": "META_ADS_ACCESS_TOKEN, META_AD_ACCOUNT_MAP",
            },
            {"id": "notion", "label": "Notion", "mode": self.settings.connector_mode("notion"), "env": "NOTION_TOKEN, NOTION_PAGE_MAP"},
            {
                "id": "clickup",
                "label": "ClickUp",
                "mode": self.settings.connector_mode("clickup"),
                "env": "CLICKUP_TOKEN, CLICKUP_LIST_MAP",
            },
            {"id": "slack", "label": "Slack", "mode": self.settings.connector_mode("slack"), "env": "SLACK_BOT_TOKEN, SLACK_ALLOWLIST"},
        ]


def build_runtime(
    settings: Settings,
    *,
    clock=None,
    http_client: httpx.AsyncClient | None = None,
) -> Runtime:
    install_redacting_logger()
    store = Store(load_seed())
    http = http_client or httpx.AsyncClient(timeout=30.0)
    apis = {
        "crm": LiveCrm(settings, http) if settings.connector_mode("hubspot") == "live" else MockCrm(store),
        "ads": LiveAds(settings, http) if _ads_live(settings) else MockAds(store),
        "docs": LiveDocs(settings, http) if settings.connector_mode("notion") == "live" else MockDocs(store),
        "projects": LiveProjects(settings, http) if settings.connector_mode("clickup") == "live" else MockProjects(store),
        "slack": LiveSlack(settings, http) if settings.connector_mode("slack") == "live" else MockSlack(store),
    }
    audit = AuditLog(settings.audit_path)
    limiter = RateLimiter(settings.rate_limit, settings.rate_window_seconds, clock=clock)
    policy = PolicyStore(settings.policy_path)
    oauth = OAuthIssuer(settings)
    verifier = SwitchboardTokenVerifier(settings)
    gateway = Gateway(settings, registry, audit, limiter, policy, store, apis)
    runtime = Runtime(
        settings=settings,
        store=store,
        audit=audit,
        limiter=limiter,
        policy=policy,
        oauth=oauth,
        verifier=verifier,
        registry=registry,
        apis=apis,
        gateway=gateway,
        http=http,
    )
    from switchboard.mcp_surface import build_http_app, build_server

    runtime.server = build_server(runtime)
    runtime.app = build_http_app(runtime)
    return runtime


def _ads_live(settings: Settings) -> bool:
    return settings.connector_mode("google_ads") == "live" or settings.connector_mode("meta_ads") == "live"
