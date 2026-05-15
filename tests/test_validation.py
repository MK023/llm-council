"""Unit tests for input validation, env loading, API key format, and Stage 2 regex parsing."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

from council.__main__ import load_env, validate_question
from council.client import OpenRouterClient
from council.config import MAX_QUESTION_LENGTH, RANK_REGEX


class TestQuestionValidation(unittest.TestCase):
    def test_valid_question_passes(self) -> None:
        self.assertEqual(
            validate_question("What is the best K8s distro?"),
            "What is the best K8s distro?",
        )

    def test_empty_question_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_question("")

    def test_whitespace_only_question_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_question("   \n\t  ")

    def test_oversized_question_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_question("x" * (MAX_QUESTION_LENGTH + 1))

    def test_boundary_size_passes(self) -> None:
        boundary = "x" * MAX_QUESTION_LENGTH
        self.assertEqual(validate_question(boundary), boundary)


class TestApiKeyValidation(unittest.TestCase):
    def test_empty_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            OpenRouterClient("")

    def test_wrong_prefix_raises(self) -> None:
        with self.assertRaises(ValueError):
            OpenRouterClient("not-a-key")

    def test_openai_prefix_raises(self) -> None:
        with self.assertRaises(ValueError):
            OpenRouterClient("sk-proj-fake")

    def test_valid_format_accepts(self) -> None:
        OpenRouterClient("sk-or-v1-fake-key-for-testing")

    def test_repr_redacts_key(self) -> None:
        client = OpenRouterClient("sk-or-v1-secret-do-not-leak")
        self.assertNotIn("secret", repr(client))
        self.assertIn("REDACTED", repr(client))


class TestLoadEnv(unittest.TestCase):
    def setUp(self) -> None:
        self._snapshot = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._snapshot)

    def test_missing_file_silent_return(self) -> None:
        """Missing .env should not raise — returns silently."""
        load_env(Path("/nonexistent/path/.env"))
        # No assertion needed — just verify no exception

    def test_preserves_equals_in_value(self) -> None:
        """A value containing '=' (e.g. base64) must be preserved entirely."""
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("MY_KEY=abc=def=ghi\n")
            f.write("OTHER=plain\n")
            temp_path = Path(f.name)
        try:
            os.environ.pop("MY_KEY", None)
            os.environ.pop("OTHER", None)
            load_env(temp_path)
            self.assertEqual(os.environ.get("MY_KEY"), "abc=def=ghi")
            self.assertEqual(os.environ.get("OTHER"), "plain")
        finally:
            temp_path.unlink()

    def test_skips_comments_and_blank_lines(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("# this is a comment\n")
            f.write("\n")
            f.write("VALID=yes\n")
            temp_path = Path(f.name)
        try:
            os.environ.pop("VALID", None)
            load_env(temp_path)
            self.assertEqual(os.environ.get("VALID"), "yes")
        finally:
            temp_path.unlink()

    def test_does_not_override_existing(self) -> None:
        """Existing env vars must not be overridden by .env (CLI/CI flags win)."""
        os.environ["PRESET"] = "from_environment"
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("PRESET=from_file\n")
            temp_path = Path(f.name)
        try:
            load_env(temp_path)
            self.assertEqual(os.environ.get("PRESET"), "from_environment")
        finally:
            temp_path.unlink()


class TestRankRegex(unittest.TestCase):
    PATTERN = re.compile(RANK_REGEX, re.IGNORECASE | re.DOTALL)

    def test_standard_format_matches(self) -> None:
        m = self.PATTERN.search("RANK: A,B,C\nREASON: Because A is most accurate.")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.groups()[:3], ("A", "B", "C"))

    def test_spaced_format_matches(self) -> None:
        m = self.PATTERN.search("RANK:  C , A , B \nREASON: depth and clarity")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.groups()[:3], ("C", "A", "B"))

    def test_lowercase_format_matches(self) -> None:
        self.assertIsNotNone(self.PATTERN.search("rank: a,b,c\nreason: lowercase works"))

    def test_missing_rank_returns_none(self) -> None:
        self.assertIsNone(self.PATTERN.search("Response: I rank them A then B"))

    def test_out_of_range_letters_returns_none(self) -> None:
        self.assertIsNone(self.PATTERN.search("RANK: D,E,F\nREASON: invalid"))

    def test_missing_reason_now_matches(self) -> None:
        """REASON is optional since 2026-05-15 (Gemini observed emitting empty REASON)."""
        m = self.PATTERN.search("RANK: A,B,C")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.groups()[:3], ("A", "B", "C"))
        # group(4) may be None when REASON is absent — must be handled by caller
        self.assertIsNone(m.group(4))

    def test_empty_reason_matches(self) -> None:
        """REASON: with empty body still matches (group(4) is empty string)."""
        m = self.PATTERN.search("RANK: A,B,C\nREASON: ")
        self.assertIsNotNone(m)


if __name__ == "__main__":
    unittest.main()
