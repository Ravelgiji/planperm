#!/usr/bin/env python3
"""CLI entry point for testing the planning agents.

Usage:
    # Advisor mode (resolves authority, fetches records, gets guidance)
    python -m agents.run --lat 53.2707 --lng -9.0568 --type "New two-storey dwelling"

    # Custom radius
    python -m agents.run --lat 53.2707 --lng -9.0568 --type "Extension" --radius 3.0

    # Draft review mode
    python -m agents.run --lat 53.2707 --lng -9.0568 --pdf /tmp/draft.pdf

    # JSON output
    python -m agents.run --lat 53.2707 --lng -9.0568 --type "New dwelling" --json
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="PlanPerm agent runner")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lng", type=float, required=True)
    parser.add_argument("--radius", dest="radius_km", type=float, default=2.0)
    parser.add_argument("--type", dest="construction_type", default="")
    parser.add_argument("--condition", dest="site_condition", default="")
    parser.add_argument("--question", default="")
    parser.add_argument("--pdf", dest="pdf_path", default="")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    from agents.graph import run

    result = run(
        lat=args.lat,
        lng=args.lng,
        radius_km=args.radius_km,
        construction_type=args.construction_type,
        site_condition=args.site_condition,
        question=args.question,
        pdf_path=args.pdf_path,
    )

    if args.as_json:
        output = {k: v for k, v in result.items() if k != "records"}
        output["record_count"] = len(result.get("records", []))
        print(json.dumps(output, indent=2, default=str))
    else:
        if result.get("authority"):
            print(f"Authority: {result['authority']} ({result.get('jurisdiction', '?')})\n")
        if result.get("summary"):
            s = result["summary"]
            print(f"Nearby: {s.get('total', 0)} records, {s.get('approval_rate', '?')}% approval rate\n")
        if result.get("advice"):
            print("=== ADVISOR ===\n")
            print(result["advice"])
        if result.get("draft_review"):
            print("=== DRAFT REVIEW ===\n")
            print(result["draft_review"])
        if result.get("errors"):
            print("\n=== ERRORS ===")
            for e in result["errors"]:
                print(f"  - {e}")

    sys.exit(1 if result.get("errors") else 0)


if __name__ == "__main__":
    main()
