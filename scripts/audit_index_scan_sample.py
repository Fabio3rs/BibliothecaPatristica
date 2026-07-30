#!/usr/bin/env python3
"""Compare deterministic index scanning with a manually reviewed OCR sample.

The audit exists to evolve heading regexes from real PG/PL/PO material. A persisted reference file
records expected headings and reviewed negatives; this script reports false negatives and false
positives without requiring a corpus-wide development scan.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_alphabetical_filtered_pages import build_fallback_filtered_pages


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object: {path}")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compare deterministic index scanning with a manually reviewed OCR sample."
    )
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT / "teste")
    ap.add_argument("--profile", choices=("general", "alphabetical"), default="alphabetical")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    reference = _read_json(args.reference)
    records: list[dict[str, Any]] = []
    total_expected = 0
    total_found = 0
    total_reviewed_non_index = 0
    total_false_positive = 0
    for item in reference.get("records") or []:
        volume_id = str(item["volume_id"])
        source_root = args.root / volume_id / "text"
        payload = build_fallback_filtered_pages(
            volume_id,
            source_root,
            volume_id[:2],
            args.profile,
        )
        candidate_names = {
            Path(str(hit["file"])).name
            for hit in payload.get("candidate_sections") or []
            if isinstance(hit, dict) and hit.get("file")
        }
        expected_names = set(item.get("expected_heading_files") or [])
        found_names = expected_names & candidate_names
        missed_names = expected_names - candidate_names
        reviewed_non_index_names = set(item.get("reviewed_non_index_files") or [])
        false_positive_names = reviewed_non_index_names & candidate_names
        total_expected += len(expected_names)
        total_found += len(found_names)
        total_reviewed_non_index += len(reviewed_non_index_names)
        total_false_positive += len(false_positive_names)
        records.append(
            {
                "volume_id": volume_id,
                "source_file_count": payload.get("file_count"),
                "candidate_file_count": len(payload.get("candidate_files") or []),
                "candidate_heading_count": len(payload.get("candidate_sections") or []),
                "expected_heading_count": len(expected_names),
                "found_heading_count": len(found_names),
                "recall": round(len(found_names) / len(expected_names), 4)
                if expected_names
                else None,
                "missed_heading_files": sorted(missed_names),
                "reviewed_non_index_count": len(reviewed_non_index_names),
                "false_positive_count": len(false_positive_names),
                "false_positive_files": sorted(false_positive_names),
            }
        )

    report = {
        "schema_version": 1,
        "reference": str(args.reference),
        "reference_seed": reference.get("seed"),
        "profile": args.profile,
        "volume_count": len(records),
        "expected_heading_count": total_expected,
        "found_heading_count": total_found,
        "heading_recall": round(total_found / total_expected, 4) if total_expected else None,
        "reviewed_non_index_count": total_reviewed_non_index,
        "false_positive_count": total_false_positive,
        "records": records,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if total_found != total_expected or total_false_positive:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
