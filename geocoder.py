"""Geocode addresses and Eircodes using OSM Nominatim."""

import requests
import functools

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "PlanPerm-PoC/0.1"}


@functools.lru_cache(maxsize=256)
def geocode(query: str) -> tuple[float, float] | None:
    """Convert an address or place name to (lat, lon). Returns None on failure."""
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "ie",
    }
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=5)
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except (requests.RequestException, KeyError, IndexError, ValueError):
        pass
    return None


# Known demo locations so the PoC works offline
KNOWN_LOCATIONS = {
    "rathmines": (53.3244, -6.2634),
    "swords": (53.4597, -6.2181),
    "drumcondra": (53.3706, -6.2522),
    "dublin": (53.3498, -6.2603),
}


def resolve_location(query: str) -> tuple[float, float] | None:
    """Try known locations first, then fall back to Nominatim."""
    key = query.strip().lower()
    if key in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[key]
    return geocode(query)
