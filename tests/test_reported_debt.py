"""The six things two reviews found, reported, and deliberately did not fix.

They were written down under the project's own rule — *notice a problem nobody asked you to
solve: say it, do not fix it* — and then closed on 2026-09-01 when asked to. One test class
per finding, because a debt paid without a gate is a debt that comes back.

None of the six was a regression. Five predate the night of 2026-08-31; the sixth predates it
too and only became interesting once `scripts/langfuse_check.py` started *reading* the file
it lands in.
"""

from __future__ import annotations

import ast
import logging
import os
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import council.observability as obs  # noqa: E402
from council.__main__ import _stderr, load_env  # noqa: E402
from council.config import MAX_RETRIES, RETRY_BACKOFF_SECONDS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@contextmanager
def _pristine_council_logger() -> Iterator[logging.Logger]:
    """Reopens `_build_logger`'s body on the REAL logger, and puts the state back.

    The first draft did this by patching `logging.getLogger` to hand back a throwaway
    logger — and `obs.logging` **is** the stdlib module, so that replaced `getLogger`
    process-wide for the duration. It passed under `unittest` and under `pytest` here, and
    broke the suite inside mutmut's tree in CI: `test_prompt_isolation.py` found five
    handlers on the council logger where it demands one, and mutmut aborted before trying a
    single mutant — a gate that reports nothing while looking busy.

    This is the shape `tests/test_observability.py` already used. Monkeypatching the standard
    library to isolate a test isolates nothing; it moves the mess somewhere less visible.
    """
    logger = logging.getLogger("council")
    saved_handlers = logger.handlers[:]
    saved_level, saved_propagate = logger.level, logger.propagate
    logger.handlers.clear()
    try:
        yield logger
    finally:
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


class TestTheLogLevelCannotBeWeaponised(unittest.TestCase):
    """(a) `COUNCIL_LOG_LEVEL` is read from the real environment.

    Whoever controls the environment could silence the only telemetry this code produces —
    or crash it outright, because `setLevel()` raises `ValueError` on a value it does not
    know. A monitoring tool that a stray environment variable can turn off is a monitoring
    tool with a switch on the outside of the door.
    """

    # `clear=False` + `pop`, and never `clear=True`. mutmut's trampoline picks which mutant
    # to activate by reading `MUTANT_UNDER_TEST` from the environment: wiping the environment
    # wipes that too, so every mutant runs as the ORIGINAL and is reported `survived`. The
    # first draft of this file used `clear=True` and made 11 of its 14 tests invisible to the
    # gate — a test written to guard a security control, guarding it only under `unittest`.
    def _level_for(self, value: str | None) -> int:
        env = {} if value is None else {"COUNCIL_LOG_LEVEL": value}
        with patch.dict(os.environ, env, clear=False), _pristine_council_logger() as logger:
            if value is None:
                os.environ.pop("COUNCIL_LOG_LEVEL", None)
            obs._build_logger()
            return logger.level

    def test_a_junk_level_falls_back_instead_of_raising(self) -> None:
        for value in ("", "LOUD", "42x", "  ", "DEBUG; DROP TABLE", "ínfo", "DEBUG DEBUG"):
            with self.subTest(value=value):
                self.assertEqual(self._level_for(value), logging.INFO)

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        """The `.strip()` is load-bearing: without it `" DEBUG "` silently becomes INFO.

        `"DEBUG\\n"` belongs here and not in the junk list above — where the first draft put
        it. `.strip()` removes the newline, so it is a valid level, and asserting otherwise
        would have pinned the wrong behaviour.
        """
        for value in ("  DEBUG  ", "DEBUG\n", "\tdebug "):
            with self.subTest(value=value):
                self.assertEqual(self._level_for(value), logging.DEBUG)

    def test_the_only_real_choice_is_more_verbose_not_less(self) -> None:
        """`DEBUG` is honoured; `WARNING` is not, and that is the point of the allow-list.

        The first draft of this file asserted that `WARNING` should be honoured, which
        contradicted the test below it in the same class: every telemetry record is emitted
        at INFO, so honouring `WARNING` *is* the silencing attack, spelled as a preference.
        Two of my own assertions could not both hold, and the one about the attack wins.
        """
        self.assertEqual(self._level_for("debug"), logging.DEBUG)
        self.assertEqual(self._level_for("WARNING"), logging.INFO)

    def test_the_default_is_info(self) -> None:
        self.assertEqual(self._level_for(None), logging.INFO)

    # There is NO test pinning `propagate = False` here, and its absence is the point:
    # setting it broke pytest's log capture badly enough to abort the mutation gate. See
    # the note in `observability.py`.

    def test_telemetry_cannot_be_silenced_below_info(self) -> None:
        """`CRITICAL` would drop every `emit()` — the records are logged at INFO.

        Silencing is the attack: the run still succeeds, the log says nothing, and the
        Langfuse check reads an empty file and reports the council emitted nothing.
        """
        for value in ("CRITICAL", "ERROR", "WARNING"):
            with self.subTest(value=value):
                self.assertLessEqual(self._level_for(value), logging.WARNING)


class TestTheQuestionHashIsWideEnoughToCorrelate(unittest.TestCase):
    """(b) 8 hex characters is 32 bits, and its job is to tell runs apart.

    The security angle is thin — in the E2E the question is in `e2e.yml`, so the hash reveals
    nothing that is not already public — but the FUNCTIONAL angle is not: at 32 bits two
    different questions can share a hash, and correlating runs is the whole purpose.
    """

    def test_the_hash_is_at_least_64_bits(self) -> None:
        self.assertGreaterEqual(len(obs.hash_question("x")) * 4, 64)

    def test_different_questions_get_different_hashes(self) -> None:
        self.assertNotEqual(obs.hash_question("a"), obs.hash_question("b"))

    # Determinism — "the same question hashes the same way" — is pinned by
    # `test_stages.py::test_the_hash_is_a_stable_sha256_prefix`, which compares against
    # `hashlib.sha256(...)` directly.
    #
    # The first draft asserted it here as `assertEqual(f(x), f(x))`, which SonarCloud
    # flagged (python:S5863) and was right to: comparing a function against a second call
    # to itself passes even if the function returns a constant. The warning was already
    # written in this repo, in the docstring of that very test, in a file this same PR
    # edits. Removed rather than rewritten — a second copy of an existing assertion is
    # not worth the line.


class TestABomDoesNotSwallowTheKey(unittest.TestCase):
    """(c) `.env` was read as `utf-8`, and `strip()` does not remove a BOM.

    Fail-closed — exit 2, no traceback — but the message said "not set" when the truth was
    "present and silently rejected", which is the kind of diagnosis that costs an evening.
    """

    def _load(self, raw: bytes) -> str | None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_bytes(raw)
            # `clear=False` for the same reason as above: `clear=True` hides the whole class
            # from the mutation gate by deleting `MUTANT_UNDER_TEST`.
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENROUTER_API_KEY", None)
                load_env(path)
                return os.environ.get("OPENROUTER_API_KEY")

    def test_a_utf8_bom_does_not_hide_the_key(self) -> None:
        self.assertEqual(self._load(b"\xef\xbb\xbfOPENROUTER_API_KEY=sk-or-v1-x\n"), "sk-or-v1-x")

    def test_a_plain_file_still_works(self) -> None:
        self.assertEqual(self._load(b"OPENROUTER_API_KEY=sk-or-v1-x\n"), "sk-or-v1-x")


class TestTheBackoffScheduleHasNoDeadEntries(unittest.TestCase):
    """(d) `(1, 2, 4)` with `MAX_RETRIES = 3` never reaches the `4`.

    The loop sleeps only BETWEEN attempts, so three attempts spend two waits. A constant that
    documents an intention the code cannot carry out is a constant that will be read as truth
    by the next person sizing the retry budget — and it already was: three documents said the
    backoff totalled seven seconds when it totalled three.
    """

    def test_every_entry_is_reachable(self) -> None:
        self.assertEqual(len(RETRY_BACKOFF_SECONDS), MAX_RETRIES - 1)

    def test_the_schedule_actually_backs_off(self) -> None:
        """Length and order are not enough: `(0, 0)` and `(2, 2)` passed the first draft.

        Both satisfy "as long as MAX_RETRIES - 1" and "non-decreasing", and the only test
        touching the values compared them against the tuple itself. The defect this finding
        is about is measured in SECONDS, so the seconds are what has to be pinned — again
        the difference between fixing the symbol and fixing the number.

        `>= 1`: retrying in zero seconds is not a backoff, it is hammering. Strictly
        increasing: a flat schedule is a fixed delay wearing the name of an exponential one.
        """
        self.assertTrue(all(second >= 1 for second in RETRY_BACKOFF_SECONDS))
        self.assertEqual(
            list(RETRY_BACKOFF_SECONDS),
            sorted(set(RETRY_BACKOFF_SECONDS)),
            "the short backoff must strictly increase",
        )


class TestEveryStderrWriterIsFlattened(unittest.TestCase):
    """(e) Ten writers reach `council.stderr`; only `OpenRouterError` passed the filter.

    That file is now PARSED — `scripts/langfuse_check.py` reads one JSON object per line — so
    a newline in anything printed there is a way to add a record. Reproduced by the review of
    #40 through an `--env` path containing a newline: three generations that never happened.
    Not reachable from the weekly E2E, which passes no `--env`, and that is exactly why it was
    worth closing before someone made it reachable.
    """

    def test_the_helper_flattens_every_line_terminator(self) -> None:
        forged = '{"ts": 1, "trace_id": "hijack", "generation_id": "gen-fake"}'
        for terminator in ("\n", "\r", "\r\n", "\x85", " ", " ", "\v", "\f"):
            with self.subTest(terminator=repr(terminator)):
                written = _stderr_capture(f"ERRORE: percorso{terminator}{forged}")
                self.assertEqual(len(written.splitlines()), 1)

    def test_ordinary_text_survives_intact(self) -> None:
        self.assertEqual(
            _stderr_capture("INPUT ERROR: question too long"), "INPUT ERROR: question too long\n"
        )

    def test_no_raw_print_to_stderr_is_left_in_main(self) -> None:
        """The helper is only a defence if nothing routes around it.

        Parsed, not grepped. The first version of this test matched lines containing both
        `file=sys.stderr` and a leading `print(` — and **missed a writer that had been split
        across lines by the formatter**, because neither of its lines satisfied both halves.
        It counted 1, reported OK, and the surviving writer interpolated `args.env`, which
        this project's own threat model treats as attacker-influenced.

        It is the same shape as the `test ! -f requirements.txt` guard already recorded in
        `CLAUDE.md`: a check written against the surface form of the code instead of its
        meaning, green against a defect it was created to catch.
        """
        source = (REPO_ROOT / "council" / "__main__.py").read_text(encoding="utf-8")
        writers = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            printed = getattr(node.func, "id", "") == "print" and any(
                keyword.arg == "file" for keyword in node.keywords
            )
            # `sys.stderr.write(...)` reaches the same file without going through `print`,
            # and the first version of this check could not see it either.
            written = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "write"
                and ast.unparse(node.func.value) == "sys.stderr"
            )
            if printed or written:
                writers.append(node.lineno)
        self.assertEqual(
            len(writers),
            1,
            f"stderr must have exactly one writer — the one inside _stderr(). Found at {writers}",
        )


def _stderr_capture(message: str) -> str:
    import io
    from contextlib import redirect_stderr

    buffer = io.StringIO()
    with redirect_stderr(buffer):
        _stderr(message)
    return buffer.getvalue()


# Finding (f) — the workflow that claimed a branch guard nobody implemented — is pinned in
# `test_workflow_fences.py` instead of here. That file already reads `.github/`, which mutmut
# does not copy into the mutants tree, so it is already on the `--ignore` list. This one stays
# OUT of that list on purpose: its other five classes exercise `council/` and can kill real
# mutants, and ignoring the whole file to accommodate one method would have quietly traded
# mutation coverage for tidiness.


if __name__ == "__main__":
    unittest.main()
