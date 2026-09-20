#!/usr/bin/env python3
"""Remove variantes OCR UUID antigas quando existe uma página canônica.

A geração antiga conhecida usa ``<uuid>-NNNN.txt``; a geração canônica mais
recente usa ``<volume>-NNN.txt``. O comando é dry-run por padrão. Em ``--apply``
ele move as variantes antigas para uma quarentena recuperável e, quando
necessário, aponta ``ocr_versions.db`` para a versão cujo hash corresponde ao
arquivo canônico mantido.

Exemplos:
    python scripts/normalize_duplicate_ocr_pages.py PG013 PG044 PG080 PG081
    python scripts/normalize_duplicate_ocr_pages.py PG013 PG044 PG080 PG081 --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.corpus_utils import page_files
import ocr_versions_db as vdb


DEFAULT_BASE_DIR = PROJECT_ROOT / "teste"
DEFAULT_DB = PROJECT_ROOT / "data" / "ocr_versions.db"
DEFAULT_QUARANTINE = PROJECT_ROOT / "data" / "ocr_duplicate_quarantine"
UUID_4DIGIT_RE = re.compile(
    r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-(\d{4})\.txt$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RepairItem:
    volume_id: str
    page_num: int
    kept_file: str
    kept_hash: str
    removed_file: str
    removed_hash: str
    current_result_id: int
    kept_result_id: int | None
    changes_current: bool
    registers_canonical: bool


def sha256_text_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"Banco não encontrado: {db_path}")
    con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def _history_row(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    page_num: int,
    text_hash: str,
) -> sqlite3.Row | None:
    row = con.execute(
        """
        SELECT id, created_at
        FROM ocr_results
        WHERE volume_id = ? AND page_num = ? AND text_hash = ?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 1
        """,
        (volume_id, page_num, text_hash),
    ).fetchone()
    return row


def build_plan(
    *,
    base_dir: Path,
    db_path: Path,
    volumes: Sequence[str],
    register_missing_canonical: bool = False,
) -> list[RepairItem]:
    con = connect_read_only(db_path)
    plan: list[RepairItem] = []
    try:
        for volume_id in volumes:
            text_dir = base_dir / volume_id / "text"
            if not text_dir.is_dir():
                raise FileNotFoundError(f"Diretório não encontrado: {text_dir}")

            for page_num, candidates in sorted(
                page_files(text_dir, suffixes=(".txt",)).items()
            ):
                if len(candidates) == 1:
                    continue
                canonical = text_dir / f"{volume_id}-{page_num:03d}.txt"
                legacy = [
                    path
                    for path in candidates
                    if (match := UUID_4DIGIT_RE.fullmatch(path.name))
                    and int(match.group(1)) == page_num
                ]
                unexpected = [
                    path for path in candidates if path != canonical and path not in legacy
                ]
                if not canonical.is_file() or len(legacy) != 1 or unexpected:
                    names = ", ".join(path.name for path in candidates)
                    raise RuntimeError(
                        f"{volume_id}:{page_num}: grupo duplicado fora do padrão seguro: {names}"
                    )

                kept_hash = sha256_text_file(canonical)
                removed_hash = sha256_text_file(legacy[0])
                kept_row = _history_row(
                    con,
                    volume_id=volume_id,
                    page_num=page_num,
                    text_hash=kept_hash,
                )
                if kept_row is None and not register_missing_canonical:
                    raise RuntimeError(
                        f"{volume_id}:{page_num}: hash canônico não existe no histórico OCR; "
                        "use --register-missing-canonical após revisar o plano"
                    )
                removed_row = _history_row(
                    con,
                    volume_id=volume_id,
                    page_num=page_num,
                    text_hash=removed_hash,
                )
                if removed_row is None:
                    raise RuntimeError(
                        f"{volume_id}:{page_num}: hash legado não existe no histórico OCR"
                    )
                current = con.execute(
                    """
                    SELECT id, text_hash
                    FROM ocr_results
                    WHERE volume_id = ? AND page_num = ? AND is_current = 1
                    ORDER BY id
                    """,
                    (volume_id, page_num),
                ).fetchall()
                if len(current) != 1:
                    raise RuntimeError(
                        f"{volume_id}:{page_num}: esperada uma versão atual; encontradas {len(current)}"
                    )
                plan.append(
                    RepairItem(
                        volume_id=volume_id,
                        page_num=page_num,
                        kept_file=str(canonical),
                        kept_hash=kept_hash,
                        removed_file=str(legacy[0]),
                        removed_hash=removed_hash,
                        current_result_id=int(current[0]["id"]),
                        kept_result_id=(int(kept_row["id"]) if kept_row else None),
                        changes_current=current[0]["text_hash"] != kept_hash,
                        registers_canonical=kept_row is None,
                    )
                )
    finally:
        con.close()
    return plan


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def apply_plan(
    *,
    plan: Sequence[RepairItem],
    db_path: Path,
    quarantine_root: Path,
) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = quarantine_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    pending_manifest = run_dir / "manifest.pending.json"
    manifest = run_dir / "manifest.json"
    payload = {
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(db_path),
        "policy": "keep canonical <volume>-NNN; quarantine legacy UUID-NNNN",
        "items": [asdict(item) for item in plan],
    }
    _write_json(pending_manifest, payload)

    con = sqlite3.connect(db_path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    moved: list[tuple[Path, Path]] = []
    registered_results: list[dict[str, int | str]] = []
    try:
        reconciliation_engine_id = None
        if any(item.registers_canonical for item in plan):
            reconciliation_engine_id = vdb.get_or_create_engine_version(
                con,
                engine="filesystem_reconciliation",
                model=None,
                prompt_key="canonical_file_import_v1",
                system_prompt=(
                    "Deterministic registration of an existing canonical OCR file; "
                    "no model inference was performed."
                ),
            )
        con.execute("BEGIN IMMEDIATE")
        for item in plan:
            source = Path(item.removed_file)
            destination = run_dir / item.volume_id / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not source.is_file():
                raise FileNotFoundError(f"Fonte desapareceu durante a aplicação: {source}")
            if sha256_text_file(source) != item.removed_hash:
                raise RuntimeError(f"Fonte mudou após o dry-run: {source}")
            if sha256_text_file(Path(item.kept_file)) != item.kept_hash:
                raise RuntimeError(f"Arquivo mantido mudou após o dry-run: {item.kept_file}")
            shutil.move(str(source), str(destination))
            moved.append((source, destination))

            if item.changes_current:
                con.execute(
                    """
                    UPDATE ocr_results
                    SET is_current = 0, updated_at = datetime('now')
                    WHERE volume_id = ? AND page_num = ? AND is_current = 1
                    """,
                    (item.volume_id, item.page_num),
                )
                if item.kept_result_id is None:
                    if reconciliation_engine_id is None:
                        raise RuntimeError("engine de reconciliação não inicializada")
                    source_row = con.execute(
                        """
                        SELECT image_path, image_hash
                        FROM ocr_results
                        WHERE id = ? AND volume_id = ? AND page_num = ?
                        """,
                        (item.current_result_id, item.volume_id, item.page_num),
                    ).fetchone()
                    if source_row is None:
                        raise RuntimeError(
                            f"{item.volume_id}:{item.page_num}: versão atual original desapareceu"
                        )
                    content = Path(item.kept_file).read_text(encoding="utf-8")
                    meta_json = json.dumps(
                        {
                            "repair": "canonical_file_import_v1",
                            "source_file": item.kept_file,
                            "replaced_current_result_id": item.current_result_id,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    cur = con.execute(
                        """
                        INSERT INTO ocr_results (
                            volume_id, page_num, image_path, image_hash,
                            engine_version_id, text_content, text_hash, is_current,
                            reprocess_reason, meta_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            item.volume_id,
                            item.page_num,
                            source_row["image_path"],
                            source_row["image_hash"],
                            reconciliation_engine_id,
                            content,
                            item.kept_hash,
                            "canonical_filesystem_reconciliation",
                            meta_json,
                        ),
                    )
                    registered_results.append(
                        {
                            "volume_id": item.volume_id,
                            "page_num": item.page_num,
                            "result_id": int(cur.lastrowid),
                        }
                    )
                else:
                    changed = con.execute(
                        """
                        UPDATE ocr_results
                        SET is_current = 1, updated_at = datetime('now')
                        WHERE id = ? AND volume_id = ? AND page_num = ?
                        """,
                        (item.kept_result_id, item.volume_id, item.page_num),
                    ).rowcount
                    if changed != 1:
                        raise RuntimeError(
                            f"{item.volume_id}:{item.page_num}: não foi possível promover OCR {item.kept_result_id}"
                        )
        con.commit()
    except BaseException:
        con.rollback()
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                shutil.move(str(destination), str(source))
        raise
    finally:
        con.close()

    payload["status"] = "complete"
    payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    payload["registered_results"] = registered_results
    _write_json(manifest, payload)
    pending_manifest.unlink()
    return manifest


def summarize(plan: Sequence[RepairItem]) -> None:
    by_volume: dict[str, tuple[int, int, int]] = {}
    for item in plan:
        total, flips, registrations = by_volume.get(item.volume_id, (0, 0, 0))
        by_volume[item.volume_id] = (
            total + 1,
            flips + int(item.changes_current),
            registrations + int(item.registers_canonical),
        )
    for volume_id, (total, flips, registrations) in sorted(by_volume.items()):
        print(
            f"{volume_id}: duplicatas={total} current_a_promover={flips} "
            f"canonicos_a_registrar={registrations}"
        )
    print(
        f"TOTAL: duplicatas={len(plan)} "
        f"current_a_promover={sum(item.changes_current for item in plan)} "
        f"canonicos_a_registrar={sum(item.registers_canonical for item in plan)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volumes", nargs="+", help="IDs exatos dos volumes")
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--quarantine-root", type=Path, default=DEFAULT_QUARANTINE)
    parser.add_argument(
        "--register-missing-canonical",
        action="store_true",
        help="registra no histórico hashes canônicos ausentes antes de promovê-los",
    )
    parser.add_argument("--apply", action="store_true", help="aplica; sem isso é dry-run")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = build_plan(
        base_dir=args.base_dir,
        db_path=args.db,
        volumes=args.volumes,
        register_missing_canonical=args.register_missing_canonical,
    )
    summarize(plan)
    if not args.apply:
        print("DRY-RUN: nenhum arquivo ou registro foi alterado")
        return 0
    manifest = apply_plan(
        plan=plan,
        db_path=args.db,
        quarantine_root=args.quarantine_root,
    )
    print(f"APLICADO: manifesto recuperável em {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
