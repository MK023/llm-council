"""SECURITY.md must stay true — a security document that lies is worse than none.

This project already shipped one: from May 2026 both `config.py` and SECURITY.md
described a ZDR posture that was never enforced (no `provider` block was sent, and
the account toggle was off). The claim was written once and never checked again.

These tests check the checkable part: that every test referenced as evidence exists,
and that the OWASP mapping covers the current taxonomy. They cannot verify that a
mitigation is *effective* — that is what the other suites are for — but they stop the
document from citing things that no longer exist.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SECURITY = _ROOT / "SECURITY.md"
_TESTS = _ROOT / "tests"

# The 2025 list. Renumbered from 2023: IDs are not interchangeable between versions.
_OWASP_2025 = {
    "LLM01": "Prompt Injection",
    "LLM02": "Sensitive Information Disclosure",
    "LLM03": "Supply Chain",
    "LLM04": "Data and Model Poisoning",
    "LLM05": "Improper Output Handling",
    "LLM06": "Excessive Agency",
    "LLM07": "System Prompt Leakage",
    "LLM08": "Vector and Embedding Weaknesses",
    "LLM09": "Misinformation",
    "LLM10": "Unbounded Consumption",
}


class TestSecurityDocIsHonest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _SECURITY.read_text(encoding="utf-8")
        cls.suite_source = "\n".join(
            p.read_text(encoding="utf-8") for p in sorted(_TESTS.glob("test_*.py"))
        )

    def test_every_referenced_test_class_exists(self) -> None:
        """`file.py::ClassName` must resolve to a real class."""
        for ref in set(re.findall(r"tests/(test_\w+\.py)::(\w+)", self.text)):
            filename, classname = ref
            path = _TESTS / filename
            with self.subTest(ref=f"{filename}::{classname}"):
                self.assertTrue(path.exists(), f"{filename} referenced but missing")
                self.assertIn(
                    f"class {classname}",
                    path.read_text(encoding="utf-8"),
                    f"{classname} referenced in SECURITY.md but not defined in {filename}",
                )

    def test_every_referenced_test_function_exists(self) -> None:
        """A bare `test_something` cited as evidence must be a real test."""
        cited = {
            name
            for name in re.findall(r"`(test_[a-z0-9_]+)`", self.text)
            if not name.endswith(".py")
        }
        for name in cited:
            with self.subTest(test=name):
                self.assertIn(
                    f"def {name}",
                    self.suite_source,
                    f"SECURITY.md cites {name}() as evidence, but no such test exists",
                )

    def test_every_referenced_file_exists(self) -> None:
        for rel in set(re.findall(r"`((?:tests|council)/[\w./]+\.py)`", self.text)):
            with self.subTest(path=rel):
                self.assertTrue((_ROOT / rel).exists(), f"{rel} referenced but missing")

    def test_owasp_mapping_is_complete(self) -> None:
        """All ten 2025 categories must be addressed — including the ones we skip."""
        for code in _OWASP_2025:
            with self.subTest(owasp=code):
                self.assertIn(f"**{code}**", self.text, f"{code} missing from the mapping")

    def test_owasp_titles_match_the_2025_taxonomy(self) -> None:
        """Guards against silently keeping a 2023 title under a 2025 number.

        The trap this catches: 2023's LLM05 was Supply Chain, 2025's LLM05 is Improper
        Output Handling. Same ID, different risk.
        """
        for code, title in _OWASP_2025.items():
            row = next((ln for ln in self.text.splitlines() if f"**{code}**" in ln), "")
            with self.subTest(owasp=code):
                self.assertIn(title, row, f"{code} should be titled '{title}' in the 2025 list")

    def test_every_category_has_an_explicit_verdict(self) -> None:
        """No category may be listed without saying whether it applies."""
        verdicts = ("Mitigated", "Out of scope", "Not applicable")
        for code in _OWASP_2025:
            row = next((ln for ln in self.text.splitlines() if f"**{code}**" in ln), "")
            with self.subTest(owasp=code):
                self.assertTrue(
                    any(v in row for v in verdicts),
                    f"{code} has no explicit verdict ({' / '.join(verdicts)})",
                )


if __name__ == "__main__":
    unittest.main()
