"""Thin planning helpers the agents need beyond planning_data.py.

Ported from plan-permit-compass-streamlit/core — kept minimal.
"""

from __future__ import annotations

import hashlib
import io
import math
from typing import Any

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Spatial candidate matching (ported from core/planning.py)
# ---------------------------------------------------------------------------

COORDINATE_CANDIDATE_M = 35.0
PROXIMATE_CANDIDATE_M = 100.0


def _haversine_m(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    R = 6_371_000.0
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lam = math.radians(lng_b - lng_a)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def site_candidates(records: list[dict[str, Any]], lat: float, lon: float) -> list[dict[str, Any]]:
    """Return nearby records classified as coordinate or proximate candidates."""
    out: list[dict[str, Any]] = []
    for r in records:
        m = _haversine_m(lat, lon, r["lat"], r["lon"])
        if m > PROXIMATE_CANDIDATE_M:
            continue
        relation = "coordinate_candidate" if m <= COORDINATE_CANDIDATE_M else "proximate_candidate"
        out.append({
            "ref": r["application_ref"],
            "distance_m": round(m, 1),
            "relation": relation,
            "decision": r["decision"],
            "description": r["description"],
            "address": r.get("address", ""),
            "application_type": r.get("application_type", ""),
            "date_received": r.get("date_received", ""),
            "date_decided": r.get("date_decided", ""),
            "link": r["link"],
        })
    return sorted(out, key=lambda x: x["distance_m"])


# ---------------------------------------------------------------------------
# Precedent search (ported from core/planning.py)
# ---------------------------------------------------------------------------

def find_precedents(records: list[dict[str, Any]], phrase: str, limit: int = 8) -> list[dict[str, Any]]:
    """Keyword-match records against a construction description."""
    terms = [w.lower() for w in phrase.split() if len(w) > 3]
    if not terms:
        return []

    def score(r: dict[str, Any]) -> int:
        text = f"{r['description']} {r.get('application_type', '')}".lower()
        return sum(t in text for t in terms)

    scored = [(r, score(r)) for r in records]
    scored = [(r, s) for r, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [r for r, _ in scored[:limit]]


# ---------------------------------------------------------------------------
# Evidence extraction (ported from core/evidence.py — HTML + PDF)
# ---------------------------------------------------------------------------

MAX_BYTES = 2_000_000
MAX_TEXT = 24_000


def extract_web_text(url: str) -> dict[str, Any]:
    """Fetch a public URL and return extracted text with provenance."""
    try:
        resp = requests.get(url, timeout=25, headers={"User-Agent": "PlanPerm/1.0"}, stream=True)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "").lower()
        is_html = "html" in ctype
        is_pdf = "pdf" in ctype or url.lower().endswith(".pdf")
        if not is_html and not is_pdf:
            return {"status": "skip", "text": "", "detail": f"Unsupported content type: {ctype}"}

        parts, total = [], 0
        for chunk in resp.iter_content(32_768):
            total += len(chunk)
            if total > MAX_BYTES:
                return {"status": "too_large", "text": "", "detail": "Exceeded 2 MB limit"}
            parts.append(chunk)
        raw = b"".join(parts)

        if is_pdf:
            text = _extract_pdf_text(raw)
        else:
            text = _extract_html_text(raw)
        text = text[:MAX_TEXT]
        if not text:
            return {"status": "empty", "text": "", "detail": "No extractable text"}
        return {"status": "ok", "text": text, "hash": hashlib.sha256(raw).hexdigest()}
    except requests.RequestException as e:
        return {"status": "error", "text": "", "detail": str(e)}


def _extract_html_text(raw: bytes) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    return "\n".join(el.get_text(" ", strip=True) for el in soup.find_all(["h1", "h2", "h3", "p", "li"]) if el.get_text(strip=True))


def _extract_pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    pages, total = [], 0
    for page in reader.pages:
        t = page.extract_text() or ""
        pages.append(t)
        total += len(t)
        if total >= MAX_TEXT:
            break
    return "\n".join(pages)


def extract_pdf_file(path: str) -> str:
    """Extract text from a local PDF file."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages, total = [], 0
    for page in reader.pages:
        t = page.extract_text() or ""
        pages.append(t)
        total += len(t)
        if total >= MAX_TEXT:
            break
    return "\n".join(pages)[:MAX_TEXT]


# ---------------------------------------------------------------------------
# Authority registry + boundary resolution (ported from core/authority_registry.py)
# ---------------------------------------------------------------------------

import json
from functools import lru_cache
from pathlib import Path

_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "authorities.json"

BOUNDARY_SERVICE_URL = (
    "https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services/"
    "National_Statutory_Boundaries_-_Local_Authorities__Ungeneralised_-_2026/"
    "FeatureServer/3"
)


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, Any]:
    return json.loads(_REGISTRY_PATH.read_text())


def authority_names(jurisdiction: str | None = None) -> list[str]:
    return [a["name"] for a in _load_registry()["authorities"]
            if jurisdiction is None or a["jurisdiction"] == jurisdiction]


def authority_by_name(name: str) -> dict[str, Any] | None:
    return next((a for a in _load_registry()["authorities"] if a["name"] == name), None)


def resolve_authority(lat: float, lng: float) -> dict[str, str]:
    """Hit the Tailte Éireann boundary service to identify the planning authority.

    Returns a dict with status, authority name, note, and confidence.
    Works for Republic of Ireland only — NI needs manual selection.
    """
    query_url = f"{BOUNDARY_SERVICE_URL}/query"
    params = {
        "f": "json",
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "ENG_NAME_VALUE,BDY_TYPE_VALUE",
        "returnGeometry": "false",
    }
    try:
        resp = requests.get(query_url, params=params, timeout=20,
                            headers={"User-Agent": "PlanPerm/1.0 authority resolver"})
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except (requests.RequestException, ValueError) as e:
        return {"status": "error", "authority": "", "note": str(e)}

    if not features:
        return {"status": "unresolved", "authority": "",
                "note": "No boundary polygon found for this coordinate."}

    name = features[0].get("attributes", {}).get("ENG_NAME_VALUE", "").title()
    matched = authority_by_name(name)
    if not matched:
        return {"status": "unresolved", "authority": name,
                "note": f"Boundary returned '{name}' but it's not in the registry."}

    return {"status": "resolved", "authority": matched["name"],
            "note": "Official boundary match — verify before submitting.",
            "jurisdiction": matched["jurisdiction"]}


def authority_guidance_url(authority_name: str) -> str | None:
    """Return the application guidance URL for an authority, or None."""
    auth = authority_by_name(authority_name)
    if not auth:
        return None
    guidance = auth.get("sources", {}).get("application_guidance")
    if guidance and guidance.get("url"):
        return guidance["url"]
    return None


def authority_sources(authority_name: str) -> list[dict[str, str]]:
    """Return all verified source links for an authority."""
    auth = authority_by_name(authority_name)
    if not auth:
        return []
    labels = {
        "planning_search": "Planning search",
        "weekly_lists": "Weekly lists",
        "planning_lists": "Planning lists",
        "application_guidance": "Application guidance",
        "ni_planning_portal": "NI Planning Register",
    }
    out = []
    for key, source in auth.get("sources", {}).items():
        if not source:
            continue
        out.append({"id": key, "title": f"{authority_name} — {labels.get(key, key)}",
                     "url": source["url"]})
    return out


# ---------------------------------------------------------------------------
# Preparation checklist (ported from core/requirements.py)
# ---------------------------------------------------------------------------

PREPARATION_STEPS = [
    ("authority_route", "Confirm the application route",
     "Identify the correct route, validation criteria and authority-specific process."),
    ("site_interest", "Confirm site and applicant interest",
     "Prepare site details, land interest and any consent information."),
    ("location_layout", "Prepare location and layout materials",
     "Verify the current scale, format, copies and site-notice requirements."),
    ("proposal_drawings", "Prepare drawings and proposal particulars",
     "Organise the drawings and project particulars needed for review."),
    ("access_environment", "Assess access, services and site constraints",
     "Identify access, drainage, water, heritage, flood and environmental issues."),
    ("lodgement", "Verify lodgement, fees and notices",
     "Check the current portal, fee, notice, and submission requirements."),
]


def preparation_checklist(authority_name: str, has_evidence: bool = False) -> list[dict[str, str]]:
    """Return the 6 preparation steps with status based on available evidence."""
    status = "evidence_retrieved" if has_evidence else "source_link_only"
    return [{"id": sid, "title": title, "guidance": desc, "status": status}
            for sid, title, desc in PREPARATION_STEPS]
