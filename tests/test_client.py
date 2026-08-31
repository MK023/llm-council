"""Unit tests for OpenRouter client error handling (network transport mocked)."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

from council.client import OpenRouterClient, OpenRouterError
from council.config import MAX_RESPONSE_BYTES


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


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))  # type: ignore[arg-type]


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
