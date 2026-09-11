"""Watch agent - monitors official sources for a workspace and explains changes.

A LangGraph node, sitting alongside `advisor_node` and `draft_review_node`. It
receives PlanningState, resolves the planning authority for the pin, scans that
authority's official weekly-list sources, compares each against the stored
baseline and reports what measurably changed.

The division of labour is deliberate:

  * **Detection is deterministic.** `core.watch` indexes the official page,
    fingerprints it and diffs it against a stored snapshot. If this agent says
    a document appeared, that is a URL set difference, not a model's opinion.
  * **The model only explains.** It receives the already-detected changes and
    writes a plain-language briefing. It never decides what changed, never
    supplies a URL, and never calculates a deadline - it reports the ones
    `core.weekly_list` computed, verbatim.

So the whole node runs with no API key: the alerts are identical, the briefing
is just templated instead of fluent.

Two boundaries are inherited from the original build and kept on purpose:
scanning is user-triggered only - never background, never email - and an alert
describes a source change, not a planning conclusion. Deadlines are reported as
estimates alongside the arithmetic that produced them, and every one points at
the primary document as the authority for timing.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from agents.helpers import authority_sources, resolve_authority
from core.env import llm_configured
from core.watch import compare, fingerprint, read_index
from core.weekly_list import fetch_document_text, is_received_list
from core.watch_store import (
    add_alerts,
    get_workspace,
    latest_snapshot,
    list_alerts,
    save_snapshot,
    save_workspace,
    touch_workspace,
)

MAX_SOURCES_PER_SCAN = 4

# Reading a list document costs a fetch plus a few model calls, so cap how many
# are opened per scan. Only "received applications" lists are candidates - they
# are the ones with an observation window still to run.
MAX_DOCUMENTS_READ_PER_SCAN = 2

_NO_WORKSPACE = (
    "No area is being watched yet. Select a site on the map and press "
    "“Watch this area” in the Monitor change panel, then scan."
)
MAX_ALERTS_IN_BRIEFING = 12
MAX_APPLICATIONS_IN_BRIEFING = 20


SYSTEM_PROMPT = """You are a cautious Irish planning source-monitoring assistant.

You are given changes that have ALREADY been detected deterministically by
comparing an official weekly-list index against a stored baseline. Your job is
only to explain them in plain language.

Rules:
- Use ONLY the detected changes given to you. Never invent a document, a URL,
  a reference number or a date.
- The comparison detects documents that were ADDED to the index. It does not
  detect removals, and it does not detect edits to a document's contents. So
  never claim that nothing was removed or nothing was modified - that was not
  checked. If nothing was added, say only that no new documents were detected.
- Never CALCULATE a deadline. Where a detected change lists applications with
  an observation deadline already computed, report those dates exactly as
  given and say they are estimates - that is the reader's answer, so give it
  plainly rather than deflecting. Where no deadline is given, say none could be
  derived; never fill the gap with one of your own.
- Always add that the published document is the authority for timing and the
  reader should confirm there.
- A new document in an index is a source change, NOT a planning decision and
  NOT a statement about any proposal or site. Never imply an outcome.
- Never predict whether permission will be granted or refused, and never give
  legal, planning or architectural advice.
- Distinguish clearly between what was detected (fact) and what a reader should
  check (action). Do not blur them.
- Be brief: a short paragraph, then a short list of what changed.
- End with: "Informational preparation support only - not legal, planning,
  architectural, or financial advice."
"""


def _scan_source(
    workspace_id: str, source: dict[str, str]
) -> tuple[list[dict], dict | None]:
    """Scan one official source.

    Returns (alerts, problem). A problem is {kind, message}: kind "failed" for
    something that went wrong and should be surfaced as a warning, kind "empty"
    for a page that simply carries no document index. The distinction matters -
    an authority's search form has no list to compare and never will, so
    flagging it as a warning on every scan trains the reader to ignore warnings.
    """
    source_url = source.get("url") or ""
    if not source_url:
        return [], None

    name = source.get("title") or source_url

    try:
        documents = read_index(source_url)
    except requests.RequestException as exc:
        return [], {"kind": "failed",
                    "message": f"{name}: could not be reached ({exc.__class__.__name__})"}
    except Exception as exc:                      # noqa: BLE001 - malformed HTML, odd encodings
        return [], {"kind": "failed", "message": f"{name}: could not be indexed ({exc})"}

    if not documents:
        return [], {
            "kind": "empty",
            "message": (
                f"{name} publishes no document index - it is a search or "
                "information page, so there is nothing on it to compare."
            ),
        }

    previous = latest_snapshot(workspace_id, source_url)
    previous_documents = previous.get("documents") if previous else None

    current_fingerprint = fingerprint(documents)

    # An unchanged fingerprint means the index is byte-identical after sorting,
    # so there is nothing to diff. Still re-save, to refresh captured_at.
    if previous and previous.get("fingerprint") == current_fingerprint:
        save_snapshot(workspace_id, source_url, documents, current_fingerprint)
        return [], None

    alerts = compare(source, documents, previous_documents)
    _attach_applications(alerts, documents)
    save_snapshot(workspace_id, source_url, documents, current_fingerprint)
    return alerts, None


def _attach_applications(alerts: list[dict], documents: list[dict]) -> None:
    """Open newly detected 'received applications' lists and read the rows.

    This is what turns "a new list was published" into "here are the
    applications in it, and here is when observations close on each". Only
    newly detected documents are opened, and only received-applications lists:
    a granted or refused list records decisions already made, and attaching a
    submission deadline to one would be wrong.

    Failure is recorded on the alert and otherwise ignored. The alert is still
    correct and still cited without the rows - it is simply less useful.
    """
    from agents.weekly_list import extract_applications, summarise_extraction

    by_url = {d["url"]: d for d in documents}
    opened = 0

    for alert in alerts:
        if alert.get("alert_type") == "baseline":
            continue
        if opened >= MAX_DOCUMENTS_READ_PER_SCAN:
            break

        document = by_url.get(alert.get("source_url") or "")
        if document is None or not is_received_list(document["title"], document["url"]):
            continue

        opened += 1
        content = fetch_document_text(document["url"])

        if content is None:
            alert["applications_note"] = (
                "The published list could not be downloaded or read, so the "
                "individual applications were not extracted."
            )
            continue

        if content.get("scanned"):
            alert["applications_note"] = (
                "This list appears to be a scanned image rather than text, so the "
                "individual applications could not be extracted. Open the primary "
                "document to read them."
            )
            continue

        try:
            extraction = extract_applications(content["text"])
        except Exception as exc:                  # noqa: BLE001 - never break a scan
            log.info("Could not extract applications from %s: %s", document["url"], exc)
            alert["applications_note"] = (
                "The individual applications could not be extracted from this list."
            )
            continue

        alert["applications"] = extraction["applications"]
        alert["applications_note"] = summarise_extraction(extraction)
        alert["applications_complete"] = extraction["complete"]
        alert["applications_method"] = extraction["method"]

        open_windows = [
            a for a in extraction["applications"]
            if a.get("deadline") and a["deadline"]["is_open"]
        ]
        if open_windows:
            soonest = min(open_windows, key=lambda a: a["deadline"]["days_left"])
            alert["soonest_deadline"] = soonest["deadline"]


def _fallback_briefing(alerts: list[dict[str, Any]], scanned: int, errors: list[str]) -> str:
    """Templated briefing. Same alerts, plainer wording."""
    if not alerts:
        lines = [
            f"Scanned {scanned} official source(s). No measurable change was detected "
            "in the indexed planning-list documents since the last scan.",
        ]
        if errors:
            lines.append("")
            lines.append("Sources that could not be checked:")
            lines.extend(f"- {error}" for error in errors)
        lines.append("")
        lines.append("Informational preparation support only - not legal, planning, "
                     "architectural, or financial advice.")
        return "\n".join(lines)

    baselines = [a for a in alerts if a.get("alert_type") == "baseline"]
    changes = [a for a in alerts if a.get("alert_type") != "baseline"]

    lines: list[str] = []

    if baselines:
        lines.append(
            f"Recorded a baseline for {len(baselines)} official source(s). A baseline "
            "is the comparison point for later scans, not a finding - nothing has been "
            "reported as changed."
        )

    if changes:
        lines.append(
            f"Detected {len(changes)} new document(s) in the official weekly-list index "
            "since the last scan. These are source changes only, not decisions about any "
            "proposal or site."
        )
        lines.append("")
        for alert in changes[:MAX_ALERTS_IN_BRIEFING]:
            lines.append(f"- {alert['title']}: {alert['body']}")
            lines.append(f"  Source: {alert['source_url']}")

            applications = alert.get("applications") or []
            if not applications:
                continue

            open_windows = [
                a for a in applications
                if a.get("deadline") and a["deadline"]["is_open"]
            ]
            lines.append(
                f"  {len(applications)} application(s) were read from this list"
                + (f", {len(open_windows)} still open for observations" if open_windows else "")
                + "."
            )

            for application in sorted(
                open_windows,
                key=lambda a: a["deadline"]["days_left"],
            )[:MAX_APPLICATIONS_IN_BRIEFING]:
                deadline = application["deadline"]
                lines.append(
                    f"    - {application.get('file_number')}: observations close "
                    f"{deadline['closes']} ({deadline['days_left']} day(s) left, "
                    f"estimated). {(application.get('description') or '')[:110]}"
                )

        lines.append("")
        lines.append(
            "Deadlines above are estimated as 5 weeks beginning on the receipt date "
            "printed in the published list. Open each primary document to confirm the "
            "deadline and what the application covers."
        )

    if errors:
        lines.append("")
        lines.append("Sources that could not be checked:")
        lines.extend(f"- {error}" for error in errors)

    lines.append("")
    lines.append("Informational preparation support only - not legal, planning, "
                 "architectural, or financial advice.")
    return "\n".join(lines)


def _build_context(
    workspace: dict[str, Any],
    alerts: list[dict[str, Any]],
    scanned: int,
    errors: list[str],
    notes: list[str] | None = None,
) -> str:
    """Everything the model is allowed to see. Detected facts only."""
    parts = [
        f"Workspace: {workspace.get('label')}",
        f"Planning authority: {workspace.get('authority') or 'not resolved'}",
        f"Official sources scanned: {scanned}",
    ]

    if errors:
        parts.append("Sources that FAILED and could not be checked: " + "; ".join(errors))

    if notes:
        parts.append(
            "Sources with no document index (expected, not a failure - do not "
            "describe these as errors or as something to worry about): "
            + "; ".join(notes)
        )

    if not alerts:
        parts.append("\nDETECTED CHANGES: none. The indexed documents are unchanged "
                     "since the last scan.")
        return "\n".join(parts)

    parts.append(f"\nDETECTED CHANGES ({len(alerts)}), already verified by index comparison:")
    for alert in alerts[:MAX_ALERTS_IN_BRIEFING]:
        parts.append(
            f"- type={alert.get('alert_type')} severity={alert.get('severity')}\n"
            f"  title: {alert.get('title')}\n"
            f"  detail: {alert.get('body')}\n"
            f"  source: {alert.get('source_url')}"
        )

        # The applications read out of the document, with the deadline already
        # computed for each. Without these in the context the model cannot
        # answer "what are the deadlines near me" and falls back to deflecting,
        # even though the pipeline has the answer.
        applications = alert.get("applications") or []
        if not applications:
            continue

        parts.append(
            f"  APPLICATIONS IN THIS DOCUMENT ({len(applications)}), extracted from "
            f"the published list. Each deadline below is ALREADY CALCULATED - report "
            f"it as given, never recompute it:"
        )
        for application in applications[:MAX_APPLICATIONS_IN_BRIEFING]:
            deadline = application.get("deadline")
            if deadline:
                timing = (
                    f"observations close {deadline['closes']} "
                    f"({deadline['days_left']} day(s) left, ESTIMATED)"
                    if deadline["is_open"]
                    else f"observation window closed {deadline['closes']} (ESTIMATED)"
                )
            else:
                timing = "no receipt date printed, so no deadline could be derived"

            parts.append(
                f"    · {application.get('file_number')} - "
                f"{(application.get('description') or '')[:150]}"
                + (f" at {application['location']}" if application.get("location") else "")
                + f". Received {application.get('date_received') or 'not stated'}; {timing}."
            )

        if alert.get("applications_complete") is False:
            parts.append(
                "    (Extraction was PARTIAL - more file numbers appear in the "
                "document than were read. Say so rather than implying this is the "
                "full list.)"
            )

    return "\n".join(parts)


def _ask_llm(context: str, question: str) -> str:
    from openai import OpenAI

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ.get("PLANPERM_LLM_BASE_URL")
    model = os.environ.get("PLANPERM_LLM_MODEL", "gpt-4.1-mini")

    client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\n\nRequest: {question}"},
        ],
        temperature=0.2,
        max_tokens=900,
    )
    return response.choices[0].message.content or ""


def watch_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node: scan a workspace's official sources and brief the user.

    Requires `workspace_id` in state - monitoring is scoped to a saved
    workspace, because a snapshot is only meaningful against a fixed location
    and authority.
    """
    errors = list(state.get("errors", []))
    workspace_id = state.get("workspace_id") or ""

    workspace = get_workspace(workspace_id) if workspace_id else None
    if workspace is None:
        errors.append(
            "Watch requires a saved workspace. Save one first - the scan needs a "
            "persistent workspace for its source snapshots and alerts."
        )
        return {
            "errors": errors,
            "watch_alerts": [],
            "watch_briefing": _NO_WORKSPACE,
            "watch_response": _NO_WORKSPACE,
            "watch_sources_scanned": 0,
        }

    # Resolve the authority if the workspace has not already recorded one, and
    # write it back. Without persisting it, a workspace saved without an
    # authority resolves it on every scan and the UI keeps reporting that
    # scanning is unconfigured.
    authority = workspace.get("authority") or ""
    if not authority:
        resolution = resolve_authority(workspace["lat"], workspace["lng"])
        authority = resolution.get("authority", "")
        if resolution.get("status") != "resolved":
            errors.append(f"Authority resolution: {resolution.get('note', 'unknown issue')}")

        if authority:
            save_workspace(
                label=workspace["label"],
                lat=workspace["lat"],
                lng=workspace["lng"],
                radius_km=workspace.get("radius_km", 2.0),
                authority=authority,
                jurisdiction=resolution.get("jurisdiction", ""),
                workspace_id=workspace_id,
            )

    sources = authority_sources(authority) if authority else []
    if not sources:
        unconfigured = (
            f"No official weekly-list source is configured for "
            f"{authority or 'this area'}. You can still open the primary source "
            "links from the research view."
        )
        errors.append(
            f"Manual index scanning is not configured for {authority or 'this area'} yet."
        )
        return {
            "authority": authority,
            "errors": errors,
            "watch_alerts": list_alerts(workspace_id, limit=MAX_ALERTS_IN_BRIEFING),
            "watch_briefing": unconfigured,
            "watch_response": unconfigured,
            "watch_sources_scanned": 0,
        }

    fresh_alerts: list[dict[str, Any]] = []
    scan_errors: list[str] = []
    scan_notes: list[str] = []
    scanned = 0

    for source in sources[:MAX_SOURCES_PER_SCAN]:
        alerts, problem = _scan_source(workspace_id, source)
        scanned += 1
        if problem:
            if problem["kind"] == "failed":
                scan_errors.append(problem["message"])
            else:
                scan_notes.append(problem["message"])
        fresh_alerts.extend(alerts)

    add_alerts(workspace_id, fresh_alerts)
    touch_workspace(workspace_id)

    new_count = sum(1 for a in fresh_alerts if a.get("alert_type") != "baseline")
    baseline_count = sum(1 for a in fresh_alerts if a.get("alert_type") == "baseline")
    errors.extend(scan_errors)

    context = _build_context(workspace, fresh_alerts, scanned, scan_errors, scan_notes)
    question = state.get("question") or (
        "Explain what changed in the official sources for this workspace, and what "
        "the reader should verify."
    )

    briefing = ""
    if llm_configured():
        try:
            briefing = _ask_llm(context, question)
        except Exception as exc:                  # noqa: BLE001 - fall back, never fail
            errors.append(f"LLM call failed: {exc}")

    if not briefing.strip():
        briefing = _fallback_briefing(fresh_alerts, scanned, scan_errors)

    return {
        "authority": authority,
        "errors": errors,
        "watch_alerts": fresh_alerts,
        "watch_new_alert_count": new_count,
        "watch_new_baseline_count": baseline_count,
        "watch_briefing": briefing,
        # `watch_response` is the key the orchestrator's watch_agent_node reads
        # when it surfaces an answer in the chat. Same text as the briefing;
        # both names are returned so the panel and the orchestrator each get
        # the key they expect.
        "watch_response": briefing,
        "watch_sources_scanned": scanned,
        "watch_sources": sources[:MAX_SOURCES_PER_SCAN],
        "watch_notes": scan_notes,
    }
