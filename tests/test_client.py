"""Unit tests for OpenRouter client error handling (network transport mocked)."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

from council.client import OpenRouterClient, OpenRouterError


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
