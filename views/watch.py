"""04 - Monitor change section.

Renders the watch agent: watch the selected area, scan its authority's official
weekly-list sources on demand, and review what measurably changed.

The panel asks for nothing the app already knows. The map pin fixes the
location, Search already supplied its name, and the authority is resolved from
the coordinates - so watching an area is one button, not a form. An earlier
version asked for a label as well, which invited someone to type "athlone"
while the pin sat on Galway and get a workspace that silently monitored the
wrong authority.

Two boundaries from the original build are kept deliberately, and stated in
the UI rather than buried here:

  * **Scanning is user-triggered only.** Nothing runs in the background and
    nothing is emailed. A watched location is a private preference, held
    locally, and the workspace is the only boundary it applies to.
  * **An alert is a source change, not a planning conclusion.** It reports
    that a document appeared in an official index. It does not interpret the
    document, judge a proposal, or state an observation deadline - the primary
    document is the authority for timing, and the UI says so on every alert.
"""

from __future__ import annotations

import html
import json
from typing import Any

import streamlit as st

from agents.helpers import authority_sources, resolve_authority
from core.watch_store import (
    delete_workspace,
    export_workspace,
    list_alerts,
    list_workspaces,
    save_workspace,
    snapshot_summary,
)

SEVERITY_STYLE = {
    "attention": ("#b45309", "Change"),
    "info": ("#0f766e", "Baseline"),
}

# How close a stored workspace must be to the pin to count as "this area".
SAME_AREA_KM = 0.5


def _safe(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _relative(timestamp: str | None) -> str:
    """"4 minutes ago" rather than an ISO timestamp nobody reads."""
    if not timestamp:
        return "not scanned yet"

    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp[:16]

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    seconds = (datetime.now(timezone.utc) - when).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def _watching_here(site: dict[str, Any]) -> dict[str, Any] | None:
    """The workspace covering the current pin, if one exists.

    Matched on coordinates rather than name: the pin is what gets monitored, so
    proximity is the only honest test of "am I already watching this area".
    """
    from planning_data import distance_km

    for workspace in list_workspaces():
        away = distance_km(site["lat"], site["lon"], workspace["lat"], workspace["lng"])
        if away <= SAME_AREA_KM:
            return workspace
    return None


def _status_line(label: str, detail: str) -> None:
    st.markdown(
        f"<div class='site-line'><span class='dot'></span>"
        f"<span>{_safe(label)}</span><span>·</span><span>{_safe(detail)}</span></div>",
        unsafe_allow_html=True,
    )


ESTIMATE_BADGE = (
    "<span class='estimate-badge'>"
    "<svg width='9' height='9' viewBox='0 0 16 16' fill='none' aria-hidden='true'>"
    "<circle cx='8' cy='8' r='6.6' stroke='currentColor' stroke-width='1.6'/>"
    "<path d='M8 5.2 L8 8.4' stroke='currentColor' stroke-width='1.6' stroke-linecap='round'/>"
    "<circle cx='8' cy='10.9' r='0.9' fill='currentColor'/>"
    "</svg>Estimated</span>"
)


def _urgency_class(deadline: dict[str, Any]) -> str:
    """Which of the four urgency states a window is in.

    Amber used to mean both "warning" and "refused", so a closing observation
    window read as a refusal. Four states keep those apart.
    """
    if not deadline.get("is_open"):
        return "deadline-closed"

    days = deadline.get("days_left", 0)
    if days <= 7:
        return "deadline-urgent"
    if days <= 14:
        return "deadline-closing"
    return "deadline-open"


def _render_deadline(deadline: dict[str, Any], received: str | None = None) -> None:
    """One deadline line: the date, its urgency, and how it was derived."""
    from core.weekly_list import describe_deadline

    st.markdown(
        f"<div class='deadline {_urgency_class(deadline)}'>"
        f"<span class='when'>{_safe(describe_deadline(deadline))}</span>"
        f"{ESTIMATE_BADGE}</div>"
        + (
            f"<div class='map-note'>Received {_safe(received)}. "
            f"{_safe(deadline['basis'])}</div>"
            if received else
            f"<div class='map-note'>{_safe(deadline['basis'])}</div>"
        ),
        unsafe_allow_html=True,
    )


SORT_ORDERS = {
    "Deadline - soonest first": "deadline",
    "Received - newest first": "received_desc",
    "Received - oldest first": "received_asc",
    "Reference": "reference",
}


def _sort_applications(
    applications: list[dict[str, Any]], order: str
) -> list[dict[str, Any]]:
    """Order the list. Rows with no usable date sort last, never first.

    Sorted on `date_received_iso`, which is the date printed in the published
    document. Deriving a year from the reference would only work for one of the
    two formats in use - Westmeath puts the year first (26/60461), Dublin puts
    it last (5001/26, WEB2338/26).
    """
    def received(application: dict[str, Any]) -> str:
        return application.get("date_received_iso") or ""

    def days_left(application: dict[str, Any]) -> tuple[int, int]:
        deadline = application.get("deadline")
        if not deadline:
            return (2, 0)                       # no date printed - last
        if not deadline.get("is_open"):
            return (1, deadline.get("days_left", 0))   # closed - after the open ones
        return (0, deadline.get("days_left", 0))

    if order == "deadline":
        return sorted(applications, key=days_left)

    if order in ("received_desc", "received_asc"):
        # Split so undated rows stay last whichever way the dated ones run -
        # reversing a single sort would float them to the top.
        dated = [a for a in applications if received(a)]
        undated = [a for a in applications if not received(a)]
        dated.sort(key=received, reverse=(order == "received_desc"))
        return dated + undated

    return sorted(applications, key=lambda a: a.get("file_number") or "")


def _render_listed_applications(alert: dict[str, Any]) -> None:
    """The individual applications read out of a newly published list.

    Deadlines are shown with the arithmetic that produced them and labelled
    estimates, because the published document is the authority for timing -
    not this subtraction. Where extraction was partial or impossible, that is
    stated rather than leaving a short list to imply completeness.
    """
    from core.weekly_list import describe_deadline

    applications = alert.get("applications") or []
    note = alert.get("applications_note")

    if not applications:
        if note:
            st.markdown(f"<div class='map-note'>{_safe(note)}</div>", unsafe_allow_html=True)
        return

    soonest = alert.get("soonest_deadline")
    if soonest and soonest.get("is_open"):
        st.markdown(
            f"<div class='deadline {_urgency_class(soonest)}'>"
            f"<span>Earliest of {len(applications)}</span>"
            f"<span class='when'>{_safe(describe_deadline(soonest))}</span>"
            f"{ESTIMATE_BADGE}</div>",
            unsafe_allow_html=True,
        )

    open_count = sum(
        1 for a in applications if a.get("deadline") and a["deadline"]["is_open"]
    )
    label = (
        f"{len(applications)} application(s) in this list"
        + (f" · {open_count} still open for observations" if open_count else "")
    )

    st.markdown(
        f"<div class='planperm-kicker'>{_safe(label)}</div>",
        unsafe_allow_html=True,
    )

    if True:
        if note:
            st.caption(note)

        # Sort key is per alert, so two lists on screen can be ordered
        # independently.
        choice = st.selectbox(
            "Order by",
            options=list(SORT_ORDERS),
            key=f"apps_sort_{alert.get('alert_id', 'x')}",
        )
        applications = _sort_applications(applications, SORT_ORDERS[choice])
        if alert.get("applications_complete") is False:
            st.warning(
                "Extraction was partial - more file numbers appear in the document "
                "than were read. Open the primary document for the full list."
            )

        columns = st.columns(2, gap="small")
        for index, application in enumerate(applications):
            deadline = application.get("deadline")
            with columns[index % 2].container(border=True):
                st.markdown(
                    f"<div style='font-weight:600'>{_safe(application['file_number'])}</div>",
                    unsafe_allow_html=True,
                )
                st.write(application.get("description", ""))

                if application.get("location"):
                    st.markdown(
                        f"<div class='map-note'>Location: "
                        f"{_safe(application['location'])}</div>",
                        unsafe_allow_html=True,
                    )

                if application.get("protected_structure"):
                    st.markdown(
                        "<div class='eyebrow' style='color:#b45309'>Protected "
                        "structure</div>",
                        unsafe_allow_html=True,
                    )

                if deadline:
                    _render_deadline(deadline, application.get("date_received"))
                else:
                    st.markdown(
                        "<div class='map-note'>No receipt date was printed for this "
                        "row, so no observation window could be derived.</div>",
                        unsafe_allow_html=True,
                    )

        st.caption(
            "Deadlines are estimated as 5 weeks beginning on the printed receipt "
            "date. Confirm with the planning authority - invalid applications, "
            "further-information requests and extensions all move the real date."
        )


def _render_under_watch(workspace_id: str) -> None:
    """What the agent is holding, shown when there is nothing new to report.

    A watch with nothing to say should still show its work. "Nothing has
    changed" on its own reads as a feature that does not do anything, when in
    fact several hundred documents are being compared on every scan.
    """
    import json

    from core.watch_store import SNAPSHOT_DIR
    from core.weekly_list import is_received_list

    directory = SNAPSHOT_DIR / workspace_id
    if not directory.is_dir():
        return

    documents: list[dict] = []
    for path in directory.glob("*.json"):
        try:
            documents += json.loads(path.read_text(encoding="utf-8")).get("documents", [])
        except (json.JSONDecodeError, OSError):
            continue

    if not documents:
        return

    received = [d for d in documents if is_received_list(d["title"], d["url"])]

    left, right = st.columns(2)
    with left:
        st.metric("Documents under watch", f"{len(documents):,}")
    with right:
        st.metric("Lists of received applications", f"{len(received):,}")

    if received:
        st.markdown(
            "<div class='map-note'>Each scan re-indexes these pages and compares "
            "them against the stored snapshot. When a new list of received "
            "applications appears, it is read and every application in it gets "
            "an estimated observation deadline.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='map-note'>Each scan re-indexes these pages and compares "
            "them against the stored snapshot. None of them is a list of "
            "received applications, so a change here reports a new document "
            "rather than individual applications.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)


def _render_alerts(workspace_id: str) -> None:
    """Show detected changes only.

    Baseline records are deliberately excluded. A baseline is the comparison
    point a first scan establishes, not a finding - listing them under a
    "changes" heading produced a screen that said "no new measurable change"
    directly above two items presented as cited changes, which is the single
    most confusing thing this panel did.
    """
    changes = [
        alert for alert in list_alerts(workspace_id, limit=25)
        if alert.get("alert_type") != "baseline"
    ]

    st.markdown(
        f"<div class='planperm-kicker'>Everything found so far ({len(changes)})</div>",
        unsafe_allow_html=True,
    )

    if not changes:
        _render_under_watch(workspace_id)

        # Distinguish "scanned, nothing new" from "never scanned" - the old copy
        # claimed a baseline had been captured even when none had.
        baselined = bool(snapshot_summary(workspace_id))
        message = (
            "Nothing has changed in the watched sources since the last scan. "
            "Scan again later."
            if baselined else
            "No baseline yet. Press <b>Capture baseline</b> to record what is "
            "published now; later scans compare against it and report what is new."
        )
        st.markdown(f"<div class='map-note'>{message}</div>", unsafe_allow_html=True)
        return

    st.caption(
        "Additions to an indexed source. Not a planning conclusion, and not a "
        "deadline - open the primary document to confirm both."
    )

    for alert in changes:
        colour, badge = SEVERITY_STYLE.get(alert.get("severity", ""), ("#64748b", "Note"))
        with st.container(border=True):
            # One markdown call with unsafe_allow_html does not also process
            # markdown syntax, so `**bold**` rendered as literal asterisks.
            # Use HTML throughout instead of mixing the two.
            st.markdown(
                f"<div class='eyebrow' style='color:{colour}'>{badge} · "
                f"{_safe(_relative(alert.get('detected_at')))}</div>"
                f"<div style='font-weight:600'>{_safe(alert.get('title'))}</div>",
                unsafe_allow_html=True,
            )
            st.write(alert.get("body", ""))

            _render_listed_applications(alert)

            if alert.get("source_url"):
                st.markdown(f"[Open the primary document]({alert['source_url']})")


# -- Public: the compact control, for the narrow left column -----------------

def render_watch_control(site: dict[str, Any], radius_km: float) -> None:
    """Status and the scan button only.

    Deliberately small. This sits beside the map so monitoring stays visible
    next to the site it applies to, while the findings - which need width -
    render below the map via `render_watch_results`.
    """
    st.markdown("<div class='planperm-kicker'>Monitor change</div>", unsafe_allow_html=True)

    workspace = _watching_here(site)

    if workspace is None:
        _status_line(site.get("label") or "Selected site", f"{radius_km:g} km")
        st.markdown(
            "<div class='map-note'>Compare this authority's official weekly-list "
            "sources over time. Scans run only when you press the button.</div>",
            unsafe_allow_html=True,
        )

        if st.button("Watch this area", key="watch_start",
                     type="primary", use_container_width=True):
            with st.spinner("Resolving the planning authority..."):
                resolution = resolve_authority(site["lat"], site["lon"])

            record = save_workspace(
                # The pin's own label, so nothing is retyped and no typed name
                # can disagree with the monitored location.
                label=site.get("label") or f"{site['lat']:.4f}, {site['lon']:.4f}",
                lat=site["lat"],
                lng=site["lon"],
                radius_km=radius_km,
                authority=resolution.get("authority", ""),
                jurisdiction=resolution.get("jurisdiction", ""),
            )
            st.session_state.workspace_id = record["workspace_id"]

            if resolution.get("status") != "resolved":
                st.warning(
                    f"Watching, but the authority could not be resolved: "
                    f"{resolution.get('note', 'unknown issue')}."
                )
            st.rerun()
        return

    st.session_state.workspace_id = workspace["workspace_id"]

    authority = workspace.get("authority") or ""
    sources = authority_sources(authority) if authority else []
    baselines = snapshot_summary(workspace["workspace_id"])

    _status_line(authority or "authority not resolved",
                 _relative(workspace.get("last_scanned")))

    if not authority or not sources:
        st.markdown(
            f"<div class='map-note'>Weekly-list scanning is not configured for "
            f"{_safe(authority or 'this area')}.</div>",
            unsafe_allow_html=True,
        )
        return

    if st.button(
        "Capture baseline" if not baselines else "Scan for changes",
        key="watch_scan", type="primary", use_container_width=True,
    ):
        # Call the watch node directly rather than through the semantic router.
        # A button labelled "Scan for changes" has already expressed its intent,
        # so putting a classifier in front of it can only go wrong - and it did:
        # "what changed?" was routed to the advisor, which reads state["lat"]
        # and raised KeyError: 'lat' because a scan only carries a workspace.
        from agents.watch import watch_node

        with st.spinner("Scanning official sources..."):
            try:
                result = watch_node({
                    "workspace_id": workspace["workspace_id"],
                    "lat": workspace["lat"],
                    "lng": workspace["lng"],
                    "errors": [],
                })
            except Exception as exc:              # noqa: BLE001 - surface, never crash
                st.error(f"The scan could not complete: {exc}")
                result = None

        if result is not None:
            st.session_state.watch_last_result = {
                "briefing": result.get("watch_briefing", ""),
                "new_count": result.get("watch_new_alert_count", 0),
                "scanned": result.get("watch_sources_scanned", 0),
                "errors": result.get("errors", []),
                "notes": result.get("watch_notes", []),
            }
            st.rerun()

    last = st.session_state.get("watch_last_result")
    if last:
        if last["new_count"]:
            st.success(f"{last['new_count']} new change(s) - see below the map.")
        elif last.get("baselines"):
            st.info(
                f"Baseline recorded for {last['baselines']} source(s). Later scans "
                "compare against it."
            )
        else:
            st.info(f"Scanned {last['scanned']} source(s) - no new changes.")


# -- Public: the findings, full width and collapsed, below the map -----------

def _render_demo(workspace: dict[str, Any]) -> None:
    """Let someone see a detection without waiting for the council to publish.

    An authority posts its weekly list about once a week, so a freshly
    baselined area correctly reports nothing for days. That is right, and
    useless for checking the agent works or showing it to anyone.

    This removes one document from the STORED BASELINE. Nothing upstream is
    touched and no alert is written here - the next scan rediscovers that
    document through the ordinary comparison: same indexing, same fingerprint,
    same diff a real publication would trigger. Only the timing is arranged,
    which is why the detection that follows is genuine.

    What it will show depends on the authority, and the panel says so rather
    than letting someone discover it mid-demo. A received-applications list
    yields applications and deadlines; anything else yields only "a document
    appeared".
    """
    from core.watch import fingerprint
    from core.watch_store import SNAPSHOT_DIR, latest_snapshot, save_snapshot
    from core.weekly_list import is_received_list

    workspace_id = workspace["workspace_id"]
    baselines = snapshot_summary(workspace_id)

    if not baselines:
        st.info(
            "Capture a baseline first - there is nothing to rewind until the "
            "agent has recorded what is published now."
        )
        return

    st.markdown(
        "<div class='map-note'>A council publishes its weekly list about once a "
        "week, so a newly watched area reports nothing for days - correctly. To "
        "see a detection now, rewind the stored baseline by one document and "
        "scan again.</div>",
        unsafe_allow_html=True,
    )

    # Prefer a received-applications list: those are the ones the agent opens
    # for per-application deadlines, and they make the fuller demonstration.
    received: list[tuple[str, dict]] = []
    other: list[tuple[str, dict]] = []

    for row in baselines:
        snapshot = latest_snapshot(workspace_id, row["source_url"])
        if not snapshot:
            continue
        for document in snapshot.get("documents", []):
            target = received if is_received_list(document["title"], document["url"]) else other
            target.append((row["source_url"], document))

    candidates = received or other
    if not candidates:
        st.warning("No baselined document to rewind for this area.")
        return

    source_url, document = candidates[0]

    if received:
        st.success(
            f"This area publishes a list of received applications, so the scan "
            f"will read it and estimate an observation deadline for every "
            f"application in it."
        )
    else:
        st.warning(
            f"{workspace.get('authority') or 'This authority'} publishes no "
            "machine-readable list of received applications - its lists sit "
            "behind a JavaScript viewer. A scan here will report that a "
            "document appeared, but cannot extract applications or deadlines "
            "from it. Watch an area served by Westmeath or Dublin County "
            "Council to see the full result."
        )

    st.markdown(
        f"<div class='map-note'>Will rewind: <b>{_safe(document['title'])}</b><br>"
        f"<span style='font-size:.72rem'>{_safe(document['url'])}</span></div>",
        unsafe_allow_html=True,
    )

    if st.button("Rewind the baseline by one document", key="watch_demo_rewind",
                 use_container_width=True):
        snapshot = latest_snapshot(workspace_id, source_url) or {}
        remaining = [
            d for d in snapshot.get("documents", []) if d["url"] != document["url"]
        ]
        save_snapshot(workspace_id, source_url, remaining, fingerprint(remaining))
        st.session_state.pop("watch_last_result", None)
        st.success(
            f"Baseline rewound - it now holds {len(remaining)} document(s). "
            "Press “Scan for changes” on the left to detect it."
        )

    st.caption(
        "This edits the local baseline only. Nothing changes at the council, no "
        "alert is written here, and the detection that follows comes from the "
        "same comparison a real publication would trigger."
    )


def render_watch_results(site: dict[str, Any]) -> None:
    """Detected changes, application deadlines, sources and data controls.

    One collapsible section so it never crowds the map, opened automatically
    when the most recent scan actually found something - a finding the user
    has to go looking for is a finding they will miss.

    Tabs rather than nested expanders inside, because Streamlit forbids an
    expander within an expander.
    """
    workspace = _watching_here(site)
    if workspace is None:
        return

    workspace_id = workspace["workspace_id"]
    changes = [
        alert for alert in list_alerts(workspace_id, limit=25)
        if alert.get("alert_type") != "baseline"
    ]

    last = st.session_state.get("watch_last_result") or {}
    open_by_default = bool(last.get("new_count"))

    # The headline has to say what the section is FOR, not just report a count.
    # "Monitor change - no changes detected" told a first-time reader nothing
    # about what was being monitored or why they should care.
    authority_name = workspace.get("authority") or "this authority"
    applications = [
        application
        for alert in changes
        for application in (alert.get("applications") or [])
    ]
    open_windows = [
        application for application in applications
        if application.get("deadline") and application["deadline"]["is_open"]
    ]

    if open_windows:
        soonest = min(open_windows, key=lambda a: a["deadline"]["days_left"])
        days = soonest["deadline"]["days_left"]
        headline = (
            f"New planning applications near {workspace['label']} — "
            f"{len(open_windows)} open for observations, earliest closes in "
            f"{days} day{'s' if days != 1 else ''}"
        )
    elif changes:
        headline = (
            f"New documents published by {authority_name} — "
            f"{len(changes)} since your last scan"
        )
    elif not workspace.get("last_scanned"):
        headline = (
            f"Ready to watch {authority_name} weekly lists — "
            "capture a baseline to start"
        )
    else:
        headline = (
            f"Watching {authority_name} weekly lists — nothing new since "
            f"{_relative(workspace.get('last_scanned'))}"
        )

    with st.expander(headline, expanded=open_by_default):
        st.caption(
            f"This section compares {authority_name}'s official weekly-list pages "
            "against the last scan, reads any newly published list of received "
            "applications, and estimates the observation deadline for each one. "
            "It scans only when you press the button."
        )
        changes_tab, sources_tab, demo_tab, data_tab = st.tabs(
            ["Detected changes", "Sources watched", "Try it", "Data and privacy"]
        )

        with changes_tab:
            if last.get("briefing"):
                scanned_when = _relative(workspace.get("last_scanned"))
                found = last.get("new_count") or 0
                baselines = last.get("baselines") or 0

                if found:
                    outcome = f"found {found} change{'s' if found != 1 else ''}"
                elif baselines:
                    outcome = f"recorded a baseline for {baselines} source(s)"
                else:
                    outcome = "nothing new"

                st.markdown(
                    f"<div class='eyebrow'>Your last scan · {_safe(scanned_when)} · "
                    f"{_safe(outcome)}</div>",
                    unsafe_allow_html=True,
                )
                st.write(last["briefing"])

                # Only genuine failures warrant a warning. A page with no
                # document index is a fact about that page, not a problem.
                for error in last.get("errors", []):
                    st.warning(error)
                for note in last.get("notes", []):
                    st.caption(note)
                st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)

            _render_alerts(workspace_id)

        with sources_tab:
            authority = workspace.get("authority") or ""
            sources = authority_sources(authority) if authority else []
            baselines = snapshot_summary(workspace_id)
            indexed = sum(row["document_count"] for row in baselines)

            st.markdown(
                f"<div class='map-note'>{indexed} document(s) baselined across "
                f"{len(baselines)} source(s) for {_safe(authority)}.</div>",
                unsafe_allow_html=True,
            )

            for source in sources:
                existing = next(
                    (b for b in baselines if b["source_url"] == source.get("url")), None
                )
                state = (
                    f"{existing['document_count']} document(s), captured "
                    f"{existing['captured_at'][:10]}"
                    if existing else "not yet baselined"
                )
                st.markdown(
                    f"- [{_safe(source.get('title') or source.get('url'))}]"
                    f"({source.get('url')})  \n"
                    f"  <span class='map-note'>{_safe(state)}</span>",
                    unsafe_allow_html=True,
                )

        with demo_tab:
            _render_demo(workspace)

        with data_tab:
            st.caption(
                "Watched locations are private preferences, stored on this machine "
                "only. Nothing is uploaded, nothing is emailed, and nothing is "
                "shared between areas. Scans happen only when you press the button."
            )
            export_column, delete_column = st.columns(2)

            with export_column:
                st.download_button(
                    "Export JSON",
                    data=json.dumps(
                        export_workspace(workspace_id), indent=2, default=str
                    ),
                    file_name=f"{workspace_id}-watch-export.json",
                    mime="application/json",
                    use_container_width=True,
                )

            with delete_column:
                if st.button("Stop watching", key="watch_delete",
                             use_container_width=True):
                    delete_workspace(workspace_id)
                    st.session_state.workspace_id = None
                    st.session_state.pop("watch_last_result", None)
                    st.rerun()
