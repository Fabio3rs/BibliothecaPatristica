"""Named, independently resumable stages for alphabetical-index analysis."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping

from .alphabetical_analysis_db import (
    begin_stage,
    connect_analysis_db,
    finish_stage,
    init_analysis_schema,
    load_locator_items,
    load_locator_results,
    load_semantic_payload,
    mark_downstream_stale,
    replace_discovery,
    replace_locator_items,
    replace_semantic_payload,
    stable_fingerprint,
    stage_state,
    store_locator_results,
    upgrade_locator_contracts,
)
from .alphabetical_artifact_validation import (
    validate_discovery_manifest,
    validate_locator_result_envelope,
)
from .alphabetical_compact_driver import (
    AgentRunner,
    FACSIMILE_HINT_CONTRACT_VERSION,
    add_deterministic_text_candidates,
    attach_candidate_facsimile_hints,
    build_discovery_prompt,
    build_locator_prompt,
    build_semantic_input,
    build_semantic_prompt,
    facsimile_inventory_fingerprint,
    load_semantic_manifest,
    run_scripture_table_repairs,
)
from .alphabetical_compact_pipeline import (
    CompactPipelineError,
    assemble_compact_payload,
    build_deterministic_locator_results,
    build_locator_items,
    shard_locator_items,
    standardize_locator_items,
    validate_locator_results,
)
from .alphabetical_checkpoints import (
    file_sha256,
    read_checkpointed_json,
    stable_json_fingerprint,
    update_source_snapshot,
    write_checkpointed_json,
)
from .alphabetical_mechanical_analysis import build_mechanical_analysis
from .alphabetical_prompt_contract import (
    DISCOVERY_CONTRACT_VERSION,
    LOCATOR_CONTRACT_VERSION,
    prompt_reference_bundle,
)
from .editorial_page_estimator import estimate_editorial_pages
from tools.scripture.citation_index import (
    DEFAULT_CITATION_DB,
    enrich_locator_items_from_citation_db,
)
from tools.scripture.evidence_locator import (
    ScriptureEvidenceConfig,
    add_scripture_evidence_candidates,
    infer_citation_format_profiles,
)


SemanticValidator = Callable[[Path], None]
PayloadImporter = Callable[[Path], None]


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


def _file_signature(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path.resolve()), "exists": False}
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _content_signature(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path.resolve()), "exists": False}
    return {
        "path": str(path.resolve()),
        "exists": True,
        "size": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def _should_reuse(
    con: Any,
    *,
    volume_id: str,
    stage: str,
    fingerprint: str,
    force: bool,
    skip_fingerprint: bool = False,
) -> bool:
    if force:
        return False
    state = stage_state(con, volume_id, stage)
    if skip_fingerprint:
        return bool(state and state["status"] == "complete")
    return bool(
        state
        and state["status"] == "complete"
        and state["input_fingerprint"] == fingerprint
    )


def _ensure_agent_discovery(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    filtered_pages_file: Path,
    intermediate_dir: Path,
    agent_runner: AgentRunner,
    force: bool,
    skip_fingerprint: bool = False,
    phase_id: str = "extract/discovery",
) -> tuple[dict[str, Any], Path, str]:
    discovery_dir = intermediate_dir / "discovery"
    source_snapshot_file = intermediate_dir / "ocr_source_snapshot.json"
    source_snapshot = update_source_snapshot(source_snapshot_file, source_root)
    discovery_input_file = discovery_dir / "discovery_input.json"
    discovery_file = discovery_dir / "discovery_manifest.json"
    discovery_prompt_file = discovery_dir / "discovery_prompt.txt"
    discovery_input = {
        "stage_contract": DISCOVERY_CONTRACT_VERSION,
        "stage": "discovery",
        **prompt_reference_bundle(),
        "volume_id": volume_id,
        "collection": collection,
        "source_root": str(source_root.resolve()),
        "source_snapshot_fingerprint": source_snapshot[
            "source_snapshot_fingerprint"
        ],
        "prefilter": _content_signature(filtered_pages_file),
    }
    discovery_input_fingerprint = stable_fingerprint(discovery_input)
    _write_json(
        discovery_input_file,
        {
            **discovery_input,
            "input_fingerprint": discovery_input_fingerprint,
        },
    )
    discovery_prompt = build_discovery_prompt(
        volume_id=volume_id,
        collection=collection,
        source_root=source_root,
        prefilter_file=filtered_pages_file,
        discovery_file=discovery_file,
        input_fingerprint=discovery_input_fingerprint,
    )
    discovery_prompt_file.parent.mkdir(parents=True, exist_ok=True)
    discovery_prompt_file.write_text(discovery_prompt, encoding="utf-8")

    def load_checkpoint(
        *,
        expected_input_fingerprint: str = discovery_input_fingerprint,
    ) -> dict[str, Any]:
        discovery = validate_discovery_manifest(
            _read_json(discovery_file),
            source_root=source_root,
            expected_volume_id=volume_id,
            expected_input_fingerprint=expected_input_fingerprint,
        )
        return discovery

    discovery: dict[str, Any] | None = None
    checkpoint_input_fingerprint = discovery_input_fingerprint
    if discovery_file.is_file() and not force:
        try:
            if skip_fingerprint:
                raw_discovery = _read_json(discovery_file)
                reusable_fingerprint = str(
                    raw_discovery.get("input_fingerprint") or ""
                )
                if (
                    raw_discovery.get("status") == "complete"
                    and reusable_fingerprint
                ):
                    discovery = load_checkpoint(
                        expected_input_fingerprint=reusable_fingerprint,
                    )
                    checkpoint_input_fingerprint = reusable_fingerprint
                else:
                    discovery = load_checkpoint()
            else:
                discovery = load_checkpoint()
        except (OSError, ValueError, json.JSONDecodeError, CompactPipelineError):
            discovery = None
    if discovery is None:
        agent_runner(
            discovery_prompt,
            discovery_file,
            phase_id,
        )
        discovery = load_checkpoint()
        checkpoint_input_fingerprint = discovery_input_fingerprint
    expansion_round = 0
    while discovery["status"] == "needs_expansion" and expansion_round < 3:
        expansion_round += 1
        expansion_prompt = f"""{discovery_prompt}

DISCOVERY EXPANSION ROUND {expansion_round}
- Read the existing checkpoint at {discovery_file}.
- Execute every actionable item currently listed in expansion_requests by inspecting the
  requested neighbors or gaps under source_root.
- Preserve valid inspected_files and segments, merge newly inspected evidence, and rewrite the
  same manifest atomically with the same input_fingerprint.
- Remove fulfilled expansion_requests. If evidence still cannot be acquired, record the bounded
  uncertainty in unresolved; do not repeat an unchanged request.
- Emit status=complete only when expansion_requests is empty. Otherwise emit needs_expansion with
  narrower, actionable requests.
"""
        agent_runner(
            expansion_prompt,
            discovery_file,
            f"{phase_id}/expansion-{expansion_round:04d}",
        )
        discovery = load_checkpoint()
    if discovery["status"] != "complete":
        raise CompactPipelineError(
            "discovery remained needs_expansion after 3 bounded expansion rounds; "
            f"inspect checkpoint {discovery_file}"
        )
    return discovery, discovery_file, checkpoint_input_fingerprint


def discover_stage(
    *,
    analysis_db: Path,
    volume_id: str,
    collection: str,
    source_root: Path,
    filtered_pages: Mapping[str, Any],
    filtered_pages_file: Path | None = None,
    intermediate_dir: Path | None = None,
    agent_runner: AgentRunner | None = None,
    force: bool = False,
    skip_fingerprint: bool = False,
) -> dict[str, Any]:
    discovery_args = (
        filtered_pages_file,
        intermediate_dir,
        agent_runner,
    )
    if any(value is not None for value in discovery_args):
        if not all(value is not None for value in discovery_args):
            raise ValueError(
                "filtered_pages_file, intermediate_dir and agent_runner "
                "must be supplied together"
            )
    source_snapshot_fingerprint: str | None = None
    if intermediate_dir is not None:
        source_snapshot = update_source_snapshot(
            intermediate_dir / "ocr_source_snapshot.json",
            source_root,
        )
        source_snapshot_fingerprint = str(
            source_snapshot["source_snapshot_fingerprint"]
        )
    fingerprint = stable_fingerprint(
        {
            "stage_contract": 2,
            **prompt_reference_bundle(),
            "stage": "discover",
            "volume_id": volume_id,
            "collection": collection,
            "source_root": str(source_root.resolve()),
            "source_snapshot_fingerprint": source_snapshot_fingerprint,
            "filtered_pages": filtered_pages,
        }
    )
    with connect_analysis_db(analysis_db) as con:
        init_analysis_schema(con)
        reusable_discovery_manifest = (
            intermediate_dir is not None
            and (intermediate_dir / "discovery" / "discovery_manifest.json").is_file()
        )
        if _should_reuse(
            con,
            volume_id=volume_id,
            stage="discover",
            fingerprint=fingerprint,
            force=force,
            skip_fingerprint=skip_fingerprint,
        ):
            if not skip_fingerprint and all(value is not None for value in discovery_args):
                _ensure_agent_discovery(
                    volume_id=volume_id,
                    collection=collection,
                    source_root=source_root,
                    filtered_pages_file=filtered_pages_file,
                    intermediate_dir=intermediate_dir,
                    agent_runner=agent_runner,
                    force=False,
                    skip_fingerprint=False,
                    phase_id="discover/semantic",
                )
            elif skip_fingerprint and reusable_discovery_manifest:
                return {
                    "status": "skipped",
                    "stage": "discover",
                    "volume_id": volume_id,
                    "reason": "checkpoint_reused",
                    "input_fingerprint": fingerprint,
                }
            elif skip_fingerprint and not reusable_discovery_manifest:
                # We do not want to re-open the same expensive discovery work when
                # fingerprints differ, but we also need a manifest for downstream stages.
                # Continue to run this stage to regenerate the manifest once.
                pass
            else:
                return {
                    "status": "skipped",
                    "stage": "discover",
                    "volume_id": volume_id,
                    "reason": "checkpoint_reused",
                    "input_fingerprint": fingerprint,
                }

        from .alphabetical_analysis_db import ensure_volume

        ensure_volume(
            con,
            volume_id=volume_id,
            collection=collection,
            source_root=source_root,
        )
        con.commit()
        run_id = begin_stage(
            con,
            volume_id=volume_id,
            stage="discover",
            input_fingerprint=fingerprint,
        )
        try:
            agent_discovery: dict[str, Any] | None = None
            discovery_file: Path | None = None
            if all(value is not None for value in discovery_args):
                agent_discovery, discovery_file, _ = _ensure_agent_discovery(
                    volume_id=volume_id,
                    collection=collection,
                    source_root=source_root,
                    filtered_pages_file=filtered_pages_file,
                    intermediate_dir=intermediate_dir,
                    agent_runner=agent_runner,
                    force=force,
                    skip_fingerprint=skip_fingerprint,
                    phase_id="discover/semantic",
                )
            page_count = replace_discovery(
                con,
                volume_id=volume_id,
                collection=collection,
                source_root=source_root,
                filtered_pages=filtered_pages,
                discovery_manifest=agent_discovery,
            )
            summary = {
                "status": "complete",
                "stage": "discover",
                "volume_id": volume_id,
                "input_fingerprint": fingerprint,
                "page_count": page_count,
                "candidate_section_count": len(
                    filtered_pages.get("candidate_sections") or []
                ),
                "segment_count": len(
                    (agent_discovery or {}).get("segments") or []
                ),
                "discovery_manifest_file": (
                    str(discovery_file) if discovery_file else None
                ),
            }
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="discover",
                status="complete",
                summary=summary,
            )
            return summary
        except BaseException as exc:
            con.rollback()
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="discover",
                status="failed",
                error_text=str(exc) or repr(exc),
            )
            raise


def extract_stage(
    *,
    analysis_db: Path,
    volume_id: str,
    collection: str,
    source_root: Path,
    filtered_pages_file: Path,
    intermediate_dir: Path,
    agent_runner: AgentRunner,
    semantic_validator: SemanticValidator | None = None,
    force: bool = False,
    skip_fingerprint: bool = False,
) -> dict[str, Any]:
    discovery_file = intermediate_dir / "discovery" / "discovery_manifest.json"
    semantic_dir = intermediate_dir / "semantic"
    manifest_file = semantic_dir / "manifest.json"
    semantic_payload_file = intermediate_dir / "semantic_payload.json"
    semantic_input_file = intermediate_dir / "semantic_input.json"
    prompt_file = intermediate_dir / "semantic_prompt.txt"

    with connect_analysis_db(analysis_db) as con:
        init_analysis_schema(con)
        discovery, discovery_file, discovery_input_fingerprint = (
            _ensure_agent_discovery(
                volume_id=volume_id,
                collection=collection,
                source_root=source_root,
                filtered_pages_file=filtered_pages_file,
                intermediate_dir=intermediate_dir,
                agent_runner=agent_runner,
                force=force,
                skip_fingerprint=skip_fingerprint,
            )
        )
        mechanical_analysis_file = intermediate_dir / "mechanical_analysis.json"
        try:
            filtered_pages = _read_json(filtered_pages_file)
        except (OSError, json.JSONDecodeError):
            filtered_pages = {}
        if not isinstance(filtered_pages, Mapping):
            filtered_pages = {}
        mechanical_candidate_files = list(
            dict.fromkeys(
                [
                    str(value)
                    for value in filtered_pages.get("candidate_files") or []
                    if str(value).strip()
                ]
                + [
                    str(value)
                    for value in discovery.get("inspected_files") or []
                    if str(value).strip()
                ]
            )
        )
        mechanical_scope = {
            **dict(filtered_pages),
            "candidate_files": mechanical_candidate_files,
        }
        source_snapshot = _read_json(intermediate_dir / "ocr_source_snapshot.json")
        mechanical_input_fingerprint = stable_json_fingerprint(
            {
                "stage_contract": 3,
                "stage": "mechanical_semantic_analysis",
                "volume_id": volume_id,
                "collection": collection,
                "discovery_sha256": file_sha256(discovery_file),
                "source_snapshot_fingerprint": source_snapshot.get(
                    "source_snapshot_fingerprint"
                ),
                "candidate_files": mechanical_candidate_files,
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
                filtered_pages=mechanical_scope,
            )
            write_checkpointed_json(
                mechanical_analysis_file,
                mechanical_analysis,
                stage="mechanical_semantic_analysis",
                input_fingerprint=mechanical_input_fingerprint,
                dependencies={
                    "discovery_sha256": file_sha256(discovery_file),
                    "source_snapshot_fingerprint": source_snapshot.get(
                        "source_snapshot_fingerprint"
                    ),
                },
                summary={
                    "inspected_file_count": mechanical_analysis.get(
                        "inspected_file_count"
                    ),
                    "candidate_line_count": mechanical_analysis.get(
                        "candidate_line_count"
                    ),
                    "material_locator_count": mechanical_analysis.get(
                        "material_locator_count"
                    ),
                },
            )
        semantic_input = build_semantic_input(
            volume_id=volume_id,
            collection=collection,
            source_root=source_root,
            filtered_pages_file=filtered_pages_file,
            discovery_file=discovery_file,
            mechanical_analysis_file=mechanical_analysis_file,
            source_snapshot_file=intermediate_dir / "ocr_source_snapshot.json",
        )
        fingerprint = stable_fingerprint(
            {
                "stage_contract": 2,
                "stage": "extract",
                **semantic_input,
            }
        )
        semantic_input_fingerprint = stable_fingerprint(semantic_input)
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
            discovery_file=discovery_file,
            mechanical_analysis_file=mechanical_analysis_file,
        )
        semantic_prompt += """

ANALYSIS DATABASE HANDOFF
- Emit an entry once, owned by the source_span where its first recoverable line begins.
- The deterministic importer derives analysis_owner_file from that source_span and stores one
  logical entry plus its independently ordered material occurrences.
- Copy the exact discovery manifest path into manifest.discovery_file.
"""
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(semantic_prompt, encoding="utf-8")
        if _should_reuse(
            con,
            volume_id=volume_id,
            stage="extract",
            fingerprint=fingerprint,
            force=force,
            skip_fingerprint=skip_fingerprint,
        ):
            return {
                "status": "skipped",
                "stage": "extract",
                "volume_id": volume_id,
                "reason": "checkpoint_reused",
                "input_fingerprint": fingerprint,
            }
        from .alphabetical_analysis_db import ensure_volume

        ensure_volume(
            con,
            volume_id=volume_id,
            collection=collection,
            source_root=source_root,
        )
        con.commit()
        run_id = begin_stage(
            con,
            volume_id=volume_id,
            stage="extract",
            input_fingerprint=fingerprint,
        )
        try:
            expected_semantic_fingerprint = semantic_input_fingerprint

            def load_checkpoint() -> dict[str, Any]:
                payload = load_semantic_manifest(
                    manifest_file,
                    expected_volume_id=volume_id,
                    expected_collection=collection,
                    expected_source_root=source_root,
                    expected_input_fingerprint=expected_semantic_fingerprint,
                    expected_discovery_file=discovery_file,
                    expected_discovery_input_fingerprint=discovery_input_fingerprint,
                )
                _write_json(semantic_payload_file, payload)
                if semantic_validator:
                    semantic_validator(semantic_payload_file)
                return payload

            semantic: dict[str, Any] | None = None
            if manifest_file.is_file() and not force:
                try:
                    semantic = load_checkpoint()
                except (OSError, ValueError, json.JSONDecodeError, SystemExit):
                    semantic = None
            if semantic is None:
                agent_runner(semantic_prompt, manifest_file, "extract/semantic")
                try:
                    semantic = load_checkpoint()
                except (OSError, ValueError, json.JSONDecodeError, SystemExit) as exc:
                    validation_error = semantic_dir / "validation_error.txt"
                    validation_error.write_text(
                        str(exc) or repr(exc),
                        encoding="utf-8",
                    )
                    repair_prompt = (
                        semantic_prompt
                        + "\n\nSEMANTIC CHECKPOINT REPAIR\n"
                        + f"- deterministic validation error: {validation_error}\n"
                        + "- Preserve valid page-owned fragments and rewrite only invalid "
                        "artifacts before writing the manifest last.\n"
                    )
                    agent_runner(
                        repair_prompt,
                        manifest_file,
                        "extract/semantic_repair",
                    )
                    semantic = load_checkpoint()
            counts = replace_semantic_payload(con, payload=semantic)
            summary = {
                "status": "complete",
                "stage": "extract",
                "volume_id": volume_id,
                "input_fingerprint": fingerprint,
                **counts,
                "semantic_manifest_file": str(manifest_file),
                "semantic_payload_file": str(semantic_payload_file),
                "semantic_input_file": str(semantic_input_file),
                "mechanical_analysis_file": str(mechanical_analysis_file),
            }
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="extract",
                status="complete",
                summary=summary,
            )
            return summary
        except BaseException as exc:
            con.rollback()
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="extract",
                status="failed",
                error_text=str(exc) or repr(exc),
            )
            raise


def locate_stage(
    *,
    analysis_db: Path,
    scripture_db: Path = DEFAULT_CITATION_DB,
    volume_id: str,
    collection: str,
    source_root: Path,
    intermediate_dir: Path,
    agent_runner: AgentRunner,
    force: bool = False,
    skip_fingerprint: bool = False,
    max_candidates: int = 4,
) -> dict[str, Any]:
    source_snapshot_file = intermediate_dir / "ocr_source_snapshot.json"
    source_snapshot = update_source_snapshot(source_snapshot_file, source_root)
    source_snapshot_fingerprint = str(
        source_snapshot["source_snapshot_fingerprint"]
    )
    facsimile_fingerprint = facsimile_inventory_fingerprint(source_root)
    with connect_analysis_db(analysis_db) as con:
        init_analysis_schema(con)
        semantic = load_semantic_payload(con, volume_id)
        fingerprint = stable_fingerprint(
            {
                "stage_contract": 2,
                "stage": "locate",
                "volume_id": volume_id,
                "semantic": semantic,
                "source_snapshot_fingerprint": source_snapshot_fingerprint,
                "facsimile_hint_contract_version": (
                    FACSIMILE_HINT_CONTRACT_VERSION
                ),
                "facsimile_inventory_fingerprint": facsimile_fingerprint,
                "locator_contract_version": LOCATOR_CONTRACT_VERSION,
                "prompt_references": prompt_reference_bundle(),
                "scripture_db": _file_signature(scripture_db),
                "max_candidates": max_candidates,
            }
        )
        if _should_reuse(
            con,
            volume_id=volume_id,
            stage="locate",
            fingerprint=fingerprint,
            force=force,
            skip_fingerprint=skip_fingerprint,
        ):
            return {
                "status": "skipped",
                "stage": "locate",
                "volume_id": volume_id,
                "reason": "checkpoint_reused",
                "input_fingerprint": fingerprint,
            }
        run_id = begin_stage(
            con,
            volume_id=volume_id,
            stage="locate",
            input_fingerprint=fingerprint,
        )
        try:
            estimator_file = intermediate_dir / "editorial_page_map.json"
            if collection in {"PG", "PL"}:
                estimator = estimate_editorial_pages(
                    volume_id=volume_id,
                    collection=collection,
                    source_root=source_root,
                    window=4,
                )
                locator_items = build_locator_items(semantic, estimator)
            else:
                locator_items = build_locator_items(semantic)
                estimator = {
                    "schema_version": 1,
                    "volume_id": volume_id,
                    "estimator_status": "unsupported_collection",
                    "fallback": "index_target_locator",
                    "entries": [],
                }
            _write_json(estimator_file, estimator)
            locator_items, deterministic_text_evidence = (
                add_deterministic_text_candidates(
                    locator_items,
                    volume_id=volume_id,
                    source_root=source_root,
                )
            )
            deterministic_text_evidence_file = (
                intermediate_dir / "deterministic_text_locator.json"
            )
            _write_json(
                deterministic_text_evidence_file,
                deterministic_text_evidence,
            )
            profiles = infer_citation_format_profiles(
                semantic,
                collection=collection,
            )
            scripture_candidate_input_fingerprint = stable_json_fingerprint(
                {
                    "stage_contract": 1,
                    "stage": "scripture_candidate_location",
                    "volume_id": volume_id,
                    "collection": collection,
                    "source_snapshot_fingerprint": (
                        source_snapshot_fingerprint
                    ),
                    "scripture_db": _file_signature(scripture_db),
                    "max_candidates": max_candidates,
                    "citation_format_profiles": profiles,
                    "locator_items": locator_items,
                }
            )
            scripture_candidate_stage_file = (
                intermediate_dir / "scripture_candidate_stage.json"
            )
            scripture_candidate_stage = None
            if not force:
                scripture_candidate_stage = read_checkpointed_json(
                    scripture_candidate_stage_file,
                    stage="scripture_candidate_location",
                    input_fingerprint=(
                        scripture_candidate_input_fingerprint
                    ),
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
                persisted_items, persisted_evidence = (
                    enrich_locator_items_from_citation_db(
                        locator_items,
                        source_root=source_root,
                        db_path=scripture_db,
                        max_candidates=max_candidates,
                    )
                )
                locator_items = persisted_items
                unmatched_scripture = {
                    str(record.get("locator_key") or "")
                    for record in persisted_evidence.get("items") or []
                    if isinstance(record, Mapping)
                    and int(record.get("candidate_count") or 0) == 0
                }
                if persisted_evidence.get("coverage_status") != "complete":
                    locator_items, scripture_evidence = (
                        add_scripture_evidence_candidates(
                            locator_items,
                            source_root=source_root,
                            collection=collection,
                            config=ScriptureEvidenceConfig(
                                max_candidates=max_candidates
                            ),
                            citation_format_profiles=profiles,
                        )
                    )
                else:
                    fallback_evidence: Mapping[str, Any] | None = None
                    if unmatched_scripture:
                        unmatched_items = [
                            item
                            for item in locator_items
                            if str(item.get("locator_key") or "")
                            in unmatched_scripture
                        ]
                        rescanned, fallback_evidence = (
                            add_scripture_evidence_candidates(
                                unmatched_items,
                                source_root=source_root,
                                collection=collection,
                                config=ScriptureEvidenceConfig(
                                    max_candidates=max_candidates
                                ),
                                citation_format_profiles=profiles,
                            )
                        )
                        rescanned_by_key = {
                            str(item.get("locator_key") or ""): item
                            for item in rescanned
                        }
                        locator_items = [
                            rescanned_by_key.get(
                                str(item.get("locator_key") or ""),
                                item,
                            )
                            for item in locator_items
                        ]
                    scripture_evidence = {
                        **persisted_evidence,
                        "citation_format_profiles": profiles,
                        "unmatched_locator_keys": sorted(
                            unmatched_scripture
                        ),
                        "fallback": dict(fallback_evidence or {}),
                        "table_repair_groups": list(
                            (fallback_evidence or {}).get(
                                "table_repair_groups"
                            )
                            or []
                        ),
                    }
                write_checkpointed_json(
                    scripture_candidate_stage_file,
                    {
                        "schema_version": 1,
                        "volume_id": volume_id,
                        "locator_items": locator_items,
                        "evidence": scripture_evidence,
                    },
                    stage="scripture_candidate_location",
                    input_fingerprint=(
                        scripture_candidate_input_fingerprint
                    ),
                    dependencies={
                        "source_snapshot_fingerprint": (
                            source_snapshot_fingerprint
                        ),
                        "scripture_db": _file_signature(scripture_db),
                    },
                    summary={"item_count": len(locator_items)},
                )
            table_report = run_scripture_table_repairs(
                volume_id=volume_id,
                locator_items=locator_items,
                citation_format_profiles=profiles,
                scripture_evidence=scripture_evidence,
                intermediate_dir=intermediate_dir,
                agent_runner=agent_runner,
            )
            scripture_evidence_file = intermediate_dir / "scripture_evidence.json"
            table_report_file = intermediate_dir / "scripture_table_report.json"
            _write_json(scripture_evidence_file, scripture_evidence)
            _write_json(table_report_file, table_report)
            attach_candidate_facsimile_hints(locator_items)
            locator_items = standardize_locator_items(
                [
                    {
                        **item,
                        "source_snapshot_fingerprint": (
                            source_snapshot_fingerprint
                        ),
                        "facsimile_inventory_fingerprint": (
                            facsimile_fingerprint
                        ),
                    }
                    for item in locator_items
                ]
            )
            locator_items_file = intermediate_dir / "analysis_locator_items.json"
            _write_json(
                locator_items_file,
                {
                    "schema_version": 2,
                    "stage": "candidate_fusion",
                    "volume_id": volume_id,
                    "source_root": str(source_root.resolve()),
                    "source_snapshot_fingerprint": (
                        source_snapshot_fingerprint
                    ),
                    "item_count": len(locator_items),
                    "items": locator_items,
                },
            )
            deterministic_results, pending = build_deterministic_locator_results(
                locator_items
            )
            counts = replace_locator_items(
                con,
                volume_id=volume_id,
                locator_items=locator_items,
                deterministic_results=deterministic_results,
            )
            summary = {
                "status": "complete",
                "stage": "locate",
                "volume_id": volume_id,
                "input_fingerprint": fingerprint,
                **counts,
                "pending": len(pending),
                "scripture_candidate_stage_file": str(
                    scripture_candidate_stage_file
                ),
                "scripture_evidence_file": str(scripture_evidence_file),
                "scripture_table_report_file": str(table_report_file),
                "editorial_page_map_file": str(estimator_file),
                "deterministic_text_evidence_file": str(
                    deterministic_text_evidence_file
                ),
                "locator_items_file": str(locator_items_file),
                "source_snapshot_file": str(source_snapshot_file),
                "facsimile_inventory_fingerprint": facsimile_fingerprint,
            }
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="locate",
                status="complete",
                summary=summary,
            )
            return summary
        except BaseException as exc:
            con.rollback()
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="locate",
                status="failed",
                error_text=str(exc) or repr(exc),
            )
            raise


def verify_stage(
    *,
    analysis_db: Path,
    volume_id: str,
    source_root: Path,
    intermediate_dir: Path,
    agent_runner: AgentRunner,
    workers: int = 1,
    shard_size: int = 40,
    force: bool = False,
    skip_fingerprint: bool = False,
) -> dict[str, Any]:
    with connect_analysis_db(analysis_db) as con:
        init_analysis_schema(con)
        contract_upgrade = upgrade_locator_contracts(con, volume_id=volume_id)
        if contract_upgrade["updated"]:
            con.commit()
        facsimile_fingerprint = facsimile_inventory_fingerprint(source_root)

        def prepare_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            enriched = attach_candidate_facsimile_hints(items)
            return standardize_locator_items(
                [
                    {
                        **item,
                        "facsimile_inventory_fingerprint": (
                            facsimile_fingerprint
                        ),
                    }
                    for item in enriched
                ]
            )

        all_items = prepare_items(load_locator_items(con, volume_id))
        owned_items = prepare_items(
            load_locator_items(
                con,
                volume_id,
                statuses={"pending", "ambiguous"},
            )
        )
        fingerprint = stable_fingerprint(
            {
                "stage_contract": 2,
                "stage": "verify",
                "volume_id": volume_id,
                "owned_items": owned_items,
                "shard_size": shard_size,
                "facsimile_hint_contract_version": (
                    FACSIMILE_HINT_CONTRACT_VERSION
                ),
                "facsimile_inventory_fingerprint": facsimile_fingerprint,
                "locator_contract_version": LOCATOR_CONTRACT_VERSION,
                "prompt_references": prompt_reference_bundle(),
            }
        )
        if _should_reuse(
            con,
            volume_id=volume_id,
            stage="verify",
            fingerprint=fingerprint,
            force=force,
            skip_fingerprint=skip_fingerprint,
        ):
            return {
                "status": "skipped",
                "stage": "verify",
                "volume_id": volume_id,
                "reason": "checkpoint_reused",
                "input_fingerprint": fingerprint,
            }
        run_id = begin_stage(
            con,
            volume_id=volume_id,
            stage="verify",
            input_fingerprint=fingerprint,
        )
        try:
            shards = shard_locator_items(owned_items, shard_size=shard_size)
            locator_dir = intermediate_dir / "analysis_locators"
            tasks: list[tuple[dict[str, Any], Path, Path]] = []
            for shard in shards:
                payload = {
                    **shard,
                    **prompt_reference_bundle(),
                    "locator_contract_version": LOCATOR_CONTRACT_VERSION,
                    "volume_id": volume_id,
                    "source_root": str(source_root),
                }
                payload["input_fingerprint"] = stable_fingerprint(payload)
                input_file = locator_dir / f"{shard['shard_id']}_input.json"
                result_file = locator_dir / f"{shard['shard_id']}_result.json"
                _write_json(input_file, payload)
                tasks.append((payload, input_file, result_file))

            def run_task(task: tuple[dict[str, Any], Path, Path]) -> dict[str, Any]:
                shard, input_file, result_file = task
                prompt = build_locator_prompt(
                    volume_id=volume_id,
                    source_root=source_root,
                    shard_file=input_file,
                    result_file=result_file,
                )
                reusable = False
                if result_file.is_file() and not force:
                    try:
                        existing = _read_json(result_file)
                        validate_locator_result_envelope(
                            existing,
                            expected_input_fingerprint=str(
                                shard["input_fingerprint"]
                            ),
                        )
                        reusable = True
                    except (
                        OSError,
                        json.JSONDecodeError,
                        CompactPipelineError,
                    ):
                        reusable = False
                if not reusable:
                    agent_runner(
                        prompt,
                        result_file,
                        f"verify/{shard['shard_id']}",
                    )
                result = _read_json(result_file)
                validate_locator_result_envelope(
                    result,
                    expected_input_fingerprint=str(
                        shard["input_fingerprint"]
                    ),
                )
                report = validate_locator_results(
                    shard["items"],
                    [result],
                    source_root=source_root,
                )
                if report["status"] != "ok":
                    raise CompactPipelineError(
                        f"invalid verify shard {shard['shard_id']}: "
                        f"{report['pending'][:3]}"
                    )
                return result

            payloads: list[dict[str, Any]] = []
            if tasks:
                with ThreadPoolExecutor(
                    max_workers=max(1, min(workers, len(tasks)))
                ) as pool:
                    futures = {pool.submit(run_task, task): task for task in tasks}
                    for future in as_completed(futures):
                        payloads.append(future.result())
            results = [
                dict(result)
                for payload in payloads
                for result in payload.get("results") or []
                if isinstance(result, Mapping)
            ]
            stored = store_locator_results(
                con,
                volume_id=volume_id,
                results=results,
            )
            mark_downstream_stale(
                con,
                volume_id=volume_id,
                after_stage="verify",
            )
            con.commit()
            final_results = load_locator_results(con, volume_id)
            report = validate_locator_results(
                all_items,
                final_results,
                post_repair=True,
                source_root=source_root,
            )
            if report["status"] != "ok":
                raise CompactPipelineError(
                    f"verify left {report['pending_count']} invalid locator result(s)"
                )
            summary = {
                "status": "complete",
                "stage": "verify",
                "volume_id": volume_id,
                "input_fingerprint": fingerprint,
                "owned_item_count": len(owned_items),
                "shard_count": len(shards),
                "stored_result_count": stored,
                "contract_upgrade": contract_upgrade,
                "facsimile_inventory_fingerprint": facsimile_fingerprint,
            }
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="verify",
                status="complete",
                summary=summary,
            )
            return summary
        except BaseException as exc:
            con.rollback()
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="verify",
                status="failed",
                error_text=str(exc) or repr(exc),
            )
            raise


def assemble_stage(
    *,
    analysis_db: Path,
    volume_id: str,
    source_root: Path,
    output_file: Path,
    semantic_validator: SemanticValidator | None = None,
    payload_importer: PayloadImporter | None = None,
    force: bool = False,
    skip_fingerprint: bool = False,
) -> dict[str, Any]:
    with connect_analysis_db(analysis_db) as con:
        init_analysis_schema(con)
        semantic = load_semantic_payload(con, volume_id)
        locator_items = load_locator_items(con, volume_id)
        locator_results = load_locator_results(con, volume_id)
        fingerprint = stable_fingerprint(
            {
                "stage_contract": 1,
                "stage": "assemble",
                "semantic": semantic,
                "locator_results": locator_results,
            }
        )
        if _should_reuse(
            con,
            volume_id=volume_id,
            stage="assemble",
            fingerprint=fingerprint,
            force=force,
            skip_fingerprint=skip_fingerprint,
        ) and output_file.is_file():
            return {
                "status": "skipped",
                "stage": "assemble",
                "volume_id": volume_id,
                "reason": "checkpoint_reused",
                "input_fingerprint": fingerprint,
                "output_file": str(output_file),
            }
        run_id = begin_stage(
            con,
            volume_id=volume_id,
            stage="assemble",
            input_fingerprint=fingerprint,
        )
        try:
            report = validate_locator_results(
                locator_items,
                locator_results,
                post_repair=True,
                source_root=source_root,
            )
            if report["status"] != "ok":
                raise CompactPipelineError(
                    f"assemble requires terminal locators; "
                    f"{report['pending_count']} remain invalid"
                )
            payload = assemble_compact_payload(
                semantic,
                locator_results,
                post_repair=True,
            )
            _write_json(output_file, payload)
            if semantic_validator:
                semantic_validator(output_file)
            if payload_importer:
                payload_importer(output_file)
            summary = {
                "status": "complete",
                "stage": "assemble",
                "volume_id": volume_id,
                "input_fingerprint": fingerprint,
                "output_file": str(output_file),
                "output_fingerprint": stable_fingerprint(payload),
                "locator_count": len(locator_results),
            }
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="assemble",
                status="complete",
                summary=summary,
            )
            return summary
        except BaseException as exc:
            con.rollback()
            finish_stage(
                con,
                run_id=run_id,
                volume_id=volume_id,
                stage="assemble",
                status="failed",
                error_text=str(exc) or repr(exc),
            )
            raise


__all__ = [
    "assemble_stage",
    "discover_stage",
    "extract_stage",
    "locate_stage",
    "verify_stage",
]
