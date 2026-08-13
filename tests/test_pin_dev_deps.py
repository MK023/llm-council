"""Which PyPI artifacts the hash pinner is willing to install.

Written after the filter shipped broken on 2026-08-13: it matched on the suffix
`linux_x86_64.whl`, and real manylinux wheels end in `manylinux_2_28_x86_64.whl`
or `manylinux2014_x86_64.whl`. Zero Linux hashes made it into the lockfile, so
`pip install --require-hashes` on the CI runner would have refused every wheel it
downloaded. The generator was silent; only the runner would have said anything.

These are real filenames, copied from the PyPI JSON API for the pinned versions.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pin_dev_deps import wanted  # noqa: E402


class TestWheelsWeMustPin(unittest.TestCase):
    """CI runs ubuntu x86_64; Marco's laptop is macOS. Both have to install."""

    def test_manylinux_2_28(self) -> None:
        self.assertTrue(wanted("zizmor-1.25.0-py3-none-manylinux_2_28_x86_64.whl"))

    def test_manylinux2014_compound_tag(self) -> None:
        self.assertTrue(
            wanted("ruff-0.15.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl")
        )

    def test_manylinux_compound_with_three_tags(self) -> None:
        self.assertTrue(
            wanted(
                "coverage-7.13.4-cp312-cp312-manylinux1_x86_64."
                "manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl"
            )
        )

    def test_pure_python_wheel(self) -> None:
        self.assertTrue(wanted("coverage-7.13.4-py3-none-any.whl"))

    def test_macos_arm64(self) -> None:
        self.assertTrue(wanted("ruff-0.15.9-py3-none-macosx_11_0_arm64.whl"))

    def test_macos_intel(self) -> None:
        self.assertTrue(wanted("ruff-0.15.9-py3-none-macosx_10_12_x86_64.whl"))


class TestArtifactsWeRefuse(unittest.TestCase):
    def test_source_distribution(self) -> None:
        """No sdist: pip would fall back to building ruff and zizmor from Rust.

        Refusing it makes pip fail loudly on an unexpected platform instead of
        starting a toolchain build nobody asked for.
        """
        self.assertFalse(wanted("ruff-0.15.9.tar.gz"))

    def test_musllinux(self) -> None:
        """Alpine is not a platform this project runs on; an unused hash is noise."""
        self.assertFalse(wanted("coverage-7.13.4-cp312-cp312-musllinux_1_2_x86_64.whl"))

    def test_windows(self) -> None:
        self.assertFalse(wanted("coverage-7.13.4-cp312-cp312-win_amd64.whl"))

    def test_linux_on_another_architecture(self) -> None:
        self.assertFalse(wanted("coverage-7.13.4-cp312-cp312-manylinux_2_28_aarch64.whl"))
