"""Unit tests for CLI orchestration: exit contracts, config errors, degraded runs.

`__main__.py` sat at 21% because it is the awkward layer — argv, stdout, env,
process exit codes. But it is also where a caller learns whether the run worked:
the exit code IS the contract. A council that quietly returns 0 after losing two
voters is worse than one that fails loudly.

Network is never touched: the three stages are patched at the boundary.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from council.__main__ import _ALLOWED_ENV_KEYS, load_env, main, parse_args
from council.client import CallResult, OpenRouterError
from council.config import MAX_QUESTION_LENGTH, MAX_TOTAL_TOKENS_PER_RUN, VOTER_MODELS
from council.stages import RankingResult, StageResult

_KEY = "sk-or-v1-test-key-not-real"


def setUpModule() -> None:
    """Silence structured telemetry during tests.

    `emit` binds its StreamHandler to sys.stderr at import time, so redirect_stderr
    (which swaps the stream later) cannot capture it — the JSON would land on the
    real terminal and drown the test output.
    """
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


def _ok(content: str = "risposta", tokens: int = 100) -> CallResult:
    return CallResult(
        content=content, cost=0.001, tokens=tokens, latency_s=1.0, attempts=1, request_id="req"
    )


def _stage1(errors: tuple[str | None, ...] = (None, None, None), tokens: int = 100):
    return [
        StageResult(
            model=m,
            result=_ok(tokens=tokens) if e is None else _ok("[VOTER_FAILED]", 0),
            error=e,
        )
        for m, e in zip(VOTER_MODELS, errors, strict=True)
    ]


def _stage2(valid: tuple[bool, ...] = (True, True, True)):
    return [
        RankingResult(
            voter=m,
            result=_ok("RANK: A,B,C"),
            rank=("A", "B", "C") if v else None,
            reason="perche' si" if v else "",
            is_valid=v,
            error=None if v else "regex_no_match (Stage 2 output did not match RANK regex)",
        )
        for m, v in zip(VOTER_MODELS, valid, strict=True)
    ]


def _run(argv: list[str], **patches) -> tuple[int, str, str]:
    """Runs main() with the three stages patched, capturing stdout/stderr."""
    defaults = {
        "stage1_responses": _stage1(),
        "stage2_rankings": _stage2(),
        "stage3_synthesis": _ok("sintesi finale"),
    }
    defaults.update(patches)
    out, err = io.StringIO(), io.StringIO()
    with (
        patch("council.__main__.stage1_responses", **_as_mock(defaults["stage1_responses"])),
        patch("council.__main__.stage2_rankings", **_as_mock(defaults["stage2_rankings"])),
        patch("council.__main__.stage3_synthesis", **_as_mock(defaults["stage3_synthesis"])),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _as_mock(value: object) -> dict[str, object]:
    """Exceptions become side_effect, everything else return_value."""
    if isinstance(value, Exception):
        return {"side_effect": value}
    return {"return_value": value}


class TestInputContract(unittest.TestCase):
    """Bad input must exit 2 and say so on stderr — never start a paid run."""

    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {"OPENROUTER_API_KEY": _KEY}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_empty_question_exits_2(self) -> None:
        code, _, err = _run(["   "])
        self.assertEqual(code, 2)
        self.assertIn("INPUT ERROR", err)

    def test_oversized_question_exits_2(self) -> None:
        code, _, err = _run(["x" * (MAX_QUESTION_LENGTH + 1)])
        self.assertEqual(code, 2)
        self.assertIn("INPUT ERROR", err)

    def test_question_at_the_cap_is_accepted(self) -> None:
        code, _, _ = _run(["x" * MAX_QUESTION_LENGTH])
        self.assertEqual(code, 0)


class TestConfigContract(unittest.TestCase):
    """A missing or malformed key must fail before any network call, with exit 2."""

    def test_missing_api_key_exits_2(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            code, _, err = _run(["domanda"])
        self.assertEqual(code, 2)
        self.assertIn("OPENROUTER_API_KEY not set", err)

    def test_malformed_api_key_exits_2(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-wrong-prefix"}, clear=True):
            code, _, err = _run(["domanda"])
        self.assertEqual(code, 2)
        self.assertIn("API KEY ERROR", err)

    def test_key_is_never_echoed_to_output(self) -> None:
        """A key printed in a log or a screenshot is a leaked key."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": _KEY}, clear=True):
            _, out, err = _run(["domanda"])
        self.assertNotIn(_KEY, out)
        self.assertNotIn(_KEY, err)


class TestExitContract(unittest.TestCase):
    """The exit code is the contract: full success, degraded, stage failure, abort."""

    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {"OPENROUTER_API_KEY": _KEY}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_clean_run_exits_0(self) -> None:
        code, out, _ = _run(["domanda"])
        self.assertEqual(code, 0)
        self.assertIn("sintesi finale", out)

    def test_failed_voter_degrades_to_exit_3_not_0(self) -> None:
        """Two voters out of three is a usable answer — but the caller must know."""
        code, out, _ = _run(["domanda"], stage1_responses=_stage1((None, "model refused", None)))
        self.assertEqual(code, 3)
        self.assertIn("sintesi finale", out)  # la risposta c'e' comunque

    def test_malformed_ranking_also_degrades_to_3(self) -> None:
        code, _, _ = _run(["domanda"], stage2_rankings=_stage2((True, False, True)))
        self.assertEqual(code, 3)

    def test_stage1_total_failure_exits_1(self) -> None:
        code, _, err = _run(["domanda"], stage1_responses=OpenRouterError("all voters down"))
        self.assertEqual(code, 1)
        self.assertIn("STAGE 1 FAILED", err)

    def test_stage3_failure_exits_1(self) -> None:
        code, _, err = _run(["domanda"], stage3_synthesis=OpenRouterError("chairman down"))
        self.assertEqual(code, 1)
        self.assertIn("STAGE 3", err)

    def test_token_ceiling_aborts_with_exit_4(self) -> None:
        """Runaway protection: abort beats burning the spend cap."""
        huge = MAX_TOTAL_TOKENS_PER_RUN  # x3 voters blows past the ceiling
        code, _, err = _run(["domanda"], stage1_responses=_stage1(tokens=huge))
        self.assertEqual(code, 4)
        self.assertIn("ABORT", err)

    def _stage1_totalling(self, total: int) -> list[StageResult]:
        """Tre votanti la cui somma di token e' ESATTAMENTE `total`."""
        each, remainder = divmod(total, len(VOTER_MODELS))
        counts = [each] * len(VOTER_MODELS)
        counts[-1] += remainder
        return [
            StageResult(model=m, result=_ok(tokens=n))
            for m, n in zip(VOTER_MODELS, counts, strict=True)
        ]

    def _rankings_costing_nothing(self) -> list[RankingResult]:
        return [
            RankingResult(
                voter=m,
                result=_ok("RANK: A,B,C", tokens=0),
                rank=("A", "B", "C"),
                reason="perche' si",
                is_valid=True,
                error=None,
            )
            for m in VOTER_MODELS
        ]

    def test_the_ceiling_itself_is_not_over_the_ceiling(self) -> None:
        """Il tetto e' un massimo consentito, non il primo valore vietato.

        Il test qui sopra spende tre volte il tetto: passa identico che il confronto sia
        `>` o `>=`, e infatti mutare l'uno nell'altro il 2026-09-04 lasciava la suite
        verde. Il valore ESATTO e' l'unico punto in cui i due si comportano in modo
        diverso, quindi e' l'unico che li distingue. Un tetto che aborta quando viene
        raggiunto butta via una run completa per un token che era nel budget.
        """
        code, _, _ = _run(
            ["domanda"],
            stage1_responses=self._stage1_totalling(MAX_TOTAL_TOKENS_PER_RUN),
            # Il tetto e' controllato DUE volte e la seconda somma anche Stage 2: con i
            # 300 token di default il totale sarebbe 50.300 e l'abort direbbe il vero.
            # A zero, il numero sotto esame e' esattamente quello di Stage 1.
            stage2_rankings=self._rankings_costing_nothing(),
        )
        # `assertNotEqual(code, 4)` sarebbe verde anche per 1 o 3: proverebbe il punto sul
        # confronto e lascerebbe passare una run degradata o fallita. Qui non c'e' nulla di
        # troncato e nessun guasto, quindi il contratto e' lo zero pieno.
        self.assertEqual(code, 0)

    def test_one_token_over_the_ceiling_aborts(self) -> None:
        """L'altra meta' della pinza: a +1 deve abortire."""
        code, _, err = _run(
            ["domanda"],
            stage1_responses=self._stage1_totalling(MAX_TOTAL_TOKENS_PER_RUN + 1),
            stage2_rankings=self._rankings_costing_nothing(),
        )
        self.assertEqual(code, 4)
        self.assertIn("ABORT", err)


class TestTheReportCarriesTheLookupKey(unittest.TestCase):
    """A degraded answer looks like a good one, so the id must ride on the good line.

    A mangled token or a truncated thought passes `_validate_response` and reaches the
    report as an ordinary reply. If the completion id only appeared in error messages,
    the single case that needs `GET /api/v1/generation?id=…` would be the one case
    without it — and the provider that served it would stay unknowable.
    """

    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": _KEY}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_the_generation_id_is_printed_next_to_a_good_answer(self) -> None:
        answered = CallResult(
            content="risposta",
            cost=0.001,
            tokens=100,
            latency_s=1.0,
            attempts=1,
            generation_id="gen-abc123",
        )
        stage1 = [StageResult(model=m, result=answered) for m in VOTER_MODELS]
        code, out, _ = _run(["domanda"], stage1_responses=stage1)
        self.assertEqual(code, 0)
        self.assertIn("gen=gen-abc123", out)

    def test_nothing_is_printed_when_the_id_is_absent(self) -> None:
        """No empty `gen=` on a line that already carries four numbers."""
        _, out, _ = _run(["domanda"])
        self.assertNotIn("gen=", out)


class TestTruncationIsNotSuccess(unittest.TestCase):
    """`finish_reason='length'` must be visible AND must colour the exit code.

    A cut answer is present, valid and incomplete: it passes every shape check, and on
    2026-08-14 two voters shipped one while the run reported [OK] and exited 0. Stage 2
    ranked half-answers, the chairman synthesised them, and nothing went red for months.

    The exit code is the contract the weekly E2E reads, so truncation belongs in it —
    otherwise a model that quietly gets more verbose degrades the council in silence.
    """

    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": _KEY}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def _cut(self, content: str = "una risposta tagliata a", **kwargs: object) -> CallResult:
        return CallResult(
            content=content,
            cost=0.001,
            tokens=100,
            latency_s=1.0,
            attempts=1,
            finish_reason="length",
            **kwargs,  # type: ignore[arg-type]
        )

    def _stage1_with_one_cut(self) -> list[StageResult]:
        return [
            StageResult(model=m, result=self._cut() if i == 0 else _ok())
            for i, m in enumerate(VOTER_MODELS)
        ]

    def test_a_truncated_voter_degrades_the_run_to_exit_3(self) -> None:
        code, _, _ = _run(["domanda"], stage1_responses=self._stage1_with_one_cut())
        self.assertEqual(code, 3)

    def test_the_truncated_voter_is_labelled_not_ok(self) -> None:
        _, out, _ = _run(["domanda"], stage1_responses=self._stage1_with_one_cut())
        self.assertIn("[TRUNCATED]", out)

    def test_the_summary_says_what_to_change(self) -> None:
        """A red run with no next step costs a week, because that is the cadence."""
        _, out, _ = _run(["domanda"], stage1_responses=self._stage1_with_one_cut())
        self.assertIn("Stage 1 truncated (1)", out)
        self.assertIn("MAX_TOKENS_STAGE_1", out)

    def test_the_counter_appears_in_the_total_line(self) -> None:
        _, out, _ = _run(["domanda"], stage1_responses=self._stage1_with_one_cut())
        self.assertIn("s1_truncated=1/3", out)

    def test_a_truncated_chairman_also_degrades_the_run(self) -> None:
        """The final answer stopping mid-thought is the worst version of this."""
        code, out, _ = _run(["domanda"], stage3_synthesis=self._cut("sintesi tagliata a"))
        self.assertEqual(code, 3)
        self.assertIn("Chairman truncated", out)

    def test_a_run_that_stops_on_its_own_is_still_a_clean_zero(self) -> None:
        """`stop` is the normal case and must stay silent, or the label means nothing."""
        finished = [
            StageResult(
                model=m,
                result=CallResult(
                    content="risposta",
                    cost=0.001,
                    tokens=100,
                    latency_s=1.0,
                    attempts=1,
                    finish_reason="stop",
                ),
            )
            for m in VOTER_MODELS
        ]
        code, out, _ = _run(["domanda"], stage1_responses=finished)
        self.assertEqual(code, 0)
        self.assertNotIn("[TRUNCATED]", out)
        self.assertIn("s1_truncated=0/3", out)


class TestTheReportShowsTheRightHalfOfEachVoter(unittest.TestCase):
    """Un votante caduto mostra il suo errore; uno riuscito mostra la sua risposta.

    Il report e' l'unica cosa che un umano legge di una run, e la scelta fra le due meta'
    e' un `if` per stage. Nessuno dei due era asserito: invertirli entrambi il 2026-09-04
    lasciava la suite verde, con la coverage di `__main__.py` al 100% di righe E branch —
    i due rami venivano eseguiti, e nessuno guardava cosa stampavano.

    Le conseguenze non sono cosmetiche. Un votante caduto che stampa `[VOTER_FAILED]`
    invece dell'errore nasconde il motivo per cui e' caduto, che e' l'unica informazione
    che quella riga esiste per dare; e un votante riuscito che stampa `ERROR: None` al
    posto della risposta butta via il lavoro pagato.
    """

    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": _KEY}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_stage1_shows_the_error_of_the_voter_that_fell(self) -> None:
        _, out, _ = _run(
            ["domanda"], stage1_responses=_stage1(errors=(None, "429 exhausted", None))
        )
        self.assertIn("ERROR: 429 exhausted", out)
        # `assertIn("429 exhausted")` da solo passava anche con il ramo invertito: il
        # messaggio ricompare nell'ERROR SUMMARY in fondo alla run, quindi l'asserzione
        # era verde per una ragione che non c'entrava con la riga sotto esame. Cio' che
        # distingue i due rami e' il segnaposto: se stampa quello, non ha stampato l'errore.
        self.assertNotIn("[VOTER_FAILED]", out)

    def test_stage1_shows_the_answer_of_the_voter_that_worked(self) -> None:
        client_said = "la risposta che il modello ha davvero prodotto"
        s1 = [StageResult(model=m, result=_ok(client_said)) for m in VOTER_MODELS]
        _, out, _ = _run(["domanda"], stage1_responses=s1)
        self.assertIn(client_said, out)
        self.assertNotIn("ERROR:", out)

    def test_stage2_shows_the_error_of_the_ranking_that_failed(self) -> None:
        s2 = [
            RankingResult(
                voter=m,
                result=_ok("[RANK_FAILED]"),
                rank=None,
                reason="",
                is_valid=False,
                error="503 service unavailable",
            )
            for m in VOTER_MODELS
        ]
        _, out, _ = _run(["domanda"], stage2_rankings=s2)
        self.assertIn("ERROR: 503 service unavailable", out)
        self.assertNotIn("[RANK_FAILED]", out)

    def test_stage2_shows_the_output_of_the_ranking_that_could_not_be_parsed(self) -> None:
        """MALFORMED non e' FAILED: il modello ha risposto, e cio' che ha scritto e'
        l'unica traccia del perche' il rank non si sia fatto leggere. Stamparlo come se
        fosse un errore la butterebbe via."""
        said = "ho classificato le risposte in ordine di qualita"
        s2 = [
            RankingResult(
                voter=m,
                result=_ok(said),
                rank=None,
                reason="",
                is_valid=False,
                error="regex_no_match (Stage 2 output did not match RANK regex)",
            )
            for m in VOTER_MODELS
        ]
        _, out, _ = _run(["domanda"], stage2_rankings=s2)
        self.assertIn(said, out)
        self.assertIn("[MALFORMED]", out)


class TestArgParsing(unittest.TestCase):
    def test_question_is_positional(self) -> None:
        self.assertEqual(parse_args(["la mia domanda"]).question, "la mia domanda")

    def test_env_path_defaults_to_cwd(self) -> None:
        self.assertEqual(parse_args(["q"]).env, Path.cwd() / ".env")

    def test_env_path_is_overridable(self) -> None:
        self.assertEqual(parse_args(["q", "--env", "/tmp/x.env"]).env, Path("/tmp/x.env"))


class TestEnvLoading(unittest.TestCase):
    def test_missing_file_is_not_an_error(self) -> None:
        load_env(Path("/nonexistent/.env"))  # must not raise

    def test_values_are_read_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("OPENROUTER_API_KEY=abc123\n# commento\n\nMALFORMED_LINE\n")
            with patch.dict(os.environ, {}, clear=True):
                load_env(p)
                self.assertEqual(os.environ["OPENROUTER_API_KEY"], "abc123")
                self.assertNotIn("MALFORMED_LINE", os.environ)

    def test_environment_wins_over_file(self) -> None:
        """Doppler injects the key as an env var: the file must never override it."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("OPENROUTER_API_KEY=from-file\n")
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "from-env"}, clear=True):
                load_env(p)
                self.assertEqual(os.environ["OPENROUTER_API_KEY"], "from-env")

    def test_value_containing_equals_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("OPENROUTER_API_KEY=a=b=c\n")
            with patch.dict(os.environ, {}, clear=True):
                load_env(p)
                self.assertEqual(os.environ["OPENROUTER_API_KEY"], "a=b=c")


class TestEnvPathIsBounded(unittest.TestCase):
    """`--env` is attacker-influenced: this tool is invoked by a Claude Code skill,
    so its arguments are assembled by a model. Reading an arbitrary KEY=VALUE file
    into os.environ is an environment injection, not just a file read."""

    def test_only_allowlisted_keys_are_imported(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("OPENROUTER_API_KEY=sk-or-v1-x\nPATH=/evil\nLD_PRELOAD=/evil.so\n")
            with patch.dict(os.environ, {}, clear=True):
                load_env(p)
                self.assertEqual(os.environ["OPENROUTER_API_KEY"], "sk-or-v1-x")
                self.assertNotIn("LD_PRELOAD", os.environ)
                self.assertNotEqual(os.environ.get("PATH"), "/evil")

    def test_langfuse_keys_are_refused(self) -> None:
        """Nothing in this process reads them, so the allow-list must not carry them.

        They were admitted for a Langfuse integration that never existed in this codebase:
        ingestion goes through OpenRouter Broadcast, an account setting. `LANGFUSE_HOST` was
        read by no line at all. An allow-list is a statement about what the program uses —
        three names it does not use made it describe a program that was never written.
        """
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(
                "LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_HOST=https://x\n"
            )
            with patch.dict(os.environ, {}, clear=True):
                load_env(p)
                for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
                    self.assertNotIn(key, os.environ)

    def test_the_allow_list_carries_only_what_the_program_reads(self) -> None:
        """One key, and the assertion names it: a set that grows silently is not a limit."""
        self.assertEqual(_ALLOWED_ENV_KEYS, frozenset({"OPENROUTER_API_KEY"}))

    def test_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            # Only load_env sits inside assertRaises: if TemporaryDirectory itself
            # raised, the test would pass for the wrong reason.
            target = Path(d)
            with self.assertRaises(ValueError):
                load_env(target)

    def test_oversized_file_is_refused(self) -> None:
        """A .env is small. /dev/zero-shaped input must not be read into memory."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "big.env"
            p.write_text("A=" + "x" * (64 * 1024 + 10))
            with self.assertRaises(ValueError):
                load_env(p)

    def test_symlink_to_a_device_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "link.env"
            try:
                link.symlink_to("/dev/null")
            except OSError:
                self.skipTest("symlink non supportati")
            with self.assertRaises(ValueError):
                load_env(link)

    def test_tilde_is_expanded(self) -> None:
        load_env(Path("~/definitely-not-here-9f3a.env"))  # must not raise

    def test_bad_env_path_exits_2_without_traceback(self) -> None:
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(os.environ, {"OPENROUTER_API_KEY": _KEY}, clear=False),
        ):
            code, _, err = _run(["domanda", "--env", d])
        self.assertEqual(code, 2)
        self.assertIn("ENV ERROR", err)


class TestStage2Contracts(unittest.TestCase):
    """Stage 2 has its own failure paths, separate from stage 1 — and its own token check."""

    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {"OPENROUTER_API_KEY": _KEY}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_stage2_total_failure_exits_1(self) -> None:
        code, _, err = _run(["domanda"], stage2_rankings=OpenRouterError("ranking layer down"))
        self.assertEqual(code, 1)
        self.assertIn("STAGE 2 FAILED", err)

    def test_token_ceiling_after_stage2_also_aborts(self) -> None:
        """The ceiling is checked twice: stage 1 alone can pass and stage 2 push it over."""
        half = MAX_TOTAL_TOKENS_PER_RUN // 3
        code, _, err = _run(
            ["domanda"],
            stage1_responses=_stage1(tokens=half),
            stage2_rankings=[
                RankingResult(
                    voter=m,
                    result=_ok("RANK: A,B,C", tokens=MAX_TOTAL_TOKENS_PER_RUN),
                    rank=("A", "B", "C"),
                    reason="r",
                    is_valid=True,
                    error=None,
                )
                for m in VOTER_MODELS
            ],
        )
        self.assertEqual(code, 4)
        self.assertIn("ABORT", err)

    def test_api_failed_voter_is_listed_in_the_error_summary(self) -> None:
        """A voter that failed at the API level must appear in the calibration hints."""
        broken = [
            RankingResult(
                voter=VOTER_MODELS[0],
                result=_ok("", 0),
                rank=None,
                reason="",
                is_valid=False,
                error="429 exhausted",
            ),
            *_stage2()[1:],
        ]
        code, out, _ = _run(["domanda"], stage2_rankings=broken)
        self.assertEqual(code, 3)
        self.assertIn("Stage 2 API failures", out)
        self.assertIn("429 exhausted", out)

    def test_failed_stage1_voter_is_printed_with_its_error(self) -> None:
        code, out, _ = _run(
            ["domanda"], stage1_responses=_stage1((None, "refused by policy", None))
        )
        self.assertIn("[FAILED]", out)
        self.assertIn("refused by policy", out)


if __name__ == "__main__":
    unittest.main()
