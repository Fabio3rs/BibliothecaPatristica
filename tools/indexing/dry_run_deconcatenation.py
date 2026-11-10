"""
Dry-run test script for deconcatenating index entries.
Runs in 100% READ-ONLY mode. Compares:
  1. What was stored in search.sqlite (concatenated blocks)
  2. What is extracted by deconcatenated_index_parser.py
  3. What is in alphabetical_indices.db (when available)
"""

import sqlite3
from pathlib import Path
from tools.indexing.deconcatenated_index_parser import parse_ocr_file


def run_dry_run_pl002():
    print("=" * 70)
    print("DRY-RUN 1: PL002, Page 706 (Philosophers & Authors)")
    print("=" * 70)

    ocr_path = Path("/homessddata/Projects/pdfocr/teste/PL002/text/c22b421c-a172-4854-a179-bea107a116ed-706.txt")
    entries = parse_ocr_file(ocr_path)

    print(f"Total entries extracted from OCR page 706: {len(entries)}\n")

    print(f"{'#':<3} | {'LEMMA':<35} | {'REFS':<25} | {'COLS'}")
    print("-" * 75)
    for i, e in enumerate(entries, 1):
        lemma_disp = (e.lemma[:32] + "...") if len(e.lemma) > 35 else e.lemma
        refs_disp = (e.refs_raw[:22] + "...") if e.refs_raw and len(e.refs_raw) > 25 else (e.refs_raw or "")
        cols_disp = ", ".join(e.inferred_columns[:4])
        print(f"{i:<3} | {lemma_disp:<35} | {refs_disp:<25} | {cols_disp}")


def run_dry_run_pg002():
    print("\n" + "=" * 70)
    print("DRY-RUN 2: PG002, Page 639 (Greek Index of Terms / Names)")
    print("=" * 70)

    ocr_path = Path("/homessddata/Projects/pdfocr/teste/PG002/text/1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-639.txt")
    entries = parse_ocr_file(ocr_path)

    print(f"Total entries extracted from OCR page 639: {len(entries)}\n")

    print(f"{'#':<3} | {'LEMMA':<35} | {'REFS':<25} | {'COLS'}")
    print("-" * 75)
    for i, e in enumerate(entries[:25], 1):
        lemma_disp = (e.lemma[:32] + "...") if len(e.lemma) > 35 else e.lemma
        refs_disp = (e.refs_raw[:22] + "...") if e.refs_raw and len(e.refs_raw) > 25 else (e.refs_raw or "")
        cols_disp = ", ".join(e.inferred_columns[:4])
        print(f"{i:<3} | {lemma_disp:<35} | {refs_disp:<25} | {cols_disp}")


def compare_with_search_sqlite():
    print("\n" + "=" * 70)
    print("COMPARISON: search.sqlite vs Corrected Parser on PL002")
    print("=" * 70)

    search_db = "/homessddata/Projects/pdfocr/data/index_catalog/consolidated_search_20261106_001/search.sqlite"
    conn = sqlite3.connect(f"file:{search_db}?mode=ro", uri=True)
    cur = conn.cursor()

    # Look for items from this specific file in search.sqlite
    file_src = "/homessddata/Projects/pdfocr/teste/PL002/text/c22b421c-a172-4854-a179-bea107a116ed-706.txt"
    rows = cur.execute(
        "SELECT id, label, ref_raw FROM items WHERE source=? ORDER BY id",
        (file_src,),
    ).fetchall()

    print(f"In search.sqlite for page 706: {len(rows)} items stored.")
    print("Sample items in search.sqlite:")
    for r in rows[:3]:
        print(f"  ID: {r[0][:16]} | ref: {r[2]}")
        print(f"  Label length: {len(r[1])} chars | Starts with: {repr(r[1][:60])}...")
    conn.close()


if __name__ == "__main__":
    run_dry_run_pl002()
    run_dry_run_pg002()
    compare_with_search_sqlite()
