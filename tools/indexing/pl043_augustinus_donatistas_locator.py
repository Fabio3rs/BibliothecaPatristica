"""
PL043 S. Augustinus (Breviculus Collationis & Elenchus Tomi VI) Locator
Sprint 92 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PL043:
1. Editorial Context:
   - PL043 contains S. Augustinus: Opera Omnia, Tomus IX (De Gestis cum Emerito, Breviculus Collationis cum Donatistis, etc.).
   - The residual items:
     1) 'COLLATIO PRIMI DIEI' -> ibid. (Breviculus Collationis cum Donatistis, cols 613-614, physical p. 311)
     2) 'Vide librum de Fide et Operibus, n' -> 6; (Tomus VI / PL040 col 202, physical p. 106, and PL043 p. 386)
     3) 'Enchiridion. n' -> 5; (Tomus VI / PL040 col 233, physical p. 121, and PL043 p. 386)
     4) 'librum de Patientia, n' -> 10; (Tomus VI / PL040 col 617, physical p. 313, and PL043 p. 386)
     5) 'et sermonem de Utilitate Jejunii, nn' -> 6 (Tomus VI / PL040 col 711, physical p. 358, and PL043 p. 386)
"""

import argparse
import glob
import json
import os
import sqlite3
import zlib
from typing import Dict, List, Optional, Tuple

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'
SUMMARY_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json'

PL043_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. COLLATIO PRIMI DIEI -> ibid. (PL043 p. 311)
    'region-entry:2dd3e503fb77bf518f5e70334ea1e9f1989339e978152bddd0490a8a443eb3dd': (
        'region-group:2dd3e503fb77bf518f5e70334ea1e9f1989339e978152bddd0490a8a443eb3dd',
        [
            ('PL043', 311, 'PL043 cols 613-614 / Breviculus Collationis cum Donatistis (Collatio primi diei)', 'pl043_collatio_primi_diei_p311'),
        ]
    ),

    # 2. Vide librum de Fide et Operibus, n -> 6; (PL040 p. 106, PL043 p. 386)
    '14accf5b1372c5ee6342f3bd82717ce0c24a928d85e8c9f363a5171d6736bd92': (
        'region-group:d580fbb7147cfc3ce691386226a96842fbae5974b4327d87dd51e77f644a66ec',
        [
            ('PL040', 106, 'PL040 col 202 / De Fide et Operibus cap. 6 (Contra Donatistas)', 'pl043_de_fide_et_operibus_p106'),
            ('PL043', 386, 'PL043 cols 763-764 / Elenchus in Sexto Tomo de Donatistis', 'pl043_de_fide_et_operibus_p386'),
        ]
    ),

    # 3. Enchiridion. n -> 5; (PL040 p. 121, PL043 p. 386)
    '072aff7379d335d634c4c19d997fe56a4f595f68cc6db38b182b7d663d9fb551': (
        'region-group:d580fbb7147cfc3ce691386226a96842fbae5974b4327d87dd51e77f644a66ec',
        [
            ('PL040', 121, 'PL040 col 233 / Enchiridion cap. 5 (De Donatistis et schismate)', 'pl043_enchiridion_p121'),
            ('PL043', 386, 'PL043 cols 763-764 / Elenchus in Sexto Tomo de Donatistis', 'pl043_enchiridion_p386'),
        ]
    ),

    # 4. librum de Patientia, n -> 10; (PL040 p. 313, PL043 p. 386)
    'ebe01cb0cd1dd26843fc921940599e8e7a82174c3a90305dc597783acf866fd3': (
        'region-group:d580fbb7147cfc3ce691386226a96842fbae5974b4327d87dd51e77f644a66ec',
        [
            ('PL040', 313, 'PL040 col 617 / De Patientia cap. 10 (De Donatistis et schismaticis)', 'pl043_de_patientia_p313'),
            ('PL043', 386, 'PL043 cols 763-764 / Elenchus in Sexto Tomo de Donatistis', 'pl043_de_patientia_p386'),
        ]
    ),

    # 5. et sermonem de Utilitate Jejunii, nn -> 6 (PL040 p. 358, PL043 p. 386)
    '9cbfdda0971cdb843d5eb37972c88b0bf70177a3f39a96c458aac65b35806afc': (
        'region-group:d580fbb7147cfc3ce691386226a96842fbae5974b4327d87dd51e77f644a66ec',
        [
            ('PL040', 358, 'PL040 cols 711-712 / De Utilitate Jejunii cap. 6 (De Donatistis et unitate Ecclesiae)', 'pl043_de_utilitate_jejunii_p358'),
            ('PL043', 386, 'PL043 cols 763-764 / Elenchus in Sexto Tomo de Donatistis', 'pl043_de_utilitate_jejunii_p386'),
        ]
    ),
}


def get_physical_file(volume: str, page_num: int) -> str:
    """Finds physical txt file matching volume and page number."""
    matches = glob.glob(f'teste/{volume}/text/*-{page_num:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{page_num}.txt')
    matches = list(dict.fromkeys(matches))
    if matches:
        return matches[0]
    return f'teste/{volume}/text/{volume}-{page_num:03d}.txt'


def build_candidate_json(
    volume: str,
    page_num: int,
    file_path: str,
    col_desc: str,
    reason: str,
    rank: int = 1,
) -> bytes:
    """Builds candidate compressed JSON payload."""
    payload = {
        'target_volume': volume,
        'page_num': page_num,
        'file': file_path,
        'col_desc': col_desc,
        'locator_reason': reason,
        'rank': rank,
        'score': 10.0,
    }
    return zlib.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def resolve_pl043_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL043 item to physical destination files."""
    if item_id not in PL043_ITEMS_RAW:
        return None
    group_id, dests = PL043_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint92(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 92 recovery for PL043 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL043' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL043',
        'sprint': 92,
        'total_source_only_initial': total_source_only,
        'resolved_count': 0,
        'candidates_created': 0,
        'applied': apply,
        'items': [],
    }

    if not source_only_rows:
        conn.close()
        return report

    items_to_update = []
    for r in source_only_rows:
        item_id, group_id, label, ref_raw, source = r
        res = resolve_pl043_item(item_id)
        if res:
            _, resolved_dests = res
            items_to_update.append((item_id, label, ref_raw, resolved_dests))

    report['resolved_count'] = len(items_to_update)

    if not apply:
        conn.close()
        return report

    candidates_inserted = 0
    for item_id, label, ref_raw, dests in items_to_update:
        for rank, (vol, page, col_desc, reason, fpath) in enumerate(dests, 1):
            cand_blob = build_candidate_json(vol, page, fpath, col_desc, reason, rank)
            cur.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, fpath, rank, 10.0, 1, cand_blob))
            candidates_inserted += 1

        cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (item_id,))

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    total_with_dest = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    total_src_only = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    total_candidates = cur.fetchone()[0]

    report['candidates_created'] = candidates_inserted
    report['total_with_destinations_now'] = total_with_dest
    report['total_source_only_now'] = total_src_only
    report['candidate_storage_records_now'] = total_candidates

    # Update summary file if exists
    if os.path.exists(summary_path):
        try:
            with open(summary_path, 'r', encoding='utf-8') as sf:
                summary = json.load(sf)
            summary['items_with_destinations'] = total_with_dest
            summary['items_source_only'] = total_src_only
            summary['candidate_storage_records'] = total_candidates
            with open(summary_path, 'w', encoding='utf-8') as sf:
                json.dump(summary, sf, indent=2, ensure_ascii=False)
        except Exception as e:
            report['summary_update_error'] = str(e)

    conn.close()
    return report


def main():
    parser = argparse.ArgumentParser(description="PL043 S. Augustinus Locator (Sprint 92)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint92(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
