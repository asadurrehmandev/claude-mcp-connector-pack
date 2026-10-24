"""stdio, streamable HTTP, conformance, and the held-out eval."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn

from switchboard.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="switchboard")
    sub = parser.add_subparsers(dest="command", required=True)

    http = sub.add_parser("http", help="Streamable HTTP MCP server and console API")
    http.add_argument("--host", default="")
    http.add_argument("--port", type=int, default=0)

    stdio = sub.add_parser("stdio", help="stdio MCP server for Claude Desktop, Claude Code, or Cursor")
    stdio.add_argument("--tenant", default="")
    stdio.add_argument("--actor", default="")

    sub.add_parser("conformance", help="In-process MCP client handshake")
    ev = sub.add_parser("eval", help="Run the held-out planner eval and write qa/eval_results.json")
    ev.add_argument("--tasks", default="qa/heldout_tasks.json")
    ev.add_argument("--out", default="qa/eval_results.json")

    args = parser.parse_args()
    if args.command == "http":
        _http(args.host, args.port)
    elif args.command == "stdio":
        _stdio(args.tenant, args.actor)
    elif args.command == "conformance":
        print(json.dumps(asyncio.run(_conformance()), indent=2))
    elif args.command == "eval":
        print(json.dumps(asyncio.run(_eval(args.tasks, args.out)), indent=2))


def _http(host: str, port: int) -> None:
    from switchboard.runtime import build_runtime

    settings = Settings.from_env()
    if host:
        settings.host = host
    if port:
        settings.port = port
    runtime = build_runtime(settings)
    uvicorn.run(runtime.app, host=settings.host, port=settings.port, log_level="info")


def _stdio(tenant: str, actor: str) -> None:
    from switchboard.runtime import build_runtime

    settings = Settings.from_env()
    settings.allow_env_principal = True
    if tenant:
        settings.tenant_id = tenant
    if actor:
        settings.actor = actor
    if not settings.tenant_id:
        raise SystemExit("stdio requires SWITCHBOARD_TENANT_ID or --tenant")
    runtime = build_runtime(settings)
    runtime.server.run(transport="stdio")


async def _conformance() -> dict:
    from mcp.client.client import Client

    from switchboard.principal import reset_principal, set_principal
    from switchboard.runtime import build_runtime
    from switchboard.eval_harness import principal_for

    runtime = build_runtime(Settings(env="demo", audit_path="", policy_path="", force_mock=True))
    token = set_principal(principal_for("halden-bikes"))
    try:
        async with Client(runtime.server, mode="legacy") as client:
            tools = await client.list_tools()
            templates = await client.list_resource_templates()
            prompts = await client.list_prompts()
            result = await client.call_tool("crm_search_contacts", {"query": "Mara"})
    finally:
        reset_principal(token)
    return {
        "tools": len(tools.tools),
        "resource_templates": len(templates.resource_templates),
        "prompts": len(prompts.prompts),
        "sample_is_error": bool(result.is_error),
    }


async def _eval(tasks: str, out: str) -> dict:
    from switchboard.eval_harness import evaluate
    from switchboard.runtime import build_runtime

    runtime = build_runtime(Settings(env="demo", audit_path="", policy_path="", force_mock=True, rate_limit=500))
    report = await evaluate(runtime, Path(tasks))
    destination = Path(out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
