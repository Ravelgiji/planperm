"""Tests for agents/intake.py — record mining, answer extraction, intent detection, loop control."""

import unittest

from agents.intake import (
    REQUIRED_KEYS,
    empty_profile,
    extract_answers_regex,
    is_complete,
    is_preparation_intent,
    mine_guidance,
    mine_records,
    missing_fields,
    next_questions,
    update_profile,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

GRANTED_RECORDS = [
    {"decision": "GRANTED", "description": "Construction of a two storey dwelling with detached garage and septic tank"},
    {"decision": "GRANTED", "description": "New single storey dwelling with 4 bedrooms, 180 sqm, connection to mains sewer"},
    {"decision": "GRANTED", "description": "Erection of two storey dwelling with attached garage and well"},
    {"decision": "GRANTED", "description": "Extension to existing dwelling, 45 sqm"},
    {"decision": "GRANTED", "description": "Construction of a two storey 3 bedroom dwelling"},
    {"decision": "REFUSED", "description": "Three storey apartment block"},
]

GUIDANCE_WITH_WASTEWATER = (
    "All applications must include a site notice and newspaper notice. "
    "The authority requires a percolation test for sites using a septic tank or "
    "treatment system for wastewater disposal. Applicants in a flood zone must "
    "provide a flood risk assessment."
)


# ---------------------------------------------------------------------------
# mine_records
# ---------------------------------------------------------------------------

class MineRecordsTests(unittest.TestCase):
    def test_prefers_granted_records(self):
        profile = mine_records(GRANTED_RECORDS)
        # 5 granted records, not 6 total
        self.assertEqual(profile["record_count"], 5)

    def test_extracts_typical_storeys(self):
        profile = mine_records(GRANTED_RECORDS)
        self.assertEqual(profile["typical_storeys"], "2")

    def test_storey_distribution(self):
        profile = mine_records(GRANTED_RECORDS)
        dist = profile["storey_distribution"]
        self.assertIn("2", dist)
        self.assertIn("1", dist)
        self.assertGreater(dist["2"], dist["1"])

    def test_extracts_bedrooms(self):
        profile = mine_records(GRANTED_RECORDS)
        self.assertIn("typical_bedrooms", profile)

    def test_extracts_floor_area(self):
        profile = mine_records(GRANTED_RECORDS)
        self.assertIn("typical_area_sqm", profile)
        lo, hi = profile["area_range_sqm"]
        self.assertGreaterEqual(lo, 30)
        self.assertLessEqual(hi, 2000)

    def test_detects_garages(self):
        profile = mine_records(GRANTED_RECORDS)
        self.assertIn("garage_prevalence", profile)
        self.assertGreater(profile["garage_prevalence"], 0)

    def test_detects_dev_types(self):
        profile = mine_records(GRANTED_RECORDS)
        self.assertIn("new dwelling", profile["common_dev_types"])

    def test_detects_wastewater(self):
        profile = mine_records(GRANTED_RECORDS)
        self.assertIn("common_wastewater", profile)

    def test_empty_records(self):
        profile = mine_records([])
        self.assertEqual(profile["record_count"], 0)

    def test_no_descriptions(self):
        records = [{"decision": "GRANTED", "description": ""}]
        profile = mine_records(records)
        self.assertNotIn("typical_storeys", profile)

    def test_falls_back_to_all_when_none_granted(self):
        records = [{"decision": "REFUSED", "description": "Two storey dwelling"}]
        profile = mine_records(records)
        self.assertEqual(profile["record_count"], 1)
        self.assertEqual(profile["typical_storeys"], "2")


# ---------------------------------------------------------------------------
# mine_guidance
# ---------------------------------------------------------------------------

class MineGuidanceTests(unittest.TestCase):
    def test_detects_wastewater_emphasis(self):
        hints = mine_guidance(GUIDANCE_WITH_WASTEWATER)
        self.assertTrue(hints.get("wastewater_emphasis"))

    def test_detects_flood_risk(self):
        hints = mine_guidance(GUIDANCE_WITH_WASTEWATER)
        self.assertTrue(hints.get("flood_risk_emphasis"))

    def test_detects_notices(self):
        hints = mine_guidance(GUIDANCE_WITH_WASTEWATER)
        self.assertTrue(hints.get("requires_notices"))

    def test_empty_guidance(self):
        self.assertEqual(mine_guidance(""), {})

    def test_unrelated_text(self):
        self.assertEqual(mine_guidance("The council offices are open Monday to Friday."), {})


# ---------------------------------------------------------------------------
# extract_answers_regex
# ---------------------------------------------------------------------------

class ExtractAnswersRegexTests(unittest.TestCase):
    def test_extracts_storeys(self):
        self.assertEqual(extract_answers_regex("Two storey")["storeys"], "2")
        self.assertEqual(extract_answers_regex("single storey")["storeys"], "1")
        self.assertEqual(extract_answers_regex("3 storey")["storeys"], "3")

    def test_extracts_bedrooms(self):
        self.assertEqual(extract_answers_regex("4 bedrooms")["bedrooms"], "4")
        self.assertEqual(extract_answers_regex("3 bed")["bedrooms"], "3")

    def test_bare_number_as_bedrooms(self):
        self.assertEqual(extract_answers_regex("3")["bedrooms"], "3")

    def test_extracts_floor_area(self):
        self.assertEqual(extract_answers_regex("about 180 sqm")["floor_area_sqm"], "180")
        self.assertEqual(extract_answers_regex("200 sq m")["floor_area_sqm"], "200")
        self.assertEqual(extract_answers_regex("150m²")["floor_area_sqm"], "150")

    def test_extracts_garage(self):
        self.assertEqual(extract_answers_regex("detached garage")["garage"], "detached")
        self.assertEqual(extract_answers_regex("yes, a garage")["garage"], "yes")

    def test_extracts_development_type(self):
        self.assertEqual(extract_answers_regex("new dwelling")["development_type"], "new dwelling")
        self.assertEqual(extract_answers_regex("an extension")["development_type"], "extension")
        self.assertEqual(extract_answers_regex("change of use")["development_type"], "change of use")

    def test_extracts_wastewater(self):
        self.assertEqual(extract_answers_regex("septic tank")["wastewater"], "septic tank")
        self.assertEqual(extract_answers_regex("treatment system")["wastewater"], "treatment system")
        self.assertEqual(extract_answers_regex("mains sewer")["wastewater"], "mains sewer")

    def test_extracts_water_supply(self):
        self.assertEqual(extract_answers_regex("mains water")["water_supply"], "mains")
        self.assertEqual(extract_answers_regex("private well")["water_supply"], "well")
        self.assertEqual(extract_answers_regex("group water scheme")["water_supply"], "group scheme")

    def test_extracts_site_access(self):
        self.assertEqual(extract_answers_regex("existing entrance")["site_access"], "existing entrance")
        self.assertEqual(extract_answers_regex("new entrance off the road")["site_access"], "new entrance")

    def test_extracts_existing_structures(self):
        self.assertEqual(extract_answers_regex("it's a greenfield site")["existing_structures"], "greenfield")
        self.assertEqual(extract_answers_regex("there's a ruin on it")["existing_structures"], "ruins")
        self.assertEqual(extract_answers_regex("existing house")["existing_structures"], "existing building")

    def test_converts_acres_to_hectares(self):
        result = extract_answers_regex("about 1 acre")
        self.assertIn("site_area_hectares", result)
        self.assertAlmostEqual(float(result["site_area_hectares"]), 0.4, delta=0.05)

    def test_hectares_direct(self):
        self.assertEqual(extract_answers_regex("0.5 hectares")["site_area_hectares"], "0.5")

    def test_compound_answer(self):
        text = "Two storey, 4 bedrooms, about 200 sqm, with a detached garage"
        result = extract_answers_regex(text)
        self.assertEqual(result["storeys"], "2")
        self.assertEqual(result["bedrooms"], "4")
        self.assertEqual(result["floor_area_sqm"], "200")
        self.assertEqual(result["garage"], "detached")

    def test_empty_text(self):
        self.assertEqual(extract_answers_regex(""), {})

    def test_irrelevant_text(self):
        result = extract_answers_regex("I'm not sure about any of that yet")
        # Should not extract phantom values
        self.assertNotIn("storeys", result)
        self.assertNotIn("bedrooms", result)


# ---------------------------------------------------------------------------
# is_preparation_intent
# ---------------------------------------------------------------------------

class PreparationIntentTests(unittest.TestCase):
    def test_build_a_house(self):
        self.assertTrue(is_preparation_intent("I want to build a house"))

    def test_construct_dwelling(self):
        self.assertTrue(is_preparation_intent("I'm planning to construct a new dwelling"))

    def test_extend(self):
        self.assertTrue(is_preparation_intent("I want to extend my home"))

    def test_what_do_i_need(self):
        self.assertTrue(is_preparation_intent("What do I need for planning permission?"))

    def test_how_to_apply(self):
        self.assertTrue(is_preparation_intent("How do I apply for planning?"))

    def test_generate_brief(self):
        self.assertTrue(is_preparation_intent("Generate a brief for me"))

    def test_planning_permission_for(self):
        self.assertTrue(is_preparation_intent("I need planning permission for a bungalow"))

    def test_prepare_checklist(self):
        self.assertTrue(is_preparation_intent("Give me a preparation checklist"))

    def test_not_triggered_by_question_about_records(self):
        self.assertFalse(is_preparation_intent("How many applications were refused nearby?"))

    def test_not_triggered_by_watch(self):
        self.assertFalse(is_preparation_intent("What changed in the weekly list?"))

    def test_not_triggered_by_greeting(self):
        self.assertFalse(is_preparation_intent("Hello"))

    def test_not_triggered_by_specific_record(self):
        self.assertFalse(is_preparation_intent("Tell me about record 24/12345"))


# ---------------------------------------------------------------------------
# Profile management and loop control
# ---------------------------------------------------------------------------

class ProfileTests(unittest.TestCase):
    def test_empty_profile_has_all_keys_none(self):
        profile = empty_profile()
        self.assertEqual(len(profile), 10)
        self.assertTrue(all(v is None for v in profile.values()))

    def test_is_complete_when_required_filled(self):
        profile = empty_profile()
        for key in REQUIRED_KEYS:
            profile[key] = "some value"
        self.assertTrue(is_complete(profile))

    def test_not_complete_when_required_missing(self):
        profile = empty_profile()
        profile["development_type"] = "new dwelling"
        # storeys and bedrooms still None
        self.assertFalse(is_complete(profile))

    def test_missing_fields_all(self):
        profile = empty_profile()
        gaps = missing_fields(profile)
        self.assertEqual(len(gaps), 10)

    def test_missing_fields_required_only(self):
        profile = empty_profile()
        gaps = missing_fields(profile, required_only=True)
        self.assertEqual(len(gaps), len(REQUIRED_KEYS))

    def test_missing_fields_shrinks_as_filled(self):
        profile = empty_profile()
        profile["storeys"] = "2"
        profile["bedrooms"] = "4"
        gaps = missing_fields(profile)
        keys = [g[0] for g in gaps]
        self.assertNotIn("storeys", keys)
        self.assertNotIn("bedrooms", keys)

    def test_next_questions_returns_batch(self):
        profile = empty_profile()
        questions = next_questions(profile)
        self.assertGreater(len(questions), 0)
        self.assertLessEqual(len(questions), 3)

    def test_next_questions_prioritises_required(self):
        profile = empty_profile()
        questions = next_questions(profile)
        keys = [q["key"] for q in questions]
        self.assertTrue(all(k in REQUIRED_KEYS for k in keys))

    def test_next_questions_empty_when_all_filled(self):
        profile = {f[0]: "value" for f in __import__("agents.intake", fromlist=["INTAKE_FIELDS"]).INTAKE_FIELDS}
        self.assertEqual(next_questions(profile), [])

    def test_next_questions_adds_area_hints(self):
        profile = empty_profile()
        area = {"typical_storeys": "2", "storey_distribution": {"2": 5, "1": 2}}
        questions = next_questions(profile, area_profile=area)
        storey_q = next((q for q in questions if q["key"] == "storeys"), None)
        self.assertIsNotNone(storey_q)
        self.assertIn("area_hint", storey_q)

    def test_guidance_hints_boost_wastewater(self):
        # With all required filled, optional fields come next.
        # Wastewater should be boosted when guidance emphasises it.
        profile = empty_profile()
        for k in REQUIRED_KEYS:
            profile[k] = "value"
        hints = {"wastewater_emphasis": True}
        questions = next_questions(profile, guidance_hints=hints)
        keys = [q["key"] for q in questions]
        self.assertIn("wastewater", keys)


class UpdateProfileTests(unittest.TestCase):
    def test_updates_from_user_text(self):
        profile = empty_profile()
        updated, extracted = update_profile(profile, "Two storey, 4 bedrooms")
        self.assertEqual(updated["storeys"], "2")
        self.assertEqual(updated["bedrooms"], "4")
        self.assertIn("storeys", extracted)

    def test_does_not_overwrite_existing(self):
        profile = empty_profile()
        profile["storeys"] = "1"
        updated, extracted = update_profile(profile, "Two storey, 4 bedrooms")
        # storeys was already filled, should not change
        self.assertEqual(updated["storeys"], "1")
        self.assertNotIn("storeys", extracted)

    def test_noop_when_all_filled(self):
        profile = {f[0]: "val" for f in __import__("agents.intake", fromlist=["INTAKE_FIELDS"]).INTAKE_FIELDS}
        updated, extracted = update_profile(profile, "Two storey, 4 bedrooms")
        self.assertEqual(extracted, {})


if __name__ == "__main__":
    unittest.main()



# ---------------------------------------------------------------------------
# Personal details (phase 2)
# ---------------------------------------------------------------------------

from agents.intake import (
    PERSONAL_REQUIRED,
    empty_personal,
    extract_personal_regex,
    format_personal_questions,
    is_personal_opt_in,
    next_personal_questions,
    personal_complete,
    personal_missing,
    update_personal,
)


class PersonalOptInTests(unittest.TestCase):
    def test_yes_variants(self):
        for text in ("yes", "Yeah", "sure", "go ahead", "please", "ok"):
            self.assertTrue(is_personal_opt_in(text), f"Expected True for '{text}'")

    def test_no_variants(self):
        for text in ("no", "Nope", "skip", "no thanks", "not now"):
            self.assertFalse(is_personal_opt_in(text), f"Expected False for '{text}'")

    def test_unclear(self):
        # Without an API key, longer ambiguous sentences return None (unclear)
        result = is_personal_opt_in("I'm not sure what you mean")
        self.assertIn(result, (False, None))

    def test_embedded_yes(self):
        # With LLM available this returns True; without it returns None (>3 words, no exact match)
        result = is_personal_opt_in("yes, please fill it in")
        self.assertIn(result, (True, None))

    def test_embedded_no(self):
        result = is_personal_opt_in("no, I'll do it myself")
        self.assertIn(result, (False, None))


class PersonalProfileTests(unittest.TestCase):
    def test_empty_personal(self):
        p = empty_personal()
        self.assertEqual(len(p), 9)
        self.assertTrue(all(v is None for v in p.values()))

    def test_personal_complete_when_required_filled(self):
        p = empty_personal()
        for k in PERSONAL_REQUIRED:
            p[k] = "value"
        self.assertTrue(personal_complete(p))

    def test_not_complete_when_missing(self):
        p = empty_personal()
        p["applicant_name"] = "Test"
        self.assertFalse(personal_complete(p))

    def test_next_questions_prioritises_required(self):
        p = empty_personal()
        questions = next_personal_questions(p)
        self.assertTrue(len(questions) > 0)
        keys = [q["key"] for q in questions]
        self.assertTrue(all(k in PERSONAL_REQUIRED for k in keys))


class ExtractPersonalRegexTests(unittest.TestCase):
    def test_email(self):
        result = extract_personal_regex("My email is test@example.ie")
        self.assertEqual(result["email"], "test@example.ie")

    def test_phone(self):
        result = extract_personal_regex("Call me on 091 555 1234")
        self.assertIn("phone", result)

    def test_eircode(self):
        result = extract_personal_regex("My eircode is H91 AB12")
        self.assertEqual(result["eircode"], "H91 AB12")

    def test_legal_interest_owner(self):
        result = extract_personal_regex("I am the owner")
        self.assertEqual(result["legal_interest"], "owner")

    def test_legal_interest_occupier(self):
        result = extract_personal_regex("I'm the occupier of the site")
        self.assertEqual(result["legal_interest"], "occupier")
