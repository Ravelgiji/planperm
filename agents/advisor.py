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
from agents.guardrails import HARDENING_SUFFIX, sanitise_input, scrub_output
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

Your job is to give the user SPECIFIC, ACTIONABLE preparation guidance based on the real data provided. Do not just list generic steps — interpret the data.

Structure every response in these sections:

1. **Your authority & site context** — Name the council, the jurisdiction, and what the nearby record data shows (approval rate, volume). One paragraph.

2. **What similar applications tell you** — Look at the precedents provided. What did granted applications have in common? What reasons appear in refusals? Cite specific record references and decisions. If a precedent was refused, say why (from the description). If there are no precedents, say so plainly.

3. **What your council specifically requires** — Extract concrete requirements from the authority guidance text: which forms, what map scales, how many copies, which newspapers for notices, what fees, what the e-planning portal URL is. Do not say "check the guidance" — pull out the actual details if they are in the text provided.

4. **Risks to watch for** — Based on nearby refusals and the site context, flag specific issues: drainage, heritage, access, protected structures, density. Only flag what appears in the data.

5. **Your preparation checklist** — The concrete next steps, ordered by what to do first. Be specific: "Get an OS map at 1:1000 scale with the site outlined in red" not "prepare location materials."

Rules:
- Never predict approval or give legal advice.
- Never invent requirements, records, or deadlines.
- Cite record references when you mention a precedent.
- If the guidance text mentions specific fees, forms, or deadlines, quote them.
- If information is missing, say what is missing rather than guessing.
- End with: "Informational preparation support only — not legal, planning, architectural, or financial advice."
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

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT + HARDENING_SUFFIX}]

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


def _build_draft_brief(state: PlanningState) -> str:
    """Compile the advisor's findings into a readable preparation brief."""
    authority = state.get("authority", "")
    jurisdiction = state.get("jurisdiction", "")
    ctype = state.get("construction_type", "")
    condition = state.get("site_condition", "")
    summary = state.get("summary", {})
    candidates = state.get("candidates", [])
    precedents = state.get("precedents", [])
    sources = state.get("sources", [])
    site_label = state.get("site_label", "")
    lat, lng = state.get("lat"), state.get("lng")
    radius = state.get("radius_km", 2.0)

    lines = [
        "# Preparation Brief",
        "",
        "> **This is a research summary, not a planning application.**",
        "> Verify every detail with the planning authority before acting.\n",
    ]

    # --- Site overview ---
    lines.append("## Your site\n")
    location = site_label or (f"{lat}, {lng}" if lat else "Not specified")
    lines.append(f"**Location:** {location}  ")
    if authority:
        lines.append(f"**Planning authority:** {authority} ({jurisdiction})  ")
    if ctype:
        lines.append(f"**Proposal:** {ctype}  ")
    if condition:
        lines.append(f"**Site condition:** {condition}  ")
    lines.append("")

    # --- What the numbers say ---
    if summary and summary.get("total"):
        total = summary["total"]
        rate = summary.get("approval_rate")
        lines.append("## What the nearby record shows\n")
        lines.append(f"Within {radius} km of your pin there are **{total} planning applications** on record.")
        if rate is not None:
            lines.append(f"Of those that have been decided, **{rate}%** were granted.\n")
            granted = summary.get("granted", 0)
            refused = summary.get("refused", 0)
            pending = summary.get("pending", 0)
            lines.append(f"| Granted | Refused | Pending |")
            lines.append(f"|---------|---------|---------|")
            lines.append(f"| {granted} | {refused} | {pending} |\n")

    # --- Records on or next to the site ---
    if candidates:
        lines.append("## What happened near your site\n")
        lines.append("These are real planning decisions within 100 metres of your pin. "
                      "They show what has been approved and refused in your immediate area — "
                      "useful context, but not a guarantee for your proposal.\n")
        for c in candidates[:6]:
            desc = c["description"]
            if len(desc) > 300:
                desc = desc[:300].rsplit(" ", 1)[0] + "…"
            address = c.get("address", "")
            date_decided = c.get("date_decided", "")
            date_received = c.get("date_received", "")
            app_type = c.get("application_type", "")

            if c["decision"] == "GRANTED":
                icon, verb = "✅", "was **granted**"
            elif c["decision"] == "REFUSED":
                icon, verb = "❌", "was **refused**"
            elif c["decision"] == "PENDING":
                icon, verb = "⏳", "is **pending a decision**"
            else:
                icon, verb = "↩️", "was **withdrawn**"

            # Lead with what happened, where, and when — not a cryptic ID
            header = f"{icon} **{c['distance_m']}m from your pin**"
            if address:
                header += f" — {address}"
            lines.append(header + "  ")

            timing = ""
            if date_decided:
                timing = f"Decided {date_decided}"
            elif date_received:
                timing = f"Received {date_received}"
            if app_type:
                timing = f"{app_type} · {timing}" if timing else app_type

            if timing:
                lines.append(f"*{timing}*  ")

            # What was proposed
            lines.append(f"{desc}  ")

            # The verdict
            lines.append(f"This application {verb}.")

            # Link and reference at the end, not the top
            ref_line = f"Reference: {c['ref']}"
            if c.get("link"):
                ref_line += f" · [View full record]({c['link']})"
            lines.append(f"*{ref_line}*\n")

    # --- What similar applications tell you ---
    if precedents:
        granted = [p for p in precedents if p["decision"] == "GRANTED"]
        refused = [p for p in precedents if p["decision"] == "REFUSED"]
        lines.append("## What similar applications tell you\n")
        if granted:
            lines.append(f"**{len(granted)} similar application(s) were granted nearby:**\n")
            for p in granted[:4]:
                desc = p["description"]
                if len(desc) > 250:
                    desc = desc[:250].rsplit(" ", 1)[0] + "…"
                lines.append(f"- **{p['application_ref']}** — {desc}")
                if p.get("link"):
                    lines.append(f"  [View record]({p['link']})")
            lines.append("")
        if refused:
            lines.append(f"**{len(refused)} similar application(s) were refused:**\n")
            lines.append("Review these to understand what the authority objected to.\n")
            for p in refused[:4]:
                desc = p["description"]
                if len(desc) > 250:
                    desc = desc[:250].rsplit(" ", 1)[0] + "…"
                lines.append(f"- **{p['application_ref']}** — {desc}")
                if p.get("link"):
                    lines.append(f"  [View record]({p['link']})")
            lines.append("")

    # --- What to prepare ---
    lines.append("## What to prepare next\n")
    lines.append("Work through these before contacting the authority or an architect.\n")
    steps = [
        ("1. Confirm the application route", "Check which type of permission applies (full, outline, retention). Your authority's guidance page will list the options."),
        ("2. Confirm site ownership and interest", "Gather title deeds, land registry folio, and any third-party consent needed."),
        ("3. Get your maps and site layout", "You need an Ordnance Survey location map (1:1000 scale, site outlined in red, land in ownership in blue) and a site layout plan (minimum 1:500 scale)."),
        ("4. Prepare drawings", "Floor plans, elevations, and sections — typically at 1:200 scale. Show existing and proposed work in different colours."),
        ("5. Check access, drainage and constraints", "Does the site have road access? Drainage and water connections? Any flood risk, protected structures, or heritage issues nearby?"),
        ("6. Verify fees, notices and submission", "Check the current fee, which newspaper to use for the public notice, and whether the authority accepts e-planning submissions."),
    ]
    for title, detail in steps:
        lines.append(f"**{title}**  ")
        lines.append(f"{detail}\n")

    # --- Authority links ---
    if sources:
        lines.append("## Authority sources\n")
        lines.append("These are the official pages for your planning authority.\n")
        for s in sources:
            lines.append(f"- [{s['title']}]({s['url']})")
        lines.append("")

    lines.append("---\n")
    lines.append("*This brief is informational preparation support only — not legal, planning, architectural, or financial advice. "
                 "Generated by PlanPerm from live government data. Verify all details with the planning authority.*")

    return "\n".join(lines)


# -- LangGraph node ------------------------------------------------------------

def advisor_node(state: PlanningState) -> PlanningState:
    """LangGraph node: resolve authority, fetch records, get evidence, generate advice.

    Reuses data already in state (from a previous turn or the UI) so follow-up
    questions skip the expensive HTTP calls and go straight to the LLM.
    """
    errors = list(state.get("errors", []))
    lat = state["lat"]
    lng = state["lng"]
    radius = state.get("radius_km", 2.0)
    ctype = state.get("construction_type", "")

    # Guard: sanitise user input before anything touches the LLM
    question = state.get("question", "")
    if question:
        cleaned, blocked = sanitise_input(question)
        if blocked:
            return {"advice": cleaned, "errors": errors}
        state = {**state, "question": cleaned}

    # 1. Resolve authority — reuse if already in state
    if state.get("authority"):
        authority = state["authority"]
        jurisdiction = state.get("jurisdiction", "Republic of Ireland")
        resolution = state.get("authority_resolution", {"status": "cached"})
    else:
        resolution = resolve_authority(lat, lng)
        authority = resolution.get("authority", "")
        jurisdiction = resolution.get("jurisdiction", "Republic of Ireland")
        if resolution["status"] != "resolved":
            errors.append(f"Authority resolution: {resolution.get('note', 'unknown issue')}")

    # 2. Fetch nearby records — reuse if already in state
    records = state.get("records")
    if records is None or len(records) == 0:
        try:
            records = fetch_nearby_applications(lat, lng, radius)
        except RuntimeError as e:
            errors.append(f"Record fetch failed: {e}")
            records = []

    summary = summarize_applications(records)
    candidates = site_candidates(records, lat, lng)
    precedents = find_precedents(records, ctype) if ctype else []

    # 3. Get authority guidance — reuse if already in state
    evidence_text = state.get("evidence_text", "")
    if not evidence_text and authority:
        guidance_url = authority_guidance_url(authority)
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
            state_update["advice"] = scrub_output(_ask_llm(merged))
        except Exception as e:
            errors.append(f"LLM call failed: {e}")
            state_update["advice"] = _fallback_advice(merged)
            state_update["errors"] = errors
    else:
        state_update["advice"] = _fallback_advice(merged)

    # 6. Build downloadable preparation brief
    state_update["draft_brief"] = _build_draft_brief({**state, **state_update})

    return state_update
