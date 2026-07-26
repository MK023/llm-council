"""Unit tests for the three-stage protocol (network mocked).

The client had 93% coverage and the protocol 40%: the easy-to-mock layer was
tested, the layer that makes this project what it is was not. These cover the
behaviours that matter — graceful degradation, blind-ranking parsing, chairman
selection — plus one privacy invariant: telemetry must never carry content.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch

from council.client import CallResult, OpenRouterError
from council.config import CHAIRMAN_MODEL, MAX_TOKENS_STAGE_3, VOTER_MODELS
from council.observability import TraceContext, emit, hash_question
from council.stages import (
    _build_metadata,
    stage1_responses,
    stage2_rankings,
    stage3_synthesis,
)


def _result(content: str) -> CallResult:
    return CallResult(
        content=content, cost=0.001, tokens=100, latency_s=1.0, attempts=1, request_id="req-1"
    )


def _client(*side_effect: object) -> MagicMock:
    client = MagicMock()
    client.call.side_effect = list(side_effect)
    return client


class TestStage1(unittest.TestCase):
    def test_every_voter_is_asked(self) -> None:
        """Assert on what was ASKED, not on the labels we attached afterwards.

        The old version only checked `[r.model for r in results]`, and those labels
        come from the loop variable — not from the client. Calling one voter three
        times left it green. Verified by mutation 2026-07-26.
        """
        client = _client(*[_result("a"), _result("b"), _result("c")])
        stage1_responses(client, "domanda")
        asked = [call.args[0] for call in client.call.call_args_list]
        self.assertEqual(asked, list(VOTER_MODELS))
        self.assertEqual(client.call.call_count, len(VOTER_MODELS))

    def test_one_failing_voter_does_not_abort_the_council(self) -> None:
        """Graceful degradation: 2/3 voters is a weaker council, not a dead one."""
        client = _client(_result("a"), OpenRouterError("model refused"), _result("c"))
        results = stage1_responses(client, "domanda")
        self.assertEqual(len(results), 3)
        self.assertIsNone(results[0].error)
        self.assertIn("refused", results[1].error or "")
        self.assertEqual(results[1].result.content, "[VOTER_FAILED]")
        self.assertIsNone(results[2].error)

    def test_all_voters_failing_still_returns_three_slots(self) -> None:
        client = _client(*[OpenRouterError("down")] * 3)
        results = stage1_responses(client, "domanda")
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.error for r in results))


class TestStage2Parsing(unittest.TestCase):
    def test_well_formed_ranking_is_parsed(self) -> None:
        client = _client(*[_result("RANK: B,A,C\nREASON: B is the most accurate one.")] * 3)
        rankings = stage2_rankings(client, "domanda", [])
        self.assertTrue(all(r.is_valid for r in rankings))
        self.assertEqual(rankings[0].rank, ("B", "A", "C"))
        self.assertEqual(rankings[0].reason, "B is the most accurate one.")

    def test_missing_reason_is_still_valid(self) -> None:
        """Gemini was observed emitting an empty REASON: the rank tuple is the signal."""
        client = _client(*[_result("RANK: A,B,C")] * 3)
        rankings = stage2_rankings(client, "domanda", [])
        self.assertTrue(all(r.is_valid for r in rankings))
        self.assertEqual(rankings[0].rank, ("A", "B", "C"))
        self.assertEqual(rankings[0].reason, "")

    def test_lowercase_ranking_is_normalised(self) -> None:
        client = _client(*[_result("rank: c,b,a\nreason: whatever it says here")] * 3)
        rankings = stage2_rankings(client, "domanda", [])
        self.assertEqual(rankings[0].rank, ("C", "B", "A"))

    def test_unparseable_output_is_flagged_not_guessed(self) -> None:
        """A malformed ranking must be marked invalid, never silently invented."""
        client = _client(*[_result("I think the second one was best, honestly.")] * 3)
        rankings = stage2_rankings(client, "domanda", [])
        self.assertFalse(any(r.is_valid for r in rankings))
        self.assertIsNone(rankings[0].rank)
        self.assertIn("regex_no_match", rankings[0].error or "")

    def test_failing_voter_is_recorded_as_invalid(self) -> None:
        client = _client(
            _result("RANK: A,B,C"), OpenRouterError("429 exhausted"), _result("RANK: A,B,C")
        )
        rankings = stage2_rankings(client, "domanda", [])
        self.assertTrue(rankings[0].is_valid)
        self.assertFalse(rankings[1].is_valid)
        self.assertIn("429", rankings[1].error or "")


class TestStage3(unittest.TestCase):
    def test_synthesis_uses_the_chairman_not_a_voter(self) -> None:
        client = _client(_result("final answer"))
        stage3_synthesis(client, "domanda", [], [])
        model_used = client.call.call_args[0][0]
        self.assertEqual(model_used, CHAIRMAN_MODEL)
        self.assertNotIn(model_used, VOTER_MODELS)

    def test_synthesis_uses_the_stage3_token_budget(self) -> None:
        client = _client(_result("final answer"))
        stage3_synthesis(client, "domanda", [], [])
        self.assertEqual(client.call.call_args[0][2], MAX_TOKENS_STAGE_3)


class TestTelemetryPrivacy(unittest.TestCase):
    """Langfuse metadata must carry identifiers, never content.

    The observability layer is allowed to know that a run happened and how much it
    cost. It is not allowed to know what was asked — this tool is used for career
    and personal decisions.
    """

    SECRET = "dovrei accettare l offerta di lavoro a Milano"

    def test_no_session_id_means_no_metadata(self) -> None:
        self.assertIsNone(_build_metadata(None, stage="stage_1"))

    def test_metadata_carries_only_identifiers(self) -> None:
        """The three OpenRouter Broadcast fields, and nothing else."""
        meta = _build_metadata("sess-123", stage="stage_1")
        assert meta is not None
        self.assertEqual(set(meta), {"user", "session_id", "trace"})
        self.assertEqual(meta["session_id"], "sess-123")
        self.assertEqual(meta["trace"]["span_name"], "stage_1")

    def test_session_id_is_capped_at_the_broadcast_limit(self) -> None:
        """OpenRouter caps user/session_id at 128 chars; Langfuse drops over 200."""
        meta = _build_metadata("x" * 300, stage="stage_1")
        assert meta is not None
        self.assertEqual(len(meta["session_id"]), 128)
        # `user` non si asserisce: e' una costante di 16 caratteri, il cap sarebbe
        # una difesa per un caso impossibile e l'assertion non potrebbe mai fallire.

    def test_question_never_reaches_the_metadata(self) -> None:
        client = _client(*[_result("a")] * 3)
        stage1_responses(client, self.SECRET, session_id="sess-123")
        for call in client.call.call_args_list:
            metadata = call.kwargs.get("metadata") or {}
            self.assertNotIn(self.SECRET, str(metadata))

    def test_answers_never_reach_the_metadata(self) -> None:
        answer = "accetta, il pacchetto e superiore alla media"
        client = _client(*[_result(answer)] * 3)
        stage2_rankings(client, self.SECRET, [], session_id="sess-123")
        for call in client.call.call_args_list:
            metadata = call.kwargs.get("metadata") or {}
            self.assertNotIn(answer, str(metadata))
            self.assertNotIn(self.SECRET, str(metadata))


class TestTraceRecordCarriesHashNotContent(unittest.TestCase):
    """`hash_question` promises "correlation without leaking question content".

    It computed the hash and nobody emitted it: the promise existed, the mechanism
    did not. These tests pin both halves — the hash IS in the record, the question
    is NOT — so the field cannot quietly go missing again.
    """

    QUESTION = "dovrei accettare l offerta di lavoro a Milano"

    def _emitted(self, trace: TraceContext) -> str:
        with patch("council.observability._LOGGER") as logger:
            emit("query_start", trace)
            return logger.info.call_args[0][0]

    def test_the_hash_reaches_the_log_record(self) -> None:
        trace = TraceContext(question_hash=hash_question(self.QUESTION))
        record = json.loads(self._emitted(trace))
        self.assertEqual(record["question_hash"], hash_question(self.QUESTION))

    def test_the_question_itself_never_reaches_the_log_record(self) -> None:
        trace = TraceContext(question_hash=hash_question(self.QUESTION))
        self.assertNotIn(self.QUESTION, self._emitted(trace))

    def test_the_hash_is_a_stable_sha256_prefix(self) -> None:
        """Correlation across runs is the point: a random id would not do.

        Pinned to the algorithm, not to itself: comparing the function against a second
        call to the same function is tautological — it would pass even if the function
        returned a constant.
        """
        expected = hashlib.sha256(self.QUESTION.encode()).hexdigest()[:8]
        actual = hash_question(self.QUESTION)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 8)

    def test_different_questions_hash_differently(self) -> None:
        first = hash_question("una domanda")
        second = hash_question("un altra domanda")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
