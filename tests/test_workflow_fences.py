"""The fence that keeps model output from talking to the GitHub runner.

In GitHub Actions a log line beginning at column 0 with `::` is a **command**, not text:
`::error::`, `::add-mask::`, `::stop-commands::`, `::add-path::`. The weekly E2E prints three
answers, three rankings and a chairman synthesis — every one of them written by a language
model — into the log of a public repository, and the probe prints third-party catalogue
strings into the same place.

Those answers cannot be sanitised: they are the tool's product, they are multi-line markdown,
and mutilating them would break what the council exists to produce. So the text is not
filtered; the command channel is closed around it.

**This file exists because that half of the defence had no gate.** The sanitising half is
covered by `test_client.py::TestErrorTextCannotForgeLines` — five tests and a mutant apiece.
The fence was fifteen lines of YAML that anyone could move, reorder or delete with every
check staying green, while `SECURITY.md` went on asserting it in prose. Two independent
adversarial reviews raised the same point on 2026-08-31, and both were right: a defence
without a gate is a defence with an expiry date nobody can see.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Workflows that print model- or third-party-controlled text into a public job log.
# `ci.yml` and `mutation.yml` run no network calls and print only our own output.
FENCED_WORKFLOWS = ("e2e.yml", "probe.yml")


def _body(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


class TestUntrustedOutputIsFenced(unittest.TestCase):
    def test_every_workflow_that_prints_model_text_opens_and_closes_the_fence(self) -> None:
        for name in FENCED_WORKFLOWS:
            with self.subTest(workflow=name):
                body = _body(name)
                self.assertIn(
                    "::stop-commands::",
                    body,
                    f"{name} prints text chosen outside this repo into a public log and no "
                    "longer disables workflow commands around it.",
                )
                self.assertRegex(
                    body,
                    r'echo "::\$\{STOP_TOKEN\}::"',
                    f"{name} opens the fence without closing it: workflow commands would "
                    "stay disabled for the rest of the job.",
                )

    def test_the_token_is_generated_per_run_and_never_hardcoded(self) -> None:
        """GitHub: *"Make sure the token you're using is randomly generated and unique for
        each run."* A literal token in a public repository is a token the model can read,
        and therefore a fence the model can open from the inside.
        """
        for name in FENCED_WORKFLOWS:
            with self.subTest(workflow=name):
                body = _body(name)
                self.assertIn("openssl rand -hex", body)
                # Only the `echo` that actually opens the fence — the prose above it names
                # `::stop-commands::` too, and a test that cannot tell a comment from a
                # command is a test that fails for the wrong reason.
                emitted = re.findall(r'echo "::stop-commands::(\S*)"', body)
                self.assertEqual(emitted, ["${STOP_TOKEN}"])

    def test_an_empty_token_cannot_pass_silently(self) -> None:
        """The step runs under `set +e`, so a missing `openssl` would not stop anything: the
        fence would open as `::stop-commands::` with no token and fail open, silently, on a
        security control. `: "${STOP_TOKEN:?}"` turns that into a loud failure.
        """
        for name in FENCED_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertRegex(_body(name), r"\$\{STOP_TOKEN:\?[^}]*\}")

    def test_the_fence_closes_even_if_the_step_is_killed(self) -> None:
        """Without a trap, a step killed by `timeout-minutes` leaves commands disabled for
        every later step — including the annotations of the very run that went wrong.

        Two traps, and the second is not decoration: measured on 2026-08-31, `trap ... EXIT`
        alone does **not** fire on SIGTERM, which is the case it was added for. Turning the
        signal into an `exit` makes the EXIT trap run, exactly once, on both paths.
        """
        for name in FENCED_WORKFLOWS:
            with self.subTest(workflow=name):
                body = _body(name)
                self.assertRegex(body, r"trap .*STOP_TOKEN.* EXIT")
                self.assertRegex(body, r"trap 'exit \d+' TERM INT")


class TestTheJobsAreBounded(unittest.TestCase):
    """A workflow with no `timeout-minutes` inherits GitHub's default of six hours."""

    def test_every_job_that_calls_the_real_api_has_a_wall_clock(self) -> None:
        for name in FENCED_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertRegex(_body(name), r"(?m)^\s+timeout-minutes: \d+$")


if __name__ == "__main__":
    unittest.main()
