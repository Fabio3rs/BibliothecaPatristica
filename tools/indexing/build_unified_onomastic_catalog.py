"""Build the comprehensive, unified, reproducible onomastic catalog across all 407 volumes.

Integrates the sanitized 272-volume baseline with the remaining 135 volumes from
patristic_indices.db, applying lemma deconcatenation, section inheritance,
deterministic target resolution (file, page, Migne column), and prepares the
long-tail residual queue for Qwen3-Embedding-8B.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from tools.corpus_utils import page_files, PROJECT_ROOT
from tools.indexing.deconcatenated_index_parser import (
    split_lemma_and_refs,
    parse_migne_columns,
    RE_SECTION_HEADING,
)
from tools.indexing.sanitized_search_consolidation import (
    is_pure_noise_label,
    clean_single_entry_text,
    digest,
)

INSTRUCT_TASK = (
    "Retrieve passages relevant to a keyword from patristic and theological texts. "
    "The keyword may be a person, work, theological theme, philosophical concept, or technical term."
)

RE_REMISSION = re.compile(
    r"\b(?:vide|videlicet|v\.|confer|cf\.|idem|ibid|ibidem|supra|infra)\b",
    re.IGNORECASE,
)
RE_ILLEGIBLE = re.compile(
    r"\[\s*(?:\?|ileg[ií]vel|lacuna|unreadable)\s*\]|\?{2,}|—{3,}",
    re.IGNORECASE,
)
RE_EDITORIAL_REF = re.compile(
    r"\b(?:t\.|tom\.|vol\.|lib\.|cap\.|capp\.|can\.|canon|epist\.)\b",
    re.IGNORECASE,
)


def pack_json(val: Any) -> bytes:
    import zlib
    if isinstance(val, (dict, list)):
        s = json.dumps(val, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(val, str):
        s = val
    else:
        s = "{}"
    return zlib.compress(s.encode("utf-8"), level=6)


def unpack_json(b: bytes) -> dict:
    import zlib
    if not b:
        return {}
    try:
        return json.loads(zlib.decompress(b).decode("utf-8"))
    except Exception:
        return {}


def classify_residual_cause(label: str, ref_raw: str, section: str, volume: str) -> str:
    comb = f"{label} {ref_raw}".strip()
    if RE_REMISSION.search(comb):
        return "unresolved_remission"
    if RE_ILLEGIBLE.search(comb):
        return "illegible_ref"
    if RE_EDITORIAL_REF.search(ref_raw or ""):
        return "editorial_ambiguity"
    if not ref_raw or len(ref_raw.strip()) == 0:
        return "insufficient_evidence"
    if re.search(r"\d{1,4}", ref_raw):
        return "missing_column_map"
    if any(n in label.split() for n in ["Joannes", "Alexander", "Gregorius", "Petrus", "Paulus", "Augustinus"]):
        return "homonym_disambiguation"
    return "ocr_defect"


def load_volume_column_map(volume_id: str, est_db_path: Path) -> dict[int, str]:
    col_to_file: dict[int, str] = {}
    if not est_db_path.exists():
        return col_to_file
    conn = sqlite3.connect(f"file:{est_db_path}?mode=ro", uri=True)
    c = conn.cursor()
    c.execute(
        "SELECT file_path, observation_json FROM file_observation_cache WHERE volume_id=?",
        (volume_id,),
    )
    for path, obs_json in c.fetchall():
        try:
            obs = json.loads(obs_json)
            for pair in obs.get("header_pairs", []):
                for col in pair:
                    col_to_file[int(col)] = path
            for s in obs.get("header_singles", []):
                col_to_file[int(s)] = path
        except Exception:
            continue
    conn.close()
    return col_to_file


def build_unified_catalog(
    sanitized_source_db: Path,
    patristic_db: Path,
    estimator_db: Path,
    out_dir: Path,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    target_db = out_dir / "search.sqlite"
    if target_db.exists():
        target_db.unlink()

    print(f"Connecting to target DB: {target_db}")
    conn = sqlite3.connect(str(target_db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-100000")  # ~100MB cache

    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE items (
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

        CREATE TABLE candidate_storage (
            item_id TEXT,
            file TEXT,
            rank INTEGER,
            score REAL,
            navigable INTEGER,
            candidate_json BLOB,
            PRIMARY KEY(item_id, file)
        ) WITHOUT ROWID;

        CREATE TABLE residual_embedding_queue (
            queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL UNIQUE,
            volume TEXT NOT NULL,
            section TEXT,
            lemma TEXT NOT NULL,
            ref_raw TEXT,
            source_file TEXT,
            query_text TEXT NOT NULL,
            candidate_files_json TEXT,
            cause_category TEXT NOT NULL,
            evidence_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            sha256_hash TEXT NOT NULL
        );

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            json TEXT
        );

        CREATE VIRTUAL TABLE items_fts USING fts5(
            label,
            section,
            ref_raw,
            volume,
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )
    conn.commit()

    # Step 1: Ingest 272 volumes from sanitized_source_db
    print(f"Migrating sanitized baseline from {sanitized_source_db}...")
    t0 = time.time()
    src_conn = sqlite3.connect(f"file:{sanitized_source_db}?mode=ro", uri=True)
    src_cur = src_conn.cursor()

    src_cur.execute("SELECT id, group_id, volume, section, label, ref_raw, source, state, metadata_json FROM items")
    batch = []
    total_migrated = 0
    for row in src_cur:
        batch.append(row)
        if len(batch) >= 10000:
            cur.executemany("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)", batch)
            conn.commit()
            total_migrated += len(batch)
            batch = []
    if batch:
        cur.executemany("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)", batch)
        conn.commit()
        total_migrated += len(batch)
    print(f"Migrated {total_migrated} items from sanitized source in {time.time()-t0:.2f}s")

    # Migrate candidates
    print("Migrating candidates...")
    t1 = time.time()
    src_cur.execute("SELECT item_id, file, rank, score, navigable, candidate_json FROM candidate_storage")
    c_batch = []
    total_cands = 0
    for row in src_cur:
        c_batch.append(row)
        if len(c_batch) >= 10000:
            cur.executemany("INSERT INTO candidate_storage VALUES(?,?,?,?,?,?)", c_batch)
            conn.commit()
            total_cands += len(c_batch)
            c_batch = []
    if c_batch:
        cur.executemany("INSERT INTO candidate_storage VALUES(?,?,?,?,?,?)", c_batch)
        conn.commit()
        total_cands += len(c_batch)
    print(f"Migrated {total_cands} candidate links in {time.time()-t1:.2f}s")
    src_conn.close()

    # Step 2: Ingest the remaining 135 volumes from patristic_indices.db
    cur.execute("SELECT DISTINCT volume FROM items")
    already_vols = set(r[0] for r in cur.fetchall())

    pat_conn = sqlite3.connect(f"file:{patristic_db}?mode=ro", uri=True)
    pat_cur = pat_conn.cursor()

    pat_cur.execute("SELECT DISTINCT volume_id FROM volumes")
    all_corpus_vols = sorted(list(set(r[0] for r in pat_cur.fetchall())))
    missing_vols = [v for v in all_corpus_vols if v not in already_vols]
    print(f"Total corpus volumes: {len(all_corpus_vols)}, missing to ingest: {len(missing_vols)}")

    items_added = 0
    cands_added = 0
    t2 = time.time()

    for vol in missing_vols:
        vol_dir = PROJECT_ROOT / "teste" / vol / "text"
        pfiles = page_files(vol_dir, suffixes=[".txt"]) if vol_dir.is_dir() else {}
        col_to_file = load_volume_column_map(vol, estimator_db)

        pat_cur.execute(
            """
            SELECT s.heading_raw, s.heading_norm, s.index_kind, e.id, e.entry_raw,
                   e.target_file, e.page_ref_raw, e.page_ref_int, e.raw_json
            FROM index_sections s
            JOIN index_entries e ON s.section_key = e.section_key
            WHERE s.volume_id = ?
            ORDER BY e.id
            """,
            (vol,),
        )
        rows = pat_cur.fetchall()

        item_batch = []
        cand_batch = []

        for h_raw, h_norm, i_kind, eid, eraw, tfile, praw, pint, rjson in rows:
            lemma, ref = split_lemma_and_refs(eraw)
            if not ref and praw:
                ref = str(praw)
            lemma = clean_single_entry_text(lemma)
            if is_pure_noise_label(lemma):
                continue

            section = h_norm or h_raw or i_kind or ""
            item_id = f"{vol}:pat:{eid}"
            group_id = digest(f"{vol}|{section}|{lemma}")[:16]

            # Resolve candidate target
            target = tfile
            score = 100.0
            if target and not Path(target).exists():
                target = None

            if not target and pint and pint in pfiles:
                target = str(pfiles[pint][0])
                score = 90.0

            if not target and pint and pint in col_to_file:
                target = col_to_file[pint]
                score = 80.0

            if not target and ref:
                for c in parse_migne_columns(ref):
                    if c.isdigit() and int(c) in col_to_file:
                        target = col_to_file[int(c)]
                        score = 75.0
                        break

            state = "with_destinations" if target else "source_only"
            source_file = target or (str(pfiles[1][0]) if 1 in pfiles else "")

            meta = {
                "source_db": "patristic_indices.db",
                "index_kind": i_kind,
                "original_entry": eraw,
            }

            item_batch.append((
                item_id,
                group_id,
                vol,
                section,
                lemma,
                ref,
                source_file,
                state,
                json.dumps(meta, ensure_ascii=False),
            ))

            if target:
                cand_blob = pack_json({
                    "volume": vol,
                    "target_file": target,
                    "ref": ref,
                    "confidence": score,
                })
                cand_batch.append((item_id, target, 1, score, 1, cand_blob))
                cands_added += 1

        if item_batch:
            cur.executemany("INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?)", item_batch)
            items_added += len(item_batch)
        if cand_batch:
            cur.executemany("INSERT OR REPLACE INTO candidate_storage VALUES(?,?,?,?,?,?)", cand_batch)
        conn.commit()

    print(f"Ingested {items_added} items and {cands_added} targets for {len(missing_vols)} volumes in {time.time()-t2:.2f}s")
    pat_conn.close()

    # Step 3: Populate FTS5 Index
    print("Building full-text search index (FTS5)...")
    t3 = time.time()
    cur.execute(
        """
        INSERT INTO items_fts(rowid, label, section, ref_raw, volume)
        SELECT rowid, label, coalesce(section, ''), coalesce(ref_raw, ''), volume
        FROM items
        """
    )
    conn.commit()
    print(f"FTS5 index populated in {time.time()-t3:.2f}s")

    # Step 4: Populate Long-Tail Residual Queue
    print("Building Long-Tail Residual Embedding Queue...")
    t4 = time.time()
    cur.execute(
        """
        SELECT id, volume, section, label, ref_raw, source
        FROM items
        WHERE state = 'source_only'
        """
    )
    queue_rows = cur.fetchall()
    print(f"Total pending items for residual queue: {len(queue_rows)}")

    vol_sample_cache: dict[str, list[str]] = {}
    q_batch = []
    for iid, vol, sec, label, ref_raw, source in queue_rows:
        cause = classify_residual_cause(label, ref_raw or "", sec or "", vol)
        query_text = f"Instruct: {INSTRUCT_TASK}\nQuery: {label}"

        # Bounded candidate files: cache by volume
        if vol not in vol_sample_cache:
            vol_dir = PROJECT_ROOT / "teste" / vol / "text"
            if vol_dir.is_dir():
                all_txt = sorted(list(vol_dir.glob("*.txt")))
                vol_sample_cache[vol] = [str(p) for p in all_txt[:15]]
            else:
                vol_sample_cache[vol] = []
        cand_files = vol_sample_cache[vol]

        evidence = {
            "volume": vol,
            "section": sec,
            "ref_raw": ref_raw,
            "cause": cause,
            "deterministic_targets_found": 0,
        }

        hash_sig = digest(f"{iid}|{query_text}|{cause}")

        q_batch.append((
            iid,
            vol,
            sec or "",
            label,
            ref_raw or "",
            source or "",
            query_text,
            json.dumps(cand_files, ensure_ascii=False),
            cause,
            json.dumps(evidence, ensure_ascii=False),
            "pending",
            hash_sig,
        ))

        if len(q_batch) >= 10000:
            cur.executemany(
                """
                INSERT OR REPLACE INTO residual_embedding_queue
                (item_id, volume, section, lemma, ref_raw, source_file, query_text, candidate_files_json, cause_category, evidence_json, status, sha256_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                q_batch,
            )
            conn.commit()
            q_batch = []

    if q_batch:
        cur.executemany(
            """
            INSERT OR REPLACE INTO residual_embedding_queue
            (item_id, volume, section, lemma, ref_raw, source_file, query_text, candidate_files_json, cause_category, evidence_json, status, sha256_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            q_batch,
        )
        conn.commit()

    print(f"Residual queue populated with {len(queue_rows)} cases in {time.time()-t4:.2f}s")

    # Step 5: Metadata & Audit Summary
    cur.execute("SELECT count(*) FROM items")
    total_items = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT volume) FROM items")
    total_volumes = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM items WHERE state = 'with_destinations'")
    with_dest = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM items WHERE state = 'source_only'")
    source_only = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM candidate_storage")
    total_cand_links = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM residual_embedding_queue")
    total_queue = cur.fetchone()[0]

    cur.execute("SELECT cause_category, count(*) FROM residual_embedding_queue GROUP BY cause_category ORDER BY count(*) DESC")
    cause_breakdown = dict(cur.fetchall())

    summary = {
        "version": "unified-onomastic-catalog-v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_volumes": total_volumes,
        "total_items": total_items,
        "items_with_destinations": with_dest,
        "items_source_only": source_only,
        "destination_coverage": with_dest / total_items if total_items else 0.0,
        "total_candidate_links": total_cand_links,
        "residual_queue_count": total_queue,
        "cause_breakdown": cause_breakdown,
        "canonical_write_allowed": False,
        "all_407_volumes_covered": total_volumes == 407,
    }

    cur.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", ("summary", json.dumps(summary, ensure_ascii=False)))
    conn.commit()
    conn.close()

    summary_file = out_dir / "catalog_summary.json"
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"Catalog complete! Summary written to {summary_file}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Build unified 407-volume catalog")
    parser.add_argument("--sanitized-db", type=Path, default=PROJECT_ROOT / "data/index_catalog/test_sanitized_20261110/search.sqlite")
    parser.add_argument("--patristic-db", type=Path, default=PROJECT_ROOT / "data/patristic_indices.db")
    parser.add_argument("--estimator-db", type=Path, default=PROJECT_ROOT / "data/editorial_page_estimator.db")
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "data/index_catalog/unified_onomastic_catalog_20261110")
    args = parser.parse_args()

    summary = build_unified_catalog(
        sanitized_source_db=args.sanitized_db,
        patristic_db=args.patristic_db,
        estimator_db=args.estimator_db,
        out_dir=args.out_dir,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
