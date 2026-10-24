"""Process configuration. Secrets come from the environment and are never logged."""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field

DEMO_SIGNING_SECRET = "switchboard-demo-signing-secret"
DEMO_OAUTH_CLIENT_ID = "northline-console"
DEMO_OAUTH_CLIENT_SECRET = "demo-console-secret"

ALL_SCOPES: tuple[str, ...] = (
    "crm.read",
    "crm.write",
    "ads.read",
    "docs.read",
    "projects.read",
    "projects.write",
    "slack.read",
    "slack.write",
)


class Settings(BaseModel):
    env: str = "demo"
    host: str = "127.0.0.1"
    port: int = 4317
    public_base_url: str = "http://127.0.0.1:4317"
    signing_secret: str = DEMO_SIGNING_SECRET
    allow_env_principal: bool = False
    tenant_id: str = ""
    scopes: list[str] = Field(default_factory=lambda: list(ALL_SCOPES))
    actor: str = "local"
    rate_limit: int = 60
    rate_window_seconds: float = 60.0
    audit_path: str = "var/audit.jsonl"
    policy_path: str = "var/policy.json"
    oauth_client_id: str = DEMO_OAUTH_CLIENT_ID
    oauth_client_secret: str = DEMO_OAUTH_CLIENT_SECRET
    oauth_redirect_uris: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:4318/oauth/callback",
            "http://localhost:4318/oauth/callback",
        ]
    )
    force_mock: bool = False
    hubspot_access_token: str = ""
    google_ads_developer_token: str = ""
    google_ads_access_token: str = ""
    google_ads_customer_map: dict[str, str] = Field(default_factory=dict)
    meta_access_token: str = ""
    meta_account_map: dict[str, str] = Field(default_factory=dict)
    notion_token: str = ""
    notion_page_map: dict[str, list[str]] = Field(default_factory=dict)
    clickup_token: str = ""
    clickup_list_map: dict[str, str] = Field(default_factory=dict)
    slack_bot_token: str = ""
    slack_allowlist: dict[str, list[str]] = Field(default_factory=dict)
    jwt_private_key_pem: str = ""
    console_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:4318", "http://localhost:4318"]
    )

    @property
    def issuer_url(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def resource_url(self) -> str:
        return f"{self.issuer_url}/mcp"

    @property
    def demo(self) -> bool:
        return self.env == "demo"

    def connector_mode(self, name: str) -> str:
        if self.force_mock:
            return "mock"
        flags = {
            "hubspot": bool(self.hubspot_access_token),
            "google_ads": bool(self.google_ads_access_token and self.google_ads_developer_token and self.google_ads_customer_map),
            "meta_ads": bool(self.meta_access_token and self.meta_account_map),
            "notion": bool(self.notion_token and self.notion_page_map),
            "clickup": bool(self.clickup_token and self.clickup_list_map),
            "slack": bool(self.slack_bot_token and self.slack_allowlist),
        }
        return "live" if flags.get(name) else "mock"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get("SWITCHBOARD_ENV", "demo").strip() or "demo"
        secret = os.environ.get("SWITCHBOARD_SIGNING_SECRET", "").strip()
        if not secret:
            secret = DEMO_SIGNING_SECRET if env == "demo" else ""
        if env == "live" and (len(secret) < 32 or secret == DEMO_SIGNING_SECRET):
            raise SystemExit(
                "Live mode requires SWITCHBOARD_SIGNING_SECRET of at least 32 characters, "
                "and it must not be the published demo secret."
            )
        if env not in {"demo", "live"}:
            raise SystemExit("SWITCHBOARD_ENV must be demo or live")
        scopes_raw = os.environ.get("SWITCHBOARD_SCOPES", "").strip()
        scopes = [part.strip() for part in scopes_raw.split(",") if part.strip()] or list(ALL_SCOPES)
        oauth_secret = os.environ.get("SWITCHBOARD_OAUTH_CLIENT_SECRET", "").strip()
        if not oauth_secret and env == "demo":
            oauth_secret = DEMO_OAUTH_CLIENT_SECRET
        return cls(
            env=env,
            host=os.environ.get("SWITCHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.environ.get("SWITCHBOARD_PORT", "4317")),
            public_base_url=os.environ.get("SWITCHBOARD_PUBLIC_BASE_URL", "http://127.0.0.1:4317").strip(),
            signing_secret=secret,
            tenant_id=os.environ.get("SWITCHBOARD_TENANT_ID", "").strip(),
            scopes=scopes,
            actor=os.environ.get("SWITCHBOARD_ACTOR", "local").strip() or "local",
            rate_limit=int(os.environ.get("SWITCHBOARD_RATE_LIMIT", "60")),
            rate_window_seconds=float(os.environ.get("SWITCHBOARD_RATE_WINDOW", "60")),
            audit_path=os.environ.get("SWITCHBOARD_AUDIT_PATH", "var/audit.jsonl"),
            policy_path=os.environ.get("SWITCHBOARD_POLICY_PATH", "var/policy.json"),
            oauth_client_id=os.environ.get("SWITCHBOARD_OAUTH_CLIENT_ID", DEMO_OAUTH_CLIENT_ID).strip(),
            oauth_client_secret=oauth_secret,
            force_mock=os.environ.get("SWITCHBOARD_FORCE_MOCK", "").strip().lower() in {"1", "true", "yes"},
            hubspot_access_token=os.environ.get("HUBSPOT_ACCESS_TOKEN", "").strip(),
            google_ads_developer_token=os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip(),
            google_ads_access_token=os.environ.get("GOOGLE_ADS_ACCESS_TOKEN", "").strip(),
            google_ads_customer_map=_json_map(os.environ.get("GOOGLE_ADS_CUSTOMER_MAP", "")),
            meta_access_token=os.environ.get("META_ADS_ACCESS_TOKEN", "").strip(),
            meta_account_map=_json_map(os.environ.get("META_AD_ACCOUNT_MAP", "")),
            notion_token=os.environ.get("NOTION_TOKEN", "").strip(),
            notion_page_map=_json_map(os.environ.get("NOTION_PAGE_MAP", "")),
            clickup_token=os.environ.get("CLICKUP_TOKEN", "").strip(),
            clickup_list_map=_json_map(os.environ.get("CLICKUP_LIST_MAP", "")),
            slack_bot_token=os.environ.get("SLACK_BOT_TOKEN", "").strip(),
            slack_allowlist=_json_map(os.environ.get("SLACK_ALLOWLIST", "")),
            jwt_private_key_pem=os.environ.get("SWITCHBOARD_JWT_PRIVATE_KEY_PEM", "").replace("\\n", "\n").strip(),
        )


def _json_map(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return {}
    return json.loads(text)
