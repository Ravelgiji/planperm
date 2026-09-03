"""PlanPerm configuration — API keys, URLs, defaults."""

import os

# ── LLM ──────────────────────────────────────────────────────────────────────
# Set via environment variable or .env file
# Supports OpenAI-compatible APIs (OpenAI, Azure, local proxies)

LLM_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL = os.environ.get("PLANPERM_LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.environ.get("PLANPERM_LLM_BASE_URL", None)  # None = default OpenAI

# ── ArcGIS FeatureServer ─────────────────────────────────────────────────────

ARCGIS_FEATURE_SERVER = (
    "https://services.arcgis.com/NzlPQPKn5QF9v2US/arcgis/rest/services/"
    "Planning_Applications_Ireland_PreProd/FeatureServer/0/query"
)
ARCGIS_MAX_RECORD_COUNT = 2000  # server-side limit per request

# ── Geocoding ────────────────────────────────────────────────────────────────

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "PlanPerm/0.2"

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_RADIUS_KM = 1.0
MAX_RADIUS_KM = 5.0
MIN_RADIUS_KM = 0.25

# ── Cache ────────────────────────────────────────────────────────────────────

CACHE_TTL_HOURS = 24  # re-fetch after this many hours
