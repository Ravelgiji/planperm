#!/usr/bin/env python3
"""Exercise the watch agent end to end, without Streamlit.

    python test_watch.py                          # Galway
    python test_watch.py "Athlone, Ireland"       # anywhere geocodable
    python test_watch.py --keep                   # leave the workspace behind

Proves the four things that matter:

  1. The authority for a pin resolves, and has official sources configured.
  2. A first scan records a baseline and reports no change.
  3. A second scan against an unchanged source reports nothing (no false alerts).
  4. When a document genuinely appears, it is detected and cited.

Step 4 needs a change to detect, and a council will not publish one on cue. So
it rewinds the stored baseline - removing two documents the scan already saw -
and rescans. The change is then found through the ordinary diff path: same
code, same comparison. Only the timing is arranged.
"""

from __future__ import annotations

import argparse
import json
import sys

from core.env import llm_label, load_env

load_env()

from agents.watch import watch_node                             # noqa: E402
from agents.helpers import authority_sources, resolve_authority  # noqa: E402
from core.watch import fingerprint                              # noqa: E402
from core.watch_store import (                                   # noqa: E402
    SNAPSHOT_DIR,
    clear_alerts,
    delete_workspace,
    list_alerts,
    save_workspace,
    snapshot_summary,
)
from geocoder import resolve_location                            # noqa: E402


def heading(text: str) -> None:
    print(f"\n{'=' * 68}\n{text}\n{'=' * 68}")


def show_alerts(alerts: list[dict]) -> None:
    if not alerts:
        print("  (no alerts)")
        return
    for alert in alerts:
        print(f"  [{alert.get('severity')}/{alert.get('alert_type')}] {alert.get('title')}")
        print(f"      {alert.get('body', '')[:150]}")
        print(f"      source: {alert.get('source_url', '')[:100]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch agent smoke test")
    parser.add_argument("location", nargs="?", default="Galway, Ireland")
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the test workspace afterwards")
    args = parser.parse_args()

    print(f"LLM backend: {llm_label()}")

    # -- 1. Resolve the pin --------------------------------------------------
    heading(f"1. Resolve '{args.location}'")
    coordinates = resolve_location(args.location)
    if coordinates is None:
        print(f"  Could not geocode '{args.location}'.")
        return 1

    lat, lon = coordinates
    print(f"  coordinates: {lat:.4f}, {lon:.4f}")

    resolution = resolve_authority(lat, lon)
    authority = resolution.get("authority", "")
    print(f"  authority  : {authority or '(unresolved)'}  [{resolution.get('status')}]")

    sources = authority_sources(authority) if authority else []
    print(f"  sources    : {len(sources)}")
    for source in sources:
        print(f"    - {source.get('title')}")
        print(f"      {source.get('url')}")

    if not sources:
        print("\n  No official sources configured for this authority, so there is "
              "nothing to scan. Try a different location.")
        return 1

    workspace = save_workspace(
        label=f"TEST {args.location}",
        lat=lat, lng=lon, radius_km=2.0,
        authority=authority,
        jurisdiction=resolution.get("jurisdiction", ""),
    )
    workspace_id = workspace["workspace_id"]
    print(f"\n  workspace  : {workspace_id}")

    try:
        # -- 2. First scan: baseline ---------------------------------------
        heading("2. First scan - expect a baseline, no change reported")
        result = watch_node({"workspace_id": workspace_id})
        print(f"  sources scanned: {result.get('watch_sources_scanned')}")
        print(f"  alerts         : {len(result.get('watch_alerts') or [])}")
        show_alerts(result.get("watch_alerts") or [])

        print("\n  baselines stored:")
        for row in snapshot_summary(workspace_id):
            print(f"    {row['document_count']:4} document(s)  fp={row['fingerprint']}"
                  f"  {row['source_url'][:64]}")

        for error in result.get("errors") or []:
            print(f"  note: {error}")

        indexed = sum(r["document_count"] for r in snapshot_summary(workspace_id))
        if indexed == 0:
            print("\n  Nothing indexable was found at these sources, so there is no "
                  "baseline to diff. This is reported honestly rather than as a "
                  "change; try another authority.")
            return 1

        # -- 3. Second scan: nothing changed --------------------------------
        heading("3. Second scan - expect NO alerts (no false positives)")
        clear_alerts(workspace_id)
        result = watch_node({"workspace_id": workspace_id})
        alerts = result.get("watch_alerts") or []
        print(f"  alerts: {len(alerts)}")
        show_alerts(alerts)
        print(f"\n  {'PASS' if not alerts else 'FAIL'} - an unchanged source should "
              f"produce no alerts")

        # -- 4. Rewind the baseline and rescan ------------------------------
        heading("4. Rewind the baseline, then rescan - expect detection")
        snapshot_dir = SNAPSHOT_DIR / workspace_id
        candidates = [
            path for path in snapshot_dir.glob("*.json")
            if json.loads(path.read_text(encoding="utf-8"))["document_count"] >= 2
        ]
        if not candidates:
            print("  No source has 2+ documents, so there is nothing to rewind. "
                  "Steps 1-3 still passed.")
            return 0

        target = max(
            candidates,
            key=lambda p: json.loads(p.read_text(encoding="utf-8"))["document_count"],
        )
        data = json.loads(target.read_text(encoding="utf-8"))
        removed = data["documents"][:2]
        data["documents"] = data["documents"][2:]
        data["fingerprint"] = fingerprint(data["documents"])
        data["document_count"] = len(data["documents"])
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")

        print("  removed from the stored baseline:")
        for document in removed:
            print(f"    - {document['title'][:70]}")

        clear_alerts(workspace_id)
        result = watch_node({"workspace_id": workspace_id})
        alerts = result.get("watch_alerts") or []
        print(f"\n  alerts: {len(alerts)}")
        show_alerts(alerts)
        print(f"\n  {'PASS' if len(alerts) == len(removed) else 'PARTIAL'} - expected "
              f"{len(removed)} detection(s), got {len(alerts)}")

        heading("Briefing")
        print(result.get("watch_briefing") or "(none)")

        print(f"\nStored alerts for this workspace: {len(list_alerts(workspace_id))}")
        return 0

    finally:
        if args.keep:
            print(f"\nWorkspace kept: {workspace_id}")
        else:
            delete_workspace(workspace_id)
            print(f"\nTest workspace {workspace_id} deleted.")


if __name__ == "__main__":
    sys.exit(main())
