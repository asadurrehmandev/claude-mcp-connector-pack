"""Local console API. Demo mode does not require a token. Live mode does."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from switchboard.auth import decode_token, issue_token
from switchboard.byok import run_byok
from switchboard.errors import SwitchboardError
from switchboard.planner import run_turn
from switchboard.principal import Principal
from switchboard.settings import ALL_SCOPES


class ToggleBody(BaseModel):
    workspace_id: str
    tool: str
    enabled: bool


class PlaygroundBody(BaseModel):
    workspace_id: str
    message: str = Field(min_length=1, max_length=4000)
    mode: str = "demo"
    provider: str = ""
    api_key: str = ""
    model: str = ""


class ConfirmStep(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ConfirmBody(BaseModel):
    workspace_id: str
    steps: list[ConfirmStep] = Field(min_length=1, max_length=8)


def build_console(runtime) -> FastAPI:
    app = FastAPI(title="Switchboard console", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime.settings.console_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.exception_handler(SwitchboardError)
    async def on_error(_: Request, exc: SwitchboardError):
        status = {
            "unauthenticated": 401,
            "tenant_isolation": 403,
            "not_found": 404,
            "validation": 400,
        }.get(exc.code, 400)
        return JSONResponse(json.loads(str(exc)), status_code=status)

    @app.get("/health")
    async def health():
        return {"status": "ok", "env": runtime.settings.env, "demo": runtime.settings.demo}

    @app.get("/meta")
    async def meta():
        return {
            "service": "switchboard",
            "env": runtime.settings.env,
            "demo": runtime.settings.demo,
            "agency": "Fernwood Studio",
            "agency_city": "Chicago",
            "demo_today": "2026-03-16",
            "mode_label": _mode_label(runtime),
            "connectors": runtime.connector_status(),
            "mcp_path": "/mcp",
            "resource": runtime.settings.resource_url,
            "issuer": runtime.settings.issuer_url,
        }

    @app.get("/workspaces")
    async def workspaces():
        rows = []
        for item in runtime.store.workspaces.values():
            row = item.model_dump()
            if runtime.settings.demo:
                row["demo_bearer"] = issue_token(
                    runtime.settings,
                    subject="elena.voss@fernwood.studio",
                    tenant_id=item.id,
                    scopes=list(ALL_SCOPES),
                    client_id=runtime.settings.oauth_client_id,
                    ttl_seconds=12 * 3600,
                )
            rows.append(row)
        rows.sort(key=lambda row: row["name"])
        return {"workspaces": rows, "demo": runtime.settings.demo}

    @app.get("/tools")
    async def tools(workspace_id: str):
        _known_workspace(runtime, workspace_id)
        return {"workspace_id": workspace_id, "tools": runtime.policy.snapshot(workspace_id, runtime.registry.tools)}

    @app.put("/tools")
    async def toggle(body: ToggleBody, request: Request):
        principal = _principal(runtime, request, body.workspace_id)
        if body.tool not in runtime.registry.tools:
            raise SwitchboardError("not_found", "Unknown tool")
        if not runtime.settings.demo and not principal.allows("crm.read"):
            raise SwitchboardError("scope_denied", "Token cannot manage tools")
        runtime.policy.set_enabled(body.workspace_id, body.tool, body.enabled)
        runtime.audit.write(
            {
                "id": "policy",
                "ts": _now(),
                "tenant_id": body.workspace_id,
                "actor": principal.subject,
                "tool": "policy.set",
                "decision": "allow",
                "error_code": None,
                "duration_ms": 0,
                "arguments": {"tool": body.tool, "enabled": body.enabled},
                "result_summary": "ok",
            }
        )
        return {"workspace_id": body.workspace_id, "tool": body.tool, "enabled": body.enabled}

    @app.get("/audit")
    async def audit(workspace_id: str = "", limit: int = 50):
        capped = max(1, min(limit, 200))
        return {"records": runtime.audit.tail(workspace_id or None, capped)}

    @app.post("/playground")
    async def playground(body: PlaygroundBody, request: Request):
        principal = _principal(runtime, request, body.workspace_id)
        key = body.api_key
        if body.mode == "byok":
            result = await run_byok(
                runtime,
                principal,
                body.message,
                provider=body.provider or "anthropic",
                api_key=key,
                model=body.model,
            )
            label = f"BYOK · {result['provider']} · {result['model']}. The key was used for this request only and was not stored."
        elif body.mode == "demo":
            result = await run_turn(runtime.gateway, principal, body.message)
            label = "Demo mode. Scripted planner on mock data. This reply was not written by a language model."
        else:
            raise SwitchboardError("validation", "mode must be demo or byok")
        reply = _strip(result["reply"], key)
        steps = json.loads(_strip(json.dumps(result["steps"]), key))
        runtime.audit.write(
            {
                "id": "playground",
                "ts": _now(),
                "tenant_id": body.workspace_id,
                "actor": principal.subject,
                "tool": "playground",
                "decision": "allow",
                "error_code": None,
                "duration_ms": 0,
                "arguments": {
                    "mode": body.mode,
                    "message": body.message,
                    **(
                        {"planner": "scripted demo planner"}
                        if body.mode == "demo"
                        else {"provider": body.provider}
                    ),
                },
                "result_summary": result["kind"],
            }
        )
        return {
            "mode": body.mode,
            "mode_label": label,
            "kind": result["kind"],
            "reply": reply,
            "steps": steps,
        }

    @app.post("/playground/confirm")
    async def confirm_preview(body: ConfirmBody, request: Request):
        """Apply a dry-run preview. confirm is set here, never read from a message."""
        principal = _principal(runtime, request, body.workspace_id)
        executed = []
        for step in body.steps:
            spec = runtime.registry.tools.get(step.tool)
            if spec is None or not spec.write:
                raise SwitchboardError("validation", "Only a write tool from a dry-run preview can be confirmed.")
            arguments = dict(step.arguments)
            arguments.pop("confirm", None)
            arguments["confirm"] = True
            payload = await runtime.gateway.invoke(step.tool, arguments, principal=principal)
            executed.append(
                {
                    "tool": step.tool,
                    "arguments": arguments,
                    "result": payload,
                    "is_error": False,
                    "error_code": None,
                    "duration_ms": 0,
                }
            )
        return {
            "mode": "confirm",
            "mode_label": "Confirm button. The boolean true was set by this action, not by the message.",
            "kind": "confirm",
            "reply": "The confirmed write finished. Details are in the tool result.",
            "steps": executed,
        }

    @app.get("/setup")
    async def setup():
        return {"snippets": setup_snippets(runtime)}

    return app


def setup_snippets(runtime) -> dict[str, str]:
    base = runtime.settings.issuer_url
    tenant = "halden-bikes"
    token_line = "<bearer token from the workspaces page in demo, or from POST /oauth/token>"
    mcp = {
        "mcpServers": {
            "switchboard": {
                "url": f"{base}/mcp",
                "headers": {"Authorization": f"Bearer {token_line}"},
            }
        }
    }
    stdio = {
        "mcpServers": {
            "switchboard": {
                "command": "switchboard",
                "args": ["stdio", "--tenant", tenant],
                "env": {
                    "SWITCHBOARD_ENV": "demo",
                    "SWITCHBOARD_TENANT_ID": tenant,
                    "SWITCHBOARD_SCOPES": ",".join(ALL_SCOPES),
                },
            }
        }
    }
    return {
        "claude_desktop": json.dumps(stdio, indent=2),
        "claude_code": "claude mcp add switchboard --transport stdio -- switchboard stdio --tenant halden-bikes",
        "cursor": json.dumps(mcp, indent=2),
        "http_env": (
            "SWITCHBOARD_ENV=demo SWITCHBOARD_HOST=127.0.0.1 SWITCHBOARD_PORT=4317 "
            "python -m switchboard http"
        ),
        "stdio_env": "SWITCHBOARD_ENV=demo SWITCHBOARD_TENANT_ID=halden-bikes python -m switchboard stdio",
        "oauth_token": (
            "curl -s -X POST "
            f"{base}/oauth/token "
            "-d grant_type=client_credentials "
            "-d client_id=fernwood-console "
            "-d client_secret=demo-console-secret "
            "-d tenant_id=halden-bikes"
        ),
    }


def _principal(runtime, request: Request, workspace_id: str) -> Principal:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        claims = decode_token(header.split(" ", 1)[1].strip(), runtime.settings)
        if claims is None:
            raise SwitchboardError("unauthenticated", "Bearer token was rejected")
        if claims["tenant_id"] != workspace_id:
            raise SwitchboardError("tenant_isolation", "Token workspace does not match workspace_id")
        return Principal(
            tenant_id=workspace_id,
            scopes=frozenset(str(claims["scope"]).split()),
            subject=str(claims.get("sub") or ""),
            client_id=str(claims.get("client_id") or ""),
        )
    if runtime.settings.demo:
        _known_workspace(runtime, workspace_id)
        return Principal(
            tenant_id=workspace_id,
            scopes=frozenset(ALL_SCOPES),
            subject="elena.voss@fernwood.studio",
            client_id="console",
        )
    raise SwitchboardError("unauthenticated", "Live mode requires a bearer token.")


def _known_workspace(runtime, workspace_id: str) -> None:
    if workspace_id not in runtime.store.workspaces:
        raise SwitchboardError("not_found", "Unknown workspace")


def _mode_label(runtime) -> str:
    if runtime.settings.demo:
        return (
            "Switchboard demo. Fernwood Studio is a fictional agency written for this project, "
            "with three clients. No live vendor calls."
        )
    return "Live credentials are configured for at least one connector where the environment says so."


def _strip(text: str, key: str) -> str:
    if key and key in text:
        return text.replace(key, "[redacted]")
    return text


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
