from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .common import page_sort_key
from .index_target_locator import normalize_for_search, resolve_index_targets


INDEX_SOURCE_SCOPE_KINDS = {
    "volume_front",
    "volume_end",
    "volume_table",
    "fascicle_inventory",
    "retrospective_table",
}
DEFINITE_INDEX_SOURCE_SCOPE_KINDS = {
    "volume_table",
    "fascicle_inventory",
    "retrospective_table",
}
INDEX_SOURCE_LABEL_RE = re.compile(
    r"\b(?:catalog\w*|contents|elenchus|index|indices|inventar\w*|ordo|sommaire|table|tabula)\b"
)
EXACT_TITLE_EVIDENCE_KINDS = {
    "header_name_match",
    "body_name_match",
    "footer_name_match",
}
FUZZY_TITLE_EVIDENCE_KINDS = {
    "header_name_fuzzy",
    "body_name_fuzzy",
    "footer_name_fuzzy",
}
TOKEN_TITLE_EVIDENCE_KINDS = {
    "header_token_cover",
    "body_token_cover",
    "header_sequence_match",
    "header_sequence_start",
}
TITLE_EVIDENCE_KINDS = (
    EXACT_TITLE_EVIDENCE_KINDS
    | FUZZY_TITLE_EVIDENCE_KINDS
    | TOKEN_TITLE_EVIDENCE_KINDS
)
MIN_TITLE_SCORE = 5.5
MIN_TITLE_SCORE_GAP = 1.5
MIN_EDITORIAL_CONFIDENCE = 0.6


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*", value):
        return int(value)
    return None


def _resolve_source_path(source_root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = source_root / path
    return path.resolve()


def _best_guess_pages(item: dict[str, Any]) -> list[int]:
    value = item.get("best_guess")
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [
            page
            for raw in value
            if (page := _as_int(raw)) is not None
        ]
    return []


def _source_files_and_positions(source_root: Path) -> tuple[list[Path], dict[str, int]]:
    files = sorted(source_root.glob("*.txt"), key=page_sort_key)
    return files, {str(path.resolve()): index for index, path in enumerate(files)}


def _index_source_files(
    payload: dict[str, Any],
    *,
    source_root: Path,
    source_files: list[Path],
    positions: dict[str, int],
) -> set[str]:
    result: set[str] = set()
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue
        scope_kind = str(section.get("scope_kind") or "")
        if scope_kind not in INDEX_SOURCE_SCOPE_KINDS:
            continue
        label = normalize_for_search(
            " ".join(
                str(section.get(key) or "")
                for key in ("index_kind", "heading_raw", "heading_norm")
            )
        )
        if (
            scope_kind not in DEFINITE_INDEX_SOURCE_SCOPE_KINDS
            and label
            and INDEX_SOURCE_LABEL_RE.search(label) is None
        ):
            continue
        start = _resolve_source_path(source_root, section.get("file_start"))
        end = _resolve_source_path(source_root, section.get("file_end"))
        if start is None and end is None:
            continue
        start_key = str((start or end).resolve())
        end_key = str((end or start).resolve())
        if start_key in positions and end_key in positions:
            left, right = sorted((positions[start_key], positions[end_key]))
            result.update(str(path.resolve()) for path in source_files[left : right + 1])
        else:
            result.update(key for key in (start_key, end_key) if key in positions)
    return result


def _editorial_maps(
    editorial_pages: dict[str, Any] | None,
    *,
    source_root: Path,
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    by_page: dict[int, list[dict[str, Any]]] = {}
    by_file: dict[str, dict[str, Any]] = {}
    for raw in (editorial_pages or {}).get("files") or []:
        if not isinstance(raw, dict):
            continue
        path = _resolve_source_path(source_root, raw.get("file"))
        if path is None:
            continue
        item = dict(raw)
        item["_resolved_file"] = str(path)
        item["_pages"] = _best_guess_pages(item)
        by_file[str(path)] = item
        for page in item["_pages"]:
            by_page.setdefault(page, []).append(item)
    return by_page, by_file


def _work_queries(work: dict[str, Any]) -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip(" /,;:-")
        if len(value) < 5 or value in candidates:
            return
        candidates.append(value)

    def add_title_variants(value: str) -> None:
        add(value)
        # Contents tables commonly append language, editor, translator, or
        # publication details that the real title page phrases differently.
        # Keep the descriptive form, but also search the stable title nucleus.
        prefix = re.split(r"\s*[\(\[]", value, maxsplit=1)[0]
        normalized_prefix = normalize_for_search(prefix)
        if len(normalized_prefix) >= 12 and len(normalized_prefix.split()) >= 3:
            add(prefix)

    title_norm = re.sub(r"\s+", " ", str(work.get("title_norm") or "")).strip(" /")
    add_title_variants(title_norm)
    title_raw = re.sub(r"\s+", " ", str(work.get("title_raw") or "")).strip(" /")
    for value in re.split(r"\s+/\s+|[;]", title_raw):
        add_title_variants(value)
    return candidates[:6]


def _is_composite_work_container(work: dict[str, Any]) -> bool:
    parts = [
        part.strip()
        for part in re.split(r"\s+/\s+", str(work.get("title_raw") or ""))
        if part.strip()
    ]
    return len(parts) >= 4


def _iter_nested_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_iter_nested_strings(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_iter_nested_strings(item))
        return result
    return []


def _has_resolved_manual_anchor_review(work: dict[str, Any]) -> bool:
    raw_json = work.get("raw_json")
    if not isinstance(raw_json, dict):
        return False
    review = raw_json.get("manual_anchor_review")
    return (
        isinstance(review, dict)
        and review.get("status") in {"resolved", "resolved_unlocated"}
    )


def _existing_index_page_hints(
    payload: dict[str, Any],
    *,
    work: dict[str, Any],
) -> list[int]:
    queries = _work_queries(work)
    if not queries:
        return []
    title_tokens = {
        token
        for token in normalize_for_search(queries[0]).split()
        if len(token) >= 3 or re.fullmatch(r"[ivxlcdm]+", token)
    }
    if len(title_tokens) < 2:
        return []
    hints: set[int] = set()
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for entry in section.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            entry_strings = [
                str(entry.get(field) or "")
                for field in ("target_raw", "normalized_target", "entry_raw")
            ]
            entry_matches = any(
                title_tokens.issubset(set(normalize_for_search(text).split()))
                for text in entry_strings
                if text
            )
            if entry_matches:
                page_ref = _as_int(entry.get("page_ref_int"))
                if page_ref is not None:
                    hints.add(page_ref)
            for text in _iter_nested_strings(entry.get("raw_json")):
                normalized_tokens = set(normalize_for_search(text).split())
                if not title_tokens.issubset(normalized_tokens):
                    continue
                numbers = [
                    int(match.group(0))
                    for match in re.finditer(r"(?<!\d)\d{1,4}(?!\d)", text)
                ]
                if numbers:
                    hints.add(numbers[-1])
    return sorted(hints)


def _candidate_has_title_evidence(candidate: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict)
        and str(item.get("kind") or "") in TITLE_EVIDENCE_KINDS
        for item in candidate.get("evidence") or []
    )


def _candidate_has_material_start_sequence(
    candidate: dict[str, Any],
    *,
    preferred_page: int | None,
) -> bool:
    sequence = candidate.get("header_sequence")
    if not isinstance(sequence, dict):
        return False
    evidence_kinds = {
        str(item.get("kind") or "")
        for item in candidate.get("evidence") or []
        if isinstance(item, dict)
    }
    estimator_pages = [
        page
        for raw in candidate.get("estimator_pages") or []
        if (page := _as_int(raw)) is not None
    ]
    estimator_confidence = float(candidate.get("estimator_confidence") or 0.0)
    editorial_page_supported = (
        preferred_page in estimator_pages
        and estimator_confidence >= MIN_EDITORIAL_CONFIDENCE
        if preferred_page is not None
        else _candidate_editorial_page(candidate) is not None
    )
    return (
        str(candidate.get("candidate_role") or "target_candidate")
        == "target_candidate"
        and bool(sequence.get("is_sequence_start"))
        and int(sequence.get("match_count") or 0) >= 3
        and bool(
            evidence_kinds
            & (EXACT_TITLE_EVIDENCE_KINDS | FUZZY_TITLE_EVIDENCE_KINDS)
        )
        and editorial_page_supported
    )


def _candidate_supports_preferred_page(
    candidate: dict[str, Any],
    preferred_page: int | None,
) -> bool:
    if preferred_page is None:
        return _candidate_editorial_page(candidate) is not None
    estimator_pages = [
        page
        for raw in candidate.get("estimator_pages") or []
        if (page := _as_int(raw)) is not None
    ]
    return (
        preferred_page in estimator_pages
        and float(candidate.get("estimator_confidence") or 0.0)
        >= MIN_EDITORIAL_CONFIDENCE
    )


def _choose_text_candidate(
    result: dict[str, Any],
    *,
    excluded_files: set[str],
    current_start_file: str | None,
    preferred_page: int | None,
) -> tuple[dict[str, Any] | None, str]:
    locator_status = str(result.get("status") or "")
    candidates = [
        item
        for item in result.get("candidates") or []
        if isinstance(item, dict)
        and str(Path(str(item.get("file") or "")).resolve()) not in excluded_files
        and str(item.get("candidate_role") or "target_candidate")
        != "index_page_candidate"
        and _candidate_has_title_evidence(item)
    ]
    if not candidates:
        if locator_status != "resolved":
            reason = str(
                result.get("ambiguity_reason")
                or locator_status
                or "unresolved"
            )
            return None, f"locator_{reason}"
        return None, "no_non_index_candidate_with_title_evidence"
    best = candidates[0]
    if locator_status != "resolved":
        if _candidate_has_material_start_sequence(
            best,
            preferred_page=preferred_page,
        ):
            return best, "resolved_by_material_start_sequence"
        reason = str(result.get("ambiguity_reason") or locator_status or "unresolved")
        return None, f"locator_{reason}"
    preferred = next(
        (
            candidate
            for candidate in candidates
            if str(Path(str(candidate.get("file") or "")).resolve())
            == str(Path(current_start_file).resolve())
        ),
        None,
    ) if current_start_file else None
    if preferred is not None:
        preferred_sequence = preferred.get("header_sequence")
        best_sequence = best.get("header_sequence")
        same_sequence = (
            isinstance(preferred_sequence, dict)
            and isinstance(best_sequence, dict)
            and preferred_sequence.get("start_file") == best_sequence.get("start_file")
            and preferred_sequence.get("end_file") == best_sequence.get("end_file")
        )
        if (
            same_sequence
            and preferred_sequence.get("is_sequence_start")
            and int(preferred_sequence.get("match_count") or 0)
            >= 4
            and _candidate_editorial_page(preferred) is not None
        ):
            return preferred, "current_anchor_confirmed_by_header_sequence"
    best_score = float(best.get("score") or 0.0)
    runner_up_score = (
        float(candidates[1].get("score") or 0.0)
        if len(candidates) > 1
        else 0.0
    )
    if best_score < MIN_TITLE_SCORE:
        return None, "candidate_score_below_threshold"
    if _candidate_has_material_start_sequence(
        best,
        preferred_page=preferred_page,
    ):
        return best, "resolved_by_material_start_sequence"
    if best_score - runner_up_score < MIN_TITLE_SCORE_GAP:
        return None, "eligible_candidate_score_gap"
    if (
        current_start_file is None
        and preferred_page is not None
        and not _candidate_supports_preferred_page(best, preferred_page)
    ):
        return None, "candidate_declared_page_not_supported"
    if _candidate_editorial_page(best, preferred_page=preferred_page) is None:
        return None, "candidate_editorial_page_not_supported"
    return best, "resolved"


def _rerun_candidate_summary(
    locator_result: dict[str, Any] | None,
    *,
    excluded_files: set[str],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for candidate in (locator_result or {}).get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        resolved_file = str(Path(str(candidate.get("file") or "")).resolve())
        summaries.append(
            {
                "file": candidate.get("file"),
                "image_file": candidate.get("image_file"),
                "score": candidate.get("score"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "inferred_printed_page": candidate.get("inferred_printed_page"),
                "estimator_pages": candidate.get("estimator_pages"),
                "estimator_confidence": candidate.get("estimator_confidence"),
                "header_sequence": candidate.get("header_sequence"),
                "excluded_index_source": resolved_file in excluded_files,
                "evidence": candidate.get("evidence") or [],
            }
        )
        if len(summaries) >= 5:
            break
    return summaries


def _set_work_anchor_rerun(
    work: dict[str, Any],
    marker: dict[str, Any] | None,
) -> bool:
    raw_json = work.get("raw_json")
    if not isinstance(raw_json, dict):
        raw_json = {"value": raw_json} if raw_json is not None else {}
    previous = raw_json.get("work_anchor_rerun")
    if marker is None:
        if "work_anchor_rerun" not in raw_json:
            return False
        del raw_json["work_anchor_rerun"]
    else:
        if previous == marker:
            return False
        raw_json["work_anchor_rerun"] = marker
    work["raw_json"] = raw_json
    return True


def _work_anchor_rerun_marker(
    *,
    inspection: dict[str, Any],
    work: dict[str, Any],
    locator_result: dict[str, Any] | None,
    decision_reason: str,
    excluded_files: set[str],
) -> dict[str, Any]:
    locator_status = str((locator_result or {}).get("status") or "unresolved")
    return {
        "status": "ambiguous" if locator_status == "ambiguous" else "unresolved",
        "reason": decision_reason,
        "inspection_reasons": list(inspection.get("reasons") or []),
        "query_names": _work_queries(work),
        "declared_anchor": {
            "start_page": work.get("start_page"),
            "start_file": work.get("start_file"),
        },
        "locator": {
            "status": locator_status,
            "ambiguity_reason": (locator_result or {}).get("ambiguity_reason"),
            "candidates": _rerun_candidate_summary(
                locator_result,
                excluded_files=excluded_files,
            ),
        },
        "agent_checks": [
            "inspect each competing OCR file and its neighboring files",
            "when a paired page image exists on disk, inspect it to resolve OCR text or printed-page ambiguity",
            "distinguish a work title page from an index, catalogue, or running header",
            "verify the editorial-page sequence without using the OCR filename suffix",
            "leave the anchor unresolved when direct OCR evidence does not decide",
        ],
    }


def _candidate_editorial_page(
    candidate: dict[str, Any],
    *,
    preferred_page: int | None = None,
) -> int | None:
    inferred = _as_int(candidate.get("inferred_printed_page"))
    estimator_pages = [
        page
        for raw in candidate.get("estimator_pages") or []
        if (page := _as_int(raw)) is not None
    ]
    confidence = float(candidate.get("estimator_confidence") or 0.0)
    if (
        preferred_page is not None
        and preferred_page in estimator_pages
        and confidence >= MIN_EDITORIAL_CONFIDENCE
    ):
        return preferred_page
    if (
        inferred is not None
        and inferred in estimator_pages
        and confidence >= MIN_EDITORIAL_CONFIDENCE
    ):
        return inferred
    if len(estimator_pages) == 1 and confidence >= MIN_EDITORIAL_CONFIDENCE:
        return estimator_pages[0]
    if (
        len(estimator_pages) == 2
        and estimator_pages[1] == estimator_pages[0] + 1
        and confidence >= 0.82
    ):
        return estimator_pages[0]
    return None


def inspect_declared_work_anchors(
    payload: dict[str, Any],
    *,
    editorial_pages: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    volume = payload.get("volume") or {}
    source_root = Path(str(volume.get("source_root") or "")).expanduser().resolve()
    source_files, positions = _source_files_and_positions(source_root)
    index_files = _index_source_files(
        payload,
        source_root=source_root,
        source_files=source_files,
        positions=positions,
    )
    _by_page, by_file = _editorial_maps(editorial_pages, source_root=source_root)
    results: list[dict[str, Any]] = []
    for index, work in enumerate(payload.get("works") or []):
        if not isinstance(work, dict):
            continue
        reasons: list[str] = []
        start_page = _as_int(work.get("start_page"))
        end_page = _as_int(work.get("end_page"))
        start_file = _resolve_source_path(source_root, work.get("start_file"))
        start_key = str(start_file) if start_file else None
        if start_page is not None and end_page is not None and start_page > end_page:
            reasons.append("start_page_after_end_page")
        if start_file is None:
            reasons.append("missing_start_file")
        elif not start_file.is_file():
            reasons.append("start_file_not_found")
        elif start_key not in positions:
            reasons.append("start_file_outside_source_root")
        if start_key in index_files:
            reasons.append("start_file_points_to_index_source")
        current_estimate = by_file.get(start_key or "")
        if (
            start_page is not None
            and current_estimate is not None
            and start_page not in current_estimate.get("_pages", [])
        ):
            reasons.append("start_page_not_supported_by_start_file")
        results.append(
            {
                "work_index": index,
                "work_key": work.get("work_key"),
                "title_raw": work.get("title_raw"),
                "status": "suspicious" if reasons else "ok",
                "reasons": reasons,
                "start_page": start_page,
                "end_page": end_page,
                "start_file": start_key,
            }
        )
    return results


def reconcile_work_anchors(
    payload: dict[str, Any],
    *,
    editorial_pages: dict[str, Any] | None,
    locator: Callable[[dict[str, Any]], dict[str, Any]] = resolve_index_targets,
) -> dict[str, Any]:
    volume = payload.get("volume") or {}
    volume_id = str(volume.get("volume_id") or "")
    source_root = Path(str(volume.get("source_root") or "")).expanduser().resolve()
    source_files, positions = _source_files_and_positions(source_root)
    index_files = _index_source_files(
        payload,
        source_root=source_root,
        source_files=source_files,
        positions=positions,
    )
    by_page, _by_file = _editorial_maps(editorial_pages, source_root=source_root)
    inspections = inspect_declared_work_anchors(
        payload,
        editorial_pages=editorial_pages,
    )
    suspicious = [item for item in inspections if item["status"] == "suspicious"]
    entries: list[dict[str, Any]] = []
    inspection_by_id: dict[str, dict[str, Any]] = {}
    for item in suspicious:
        work = payload["works"][item["work_index"]]
        if _has_resolved_manual_anchor_review(work):
            continue
        queries = _work_queries(work)
        if not queries:
            continue
        entry_id = f"work:{item['work_index']}"
        entries.append(
            {
                "entry_id": entry_id,
                "lemma_raw": queries[0],
                "query_names": queries,
                "query_match_mode": "best",
                "page_hint_ints": sorted(
                    {
                        *(_existing_index_page_hints(payload, work=work)),
                        *(
                            [declared_start]
                            if (declared_start := _as_int(work.get("start_page")))
                            is not None
                            else []
                        ),
                    }
                ),
            }
        )
        inspection_by_id[entry_id] = item

    locator_output = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "entries": [],
    }
    if entries:
        locator_output = locator(
            {
                "volume_id": volume_id,
                "source_root": str(source_root),
                "options": {"top_k": 12, "adjacency_window": 4},
                "entries": entries,
            }
        )

    result_by_id = {
        str(item.get("entry_id") or ""): item
        for item in locator_output.get("entries") or []
        if isinstance(item, dict)
    }
    report_items: list[dict[str, Any]] = []
    annotation_changed_count = 0
    for inspection in inspections:
        work = payload["works"][inspection["work_index"]]
        report_item = dict(inspection)
        report_item["changes"] = {}
        report_item["locator_status"] = None
        report_item["decision_reason"] = None
        report_item["annotation_changed"] = False
        report_item["chosen_candidate"] = None
        report_item["rerun"] = None
        if (
            inspection["status"] == "suspicious"
            and _has_resolved_manual_anchor_review(work)
        ):
            report_item["status"] = "manually_resolved"
            report_item["decision_reason"] = "manual_anchor_review"
            report_item["annotation_changed"] = _set_work_anchor_rerun(work, None)
            annotation_changed_count += int(report_item["annotation_changed"])
            report_items.append(report_item)
            continue
        if inspection["status"] != "suspicious":
            report_item["annotation_changed"] = _set_work_anchor_rerun(work, None)
            annotation_changed_count += int(report_item["annotation_changed"])
            report_items.append(report_item)
            continue

        entry_id = f"work:{inspection['work_index']}"
        locator_result = result_by_id.get(entry_id)
        if _is_composite_work_container(work):
            chosen, decision_reason = None, "composite_work_requires_agent"
        else:
            chosen, decision_reason = (
                _choose_text_candidate(
                    locator_result,
                    excluded_files=index_files,
                    current_start_file=inspection.get("start_file"),
                    preferred_page=inspection.get("start_page"),
                )
                if locator_result is not None
                else (None, "locator_result_missing")
            )
        report_item["decision_reason"] = decision_reason
        if locator_result is not None:
            report_item["locator_status"] = locator_result.get("status")
        if chosen is None:
            report_item["status"] = "unresolved"
            marker = _work_anchor_rerun_marker(
                inspection=inspection,
                work=work,
                locator_result=locator_result,
                decision_reason=decision_reason,
                excluded_files=index_files,
            )
            report_item["annotation_changed"] = _set_work_anchor_rerun(
                work,
                marker,
            )
            report_item["rerun"] = marker
            annotation_changed_count += int(report_item["annotation_changed"])
            report_items.append(report_item)
            continue

        old_start_page = _as_int(work.get("start_page"))
        corrected_start_page = _candidate_editorial_page(
            chosen,
            preferred_page=old_start_page,
        )
        end_page = _as_int(work.get("end_page"))
        if (
            corrected_start_page is not None
            and end_page is not None
            and corrected_start_page > end_page
        ):
            report_item["status"] = "unresolved"
            report_item["decision_reason"] = "candidate_start_page_after_end_page"
            marker = _work_anchor_rerun_marker(
                inspection=inspection,
                work=work,
                locator_result=locator_result,
                decision_reason=report_item["decision_reason"],
                excluded_files=index_files,
            )
            report_item["annotation_changed"] = _set_work_anchor_rerun(
                work,
                marker,
            )
            report_item["rerun"] = marker
            annotation_changed_count += int(report_item["annotation_changed"])
            report_items.append(report_item)
            continue

        old_start_file = work.get("start_file")
        report_item["chosen_candidate"] = _rerun_candidate_summary(
            {"candidates": [chosen]},
            excluded_files=index_files,
        )[0]
        new_start_file = str(Path(str(chosen["file"])).resolve())
        if old_start_file != new_start_file:
            work["start_file"] = new_start_file
            report_item["changes"]["start_file"] = {
                "from": old_start_file,
                "to": new_start_file,
            }

        if corrected_start_page is not None and corrected_start_page != old_start_page:
            work["start_page"] = corrected_start_page
            report_item["changes"]["start_page"] = {
                "from": old_start_page,
                "to": corrected_start_page,
            }

        if report_item["changes"]:
            raw_json = work.get("raw_json")
            if not isinstance(raw_json, dict):
                raw_json = {"value": raw_json} if raw_json is not None else {}
            raw_json["deterministic_anchor_reconciliation"] = {
                "reasons": inspection["reasons"],
                "changes": report_item["changes"],
                "candidate": {
                    "file": chosen.get("file"),
                    "image_file": chosen.get("image_file"),
                    "score": chosen.get("score"),
                    "probability": chosen.get("probability"),
                    "inferred_printed_page": chosen.get("inferred_printed_page"),
                    "estimator_pages": chosen.get("estimator_pages"),
                    "estimator_confidence": chosen.get("estimator_confidence"),
                    "header_sequence": chosen.get("header_sequence"),
                    "evidence": chosen.get("evidence"),
                },
            }
            work["raw_json"] = raw_json
            report_item["status"] = "repaired"
            report_item["annotation_changed"] = _set_work_anchor_rerun(work, None)
            annotation_changed_count += int(report_item["annotation_changed"])
        else:
            report_item["status"] = "unresolved"
            marker = _work_anchor_rerun_marker(
                inspection=inspection,
                work=work,
                locator_result=locator_result,
                decision_reason="candidate_confirmed_existing_anchor",
                excluded_files=index_files,
            )
            report_item["annotation_changed"] = _set_work_anchor_rerun(
                work,
                marker,
            )
            report_item["rerun"] = marker
            annotation_changed_count += int(report_item["annotation_changed"])
        report_items.append(report_item)

    section_scope = {
        str(section.get("section_key") or ""): str(section.get("scope_kind") or "")
        for section in payload.get("sections") or []
        if isinstance(section, dict)
    }
    ordered_indexes = sorted(
        range(len(payload.get("works") or [])),
        key=lambda index: (
            _as_int(payload["works"][index].get("work_order")) or index + 1,
            index,
        ),
    )
    report_by_index = {item["work_index"]: item for item in report_items}
    for current_index, next_index in zip(ordered_indexes, ordered_indexes[1:]):
        current = payload["works"][current_index]
        following = payload["works"][next_index]
        current_start = _as_int(current.get("start_page"))
        current_end = _as_int(current.get("end_page"))
        next_start = _as_int(following.get("start_page"))
        current_source = str(current.get("source_section_key") or "")
        next_source = str(following.get("source_section_key") or "")
        current_scope = section_scope.get(current_source, "")
        sibling_boundary = (
            current_scope in {"work_front", "work_front_matter"}
            or (
                current_scope == "volume_end"
                and current_source
                and current_source == next_source
            )
        )
        if not (
            sibling_boundary
            and current_start is not None
            and current_end is not None
            and next_start is not None
            and current_start < next_start <= current_end
        ):
            continue

        report_item = report_by_index[current_index]
        if "end_page_overlaps_next_work" not in report_item["reasons"]:
            report_item["reasons"].append("end_page_overlaps_next_work")
        marker = {
            "status": "ambiguous",
            "reason": "overlapping_editorial_boundaries_require_scan_layout_review",
            "inspection_reasons": list(report_item["reasons"]),
            "declared_anchor": {
                "start_page": current_start,
                "end_page": current_end,
                "start_file": current.get("start_file"),
                "end_file": current.get("end_file"),
            },
            "following_work": {
                "work_key": following.get("work_key"),
                "start_page": next_start,
                "start_file": following.get("start_file"),
            },
            "agent_checks": [
                "inspect the shared physical scan and its columns or facing pages",
                "when a paired page image exists on disk, inspect it before changing a shared boundary",
                "allow adjacent works to share one editorial or physical page",
                "change end_page only when body or column evidence proves the boundary",
            ],
        }
        current_raw_json = current.get("raw_json")
        previous_marker = (
            current_raw_json.get("work_anchor_rerun")
            if isinstance(current_raw_json, dict)
            else None
        )
        if (
            isinstance(previous_marker, dict)
            and previous_marker.get("reason") != marker["reason"]
        ):
            marker["anchor_locator_review"] = previous_marker
        annotation_changed = _set_work_anchor_rerun(current, marker)
        report_item["rerun"] = marker
        report_item["annotation_changed"] = (
            bool(report_item.get("annotation_changed")) or annotation_changed
        )
        annotation_changed_count += int(annotation_changed)
        report_item["decision_reason"] = marker["reason"]
        report_item["status"] = "unresolved"

    changed_count = sum(bool(item["changes"]) for item in report_items)
    rerun_count = sum(
        isinstance((work.get("raw_json") or {}), dict)
        and isinstance((work.get("raw_json") or {}).get("work_anchor_rerun"), dict)
        for work in payload.get("works") or []
        if isinstance(work, dict)
    )
    return {
        "schema_version": 1,
        "volume_id": volume_id,
        "source_root": str(source_root),
        "work_count": len(inspections),
        "suspicious_count": len(suspicious),
        "changed_count": changed_count,
        "annotation_changed_count": annotation_changed_count,
        "payload_mutation_count": changed_count + annotation_changed_count,
        "rerun_count": rerun_count,
        "ambiguous_count": sum(
            isinstance(work.get("raw_json"), dict)
            and isinstance(work["raw_json"].get("work_anchor_rerun"), dict)
            and work["raw_json"]["work_anchor_rerun"].get("status") == "ambiguous"
            for work in payload.get("works") or []
            if isinstance(work, dict)
        ),
        "unresolved_count": sum(item["status"] == "unresolved" for item in report_items),
        "items": report_items,
    }
