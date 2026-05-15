"""Structured logging + optional Langfuse opt-in instrumentation.

Default backend: stdlib `logging` with JSON formatter to stderr.
Opt-in backend: if `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` are set in env,
emit trace + span events as structured log records that a Langfuse forwarder
(or the OpenRouter Langfuse plugin) can consume.
"""
from __future__ import annotations

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
_LANGFUSE_ENABLED = bool(
    os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
)


@dataclass
class TraceContext:
    """Per-query trace context, propagated across all stages."""

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    question_hash: str = ""
    started_at: float = field(default_factory=time.time)


def emit(event: str, trace: TraceContext, **fields: Any) -> None:
    """Emits a structured JSON log line on stderr (does NOT pollute stdout)."""
    record = {
        "ts": round(time.time(), 3),
        "trace_id": trace.trace_id,
        "event": event,
        "langfuse_opt_in": _LANGFUSE_ENABLED,
        **fields,
    }
    _LOGGER.info(json.dumps(record, ensure_ascii=False))


def hash_question(question: str) -> str:
    """8-char prefix hash for trace correlation without leaking question content to logs."""
    import hashlib

    return hashlib.sha256(question.encode()).hexdigest()[:8]
