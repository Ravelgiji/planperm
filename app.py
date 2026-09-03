"""PlanPerm — Planning Permission Intelligence Agent."""

import streamlit as st
import pandas as pd
import pydeck as pdk

from planning_api import query_cached
from geocoder import resolve_location
from analysis import (
    compute_stats,
    format_stats_text,
    compute_timeline_stats,
    compute_appeal_stats,
)
from agent import run_agent
from config import LLM_API_KEY, DEFAULT_RADIUS_KM, MIN_RADIUS_KM, MAX_RADIUS_KM

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="PlanPerm", page_icon="📍", layout="wide")

# ── Custom CSS ───────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .disclaimer-bar {
        background-color: #fef2f2;
        border: 1px solid #ef4444;
        border-radius: 6px;
        padding: 8px 12px;
        color: #991b1b;
        font-size: 0.85em;
        font-weight: 500;
        text-align: center;
        margin-top: 8px;
    }
    .detail-card {
        background-color: #1e293b;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .stat-metric {
        text-align: center;
        padding: 8px;
    }
    .stat-metric .value {
        font-size: 1.8em;
        font-weight: 700;
    }
    .stat-metric .label {
        font-size: 0.8em;
        opacity: 0.7;
    }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("📍 PlanPerm")
st.sidebar.caption("Planning Permission Intelligence Agent")

location_input = st.sidebar.text_input(
    "Enter a location",
    placeholder="e.g. Rathmines, Athlone, Cork",
)

radius_km = st.sidebar.slider(
    "Search radius (km)",
    MIN_RADIUS_KM, MAX_RADIUS_KM, DEFAULT_RADIUS_KM, 0.25,
)

# Sidebar info
st.sidebar.markdown("---")
if LLM_API_KEY:
    st.sidebar.success("🤖 AI agent active", icon="✅")
else:
    st.sidebar.warning("🤖 No API key — using basic mode", icon="⚠️")
    st.sidebar.caption("Set `OPENAI_API_KEY` env var to enable the AI agent.")

st.sidebar.markdown("---")
st.sidebar.markdown(
    '<div class="disclaimer-bar">⚠️ Informational only. Not legal advice. '
    'Consult a planning professional before submitting.</div>',
    unsafe_allow_html=True,
)

# ── Landing page ─────────────────────────────────────────────────────────────

if not location_input:
    st.title("📍 PlanPerm")
    st.markdown(
        "Enter any location in Ireland to see planning permission patterns, "
        "approval rates, and get AI-powered advice on your development plans.\n\n"
        "**Try:** Rathmines · Athlone · Swords · Cork · Galway · Killarney"
    )
    st.stop()

# ── Resolve location ─────────────────────────────────────────────────────────

coords = resolve_location(location_input)

if coords is None:
    st.error(f"Could not find location: '{location_input}'. Try a town or area name in Ireland.")
    st.stop()

centre_lat, centre_lon = coords

# ── Load data ────────────────────────────────────────────────────────────────

area_key = location_input.strip().lower().replace(" ", "_")

with st.spinner("Fetching planning data from MyPlan.ie..."):
    apps = query_cached(area_key, centre_lat, centre_lon, radius_km)

if not apps:
    st.warning(
        "No planning applications found in this radius. "
        "Try increasing the search radius or a different location."
    )
    st.stop()

# ── Compute stats ────────────────────────────────────────────────────────────

stats = compute_stats(apps)
timeline = compute_timeline_stats(apps)
appeals = compute_appeal_stats(apps)

# ── Header ───────────────────────────────────────────────────────────────────

st.title(f"📍 Planning in {location_input.title()}")
st.caption(f"Data source: MyPlan.ie (ArcGIS) · {len(apps)} applications · {radius_km}km radius")

# ── Key metrics row ──────────────────────────────────────────────────────────

m1, m2, m3, m4 = st.columns(4)
m1.metric("Applications", stats["total"])
m2.metric("Approval Rate", f"{stats['approval_rate']}%" if stats["approval_rate"] is not None else "N/A")
m3.metric("Avg Decision Time", f"{timeline['avg_days']} days" if timeline and timeline.get("avg_days") else "N/A")
m4.metric("Appeals Success", f"{appeals['appeal_success_rate']}%" if appeals and appeals.get("appeals_filed", 0) > 0 else "N/A")

# ── Map + Stats columns ─────────────────────────────────────────────────────

col_map, col_stats = st.columns([3, 2])

# ── Map ──────────────────────────────────────────────────────────────────────

with col_map:
    df = pd.DataFrame(apps)

    colour_map = {
        "GRANTED": [34, 197, 94, 180],
        "REFUSED": [239, 68, 68, 180],
        "PENDING": [234, 179, 8, 180],
        "WITHDRAWN": [148, 163, 184, 180],
    }
    default_colour = [148, 163, 184, 180]
    df["colour"] = df["decision"].map(lambda d: colour_map.get(d, default_colour))

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df,
        get_position=["lon", "lat"],
        get_fill_color="colour",
        get_radius=30,
        pickable=True,
        auto_highlight=True,
    )

    view = pdk.ViewState(
        latitude=centre_lat,
        longitude=centre_lon,
        zoom=14,
        pitch=0,
    )

    tooltip = {
        "html": (
            "<b>{application_ref}</b><br/>"
            "{address}<br/>"
            "<i>{description}</i><br/>"
            "Decision: <b>{decision}</b><br/>"
            "Type: {application_type}<br/>"
            "Received: {date_received}<br/>"
            "Decided: {date_decided}"
        ),
        "style": {"backgroundColor": "#1e293b", "color": "white", "fontSize": "12px"},
    }

    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view, tooltip=tooltip))
    st.markdown("🟢 Granted &nbsp;&nbsp; 🔴 Refused &nbsp;&nbsp; 🟡 Pending &nbsp;&nbsp; ⚪ Withdrawn")

# ── Stats panel ──────────────────────────────────────────────────────────────

with col_stats:
    st.subheader("Area Statistics")
    st.markdown(format_stats_text(stats))

    # Timeline trend
    if timeline and timeline.get("by_year"):
        st.subheader("Approval Trend")
        year_data = pd.DataFrame([
            {"Year": str(y), "Approval Rate %": r}
            for y, r in sorted(timeline["by_year"].items())
        ])
        if not year_data.empty:
            st.bar_chart(year_data.set_index("Year"))

        if timeline.get("trend"):
            st.caption(timeline["trend"])

    # Appeal stats
    if appeals and appeals.get("appeals_filed", 0) > 0:
        st.subheader("Appeals")
        st.markdown(
            f"Of **{appeals['total_refused']}** refusals, "
            f"**{appeals['appeals_filed']}** were appealed ({appeals['appeal_rate']}%). "
            f"**{appeals['appeals_granted']}** appeals succeeded ({appeals['appeal_success_rate']}%)."
        )

# ── Application detail expander ──────────────────────────────────────────────

st.markdown("---")

with st.expander("📋 Browse all applications", expanded=False):
    display_df = pd.DataFrame(apps)

    # Select and rename columns for display
    display_cols = {
        "application_ref": "Ref",
        "address": "Address",
        "description": "Description",
        "application_type": "Type",
        "decision": "Decision",
        "date_received": "Received",
        "date_decided": "Decided",
        "council": "Council",
    }
    available = [c for c in display_cols if c in display_df.columns]
    show_df = display_df[available].rename(columns=display_cols)

    # Decision filter
    decision_filter = st.multiselect(
        "Filter by decision",
        options=["GRANTED", "REFUSED", "PENDING", "WITHDRAWN"],
        default=["GRANTED", "REFUSED", "PENDING"],
    )
    if decision_filter:
        show_df = show_df[show_df["Decision"].isin(decision_filter)]

    st.dataframe(show_df, use_container_width=True, height=400)

    # Source links
    linked = [a for a in apps if a.get("link")]
    if linked:
        st.caption(f"{len(linked)} applications have direct links to council records.")

# ── Chat interface ───────────────────────────────────────────────────────────

st.markdown("---")
st.subheader("💬 Ask about this area")

if LLM_API_KEY:
    st.caption("AI agent powered by LLM with function-calling — answers are grounded in the real data above.")
else:
    st.caption("Basic mode — set OPENAI_API_KEY for smarter answers.")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("e.g. What are my chances for a rear extension?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            # Build chat history for context (user/assistant pairs only)
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages[:-1]  # exclude the just-added user msg
            ]
            response = run_agent(prompt, apps, stats, chat_history=history)

        st.markdown(response)

    st.session_state.messages.append({"role": "assistant", "content": response})
