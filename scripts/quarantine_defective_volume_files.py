#!/usr/bin/env python3
"""Audit or quarantine filesystem derivatives of confirmed wrong-source volumes.

The default is a dry-run. ``--apply`` moves selected paths to a quarantine tree
while preserving paths relative to the repository. Nothing is unlinked. The
legacy Pagefind tree is a separate opt-in phase because it is unrelated to the
current PatrologiaIndexer publication pipeline.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    PROJECT_ROOT
    / "data"
    / "volume_similarity_audit"
    / "defective_volume_cleanup_manifest.json"
)
AUTHORIZED_VOLUME_IDS = ("PG024", "PG031", "PG084", "PG116", "PL124")
CONFIRMATION = ",".join(AUTHORIZED_VOLUME_IDS)
DEFAULT_PHASES = ("ocr", "derived", "publication")
ALL_PHASES = DEFAULT_PHASES + ("legacy-pagefind",)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema_version") != 1:
        raise SystemExit(f"Unsupported cleanup manifest schema: {path}")
    volume_ids = tuple(manifest.get("volume_ids") or ())
    if volume_ids != AUTHORIZED_VOLUME_IDS:
        raise SystemExit(
            "Refusing a changed volume set. Expected exactly "
            f"{CONFIRMATION}; found {','.join(volume_ids)}"
        )
    if len(volume_ids) != len(set(volume_ids)):
        raise SystemExit("Cleanup manifest contains duplicate volume IDs")
    if any(not re.fullmatch(r"(?:PG|PL|PO)\d{3}", item) for item in volume_ids):
        raise SystemExit("Cleanup manifest contains an invalid volume ID")
    return manifest, hashlib.sha256(raw).hexdigest()


def _add_existing(targets: dict[Path, set[str]], path: Path, phase: str) -> None:
    if path.exists() or path.is_symlink():
        targets.setdefault(path, set()).add(phase)


def _add_prefix_matches(
    targets: dict[Path, set[str]], directory: Path, volume_ids: Sequence[str], phase: str
) -> None:
    if not directory.is_dir():
        return
    for volume_id in volume_ids:
        for path in directory.glob(f"{volume_id}*"):
            _add_existing(targets, path, phase)


def _collect_candidates(
    root: Path, volume_ids: Sequence[str], phases: Sequence[str]
) -> dict[Path, set[str]]:
    candidates: dict[Path, set[str]] = {}
    selected = set(phases)

    if "ocr" in selected:
        for volume_id in volume_ids:
            _add_existing(candidates, root / "teste" / volume_id, "ocr")
            _add_existing(candidates, root / "teste" / f"{volume_id}.pdf.log", "ocr")
            _add_existing(
                candidates,
                root
                / "data"
                / ".resumo_locks"
                / f"patristica_resumos.db.{volume_id}.lock",
                "ocr",
            )

    if "derived" in selected:
        for relative in (
            "data/alphabetical_index_payloads",
            "data/alphabetical_prefiltered",
            "data/index_payloads",
            "data/index_payload_audits",
            "data/index_logs",
            "data/alphabetical_index_logs",
            "data/shards/enrichment",
            "web/public/meta",
            "web/public/snapshots",
            "web/public/indices",
            "web/public/alpha",
            "web/public/segments",
        ):
            _add_prefix_matches(candidates, root / relative, volume_ids, "derived")
        for relative in (
            "data/intermediate_payloads",
            "data/index_intermediate_payloads",
        ):
            for volume_id in volume_ids:
                _add_existing(candidates, root / relative / volume_id, "derived")

    if "publication" in selected:
        for relative in (
            "data/index_payload_audits/summary.json",
            "data/scripture_citations.db.report.json",
            "data/shards/enrichment/index.json",
            "web/public/volumes.json",
            "web/public/indices/manifest.json",
            "web/public/dict",
            "web/public/indexador/search",
            "web/public/indexador/indices",
            "web/public/scripture/v3",
            "web/public/scripture/references/v1",
            "web/dist",
        ):
            _add_existing(candidates, root / relative, "publication")

    if "legacy-pagefind" in selected:
        _add_existing(candidates, root / "web" / "public" / "pagefind", "legacy-pagefind")

    return _without_nested_duplicates(candidates)


def _without_nested_duplicates(
    candidates: dict[Path, set[str]],
) -> dict[Path, set[str]]:
    kept: dict[Path, set[str]] = {}
    for path in sorted(candidates, key=lambda item: (len(item.parts), str(item))):
        parent = next((item for item in kept if item in path.parents), None)
        if parent is None:
            kept[path] = set(candidates[path])
        else:
            kept[parent].update(candidates[path])
    return kept


def _path_stats(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {
            "kind": "symlink",
            "files": 0,
            "directories": 0,
            "logical_bytes": 0,
        }
    if path.is_file():
        return {
            "kind": "file",
            "files": 1,
            "directories": 0,
            "logical_bytes": path.stat().st_size,
        }
    files = 0
    directories = 1
    logical_bytes = 0
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        if child.is_file():
            files += 1
            logical_bytes += child.stat().st_size
        elif child.is_dir():
            directories += 1
    return {
        "kind": "directory",
        "files": files,
        "directories": directories,
        "logical_bytes": logical_bytes,
    }


def _ocr_page_counts(root: Path, volume_ids: Sequence[str]) -> dict[str, dict[str, int] | None]:
    counts: dict[str, dict[str, int] | None] = {}
    for volume_id in volume_ids:
        volume_dir = root / "teste" / volume_id
        if not volume_dir.is_dir():
            counts[volume_id] = None
            continue
        counts[volume_id] = {
            "images": len(list((volume_dir / "images").glob("*.png"))),
            "texts": len(list((volume_dir / "text").glob("*.txt"))),
        }
    return counts


def _shared_artifact_warnings(
    root: Path, volume_ids: Sequence[str]
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    repair_path = (
        root
        / "data"
        / "index_repairs"
        / "manual_work_anchor_repairs_2026-08-23.json"
    )
    if repair_path.is_file():
        payload = json.loads(repair_path.read_text(encoding="utf-8"))
        repairs = payload.get("repairs") or []
        selected = [
            row for row in repairs if str(row.get("volume_id") or "") in volume_ids
        ]
        if selected:
            by_volume = {
                volume_id: sum(
                    1
                    for row in selected
                    if str(row.get("volume_id") or "") == volume_id
                )
                for volume_id in volume_ids
            }
            warnings.append(
                {
                    "path": str(repair_path),
                    "reason": (
                        "Shared historical/manual file: preserve the file, but do "
                        "not reuse selected-volume repairs against replacement OCR."
                    ),
                    "selected_rows": len(selected),
                    "selected_rows_by_volume": {
                        key: value for key, value in by_volume.items() if value
                    },
                    "action": "preserve_and_review_manually",
                }
            )
    return warnings


def _validate_ocr_guard(
    counts: dict[str, dict[str, int] | None],
    manifest: dict[str, Any],
    *,
    allow_count_drift: bool,
) -> list[str]:
    expected = manifest.get("old_ocr_pages") or {}
    drift: list[str] = []
    for volume_id, observed in counts.items():
        if observed is None:
            continue
        old_pages = int(expected[volume_id])
        for kind in ("images", "texts"):
            if observed[kind] != old_pages:
                drift.append(
                    f"{volume_id}:{kind}: expected old={old_pages} or missing, "
                    f"found {observed[kind]}"
                )
    if drift and not allow_count_drift:
        raise RuntimeError("OCR page-count guard rejected apply:\n- " + "\n- ".join(drift))
    return drift


def _validate_source_path(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Candidate escapes project root: {path}") from exc
    if resolved == root:
        raise RuntimeError("Project root cannot be quarantined")
    return resolved


def _validate_quarantine(root: Path, quarantine: Path, candidates: Iterable[Path]) -> Path:
    resolved = quarantine.resolve()
    forbidden = {
        Path("/").resolve(),
        root,
        (root / "data").resolve(),
        (root / "teste").resolve(),
        (root / "web").resolve(),
        (root / "web" / "public").resolve(),
    }
    if resolved in forbidden:
        raise RuntimeError(f"Unsafe quarantine root: {resolved}")
    for candidate in candidates:
        source = candidate.resolve()
        if (
            resolved == source
            or resolved in source.parents
            or source in resolved.parents
        ):
            raise RuntimeError(f"Quarantine overlaps a selected source: {source}")
    return resolved


def _move_to_quarantine(
    root: Path,
    quarantine: Path,
    candidates: Sequence[Path],
) -> list[dict[str, str]]:
    moves: list[tuple[Path, Path]] = []
    for source in candidates:
        relative = _validate_source_path(root, source).relative_to(root)
        destination = quarantine / relative
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Quarantine destination already exists: {destination}")
        moves.append((source, destination))

    completed: list[tuple[Path, Path]] = []
    try:
        for source, destination in moves:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            completed.append((source, destination))
    except BaseException:
        for source, destination in reversed(completed):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        raise
    return [
        {"source": str(source), "destination": str(destination)}
        for source, destination in completed
    ]


def _selected_phases(values: list[str] | None) -> tuple[str, ...]:
    if not values:
        return DEFAULT_PHASES
    phases: list[str] = []
    for value in values:
        if value not in ALL_PHASES:
            raise SystemExit(f"Unknown phase: {value}")
        if value not in phases:
            phases.append(value)
    return tuple(phases)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit or move wrong-source OCR derivatives to a recoverable "
            "quarantine. Dry-run is the default."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--phase",
        action="append",
        choices=ALL_PHASES,
        help=(
            "Repeat to select phases. Default: ocr, derived, publication. "
            "legacy-pagefind is always opt-in."
        ),
    )
    parser.add_argument("--report", type=Path, help="Write an atomic JSON audit report")
    parser.add_argument("--apply", action="store_true", help="Move paths to quarantine")
    parser.add_argument(
        "--quarantine-dir",
        type=Path,
        help="Required with --apply; must not be a source/root directory",
    )
    parser.add_argument(
        "--confirm",
        help=f"Required with --apply; exact value: {CONFIRMATION}",
    )
    parser.add_argument(
        "--allow-count-drift",
        action="store_true",
        help="Apply despite old OCR page-count drift",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    manifest_path = args.manifest.resolve()
    manifest, manifest_sha256 = _load_manifest(manifest_path)
    volume_ids = tuple(manifest["volume_ids"])
    phases = _selected_phases(args.phase)

    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(
            "Refusing apply without exact --confirm "
            f"{CONFIRMATION}"
        )
    if args.apply and args.quarantine_dir is None:
        raise SystemExit("--quarantine-dir is required with --apply")
    if args.apply and args.report is None:
        raise SystemExit("--report is required with --apply")

    candidates = _collect_candidates(root, volume_ids, phases)
    ordered_candidates = sorted(candidates)
    ocr_counts = _ocr_page_counts(root, volume_ids)
    drift = _validate_ocr_guard(
        ocr_counts,
        manifest,
        allow_count_drift=(not args.apply or bool(args.allow_count_drift)),
    )
    candidate_rows: list[dict[str, Any]] = []
    for path in ordered_candidates:
        _validate_source_path(root, path)
        candidate_rows.append(
            {
                "path": str(path),
                "relative_path": str(path.resolve().relative_to(root)),
                "phases": sorted(candidates[path]),
                **_path_stats(path),
            }
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "dry_run",
        "started_at": _utc_now(),
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "project_root": str(root),
        "volume_ids": list(volume_ids),
        "phases": list(phases),
        "apply": bool(args.apply),
        "ocr_page_counts": ocr_counts,
        "count_drift": drift,
        "shared_artifact_warnings": _shared_artifact_warnings(root, volume_ids),
        "candidates": candidate_rows,
        "candidate_totals": {
            "top_level_paths": len(candidate_rows),
            "files": sum(int(row["files"]) for row in candidate_rows),
            "directories": sum(int(row["directories"]) for row in candidate_rows),
            "logical_bytes": sum(int(row["logical_bytes"]) for row in candidate_rows),
        },
        "moves": [],
    }
    report_path = args.report.resolve() if args.report else None
    if report_path:
        _write_json_atomic(report_path, report)

    if args.apply:
        quarantine = _validate_quarantine(
            root, args.quarantine_dir, ordered_candidates
        )
        report["quarantine_dir"] = str(quarantine)
        try:
            report["moves"] = _move_to_quarantine(
                root, quarantine, ordered_candidates
            )
            report["status"] = "applied"
        except BaseException as exc:
            report["status"] = "failed"
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["failed_at"] = _utc_now()
            if report_path:
                _write_json_atomic(report_path, report)
            raise

    report["finished_at"] = _utc_now()
    if report_path:
        _write_json_atomic(report_path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
