#!/usr/bin/env python3
"""Reingere crops de linha no mesmo DB, recalculando `lines.line_image` in-place.

Uso:
  python tools/reingest_line_images.py --db ocr.db --dry-run
  python tools/reingest_line_images.py --db ocr.db --preprocess-mode adaptive_soft --limit 200
  python tools/reingest_line_images.py --db ocr.db --volume PG004

O comando:
  - lê `image_path` e `bbox` de cada linha
  - reaplica o pré-processamento escolhido na página inteira
  - recorta novamente a linha
  - grava o novo PNG em `lines.line_image`
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from pathlib import Path

import cv2
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sample import preprocess_image


def connect_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_crop(image_path: str, bbox_json: str, preprocess_mode: str) -> bytes | None:
    img = cv2.imread(image_path)
    if img is None:
        return None
    processed, _ = preprocess_image(img, mode=preprocess_mode)
    try:
        bbox = json.loads(bbox_json) if isinstance(bbox_json, str) else bbox_json
        x = int(bbox.get("x", 0))
        y = int(bbox.get("y", 0))
        w = int(bbox.get("w", 0))
        h = int(bbox.get("h", 0))
    except Exception:
        return None

    x1 = max(0, x - 2)
    y1 = max(0, y - 2)
    x2 = min(processed.shape[1], x + w + 2)
    y2 = min(processed.shape[0], y + h + 2)
    crop = processed[y1:y2, x1:x2]
    buf = io.BytesIO()
    Image.fromarray(crop).save(buf, format="PNG")
    return buf.getvalue()


def build_query(volume: str | None, limit: int | None) -> tuple[str, list]:
    query = """
        SELECT id, image_path, bbox, line_image
        FROM lines
        WHERE image_path IS NOT NULL
          AND image_path <> ''
          AND bbox IS NOT NULL
          AND bbox <> ''
    """
    params: list = []
    if volume:
        query += " AND volume = ?"
        params.append(volume)
    query += " ORDER BY id ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return query, params


def reingest(db: str, preprocess_mode: str, volume: str | None, limit: int | None, dry_run: bool) -> None:
    con = connect_db(db)
    query, params = build_query(volume, limit)
    rows = con.execute(query, params).fetchall()
    if not rows:
        print("[INFO] Nenhuma linha elegível.")
        con.close()
        return

    print(
        f"[INFO] Linhas elegíveis: {len(rows)} | mode={preprocess_mode} | volume={volume or 'all'}"
    )
    if dry_run:
        print("[DRY-RUN] Nenhuma alteração será escrita.")
        sample_ids = ", ".join(str(r["id"]) for r in rows[:20])
        print(f"[DRY-RUN] IDs amostrados: {sample_ids}")
        con.close()
        return

    updated = 0
    skipped = 0
    for row in rows:
        try:
            crop_bytes = load_crop(row["image_path"], row["bbox"], preprocess_mode)
            if crop_bytes is None:
                skipped += 1
                continue
            con.execute(
                "UPDATE lines SET line_image = ?, updated_at = datetime('now') WHERE id = ?",
                (crop_bytes, row["id"]),
            )
            updated += 1
        except Exception as e:
            skipped += 1
            print(f"[WARN] id={row['id']}: {e}")

    con.commit()
    con.close()
    print(f"[DONE] updated={updated} skipped={skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="ocr.db", help="Caminho do SQLite")
    parser.add_argument(
        "--preprocess-mode",
        choices=["adaptive_soft", "sample"],
        default="adaptive_soft",
        help="Receita de pré-processamento",
    )
    parser.add_argument("--volume", help="Filtra por volume, ex.: PG004")
    parser.add_argument("--limit", type=int, help="Limita o número de linhas")
    parser.add_argument("--dry-run", action="store_true", help="Não grava nada")
    args = parser.parse_args()
    reingest(args.db, args.preprocess_mode, args.volume, args.limit, args.dry_run)


if __name__ == "__main__":
    main()
