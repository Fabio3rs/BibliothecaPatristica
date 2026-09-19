#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.probe_chapter_targets import is_chapter_section, run_probe


BACKFILL_VERSION = 4


def comparable_path(value: Any, source_root: Path) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path.resolve()
    source_candidate = (source_root / path).resolve()
    if source_candidate.is_file():
        return source_candidate
    return (PROJECT_ROOT / path).resolve()


def target_evidence(chapter_ref: dict[str, Any]) -> dict[str, Any]:
    target = chapter_ref["target"]
    return {
        "ordinal": chapter_ref["ordinal"],
        "ordinal_roman": chapter_ref["ordinal_roman"],
        "matched_marker_ordinal": target.get("ordinal"),
        "target_file": target["target_file"],
        "physical_page": target.get("physical_page"),
        "heading_raw": target.get("heading_raw"),
        "line": target.get("line"),
        "marker_style": target.get("style"),
        "normalized_similarity": target.get("normalized_similarity"),
        "context": target.get("context"),
    }


def add_entry_evidence(
    entry: dict[str, Any],
    *,
    section_result: dict[str, Any],
    entry_result: dict[str, Any],
    previous_target_file: str | None = None,
) -> None:
    raw_json = entry.get("raw_json")
    if not isinstance(raw_json, dict):
        raw_json = {"prior_raw_json": raw_json} if raw_json is not None else {}
        entry["raw_json"] = raw_json
    raw_json["ex_post_chapter_target_locator"] = {
        "version": BACKFILL_VERSION,
        "strategy": section_result["strategy"],
        "body_window": section_result["body_window"],
        "marker_family": section_result["selected_marker_family"],
        "marker_coverage": section_result["proposed_marker_coverage"],
        "ordinal_raw": entry_result.get("ordinal_raw"),
        "chapter_targets": [
            target_evidence(ref)
            for ref in entry_result.get("chapter_refs") or []
            if ref.get("status") == "resolved" and isinstance(ref.get("target"), dict)
        ],
    }
    if previous_target_file:
        raw_json["ex_post_chapter_target_locator"]["replaced_target_file"] = previous_target_file
        raw_json["ex_post_chapter_target_locator"]["replacement_reason"] = (
            "systematic_work_start_anchor_collapse"
        )


def is_systematic_work_start_anchor_collapse(
    payload: dict[str, Any],
    section: dict[str, Any],
    section_result: dict[str, Any],
    conflicts: list[dict[str, Any]],
    source_root: Path,
) -> bool:
    if (
        len(conflicts) < 5
        or int(section_result.get("segmented_run_count") or 0) < 2
        or float(section_result.get("proposed_marker_coverage") or 0.0) < 0.9
        or float(section_result.get("normalized_similarity_mean") or 0.0) < 0.75
        or any(
            float(value) < 0.45
            for value in section_result.get("segment_similarity_means") or []
        )
        or not section_result.get("resolved_targets_monotonic")
    ):
        return False
    work = next(
        (
            item
            for item in payload.get("works") or []
            if isinstance(item, dict) and item.get("work_key") == section.get("work_key")
        ),
        None,
    )
    if not work:
        return False
    work_start = comparable_path(work.get("start_file"), source_root)
    existing_paths = {
        comparable_path(item.get("existing_target_file"), source_root)
        for item in conflicts
    }
    proposed_paths = {
        comparable_path(value, source_root)
        for item in conflicts
        for value in item.get("proposed_target_files") or []
    }
    return (
        work_start is not None
        and existing_paths == {work_start}
        and len({path for path in proposed_paths if path is not None}) >= 2
    )


def prepare_payload_backfill(
    payload: dict[str, Any],
    probe: dict[str, Any],
    *,
    allowed_sections: set[str] | None = None,
    replace_collapsed_work_start_targets: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = copy.deepcopy(payload)
    volume = updated.get("volume") or {}
    source_root = Path(str(volume.get("source_root") or ""))
    if not source_root.is_absolute():
        source_root = PROJECT_ROOT / source_root
    source_root = source_root.resolve()

    section_results = {
        str(section.get("section_key")): section
        for section in probe.get("sections") or []
        if isinstance(section, dict) and section.get("section_key")
    }
    report_sections: list[dict[str, Any]] = []
    proposed_updates = 0
    conflicts = 0
    replaced_conflicts = 0
    cleared_collapsed_targets = 0
    verified_existing_targets = 0

    for section in updated.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_key = str(section.get("section_key") or "")
        result = section_results.get(section_key)
        if result is None:
            continue
        section_report: dict[str, Any] = {
            "section_key": section_key,
            "accepted": bool(result.get("marker_run_accepted")),
            "marker_family": result.get("selected_marker_family"),
            "marker_coverage": result.get("proposed_marker_coverage"),
            "segmented_run_count": result.get("segmented_run_count", 0),
            "segment_coverages": result.get("segment_coverages", []),
            "segment_similarity_means": result.get("segment_similarity_means", []),
            "normalized_similarity_mean": result.get("normalized_similarity_mean"),
            "ordinal_sources": result.get("ordinal_sources"),
            "entry_count": result.get("entry_count"),
            "chapter_ref_count": result.get("chapter_ref_count"),
            "unparsed_entry_count": result.get("unparsed_entry_count"),
            "proposed_resolved_chapter_count": result.get("proposed_resolved_chapter_count"),
            "proposed_missing_ordinals": result.get("proposed_missing_ordinals"),
            "body_window": result.get("body_window"),
            "search_eligible": result.get("search_eligible", True),
            "rejection_reason": result.get("rejection_reason"),
            "existing_conflicts": [],
            "updates": [],
            "cleared_targets": [],
            "verified_existing_targets": [],
        }
        report_sections.append(section_report)
        if allowed_sections is not None and section_key not in allowed_sections:
            section_report["accepted"] = False
            section_report["status"] = "excluded_by_selection"
            continue
        if not result.get("marker_run_accepted"):
            section_report["status"] = "rejected_marker_run"
            continue

        entries = [entry for entry in section.get("entries") or [] if isinstance(entry, dict)]
        entry_results = result.get("entries") or []
        if len(entries) != len(entry_results):
            section_report["status"] = "rejected_entry_count_mismatch"
            section_report["payload_entry_count"] = len(entries)
            section_report["probe_entry_count"] = len(entry_results)
            continue

        proposals: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
        conflicting_proposals: list[
            tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]
        ] = []
        matching_existing_proposals: list[
            tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]
        ] = []
        for entry, entry_result in zip(entries, entry_results):
            refs = [
                ref
                for ref in entry_result.get("chapter_refs") or []
                if ref.get("status") == "resolved" and isinstance(ref.get("target"), dict)
            ]
            if entry_result.get("status") != "resolved" or not refs:
                continue
            proposal_paths = [
                comparable_path(ref["target"]["target_file"], source_root)
                for ref in refs
            ]
            proposal_paths = [path for path in proposal_paths if path is not None]
            existing = comparable_path(entry.get("target_file"), source_root)
            if existing is not None and existing not in proposal_paths:
                section_report["existing_conflicts"].append(
                    {
                        "entry_key": entry.get("entry_key"),
                        "existing_target_file": entry.get("target_file"),
                        "proposed_target_files": [ref["target"]["target_file"] for ref in refs],
                    }
                )
                conflicting_proposals.append((entry, entry_result, refs))
                continue
            if existing is not None:
                matching_existing_proposals.append((entry, entry_result, refs))
                continue
            if existing is None:
                proposals.append((entry, entry_result, refs))

        if section_report["existing_conflicts"]:
            if replace_collapsed_work_start_targets and is_systematic_work_start_anchor_collapse(
                updated,
                section,
                result,
                section_report["existing_conflicts"],
                source_root,
            ):
                section_report["replacement_mode"] = "systematic_work_start_anchor_collapse"
                section_report["replaced_conflicts"] = section_report["existing_conflicts"]
                replaced_conflicts += len(section_report["existing_conflicts"])
                section_report["existing_conflicts"] = []
                proposals.extend(conflicting_proposals)
            else:
                section_report["status"] = "rejected_existing_target_conflict"
                conflicts += len(section_report["existing_conflicts"])
                continue

        prior_section_locator = (
            (section.get("raw_json") or {}).get("ex_post_chapter_target_locator")
            if isinstance(section.get("raw_json"), dict)
            else None
        )
        replacement_mode_active = bool(
            section_report.get("replacement_mode")
            or (
                isinstance(prior_section_locator, dict)
                and prior_section_locator.get("replacement_mode")
                == "systematic_work_start_anchor_collapse"
            )
            or any(
                isinstance(entry.get("raw_json"), dict)
                and "systematic_work_start_anchor" in str(
                    (entry["raw_json"].get("ex_post_chapter_target_locator") or {}).get(
                        "replacement_reason"
                    )
                )
                for entry in entries
            )
        )
        if replacement_mode_active:
            section_report["replacement_mode"] = "systematic_work_start_anchor_collapse"

        for entry, entry_result, refs in proposals:
            previous_target_file = entry.get("target_file")
            entry["target_file"] = refs[0]["target"]["target_file"]
            add_entry_evidence(
                entry,
                section_result=result,
                entry_result=entry_result,
                previous_target_file=str(previous_target_file) if previous_target_file else None,
            )
            section_report["updates"].append(
                {
                    "entry_key": entry.get("entry_key"),
                    "entry_order": entry.get("entry_order"),
                    "entry_raw": entry.get("entry_raw"),
                    "target_raw": entry.get("target_raw"),
                    "target_file": entry["target_file"],
                    "previous_target_file": previous_target_file,
                    "chapter_targets": [target_evidence(ref) for ref in refs],
                }
            )

        if replacement_mode_active:
            for entry, entry_result, refs in matching_existing_proposals:
                prior_entry_locator = (
                    (entry.get("raw_json") or {}).get("ex_post_chapter_target_locator")
                    if isinstance(entry.get("raw_json"), dict)
                    else None
                )
                if (
                    isinstance(prior_entry_locator, dict)
                    and prior_entry_locator.get("version") == BACKFILL_VERSION
                    and prior_entry_locator.get("chapter_targets")
                ):
                    continue
                add_entry_evidence(
                    entry,
                    section_result=result,
                    entry_result=entry_result,
                )
                verified = {
                    "entry_key": entry.get("entry_key"),
                    "entry_order": entry.get("entry_order"),
                    "entry_raw": entry.get("entry_raw"),
                    "target_file": entry.get("target_file"),
                    "chapter_targets": [target_evidence(ref) for ref in refs],
                    "action": "verify_existing_target",
                }
                section_report["verified_existing_targets"].append(verified)
                section_report["updates"].append(verified)
                verified_existing_targets += 1

            work = next(
                (
                    item
                    for item in updated.get("works") or []
                    if isinstance(item, dict)
                    and item.get("work_key") == section.get("work_key")
                ),
                None,
            )
            work_start = comparable_path(work.get("start_file"), source_root) if work else None
            for entry, entry_result in zip(entries, entry_results):
                refs = entry_result.get("chapter_refs") or []
                if (
                    not refs
                    or any(ref.get("status") == "resolved" for ref in refs)
                    or work_start is None
                    or comparable_path(entry.get("target_file"), source_root) != work_start
                ):
                    continue
                previous_target_file = str(entry["target_file"])
                entry["target_file"] = None
                add_entry_evidence(
                    entry,
                    section_result=result,
                    entry_result=entry_result,
                    previous_target_file=previous_target_file,
                )
                entry["raw_json"]["ex_post_chapter_target_locator"]["replacement_reason"] = (
                    "cleared_unresolved_systematic_work_start_anchor"
                )
                cleared = {
                    "entry_key": entry.get("entry_key"),
                    "entry_order": entry.get("entry_order"),
                    "entry_raw": entry.get("entry_raw"),
                    "previous_target_file": previous_target_file,
                    "target_file": None,
                    "action": "clear_unresolved_collapsed_target",
                }
                section_report["cleared_targets"].append(cleared)
                section_report["updates"].append(cleared)
                cleared_collapsed_targets += 1

        section_report["status"] = "accepted"
        section_report["update_count"] = len(section_report["updates"])
        proposed_updates += len(section_report["updates"])
        if section_report["updates"]:
            raw_json = section.get("raw_json")
            if not isinstance(raw_json, dict):
                raw_json = {"prior_raw_json": raw_json} if raw_json is not None else {}
                section["raw_json"] = raw_json
            raw_json["ex_post_chapter_target_locator"] = {
                "version": BACKFILL_VERSION,
                "strategy": result["strategy"],
                "body_window": result["body_window"],
                "marker_family": result["selected_marker_family"],
                "marker_coverage": result["proposed_marker_coverage"],
                "segmented_run_count": result.get("segmented_run_count", 0),
                "segment_coverages": result.get("segment_coverages", []),
                "segment_similarity_means": result.get("segment_similarity_means", []),
                "updated_entry_count": len(section_report["updates"]),
            }
            if section_report.get("replacement_mode"):
                raw_json["ex_post_chapter_target_locator"]["replacement_mode"] = (
                    section_report["replacement_mode"]
                )
                raw_json["ex_post_chapter_target_locator"]["replaced_conflict_count"] = len(
                    section_report.get("replaced_conflicts") or []
                )

    return updated, {
        "volume_id": volume.get("volume_id"),
        "payload_status": "proposed" if proposed_updates else "unchanged",
        "proposed_update_count": proposed_updates,
        "existing_conflict_count": conflicts,
        "replaced_conflict_count": replaced_conflicts,
        "cleared_collapsed_target_count": cleared_collapsed_targets,
        "verified_existing_target_count": verified_existing_targets,
        "sections": report_sections,
    }


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.chapter-target-backfill.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply deterministic chapter target backfill to existing payloads."
    )
    parser.add_argument(
        "--payload-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "index_payloads",
    )
    parser.add_argument("--glob", default="*_indices.json")
    parser.add_argument("--volume", action="append", default=[])
    parser.add_argument(
        "--section",
        action="append",
        default=[],
        help="Apply/propose only this exact section_key (repeatable)",
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--replace-collapsed-work-start-targets",
        action="store_true",
        help=(
            "Replace only high-confidence multi-segment conflicts whose existing targets "
            "all collapse to the associated work start."
        ),
    )
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()

    if args.apply and args.backup_dir is None:
        parser.error("--apply requires --backup-dir")

    requested = {value.upper() for value in args.volume}
    requested_sections = set(args.section) if args.section else None
    payloads = sorted(args.payload_dir.resolve().glob(args.glob))
    if requested:
        payloads = [
            path
            for path in payloads
            if path.name.removesuffix("_indices.json").upper() in requested
        ]
    if not payloads:
        raise SystemExit("No matching index payloads found")

    reports: list[dict[str, Any]] = []
    changed_payloads: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for index, payload_path in enumerate(payloads, start=1):
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            has_missing_chapter_entries = any(
                isinstance(section, dict)
                and is_chapter_section(section)
                and (
                    requested_sections is None
                    or str(section.get("section_key") or "") in requested_sections
                )
                and any(
                    isinstance(entry, dict) and not entry.get("target_file")
                    for entry in section.get("entries") or []
                )
                for section in payload.get("sections") or []
            )
            if not has_missing_chapter_entries and not args.replace_collapsed_work_start_targets:
                report = {
                    "volume_id": (payload.get("volume") or {}).get("volume_id"),
                    "payload_file": str(payload_path),
                    "payload_status": "unchanged",
                    "proposed_update_count": 0,
                    "existing_conflict_count": 0,
                    "replaced_conflict_count": 0,
                    "cleared_collapsed_target_count": 0,
                    "verified_existing_target_count": 0,
                    "sections": [],
                }
                reports.append(report)
                continue
            probe = run_probe(payload_path)
            updated, report = prepare_payload_backfill(
                payload,
                probe,
                allowed_sections=requested_sections,
                replace_collapsed_work_start_targets=args.replace_collapsed_work_start_targets,
            )
            report["payload_file"] = str(payload_path)
            reports.append(report)
            if report["proposed_update_count"]:
                changed_payloads.append((payload_path, updated, report))
            print(
                f"[{index}/{len(payloads)}] {report['volume_id']}: "
                f"updates={report['proposed_update_count']} "
                f"conflicts={report['existing_conflict_count']} "
                f"replaced={report['replaced_conflict_count']} "
                f"cleared={report['cleared_collapsed_target_count']} "
                f"verified={report['verified_existing_target_count']}"
            )
        except Exception as exc:
            reports.append(
                {
                    "volume_id": payload_path.name.removesuffix("_indices.json"),
                    "payload_file": str(payload_path),
                    "payload_status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "proposed_update_count": 0,
                    "existing_conflict_count": 0,
                    "replaced_conflict_count": 0,
                    "cleared_collapsed_target_count": 0,
                    "verified_existing_target_count": 0,
                    "sections": [],
                }
            )
            print(f"[{index}/{len(payloads)}] {payload_path.name}: ERROR {exc}")

    applied_files: list[str] = []
    if args.apply:
        backup_dir = args.backup_dir.resolve()
        backup_dir.mkdir(parents=True, exist_ok=False)
        for payload_path, updated, report in changed_payloads:
            backup_path = backup_dir / payload_path.name
            shutil.copy2(payload_path, backup_path)
            write_json_atomic(payload_path, updated)
            report["payload_status"] = "applied"
            report["backup_file"] = str(backup_path)
            applied_files.append(str(payload_path))

    summary = {
        "schema_version": 1,
        "operation": "ex_post_chapter_target_backfill",
        "mode": "apply" if args.apply else "dry_run",
        "payload_count": len(payloads),
        "changed_payload_count": len(changed_payloads),
        "proposed_update_count": sum(
            report["proposed_update_count"] for report in reports
        ),
        "existing_conflict_count": sum(
            report["existing_conflict_count"] for report in reports
        ),
        "replaced_conflict_count": sum(
            report["replaced_conflict_count"] for report in reports
        ),
        "cleared_collapsed_target_count": sum(
            report["cleared_collapsed_target_count"] for report in reports
        ),
        "verified_existing_target_count": sum(
            report["verified_existing_target_count"] for report in reports
        ),
        "error_count": sum(report["payload_status"] == "error" for report in reports),
        "selected_sections": sorted(requested_sections) if requested_sections else None,
        "applied_files": applied_files,
        "volumes": reports,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(args.report.resolve(), summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "volumes"}, indent=2))


if __name__ == "__main__":
    main()
