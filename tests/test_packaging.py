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

import council

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


class TestTheVersionIsWrittenOnce(unittest.TestCase):
    """Three files carry the version, and nothing checked that they agree.

    It is the same shape as the test count that was written in four places with four
    different numbers: a fact repeated by hand drifts, and the drift is silent because
    each copy looks right on its own. `pyproject.toml` is the manifest and therefore
    the source; `__init__.py` feeds the User-Agent that OpenRouter sees, and
    `sonar-project.properties` labels the analysis. A run reporting a version the
    package does not have is a run nobody can correlate.
    """

    def _manifest_version(self) -> str:
        with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
            return tomllib.load(fh)["project"]["version"]

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib landed in 3.11")
    def test_the_package_reports_the_manifest_version(self) -> None:
        self.assertEqual(council.__version__, self._manifest_version())

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib landed in 3.11")
    def test_sonar_analyses_the_manifest_version(self) -> None:
        righe = (REPO_ROOT / "sonar-project.properties").read_text().splitlines()
        dichiarata = next(
            r.split("=", 1)[1].strip() for r in righe if r.startswith("sonar.projectVersion")
        )
        self.assertEqual(dichiarata, self._manifest_version())
