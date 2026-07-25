"""Unit tests for privacy-preserving provider routing and jury composition.

Two invariants are enforced here, both previously expressed only as comments:

1. Every request must carry the ZDR routing constraint, fail-closed. Without it
   OpenRouter picks any endpoint that serves the model, so a model chosen for
   its zero-retention guarantee can be answered by a provider that retains.
2. The jury must span four distinct model houses, with the chairman outside the
   voter pool (no self-favor bias in synthesis).
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from council.client import OpenRouterClient
from council.config import (
    CHAIRMAN_MODEL,
    PROVIDER_ROUTING,
    VOTER_MODELS,
)

_OK_BODY: dict[str, Any] = {
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"total_tokens": 10, "cost": 0.0001},
}


def _mock_response(body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.headers = {}
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _house(model_id: str) -> str:
    """The organisation that trained the weights — the part before the slash."""
    return model_id.split("/", 1)[0]


class TestProviderRoutingInPayload(unittest.TestCase):
    """The ZDR constraint must reach the wire, not just the config module."""

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    def _sent_payload(self, mock_urlopen: MagicMock) -> dict[str, Any]:
        request = mock_urlopen.call_args[0][0]
        return json.loads(request.data.decode())

    @patch("council.client.urllib.request.urlopen")
    def test_payload_carries_provider_block(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIn("provider", self._sent_payload(mock_urlopen))

    @patch("council.client.urllib.request.urlopen")
    def test_payload_requires_zero_data_retention(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIs(self._sent_payload(mock_urlopen)["provider"]["zdr"], True)

    @patch("council.client.urllib.request.urlopen")
    def test_payload_denies_data_collection(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        self.assertEqual(self._sent_payload(mock_urlopen)["provider"]["data_collection"], "deny")

    @patch("council.client.urllib.request.urlopen")
    def test_payload_is_fail_closed_on_fallbacks(self, mock_urlopen: MagicMock) -> None:
        """No silent downgrade: fail rather than answer from a retaining endpoint."""
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIs(self._sent_payload(mock_urlopen)["provider"]["allow_fallbacks"], False)

    @patch("council.client.urllib.request.urlopen")
    def test_metadata_does_not_displace_provider(self, mock_urlopen: MagicMock) -> None:
        """Langfuse metadata and routing must coexist — one must not overwrite the other."""
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call(
            "test/model",
            self.messages,
            max_tokens=10,
            metadata={"langfuse_session_id": "abc"},
        )
        payload = self._sent_payload(mock_urlopen)
        self.assertIn("provider", payload)
        self.assertEqual(payload["metadata"]["langfuse_session_id"], "abc")


class TestRoutingConstant(unittest.TestCase):
    """PROVIDER_ROUTING is the single source of truth for the privacy posture."""

    def test_zdr_is_required(self) -> None:
        self.assertIs(PROVIDER_ROUTING["zdr"], True)

    def test_data_collection_denied(self) -> None:
        self.assertEqual(PROVIDER_ROUTING["data_collection"], "deny")

    def test_fallbacks_disabled(self) -> None:
        self.assertIs(PROVIDER_ROUTING["allow_fallbacks"], False)


class TestJuryComposition(unittest.TestCase):
    """Cross-vendor divergence is the whole point: it must be enforced, not commented."""

    def test_three_voters(self) -> None:
        self.assertEqual(len(VOTER_MODELS), 3)

    def test_odd_number_of_voters(self) -> None:
        """Even juries cannot produce a majority in peer ranking."""
        self.assertEqual(len(VOTER_MODELS) % 2, 1)

    def test_voters_come_from_distinct_houses(self) -> None:
        houses = [_house(m) for m in VOTER_MODELS]
        self.assertEqual(len(set(houses)), len(houses), f"duplicate house in {houses}")

    def test_chairman_is_outside_the_voter_pool(self) -> None:
        self.assertNotIn(CHAIRMAN_MODEL, VOTER_MODELS)

    def test_chairman_house_differs_from_every_voter(self) -> None:
        """Synthesising your own answer invites self-favor bias."""
        self.assertNotIn(_house(CHAIRMAN_MODEL), {_house(m) for m in VOTER_MODELS})

    def test_anthropic_is_excluded_everywhere(self) -> None:
        """Whoever orchestrates the council does not sit in it."""
        for model in (*VOTER_MODELS, CHAIRMAN_MODEL):
            self.assertNotEqual(_house(model), "anthropic")


if __name__ == "__main__":
    unittest.main()
