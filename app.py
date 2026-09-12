"""PlanPerm — concise Streamlit planning-evidence workspace."""

from __future__ import annotations

import html
import tempfile
from typing import Any

import folium
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

from agents.graph import run as run_planning_graph
from core.env import load_env
from geocoder import resolve_location
from planning_data import area_total, fetch_nearby_applications, summarize_applications
from views.records import render_records
from views.watch import render_watch_control, render_watch_results

# The orchestrator and the agents read OPENAI_API_KEY straight from the
# environment, and nothing was reading the .env file LOCAL_SETUP.md tells you
# to create - so the key never arrived and semantic routing silently degraded
# to the rule-based fallback. Load it before any agent import is used.
load_env()


DEFAULT_SITE = {"lat": 53.2707, "lon": -9.0568, "label": "Galway, Ireland"}
RADIUS_OPTIONS = [0.5, 1.0, 2.0, 3.0, 5.0]
# Markers drawn on the map. The stats use every record; plotting several
# thousand pins would make pan and zoom unusable, so the map shows the
# nearest few and the site line says so.
MAP_MARKER_LIMIT = 150
HERO_IMAGE = "https://files.manuscdn.com/user_upload_by_module/session_file/310519663940374058/eqTMQcrXTmJCYOhP.jpg"
DECISION_COLORS = {
    "GRANTED": "#0f766e",
    "REFUSED": "#b45309",
    "PENDING": "#b7791f",
    "WITHDRAWN": "#64748b",
}


st.set_page_config(page_title="PlanPerm", page_icon="✦", layout="wide", initial_sidebar_state="collapsed")


def apply_theme(dark_mode: bool) -> None:
    mode = """
      :root { --ink:#e8f0ec; --paper:#0d1619; --surface:#142124; --surface-2:#19292d; --composer:#102024; --line:#294047; --green:#33b7a8; --green-deep:#167d73; --muted:#a0b1ae; --soft:#203338; --shadow:#02080977; --urgent:#e08a3c; --urgent-tint:#2a1f16; --urgent-edge:#4d3520; --closing:#d3a04a; --closing-tint:#262017; --closing-edge:#4a3d22; --settled:#94a3b8; --settled-tint:#1b2327; --settled-edge:#33414a; }
      .stApp { background: radial-gradient(circle at 85% 2%, #1c484344, transparent 26rem), linear-gradient(145deg, #0c1417, #101d20); }
      .planperm-hero { background-image: linear-gradient(90deg, #0f1c1fe8 0%, #0f1c1fc7 38%, #0f1c1f1a 74%, #0f1c1f36), url('""" + HERO_IMAGE + """'); }
      [data-testid="stMetric"] { background: linear-gradient(145deg, #19282b, #122024); }
      [data-testid="stExpander"] { background: var(--surface); }
      .site-line { border-color:#28514d; color:#c7ded7; }
      .map-frame iframe { filter: brightness(.78) saturate(.8); }
      [data-testid="stChatMessage"] { background:#18282a; border-color:#2a4545; }
          [data-testid="stTextInput"] input, [data-testid="stChatInput"], [data-testid="stChatInput"] textarea { background:#102024 !important; color:var(--ink) !important; }
      .hero-stat { background:#112629c7; border-color:#38645f; }
    """ if dark_mode else """
      :root { --ink:#12383c; --paper:#f5f4ee; --surface:#fffefb; --surface-2:#f0f5ef; --composer:#fffefb; --line:#dce1da; --green:#0f766e; --green-deep:#0b5c56; --muted:#69746f; --soft:#edf4f0; --shadow:#173c3110; --urgent:#b45309; --urgent-tint:#fdefe4; --urgent-edge:#f0d3bb; --closing:#b7791f; --closing-tint:#fdf6e9; --closing-edge:#ecdcb8; --settled:#64748b; --settled-tint:#f3f3f1; --settled-edge:#dcdedb; }
      .stApp { background: radial-gradient(circle at 82% 4%, #dcebe155, transparent 23rem), var(--paper); }
      .planperm-hero { background-image: linear-gradient(90deg, #f7f4ecf5 0%, #f7f4ecdf 38%, #f7f4ec3d 71%, #f7f4ec00), url('""" + HERO_IMAGE + """'); }
      [data-testid="stMetric"] { background: linear-gradient(145deg, #fffefc, #f7f8f3); }
      .site-line { border-color:#dce9df; color:#31534c; }
      [data-testid="stChatMessage"] { background:#f8fbf8; border-color:#e3e8e1; }
      .hero-stat { background:#fffefbd9; border-color:#e4e9e1; }
    """
    st.markdown(
        """
        <style>
          @import url('https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&display=swap');
          """ + mode + """
          .stApp, .stApp * { font-family: 'DM Sans', sans-serif; }
          /* The rule above uses `*`, which also captures Streamlit's Material
             icon spans — an icon font renders its ligature name as literal
             text, so expander chevrons showed up as "keyboard_arrow_right".
             Hand the icon font back to anything that needs it. */
          [data-testid="stIconMaterial"], span.material-symbols-rounded,
          span.material-symbols-outlined, .stApp [class*="material-symbols"] {
            font-family: 'Material Symbols Rounded', 'Material Symbols Outlined' !important;
          }
          .stApp, .stApp p, .stApp label, .stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stWidgetLabel"] p { color:var(--ink); }
          .stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stWidgetLabel"] p { color:var(--muted) !important; }
          .stApp [data-testid="stTextInput"] input::placeholder, .stApp textarea::placeholder { color:var(--muted) !important; opacity:.9; }
          .stApp [data-testid="stTextInput"] input, .stApp textarea, .stApp [data-testid="stChatInput"] textarea { -webkit-text-fill-color:var(--ink) !important; caret-color:var(--ink) !important; color:var(--ink) !important; }
          .stApp [data-testid="stToggle"] label, .stApp [data-testid="stToggle"] label p { color:var(--ink) !important; font-weight:650; }
          [data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer { visibility: hidden; height: 0; }
          .block-container { max-width: 1510px; padding: 1.2rem 2.5rem 3.5rem; }
          [data-testid="stHorizontalBlock"] { gap: 1.15rem; }
          h1, h2, h3 { color: var(--ink); letter-spacing: -0.035em; }
          [data-testid="stMetric"] { border: 1px solid var(--line); border-radius: 17px; box-shadow: 0 1px 1px var(--shadow), 0 11px 30px var(--shadow); min-height: 100px; padding: 1rem 1.1rem; }
          [data-testid="stMetricLabel"] { color: var(--muted); font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; }
          [data-testid="stMetricValue"] { color: var(--ink); font-family: 'Newsreader', Georgia, serif; font-variant-numeric: tabular-nums; font-weight: 500; }
          [data-testid="stVerticalBlockBorderWrapper"] { background:transparent !important; border:0 !important; border-radius:0 !important; box-shadow:none !important; padding:0 !important; }
          .masthead { align-items:center; display:flex; justify-content:space-between; margin-bottom:.35rem; padding:.15rem 0 .45rem; }
          .brand-lockup { align-items:center; color:var(--ink); display:flex; font-size:1rem; font-weight:700; gap:.55rem; letter-spacing:-.03em; }
          .brand-mark { align-items:center; display:inline-flex; height:1.25rem; width:1.25rem; }
          .brand-mark svg { display:block; height:100%; width:100%; }
          .brand-lockup .brand-mark { color:var(--green); }
          .brand-detail { color:var(--muted); font-size:.68rem; font-weight:600; letter-spacing:.08em; text-transform:uppercase; }
          .theme-label { color:var(--muted); font-size:.68rem; font-weight:700; letter-spacing:.07em; margin-right:.18rem; text-align:right; text-transform:uppercase; }
          .planperm-hero { background-position:center right; background-repeat:no-repeat; background-size:cover; border:1px solid var(--line); border-radius:20px; box-shadow:0 14px 38px var(--shadow); margin:0 0 1.15rem; min-height:134px; overflow:hidden; padding:1.05rem 1.35rem; }
          .planperm-hero .eyebrow { color:var(--green); font-size:.67rem; font-weight:800; letter-spacing:.15em; text-transform:uppercase; }
          .planperm-hero p { color:var(--muted) !important; font-size:.88rem; line-height:1.45; margin:.3rem 0 0; max-width:460px; }
          .hero-metrics { display:flex; gap:.55rem; margin-top:.75rem; max-width:540px; }
          .hero-stat { backdrop-filter:blur(10px); border:1px solid; border-radius:11px; min-width:0; padding:.5rem .7rem; flex:1; }
          .hero-stat span { color:var(--muted); display:block; font-size:.59rem; font-weight:750; letter-spacing:.08em; overflow:hidden; text-overflow:ellipsis; text-transform:uppercase; white-space:nowrap; }
          .hero-stat strong { color:var(--ink); display:block; font-family:'Newsreader', Georgia, serif; font-size:1.46rem; font-variant-numeric:tabular-nums; font-weight:500; line-height:1.1; margin-top:.12rem; }
          .planperm-kicker { color:var(--green); font-size:.67rem; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
          .planperm-note, .map-note { color:var(--muted); font-size:.78rem; line-height:1.45; }
          .map-note { margin:.35rem 0 0; }
          .site-line { align-items:center; border:0 !important; border-bottom:0 !important; border-radius:0; box-shadow:none !important; display:flex; font-size:.76rem; gap:.45rem; margin:0 0 .5rem; outline:0 !important; padding:.18rem 0; }
          .site-line .dot { background:var(--green); border-radius:50%; box-shadow:0 0 0 3px #0f766e22; height:.45rem; width:.45rem; }
          .map-frame { border:0; border-radius:14px; box-shadow:0 8px 24px var(--shadow); overflow:hidden; }
          .map-frame iframe { border:0 !important; display:block; transition:filter 180ms ease; }
          [data-testid="stChatMessage"] { border:1px solid; border-radius:12px; padding:.45rem .55rem; }
          .st-key-assistant_card { background:linear-gradient(160deg, var(--surface), var(--surface-2)); border:1px solid var(--line); border-radius:16px; box-shadow:0 10px 28px var(--shadow); box-sizing:border-box; height:570px; overflow:hidden; padding:1rem; }
          .st-key-assistant_card > [data-testid="stVerticalBlock"] { display:flex; flex-direction:column; height:100%; }
          .assistant-header { align-items:center; display:flex; gap:.65rem; margin-bottom:.72rem; }
          .assistant-glyph { align-items:center; background:var(--green); border-radius:9px; box-shadow:0 4px 10px #0f766e30; color:#fff; display:flex; font-size:.9rem; font-weight:800; height:2rem; justify-content:center; width:2rem; }
          .assistant-title { color:var(--ink); font-size:.88rem; font-weight:750; letter-spacing:-.015em; }
          .assistant-subtitle { color:var(--muted); font-size:.7rem; margin-top:.04rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .assistant-ready { align-items:center; color:var(--muted); display:flex; font-size:.68rem; gap:.35rem; margin:0 0 .8rem; }
          .assistant-ready:before { background:var(--green); border-radius:50%; content:''; display:inline-block; height:.38rem; width:.38rem; }
          .assistant-welcome { background:var(--soft); border:1px solid var(--line); border-radius:11px; color:var(--ink); font-size:.78rem; line-height:1.48; margin:.15rem 0 .85rem; padding:.7rem .75rem; }
          .st-key-assistant_history { flex:1 1 auto; min-height:0; overflow-y:auto; padding-right:.2rem; }
          .st-key-assistant_history::-webkit-scrollbar { width:7px; }
          .st-key-assistant_history::-webkit-scrollbar-thumb { background:var(--line); border-radius:999px; }
          .st-key-assistant_card [data-testid="stChatInput"] { flex:0 0 auto; }
          .st-key-assistant_card [data-testid="stChatInput"], .st-key-assistant_card [data-testid="stChatInput"] div, .st-key-assistant_card [data-testid="stChatInput"] textarea { background:var(--composer) !important; border-color:var(--line) !important; }
          .st-key-assistant_card [data-testid="stChatInput"] button { background:transparent !important; }
          .st-key-assistant_card [data-testid="stChatInput"] { margin-top:.35rem; }
          /* Magnify control. Sits in the card header and toggles the overlay
             below. Styled as a quiet icon button so it reads as chrome rather
             than a primary action. The wrapper is flexed to the right because
             its column is proportional: at the overlay's 1080px the column is
             ~180px wide, so a left-aligned 2rem button floats well short of
             the edge. */
          .st-key-assistant_magnify { display:flex !important; justify-content:flex-end !important; }
          .st-key-assistant_magnify button { align-items:center; background:transparent !important; border:1px solid var(--line) !important; border-radius:9px !important; color:var(--muted) !important; display:flex !important; flex:0 0 auto !important; font-size:.92rem !important; height:2rem !important; justify-content:center; min-height:2rem !important; padding:0 !important; transition:background 140ms ease, color 140ms ease, transform 140ms ease; width:2rem !important; }
          .st-key-assistant_magnify button:hover { background:var(--soft) !important; color:var(--ink) !important; transform:none !important; }
          .st-key-assistant_magnify button p { font-size:.92rem !important; line-height:1 !important; margin:0 !important; }
          @keyframes assistantZoomIn { from { opacity:0; transform:translate(-50%,-50%) scale(.965); } to { opacity:1; transform:translate(-50%,-50%) scale(1); } }
          @keyframes assistantFadeIn { from { opacity:0; } to { opacity:1; } }
          .section-rule { background:var(--line); height:1px; margin:1rem 0; width:100%; }
          .deadline { align-items:baseline; border-radius:9px; display:flex; flex-wrap:wrap; font-size:.82rem; gap:.4rem; margin:.35rem 0 0; padding:.42rem .6rem; }
          .deadline .when { font-family:'Newsreader', Georgia, serif; font-size:.95rem; font-variant-numeric:tabular-nums; font-weight:500; }
          .deadline-open { background:var(--soft); border:1px solid var(--line); border-left:3px solid var(--green); color:var(--green); }
          .deadline-closing { background:var(--closing-tint); border:1px solid var(--closing-edge); border-left:3px solid var(--closing); color:var(--closing); }
          .deadline-urgent { background:var(--urgent-tint); border:1px solid var(--urgent-edge); border-left:3px solid var(--urgent); color:var(--urgent); }
          .deadline-closed { background:var(--settled-tint); border:1px solid var(--settled-edge); border-left:3px solid var(--settled); color:var(--settled); }
          .estimate-badge { align-items:center; background:var(--settled-tint); border:1px solid var(--settled-edge); border-radius:6px; color:var(--settled); display:inline-flex; font-size:.6rem; font-weight:700; gap:.25rem; letter-spacing:.03em; padding:.1rem .35rem; text-transform:uppercase; vertical-align:middle; }
          [data-testid="stChatInput"] { border-color:var(--line); border-radius:11px; }
          .stButton > button, [data-testid="stFormSubmitButton"] > button { border-color:var(--line); border-radius:10px; color:var(--ink); font-weight:650; transition:transform 140ms ease, box-shadow 140ms ease; }
          .stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover { box-shadow:0 5px 12px var(--shadow); transform:translateY(-1px); }
          .stButton > button:active, [data-testid="stFormSubmitButton"] > button:active { transform:scale(.98); }
          /* Primary buttons. The label lives inside a stMarkdownContainer, and
             the global rule above paints that element var(--ink) — which beat
             this rule's `color` and left dark text on dark teal. Re-assert the
             colour on the inner element, and cover the newer stBaseButton
             testids as well as the older kind="primary" attribute. */
          .stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button[kind="primary"],
          [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
            background:var(--green-deep) !important; border-color:var(--green-deep) !important; color:#fff !important;
          }
          .stButton > button[kind="primary"] *, [data-testid="stFormSubmitButton"] > button[kind="primary"] *,
          [data-testid="stBaseButton-primary"] *, [data-testid="stBaseButton-primaryFormSubmit"] * {
            color:#fff !important; -webkit-text-fill-color:#fff !important;
          }
          .stButton > button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover,
          [data-testid="stBaseButton-primaryFormSubmit"]:hover {
            background:var(--green) !important; border-color:var(--green) !important;
          }
          [data-testid="stSelectbox"] > div, [data-testid="stTextInput"] input { border-radius:10px; }
          [data-testid="stExpander"] { border:1px solid var(--line); border-radius:13px; }
          @media (max-width:700px) { .block-container { padding:1rem .85rem 2.2rem; } .masthead { align-items:flex-start; } .planperm-hero { border-radius:16px; min-height:168px; padding:1.1rem; } .brand-detail { display:none; } .hero-metrics { gap:.38rem; } .hero-stat { padding:.46rem .5rem; } .hero-stat strong { font-size:1.22rem; } .st-key-assistant_card { height:500px; margin-top:.25rem; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


# Applied only while the assistant is magnified. Injected conditionally rather
# than toggled via a body class, because Streamlit gives us no hook to set one.
# The card is lifted out of its narrow column and centred over the workspace,
# which is what makes long advisor replies readable - the column is ~23% wide,
# so a 600-word answer wraps into a very tall ribbon.
ASSISTANT_EXPANDED_CSS = """
<style>
  .assistant-backdrop { background:rgba(8,20,22,.46); inset:0; position:fixed; z-index:999; }
  .st-key-assistant_card {
    height:min(880px, 88vh) !important;
    left:50%; top:50%;
    position:fixed !important;
    transform:translate(-50%,-50%);
    width:min(1080px, 94vw) !important;
    z-index:1000;
    box-shadow:0 30px 80px rgba(0,0,0,.34) !important;
  }
  /* The history pane carries an inline height from st.container(height=...).
     Release it so the flex column decides, letting the pane absorb the extra
     vertical space instead of scrolling inside a short box. */
  .st-key-assistant_history { height:auto !important; max-height:none !important; }
  /* Long-form answers only. Caps the measure so text does not run the full
     1080px, which is past comfortable reading length. */
  .st-key-assistant_card [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] { max-width:78ch; }
</style>
"""

# Zoom/fade is emitted only on the rerun that follows a toggle. Injecting it on
# every rerun would replay the animation each time a chat message arrives.
ASSISTANT_ANIMATION_CSS = """
<style>
  .assistant-backdrop { animation:assistantFadeIn 200ms ease; }
  .st-key-assistant_card { animation:assistantZoomIn 240ms cubic-bezier(.4,0,.2,1); }
  @media (prefers-reduced-motion: reduce) {
    .assistant-backdrop, .st-key-assistant_card { animation:none !important; }
  }
</style>
"""


def toggle_assistant_size() -> None:
    st.session_state.assistant_expanded = not st.session_state.get("assistant_expanded", False)
    st.session_state.assistant_animate = True


def ensure_state() -> None:
    if "site" not in st.session_state:
        st.session_state.site = DEFAULT_SITE.copy()
    if "dark_mode" not in st.session_state:
        st.session_state.dark_mode = st.query_params.get("theme") == "dark"
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "search_error" not in st.session_state:
        st.session_state.search_error = ""
    if "workspace_id" not in st.session_state:
        st.session_state.workspace_id = None
    if "assistant_expanded" not in st.session_state:
        st.session_state.assistant_expanded = False


def set_site(lat: float, lon: float, label: str) -> None:
    current = st.session_state.site
    has_changed = abs(current["lat"] - lat) > 0.00005 or abs(current["lon"] - lon) > 0.00005
    st.session_state.site = {"lat": lat, "lon": lon, "label": label}
    if has_changed:
        st.session_state.messages = []
        st.session_state.pop("agent_cache", None)
        st.session_state.pop("last_draft_brief", None)


def safe_text(value: Any) -> str:
    return html.escape(str(value or "—"))


def strip_route_prefix(message: dict[str, Any]) -> str:
    """An assistant turn without its "Routed to: ..." banner.

    That banner is UI chrome showing which specialist answered. Feeding it back
    as conversation would teach the model to imitate it.
    """
    content = str(message.get("content") or "")
    if message.get("role") != "assistant" or not content.startswith("**Routed to:"):
        return content
    _, _, rest = content.partition("\n\n")
    return rest or content


def save_uploaded_draft(uploaded_file: Any) -> str | None:
    """Write an uploaded PDF to a temp file so the draft agent can read it."""
    if uploaded_file is None:
        st.session_state.pop("draft_pdf_path", None)
        st.session_state.pop("draft_pdf_signature", None)
        return None
    signature = f"{uploaded_file.name}:{uploaded_file.size}"
    if st.session_state.get("draft_pdf_signature") != signature:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            st.session_state.draft_pdf_path = tmp.name
        st.session_state.draft_pdf_signature = signature
    return str(st.session_state.get("draft_pdf_path") or "") or None


def application_popup(application: dict[str, Any]) -> str:
    source_url = str(application.get("link") or "")
    if not source_url.startswith(("https://", "http://")):
        source_url = "https://services.arcgis.com/NzlPQPKn5QF9v2US/arcgis/rest/services/Planning_Applications_Ireland_PreProd/FeatureServer/0"

    description = str(application.get("description") or "Planning application details are available in the original record.")
    short_description = safe_text(description[:260] + ("…" if len(description) > 260 else ""))
    return f"""
      <div style="font-family:Arial,sans-serif;min-width:268px;max-width:320px;color:#173d3d;line-height:1.42;padding:2px">
        <div style="align-items:center;border-bottom:1px solid #dbe6e1;display:flex;justify-content:space-between;padding-bottom:9px">
          <span style="background:{DECISION_COLORS.get(application['decision'], '#64748b')};border-radius:999px;color:#fff;font-size:10px;font-weight:700;letter-spacing:.07em;padding:4px 7px">{safe_text(application['decision'])}</span>
          <span style="color:#71827c;font-size:10px;font-weight:700;letter-spacing:.06em">PLANNING RECORD</span>
        </div>
        <div style="font-size:18px;font-weight:700;letter-spacing:-.02em;margin:10px 0 4px">{safe_text(application['application_ref'])}</div>
        <div style="color:#36544e;font-size:12px;font-weight:600;margin-bottom:10px">{safe_text(application['address'])}</div>
        <div style="background:#f1f6f3;border-radius:8px;color:#516660;font-size:11px;margin-bottom:10px;padding:7px 8px"><strong style="color:#30534a">{safe_text(application['application_type'])}</strong><br>Received {safe_text(application['date_received'])}</div>
        <div style="color:#526761;font-size:12px;margin-bottom:12px">{short_description}</div>
        <a href="{html.escape(source_url, quote=True)}" target="_blank" rel="noopener noreferrer" style="align-items:center;background:#0f766e;border-radius:8px;color:#fff;display:flex;font-size:12px;font-weight:700;justify-content:center;padding:9px 10px;text-decoration:none">View original planning record ↗</a>
      </div>
    """


def build_map(site: dict[str, Any], applications: list[dict[str, Any]], radius_km: float) -> folium.Map:
    planning_map = folium.Map(
        location=[site["lat"], site["lon"]],
        zoom_start=13 if radius_km >= 2 else 14,
        tiles="OpenStreetMap",
        control_scale=True,
        prefer_canvas=True,
        doubleClickZoom=False,
        scrollWheelZoom=True,
    )
    Draw(
        position="topleft",
        draw_options={
            "polyline": False,
            "polygon": False,
            "rectangle": False,
            "circle": False,
            "circlemarker": False,
            "marker": {"repeatMode": False},
        },
        edit_options={"edit": False, "remove": False},
    ).add_to(planning_map)
    folium.Circle(
        location=[site["lat"], site["lon"]],
        radius=radius_km * 1000,
        color="#0f766e",
        fill=True,
        fill_opacity=0.04,
        weight=2,
    ).add_to(planning_map)
    folium.Marker(
        location=[site["lat"], site["lon"]],
        tooltip="Selected site",
        icon=folium.Icon(color="darkgreen", icon="home", prefix="fa"),
    ).add_to(planning_map)

    for application in applications[:MAP_MARKER_LIMIT]:
        decision = application["decision"]
        folium.CircleMarker(
            location=[application["lat"], application["lon"]],
            radius=6,
            color="#ffffff",
            weight=1.5,
            fill=True,
            fill_color=DECISION_COLORS.get(decision, "#64748b"),
            fill_opacity=0.96,
            tooltip=f"{decision}: {application['application_ref']}",
            popup=folium.Popup(application_popup(application), max_width=360),
        ).add_to(planning_map)
    return planning_map


def assistant_panel(site: dict[str, Any], applications: list[dict[str, Any]], radius_km: float) -> None:
    site_label = str(site["label"])
    expanded = bool(st.session_state.get("assistant_expanded", False))
    header_column, action_column = st.columns([1, 0.2], vertical_alignment="center")
    with header_column:
        st.markdown(
            f"""
            <div class='assistant-anchor'></div>
            <div class='assistant-header'>
              <div class='assistant-glyph'>✦</div>
              <div><div class='assistant-title'>PlanPerm assistant</div><div class='assistant-subtitle'>{safe_text(site_label)}</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with action_column:
        st.button(
            "⤡" if expanded else "⤢",
            key="assistant_magnify",
            help="Shrink back into the column" if expanded else "Magnify for long replies",
            on_click=toggle_assistant_size,
        )
    st.markdown(
        "<div class='assistant-ready'>Planning context ready</div>",
        unsafe_allow_html=True,
    )
    uploaded_draft = st.file_uploader(
        "Draft PDF (optional — enables document review)",
        type=["pdf"],
        key="draft_pdf_upload",
    )
    pdf_path = save_uploaded_draft(uploaded_draft)
    history = st.container(height=560 if expanded else 330, border=False, key="assistant_history")
    with history:
        if not st.session_state.messages:
            # Summary card showing loaded context
            summary = summarize_applications(applications)
            rate_text = f"{summary['approval_rate']}% approval" if summary.get("approval_rate") is not None else "no decisions yet"
            st.markdown(
                f"<div class='assistant-welcome'>"
                f"📍 <strong>{safe_text(site_label)}</strong> · {summary['total']} records · {rate_text}<br>"
                f"<span style='font-size:.82em;opacity:.8'>Ask about your site, nearby decisions, what you need to prepare, or upload a draft PDF for review.</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.write(message["content"])

    if prompt := (st.session_state.pop("prefill_question", None) or st.chat_input("Ask PlanPerm", key="assistant_prompt")):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with history:
            with st.chat_message("user"):
                st.write(prompt)

        # Intercept greetings and small talk — no need to call the agent
        from agents.guardrails import is_small_talk, GREETING_RESPONSE
        if is_small_talk(prompt):
            content = GREETING_RESPONSE
            st.session_state.messages.append({"role": "assistant", "content": content})
            with history:
                with st.chat_message("assistant"):
                    st.write(content)
        else:
            try:
                # Pass cached context so follow-ups skip expensive HTTP calls
                cached = st.session_state.get("agent_cache", {})
                with st.spinner("Thinking..."):
                    result = run_planning_graph(
                        question=prompt,
                        lat=float(site["lat"]),
                        lng=float(site["lon"]),
                        radius_km=radius_km,
                        site_label=site_label,
                        records=applications or cached.get("records"),
                        pdf_path=pdf_path,
                        workspace_id=st.session_state.get("workspace_id") or "",
                        # Carry forward resolved context from previous turns
                        authority=cached.get("authority", ""),
                        jurisdiction=cached.get("jurisdiction", ""),
                        authority_resolution=cached.get("authority_resolution", {}),
                        evidence_text=cached.get("evidence_text", ""),
                        chat_history=[
                            {"role": message["role"], "content": strip_route_prefix(message)}
                            for message in st.session_state.messages[-8:-1]
                        ],
                    )

                # Cache the resolved context for next turn
                st.session_state["agent_cache"] = {
                    k: result[k] for k in ("authority", "jurisdiction", "authority_resolution",
                                             "evidence_text", "records", "summary", "sources",
                                             "candidates", "precedents", "checklist")
                    if k in result
                }

                errors = result.get("errors", [])
                if errors:
                    if any("rate limit" in str(e).lower() or "429" in str(e) for e in errors):
                        answer = "⚠️ The AI service is temporarily rate-limited. The planning data was still retrieved — here's what we found:\n\n" + (result.get("response") or result.get("advice") or "Please try again in a moment.")
                    else:
                        answer = result.get("response") or result.get("advice") or "The advisor encountered an issue. The planning data may still be useful — check the map for nearby records."
                else:
                    answer = result.get("response", result.get("advice", "No response returned."))

                # Clean label instead of debug routing header
                decision = result.get("orchestrator", {})
                route = decision.get("route", "advisor")
                route_labels = {"advisor": "📋 Advisor", "draft": "📄 Draft Review", "watch": "👁 Watch", "clarify": "❓ Clarify", "coordinator": "🔗 Coordinator"}
                label = route_labels.get(route, route)
                content = f"*{label}*\n\n{answer}"

                draft_brief = result.get("draft_brief", "")
                if draft_brief:
                    st.session_state["last_draft_brief"] = draft_brief
                else:
                    # Don't carry forward the brief from a previous question
                    st.session_state.pop("last_draft_brief", None)

            except Exception as exc:
                content = f"⚠️ Something went wrong while processing your question. Please try again.\n\n*Detail: {type(exc).__name__}*"

            st.session_state.messages.append({"role": "assistant", "content": content})
            with history:
                with st.chat_message("assistant"):
                    st.write(content)

    if st.session_state.get("last_draft_brief"):
        from agents.pdf_brief import brief_to_pdf
        pdf_bytes = brief_to_pdf(st.session_state["last_draft_brief"])
        st.download_button(
            "⬇ Download preparation brief (PDF)",
            data=pdf_bytes,
            file_name="planperm_preparation_brief.pdf",
            mime="application/pdf",
        )


ensure_state()
masthead_brand, masthead_theme = st.columns([5.7, 0.8], vertical_alignment="center")
with masthead_brand:
    st.markdown("<div class='masthead'><div class='brand-lockup'><span class='brand-mark'><svg viewBox='0 0 34 34' fill='none' aria-hidden='true'><rect x='3.5' y='3.5' width='12' height='12' rx='2' stroke='currentColor' stroke-width='2.4'/><rect x='18.5' y='3.5' width='12' height='12' rx='2' stroke='currentColor' stroke-width='2.4' opacity='0.32'/><rect x='3.5' y='18.5' width='12' height='12' rx='2' stroke='currentColor' stroke-width='2.4' opacity='0.32'/><rect x='18.5' y='18.5' width='12' height='12' rx='2' fill='currentColor'/></svg></span><span>planperm</span><span class='brand-detail'>Planning intelligence</span></div></div>", unsafe_allow_html=True)
with masthead_theme:
    current_dark_mode = bool(st.session_state.get("dark_mode", False))
    theme_label_column, theme_toggle_column = st.columns([1.55, 1], vertical_alignment="center")
    with theme_label_column:
        st.markdown(
            f"<div class='theme-label'>{'Light' if current_dark_mode else 'Dark'}</div>",
            unsafe_allow_html=True,
        )
    with theme_toggle_column:
        dark_mode = st.toggle("Theme switch", key="dark_mode", help="Switch workspace theme", label_visibility="collapsed")
apply_theme(dark_mode)

site = st.session_state.site
if "radius_km" not in st.session_state:
    st.session_state.radius_km = 2.0
radius_km = float(st.session_state.radius_km)
with st.spinner("Loading nearby applications..."):
    try:
        applications = fetch_nearby_applications(site["lat"], site["lon"], radius_km)
        fetch_error = ""
    except Exception as error:
        applications = []
        fetch_error = f"Planning data is temporarily unavailable. The map will show your site without records. ({type(error).__name__})"
summary = summarize_applications(applications)
approval_metric = f"{summary['approval_rate']}%" if summary["approval_rate"] is not None else "—"

# The fetch stops at a record cap. Where it did, the real total comes from a
# server-side count of the circle - otherwise the headline would quietly be the
# cap. The decision counts still come from the records actually fetched, so
# they are a sample when the list is incomplete, and labelled as one.
if applications:
    area_count, list_complete = area_total(site["lat"], site["lon"], radius_km)
else:
    area_count, list_complete = 0, True

total_metric = f"{area_count:,}"
refused_metric = (
    f"{summary['refused']:,}" if list_complete else f"{summary['refused']:,}+"
)

st.markdown(
    f"""
    <section class="planperm-hero">
      <div class="eyebrow">Local planning intelligence</div>
      <p>Live planning decisions and source records around your selected site.</p>
      <div class="hero-metrics">
        <div class="hero-stat"><span>Applications</span><strong>{total_metric}</strong></div>
        <div class="hero-stat"><span>Approval rate</span><strong>{approval_metric}</strong></div>
        <div class="hero-stat"><span>Refused</span><strong>{refused_metric}</strong></div>
      </div>
    </section>
    """,
    unsafe_allow_html=True,
)

controls_column, map_column, assistant_column = st.columns([1.15, 3.25, 1.35], gap="medium")

with controls_column:
    with st.container(border=False):
        st.markdown("<div class='planperm-kicker'>Search</div>", unsafe_allow_html=True)
        with st.form("site_search", border=False):
            search_query = st.text_input("Location", placeholder="Town or address", label_visibility="collapsed")
            find_site = st.form_submit_button("Find site", type="primary", use_container_width=True)
        if find_site:
            try:
                coordinates = resolve_location(search_query) if search_query.strip() else None
            except Exception:
                coordinates = None
            if coordinates is None:
                st.session_state.search_error = "Location not found. Try a town or address."
            else:
                set_site(coordinates[0], coordinates[1], search_query.strip())
                st.session_state.search_error = ""
                st.rerun()
        if st.session_state.search_error:
            st.error(st.session_state.search_error)
        st.markdown("<div class='map-note'>Use the pin tool on the map to select a site. Pan and zoom without changing the planning record.</div>", unsafe_allow_html=True)

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    with st.container(border=False):
        st.markdown("<div class='planperm-kicker'>Radius</div>", unsafe_allow_html=True)
        st.select_slider(
            "Search radius",
            options=RADIUS_OPTIONS,
            key="radius_km",
            format_func=lambda value: f"{value:g} km",
            label_visibility="collapsed",
        )

    st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
    with st.container(border=False):
        render_watch_control(site, radius_km)

    # Quick-ask about a specific record from the map
    if applications:
        st.markdown("<div class='section-rule'></div>", unsafe_allow_html=True)
        with st.container(border=False):
            st.markdown("<div class='planperm-kicker'>Ask about a record</div>", unsafe_allow_html=True)
            record_options = [f"{a['application_ref']} — {a['decision']} — {a.get('address', '')[:40]}" for a in applications[:20]]
            selected_record = st.selectbox("Select a record", record_options, label_visibility="collapsed", key="record_select")
            if st.button("Ask the advisor about this", use_container_width=True):
                ref = selected_record.split(" — ")[0]
                record = next((a for a in applications if a["application_ref"] == ref), None)
                if record:
                    question = f"Tell me about planning record {ref} at {record.get('address', 'this location')}. It was {record['decision']}. How does it compare to what I might propose here?"
                    st.session_state["prefill_question"] = question
                    st.rerun()

with map_column:
    st.markdown(
        f"<div class='site-line'><span class='dot'></span>"
        f"<span>{safe_text(site['label'])}</span><span>·</span>"
        f"<span>{radius_km:g} km radius</span><span>·</span>"
        f"<span>{area_count:,} records</span>"
        + (
            f"<span>·</span><span>list holds nearest {len(applications):,}</span>"
            if not list_complete else ""
        )
        + (
            f"<span>·</span><span>map shows nearest {min(len(applications), MAP_MARKER_LIMIT)}</span>"
            if len(applications) > MAP_MARKER_LIMIT else ""
        )
        + "</div>",
        unsafe_allow_html=True,
    )
    if fetch_error:
        st.warning(fetch_error)
    planning_map = build_map(site, applications, radius_km)
    map_key = f"map-{site['lat']:.5f}-{site['lon']:.5f}-{radius_km}"
    st.markdown("<div class='map-frame'>", unsafe_allow_html=True)
    map_state = st_folium(
        planning_map,
        height=570,
        use_container_width=True,
        key=map_key,
        returned_objects=["last_active_drawing"],
    )
    st.markdown("</div>", unsafe_allow_html=True)
    selected_drawing = map_state.get("last_active_drawing") if map_state else None
    geometry = selected_drawing.get("geometry", {}) if isinstance(selected_drawing, dict) else {}
    coordinates = geometry.get("coordinates", []) if isinstance(geometry, dict) else []
    if geometry.get("type") == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
        clicked_lon, clicked_lat = float(coordinates[0]), float(coordinates[1])
        if abs(site["lat"] - clicked_lat) > 0.00005 or abs(site["lon"] - clicked_lon) > 0.00005:
            set_site(clicked_lat, clicked_lon, f"Pinned site · {clicked_lat:.5f}, {clicked_lon:.5f}")
            st.rerun()

with assistant_column:
    # Both must sit outside the card: the backdrop needs to paint beneath it,
    # and a child element cannot render below its own parent's background.
    if st.session_state.get("assistant_expanded"):
        animation = ASSISTANT_ANIMATION_CSS if st.session_state.pop("assistant_animate", False) else ""
        st.markdown(
            ASSISTANT_EXPANDED_CSS + animation + "<div class='assistant-backdrop'></div>",
            unsafe_allow_html=True,
        )
    else:
        st.session_state.pop("assistant_animate", None)
    with st.container(border=True, key="assistant_card"):
        assistant_panel(site, applications, radius_km)

# Below the map, full width. The records list answers "what is here?" and the
# watch findings answer "what is new?" - in that order, because the first is
# what someone asks first.
render_records(applications, str(site["label"]))
render_watch_results(site)
