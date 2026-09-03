"""Generate realistic mock planning applications for Dublin City."""

import json
import random
from pathlib import Path

# Centre points for demo areas
AREAS = {
    "rathmines": (53.3244, -6.2634),
    "swords": (53.4597, -6.2181),
    "drumcondra": (53.3706, -6.2522),
}

APPLICATION_TYPES = [
    "Permission",
    "Retention Permission",
    "Outline Permission",
    "Permission Consequent",
]

DESCRIPTIONS_GRANTED = [
    "Single-storey extension to rear of existing dwelling",
    "Conversion of attic space to habitable room with dormer to rear",
    "Demolition of existing garage and construction of single-storey extension",
    "Construction of new vehicular entrance and driveway",
    "Change of use from retail to cafe at ground floor level",
    "Amendments to previously approved two-storey extension",
    "Construction of detached domestic garage to side of dwelling",
    "Alterations and extension to existing first-floor apartment",
    "New single-storey granny flat to rear garden",
    "Renovation and extension of protected structure dwelling",
]

DESCRIPTIONS_REFUSED = [
    "Two-storey extension to rear of dwelling overlooking adjacent property",
    "Construction of new dwelling in rear garden of existing house",
    "Three-storey mixed-use building on infill site",
    "Change of use from residential to short-term letting",
    "Extension exceeding 40sqm to semi-detached dwelling",
]

DESCRIPTIONS_PENDING = [
    "Two-storey extension with roof terrace to rear",
    "Sub-division of existing dwelling into two residential units",
    "Construction of replacement dwelling on existing site",
    "Extension and renovation of end-of-terrace dwelling",
]

REFUSAL_REASONS = [
    "Overlooking and loss of privacy to adjacent properties",
    "Overbearing impact and overshadowing of neighbouring dwelling",
    "Visually incongruous with established streetscape character",
    "Overdevelopment of the site",
    "Insufficient private open space provision",
    "Inadequate car parking and impact on traffic safety",
    "Non-compliance with development plan standards for residential extensions",
    "Negative impact on residential amenity of the area",
]

CONDITIONS = [
    "External finishes to match existing dwelling",
    "Submission of drainage plan prior to commencement",
    "Development contribution of €3,200 to be paid",
    "Hours of construction limited to 08:00-18:00 Mon-Fri",
    "Obscure glazing to be used on windows facing adjacent property",
    "Landscaping plan to be submitted and agreed",
    "Retention of existing boundary wall and vegetation",
    "Archaeological monitoring during ground works",
]

STREETS = [
    "Rathmines Road", "Leinster Road", "Castlewood Avenue",
    "Palmerston Road", "Cowper Road", "Belgrave Square",
    "Rathgar Road", "Grosvenor Road", "Highfield Road",
    "Kenilworth Road", "Maxwell Road", "Mountpleasant Avenue",
    "Church Avenue", "Main Street", "Bridge Street",
    "Forest Road", "Brackenstown Road", "Seatown Road",
    "Malahide Road", "Dublin Road", "North Street",
    "Griffith Avenue", "Drumcondra Road", "Clonliffe Road",
    "Hollybank Road", "Iona Road", "Home Farm Road",
]


def _jitter(centre: tuple[float, float], radius_km: float = 1.0) -> tuple[float, float]:
    """Add random offset to a centre point within roughly radius_km."""
    # ~0.009 degrees latitude ≈ 1km at Dublin's latitude
    lat_offset = random.uniform(-0.009, 0.009) * radius_km
    lon_offset = random.uniform(-0.014, 0.014) * radius_km
    return round(centre[0] + lat_offset, 6), round(centre[1] + lon_offset, 6)


def _random_date(year_start: int = 2020, year_end: int = 2026) -> str:
    year = random.randint(year_start, year_end)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year}-{month:02d}-{day:02d}"


def generate_applications(area: str, count: int = 150, centre: tuple[float, float] | None = None) -> list[dict]:
    """Generate `count` mock planning applications for the given area."""
    if centre is None:
        centre = AREAS.get(area, AREAS["rathmines"])
    apps = []

    for i in range(count):
        ref_year = random.randint(20, 26)
        ref_num = random.randint(1000, 9999)
        ref = f"WEB{ref_num}/{ref_year}"

        # Weighted: ~70% granted, ~18% refused, ~12% pending
        roll = random.random()
        if roll < 0.70:
            decision = "GRANTED"
            desc = random.choice(DESCRIPTIONS_GRANTED)
            refusal = []
            conds = random.sample(CONDITIONS, k=random.randint(1, 4))
        elif roll < 0.88:
            decision = "REFUSED"
            desc = random.choice(DESCRIPTIONS_REFUSED)
            refusal = random.sample(REFUSAL_REASONS, k=random.randint(1, 3))
            conds = []
        else:
            decision = "PENDING"
            desc = random.choice(DESCRIPTIONS_PENDING)
            refusal = []
            conds = []

        lat, lon = _jitter(centre)
        street = random.choice(STREETS)
        house_num = random.randint(1, 120)
        date_received = _random_date()

        if decision == "PENDING":
            date_decided = None
        else:
            # Decision 8-16 weeks after receipt
            # ponytail: naive date math, good enough for mock data
            parts = date_received.split("-")
            decided_month = int(parts[1]) + random.randint(2, 4)
            decided_year = int(parts[0])
            if decided_month > 12:
                decided_month -= 12
                decided_year += 1
            date_decided = f"{decided_year}-{decided_month:02d}-{random.randint(1, 28):02d}"

        apps.append({
            "application_ref": ref,
            "council": "dublin_city",
            "address": f"{house_num} {street}, Dublin",
            "lat": lat,
            "lon": lon,
            "application_type": random.choice(APPLICATION_TYPES),
            "description": desc,
            "date_received": date_received,
            "date_decided": date_decided,
            "decision": decision,
            "conditions": conds,
            "refusal_reasons": refusal,
        })

    return apps


def load_or_generate(area: str, count: int = 150, centre: tuple[float, float] | None = None) -> list[dict]:
    """Load cached data for an area, or generate and cache it."""
    cache_dir = Path(__file__).parent / "data"
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"{area}.json"

    if cache_file.exists():
        return json.loads(cache_file.read_text())

    apps = generate_applications(area, count, centre=centre)
    cache_file.write_text(json.dumps(apps, indent=2))
    return apps


if __name__ == "__main__":
    for area_name in AREAS:
        data = load_or_generate(area_name)
        print(f"{area_name}: {len(data)} applications generated")
