"""Filesystem orchestration for the compact alphabetical-index pipeline.

The semantic and locator agents exchange small JSON artifacts by path.  Python
owns sharding, exact-once validation, deterministic assembly, and the optional
single repair pass.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping

from .alphabetical_artifact_validation import (
    validate_contract_versions,
    validate_discovery_semantic_coverage,
    validate_discovery_manifest,
    validate_locator_result_envelope,
    validate_non_overlapping_consumed_spans,
    validate_semantic_fragment_v2,
)
from .alphabetical_compact_pipeline import (
    CompactPipelineError,
    assemble_compact_payload,
    build_deterministic_locator_results,
    build_locator_items,
    build_repair_request,
    coerce_semantic_payload,
    merge_repair_results,
    shard_repair_request,
    shard_locator_items,
    standardize_locator_items,
    validate_locator_results,
)
from .alphabetical_checkpoints import (
    read_checkpointed_json,
    stable_json_fingerprint,
    update_source_snapshot,
    write_checkpointed_json,
)
from .alphabetical_prompt_contract import (
    DISCOVERY_CONTRACT_VERSION,
    GLOSSARY_DATA_PATH,
    GLOSSARY_VERSION,
    INTERPRETATION_CONTRACT_VERSION,
    LOCATOR_CONTRACT_VERSION,
    OUTPUT_SCHEMA_VERSION,
    PROMPT_CONTRACT_VERSION,
    prompt_reference_bundle,
    prompt_reference_lines,
)
from .editorial_page_estimator import estimate_editorial_pages
from .alphabetical_mechanical_analysis import build_mechanical_analysis
from .index_target_locator import resolve_index_targets, resolve_paired_page_image
from tools.scripture.citation_index import (
    DEFAULT_CITATION_DB,
    enrich_locator_items_from_citation_db,
)
from tools.scripture.evidence_locator import (
    ScriptureEvidenceConfig,
    add_scripture_evidence_candidates,
    infer_citation_format_profiles,
)


AgentRunner = Callable[[str, Path, str], None]
SemanticValidator = Callable[[Path], None]
SEMANTIC_CONTRACT_VERSION = PROMPT_CONTRACT_VERSION
FACSIMILE_HINT_CONTRACT_VERSION = 1


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def facsimile_inventory_fingerprint(source_root: Path) -> str:
    """Fingerprint sibling PNG identity without loading image contents."""

    images_dir = source_root.parent / "images"
    files: list[dict[str, Any]] = []
    if images_dir.is_dir():
        for path in sorted(images_dir.glob("*.png")):
            if not path.is_file():
                continue
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
    return stable_json_fingerprint(
        {
            "contract_version": FACSIMILE_HINT_CONTRACT_VERSION,
            "images_dir": str(images_dir.resolve()),
            "files": files,
        }
    )


def _known_notation_keys() -> set[str]:
    payload = _read_json(GLOSSARY_DATA_PATH)
    if not isinstance(payload, Mapping) or payload.get("glossary_version") != GLOSSARY_VERSION:
        raise CompactPipelineError("versioned notation glossary is invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise CompactPipelineError("versioned notation glossary entries must be an array")
    keys = {
        str(entry.get("notation_key") or "").strip()
        for entry in entries
        if isinstance(entry, Mapping)
    }
    if "" in keys or not keys:
        raise CompactPipelineError("versioned notation glossary contains invalid keys")
    return keys


def build_semantic_input(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    filtered_pages_file: Path,
    discovery_file: Path | None = None,
    mechanical_analysis_file: Path | None = None,
    source_snapshot_file: Path | None = None,
) -> dict[str, Any]:
    resolved_source_root = source_root.resolve()
    resolved_filtered_pages = filtered_pages_file.resolve()
    payload = {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        **prompt_reference_bundle(),
        "volume_id": volume_id,
        "collection": collection,
        "source_root": str(resolved_source_root),
        "filtered_pages_file": str(resolved_filtered_pages),
        "filtered_pages_sha256": _file_sha256(resolved_filtered_pages),
    }
    if discovery_file is not None:
        resolved_discovery = discovery_file.resolve()
        payload.update(
            {
                "discovery_file": str(resolved_discovery),
                "discovery_sha256": _file_sha256(resolved_discovery),
            }
        )
    if mechanical_analysis_file is not None:
        resolved_mechanical = mechanical_analysis_file.resolve()
        payload.update(
            {
                "mechanical_analysis_file": str(resolved_mechanical),
                "mechanical_analysis_sha256": _file_sha256(resolved_mechanical),
            }
        )
    if source_snapshot_file is not None:
        resolved_snapshot = source_snapshot_file.resolve()
        snapshot_fingerprint: str | None = None
        try:
            snapshot_payload = _read_json(resolved_snapshot)
            if isinstance(snapshot_payload, Mapping):
                snapshot_fingerprint = str(
                    snapshot_payload.get("source_snapshot_fingerprint") or ""
                ) or None
        except (OSError, json.JSONDecodeError):
            snapshot_fingerprint = None
        payload.update(
            {
                "source_snapshot_file": str(resolved_snapshot),
                "source_snapshot_fingerprint": snapshot_fingerprint,
            }
        )
    return payload


def build_discovery_prompt(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    prefilter_file: Path,
    discovery_file: Path,
    input_fingerprint: str,
) -> str:
    references = prompt_reference_lines()
    return f"""$alphabetical-index-extractor

PHASE: DISCOVERY

Inspect the alphabetical/remissive index topology of exactly one volume. Start from the
prefilter leads, investigate freely inside source_root, and write a segmented ownership
manifest. Do not extract entries or locate cited material in this phase.

RUNTIME
- volume_id: {volume_id}
- collection: {collection}
- source_root: {source_root}
- prefilter evidence: {prefilter_file}
- input_fingerprint: {input_fingerprint}
- required output: {discovery_file}

AUTHORITATIVE REFERENCES
{references}

AUTONOMY
- You may read any file inside source_root, use rg and existing read-only helpers, inspect
  neighbors, and expand beyond the prefilter.
- The prefilter is evidence, not ground truth. Generic INDEX/TABLE hits never prove ownership.
- Work from the physical end toward the beginning and keep independent PO fascicles or works
  distinguishable.

OUTPUT CONTRACT
- Write schema_version={OUTPUT_SCHEMA_VERSION}, stage=discovery, all four contract versions,
  volume_id, source_root, input_fingerprint, status, inspected_files, segments,
  expansion_requests, and unresolved.
- Every segment has segment_id, file, inclusive line_start/line_end, role, and reason.
  Roles are owned|boundary|context|uncertain.
- A file may contain multiple non-overlapping segments. ORDO RERUM and editorial closures are
  boundary material even when they begin in the middle of a file.
- status=complete requires no expansion_requests. Use needs_expansion when more evidence is
  required; never call an uninspected gap complete. needs_expansion is a durable, non-terminal
  checkpoint which the driver will redispatch for bounded expansion rounds.
- Every expansion request must be actionable: request_id, reason, non-empty anchor_files,
  direction=before|after|both|specific, and max_files (1..200). Do not repeat a request unchanged
  after inspecting it; move irreducible uncertainty to unresolved.
- Validate the artifact against the discovery schema named above before finishing.

Return only:
{{"status":"ok","volume_id":"{volume_id}","written_file":"{discovery_file}"}}
"""


def build_semantic_prompt(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    filtered_pages_file: Path,
    semantic_dir: Path,
    manifest_file: Path,
    semantic_input_fingerprint: str | None = None,
    discovery_file: Path | None = None,
    mechanical_analysis_file: Path | None = None,
) -> str:
    if semantic_input_fingerprint is None:
        semantic_input_fingerprint = _fingerprint(
            build_semantic_input(
                volume_id=volume_id,
                collection=collection,
                source_root=source_root,
                filtered_pages_file=filtered_pages_file,
                discovery_file=discovery_file,
                mechanical_analysis_file=mechanical_analysis_file,
            )
        )
    starting_evidence = discovery_file or filtered_pages_file
    mechanical_evidence = mechanical_analysis_file or "(not supplied)"
    references = prompt_reference_lines()
    return f"""$alphabetical-index-extractor

PHASE: SEMANTIC EXTRACTION

Read and structure the complete owned alphabetical/remissive index of one volume. Do not locate
cited material yet. The output is a set of small, independently validatable JSON fragments.

RUNTIME
- volume_id: {volume_id}
- collection: {collection}
- source_root: {source_root}
- prefilter evidence: {filtered_pages_file}
- discovery/prefilter starting evidence: {starting_evidence}
- deterministic regex/Aho-Corasick evidence: {mechanical_evidence}
- input_fingerprint: {semantic_input_fingerprint}
- artifact directory: {semantic_dir}
- required manifest, written last: {manifest_file}

AUTHORITATIVE REFERENCES
{references}

AUTONOMY AND SCOPE
- Read the references first. You may inspect any file inside source_root, use rg and existing
  read-only helpers, and create checkpoints only below the artifact directory.
- Starting evidence is a lead, not a reading limit. Follow complete sections and page
  continuations. Stop ownership at ORDO RERUM or another editorial closure, including mid-file.
- Use the deterministic evidence to avoid re-detecting clear headings, notation, locators and
  soft-wrap candidates. Validate every suggestion against OCR; it is evidence, not permission to
  invent an entry or normalize a damaged literal.
- If an unfamiliar notation matters, inspect the versioned glossary and corpus parallels inside
  this volume. Preserve the literal and write uncertainty or a proposal to
  {semantic_dir / "glossary_suggestions.json"}; never create a silent global rule.

FRAGMENT CONTRACT
- Write volume.json, coverage.json, and one schema_version={OUTPUT_SCHEMA_VERSION} fragment per
  owned section under {semantic_dir / "sections"}.
- volume.json is the plain volume object and coverage.json is the plain coverage object described
  by the output format. Fragments and manifest are the versioned control envelopes; do not add a
  second `volume` or `coverage` wrapper inside those component files.
- Every fragment copies all four contract versions and has task_id, input_fingerprint,
  consumed_spans, residual_spans, sections, nodes, entries, refs, scripture_refs, unresolved,
  decision_log, and notes.
- Every entry has an inclusive source_span inside consumed_spans. A ref may inherit its entry
  span; give it its own source_span when it occupies another OCR line or block.
- Preserve raw OCR exactly. Derived normalization and soft-wrap repair never replace raw fields.
- One logical name/subject entry may own many refs. Split printed page lists into separate refs.
  Preserve a printed range as one editorial_range; never invent its member pages. Open seq.,
  seqq., fin and passim forms also remain one conservative ref.
- Biblical passages belong in scripture_refs; material occurrences belong in refs and link by
  scripture_ref_order. Distinct passages are distinct entries. Incidental mentions emit no
  scripture_ref. Keep target_file, target_file_probability, and target_file_best null.
- Put pipeline_owner=alphabetical, alphabetical_role=owned_section,
  material_reference_mode, and scripture_mode in each section raw_json.
- Use notation objects from the versioned glossary. An unknown notation belongs in unresolved,
  not in an invented notation_key.
- Validate every fragment against the semantic fragment schema before writing the manifest.

COVERAGE AND MANIFEST
- consumed_spans and residual_spans together explain all assigned owned material. Record
  ownership-changing choices in decision_log, not free-form reasoning.
- Boundary decisions record file plus precise line_or_block and are never emitted as sections.
- If no owned index exists, write no section fragments and coverage.entries_status=
  no_index_section with a reason and inspected evidence. Empty owned sections require
  no_line_items or genuinely unrecoverable_ocr.
- The schema_version={OUTPUT_SCHEMA_VERSION} manifest copies all four contract versions and the
  exact input_fingerprint. It names volume_file, coverage_file, optional discovery_file,
  section_fragments, boundary_decisions, and optional glossary_suggestions_file.

Return only:
{{"status":"ok","volume_id":"{volume_id}","written_file":"{manifest_file}"}}
"""


def build_locator_prompt(
    *,
    volume_id: str,
    source_root: Path,
    shard_file: Path,
    result_file: Path,
) -> str:
    references = prompt_reference_lines()
    return f"""$alphabetical-index-extractor

PHASE: MATERIAL LOCATION

RUNTIME
- volume_id: {volume_id}
- source_root: {source_root}
- shard input: {shard_file}
- required result: {result_file}

AUTHORITATIVE REFERENCES
{references}

CONTRACT
- The shard owns exactly its listed (entry_key, ref_order) pairs. Inspect any necessary file and
  neighbors inside source_root, but do not modify semantic objects.
- Within each input item, locator_format_version=2 and locator_contract define the coordinate
  meanings. Treat its index_source,
  cited_location, search_hints, and target_resolution as the authoritative coordinate groups.
  Flat page_ref_int/target_file/editorial_anchor_file fields are compatibility aliases only.
- index_source contains OCR files holding the index text; cited_location contains printed
  editorial coordinates quoted by the index; target_resolution is the body OCR file decision.
- Treat estimator, regex and scripture helper candidates as evidence. Exclude the source index
  intervals and never equate a filename suffix with an editorial page.
- A candidate `facsimile_hint` is an intermediate, deterministic OCR-to-image pairing only. When
  punctuation, glyphs, columns, headers, or page layout matter, open its `image_path` before using
  visual evidence. `inspection_status=not_inspected` is never visual confirmation. Do not copy the
  PNG path into the locator result or canonical payload; report the observation against the OCR
  target file instead.
- Resolve siblings independently. A printed editorial page requires page-specific evidence tying
  it to target_file. A page-less target_locator may instead use the strict independent bundle:
  exact work-title locator + locator-number cooccurrence + section/work-family match.
- In result evidence, prefer canonical ocr_file_path and observed_editorial_page_number; the
  validator also accepts legacy file/editorial_page and normalizes them mechanically.
- ambiguous keeps target_file null and requires a reason, readable competing_candidates (at
  least two), and attempted files or searches. unrecoverable_ocr is only for evidence
  that cannot be recovered.
- Emit one result per owned pair and no others. Copy input_fingerprint and write
  schema_version={OUTPUT_SCHEMA_VERSION}, all four contract versions,
  locator_contract_version={LOCATOR_CONTRACT_VERSION}, volume_id and results.

Return only:
{{"status":"ok","volume_id":"{volume_id}","written_file":"{result_file}"}}
"""


def build_repair_prompt(
    *,
    volume_id: str,
    source_root: Path,
    repair_request_file: Path,
    repair_result_file: Path,
) -> str:
    references = prompt_reference_lines()
    return f"""$alphabetical-index-extractor

PHASE: LOCATOR REPAIR

RUNTIME
- volume_id: {volume_id}
- source_root: {source_root}
- repair request: {repair_request_file}
- required repair result: {repair_result_file}

AUTHORITATIVE REFERENCES
{references}

CONTRACT
- Process exactly the failed or unresolved locator objects in the request. Do not load or rewrite
  the semantic payload.
- Read locator.locator_contract first. Keep index OCR coordinates, cited editorial coordinates,
  and the resolved body OCR target separate; flat fields are compatibility aliases.
- Inspect candidates, neighbors and OCR-tolerant searches. resolved still requires page-specific
  evidence unless a page-less target_locator has the strict three-signal work-locator bundle.
- If a candidate has `facsimile_hint`, open its `image_path` when the unresolved issue is visual.
  The hint is uninspected pairing metadata; do not copy the PNG path into the repair result.
- Preserve ambiguous when readable candidates remain tied. Never turn material ambiguity into
  false unrecoverable_ocr. Ambiguous requires reason + competitors + attempts;
  unrecoverable_ocr requires reason + attempts.
- Copy request_fingerprint as input_fingerprint. Use the locator result shapes unchanged and
  write schema_version={OUTPUT_SCHEMA_VERSION}, all four contract versions,
  locator_contract_version={LOCATOR_CONTRACT_VERSION}, volume_id and results.

Return only:
{{"status":"ok","volume_id":"{volume_id}","written_file":"{repair_result_file}"}}
"""


def build_scripture_table_agent_prompt(
    *,
    volume_id: str,
    input_file: Path,
    result_file: Path,
) -> str:
    return f"""$alphabetical-index-extractor

TASK
Repair only the unresolved scripture-table rows in one compact, section-local artifact.
This supplies parsing evidence to the locator; do not inspect whole OCR pages or rewrite the
semantic entries.

RUNTIME
- volume_id: {volume_id}
- compact input: {input_file}
- required result: {result_file}

RULES
- Apply only the input `citation_format_profile` to its `unresolved_lines`.
- Preserve raw lines, tabs, spacing, uncertainty, and existing partial JSON as evidence.
- Emit exactly one result for every `line_id`, and no additional result.
- Results may contain only `line_id`, `status`, `scripture_refs`, `refs`, and `reason`.
- Return only values justified by that row and profile; never invent a passage, page, or file.
- Use `status=unresolved` with a reason when ambiguity remains.
- Copy the input `input_fingerprint` into the result.
- Write schema_version={OUTPUT_SCHEMA_VERSION}, all four contract versions, volume_id,
  input_fingerprint, and results to the required result path.

Return only:
{{"status":"ok","volume_id":"{volume_id}","written_file":"{result_file}"}}
"""


def _validated_table_repair_results(
    payload: Any,
    *,
    expected_line_ids: set[str],
    input_fingerprint: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise CompactPipelineError("scripture-table result must be an object")
    if payload.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CompactPipelineError(
            f"scripture-table result.schema_version must be {OUTPUT_SCHEMA_VERSION}"
        )
    validate_contract_versions(payload, label="scripture-table result")
    if payload.get("input_fingerprint") != input_fingerprint:
        raise CompactPipelineError("scripture-table result input_fingerprint mismatch")
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise CompactPipelineError("scripture-table result.results must be an array")
    allowed_fields = {
        "line_id",
        "status",
        "scripture_refs",
        "refs",
        "reason",
    }
    by_line: dict[str, dict[str, Any]] = {}
    for index, raw_result in enumerate(raw_results):
        if not isinstance(raw_result, Mapping):
            raise CompactPipelineError(
                f"scripture-table result.results[{index}] must be an object"
            )
        unexpected = set(raw_result) - allowed_fields
        if unexpected:
            raise CompactPipelineError(
                f"scripture-table result {index} has forbidden fields: "
                f"{sorted(unexpected)!r}"
            )
        line_id = str(raw_result.get("line_id") or "")
        if line_id not in expected_line_ids or line_id in by_line:
            raise CompactPipelineError(
                f"scripture-table result has unknown or duplicate line_id: {line_id!r}"
            )
        if raw_result.get("status") not in {
            "resolved",
            "repaired",
            "unresolved",
            "ambiguous",
        }:
            raise CompactPipelineError(
                f"scripture-table result {line_id!r} has invalid status"
            )
        for field in ("scripture_refs", "refs"):
            if not isinstance(raw_result.get(field, []), list):
                raise CompactPipelineError(
                    f"scripture-table result {line_id!r}.{field} must be an array"
                )
        by_line[line_id] = dict(raw_result)
    if set(by_line) != expected_line_ids:
        raise CompactPipelineError(
            "scripture-table result omitted line_id values: "
            f"{sorted(expected_line_ids - set(by_line))!r}"
        )
    return [by_line[line_id] for line_id in sorted(by_line)]


def _attach_table_repair_suggestions(
    locator_items: list[dict[str, Any]],
    *,
    unresolved_lines: list[Mapping[str, Any]],
    results: list[dict[str, Any]],
) -> int:
    lines_by_id = {
        str(line.get("line_id") or ""): line
        for line in unresolved_lines
        if isinstance(line, Mapping)
    }
    items_by_entry_and_ref = {
        (str(item.get("entry_key") or ""), int(item.get("ref_order") or 0)): item
        for item in locator_items
    }
    attached = 0
    for result in results:
        line_id = str(result["line_id"])
        source_line = lines_by_id[line_id]
        partial = source_line.get("partial_json")
        if not isinstance(partial, Mapping):
            continue
        entry_key = str(partial.get("entry_key") or "")
        pages = sorted(
            {
                int(ref["page_ref_int"])
                for ref in result.get("refs") or []
                if isinstance(ref, Mapping)
                and isinstance(ref.get("page_ref_int"), int)
                and not isinstance(ref.get("page_ref_int"), bool)
                and int(ref["page_ref_int"]) > 0
            }
        )
        source_ref_orders = {
            int(ref["ref_order"])
            for ref in partial.get("refs") or []
            if isinstance(ref, Mapping)
            and isinstance(ref.get("ref_order"), int)
            and not isinstance(ref.get("ref_order"), bool)
        }
        for ref_order in source_ref_orders:
            item = items_by_entry_and_ref.get((entry_key, ref_order))
            if item is None:
                continue
            evidence: dict[str, Any] = {
                "source": "section_local_table_repair",
                "line_id": line_id,
                "status": result["status"],
                "suggested_editorial_pages": pages,
                "evidence": str(source_line.get("raw_line") or "")[:320],
            }
            if result.get("reason"):
                evidence["reason"] = str(result["reason"])[:240]
            item.setdefault("table_reference_suggestions", []).append(evidence)
            attached += 1
    return attached


def run_scripture_table_repairs(
    *,
    volume_id: str,
    locator_items: list[dict[str, Any]],
    citation_format_profiles: Mapping[str, Mapping[str, Any]],
    scripture_evidence: Mapping[str, Any],
    intermediate_dir: Path,
    agent_runner: AgentRunner,
) -> dict[str, Any]:
    groups = [
        group
        for group in scripture_evidence.get("table_repair_groups") or []
        if isinstance(group, Mapping) and group.get("unresolved_lines")
    ]
    records: list[dict[str, Any]] = []
    attached_count = 0
    table_dir = intermediate_dir / "scripture_tables"
    for index, group in enumerate(groups, start=1):
        section_key = str(group.get("section_key") or "")
        unresolved_lines = [
            line
            for line in group.get("unresolved_lines") or []
            if isinstance(line, Mapping)
        ]
        line_ids = {str(line.get("line_id") or "") for line in unresolved_lines}
        if not section_key or "" in line_ids or len(line_ids) != len(unresolved_lines):
            records.append(
                {
                    "section_key": section_key,
                    "status": "invalid_input",
                    "error": "missing or duplicate section/line ownership",
                }
            )
            continue
        input_payload = {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            **prompt_reference_bundle(),
            "stage": "scripture_table_repair",
            "volume_id": volume_id,
            "section_key": section_key,
            "citation_format_profile": dict(
                citation_format_profiles.get(section_key, {})
            ),
            "unresolved_lines": unresolved_lines,
        }
        input_payload["input_fingerprint"] = _fingerprint(input_payload)
        input_file = table_dir / f"table-{index:04d}_input.json"
        result_file = table_dir / f"table-{index:04d}_result.json"
        _write_json(input_file, input_payload)
        try:
            if result_file.is_file():
                results = _validated_table_repair_results(
                    _read_json(result_file),
                    expected_line_ids=line_ids,
                    input_fingerprint=str(input_payload["input_fingerprint"]),
                )
                reused = True
            else:
                agent_runner(
                    build_scripture_table_agent_prompt(
                        volume_id=volume_id,
                        input_file=input_file,
                        result_file=result_file,
                    ),
                    result_file,
                    f"scripture_tables/table-{index:04d}",
                )
                results = _validated_table_repair_results(
                    _read_json(result_file),
                    expected_line_ids=line_ids,
                    input_fingerprint=str(input_payload["input_fingerprint"]),
                )
                reused = False
            attached = _attach_table_repair_suggestions(
                locator_items,
                unresolved_lines=unresolved_lines,
                results=results,
            )
            attached_count += attached
            records.append(
                {
                    "section_key": section_key,
                    "status": "ok",
                    "input_file": str(input_file),
                    "result_file": str(result_file),
                    "line_count": len(line_ids),
                    "suggestions_attached": attached,
                    "reused": reused,
                }
            )
        except Exception as exc:
            records.append(
                {
                    "section_key": section_key,
                    "status": "error",
                    "input_file": str(input_file),
                    "result_file": str(result_file),
                    "error": str(exc) or repr(exc),
                }
            )
    return {
        "schema_version": 1,
        "stage": "scripture_table_repair",
        "volume_id": volume_id,
        "group_count": len(groups),
        "suggestions_attached": attached_count,
        "groups": records,
    }


def _resolve_manifest_path(value: Any, *, manifest_file: Path) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = manifest_file.parent / path
    resolved = path.resolve()
    try:
        resolved.relative_to(manifest_file.parent.resolve())
    except ValueError as exc:
        raise CompactPipelineError(
            f"semantic manifest path escapes its artifact directory: {resolved}"
        ) from exc
    return resolved


def _resolve_manifest_path_matching_expected(
    value: Any,
    *,
    manifest_file: Path,
    expected_file: Path,
) -> Path:
    expected = expected_file.resolve()
    if value is None:
        return expected
    declared = Path(str(value)).expanduser()
    if not declared.is_absolute():
        declared = manifest_file.parent / declared
    declared = declared.resolve()
    if declared != expected:
        raise CompactPipelineError(
            "semantic manifest discovery_file does not match the discovery "
            f"checkpoint for the current run: declared={declared}, expected={expected}"
        )
    return expected


def load_semantic_manifest(
    manifest_file: Path,
    *,
    expected_volume_id: str,
    expected_collection: str,
    expected_source_root: Path,
    expected_input_fingerprint: str,
    expected_discovery_file: Path | None = None,
    expected_discovery_input_fingerprint: str | None = None,
) -> dict[str, Any]:
    manifest = _read_json(manifest_file)
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise CompactPipelineError(f"semantic manifest is not complete: {manifest_file}")
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, OUTPUT_SCHEMA_VERSION}:
        raise CompactPipelineError(
            f"unsupported semantic manifest.schema_version: {schema_version!r}"
        )
    is_v2 = schema_version == OUTPUT_SCHEMA_VERSION
    if is_v2:
        validate_contract_versions(manifest, label="semantic manifest")
    if manifest.get("stage") != "semantic":
        raise CompactPipelineError("semantic manifest.stage must be 'semantic'")
    if str(manifest.get("volume_id") or "") != expected_volume_id:
        raise CompactPipelineError(
            "semantic manifest volume_id does not match the current run"
        )
    if str(manifest.get("collection") or "") != expected_collection:
        raise CompactPipelineError(
            "semantic manifest collection does not match the current run"
        )
    manifest_source_root = Path(str(manifest.get("source_root") or "")).resolve()
    if manifest_source_root != expected_source_root.resolve():
        raise CompactPipelineError(
            "semantic manifest source_root does not match the current run"
        )
    manifest_fingerprint = str(
        manifest.get("input_fingerprint")
        or manifest.get("semantic_input_fingerprint")
        or ""
    )
    if manifest_fingerprint != expected_input_fingerprint:
        raise CompactPipelineError(
            "semantic manifest input fingerprint does not match current input"
        )
    fragments: list[Mapping[str, Any]] = []
    validated_v2_fragments: list[Mapping[str, Any]] = []
    validated_discovery: Mapping[str, Any] | None = None
    if is_v2 and (
        expected_discovery_file is not None
        or manifest.get("discovery_file") is not None
    ):
        if expected_discovery_file is not None:
            discovery_file = _resolve_manifest_path_matching_expected(
                manifest.get("discovery_file"),
                manifest_file=manifest_file,
                expected_file=expected_discovery_file,
            )
        else:
            discovery_file = _resolve_manifest_path(
                manifest.get("discovery_file"),
                manifest_file=manifest_file,
            )
        discovery_fingerprint = (
            expected_discovery_input_fingerprint
            or expected_input_fingerprint
        )
        validated_discovery = validate_discovery_manifest(
            _read_json(discovery_file),
            source_root=expected_source_root,
            expected_volume_id=expected_volume_id,
            expected_input_fingerprint=discovery_fingerprint,
        )
    volume_file = _resolve_manifest_path(
        manifest.get("volume_file"), manifest_file=manifest_file
    )
    coverage_file = _resolve_manifest_path(
        manifest.get("coverage_file"), manifest_file=manifest_file
    )
    volume_artifact = _read_json(volume_file)
    if isinstance(volume_artifact, dict) and "volume" not in volume_artifact:
        volume_artifact = {"volume": volume_artifact}
    fragments.append(volume_artifact)
    records = manifest.get("section_fragments")
    if not isinstance(records, list):
        raise CompactPipelineError("semantic manifest.section_fragments must be an array")
    seen_section_keys: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise CompactPipelineError(
                f"semantic manifest.section_fragments[{index}] must be an object"
            )
        section_key = str(record.get("section_key") or "").strip()
        if not section_key:
            raise CompactPipelineError(
                f"semantic manifest.section_fragments[{index}].section_key is required"
            )
        if section_key in seen_section_keys:
            raise CompactPipelineError(
                f"duplicate semantic section fragment ownership: {section_key}"
            )
        seen_section_keys.add(section_key)
        fragment_file = _resolve_manifest_path(
            record.get("file"), manifest_file=manifest_file
        )
        fragment = _read_json(fragment_file)
        if is_v2:
            fragment = validate_semantic_fragment_v2(
                fragment,
                source_root=expected_source_root,
                expected_input_fingerprint=expected_input_fingerprint,
                expected_section_key=section_key,
                known_notation_keys=_known_notation_keys(),
            )
            validated_v2_fragments.append(fragment)
        fragment_sections = (
            fragment.get("sections") if isinstance(fragment, Mapping) else None
        )
        fragment_section_keys = {
            str(item.get("section_key") or "")
            for item in (fragment_sections or [])
            if isinstance(item, Mapping)
        }
        if fragment_section_keys != {section_key}:
            raise CompactPipelineError(
                f"semantic fragment ownership mismatch for {section_key}: "
                f"{sorted(fragment_section_keys)!r}"
            )
        fragments.append(fragment)
    if is_v2:
        validate_non_overlapping_consumed_spans(validated_v2_fragments)
        if validated_discovery is not None:
            validate_discovery_semantic_coverage(
                validated_discovery,
                validated_v2_fragments,
            )
    boundary_decisions = manifest.get("boundary_decisions")
    if not isinstance(boundary_decisions, list):
        raise CompactPipelineError("semantic manifest.boundary_decisions must be an array")
    for index, decision in enumerate(boundary_decisions):
        if not isinstance(decision, Mapping):
            raise CompactPipelineError(
                f"semantic manifest.boundary_decisions[{index}] must be an object"
            )
        if decision.get("kind") != "stop_boundary":
            raise CompactPipelineError(
                f"semantic boundary {index} kind must be 'stop_boundary'"
            )
        for field in ("heading_raw", "file", "line_or_block", "reason"):
            value = decision.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise CompactPipelineError(
                    f"semantic boundary {index} requires non-empty {field}"
                )
    coverage_artifact = _read_json(coverage_file)
    if isinstance(coverage_artifact, dict) and "coverage" not in coverage_artifact:
        coverage_artifact = {"coverage": coverage_artifact}
    fragments.append(coverage_artifact)
    payload = coerce_semantic_payload(fragments)
    volume = payload.get("volume", {})
    volume_id = str(volume.get("volume_id") or "")
    if volume_id != expected_volume_id:
        raise CompactPipelineError(
            f"semantic volume mismatch: expected={expected_volume_id!r}, "
            f"payload={volume_id!r}"
        )
    if str(volume.get("collection") or "") != expected_collection:
        raise CompactPipelineError(
            "semantic payload collection does not match the current run"
        )
    payload_source_root = Path(str(volume.get("source_root") or "")).resolve()
    if payload_source_root != expected_source_root.resolve():
        raise CompactPipelineError(
            "semantic payload source_root does not match the current run"
        )
    coverage = payload.get("coverage")
    refs = payload.get("refs")
    if (
        isinstance(coverage, dict)
        and coverage.get("locator_status") is None
        and isinstance(refs, list)
        and any(
            isinstance(ref, Mapping) and ref.get("target_file") is None
            for ref in refs
        )
    ):
        coverage["locator_status"] = "partial"
    return payload


_TRAILING_ROMAN_RE = re.compile(r"\s+([ivxlcdm]+)$", re.IGNORECASE)
_ROMAN_NAME_ORDINALS = {
    "i": ("primus", "primi", "primo", "primum"),
    "ii": ("secundus", "secundi", "secundo", "secundum"),
    "iii": ("tertius", "tertii", "tertio", "tertium"),
    "iv": ("quartus", "quarti", "quarto", "quartum"),
    "v": ("quintus", "quinti", "quinto", "quintum"),
    "vi": ("sextus", "sexti", "sexto", "sextum"),
    "vii": ("septimus", "septimi", "septimo", "septimum"),
    "viii": ("octavus", "octavi", "octavo", "octavum"),
    "ix": ("nonus", "noni", "nono", "nonum"),
    "x": ("decimus", "decimi", "decimo", "decimum"),
}
_LATIN_SINGLE_NAME_RE = re.compile(r"^[A-Za-zÆŒæœ]{3,}$")


def _latin_name_case_variants(name: str) -> list[str]:
    """Generate conservative Latin case forms for one-token onomastic names."""

    if not _LATIN_SINGLE_NAME_RE.fullmatch(name):
        return []
    folded = name.casefold()
    if folded.endswith("us") and len(name) >= 6:
        stem = name[:-2]
        return [f"{stem}{suffix}" for suffix in ("i", "o", "um")]
    if folded.endswith("o"):
        return [f"{name}{suffix}" for suffix in ("nis", "ni", "nem", "ne")]
    if folded.endswith("a") and len(name) >= 5:
        stem = name[:-1]
        return [f"{stem}{suffix}" for suffix in ("ae", "am")]
    return []


def _search_queries(item: Mapping[str, Any]) -> list[str]:
    lemma = str(item.get("lemma_raw") or "").strip()
    if not lemma:
        return []
    repaired = re.sub(
        r"(?<=[^\W\d_])-\s*\n\s*(?=[^\W\d_])",
        "",
        lemma,
    )
    candidates = [repaired]
    first_clause = re.split(r"\s*[;:]\s*|\s+[—–]\s+", repaired, maxsplit=1)[0].strip()
    if len(first_clause) >= 12 and len(first_clause.split()) >= 2:
        candidates.append(first_clause)
    if str(item.get("section_kind") or "").startswith("onomastic_"):
        # Editorial descriptions ("bp. of ...", "chamberlain", etc.) are useful
        # semantics but often do not occur verbatim in the referenced body text.
        name_head = re.split(r"[,;(]", repaired, maxsplit=1)[0].strip(" —–-")
        if len(name_head) >= 4:
            candidates.append(name_head)
        roman_match = _TRAILING_ROMAN_RE.search(name_head)
        if roman_match:
            base_name = name_head[: roman_match.start()].strip()
            if len(base_name) >= 5:
                candidates.append(base_name)
            for ordinal in _ROMAN_NAME_ORDINALS.get(
                roman_match.group(1).casefold(),
                (),
            ):
                candidates.append(f"{base_name} {ordinal}")
        else:
            base_name = name_head
        candidates.extend(_latin_name_case_variants(base_name))
    return list(dict.fromkeys(value for value in candidates if value))


def _helper_request(
    locator_items: list[dict[str, Any]],
    *,
    volume_id: str,
    source_root: Path,
    workers: int = 1,
) -> dict[str, Any]:
    representative_by_entry: dict[str, dict[str, Any]] = {}
    sibling_pages_by_entry: dict[str, set[int]] = {}
    section_entries: dict[str, list[dict[str, Any]]] = {}
    for item in locator_items:
        entry_key = str(item.get("entry_key") or "")
        representative_by_entry.setdefault(entry_key, item)
        sibling_pages_by_entry.setdefault(entry_key, set()).update(
            int(value)
            for value in item.get("cited_pages") or []
            if isinstance(value, int) and not isinstance(value, bool)
        )
    for item in representative_by_entry.values():
        section_entries.setdefault(str(item.get("section_key") or ""), []).append(item)
    neighbor_groups_by_entry: dict[str, list[list[str]]] = {}
    for section_items in section_entries.values():
        section_items.sort(
            key=lambda item: (
                int(item.get("entry_order"))
                if str(item.get("entry_order") or "").isdigit()
                else 2**31 - 1,
                str(item.get("entry_key") or ""),
            )
        )
        for index, item in enumerate(section_items):
            groups: list[list[str]] = []
            if str(item.get("section_kind") or "").startswith("onomastic_"):
                for neighbor_index in range(
                    max(0, index - 2),
                    min(len(section_items), index + 3),
                ):
                    if neighbor_index == index:
                        continue
                    queries = _search_queries(section_items[neighbor_index])
                    if queries:
                        groups.append(queries[:6])
            neighbor_groups_by_entry[str(item.get("entry_key") or "")] = groups
    entries = []
    for item in locator_items:
        lemma = str(item.get("lemma_raw") or "").strip()
        context = str(item.get("entry_excerpt") or "")[:300]
        entry_key = str(item.get("entry_key") or "")
        entries.append(
            {
                "entry_id": item["locator_key"],
                "lemma_raw": lemma,
                "query_names": _search_queries(item),
                "section_kind": item.get("section_kind"),
                "section_heading": item.get("section_heading"),
                "section_file_start": item.get("section_file_start"),
                "section_file_end": item.get("section_file_end"),
                "excluded_index_intervals": deepcopy(
                    item.get("excluded_index_intervals") or []
                ),
                "ref_kind": item.get("ref_kind"),
                "ref_raw": item.get("ref_raw"),
                "neighbor_query_groups": neighbor_groups_by_entry.get(entry_key, []),
                "sibling_page_hint_ints": sorted(
                    sibling_pages_by_entry.get(entry_key, set())
                ),
                "page_hints": [
                    str(item.get("page_ref_raw") or item.get("page_ref_int") or "")
                ],
                "page_hint_ints": list(item.get("cited_pages") or []),
                "context_raw": context,
            }
        )
    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": 3,
            "adjacency_window": 4,
            "workers": max(1, workers),
        },
        "entries": entries,
    }


def _compact_helper_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    raw_evidence = [
        item
        for item in candidate.get("evidence") or []
        if isinstance(item, Mapping)
    ]
    priority_kinds = {
        "body_locator_match",
        "body_locator_cer_match",
        "body_locator_name_unique",
        "body_work_locator_match",
        "header_work_locator_match",
        "work_locator_number_cooccurrence",
        "section_heading_family_match",
        "editorial_page_cer_neighbor_validated",
        "internal_locator_vs_editorial_sequence",
        "onomastic_neighbor_cohort",
        "onomastic_neighbor_cohort_unique",
        "onomastic_sibling_hint_family",
        "section_heading_family_match",
        "body_name_match",
        "body_name_fuzzy",
        "inferred_page_match",
        "estimator_page_match",
    }
    ordered_evidence = sorted(
        enumerate(raw_evidence),
        key=lambda pair: (
            0 if str(pair[1].get("kind") or "") in priority_kinds else 1,
            pair[0],
        ),
    )
    image_path = str(
        candidate.get("image_path") or candidate.get("image_file") or ""
    ).strip()
    compact = {
        "file": candidate.get("file"),
        "probability": candidate.get("probability"),
        "candidate_role": candidate.get("candidate_role"),
        "reason_summary": candidate.get("reason_summary"),
        "inferred_printed_page": candidate.get("inferred_printed_page"),
        "evidence": [
            {
                "kind": item.get("kind"),
                "raw": str(item.get("raw") or "")[:160],
                "weight": item.get("weight"),
            }
            for _index, item in ordered_evidence[:8]
        ],
    }
    if image_path:
        compact["facsimile_hint"] = {
            "image_path": image_path,
            "pairing_basis": "physical_sequence_suffix",
            "editorial_page_inferred": False,
            "inspection_status": "not_inspected",
        }
    return compact


def attach_candidate_facsimile_hints(
    locator_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach uninspected sibling-image hints to intermediate candidates only."""

    for item in locator_items:
        for candidate in item.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            file_path = str(candidate.get("file") or "").strip()
            if not file_path:
                continue
            image_path = str(resolve_paired_page_image(file_path) or "").strip()
            candidate.pop("image_file", None)
            candidate.pop("image_path", None)
            if not image_path:
                candidate.pop("facsimile_hint", None)
                continue
            candidate["facsimile_hint"] = {
                "image_path": image_path,
                "pairing_basis": "physical_sequence_suffix",
                "editorial_page_inferred": False,
                "inspection_status": "not_inspected",
            }
    return locator_items


def _merge_locator_candidates(
    existing_candidates: list[Any],
    helper_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_file: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for candidate in [*existing_candidates, *helper_candidates]:
        if not isinstance(candidate, Mapping):
            continue
        file_path = str(candidate.get("file") or "").strip()
        if not file_path:
            continue
        if file_path not in by_file:
            by_file[file_path] = dict(candidate)
            by_file[file_path]["file"] = file_path
            by_file[file_path]["evidence"] = []
            order.append(file_path)
        merged = by_file[file_path]
        for field in (
            "candidate_role",
            "helper_status",
            "helper_is_best",
            "helper_rank",
            "inferred_printed_page",
            "matched_page",
            "reason_summary",
            "facsimile_hint",
        ):
            if candidate.get(field) is not None:
                merged[field] = deepcopy(candidate[field])
        probabilities = [
            value
            for value in (merged.get("probability"), candidate.get("probability"))
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if probabilities:
            merged["probability"] = max(float(value) for value in probabilities)
        seen_evidence = {
            (
                str(evidence.get("kind") or ""),
                str(evidence.get("detail") or evidence.get("raw") or ""),
            )
            for evidence in merged["evidence"]
            if isinstance(evidence, Mapping)
        }
        for evidence in candidate.get("evidence") or []:
            if not isinstance(evidence, Mapping):
                continue
            key = (
                str(evidence.get("kind") or ""),
                str(evidence.get("detail") or evidence.get("raw") or ""),
            )
            if key not in seen_evidence:
                merged["evidence"].append(deepcopy(dict(evidence)))
                seen_evidence.add(key)
    return sorted(
        (by_file[file_path] for file_path in order),
        key=lambda candidate: (
            -float(candidate.get("probability") or 0.0),
            str(candidate.get("file") or ""),
        ),
    )


def add_deterministic_text_candidates(
    locator_items: list[dict[str, Any]],
    *,
    volume_id: str,
    source_root: Path,
    workers: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not locator_items:
        return locator_items, {
            "schema_version": 1,
            "volume_id": volume_id,
            "estimator_status": "unsupported_collection",
            "fallback": "index_target_locator",
            "entries": [],
        }
    request = _helper_request(
        locator_items,
        volume_id=volume_id,
        source_root=source_root,
        workers=workers,
    )
    raw_result = resolve_index_targets(request)
    by_key = {
        str(item.get("entry_id") or ""): item
        for item in (raw_result.get("entries") or [])
        if isinstance(item, Mapping)
    }
    condensed_entries = []
    for item in locator_items:
        result = by_key.get(str(item["locator_key"]), {})
        candidates = []
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
        item["candidates"] = _merge_locator_candidates(
            list(item.get("candidates") or []),
            candidates,
        )
        condensed_entries.append(
            {
                "locator_key": item["locator_key"],
                "status": result.get("status") or "unresolved",
                "candidates": item["candidates"],
            }
        )
    artifact = {
        "schema_version": 1,
        "volume_id": volume_id,
        "estimator_status": "unsupported_collection",
        "fallback": "index_target_locator",
        "entries": condensed_entries,
    }
    attach_candidate_facsimile_hints(locator_items)
    return locator_items, artifact


def add_po_fallback_candidates(
    locator_items: list[dict[str, Any]],
    *,
    volume_id: str,
    source_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Backward-compatible alias for the collection-neutral text locator."""

    return add_deterministic_text_candidates(
        locator_items,
        volume_id=volume_id,
        source_root=source_root,
    )


def _valid_existing_results(
    items: list[dict[str, Any]],
    result_file: Path,
    *,
    input_fingerprint: str,
    source_root: Path,
    post_repair: bool = False,
) -> bool:
    if not result_file.is_file():
        return False
    try:
        payload = _read_json(result_file)
        validate_locator_result_envelope(
            payload,
            expected_input_fingerprint=input_fingerprint,
        )
        report = validate_locator_results(
            items,
            payload,
            post_repair=post_repair,
            source_root=source_root,
        )
    except (OSError, json.JSONDecodeError, CompactPipelineError):
        return False
    return report["submitted_count"] == len(items) and report["status"] == "ok"


def _locator_item_fingerprint(item: Mapping[str, Any]) -> str:
    return stable_json_fingerprint(
        {
            "locator_contract_version": LOCATOR_CONTRACT_VERSION,
            "prompt_contract_version": PROMPT_CONTRACT_VERSION,
            "item": item,
        }
    )


def _locator_cache_file(cache_dir: Path, locator_key_value: str) -> Path:
    digest = hashlib.sha256(locator_key_value.encode("utf-8")).hexdigest()
    return cache_dir / digest[:2] / f"{digest}.json"


def _load_locator_result_cache(
    items: list[dict[str, Any]],
    *,
    cache_dir: Path,
    source_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for item in items:
        locator_key_value = str(item.get("locator_key") or "")
        cache_file = _locator_cache_file(cache_dir, locator_key_value)
        try:
            payload = _read_json(cache_file)
            if not isinstance(payload, Mapping):
                raise CompactPipelineError("locator cache entry must be an object")
            if payload.get("schema_version") != 1:
                raise CompactPipelineError("locator cache schema mismatch")
            if payload.get("locator_key") != locator_key_value:
                raise CompactPipelineError("locator cache ownership mismatch")
            if payload.get("item_input_fingerprint") != _locator_item_fingerprint(
                item
            ):
                raise CompactPipelineError("locator cache input mismatch")
            result = payload.get("result")
            report = validate_locator_results(
                [item],
                [result],
                source_root=source_root,
            )
            if report["status"] != "ok" or report["accepted_count"] != 1:
                raise CompactPipelineError("locator cache result is invalid")
            accepted.append(dict(report["accepted_results"][0]))
        except (
            OSError,
            json.JSONDecodeError,
            CompactPipelineError,
            TypeError,
        ):
            pending.append(item)
    return accepted, pending


def _store_locator_result_cache(
    items: list[dict[str, Any]],
    results: Any,
    *,
    cache_dir: Path,
    source_root: Path,
) -> int:
    report = validate_locator_results(
        items,
        results,
        source_root=source_root,
    )
    accepted_by_pair = {
        (str(result["entry_key"]), int(result["ref_order"])): result
        for result in report["accepted_results"]
    }
    stored = 0
    for item in items:
        pair = (str(item["entry_key"]), int(item["ref_order"]))
        result = accepted_by_pair.get(pair)
        if result is None:
            continue
        locator_key_value = str(item["locator_key"])
        _write_json(
            _locator_cache_file(cache_dir, locator_key_value),
            {
                "schema_version": 1,
                "locator_key": locator_key_value,
                "item_input_fingerprint": _locator_item_fingerprint(item),
                "result": result,
            },
        )
        stored += 1
    return stored


def run_compact_extraction(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    filtered_pages_file: Path,
    intermediate_dir: Path,
    output_file: Path,
    agent_runner: AgentRunner,
    semantic_validator: SemanticValidator | None = None,
    locator_chunk_size: int = 40,
    locator_workers: int = 1,
    scripture_evidence_top_n: int = 4,
    scripture_evidence_snippet_chars: int = 220,
    scripture_db_path: Path = DEFAULT_CITATION_DB,
    deterministic_text_locator: bool = True,
    deterministic_locator_workers: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    semantic_dir = intermediate_dir / "semantic"
    manifest_file = semantic_dir / "manifest.json"
    semantic_payload_file = intermediate_dir / "semantic_payload.json"
    mechanical_analysis_file = intermediate_dir / "mechanical_analysis.json"
    source_snapshot_file = intermediate_dir / "ocr_source_snapshot.json"
    source_snapshot = update_source_snapshot(source_snapshot_file, source_root)
    source_snapshot_fingerprint = str(
        source_snapshot["source_snapshot_fingerprint"]
    )
    facsimile_fingerprint = facsimile_inventory_fingerprint(source_root)
    try:
        filtered_pages = _read_json(filtered_pages_file)
    except (OSError, json.JSONDecodeError):
        filtered_pages = {}
    if not isinstance(filtered_pages, Mapping):
        filtered_pages = {}
    mechanical_input_fingerprint = stable_json_fingerprint(
        {
            "stage_contract": 2,
            "stage": "mechanical_semantic_analysis",
            "volume_id": volume_id,
            "collection": collection,
            "filtered_pages_sha256": _file_sha256(filtered_pages_file),
            "source_snapshot_fingerprint": source_snapshot_fingerprint,
            "prompt_references": prompt_reference_bundle(),
        }
    )
    mechanical_analysis = read_checkpointed_json(
        mechanical_analysis_file,
        stage="mechanical_semantic_analysis",
        input_fingerprint=mechanical_input_fingerprint,
    )
    if not isinstance(mechanical_analysis, Mapping):
        mechanical_analysis = build_mechanical_analysis(
            volume_id=volume_id,
            collection=collection,
            source_root=source_root,
            filtered_pages=filtered_pages,
        )
        write_checkpointed_json(
            mechanical_analysis_file,
            mechanical_analysis,
            stage="mechanical_semantic_analysis",
            input_fingerprint=mechanical_input_fingerprint,
            dependencies={
                "filtered_pages_sha256": _file_sha256(filtered_pages_file),
                "source_snapshot_fingerprint": source_snapshot_fingerprint,
            },
            summary={
                "inspected_file_count": mechanical_analysis.get(
                    "inspected_file_count"
                ),
                "candidate_line_count": mechanical_analysis.get(
                    "candidate_line_count"
                ),
            },
        )
    mechanical_analysis = dict(mechanical_analysis)
    semantic_input = build_semantic_input(
        volume_id=volume_id,
        collection=collection,
        source_root=source_root,
        filtered_pages_file=filtered_pages_file,
        mechanical_analysis_file=mechanical_analysis_file,
        source_snapshot_file=source_snapshot_file,
    )
    semantic_input_fingerprint = _fingerprint(semantic_input)
    semantic_input_file = intermediate_dir / "semantic_input.json"
    _write_json(
        semantic_input_file,
        {
            **semantic_input,
            "semantic_input_fingerprint": semantic_input_fingerprint,
        },
    )
    semantic_prompt = build_semantic_prompt(
        volume_id=volume_id,
        collection=collection,
        source_root=source_root,
        filtered_pages_file=filtered_pages_file,
        semantic_dir=semantic_dir,
        manifest_file=manifest_file,
        semantic_input_fingerprint=semantic_input_fingerprint,
        mechanical_analysis_file=mechanical_analysis_file,
    )
    prompt_file = intermediate_dir / "semantic_prompt.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(semantic_prompt, encoding="utf-8")
    if dry_run:
        return {
            "status": "dry-run",
            "volume_id": volume_id,
            "semantic_prompt_file": str(prompt_file),
            "semantic_input_file": str(semantic_input_file),
            "semantic_input_fingerprint": semantic_input_fingerprint,
            "semantic_manifest_file": str(manifest_file),
            "mechanical_analysis_file": str(mechanical_analysis_file),
            "source_snapshot_file": str(source_snapshot_file),
            "source_snapshot_fingerprint": source_snapshot_fingerprint,
            "mechanical_candidate_line_count": mechanical_analysis[
                "candidate_line_count"
            ],
            "downstream": "awaiting_semantic",
        }

    def load_and_validate_semantic() -> dict[str, Any]:
        loaded = load_semantic_manifest(
            manifest_file,
            expected_volume_id=volume_id,
            expected_collection=collection,
            expected_source_root=source_root,
            expected_input_fingerprint=semantic_input_fingerprint,
        )
        _write_json(semantic_payload_file, loaded)
        if semantic_validator is not None:
            semantic_validator(semantic_payload_file)
        return loaded

    semantic: dict[str, Any] | None = None
    if manifest_file.is_file():
        try:
            semantic = load_and_validate_semantic()
        except (OSError, json.JSONDecodeError, CompactPipelineError, SystemExit):
            semantic = None
    if semantic is None:
        agent_runner(semantic_prompt, manifest_file, "semantic")
        try:
            semantic = load_and_validate_semantic()
        except (OSError, json.JSONDecodeError, CompactPipelineError, SystemExit) as exc:
            validation_error_file = semantic_dir / "validation_error.txt"
            validation_error_file.write_text(str(exc) or repr(exc), encoding="utf-8")
            repair_prompt = (
                semantic_prompt
                + "\n\nSEMANTIC CHECKPOINT REPAIR\n"
                + f"- The deterministic validator rejected the artifacts: {validation_error_file}\n"
                + "- Read the exact error, preserve valid extracted material, correct only the "
                "invalid semantic artifacts, and rewrite the manifest last.\n"
            )
            agent_runner(repair_prompt, manifest_file, "semantic_repair")
            semantic = load_and_validate_semantic()

    estimator_file = intermediate_dir / "editorial_page_map.json"
    if collection in {"PG", "PL"}:
        estimator_input_fingerprint = stable_json_fingerprint(
            {
                "stage_contract": 2,
                "stage": "editorial_page_inference",
                "volume_id": volume_id,
                "collection": collection,
                "window": 4,
                "source_snapshot_fingerprint": source_snapshot_fingerprint,
            }
        )
        estimator = read_checkpointed_json(
            estimator_file,
            stage="editorial_page_inference",
            input_fingerprint=estimator_input_fingerprint,
        )
        if not isinstance(estimator, Mapping):
            estimator = estimate_editorial_pages(
                volume_id=volume_id,
                collection=collection,
                source_root=source_root,
                window=4,
            )
            write_checkpointed_json(
                estimator_file,
                estimator,
                stage="editorial_page_inference",
                input_fingerprint=estimator_input_fingerprint,
                dependencies={
                    "source_snapshot_fingerprint": source_snapshot_fingerprint,
                },
                summary={
                    "file_count": len(estimator.get("files") or []),
                },
            )
        locator_items = build_locator_items(semantic, estimator)
    else:
        locator_items = build_locator_items(semantic)
        estimator = {
            "schema_version": 1,
            "volume_id": volume_id,
            "estimator_status": "unsupported_collection",
            "fallback": "index_target_locator"
            if deterministic_text_locator
            else "disabled",
            "entries": [],
        }
        _write_json(estimator_file, estimator)

    deterministic_text_evidence: dict[str, Any] = {
        "schema_version": 1,
        "volume_id": volume_id,
        "status": "disabled",
        "entries": [],
    }
    deterministic_text_stage_file = (
        intermediate_dir / "deterministic_text_stage.json"
    )
    if deterministic_text_locator and locator_items:
        deterministic_text_input_fingerprint = stable_json_fingerprint(
            {
                "stage_contract": 2,
                "stage": "deterministic_text_location",
                "volume_id": volume_id,
                "source_snapshot_fingerprint": source_snapshot_fingerprint,
                "facsimile_hint_contract_version": (
                    FACSIMILE_HINT_CONTRACT_VERSION
                ),
                "facsimile_inventory_fingerprint": facsimile_fingerprint,
                "workers_semantics": "workers_do_not_change_results",
                "locator_items": locator_items,
            }
        )
        deterministic_text_stage = read_checkpointed_json(
            deterministic_text_stage_file,
            stage="deterministic_text_location",
            input_fingerprint=deterministic_text_input_fingerprint,
        )
        if isinstance(deterministic_text_stage, Mapping) and isinstance(
            deterministic_text_stage.get("locator_items"), list
        ):
            locator_items = [
                dict(item)
                for item in deterministic_text_stage["locator_items"]
                if isinstance(item, Mapping)
            ]
            deterministic_text_evidence = dict(
                deterministic_text_stage.get("evidence") or {}
            )
        else:
            locator_items, deterministic_text_evidence = (
                add_deterministic_text_candidates(
                    locator_items,
                    volume_id=volume_id,
                    source_root=source_root,
                    workers=deterministic_locator_workers,
                )
            )
            write_checkpointed_json(
                deterministic_text_stage_file,
                {
                    "schema_version": 1,
                    "volume_id": volume_id,
                    "locator_items": locator_items,
                    "evidence": deterministic_text_evidence,
                },
                stage="deterministic_text_location",
                input_fingerprint=deterministic_text_input_fingerprint,
                dependencies={
                    "source_snapshot_fingerprint": source_snapshot_fingerprint,
                },
                summary={"item_count": len(locator_items)},
            )
    deterministic_text_evidence_file = (
        intermediate_dir / "deterministic_text_locator.json"
    )
    _write_json(deterministic_text_evidence_file, deterministic_text_evidence)

    citation_format_profiles = infer_citation_format_profiles(
        semantic,
        collection=collection,
    )
    if scripture_db_path.is_file():
        scripture_db_stat = scripture_db_path.stat()
        scripture_db_signature: dict[str, Any] | None = {
            "path": str(scripture_db_path.resolve()),
            "size": scripture_db_stat.st_size,
            "mtime_ns": scripture_db_stat.st_mtime_ns,
        }
    else:
        scripture_db_signature = None
    scripture_candidate_input_fingerprint = stable_json_fingerprint(
        {
            "stage_contract": 2,
            "stage": "scripture_candidate_location",
            "volume_id": volume_id,
            "collection": collection,
            "source_snapshot_fingerprint": source_snapshot_fingerprint,
            "scripture_db": scripture_db_signature,
            "max_candidates": scripture_evidence_top_n,
            "snippet_chars": scripture_evidence_snippet_chars,
            "citation_format_profiles": citation_format_profiles,
            "locator_items": locator_items,
        }
    )
    scripture_candidate_stage_file = (
        intermediate_dir / "scripture_candidate_stage.json"
    )
    scripture_candidate_stage = read_checkpointed_json(
        scripture_candidate_stage_file,
        stage="scripture_candidate_location",
        input_fingerprint=scripture_candidate_input_fingerprint,
    )
    if isinstance(scripture_candidate_stage, Mapping) and isinstance(
        scripture_candidate_stage.get("locator_items"), list
    ):
        locator_items = [
            dict(item)
            for item in scripture_candidate_stage["locator_items"]
            if isinstance(item, Mapping)
        ]
        scripture_evidence = dict(
            scripture_candidate_stage.get("evidence") or {}
        )
    else:
        persisted_items, persisted_evidence = enrich_locator_items_from_citation_db(
            locator_items,
            source_root=source_root,
            db_path=scripture_db_path,
            max_candidates=scripture_evidence_top_n,
        )
        if persisted_evidence.get("coverage_status") == "complete":
            locator_items = persisted_items
            unmatched_keys = {
                str(record.get("locator_key") or "")
                for record in persisted_evidence.get("items") or []
                if isinstance(record, Mapping)
                and int(record.get("candidate_count") or 0) == 0
            }
            fallback_evidence: Mapping[str, Any] | None = None
            if unmatched_keys:
                rescanned, fallback_evidence = add_scripture_evidence_candidates(
                    [
                        item
                        for item in locator_items
                        if str(item.get("locator_key") or "") in unmatched_keys
                    ],
                    source_root=source_root,
                    collection=collection,
                    config=ScriptureEvidenceConfig(
                        max_candidates=scripture_evidence_top_n,
                        snippet_chars=scripture_evidence_snippet_chars,
                    ),
                    citation_format_profiles=citation_format_profiles,
                )
                rescanned_by_key = {
                    str(item.get("locator_key") or ""): item for item in rescanned
                }
                locator_items = [
                    rescanned_by_key.get(
                        str(item.get("locator_key") or ""), item
                    )
                    for item in locator_items
                ]
            scripture_evidence = {
                **persisted_evidence,
                "citation_format_profiles": citation_format_profiles,
                "unmatched_locator_keys": sorted(unmatched_keys),
                "fallback": dict(fallback_evidence or {}),
                "table_repair_groups": list(
                    (fallback_evidence or {}).get("table_repair_groups") or []
                ),
            }
        else:
            locator_items, scripture_evidence = add_scripture_evidence_candidates(
                locator_items,
                source_root=source_root,
                collection=collection,
                config=ScriptureEvidenceConfig(
                    max_candidates=scripture_evidence_top_n,
                    snippet_chars=scripture_evidence_snippet_chars,
                ),
                citation_format_profiles=citation_format_profiles,
            )
        write_checkpointed_json(
            scripture_candidate_stage_file,
            {
                "schema_version": 1,
                "volume_id": volume_id,
                "locator_items": locator_items,
                "evidence": scripture_evidence,
            },
            stage="scripture_candidate_location",
            input_fingerprint=scripture_candidate_input_fingerprint,
            dependencies={
                "source_snapshot_fingerprint": source_snapshot_fingerprint,
                "scripture_db": scripture_db_signature,
            },
            summary={"item_count": len(locator_items)},
        )
    scripture_table_report = run_scripture_table_repairs(
        volume_id=volume_id,
        locator_items=locator_items,
        citation_format_profiles=citation_format_profiles,
        scripture_evidence=scripture_evidence,
        intermediate_dir=intermediate_dir,
        agent_runner=agent_runner,
    )
    scripture_table_report_file = intermediate_dir / "scripture_table_report.json"
    _write_json(scripture_table_report_file, scripture_table_report)
    scripture_evidence_file = intermediate_dir / "scripture_evidence.json"
    _write_json(scripture_evidence_file, scripture_evidence)

    attach_candidate_facsimile_hints(locator_items)
    locator_items = standardize_locator_items(
        [
            {
                **item,
                "source_snapshot_fingerprint": source_snapshot_fingerprint,
                "facsimile_inventory_fingerprint": facsimile_fingerprint,
            }
            for item in locator_items
        ]
    )
    locator_items_file = intermediate_dir / "locator_items.json"
    locator_items_input_fingerprint = stable_json_fingerprint(
        {
            "stage_contract": 2,
            "stage": "candidate_fusion",
            "semantic_fingerprint": _fingerprint(semantic),
            "source_snapshot_fingerprint": source_snapshot_fingerprint,
            "scripture_candidate_input_fingerprint": (
                scripture_candidate_input_fingerprint
            ),
            "scripture_table_report": scripture_table_report,
            "locator_items": locator_items,
        }
    )
    write_checkpointed_json(
        locator_items_file,
        {
            "schema_version": 2,
            "stage": "candidate_fusion",
            "volume_id": volume_id,
            "source_root": str(source_root.resolve()),
            "source_snapshot_fingerprint": source_snapshot_fingerprint,
            "item_count": len(locator_items),
            "items": locator_items,
        },
        stage="candidate_fusion",
        input_fingerprint=locator_items_input_fingerprint,
        dependencies={
            "source_snapshot_fingerprint": source_snapshot_fingerprint,
            "semantic_fingerprint": _fingerprint(semantic),
        },
        summary={"item_count": len(locator_items)},
    )

    deterministic_results, pending_locator_items = (
        build_deterministic_locator_results(locator_items)
    )
    locator_cache_dir = intermediate_dir / "locator_cache"
    cached_locator_results, pending_locator_items = _load_locator_result_cache(
        pending_locator_items,
        cache_dir=locator_cache_dir,
        source_root=source_root,
    )
    shards = shard_locator_items(
        pending_locator_items,
        shard_size=locator_chunk_size,
    )
    locator_dir = intermediate_dir / "locators"
    chunk_records: list[dict[str, Any]] = []
    pending_runs: list[tuple[dict[str, Any], Path, Path]] = []
    for shard in shards:
        shard_section_keys = {
            str(item.get("section_key") or "")
            for item in shard["items"]
            if isinstance(item.get("scripture_ref"), Mapping)
        }
        shard_payload = {
            **shard,
            **prompt_reference_bundle(),
            "locator_contract_version": LOCATOR_CONTRACT_VERSION,
            "volume_id": volume_id,
            "source_root": str(source_root),
            "citation_format_profiles": {
                key: citation_format_profiles[key]
                for key in sorted(shard_section_keys)
                if key in citation_format_profiles
            },
        }
        shard_payload["input_fingerprint"] = _fingerprint(shard_payload)
        shard_id = str(shard["shard_id"])
        input_file = locator_dir / f"{shard_id}_input.json"
        result_file = locator_dir / f"{shard_id}_result.json"
        _write_json(input_file, shard_payload)
        record = {
            "shard_id": shard_id,
            "input_file": str(input_file),
            "result_file": str(result_file),
            "input_fingerprint": shard_payload["input_fingerprint"],
            "locator_keys": [item["locator_key"] for item in shard["items"]],
        }
        chunk_records.append(record)
        if not _valid_existing_results(
            list(shard["items"]),
            result_file,
            input_fingerprint=str(shard_payload["input_fingerprint"]),
            source_root=source_root,
        ):
            pending_runs.append((shard, input_file, result_file))

    workplan = {
        "schema_version": 1,
        "stage": "locator",
        "volume_id": volume_id,
        "source_root": str(source_root),
        "semantic_fingerprint": _fingerprint(semantic),
        "scripture_evidence_file": str(scripture_evidence_file),
        "deterministic_text_evidence_file": str(
            deterministic_text_evidence_file
        ),
        "scripture_table_report_file": str(scripture_table_report_file),
        "chunk_size": locator_chunk_size,
        "item_count": len(locator_items),
        "deterministic_resolved_count": len(deterministic_results),
        "cached_locator_result_count": len(cached_locator_results),
        "agent_item_count": len(pending_locator_items),
        "source_snapshot_fingerprint": source_snapshot_fingerprint,
        "facsimile_inventory_fingerprint": facsimile_fingerprint,
        "locator_items_file": str(locator_items_file),
        "chunks": chunk_records,
    }
    workplan_file = intermediate_dir / "locator_workplan.json"
    _write_json(workplan_file, workplan)

    execution_errors: list[dict[str, Any]] = []

    def run_shard(task: tuple[dict[str, Any], Path, Path]) -> None:
        shard, input_file, result_file = task
        shard_id = str(shard["shard_id"])
        prompt = build_locator_prompt(
            volume_id=volume_id,
            source_root=source_root,
            shard_file=input_file,
            result_file=result_file,
        )
        agent_runner(prompt, result_file, f"locators/{shard_id}")

    if pending_runs:
        with ThreadPoolExecutor(max_workers=max(1, min(locator_workers, len(pending_runs)))) as pool:
            futures = {pool.submit(run_shard, task): task for task in pending_runs}
            for future in as_completed(futures):
                shard, _, result_file = futures[future]
                try:
                    future.result()
                except BaseException as exc:
                    execution_errors.append(
                        {
                            "shard_id": shard["shard_id"],
                            "result_file": str(result_file),
                            "error": str(exc) or repr(exc),
                        }
                    )

    locator_results: list[Any] = [
        *deterministic_results,
        *cached_locator_results,
    ]
    for record in chunk_records:
        result_file = Path(record["result_file"])
        if result_file.is_file():
            try:
                result_payload = _read_json(result_file)
                validate_locator_result_envelope(
                    result_payload,
                    expected_input_fingerprint=str(record["input_fingerprint"]),
                )
                locator_results.append(result_payload)
                shard_items_by_key = {
                    str(item.get("locator_key") or ""): item
                    for item in pending_locator_items
                }
                _store_locator_result_cache(
                    [
                        shard_items_by_key[key]
                        for key in record["locator_keys"]
                        if key in shard_items_by_key
                    ],
                    result_payload,
                    cache_dir=locator_cache_dir,
                    source_root=source_root,
                )
            except (OSError, json.JSONDecodeError, CompactPipelineError) as exc:
                execution_errors.append(
                    {
                        "shard_id": record["shard_id"],
                        "result_file": str(result_file),
                        "error": str(exc),
                    }
                )

    report = validate_locator_results(
        locator_items,
        locator_results,
        source_root=source_root,
    )
    report["execution_errors"] = execution_errors
    report_file = intermediate_dir / "locator_validation_report.json"
    _write_json(report_file, report)
    repaired = False
    final_results: Any = locator_results
    if report["status"] != "ok":
        repair_request = build_repair_request(locator_items, report)
        repair_request["volume_id"] = volume_id
        repair_request["source_root"] = str(source_root)
        repair_request.update(prompt_reference_bundle())
        repair_request["locator_contract_version"] = LOCATOR_CONTRACT_VERSION
        repair_request["execution_errors"] = execution_errors
        repair_request["request_fingerprint"] = _fingerprint(repair_request)
        repair_request_file = intermediate_dir / "repair_request.json"
        repair_result_file = intermediate_dir / "repair_result.json"
        _write_json(repair_request_file, repair_request)
        if not repair_request["items"]:
            raise CompactPipelineError(
                "locator execution failed without recoverable citation ownership"
            )
        repair_items = [
            dict(record["locator"])
            for record in repair_request["items"]
            if isinstance(record, Mapping)
            and isinstance(record.get("locator"), Mapping)
        ]
        if len(repair_items) <= locator_chunk_size:
            repair_prompt = build_repair_prompt(
                volume_id=volume_id,
                source_root=source_root,
                repair_request_file=repair_request_file,
                repair_result_file=repair_result_file,
            )
            if not _valid_existing_results(
                repair_items,
                repair_result_file,
                input_fingerprint=str(
                    repair_request["request_fingerprint"]
                ),
                source_root=source_root,
                post_repair=True,
            ):
                agent_runner(repair_prompt, repair_result_file, "repair")
            repair_results = _read_json(repair_result_file)
            validate_locator_result_envelope(
                repair_results,
                expected_input_fingerprint=str(
                    repair_request["request_fingerprint"]
                ),
            )
            _store_locator_result_cache(
                repair_items,
                repair_results,
                cache_dir=locator_cache_dir,
                source_root=source_root,
            )
        else:
            repair_payloads: list[Mapping[str, Any]] = []
            repair_dir = intermediate_dir / "repairs"
            common = {
                key: deepcopy(value)
                for key, value in repair_request.items()
                if key not in {"items", "item_count", "request_fingerprint"}
            }
            for repair_shard in shard_repair_request(
                repair_request,
                shard_size=locator_chunk_size,
            ):
                shard_request = {**common, **repair_shard}
                shard_request["request_fingerprint"] = _fingerprint(
                    shard_request
                )
                shard_id = str(shard_request["repair_shard_id"])
                shard_request_file = repair_dir / f"{shard_id}_request.json"
                shard_result_file = repair_dir / f"{shard_id}_result.json"
                _write_json(shard_request_file, shard_request)
                shard_items = [
                    dict(record["locator"])
                    for record in shard_request["items"]
                    if isinstance(record, Mapping)
                    and isinstance(record.get("locator"), Mapping)
                ]
                if not _valid_existing_results(
                    shard_items,
                    shard_result_file,
                    input_fingerprint=str(
                        shard_request["request_fingerprint"]
                    ),
                    source_root=source_root,
                    post_repair=True,
                ):
                    prompt = build_repair_prompt(
                        volume_id=volume_id,
                        source_root=source_root,
                        repair_request_file=shard_request_file,
                        repair_result_file=shard_result_file,
                    )
                    agent_runner(
                        prompt,
                        shard_result_file,
                        f"repairs/{shard_id}",
                    )
                shard_results = _read_json(shard_result_file)
                validate_locator_result_envelope(
                    shard_results,
                    expected_input_fingerprint=str(
                        shard_request["request_fingerprint"]
                    ),
                )
                _store_locator_result_cache(
                    shard_items,
                    shard_results,
                    cache_dir=locator_cache_dir,
                    source_root=source_root,
                )
                repair_payloads.append(shard_results)
            repaired_results = merge_repair_results([], repair_payloads)
            repair_results = {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                **prompt_reference_bundle(),
                "locator_contract_version": LOCATOR_CONTRACT_VERSION,
                "volume_id": volume_id,
                "input_fingerprint": repair_request[
                    "request_fingerprint"
                ],
                "results": repaired_results,
            }
            _write_json(repair_result_file, repair_results)
        final_results = merge_repair_results(locator_results, repair_results)
        repaired = True

    final_report = validate_locator_results(
        locator_items,
        final_results,
        post_repair=repaired,
        source_root=source_root,
    )
    if final_report["status"] != "ok":
        reasons = [
            str(item.get("reason") or "pending")
            for item in final_report["pending"][:5]
        ]
        raise CompactPipelineError(
            f"compact repair left {final_report['pending_count']} invalid locator "
            f"result(s): {reasons}"
        )
    payload = assemble_compact_payload(
        semantic,
        final_results,
        post_repair=repaired,
    )
    _write_json(output_file, payload)
    assembly_report = {
        "schema_version": 1,
        "status": "ok",
        "volume_id": volume_id,
        "semantic_fingerprint": _fingerprint(semantic),
        "locator_item_count": len(locator_items),
        "deterministic_resolved_count": len(deterministic_results),
        "cached_locator_result_count": len(cached_locator_results),
        "agent_locator_item_count": len(pending_locator_items),
        "locator_shard_count": len(shards),
        "repair_ran": repaired,
        "source_snapshot_fingerprint": source_snapshot_fingerprint,
        "facsimile_inventory_fingerprint": facsimile_fingerprint,
        "output_file": str(output_file),
        "output_fingerprint": _fingerprint(payload),
    }
    assembly_report_file = intermediate_dir / "assembly_report.json"
    _write_json(assembly_report_file, assembly_report)
    return {
        **assembly_report,
        "semantic_prompt_file": str(prompt_file),
        "semantic_input_file": str(semantic_input_file),
        "semantic_input_fingerprint": semantic_input_fingerprint,
        "semantic_manifest_file": str(manifest_file),
        "mechanical_analysis_file": str(mechanical_analysis_file),
        "mechanical_candidate_line_count": mechanical_analysis[
            "candidate_line_count"
        ],
        "semantic_payload_file": str(semantic_payload_file),
        "source_snapshot_file": str(source_snapshot_file),
        "locator_items_file": str(locator_items_file),
        "editorial_page_map_file": str(estimator_file),
        "scripture_evidence_file": str(scripture_evidence_file),
        "deterministic_text_evidence_file": str(
            deterministic_text_evidence_file
        ),
        "locator_workplan_file": str(workplan_file),
        "locator_validation_report_file": str(report_file),
        "assembly_report_file": str(assembly_report_file),
    }
