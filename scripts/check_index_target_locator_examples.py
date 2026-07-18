#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.index_target_locator import resolve_index_targets


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_example_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(
        path
        for path in root.rglob("*.json")
        if path.is_file()
    )


def compare_entry(result_entry: dict[str, Any], request_entry: dict[str, Any]) -> dict[str, Any]:
    expected = request_entry.get("expected_manual") or {}
    section_start_file = expected.get("section_start_file") or expected.get("target_file")
    section_start_targets = list(expected.get("accepted_section_start_targets") or expected.get("accepted_targets") or [])
    if section_start_file and section_start_file not in section_start_targets:
        section_start_targets = [section_start_file, *section_start_targets]

    editorial_anchor_file = expected.get("editorial_anchor_file") or expected.get("target_file")
    editorial_anchor_targets = list(expected.get("accepted_editorial_anchor_targets") or expected.get("accepted_targets") or [])
    if editorial_anchor_file and editorial_anchor_file not in editorial_anchor_targets:
        editorial_anchor_targets = [editorial_anchor_file, *editorial_anchor_targets]

    best = result_entry.get("best_candidate") or {}
    actual_file = best.get("file")
    section_start_status = "skip"
    if section_start_targets:
        section_start_status = "ok" if actual_file in section_start_targets else "fail"
    editorial_anchor_status = "skip"
    if editorial_anchor_targets:
        editorial_anchor_status = "ok" if actual_file in editorial_anchor_targets else "fail"

    status = "skip"
    statuses = [item for item in [section_start_status, editorial_anchor_status] if item != "skip"]
    if statuses:
        status = "ok" if "ok" in statuses else "fail"
    return {
        "entry_id": request_entry.get("entry_id"),
        "status": status,
        "section_start_status": section_start_status,
        "editorial_anchor_status": editorial_anchor_status,
        "section_start_file": section_start_file,
        "accepted_section_start_targets": section_start_targets,
        "editorial_anchor_file": editorial_anchor_file,
        "accepted_editorial_anchor_targets": editorial_anchor_targets,
        "actual_file": actual_file,
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
    }


def evaluate_file(path: Path) -> dict[str, Any]:
    request = load_json(path)
    result = resolve_index_targets(request)
    comparisons: list[dict[str, Any]] = []
    for request_entry, result_entry in zip(request.get("entries") or [], result.get("entries") or [], strict=False):
        comparisons.append(compare_entry(result_entry, request_entry))

    ok_count = sum(1 for item in comparisons if item["status"] == "ok")
    fail_count = sum(1 for item in comparisons if item["status"] == "fail")
    skip_count = sum(1 for item in comparisons if item["status"] == "skip")
    return {
        "file": str(path),
        "volume_id": request.get("volume_id"),
        "ok": ok_count,
        "fail": fail_count,
        "skip": skip_count,
        "comparisons": comparisons,
    }


def print_human_report(report: list[dict[str, Any]]) -> None:
    total_ok = 0
    total_fail = 0
    total_skip = 0
    for item in report:
        total_ok += item["ok"]
        total_fail += item["fail"]
        total_skip += item["skip"]
        print(f"{item['file']} [{item['volume_id']}]")
        print(f"  ok={item['ok']} fail={item['fail']} skip={item['skip']}")
        for comp in item["comparisons"]:
            print(f"  - {comp['entry_id']}: {comp['status']}")
            print(f"    section_start: {comp['section_start_status']}")
            print(f"    expected_section_start: {comp['section_start_file']}")
            print(f"    accepted_section_start: {comp['accepted_section_start_targets']}")
            print(f"    editorial_anchor: {comp['editorial_anchor_status']}")
            print(f"    expected_editorial_anchor: {comp['editorial_anchor_file']}")
            print(f"    accepted_editorial_anchor: {comp['accepted_editorial_anchor_targets']}")
            print(f"    actual:   {comp['actual_file']}")
            print(f"    role:     {comp['candidate_role']}")
            print(f"    summary:  {comp['reason_summary']}")
        print()
    print(f"TOTAL ok={total_ok} fail={total_fail} skip={total_skip}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Roda o index_target_locator sobre JSONs de exemplo e compara "
            "best_candidate.file com os gabaritos manuais de início de seção "
            "e âncora editorial."
        )
    )
    ap.add_argument(
        "--input",
        type=Path,
        default=Path("docs/examples/index_target_locator"),
        help="Arquivo JSON único ou diretório com exemplos.",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emite o relatório em JSON.",
    )
    args = ap.parse_args()

    files = iter_example_files(args.input)
    if not files:
        raise SystemExit(f"No JSON example files found in {args.input}")

    report = [evaluate_file(path) for path in files]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print_human_report(report)


if __name__ == "__main__":
    main()
