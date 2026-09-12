#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.editorial_page_estimator import (
    DEFAULT_ESTIMATOR_DB,
    estimate_editorial_pages,
)
from tools.indexing.index_work_anchor_reconciler import reconcile_work_anchors


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAYLOAD_DIR = PROJECT_ROOT / "data" / "index_payloads"
DEFAULT_DB = PROJECT_ROOT / "data" / "patristic_indices.db"
IMPORT_SCRIPT = PROJECT_ROOT / "scripts" / "import_index_json.py"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _invalid_ranges(payload: dict[str, Any]) -> list[str]:
    invalid: list[str] = []
    for index, work in enumerate(payload.get("works") or []):
        if not isinstance(work, dict):
            continue
        start = work.get("start_page")
        end = work.get("end_page")
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and start > end
        ):
            invalid.append(str(work.get("work_key") or f"works[{index}]"))
    return invalid


def _import_payload(path: Path, db_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(IMPORT_SCRIPT),
            "--input",
            str(path),
            "--db",
            str(db_path),
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
            f"failed to import {path.name}: {result.stderr or result.stdout}"
        )


def _select_payloads(
    payload_dir: Path,
    *,
    volume_ids: list[str],
    all_volumes: bool,
) -> list[Path]:
    if all_volumes:
        return sorted(payload_dir.glob("*_indices.json"))
    paths = [
        payload_dir / f"{volume_id.upper()}_indices.json"
        for blob in volume_ids
        for volume_id in blob.split(",")
        if volume_id.strip()
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("payload not found: " + ", ".join(missing))
    return paths


def _process_payload(
    path: Path,
    *,
    editorial_page_db: Path,
    use_editorial_page_cache: bool,
    apply: bool,
    no_import: bool,
    db_path: Path,
) -> tuple[dict[str, Any], str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        volume = payload.get("volume") or {}
        volume_id = str(volume.get("volume_id") or path.name[:-13])
        collection = str(volume.get("collection") or volume_id[:2]).upper()
        source_root = Path(str(volume.get("source_root") or "")).expanduser()
        editorial: dict[str, Any] | None = None
        if collection in {"PG", "PL"}:
            editorial = estimate_editorial_pages(
                volume_id=volume_id,
                source_root=source_root,
                collection=collection,
                db_path=editorial_page_db,
                use_cache=use_editorial_page_cache,
            )
        report = reconcile_work_anchors(
            payload,
            editorial_pages=editorial,
        )
        report["payload_file"] = str(path)
        report["apply_requested"] = apply
        invalid = _invalid_ranges(payload)
        report["remaining_invalid_ranges"] = invalid
        report["apply_status"] = "dry_run"
        if apply and int(report.get("payload_mutation_count") or 0):
            if invalid and not no_import:
                report["apply_status"] = "skipped_invalid_ranges"
                return report, volume_id
            _write_json_atomic(path, payload)
            if invalid:
                report["apply_status"] = (
                    "payload_updated_invalid_ranges_requires_rerun"
                )
            elif not no_import:
                _import_payload(path, db_path)
                report["apply_status"] = "payload_and_db_updated"
            else:
                report["apply_status"] = "payload_updated"
        elif apply:
            report["apply_status"] = "no_changes"
        return report, None
    except Exception as exc:
        return (
            {
                "payload_file": str(path),
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            path.name[:-13],
        )


def _process_payload_job(
    job: tuple[Path, Path, bool, bool, bool, Path],
) -> tuple[dict[str, Any], str | None]:
    (
        path,
        editorial_page_db,
        use_editorial_page_cache,
        apply,
        no_import,
        db_path,
    ) = job
    return _process_payload(
        path,
        editorial_page_db=editorial_page_db,
        use_editorial_page_cache=use_editorial_page_cache,
        apply=apply,
        no_import=no_import,
        db_path=db_path,
    )


def _print_result(result: tuple[dict[str, Any], str | None]) -> None:
    report, failure = result
    if failure:
        print(
            f"[ERROR] {Path(str(report.get('payload_file') or failure)).name}: "
            f"{report.get('error') or report.get('apply_status')}",
            file=sys.stderr,
        )
        return
    print(
        json.dumps(
            {
                "volume_id": report.get("volume_id"),
                "changed_count": report.get("changed_count"),
                "annotation_changed_count": report.get(
                    "annotation_changed_count"
                ),
                "rerun_count": report.get("rerun_count"),
                "unresolved_count": report.get("unresolved_count"),
                "apply_status": report.get("apply_status"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Audit or deterministically repair existing work anchors by combining "
            "existing index references, editorial-page estimates, and OCR title evidence."
        )
    )
    selection = ap.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--volume-id",
        action="append",
        default=[],
        help="Volume id; repeat or pass comma-separated ids",
    )
    selection.add_argument("--all-volumes", action="store_true")
    ap.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--editorial-page-db", type=Path, default=DEFAULT_ESTIMATOR_DB)
    ap.add_argument("--no-editorial-page-cache", action="store_true")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Atomically update changed payloads and replace their rows in SQLite",
    )
    ap.add_argument(
        "--no-import",
        action="store_true",
        help="With --apply, update payload JSON only",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Multiprocess payload workers (no fixed upper limit)",
    )
    ap.add_argument("--report-json", type=Path)
    args = ap.parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")
    if args.workers > 1 and args.apply and not args.no_import:
        raise SystemExit(
            "Parallel --apply requires --no-import; import validated payloads serially afterward"
        )

    payload_paths = _select_payloads(
        args.payload_dir.resolve(),
        volume_ids=args.volume_id,
        all_volumes=args.all_volumes,
    )
    editorial_page_db = args.editorial_page_db.resolve()
    db_path = args.db.resolve()
    jobs = [
        (
            path,
            editorial_page_db,
            not args.no_editorial_page_cache,
            bool(args.apply),
            bool(args.no_import),
            db_path,
        )
        for path in payload_paths
    ]
    print(
        json.dumps(
            {
                "event": "start",
                "payload_count": len(jobs),
                "workers": args.workers,
                "apply": bool(args.apply),
            }
        ),
        flush=True,
    )
    if args.workers == 1:
        processed = []
        for index, job in enumerate(jobs, start=1):
            print(
                json.dumps(
                    {
                        "event": "progress",
                        "completed": index - 1,
                        "total": len(jobs),
                        "current": job[0].name,
                    }
                ),
                flush=True,
            )
            result = _process_payload_job(job)
            processed.append(result)
            _print_result(result)
    else:
        ordered: list[tuple[dict[str, Any], str | None] | None] = [
            None
        ] * len(jobs)
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            future_indexes = {
                executor.submit(_process_payload_job, job): index
                for index, job in enumerate(jobs)
            }
            pending = set(future_indexes)
            completed_count = 0
            while pending:
                done, pending = wait(
                    pending,
                    timeout=30,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    print(
                        json.dumps(
                            {
                                "event": "heartbeat",
                                "completed": completed_count,
                                "remaining": len(pending),
                                "total": len(jobs),
                            }
                        ),
                        flush=True,
                    )
                    continue
                for future in done:
                    index = future_indexes[future]
                    result = future.result()
                    ordered[index] = result
                    completed_count += 1
                    _print_result(result)
        processed = [result for result in ordered if result is not None]

    reports = [report for report, _failure in processed]
    failures = [failure for _report, failure in processed if failure]

    output = {
        "schema_version": 1,
        "apply": bool(args.apply),
        "workers": args.workers,
        "payload_count": len(payload_paths),
        "changed_volume_count": sum(
            int(report.get("changed_count") or 0) > 0
            for report in reports
        ),
        "failure_count": len(failures),
        "failures": failures,
        "reports": reports,
    }
    if args.report_json:
        _write_json_atomic(args.report_json.resolve(), output)
    print(
        json.dumps(
            {
                "event": "complete",
                "payload_count": len(payload_paths),
                "changed_volume_count": output["changed_volume_count"],
                "failure_count": len(failures),
                "report_json": str(args.report_json.resolve())
                if args.report_json
                else None,
            }
        ),
        flush=True,
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
