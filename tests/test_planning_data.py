import unittest

from planning_data import normalize_decision, source_url, summarize_applications


class PlanningDataTests(unittest.TestCase):
    def test_normalizes_refusal_before_conditional_language(self):
        self.assertEqual(normalize_decision("Refuse conditional permission"), "REFUSED")

    def test_prefers_authoritative_record_link(self):
        self.assertEqual(source_url("https://planning.example/record/1", 1), "https://planning.example/record/1")

    def test_summary_uses_decided_applications_for_approval_rate(self):
        summary = summarize_applications([
            {"decision": "GRANTED"},
            {"decision": "REFUSED"},
            {"decision": "PENDING"},
        ])
        self.assertEqual(summary["approval_rate"], 50)
        self.assertEqual(summary["total"], 3)


if __name__ == "__main__":
    unittest.main()
