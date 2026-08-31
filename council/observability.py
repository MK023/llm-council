"""Structured logging to stderr. One backend, and it is stdlib `logging`.

This module does NOT talk to Langfuse and never did. Traces reach Langfuse through
OpenRouter Broadcast — an account setting, outside this repo — from the `user`,
`session_id` and `trace` fields `client.py` puts in the request body.

Until 2026-08-31 this docstring promised an "opt-in Langfuse backend" and every record
carried `langfuse_opt_in`, a boolean that went true when two environment variables
existed. It made no call, changed no behaviour, and said nothing about whether a trace
had arrived anywhere — but a reader who saw `true` concluded telemetry was flowing. It
was flowing, and nobody had ever checked: the lamp made an unverified fact look verified,
which is worse than no lamp.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("council")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(os.environ.get("COUNCIL_LOG_LEVEL", "INFO").upper())
    return logger


_LOGGER = _build_logger()


@dataclass
class TraceContext:
    """Per-query trace context, propagated across all stages."""

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    question_hash: str = ""


def emit(event: str, trace: TraceContext, **fields: Any) -> None:
    """Emits a structured JSON log line on stderr (does NOT pollute stdout)."""
    record = {
        "ts": round(time.time(), 3),
        "trace_id": trace.trace_id,
        # Correlates runs on the SAME question across sessions, without ever storing
        # the question. Computed since day one, emitted only from 2026-07-26 — the
        # promise was in the docstring, the mechanism was missing.
        "question_hash": trace.question_hash,
        "event": event,
        **fields,
    }
    _LOGGER.info(json.dumps(record, ensure_ascii=False))


def hash_question(question: str) -> str:
    """8-char prefix hash for trace correlation without leaking question content to logs."""
    return hashlib.sha256(question.encode()).hexdigest()[:8]
