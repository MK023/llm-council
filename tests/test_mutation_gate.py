"""The gate that reads the mutation score, and the ways it must refuse to pass.

Coverage says which lines ran; the mutation score says whether the assertions on
them are worth anything. This gate is what turns that number into a red build —
so the interesting tests here are not "does it compute a ratio", they are the
three ways a mutation gate silently stops gating: an empty run, an interrupted
run, and a rounding error at the boundary.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.mutation_gate import GateError, evaluate, score  # noqa: E402


def stats(**overrides: int | bool) -> dict[str, int | bool]:
    """A mutmut stats payload with every field mutmut writes, overridable per test."""
    base: dict[str, int | bool] = {
        "killed": 0,
        "survived": 0,
        "total": 0,
        "no_tests": 0,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 0,
        "check_was_interrupted_by_user": False,
        "segfault": 0,
    }
    base.update(overrides)
    return base


class TestScore(unittest.TestCase):
    def test_killed_over_the_mutants_that_actually_ran(self) -> None:
        self.assertEqual(score(stats(killed=90, survived=10, total=100)), 90.0)

    def test_skipped_mutants_leave_the_denominator(self) -> None:
        """A skipped mutant was never tried, so counting it as a failure would be a lie."""
        self.assertEqual(score(stats(killed=45, survived=5, skipped=50, total=100)), 90.0)

    def test_survivors_by_any_name_still_count_against_the_score(self) -> None:
        """suspicious/timeout/no_tests are not kills — only `killed` is a kill."""
        self.assertEqual(
            score(stats(killed=70, survived=10, suspicious=10, timeout=5, no_tests=5, total=100)),
            70.0,
        )


class TestGateRefusals(unittest.TestCase):
    def test_a_run_with_no_mutants_never_passes(self) -> None:
        """The failure mode this gate exists to prevent.

        Point mutmut at the wrong path and it produces zero mutants. A ratio-only
        gate divides by zero or, worse, treats 0/0 as perfect and reports green —
        a gate that cannot fail, guarding a suite whose whole point is catching
        tests that cannot fail.
        """
        with self.assertRaises(GateError) as ctx:
            evaluate(stats(total=0), threshold=80.0)
        self.assertIn("no mutants", str(ctx.exception))

    def test_an_interrupted_run_never_passes(self) -> None:
        """Partial results are not results: the mutants not yet tried are unknown."""
        with self.assertRaises(GateError) as ctx:
            evaluate(stats(killed=100, total=100, check_was_interrupted_by_user=True), 80.0)
        self.assertIn("interrupted", str(ctx.exception))

    def test_below_threshold_fails(self) -> None:
        with self.assertRaises(GateError) as ctx:
            evaluate(stats(killed=79, survived=21, total=100), threshold=80.0)
        self.assertIn("79.0", str(ctx.exception))

    def test_exactly_on_the_threshold_passes(self) -> None:
        """`>=`, not `>`: a threshold you cannot actually reach is a moving goalpost."""
        self.assertEqual(evaluate(stats(killed=80, survived=20, total=100), threshold=80.0), 80.0)

    def test_above_threshold_passes(self) -> None:
        self.assertEqual(evaluate(stats(killed=95, survived=5, total=100), threshold=80.0), 95.0)


class TestReadingTheFile(unittest.TestCase):
    def test_missing_stats_file_fails_the_gate(self) -> None:
        """If mutmut died before writing, the build must not read that as success."""
        from scripts.mutation_gate import load

        with TemporaryDirectory() as tmp, self.assertRaises(GateError):
            load(Path(tmp) / "nope.json")

    def test_reads_the_payload_mutmut_writes(self) -> None:
        from scripts.mutation_gate import load

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mutmut-cicd-stats.json"
            path.write_text(json.dumps(stats(killed=3, survived=1, total=4)))
            self.assertEqual(load(path)["killed"], 3)
