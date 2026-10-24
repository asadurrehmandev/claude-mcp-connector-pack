"""Append-only audit trail. Records are redacted before they touch memory or disk."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from switchboard.redact import redact_obj


class AuditLog:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> dict[str, Any]:
        clean = redact_obj(record)
        with self._lock:
            self.records.append(clean)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(clean, sort_keys=True) + "\n")
        return clean

    def tail(self, tenant_id: str | None, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self.records)
        if tenant_id:
            rows = [row for row in rows if row.get("tenant_id") == tenant_id]
        return list(reversed(rows[-limit:]))

    def dump_text(self) -> str:
        with self._lock:
            return json.dumps(self.records, sort_keys=True)
