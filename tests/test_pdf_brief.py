"""Tests for PDF brief generation — encoding safety and structure."""

import unittest

from agents.pdf_brief import brief_to_pdf, _safe_text


class SafeTextTests(unittest.TestCase):
    def test_em_dash_replaced(self):
        self.assertNotIn("\u2014", _safe_text("PlanPerm \u2014 Brief"))

    def test_irish_accents_replaced(self):
        result = _safe_text("Tailte Éireann")
        self.assertNotIn("É", result)

    def test_emoji_replaced(self):
        result = _safe_text("✅ Addressed")
        self.assertNotIn("✅", result)

    def test_plain_ascii_unchanged(self):
        self.assertEqual(_safe_text("Hello world"), "Hello world")

    def test_encodes_to_latin1(self):
        result = _safe_text("Café résumé naïve")
        result.encode("latin-1")  # should not raise


class BriefToPdfTests(unittest.TestCase):
    def test_generates_bytes(self):
        md = "# Test Brief\n\nThis is a test.\n\n- Item one\n- Item two\n"
        result = brief_to_pdf(md)
        self.assertIsInstance(result, bytes)
        self.assertTrue(len(result) > 100)

    def test_starts_with_pdf_header(self):
        md = "# Hello\n\nWorld"
        result = brief_to_pdf(md)
        self.assertTrue(result.startswith(b"%PDF"))

    def test_handles_unicode_content(self):
        md = "# PlanPerm \u2014 Brief\n\n✅ Granted\n❌ Refused\n⚠️ Unclear\nÉireann"
        result = brief_to_pdf(md)
        self.assertIsInstance(result, bytes)
        self.assertTrue(result.startswith(b"%PDF"))

    def test_handles_empty_markdown(self):
        result = brief_to_pdf("")
        self.assertIsInstance(result, bytes)

    def test_handles_long_content(self):
        md = "# Brief\n\n" + ("This is a long paragraph. " * 200) + "\n"
        result = brief_to_pdf(md)
        self.assertTrue(len(result) > 500)


if __name__ == "__main__":
    unittest.main()
