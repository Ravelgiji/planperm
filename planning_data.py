"""Small, source-linked ArcGIS adapter for the Streamlit PlanPerm workspace."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import requests


ARCGIS_URL = "https://services.arcgis.com/NzlPQPKn5QF9v2US/arcgis/rest/services/Planning_Applications_Ireland_PreProd/FeatureServer/0/query"
ARCGIS_LAYER_URL = "https://services.arcgis.com/NzlPQPKn5QF9v2US/arcgis/rest/services/Planning_Applications_Ireland_PreProd/FeatureServer/0"
OUT_FIELDS = [
    "OBJECTID",
    "PlanningAuthority",
    "ApplicationNumber",
    "DevelopmentDescription",
    "DevelopmentAddress",
    "ApplicationType",
    "Decision",
    "ReceivedDate",
    "DecisionDate",
    "LinkAppDetails",
]


def normalize_decision(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if not value or value == "N/A":
        return "PENDING"
    if any(term in value for term in ("REFUS", "REJECT", "INVALID")):
        return "REFUSED"
    if any(term in value for term in ("GRANT", "APPROVE", "CONDITIONAL", "UNCONDITIONAL", "SPLIT DECISION")):
        return "GRANTED"
    if "WITHDRAW" in value:
        return "WITHDRAWN"
    return "PENDING"


def source_url(record_link: Any, object_id: Any) -> str:
    if isinstance(record_link, str) and record_link.strip():
        return record_link.strip()
    if object_id is not None:
        return f"{ARCGIS_LAYER_URL}/{object_id}?f=html"
    return ARCGIS_LAYER_URL


def distance_km(origin_lat: float, origin_lon: float, lat: float, lon: float) -> float:
    radians = math.radians
    d_lat, d_lon = radians(lat - origin_lat), radians(lon - origin_lon)
    value = math.sin(d_lat / 2) ** 2 + math.cos(radians(origin_lat)) * math.cos(radians(lat)) * math.sin(d_lon / 2) ** 2
    return 6371 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def epoch_to_date(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return None


@lru_cache(maxsize=128)
def fetch_nearby_applications(lat: float, lon: float, radius_km: float) -> list[dict[str, Any]]:
    """Fetch and distance-filter live applications for every selected site and radius."""
    lat_offset = radius_km / 111
    lon_offset = radius_km / (111 * math.cos(math.radians(lat)))
    params = {
        "f": "json",
        "geometry": f"{lon - lon_offset},{lat - lat_offset},{lon + lon_offset},{lat + lat_offset}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(OUT_FIELDS),
        "returnGeometry": "true",
        "resultRecordCount": 250,
        "orderByFields": "OBJECTID DESC",
    }
    try:
        response = requests.get(ARCGIS_URL, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise RuntimeError("The planning data service is currently unavailable. Please retry shortly.") from error

    if payload.get("error"):
        raise RuntimeError(payload["error"].get("message", "The planning data service returned an error."))

    applications: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        attributes = feature.get("attributes", {})
        geometry = feature.get("geometry", {})
        if not isinstance(geometry.get("x"), (int, float)) or not isinstance(geometry.get("y"), (int, float)):
            continue
        application_lat, application_lon = float(geometry["y"]), float(geometry["x"])
        application_distance = distance_km(lat, lon, application_lat, application_lon)
        if application_distance > radius_km:
            continue
        applications.append(
            {
                "application_ref": str(attributes.get("ApplicationNumber") or "Planning record"),
                "council": str(attributes.get("PlanningAuthority") or ""),
                "address": str(attributes.get("DevelopmentAddress") or ""),
                "lat": application_lat,
                "lon": application_lon,
                "application_type": str(attributes.get("ApplicationType") or ""),
                "description": str(attributes.get("DevelopmentDescription") or ""),
                "date_received": epoch_to_date(attributes.get("ReceivedDate")),
                "date_decided": epoch_to_date(attributes.get("DecisionDate")),
                "decision": normalize_decision(str(attributes.get("Decision") or "")),
                "link": source_url(attributes.get("LinkAppDetails"), attributes.get("OBJECTID")),
                "distance_km": round(application_distance, 2),
            }
        )
    return sorted(applications, key=lambda application: application["distance_km"])


def summarize_applications(applications: list[dict[str, Any]]) -> dict[str, int | None]:
    granted = sum(application["decision"] == "GRANTED" for application in applications)
    refused = sum(application["decision"] == "REFUSED" for application in applications)
    pending = sum(application["decision"] == "PENDING" for application in applications)
    decided = granted + refused
    return {
        "total": len(applications),
        "granted": granted,
        "refused": refused,
        "pending": pending,
        "approval_rate": round((granted / decided) * 100) if decided else None,
    }
