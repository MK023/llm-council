"""The stdlib-only promise, checked against the manifest that actually declares it.

Until 2026-08-13 this invariant was defended in CI by `test ! -f requirements.txt`
— a file this project would never create, since its manifest is pyproject.toml.
Adding `dependencies = ["requests"]` passed every gate. The public case study on
marcobellingeri.dev says "zero runtime dependencies, no supply chain attack
surface"; that sentence now has something behind it.

Runtime only. Dev tools (ruff, coverage, zizmor) are a separate, hash-pinned
supply chain — see requirements-dev.txt.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(sys.version_info >= (3, 11), "tomllib landed in 3.11")
class TestNoRuntimeDependencies(unittest.TestCase):
    """`python -m council` must run on a bare interpreter, with nothing installed."""

    def setUp(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as manifest:
            self.project = tomllib.load(manifest)["project"]

    def test_no_declared_dependencies(self) -> None:
        declared = self.project.get("dependencies", [])
        self.assertEqual(
            declared,
            [],
            f"pyproject declares runtime dependencies {declared}. The README, SECURITY.md "
            "and the public case study all claim there are none — change the claims first.",
        )

    def test_no_optional_dependency_groups(self) -> None:
        """An `[project.optional-dependencies]` extra is still a runtime import path.

        `pip install llm-council[something]` would pull it, and 'zero dependencies'
        would quietly become 'zero dependencies unless you ask nicely'.
        """
        extras = self.project.get("optional-dependencies", {})
        self.assertEqual(
            extras,
            {},
            f"pyproject declares optional dependency groups {sorted(extras)}.",
        )

    def test_no_bare_requirements_txt(self) -> None:
        """The old CI guard, kept but narrowed to what it actually means.

        A bare `requirements.txt` is what pip, Dependabot, Snyk and every human
        reader treat as "the runtime dependencies of this project". Its absence
        stays part of the contract even though it was never the real manifest.

        Suffixed files (`requirements-dev.txt`, `requirements-mutation.txt`) are
        tooling and are allowed: they are installed by CI jobs, never by anyone
        running the council.
        """
        self.assertFalse(
            (REPO_ROOT / "requirements.txt").exists(),
            "requirements.txt exists — runtime dependencies belong in pyproject, "
            "and this project has none. Name tooling files requirements-<what>.txt.",
        )
