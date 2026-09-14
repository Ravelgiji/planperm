"""Tests for agents/form_fill.py — PDF overlay form filling."""

import unittest
from pathlib import Path

from agents.form_fill import _build_description, _build_overlay, _wrap, FORM_PATH


SAMPLE_PROFILE = {
    "development_type": "new dwelling",
    "storeys": "2",
    "bedrooms": "4",
    "floor_area_sqm": "185",
    "garage": "detached",
    "water_supply": "mains",
    "wastewater": "mains sewer",
    "site_area_hectares": "0.2",
    "existing_structures": "greenfield",
}

SAMPLE_PERSONAL = {
    "applicant_name": "Sean O'Brien",
    "address": "12 Salthill Road, Salthill, Galway",
    "eircode": "H91 AB12",
    "phone": "091 555 1234",
    "email": "sean.obrien@example.ie",
    "site_address": "Plot 7, Knocknacarra Road, Knocknacarra, Galway",
    "legal_interest": "owner",
}


class BuildDescriptionTests(unittest.TestCase):
    def test_full_profile(self):
        desc = _build_description(SAMPLE_PROFILE)
        self.assertIn("2-storey", desc)
        self.assertIn("4 bedrooms", desc)
        self.assertIn("185 sq.m", desc)
        self.assertIn("detached garage", desc)

    def test_minimal_profile(self):
        desc = _build_description({"development_type": "extension"})
        self.assertTrue(desc)
        self.assertIn("extension", desc.lower())

    def test_empty_profile(self):
        self.assertEqual(_build_description({}), "")


class WrapTests(unittest.TestCase):
    def test_short_text(self):
        self.assertEqual(_wrap("hello", 80), ["hello"])

    def test_wraps_at_word_boundary(self):
        lines = _wrap("one two three four five six", 12)
        self.assertTrue(all(len(l) <= 16 for l in lines))  # some slack for last word
        self.assertEqual(" ".join(lines), "one two three four five six")

    def test_empty(self):
        self.assertEqual(_wrap("", 80), [])


class BuildOverlayTests(unittest.TestCase):
    def test_returns_page_entries(self):
        result = _build_overlay(SAMPLE_PROFILE, SAMPLE_PERSONAL)
        # Should have entries on page 2 (form fields), page 3 (legal/area), page 11 (contact)
        self.assertTrue(len(result) > 0)

    def test_permission_tick_on_page_2(self):
        result = _build_overlay(SAMPLE_PROFILE, SAMPLE_PERSONAL)
        page2 = result.get(2, [])
        # Should have a tick mark
        ticks = [e for e in page2 if "X" == e[2]]
        self.assertTrue(len(ticks) >= 1)

    def test_applicant_name_on_page_11(self):
        result = _build_overlay(SAMPLE_PROFILE, SAMPLE_PERSONAL)
        page11 = result.get(11, [])
        names = [e for e in page11 if "Sean" in e[2]]
        self.assertTrue(len(names) >= 1)

    def test_description_on_page_2(self):
        result = _build_overlay(SAMPLE_PROFILE, SAMPLE_PERSONAL)
        page2 = result.get(2, [])
        desc_entries = [e for e in page2 if "storey" in e[2].lower() or "bedroom" in e[2].lower()]
        self.assertTrue(len(desc_entries) >= 1)

    def test_site_area_on_page_3(self):
        result = _build_overlay(SAMPLE_PROFILE, SAMPLE_PERSONAL)
        page3 = result.get(3, [])
        area_entries = [e for e in page3 if "0.2" in e[2]]
        self.assertTrue(len(area_entries) >= 1)

    def test_no_personal_still_works(self):
        result = _build_overlay(SAMPLE_PROFILE, None)
        self.assertTrue(len(result) > 0)

    def test_empty_profile_minimal(self):
        result = _build_overlay({}, {})
        # Should still produce at least the permission tick (default)
        page2 = result.get(2, [])
        self.assertTrue(len(page2) >= 1)


class FillFormTests(unittest.TestCase):
    @unittest.skipUnless(FORM_PATH.is_file(), "Galway form PDF not found")
    def test_generates_pdf_bytes(self):
        from agents.form_fill import fill_form
        result = fill_form(SAMPLE_PROFILE, SAMPLE_PERSONAL)
        self.assertIsInstance(result, bytes)
        self.assertTrue(result.startswith(b"%PDF"))
        self.assertGreater(len(result), 1000)

    @unittest.skipUnless(FORM_PATH.is_file(), "Galway form PDF not found")
    def test_generates_without_personal(self):
        from agents.form_fill import fill_form
        result = fill_form(SAMPLE_PROFILE)
        self.assertIsInstance(result, bytes)
        self.assertTrue(result.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
