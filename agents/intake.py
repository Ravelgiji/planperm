"""Intake engine — gathers development details before generating an enriched brief.

Mines nearby planning records and authority guidance to understand what's
typical in the area, then identifies what the user hasn't told us yet and
produces targeted follow-up questions.

The conversation loop is:
  1. User says "I want to build a house" (or similar)
  2. Advisor detects this is a preparation intent, starts intake
  3. Intake mines records → builds area profile → finds gaps → asks questions
  4. User answers → intake extracts answers → updates profile → checks gaps
  5. When enough is known, advisor generates an enriched preparation brief

The profile is a plain dict, not a Pydantic model, because it travels through
LangGraph state and Streamlit session_state — both need it to be serialisable
without ceremony.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# Intake field definitions
# ---------------------------------------------------------------------------

# Each field: (key, label, question_text, required)
# Required fields must be answered before the brief is generated.
# Optional fields enrich the brief but don't block it.
INTAKE_FIELDS: list[tuple[str, str, str, bool]] = [
    ("development_type", "Type of development",
     "What type of development are you planning? For example: new dwelling, extension, renovation, change of use, or something else?",
     True),
    ("storeys", "Number of storeys",
     "How many storeys will the building have?",
     True),
    ("bedrooms", "Number of bedrooms",
     "How many bedrooms are you planning?",
     True),
    ("floor_area_sqm", "Approximate floor area",
     "What's the approximate total floor area in square metres?",
     False),
    ("garage", "Garage or outbuilding",
     "Will there be a garage or other outbuilding? If so, detached or attached?",
     False),
    ("site_access", "Site access",
     "How is the site accessed — existing entrance, new entrance, or shared access?",
     False),
    ("water_supply", "Water supply",
     "What's the water supply — mains water, private well, or group water scheme?",
     False),
    ("wastewater", "Wastewater",
     "How will wastewater be handled — mains sewer, septic tank, or treatment system?",
     False),
    ("site_area_hectares", "Site area",
     "What's the approximate site area (in hectares or acres)?",
     False),
    ("existing_structures", "Existing structures",
     "Is there anything on the site currently — an existing building, ruins, greenfield?",
     False),
]

FIELD_KEYS = {f[0] for f in INTAKE_FIELDS}
REQUIRED_KEYS = {f[0] for f in INTAKE_FIELDS if f[3]}

# How many questions to ask per turn. Batching avoids the user feeling
# interrogated, but still gathers data efficiently.
QUESTIONS_PER_TURN = 3


# ---------------------------------------------------------------------------
# Mining nearby records for area patterns
# ---------------------------------------------------------------------------

_STOREY_RE = re.compile(
    r"\b(single|one|1|two|2|three|3|four|4)\s*[-\s]?\s*store?y", re.I
)
_STOREY_MAP = {
    "single": "1", "one": "1", "1": "1",
    "two": "2", "2": "2",
    "three": "3", "3": "3",
    "four": "4", "4": "4",
}

_BEDROOM_RE = re.compile(r"\b(\d{1,2})\s*[-\s]?\s*bed(?:room)?s?\b", re.I)

_AREA_SQM_RE = re.compile(
    r"\b(\d{2,5})\s*(?:sq\.?\s*m(?:etres?)?|m²|sqm)\b", re.I
)

_GARAGE_RE = re.compile(
    r"\b(detached|attached)?\s*garage\b", re.I
)

_DEV_TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("new dwelling", re.compile(r"\b(?:new|construction of(?:\s+a)?|erect(?:ion)?(?:\s+of)?)\s+(?:\w+\s+){0,4}dwell", re.I)),
    ("extension", re.compile(r"\bextension\b", re.I)),
    ("renovation", re.compile(r"\brenovation|refurbish|alteration", re.I)),
    ("change of use", re.compile(r"\bchange\s+of\s+use\b", re.I)),
    ("retention", re.compile(r"\bretention\b", re.I)),
    ("apartment", re.compile(r"\bapartment|flat\b", re.I)),
    ("commercial", re.compile(r"\bcommercial|retail|office|shop\b", re.I)),
    ("agricultural", re.compile(r"\bagricultural|farm|shed\b", re.I)),
]

_WASTEWATER_RE = re.compile(
    r"\b(septic|treatment\s+(?:system|plant|unit)|wastewater|mains\s+sewer)\b", re.I
)
_WELL_RE = re.compile(r"\b(well|borehole|group\s+(?:water|scheme))\b", re.I)


def mine_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract typical development patterns from nearby planning records.

    Returns an area profile: what's common in granted applications nearby.
    This gives the user context ("most granted dwellings here are two-storey")
    and helps the advisor tailor questions.
    """
    # Only mine granted records — they represent what the authority accepts
    granted = [r for r in records if r.get("decision") == "GRANTED"]
    if not granted:
        granted = records  # fallback: use all if nothing granted

    storey_counts: Counter[str] = Counter()
    bedroom_counts: Counter[str] = Counter()
    area_values: list[int] = []
    dev_types: Counter[str] = Counter()
    garage_count = 0
    wastewater_types: Counter[str] = Counter()
    water_types: Counter[str] = Counter()

    for r in granted:
        desc = r.get("description", "")
        if not desc:
            continue

        # Storeys
        for m in _STOREY_RE.finditer(desc):
            word = m.group(1).lower()
            storey_counts[_STOREY_MAP.get(word, word)] += 1

        # Bedrooms
        for m in _BEDROOM_RE.finditer(desc):
            bedroom_counts[m.group(1)] += 1

        # Floor area
        for m in _AREA_SQM_RE.finditer(desc):
            val = int(m.group(1))
            if 30 <= val <= 2000:  # sanity bounds
                area_values.append(val)

        # Development type
        for dtype, pattern in _DEV_TYPE_PATTERNS:
            if pattern.search(desc):
                dev_types[dtype] += 1
                break  # one type per record

        # Garage
        if _GARAGE_RE.search(desc):
            garage_count += 1

        # Wastewater
        m = _WASTEWATER_RE.search(desc)
        if m:
            wastewater_types[m.group(1).lower()] += 1

        # Water
        m = _WELL_RE.search(desc)
        if m:
            water_types[m.group(1).lower()] += 1

    profile: dict[str, Any] = {"record_count": len(granted)}

    if storey_counts:
        profile["typical_storeys"] = storey_counts.most_common(1)[0][0]
        profile["storey_distribution"] = dict(storey_counts.most_common())
    if bedroom_counts:
        profile["typical_bedrooms"] = bedroom_counts.most_common(1)[0][0]
        profile["bedroom_distribution"] = dict(bedroom_counts.most_common())
    if area_values:
        area_values.sort()
        profile["typical_area_sqm"] = area_values[len(area_values) // 2]  # median
        profile["area_range_sqm"] = (area_values[0], area_values[-1])
    if dev_types:
        profile["common_dev_types"] = [t for t, _ in dev_types.most_common(3)]
    if garage_count:
        profile["garage_prevalence"] = round(garage_count / len(granted) * 100)
    if wastewater_types:
        profile["common_wastewater"] = wastewater_types.most_common(1)[0][0]
    if water_types:
        profile["common_water"] = water_types.most_common(1)[0][0]

    return profile


def mine_guidance(evidence_text: str) -> dict[str, Any]:
    """Extract requirement hints from the authority guidance text.

    These aren't field values but flags that certain topics matter to this
    authority, so the intake should ask about them.
    """
    if not evidence_text:
        return {}

    hints: dict[str, Any] = {}
    lowered = evidence_text.lower()

    if any(w in lowered for w in ("site notice", "newspaper notice", "public notice")):
        hints["requires_notices"] = True
    if any(w in lowered for w in ("wastewater", "septic", "treatment system", "percolation")):
        hints["wastewater_emphasis"] = True
    if any(w in lowered for w in ("flood risk", "flood zone", "flood plain")):
        hints["flood_risk_emphasis"] = True
    if any(w in lowered for w in ("protected structure", "heritage", "architectural conservation")):
        hints["heritage_emphasis"] = True
    if any(w in lowered for w in ("sight line", "sightline", "visibility splay", "road frontage")):
        hints["access_emphasis"] = True

    return hints


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------

def empty_profile() -> dict[str, Any]:
    """A blank intake profile. Keys present but None."""
    return {f[0]: None for f in INTAKE_FIELDS}


def missing_fields(profile: dict[str, Any], required_only: bool = False) -> list[tuple[str, str, str]]:
    """Fields the user hasn't answered yet.

    Returns list of (key, label, question_text).
    """
    fields = INTAKE_FIELDS if not required_only else [f for f in INTAKE_FIELDS if f[3]]
    return [
        (f[0], f[1], f[2])
        for f in fields
        if not profile.get(f[0])
    ]


def is_complete(profile: dict[str, Any]) -> bool:
    """True when all required fields have values."""
    return all(profile.get(k) for k in REQUIRED_KEYS)


def next_questions(
    profile: dict[str, Any],
    area_profile: dict[str, Any] | None = None,
    guidance_hints: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """The next batch of questions to ask, enriched with area context.

    Returns up to QUESTIONS_PER_TURN dicts with 'key', 'label', 'question',
    and optionally 'area_hint' (what's typical nearby).
    """
    gaps = missing_fields(profile)
    if not gaps:
        return []

    # Prioritise required fields, then optional ones that the guidance
    # emphasises for this authority
    area_profile = area_profile or {}
    guidance_hints = guidance_hints or {}

    def priority(field_tuple: tuple[str, str, str]) -> int:
        key = field_tuple[0]
        # Required fields first
        if key in REQUIRED_KEYS:
            base = 0
        else:
            base = 100
        # Boost fields the guidance emphasises
        if key == "wastewater" and guidance_hints.get("wastewater_emphasis"):
            base -= 10
        if key == "site_access" and guidance_hints.get("access_emphasis"):
            base -= 10
        return base

    gaps.sort(key=priority)
    batch = gaps[:QUESTIONS_PER_TURN]

    questions = []
    for key, label, question_text in batch:
        q: dict[str, str] = {"key": key, "label": label, "question": question_text}

        # Add area context where we have it
        if key == "storeys" and "typical_storeys" in area_profile:
            dist = area_profile.get("storey_distribution", {})
            dist_text = ", ".join(f"{k}-storey ({v})" for k, v in dist.items())
            q["area_hint"] = f"Nearby granted applications: {dist_text}."
        elif key == "bedrooms" and "typical_bedrooms" in area_profile:
            q["area_hint"] = f"Most common nearby: {area_profile['typical_bedrooms']} bedrooms."
        elif key == "floor_area_sqm" and "typical_area_sqm" in area_profile:
            lo, hi = area_profile.get("area_range_sqm", (0, 0))
            q["area_hint"] = f"Nearby granted: median {area_profile['typical_area_sqm']} sqm (range {lo}–{hi})."
        elif key == "garage" and "garage_prevalence" in area_profile:
            q["area_hint"] = f"{area_profile['garage_prevalence']}% of nearby granted applications include a garage."
        elif key == "wastewater" and "common_wastewater" in area_profile:
            q["area_hint"] = f"Most common nearby: {area_profile['common_wastewater']}."
        elif key == "water_supply" and "common_water" in area_profile:
            q["area_hint"] = f"Most common nearby: {area_profile['common_water']}."

        questions.append(q)

    return questions


# ---------------------------------------------------------------------------
# Extracting answers from user text (LLM-based when available, regex fallback)
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """You extract development details from a user's answer about their planned building project.

Given the user's message and a list of fields we're looking for, extract any values mentioned.

Return ONLY a JSON object mapping field keys to the extracted value (as a string).
Only include fields that the user actually answered. If a field is not mentioned, omit it.

Field keys and what they mean:
- development_type: type of development (new dwelling, extension, renovation, etc.)
- storeys: number of storeys (return as a digit string like "2")
- bedrooms: number of bedrooms (return as a digit string like "4")
- floor_area_sqm: floor area in square metres (return as a number string like "180")
- garage: whether there's a garage (return "yes", "no", "detached", or "attached")
- site_access: how the site is accessed (existing entrance, new entrance, shared)
- water_supply: water supply type (mains, well, group scheme)
- wastewater: wastewater handling (mains sewer, septic tank, treatment system)
- site_area_hectares: site area in hectares (convert from acres if given: 1 acre ≈ 0.4 ha)
- existing_structures: what's currently on the site (greenfield, existing building, ruins, etc.)

Be precise. If the user says "two storey" set storeys to "2". If they say "half acre" set site_area_hectares to "0.2".
"""


def extract_answers_llm(
    user_text: str,
    pending_keys: list[str],
) -> dict[str, str]:
    """Use the LLM to extract field values from the user's response."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {}

    try:
        import json
        from openai import OpenAI

        base_url = os.getenv("PLANPERM_LLM_BASE_URL")
        model = os.getenv("PLANPERM_LLM_MODEL", "gpt-4.1-mini")
        client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))

        fields_desc = "\n".join(
            f"- {key}" for key in pending_keys
        )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"Fields we're looking for:\n{fields_desc}\n\nUser's answer:\n{user_text}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=300,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        # Only accept keys we asked about
        return {k: str(v) for k, v in parsed.items() if k in pending_keys and v}
    except Exception:
        return {}


def extract_answers_regex(user_text: str) -> dict[str, str]:
    """Deterministic fallback: pull what we can from the text with regex."""
    found: dict[str, str] = {}
    text = user_text.strip()
    lowered = text.lower()

    # Storeys
    m = _STOREY_RE.search(text)
    if m:
        found["storeys"] = _STOREY_MAP.get(m.group(1).lower(), m.group(1))

    # Bedrooms
    m = _BEDROOM_RE.search(text)
    if m:
        found["bedrooms"] = m.group(1)

    # Floor area
    m = _AREA_SQM_RE.search(text)
    if m:
        found["floor_area_sqm"] = m.group(1)

    # Garage
    m = _GARAGE_RE.search(text)
    if m:
        found["garage"] = m.group(1) or "yes"

    # Development type (first match)
    for dtype, pattern in _DEV_TYPE_PATTERNS:
        if pattern.search(text):
            found["development_type"] = dtype
            break

    # Simple number answers (if user just says "3" or "4 bedrooms")
    if not found.get("bedrooms"):
        m = re.match(r"^(\d{1,2})\s*$", text.strip())
        if m:
            val = int(m.group(1))
            if 1 <= val <= 10:
                found["bedrooms"] = m.group(1)

    # Wastewater
    if "septic" in lowered:
        found["wastewater"] = "septic tank"
    elif "treatment" in lowered:
        found["wastewater"] = "treatment system"
    elif "mains sewer" in lowered or "public sewer" in lowered:
        found["wastewater"] = "mains sewer"

    # Water
    if "mains water" in lowered or "public water" in lowered:
        found["water_supply"] = "mains"
    elif "well" in lowered or "borehole" in lowered:
        found["water_supply"] = "well"
    elif "group" in lowered and ("water" in lowered or "scheme" in lowered):
        found["water_supply"] = "group scheme"

    # Site access
    if "existing entrance" in lowered or "existing access" in lowered:
        found["site_access"] = "existing entrance"
    elif "new entrance" in lowered or "new access" in lowered:
        found["site_access"] = "new entrance"
    elif "shared" in lowered and "access" in lowered:
        found["site_access"] = "shared access"

    # Greenfield / existing
    if "greenfield" in lowered or "empty site" in lowered or "bare" in lowered:
        found["existing_structures"] = "greenfield"
    elif "ruin" in lowered:
        found["existing_structures"] = "ruins"
    elif "existing" in lowered and ("house" in lowered or "building" in lowered or "dwelling" in lowered):
        found["existing_structures"] = "existing building"

    # Site area (acres or hectares)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hectares?|ha)\b", lowered)
    if m:
        found["site_area_hectares"] = m.group(1)
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:acre)s?\b", lowered)
        if m:
            ha = round(float(m.group(1)) * 0.4047, 2)
            found["site_area_hectares"] = str(ha)

    return found


def extract_answers(
    user_text: str,
    pending_keys: list[str],
) -> dict[str, str]:
    """Extract field values from user text. LLM first, regex fallback."""
    # Try LLM
    result = extract_answers_llm(user_text, pending_keys)
    if result:
        # Merge with regex for anything the LLM missed
        regex_result = extract_answers_regex(user_text)
        for k, v in regex_result.items():
            if k in pending_keys and k not in result:
                result[k] = v
        return result

    # Pure regex fallback
    regex_result = extract_answers_regex(user_text)
    return {k: v for k, v in regex_result.items() if k in pending_keys}


def update_profile(
    profile: dict[str, Any],
    user_text: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Update the profile with values extracted from the user's answer.

    Returns (updated_profile, extracted_fields).
    """
    pending = [k for k, _, _, _ in INTAKE_FIELDS if not profile.get(k)]
    if not pending:
        return profile, {}

    extracted = extract_answers(user_text, pending)
    updated = {**profile}
    for k, v in extracted.items():
        if v:
            updated[k] = v

    return updated, extracted


# ---------------------------------------------------------------------------
# Question formatting
# ---------------------------------------------------------------------------

def format_questions(
    questions: list[dict[str, str]],
    area_profile: dict[str, Any] | None = None,
) -> str:
    """Format questions into a readable message for the user."""
    if not questions:
        return ""

    lines: list[str] = []
    lines.append("To give you the best preparation brief, I need a few more details about your project:\n")

    for i, q in enumerate(questions, 1):
        lines.append(f"**{i}. {q['label']}**")
        lines.append(q["question"])
        if q.get("area_hint"):
            lines.append(f"*({q['area_hint']})*")
        lines.append("")

    lines.append("Answer as many as you can — you can skip any you're not sure about yet.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Intent detection — does the user want a preparation brief?
# ---------------------------------------------------------------------------

_PREPARATION_INTENT_PATTERNS = [
    re.compile(r"\b(?:want|plan(?:ning)?|going|like|need)\s+to\s+(?:build|construct|erect|extend|renovate|develop)\b", re.I),
    re.compile(r"\b(?:build|construct|erect)\s+(?:a\s+)?(?:house|home|dwelling|extension|garage|bungalow)\b", re.I),
    re.compile(r"\b(?:new\s+)?(?:dwelling|house|home|bungalow)\b.*\b(?:plan|permission|apply|application)\b", re.I),
    re.compile(r"\b(?:what\s+do\s+i\s+need|how\s+(?:do\s+i|to)\s+(?:apply|prepare|start))\b", re.I),
    re.compile(r"\b(?:prepare|preparation|brief|guide|checklist|draft\s+for\s+me|generate\s+a?\s*(?:brief|guide|draft))\b", re.I),
    re.compile(r"\b(?:planning\s+permission|planning\s+application)\s+(?:for|to)\b", re.I),
]


def is_preparation_intent(text: str) -> bool:
    """Does this message indicate the user wants to prepare a planning application?

    This is the trigger that starts the intake conversation.
    """
    return any(p.search(text) for p in _PREPARATION_INTENT_PATTERNS)


# ---------------------------------------------------------------------------
# Personal details (phase 2) — only gathered if user opts in
# ---------------------------------------------------------------------------

PERSONAL_FIELDS: list[tuple[str, str, str, bool]] = [
    ("applicant_name", "Your name",
     "What is your full name (as it will appear on the application)?",
     True),
    ("address", "Your address",
     "What is your postal address?",
     True),
    ("eircode", "Eircode",
     "What is your Eircode?",
     False),
    ("phone", "Phone number",
     "What is your phone number?",
     False),
    ("email", "Email address",
     "What is your email address?",
     False),
    ("site_address", "Site address",
     "What is the postal address or townland of the proposed development site?",
     True),
    ("legal_interest", "Legal interest",
     "What is your legal interest in the site — owner, occupier, or other?",
     True),
    ("owner_name", "Owner's name",
     "If you are not the owner, what is the owner's name?",
     False),
    ("owner_address", "Owner's address",
     "If you are not the owner, what is the owner's address?",
     False),
]

PERSONAL_KEYS = {f[0] for f in PERSONAL_FIELDS}
PERSONAL_REQUIRED = {f[0] for f in PERSONAL_FIELDS if f[3]}
PERSONAL_QUESTIONS_PER_TURN = 4


def empty_personal() -> dict[str, Any]:
    """A blank personal details dict."""
    return {f[0]: None for f in PERSONAL_FIELDS}


def personal_missing(personal: dict[str, Any], required_only: bool = False) -> list[tuple[str, str, str]]:
    """Personal fields not yet answered."""
    fields = PERSONAL_FIELDS if not required_only else [f for f in PERSONAL_FIELDS if f[3]]
    return [(f[0], f[1], f[2]) for f in fields if not personal.get(f[0])]


def personal_complete(personal: dict[str, Any]) -> bool:
    """True when all required personal fields have values."""
    return all(personal.get(k) for k in PERSONAL_REQUIRED)


def next_personal_questions(personal: dict[str, Any]) -> list[dict[str, str]]:
    """Next batch of personal detail questions."""
    gaps = personal_missing(personal)
    if not gaps:
        return []

    # Required first
    def priority(t: tuple[str, str, str]) -> int:
        return 0 if t[0] in PERSONAL_REQUIRED else 100

    gaps.sort(key=priority)
    batch = gaps[:PERSONAL_QUESTIONS_PER_TURN]
    return [{"key": k, "label": label, "question": q} for k, label, q in batch]


def format_personal_questions(questions: list[dict[str, str]]) -> str:
    """Format personal detail questions for the user."""
    if not questions:
        return ""
    lines = ["To fill in the application form, I need your details. "
             "**This information stays in your browser session only — nothing is stored.**\n"]
    for i, q in enumerate(questions, 1):
        lines.append(f"**{i}. {q['label']}**")
        lines.append(q["question"])
        lines.append("")
    lines.append("Answer what you can — skip any you'd prefer to fill in yourself on the printed form.")
    return "\n".join(lines)


def extract_personal_regex(text: str) -> dict[str, str]:
    """Regex extraction for personal details. Simple pattern matching."""
    found: dict[str, str] = {}
    lowered = text.lower()

    # Email
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if m:
        found["email"] = m.group(0)

    # Phone (Irish formats)
    m = re.search(r"\b(0\d[\d\s\-]{7,12}|\+353[\d\s\-]{8,12})\b", text)
    if m:
        found["phone"] = m.group(1).strip()

    # Eircode
    m = re.search(r"\b([A-Z]\d{2}\s?[A-Z0-9]{4})\b", text, re.I)
    if m:
        found["eircode"] = m.group(1).upper()

    # Legal interest
    if "owner" in lowered and "not" not in lowered:
        found["legal_interest"] = "owner"
    elif "occupier" in lowered:
        found["legal_interest"] = "occupier"
    elif "other" in lowered:
        found["legal_interest"] = "other"

    return found


def extract_personal_llm(text: str, pending_keys: list[str]) -> dict[str, str]:
    """LLM extraction for personal details."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {}

    try:
        import json as _json
        from openai import OpenAI

        base_url = os.getenv("PLANPERM_LLM_BASE_URL")
        model = os.getenv("PLANPERM_LLM_MODEL", "gpt-4.1-mini")
        client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))

        fields_desc = "\n".join(f"- {k}" for k in pending_keys)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Extract personal details from the user's answer. "
                 "Return a JSON object mapping field keys to values. Only include fields actually mentioned.\n"
                 "Keys: applicant_name, address, eircode, phone, email, site_address, legal_interest, owner_name, owner_address"},
                {"role": "user", "content": f"Fields needed:\n{fields_desc}\n\nUser's answer:\n{text}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=300,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = _json.loads(raw)
        return {k: str(v) for k, v in parsed.items() if k in pending_keys and v}
    except Exception:
        return {}


def update_personal(
    personal: dict[str, Any],
    user_text: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Update personal details from user text. Returns (updated, extracted)."""
    pending = [k for k, _, _, _ in PERSONAL_FIELDS if not personal.get(k)]
    if not pending:
        return personal, {}

    # LLM first, regex fallback
    extracted = extract_personal_llm(user_text, pending)
    if not extracted:
        extracted = extract_personal_regex(user_text)
        extracted = {k: v for k, v in extracted.items() if k in pending}
    else:
        regex = extract_personal_regex(user_text)
        for k, v in regex.items():
            if k in pending and k not in extracted:
                extracted[k] = v

    updated = {**personal}
    for k, v in extracted.items():
        if v:
            updated[k] = v
    return updated, extracted


# ---------------------------------------------------------------------------
# The "do you want me to fill your data" offer
# ---------------------------------------------------------------------------

OFFER_PERSONAL_TEXT = (
    "I can also pre-fill the **Galway City Council planning application form** "
    "with your personal details (name, address, site address). "
    "**Nothing is stored** — the filled form exists only as a download.\n\n"
    "Would you like me to fill in your details too? (yes/no)"
)


def is_personal_opt_in(text: str) -> bool | None:
    """Detect yes/no to the personal details offer.

    Returns True for yes, False for no, None if unclear.
    """
    lowered = text.strip().lower().rstrip("!.,")
    if lowered in ("yes", "y", "yeah", "yep", "sure", "go ahead", "please", "ok", "okay", "do it"):
        return True
    if lowered in ("no", "n", "nah", "nope", "skip", "no thanks", "not now"):
        return False
    if re.search(r"\b(yes|yeah|please|go ahead|fill)\b", lowered):
        return True
    if re.search(r"\b(no|skip|don'?t|not now|not sure)\b", lowered):
        return False
    return None
