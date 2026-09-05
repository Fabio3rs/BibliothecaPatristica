"""Create deterministic, direction-aware OCR workplans for both index pipelines.

The workplan is the boundary between cheap mechanical work and semantic agent work. It records
physical files, printed-header evidence, purpose-specific traversal order, bounded chunks, and
coverage estimates while deliberately avoiding final entry segmentation.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .common import page_number, page_sort_key
from .index_entry_estimator import (
    analyze_page_boundary,
    combine_entry_estimates,
    estimate_page_entries,
)
from .ocr_xml_utils import read_ocr_page


HEADER_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
COMPLETE_MANIFEST_STATUSES = {"verified", "complete"}
CLOSURE_HEADING_RE = re.compile(
    r"\b(?:CORRIGENDA|ADDENDA|ERRATA|FINIS|APPROBATION|PRIVILEGE)\b",
    re.IGNORECASE,
)
WORKPLAN_SCHEMA_VERSION = 2
CHUNK_CONTRACT_VERSION = 2
SECTION_CONTINUATION_LOOKAHEAD = 24
ALPHABETICAL_STOP_MARKER = "ordo rerum"
RUNNING_INDEX_MARKERS = {"index", "indices", "index rerum"}


def _normalize_heading(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _canonical_path(value: Any, source_root: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path.resolve()
    project_relative = path.resolve()
    if project_relative.exists():
        return project_relative
    return (source_root / path.name).resolve()


def _header_evidence(path: Path) -> dict[str, Any]:
    try:
        header_raw = read_ocr_page(path).header_text.strip()
    except OSError:
        header_raw = ""
    return {
        "physical_file": str(path),
        "physical_file_seq": page_number(path),
        "editorial_header_raw": header_raw or None,
        "editorial_page_candidates": [int(value) for value in HEADER_NUMBER_RE.findall(header_raw)],
    }


def _chunk_input_fingerprint(
    paths: list[Path],
    *,
    cache: dict[str, dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    for path in _unique_paths(paths):
        key = str(path)
        record = cache.get(key)
        if record is None:
            stat = path.stat()
            record = {
                "file": key,
                "bytes": stat.st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            cache[key] = record
        records.append(record)
    envelope = {
        "contract_version": CHUNK_CONTRACT_VERSION,
        "files": records,
    }
    digest = hashlib.sha256(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest, records


def reconcile_workplan_progress(
    workplan: dict[str, Any],
    previous_workplan: dict[str, Any] | None,
) -> dict[str, Any]:
    """Carry forward only completed chunks whose OCR/contract fingerprint is unchanged."""
    if not isinstance(previous_workplan, dict):
        return workplan
    previous_by_id = {
        str(chunk.get("chunk_id") or ""): chunk
        for chunk in previous_workplan.get("chunks") or []
        if isinstance(chunk, dict)
    }
    reused = 0
    for chunk in workplan.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        previous = previous_by_id.get(str(chunk.get("chunk_id") or ""))
        if not previous:
            continue
        if previous.get("input_fingerprint") != chunk.get("input_fingerprint"):
            continue
        if "attempt_count" in previous:
            chunk["attempt_count"] = previous["attempt_count"]
        if "last_validation_failure" in previous:
            chunk["last_validation_failure"] = previous["last_validation_failure"]
        if "last_error" in previous:
            chunk["last_error"] = previous["last_error"]
        if "last_error_type" in previous:
            chunk["last_error_type"] = previous["last_error_type"]
        if previous.get("status") != "complete":
            continue
        output_file = Path(str(chunk.get("output_file") or ""))
        if not output_file.is_file():
            continue
        for key in ("status", "completed_at", "entry_count", "coverage_check"):
            if key in previous:
                chunk[key] = previous[key]
        reused += 1
    if workplan.get("chunks") and all(
        isinstance(chunk, dict) and chunk.get("status") == "complete"
        for chunk in workplan["chunks"]
    ):
        workplan["manifest_status"] = "complete"
    workplan["resume_diagnostics"] = {
        "previous_workplan_present": True,
        "reused_complete_chunks": reused,
        "planned_chunks": len(workplan.get("chunks") or []),
    }
    return workplan


def _chunk_paths(paths: list[Path], *, max_files: int, overlap: int) -> list[list[Path]]:
    if not paths:
        return []
    max_files = max(1, max_files)
    overlap = max(0, min(overlap, max_files - 1))
    chunks: list[list[Path]] = []
    start = 0
    while start < len(paths):
        chunk = paths[start : start + max_files]
        chunks.append(chunk)
        if start + max_files >= len(paths):
            break
        start += max_files - overlap
    return chunks


def _group_heading_hits(
    candidate_sections: list[dict[str, Any]],
    position_by_path: dict[str, int],
) -> list[list[dict[str, Any]]]:
    usable = [
        item
        for item in candidate_sections
        if isinstance(item, dict) and str(item.get("file") or "") in position_by_path
    ]
    usable.sort(key=lambda item: position_by_path[str(item["file"])])
    groups: list[list[dict[str, Any]]] = []
    for item in usable:
        marker = _normalize_heading(item.get("marker") or item.get("heading"))
        marker_group = (
            "running_index_header" if marker in RUNNING_INDEX_MARKERS else marker
        )
        position = position_by_path[str(item["file"])]
        if groups:
            previous = groups[-1][-1]
            previous_marker = _normalize_heading(previous.get("marker") or previous.get("heading"))
            previous_marker_group = (
                "running_index_header"
                if previous_marker in RUNNING_INDEX_MARKERS
                else previous_marker
            )
            previous_position = position_by_path[str(previous["file"])]
            if position == previous_position or (
                marker_group == previous_marker_group
                and position - previous_position <= 3
            ):
                groups[-1].append(item)
                continue
        groups.append([item])
    return groups


def _is_alphabetical_stop(item: dict[str, Any]) -> bool:
    marker = _normalize_heading(item.get("marker"))
    if marker.startswith(ALPHABETICAL_STOP_MARKER):
        return True
    return ALPHABETICAL_STOP_MARKER in _normalize_heading(item.get("heading"))


def build_index_workplan(
    *,
    volume_id: str,
    source_root: Path,
    collection: str,
    filtered_pages: dict[str, Any],
    pipeline_kind: str,
    chunk_output_dir: Path,
    max_files_per_chunk: int = 6,
    chunk_overlap: int = 1,
    context_window: int = 4,
) -> dict[str, Any]:
    if pipeline_kind not in {"general", "alphabetical"}:
        raise ValueError(f"Unsupported pipeline_kind: {pipeline_kind}")
    reverse_extraction = pipeline_kind == "alphabetical"
    source_root = source_root.resolve()
    source_files = sorted(source_root.glob("*.txt"), key=page_sort_key)
    position_by_path = {str(path): index for index, path in enumerate(source_files)}
    candidate_sections = filtered_pages.get("candidate_sections") or []
    if not isinstance(candidate_sections, list):
        candidate_sections = []
    candidate_sections = [
        {**item, "file": str(_canonical_path(item.get("file"), source_root))}
        for item in candidate_sections
        if isinstance(item, dict) and item.get("file")
    ]
    use_ordo_boundary = pipeline_kind == "alphabetical" and collection in {"PG", "PL"}
    boundary_hits = (
        [item for item in candidate_sections if _is_alphabetical_stop(item)]
        if use_ordo_boundary
        else []
    )
    section_hits = [
        item for item in candidate_sections if item not in boundary_hits
    ]
    groups = _group_heading_hits(section_hits, position_by_path)
    semantic_boundaries = [
        {
            "kind": "alphabetical_stop",
            "marker": "ORDO RERUM",
            "physical_file": str(item["file"]),
            "physical_file_seq": page_number(Path(str(item["file"]))),
            "line": item.get("line"),
            "heading_raw": item.get("heading") or item.get("text") or item.get("marker"),
            "rule": (
                "Do not emit ORDO RERUM or later contents-table material in the "
                "alphabetical pipeline. If this heading begins mid-file, material before "
                "the heading remains eligible."
            ),
        }
        for item in boundary_hits
    ]
    boundary_positions = sorted(
        {
            position_by_path[str(item["file"])]
            for item in boundary_hits
            if str(item["file"]) in position_by_path
        }
    )
    candidate_paths = [
        _canonical_path(value, source_root)
        for value in (filtered_pages.get("candidate_files") or [])
        if str(_canonical_path(value, source_root)) in position_by_path
    ]
    candidate_paths = _unique_paths(candidate_paths)
    candidate_positions = {position_by_path[str(path)] for path in candidate_paths}
    group_start_positions = sorted(
        min(position_by_path[str(item["file"])] for item in group) for group in groups
    )
    if reverse_extraction:
        groups.reverse()

    sections: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    candidate_index_files: list[Path] = []
    entry_estimate_by_path: dict[str, dict[str, Any]] = {}
    fingerprint_cache: dict[str, dict[str, Any]] = {}
    globally_owned_paths: set[str] = set()

    def entry_estimate(path: Path) -> dict[str, Any]:
        key = str(path)
        if key not in entry_estimate_by_path:
            entry_estimate_by_path[key] = estimate_page_entries(path)
        return entry_estimate_by_path[key]

    def is_likely_continuation_page(path: Path) -> bool:
        try:
            header = read_ocr_page(path).header_text
        except OSError:
            return False
        if CLOSURE_HEADING_RE.search(header):
            return False
        estimate = entry_estimate(path)
        line_count = int(estimate.get("line_count") or 0)
        likely = int((estimate.get("estimated_entry_count") or {}).get("likely") or 0)
        return line_count > 0 and likely >= 1 and likely / line_count >= 0.25

    def add_section(
        *,
        heading: str,
        marker: str,
        section_paths: list[Path],
        heading_variants: list[str],
        evidence: list[dict[str, Any]],
        confidence: str,
        stop_boundaries: list[dict[str, Any]] | None = None,
    ) -> None:
        section_paths = [
            path
            for path in _unique_paths(section_paths)
            if str(path) not in globally_owned_paths
        ]
        if not section_paths:
            return
        globally_owned_paths.update(str(path) for path in section_paths)
        stop_boundaries = stop_boundaries or []
        section_order = len(sections) + 1
        section_id = f"{volume_id}:candidate-section:{section_order:03d}"
        physical_positions = [position_by_path[str(path)] for path in section_paths]
        low_position = min(physical_positions)
        high_position = max(physical_positions)
        context_start = max(0, low_position - max(0, context_window))
        context_end = min(len(source_files), high_position + max(0, context_window) + 1)
        context_paths = source_files[context_start:context_end]
        if reverse_extraction:
            context_paths = list(reversed(context_paths))
        ascending_section_paths = sorted(
            section_paths, key=lambda path: position_by_path[str(path)]
        )
        boundary_evidence = [
            analyze_page_boundary(left, right)
            for left, right in zip(ascending_section_paths, ascending_section_paths[1:])
        ]
        boundary_by_left = {
            str(item["physical_left_file"]): item for item in boundary_evidence
        }
        section_chunks: list[str] = []
        owned_in_section: set[str] = set()

        raw_parts = _chunk_paths(
            section_paths, max_files=max_files_per_chunk, overlap=chunk_overlap
        )
        for part_order, part_paths in enumerate(raw_parts, start=1):
            owned_paths = [path for path in part_paths if str(path) not in owned_in_section]
            if not owned_paths:
                continue
            overlap_context_paths = [path for path in part_paths if path not in owned_paths]
            owned_in_section.update(str(path) for path in owned_paths)
            chunk_id = f"{volume_id}:chunk:{section_order:03d}:{part_order:03d}"
            chunk_file = chunk_output_dir / f"section_{section_order:03d}_part_{part_order:03d}.json"
            part_positions = [position_by_path[str(path)] for path in part_paths]
            part_low = min(part_positions)
            part_high = max(part_positions)
            part_context_start = max(0, part_low - max(0, context_window))
            part_context_end = min(len(source_files), part_high + max(0, context_window) + 1)
            part_context_paths = source_files[part_context_start:part_context_end]
            if reverse_extraction:
                part_context_paths = list(reversed(part_context_paths))
            input_fingerprint, input_files = _chunk_input_fingerprint(
                [*owned_paths, *overlap_context_paths, *part_context_paths],
                cache=fingerprint_cache,
            )
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "section_id": section_id,
                    "chunk_order": len(chunks) + 1,
                    "extraction_direction": (
                        "physical_end_to_start" if reverse_extraction else "physical_start_to_end"
                    ),
                    "status": "pending",
                    "chunk_contract_version": CHUNK_CONTRACT_VERSION,
                    "input_fingerprint": input_fingerprint,
                    "input_files": input_files,
                    "physical_files": [str(path) for path in owned_paths],
                    "overlap_context_files": [str(path) for path in overlap_context_paths],
                    "context_files": [
                        str(path) for path in part_context_paths
                    ],
                    "deterministic_entry_estimate": combine_entry_estimates(
                        [entry_estimate(path) for path in owned_paths]
                    ),
                    "page_boundary_evidence": [
                        item
                        for item in boundary_evidence
                        if item["physical_left_file"] in {str(path) for path in owned_paths}
                        or item["physical_right_file"] in {str(path) for path in owned_paths}
                    ],
                    "required_boundary_decisions": [
                        {
                            "physical_left_file": item["physical_left_file"],
                            "physical_right_file": item["physical_right_file"],
                        }
                        for item in boundary_evidence
                        if item["likelihood"] == "high"
                        and item["physical_left_file"] in {str(path) for path in owned_paths}
                    ],
                    "semantic_stop_boundaries": [
                        item
                        for item in stop_boundaries
                        if item["physical_file"]
                        in {
                            str(path)
                            for path in [
                                *owned_paths,
                                *overlap_context_paths,
                                *part_context_paths,
                            ]
                        }
                    ],
                    "output_file": str(chunk_file),
                }
            )
            section_chunks.append(chunk_id)

        sections.append(
            {
                "section_id": section_id,
                "section_order": section_order,
                "status": "candidate",
                "heading_raw": heading or marker or section_paths[0].name,
                "marker": marker or None,
                "marker_variants": list(
                    dict.fromkeys(
                        str(item.get("marker") or "")
                        for item in evidence
                        if item.get("marker")
                    )
                ),
                "heading_variants": heading_variants,
                "extraction_direction": (
                    "physical_end_to_start" if reverse_extraction else "physical_start_to_end"
                ),
                "extraction_first_file": str(section_paths[0]),
                "extraction_last_file": str(section_paths[-1]),
                "physical_file_low": str(source_files[low_position]),
                "physical_file_high": str(source_files[high_position]),
                "physical_files": [str(path) for path in section_paths],
                "context_files": [str(path) for path in context_paths],
                "header_evidence": [_header_evidence(path) for path in section_paths],
                "deterministic_entry_estimate": combine_entry_estimates(
                    [entry_estimate(path) for path in section_paths]
                ),
                "page_boundary_evidence": boundary_evidence,
                "chunk_ids": section_chunks,
                "confidence": confidence,
                "evidence": evidence,
                "semantic_stop_boundaries": stop_boundaries,
            }
        )
        candidate_index_files.extend(section_paths)

    for group in groups:
        positions = [position_by_path[str(item["file"])] for item in group]
        section_start = min(positions)
        section_end = max(positions)
        later_heading_starts = [
            position for position in group_start_positions if position > section_start
        ]
        next_heading_start = later_heading_starts[0] if later_heading_starts else len(source_files)
        max_section_end = min(
            len(source_files) - 1,
            section_start + SECTION_CONTINUATION_LOOKAHEAD,
            next_heading_start - 1,
        )
        following_boundary_positions = [
            position for position in boundary_positions if position >= section_start
        ]
        section_stop_boundaries: list[dict[str, Any]] = []
        if following_boundary_positions:
            boundary_position = following_boundary_positions[0]
            max_section_end = min(max_section_end, boundary_position)
            section_stop_boundaries = [
                item
                for item in semantic_boundaries
                if position_by_path.get(item["physical_file"]) == boundary_position
            ]
        while section_end < max_section_end:
            next_position = section_end + 1
            next_path = source_files[next_position]
            if is_likely_continuation_page(next_path):
                section_end = next_position
                continue
            following_position = next_position + 1
            if (
                following_position <= max_section_end
                and not CLOSURE_HEADING_RE.search(read_ocr_page(next_path).header_text)
                and is_likely_continuation_page(source_files[following_position])
            ):
                section_end = following_position
                continue
            break
        section_paths = source_files[section_start : section_end + 1]
        if reverse_extraction:
            section_paths = list(reversed(section_paths))
        add_section(
            heading=str(group[0].get("heading") or group[0].get("marker") or ""),
            marker=str(group[0].get("marker") or ""),
            section_paths=section_paths,
            heading_variants=list(
                dict.fromkeys(str(item.get("heading") or "") for item in group if item.get("heading"))
            ),
            evidence=group,
            confidence="high" if len(group) > 1 else "medium",
            stop_boundaries=section_stop_boundaries,
        )

    if not sections:
        fallback_paths = list(candidate_paths)
        fallback_paths.sort(key=page_sort_key, reverse=reverse_extraction)
        if fallback_paths and not boundary_hits:
            add_section(
                heading="Unverified candidate region",
                marker="fallback_candidate_region",
                section_paths=fallback_paths,
                heading_variants=[],
                evidence=[],
                confidence="low",
            )

    return {
        "schema_version": WORKPLAN_SCHEMA_VERSION,
        "volume_id": volume_id,
        "collection": collection,
        "source_root": str(source_root),
        "pipeline_kind": pipeline_kind,
        "pipeline_purpose": (
            "Read opening/front-matter tables of works, subworks, divisions, and volume contents."
            if pipeline_kind == "general"
            else
            "Read closing alphabetical/analytical indexes of names, subjects, citations, "
            "scripture, and cross-references."
        ),
        "extraction_direction": (
            "physical_end_to_start" if reverse_extraction else "physical_start_to_end"
        ),
        "manifest_status": "candidate" if sections else "no_candidates",
        "numbering_glossary": {
            "physical_file": (
                "The suffix in {uuid}-NNN.txt is the physical OCR file/scan sequence only. "
                "It is never an editorial page number."
            ),
            "editorial_page": (
                "Editorial page numbers are printed by the historical volume, commonly as "
                "`NUMBER PAGE-TITLE NUMBER+1` in a facing-page header. OCR generators may keep "
                "that in one header block, split it across multiple blocks, preserve only one "
                "side, corrupt digits through CER, or omit it. Infer missing/corrupt values from "
                "several physical files before and after; never substitute the OCR filename suffix."
            ),
            "cited_reference": (
                "Numbers printed inside index entries are non-physical source/reference data. "
                "They may be editorial pages, columns, folios, notes, scripture chapter/verse, "
                "book/chapter numbers, manuscript identifiers, years, or another reference "
                "system. Map them to a physical OCR file only through explicit textual, local "
                "sequence, or estimator evidence."
            ),
        },
        "filtered_pages_source": filtered_pages.get("source"),
        "scan_diagnostics": {
            "source_file_count": len(source_files),
            "input_candidate_file_count": len(filtered_pages.get("candidate_files") or []),
            "input_candidate_section_count": len(filtered_pages.get("candidate_sections") or []),
            "recognized_candidate_section_count": sum(len(group) for group in groups),
            "semantic_boundary_count": len(semantic_boundaries),
            "planned_section_count": len(sections),
            "planned_chunk_count": len(chunks),
            "entry_estimate_file_count": len(entry_estimate_by_path),
            "section_continuation_lookahead": SECTION_CONTINUATION_LOOKAHEAD,
        },
        "semantic_boundaries": semantic_boundaries,
        "candidate_index_files": [str(path) for path in _unique_paths(candidate_index_files)],
        "sections": sections,
        "chunks": chunks,
        "chunk_policy": {
            "max_physical_files": max(1, max_files_per_chunk),
            "overlap_physical_files": max(0, min(chunk_overlap, max_files_per_chunk - 1)),
            "context_window_physical_files": max(0, context_window),
            "fresh_context_required": True,
            "resume_session": False,
            "processing_order": (
                "descending_physical_file_sequence"
                if reverse_extraction
                else "ascending_physical_file_sequence"
            ),
        },
    }


def workplan_is_complete(workplan: dict[str, Any]) -> bool:
    return str(workplan.get("manifest_status") or "") in COMPLETE_MANIFEST_STATUSES and all(
        str(chunk.get("status") or "") == "complete" for chunk in (workplan.get("chunks") or [])
    )
