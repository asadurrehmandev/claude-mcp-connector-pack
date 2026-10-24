"""Structured tool errors. The message is JSON so MCP clients can parse it."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError


class SwitchboardError(ToolError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.public_message = message
        self.details = details or {}
        payload = {"error": {"code": code, "message": message, "details": self.details}}
        super().__init__(json.dumps(payload, sort_keys=True))
