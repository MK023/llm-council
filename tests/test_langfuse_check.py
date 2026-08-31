"""The check that asks whether traces actually arrived — and the ways it must stay quiet.

The interesting tests here are not "does it count observations". They are the four ways a
monitor stops being a monitor: it alarms on a delay it was told to expect, it alarms on
noise it should ignore, it kills the thing it watches when the API is down, or it reads the
run's identity from a field that splits when a stage fails.

Every one of those was a real draft of this script, corrected by measurement against the
live API on 2026-08-31 rather than by reading the docs alone.
"""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.langfuse_check import (  # noqa: E402
    CALLS_PER_RUN,
    FRESHNESS_WINDOW_DAYS,
    complete_runs_since,
    count_arrived,
    main,
    read_telemetry,
    recent_spend,
)


def _stderr_file(directory: str, *lines: str) -> str:
    path = Path(directory) / "council.stderr"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _record(**fields: object) -> str:
    base: dict[str, object] = {"ts": 1.0, "trace_id": "abc123", "question_hash": "dead"}
    base.update(fields)
    return json.dumps(base)


class TestReadTelemetry(unittest.TestCase):
    """What the council itself says it managed to send."""

    def test_counts_only_calls_that_reached_openrouter(self) -> None:
        """A `generation_id` is proof a call landed; its absence is proof one did not."""
        with TemporaryDirectory() as d:
            path = _stderr_file(
                d,
                _record(event="query_start"),
                _record(event="stage1_response", generation_id="gen-1"),
                _record(event="stage1_response", generation_id="gen-2"),
                _record(event="stage2_ranking", generation_id=None),
                _record(event="stage3_synthesis", generation_id="gen-3"),
            )
            session, sent = read_telemetry(path)
        self.assertEqual(session, "abc123")
        self.assertEqual(sent, 3)

    def test_a_run_that_dies_at_stage_3_is_still_readable(self) -> None:
        """`query_complete` is never emitted when Stage 3 fails — main returns 1 first.

        This is why the count comes from `generation_id`s and not from that record's
        counters: a run that dies half way is exactly the run worth checking.
        """
        with TemporaryDirectory() as d:
            path = _stderr_file(
                d,
                _record(event="stage1_response", generation_id="gen-1"),
                _record(event="stage3_failed", error="API error"),
                "STAGE 3 FAILED: API error: {'code': 502}",
            )
            session, sent = read_telemetry(path)
        self.assertEqual(session, "abc123")
        self.assertEqual(sent, 1)

    def test_plain_text_and_broken_json_are_skipped_not_fatal(self) -> None:
        """stderr carries prose beside the JSON, and half a line if a run is killed."""
        with TemporaryDirectory() as d:
            path = _stderr_file(
                d,
                "QUESTION: something",
                "{not json at all",
                "[]",
                _record(event="stage1_response", generation_id="gen-1"),
            )
            session, sent = read_telemetry(path)
        self.assertEqual((session, sent), ("abc123", 1))

    def test_no_telemetry_yields_no_session(self) -> None:
        with TemporaryDirectory() as d:
            path = _stderr_file(d, "nothing structured here")
            self.assertEqual(read_telemetry(path), (None, 0))

    def test_a_missing_file_is_not_an_exception(self) -> None:
        """The step runs with `if: always()`, so it also runs when the council never did.

        Raising here would fail the job — the guard killing the thing it guards, which is
        the one failure mode this whole script is written to avoid.
        """
        with TemporaryDirectory() as d:
            self.assertEqual(read_telemetry(str(Path(d) / "never-written")), (None, 0))
        self.assertEqual(read_telemetry(""), (None, 0))


class TestArrivalIsCountedBySession(unittest.TestCase):
    """Measured on the live API, and the reason this filters by session and not by trace."""

    def test_counts_generations_and_sums_cost(self) -> None:
        page = {
            "data": [
                {"totalCost": 0.001, "traceId": "t1"},
                {"totalCost": 0.002, "traceId": "t2"},
                {"totalCost": None, "traceId": "t2"},
            ]
        }
        with patch("scripts.langfuse_check._api_get", return_value=page) as api:
            arrived, cost = count_arrived("https://x", "auth", "sess-1")
        self.assertEqual(arrived, 3)
        self.assertAlmostEqual(cost, 0.003)
        self.assertEqual(api.call_args.args[2]["sessionId"], "sess-1")

    def test_the_query_filters_by_session_never_by_trace(self) -> None:
        """On 2026-08-31 one run landed as TWO traces: six voter calls under the trace id
        the client asked for, and the failed chairman under a trace id OpenRouter minted
        for itself. `sessionId` was identical on both. Filtering by `traceId` would have
        counted 6 of 7 and reported data loss that never happened — the false alarm that
        teaches everyone to ignore the check.
        """
        with patch("scripts.langfuse_check._api_get", return_value={"data": []}) as api:
            count_arrived("https://x", "auth", "sess-1")
        params = api.call_args.args[2]
        self.assertIn("sessionId", params)
        self.assertNotIn("traceId", params)


class TestFreshnessIsWhatAlarms(unittest.TestCase):
    """The alarm lives on a window measured in days, because the delay is measured in minutes."""

    def test_a_session_needs_a_full_run_to_count_as_complete(self) -> None:
        data = [{"sessionId": "s1", "startTime": f"2026-08-3{i % 2}"} for i in range(CALLS_PER_RUN)]
        data += [{"sessionId": "s2", "startTime": "2026-08-30"}] * (CALLS_PER_RUN - 1)
        with patch("scripts.langfuse_check._api_get", return_value={"data": data, "meta": {}}):
            complete, sessions, latest = complete_runs_since("https://x", "auth", 8)
        self.assertEqual(complete, 1)
        self.assertEqual(sessions, 2)
        self.assertEqual(latest, "2026-08-31")

    def test_observations_without_a_session_are_not_council_runs(self) -> None:
        """The Langfuse project also receives every other call on the OpenRouter account.

        Broadcast is account-wide, not project-wide: on 2026-08-31 four unrelated
        generations landed in the same project with no session and no user. Counting them
        would make ingestion look alive on traffic that has nothing to do with the council.
        """
        data = [{"sessionId": None, "startTime": "2026-08-31"}] * 20
        with patch("scripts.langfuse_check._api_get", return_value={"data": data, "meta": {}}):
            complete, sessions, latest = complete_runs_since("https://x", "auth", 8)
        self.assertEqual((complete, sessions, latest), (0, 0, None))

    def test_pagination_is_followed_to_the_end(self) -> None:
        pages = [
            {"data": [{"sessionId": "s1", "startTime": "2026-08-30"}] * 4, "meta": {"cursor": "c"}},
            {"data": [{"sessionId": "s1", "startTime": "2026-08-31"}] * 3, "meta": {}},
        ]
        with patch("scripts.langfuse_check._api_get", side_effect=pages):
            complete, sessions, _ = complete_runs_since("https://x", "auth", 8)
        self.assertEqual((complete, sessions), (1, 1))

    def test_spend_sums_across_pages(self) -> None:
        pages = [
            {"data": [{"totalCost": 0.01}, {"totalCost": None}], "meta": {"cursor": "c"}},
            {"data": [{"totalCost": 0.02}], "meta": {"cursor": None}},
        ]
        with patch("scripts.langfuse_check._api_get", side_effect=pages):
            spend, calls = recent_spend("https://x", "auth", 30)
        self.assertAlmostEqual(spend, 0.03)
        self.assertEqual(calls, 3)


class TestMainNeverKillsWhatItWatches(unittest.TestCase):
    """`main` returns 0 on every path. The council's verdict belongs to the step before."""

    def setUp(self) -> None:
        self.env = {
            "LANGFUSE_PUBLIC_KEY": "pk",
            "LANGFUSE_SECRET_KEY": "sk",
            "LANGFUSE_BASE_URL": "https://x",
        }

    def _run(self, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with (
            patch.dict("os.environ", self.env if env is None else env, clear=True),
            patch.object(sys, "argv", ["langfuse_check.py", *argv]),
            redirect_stdout(buffer),
        ):
            code = main()
        return code, buffer.getvalue()

    def test_missing_credentials_skip_the_check_without_failing(self) -> None:
        with TemporaryDirectory() as d:
            path = _stderr_file(d, _record(event="a", generation_id="g"))
            code, out = self._run([path], env={})
        self.assertEqual(code, 0)
        self.assertIn("::warning::", out)

    def test_an_unreachable_api_leaves_the_verdict_alone(self) -> None:
        """Langfuse being down must never turn the council's own result red."""
        with TemporaryDirectory() as d:
            path = _stderr_file(d, _record(event="a", generation_id="g"))
            with patch(
                "scripts.langfuse_check._api_get",
                side_effect=urllib.error.URLError("down"),
            ):
                code, out = self._run([path])
        self.assertEqual(code, 0)
        self.assertIn("verdetto invariato", out)

    def test_fewer_arrivals_than_sent_is_reported_but_never_warned(self) -> None:
        """Langfuse Cloud documents up to 15 minutes of ingestion delay for third-party
        exporters, and OpenRouter Broadcast is one. A 90-second poll that shouted "data
        loss" would be shouting at a documented, legitimate delay.
        """
        session_page = {"data": []}
        fresh_page = {
            "data": [{"sessionId": "s", "startTime": "2026-08-31"}] * CALLS_PER_RUN,
            "meta": {},
        }
        with TemporaryDirectory() as d:
            path = _stderr_file(d, _record(event="a", generation_id="g"))
            with (
                patch("scripts.langfuse_check.time.sleep"),
                patch(
                    "scripts.langfuse_check._api_get",
                    side_effect=[session_page] * 6 + [fresh_page, {"data": [], "meta": {}}],
                ),
            ):
                code, out = self._run([path])
        self.assertEqual(code, 0)
        self.assertIn("arrivate", out)
        self.assertNotIn("::warning::", out)

    def test_no_complete_run_in_the_window_is_the_one_thing_that_warns(self) -> None:
        empty = {"data": [], "meta": {}}
        with TemporaryDirectory() as d:
            path = _stderr_file(d, _record(event="a", generation_id="g"))
            with (
                patch("scripts.langfuse_check.time.sleep"),
                patch("scripts.langfuse_check._api_get", return_value=empty),
            ):
                code, out = self._run([path])
        self.assertEqual(code, 0)
        self.assertIn("::warning::", out)
        self.assertIn(str(FRESHNESS_WINDOW_DAYS), out)

    def test_the_freshness_window_outlives_a_late_scheduler(self) -> None:
        """Eight days, not seven: GitHub cron slipped by nearly seven hours on 2026-08-31.

        A window equal to the schedule would fire on a late scheduler instead of on dead
        ingestion — an alarm about the wrong system.
        """
        self.assertGreater(FRESHNESS_WINDOW_DAYS, 7)


if __name__ == "__main__":
    unittest.main()
