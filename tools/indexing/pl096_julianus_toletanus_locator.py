"""
PL096 S. Julianus Toletanus Locator
Sprint 88 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PL096:
1. Editorial Context:
   - PL096 contains S. Julianus Toletanus: Antikeimenon Libri Duo (De Contrariis Sententiis Sacrae Scripturae, cols 585-706).
   - The 'Index Capitulorum in sequentibus Antikeimenon libris duobus' (cols 585-594, physical pp. 298-301)
     indexes the interrogations/chapters of Liber I (Vetus Testamentum) and Liber II (Novum Testamentum).
   - The 5 residual items:
     1) 'Deus vult omnes homines salvos fieri...' -> (ii, 71.) (Liber II, Interrogatio LXI / alias LXXI, cols 697-698, physical p. 353)
     2) 'Mors super illos veniet : Viventes descendunt ad inferna.' -> (i, 76.) (Liber I, Interrogatio LXXVI, cols 629-630, physical p. 319)
     3) 'Pax in sublimibus habetur :' -> (i, 153.) (Liber I, Interrogatio CXXXV / alias CXXXI / 153, cols 661-662, physical p. 335)
     4) 'Spiritus Domini bonus esse dicitur : Spiritus Domini malus in Saul.' -> (i, 145.) (Liber I, Interrogatio CXV / alias CXI / 145, cols 651-652, physical p. 330)
     5) 'Terra in æternum stabit : Terra et omnia auferuntur.' -> (ii, 114.) [typo for (i, 114.)] (Liber I, Interrogatio CXIV / alias CX / 114, cols 649-650, physical p. 329)
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

PL096_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Deus vult omnes homines salvos fieri... -> (ii, 71.) (PL096 p. 353)
    'region-entry:7de0d93468261a5d90e376f93bd9b40f584bff8fb69b1e7e695a69ce2d5304d7': (
        'region-group:7de0d93468261a5d90e376f93bd9b40f584bff8fb69b1e7e695a69ce2d5304d7',
        [
            ('PL096', 353, 'PL096 cols 697-698 / Antikeimenon Lib. II Interrog. LXI/LXXI (Deus vult omnes homines salvos fieri vs Deus cui vult miseretur)', 'pl096_deus_vult_omnes_salvos_p353'),
        ]
    ),

    # 2. Mors super illos veniet : Viventes descendunt ad inferna. -> (i, 76.) (PL096 p. 319)
    'region-entry:7cf6bc6e1f12d36196f2a82f897a4cd2bc2e652c9a990e0ee789759172347f96': (
        'region-group:7cf6bc6e1f12d36196f2a82f897a4cd2bc2e652c9a990e0ee789759172347f96',
        [
            ('PL096', 319, 'PL096 cols 629-630 / Antikeimenon Lib. I Interrog. LXXVI (Veniat mors super illos et descendant viventes in infernum)', 'pl096_mors_super_illos_veniet_p319'),
        ]
    ),

    # 3. Pax in sublimibus habetur : -> (i, 153.) (PL096 p. 335)
    'region-entry:97ef28fce46938f8799052c9ea717a89e87a6a5ea170d345c5fcd34a166791a8': (
        'region-group:97ef28fce46938f8799052c9ea717a89e87a6a5ea170d345c5fcd34a166791a8',
        [
            ('PL096', 335, 'PL096 cols 661-662 / Antikeimenon Lib. I Interrog. CXXXV/153 (Potestas et terror apud eum qui facit pacem in sublimibus suis)', 'pl096_pax_in_sublimibus_p335'),
        ]
    ),

    # 4. Spiritus Domini bonus esse dicitur : Spiritus Domini malus in Saul. -> (i, 145.) (PL096 p. 330)
    'region-entry:88d3e4cbc49ccde36d8a0773deb343d1635d6be131f8c3c1f42ca7d02493e70c': (
        'region-group:88d3e4cbc49ccde36d8a0773deb343d1635d6be131f8c3c1f42ca7d02493e70c',
        [
            ('PL096', 330, 'PL096 cols 651-652 / Antikeimenon Lib. I Interrog. CXV/145 (Spiritus Domini bonus vs Spiritus Domini malus in Saul)', 'pl096_spiritus_domini_bonus_malus_p330'),
        ]
    ),

    # 5. Terra in æternum stabit : Terra et omnia auferuntur. -> (ii, 114.) [typo for (i, 114.)] (PL096 p. 329)
    'region-entry:ad6ebfd0e5d46b06b933edeb8faa34a4a481ce8469eccd32c0ca6b38a4537af1': (
        'region-group:ad6ebfd0e5d46b06b933edeb8faa34a4a481ce8469eccd32c0ca6b38a4537af1',
        [
            ('PL096', 329, 'PL096 cols 649-650 / Antikeimenon Lib. I Interrog. CXIV/114 (Terra vero in æternum stabit vs omnia hæc humiliari et auferri)', 'pl096_terra_in_aeternum_stabit_p329'),
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


def resolve_pl096_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL096 item to physical destination files."""
    if item_id not in PL096_ITEMS_RAW:
        return None
    group_id, dests = PL096_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint88(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 88 recovery for PL096 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL096' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL096',
        'sprint': 88,
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
        res = resolve_pl096_item(item_id)
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
    parser = argparse.ArgumentParser(description="PL096 S. Julianus Toletanus Locator (Sprint 88)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint88(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
