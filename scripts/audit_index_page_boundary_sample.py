#!/usr/bin/env python3
"""Audit cross-page index continuation rules against manually read physical scans.

The reference file stores a small, reviewable set of real page boundaries from PG/PL/PO. This
script was created so changes to OCR cleanup or continuation regexes can be checked for both missed
joins and unsafe automatic joins without scanning the full corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.index_entry_estimator import analyze_page_boundary


def audit_reference(reference: dict[str, Any], project_root: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for item in reference.get("boundaries") or []:
        left = (project_root / str(item["physical_left_file"])).resolve()
        right = (project_root / str(item["physical_right_file"])).resolve()
        evidence = analyze_page_boundary(left, right)
        expected = bool(item["is_same_logical_entry"])
        detected = evidence["likelihood"] == "high"
        findings.append(
            {
                "volume_id": item.get("volume_id"),
                "physical_left_file": str(left),
                "physical_right_file": str(right),
                "expected_same_logical_entry": expected,
                "detected_high_likelihood": detected,
                "matches_manual_review": expected == detected,
                "manual_reason": item.get("reason"),
                "evidence": evidence,
            }
        )
    missed = [item for item in findings if item["expected_same_logical_entry"] and not item["detected_high_likelihood"]]
    unsafe = [item for item in findings if not item["expected_same_logical_entry"] and item["detected_high_likelihood"]]
    return {
        "schema_version": 1,
        "reference_seed": reference.get("seed"),
        "boundary_count": len(findings),
        "matched_count": sum(bool(item["matches_manual_review"]) for item in findings),
        "missed_continuation_count": len(missed),
        "unsafe_join_count": len(unsafe),
        "status": "ok" if not missed and not unsafe else "review_required",
        "findings": findings,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compare page-boundary continuation detection with a manual OCR reference."
    )
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()

    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    report = audit_reference(reference, args.project_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({key: value for key, value in report.items() if key != "findings"}, ensure_ascii=False))
    raise SystemExit(0 if report["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
