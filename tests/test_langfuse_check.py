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

from scripts import langfuse_check as lc  # noqa: E402
from scripts.langfuse_check import (  # noqa: E402
    CALLS_PER_RUN,
    FRESHNESS_WINDOW_DAYS,
    MAX_PAGES,
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


class TestTheRequestItself(unittest.TestCase):
    """`_api_get` was reached by no test at all: v1 for v2, Bearer for Basic, no timeout —
    every one of those mutants stayed green, because every other test mocks this function.
    """

    def _capture(self, base_url: str = "https://cloud.example") -> tuple[str, dict[str, str]]:
        captured: dict[str, object] = {}

        class _Resp:
            def __enter__(self_inner):  # noqa: N805
                return self_inner

            def __exit__(self_inner, *_: object) -> bool:  # noqa: N805
                return False

            def read(self_inner) -> bytes:  # noqa: N805
                return b'{"data": [], "meta": {}}'

        def fake_urlopen(req: object, timeout: object = None) -> object:
            captured["url"] = req.full_url  # type: ignore[attr-defined]
            captured["headers"] = dict(req.headers)  # type: ignore[attr-defined]
            captured["timeout"] = timeout
            return _Resp()

        with patch("scripts.langfuse_check.urllib.request.urlopen", fake_urlopen):
            lc._api_get(base_url, "dXNlcjpwYXNz", {"limit": "1"})
        return str(captured["url"]), captured  # type: ignore[return-value]

    def test_it_calls_the_v2_endpoint_with_basic_auth_and_a_timeout(self) -> None:
        url, captured = self._capture()
        self.assertIn("/api/public/v2/observations?", url)
        self.assertNotIn("/v1/", url)
        self.assertEqual(captured["headers"].get("Authorization"), "Basic dXNlcjpwYXNz")  # type: ignore[union-attr]
        self.assertEqual(captured["timeout"], lc.TIMEOUT_S)

    def test_a_non_https_base_url_is_refused(self) -> None:
        """The base URL comes from a secret and reaches `urlopen`, which speaks `file://`
        (reading local disk instead of the network) and `http://` (Basic auth in the clear).
        """
        for base in ("file:///etc", "http://insecure.example", "ftp://x", "HTTPS_evil"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                lc._api_get(base, "auth", {})

    def test_https_is_accepted_case_insensitively(self) -> None:
        url, _ = self._capture("HTTPS://Cloud.Example/")
        self.assertTrue(url.lower().startswith("https://"))


class TestTheUserIdIsNotACopy(unittest.TestCase):
    def test_it_is_the_same_object_the_council_sends(self) -> None:
        """Two hand-kept copies drifting would not silence this check — it would make it
        warn every week forever about an ingestion that is working perfectly.
        """
        from council.config import USER_ID
        from council.stages import _USER_ID

        self.assertEqual(lc.COUNCIL_USER_ID, USER_ID)
        self.assertEqual(_USER_ID, USER_ID)


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

    def test_a_stuck_cursor_cannot_hang_the_job(self) -> None:
        """An API that keeps returning the same cursor looped forever, and the job died on
        `timeout-minutes` — a red build blamed on the council for a fault in the monitor.
        """
        stuck = {"data": [], "meta": {"cursor": "always-the-same"}}
        for call in (
            lambda: complete_runs_since("https://x", "auth", 8),
            lambda: recent_spend("https://x", "auth", 30),
        ):
            with self.subTest(call=call):
                with patch("scripts.langfuse_check._api_get", return_value=stuck) as api:
                    call()
                self.assertEqual(api.call_count, MAX_PAGES)

    def test_the_page_cap_is_a_number_and_not_just_a_symbol(self) -> None:
        """The test above asserts `call_count == MAX_PAGES`, which passes at any value.

        Set `MAX_PAGES = 100000` and it stays green while the loop is unbounded again — the
        assertion follows the constant instead of constraining it. Measured: a 30-day window
        holds ~270 observations, so three pages of 100. Fifty is sixteen times the need and
        still a bound; a hundred thousand is not a bound, it is the `while True` renamed.
        """
        self.assertGreaterEqual(MAX_PAGES, 10)
        self.assertLessEqual(MAX_PAGES, 100)

    def test_a_price_that_arrives_as_a_string_is_still_money(self) -> None:
        """Langfuse's own doc sample shows price fields as strings (`"0.000005"`), and
        `sum()` over a string raises TypeError — which used to escape `main` entirely.
        """
        page = {"data": [{"totalCost": "0.01"}, {"totalCost": 0.02}], "meta": {}}
        with patch("scripts.langfuse_check._api_get", return_value=page):
            _, cost = count_arrived("https://x", "auth", "s")
            spend, _ = recent_spend("https://x", "auth", 30)
        self.assertAlmostEqual(cost, 0.03)
        self.assertAlmostEqual(spend, 0.03)

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

    def test_no_shape_of_malformed_response_can_fail_the_job(self) -> None:
        """The contract is "never fail", and the first draft wrote it as a list of types.

        `{"data": null}` and a string `totalCost` both escaped that list as `TypeError`,
        exited non-zero, turned the E2E red and sent `status=error` to Sentry — a false
        alarm about the council raised by the thing watching the council.
        """
        shapes: list[object] = [
            {"meta": {}},
            {"data": None, "meta": {}},
            {"data": [], "meta": []},
            {"data": [{"totalCost": "not-a-number"}], "meta": {}},
            {"data": "a string"},
            [],
            None,
        ]
        for shape in shapes:
            with self.subTest(shape=shape), TemporaryDirectory() as d:
                path = _stderr_file(d, _record(event="a", generation_id="g"))
                with (
                    patch("scripts.langfuse_check.time.sleep"),
                    patch("scripts.langfuse_check._api_get", return_value=shape),
                ):
                    code, _ = self._run([path])
                self.assertEqual(code, 0)

    def test_an_exception_never_leaks_the_url_or_the_credentials(self) -> None:
        """Only the exception's TYPE is printed. Its message could carry the request URL,
        and the URL is built from a secret.
        """
        with TemporaryDirectory() as d:
            path = _stderr_file(d, _record(event="a", generation_id="g"))
            with (
                patch("scripts.langfuse_check.time.sleep"),
                patch(
                    "scripts.langfuse_check._api_get",
                    side_effect=RuntimeError("https://secret-host/x?key=pk-lf-CANARY"),
                ),
            ):
                code, out = self._run([path])
        self.assertEqual(code, 0)
        self.assertNotIn("CANARY", out)
        self.assertNotIn("secret-host", out)
        self.assertIn("RuntimeError", out)

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
