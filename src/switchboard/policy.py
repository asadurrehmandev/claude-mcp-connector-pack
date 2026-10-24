"""Per-workspace tool toggles. A workspace with no saved policy is read-only."""

from __future__ import annotations

import json
from pathlib import Path

from switchboard.constants import DEMO_TENANTS
from switchboard.tools import ToolSpec


class PolicyStore:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self.overrides: dict[str, dict[str, bool]] = {}
        if self.path is not None and self.path.exists():
            self.overrides = json.loads(self.path.read_text(encoding="utf-8"))

    def is_enabled(self, tenant_id: str, spec: ToolSpec) -> bool:
        chosen = self.overrides.get(tenant_id, {})
        if spec.name in chosen:
            return bool(chosen[spec.name])
        if tenant_id in DEMO_TENANTS:
            return True
        return not spec.write

    def set_enabled(self, tenant_id: str, tool_name: str, enabled: bool) -> None:
        self.overrides.setdefault(tenant_id, {})[tool_name] = enabled
        self._save()

    def snapshot(self, tenant_id: str, specs: dict[str, ToolSpec]) -> list[dict[str, object]]:
        rows = []
        for name in sorted(specs):
            spec = specs[name]
            rows.append(
                {
                    "name": name,
                    "description": spec.description,
                    "scope": spec.scope,
                    "write": spec.write,
                    "destructive": spec.destructive,
                    "enabled": self.is_enabled(tenant_id, spec),
                }
            )
        return rows

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.overrides, indent=2, sort_keys=True), encoding="utf-8")
