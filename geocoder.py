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
    "galway": (53.2707, -9.0568),
    "cork": (51.8985, -8.4756),
    "limerick": (52.6638, -8.6267),
    "waterford": (52.2593, -7.1101),
    "kilkenny": (52.6541, -7.2448),
    "athlone": (53.4233, -7.9407),
    "sligo": (54.2766, -8.4761),
    "ennis": (52.8432, -8.9867),
    "tralee": (52.2709, -9.7023),
    "letterkenny": (54.9558, -7.7342),
    "drogheda": (53.7189, -6.3478),
    "dundalk": (54.0037, -6.4016),
    "navan": (53.6529, -6.6814),
    "mullingar": (53.5268, -7.3383),
    "roscommon": (53.6272, -8.1891),
    "carlow": (52.8365, -6.9341),
    "wexford": (52.3369, -6.4633),
    "westport": (53.7990, -9.5200),
    "castlebar": (53.7609, -9.2988),
    "tullamore": (53.2739, -7.4893),
    "longford": (53.7274, -7.7933),
    "cavan": (53.9908, -7.3606),
    "monaghan": (54.2492, -6.9683),
    "belfast": (54.5973, -5.9301),
    "derry": (55.0068, -7.3183),
    "newry": (54.1751, -6.3402),
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
