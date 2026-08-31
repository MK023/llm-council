"""Unit tests for OpenRouter client error handling (network transport mocked)."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

from council.client import OpenRouterClient, OpenRouterError
from council.config import (
    MAX_RESPONSE_BYTES,
    MAX_RETRIES,
    RATE_LIMIT_FALLBACK_SECONDS,
    RETRY_AFTER_CAP_SECONDS,
    RETRY_BACKOFF_SECONDS,
    VOTER_MODELS,
)


def _mock_response(
    body: dict[str, Any] | bytes,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Builds a mock urlopen context-manager response."""
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.headers = headers or {}
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _http_error(
    code: int, body: bytes = b"", headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(body))  # type: ignore[arg-type]


_OK_BODY: dict[str, Any] = {
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"total_tokens": 10, "cost": 0.0001},
}


class TestRetryLogic(unittest.TestCase):
    """HTTP-error retry behavior: retry only on rate-limit + server transient."""

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_429_retries_then_succeeds(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        mock_urlopen.side_effect = [_http_error(429), _mock_response(_OK_BODY)]
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.content, "ok")
        self.assertEqual(result.attempts, 2)
        mock_sleep.assert_called_once()

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_503_retries_then_succeeds(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        mock_urlopen.side_effect = [_http_error(503), _mock_response(_OK_BODY)]
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.attempts, 2)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_401_fails_fast_no_retry(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """Auth failure (401) must NOT retry — wastes quota and masks bug."""
        mock_urlopen.side_effect = _http_error(401, b'{"error":"invalid key"}')
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(ctx.exception.status_code, 401)
        mock_sleep.assert_not_called()
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_400_fails_fast_no_retry(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """Bad request (400) must NOT retry — caller bug, not transient."""
        mock_urlopen.side_effect = _http_error(400, b'{"error":"bad model"}')
        with self.assertRaises(OpenRouterError):
            self.client.call("test/model", self.messages, max_tokens=10)
        mock_sleep.assert_not_called()

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_urlerror_retries_until_max(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Connection error retries up to MAX_RETRIES, then raises."""
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIn("All 3 attempts failed", str(ctx.exception))
        self.assertEqual(mock_urlopen.call_count, 3)


class TestErrorTextCannotForgeLines(unittest.TestCase):
    """An error message carries the provider's words, and those words reach two parsers.

    The text inside `{"error": ...}` is chosen upstream of us. It is printed to stderr, where
    `scripts/langfuse_check.py` now parses one JSON object per line, and into the public log
    of the weekly E2E, where a line starting at column 0 with `::` is a GitHub workflow
    command. A newline in that text is therefore not cosmetic: it is a way to add a line.

    Sanitising in the constructor rather than at each `print` is deliberate — every message
    converges here, and a guard per call site is a guard that will be forgotten at the next
    one.
    """

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    def _message_for(self, error: object) -> str:
        with (
            patch("council.client.time.sleep"),
            patch("council.client.urllib.request.urlopen") as mock_urlopen,
        ):
            mock_urlopen.return_value = _mock_response({"error": error})
            with self.assertRaises(OpenRouterError) as ctx:
                self.client.call("test/model", self.messages, max_tokens=10)
        return str(ctx.exception)

    def test_a_provider_message_cannot_add_a_line(self) -> None:
        for payload in (
            "boom\n::error::forged",
            "boom\r\n::add-mask::secret",
            "boom\r::stop-commands::tok",
            {"message": 'boom\n{"trace_id": "x", "generation_id": "fake"}'},
        ):
            with self.subTest(payload=payload):
                message = self._message_for(payload)
                self.assertNotIn("\n", message)
                self.assertNotIn("\r", message)

    def test_control_characters_do_not_survive(self) -> None:
        """ESC drives terminal escape sequences; NUL truncates in some consumers.

        Every character asserted against must appear in the INPUT. The first draft checked
        for `\\x7f` in a payload that never contained one — an assertion about an absent
        character is vacuously true, and the mutant that drops DEL from the table survived
        the whole suite because of it.
        """
        codes = [*range(0x20), 0x7F, 0x85, 0x2028, 0x2029]
        hostile = "".join(chr(code) for code in codes)
        message = self._message_for(f"red \x1b[31mtext\x1b[0m{hostile}end")
        for code in codes:
            with self.subTest(code=hex(code)):
                self.assertNotIn(chr(code), message)
        self.assertIn("end", message)

    def test_the_line_count_promise_holds_for_every_terminator_python_knows(self) -> None:
        """`splitlines()` splits on NEL, LS and PS, which are not C0.

        The first draft asserted `len(splitlines()) == 1` while feeding only `\\n` — an oracle
        stronger than the table could keep, passing by luck. Not exploitable against today's
        reader (`for line in fh` honours only `\\n`/`\\r`/`\\r\\n`), but this codebase already
        uses `.splitlines()` elsewhere, so the gap was one refactor from opening.
        """
        forged = '{"ts": 1, "trace_id": "hijack", "generation_id": "gen-fake"}'
        for terminator in ("\n", "\r", "\r\n", "\x85", " ", " ", "\v", "\f"):
            with self.subTest(terminator=repr(terminator)):
                message = self._message_for(f"boom{terminator}{forged}")
                self.assertEqual(len(message.splitlines()), 1)

    def test_the_request_id_is_flattened_too(self) -> None:
        """It comes from a response header — an obs-fold continuation can put a CRLF in it —
        and `__main__.py` prints it beside the message.
        """
        exc = OpenRouterError("ok", request_id="req\r\n::error::forged")
        self.assertEqual(exc.request_id, "req  ::error::forged")
        self.assertIsNone(OpenRouterError("ok").request_id)

    def test_a_non_string_message_does_not_raise_inside_the_constructor(self) -> None:
        """An `AttributeError` raised here would escape every `except OpenRouterError`."""
        self.assertIn("502", str(OpenRouterError(502)))  # type: ignore[arg-type]

    def test_a_forged_json_line_cannot_reach_a_line_parser(self) -> None:
        """The concrete consequence: `langfuse_check.read_telemetry` counts one object per
        line, so a newline in this text would let a provider invent generations that never
        happened.
        """
        forged = '{"ts": 1, "trace_id": "hijack", "generation_id": "gen-fake"}'
        message = self._message_for(f"boom\n{forged}\n{forged}")
        self.assertEqual(len(message.splitlines()), 1)
        # A space and not an empty string: deleting the newline outright would glue the
        # words on either side of it. Safe either way, unreadable one way — and this project
        # has already paid once for a diagnosis that was technically present and useless.
        self.assertIn("boom {", message)

    def test_ordinary_text_is_left_alone(self) -> None:
        """Sanitising must not mangle the diagnosis it exists to protect."""
        message = self._message_for({"message": "Provider returned error", "code": 502})
        self.assertIn("Provider returned error", message)
        self.assertIn("502", message)

    def test_our_own_messages_are_unchanged(self) -> None:
        """Every OpenRouterError goes through this, including the ones we write ourselves."""
        self.assertEqual(
            str(OpenRouterError("All 3 attempts failed for model='x': HTTPError HTTP 502")),
            "All 3 attempts failed for model='x': HTTPError HTTP 502",
        )


class TestRateLimitBackoff(unittest.TestCase):
    """A 429 waits for a window to reopen; a 5xx waits for a moment. Different waits.

    Twice — 2026-08-24 and 2026-08-31 — the weekly E2E lost the same seat to a Stage 2 429
    after three attempts spanning three seconds. OpenRouter documents `Retry-After` on that
    response and the client read only the body, never the headers.
    """

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_retry_after_is_obeyed(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        mock_urlopen.side_effect = [
            _http_error(429, headers={"Retry-After": "12"}),
            _mock_response(_OK_BODY),
        ]
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.attempts, 2)
        mock_sleep.assert_called_once_with(12)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_retry_after_is_capped(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """An hour-long hint must not park the run for an hour."""
        mock_urlopen.side_effect = [
            _http_error(429, headers={"Retry-After": "3600"}),
            _mock_response(_OK_BODY),
        ]
        self.client.call("test/model", self.messages, max_tokens=10)
        mock_sleep.assert_called_once_with(RETRY_AFTER_CAP_SECONDS)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_429_without_a_hint_uses_the_rate_limit_fallback(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """The exact shape of the two E2E failures: a 429 and no header to read."""
        mock_urlopen.side_effect = [_http_error(429), _mock_response(_OK_BODY)]
        self.client.call("test/model", self.messages, max_tokens=10)
        mock_sleep.assert_called_once_with(RATE_LIMIT_FALLBACK_SECONDS)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_server_error_keeps_the_short_backoff(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A 502 waits for an instant, not for a window: the long waits are 429-only."""
        mock_urlopen.side_effect = [_http_error(502), _mock_response(_OK_BODY)]
        self.client.call("test/model", self.messages, max_tokens=10)
        mock_sleep.assert_called_once_with(RETRY_BACKOFF_SECONDS[0])

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_503_hint_is_obeyed_too(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """OpenRouter documents Retry-After on 429 **and** 503, and a hint is an instruction.

        The first draft asserted the opposite here, on a comment that claimed 429 was the
        only code. The docs say: *"On 429 Too Many Requests and 503 Service Unavailable
        responses, OpenRouter may include a standard HTTP Retry-After response header"*.
        A test written from the code's own claim only ever confirms the claim.
        """
        mock_urlopen.side_effect = [
            _http_error(503, headers={"Retry-After": "25"}),
            _mock_response(_OK_BODY),
        ]
        self.client.call("test/model", self.messages, max_tokens=10)
        mock_sleep.assert_called_once_with(25)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_503_without_a_hint_keeps_the_short_backoff(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """The long fallback is a guess about a rate-limit window: 503 has no window."""
        mock_urlopen.side_effect = [_http_error(503), _mock_response(_OK_BODY)]
        self.client.call("test/model", self.messages, max_tokens=10)
        mock_sleep.assert_called_once_with(RETRY_BACKOFF_SECONDS[0])

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_an_undocumented_code_ignores_the_header(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """502 is not on OpenRouter's Retry-After list: obeying it there invents policy."""
        mock_urlopen.side_effect = [
            _http_error(502, headers={"Retry-After": "25"}),
            _mock_response(_OK_BODY),
        ]
        self.client.call("test/model", self.messages, max_tokens=10)
        mock_sleep.assert_called_once_with(RETRY_BACKOFF_SECONDS[0])

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_429_in_the_body_waits_like_a_429_in_the_status_line(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """OpenRouter puts provider errors in a 200 body — a rate limit among them.

        Without this the same diagnosis got 3 seconds through one channel and 40 through the
        other, and the slow one is the channel an attacker does not pick. It is the same
        class PR #36 closed for 502, left open for 429 the day after.
        """
        mock_urlopen.return_value = _mock_response({"error": {"code": 429, "message": "slow down"}})
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(
            [call.args[0] for call in mock_sleep.call_args_list],
            [RATE_LIMIT_FALLBACK_SECONDS, RATE_LIMIT_FALLBACK_SECONDS],
        )

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_an_unparsable_hint_falls_back_instead_of_crashing(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Retry-After also has an HTTP-date form, and a hostile endpoint can send anything.

        The header is attacker-influenced input at a trust boundary: it must never reach
        `time.sleep` unvalidated, and must never raise.
        """
        # -1 is here and not for symmetry: the boundary mutant `< 0` -> `< -1` lets it
        # through to `time.sleep(-1)`, which raises ValueError, escapes the retry loop and
        # kills the run. Without this value that mutant survives the whole suite.
        for hint in ("Wed, 21 Oct 2026 07:28:00 GMT", "soon", "", "-1", "-5", "1e9", "nan"):
            with self.subTest(hint=hint):
                mock_sleep.reset_mock()
                mock_urlopen.side_effect = [
                    _http_error(429, headers={"Retry-After": hint}),
                    _mock_response(_OK_BODY),
                ]
                self.client.call("test/model", self.messages, max_tokens=10)
                (waited,), _ = mock_sleep.call_args
                self.assertGreaterEqual(waited, 0)
                self.assertLessEqual(waited, RETRY_AFTER_CAP_SECONDS)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_zero_hint_means_now_not_the_fallback(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """`Retry-After: 0` is a usable hint meaning 'immediately', not a missing one.

        Guards the boundary of the sign check: `< 0` and `<= 0` differ only here, and the
        second would sit out twenty seconds a provider explicitly said were unnecessary.
        """
        mock_urlopen.side_effect = [
            _http_error(429, headers={"Retry-After": "0"}),
            _mock_response(_OK_BODY),
        ]
        self.client.call("test/model", self.messages, max_tokens=10)
        mock_sleep.assert_called_once_with(0)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_long_wait_does_not_leak_into_the_next_attempt(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A 429 then a 502: the second wait is the short one, not the rate-limit one.

        The override is per-attempt state. Hoisting it out of the loop leaves one seat's
        rate limit slowing down every later hiccup in the same call — invisible, because
        every assertion about a single-code sequence would still pass.
        """
        mock_urlopen.side_effect = [_http_error(429), _http_error(502), _mock_response(_OK_BODY)]
        self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(
            [call.args[0] for call in mock_sleep.call_args_list],
            [RATE_LIMIT_FALLBACK_SECONDS, RETRY_BACKOFF_SECONDS[1]],
        )

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_persistent_429_still_names_the_code(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        mock_urlopen.side_effect = _http_error(429, headers={"Retry-After": "5"})
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(mock_urlopen.call_count, MAX_RETRIES)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(mock_sleep.call_count, MAX_RETRIES - 1)


class TestBackoffBudgetIsCoherent(unittest.TestCase):
    """The constants have to hold a relationship, and every other test pins only the symbol.

    `RATE_LIMIT_FALLBACK_SECONDS = 0` passed the entire suite: each assertion compared the
    behaviour against the constant, so the constant could be anything and stay green. These
    assertions are about the numbers themselves — the mutation gate reaches `config.py` too.
    """

    def test_the_fallback_is_longer_than_the_whole_short_backoff(self) -> None:
        """Otherwise it does not solve the problem it exists for: three seconds, twice."""
        self.assertGreater(RATE_LIMIT_FALLBACK_SECONDS, sum(RETRY_BACKOFF_SECONDS))

    def test_the_cap_is_not_below_the_fallback(self) -> None:
        """A ceiling under the guess would mean trusting a provider's number less than ours."""
        self.assertGreaterEqual(RETRY_AFTER_CAP_SECONDS, RATE_LIMIT_FALLBACK_SECONDS)

    def test_the_worst_case_wait_stays_inside_the_e2e_budget(self) -> None:
        """Seven sequential calls, every one rate-limited at the ceiling, under ten minutes.

        `e2e.yml` carries `timeout-minutes: 15`. If raising these constants ever pushed the
        worst case past it, the weekly sentinel would start dying of its own patience and
        report a timeout instead of the seat that is actually failing.
        """
        sleeps_per_call = MAX_RETRIES - 1
        worst_case_s = sleeps_per_call * RETRY_AFTER_CAP_SECONDS * len(VOTER_MODELS) * 2
        self.assertLess(worst_case_s, 10 * 60)


class TestErrorInsideBodyRetry(unittest.TestCase):
    """OpenRouter delivers upstream failures inside a 200 body, not as an HTTP status.

    On 2026-08-31 the weekly E2E lost its chairman — and with it the whole run — to
    `{"error": {"message": "Internal server error", "code": 502}}` returned with HTTP 200.
    `attempts=1`: no retry, although 502 is listed in RETRYABLE_STATUS_CODES. The list was
    only ever consulted from `except HTTPError`, which a 200 never reaches.
    """

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_body_error_502_retries_then_succeeds(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """The exact payload that killed the 2026-08-31 chairman."""
        mock_urlopen.side_effect = [
            _mock_response({"error": {"message": "Internal server error", "code": 502}}),
            _mock_response(_OK_BODY),
        ]
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.content, "ok")
        self.assertEqual(result.attempts, 2)
        mock_sleep.assert_called_once()

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_body_error_502_exhausts_retries_and_names_the_code(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A persistent 502 must still surface its code — the code IS the diagnosis."""
        mock_urlopen.return_value = _mock_response({"error": {"code": 502}})
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("502", str(ctx.exception))

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_body_error_400_fails_fast(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A caller bug in the body is still a caller bug: retrying wastes quota."""
        mock_urlopen.return_value = _mock_response({"error": {"code": 400, "message": "bad"}})
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertEqual(ctx.exception.status_code, 400)
        mock_sleep.assert_not_called()

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_body_error_without_a_code_fails_fast(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """No code means no transient diagnosis: fail fast rather than guess.

        Both shapes, because they take different branches: a string `error` never reaches
        `.get("code")` at all, a dict without `code` reaches it and gets None. The first
        draft tested only the string and so left the branch it meant to cover untested.
        """
        for body in ({"error": "model not found"}, {"error": {"message": "no code here"}}):
            with self.subTest(body=body):
                mock_urlopen.reset_mock()
                mock_urlopen.return_value = _mock_response(body)
                with self.assertRaises(OpenRouterError) as ctx:
                    self.client.call("test/model", self.messages, max_tokens=10)
                self.assertEqual(mock_urlopen.call_count, 1)
                self.assertIsNone(ctx.exception.status_code)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_non_int_code_is_not_a_code(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """`502.0 in RETRYABLE_STATUS_CODES` is True — the isinstance guard is the defence.

        Without it a float, or a numeric string coerced somewhere upstream, would buy three
        attempts off a provider we never agreed to trust that far. This assertion exists
        because the mutant that deletes the guard survived the whole suite.
        """
        for code in (502.0, "502", True, None):
            with self.subTest(code=code):
                mock_urlopen.reset_mock()
                mock_urlopen.return_value = _mock_response({"error": {"code": code}})
                with self.assertRaises(OpenRouterError) as ctx:
                    self.client.call("test/model", self.messages, max_tokens=10)
                self.assertEqual(mock_urlopen.call_count, 1)
                self.assertNotEqual(ctx.exception.status_code, 502)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_size_cap_still_costs_exactly_one_request(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """An oversized body is a compromised endpoint, and gets ONE chance, not three.

        The property held only because the size-cap raise omits `status_code`. Omission is
        not an assertion: the existing size-cap tests use `return_value`, so they would have
        passed at three attempts too. This pins the request count itself.
        """
        mock_urlopen.return_value = _mock_response(b"x" * (MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertIsNone(ctx.exception.status_code)
        mock_sleep.assert_not_called()

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_exhaustion_keeps_the_provider_words_and_the_request_id(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """The diagnosis must survive the retries, or widening them made things worse.

        `request_id` and the upstream provider's name are the two fields you take to
        OpenRouter to ask what happened. The first draft of this retry path dropped both,
        making a persistent 502 less diagnosable than a plain 400.
        """
        body = {
            "error": {
                "message": "Provider returned error",
                "code": 502,
                "metadata": {"provider_name": "Novita"},
            }
        }
        mock_urlopen.return_value = _mock_response(body, {"x-request-id": "req-chairman"})
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("chair/model", self.messages, max_tokens=10)
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(ctx.exception.request_id, "req-chairman")
        self.assertIn("Novita", str(ctx.exception))
        self.assertIn("502", str(ctx.exception))

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_semantic_failures_still_fail_fast(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A refusal and a truncation are verdicts, not hiccups — retrying changes nothing."""
        for body in (
            {"choices": [{"message": {"content": "", "refusal": "policy"}}]},
            {
                "choices": [
                    {
                        "message": {"content": "", "reasoning": "thinking"},
                        "finish_reason": "length",
                    }
                ]
            },
        ):
            with self.subTest(body=body):
                mock_urlopen.reset_mock()
                mock_urlopen.return_value = _mock_response(body)
                with self.assertRaises(OpenRouterError):
                    self.client.call("test/model", self.messages, max_tokens=10)
                self.assertEqual(mock_urlopen.call_count, 1)


class TestResponseValidation(unittest.TestCase):
    """Schema validation of API responses."""

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_missing_choices_array_fails(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        mock_urlopen.return_value = _mock_response({"usage": {}})
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIn("choices", str(ctx.exception))

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_api_error_field_fails(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"error": "model not found"})
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIn("API error", str(ctx.exception))

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_oversized_response_caps(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """A 300KB response must trip the 256KB cap and raise."""
        mock_urlopen.return_value = _mock_response(b"x" * (300 * 1024))
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIn("exceeded", str(ctx.exception).lower())

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_cap_is_applied_when_reading_not_after(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """The defence has two halves, and only one was tested.

        A MagicMock returns its payload whatever argument `read()` gets, so the test
        above passes even with an unbounded `resp.read()` — the size check catches it
        afterwards, but the bytes are already in memory. Against a compromised endpoint
        streaming gigabytes, "read everything then complain" is not a defence.
        This asserts the bounded read itself. Verified by mutation 2026-07-26.
        """
        mock_urlopen.return_value = _mock_response(b"x" * (300 * 1024))
        with self.assertRaises(OpenRouterError):
            self.client.call("test/model", self.messages, max_tokens=10)
        read_call = mock_urlopen.return_value.read.call_args
        self.assertTrue(read_call.args, "read() chiamata senza limite di byte")
        self.assertLessEqual(read_call.args[0], MAX_RESPONSE_BYTES + 1)


class TestEdgeCases(unittest.TestCase):
    """Edge cases that previously caused silent failures or crashes."""

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_null_cost_defaults_to_zero(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A response with cost=None must not propagate TypeError."""
        body = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 5, "cost": None},
        }
        mock_urlopen.return_value = _mock_response(body)
        client = OpenRouterClient("sk-or-v1-test")
        result = client.call("test/model", [{"role": "user", "content": "x"}], max_tokens=10)
        self.assertEqual(result.cost, 0.0)
        self.assertEqual(result.tokens, 5)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_request_id_captured(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(
            _OK_BODY, headers={"x-request-id": "req-abc-123"}
        )
        client = OpenRouterClient("sk-or-v1-test")
        result = client.call("test/model", [{"role": "user", "content": "x"}], max_tokens=10)
        self.assertEqual(result.request_id, "req-abc-123")

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_missing_request_id_is_none(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY, headers={})
        client = OpenRouterClient("sk-or-v1-test")
        result = client.call("test/model", [{"role": "user", "content": "x"}], max_tokens=10)
        self.assertIsNone(result.request_id)


class TestRepr(unittest.TestCase):
    """Defense against accidental key leakage via debug print."""

    def test_repr_does_not_contain_key(self) -> None:
        client = OpenRouterClient("sk-or-v1-secret-do-not-leak-this")
        self.assertNotIn("secret-do-not-leak", repr(client))
        self.assertIn("REDACTED", repr(client))


if __name__ == "__main__":
    unittest.main()
