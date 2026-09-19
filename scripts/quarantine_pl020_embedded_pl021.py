#!/usr/bin/env python3
"""Move o segmento PL XXI incorporado em PL020 para quarentena recuperavel.

O intervalo confirmado no fac-simile e 612--1223 (inclusive). O modo padrao e
somente auditoria. A aplicacao exige confirmacao literal, nao sobrescreve
arquivos e pode ser retomada depois de uma interrupcao.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "teste" / "PL020"
DEFAULT_QUARANTINE = (
    Path("/homessddata/patristica/quarantine")
    / "PL020_embedded_PL021_pages_0612_1223"
)
VOLUME_ID = "PL020"
FIRST_PAGE = 612
LAST_PAGE = 1223
CONFIRMATION = "PL020:612-1223"
EXPECTED_FILES = {"images": 612, "text": 1000}
EXPECTED_UNIQUE_PAGES = {"images": 612, "text": 612}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def trailing_page_number(path: Path) -> int | None:
    stem = path.stem
    tail = stem.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _files(directory: Path) -> Iterable[Path]:
    if not directory.is_dir():
        return ()
    return (path for path in directory.iterdir() if path.is_file())


def validate_paths(source: Path, quarantine: Path) -> tuple[Path, Path]:
    source = source.resolve()
    quarantine = quarantine.resolve()
    if source == quarantine or source in quarantine.parents or quarantine in source.parents:
        raise RuntimeError("A quarentena nao pode coincidir nem se sobrepor a PL020")
    forbidden_destinations = {
        Path("/").resolve(),
        PROJECT_ROOT.resolve(),
        (PROJECT_ROOT / "teste").resolve(),
        Path("/homessddata/patristica").resolve(),
    }
    if source == Path("/") or quarantine in forbidden_destinations:
        raise RuntimeError("Caminho raiz recusado")
    return source, quarantine


@contextmanager
def volume_lock() -> Iterator[None]:
    lock_dir = PROJECT_ROOT / "data" / ".resumo_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"patristica_resumos.db.{VOLUME_ID}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"PL020 esta sendo processado: {lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def build_inventory(
    source: Path,
    quarantine: Path,
    *,
    include_hashes: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source, quarantine = validate_paths(source, quarantine)
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}

    for kind in ("images", "text"):
        source_dir = source / kind
        destination_dir = quarantine / kind
        names: set[str] = set()
        for directory in (source_dir, destination_dir):
            for path in _files(directory):
                page_num = trailing_page_number(path)
                if page_num is not None and FIRST_PAGE <= page_num <= LAST_PAGE:
                    names.add(path.name)

        pages: set[int] = set()
        kind_rows: list[dict[str, Any]] = []
        for name in sorted(names):
            page_num = trailing_page_number(Path(name))
            if page_num is None:
                continue
            pages.add(page_num)
            old_path = source_dir / name
            new_path = destination_dir / name
            old_exists = old_path.is_file()
            new_exists = new_path.is_file()
            if old_exists and new_exists:
                state = "conflict"
                observed = old_path
            elif old_exists:
                state = "pending"
                observed = old_path
            elif new_exists:
                state = "moved"
                observed = new_path
            else:
                raise AssertionError("Nome inventariado sem arquivo")
            stat = observed.stat()
            row: dict[str, Any] = {
                "kind": kind,
                "page_num": page_num,
                "name": name,
                "source": str(old_path),
                "destination": str(new_path),
                "state": state,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            if include_hashes:
                row["sha256"] = file_sha256(observed)
                if old_exists and new_exists:
                    row["destination_sha256"] = file_sha256(new_path)
            kind_rows.append(row)

        expected_pages = set(range(FIRST_PAGE, LAST_PAGE + 1))
        missing_pages = sorted(expected_pages - pages)
        unexpected_pages = sorted(pages - expected_pages)
        counts = {
            "files": len(kind_rows),
            "unique_pages": len(pages),
            "pending": sum(row["state"] == "pending" for row in kind_rows),
            "moved": sum(row["state"] == "moved" for row in kind_rows),
            "conflicts": sum(row["state"] == "conflict" for row in kind_rows),
            "missing_pages": missing_pages,
            "unexpected_pages": unexpected_pages,
            "logical_bytes": sum(int(row["size"]) for row in kind_rows),
        }
        summary[kind] = counts
        rows.extend(kind_rows)

    return rows, summary


def assert_expected_inventory(summary: dict[str, Any]) -> None:
    problems: list[str] = []
    for kind in ("images", "text"):
        observed = summary[kind]
        if observed["files"] != EXPECTED_FILES[kind]:
            problems.append(
                f"{kind}: esperados {EXPECTED_FILES[kind]} arquivos; "
                f"encontrados {observed['files']}"
            )
        if observed["unique_pages"] != EXPECTED_UNIQUE_PAGES[kind]:
            problems.append(
                f"{kind}: esperadas {EXPECTED_UNIQUE_PAGES[kind]} paginas; "
                f"encontradas {observed['unique_pages']}"
            )
        if observed["missing_pages"]:
            problems.append(f"{kind}: paginas ausentes {observed['missing_pages'][:20]}")
        if observed["conflicts"]:
            problems.append(
                f"{kind}: {observed['conflicts']} destinos ja coexistem com a origem"
            )
    if problems:
        raise RuntimeError("Inventario recusado:\n- " + "\n- ".join(problems))


def move_pending(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    moves = [row for row in rows if row["state"] == "pending"]
    completed: list[tuple[Path, Path]] = []
    try:
        for row in moves:
            source = Path(str(row["source"]))
            destination = Path(str(row["destination"]))
            if not source.is_file():
                raise FileNotFoundError(source)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            completed.append((source, destination))
    except BaseException:
        for source, destination in reversed(completed):
            if destination.is_file() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        raise
    return [
        {"source": str(source), "destination": str(destination)}
        for source, destination in completed
    ]


def rollback_moves(moves: Sequence[dict[str, str]]) -> None:
    for row in reversed(moves):
        source = Path(row["source"])
        destination = Path(row["destination"])
        if destination.is_file() and not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-volume-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--quarantine-dir", type=Path, default=DEFAULT_QUARANTINE)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--hash-files", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm",
        help=f"Obrigatorio com --apply; valor exato: {CONFIRMATION}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(f"Recusado sem --confirm {CONFIRMATION}")
    if args.apply and args.report is None:
        raise SystemExit("--report e obrigatorio com --apply")

    source, quarantine = validate_paths(
        args.source_volume_dir, args.quarantine_dir
    )
    rows, summary = build_inventory(
        source, quarantine, include_hashes=bool(args.hash_files)
    )
    assert_expected_inventory(summary)
    report: dict[str, Any] = {
        "schema_version": 1,
        "operation": "quarantine_pl020_embedded_pl021",
        "status": "dry_run",
        "started_at": utc_now(),
        "apply": bool(args.apply),
        "volume_id": VOLUME_ID,
        "first_page": FIRST_PAGE,
        "last_page": LAST_PAGE,
        "source_volume_dir": str(source),
        "quarantine_dir": str(quarantine),
        "hashes_included": bool(args.hash_files),
        "summary_before": summary,
        "files": rows,
        "moves": [],
    }
    report_path = args.report.resolve() if args.report else None
    if report_path:
        write_json_atomic(report_path, report)

    try:
        if args.apply:
            with volume_lock():
                report["moves"] = move_pending(rows)
            _after_rows, after = build_inventory(
                source, quarantine, include_hashes=False
            )
            assert_expected_inventory(after)
            if any(after[kind]["pending"] for kind in ("images", "text")):
                raise RuntimeError("Ainda existem arquivos do segmento na origem")
            report["summary_after"] = after
            report["status"] = "applied"
    except BaseException as exc:
        rollback_error = None
        if report["moves"]:
            try:
                rollback_moves(report["moves"])
                report["rolled_back_moves"] = len(report["moves"])
            except BaseException as rollback_exc:
                rollback_error = f"{type(rollback_exc).__name__}: {rollback_exc}"
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        if rollback_error:
            report["rollback_error"] = rollback_error
        report["failed_at"] = utc_now()
        if report_path:
            write_json_atomic(report_path, report)
        raise

    report["finished_at"] = utc_now()
    if report_path:
        write_json_atomic(report_path, report)
    compact = {
        "status": report["status"],
        "source": str(source),
        "quarantine": str(quarantine),
        "summary": report.get("summary_after", summary),
        "moved_now": len(report["moves"]),
        "report": str(report_path) if report_path else None,
    }
    print(json.dumps(compact, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
