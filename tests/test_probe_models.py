"""The probe is a diagnostic, but its verdict is logic and logic gets a check.

The number that matters here is the count of candidates that came back truncated or
refused: it is what decides whether a model is defensible for a seat. Getting that
count wrong would hand a seat to a model that cannot hold it — the mistake this whole
script exists to stop repeating.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from council.client import CallResult, OpenRouterError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_models import probe  # noqa: E402


def _answered(generation_id: str = "gen-1") -> CallResult:
    return CallResult(
        content="una risposta",
        cost=0.001,
        tokens=100,
        latency_s=1.0,
        attempts=1,
        generation_id=generation_id,
    )


def _run(
    call_results: list[object],
    stats: list[dict[str, object]],
    models: tuple[str, ...] = ("a/uno", "b/due"),
) -> str:
    client = MagicMock()
    client.call.side_effect = call_results
    out = io.StringIO()
    with (
        patch("scripts.probe_models.OpenRouterClient", return_value=client),
        patch("scripts.probe_models.generation_stats", side_effect=stats),
        redirect_stdout(out),
    ):
        probe(models, "sk-or-v1-test")
    return out.getvalue()


class TestTheVerdict(unittest.TestCase):
    def test_a_finished_answer_is_not_counted_as_truncated(self) -> None:
        output = _run(
            [_answered(), _answered()],
            [{"finish_reason": "stop"}, {"finish_reason": "stop"}],
        )
        self.assertIn("candidati troncati o falliti: 0/2", output)

    def test_length_is_truncated_even_though_the_call_succeeded(self) -> None:
        """The whole point: a cut answer arrives as a valid CallResult."""
        output = _run(
            [_answered(), _answered()],
            [{"finish_reason": "length"}, {"finish_reason": "stop"}],
        )
        self.assertIn("candidati troncati o falliti: 1/2", output)

    def test_a_refusal_counts_against_the_candidate(self) -> None:
        output = _run(
            [OpenRouterError("model refused"), _answered()],
            [{"finish_reason": "stop"}],
        )
        self.assertIn("candidati troncati o falliti: 1/2", output)
        self.assertIn("RIFIUTATO/FALLITO", output)

    def test_the_provider_and_the_reasoning_spend_are_reported(self) -> None:
        """`native_tokens_reasoning` is the field the response body does not show."""
        output = _run(
            [_answered()],
            [
                {
                    "finish_reason": "stop",
                    "provider_name": "Novita",
                    "native_tokens_completion": 700,
                    "native_tokens_reasoning": 800,
                    "total_cost": 0.002,
                }
            ],
            models=("solo/uno",),
        )
        self.assertIn("Novita", output)
        self.assertIn("800", output)


if __name__ == "__main__":
    unittest.main()
