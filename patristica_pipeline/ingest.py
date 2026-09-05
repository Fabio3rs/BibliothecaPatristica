from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .common import (
    now_iso,
    normalize_text,
    page_number,
    page_sort_key,
    parse_volume_info,
    split_long_block,
    is_header,
)
from .db import connect_db

# Importação opcional — o ingest funciona sem ocr_versions_db
try:
    import ocr_versions_db as _vdb
except ImportError:
    _vdb = None  # type: ignore[assignment]


def build_blocks(page_rows: Sequence[Dict[str, object]]) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    for row in page_rows:
        page_id = str(row["page_id"])
        text = str(row["text_norm"])
        if not text:
            continue
        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        for part in parts:
            blocks.append({"page_id": page_id, "text": part})
    return blocks


def chunk_blocks(
    blocks: Sequence[Dict[str, str]],
    min_chars: int,
    max_chars: int,
    hard_max_chars: int,
) -> List[List[Dict[str, str]]]:
    chunks: List[List[Dict[str, str]]] = []
    cur: List[Dict[str, str]] = []
    cur_len = 0

    for block in blocks:
        text = block["text"]

        if len(text) > hard_max_chars:
            for sub in split_long_block(text, hard_max_chars):
                sub_block = {"page_id": block["page_id"], "text": sub}
                if is_header(sub) and cur_len >= min_chars:
                    if cur:
                        chunks.append(cur)
                    cur = []
                    cur_len = 0
                if cur_len + len(sub) + 2 > max_chars and cur_len >= min_chars:
                    if cur:
                        chunks.append(cur)
                    cur = []
                    cur_len = 0
                cur.append(sub_block)
                cur_len += len(sub) + 2
            continue

        if is_header(text) and cur_len >= min_chars:
            if cur:
                chunks.append(cur)
            cur = []
            cur_len = 0

        if cur_len + len(text) + 2 > max_chars and cur_len >= min_chars:
            if cur:
                chunks.append(cur)
            cur = []
            cur_len = 0

        cur.append(block)
        cur_len += len(text) + 2

    if cur:
        chunks.append(cur)
    return chunks


def list_volume_dirs(root: Path, series_filter: Optional[Sequence[str]], limit: Optional[int]) -> List[Path]:
    wanted = {s.upper().strip() for s in series_filter} if series_filter else None
    out: List[Path] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        info = parse_volume_info(d)
        if not info:
            continue
        if wanted and info.series not in wanted:
            continue
        out.append(d)
    out.sort(key=lambda p: (parse_volume_info(p).series, parse_volume_info(p).number, parse_volume_info(p).suffix))
    return out[:limit] if limit else out


def ingest_ocr_to_text_db(
    root: Path,
    text_db: Path,
    series_filter: Optional[Sequence[str]],
    min_chars: int,
    max_chars: int,
    hard_max_chars: int,
    limit: Optional[int],
    versions_db: Optional[Path] = None,
) -> None:
    volume_dirs = list_volume_dirs(root, series_filter, limit)
    total_pages = 0
    total_chunks = 0

    # Abrir conexão read-only com ocr_versions.db (se disponível)
    _ver_con = None
    if _vdb is not None:
        vdb_path = versions_db or _vdb.DEFAULT_DB_PATH
        if vdb_path.exists():
            try:
                _ver_con = _vdb.open_versions_db(vdb_path)
            except Exception:
                pass  # silencioso — ingest funciona sem

    with connect_db(text_db) as con:
        for volume_dir in volume_dirs:
            info = parse_volume_info(volume_dir)
            if not info:
                continue
            text_dir = volume_dir / "text"
            if not text_dir.exists():
                continue

            pages = [p for p in text_dir.glob("*.txt") if p.name != "texto_extraido.txt"]
            if not pages:
                continue
            pages.sort(key=page_sort_key)

            ts = now_iso()
            con.execute(
                """
                INSERT INTO volumes(volume_id, series, volume_number, volume_suffix, source_dir, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(volume_id) DO UPDATE SET
                    series=excluded.series,
                    volume_number=excluded.volume_number,
                    volume_suffix=excluded.volume_suffix,
                    source_dir=excluded.source_dir,
                    updated_at=excluded.updated_at
                """,
                (info.volume_id, info.series, info.number, info.suffix, info.source_dir, ts),
            )

            # Reindex the full volume atomically.
            con.execute("DELETE FROM chunks WHERE volume_id = ?", (info.volume_id,))
            con.execute("DELETE FROM pages WHERE volume_id = ?", (info.volume_id,))

            page_rows: List[Dict[str, object]] = []
            for page_path in pages:
                raw = page_path.read_text(encoding="utf-8", errors="ignore")
                norm = normalize_text(raw)
                pid = page_path.name
                pnum = page_number(page_path)

                # Consultar ocr_result_id corrente (se ocr_versions.db disponível)
                ocr_result_id = None
                if _ver_con is not None and pnum is not None:
                    try:
                        cur_ver = _vdb.get_current_result(_ver_con, info.volume_id, pnum)
                        if cur_ver is not None:
                            ocr_result_id = cur_ver["id"]
                    except Exception:
                        pass

                cur = con.execute(
                    """
                    INSERT INTO pages(page_id, volume_id, page_num, file_name, file_path,
                                      text_raw, text_norm, char_count, ocr_result_id, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        info.volume_id,
                        pnum,
                        page_path.name,
                        str(page_path.resolve()),
                        raw,
                        norm,
                        len(norm),
                        ocr_result_id,
                        ts,
                    ),
                )
                page_rows.append({"page_id": pid, "text_norm": norm, "page_num": pnum, "db_id": cur.lastrowid})
                total_pages += 1

            blocks = build_blocks(page_rows)
            chunks = chunk_blocks(blocks, min_chars=min_chars, max_chars=max_chars, hard_max_chars=hard_max_chars)

            for idx, chunk in enumerate(chunks, start=1):
                chunk_id = f"{info.volume_id}::C{idx:04d}"
                chunk_text = "\n\n".join(b["text"] for b in chunk).strip()
                con.execute(
                    """
                    INSERT INTO chunks(chunk_id, volume_id, chunk_seq, text_norm, char_count, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (chunk_id, info.volume_id, idx, chunk_text, len(chunk_text), ts),
                )

                page_ids_ordered: List[str] = []
                seen = set()
                for b in chunk:
                    pid = b["page_id"]
                    if pid not in seen:
                        seen.add(pid)
                        page_ids_ordered.append(pid)

                for order, pid in enumerate(page_ids_ordered, start=1):
                    con.execute(
                        "INSERT INTO chunk_pages(chunk_id, page_id, page_order) VALUES(?, ?, ?)",
                        (chunk_id, pid, order),
                    )
                total_chunks += 1

            con.commit()

    if _ver_con is not None:
        _ver_con.close()

    print(
        f"[OK] OCR ingest complete. volumes={len(volume_dirs)} pages={total_pages} chunks={total_chunks} db={text_db}"
    )
