"""Browsable list of the planning applications near the pin.

The map shows dots and the hero shows counts, but there was no way to read the
records themselves - so "are there any applications at Bastion Quay?" had no
answer on screen, even though 106 of them sit within 500 m. This is that list.
"""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

DECISION_STYLE = {
    "GRANTED": ("#0f766e", "Granted"),
    "REFUSED": ("#b45309", "Refused"),
    "PENDING": ("#b7791f", "Pending"),
    "WITHDRAWN": ("#64748b", "Withdrawn"),
}

PAGE_SIZE = 12

# Records arrive nearest-first. Dates are already ISO strings, so they sort as
# text without parsing - and there is no need to infer a year from the
# reference, which would not survive both formats anyway.
SORT_ORDERS = {
    "Nearest first": "distance",
    "Received - newest first": "received_desc",
    "Received - oldest first": "received_asc",
    "Decided - most recent first": "decided_desc",
}


def _sort_records(
    records: list[dict[str, Any]], order: str
) -> list[dict[str, Any]]:
    """Order the list. Records with no date for the chosen field sort last.

    Split rather than reversed: reversing one sort would float the undated rows
    to the top, which is where they are least useful.
    """
    if order == "distance":
        return sorted(records, key=lambda r: r.get("distance_km") or 0)

    field = "date_decided" if order.startswith("decided") else "date_received"
    newest_first = order.endswith("_desc")

    dated = [r for r in records if r.get(field)]
    undated = [r for r in records if not r.get(field)]
    dated.sort(key=lambda r: r[field], reverse=newest_first)
    return dated + undated


def _safe(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def render_records(
    applications: list[dict[str, Any]],
    site_label: str,
    area_count: int | None = None,
) -> None:
    """The applications near the pin, nearest first, filterable.

    `area_count` is how many exist in the area, which can exceed what the list
    holds. Naming both in the label matters: the hero read 20,574 while this
    said 7,603, and nothing on the label explained why one screen showed two
    different totals.
    """
    if not applications:
        return

    held = len(applications)
    partial = area_count is not None and area_count > held
    heading = (
        f"Planning applications near {site_label} "
        + (f"(nearest {held:,} of {area_count:,})" if partial else f"({held:,})")
    )

    with st.expander(heading, expanded=False):
        st.caption(
            (
                f"The {held:,} nearest of {area_count:,} records in this area - "
                "reduce the radius to bring the whole set into the list. "
                if partial else
                "Every record the national dataset holds for this area, nearest "
                "first. "
            )
            + "This is the published history; use Monitor change below for what "
            "has appeared since your last check."
        )

        filter_column, sort_column, search_column = st.columns([1, 1.2, 2])

        with filter_column:
            decision = st.selectbox(
                "Decision",
                options=["All", "GRANTED", "REFUSED", "PENDING", "WITHDRAWN"],
                key="records_decision",
            )

        with sort_column:
            order = st.selectbox(
                "Order by",
                options=list(SORT_ORDERS),
                key="records_sort",
            )

        with search_column:
            query = st.text_input(
                "Search address or description",
                placeholder="e.g. Shannon Weir, extension, retention",
                key="records_query",
            )

        shown = applications
        if decision != "All":
            shown = [a for a in shown if (a.get("decision") or "") == decision]

        if query.strip():
            needle = query.strip().lower()
            shown = [
                a for a in shown
                if needle in (a.get("address") or "").lower()
                or needle in (a.get("description") or "").lower()
                or needle in (a.get("application_ref") or "").lower()
            ]

        if not shown:
            st.markdown(
                "<div class='map-note'>Nothing matches that filter. Clear the "
                "search or choose a different decision.</div>",
                unsafe_allow_html=True,
            )
            return

        # Ordered after filtering, so the page of 12 is the right 12.
        shown = _sort_records(shown, SORT_ORDERS[order])

        st.markdown(
            f"<div class='map-note'>Showing {min(len(shown), PAGE_SIZE)} of "
            f"{len(shown):,} matching record(s).</div>",
            unsafe_allow_html=True,
        )

        for application in shown[:PAGE_SIZE]:
            colour, label = DECISION_STYLE.get(
                application.get("decision") or "", ("#64748b", "No decision published")
            )
            distance = application.get("distance_km")

            with st.container(border=True):
                st.markdown(
                    f"<div class='eyebrow' style='color:{colour}'>{label}"
                    + (f" · {distance:g} km away" if distance is not None else "")
                    + (f" · received {_safe(application.get('date_received'))}"
                       if application.get("date_received") else "")
                    + "</div>"
                    f"<div style='font-weight:650'>"
                    f"{_safe(application.get('application_ref'))}</div>",
                    unsafe_allow_html=True,
                )

                if application.get("address"):
                    st.markdown(
                        f"<div class='map-note'>{_safe(application['address'])}</div>",
                        unsafe_allow_html=True,
                    )

                description = (application.get("description") or "").strip()
                if description:
                    st.write(
                        description if len(description) <= 320
                        else description[:317].rsplit(" ", 1)[0] + "..."
                    )

                link = application.get("link") or ""
                if link.startswith(("http://", "https://")):
                    st.markdown(f"[Open the planning record]({link})")

        if len(shown) > PAGE_SIZE:
            st.caption(
                f"{len(shown) - PAGE_SIZE:,} further record(s) match. Narrow the "
                "search, or reduce the radius, to see them."
            )
