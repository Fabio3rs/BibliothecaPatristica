"""
PL098 Codex Carolinus & Monumenta Cenni Locator
Sprint 74 of Onomastic Catalog Recovery

Resolves all 7 residual source_only items from PL098:
1. Editorial Context:
   - PL098 contains:
     * Cols 9-440: Codex Carolinus (Epistolae Pontificum Romanorum ad Carolum Magnum, Pippinum, etc.)
     * Cols 439-650: Monumenta Diplomatica Pontificia (Diplomata Ludovici Pii, Gregorii IV, etc.)
     * Cols 651-850: Epistolae Rudolphi I Regis Romanorum ad Gregorium X Pont. Max. (Lausannensia, etc.)
     * Cols 875-920 (physical pp. 441-455): INDEX RERUM ET VERBORUM IN TOMO II EDITIONIS CENNI
   - The index refers to the pages and sections of Gaetano Cenni's Monumenta Carolina (Rome, 1754):
     * 'Francigaranus Romanis open ferre, 1 seqq' -> Francorum reges Romanis opem ferunt, physical pp. 9, 11
     * 'Romæ. collector, 20' -> Codex Carolinus, physical p. 19
     * 'gubernatur militaris in Romandolaniis... 534 seqq' -> Epp. Rudolphi I, cols 725-731, physical pp. 367, 370
     * 'Probus minorita legatus Rud. ad Greg. X, 544' -> Epp. Rudolphi I, cols 743-746, physical p. 376
     * 'Donationis integræ possessionem flagiat, 545' -> Epp. Rudolphi I, cols 743-746, physical p. 376
     * 'Pignorationes, sen repressaliæ vetitæ a Gregorio X... 545' -> Epp. Rudolphi I, cols 747-748, physical p. 378
     * 'Philin. dux Carinthiæ amovetur, 558, 596' -> Epp. Rudolphi I lib. II, cols 759-772, physical pp. 384, 390
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

PL098_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Francigaranus Romanis open ferre -> 1 seqq (pp. 9, 11)
    'region-entry:a3e4b01ff4510886e57c8c16aeb6b2c47e8dcb41da59dc86ba5520ea732a1057': (
        'region-entry:a3e4b01ff4510886e57c8c16aeb6b2c47e8dcb41da59dc86ba5520ea732a1057',
        [
            ('PL098', 9, 'PL098 cols 9-10 (Codex Carolinus Praefatio: Francorum reges Romanis opem ferunt initium)', 'pl098_carolinus_opem_p9'),
            ('PL098', 11, 'PL098 cols 13-14 (Codex Carolinus Praefatio: Francorum reges Romanis opem ferunt continuatio)', 'pl098_carolinus_opem_p11')
        ]
    ),

    # 2. Romæ. collector, 20 -> p. 19
    'region-entry:cfb572d0ae98a75d7b9761c635141eff3924085cdaf9f159b00f5b9e6163d24e': (
        'region-entry:cfb572d0ae98a75d7b9761c635141eff3924085cdaf9f159b00f5b9e6163d24e',
        [
            ('PL098', 19, 'PL098 col 20 (Codex Carolinus Epist. I: Romae collector / res Romana)', 'pl098_carolinus_collector_p19')
        ]
    ),

    # 3. gubernatur militaris in Romandolaniis, quam credit juris imp -> 534 seqq (pp. 367, 370)
    'region-entry:86fdb9cfbb31f4bc2673e2c18c56297889dff88d99aa8d0bcd6f7552a9b7ef15': (
        'region-entry:86fdb9cfbb31f4bc2673e2c18c56297889dff88d99aa8d0bcd6f7552a9b7ef15',
        [
            ('PL098', 367, 'PL098 cols 725-726 (Epp. Rudolphi I: Gubernator militaris in Romandiola)', 'pl098_rudolphus_romandiola_p367'),
            ('PL098', 370, 'PL098 col 731 (Epp. Rudolphi I: Rector Romandiolae falso creditus juris imperii)', 'pl098_rudolphus_romandiola_p370')
        ]
    ),

    # 4. Probus minorita legatus Rud. ad Greg. X -> 544 (p. 376)
    'region-entry:70764239e4621c1323bb61891e51027583f04c2751e6136e5a4e5c2036a5eedf': (
        'region-entry:70764239e4621c1323bb61891e51027583f04c2751e6136e5a4e5c2036a5eedf',
        [
            ('PL098', 376, 'PL098 cols 743-744 (Epp. Rudolphi I: Probus minorita legatus Rudolphi ad Gregorium X)', 'pl098_rudolphus_probus_p376')
        ]
    ),

    # 5. Donationis integræ pos- sessionem flagiat -> 545 (p. 376)
    'region-entry:837312bab50cc76b3669cde154e23f2f5d55021f96734d0d5b6c3778ca53df16': (
        'region-entry:837312bab50cc76b3669cde154e23f2f5d55021f96734d0d5b6c3778ca53df16',
        [
            ('PL098', 376, 'PL098 cols 743-744 (Epp. Rudolphi I: Donationis integrae possessionem flagitat)', 'pl098_rudolphus_donatio_p376')
        ]
    ),

    # 6. Pignorationes, sen repressaliæ vetitæ a Gregorio X in ecclesiasticis -> 545 (p. 378)
    'region-entry:4bf509311f84c6a9d65786e99ccc68ad3b5da4dc1f25cfaf1515449d87156d91': (
        'region-entry:4bf509311f84c6a9d65786e99ccc68ad3b5da4dc1f25cfaf1515449d87156d91',
        [
            ('PL098', 378, 'PL098 cols 747-748 (Epp. Rudolphi I: Pignorationes seu repressaliae vetitae a Gregorio X)', 'pl098_rudolphus_repressaliae_p378')
        ]
    ),

    # 7. Philin. dux Carinthiæ amovetur -> 558, 596 (pp. 384, 390)
    'region-entry:b2e83427e5af0861ffbae04b050fcf567fdcbc349e5111e7d148b4e4b2739cf6': (
        'region-entry:b2e83427e5af0861ffbae04b050fcf567fdcbc349e5111e7d148b4e4b2739cf6',
        [
            ('PL098', 384, 'PL098 cols 759-760 (Epp. Rudolphi I lib. II: Philippus dux Carinthiae amovetur)', 'pl098_rudolphus_carinthia_p384'),
            ('PL098', 390, 'PL098 cols 771-772 (Epp. Rudolphi I lib. II: Carinthiae et Styriae tractatus)', 'pl098_rudolphus_carinthia_p390')
        ]
    ),
}


def get_physical_file(volume: str, page_num: int) -> str:
    """Finds physical txt file matching volume and page number."""
    matches = glob.glob(f'teste/{volume}/text/*-{page_num:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{page_num}.txt')
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


def resolve_pl098_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL098 item to physical destination files."""
    if item_id not in PL098_ITEMS_RAW:
        return None
    group_id, dests = PL098_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint74(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 74 recovery for PL098 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL098' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL098',
        'sprint': 74,
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
        res = resolve_pl098_item(item_id)
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
    total_source_only_after = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = cur.fetchone()[0]

    report['candidates_created'] = candidates_inserted
    report['total_with_destinations_now'] = total_with_dest
    report['total_source_only_now'] = total_source_only_after
    report['candidate_storage_records'] = cand_count

    conn.close()

    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        summary['items_with_destinations'] = total_with_dest
        summary['items_source_only'] = total_source_only_after
        summary['candidate_storage_records'] = cand_count
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Sprint 74: PL098 Codex Carolinus Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint74(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
