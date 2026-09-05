"""Mechanical validation for versioned alphabetical agent artifacts."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from .alphabetical_compact_pipeline import (
    CompactPipelineError,
    coerce_semantic_payload,
)
from .alphabetical_prompt_contract import (
    GLOSSARY_VERSION,
    INTERPRETATION_CONTRACT_VERSION,
    LOCATOR_CONTRACT_VERSION,
    OUTPUT_SCHEMA_VERSION,
    PROMPT_CONTRACT_VERSION,
)
from .index_pipeline_ownership import alphabetical_section_ownership


SEGMENT_ROLES = {"owned", "boundary", "context", "uncertain"}
DISCOVERY_STATUSES = {"complete", "needs_expansion"}
NOTATION_STATUSES = {
    "literal_only",
    "resolved",
    "ambiguous",
    "not_applicable",
}
MATERIAL_REFERENCE_MODES = {"remissive", "parallel", "source_only"}
SCRIPTURE_MODES = {
    "none",
    "citation_index",
    "pericope_index",
    "concordance_component",
    "textual_apparatus",
    "incidental_mention",
}
MATERIAL_REF_KINDS = {
    "editorial_page",
    "editorial_column",
    "editorial_page_column",
    "editorial_range",
    "editorial_page_line",
    "target_locator",
    "parallel_locator",
    "unresolved",
}


@lru_cache(maxsize=4096)
def _source_line_count(
    path_text: str,
    source_size: int,
    source_mtime_ns: int,
) -> int:
    del source_size, source_mtime_ns
    with Path(path_text).open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


@lru_cache(maxsize=8192)
def _source_span_sha256(
    path_text: str,
    source_size: int,
    source_mtime_ns: int,
    line_start: int,
    line_end: int,
) -> str:
    del source_size, source_mtime_ns
    lines = Path(path_text).read_text(
        encoding="utf-8", errors="replace"
    ).splitlines(keepends=True)
    selected = "".join(lines[line_start - 1 : line_end])
    return hashlib.sha256(selected.encode("utf-8")).hexdigest()


def _required_text(obj: Mapping[str, Any], field: str, *, label: str) -> str:
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CompactPipelineError(f"{label}.{field} must be a non-empty string")
    return value


def _required_list(obj: Mapping[str, Any], field: str, *, label: str) -> list[Any]:
    value = obj.get(field)
    if not isinstance(value, list):
        raise CompactPipelineError(f"{label}.{field} must be an array")
    return value


def _inside_root(path: Path, source_root: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(source_root.expanduser().resolve())
    except ValueError as exc:
        raise CompactPipelineError(
            f"{label} escapes source_root: {resolved}"
        ) from exc
    return resolved


def validate_source_span(
    raw_span: Any,
    *,
    source_root: Path,
    label: str,
    require_existing_file: bool = True,
) -> dict[str, Any]:
    if not isinstance(raw_span, Mapping):
        raise CompactPipelineError(f"{label} must be an object")
    file_text = _required_text(raw_span, "file", label=label)
    path = _inside_root(Path(file_text), source_root, label=f"{label}.file")
    if require_existing_file and not path.is_file():
        raise CompactPipelineError(f"{label}.file does not exist: {path}")
    line_start = raw_span.get("line_start")
    line_end = raw_span.get("line_end")
    if (
        not isinstance(line_start, int)
        or isinstance(line_start, bool)
        or line_start < 1
    ):
        raise CompactPipelineError(f"{label}.line_start must be a positive integer")
    if (
        not isinstance(line_end, int)
        or isinstance(line_end, bool)
        or line_end < line_start
    ):
        raise CompactPipelineError(
            f"{label}.line_end must be an integer >= line_start"
        )
    if require_existing_file:
        stat = path.stat()
        line_count = _source_line_count(
            str(path),
            stat.st_size,
            stat.st_mtime_ns,
        )
        if line_end > line_count:
            raise CompactPipelineError(
                f"{label}.line_end={line_end} exceeds source line count "
                f"{line_count}"
            )
    text_sha256 = raw_span.get("text_sha256")
    if text_sha256 is not None and (
        not isinstance(text_sha256, str)
        or len(text_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in text_sha256.casefold())
    ):
        raise CompactPipelineError(
            f"{label}.text_sha256 must be a hexadecimal SHA-256"
        )
    if text_sha256 is not None and require_existing_file:
        stat = path.stat()
        observed_hash = _source_span_sha256(
            str(path),
            stat.st_size,
            stat.st_mtime_ns,
            line_start,
            line_end,
        )
        if observed_hash != text_sha256.casefold():
            raise CompactPipelineError(
                f"{label}.text_sha256 does not match the current OCR span"
            )
    return {
        **dict(raw_span),
        "file": str(path),
        "line_start": line_start,
        "line_end": line_end,
    }


def _span_is_covered(
    span: Mapping[str, Any],
    containers: Sequence[Mapping[str, Any]],
) -> bool:
    return any(
        str(container["file"]) == str(span["file"])
        and int(container["line_start"]) <= int(span["line_start"])
        and int(container["line_end"]) >= int(span["line_end"])
        for container in containers
    )


def validate_contract_versions(payload: Mapping[str, Any], *, label: str) -> None:
    expected = {
        "prompt_contract_version": PROMPT_CONTRACT_VERSION,
        "interpretation_contract_version": INTERPRETATION_CONTRACT_VERSION,
        "glossary_version": GLOSSARY_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise CompactPipelineError(
                f"{label}.{field} mismatch: expected {value}, "
                f"got {payload.get(field)!r}"
            )


def validate_locator_result_envelope(
    payload: Any,
    *,
    expected_input_fingerprint: str,
    allow_legacy_v1: bool = False,
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise CompactPipelineError("locator result must be an object")
    if payload.get("input_fingerprint") != expected_input_fingerprint:
        raise CompactPipelineError("locator result input_fingerprint mismatch")
    if payload.get("schema_version") == 1 and allow_legacy_v1:
        return payload
    if payload.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CompactPipelineError(
            f"locator result.schema_version must be {OUTPUT_SCHEMA_VERSION}"
        )
    validate_contract_versions(payload, label="locator result")
    if payload.get("locator_contract_version") != LOCATOR_CONTRACT_VERSION:
        raise CompactPipelineError(
            "locator result.locator_contract_version mismatch"
        )
    if not isinstance(payload.get("results"), list):
        raise CompactPipelineError("locator result.results must be an array")
    return payload


def validate_discovery_manifest(
    payload: Any,
    *,
    source_root: Path,
    expected_volume_id: str,
    expected_input_fingerprint: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise CompactPipelineError("discovery manifest must be an object")
    if payload.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CompactPipelineError(
            f"discovery manifest.schema_version must be {OUTPUT_SCHEMA_VERSION}"
        )
    validate_contract_versions(payload, label="discovery manifest")
    if payload.get("stage") != "discovery":
        raise CompactPipelineError("discovery manifest.stage must be 'discovery'")
    if payload.get("volume_id") != expected_volume_id:
        raise CompactPipelineError("discovery manifest.volume_id mismatch")
    if payload.get("input_fingerprint") != expected_input_fingerprint:
        raise CompactPipelineError("discovery manifest.input_fingerprint mismatch")
    manifest_root = Path(_required_text(payload, "source_root", label="discovery"))
    if manifest_root.resolve() != source_root.resolve():
        raise CompactPipelineError("discovery manifest.source_root mismatch")
    status = payload.get("status")
    if status not in DISCOVERY_STATUSES:
        raise CompactPipelineError(
            f"discovery manifest.status must be one of {sorted(DISCOVERY_STATUSES)}"
        )
    inspected = _required_list(payload, "inspected_files", label="discovery")
    inspected_files: set[str] = set()
    for index, value in enumerate(inspected):
        path = _inside_root(
            Path(str(value)),
            source_root,
            label=f"discovery.inspected_files[{index}]",
        )
        if not path.is_file():
            raise CompactPipelineError(
                f"discovery.inspected_files[{index}] does not exist: {path}"
            )
        if str(path) in inspected_files:
            raise CompactPipelineError(
                f"duplicate discovery inspected file: {path}"
            )
        inspected_files.add(str(path))
    segments = _required_list(payload, "segments", label="discovery")
    seen_ids: set[str] = set()
    normalized_segments: list[dict[str, Any]] = []
    by_file: dict[str, list[dict[str, Any]]] = {}
    for index, raw_segment in enumerate(segments):
        label = f"discovery.segments[{index}]"
        if not isinstance(raw_segment, Mapping):
            raise CompactPipelineError(f"{label} must be an object")
        segment_id = _required_text(raw_segment, "segment_id", label=label)
        if segment_id in seen_ids:
            raise CompactPipelineError(
                f"duplicate discovery segment_id: {segment_id}"
            )
        seen_ids.add(segment_id)
        role = raw_segment.get("role")
        if role not in SEGMENT_ROLES:
            raise CompactPipelineError(
                f"{label}.role must be one of {sorted(SEGMENT_ROLES)}"
            )
        _required_text(raw_segment, "reason", label=label)
        span = validate_source_span(
            raw_segment,
            source_root=source_root,
            label=label,
        )
        if span["file"] not in inspected_files:
            raise CompactPipelineError(
                f"{label}.file is absent from inspected_files"
            )
        normalized_segments.append(span)
        by_file.setdefault(str(span["file"]), []).append(span)
    for file_path, file_segments in by_file.items():
        ordered = sorted(
            file_segments,
            key=lambda item: (int(item["line_start"]), int(item["line_end"])),
        )
        for previous, current in zip(ordered, ordered[1:]):
            if int(current["line_start"]) <= int(previous["line_end"]):
                raise CompactPipelineError(
                    "overlapping discovery segments in "
                    f"{file_path}: {previous['segment_id']} and "
                    f"{current['segment_id']}"
                )
    expansion = _required_list(
        payload,
        "expansion_requests",
        label="discovery",
    )
    normalized_expansion: list[dict[str, Any]] = []
    seen_request_ids: set[str] = set()
    for index, raw_request in enumerate(expansion):
        label = f"discovery.expansion_requests[{index}]"
        if not isinstance(raw_request, Mapping):
            raise CompactPipelineError(f"{label} must be an object")
        request_id = _required_text(raw_request, "request_id", label=label)
        if request_id in seen_request_ids:
            raise CompactPipelineError(
                f"duplicate discovery expansion request_id: {request_id}"
            )
        seen_request_ids.add(request_id)
        _required_text(raw_request, "reason", label=label)
        direction = raw_request.get("direction")
        if direction not in {"before", "after", "both", "specific"}:
            raise CompactPipelineError(
                f"{label}.direction must be before|after|both|specific"
            )
        max_files = raw_request.get("max_files")
        if (
            not isinstance(max_files, int)
            or isinstance(max_files, bool)
            or not 1 <= max_files <= 200
        ):
            raise CompactPipelineError(
                f"{label}.max_files must be an integer from 1 to 200"
            )
        raw_anchors = _required_list(raw_request, "anchor_files", label=label)
        if not raw_anchors:
            raise CompactPipelineError(f"{label}.anchor_files must not be empty")
        anchors: list[str] = []
        for anchor_index, raw_anchor in enumerate(raw_anchors):
            anchor = _inside_root(
                Path(str(raw_anchor)),
                source_root,
                label=f"{label}.anchor_files[{anchor_index}]",
            )
            if not anchor.is_file():
                raise CompactPipelineError(
                    f"{label}.anchor_files[{anchor_index}] does not exist: {anchor}"
                )
            anchor_text = str(anchor)
            if anchor_text in anchors:
                raise CompactPipelineError(f"{label}.anchor_files contains duplicates")
            anchors.append(anchor_text)
        normalized_expansion.append(
            {**dict(raw_request), "anchor_files": anchors}
        )
    unresolved = _required_list(payload, "unresolved", label="discovery")
    if status == "complete" and expansion:
        raise CompactPipelineError(
            "complete discovery manifest cannot contain expansion_requests"
        )
    if status == "needs_expansion" and not expansion:
        raise CompactPipelineError(
            "needs_expansion discovery manifest requires expansion_requests"
        )
    return {
        **dict(payload),
        "inspected_files": sorted(inspected_files),
        "segments": normalized_segments,
        "expansion_requests": normalized_expansion,
        "unresolved": unresolved,
    }


def _validate_notation(
    raw_notation: Any,
    *,
    label: str,
    known_notation_keys: set[str] | None,
) -> None:
    if not isinstance(raw_notation, Mapping):
        raise CompactPipelineError(f"{label} must be an object")
    notation_key = _required_text(raw_notation, "notation_key", label=label)
    if known_notation_keys is not None and notation_key not in known_notation_keys:
        raise CompactPipelineError(
            f"{label}.notation_key is absent from the versioned glossary: "
            f"{notation_key!r}"
        )
    if raw_notation.get("resolution_status") not in NOTATION_STATUSES:
        raise CompactPipelineError(
            f"{label}.resolution_status must be one of "
            f"{sorted(NOTATION_STATUSES)}"
        )
    inherited = raw_notation.get("inherits_from_ref_order")
    if inherited is not None and (
        not isinstance(inherited, int)
        or isinstance(inherited, bool)
        or inherited < 1
    ):
        raise CompactPipelineError(
            f"{label}.inherits_from_ref_order must be a positive integer or null"
        )


def validate_semantic_fragment_v2(
    payload: Any,
    *,
    source_root: Path,
    expected_input_fingerprint: str,
    expected_section_key: str,
    known_notation_keys: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise CompactPipelineError("semantic fragment must be an object")
    if payload.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CompactPipelineError(
            f"semantic fragment.schema_version must be {OUTPUT_SCHEMA_VERSION}"
        )
    validate_contract_versions(payload, label="semantic fragment")
    _required_text(payload, "task_id", label="semantic fragment")
    if payload.get("input_fingerprint") != expected_input_fingerprint:
        raise CompactPipelineError("semantic fragment.input_fingerprint mismatch")
    consumed_raw = _required_list(
        payload,
        "consumed_spans",
        label="semantic fragment",
    )
    if not consumed_raw:
        raise CompactPipelineError(
            "semantic fragment.consumed_spans must not be empty"
        )
    consumed = [
        validate_source_span(
            span,
            source_root=source_root,
            label=f"semantic fragment.consumed_spans[{index}]",
        )
        for index, span in enumerate(consumed_raw)
    ]
    residual_raw = _required_list(
        payload,
        "residual_spans",
        label="semantic fragment",
    )
    residual = []
    for index, raw_span in enumerate(residual_raw):
        label = f"semantic fragment.residual_spans[{index}]"
        span = validate_source_span(
            raw_span,
            source_root=source_root,
            label=label,
        )
        _required_text(raw_span, "reason", label=label)
        residual.append(span)
    sections = _required_list(payload, "sections", label="semantic fragment")
    if len(sections) != 1 or not isinstance(sections[0], Mapping):
        raise CompactPipelineError(
            "semantic fragment.sections must own exactly one section"
        )
    if sections[0].get("section_key") != expected_section_key:
        raise CompactPipelineError(
            "semantic fragment section_key does not match manifest ownership"
        )
    section = sections[0]
    owned_by_alphabetical, ownership_reason = alphabetical_section_ownership(
        dict(section)
    )
    if not owned_by_alphabetical:
        raise CompactPipelineError(
            "semantic fragment section belongs outside the alphabetical pipeline: "
            f"{ownership_reason}"
        )
    for field in ("file_start", "file_end"):
        section_path = _inside_root(
            Path(_required_text(section, field, label="semantic fragment.sections[0]")),
            source_root,
            label=f"semantic fragment.sections[0].{field}",
        )
        if not section_path.is_file():
            raise CompactPipelineError(
                f"semantic fragment.sections[0].{field} does not exist: {section_path}"
            )
    taxonomy = section.get("raw_json")
    if not isinstance(taxonomy, Mapping):
        raise CompactPipelineError(
            "semantic fragment.sections[0].raw_json must be an object"
        )
    expected_taxonomy = {
        "pipeline_owner": {"alphabetical"},
        "alphabetical_role": {"owned_section"},
        "material_reference_mode": MATERIAL_REFERENCE_MODES,
        "scripture_mode": SCRIPTURE_MODES,
    }
    for field, accepted in expected_taxonomy.items():
        if taxonomy.get(field) not in accepted:
            raise CompactPipelineError(
                f"semantic fragment.sections[0].raw_json.{field} must be one of "
                f"{sorted(accepted)}"
            )
    entries = _required_list(payload, "entries", label="semantic fragment")
    entry_keys: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"semantic fragment.entries[{index}]"
        if not isinstance(entry, Mapping):
            raise CompactPipelineError(f"{label} must be an object")
        entry_key = _required_text(entry, "entry_key", label=label)
        if entry_key in entry_keys:
            raise CompactPipelineError(
                f"duplicate semantic fragment entry_key: {entry_key}"
            )
        entry_keys.add(entry_key)
        span = validate_source_span(
            entry.get("source_span"),
            source_root=source_root,
            label=f"{label}.source_span",
        )
        if not _span_is_covered(span, consumed):
            raise CompactPipelineError(
                f"{label}.source_span is not covered by consumed_spans"
            )
    refs = _required_list(payload, "refs", label="semantic fragment")
    _required_list(payload, "nodes", label="semantic fragment")
    _required_list(payload, "scripture_refs", label="semantic fragment")
    _required_list(payload, "unresolved", label="semantic fragment")
    _required_list(payload, "decision_log", label="semantic fragment")
    _required_list(payload, "notes", label="semantic fragment")
    for index, ref in enumerate(refs):
        label = f"semantic fragment.refs[{index}]"
        if not isinstance(ref, Mapping):
            raise CompactPipelineError(f"{label} must be an object")
        _required_text(ref, "entry_key", label=label)
        _required_text(ref, "ref_raw", label=label)
        if ref.get("ref_kind") not in MATERIAL_REF_KINDS:
            raise CompactPipelineError(
                f"{label}.ref_kind must be one of {sorted(MATERIAL_REF_KINDS)}"
            )
        if ref.get("target_file") is not None or ref.get(
            "target_file_probability"
        ) is not None:
            raise CompactPipelineError(
                f"{label} must leave semantic target fields null"
            )
        source_span = ref.get("source_span")
        if source_span is not None:
            span = validate_source_span(
                source_span,
                source_root=source_root,
                label=f"{label}.source_span",
            )
            if not _span_is_covered(span, consumed):
                raise CompactPipelineError(
                    f"{label}.source_span is not covered by consumed_spans"
                )
        notation = ref.get("notation", [])
        if not isinstance(notation, list):
            raise CompactPipelineError(f"{label}.notation must be an array")
        for notation_index, item in enumerate(notation):
            _validate_notation(
                item,
                label=f"{label}.notation[{notation_index}]",
                known_notation_keys=known_notation_keys,
            )
    coerce_semantic_payload(
        {
            "schema_version": 1,
            "volume": {},
            "coverage": {},
            "notes": [],
            "sections": sections,
            "nodes": payload.get("nodes") or [],
            "entries": entries,
            "refs": refs,
            "scripture_refs": payload.get("scripture_refs") or [],
        }
    )
    return {
        **dict(payload),
        "consumed_spans": consumed,
        "residual_spans": residual,
    }


def validate_non_overlapping_consumed_spans(
    fragments: Sequence[Mapping[str, Any]],
) -> None:
    spans: list[tuple[str, int, int, str]] = []
    for fragment in fragments:
        task_id = str(fragment.get("task_id") or "")
        for span in fragment.get("consumed_spans") or []:
            if isinstance(span, Mapping):
                spans.append(
                    (
                        str(span.get("file") or ""),
                        int(span.get("line_start") or 0),
                        int(span.get("line_end") or 0),
                        task_id,
                    )
                )
    spans.sort()
    for previous, current in zip(spans, spans[1:]):
        if (
            current[0] == previous[0]
            and current[1] <= previous[2]
            and current[3] != previous[3]
        ):
            raise CompactPipelineError(
                "semantic fragments consume overlapping source spans: "
                f"{previous[3]!r} and {current[3]!r} in {current[0]}"
            )


def validate_discovery_semantic_coverage(
    discovery: Mapping[str, Any],
    fragments: Sequence[Mapping[str, Any]],
) -> None:
    owned = [
        segment
        for segment in discovery.get("segments") or []
        if isinstance(segment, Mapping) and segment.get("role") == "owned"
    ]
    assigned: list[Mapping[str, Any]] = []
    for fragment in fragments:
        assigned.extend(
            span
            for field in ("consumed_spans", "residual_spans")
            for span in fragment.get(field) or []
            if isinstance(span, Mapping)
        )
    for span in assigned:
        if not any(_span_is_covered(span, [owned_span]) for owned_span in owned):
            raise CompactPipelineError(
                "semantic fragment span is outside discovery-owned material: "
                f"{span.get('file')}:{span.get('line_start')}-{span.get('line_end')}"
            )
    for owned_span in owned:
        pieces = sorted(
            (
                int(span["line_start"]),
                int(span["line_end"]),
            )
            for span in assigned
            if str(span.get("file")) == str(owned_span.get("file"))
            and int(span.get("line_start") or 0)
            >= int(owned_span.get("line_start") or 0)
            and int(span.get("line_end") or 0)
            <= int(owned_span.get("line_end") or 0)
        )
        cursor = int(owned_span["line_start"])
        for start, end in pieces:
            if start > cursor:
                break
            cursor = max(cursor, end + 1)
            if cursor > int(owned_span["line_end"]):
                break
        if cursor <= int(owned_span["line_end"]):
            raise CompactPipelineError(
                "discovery-owned segment is not completely explained by semantic "
                f"consumed/residual spans: {owned_span.get('segment_id')} "
                f"starting at uncovered line {cursor}"
            )
