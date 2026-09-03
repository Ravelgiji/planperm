"""Query the ArcGIS Planning Applications Ireland FeatureServer."""

import math
import time
import requests
import json
from datetime import datetime, timezone
from pathlib import Path

from config import ARCGIS_FEATURE_SERVER, ARCGIS_MAX_RECORD_COUNT, CACHE_TTL_HOURS

OUT_FIELDS = [
    "OBJECTID",
    "PlanningAuthority",
    "ApplicationNumber",
    "DevelopmentDescription",
    "DevelopmentAddress",
    "ApplicationType",
    "ApplicationStatus",
    "Decision",
    "ReceivedDate",
    "DecisionDate",
    "AppealRefNumber",
    "AppealStatus",
    "AppealDecision",
    "AppealDecisionDate",
    "LinkAppDetails",
    "NumResidentialUnits",
    "FloorArea",
    "AreaofSite",
]


def _bbox_from_centre(lat: float, lon: float, radius_km: float) -> str:
    """Build a WGS84 bounding box string for ArcGIS geometry filter."""
    lat_offset = radius_km / 111.0
    lon_offset = radius_km / (111.0 * math.cos(math.radians(lat)))
    return f"{lon - lon_offset},{lat - lat_offset},{lon + lon_offset},{lat + lat_offset}"


def _epoch_to_date(epoch_ms) -> str | None:
    """Convert ArcGIS epoch-millisecond timestamp to YYYY-MM-DD."""
    if epoch_ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return None


def _normalise_decision(raw: str) -> str:
    """Map ArcGIS decision values to GRANTED / REFUSED / PENDING / WITHDRAWN."""
    r = raw.upper().strip()
    if not r or r == "N/A":
        return "PENDING"

    # Check refused first — avoids "REFUSE CONDITIONAL" edge case
    if any(k in r for k in ("REFUS", "REJECT", "INVALID")):
        return "REFUSED"
    if any(k in r for k in ("GRANT", "APPROVE", "CONDITIONAL", "UNCONDITIONAL", "SPLIT DECISION")):
        return "GRANTED"
    if any(k in r for k in ("WITHDRAWN", "WITHDRAW")):
        return "WITHDRAWN"
    return "PENDING"


def _parse_feature(feat: dict) -> dict:
    """Convert a single ArcGIS feature to our normalised schema."""
    attrs = feat.get("attributes", {})
    geom = feat.get("geometry", {})

    decision_raw = (attrs.get("Decision") or "").strip()

    return {
        "application_ref": attrs.get("ApplicationNumber") or "",
        "council": attrs.get("PlanningAuthority") or "",
        "address": attrs.get("DevelopmentAddress") or "",
        "lat": geom.get("y"),
        "lon": geom.get("x"),
        "application_type": (attrs.get("ApplicationType") or "").strip(),
        "description": attrs.get("DevelopmentDescription") or "",
        "status": (attrs.get("ApplicationStatus") or "").strip(),
        "date_received": _epoch_to_date(attrs.get("ReceivedDate")),
        "date_decided": _epoch_to_date(attrs.get("DecisionDate")),
        "decision": _normalise_decision(decision_raw),
        "decision_raw": decision_raw,
        "conditions": [],
        "refusal_reasons": [],
        "appeal_ref": attrs.get("AppealRefNumber") or "",
        "appeal_status": (attrs.get("AppealStatus") or "").strip(),
        "appeal_decision": (attrs.get("AppealDecision") or "").strip(),
        "appeal_decision_date": _epoch_to_date(attrs.get("AppealDecisionDate")),
        "link": attrs.get("LinkAppDetails") or "",
        "floor_area": attrs.get("FloorArea"),
        "site_area": attrs.get("AreaofSite"),
        "residential_units": attrs.get("NumResidentialUnits"),
    }


# ── Query with pagination ────────────────────────────────────────────────────

def query_planning_applications(
    lat: float,
    lon: float,
    radius_km: float = 1.0,
    max_total: int = 6000,
) -> list[dict]:
    """Fetch planning applications within radius_km of (lat, lon).

    Paginates through the ArcGIS API (which caps at 2000 per request)
    until all results are fetched or max_total is reached.
    """
    bbox = _bbox_from_centre(lat, lon, radius_km)
    all_apps = []
    offset = 0
    page_size = ARCGIS_MAX_RECORD_COUNT

    while offset < max_total:
        params = {
            "f": "json",
            "geometry": bbox,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": ",".join(OUT_FIELDS),
            "returnGeometry": "true",
            "resultRecordCount": page_size,
            "resultOffset": offset,
            "orderByFields": "OBJECTID ASC",
        }

        try:
            resp = requests.get(ARCGIS_FEATURE_SERVER, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.Timeout:
            # ponytail: retry once on timeout, then give up with what we have
            try:
                resp = requests.get(ARCGIS_FEATURE_SERVER, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError):
                break
        except (requests.RequestException, ValueError):
            break

        if "error" in data:
            break

        features = data.get("features", [])
        if not features:
            break

        for feat in features:
            app = _parse_feature(feat)
            if app["lat"] is not None and app["lon"] is not None:
                all_apps.append(app)

        # If we got fewer than page_size, we've reached the end
        if len(features) < page_size:
            break

        offset += page_size
        # Be polite to the API
        time.sleep(0.2)

    return all_apps


# ── Cache layer with TTL ─────────────────────────────────────────────────────

CACHE_DIR = Path(__file__).parent / "data"

# Cache metadata file stores fetch timestamps
_CACHE_META_FILE = CACHE_DIR / "_cache_meta.json"


def _load_cache_meta() -> dict:
    if _CACHE_META_FILE.exists():
        try:
            return json.loads(_CACHE_META_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache_meta(meta: dict):
    try:
        _CACHE_META_FILE.write_text(json.dumps(meta, indent=2))
    except OSError:
        pass


def _cache_key(area_key: str, radius_km: float) -> str:
    return f"real_{area_key}_{radius_km}km"


def _is_cache_fresh(cache_key: str) -> bool:
    meta = _load_cache_meta()
    fetched_at = meta.get(cache_key)
    if fetched_at is None:
        return False
    age_hours = (time.time() - fetched_at) / 3600
    return age_hours < CACHE_TTL_HOURS


def query_cached(
    area_key: str,
    lat: float,
    lon: float,
    radius_km: float = 1.0,
) -> list[dict]:
    """Query with a file cache keyed on area_key + radius. Respects TTL."""
    CACHE_DIR.mkdir(exist_ok=True)
    key = _cache_key(area_key, radius_km)
    cache_file = CACHE_DIR / f"{key}.json"

    if cache_file.exists() and _is_cache_fresh(key):
        try:
            return json.loads(cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    apps = query_planning_applications(lat, lon, radius_km)
    if apps:
        try:
            cache_file.write_text(json.dumps(apps, indent=2, default=str))
            meta = _load_cache_meta()
            meta[key] = time.time()
            _save_cache_meta(meta)
        except OSError:
            pass

    return apps


def clear_cache():
    """Delete all cached data files."""
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)


if __name__ == "__main__":
    apps = query_planning_applications(53.3244, -6.2634, 1.0)
    print(f"Found {len(apps)} applications near Rathmines")
    if apps:
        a = apps[0]
        print(f"  First: {a['application_ref']} — {a['description'][:60]}... — {a['decision']}")
