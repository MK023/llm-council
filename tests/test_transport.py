"""The HTTP contract of the client: what goes on the wire, and the retry arithmetic.

The suite already proved the client *behaves* — it retries on 429, it fails fast on
401, it caps the read. What no assertion covered was the shape of the request itself
(URL, headers, timeout, body) and the numbers behind the retry loop (which backoff,
how many, how the attempt counter and the latency are computed).

Mutation testing named the gap: 39 mutants survived inside `call` and 27 inside
`_request` on 2026-08-13, with 100% line coverage. Every line ran; nothing checked
what the line produced. These tests pin the produced values.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from typing import Any
from unittest.mock import MagicMock, call, patch

from council.client import OpenRouterClient, OpenRouterError, _build_payload
from council.config import (
    APP_TITLE,
    HTTP_REFERER,
    MAX_RETRIES,
    OPENROUTER_URL,
    PROVIDER_ROUTING,
    RETRY_BACKOFF_SECONDS,
    TEMPERATURE,
    TIMEOUT_SECONDS,
    USER_AGENT,
)

_OK_BODY: dict[str, Any] = {
    "choices": [{"message": {"content": "  ok  "}}],
    "usage": {"total_tokens": 10, "cost": 0.0001},
}


def _mock_response(
    body: dict[str, Any] | bytes,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    resp = MagicMock()
    resp.read.return_value = raw
    resp.headers = headers if headers is not None else {}
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))  # type: ignore[arg-type]


class TestRequestOnTheWire(unittest.TestCase):
    """URL, headers and timeout are part of the contract with OpenRouter."""

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    def _sent_request(self, mock_urlopen: MagicMock) -> Any:
        return mock_urlopen.call_args[0][0]

    def _sent_headers(self, mock_urlopen: MagicMock) -> dict[str, str]:
        # urllib capitalises header names on the way in; compare on a lowered key.
        return {k.lower(): v for k, v in self._sent_request(mock_urlopen).headers.items()}

    @patch("council.client.urllib.request.urlopen")
    def test_the_endpoint_is_the_configured_one(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(self._sent_request(mock_urlopen).full_url, OPENROUTER_URL)

    @patch("council.client.urllib.request.urlopen")
    def test_the_key_travels_as_a_bearer_token(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(
            self._sent_headers(mock_urlopen)["authorization"], "Bearer sk-or-v1-test-key"
        )

    @patch("council.client.urllib.request.urlopen")
    def test_the_identifying_headers_are_all_present(self, mock_urlopen: MagicMock) -> None:
        """OpenRouter attributes usage by these three; a typo makes the app anonymous."""
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        headers = self._sent_headers(mock_urlopen)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["user-agent"], USER_AGENT)
        self.assertEqual(headers["http-referer"], HTTP_REFERER)
        self.assertEqual(headers["x-title"], APP_TITLE)

    @patch("council.client.urllib.request.urlopen")
    def test_the_call_is_bounded_by_the_configured_timeout(self, mock_urlopen: MagicMock) -> None:
        """No timeout means a hung provider hangs the whole council."""
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], TIMEOUT_SECONDS)

    @patch("council.client.urllib.request.urlopen")
    def test_the_body_is_exactly_the_built_payload(self, mock_urlopen: MagicMock) -> None:
        """Full equality, not `assertIn`: a renamed key is a request the API ignores."""
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=42, temperature=0.25)
        self.assertEqual(
            json.loads(self._sent_request(mock_urlopen).data.decode()),
            {
                "model": "test/model",
                "messages": self.messages,
                "max_tokens": 42,
                "temperature": 0.25,
                "provider": dict(PROVIDER_ROUTING),
            },
        )

    @patch("council.client.urllib.request.urlopen")
    def test_temperature_defaults_to_the_configured_one(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        payload = json.loads(self._sent_request(mock_urlopen).data.decode())
        self.assertEqual(payload["temperature"], TEMPERATURE)


class TestRequestIdCapture(unittest.TestCase):
    """The request id is the only handle on a call once it is over."""

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    @patch("council.client.urllib.request.urlopen")
    def test_the_openrouter_header_is_the_fallback(self, mock_urlopen: MagicMock) -> None:
        """Two spellings in the wild; the second one was never exercised."""
        mock_urlopen.return_value = _mock_response(
            _OK_BODY, headers={"openrouter-request-id": "or-999"}
        )
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.request_id, "or-999")

    @patch("council.client.urllib.request.urlopen")
    def test_the_standard_header_wins_when_both_are_present(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(
            _OK_BODY, headers={"x-request-id": "x-1", "openrouter-request-id": "or-2"}
        )
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.request_id, "x-1")

    @patch("council.client.urllib.request.urlopen")
    def test_the_request_id_travels_with_the_size_cap_error(self, mock_urlopen: MagicMock) -> None:
        """A 256KB abort with no request id cannot be traced back to a provider."""
        mock_urlopen.return_value = _mock_response(
            b"x" * (300 * 1024), headers={"x-request-id": "x-cap"}
        )
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(ctx.exception.request_id, "x-cap")


class TestGenerationId(unittest.TestCase):
    """The completion id from the body — the documented key to ask who served a call.

    OpenRouter's OpenAPI spec marks `id` **required** on `ChatResult` ("Unique completion
    identifier"), and `GET /api/v1/generation?id=…` returns `data.provider_name`, the name
    of the provider that actually answered. That lookup is the only way to investigate a
    reply that arrives well-formed and *wrong* — a mangled token, a quantised endpoint.

    The header `request_id` does not substitute for it: on the live E2E of 2026-08-14
    OpenRouter sent neither `x-request-id` nor `openrouter-request-id`, and the field was
    null on 7 telemetry records out of 7. These tests exist because the suite was green
    through all of that — it mocked headers the real API does not send.
    """

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    @patch("council.client.urllib.request.urlopen")
    def test_the_completion_id_is_captured_from_the_body(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(
            {**_OK_BODY, "id": "gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG"}
        )
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.generation_id, "gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG")

    @patch("council.client.urllib.request.urlopen")
    def test_it_does_not_come_from_the_header(self, mock_urlopen: MagicMock) -> None:
        """Two different identifiers, and only one of them is documented for the lookup."""
        mock_urlopen.return_value = _mock_response(
            {**_OK_BODY, "id": "gen-abc"}, headers={"x-request-id": "x-1"}
        )
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.generation_id, "gen-abc")
        self.assertEqual(result.request_id, "x-1")

    @patch("council.client.urllib.request.urlopen")
    def test_a_body_without_an_id_leaves_it_none_rather_than_crashing(
        self, mock_urlopen: MagicMock
    ) -> None:
        """The spec says required; a proxy in the middle is not bound by the spec."""
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.assertIsNone(
            self.client.call("test/model", self.messages, max_tokens=10).generation_id
        )

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_unnamed_empty_content_error_carries_the_lookup_key(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """That branch is the one with no diagnosis yet: it must be answerable by asking."""
        mock_urlopen.return_value = _mock_response(
            {"id": "gen-vuoto", "choices": [{"message": {"content": ""}}]}
        )
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIn("generation_id='gen-vuoto'", str(ctx.exception))


class TestRetryArithmetic(unittest.TestCase):
    """How many times, how long between, and what the counters end up saying."""

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_backoff_follows_the_configured_sequence(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Three attempts sleep twice, and the two values come from the table in order."""
        mock_urlopen.side_effect = urllib.error.URLError("down")
        with self.assertRaises(OpenRouterError):
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(
            mock_sleep.call_args_list,
            [call(RETRY_BACKOFF_SECONDS[0]), call(RETRY_BACKOFF_SECONDS[1])],
        )

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_last_attempt_does_not_sleep_before_giving_up(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A sleep after the final failure is pure latency: nobody waits on it."""
        mock_urlopen.side_effect = urllib.error.URLError("down")
        with self.assertRaises(OpenRouterError):
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(mock_urlopen.call_count, MAX_RETRIES)
        self.assertEqual(mock_sleep.call_count, MAX_RETRIES - 1)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_exhaustion_error_names_the_model_and_the_last_cause(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """The message is the only diagnostic left once every attempt is spent."""
        mock_urlopen.side_effect = urllib.error.URLError("down")
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("voter/one", self.messages, max_tokens=10)
        self.assertEqual(
            str(ctx.exception),
            f"All {MAX_RETRIES} attempts failed for model='voter/one': URLError",
        )

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_malformed_json_is_a_transient_failure(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A truncated body is a transport accident, not an API verdict: retry it."""
        mock_urlopen.side_effect = [
            _mock_response(b"{not json"),
            _mock_response(_OK_BODY),
        ]
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.attempts, 2)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_non_retryable_body_is_truncated(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """An error page is not a log file: 500 bytes of it, no more."""
        mock_urlopen.side_effect = _http_error(400, b"E" * 900)
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(str(ctx.exception).count("E"), 500)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_retryable_status_that_never_recovers_still_gives_up(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        mock_urlopen.side_effect = _http_error(429)
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(mock_urlopen.call_count, MAX_RETRIES)
        self.assertIn("HTTPError", str(ctx.exception))

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_the_exhaustion_error_names_the_status_code(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """429 and 503 are the same word and opposite decisions: change seat, or wait."""
        mock_urlopen.side_effect = _http_error(429)
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertEqual(ctx.exception.status_code, 429)

    @patch("council.client.time.sleep")
    @patch("council.client.urllib.request.urlopen")
    def test_a_transport_failure_has_no_status_to_report(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A URLError carries no code, and inventing one would be worse than none."""
        mock_urlopen.side_effect = urllib.error.URLError("down")
        with self.assertRaises(OpenRouterError) as ctx:
            self.client.call("test/model", self.messages, max_tokens=10)
        self.assertNotIn("HTTP", str(ctx.exception))
        self.assertIsNone(ctx.exception.status_code)


class TestResultMapping(unittest.TestCase):
    """What the API said, translated into the CallResult the protocol reads."""

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    @patch("council.client.urllib.request.urlopen")
    def test_the_content_is_stripped(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.assertEqual(self.client.call("test/model", self.messages, max_tokens=10).content, "ok")

    @patch("council.client.time.perf_counter")
    @patch("council.client.urllib.request.urlopen")
    def test_the_latency_is_the_elapsed_time_to_two_decimals(
        self, mock_urlopen: MagicMock, mock_perf: MagicMock
    ) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        mock_perf.side_effect = [10.0, 11.23456]
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.latency_s, 1.23)

    @patch("council.client.urllib.request.urlopen")
    def test_usage_is_read_from_the_response(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 1234, "cost": 0.075},
            }
        )
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.cost, 0.075)
        self.assertEqual(result.tokens, 1234)

    @patch("council.client.urllib.request.urlopen")
    def test_a_response_without_usage_costs_nothing_rather_than_crashing(
        self, mock_urlopen: MagicMock
    ) -> None:
        mock_urlopen.return_value = _mock_response({"choices": [{"message": {"content": "ok"}}]})
        result = self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(result.cost, 0.0)
        self.assertEqual(result.tokens, 0)

    @patch("council.client.urllib.request.urlopen")
    def test_a_first_attempt_success_reports_one_attempt(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.assertEqual(self.client.call("test/model", self.messages, max_tokens=10).attempts, 1)


class TestPayloadAllowlist(unittest.TestCase):
    """`_build_payload` is the guardrail between a caller's dict and the wire."""

    def _payload(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        return _build_payload("m", [{"role": "user", "content": "q"}], 10, 0.5, metadata)

    def test_no_metadata_means_no_trace_fields(self) -> None:
        self.assertEqual(
            set(self._payload(None)), {"model", "messages", "max_tokens", "temperature", "provider"}
        )

    def test_the_three_broadcast_fields_pass(self) -> None:
        payload = self._payload({"user": "u", "session_id": "s", "trace": {"trace_id": "t"}})
        self.assertEqual(payload["user"], "u")
        self.assertEqual(payload["session_id"], "s")
        self.assertEqual(payload["trace"], {"trace_id": "t"})

    def test_anything_else_is_dropped(self) -> None:
        """Not a merge: an unknown key never reaches the body at all."""
        payload = self._payload({"session_id": "s", "langfuse_tags": ["x"], "top_p": 0.1})
        self.assertNotIn("langfuse_tags", payload)
        self.assertNotIn("top_p", payload)

    def test_the_routing_constant_is_copied_not_shared(self) -> None:
        """A caller mutating its own payload must not rewrite the privacy posture."""
        payload = self._payload(None)
        self.assertIsNot(payload["provider"], PROVIDER_ROUTING)
        payload["provider"]["zdr"] = False
        self.assertIs(PROVIDER_ROUTING["zdr"], True)


if __name__ == "__main__":
    unittest.main()
