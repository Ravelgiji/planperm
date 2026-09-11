"""Advisor agent — researches a site and gives planning preparation guidance.

This is a LangGraph node. It receives PlanningState, enriches it with
authority resolution, nearby records, candidates, precedents, evidence,
and a preparation checklist, then asks the LLM for advice.
Falls back to a deterministic summary when no API key is available.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from agents.helpers import (
    authority_guidance_url,
    authority_sources,
    extract_web_text,
    find_precedents,
    preparation_checklist,
    resolve_authority,
    site_candidates,
)
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
    sources: list[dict[str, str]]
    evidence_text: str
    checklist: list[dict[str, str]]
    advice: str
    draft_text: str
    draft_review: str
    errors: list[str]


# -- Prompts -------------------------------------------------------------------

SYSTEM_PROMPT = """You are a cautious Irish planning permit preparation advisor.

Rules:
- Provide general informational support only.
- Never predict approval or give legal advice.
- Never invent requirements or deadlines.
- Separate published-record facts from general guidance.
- If you reference a record, cite its reference number and decision.
- When authority guidance text is provided, reference it but note it may be outdated.
- End every response with: "Informational preparation support only — not legal, planning, architectural, or financial advice."

You will receive:
- The site location, jurisdiction, and responsible planning authority.
- A summary of nearby planning applications (approval rate, counts).
- Spatial candidates (records very close to the pin).
- Keyword-matched precedents (similar past applications).
- Authority guidance text (if retrieved).
- A preparation checklist.
- The user's specific question (if any).

Base your answer on the data provided. Do not hallucinate records.
"""


def _build_context(state: PlanningState) -> str:
    parts = [
        f"Site: {state.get('lat', '?')}, {state.get('lng', '?')}",
        f"Jurisdiction: {state.get('jurisdiction', 'Unknown')}",
        f"Authority: {state.get('authority', 'Unknown')}",
        f"Construction type: {state.get('construction_type', 'Not specified')}",
        f"Site condition: {state.get('site_condition', 'Not specified')}",
    ]

    # State the radius the figures cover, and its ceiling. Asked about "20 km",
    # the advisor would otherwise answer from the 2 km set without saying the
    # numbers were for a smaller area than requested.
    radius = state.get("radius_km", 2.0)
    parts.append(
        f"Search radius for every figure below: {radius} km. The tool offers "
        "0.5, 1, 2, 3 and 5 km only - if the user asks about a wider area, say "
        "plainly that 5 km is the widest available and give the figures for the "
        "current radius rather than estimating a larger one."
    )

    summary = state.get("summary", {})
    if summary:
        parts.append(f"\nNearby records within {radius} km: {summary.get('total', 0)} total, "
                      f"approval rate {summary.get('approval_rate', '?')}%, "
                      f"{summary.get('granted', 0)} granted, "
                      f"{summary.get('refused', 0)} refused.")

    candidates = state.get("candidates", [])
    if candidates:
        parts.append("\nSpatial candidates (records near the pin):")
        for c in candidates[:5]:
            parts.append(f"  - {c['ref']} ({c['decision']}) — {c['distance_m']}m — {c['description'][:120]}")

    precedents = state.get("precedents", [])
    if precedents:
        parts.append("\nKeyword-matched precedents:")
        for p in precedents[:5]:
            parts.append(f"  - {p['application_ref']} ({p['decision']}) — {p['description'][:120]}")

    evidence = state.get("evidence_text", "")
    if evidence:
        parts.append(f"\nAuthority guidance (extracted, may be outdated):\n{evidence[:3000]}")

    checklist = state.get("checklist", [])
    if checklist:
        parts.append("\nPreparation checklist:")
        for item in checklist:
            parts.append(f"  - [{item['status']}] {item['title']}: {item['guidance']}")

    sources = state.get("sources", [])
    if sources:
        parts.append("\nAuthority source links:")
        for s in sources:
            parts.append(f"  - {s['title']}: {s['url']}")

    return "\n".join(parts)


def _fallback_advice(state: PlanningState) -> str:
    summary = state.get("summary", {})
    candidates = state.get("candidates", [])
    precedents = state.get("precedents", [])
    ctype = state.get("construction_type", "the proposed development")
    authority = state.get("authority", "Unknown authority")

    lines = [f"**Planning context for {ctype}**\n"]
    lines.append(f"- Authority: {authority} ({state.get('jurisdiction', '?')})")
    if summary:
        lines.append(f"- {summary.get('total', 0)} nearby records, "
                      f"approval rate: {summary.get('approval_rate', 'N/A')}%.")
    if candidates:
        lines.append(f"- {len(candidates)} spatial candidate(s) within 100m of the pin.")
    if precedents:
        refs = ", ".join(f"{p['application_ref']} ({p['decision']})" for p in precedents[:4])
        lines.append(f"- Similar past applications: {refs}.")
    else:
        lines.append("- No keyword-matched precedents in the current record set.")

    evidence = state.get("evidence_text", "")
    if evidence:
        lines.append(f"- Authority guidance retrieved ({len(evidence)} chars) — review recommended.")

    checklist = state.get("checklist", [])
    if checklist:
        lines.append("\n**Preparation steps:**")
        for item in checklist:
            lines.append(f"  - {item['title']}")

    sources = state.get("sources", [])
    if sources:
        lines.append("\n**Authority sources:**")
        for s in sources[:4]:
            lines.append(f"  - [{s['title']}]({s['url']})")

    lines.append("\n*Informational preparation support only — not legal, planning, "
                 "architectural, or financial advice.*")
    return "\n".join(lines)


def _ask_llm(state: PlanningState) -> str:
    from openai import OpenAI

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ.get("PLANPERM_LLM_BASE_URL")
    model = os.environ.get("PLANPERM_LLM_MODEL", "gpt-4.1-mini")

    client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))
    question = state.get("question") or f"What do I need to prepare for a {state.get('construction_type', 'planning application')}?"
    context = _build_context(state)

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Prior turns, so a follow-up reads as a follow-up. Trimmed to the last few
    # exchanges; the site context is rebuilt fresh each turn regardless.
    for earlier in (state.get("chat_history") or [])[-6:]:
        role = earlier.get("role")
        content = str(earlier.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:1500]})

    messages.append({"role": "user", "content": f"{context}\n\nQuestion: {question}"})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=1000,
    )
    return response.choices[0].message.content or _fallback_advice(state)


# -- LangGraph node ------------------------------------------------------------

def advisor_node(state: PlanningState) -> PlanningState:
    """LangGraph node: resolve authority, fetch records, get evidence, generate advice."""
    errors = list(state.get("errors", []))
    lat = state["lat"]
    lng = state["lng"]
    radius = state.get("radius_km", 2.0)
    ctype = state.get("construction_type", "")

    # 1. Resolve authority from boundary
    resolution = resolve_authority(lat, lng)
    authority = resolution.get("authority", "")
    jurisdiction = resolution.get("jurisdiction", "Republic of Ireland")
    if resolution["status"] != "resolved":
        errors.append(f"Authority resolution: {resolution.get('note', 'unknown issue')}")

    # 2. Fetch nearby records
    try:
        records = fetch_nearby_applications(lat, lng, radius)
    except RuntimeError as e:
        errors.append(f"Record fetch failed: {e}")
        records = []

    summary = summarize_applications(records)
    candidates = site_candidates(records, lat, lng)
    precedents = find_precedents(records, ctype) if ctype else []

    # 3. Get authority guidance (if we resolved an authority)
    evidence_text = ""
    guidance_url = authority_guidance_url(authority) if authority else None
    if guidance_url:
        ev = extract_web_text(guidance_url)
        if ev["status"] == "ok":
            evidence_text = ev["text"]
        else:
            errors.append(f"Guidance retrieval: {ev.get('detail', ev['status'])}")

    # 4. Build checklist and source links
    sources = authority_sources(authority) if authority else []
    checklist = preparation_checklist(authority, has_evidence=bool(evidence_text))

    state_update: PlanningState = {
        "jurisdiction": jurisdiction,
        "authority": authority,
        "authority_resolution": resolution,
        "records": records,
        "summary": summary,
        "candidates": candidates,
        "precedents": precedents,
        "sources": sources,
        "evidence_text": evidence_text,
        "checklist": checklist,
        "errors": errors,
    }

    # 5. Generate advice
    merged = {**state, **state_update}
    if os.environ.get("OPENAI_API_KEY"):
        try:
            state_update["advice"] = _ask_llm(merged)
        except Exception as e:
            errors.append(f"LLM call failed: {e}")
            state_update["advice"] = _fallback_advice(merged)
            state_update["errors"] = errors
    else:
        state_update["advice"] = _fallback_advice(merged)

    return state_update
