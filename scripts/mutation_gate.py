#!/usr/bin/env python3
"""Turn mutmut's CI/CD stats into a build verdict.

`mutmut run` always exits 0, survivors or not — so on its own it is a report, not
a gate. This reads the JSON it exports and fails the build when the mutation score
drops below the declared threshold.

    python scripts/mutation_gate.py mutants/mutmut-cicd-stats.json 80

The threshold is an argument, never a constant in here: it is policy, it lives in
the workflow next to the schedule that enforces it, and it gets raised by editing
one line in one place.

stdlib-only, like the package it guards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


class GateError(Exception):
    """The build must go red. Carries the reason, already phrased for a CI log."""


def load(path: Path) -> dict[str, Any]:
    """Reads mutmut's stats file, treating its absence as a failure and not a zero.

    mutmut writes this at the end of `export-cicd-stats`. If it is missing the run
    died somewhere earlier, and an absent file must never read as a passing gate.
    """
    try:
        with path.open() as stats_file:
            return json.load(stats_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read mutation stats at {path}: {exc}") from exc


def score(stats: dict[str, Any]) -> float:
    """Percentage of tried mutants that the suite killed.

    Only `killed` counts as a kill: `suspicious`, `timeout` and `no_tests` are
    mutants the suite failed to convict, whatever the reason. `skipped` mutants
    were never tried, so they leave the denominator rather than count as losses.
    """
    tried = stats["total"] - stats["skipped"]
    return 100.0 * stats["killed"] / tried


def evaluate(stats: dict[str, Any], threshold: float) -> float:
    """Returns the score, or raises GateError with the reason the build must fail."""
    # Zero mutants is the quiet failure: point mutmut at the wrong path and 0/0 is
    # either a crash or, in a sloppier gate, a green tick. Neither is a measurement.
    if stats["total"] - stats["skipped"] <= 0:
        raise GateError("no mutants were tried — check the mutmut source paths")

    if stats["check_was_interrupted_by_user"]:
        raise GateError("the mutation run was interrupted; partial results are not results")

    actual = score(stats)
    if actual < threshold:
        raise GateError(
            f"mutation score {actual:.1f}% is below the {threshold:.1f}% floor "
            f"({stats['killed']} killed, {stats['survived']} survived, "
            f"{stats['suspicious']} suspicious, {stats['timeout']} timeout, "
            f"{stats['no_tests']} without a test)"
        )
    return actual


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    stats_path, threshold = Path(argv[0]), float(argv[1])
    try:
        actual = evaluate(load(stats_path), threshold)
    except GateError as failure:
        print(f"::error::mutation gate: {failure}")
        return 1

    print(f"mutation score {actual:.1f}% (floor {threshold:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
