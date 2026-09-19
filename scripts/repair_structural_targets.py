#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.indexing.structural_target_repair import (
    MINIMUM_EDITORIAL_PAGE_CONFIDENCE,
    apply_manifest_volume,
    build_repair_manifest,
    classify_segments,
    decision_algorithm_sha256,
    payload_sha256,
    validate_repaired_payload,
)
from tools.indexing.index_operation_lock import index_operation_lock


DEFAULT_PAYLOAD_DIR = PROJECT_ROOT / "data" / "index_payloads"
IMPORT_SCRIPT = PROJECT_ROOT / "scripts" / "import_index_json.py"
LOCK_NAME = ".patristic-index-write.lock"
STATE_DIR_NAME = ".structural-target-repair"


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with source.open("rb") as input_handle, os.fdopen(descriptor, "wb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def sqlite_snapshot(source_path: Path, destination_path: Path) -> str:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.unlink(missing_ok=True)
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as destination:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"SQLite snapshot integrity check failed: {integrity}")
        violations = destination.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"SQLite snapshot foreign-key check failed: {violations[:10]}")
    with destination_path.open("rb") as handle:
        os.fsync(handle.fileno())
    fsync_directory(destination_path.parent)
    return sha256_file(destination_path)


def sqlite_promote(
    source_path: Path,
    destination_path: Path,
    *,
    allow_corrupt_destination: bool = False,
) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.parent / (
        f".{destination_path.name}.promote-{uuid.uuid4().hex}.sqlite"
    )
    destination_mode = destination_path.stat().st_mode & 0o777 if destination_path.exists() else 0o644
    try:
        sqlite_snapshot(source_path, temporary)
        os.chmod(temporary, destination_mode)
        try:
            with sqlite3.connect(destination_path) as current:
                checkpoint = current.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint and int(checkpoint[0]) != 0:
                    raise RuntimeError(
                        f"cannot promote SQLite while WAL readers/writers are busy: {checkpoint}"
                    )
        except sqlite3.DatabaseError as exc:
            corrupt_destination = any(
                marker in str(exc).casefold()
                for marker in ("not a database", "file is encrypted")
            )
            if not allow_corrupt_destination or not corrupt_destination:
                raise
        os.replace(temporary, destination_path)
        for suffix in ("-wal", "-shm", "-journal"):
            destination_path.with_name(destination_path.name + suffix).unlink(missing_ok=True)
        fsync_directory(destination_path.parent)
        with sqlite3.connect(destination_path) as promoted:
            promoted.execute("PRAGMA synchronous=FULL")
            integrity = promoted.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError(f"promoted SQLite integrity check failed: {integrity}")
            violations = promoted.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"promoted SQLite foreign-key check failed: {violations[:10]}")
        with destination_path.open("rb") as handle:
            os.fsync(handle.fileno())
    finally:
        temporary.unlink(missing_ok=True)
    fsync_directory(destination_path.parent)


def sqlite_logical_sha(path: Path, state_dir: Path) -> str:
    probe = state_dir / f"db-fingerprint-{uuid.uuid4().hex}.sqlite"
    try:
        return sqlite_snapshot(path, probe)
    finally:
        probe.unlink(missing_ok=True)
        fsync_directory(state_dir)


class TerminationGuard:
    def __init__(self) -> None:
        self.requested_signal: int | None = None
        self.previous: dict[int, Any] = {}

    def __enter__(self) -> "TerminationGuard":
        for candidate in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self.previous[candidate] = signal.getsignal(candidate)
            signal.signal(candidate, self._request_stop)
        return self

    def _request_stop(self, signum: int, _frame: Any) -> None:
        self.requested_signal = signum

    def checkpoint(self) -> None:
        if self.requested_signal is not None:
            raise InterruptedError(
                f"termination signal {self.requested_signal} received at a safe checkpoint"
            )

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        for candidate, previous in self.previous.items():
            signal.signal(candidate, previous)


def transaction_paths(state_dir: Path) -> tuple[Path, Path]:
    return state_dir / "active_transaction.json", state_dir / "last_transaction.json"


def persist_transaction_state(active_path: Path, state: dict[str, Any]) -> None:
    state["sequence"] = int(state.get("sequence") or 0) + 1
    write_json_atomic(active_path, state)
    backup_dir = Path(state["backup_dir"])
    write_json_atomic(backup_dir / "transaction_state.json", state)


def finalize_transaction_state(active_path: Path, state: dict[str, Any]) -> None:
    _, last_path = transaction_paths(active_path.parent)
    write_json_atomic(last_path, state)
    active_path.unlink(missing_ok=True)
    fsync_directory(active_path.parent)


def _validated_transaction_files(
    state: dict[str, Any], payload_dir: Path
) -> list[tuple[dict[str, Any], Path, Path]]:
    validated: list[tuple[dict[str, Any], Path, Path]] = []
    backup_dir = Path(str(state.get("backup_dir") or "")).resolve()
    if not backup_dir.is_dir():
        raise RuntimeError(f"transaction backup directory is missing: {backup_dir}")
    for item in state.get("files") or []:
        target = Path(str(item.get("target") or "")).resolve()
        backup = Path(str(item.get("backup") or "")).resolve()
        try:
            target.relative_to(payload_dir.resolve())
            backup.relative_to(backup_dir)
        except ValueError as exc:
            raise RuntimeError("transaction journal contains an unsafe file path") from exc
        if not backup.is_file() or sha256_file(backup) != item.get("before_sha256"):
            raise RuntimeError(f"transaction backup hash mismatch: {backup}")
        validated.append((item, target, backup))
    return validated


def recover_interrupted_transaction(state_dir: Path, payload_dir: Path) -> dict[str, Any] | None:
    active_path, _ = transaction_paths(state_dir)
    if not active_path.is_file():
        return None
    state = json.loads(active_path.read_text(encoding="utf-8"))
    if state.get("operation") != "deterministic_structural_target_repair":
        raise RuntimeError(f"unknown active transaction journal: {active_path}")
    files = _validated_transaction_files(state, payload_dir)
    phase = str(state.get("phase") or "")
    committed = phase in {"COMMITTED", "FINALIZED"}

    for item, target, _ in files:
        if not target.is_file():
            raise RuntimeError(f"transaction target is missing during recovery: {target}")
        current = sha256_file(target)
        allowed = {item.get("before_sha256"), item.get("after_sha256")}
        if current not in allowed:
            raise RuntimeError(
                f"refusing recovery because payload has an unknown external state: {target}"
            )

    database = state.get("database")
    if isinstance(database, dict):
        target_db = Path(database["target"]).resolve()
        backup_db = Path(database["backup"]).resolve()
        if not backup_db.is_file() or sha256_file(backup_db) != database.get("before_sha256"):
            raise RuntimeError(f"transaction database backup hash mismatch: {backup_db}")
        mutation_may_be_incomplete = phase in {"INSTALLING_DATABASE", "ROLLING_BACK"}
        try:
            current_db_sha = sqlite_logical_sha(target_db, state_dir)
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            if not mutation_may_be_incomplete:
                raise
            current_db_sha = None
        allowed_db = {database.get("before_sha256"), database.get("after_sha256")}
        if current_db_sha not in allowed_db and not mutation_may_be_incomplete:
            raise RuntimeError(
                f"refusing recovery because SQLite has an unknown external state: {target_db}"
            )

    if committed:
        if any(sha256_file(target) != item.get("after_sha256") for item, target, _ in files):
            raise RuntimeError("committed transaction does not contain all expected payload hashes")
        if isinstance(database, dict):
            current_db_sha = sqlite_logical_sha(Path(database["target"]), state_dir)
            if current_db_sha != database.get("after_sha256"):
                raise RuntimeError("committed transaction does not contain the expected SQLite state")
        applied_report = Path(str(state.get("applied_report") or "")).resolve()
        applied_summary = state.get("applied_summary")
        if not isinstance(applied_summary, dict) or not str(applied_report):
            raise RuntimeError("committed transaction lacks its applied-report recovery data")
        if applied_report.is_file():
            current_report = json.loads(applied_report.read_text(encoding="utf-8"))
            if current_report != applied_summary:
                raise RuntimeError(f"existing applied report differs from journal: {applied_report}")
        else:
            write_json_atomic(applied_report, applied_summary)
        state["phase"] = "FINALIZED"
        state["recovery"] = "completed_forward"
        persist_transaction_state(active_path, state)
        finalize_transaction_state(active_path, state)
        return state

    state["phase"] = "ROLLING_BACK"
    persist_transaction_state(active_path, state)
    for item, target, backup in files:
        copy_file_atomic(backup, target)
        if sha256_file(target) != item.get("before_sha256"):
            raise RuntimeError(f"payload rollback verification failed: {target}")
    if isinstance(database, dict):
        sqlite_promote(
            Path(database["backup"]),
            Path(database["target"]),
            allow_corrupt_destination=True,
        )
        restored_db_sha = sqlite_logical_sha(Path(database["target"]), state_dir)
        if restored_db_sha != database.get("before_sha256"):
            raise RuntimeError("SQLite rollback verification failed")
    state["phase"] = "ROLLED_BACK"
    state["recovery"] = "restored_before_state"
    persist_transaction_state(active_path, state)
    finalize_transaction_state(active_path, state)
    return state


def default_lock_path(payload_dir: Path) -> Path:
    return payload_dir.resolve().parent / LOCK_NAME


def default_state_dir(payload_dir: Path) -> Path:
    return payload_dir.resolve().parent / STATE_DIR_NAME


def test_failpoint(name: str) -> None:
    if os.getenv("STRUCTURAL_TARGET_REPAIR_FAILPOINT") == name:
        raise RuntimeError(f"test failpoint reached: {name}")


def selected_payloads(payload_dir: Path, volumes: list[str], all_volumes: bool) -> list[Path]:
    if not volumes and not all_volumes:
        raise SystemExit("Select at least one --volume or pass --all-volumes explicitly.")
    requested = {volume.upper() for volume in volumes}
    paths = sorted(payload_dir.resolve().glob("*_indices.json"))
    if requested:
        paths = [path for path in paths if path.name.removesuffix("_indices.json").upper() in requested]
    if not paths:
        raise SystemExit("No matching general-index payloads found.")
    return paths


def load_reviewed_overrides(path: Path | None) -> tuple[list[dict[str, Any]], str | None]:
    if path is None:
        return [], None
    resolved = path.resolve()
    document = json.loads(resolved.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or not isinstance(document.get("overrides"), list):
        raise SystemExit(f"invalid reviewed-overrides document: {resolved}")
    overrides = document["overrides"]
    if not all(isinstance(item, dict) for item in overrides):
        raise SystemExit(f"reviewed overrides must all be objects: {resolved}")
    return overrides, sha256_file(resolved)


def _build_manifest_worker(
    payload_path: Path,
    reviewed_overrides: list[dict[str, Any]],
    decision_config: dict[str, float],
) -> dict[str, Any]:
    """Build one volume in a long-lived corpus-level worker.

    Per-volume OCR pools are disabled here.  Parallelizing volumes keeps the
    requested process budget bounded and avoids creating up to ``workers`` new
    processes for every one of hundreds of volumes.
    """
    return build_repair_manifest(
        payload_path,
        workers=1,
        reviewed_overrides=reviewed_overrides,
        minimum_sequence_coverage=decision_config["minimum_sequence_coverage"],
        minimum_title_similarity=decision_config["minimum_title_similarity"],
        minimum_unique_similarity=decision_config["minimum_unique_similarity"],
        minimum_unique_margin=decision_config["minimum_unique_margin"],
    )


def _prefers_intra_volume_pool(payload_path: Path) -> bool:
    """Mirror the locator's pool threshold without retaining the payload."""
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    segments, _ = classify_segments(payload)
    eligible_count = sum(len(segment.entries) for segment in segments)
    return eligible_count >= 100 and len(segments) > 1


def _run_dry_run_locked(args: argparse.Namespace) -> dict[str, Any]:
    payloads = selected_payloads(args.payload_dir, args.volume, args.all_volumes)
    reviewed_overrides, reviewed_overrides_sha256 = load_reviewed_overrides(
        getattr(args, "reviewed_overrides", None)
    )
    volumes: list[dict[str, Any]] = []
    report_path = args.report.resolve()
    parts_dir = report_path.with_name(report_path.name + ".parts")
    resume = bool(getattr(args, "resume", False))
    if resume:
        if not parts_dir.is_dir():
            raise SystemExit(f"resume parts directory does not exist: {parts_dir}")
    else:
        parts_dir.mkdir(parents=True, exist_ok=False)
        fsync_directory(parts_dir.parent)
    algorithm_sha = decision_algorithm_sha256()
    expected_decision_config = {
        "minimum_sequence_coverage": args.minimum_sequence_coverage,
        "minimum_title_similarity": args.minimum_title_similarity,
        "minimum_unique_similarity": args.minimum_unique_similarity,
        "minimum_unique_margin": args.minimum_unique_margin,
        "minimum_editorial_page_confidence": MINIMUM_EDITORIAL_PAGE_CONFIDENCE,
    }

    def build_summary(*, complete: bool) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation": "deterministic_structural_target_repair",
            "mode": "dry_run",
            "status": "complete" if complete else "in_progress",
            "config": {
                "minimum_sequence_coverage": args.minimum_sequence_coverage,
                "minimum_title_similarity": args.minimum_title_similarity,
                "minimum_unique_similarity": args.minimum_unique_similarity,
                "minimum_unique_margin": args.minimum_unique_margin,
                "scan_workers": args.workers,
                "reviewed_overrides_file": (
                    str(args.reviewed_overrides.resolve()) if args.reviewed_overrides else None
                ),
                "reviewed_overrides_sha256": reviewed_overrides_sha256,
            },
            "volume_count_requested": len(payloads),
            "volume_count": len(volumes),
            "apply_ready": complete and all(item.get("apply_ready") for item in volumes),
            "counts": {
                key: sum(int((item.get("counts") or {}).get(key) or 0) for item in volumes)
                for key in (
                    "eligible",
                    "resolved",
                    "ambiguous",
                    "unresolved",
                    "conflict",
                    "already_canonical",
                    "preserved_existing",
                    "patches",
                )
            },
            "volumes": volumes,
        }

    completed_by_order: dict[int, dict[str, Any]] = {}
    pending: list[tuple[int, Path, Path, list[dict[str, Any]], bool]] = []

    def checkpoint(order: int, part_path: Path, part: dict[str, Any]) -> None:
        nonlocal volumes
        write_json_atomic(part_path, part)
        completed_by_order[order] = part
        volumes = [completed_by_order[index] for index in sorted(completed_by_order)]
        progress = build_summary(complete=False)
        progress["completed_volume_ids"] = [item.get("volume_id") for item in volumes]
        progress["parts_dir"] = str(parts_dir)
        progress["volumes"] = []
        write_json_atomic(report_path, progress)

    for order, payload_path in enumerate(payloads, start=1):
        volume_id = payload_path.name.removesuffix("_indices.json")
        part_path = parts_dir / f"{order:04d}-{volume_id}.json"
        if resume and part_path.is_file():
            part = json.loads(part_path.read_text(encoding="utf-8"))
            expected_overrides = [
                item
                for item in reviewed_overrides
                if str(item.get("volume_id") or "") == volume_id
            ]
            if (
                part.get("volume_id") != volume_id
                or Path(str(part.get("payload_file") or "")).resolve() != payload_path.resolve()
                or part.get("input_sha256") != payload_sha256(payload_path)
                or part.get("algorithm_sha256") != algorithm_sha
                or part.get("decision_config") != expected_decision_config
                or part.get("reviewed_overrides") != expected_overrides
            ):
                raise SystemExit(f"resume checkpoint no longer matches current input/code: {part_path}")
            print(f"[{order}/{len(payloads)}] resume {payload_path.name}", flush=True)
            completed_by_order[order] = part
        else:
            selected_overrides = [
                item
                for item in reviewed_overrides
                if str(item.get("volume_id") or "") == volume_id
            ]
            pending.append(
                (
                    order,
                    payload_path,
                    part_path,
                    selected_overrides,
                    args.workers > 1 and _prefers_intra_volume_pool(payload_path),
                )
            )

    volumes = [completed_by_order[index] for index in sorted(completed_by_order)]
    intra_volume_tasks = [task for task in pending if task[4]]
    volume_pool_tasks = [task for task in pending if not task[4]]
    completed_pending = 0

    for order, payload_path, part_path, selected_overrides, _ in intra_volume_tasks:
        print(
            f"[{completed_pending + 1}/{len(pending)}] analyze {payload_path.name} "
            f"with intra-volume pool",
            flush=True,
        )
        part = build_repair_manifest(
            payload_path,
            workers=args.workers,
            reviewed_overrides=selected_overrides,
            minimum_sequence_coverage=args.minimum_sequence_coverage,
            minimum_title_similarity=args.minimum_title_similarity,
            minimum_unique_similarity=args.minimum_unique_similarity,
            minimum_unique_margin=args.minimum_unique_margin,
        )
        checkpoint(order, part_path, part)
        completed_pending += 1

    if volume_pool_tasks and args.workers > 1 and len(volume_pool_tasks) > 1:
        print(
            f"[PARALLEL] analyze {len(volume_pool_tasks)} remaining volumes with "
            f"{min(args.workers, len(volume_pool_tasks))} workers",
            flush=True,
        )
        worker_config = {
            key: float(value)
            for key, value in expected_decision_config.items()
            if key != "minimum_editorial_page_confidence"
        }
        with ProcessPoolExecutor(max_workers=min(args.workers, len(pending))) as executor:
            future_tasks = {
                executor.submit(
                    _build_manifest_worker,
                    payload_path,
                    selected_overrides,
                    worker_config,
                ): (order, payload_path, part_path)
                for order, payload_path, part_path, selected_overrides, _ in volume_pool_tasks
            }
            for future in as_completed(future_tasks):
                order, payload_path, part_path = future_tasks[future]
                part = future.result()
                checkpoint(order, part_path, part)
                completed_pending += 1
                print(
                    f"[{completed_pending}/{len(pending)}] done {payload_path.name}",
                    flush=True,
                )
    else:
        for order, payload_path, part_path, selected_overrides, _ in volume_pool_tasks:
            print(
                f"[{completed_pending + 1}/{len(pending)}] analyze {payload_path.name}",
                flush=True,
            )
            part = build_repair_manifest(
                payload_path,
                workers=args.workers,
                reviewed_overrides=selected_overrides,
                minimum_sequence_coverage=args.minimum_sequence_coverage,
                minimum_title_similarity=args.minimum_title_similarity,
                minimum_unique_similarity=args.minimum_unique_similarity,
                minimum_unique_margin=args.minimum_unique_margin,
            )
            checkpoint(order, part_path, part)
            completed_pending += 1

    volumes = [completed_by_order[index] for index in range(1, len(payloads) + 1)]
    summary = build_summary(complete=True)
    summary["parts_dir"] = str(parts_dir)
    write_json_atomic(report_path, summary)
    return summary


def run_dry_run(args: argparse.Namespace) -> dict[str, Any]:
    lock_path = Path(getattr(args, "lock_file", None) or default_lock_path(args.payload_dir))
    state_dir = Path(getattr(args, "state_dir", None) or default_state_dir(args.payload_dir))
    active_path, _ = transaction_paths(state_dir.resolve())
    timeout = float(getattr(args, "lock_timeout", 0.0) or 0.0)
    with index_operation_lock(
        lock_path,
        exclusive=False,
        timeout=timeout,
        active_transaction_path=active_path,
    ):
        return _run_dry_run_locked(args)


def reviewed_resolved_only(item: dict[str, Any]) -> dict[str, Any]:
    selected = dict(item)
    selected["patches"] = [
        patch
        for patch in item.get("patches") or []
        if (patch.get("physical_target_evidence") or {}).get("method") == "reviewed_override"
        and (patch.get("physical_target_evidence") or {}).get("status") == "resolved"
        and patch.get("after_target_file") is not None
    ]
    return selected


def _run_apply_locked(args: argparse.Namespace, state_dir: Path) -> dict[str, Any]:
    if args.backup_dir is None:
        raise SystemExit("--apply-report requires --backup-dir.")
    report_path = args.apply_report.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("operation") != "deterministic_structural_target_repair" or report.get("mode") != "dry_run":
        raise SystemExit("The apply input is not a structural-target dry-run report.")
    if report.get("status", "complete") != "complete":
        raise SystemExit("The dry-run report is incomplete and cannot be applied.")
    only_reviewed_resolved = bool(getattr(args, "only_reviewed_resolved", False))
    if not report.get("apply_ready"):
        if not only_reviewed_resolved:
            raise SystemExit("Dry-run report contains conflicts and is not apply-ready.")
        invalid_volumes = [
            str(item.get("volume_id") or "")
            for item in report.get("volumes") or []
            if item.get("semantic_validation_errors")
        ]
        if invalid_volumes:
            raise SystemExit(
                "Reviewed-only apply cannot bypass semantic validation errors: "
                + ", ".join(invalid_volumes)
            )

    config = report.get("config") or {}
    for key, default in (
        ("minimum_sequence_coverage", 0.8),
        ("minimum_title_similarity", 0.58),
        ("minimum_unique_similarity", 0.82),
        ("minimum_unique_margin", 0.15),
    ):
        value = float(config.get(key, default))
        if not 0.0 <= value <= 1.0:
            raise SystemExit(f"invalid dry-run config {key}: {value}")
    payload_dir = args.payload_dir.resolve()
    prepared: list[tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    seen_paths: set[Path] = set()
    seen_volumes: set[str] = set()
    for item in report.get("volumes") or []:
        manifest_path = Path(str(item.get("payload_file") or "")).resolve()
        try:
            manifest_path.relative_to(payload_dir)
        except ValueError as exc:
            raise SystemExit(f"manifest payload is outside --payload-dir: {manifest_path}") from exc
        volume_id = str(item.get("volume_id") or "")
        if manifest_path.name != f"{volume_id}_indices.json":
            raise SystemExit(f"manifest volume/path identity mismatch: {manifest_path}")
        if manifest_path in seen_paths or volume_id in seen_volumes:
            raise SystemExit(f"duplicate volume or payload in manifest: {volume_id}")
        seen_paths.add(manifest_path)
        seen_volumes.add(volume_id)
        recomputed = build_repair_manifest(
            manifest_path,
            workers=args.workers,
            reviewed_overrides=item.get("reviewed_overrides") or [],
            minimum_sequence_coverage=float(config.get("minimum_sequence_coverage", 0.8)),
            minimum_title_similarity=float(config.get("minimum_title_similarity", 0.58)),
            minimum_unique_similarity=float(config.get("minimum_unique_similarity", 0.82)),
            minimum_unique_margin=float(config.get("minimum_unique_margin", 0.15)),
        )
        comparable_item = dict(item)
        comparable_item.pop("scan_workers", None)
        comparable_recomputed = dict(recomputed)
        comparable_recomputed.pop("scan_workers", None)
        if comparable_item != comparable_recomputed:
            raise SystemExit(f"manifest decisions do not match a fresh deterministic run: {manifest_path}")
        apply_item = reviewed_resolved_only(item) if only_reviewed_resolved else item
        if only_reviewed_resolved:
            selected_locations = {
                (int(patch["section_index"]), int(patch["entry_index"]))
                for patch in apply_item.get("patches") or []
            }
            conflict_locations = {
                (int(conflict["section_index"]), int(conflict["entry_index"]))
                for conflict in item.get("conflicts") or []
            }
            if selected_locations & conflict_locations:
                raise SystemExit(
                    f"reviewed patch overlaps a conflict and cannot be applied: {manifest_path}"
                )
            apply_item["apply_ready"] = True
        if not apply_item.get("patches"):
            continue
        path, updated = apply_manifest_volume(apply_item)
        before = json.loads(path.read_text(encoding="utf-8"))
        validate_repaired_payload(before, updated, apply_item)
        prepared.append((path, before, updated, apply_item))

    database = args.reimport_db.resolve() if args.reimport_db else None
    if database is not None and not database.is_file():
        raise SystemExit(f"--reimport-db does not exist: {database}")

    backup_dir = args.backup_dir.resolve()
    if not prepared:
        backup_dir.mkdir(parents=True, exist_ok=False)
        fsync_directory(backup_dir.parent)
        applied = {
            "schema_version": 1,
            "operation": "deterministic_structural_target_repair",
            "mode": "apply",
            "source_report": str(report_path),
            "backup_dir": str(backup_dir),
            "volume_count": 0,
            "patch_count": 0,
            "selection": (
                "reviewed_resolved_only"
                if only_reviewed_resolved
                else "all_report_patches"
            ),
            "applied_files": [],
            "reimported_db": None,
            "transaction_id": None,
            "transaction_state": None,
        }
        applied_report = args.applied_report or report_path.with_name(
            report_path.stem + "_applied.json"
        )
        write_json_atomic(applied_report.resolve(), applied)
        return applied
    backup_dir.mkdir(parents=True, exist_ok=False)
    fsync_directory(backup_dir.parent)
    stage_dir = backup_dir / "stage"
    stage_dir.mkdir()
    fsync_directory(backup_dir)
    transaction_id = uuid.uuid4().hex
    active_path, _ = transaction_paths(state_dir)
    file_inventory: list[dict[str, Any]] = []
    database_state: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    applied_report = (
        args.applied_report
        or report_path.with_name(report_path.stem + "_applied.json")
    ).resolve()
    applied = {
        "schema_version": 1,
        "operation": "deterministic_structural_target_repair",
        "mode": "apply",
        "source_report": str(report_path),
        "backup_dir": str(backup_dir),
        "volume_count": len(prepared),
        "patch_count": sum(len(item.get("patches") or []) for _, _, _, item in prepared),
        "selection": (
            "reviewed_resolved_only"
            if only_reviewed_resolved
            else "all_report_patches"
        ),
        "applied_files": [str(path) for path, _, _, _ in prepared],
        "reimported_db": str(database) if database is not None else None,
        "transaction_id": transaction_id,
        "transaction_state": str(backup_dir / "transaction_state.json"),
    }

    with TerminationGuard() as termination:
        try:
            for path, _, updated, item in prepared:
                if payload_sha256(path) != item.get("input_sha256"):
                    raise RuntimeError(f"payload changed before backup: {path}")
                backup_path = backup_dir / path.name
                copy_file_atomic(path, backup_path)
                before_sha = sha256_file(backup_path)
                if before_sha != item.get("input_sha256"):
                    raise RuntimeError(f"payload backup hash mismatch: {backup_path}")
                staged_payload = stage_dir / path.name
                write_json_atomic(staged_payload, updated)
                file_inventory.append(
                    {
                        "target": str(path),
                        "backup": str(backup_path),
                        "staged": str(staged_payload),
                        "before_sha256": before_sha,
                        "after_sha256": sha256_file(staged_payload),
                    }
                )
                termination.checkpoint()

            database_backup = backup_dir / database.name if database is not None else None
            staged_database = stage_dir / f"working-{database.name}" if database is not None else None
            ready_database = stage_dir / f"ready-{database.name}" if database is not None else None
            if database is not None and database_backup is not None:
                before_db_sha = sqlite_snapshot(database, database_backup)
                copy_file_atomic(database_backup, staged_database)
                for path, _, _, _ in prepared:
                    staged_payload = stage_dir / path.name
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(IMPORT_SCRIPT),
                            "--input",
                            str(staged_payload),
                            "--db",
                            str(staged_database),
                            "--replace",
                        ],
                        cwd=PROJECT_ROOT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        capture_output=True,
                        check=False,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"failed to stage reimport of {path.name}: {result.stderr or result.stdout}"
                        )
                    termination.checkpoint()
                after_db_sha = sqlite_snapshot(staged_database, ready_database)
                database_state = {
                    "target": str(database),
                    "backup": str(database_backup),
                    "staged": str(ready_database),
                    "before_sha256": before_db_sha,
                    "after_sha256": after_db_sha,
                }

            state = {
                "schema_version": 1,
                "operation": "deterministic_structural_target_repair",
                "transaction_id": transaction_id,
                "phase": "PREPARED",
                "sequence": 0,
                "source_report": str(report_path),
                "source_report_sha256": sha256_file(report_path),
                "backup_dir": str(backup_dir),
                "files": file_inventory,
                "database": database_state,
                "applied_report": str(applied_report),
                "applied_summary": applied,
            }
            persist_transaction_state(active_path, state)
            test_failpoint("PREPARED")
            termination.checkpoint()

            for path, _, _, item in prepared:
                if payload_sha256(path) != item.get("input_sha256"):
                    raise RuntimeError(f"payload changed between preflight and write: {path}")

            state["phase"] = "INSTALLING_JSONS"
            persist_transaction_state(active_path, state)
            for file_index, item in enumerate(file_inventory):
                copy_file_atomic(Path(item["staged"]), Path(item["target"]))
                if sha256_file(Path(item["target"])) != item["after_sha256"]:
                    raise RuntimeError(f"installed payload hash mismatch: {item['target']}")
                if file_index == 0:
                    test_failpoint("AFTER_FIRST_JSON")
                termination.checkpoint()
            state["phase"] = "JSONS_INSTALLED"
            persist_transaction_state(active_path, state)
            test_failpoint("JSONS_INSTALLED")

            if database_state is not None:
                state["phase"] = "INSTALLING_DATABASE"
                persist_transaction_state(active_path, state)
                test_failpoint("BEFORE_DATABASE_INSTALL")
                sqlite_promote(Path(database_state["staged"]), Path(database_state["target"]))
                test_failpoint("AFTER_DATABASE_INSTALL")
                termination.checkpoint()

            for item in file_inventory:
                if sha256_file(Path(item["target"])) != item["after_sha256"]:
                    raise RuntimeError(f"final payload verification failed: {item['target']}")
            if database_state is not None:
                final_db_sha = sqlite_logical_sha(Path(database_state["target"]), state_dir)
                if final_db_sha != database_state["after_sha256"]:
                    raise RuntimeError("final SQLite fingerprint does not match staged database")
            state["phase"] = "DATA_VERIFIED"
            persist_transaction_state(active_path, state)
            test_failpoint("DATA_VERIFIED")
            termination.checkpoint()
            state["phase"] = "COMMITTED"
            persist_transaction_state(active_path, state)
            test_failpoint("COMMITTED")
            write_json_atomic(applied_report, applied)
            state["phase"] = "FINALIZED"
            persist_transaction_state(active_path, state)
            finalize_transaction_state(active_path, state)
        except BaseException:
            if state is not None and active_path.is_file():
                recover_interrupted_transaction(state_dir, payload_dir)
            raise

    return applied


def run_apply(args: argparse.Namespace) -> dict[str, Any]:
    payload_dir = args.payload_dir.resolve()
    lock_path = Path(getattr(args, "lock_file", None) or default_lock_path(payload_dir))
    state_dir = Path(getattr(args, "state_dir", None) or default_state_dir(payload_dir)).resolve()
    timeout = float(getattr(args, "lock_timeout", 0.0) or 0.0)
    state_dir.mkdir(parents=True, exist_ok=True)
    fsync_directory(state_dir.parent)
    active_path, _ = transaction_paths(state_dir)
    with index_operation_lock(
        lock_path,
        exclusive=True,
        timeout=timeout,
        active_transaction_path=active_path,
        allow_active_transaction=True,
    ):
        recovered = recover_interrupted_transaction(state_dir, payload_dir)
        if (
            recovered is not None
            and recovered.get("recovery") == "completed_forward"
            and Path(str(recovered.get("source_report") or "")).resolve()
            == args.apply_report.resolve()
        ):
            return recovered["applied_summary"]
        return _run_apply_locked(args, state_dir)


def run_recover_only(args: argparse.Namespace) -> dict[str, Any]:
    payload_dir = args.payload_dir.resolve()
    lock_path = Path(getattr(args, "lock_file", None) or default_lock_path(payload_dir))
    state_dir = Path(getattr(args, "state_dir", None) or default_state_dir(payload_dir)).resolve()
    timeout = float(getattr(args, "lock_timeout", 0.0) or 0.0)
    state_dir.mkdir(parents=True, exist_ok=True)
    active_path, _ = transaction_paths(state_dir)
    with index_operation_lock(
        lock_path,
        exclusive=True,
        timeout=timeout,
        active_transaction_path=active_path,
        allow_active_transaction=True,
    ):
        recovered = recover_interrupted_transaction(state_dir, payload_dir)
    return {
        "schema_version": 1,
        "operation": "deterministic_structural_target_repair",
        "mode": "recover_only",
        "status": "recovered" if recovered is not None else "no_active_transaction",
        "transaction": recovered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic ex-post repair pipeline for physical targets of chapters and other "
            "structural index entries. Dry-run is the default; apply consumes an immutable report."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply-report", type=Path)
    mode.add_argument("--recover-only", action="store_true")
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--volume", action="append", default=[])
    parser.add_argument("--all-volumes", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--reviewed-overrides",
        type=Path,
        help=(
            "Versioned JSON allow-list for corpus corrections that were verified manually; "
            "the selected overrides are embedded in the immutable dry-run report."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a dry-run from its durable per-volume report parts.",
    )
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--applied-report", type=Path)
    parser.add_argument(
        "--only-reviewed-resolved",
        action="store_true",
        help=(
            "Apply only resolved, non-null patches whose evidence method is reviewed_override; "
            "the complete immutable report is still recomputed and verified first, while "
            "unselected target conflicts remain quarantined."
        ),
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        help="Advisory corpus lock (default: data/.patristic-index-write.lock).",
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=0.0,
        help="Seconds to wait for the corpus lock; default fails immediately.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="Persistent active-transaction journal directory.",
    )
    parser.add_argument(
        "--reimport-db",
        type=Path,
        help="After applying JSON changes, reimport each affected volume with --replace into this SQLite DB.",
    )
    parser.add_argument("--minimum-sequence-coverage", type=float, default=0.8)
    parser.add_argument("--minimum-title-similarity", type=float, default=0.58)
    parser.add_argument("--minimum-unique-similarity", type=float, default=0.82)
    parser.add_argument("--minimum-unique-margin", type=float, default=0.15)
    parser.add_argument(
        "--workers",
        type=int,
        default=min(12, os.cpu_count() or 1),
        help=(
            "Maximum process budget. Heavy volumes use it internally; small volumes are "
            "distributed across a persistent corpus-level pool."
        ),
    )
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.lock_timeout < 0:
        parser.error("--lock-timeout cannot be negative")
    for name in (
        "minimum_sequence_coverage",
        "minimum_title_similarity",
        "minimum_unique_similarity",
        "minimum_unique_margin",
    ):
        if not 0.0 <= getattr(args, name) <= 1.0:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")

    if args.recover_only:
        if args.resume:
            parser.error("--resume is only valid for dry-run")
        if args.only_reviewed_resolved:
            parser.error("--only-reviewed-resolved is only valid with --apply-report")
        result = run_recover_only(args)
    elif args.apply_report:
        if args.resume:
            parser.error("--resume is only valid for dry-run")
        if args.reviewed_overrides is not None:
            parser.error("--reviewed-overrides is only valid for dry-run")
        result = run_apply(args)
    else:
        if args.only_reviewed_resolved:
            parser.error("--only-reviewed-resolved is only valid with --apply-report")
        if args.reimport_db is not None:
            parser.error("--reimport-db is only valid with --apply-report")
        if args.report is None:
            parser.error("dry-run requires --report")
        result = run_dry_run(args)
    print(json.dumps({key: value for key, value in result.items() if key != "volumes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
