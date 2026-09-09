"""Irish town and address resolver used by the Streamlit search panel."""

from __future__ import annotations

from functools import lru_cache

import requests


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "PlanPerm/1.0 (planning research tool)"}
KNOWN_LOCATIONS = {
    "rathmines": (53.3244, -6.2634),
    "clontarf": (53.3638, -6.1923),
    "swords": (53.4597, -6.2181),
    "drumcondra": (53.3706, -6.2522),
    "dublin": (53.3498, -6.2603),
}


@lru_cache(maxsize=256)
def resolve_location(query: str) -> tuple[float, float] | None:
    key = query.strip().lower()
    if key in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[key]
    if not key:
        return None
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "ie"},
            headers=HEADERS,
            timeout=8,
        )
        response.raise_for_status()
        result = response.json()
        if result:
            return float(result[0]["lat"]), float(result[0]["lon"])
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        return None
    return None
