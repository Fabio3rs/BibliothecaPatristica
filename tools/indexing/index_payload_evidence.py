"""Check extracted index objects against OCR text with deterministic evidence matching.

This module supports both sister pipelines and legacy-payload audits. Exact, normalized, and fuzzy
matches answer whether text can be found in the declared physical evidence; they do not equate
editorial page references with OCR filename suffixes and do not replace human/agent semantics.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path
from typing import Any

from tools.corpus_utils import page_sort_key
from .index_work_anchor_reconciler import inspect_declared_work_anchors
from tools.ocr_xml_utils import read_ocr_page


ENTRY_BOUNDARY_RE = re.compile(
    r"(?:\b(?:[IVXLCDM]+,\s*)?\d{1,4}(?:\s*[A-Z])?[.;]?\s+)([A-ZÆŒÀ-ÖØ-Þ]{3,}\b)"
)
LIST_BEARING_SCOPE_KINDS = {
    "volume_table",
    "fascicle_inventory",
    "work_internal_table",
    "work_index_nominal",
    "work_index_scripture",
    "work_index_alphabetical",
    "work_index_analytic",
    "retrospective_table",
}
LIST_HEADING_RE = re.compile(r"\b(?:INDEX|INDICES|TABLE|ELENCHUS|ORDO|ONOMASTIC)\b", re.IGNORECASE)
EMPTY_ENTRY_STATUSES = {"no_line_items", "unrecoverable_ocr"}
_SOURCE_FILES_CACHE: dict[str, list[Path]] = {}
_PAGE_CACHE: dict[str, tuple[str, list[str]]] = {}
_CROSS_PAGE_HYPHEN_RE = re.compile(r"(?<=\w)[-‐‑]\s*\f\s*(?=\w)")
_FILE_FAMILY_RE = re.compile(r"-\d+\.txt$", re.IGNORECASE)


def _file_family(path: Path) -> str:
    return _FILE_FAMILY_RE.sub("", path.name)


def normalize_evidence_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = (
        text.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
    )
    text = text.casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = "".join(char if char.isalnum() else " " for char in text)
    return re.sub(r"\s+", " ", text).strip()


def _section_files(
    section: dict[str, Any],
    *,
    source_files: list[Path],
    position_by_path: dict[str, int],
) -> list[Path]:
    start = str(section.get("file_start") or section.get("physical_file_start") or "")
    end = str(section.get("file_end") or section.get("physical_file_end") or "")
    if start in position_by_path and end in position_by_path:
        left = position_by_path[start]
        right = position_by_path[end]
        if left > right:
            left, right = right, left
        return source_files[left : right + 1]
    result: list[Path] = []
    for value in section.get("physical_files") or []:
        if str(value) in position_by_path:
            result.append(source_files[position_by_path[str(value)]])
    for value in (start, end):
        if value in position_by_path:
            result.append(source_files[position_by_path[value]])
    return list(dict.fromkeys(result))


def _sample_indexes(total: int, sample_size: int | None) -> list[int]:
    if sample_size is None or sample_size <= 0 or sample_size >= total:
        return list(range(total))
    if sample_size == 1:
        return [0]
    return sorted({round(index * (total - 1) / (sample_size - 1)) for index in range(sample_size)})


def _best_line_match(query: str, lines: list[str]) -> tuple[float, str | None]:
    best_score = 0.0
    best_line: str | None = None
    query_tokens = query.split()
    for line in lines:
        if not line:
            continue
        candidates = [line]
        line_tokens = line.split()
        if query_tokens and len(line_tokens) > len(query_tokens):
            width = min(len(line_tokens), max(len(query_tokens) + 3, 4))
            for start in range(0, len(line_tokens) - width + 1):
                candidates.append(" ".join(line_tokens[start : start + width]))
        for candidate in candidates:
            score = difflib.SequenceMatcher(None, query, candidate).ratio()
            if score > best_score:
                best_score = score
                best_line = candidate
    return best_score, best_line


def verify_index_payload_evidence(
    payload: dict[str, Any],
    *,
    sample_size: int | None = 200,
    fuzzy_threshold: float = 0.78,
    max_fuzzy_files: int = 4,
) -> dict[str, Any]:
    volume = payload.get("volume") or {}
    volume_id = str(volume.get("volume_id") or "")
    source_root = Path(str(volume.get("source_root") or ""))
    source_root_key = str(source_root)
    if source_root_key not in _SOURCE_FILES_CACHE:
        _SOURCE_FILES_CACHE[source_root_key] = (
            sorted(source_root.glob("*.txt"), key=page_sort_key) if source_root.exists() else []
        )
    source_files = _SOURCE_FILES_CACHE[source_root_key]
    position_by_path = {str(path): index for index, path in enumerate(source_files)}
    files_by_family: dict[str, list[Path]] = {}
    for path in source_files:
        files_by_family.setdefault(_file_family(path), []).append(path)
    sections = [item for item in (payload.get("sections") or []) if isinstance(item, dict)]
    section_by_key = {str(item.get("section_key") or ""): item for item in sections}
    section_file_cache: dict[str, list[Path]] = {}
    def load_page(path: Path) -> tuple[str, list[str]]:
        key = str(path)
        if key not in _PAGE_CACHE:
            page = read_ocr_page(path)
            lines = [normalize_evidence_text(line) for line in page.all_text.splitlines()]
            _PAGE_CACHE[key] = (normalize_evidence_text(page.all_text), [line for line in lines if line])
        return _PAGE_CACHE[key]

    def combined_page_text(paths: list[Path]) -> str:
        raw_pages = [read_ocr_page(path).all_text for path in paths]
        joined = _CROSS_PAGE_HYPHEN_RE.sub("", "\f".join(raw_pages))
        return normalize_evidence_text(joined)

    flattened: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    if payload.get("schema_version") is not None and isinstance(payload.get("entries"), list):
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            section_key = str(entry.get("section_key") or "")
            flattened.append((section_key, section_by_key.get(section_key, {}), entry))
        pipeline_kind = "alphabetical"
    else:
        for section in sections:
            section_key = str(section.get("section_key") or "")
            for entry in section.get("entries") or []:
                if isinstance(entry, dict):
                    flattened.append((section_key, section, entry))
        pipeline_kind = "general"

    entry_count_by_section: dict[str, int] = {}
    for section_key, _section, _entry in flattened:
        entry_count_by_section[section_key] = entry_count_by_section.get(section_key, 0) + 1
    empty_list_sections: list[dict[str, Any]] = []
    for section in sections:
        section_key = str(section.get("section_key") or "")
        heading = str(section.get("heading_raw") or section.get("index_kind") or "")
        if pipeline_kind == "alphabetical":
            is_list_bearing = True
        else:
            is_list_bearing = (
                str(section.get("scope_kind") or "") in LIST_BEARING_SCOPE_KINDS
                or bool(LIST_HEADING_RE.search(heading))
            )
        if not is_list_bearing or entry_count_by_section.get(section_key, 0):
            continue
        raw_json = section.get("raw_json")
        status = raw_json.get("entries_status") if isinstance(raw_json, dict) else None
        reason = raw_json.get("entries_status_reason") if isinstance(raw_json, dict) else None
        evidence = raw_json.get("evidence_files") if isinstance(raw_json, dict) else None
        justified = (
            status in EMPTY_ENTRY_STATUSES
            and isinstance(reason, str)
            and bool(reason.strip())
            and isinstance(evidence, list)
            and bool(evidence)
        )
        empty_list_sections.append(
            {
                "section_key": section_key,
                "heading_raw": heading,
                "entries_status": status,
                "justified": justified,
            }
        )

    results: list[dict[str, Any]] = []
    for index in _sample_indexes(len(flattened), sample_size):
        section_key, section, entry = flattened[index]
        raw_json = entry.get("raw_json")
        inheritance = (
            raw_json.get("inheritance")
            if isinstance(raw_json, dict) and isinstance(raw_json.get("inheritance"), dict)
            else {}
        )
        query_raw = (
            entry.get("entry_raw")
            if pipeline_kind == "alphabetical" and any(inheritance.values())
            else entry.get("lemma_raw")
            if pipeline_kind == "alphabetical"
            else entry.get("target_raw") or entry.get("normalized_target")
        )
        query_raw = query_raw or entry.get("entry_raw") or ""
        query = normalize_evidence_text(query_raw)
        source_file = None
        declared_source_files: list[str] = []
        if isinstance(raw_json, dict):
            source_file = raw_json.get("source_file")
            if isinstance(raw_json.get("source_files"), list):
                declared_source_files = [
                    str(value) for value in raw_json["source_files"] if str(value)
                ]
        files: list[Path] = []
        for value in declared_source_files:
            if value in position_by_path:
                files.append(source_files[position_by_path[value]])
        if source_file and str(source_file) in position_by_path:
            files.append(source_files[position_by_path[str(source_file)]])
        files = list(dict.fromkeys(files))
        if not files:
            if section_key not in section_file_cache:
                section_file_cache[section_key] = _section_files(
                    section,
                    source_files=source_files,
                    position_by_path=position_by_path,
                )
            files = section_file_cache[section_key]

        method = "unverified"
        best_score = 0.0
        best_file: str | None = None
        best_text: str | None = None
        scope_repair: dict[str, Any] | None = None
        if not query:
            method = "not_applicable"
        elif not files:
            method = "missing_physical_source"
        else:
            for path in files:
                page_text, page_lines = load_page(path)
                if query in page_text:
                    method = "exact"
                    best_score = 1.0
                    best_file = str(path)
                    best_text = query
                    break
            if method != "exact" and len(files) > 1:
                combined_text = combined_page_text(sorted(files, key=page_sort_key))
                if query in combined_text:
                    method = "exact_cross_page"
                    best_score = 1.0
                    best_file = " | ".join(str(path) for path in sorted(files, key=page_sort_key))
                    best_text = query
            if method != "exact":
                fuzzy_files = files
                if len(fuzzy_files) > max(1, max_fuzzy_files):
                    positions = _sample_indexes(len(fuzzy_files), max(1, max_fuzzy_files))
                    fuzzy_files = [fuzzy_files[position] for position in positions]
                for path in fuzzy_files:
                    _page_text, page_lines = load_page(path)
                    score, candidate = _best_line_match(query, page_lines)
                    if score > best_score:
                        best_score = score
                        best_file = str(path)
                        best_text = candidate
            if method not in {"exact", "exact_cross_page"} and best_score >= fuzzy_threshold:
                method = "fuzzy"
            if method == "unverified":
                declared_set = {str(path) for path in files}
                boundary_paths = [
                    source_files[position_by_path[str(value)]]
                    for value in (section.get("file_start"), section.get("file_end"))
                    if str(value) in position_by_path
                ]
                neighbor_candidates: list[Path] = []
                for boundary in boundary_paths:
                    family_files = files_by_family.get(_file_family(boundary), [])
                    try:
                        family_index = family_files.index(boundary)
                    except ValueError:
                        continue
                    neighbor_candidates.extend(
                        family_files[max(0, family_index - 2) : family_index + 3]
                    )
                for path in dict.fromkeys(neighbor_candidates):
                    if str(path) in declared_set:
                        continue
                    page_text, _page_lines = load_page(path)
                    if query not in page_text:
                        continue
                    method = "exact_neighbor_outside_declared_scope"
                    best_score = 1.0
                    best_file = str(path)
                    best_text = query
                    start = str(section.get("file_start") or "")
                    end = str(section.get("file_end") or "")
                    candidate_number = page_sort_key(path)[0]
                    start_number = page_sort_key(Path(start))[0] if start else None
                    end_number = page_sort_key(Path(end))[0] if end else None
                    if start_number is not None and candidate_number < start_number:
                        action = "extend_file_start"
                    elif end_number is not None and candidate_number > end_number:
                        action = "extend_file_end"
                    else:
                        action = "inspect_section_scope"
                    scope_repair = {
                        "action": action,
                        "current_file_start": start or None,
                        "current_file_end": end or None,
                        "suggested_evidence_file": str(path),
                    }
                    break

        entry_raw = str(entry.get("entry_raw") or "")
        cross_page_entry_raw_method: str | None = None
        if len(files) > 1 and entry_raw:
            entry_query = normalize_evidence_text(entry_raw)
            if entry_query in combined_page_text(sorted(files, key=page_sort_key)):
                cross_page_entry_raw_method = "exact_cross_page"
            else:
                cross_page_entry_raw_method = "not_found_cross_page"
        boundary_candidates = ENTRY_BOUNDARY_RE.findall(entry_raw)
        results.append(
            {
                "entry_index": index,
                "entry_key": entry.get("entry_key"),
                "section_key": section_key,
                "query_raw": str(query_raw)[:300],
                "method": method,
                "score": round(best_score, 4),
                "best_file": best_file,
                "best_text": best_text[:300] if best_text else None,
                "scope_repair": scope_repair,
                "declared_source_file_count": len(declared_source_files),
                "cross_page_entry_raw_method": cross_page_entry_raw_method,
                "segmentation_suspect": len(boundary_candidates) >= 2,
                "boundary_candidates": boundary_candidates[:10],
            }
        )

    counts: dict[str, int] = {}
    for item in results:
        counts[item["method"]] = counts.get(item["method"], 0) + 1
    verified = (
        counts.get("exact", 0)
        + counts.get("exact_cross_page", 0)
        + counts.get("fuzzy", 0)
    )
    applicable = len(results) - counts.get("not_applicable", 0)
    work_anchor_results = (
        inspect_declared_work_anchors(payload)
        if pipeline_kind == "general"
        else []
    )
    return {
        "schema_version": 1,
        "volume_id": volume_id,
        "pipeline_kind": pipeline_kind,
        "payload_entry_count": len(flattened),
        "sampled_entry_count": len(results),
        "source_file_count": len(source_files),
        "fuzzy_threshold": fuzzy_threshold,
        "counts": counts,
        "verified_ratio": round(verified / applicable, 4) if applicable else None,
        "segmentation_suspect_count": sum(bool(item["segmentation_suspect"]) for item in results),
        "declared_cross_page_entry_count": sum(
            int(item["declared_source_file_count"] > 1) for item in results
        ),
        "cross_page_entry_raw_not_found_count": sum(
            item["cross_page_entry_raw_method"] == "not_found_cross_page" for item in results
        ),
        "empty_list_section_count": len(empty_list_sections),
        "unjustified_empty_list_section_count": sum(
            not bool(item["justified"]) for item in empty_list_sections
        ),
        "empty_list_sections": empty_list_sections,
        "work_anchor_count": len(work_anchor_results),
        "suspicious_work_anchor_count": sum(
            item.get("status") == "suspicious"
            for item in work_anchor_results
        ),
        "work_anchor_results": work_anchor_results,
        "results": results,
    }
