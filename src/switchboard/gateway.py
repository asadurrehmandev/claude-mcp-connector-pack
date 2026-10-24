"""The single path every tool call takes: auth, scope, policy, rate limit, confirm, audit."""

from __future__ import annotations

import time
import uuid
from typing import Any

from switchboard.audit import AuditLog
from switchboard.constants import RESULT_NOTICE
from switchboard.errors import SwitchboardError
from switchboard.policy import PolicyStore
from switchboard.principal import Principal, resolve_principal
from switchboard.ratelimit import RateLimiter
from switchboard.settings import Settings
from switchboard.store import Store
from switchboard.tools import CallCtx, Registry


class Gateway:
    def __init__(
        self,
        settings: Settings,
        registry: Registry,
        audit: AuditLog,
        limiter: RateLimiter,
        policy: PolicyStore,
        store: Store,
        apis: dict[str, Any],
    ):
        self.settings = settings
        self.registry = registry
        self.audit = audit
        self.limiter = limiter
        self.policy = policy
        self.store = store
        self.apis = apis
        self.unsafe_write_reverts = 0

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        principal: Principal | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        args = dict(arguments or {})
        actor = ""
        tenant_id = ""
        decision = "deny"
        error_code = ""
        summary = ""
        try:
            current = principal or resolve_principal(self.settings)
            actor = current.subject
            tenant_id = current.tenant_id
            spec = self.registry.tools.get(name)
            if spec is None:
                raise SwitchboardError("not_found", f"Unknown tool {name}")
            retry = self.limiter.check(tenant_id, name)
            if retry is not None:
                raise SwitchboardError(
                    "rate_limited",
                    "Rate limit exceeded for this tool.",
                    {"retry_after_seconds": round(retry, 3), "limit": self.limiter.limit},
                )
            if not self.policy.is_enabled(tenant_id, spec):
                raise SwitchboardError(
                    "tool_disabled",
                    "This tool is turned off for the workspace. Read tools are the default.",
                    {"tool": name},
                )
            if not current.allows(spec.scope):
                raise SwitchboardError(
                    "scope_denied",
                    "The access token does not include the scope this tool requires.",
                    {"required": spec.scope},
                )
            requested = str(args.get("workspace_id") or "").strip()
            if requested and requested != tenant_id:
                raise SwitchboardError(
                    "tenant_isolation",
                    "workspace_id does not match the authenticated workspace.",
                    {"authenticated_workspace": tenant_id},
                )
            args.pop("workspace_id", None)
            confirm = bool(args.get("confirm", False))
            ctx = CallCtx(
                tenant_id=tenant_id,
                actor=actor,
                crm=self.apis["crm"],
                ads=self.apis["ads"],
                docs=self.apis["docs"],
                projects=self.apis["projects"],
                slack=self.apis["slack"],
            )
            snap = self.store.snapshot()
            before = self.store.revision
            try:
                payload = await spec.handler(ctx, **args)
            except SwitchboardError:
                if spec.write and not confirm:
                    self.store.restore(snap)
                raise
            except TypeError as exc:
                raise SwitchboardError("validation", "The arguments do not match this tool.") from exc
            except Exception as exc:
                if spec.write and not confirm:
                    self.store.restore(snap)
                raise SwitchboardError("upstream", "The connector failed.", {"tool": name}) from exc
            if spec.write and not confirm and self.store.revision != before:
                self.store.restore(snap)
                self.unsafe_write_reverts += 1
                raise SwitchboardError(
                    "unsafe_write_blocked",
                    "A write was attempted without confirm. The change was reverted.",
                    {"tool": name},
                )
            if spec.write and not confirm and not (isinstance(payload, dict) and payload.get("dry_run") is True):
                raise SwitchboardError("confirm_required", "Write tools must return a dry-run preview when confirm is false.")
            decision = "allow"
            summary = "dry_run" if isinstance(payload, dict) and payload.get("dry_run") else "ok"
            return {
                "untrusted": not bool(isinstance(payload, dict) and payload.get("dry_run")),
                "notice": RESULT_NOTICE,
                "dry_run": bool(isinstance(payload, dict) and payload.get("dry_run")),
                "data": payload,
            }
        except SwitchboardError as exc:
            error_code = exc.code
            summary = exc.code
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            self.audit.write(
                {
                    "id": "aud_" + uuid.uuid4().hex[:12],
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "tenant_id": tenant_id,
                    "actor": actor,
                    "tool": name,
                    "decision": decision,
                    "error_code": error_code or None,
                    "duration_ms": duration_ms,
                    "arguments": args,
                    "result_summary": summary or "error",
                }
            )
