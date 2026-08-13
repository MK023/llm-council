"""The telemetry layer: how the logger is built, and what a record actually contains.

`observability.py` stays inside the mutation gate on purpose — its output carries the
privacy claim ("never the question, never the answers"). The claim was tested; the
record around it was not. 34 mutants survived here on 2026-08-13: the logger's stream,
its level, its formatter, and every field name in the emitted JSON.

A log line nobody asserts on is a log line that can silently change shape, and the
first person to notice is whoever needs it during an incident.

## The four survivors left here are equivalent mutants — do not chase them

Read from the run diffs on 2026-08-13. Each changes the source and not the behaviour,
so no assertion can distinguish them and writing one would only pin an implementation
detail:

| Mutation | Why it behaves identically |
|---|---|
| `StreamHandler(sys.stderr)` → `StreamHandler(None)` | `None` is the documented default, and the default is `sys.stderr` |
| `Formatter("%(message)s")` → `Formatter(None)` | `fmt=None` means `"%(message)s"` |
| `os.environ.get(..., "INFO")` → `..., "info"` | `.upper()` runs on the result either way |
| `json.dumps(..., ensure_ascii=False)` → `ensure_ascii=None` | `None` is falsy, and the parameter is read as a boolean |

That is the mutation score's ceiling on this module, not a gap in these tests.
"""

from __future__ import annotations

import json
import logging
import sys
import unittest
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import council.observability as obs
from council.observability import TraceContext, _build_logger, emit


@contextmanager
def _pristine_logger() -> Any:
    """Hands `_build_logger` the un-built state it only ever sees once, at import.

    The module builds the logger at import time, so by the time a test runs the
    early return is the only path left. Clearing the handlers reopens the body, and
    the original state goes back afterwards — the logger is process-wide and the
    rest of the suite emits through it.
    """
    logger = logging.getLogger("council")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    logger.handlers.clear()
    try:
        yield logger
    finally:
        logger.handlers.clear()
        logger.handlers.extend(saved_handlers)
        logger.setLevel(saved_level)


class TestLoggerConstruction(unittest.TestCase):
    def test_it_is_the_council_logger(self) -> None:
        with _pristine_logger():
            self.assertEqual(_build_logger().name, "council")

    def test_records_go_to_stderr_and_never_to_stdout(self) -> None:
        """stdout is the answer the user pipes somewhere; telemetry must not join it."""
        with _pristine_logger():
            handler = _build_logger().handlers[0]
        self.assertIsInstance(handler, logging.StreamHandler)
        self.assertIs(handler.stream, sys.stderr)

    def test_the_formatter_adds_nothing_around_the_json(self) -> None:
        """The line IS the JSON: a timestamp prefix would make it unparseable."""
        with _pristine_logger():
            formatter = _build_logger().handlers[0].formatter
        assert formatter is not None
        self.assertEqual(formatter._style._fmt, "%(message)s")

    def test_the_level_defaults_to_info(self) -> None:
        with _pristine_logger(), patch.dict("os.environ", {}, clear=False) as env:
            env.pop("COUNCIL_LOG_LEVEL", None)
            self.assertEqual(_build_logger().level, logging.INFO)

    def test_the_level_comes_from_the_environment_case_insensitively(self) -> None:
        with _pristine_logger(), patch.dict("os.environ", {"COUNCIL_LOG_LEVEL": "debug"}):
            self.assertEqual(_build_logger().level, logging.DEBUG)

    def test_a_second_call_reuses_the_handler_instead_of_stacking_one(self) -> None:
        """Two handlers means every line printed twice — a classic, and silent."""
        with _pristine_logger():
            first = _build_logger()
            second = _build_logger()
            self.assertIs(first, second)
            self.assertEqual(len(second.handlers), 1)


class TestEmittedRecord(unittest.TestCase):
    """The JSON line, field by field."""

    def _emit(self, event: str = "query_start", **fields: Any) -> dict[str, Any]:
        trace = TraceContext(trace_id="trace-abc", question_hash="deadbeef")
        with patch.object(obs, "_LOGGER") as logger:
            emit(event, trace, **fields)
            return json.loads(logger.info.call_args[0][0])

    def test_the_record_carries_exactly_the_declared_fields(self) -> None:
        record = self._emit()
        self.assertEqual(
            set(record), {"ts", "trace_id", "question_hash", "event", "langfuse_opt_in"}
        )
        self.assertEqual(record["trace_id"], "trace-abc")
        self.assertEqual(record["question_hash"], "deadbeef")
        self.assertEqual(record["event"], "query_start")
        self.assertEqual(record["langfuse_opt_in"], obs._LANGFUSE_ENABLED)

    def test_the_event_name_is_the_one_passed_in(self) -> None:
        self.assertEqual(self._emit("stage_2_done")["event"], "stage_2_done")

    def test_extra_fields_are_merged_into_the_record(self) -> None:
        record = self._emit(cost=0.42, voters=3)
        self.assertEqual(record["cost"], 0.42)
        self.assertEqual(record["voters"], 3)

    def test_the_timestamp_is_a_wall_clock_second_to_three_decimals(self) -> None:
        with patch.object(obs.time, "time", return_value=1_760_000_000.123456):
            self.assertEqual(self._emit()["ts"], 1_760_000_000.123)

    def test_non_ascii_stays_readable_rather_than_escaped(self) -> None:
        """`ensure_ascii=False` is the difference between a log you can grep and one you can't."""
        trace = TraceContext(trace_id="t", question_hash="h")
        with patch.object(obs, "_LOGGER") as logger:
            emit("done", trace, note="perché è così")
            line = logger.info.call_args[0][0]
        self.assertIn("perché è così", line)

    def test_it_logs_at_info_and_nowhere_else(self) -> None:
        trace = TraceContext(trace_id="t", question_hash="h")
        with patch.object(obs, "_LOGGER") as logger:
            emit("done", trace)
        logger.info.assert_called_once()
        logger.warning.assert_not_called()
        logger.debug.assert_not_called()


class TestTraceContextDefaults(unittest.TestCase):
    def test_each_context_gets_its_own_trace_id(self) -> None:
        """A shared default would collapse every run into one trace."""
        drawn = [TraceContext().trace_id for _ in range(50)]
        self.assertEqual(len(set(drawn)), len(drawn))

    def test_the_trace_id_is_a_bare_uuid_hex(self) -> None:
        """No dashes: it is copied into `session_id`, which OpenRouter caps at 128."""
        trace_id = TraceContext().trace_id
        self.assertEqual(len(trace_id), 32)
        self.assertNotIn("-", trace_id)

    def test_the_question_hash_starts_empty_rather_than_absent(self) -> None:
        self.assertEqual(TraceContext().question_hash, "")


if __name__ == "__main__":
    unittest.main()
