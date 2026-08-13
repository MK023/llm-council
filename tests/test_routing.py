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

from council.client import OpenRouterClient, OpenRouterError
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
    def test_trace_fields_do_not_displace_provider(self, mock_urlopen: MagicMock) -> None:
        """Broadcast trace fields and routing must coexist — neither overwrites the other."""
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call(
            "test/model",
            self.messages,
            max_tokens=10,
            metadata={"session_id": "abc", "user": "marco", "trace": {"trace_id": "abc"}},
        )
        payload = self._sent_payload(mock_urlopen)
        self.assertIn("provider", payload)
        self.assertEqual(payload["session_id"], "abc")
        self.assertEqual(payload["user"], "marco")

    @patch("council.client.urllib.request.urlopen")
    def test_caller_cannot_override_provider_through_trace_fields(
        self, mock_urlopen: MagicMock
    ) -> None:
        """The allowlist is a guardrail: `provider` carries the privacy guarantee."""
        mock_urlopen.return_value = _mock_response(_OK_BODY)
        self.client.call(
            "test/model",
            self.messages,
            max_tokens=10,
            metadata={"provider": {"zdr": False}, "model": "evil/model", "session_id": "ok"},
        )
        payload = self._sent_payload(mock_urlopen)
        self.assertIs(payload["provider"]["zdr"], True)
        self.assertEqual(payload["model"], "test/model")
        self.assertEqual(payload["session_id"], "ok")


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


class TestReasoningModelDiagnosis(unittest.TestCase):
    """An empty answer must say WHY it is empty.

    A reasoning model that runs out of budget returns HTTP 200 with empty content,
    a populated `reasoning` field and finish_reason='length'. The old error message
    said "no string 'content'" — the symptom, not the cause — and that ambiguity cost
    this project two model swaps chasing an imaginary Italian-language weakness.
    """

    def setUp(self) -> None:
        self.client = OpenRouterClient("sk-or-v1-test-key")
        self.messages = [{"role": "user", "content": "test"}]

    def _call_expecting_error(self, body: dict[str, Any]) -> str:
        with patch("council.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response(body)
            with self.assertRaises(OpenRouterError) as ctx:
                self.client.call("test/model", self.messages, max_tokens=10)
        return str(ctx.exception)

    def test_exhausted_reasoning_is_named_not_guessed(self) -> None:
        msg = self._call_expecting_error(
            {
                "choices": [
                    {
                        "message": {"content": "", "reasoning": "Let me think about this..."},
                        "finish_reason": "length",
                    }
                ]
            }
        )
        self.assertIn("Reasoning model exhausted max_tokens", msg)
        self.assertIn("max_tokens", msg)

    def test_empty_string_content_is_not_a_valid_answer(self) -> None:
        """A blank voter is not a voter: whitespace must fail like a missing field."""
        msg = self._call_expecting_error(
            {"choices": [{"message": {"content": "   \n  "}, "finish_reason": "stop"}]}
        )
        self.assertIn("no usable", msg)

    def test_refusal_still_takes_precedence(self) -> None:
        msg = self._call_expecting_error(
            {
                "choices": [
                    {
                        "message": {"content": None, "refusal": "I cannot help with that"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        self.assertIn("Model refused", msg)

    def test_non_object_response_is_rejected(self) -> None:
        """A JSON array where an object is expected is a malformed response, not an answer."""
        with patch("council.client.urllib.request.urlopen") as mock_urlopen:
            resp = MagicMock()
            resp.read.return_value = b'["not", "an", "object"]'
            resp.headers = {}
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = resp
            with self.assertRaises(OpenRouterError) as ctx:
                self.client.call("test/model", self.messages, max_tokens=10)
        self.assertIn("not a JSON object", str(ctx.exception))

    def test_empty_without_reasoning_is_not_blamed_on_reasoning(self) -> None:
        """Do not diagnose what is not there — the wrong cause is worse than none."""
        msg = self._call_expecting_error(
            {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}
        )
        self.assertNotIn("Reasoning model", msg)
        self.assertIn("no usable", msg)

    def test_unnamed_empty_content_carries_its_evidence(self) -> None:
        """The sentinel that fails without evidence costs a week: it fires once a week."""
        msg = self._call_expecting_error(
            {
                "choices": [
                    {
                        "message": {"content": []},
                        "finish_reason": "stop",
                        "native_finish_reason": "content_filter",
                    }
                ]
            }
        )
        self.assertIn("finish_reason='stop'", msg)
        self.assertIn("native_finish_reason='content_filter'", msg)
        self.assertIn("content_type=list", msg)
        self.assertIn("reasoning=absent", msg)


class TestNoBudgetBurnerInTheCouncil(unittest.TestCase):
    """Guard for the rule written in config.py: no seat may burn its budget thinking.

    The catalogue DOES mark reasoning models — `GET /api/v1/models` carries a `reasoning`
    object, "Omitted for non-reasoning models". This test cannot read it: the unit suite
    never touches the network, by design. So the list stays a deny-list, and it is a
    record of what was measured rather than a prediction.

    It is not only about the model, either. `kimi-k2-0905` has reasoning OMITTED in the
    catalogue and still burned 715-800 tokens producing nothing, because NOVITA served it
    that way — visible only as `native_tokens_reasoning` on `GET /api/v1/generation`.
    A model property and a provider behaviour are different things, and a seat is lost
    to either. Hence "budget burner", not "reasoning model".

    Until 2026-08-14 only the CHAIRMAN was checked here, and meanwhile all three voters
    had drifted onto reasoning models — including one whose reasoning is `mandatory`.
    A rule enforced on one of four seats is a rule enforced nowhere.
    """

    KNOWN_BUDGET_BURNERS = frozenset(
        {
            # Reasoning by design, measured empty on the real stage-1 prompt.
            "qwen/qwen3.6-35b-a3b",
            "qwen/qwen3.5-9b",
            "qwen/qwen3.5-27b",
            "qwen/qwen3.5-122b-a10b",
            "qwen/qwen3-235b-a22b-thinking-2507",
            "deepseek/deepseek-r1-0528",
            "moonshotai/kimi-k3",
            "deepseek/deepseek-v4-pro",
            # Held seats until 2026-08-14. The catalogue lists reasoning on all three;
            # `gemini-3.5-flash-lite` has it `mandatory: true`, so it cannot be turned off.
            "deepseek/deepseek-v4-flash",
            "google/gemini-3.5-flash-lite",
            "openai/gpt-5.6-luna",
            # Not a reasoning model. Novita makes it behave like one.
            "moonshotai/kimi-k2-0905",
        }
    )

    def test_the_chairman_is_not_a_known_budget_burner(self) -> None:
        self.assertNotIn(
            CHAIRMAN_MODEL,
            self.KNOWN_BUDGET_BURNERS,
            "a reasoning chairman that runs out of budget loses the entire run",
        )

    def test_no_voter_is_a_known_budget_burner(self) -> None:
        """The seat that was never checked. All three had drifted."""
        for voter in VOTER_MODELS:
            self.assertNotIn(
                voter,
                self.KNOWN_BUDGET_BURNERS,
                f"{voter} was measured spending its budget without answering",
            )


if __name__ == "__main__":
    unittest.main()
