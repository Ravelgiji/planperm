"""Analyse planning applications — stats, patterns, precedents, timelines, appeals."""

from collections import Counter
from datetime import datetime
import math
import statistics


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two coordinates."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def filter_by_radius(apps: list[dict], lat: float, lon: float, radius_km: float = 1.0) -> list[dict]:
    """Return applications within radius_km of (lat, lon)."""
    return [
        a for a in apps
        if a.get("lat") and a.get("lon")
        and haversine_km(lat, lon, a["lat"], a["lon"]) <= radius_km
    ]


# ── Core stats ───────────────────────────────────────────────────────────────

def compute_stats(apps: list[dict]) -> dict:
    """Compute summary statistics for a set of applications."""
    if not apps:
        return {"total": 0, "message": "No applications found in this area."}

    total = len(apps)
    decisions = Counter(a["decision"] for a in apps)
    granted = decisions.get("GRANTED", 0)
    refused = decisions.get("REFUSED", 0)
    pending = decisions.get("PENDING", 0)
    withdrawn = decisions.get("WITHDRAWN", 0)

    decided = granted + refused
    approval_rate = round(granted / decided * 100, 1) if decided > 0 else None

    # Common refusal reasons (from mock data — real API doesn't have these)
    all_refusals = []
    for a in apps:
        all_refusals.extend(a.get("refusal_reasons") or [])
    top_refusals = Counter(all_refusals).most_common(5)

    # Common conditions (from mock data)
    all_conditions = []
    for a in apps:
        all_conditions.extend(a.get("conditions") or [])
    top_conditions = Counter(all_conditions).most_common(5)

    # Approval rate by application type
    type_stats = {}
    for a in apps:
        atype = (a.get("application_type") or "Unknown").strip()
        if not atype:
            atype = "Unknown"
        if atype not in type_stats:
            type_stats[atype] = {"granted": 0, "refused": 0, "pending": 0, "withdrawn": 0}
        key = a["decision"].lower()
        type_stats[atype][key] = type_stats[atype].get(key, 0) + 1

    type_rates = {}
    for atype, counts in type_stats.items():
        d = counts.get("granted", 0) + counts.get("refused", 0)
        if d > 0:
            type_rates[atype] = round(counts["granted"] / d * 100, 1)

    return {
        "total": total,
        "granted": granted,
        "refused": refused,
        "pending": pending,
        "withdrawn": withdrawn,
        "approval_rate": approval_rate,
        "top_refusal_reasons": top_refusals,
        "top_conditions": top_conditions,
        "approval_by_type": type_rates,
    }


# ── Timeline stats ───────────────────────────────────────────────────────────

def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def compute_timeline_stats(apps: list[dict]) -> dict | None:
    """Compute decision timeline statistics."""
    durations = []
    by_year: dict[int, dict] = {}

    for a in apps:
        received = _parse_date(a.get("date_received"))
        decided = _parse_date(a.get("date_decided"))
        decision = a.get("decision", "")

        if decided and decided.year not in by_year:
            by_year[decided.year] = {"granted": 0, "refused": 0}

        if decision == "GRANTED" and decided:
            by_year[decided.year]["granted"] += 1
        elif decision == "REFUSED" and decided:
            by_year[decided.year]["refused"] += 1

        if received and decided and decision in ("GRANTED", "REFUSED"):
            days = (decided - received).days
            if 0 < days < 1000:  # sanity check
                durations.append(days)

    if not durations and not by_year:
        return None

    result = {}

    if durations:
        result["avg_days"] = round(statistics.mean(durations))
        result["median_days"] = round(statistics.median(durations))
        result["min_days"] = min(durations)
        result["max_days"] = max(durations)

    # Approval rate by year
    year_rates = {}
    for year, counts in sorted(by_year.items()):
        d = counts["granted"] + counts["refused"]
        if d >= 3:  # need at least 3 decided to be meaningful
            year_rates[year] = round(counts["granted"] / d * 100, 1)
    result["by_year"] = year_rates

    # Trend: compare last 2 years with available data
    years = sorted(year_rates.keys())
    if len(years) >= 2:
        recent = year_rates[years[-1]]
        previous = year_rates[years[-2]]
        diff = recent - previous
        if diff > 5:
            result["trend"] = f"Approval rate is **rising** ({previous}% → {recent}% over last 2 years with data)"
        elif diff < -5:
            result["trend"] = f"Approval rate is **falling** ({previous}% → {recent}% over last 2 years with data)"
        else:
            result["trend"] = f"Approval rate is **stable** ({previous}% → {recent}% over last 2 years with data)"
    else:
        result["trend"] = None

    return result


# ── Appeal stats ─────────────────────────────────────────────────────────────

def compute_appeal_stats(apps: list[dict]) -> dict | None:
    """Compute appeal statistics from the data."""
    refused = [a for a in apps if a["decision"] == "REFUSED"]
    if not refused:
        return None

    appealed = [a for a in refused if (a.get("appeal_status") or "").strip()]
    appeals_granted = [
        a for a in appealed
        if "GRANT" in (a.get("appeal_decision") or "").upper()
    ]

    total_refused = len(refused)
    appeals_filed = len(appealed)

    return {
        "total_refused": total_refused,
        "appeals_filed": appeals_filed,
        "appeal_rate": round(appeals_filed / total_refused * 100, 1) if total_refused > 0 else 0,
        "appeals_granted": len(appeals_granted),
        "appeal_success_rate": round(len(appeals_granted) / appeals_filed * 100, 1) if appeals_filed > 0 else 0,
    }


# ── Precedent matching ───────────────────────────────────────────────────────

def find_precedents(apps: list[dict], description_keywords: list[str], limit: int = 10) -> list[dict]:
    """Find applications whose description matches any of the keywords.

    Scores by keyword overlap count. Only returns decided applications
    (GRANTED or REFUSED), sorted by relevance then recency.
    """
    if not description_keywords:
        return []

    keywords_lower = [k.lower() for k in description_keywords if len(k) > 2]
    if not keywords_lower:
        return []

    scored = []
    for a in apps:
        desc_lower = (a.get("description") or "").lower()
        score = sum(1 for kw in keywords_lower if kw in desc_lower)
        if score > 0 and a.get("decision") in ("GRANTED", "REFUSED"):
            scored.append((score, a.get("date_decided") or "", a))

    # Sort by score desc, then date desc (most recent first)
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [a for _, _, a in scored[:limit]]


# ── Formatting ───────────────────────────────────────────────────────────────

def format_stats_text(stats: dict) -> str:
    """Format stats dict into a readable summary string."""
    if stats.get("total", 0) == 0:
        return "No applications found in this area."

    lines = [
        f"**{stats['total']}** applications found in area",
        f"- Granted: {stats['granted']}  |  Refused: {stats['refused']}  |  Pending: {stats['pending']}",
    ]

    if stats.get("withdrawn", 0) > 0:
        lines.append(f"- Withdrawn: {stats['withdrawn']}")

    if stats["approval_rate"] is not None:
        lines.append(f"- **Overall approval rate: {stats['approval_rate']}%**")

    if stats["top_refusal_reasons"]:
        lines.append("\n**Top refusal reasons:**")
        for reason, count in stats["top_refusal_reasons"]:
            lines.append(f"- {reason} ({count}x)")

    if stats["top_conditions"]:
        lines.append("\n**Common conditions on grants:**")
        for cond, count in stats["top_conditions"]:
            lines.append(f"- {cond} ({count}x)")

    if stats["approval_by_type"]:
        lines.append("\n**Approval rate by type:**")
        for atype, rate in sorted(stats["approval_by_type"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- {atype}: {rate}%")

    return "\n".join(lines)
