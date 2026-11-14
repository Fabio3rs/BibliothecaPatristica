"""Apply deterministic mechanical resolution for inter-volume Roman Tome + Column citations.

Follows the explicit editorial rule stated on PL218 p. 12:
- Roman numeral = Migne volume (PL001 to PL221, or PG001 to PG161)
- Arabic numeral = Column number in that target volume

Maps citations directly to physical page files via editorial_page_estimator.db column cache.
Replaces spurious host-volume self-links and elevates items to 'with_destinations'.
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

from tools.corpus_utils import PROJECT_ROOT
from tools.indexing.roman_pair_index_grammar import roman_value
from tools.indexing.build_unified_onomastic_catalog import pack_json

RE_TOME_COL = re.compile(r"\b([IVXLCDM]+)[\s,.:;—–]+(\d{1,4})\b")


def get_volume_column_map(volume_id: str, est_cur: sqlite3.Cursor) -> dict[int, str]:
    est_cur.execute(
        "SELECT file_path, observation_json FROM file_observation_cache WHERE volume_id=?",
        (volume_id,),
    )
    col_map: dict[int, str] = {}
    for path, obs_json in est_cur.fetchall():
        try:
            obs = json.loads(obs_json)
            for pair in obs.get("header_pairs", []):
                for c in pair:
                    col_map[int(c)] = path
            for s in obs.get("header_singles", []):
                col_map[int(s)] = path
        except Exception:
            pass
    return col_map


def repair_inter_volume_targets(catalog_db_path: Path, est_db_path: Path) -> dict[str, Any]:
    print(f"Connecting to catalog DB: {catalog_db_path}")
    cat_conn = sqlite3.connect(str(catalog_db_path))
    cat_cur = cat_conn.cursor()

    print(f"Connecting to estimator DB: {est_db_path}")
    est_conn = sqlite3.connect(f"file:{est_db_path}?mode=ro", uri=True)
    est_cur = est_conn.cursor()

    cat_cur.execute(
        """
        SELECT id, volume, label, ref_raw
        FROM items
        WHERE ref_raw GLOB '*[IVXLCDM]*, *[0-9]*'
           OR ref_raw GLOB '*[IVXLCDM]*.*[0-9]*'
           OR ref_raw GLOB '*[IVXLCDM]* [0-9]*'
        """
    )
    candidate_items = cat_cur.fetchall()
    print(f"Found {len(candidate_items)} items with potential Roman Tome + Column citations.")

    vol_col_cache: dict[str, dict[int, str]] = {}
    repaired_links = 0
    newly_resolved_items = set()
    t0 = time.time()

    cand_inserts = []
    items_to_update_state = set()

    for n, (iid, host_vol, label, ref_raw) in enumerate(candidate_items, 1):
        if not ref_raw:
            continue
        m = RE_TOME_COL.search(ref_raw)
        if not m:
            continue
        tome_rom, col_str = m.group(1), m.group(2)
        tome_num = roman_value(tome_rom)
        if not tome_num:
            continue

        prefix = "PG" if host_vol.startswith("PG") else "PL"
        max_tome = 161 if prefix == "PG" else 221
        if not (1 <= tome_num <= max_tome):
            continue

        tgt_vol = f"{prefix}{tome_num:03d}"
        col_num = int(col_str)

        if tgt_vol not in vol_col_cache:
            vol_col_cache[tgt_vol] = get_volume_column_map(tgt_vol, est_cur)

        col_map = vol_col_cache[tgt_vol]
        tgt_file = col_map.get(col_num)

        # Tolerance: if exact col_num not in header, check col_num-1 or col_num+1
        if not tgt_file and (col_num - 1) in col_map:
            tgt_file = col_map[col_num - 1]
        elif not tgt_file and (col_num + 1) in col_map:
            tgt_file = col_map[col_num + 1]

        if not tgt_file:
            continue

        # Check existing candidate links for this item
        cat_cur.execute("SELECT file FROM candidate_storage WHERE item_id=?", (iid,))
        existing_files = [r[0] for r in cat_cur.fetchall()]

        # If it points to host volume (self-link error) or has no files, replace/insert!
        has_self_link = any(f"teste/{host_vol}/" in f for f in existing_files)
        if has_self_link or not existing_files:
            if has_self_link:
                # Remove false self-links
                cat_cur.execute(
                    "DELETE FROM candidate_storage WHERE item_id=? AND file LIKE ?",
                    (iid, f"%teste/{host_vol}/%"),
                )

            cand_blob = pack_json({
                "rule": "inter_volume_roman_tome_column",
                "target_volume": tgt_vol,
                "column": col_num,
                "target_file": tgt_file,
                "confidence": 95.0,
            })
            cand_inserts.append((iid, tgt_file, 1, 95.0, 1, cand_blob))
            repaired_links += 1
            newly_resolved_items.add(iid)
            items_to_update_state.add(iid)

        if len(cand_inserts) >= 500:
            cat_cur.executemany(
                """
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?,?,?,?,?,?)
                """,
                cand_inserts,
            )
            cat_conn.commit()
            cand_inserts = []

    if cand_inserts:
        cat_cur.executemany(
            """
            INSERT OR REPLACE INTO candidate_storage
            (item_id, file, rank, score, navigable, candidate_json)
            VALUES (?,?,?,?,?,?)
            """,
            cand_inserts,
        )
        cat_conn.commit()

    print(f"Repaired {repaired_links} candidate links across {len(newly_resolved_items)} items.")

    # Update items state
    if items_to_update_state:
        print(f"Updating state for {len(items_to_update_state)} items to 'with_destinations'...")
        item_list = list(items_to_update_state)
        for i in range(0, len(item_list), 5000):
            chunk = item_list[i : i + 5000]
            cat_cur.execute(
                f"UPDATE items SET state='with_destinations' WHERE id IN ({','.join(['?']*len(chunk))})",
                chunk,
            )
            # Remove from residual queue if present
            cat_cur.execute(
                f"DELETE FROM residual_embedding_queue WHERE item_id IN ({','.join(['?']*len(chunk))})",
                chunk,
            )
            cat_conn.commit()

    est_conn.close()

    # Recompute summary
    cat_cur.execute("SELECT count(*) FROM items")
    tot_items = cat_cur.fetchone()[0]
    cat_cur.execute("SELECT count(*) FROM items WHERE state='with_destinations'")
    with_dest = cat_cur.fetchone()[0]
    cat_cur.execute("SELECT count(*) FROM residual_embedding_queue")
    queue_cnt = cat_cur.fetchone()[0]

    summary = {
        "repaired_links": repaired_links,
        "newly_resolved_items": len(newly_resolved_items),
        "total_items": tot_items,
        "items_with_destinations": with_dest,
        "destination_coverage": round(with_dest / tot_items if tot_items else 0.0, 4),
        "residual_queue_count": queue_cnt,
        "duration_seconds": round(time.time() - t0, 2),
    }

    cat_cur.execute("INSERT OR REPLACE INTO metadata VALUES('summary', ?)", (json.dumps(summary),))
    cat_conn.commit()
    cat_conn.close()

    print(f"Completed repair in {time.time()-t0:.2f}s:")
    print(json.dumps(summary, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description="Repair Inter-Volume Roman Tome Targets")
    parser.add_argument("--catalog-db", type=Path, default=PROJECT_ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite")
    parser.add_argument("--estimator-db", type=Path, default=PROJECT_ROOT / "data/editorial_page_estimator.db")
    args = parser.parse_args()

    repair_inter_volume_targets(args.catalog_db, args.estimator_db)


if __name__ == "__main__":
    main()
