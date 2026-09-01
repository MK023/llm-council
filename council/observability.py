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

# Only levels at or below INFO: every telemetry record is emitted at INFO, so anything
# above it is a silencer, not a verbosity setting. `DEBUG` is the only genuine choice here.
_LOG_LEVELS: dict[str, int] = {"DEBUG": logging.DEBUG, "INFO": logging.INFO}


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("council")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    # No propagation to the root logger. `council.stderr` is parsed one JSON object per
    # line, and a root handler configured by anything else in the process would emit a
    # SECOND, differently formatted copy of every record into the same file.
    logger.propagate = False
    # `COUNCIL_LOG_LEVEL` is read from the real environment, so whoever controls the
    # environment controls the only telemetry this code produces. Two failure modes, and
    # the quiet one is the worse: an unknown value made `setLevel()` raise `ValueError`
    # and killed the process, while a valid-but-high value (`CRITICAL`) silenced every
    # `emit()` — the run still succeeds, the log says nothing, and the Langfuse check
    # reads an empty file and reports that the council emitted nothing at all.
    #
    # The allow-list caps it at INFO rather than trusting the name: a monitoring tool with
    # a switch on the outside of the door is not a monitoring tool.
    requested = os.environ.get("COUNCIL_LOG_LEVEL", "INFO").strip().upper()
    logger.setLevel(_LOG_LEVELS.get(requested, logging.INFO))
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
    """Prefix hash for trace correlation without leaking question content to logs.

    Sixteen hex characters, not eight. The argument is functional before it is defensive:
    the field exists to tell runs on the SAME question apart from runs on a different one,
    and 32 bits is narrow enough for two unrelated questions to collide and quietly merge.

    The confidentiality angle is real but thin, and worth stating rather than implying: a
    truncated unsalted digest can be CONFIRMED by anyone holding a candidate question.

    Where it actually goes matters, and the first version of this docstring got it wrong —
    it claimed the hashes reach Langfuse. They do not. This value never leaves `emit()`: it
    exists only in the stderr record. What travels to OpenRouter, and from there to Langfuse,
    is `session_id`, which is a `uuid4`. So the exposure is exactly two places: Marco's own
    terminal, and the public log of the weekly E2E — where the question is public anyway,
    because it is written in `e2e.yml`.

    A salt would close the confirmation gap and destroy the cross-session correlation this
    field exists for. Against an exposure that small, that is a bad trade, and the reasoning
    belongs here rather than in someone's head.
    """
    return hashlib.sha256(question.encode()).hexdigest()[:16]
