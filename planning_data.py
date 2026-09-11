"""Small, source-linked ArcGIS adapter for the Streamlit PlanPerm workspace."""

from __future__ import annotations

import json
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


PAGE_SIZE = 2000          # the server's own per-request maximum
MAX_RECORDS = 8000        # enough for a 5 km urban radius without a long wait


CIRCLE_POINTS = 64        # a 64-gon encloses 99.84% of the circle's area


def _circle_ring(lat: float, lon: float, radius_km: float) -> list[list[float]]:
    """A polygon approximating the search circle, in lon/lat pairs."""
    ring = []
    for step in range(CIRCLE_POINTS + 1):
        angle = 2 * math.pi * step / CIRCLE_POINTS
        d_lat = (radius_km / 111.0) * math.cos(angle)
        d_lon = (radius_km / (111.0 * math.cos(math.radians(lat)))) * math.sin(angle)
        ring.append([round(lon + d_lon, 6), round(lat + d_lat, 6)])
    return ring


@lru_cache(maxsize=256)
def count_nearby(lat: float, lon: float, radius_km: float) -> int | None:
    """How many applications lie within radius_km. None if the count fails.

    Counted by the server against a true circle, so it is exact at any radius
    and costs one request with no records transferred - about two seconds even
    for a 20 km search over 120,000 records. That matters because fetching
    records is capped: at 5 km in Dublin the fetch returns 7,603 of 20,435, and
    without this the headline figure would silently be the cap rather than the
    answer.

    Sent by POST: the polygon does not fit in a query string, and a GET comes
    back as a non-JSON error page.
    """
    payload = {
        "f": "json",
        "geometry": json.dumps({
            "rings": [_circle_ring(lat, lon, radius_km)],
            "spatialReference": {"wkid": 4326},
        }),
        "geometryType": "esriGeometryPolygon",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "returnCountOnly": "true",
    }

    try:
        response = requests.post(ARCGIS_URL, data=payload, timeout=45)
        response.raise_for_status()
        count = response.json().get("count")
    except (requests.RequestException, ValueError):
        return None

    return int(count) if isinstance(count, int) else None


def _bbox(lat: float, lon: float, radius_km: float) -> str:
    lat_offset = radius_km / 111
    lon_offset = radius_km / (111 * math.cos(math.radians(lat)))
    return (
        f"{lon - lon_offset},{lat - lat_offset},"
        f"{lon + lon_offset},{lat + lat_offset}"
    )


def _parse_feature(feature: dict[str, Any], lat: float, lon: float) -> dict[str, Any] | None:
    attributes = feature.get("attributes", {})
    geometry = feature.get("geometry", {})
    if not isinstance(geometry.get("x"), (int, float)) or not isinstance(geometry.get("y"), (int, float)):
        return None

    application_lat, application_lon = float(geometry["y"]), float(geometry["x"])
    return {
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
        "distance_km": round(distance_km(lat, lon, application_lat, application_lon), 2),
    }


@lru_cache(maxsize=128)
def fetch_nearby(lat: float, lon: float, radius_km: float) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Every application within radius_km of the pin, plus metadata.

    Paginates. The previous version asked for a single page of 250 records
    ordered by OBJECTID DESC and then distance-filtered, which was wrong in a
    way that got worse as the radius grew: a 2 km search reported 215 of 7,024
    records, and a 5 km search reported *two*, because the 250 newest records
    in a large bounding box cluster geographically and mostly fell outside the
    circle. The count went DOWN as the radius went up.

    Returns (applications, meta) where meta carries `truncated` - true when the
    area holds more records than MAX_RECORDS, so callers can say the figure is
    a floor rather than presenting it as complete.
    """
    params = {
        "f": "json",
        "geometry": _bbox(lat, lon, radius_km),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(OUT_FIELDS),
        "returnGeometry": "true",
        "resultRecordCount": PAGE_SIZE,
        # Ordered by id so paging is stable; the full set is fetched either
        # way, so this no longer biases which records are seen.
        "orderByFields": "OBJECTID ASC",
    }

    applications: list[dict[str, Any]] = []
    offset = 0
    truncated = False

    while offset < MAX_RECORDS:
        try:
            response = requests.get(
                ARCGIS_URL, params={**params, "resultOffset": offset}, timeout=30
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            if applications:
                # Keep what we have rather than losing a good partial result.
                truncated = True
                break
            raise RuntimeError(
                "The planning data service is currently unavailable. Please retry shortly."
            ) from error

        if payload.get("error"):
            raise RuntimeError(
                payload["error"].get("message", "The planning data service returned an error.")
            )

        features = payload.get("features", [])
        if not features:
            break

        for feature in features:
            record = _parse_feature(feature, lat, lon)
            # The bounding box has corners; the radius does not.
            if record is not None and record["distance_km"] <= radius_km:
                applications.append(record)

        offset += len(features)

        if not payload.get("exceededTransferLimit"):
            break
    else:
        truncated = True

    applications.sort(key=lambda application: application["distance_km"])

    return tuple(applications), {
        "truncated": truncated,
        "scanned": offset,
        "radius_km": radius_km,
    }


def fetch_nearby_applications(lat: float, lon: float, radius_km: float) -> list[dict[str, Any]]:
    """Applications within radius_km of the pin, nearest first."""
    applications, _ = fetch_nearby(lat, lon, radius_km)
    return list(applications)


def area_total(lat: float, lon: float, radius_km: float) -> tuple[int, bool]:
    """The number of applications in the area, and whether the list holds them all.

    Returns (total, list_is_complete).

    When the fetch completed, the fetched count IS the total - exact, and
    consistent with the records the list shows. When it hit the cap, the fetched
    count is just the cap, so the total comes from the server-side circle count
    instead: at 5 km in Dublin that is 20,435 rather than the 7,603 the fetch
    returned.
    """
    applications, meta = fetch_nearby(lat, lon, radius_km)

    if not meta["truncated"]:
        return len(applications), True

    counted = count_nearby(lat, lon, radius_km)
    return (counted if counted is not None else len(applications)), False


def area_is_truncated(lat: float, lon: float, radius_km: float) -> bool:
    """True when the area holds more records than one fetch will return."""
    _, meta = fetch_nearby(lat, lon, radius_km)
    return bool(meta["truncated"])


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
