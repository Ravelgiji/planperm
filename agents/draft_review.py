"""Draft review agent — checks an existing PDF against historical data.

This is a LangGraph node. It extracts text from a user-provided PDF,
compares it against nearby records and precedents, and tells the user
what looks aligned and what's missing.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from agents.helpers import extract_pdf_file, find_precedents, site_candidates
from planning_data import fetch_nearby_applications, summarize_applications


class PlanningState(TypedDict, total=False):
    lat: float
    lng: float
    construction_type: str
    site_condition: str
    question: str
    pdf_path: str
    records: list[dict[str, Any]]
    summary: dict[str, Any]
    candidates: list[dict[str, Any]]
    precedents: list[dict[str, Any]]
    advice: str
    draft_text: str
    draft_review: str
    errors: list[str]


REVIEW_PROMPT = """You are a cautious Irish planning document reviewer.

You will receive:
1. Text extracted from the user's draft planning application PDF.
2. Nearby planning records with decisions (granted, refused, pending).
3. Spatial candidates — records very close to the user's pin.
4. Keyword-matched precedents — similar past applications and their outcomes.

Your task:
- Compare the draft against what was granted and refused nearby.
- Identify what the draft covers well.
- Identify gaps: things that nearby granted applications included but this draft doesn't mention.
- Flag risk areas: anything that resembles reasons for refusal in nearby records.
- Do NOT predict the outcome. Do NOT give legal advice.
- Be specific — cite record references when relevant.
- End with: "This is an informational review only — not a legal or planning assessment."
"""


def _build_review_context(state: dict[str, Any]) -> str:
    parts = [
        f"Site: {state.get('lat', '?')}, {state.get('lng', '?')}",
        f"Construction type: {state.get('construction_type', 'Not specified')}",
    ]

    summary = state.get("summary", {})
    if summary:
        parts.append(f"\nNearby: {summary.get('total', 0)} records, "
                      f"approval rate {summary.get('approval_rate', '?')}%.")

    candidates = state.get("candidates", [])
    if candidates:
        parts.append("\nSpatial candidates:")
        for c in candidates[:5]:
            parts.append(f"  - {c['ref']} ({c['decision']}) — {c['distance_m']}m — {c['description'][:120]}")

    precedents = state.get("precedents", [])
    if precedents:
        parts.append("\nSimilar past applications:")
        for p in precedents[:6]:
            parts.append(f"  - {p['application_ref']} ({p['decision']}) — {p['description'][:150]}")

    draft = state.get("draft_text", "")
    if draft:
        parts.append(f"\n--- DRAFT APPLICATION TEXT (first 6000 chars) ---\n{draft[:6000]}")

    return "\n".join(parts)


def _fallback_review(state: dict[str, Any]) -> str:
    precedents = state.get("precedents", [])
    candidates = state.get("candidates", [])
    summary = state.get("summary", {})
    has_draft = bool(state.get("draft_text", "").strip())

    lines = ["**Draft review summary**\n"]
    if not has_draft:
        lines.append("- No text could be extracted from the provided PDF.")
        return "\n".join(lines)

    lines.append(f"- Draft text extracted ({len(state.get('draft_text', ''))} characters).")
    if summary:
        lines.append(f"- {summary.get('total', 0)} nearby records, "
                      f"approval rate: {summary.get('approval_rate', 'N/A')}%.")
    if candidates:
        lines.append(f"- {len(candidates)} record(s) within 100m of the pin — review these for context.")
    if precedents:
        granted = [p for p in precedents if p["decision"] == "GRANTED"]
        refused = [p for p in precedents if p["decision"] == "REFUSED"]
        if granted:
            lines.append(f"- {len(granted)} similar application(s) were granted.")
        if refused:
            lines.append(f"- {len(refused)} similar application(s) were refused — check reasons.")
    lines.append("\n*Full LLM review requires an OpenAI API key. "
                 "This is an informational review only — not a legal or planning assessment.*")
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
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0.2,
        max_tokens=1000,
    )
    return response.choices[0].message.content or _fallback_review(state)


# -- LangGraph node ------------------------------------------------------------

def draft_review_node(state: PlanningState) -> PlanningState:
    """LangGraph node: extract PDF, fetch records if needed, compare, review."""
    errors = list(state.get("errors", []))
    lat = state["lat"]
    lng = state["lng"]
    ctype = state.get("construction_type", "")
    pdf_path = state.get("pdf_path", "")

    # 1. Extract PDF text
    draft_text = ""
    if pdf_path:
        try:
            draft_text = extract_pdf_file(pdf_path)
        except Exception as e:
            errors.append(f"PDF extraction failed: {e}")

    # 2. Fetch records (reuse if already in state from advisor)
    records = state.get("records")
    if records is None:
        try:
            records = fetch_nearby_applications(lat, lng, 2.0)
        except RuntimeError as e:
            errors.append(f"Record fetch failed: {e}")
            records = []

    summary = summarize_applications(records)
    candidates = site_candidates(records, lat, lng)

    # Use draft text for precedent matching if no construction_type given
    search_phrase = ctype or draft_text[:200]
    precedents = find_precedents(records, search_phrase) if search_phrase else []

    state_update: PlanningState = {
        "records": records,
        "summary": summary,
        "candidates": candidates,
        "precedents": precedents,
        "draft_text": draft_text,
        "errors": errors,
    }

    # 3. Review
    merged = {**state, **state_update}
    if os.environ.get("OPENAI_API_KEY") and draft_text:
        try:
            state_update["draft_review"] = _ask_llm_review(merged)
        except Exception as e:
            errors.append(f"LLM review failed: {e}")
            state_update["draft_review"] = _fallback_review(merged)
            state_update["errors"] = errors
    else:
        state_update["draft_review"] = _fallback_review(merged)

    return state_update
