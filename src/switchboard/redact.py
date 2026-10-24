"""Redact secrets and common PII before anything is written to a log."""

from __future__ import annotations

import logging
import re
from typing import Any

_SB1 = re.compile(r"sb1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_SK = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{6,}")
_ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|signing[_-]?secret)"
    r"\s*[:=]\s*\S+"
)
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]*)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s](\d{4})\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def redact_text(value: str) -> str:
    value = _BEARER.sub("Bearer [redacted]", value)
    value = _SB1.sub("sb1.[redacted]", value)
    value = _SK.sub("sk-[redacted]", value)
    value = _ASSIGN.sub(lambda m: f"{m.group(1)}=[redacted]", value)
    value = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(3)}", value)
    value = _PHONE.sub(lambda m: f"***-***-{m.group(1)}", value)
    value = _SSN.sub("***-**-****", value)
    return value


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): redact_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, tuple):
        return [redact_obj(v) for v in value]
    return value


class RedactFilter(logging.Filter):
    """Rewrite log records in place. Attach this to the switchboard logger."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact_text(v) if isinstance(v, str) else v for k, v in record.args.items()}
            else:
                record.args = tuple(redact_text(a) if isinstance(a, str) else a for a in record.args)
        return True


def install_redacting_logger() -> logging.Logger:
    logger = logging.getLogger("switchboard")
    if not any(isinstance(f, RedactFilter) for f in logger.filters):
        logger.addFilter(RedactFilter())
    logger.setLevel(logging.INFO)
    return logger
