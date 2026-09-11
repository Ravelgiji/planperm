"""Local persistence for the watch agent.

Keeps the function names the original `core/store.py` exposed to the watch
view - `get_workspace`, `latest_snapshot`, `save_snapshot`, `add_alerts`,
`list_alerts` - so the recovered design still fits. Scoped to watch state
only, rather than resurrecting the whole superseded store.

A **workspace** is the boundary for monitoring: a saved location, radius and
resolved authority. Snapshots and alerts belong to one, which is what makes
"has anything changed *here*" a meaningful question.

Everything is JSON on disk under `watch_data/`, which is gitignored. Watched
locations are private preferences: nothing is uploaded, nothing is shared
between workspaces, and `export_workspace` exists so a user can take their
data and leave. This mirrors the original build's stance - it never scanned in
the background and never sent email, and neither does this.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[1] / "watch_data"
WORKSPACES_FILE = DATA_DIR / "workspaces.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
ALERT_DIR = DATA_DIR / "alerts"

MAX_ALERTS_PER_WORKSPACE = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt file must not take the app down. The next write repairs it;
        # at worst one scan re-reports.
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)          # atomic, so an interrupted write cannot truncate


def _source_key(source_url: str) -> str:
    """Filesystem-safe, stable key for one monitored source URL."""
    return hashlib.sha1(source_url.encode()).hexdigest()[:16]


# -- Workspaces --------------------------------------------------------------

def make_workspace_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "site").lower()).strip("-") or "site"
    digest = hashlib.sha1(f"{label}{_now()}".encode()).hexdigest()[:6]
    return f"{slug[:32]}-{digest}"


def list_workspaces() -> list[dict[str, Any]]:
    return _read(WORKSPACES_FILE, [])


def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    return next((w for w in list_workspaces() if w["workspace_id"] == workspace_id), None)


def save_workspace(
    label: str,
    lat: float,
    lng: float,
    radius_km: float = 2.0,
    authority: str = "",
    jurisdiction: str = "",
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Create or update a workspace. Returns the stored record."""
    workspaces = list_workspaces()
    workspace_id = workspace_id or make_workspace_id(label)

    existing = next((w for w in workspaces if w["workspace_id"] == workspace_id), None)
    record = {
        "workspace_id": workspace_id,
        "label": label,
        "lat": lat,
        "lng": lng,
        "radius_km": radius_km,
        "authority": authority,
        "jurisdiction": jurisdiction,
        "created_at": existing["created_at"] if existing else _now(),
        "last_scanned": existing.get("last_scanned") if existing else None,
    }

    workspaces = [w for w in workspaces if w["workspace_id"] != workspace_id]
    workspaces.append(record)
    _write(WORKSPACES_FILE, workspaces)
    return record


def touch_workspace(workspace_id: str) -> None:
    workspaces = list_workspaces()
    for workspace in workspaces:
        if workspace["workspace_id"] == workspace_id:
            workspace["last_scanned"] = _now()
            _write(WORKSPACES_FILE, workspaces)
            return


def delete_workspace(workspace_id: str) -> bool:
    workspaces = list_workspaces()
    remaining = [w for w in workspaces if w["workspace_id"] != workspace_id]
    if len(remaining) == len(workspaces):
        return False

    _write(WORKSPACES_FILE, remaining)
    (ALERT_DIR / f"{workspace_id}.json").unlink(missing_ok=True)
    snapshot_dir = SNAPSHOT_DIR / workspace_id
    if snapshot_dir.is_dir():
        for path in snapshot_dir.iterdir():
            path.unlink(missing_ok=True)
        snapshot_dir.rmdir()
    return True


# -- Snapshots (the comparison baseline) -------------------------------------

def latest_snapshot(workspace_id: str, source_url: str) -> dict[str, Any] | None:
    """The last stored index for one source. None means no baseline yet.

    None is meaningful, not an error: `core.watch.compare` treats it as "record
    a baseline and report nothing", which is what a first scan should do.
    """
    path = SNAPSHOT_DIR / workspace_id / f"{_source_key(source_url)}.json"
    return _read(path, None)


def save_snapshot(
    workspace_id: str,
    source_url: str,
    documents: list[dict[str, str]],
    fingerprint: str,
) -> None:
    """Record what a source looked like, to compare the next scan against."""
    path = SNAPSHOT_DIR / workspace_id / f"{_source_key(source_url)}.json"
    _write(path, {
        "source_url": source_url,
        "fingerprint": fingerprint,
        "documents": documents,
        "captured_at": _now(),
        "document_count": len(documents),
    })


def snapshot_summary(workspace_id: str) -> list[dict[str, Any]]:
    """Every source baselined for a workspace, for display."""
    directory = SNAPSHOT_DIR / workspace_id
    if not directory.is_dir():
        return []

    rows = []
    for path in sorted(directory.glob("*.json")):
        snapshot = _read(path, None)
        if snapshot:
            rows.append({
                "source_url": snapshot.get("source_url"),
                "document_count": snapshot.get("document_count", 0),
                "captured_at": snapshot.get("captured_at"),
                "fingerprint": (snapshot.get("fingerprint") or "")[:12],
            })
    return rows


# -- Alerts ------------------------------------------------------------------

def _alert_id(workspace_id: str, alert: dict[str, Any]) -> str:
    """Identity of one alert, so re-scanning does not duplicate it."""
    basis = f"{workspace_id}|{alert.get('alert_type')}|{alert.get('source_url')}|{alert.get('title')}"
    return hashlib.sha1(basis.encode()).hexdigest()[:12]


def add_alerts(workspace_id: str, alerts: list[dict[str, Any]]) -> int:
    """Append alerts, skipping ones already stored. Returns how many were new."""
    if not alerts:
        return 0

    path = ALERT_DIR / f"{workspace_id}.json"
    existing = _read(path, [])
    seen = {a.get("alert_id") for a in existing}

    fresh = []
    for alert in alerts:
        stamped = {**alert, "alert_id": _alert_id(workspace_id, alert), "detected_at": _now()}
        if stamped["alert_id"] in seen:
            continue
        seen.add(stamped["alert_id"])
        fresh.append(stamped)

    if fresh:
        combined = (existing + fresh)[-MAX_ALERTS_PER_WORKSPACE:]
        _write(path, combined)

    return len(fresh)


def list_alerts(workspace_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Stored alerts, newest first."""
    alerts = _read(ALERT_DIR / f"{workspace_id}.json", [])
    alerts.sort(key=lambda a: a.get("detected_at") or "", reverse=True)
    return alerts[:limit] if limit else alerts


def clear_alerts(workspace_id: str) -> None:
    (ALERT_DIR / f"{workspace_id}.json").unlink(missing_ok=True)


def export_workspace(workspace_id: str) -> dict[str, Any]:
    """Everything held about one workspace, for the user to take away."""
    return {
        "exported_at": _now(),
        "workspace": get_workspace(workspace_id),
        "snapshots": snapshot_summary(workspace_id),
        "alerts": list_alerts(workspace_id),
    }
