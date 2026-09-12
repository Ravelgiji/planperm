"""Draft review agent — checks an existing PDF against historical data and the
preparation checklist.

This is a LangGraph node. It extracts text from a user-provided PDF,
cross-references it against the preparation checklist, nearby records, and
precedents, then produces a structured review with pass/fail per item.
"""

from __future__ import annotations

import os
import re
from typing import Any, TypedDict

from agents.helpers import (
    extract_pdf_file,
    find_precedents,
    preparation_checklist,
    resolve_authority,
    site_candidates,
)
from agents.guardrails import HARDENING_SUFFIX, scrub_output
from planning_data import fetch_nearby_applications, summarize_applications


class PlanningState(TypedDict, total=False):
    lat: float
    lng: float
    radius_km: float
    construction_type: str
    site_condition: str
    question: str
    pdf_path: str
    jurisdiction: str
    authority: str
    authority_resolution: dict[str, str]
    records: list[dict[str, Any]]
    summary: dict[str, Any]
    candidates: list[dict[str, Any]]
    precedents: list[dict[str, Any]]
    evidence_text: str
    checklist: list[dict[str, str]]
    advice: str
    draft_text: str
    draft_review: str
    errors: list[str]


REVIEW_PROMPT = """You are a cautious Irish planning document reviewer.

You will receive:
1. Text extracted from the user's draft planning application PDF.
2. A preparation checklist — check each item against the draft.
3. Nearby planning records with decisions.
4. Keyword-matched precedents — similar granted and refused applications.
5. The authority and jurisdiction.

Structure your review in these sections:

**1. Checklist cross-check** — For each preparation item, state:
  - ✅ Addressed — if the draft clearly covers it (quote the relevant part)
  - ❌ Missing — if the draft doesn't mention it at all
  - ⚠️ Unclear — if it's mentioned but incomplete or vague

**2. Comparison with granted applications** — What do nearby granted applications
   include that this draft also covers? What do they include that this draft is
   missing? Cite specific record references.

**3. Risk flags** — Based on nearby refusals, what issues might affect this draft?
   Only flag what appears in the data. If refused applications nearby mention
   drainage, access, overlooking, density, or heritage — check if the draft
   addresses those.

**4. Strengths** — What does the draft do well compared to what was granted nearby?

**5. Recommended actions** — Concrete next steps to improve the draft.

Rules:
- Do NOT predict the outcome.
- Do NOT give legal advice.
- Cite record references when comparing.
- If information is missing from the draft, say what is missing specifically.
- End with: "This is an informational review only — not a legal or planning assessment."
"""


# -- Checklist keyword cross-check (deterministic) ----------------------------

_CHECKLIST_KEYWORDS: dict[str, list[str]] = {
    "authority_route": ["permission", "application type", "outline", "retention", "full permission", "planning application"],
    "site_interest": ["ownership", "folio", "title", "consent", "applicant", "land registry", "interest in land"],
    "location_layout": ["ordnance survey", "os map", "site plan", "layout", "1:1000", "1:500", "location map", "site boundary"],
    "proposal_drawings": ["floor plan", "elevation", "section", "drawing", "1:200", "scale", "proposed", "existing"],
    "access_environment": ["access", "drainage", "water", "wastewater", "flood", "heritage", "protected structure", "habitat", "traffic"],
    "lodgement": ["fee", "newspaper", "site notice", "public notice", "e-planning", "submission", "lodgement", "portal"],
}


def _checklist_cross_check(draft_text: str, checklist: list[dict[str, str]]) -> list[dict[str, str]]:
    """Check each checklist item against the draft text using keyword matching."""
    lowered = draft_text.lower()
    results = []
    for item in checklist:
        keywords = _CHECKLIST_KEYWORDS.get(item["id"], [])
        found = [kw for kw in keywords if kw in lowered]
        if len(found) >= 2:
            status = "✅ Addressed"
            detail = f"Draft mentions: {', '.join(found[:3])}"
        elif found:
            status = "⚠️ Unclear"
            detail = f"Draft mentions '{found[0]}' but may need more detail"
        else:
            status = "❌ Missing"
            detail = f"No mention of: {', '.join(keywords[:3])}"
        results.append({**item, "review_status": status, "review_detail": detail})
    return results


def _build_review_context(state: dict[str, Any]) -> str:
    parts = [
        f"Site: {state.get('lat', '?')}, {state.get('lng', '?')}",
        f"Authority: {state.get('authority', 'Unknown')}",
        f"Jurisdiction: {state.get('jurisdiction', 'Unknown')}",
        f"Construction type: {state.get('construction_type', 'Not specified')}",
    ]

    summary = state.get("summary", {})
    if summary:
        parts.append(f"\nNearby: {summary.get('total', 0)} records, "
                      f"approval rate {summary.get('approval_rate', '?')}%.")

    # Checklist with preliminary cross-check results
    checklist_results = state.get("checklist_results", [])
    if checklist_results:
        parts.append("\nPREPARATION CHECKLIST CROSS-CHECK (verify and refine these):")
        for item in checklist_results:
            parts.append(f"  {item['review_status']} {item['title']}: {item['review_detail']}")

    candidates = state.get("candidates", [])
    if candidates:
        parts.append("\nSpatial candidates:")
        for c in candidates[:5]:
            parts.append(f"  - {c['ref']} ({c['decision']}) — {c['distance_m']}m — {c['description'][:150]}")

    precedents = state.get("precedents", [])
    if precedents:
        granted = [p for p in precedents if p["decision"] == "GRANTED"]
        refused = [p for p in precedents if p["decision"] == "REFUSED"]
        if granted:
            parts.append("\nGranted precedents:")
            for p in granted[:4]:
                parts.append(f"  - {p['application_ref']}: {p['description'][:200]}")
        if refused:
            parts.append("\nRefused precedents (check what caused refusal):")
            for p in refused[:4]:
                parts.append(f"  - {p['application_ref']}: {p['description'][:200]}")

    draft = state.get("draft_text", "")
    if draft:
        parts.append(f"\n--- DRAFT APPLICATION TEXT (first 8000 chars) ---\n{draft[:8000]}")

    return "\n".join(parts)


def _fallback_review(state: dict[str, Any]) -> str:
    """Deterministic review — cross-checks the checklist without an LLM."""
    draft_text = state.get("draft_text", "")
    precedents = state.get("precedents", [])
    candidates = state.get("candidates", [])
    summary = state.get("summary", {})
    checklist_results = state.get("checklist_results", [])

    lines = ["**Draft Review**\n"]

    if not draft_text.strip():
        lines.append("No text could be extracted from the provided PDF.\n")
        lines.append("*This is an informational review only — not a legal or planning assessment.*")
        return "\n".join(lines)

    lines.append(f"Extracted {len(draft_text):,} characters from the draft.\n")

    # Checklist cross-check
    if checklist_results:
        lines.append("**Checklist cross-check:**\n")
        for item in checklist_results:
            lines.append(f"{item['review_status']} **{item['title']}** — {item['review_detail']}")
        lines.append("")

    # Nearby context
    if summary:
        lines.append(f"**Nearby context:** {summary.get('total', 0)} records, "
                      f"approval rate: {summary.get('approval_rate', 'N/A')}%.")
    if candidates:
        lines.append(f"- {len(candidates)} record(s) within 100m of the pin.")

    # Precedent comparison
    if precedents:
        granted = [p for p in precedents if p["decision"] == "GRANTED"]
        refused = [p for p in precedents if p["decision"] == "REFUSED"]
        if granted:
            lines.append(f"\n**{len(granted)} similar application(s) were granted nearby.**")
            # Check keyword overlap between draft and granted descriptions
            draft_words = set(re.findall(r'\b\w{4,}\b', draft_text.lower()))
            for p in granted[:3]:
                desc_words = set(re.findall(r'\b\w{4,}\b', p['description'].lower()))
                overlap = draft_words & desc_words - {"will", "consist", "permission", "development", "which", "proposed", "existing"}
                if overlap:
                    shared = ", ".join(sorted(overlap)[:5])
                    lines.append(f"  - {p['application_ref']}: shares terms with your draft ({shared})")
                else:
                    lines.append(f"  - {p['application_ref']}: low keyword overlap — review this record")
        if refused:
            lines.append(f"\n**{len(refused)} similar application(s) were refused — review for risk areas.**")
            for p in refused[:3]:
                lines.append(f"  - {p['application_ref']}: {p['description'][:150]}")
    else:
        lines.append("\nNo keyword-matched precedents found in the current record set.")

    lines.append("\n*This is an informational review only — not a legal or planning assessment.*")
    return "\n".join(lines)


def _ask_llm_review(state: dict[str, Any]) -> str:
    from openai import OpenAI

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ.get("PLANPERM_LLM_BASE_URL")
    model = os.environ.get("PLANPERM_LLM_MODEL", "gpt-4o-mini")

    client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))
    context = _build_review_context(state)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": REVIEW_PROMPT + HARDENING_SUFFIX},
            {"role": "user", "content": context},
        ],
        temperature=0.2,
        max_tokens=1200,
    )
    return response.choices[0].message.content or _fallback_review(state)


# -- LangGraph node ------------------------------------------------------------

def draft_review_node(state: PlanningState) -> PlanningState:
    """LangGraph node: extract PDF, cross-check against checklist and records, review."""
    errors = list(state.get("errors", []))
    lat = state["lat"]
    lng = state["lng"]
    radius = state.get("radius_km", 2.0)
    ctype = state.get("construction_type", "")
    pdf_path = state.get("pdf_path", "")

    # 1. Extract PDF text
    draft_text = ""
    if pdf_path:
        try:
            draft_text = extract_pdf_file(pdf_path)
        except Exception as e:
            errors.append(f"PDF extraction failed: {e}")

    # 2. Resolve authority (reuse from state if available)
    authority = state.get("authority", "")
    jurisdiction = state.get("jurisdiction", "")
    if not authority:
        resolution = resolve_authority(lat, lng)
        authority = resolution.get("authority", "")
        jurisdiction = resolution.get("jurisdiction", "Republic of Ireland")

    # 3. Fetch records (reuse if already in state)
    records = state.get("records")
    if records is None:
        try:
            records = fetch_nearby_applications(lat, lng, radius)
        except RuntimeError as e:
            errors.append(f"Record fetch failed: {e}")
            records = []

    summary = summarize_applications(records)
    candidates = site_candidates(records, lat, lng)

    search_phrase = ctype or draft_text[:200]
    precedents = find_precedents(records, search_phrase) if search_phrase else []

    # 4. Cross-check draft against preparation checklist
    checklist = preparation_checklist(authority, has_evidence=bool(draft_text))
    checklist_results = _checklist_cross_check(draft_text, checklist) if draft_text else checklist

    state_update: PlanningState = {
        "authority": authority,
        "jurisdiction": jurisdiction,
        "records": records,
        "summary": summary,
        "candidates": candidates,
        "precedents": precedents,
        "draft_text": draft_text,
        "checklist": checklist_results,
        "errors": errors,
    }

    # 5. Review
    merged = {**state, **state_update, "checklist_results": checklist_results}
    if os.environ.get("OPENAI_API_KEY") and draft_text:
        try:
            state_update["draft_review"] = scrub_output(_ask_llm_review(merged))
        except Exception as e:
            errors.append(f"LLM review failed: {e}")
            state_update["draft_review"] = _fallback_review(merged)
            state_update["errors"] = errors
    else:
        state_update["draft_review"] = _fallback_review(merged)

    return state_update
