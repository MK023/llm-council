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
from council.config import (
    CHAIRMAN_MODEL,
    MAX_TOKENS_STAGE_1,
    MAX_TOKENS_STAGE_2,
    MAX_TOKENS_STAGE_3,
    VOTER_MODELS,
)
from council.observability import TraceContext, emit, hash_question
from council.stages import (
    _FAILED_RESULT,
    RankingResult,
    StageResult,
    _build_metadata,
    _collect_failures,
    stage1_responses,
    stage2_rankings,
    stage3_synthesis,
)


def _result(content: str) -> CallResult:
    return CallResult(
        content=content, cost=0.001, tokens=100, latency_s=1.0, attempts=1, request_id="req-1"
    )


def _stage_result(content: str) -> StageResult:
    return StageResult(model="voter/one", result=_result(content))


def _ranking_result(content: str) -> RankingResult:
    return RankingResult(
        voter="voter/one", result=_result(content), rank=("A", "B", "C"), reason="", is_valid=True
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


class TestWhatEachStageActuallyAsks(unittest.TestCase):
    """The arguments the stages hand the client — budgets, messages, span names.

    Everything below ran under the existing suite without a single assertion on the
    value produced: 81 mutants survived across the three stages on 2026-08-13. A stage
    that sends the stage-2 budget on stage 1, or labels every span `stage_1`, is a
    working council with unusable telemetry and truncated answers.
    """

    def test_stage1_sends_the_question_as_the_user_turn(self) -> None:
        client = _client(*[_result("a")] * 3)
        stage1_responses(client, "domanda")
        self.assertEqual(
            client.call.call_args_list[0].args[1], [{"role": "user", "content": "domanda"}]
        )

    def test_stage1_uses_its_own_token_budget(self) -> None:
        client = _client(*[_result("a")] * 3)
        stage1_responses(client, "domanda")
        self.assertEqual(client.call.call_args_list[0].args[2], MAX_TOKENS_STAGE_1)

    def test_stage2_uses_its_own_token_budget(self) -> None:
        """Stage 2 returns three letters and a sentence; a stage-1 budget just costs more."""
        client = _client(*[_result("RANK: A,B,C")] * 3)
        stage2_rankings(client, "domanda", [])
        self.assertEqual(client.call.call_args_list[0].args[2], MAX_TOKENS_STAGE_2)

    def test_every_voter_ranks_the_same_prompt(self) -> None:
        """Blind ranking only means anything if the three read an identical brief."""
        client = _client(*[_result("RANK: A,B,C")] * 3)
        stage2_rankings(client, "domanda", [_stage_result("alpha")])
        prompts = {c.args[1][0]["content"] for c in client.call.call_args_list}
        self.assertEqual(len(prompts), 1)

    def test_stage2_ranks_the_stage1_answers_not_the_question_alone(self) -> None:
        client = _client(*[_result("RANK: A,B,C")] * 3)
        stage2_rankings(client, "domanda", [_stage_result("risposta-alfa")])
        self.assertIn("risposta-alfa", client.call.call_args_list[0].args[1][0]["content"])

    def test_stage3_reads_both_the_answers_and_the_rankings(self) -> None:
        """Until 2026-07-26 the rankings reached the chairman raw; both must be there."""
        client = _client(_result("finale"))
        stage3_synthesis(
            client,
            "domanda",
            [_stage_result("risposta-alfa")],
            [_ranking_result("RANK: A,B,C\nREASON: perché sì")],
        )
        prompt = client.call.call_args.args[1][0]["content"]
        self.assertIn("risposta-alfa", prompt)
        self.assertIn("perché sì", prompt)

    def test_each_stage_labels_its_own_span(self) -> None:
        """One span name for all three stages is telemetry that cannot be read."""
        spans = []
        for run in (
            lambda c: stage1_responses(c, "d", session_id="s"),
            lambda c: stage2_rankings(c, "d", [], session_id="s"),
            lambda c: stage3_synthesis(c, "d", [], [], session_id="s"),
        ):
            client = _client(*[_result("RANK: A,B,C")] * 3)
            run(client)
            spans.append(client.call.call_args_list[0].kwargs["metadata"]["trace"]["span_name"])
        self.assertEqual(spans, ["stage_1", "stage_2", "stage_3_chairman"])

    def test_without_a_session_no_trace_fields_are_sent(self) -> None:
        client = _client(*[_result("a")] * 3)
        stage1_responses(client, "domanda")
        self.assertIsNone(client.call.call_args_list[0].kwargs["metadata"])

    def test_the_trace_id_keeps_the_full_session_while_session_id_is_capped(self) -> None:
        """The cap protects the Broadcast field; the trace id is ours and stays whole."""
        meta = _build_metadata("s" * 300, stage="stage_1")
        assert meta is not None
        self.assertEqual(len(meta["trace"]["trace_id"]), 300)
        self.assertEqual(meta["trace"]["trace_name"], "llm-council")

    def test_a_failed_voter_leaves_an_empty_seat_not_a_fake_answer(self) -> None:
        """The sentinel is what the report and the token ceiling both count on."""
        client = _client(*[OpenRouterError("down")] * 3)
        failed = stage1_responses(client, "domanda")[0].result
        self.assertEqual(failed.content, "[VOTER_FAILED]")
        self.assertEqual(failed.cost, 0.0)
        self.assertEqual(failed.tokens, 0)
        self.assertEqual(failed.attempts, 0)
        self.assertIsNone(failed.request_id)

    def test_the_reason_is_stripped_of_its_surrounding_whitespace(self) -> None:
        client = _client(*[_result("RANK: A,B,C\nREASON:   spaziata   ")] * 3)
        self.assertEqual(stage2_rankings(client, "domanda", [])[0].reason, "spaziata")

    def test_an_unparseable_ranking_says_which_check_rejected_it(self) -> None:
        client = _client(*[_result("nessun rank qui")] * 3)
        self.assertEqual(
            stage2_rankings(client, "domanda", [])[0].error,
            "regex_no_match (Stage 2 output did not match RANK regex)",
        )

    def test_a_failed_ranking_carries_the_api_error_and_no_rank(self) -> None:
        client = _client(*[OpenRouterError("429 exhausted")] * 3)
        ranking = stage2_rankings(client, "domanda", [])[0]
        self.assertIsNone(ranking.rank)
        self.assertEqual(ranking.reason, "")
        # `assertIs(..., False)` and not `assertFalse`: the latter accepts None, and
        # `is_valid=None` is exactly the mutation that survived here. A flag read as a
        # boolean must be pinned to the boolean, or the assertion is half a check.
        self.assertIs(ranking.is_valid, False)
        self.assertEqual(ranking.error, "429 exhausted")
        self.assertIs(ranking.result, _FAILED_RESULT)

    def test_stage2_sends_the_prompt_as_a_user_turn(self) -> None:
        """Full equality on the turn, as stage 1 already had it.

        Reading only `messages[0]["content"]` leaves the role free to be anything:
        the key and the value `"user"` were both unasserted, and a chat completion
        with a mangled role is a request the API answers differently or not at all.
        """
        client = _client(*[_result("RANK: A,B,C")] * 3)
        stage2_rankings(client, "domanda", [_stage_result("alfa")])
        sent = client.call.call_args_list[0].args[1]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["role"], "user")
        self.assertEqual(set(sent[0]), {"role", "content"})

    def test_stage3_sends_the_prompt_as_a_user_turn(self) -> None:
        client = _client(_result("finale"))
        stage3_synthesis(client, "domanda", [_stage_result("alfa")], [])
        sent = client.call.call_args.args[1]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["role"], "user")
        self.assertEqual(set(sent[0]), {"role", "content"})

    def test_an_unparseable_ranking_leaves_no_half_filled_result(self) -> None:
        """The no-match branch has its own `rank`/`reason`/`is_valid`, unasserted.

        Only `.error` was checked here, so the other three fields of that branch
        could hold anything — a rank invented out of a failed parse is exactly the
        thing this branch exists to prevent.
        """
        parsed = _result("nessun rank qui")
        client = _client(*[parsed] * 3)
        ranking = stage2_rankings(client, "domanda", [])[0]
        self.assertIsNone(ranking.rank)
        self.assertEqual(ranking.reason, "")
        self.assertIs(ranking.is_valid, False)
        # The unparsed answer is kept: it is what the report shows when a voter goes off-format.
        self.assertIs(ranking.result, parsed)
        # And it keeps its name, as the other two branches already did.
        self.assertEqual(ranking.voter, VOTER_MODELS[0])

    def test_a_failed_voter_keeps_its_own_name_on_the_result(self) -> None:
        """Attribution must survive the failure, or the error summary blames the wrong model."""
        client = _client(_result("a"), OpenRouterError("down"), _result("c"))
        results = stage1_responses(client, "domanda")
        self.assertEqual([r.model for r in results], list(VOTER_MODELS))
        self.assertEqual(results[1].model, VOTER_MODELS[1])

    def test_a_failed_ranking_keeps_its_own_voter(self) -> None:
        client = _client(_result("RANK: A,B,C"), OpenRouterError("down"), _result("RANK: A,B,C"))
        rankings = stage2_rankings(client, "domanda", [])
        self.assertEqual([r.voter for r in rankings], list(VOTER_MODELS))
        self.assertEqual(rankings[1].voter, VOTER_MODELS[1])

    def test_stage2_asks_every_voter_by_name(self) -> None:
        """As stage 1 already did. Without it the model argument could be anything."""
        client = _client(*[_result("RANK: A,B,C")] * 3)
        stage2_rankings(client, "domanda", [])
        asked = [c.args[0] for c in client.call.call_args_list]
        self.assertEqual(asked, list(VOTER_MODELS))

    def test_a_valid_ranking_carries_the_answer_it_parsed(self) -> None:
        """The rank is derived; the result is the evidence it was derived from."""
        parsed = _result("RANK: B,A,C\nREASON: perché sì.")
        client = _client(*[parsed] * 3)
        self.assertIs(stage2_rankings(client, "domanda", [])[0].result, parsed)

    def test_stage1_carries_the_answer_the_client_returned(self) -> None:
        answered = _result("la risposta")
        client = _client(answered, answered, answered)
        self.assertIs(stage1_responses(client, "domanda")[0].result, answered)

    def test_the_question_reaches_the_ranking_prompt(self) -> None:
        """Ranking answers without the question is ranking on prose, not on the answer.

        `stage2_prompt(None, responses)` survived: every test read the *responses* out
        of the prompt and none read the question, so the voters could have been asked
        to judge three texts with nothing to judge them against.
        """
        client = _client(*[_result("RANK: A,B,C")] * 3)
        stage2_rankings(client, "domanda-unica", [_stage_result("alfa")])
        self.assertIn("domanda-unica", client.call.call_args_list[0].args[1][0]["content"])

    def test_the_question_reaches_the_synthesis_prompt(self) -> None:
        """Same hole on the chairman, where the answer the user reads is written."""
        client = _client(_result("finale"))
        stage3_synthesis(client, "domanda-unica", [_stage_result("alfa")], [])
        self.assertIn("domanda-unica", client.call.call_args.args[1][0]["content"])


if __name__ == "__main__":
    unittest.main()


class TestTheLabelBlamesTheRightVoter(unittest.TestCase):
    """`_collect_failures` decides WHO gets named in the ERROR SUMMARY.

    The labels are positional — `chr(65 + i)` — and until 2026-08-14 nothing asserted
    it: mutating the arithmetic to `chr(65 - i)` or `chr(66 + i)` left the suite green.
    A summary that blames the wrong voter is worse than no summary, because it sends
    the next person to re-measure a model that was working. Same family as the voter
    attribution already pinned above, one layer up.

    These moved here from `__main__.py`, which is outside the mutation gate. They are
    decisions, not printing, so they belong where mutants are tried.
    """

    def _stage1(self, *errors: str | None) -> list[StageResult]:
        errors = errors or (None,) * len(VOTER_MODELS)
        return [
            StageResult(model=m, result=_result("x"), error=e)
            for m, e in zip(VOTER_MODELS, errors, strict=True)
        ]

    def _rankings(self, *errors: str | None) -> list[RankingResult]:
        errors = errors or (None,) * len(VOTER_MODELS)
        return [
            RankingResult(
                voter=m,
                result=_result("x"),
                rank=None if e else ("A", "B", "C"),
                reason="",
                is_valid=not e,
                error=e,
            )
            for m, e in zip(VOTER_MODELS, errors, strict=True)
        ]

    def test_the_second_voter_is_labelled_B(self) -> None:
        failed, _, _, _ = _collect_failures(self._stage1(None, "caduto", None), self._rankings())
        self.assertEqual([label for label, _, _ in failed], ["B"])

    def test_the_third_voter_is_labelled_C(self) -> None:
        failed, _, _, _ = _collect_failures(self._stage1(None, None, "caduto"), self._rankings())
        self.assertEqual([label for label, _, _ in failed], ["C"])

    def test_the_labels_run_A_B_C_in_order(self) -> None:
        failed, _, _, _ = _collect_failures(self._stage1("g", "g", "g"), self._rankings())
        self.assertEqual([label for label, _, _ in failed], ["A", "B", "C"])
        self.assertEqual([model for _, model, _ in failed], list(VOTER_MODELS))

    def test_a_stage2_failure_keeps_its_position_and_its_message(self) -> None:
        _, s2_failed, _, _ = _collect_failures(
            self._stage1(None, None, None), self._rankings(None, "429 exhausted", None)
        )
        self.assertEqual([(label, err) for label, _, err in s2_failed], [("B", "429 exhausted")])

    def test_a_truncated_voter_keeps_its_position(self) -> None:
        s1 = [
            StageResult(
                model=m,
                result=CallResult(
                    content="tagliata",
                    cost=0.0,
                    tokens=1,
                    latency_s=0.0,
                    attempts=1,
                    finish_reason="length" if i == 2 else "stop",
                ),
            )
            for i, m in enumerate(VOTER_MODELS)
        ]
        _, _, _, truncated = _collect_failures(s1, self._rankings())
        self.assertEqual(truncated, [("C", VOTER_MODELS[2])])

    def test_malformed_and_failed_are_split_on_the_regex_marker(self) -> None:
        """The discriminator is the literal `regex_no_match`: it decides which list."""
        _, s2_failed, s2_malformed, _ = _collect_failures(
            self._stage1(None, None, None),
            self._rankings("regex_no_match (Stage 2 output did not match RANK regex)", "500", None),
        )
        self.assertEqual([label for label, _ in s2_malformed], ["A"])
        self.assertEqual([label for label, _, _ in s2_failed], ["B"])
