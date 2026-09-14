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
from agents.intake import (
    INTAKE_FIELDS,
    empty_profile,
    extract_answers,
    format_questions,
    is_complete as intake_complete,
    is_preparation_intent,
    mine_guidance,
    mine_records,
    missing_fields,
    next_questions,
    update_profile,
    # Personal details (phase 2)
    OFFER_PERSONAL_TEXT,
    empty_personal,
    format_personal_questions,
    is_personal_opt_in,
    next_personal_questions,
    personal_complete,
    update_personal,
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
    # Intake conversation loop
    intake_phase: str           # "idle" | "gathering" | "complete" | "offer_personal" | "gathering_personal" | "done"
    intake_profile: dict[str, Any]
    intake_area_profile: dict[str, Any]
    intake_guidance_hints: dict[str, Any]
    intake_personal: dict[str, Any]
    filled_form_pdf: bytes


# -- Prompts -------------------------------------------------------------------

SYSTEM_PROMPT = """You are PlanPerm, a conversational Irish planning permission advisor.

You have access to REAL planning records from the national database, the user's local authority details, and the authority's published guidance. Your job is to answer the user's SPECIFIC question using this real data.

HOW TO RESPOND:
- Answer the question asked. If they ask about refusals, show them refusals. If they ask about fees, find the fees in the guidance text. Do not give a generic overview unless they ask for one.
- Cite specific records by reference number, address, decision, and description. Do not summarise when you can be specific.
- When listing records, include: reference, address, what was proposed, the decision, and the link if available.
- Be conversational. If the question is vague, ask a clarifying follow-up: "Do you want me to focus on the closest records, or ones similar to what you're planning?"
- Keep responses focused. A question about drainage doesn't need a full checklist.

WHAT YOU KNOW:
- The user's site location, council, and jurisdiction.
- Nearby planning records with decisions, descriptions, addresses, dates, and links.
- Spatial candidates: records within 100m of the pin.
- Keyword-matched precedents: similar applications to what the user described.
- The council's published application guidance (may be outdated — say so).
- A preparation checklist (only mention if the user asks about preparation or next steps).

RULES:
- Never predict approval or give legal advice.
- Never invent records, requirements, or deadlines.
- Always cite record references when mentioning a specific application.
- ALWAYS include the record's link when discussing a specific application, so the user can check the original filing and refusal reasons themselves. Format as: [View record](url)
- If the guidance text contains specific details (fees, forms, newspaper names, portal URLs), quote them directly.
- If you don't have the information to answer, say so plainly and suggest where to look.
- When a record was refused and you don't know the specific reason, say: "The refusal reason is not in the data I have — check the original record for the decision details" and include the link.
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
        parts.append("\nSpatial candidates (records within 100m of the pin):")
        for c in candidates[:8]:
            parts.append(f"  - {c['ref']} | {c['decision']} | {c.get('address', '')} | {c['distance_m']}m | received {c.get('date_received', '?')} | decided {c.get('date_decided', '?')} | {c['description'][:180]} | link: {c.get('link', '')}")

    # Include more records by decision type so the LLM can answer specific questions
    records = state.get("records") or []
    if records:
        def _date_sort_key(r: dict) -> str:
            return r.get("date_received") or r.get("date_decided") or "0000-00-00"

        refused = sorted([r for r in records if r["decision"] == "REFUSED"], key=_date_sort_key, reverse=True)
        granted = sorted([r for r in records if r["decision"] == "GRANTED"], key=_date_sort_key, reverse=True)
        pending = sorted([r for r in records if r["decision"] == "PENDING"], key=_date_sort_key, reverse=True)

        if refused:
            parts.append(f"\nREFUSED applications nearby ({len(refused)} total, newest first):")
            for r in refused[:10]:
                parts.append(f"  - {r['application_ref']} | {r.get('address', '')} | {r.get('distance_km', '?')}km | received {r.get('date_received', '?')} | decided {r.get('date_decided', '?')} | {r['description'][:180]} | link: {r.get('link', '')}")

        if granted:
            parts.append(f"\nGRANTED applications nearby ({len(granted)} total, newest first):")
            for r in granted[:8]:
                parts.append(f"  - {r['application_ref']} | {r.get('address', '')} | {r.get('distance_km', '?')}km | received {r.get('date_received', '?')} | decided {r.get('date_decided', '?')} | {r['description'][:180]} | link: {r.get('link', '')}")

        if pending:
            parts.append(f"\nPENDING applications nearby ({len(pending)} total, newest first):")
            for r in pending[:5]:
                parts.append(f"  - {r['application_ref']} | {r.get('address', '')} | {r.get('distance_km', '?')}km | received {r.get('date_received', '?')} | {r['description'][:180]} | link: {r.get('link', '')}")

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

    # Include intake profile if available — the user's specific project details
    profile = state.get("intake_profile") or {}
    profile_items = {k: v for k, v in profile.items() if v}
    if profile_items:
        parts.append("\nUser's project details (gathered through intake):")
        for k, v in profile_items.items():
            label = k.replace("_", " ").title()
            parts.append(f"  - {label}: {v}")

    area_prof = state.get("intake_area_profile") or {}
    if area_prof:
        parts.append("\nArea analysis (from granted applications nearby):")
        if area_prof.get("typical_storeys"):
            parts.append(f"  - Most common: {area_prof['typical_storeys']}-storey")
        if area_prof.get("typical_bedrooms"):
            parts.append(f"  - Most common: {area_prof['typical_bedrooms']} bedrooms")
        if area_prof.get("typical_area_sqm"):
            lo, hi = area_prof.get("area_range_sqm", (0, 0))
            parts.append(f"  - Floor area: median {area_prof['typical_area_sqm']} sqm (range {lo}-{hi})")
        if area_prof.get("common_dev_types"):
            parts.append(f"  - Common types: {', '.join(area_prof['common_dev_types'])}")

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
        max_tokens=1500,
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

    # --- Your project (from intake profile) ---
    profile = state.get("intake_profile") or {}
    area_prof = state.get("intake_area_profile") or {}
    has_profile = any(profile.get(k) for k in profile)
    if has_profile:
        lines.append("## Your project\n")
        lines.append("These are the details you provided about your planned development.\n")
        _profile_labels = {
            "development_type": "Development type",
            "storeys": "Storeys",
            "bedrooms": "Bedrooms",
            "floor_area_sqm": "Floor area",
            "garage": "Garage",
            "site_access": "Site access",
            "water_supply": "Water supply",
            "wastewater": "Wastewater",
            "site_area_hectares": "Site area",
            "existing_structures": "Existing structures",
        }
        for key, label in _profile_labels.items():
            val = profile.get(key)
            if val:
                suffix = ""
                if key == "floor_area_sqm":
                    suffix = " sqm"
                elif key == "site_area_hectares":
                    suffix = " hectares"
                elif key == "storeys":
                    suffix = " storey" if val == "1" else " storeys"
                    val = ""  # included in suffix
                lines.append(f"- **{label}:** {val}{suffix}".replace(":  ", ": ").strip())
        lines.append("")

        # Area comparison
        if area_prof:
            lines.append("### How your project compares to the area\n")
            if area_prof.get("typical_storeys") and profile.get("storeys"):
                user_s = profile["storeys"]
                typical_s = area_prof["typical_storeys"]
                if user_s == typical_s:
                    lines.append(f"- Your {user_s}-storey plan matches the most common pattern nearby.")
                else:
                    dist = area_prof.get("storey_distribution", {})
                    dist_text = ", ".join(f"{k}-storey ({v})" for k, v in dist.items())
                    lines.append(f"- Your plan is {user_s}-storey. Nearby granted: {dist_text}.")
            if area_prof.get("typical_area_sqm") and profile.get("floor_area_sqm"):
                try:
                    user_area = int(profile["floor_area_sqm"])
                    typical = area_prof["typical_area_sqm"]
                    lo, hi = area_prof.get("area_range_sqm", (0, 0))
                    if lo <= user_area <= hi:
                        lines.append(f"- Your floor area ({user_area} sqm) is within the range of nearby granted applications ({lo}–{hi} sqm, median {typical}).")
                    elif user_area > hi:
                        lines.append(f"- Your floor area ({user_area} sqm) exceeds nearby granted applications (range {lo}–{hi} sqm). Consider whether this may attract scrutiny.")
                except (ValueError, TypeError):
                    pass
            if area_prof.get("garage_prevalence") and profile.get("garage"):
                lines.append(f"- {area_prof['garage_prevalence']}% of nearby granted applications include a garage.")
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

def _resolve_and_fetch(state: PlanningState, errors: list[str]) -> dict[str, Any]:
    """Shared data-fetching logic: resolve authority, fetch records, evidence.

    Reuses data already in state so follow-up turns skip expensive HTTP calls.
    Returns a dict of resolved fields to merge into state.
    """
    lat = state["lat"]
    lng = state["lng"]
    radius = state.get("radius_km", 2.0)
    ctype = state.get("construction_type", "")

    # 1. Resolve authority
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

    # 2. Fetch nearby records
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

    # 3. Authority guidance
    evidence_text = state.get("evidence_text", "")
    if not evidence_text and authority:
        guidance_url = authority_guidance_url(authority)
        if guidance_url:
            ev = extract_web_text(guidance_url)
            if ev["status"] == "ok":
                evidence_text = ev["text"]
            else:
                errors.append(f"Guidance retrieval: {ev.get('detail', ev['status'])}")

    # 4. Checklist and sources
    sources = authority_sources(authority) if authority else []
    checklist = preparation_checklist(authority, has_evidence=bool(evidence_text))

    return {
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


def advisor_node(state: PlanningState) -> PlanningState:
    """LangGraph node: resolve authority, fetch records, get evidence, generate advice.

    Supports a multi-turn intake conversation:
    - When the user expresses preparation intent ("I want to build a house"),
      the node starts gathering details before generating the brief.
    - Each turn: extract answers from user text → check completeness → ask more
      questions or generate the enriched brief.
    - Non-preparation questions bypass intake and get direct advice as before.
    """
    errors = list(state.get("errors", []))

    # Guard: sanitise user input
    question = state.get("question", "")
    if question:
        cleaned, blocked = sanitise_input(question)
        if blocked:
            return {"advice": cleaned, "errors": errors}
        state = {**state, "question": cleaned}
        question = cleaned

    # Fetch shared data (authority, records, evidence)
    resolved = _resolve_and_fetch(state, errors)
    merged = {**state, **resolved}

    # -- Intake conversation loop --
    intake_phase = state.get("intake_phase", "idle")
    profile = state.get("intake_profile") or {}
    area_profile = state.get("intake_area_profile")
    guidance_hints = state.get("intake_guidance_hints")

    # Detect preparation intent on fresh questions (phase=idle)
    if intake_phase == "idle" and question and is_preparation_intent(question):
        intake_phase = "gathering"
        profile = empty_profile()

        # Pre-fill development_type from the question if detectable
        from agents.intake import extract_answers_regex
        initial = extract_answers_regex(question)
        for k, v in initial.items():
            if v:
                profile[k] = v

        # Also carry construction_type into profile if set
        ctype = state.get("construction_type", "")
        if ctype and not profile.get("development_type"):
            profile["development_type"] = ctype

    # Mine records and guidance (once, on first gathering turn)
    if intake_phase == "gathering":
        records = resolved.get("records") or []
        evidence = resolved.get("evidence_text", "")

        if area_profile is None:
            area_profile = mine_records(records)
        if guidance_hints is None:
            guidance_hints = mine_guidance(evidence)

        # Extract answers from the current question (if we're mid-conversation)
        if question and any(not profile.get(f[0]) for f in INTAKE_FIELDS):
            profile, _ = update_profile(profile, question)

        # Check if we have enough
        if intake_complete(profile):
            intake_phase = "complete"
        else:
            # Ask next batch of questions
            questions = next_questions(profile, area_profile, guidance_hints)
            if not questions:
                # All fields answered or skippable
                intake_phase = "complete"
            else:
                advice_text = format_questions(questions, area_profile)

                return {
                    **resolved,
                    "advice": advice_text,
                    "intake_phase": "gathering",
                    "intake_profile": profile,
                    "intake_area_profile": area_profile,
                    "intake_guidance_hints": guidance_hints,
                }

    # -- Generate output --
    if intake_phase == "complete":
        # Enrich construction_type from the profile
        if profile.get("development_type"):
            merged["construction_type"] = profile["development_type"]

        # Re-find precedents with the now-known construction type
        ctype = merged.get("construction_type", "")
        if ctype and resolved.get("records"):
            resolved["precedents"] = find_precedents(resolved["records"], ctype)
            merged = {**state, **resolved}

        # Generate enriched advice + brief
        merged_with_profile = {**merged, "intake_profile": profile, "intake_area_profile": area_profile or {}}

        if os.environ.get("OPENAI_API_KEY"):
            try:
                advice = scrub_output(_ask_llm(merged_with_profile))
            except Exception as e:
                errors.append(f"LLM call failed: {e}")
                advice = _fallback_advice(merged_with_profile)
                resolved["errors"] = errors
        else:
            advice = _fallback_advice(merged_with_profile)

        brief = _build_draft_brief({**merged, "intake_profile": profile, "intake_area_profile": area_profile or {}})

        # Only offer the Galway form fill when the authority is Galway City Council
        authority = merged.get("authority", "")
        is_galway = "galway city" in authority.lower()

        if is_galway:
            advice_with_offer = advice + "\n\n---\n\n" + OFFER_PERSONAL_TEXT
            next_phase = "offer_personal"
        else:
            advice_with_offer = advice
            next_phase = "done"

        resolved["advice"] = advice_with_offer
        resolved["draft_brief"] = brief
        resolved["intake_phase"] = next_phase
        resolved["intake_profile"] = profile
        resolved["intake_area_profile"] = area_profile or {}
        resolved["intake_guidance_hints"] = guidance_hints or {}
        return resolved

    # -- Phase: waiting for yes/no on personal details --
    if intake_phase == "offer_personal":
        opt_in = is_personal_opt_in(question)
        if opt_in is True:
            # Start gathering personal details
            personal = empty_personal()
            questions = next_personal_questions(personal)
            advice_text = format_personal_questions(questions)
            return {
                **resolved,
                "advice": advice_text,
                "intake_phase": "gathering_personal",
                "intake_profile": profile,
                "intake_area_profile": area_profile or {},
                "intake_guidance_hints": guidance_hints or {},
                "intake_personal": personal,
            }
        elif opt_in is False:
            # User declined — done, keep the brief as-is
            return {
                **resolved,
                "advice": "No problem. Your preparation brief is ready for download. "
                          "You can fill in the personal details on the printed form yourself.",
                "intake_phase": "done",
                "intake_profile": profile,
                "intake_area_profile": area_profile or {},
                "intake_guidance_hints": guidance_hints or {},
            }
        else:
            # Unclear answer — ask again
            return {
                **resolved,
                "advice": "I didn't catch that — would you like me to fill in your personal details "
                          "on the application form? Just say **yes** or **no**.",
                "intake_phase": "offer_personal",
                "intake_profile": profile,
                "intake_area_profile": area_profile or {},
                "intake_guidance_hints": guidance_hints or {},
            }

    # -- Phase: gathering personal details --
    if intake_phase == "gathering_personal":
        personal = state.get("intake_personal") or empty_personal()

        if question:
            personal, _ = update_personal(personal, question)

        if personal_complete(personal):
            # Generate filled form PDF
            from agents.form_fill import fill_form
            try:
                filled_pdf = fill_form(profile, personal)
            except Exception as e:
                errors.append(f"Form fill failed: {e}")
                filled_pdf = b""

            return {
                **resolved,
                "advice": "Your application form has been pre-filled and is ready for download. "
                          "Review it carefully before submitting — this is a draft, not a submission.\n\n"
                          "*Informational preparation support only — not legal, planning, "
                          "architectural, or financial advice.*",
                "intake_phase": "done",
                "intake_profile": profile,
                "intake_area_profile": area_profile or {},
                "intake_guidance_hints": guidance_hints or {},
                "intake_personal": personal,
                "filled_form_pdf": filled_pdf,
            }
        else:
            questions = next_personal_questions(personal)
            if not questions:
                intake_phase = "done"
            else:
                return {
                    **resolved,
                    "advice": format_personal_questions(questions),
                    "intake_phase": "gathering_personal",
                    "intake_profile": profile,
                    "intake_area_profile": area_profile or {},
                    "intake_guidance_hints": guidance_hints or {},
                    "intake_personal": personal,
                }

    # -- Regular (non-intake) advisor flow --
    if os.environ.get("OPENAI_API_KEY"):
        try:
            resolved["advice"] = scrub_output(_ask_llm(merged))
        except Exception as e:
            errors.append(f"LLM call failed: {e}")
            resolved["advice"] = _fallback_advice(merged)
            resolved["errors"] = errors
    else:
        resolved["advice"] = _fallback_advice(merged)

    # Generate brief on explicit preparation questions (non-intake path)
    question_lower = question.lower()
    is_preparation_question = any(w in question_lower for w in (
        "what do i need", "prepare", "brief", "checklist", "guide", "download",
        "how to apply", "application process", "steps to", "generate a draft",
        "generate a brief", "generate a guide", "create a brief", "create a guide",
        "give me a guide", "give me a brief", "summary", "preparation",
        "draft for me",
    ))
    ctype = merged.get("construction_type", "")
    if is_preparation_question and (ctype or "new" in question_lower or "house" in question_lower or "dwelling" in question_lower):
        resolved["draft_brief"] = _build_draft_brief(merged)

    # Preserve intake state across turns
    resolved["intake_phase"] = intake_phase
    resolved["intake_profile"] = profile
    if area_profile is not None:
        resolved["intake_area_profile"] = area_profile
    if guidance_hints is not None:
        resolved["intake_guidance_hints"] = guidance_hints

    return resolved
