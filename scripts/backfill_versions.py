#!/usr/bin/env python3
"""Backfill: popula ``ocr_versions.db`` a partir dos ``.txt`` já existentes em disco.

Uso:
    python scripts/backfill_versions.py [--base-dir teste/] [--db data/ocr_versions.db] [--dry-run] [--procs N]

v4 — Pipeline multi-processo:
  - N workers computam SHA256 de imagens + lêem .txt (CPU+IO bound, paralelo)
  - 1 writer thread drena a fila e faz batch INSERT no SQLite (single-writer, sem contenção)
  - Workers enviam tuplas prontas para a fila; writer faz batch de BATCH_SIZE
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_versions_db as vdb

BATCH_SIZE = 5000
SENTINEL = None  # sinaliza fim de dados


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_page_num(txt_path: Path) -> int | None:
    m = re.search(r"-([0-9]{1,4})$", txt_path.stem)
    return int(m.group(1)) if m else None


def find_image_for_txt(txt_path: Path, images_dir: Path) -> Path | None:
    page_num = parse_page_num(txt_path)
    if page_num is None:
        return None
    volume = txt_path.parent.parent.name
    stable = images_dir / f"{volume}-{page_num:03d}.png"
    if stable.exists():
        return stable
    candidates = sorted(images_dir.glob(f"*-{page_num:03d}.png"))
    if candidates:
        return candidates[0]
    candidates = sorted(images_dir.glob(f"*-{page_num}.png"))
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

LEGACY_SYSTEM_PROMPT = ""
LEGACY_ENGINE = "unknown"
LEGACY_PROMPT_KEY = "legacy_unknown"


# ---------------------------------------------------------------------------
# Worker: computa hashes e envia tuplas para a fila
# ---------------------------------------------------------------------------


def _compute_worker(
    volume_dirs: list[str],
    data_queue: mp.Queue,
    progress_queue: mp.Queue,
    worker_id: int,
) -> None:
    """Cada worker calcula hashes e enfileira tuplas prontas para INSERT.

    Envia apenas metadados + hashes (sem text_content) para evitar
    serializar dezenas de MB pelo pipe IPC. O writer lê o texto do disco.
    """
    image_hash_cache: dict[str, str] = {}
    processed = 0
    errors = 0

    for vol_path_str in volume_dirs:
        vol_dir = Path(vol_path_str)
        text_dir = vol_dir / "text"
        images_dir = vol_dir / "images"

        if not text_dir.is_dir():
            continue

        txt_files = sorted(text_dir.glob("*.txt"))
        if not txt_files:
            continue

        volume_id = vol_dir.name

        for txt_path in txt_files:
            try:
                page_num = parse_page_num(txt_path)
                if page_num is None:
                    errors += 1
                    continue

                text_content = txt_path.read_text(encoding="utf-8", errors="replace")
                text_hash = vdb.sha256_str(text_content)

                img_path = find_image_for_txt(txt_path, images_dir) if images_dir.is_dir() else None

                if img_path is not None:
                    img_key = str(img_path)
                    if img_key in image_hash_cache:
                        image_hash = image_hash_cache[img_key]
                    else:
                        image_hash = vdb.sha256_file(img_path)
                        image_hash_cache[img_key] = image_hash
                    image_path_str = img_key
                else:
                    image_hash = vdb.sha256_str(f"synthetic:{volume_id}:{page_num}")
                    image_path_str = f"{volume_id}/images/{volume_id}-{page_num:03d}.png"

                # Envia metadados + hashes + path do txt (sem o conteúdo!)
                data_queue.put((volume_id, page_num, image_path_str, image_hash,
                                str(txt_path), text_hash))
                processed += 1

            except Exception as e:
                print(f"  [W{worker_id}][ERROR] {txt_path.name}: {e}", flush=True)
                errors += 1

        progress_queue.put(("vol_done", worker_id, volume_id, processed, errors))

    # Sinalizar que este worker terminou
    data_queue.put(SENTINEL)


# ---------------------------------------------------------------------------
# Writer: single-thread, drena a fila e faz batch INSERT
# ---------------------------------------------------------------------------


def _writer_thread(
    data_queue: mp.Queue,
    db_path: Path,
    n_workers: int,
    stats: dict,
) -> None:
    """Thread única que lê dados da fila e insere no SQLite.

    Usa temp table + SQL puro para fazer idempotência, desativação e INSERT
    em bloco — evitando o round-trip Python↔SQLite por registro.
    """
    con = vdb.open_versions_db(db_path)

    ev_id = vdb.get_or_create_engine_version(
        con,
        engine=LEGACY_ENGINE,
        model=None,
        prompt_key=LEGACY_PROMPT_KEY,
        system_prompt=LEGACY_SYSTEM_PROMPT,
    )

    # Temp table para staging de cada batch
    con.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _staging (
            volume_id  TEXT,
            page_num   INTEGER,
            image_path TEXT,
            image_hash TEXT,
            txt_path   TEXT,
            text_hash  TEXT
        )
    """)

    registered = 0
    skipped = 0
    sentinels_seen = 0
    batch: list[tuple] = []

    def flush():
        nonlocal registered, skipped
        if not batch:
            return
        batch_size = len(batch)

        con.execute("DELETE FROM _staging")
        con.executemany(
            "INSERT INTO _staging VALUES (?,?,?,?,?,?)",
            batch,
        )

        # Filtrar os que já existem (idempotência) — pega só os novos
        new_rows_meta = con.execute(f"""
            SELECT s.volume_id, s.page_num, s.image_path, s.image_hash,
                   s.txt_path, s.text_hash
            FROM _staging s
            WHERE NOT EXISTS (
                SELECT 1 FROM ocr_results r
                WHERE r.image_hash = s.image_hash
                  AND r.engine_version_id = {ev_id}
                  AND r.text_hash = s.text_hash
            )
        """).fetchall()

        if new_rows_meta:
            # Desativar versões anteriores apenas das páginas que vamos inserir
            vol_page_pairs = [(r[0], r[1]) for r in new_rows_meta]
            con.executemany(
                "UPDATE ocr_results SET is_current=0, updated_at=datetime('now') "
                "WHERE volume_id=? AND page_num=? AND is_current=1",
                vol_page_pairs,
            )

            # Ler text_content do disco e inserir (textos no page cache do OS)
            insert_rows = []
            for vol_id, page_num, img_path, img_hash, txt_p, txt_hash in new_rows_meta:
                text_content = Path(txt_p).read_text(encoding="utf-8", errors="replace")
                insert_rows.append((
                    vol_id, page_num, img_path, img_hash,
                    ev_id, text_content, txt_hash, 1, "legacy_unknown",
                ))

            con.executemany(
                """INSERT INTO ocr_results
                   (volume_id, page_num, image_path, image_hash, engine_version_id,
                    text_content, text_hash, is_current, reprocess_reason)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                insert_rows,
            )

        con.commit()

        registered += len(new_rows_meta)
        skipped += batch_size - len(new_rows_meta)
        batch.clear()

    while sentinels_seen < n_workers:
        try:
            item = data_queue.get(timeout=2.0)
        except Exception:
            continue

        if item is SENTINEL:
            sentinels_seen += 1
            continue

        batch.append(item)
        if len(batch) >= BATCH_SIZE:
            flush()
            stats["registered"] = registered
            stats["skipped"] = skipped

    # Flush final
    flush()
    con.execute("DROP TABLE IF EXISTS _staging")
    con.close()

    stats["registered"] = registered
    stats["skipped"] = skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill ocr_versions.db a partir dos .txt existentes em disco."
    )
    parser.add_argument("--base-dir", type=Path, default=Path("teste"))
    parser.add_argument("--db", type=Path, default=vdb.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--volumes", nargs="*")
    parser.add_argument("--procs", type=int, default=min(os.cpu_count() or 4, 16),
                        help="Número de workers de computação (default: min(cpu_count, 16))")
    args = parser.parse_args()

    base_dir: Path = args.base_dir
    if not base_dir.is_dir():
        print(f"Diretório base não encontrado: {base_dir}")
        sys.exit(1)

    if args.volumes:
        volume_dirs = [base_dir / v for v in args.volumes]
        volume_dirs = [d for d in volume_dirs if d.is_dir()]
    else:
        volume_dirs = sorted(
            d for d in base_dir.iterdir()
            if d.is_dir() and (d / "text").is_dir()
        )

    if not volume_dirs:
        print("Nenhum volume encontrado.")
        sys.exit(0)

    if args.dry_run:
        total = sum(len(list((d / "text").glob("*.txt"))) for d in volume_dirs)
        print(f"[DRY-RUN] {len(volume_dirs)} volumes, {total} arquivos .txt")
        return

    n_procs = min(args.procs, len(volume_dirs))

    # Garantir schema
    con0 = vdb.open_versions_db(args.db)
    con0.close()

    # Dividir volumes round-robin
    chunks: list[list[str]] = [[] for _ in range(n_procs)]
    for i, vol_dir in enumerate(volume_dirs):
        chunks[i % n_procs].append(str(vol_dir))

    print(f"Backfill: {len(volume_dirs)} volumes, {n_procs} compute workers + 1 writer")

    data_queue: mp.Queue = mp.Queue(maxsize=n_procs * BATCH_SIZE * 2)
    progress_queue: mp.Queue = mp.Queue()
    t0 = time.monotonic()

    # Lançar compute workers
    workers: list[mp.Process] = []
    for i, chunk in enumerate(chunks):
        p = mp.Process(target=_compute_worker, args=(chunk, data_queue, progress_queue, i))
        p.start()
        workers.append(p)

    # Lançar writer (thread no processo principal)
    writer_stats: dict = {"registered": 0, "skipped": 0}
    writer = threading.Thread(
        target=_writer_thread,
        args=(data_queue, args.db, n_procs, writer_stats),
        daemon=True,
    )
    writer.start()

    # Monitorar progresso
    vols_done = 0
    total_errors = 0
    last_print = time.monotonic()

    while writer.is_alive():
        try:
            while True:
                msg = progress_queue.get(timeout=1.0)
                if msg[0] == "vol_done":
                    _, wid, vol_id, proc, err = msg
                    vols_done += 1
                    total_errors = max(total_errors, err)  # approximation
        except Exception:
            pass

        now = time.monotonic()
        if now - last_print >= 10.0:
            elapsed = now - t0
            reg = writer_stats["registered"]
            skip = writer_stats["skipped"]
            rate = (reg + skip) / max(elapsed, 0.001)
            qsize = data_queue.qsize() if hasattr(data_queue, 'qsize') else -1
            print(
                f"  [{elapsed:.0f}s] vols={vols_done}/{len(volume_dirs)}  "
                f"reg={reg}  skip={skip}  "
                f"({rate:.0f} pgs/s)  queue={qsize}",
                flush=True,
            )
            last_print = now

    for p in workers:
        p.join()
    writer.join()

    elapsed = time.monotonic() - t0
    reg = writer_stats["registered"]
    skip = writer_stats["skipped"]
    rate = (reg + skip) / max(elapsed, 0.001)

    print(f"\n{'='*60}")
    print(f"Backfill concluído em {elapsed:.1f}s  ({n_procs} compute + 1 writer)")
    print(f"  Volumes:     {len(volume_dirs)}")
    print(f"  Registrados: {reg}")
    print(f"  Skipped:     {skip}")
    print(f"  Rate:        {rate:.0f} pgs/s")


if __name__ == "__main__":
    main()
