"""Tests for agents/helpers.py — spatial matching, precedents, authority, checklist."""

import unittest

from agents.helpers import (
    authority_by_name,
    authority_names,
    authority_sources,
    find_precedents,
    preparation_checklist,
    site_candidates,
)


SAMPLE_RECORDS = [
    {"application_ref": "EXACT", "lat": 53.2707, "lon": -9.0568, "decision": "GRANTED",
     "description": "New two storey dwelling", "address": "1 Main St", "link": "https://example.test/1",
     "application_type": "PERMISSION", "date_received": "2024-01-01", "date_decided": "2024-06-01"},
    {"application_ref": "NEAR", "lat": 53.2712, "lon": -9.0568, "decision": "REFUSED",
     "description": "Three storey apartment block", "address": "2 Main St", "link": "https://example.test/2",
     "application_type": "PERMISSION", "date_received": "2024-02-01", "date_decided": "2024-07-01"},
    {"application_ref": "FAR", "lat": 53.28, "lon": -9.0568, "decision": "GRANTED",
     "description": "Extension to dwelling", "address": "3 Main St", "link": "https://example.test/3",
     "application_type": "PERMISSION", "date_received": "2024-03-01", "date_decided": "2024-08-01"},
]


class SiteCandidatesTests(unittest.TestCase):
    def test_finds_nearby_records(self):
        candidates = site_candidates(SAMPLE_RECORDS, 53.2707, -9.0568)
        refs = [c["ref"] for c in candidates]
        self.assertIn("EXACT", refs)
        self.assertIn("NEAR", refs)
        self.assertNotIn("FAR", refs)

    def test_classifies_coordinate_vs_proximate(self):
        candidates = site_candidates(SAMPLE_RECORDS, 53.2707, -9.0568)
        exact = next(c for c in candidates if c["ref"] == "EXACT")
        near = next(c for c in candidates if c["ref"] == "NEAR")
        self.assertEqual(exact["relation"], "coordinate_candidate")
        self.assertEqual(near["relation"], "proximate_candidate")

    def test_carries_address_and_dates(self):
        candidates = site_candidates(SAMPLE_RECORDS, 53.2707, -9.0568)
        exact = next(c for c in candidates if c["ref"] == "EXACT")
        self.assertEqual(exact["address"], "1 Main St")
        self.assertEqual(exact["date_decided"], "2024-06-01")

    def test_sorted_by_distance(self):
        candidates = site_candidates(SAMPLE_RECORDS, 53.2707, -9.0568)
        distances = [c["distance_m"] for c in candidates]
        self.assertEqual(distances, sorted(distances))

    def test_empty_records(self):
        self.assertEqual(site_candidates([], 53.27, -9.05), [])


class PrecedentTests(unittest.TestCase):
    def test_finds_matching_records(self):
        results = find_precedents(SAMPLE_RECORDS, "two storey dwelling")
        self.assertTrue(any(r["application_ref"] == "EXACT" for r in results))

    def test_no_match_returns_empty(self):
        self.assertEqual(find_precedents(SAMPLE_RECORDS, "xyz"), [])

    def test_short_words_ignored(self):
        # Words <= 3 chars should be skipped
        self.assertEqual(find_precedents(SAMPLE_RECORDS, "a to"), [])

    def test_respects_limit(self):
        results = find_precedents(SAMPLE_RECORDS, "dwelling", limit=1)
        self.assertLessEqual(len(results), 1)


class AuthorityRegistryTests(unittest.TestCase):
    def test_loads_all_42_authorities(self):
        self.assertEqual(len(authority_names()), 42)

    def test_roi_has_31(self):
        self.assertEqual(len(authority_names("Republic of Ireland")), 31)

    def test_ni_has_11(self):
        self.assertEqual(len(authority_names("Northern Ireland")), 11)

    def test_lookup_by_name(self):
        galway = authority_by_name("Galway City Council")
        self.assertIsNotNone(galway)
        self.assertEqual(galway["jurisdiction"], "Republic of Ireland")

    def test_unknown_authority_returns_none(self):
        self.assertIsNone(authority_by_name("Nonexistent Council"))

    def test_authority_sources_returns_links(self):
        sources = authority_sources("Galway City Council")
        self.assertTrue(len(sources) > 0)
        self.assertTrue(all("url" in s for s in sources))


class ChecklistTests(unittest.TestCase):
    def test_returns_6_items(self):
        items = preparation_checklist("Galway City Council")
        self.assertEqual(len(items), 6)

    def test_status_reflects_evidence(self):
        without = preparation_checklist("Galway City Council", has_evidence=False)
        with_ev = preparation_checklist("Galway City Council", has_evidence=True)
        self.assertTrue(all(i["status"] == "source_link_only" for i in without))
        self.assertTrue(all(i["status"] == "evidence_retrieved" for i in with_ev))

    def test_items_have_required_fields(self):
        for item in preparation_checklist("Test"):
            self.assertIn("id", item)
            self.assertIn("title", item)
            self.assertIn("guidance", item)


if __name__ == "__main__":
    unittest.main()
