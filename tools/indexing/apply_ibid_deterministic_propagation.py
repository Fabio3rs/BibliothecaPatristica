#!/usr/bin/env python3
"""Deterministic Ibid Predecessor Propagation for Patristic Index Entries.

Resolves entries with pure 'ibid.' / 'ib.' citations by propagating the physical
candidate destinations from the immediate predecessor entry within the same
index reading sequence.

Audited rules:
1. Intra-page precedence: items within the same source index file ordered by source_span start.
2. Consecutive page continuity: allows propagation to the top of subsequent index pages
   if file sequence gap <= max_file_gap (default 2).
3. Section boundary isolation: resets propagation state when a different non-null section is entered.
4. Updates items.state to 'with_destinations', stores provenance in candidate_storage,
   and marks residual_embedding_queue items as 'confirmed'.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
import zlib

ROOT = Path(__file__).resolve().parents[2]
RE_FILE_SEQ = re.compile(r"-(\d+)\.txt$")
RE_PURE_IBID = re.compile(r"^(?:ibid(?:em)?|ib)(?:\.|\b)", re.IGNORECASE)


def extract_file_seq(path_str: str) -> int:
    m = RE_FILE_SEQ.search(path_str)
    return int(m.group(1)) if m else 0


def propagate_ibid_destinations(
    catalog_db_path: Path,
    max_file_gap: int = 2,
    apply: bool = False,
) -> Dict[str, Any]:
    t0 = time.time()
    conn = sqlite3.connect(str(catalog_db_path))
    cur = conn.cursor()

    # 1. Cache all existing top-ranked candidates
    cur.execute(
        """
        SELECT item_id, file, score, candidate_json
        FROM candidate_storage
        WHERE rank = 1
        """
    )
    cand_cache: Dict[str, Tuple[str, float, Optional[bytes]]] = {
        row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()
    }

    # 2. Load all items with source info
    cur.execute(
        """
        SELECT id, volume, source, section, label, ref_raw, state, metadata_json
        FROM items
        ORDER BY volume, source
        """
    )
    all_items = cur.fetchall()

    vol_files = defaultdict(lambda: defaultdict(list))
    for r in all_items:
        iid, vol, src, sec, lbl, ref, state, meta_json = r
        if not src:
            continue
        meta = json.loads(meta_json) if meta_json else {}
        span = meta.get("source_span")
        start = span[0] if span and isinstance(span, list) else 9999999
        vol_files[vol][src].append((start, iid, vol, src, sec, lbl, ref, state, meta))

    promotions: List[Dict[str, Any]] = []
    intra_count = 0
    cross_count = 0
    skipped_count = 0

    for vol, files in vol_files.items():
        sorted_files = sorted(files.keys(), key=extract_file_seq)
        vol_last_cand = None
        vol_last_seq = None
        vol_last_info = None
        vol_last_section = None

        for src in sorted_files:
            curr_seq = extract_file_seq(src)
            # Gap check: reset if index page discontinuity
            if vol_last_seq is not None and (curr_seq - vol_last_seq > max_file_gap):
                vol_last_cand = None
                vol_last_seq = None
                vol_last_info = None
                vol_last_section = None

            items = files[src]
            items.sort(key=lambda x: x[0])  # sort by source_span start
            file_had_cand = False

            for start, iid, v, source_path, sec, lbl, ref, state, meta in items:
                # Check section boundary
                if sec is not None and vol_last_section is not None and sec != vol_last_section:
                    vol_last_cand = None
                    vol_last_info = None

                ref_clean = (ref or "").strip()
                is_pure_ibid = bool(
                    RE_PURE_IBID.match(ref_clean) and not re.search(r"\d+", ref_clean)
                )

                if state == "with_destinations" and iid in cand_cache:
                    vol_last_cand = cand_cache[iid]
                    vol_last_info = (iid, lbl, ref)
                    vol_last_seq = curr_seq
                    vol_last_section = sec
                    file_had_cand = True
                elif is_pure_ibid and state == "source_only":
                    if vol_last_cand is not None:
                        is_intra = file_had_cand
                        if is_intra:
                            intra_count += 1
                        else:
                            cross_count += 1

                        promotions.append({
                            "item_id": iid,
                            "volume": vol,
                            "label": lbl,
                            "ref_raw": ref,
                            "is_intra_page": is_intra,
                            "target_file": vol_last_cand[0],
                            "target_score": vol_last_cand[1],
                            "parent_id": vol_last_info[0],
                            "parent_label": vol_last_info[1],
                            "parent_ref": vol_last_info[2],
                            "meta": meta,
                        })
                    else:
                        skipped_count += 1

    # Apply promotions atomically if requested
    applied_count = 0
    if apply and promotions:
        for p in promotions:
            iid = p["item_id"]
            tfile = p["target_file"]
            score = p["target_score"]

            # 1. Update items table
            cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (iid,))

            # 2. Update residual_embedding_queue if present
            cur.execute(
                """
                UPDATE residual_embedding_queue
                SET status = 'confirmed', candidate_files_json = ?
                WHERE item_id = ?
                """,
                (json.dumps([tfile]), iid),
            )

            # 3. Insert into candidate_storage
            cand_obj = {
                "file": tfile,
                "score": score,
                "navigable": True,
                "certified": True,
                "method": "ibid_predecessor_propagation",
                "inherited_from_item_id": p["parent_id"],
                "inherited_from_ref": p["parent_ref"],
                "inherited_from_label": p["parent_label"],
                "is_intra_page": p["is_intra_page"],
            }
            blob = zlib.compress(json.dumps(cand_obj).encode("utf-8"))
            cur.execute(
                """
                INSERT OR REPLACE INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, 1, ?, 1, ?)
                """,
                (iid, tfile, score, blob),
            )
            applied_count += 1

        conn.commit()

        # Update summary metrics
        summary_path = catalog_db_path.parent / "catalog_summary.json"
        if summary_path.exists():
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                cur.execute("SELECT count(*) FROM items WHERE state = 'with_destinations'")
                sdata["items_with_destinations"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM residual_embedding_queue WHERE status = 'pending'")
                sdata["residual_queue_count"] = cur.fetchone()[0]
                if sdata.get("total_items", 0) > 0:
                    sdata["destination_coverage"] = round(sdata["items_with_destinations"] / sdata["total_items"], 4)
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(sdata, f, indent=2)
            except Exception:
                pass

    conn.close()
    duration = time.time() - t0

    return {
        "status": "success",
        "dry_run": not apply,
        "promoted_items_count": len(promotions),
        "applied_count": applied_count,
        "intra_page_count": intra_count,
        "consecutive_cross_page_count": cross_count,
        "skipped_count": skipped_count,
        "duration_seconds": round(duration, 2),
        "sample_promotions": promotions[:10],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Deterministic Ibid Predecessor Propagation for Patristic Index Entries."
    )
    parser.add_argument(
        "--catalog-db",
        type=Path,
        default=ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite",
        help="Path to search.sqlite catalog database.",
    )
    parser.add_argument(
        "--max-file-gap",
        type=int,
        default=2,
        help="Maximum file sequence gap for consecutive index pages (default: 2).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply confirmed ibid destinations atomically to database.",
    )
    args = parser.parse_args()

    res = propagate_ibid_destinations(
        catalog_db_path=args.catalog_db,
        max_file_gap=args.max_file_gap,
        apply=args.apply,
    )

    print(json.dumps({k: v for k, v in res.items() if k != "sample_promotions"}, indent=2))
    print("\nSample promotions:")
    for s in res.get("sample_promotions", []):
        mode = "intra-page" if s["is_intra_page"] else "cross-page"
        print(f"  • [{s['volume']}] \"{s['label'][:45]}\" ({mode})")
        print(f"    Inherited from: \"{s['parent_label'][:40]}\" (ref: {s['parent_ref']})")
        print(f"    Target: {Path(s['target_file']).name}")


if __name__ == "__main__":
    main()
