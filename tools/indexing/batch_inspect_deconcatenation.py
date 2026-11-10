"""
Batch Inspection of Index Deconcatenation across multiple volumes.
Runs 100% in READ-ONLY mode.
"""

from __future__ import annotations

import glob
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

from tools.indexing.deconcatenated_index_parser import parse_ocr_file, ExtractedEntry

ROOT = Path("/homessddata/Projects/pdfocr")
SEARCH_DB = ROOT / "data/index_catalog/consolidated_search_20261106_001/search.sqlite"


def get_search_items_for_file(conn: sqlite3.Connection, file_path: str):
    cur = conn.cursor()
    return cur.execute(
        "SELECT id, label, ref_raw FROM items WHERE source=?", (file_path,)
    ).fetchall()


def inspect_page(
    conn: sqlite3.Connection,
    volume: str,
    page_txt: Path,
) -> dict:
    source_str = str(page_txt)
    entries = parse_ocr_file(page_txt)
    old_items = get_search_items_for_file(conn, source_str)

    ibid_count = sum(1 for e in entries if e.inherited_ibid)
    long_entries = sum(1 for e in entries if len(e.lemma) > 150)
    entries_with_cols = sum(1 for e in entries if e.inferred_columns)

    old_long_labels = sum(1 for it in old_items if len(it[1]) > 150)

    return {
        "volume": volume,
        "file": page_txt.name,
        "new_entries_count": len(entries),
        "old_items_count": len(old_items),
        "ibid_resolved": ibid_count,
        "with_columns": entries_with_cols,
        "new_long_lemmas": long_entries,
        "old_long_labels": old_long_labels,
        "sample_entries": entries[:5],
        "sample_ibid": [e for e in entries if e.inherited_ibid][:3],
    }


def main():
    print("=" * 80)
    print("BATCH INSPECTION: DECONCATENATION & IBID. PROPAGATION (READ-ONLY)")
    print("=" * 80)

    conn = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)

    # Selected representative volumes and index pages
    test_targets = [
        # PL002 - Author index
        ("PL002", [704, 705, 706]),
        # PG002 - Greek terms & onomastic
        ("PG002", [637, 638, 639]),
        # PL010 - Dense analytic index with massive paragraphs
        ("PL010", [472, 473, 500, 513]),
        # PL077 - Huge volume with 1200+ suspect labels
        ("PL077", [688, 689, 690]),
        # PG110 - Mixed Greek/Latin Chronicle index
        ("PG110", [664, 665]),
    ]

    totals = Counter()

    for vol, page_nums in test_targets:
        print(f"\n>>> VOLUME {vol} <<<")
        vol_dir = ROOT / f"teste/{vol}/text"
        if not vol_dir.exists():
            print(f"Directory {vol_dir} not found, skipping.")
            continue

        for p_num in page_nums:
            # Find matching file *-p_num.txt or *p_num.txt
            matches = list(vol_dir.glob(f"*-{p_num}.txt")) or list(vol_dir.glob(f"*{p_num}.txt"))
            if not matches:
                print(f"  Page {p_num}: file not found.")
                continue

            target_file = matches[0]
            res = inspect_page(conn, vol, target_file)

            totals["pages"] += 1
            totals["new_entries"] += res["new_entries_count"]
            totals["old_items"] += res["old_items_count"]
            totals["ibid_resolved"] += res["ibid_resolved"]
            totals["old_long_labels"] += res["old_long_labels"]
            totals["new_long_lemmas"] += res["new_long_lemmas"]

            print(
                f"  Page {p_num:>4} ({target_file.name[-20:]}): "
                f"New Entries: {res['new_entries_count']:>3} | "
                f"Old in search: {res['old_items_count']:>3} | "
                f"Ibid. fixed: {res['ibid_resolved']:>2} | "
                f"Cols linked: {res['with_columns']:>3} | "
                f"Long labels (>150): {res['old_long_labels']} old -> {res['new_long_lemmas']} new"
            )

            # Show ibid. resolution samples if present
            if res["sample_ibid"]:
                for ib in res["sample_ibid"][:2]:
                    print(f"      [IBID HERDADO] Lemma: {repr(ib.lemma[:40])} -> herdou colunas: {ib.inferred_columns}")

    conn.close()

    print("\n" + "=" * 80)
    print("TOTALS SUMMARY ACROSS TESTED BATCH:")
    print(f"  Pages inspected: {totals['pages']}")
    print(f"  Old items in search.sqlite: {totals['old_items']}")
    print(f"  New individual entries extracted: {totals['new_entries']}")
    print(f"  Entries with 'ibid.' resolved to preceding column: {totals['ibid_resolved']}")
    print(f"  Defective concatenated labels (>150 chars): {totals['old_long_labels']} -> {totals['new_long_lemmas']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
