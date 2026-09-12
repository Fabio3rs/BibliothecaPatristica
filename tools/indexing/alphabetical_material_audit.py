"""Cross-audit extracted alphabetical payloads, DB rows, and OCR targets.

The auditor is deliberately read-only.  It emits repair proposals only when the
same deterministic gate used by the compact pipeline resolves a unique target.
"""

from __future__ import annotations

import json
import re
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .alphabetical_compact_driver import (
    _compact_helper_candidate,
    _helper_request,
    _merge_locator_candidates,
)
from .alphabetical_compact_pipeline import (
    build_deterministic_locator_results,
    build_locator_items,
)
from .index_target_locator import resolve_index_targets
from .index_payload_evidence import verify_index_payload_evidence


BOUNDARY_SECTION_KINDS = {"ordo_rerum", "editorial_closure"}
PRINTED_LOCATOR_RE = re.compile(
    r"(?<![\w.])\d{1,4}(?:\s*[A-D])?(?:\s*(?:[-–—,;]|seq\.?|seqq\.?|fin\.?))?",
    re.IGNORECASE,
)
FILE_NUMBER_RE = re.compile(r"(?:^|[-_])(\d+)(?:\.txt)?$", re.IGNORECASE)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _same_file(left: Any, right: Any) -> bool:
    if not left or not right:
        return False
    left_path = Path(str(left))
    right_path = Path(str(right))
    return str(left_path) == str(right_path) or left_path.name == right_path.name


def _file_number(value: Any) -> int | None:
    if not value:
        return None
    match = FILE_NUMBER_RE.search(Path(str(value)).name)
    return int(match.group(1)) if match else None


def _inside_section(target: Any, section: Mapping[str, Any]) -> bool:
    if not target:
        return False
    start = section.get("file_start")
    end = section.get("file_end")
    if _same_file(target, start) or _same_file(target, end):
        return True
    target_number = _file_number(target)
    start_number = _file_number(start)
    end_number = _file_number(end)
    if None in {target_number, start_number, end_number}:
        return False
    low, high = sorted((int(start_number), int(end_number)))
    return low <= int(target_number) <= high


def _is_source_only(entry: Mapping[str, Any], section: Mapping[str, Any]) -> bool:
    for raw in (entry.get("raw_json"), section.get("raw_json")):
        if _json_object(raw).get("material_reference_mode") == "source_only":
            return True
    return False


def _issue(kind: str, severity: str, **detail: Any) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, **detail}


def _sample_evenly(items: Sequence[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    if limit is None or limit <= 0 or len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    indexes = sorted(
        {round(index * (len(items) - 1) / (limit - 1)) for index in range(limit)}
    )
    return [items[index] for index in indexes]


def _load_db_snapshot(db_path: Path | None, volume_id: str) -> dict[str, Any]:
    empty = {
        "available": False,
        "volume_found": False,
        "sections": {},
        "entries": {},
        "refs": {},
    }
    if db_path is None or not db_path.exists():
        return empty
    uri = f"file:{db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30.0) as con:
        con.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required = {
            "alphabetical_volumes",
            "alphabetical_sections",
            "alphabetical_entries",
            "alphabetical_refs",
        }
        if not required <= tables:
            return empty
        volume = con.execute(
            "SELECT volume_id, collection, source_root FROM alphabetical_volumes WHERE volume_id = ?",
            (volume_id,),
        ).fetchone()
        if volume is None:
            return {**empty, "available": True}
        sections = {
            str(row["section_key"]): dict(row)
            for row in con.execute(
                "SELECT section_key, section_kind, heading_raw, file_start, file_end "
                "FROM alphabetical_sections WHERE volume_id = ?",
                (volume_id,),
            )
        }
        entries = {
            str(row["entry_key"]): dict(row)
            for row in con.execute(
                """
                SELECT e.entry_key, e.section_key, e.entry_order, e.entry_kind,
                       e.lemma_raw, e.entry_raw,
                       COALESCE(
                           e.resolved_target_ocr_file, e.target_file_best
                       ) AS resolved_target_ocr_file
                FROM alphabetical_entries e
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id = ?
                """,
                (volume_id,),
            )
        }
        refs = {
            (str(row["entry_key"]), int(row["ref_order"])): dict(row)
            for row in con.execute(
                """
                SELECT r.ref_id, r.entry_key, r.ref_order, r.ref_kind,
                       r.ref_raw,
                       COALESCE(
                           r.cited_editorial_page_start_number, r.page_ref_int
                       ) AS cited_editorial_page_start_number,
                       COALESCE(
                           r.resolved_target_ocr_file, r.target_file
                       ) AS resolved_target_ocr_file,
                       COALESCE(
                           r.target_ocr_file_candidate_score,
                           r.target_file_probability
                       ) AS target_ocr_file_candidate_score,
                       r.locator_status
                FROM alphabetical_refs r
                JOIN alphabetical_entries e ON e.entry_key = r.entry_key
                JOIN alphabetical_sections s ON s.section_key = e.section_key
                WHERE s.volume_id = ?
                """,
                (volume_id,),
            )
        }
        return {
            "available": True,
            "volume_found": True,
            "volume": dict(volume),
            "sections": sections,
            "entries": entries,
            "refs": refs,
        }


def _structural_audit(
    payload: Mapping[str, Any],
    db: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sections = {
        str(item.get("section_key") or ""): item
        for item in payload.get("sections") or []
        if isinstance(item, Mapping) and item.get("section_key")
    }
    entries = {
        str(item.get("entry_key") or ""): item
        for item in payload.get("entries") or []
        if isinstance(item, Mapping) and item.get("entry_key")
    }
    refs = {
        (str(item.get("entry_key") or ""), int(item.get("ref_order") or 0)): item
        for item in payload.get("refs") or []
        if isinstance(item, Mapping)
        and item.get("entry_key")
        and isinstance(item.get("ref_order"), int)
    }
    issues: list[dict[str, Any]] = []
    refs_by_entry: dict[str, list[Mapping[str, Any]]] = {}
    for (entry_key, _), ref in refs.items():
        refs_by_entry.setdefault(entry_key, []).append(ref)

    for section_key, section in sections.items():
        if str(section.get("section_kind") or "") in BOUNDARY_SECTION_KINDS:
            issues.append(
                _issue(
                    "boundary_section_emitted",
                    "error",
                    section_key=section_key,
                    section_kind=section.get("section_kind"),
                    heading_raw=section.get("heading_raw"),
                )
            )
        section_entries = [
            entry for entry in entries.values() if entry.get("section_key") == section_key
        ]
        if not section_entries:
            issues.append(
                _issue(
                    "empty_section",
                    "warning",
                    section_key=section_key,
                    heading_raw=section.get("heading_raw"),
                )
            )

    for entry_key, entry in entries.items():
        section = sections.get(str(entry.get("section_key") or ""), {})
        entry_refs = refs_by_entry.get(entry_key, [])
        raw = str(entry.get("entry_raw") or entry.get("context_raw") or "")
        if not entry_refs and PRINTED_LOCATOR_RE.search(raw) and not _is_source_only(entry, section):
            issues.append(
                _issue(
                    "locator_text_without_refs",
                    "error",
                    entry_key=entry_key,
                    entry_raw=raw[:300],
                )
            )
        orders = sorted(int(ref.get("ref_order") or 0) for ref in entry_refs)
        if orders and orders != list(range(1, max(orders) + 1)):
            issues.append(
                _issue(
                    "ref_order_gap",
                    "warning",
                    entry_key=entry_key,
                    ref_orders=orders,
                )
            )

    for pair, ref in refs.items():
        entry = entries.get(pair[0], {})
        section = sections.get(str(entry.get("section_key") or ""), {})
        target = ref.get("target_file")
        if target and not Path(str(target)).exists():
            issues.append(
                _issue(
                    "target_file_missing",
                    "error",
                    entry_key=pair[0],
                    ref_order=pair[1],
                    target_file=target,
                )
            )
        if target and _inside_section(target, section):
            issues.append(
                _issue(
                    "target_inside_index_section",
                    "error",
                    entry_key=pair[0],
                    ref_order=pair[1],
                    target_file=target,
                    section_key=entry.get("section_key"),
                )
            )

    if db.get("available") and not db.get("volume_found"):
        issues.append(_issue("volume_missing_from_db", "error"))
    elif db.get("volume_found"):
        comparisons = (
            ("section", set(sections), set(db["sections"])),
            ("entry", set(entries), set(db["entries"])),
            ("ref", set(refs), set(db["refs"])),
        )
        for label, payload_keys, db_keys in comparisons:
            for side, values in (
                ("db", sorted(payload_keys - db_keys)),
                ("payload", sorted(db_keys - payload_keys)),
            ):
                if values:
                    issues.append(
                        _issue(
                            f"{label}_missing_from_{side}",
                            "error",
                            count=len(values),
                            sample=values[:25],
                        )
                    )
        target_drifts: list[dict[str, Any]] = []
        for pair in sorted(set(refs) & set(db["refs"])):
            payload_target = refs[pair].get("target_file")
            db_target = db["refs"][pair].get("resolved_target_ocr_file")
            if bool(payload_target) != bool(db_target) or (
                payload_target and db_target and not _same_file(payload_target, db_target)
            ):
                target_drifts.append(
                    {
                        "entry_key": pair[0],
                        "ref_order": pair[1],
                        "payload_target_ocr_file": payload_target,
                        "db_target_ocr_file": db_target,
                    }
                )
        if target_drifts:
            issues.append(
                _issue(
                    "target_drift_payload_db",
                    "warning",
                    count=len(target_drifts),
                    sample=target_drifts[:25],
                )
            )

    summary = {
        "payload_section_count": len(sections),
        "payload_entry_count": len(entries),
        "payload_ref_count": len(refs),
        "db_available": bool(db.get("available")),
        "db_volume_found": bool(db.get("volume_found")),
        "db_section_count": len(db.get("sections") or {}),
        "db_entry_count": len(db.get("entries") or {}),
        "db_ref_count": len(db.get("refs") or {}),
    }
    return issues, summary


def _locator_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(payload))
    boundary_keys = {
        str(section.get("section_key") or "")
        for section in result.get("sections") or []
        if isinstance(section, Mapping)
        and str(section.get("section_kind") or "") in BOUNDARY_SECTION_KINDS
    }
    if not boundary_keys:
        return result
    result["sections"] = [
        section
        for section in result.get("sections") or []
        if str(section.get("section_key") or "") not in boundary_keys
    ]
    kept_entries = {
        str(entry.get("entry_key") or "")
        for entry in result.get("entries") or []
        if str(entry.get("section_key") or "") not in boundary_keys
    }
    result["entries"] = [
        entry
        for entry in result.get("entries") or []
        if str(entry.get("entry_key") or "") in kept_entries
    ]
    for field in ("refs", "scripture_refs"):
        result[field] = [
            item
            for item in result.get(field) or []
            if str(item.get("entry_key") or "") in kept_entries
        ]
    result["nodes"] = [
        node
        for node in result.get("nodes") or []
        if str(node.get("section_key") or "") not in boundary_keys
    ]
    return result


def _mechanical_audit(
    payload: Mapping[str, Any],
    *,
    db: Mapping[str, Any],
    source_root: Path,
    max_refs: int | None,
    workers: int,
) -> dict[str, Any]:
    locator_items = build_locator_items(_locator_payload(payload))
    refs_by_pair = {
        (str(ref.get("entry_key") or ""), int(ref.get("ref_order") or 0)): ref
        for ref in payload.get("refs") or []
        if isinstance(ref, Mapping)
    }
    with_target = [
        item
        for item in locator_items
        if refs_by_pair.get((str(item["entry_key"]), int(item["ref_order"])), {}).get(
            "target_file"
        )
    ]
    without_target = [item for item in locator_items if item not in with_target]
    if max_refs is None or max_refs <= 0:
        selected = locator_items
    elif with_target and without_target:
        target_limit = max(1, max_refs // 2)
        selected_targets = _sample_evenly(with_target, min(target_limit, max_refs))
        remaining = max_refs - len(selected_targets)
        selected = [
            *selected_targets,
            *(_sample_evenly(without_target, remaining) if remaining > 0 else []),
        ]
    else:
        selected = _sample_evenly(locator_items, max_refs)
    selected_keys = {str(item["locator_key"]) for item in selected}
    request = _helper_request(
        locator_items,
        volume_id=str((payload.get("volume") or {}).get("volume_id") or ""),
        source_root=source_root,
        workers=workers,
    )
    request["entries"] = [
        entry
        for entry in request["entries"]
        if str(entry.get("entry_id") or "") in selected_keys
    ]
    helper = resolve_index_targets(request)
    helper_by_key = {
        str(item.get("entry_id") or ""): item
        for item in helper.get("entries") or []
        if isinstance(item, Mapping)
    }
    enriched: list[dict[str, Any]] = []
    for item in selected:
        local = deepcopy(item)
        result = helper_by_key.get(str(item["locator_key"]), {})
        candidates: list[dict[str, Any]] = []
        for rank, candidate in enumerate(result.get("candidates") or [], start=1):
            if rank > 3 or not isinstance(candidate, Mapping) or not candidate.get("file"):
                continue
            compact = _compact_helper_candidate(candidate)
            compact.update(
                {
                    "helper_status": result.get("status") or "unresolved",
                    "helper_rank": rank,
                    "helper_is_best": rank == 1,
                }
            )
            candidates.append(compact)
        local["candidates"] = _merge_locator_candidates([], candidates)
        enriched.append(local)
    deterministic, _pending = build_deterministic_locator_results(enriched)
    deterministic_by_pair = {
        (str(item["entry_key"]), int(item["ref_order"])): item
        for item in deterministic
    }

    results: list[dict[str, Any]] = []
    action_counts: dict[str, int] = {}
    db_action_counts: dict[str, int] = {}
    for item in enriched:
        pair = (str(item["entry_key"]), int(item["ref_order"]))
        stored_target = refs_by_pair.get(pair, {}).get("target_file")
        db_target = (db.get("refs") or {}).get(pair, {}).get(
            "resolved_target_ocr_file"
        )
        helper_result = helper_by_key.get(str(item["locator_key"]), {})
        best = helper_result.get("best_candidate") or {}
        deterministic_result = deterministic_by_pair.get(pair)
        proposed_target = (
            deterministic_result.get("target_file") if deterministic_result else None
        )
        if proposed_target and not stored_target:
            action = "fill_missing_target"
        elif proposed_target and stored_target and not _same_file(proposed_target, stored_target):
            action = "replace_suspicious_target"
        elif proposed_target and _same_file(proposed_target, stored_target):
            action = "confirm_existing_target"
        elif stored_target and best.get("file") and not _same_file(best.get("file"), stored_target):
            action = "review_competing_target"
        else:
            action = "keep_pending"
        action_counts[action] = action_counts.get(action, 0) + 1
        if not db.get("volume_found"):
            db_action = "import_volume_first"
        elif proposed_target and not db_target:
            db_action = "fill_db_target"
        elif proposed_target and db_target and not _same_file(proposed_target, db_target):
            db_action = "replace_db_target"
        elif proposed_target and _same_file(proposed_target, db_target):
            db_action = "confirm_db_target"
        else:
            db_action = "keep_db_pending"
        db_action_counts[db_action] = db_action_counts.get(db_action, 0) + 1
        top_candidate = (helper_result.get("candidates") or [{}])[0]
        results.append(
            {
                "entry_key": pair[0],
                "ref_order": pair[1],
                "lemma_raw": item.get("lemma_raw"),
                "ref_raw": item.get("ref_raw"),
                "stored_target_ocr_file": stored_target,
                "db_stored_target_ocr_file": db_target,
                "helper_status": helper_result.get("status"),
                "helper_best_ocr_file": best.get("file"),
                "helper_probability": best.get("probability"),
                "top_evidence_kinds": [
                    evidence.get("kind")
                    for evidence in top_candidate.get("evidence") or []
                    if isinstance(evidence, Mapping)
                ],
                "action": action,
                "db_action": db_action,
                "proposed_target_ocr_file": proposed_target,
                "proposal_confidence": (
                    deterministic_result.get("confidence")
                    if deterministic_result
                    else None
                ),
            }
        )
    return {
        "eligible_ref_count": len(locator_items),
        "sampled_ref_count": len(enriched),
        "action_counts": action_counts,
        "db_action_counts": db_action_counts,
        "results": results,
    }


def audit_extracted_material(
    payload: Mapping[str, Any],
    *,
    db_path: Path | None = None,
    max_refs: int | None = 50,
    workers: int = 1,
    run_ocr: bool = True,
) -> dict[str, Any]:
    volume = payload.get("volume") or {}
    volume_id = str(volume.get("volume_id") or "").strip()
    if not volume_id:
        raise ValueError("payload.volume.volume_id is required")
    source_root = Path(str(volume.get("source_root") or "")).expanduser()
    db = _load_db_snapshot(db_path, volume_id)
    issues, counts = _structural_audit(payload, db)
    mechanical: dict[str, Any] | None = None
    mechanical_error: str | None = None
    source_evidence: dict[str, Any] | None = None
    source_evidence_error: str | None = None
    if run_ocr:
        if not source_root.is_dir():
            mechanical_error = f"source_root not found: {source_root}"
            source_evidence_error = mechanical_error
        else:
            try:
                source_evidence = verify_index_payload_evidence(
                    dict(payload),
                    sample_size=max_refs,
                )
            except Exception as exc:
                source_evidence_error = f"{type(exc).__name__}: {exc}"
            try:
                mechanical = _mechanical_audit(
                    payload,
                    db=db,
                    source_root=source_root,
                    max_refs=max_refs,
                    workers=workers,
                )
            except Exception as exc:
                mechanical_error = f"{type(exc).__name__}: {exc}"
    issue_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for issue in issues:
        issue_counts[issue["kind"]] = issue_counts.get(issue["kind"], 0) + 1
        severity_counts[issue["severity"]] = severity_counts.get(issue["severity"], 0) + 1
    review_queue: list[dict[str, Any]] = []
    for issue in issues:
        if issue["severity"] == "error":
            review_queue.append(
                {
                    "priority": "high",
                    "category": "structural_issue",
                    "detail": issue,
                }
            )
    for result in (mechanical or {}).get("results") or []:
        action = result.get("action")
        if action in {"fill_missing_target", "replace_suspicious_target"}:
            review_queue.append(
                {
                    "priority": "high",
                    "category": "deterministic_target_repair",
                    "detail": result,
                }
            )
        elif action == "review_competing_target":
            review_queue.append(
                {
                    "priority": "medium",
                    "category": "competing_target",
                    "detail": result,
                }
            )
    for result in (source_evidence or {}).get("results") or []:
        if result.get("segmentation_suspect"):
            priority = "high"
            category = "segmentation_suspect"
        elif result.get("method") == "exact_neighbor_outside_declared_scope":
            priority = "high"
            category = "source_scope_mismatch"
        elif result.get("method") in {"unverified", "missing_physical_source"}:
            priority = "medium"
            category = "source_text_unverified"
        else:
            continue
        review_queue.append(
            {
                "priority": priority,
                "category": category,
                "detail": result,
            }
        )
    return {
        "schema_version": 2,
        "mode": "read_only_audit",
        "volume_id": volume_id,
        "payload_counts": counts,
        "issue_counts": issue_counts,
        "severity_counts": severity_counts,
        "issues": issues,
        "source_evidence": source_evidence,
        "source_evidence_error": source_evidence_error,
        "mechanical": mechanical,
        "mechanical_error": mechanical_error,
        "review_queue_count": len(review_queue),
        "review_queue": review_queue,
    }
