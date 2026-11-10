"""
Sanitized Search Consolidation with Multiprocessing Pool (20 workers).

Builds a sanitized search catalog from a source baseline database:
1. Parallelized across volumes using multiprocessing.Pool(workers=20).
2. Deconcatenates multi-headword blocks using deconcatenated_index_parser (Migne & canon rules).
3. Detects and records index section hierarchies (e.g., 'AUCTORES VETERES' -> 'ARISTOTELES').
4. Deduplicates clone records (e.g., 94 identical author rows collapsed into 1 lemma
   with aggregated candidates).
5. Filters out pure numerical/punctuation noise (e.g., label='115').
6. Propagates 'ibid.' context to ensure referential integrity.
7. Aggregates per-volume partitions and builds clean FTS5 index including sections.

Safety: Reads source databases and OCR files strictly in read-only mode (mode=ro / ro=1).
Writes only to the target staging directory. Baseline search.sqlite is preserved untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.indexing.candidate_search import normalize
from tools.indexing.deconcatenated_index_parser import (
    deconcatenate_text_block,
    ExtractedEntry,
    parse_migne_columns,
)
from tools.indexing.experiment_storage import pack_json, register_json_storage


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_pure_noise_label(label: str) -> bool:
    """Check if label is pure numbers, punctuation or whitespace artifact."""
    if not label:
        return True
    s = label.strip()
    if len(s) <= 3 and not re.search(r"[a-zA-Z\u0370-\u03ff\u1f00-\u1fff]", s):
        return True
    if re.fullmatch(r"\d{1,4}[.,;]?", s):
        return True
    letters = re.findall(r"[a-zA-Z\u0370-\u03ff\u1f00-\u1fff]", s)
    if len(letters) < 2:
        return True
    return False


def clean_single_entry_text(text: str) -> str:
    """Clean linebreaks, hyphenation, leading punctuation artifacts, and stray leading numbers."""
    cleaned = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    cleaned = re.sub(r"\s*\n\s*", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Strip leading punctuation artifacts (e.g. '; 617 et seqq.' or ', ')
    cleaned = re.sub(r"^[,.;:!?—–-]+\s*", "", cleaned).strip()
    # Strip stray leading reference numbers if followed by letters (e.g. '617 et seqq. Ἀριστοτέλης')
    cleaned = re.sub(r"^(?:\d{1,4}(?:\s*et\s+seqq?|\s*[A-F])?(?:\s*,\s*\d{1,4})*(?:\s*,\s*etc\.?)?[.,;]?\s+)", "", cleaned)
    return cleaned.strip()


def init_partition_db(part_path: Path) -> sqlite3.Connection:
    if part_path.exists():
        part_path.unlink()
    conn = sqlite3.connect(str(part_path))
    register_json_storage(conn)
    conn.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;

        CREATE TABLE items(
            id TEXT PRIMARY KEY,
            group_id TEXT,
            volume TEXT,
            section TEXT,
            label TEXT,
            ref_raw TEXT,
            source TEXT,
            state TEXT,
            metadata_json TEXT
        );

        CREATE TABLE candidate_storage(
            item_id TEXT,
            file TEXT,
            rank INTEGER,
            score REAL,
            navigable INTEGER,
            candidate_json BLOB,
            PRIMARY KEY(item_id, file)
        ) WITHOUT ROWID;

        CREATE TABLE candidate_origins(
            item_id TEXT,
            file TEXT,
            origin TEXT,
            PRIMARY KEY(item_id, file, origin)
        ) WITHOUT ROWID;

        CREATE TABLE item_origins(
            item_id TEXT,
            origin TEXT,
            role TEXT,
            PRIMARY KEY(item_id, origin)
        ) WITHOUT ROWID;
    """)
    return conn


def process_volume_partition(args: tuple[str, str, str]) -> dict[str, Any]:
    """Worker task: sanitize items for a single volume (read-only on source)."""
    source_db_uri, part_db_path_str, volume = args
    part_path = Path(part_db_path_str)
    part_path.parent.mkdir(parents=True, exist_ok=True)

    stats: Counter = Counter()
    stats["volume"] = volume

    src_conn = sqlite3.connect(source_db_uri, uri=True)
    src_conn.row_factory = sqlite3.Row
    register_json_storage(src_conn)

    dest_conn = init_partition_db(part_path)

    # Fetch items for this volume
    src_items = src_conn.execute(
        "SELECT * FROM items WHERE volume=? ORDER BY source, id", (volume,)
    ).fetchall()
    stats["source_items"] = len(src_items)

    if not src_items:
        src_conn.close()
        dest_conn.close()
        return dict(stats)

    # Group by source page
    page_groups = defaultdict(list)
    for it in src_items:
        page_groups[it["source"]].append(it)

    for source_file, items_in_page in page_groups.items():
        stats["pages_processed"] += 1

        label_occurrences = defaultdict(list)
        for it in items_in_page:
            label_occurrences[it["label"].strip()].append(it)

        inherited_context: dict[str, Any] = {
            "last_columns": [],
            "last_tome": None,
            "current_section": None,
        }

        for label_text, matching_items in label_occurrences.items():
            if is_pure_noise_label(label_text):
                stats["filtered_noise_items"] += len(matching_items)
                continue

            # Case A: Massive Clones (> 2 identical rows for the same long label)
            if len(matching_items) >= 2 and len(label_text) > 80:
                stats["clone_clusters"] += 1
                stats["clones_collapsed"] += len(matching_items) - 1

                first_item = matching_items[0]
                if "\n" in label_text or ";" in label_text:
                    sub_entries = deconcatenate_text_block(
                        label_text,
                        source_file=source_file,
                        inherited_context=inherited_context,
                    )
                else:
                    sub_entries = [
                        ExtractedEntry(
                            lemma=clean_single_entry_text(label_text.split(",")[0]),
                            refs_raw=", ".join(it["ref_raw"] for it in matching_items if it["ref_raw"]),
                            full_text=label_text,
                            line_number=1,
                            source_file=source_file,
                            inferred_columns=parse_migne_columns(label_text),
                            section=inherited_context.get("current_section"),
                        )
                    ]

                aggregated_candidates = []
                for it in matching_items:
                    cands = src_conn.execute(
                        "SELECT * FROM candidate_storage WHERE item_id=?", (it["id"],)
                    ).fetchall()
                    aggregated_candidates.extend(cands)

                for sub in sub_entries:
                    if is_pure_noise_label(sub.lemma):
                        stats["filtered_noise_items"] += 1
                        continue
                    new_id = digest(f"{volume}:{source_file}:{sub.lemma}")
                    clean_refs = sub.refs_raw or ", ".join(
                        it["ref_raw"] for it in matching_items if it["ref_raw"]
                    )
                    state = "with_destinations" if aggregated_candidates else "source_only"
                    meta = {
                        "section": sub.section,
                        "sanitized_from_clones": len(matching_items),
                        "original_sample_id": first_item["id"],
                        "columns": sub.inferred_columns,
                        "inherited_ibid": sub.inherited_ibid,
                    }

                    dest_conn.execute(
                        "INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                        (new_id, first_item["group_id"], volume, sub.section, sub.lemma, clean_refs, source_file, state, json.dumps(meta)),
                    )
                    stats["new_items_inserted"] += 1

                    for cand in aggregated_candidates:
                        dest_conn.execute(
                            "INSERT OR IGNORE INTO candidate_storage VALUES(?,?,?,?,?,?)",
                            (new_id, cand["file"], cand["rank"], cand["score"], cand["navigable"], cand["candidate_json"]),
                        )
                continue

            # Case B: Megablock or multi-entry item
            item = matching_items[0]
            has_inline = bool(re.search(r"\b\d{1,4}(?:\s*,\s*[a-f])?[.;]\s+[A-Za-z\u0370-\u03ff\u1f00-\u1fff]", label_text))
            if ("\n" in label_text and len(label_text) > 80) or len(label_text) > 150 or (has_inline and len(label_text) > 50):
                sub_entries = deconcatenate_text_block(
                    label_text,
                    source_file=source_file,
                    inherited_context=inherited_context,
                )
                if len(sub_entries) > 1:
                    stats["megablocks_deconcatenated"] += 1
                    stats["entries_from_megablocks"] += len(sub_entries)

                    cands = src_conn.execute(
                        "SELECT * FROM candidate_storage WHERE item_id=?", (item["id"],)
                    ).fetchall()

                    for sub in sub_entries:
                        clean_sub_lemma = clean_single_entry_text(sub.lemma)
                        if is_pure_noise_label(clean_sub_lemma):
                            stats["filtered_noise_items"] += 1
                            continue
                        sub_id = digest(f"{volume}:{source_file}:{clean_sub_lemma}:{sub.line_number}")
                        state = "with_destinations" if cands else "source_only"
                        meta = {
                            "section": sub.section,
                            "deconcatenated_from": item["id"],
                            "columns": sub.inferred_columns,
                            "inherited_ibid": sub.inherited_ibid,
                        }
                        dest_conn.execute(
                            "INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                            (sub_id, item["group_id"], volume, sub.section, clean_sub_lemma, sub.refs_raw or item["ref_raw"], source_file, state, json.dumps(meta)),
                        )
                        stats["new_items_inserted"] += 1

                        for cand in cands:
                            dest_conn.execute(
                                "INSERT OR IGNORE INTO candidate_storage VALUES(?,?,?,?,?,?)",
                                (sub_id, cand["file"], cand["rank"], cand["score"], cand["navigable"], cand["candidate_json"]),
                            )
                    continue

            # Case C: Clean normal entry
            clean_lemma = clean_single_entry_text(label_text)
            sec = inherited_context.get("current_section")
            existing_meta = {}
            if item["metadata_json"]:
                try:
                    existing_meta = json.loads(item["metadata_json"])
                except Exception:
                    pass
            if sec:
                existing_meta["section"] = sec

            dest_conn.execute(
                "INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                (item["id"], item["group_id"], volume, sec, clean_lemma, item["ref_raw"], source_file, item["state"], json.dumps(existing_meta) if existing_meta else None),
            )
            stats["new_items_inserted"] += 1

            cands = src_conn.execute(
                "SELECT * FROM candidate_storage WHERE item_id=?", (item["id"],)
            ).fetchall()
            for cand in cands:
                dest_conn.execute(
                    "INSERT OR IGNORE INTO candidate_storage VALUES(?,?,?,?,?,?)",
                    (item["id"], cand["file"], cand["rank"], cand["score"], cand["navigable"], cand["candidate_json"]),
                )

    dest_conn.commit()
    src_conn.close()
    dest_conn.close()

    return dict(stats)


def master_merge_and_index(
    master_db_path: Path,
    part_paths: list[Path],
    source_db_path: Path,
    filter_volumes: list[str] | None,
    total_stats: Counter,
) -> None:
    """Merge partition SQLite databases into master DB and build FTS5 index."""
    if master_db_path.exists():
        master_db_path.unlink()

    conn = sqlite3.connect(str(master_db_path))
    register_json_storage(conn)
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA cache_size=-64000;

        CREATE TABLE items(
            id TEXT PRIMARY KEY,
            group_id TEXT,
            volume TEXT,
            section TEXT,
            label TEXT,
            ref_raw TEXT,
            source TEXT,
            state TEXT,
            metadata_json TEXT
        );

        CREATE TABLE candidate_storage(
            item_id TEXT,
            file TEXT,
            rank INTEGER,
            score REAL,
            navigable INTEGER,
            candidate_json BLOB,
            PRIMARY KEY(item_id, file)
        ) WITHOUT ROWID;

        CREATE VIEW candidates AS
            SELECT item_id, file, rank, score, navigable, experiment_json(candidate_json) AS candidate_json
            FROM candidate_storage;

        CREATE TABLE candidate_origins(
            item_id TEXT,
            file TEXT,
            origin TEXT,
            PRIMARY KEY(item_id, file, origin)
        ) WITHOUT ROWID;

        CREATE TABLE item_origins(
            item_id TEXT,
            origin TEXT,
            role TEXT,
            PRIMARY KEY(item_id, origin)
        ) WITHOUT ROWID;

        CREATE TABLE metadata(
            key TEXT PRIMARY KEY,
            json TEXT
        );
    """)

    print(f"Merging {len(part_paths)} partition files into {master_db_path}...", flush=True)
    for idx, part in enumerate(part_paths, 1):
        if not part.exists():
            continue
        conn.execute(f"ATTACH DATABASE ? AS part_{idx}", (str(part),))
        conn.execute(f"INSERT OR IGNORE INTO items SELECT * FROM part_{idx}.items")
        conn.execute(f"INSERT OR IGNORE INTO candidate_storage SELECT * FROM part_{idx}.candidate_storage")
        conn.commit()
        conn.execute(f"DETACH DATABASE part_{idx}")
        if idx % 10 == 0 or idx == len(part_paths):
            print(f"  Merged {idx}/{len(part_paths)} partitions...", flush=True)

    print("Building items_fts index (unicode61)...", flush=True)
    conn.execute("""
        CREATE VIRTUAL TABLE items_fts USING fts5(
            label,
            section,
            ref_raw,
            volume,
            tokenize='unicode61 remove_diacritics 2'
        );
    """)

    conn.execute("""
        INSERT INTO items_fts(rowid, label, section, ref_raw, volume)
        SELECT rowid, label, coalesce(section, ''), coalesce(ref_raw, ''), volume FROM items;
    """)
    conn.commit()

    final_items = conn.execute("SELECT count(*) FROM items").fetchone()[0]
    final_fts = conn.execute("SELECT count(*) FROM items_fts").fetchone()[0]
    final_cands = conn.execute("SELECT count(*) FROM candidate_storage").fetchone()[0]

    total_stats["final_items_count"] = final_items
    total_stats["final_fts_count"] = final_fts
    total_stats["final_candidate_links"] = final_cands

    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_db": str(source_db_path),
        "target_db": str(master_db_path),
        "volumes_filtered": filter_volumes,
        "stats": dict(total_stats),
    }
    conn.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", ("sanitization_summary", json.dumps(summary)))
    conn.commit()
    conn.close()


def sanitize_catalog_multiprocess(
    source_db_path: Path,
    target_db_path: Path,
    filter_volumes: list[str] | None = None,
    workers: int = 20,
) -> dict[str, Any]:
    t0 = time.time()
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    parts_dir = target_db_path.parent / "partitions"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Discover volumes from source DB (read-only)
    source_uri = f"file:{source_db_path.resolve()}?mode=ro"
    src_conn = sqlite3.connect(source_uri, uri=True)
    if filter_volumes:
        volumes = filter_volumes
    else:
        rows = src_conn.execute("SELECT DISTINCT volume FROM items ORDER BY volume").fetchall()
        volumes = [r[0] for r in rows]
    src_conn.close()

    print(f"Sanitizing {len(volumes)} volumes using {workers} worker processes...", flush=True)
    print(f"Source DB: {source_db_path} (mode=ro)", flush=True)
    print(f"Target DB: {target_db_path}", flush=True)

    worker_args = []
    part_paths = []
    for vol in volumes:
        part_file = parts_dir / f"part_{vol}.sqlite"
        part_paths.append(part_file)
        worker_args.append((source_uri, str(part_file), vol))

    total_stats: Counter = Counter()

    # 2. Run workers in parallel
    with multiprocessing.Pool(processes=workers) as pool:
        for idx, res in enumerate(pool.imap_unordered(process_volume_partition, worker_args), 1):
            vol = res.get("volume", "")
            for k, v in res.items():
                if isinstance(v, (int, float)):
                    total_stats[k] += v
            if idx % 10 == 0 or idx == len(volumes):
                print(
                    f"[{idx}/{len(volumes)}] Done {vol} - "
                    f"Total processed: {total_stats['source_items']:,} items, "
                    f"collapsed: {total_stats['clones_collapsed']:,}, "
                    f"deconcatenated: {total_stats['megablocks_deconcatenated']:,}",
                    flush=True,
                )

    # 3. Master merge and FTS5 creation
    master_merge_and_index(
        master_db_path=target_db_path,
        part_paths=part_paths,
        source_db_path=source_db_path,
        filter_volumes=filter_volumes,
        total_stats=total_stats,
    )

    # Clean up temporary partition files
    shutil.rmtree(parts_dir, ignore_errors=True)

    elapsed = time.time() - t0
    total_stats["elapsed_seconds"] = round(elapsed, 2)
    print(f"\nCompleted in {elapsed:.2f}s ({elapsed/60:.2f}m)", flush=True)

    return dict(total_stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multiprocess Sanitized Search Consolidation (20 workers).")
    parser.add_argument("--source", type=Path, default=Path("/homessddata/Projects/pdfocr/data/index_catalog/consolidated_search_20261106_001/search.sqlite"))
    parser.add_argument("--target", type=Path, default=Path("/homessddata/Projects/pdfocr/data/index_catalog/test_sanitized_20261110/search.sqlite"))
    parser.add_argument("--volumes", nargs="*", default=None, help="Optional subset of volumes to process.")
    parser.add_argument("--workers", type=int, default=20, help="Number of concurrent worker processes (default: 20).")
    args = parser.parse_args()

    results = sanitize_catalog_multiprocess(
        source_db_path=args.source,
        target_db_path=args.target,
        filter_volumes=args.volumes,
        workers=args.workers,
    )

    print("\n" + "=" * 60, flush=True)
    print("SANITIZATION SUMMARY:", flush=True)
    for k, v in sorted(results.items()):
        print(f"  {k}: {v}", flush=True)
    print("=" * 60, flush=True)
