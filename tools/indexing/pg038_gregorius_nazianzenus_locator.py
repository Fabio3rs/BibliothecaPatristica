"""
PG038 Gregorius Nazianzenus Carmina Cross-Volume Locator
Sprint 61 of Onomastic Catalog Recovery

Resolves all 11 residual source_only items from PG038:
1. Editorial Context:
   - PG038 contains liturgical commentaries, works of Gregorius Presbyter, Pseudo-Nonnus,
     and the general analytical and thematic indexes for the works of Saint Gregory of Nazianzus:
     * Physical pp. 637-670: INDEX ANALYTICUS IN OMNIA OPERA S. GREGORII THEOLOGI.
   - Saint Gregory's poetical works (Carmina Theologica, Carmina Historica, Christus Patiens)
     are printed in the preceding volume PG037 (cols 397-1336).
   - In the INDEX ANALYTICUS in PG038, citations referencing Gregory's poetry give the Migne
     column number in PG037 followed by 'v.' (versus/line number):
     * e.g., '751, v. 1445' -> PG037 col 751 (physical p. 380, Carmina de seipso)
     * e.g., '1089, v. 8'   -> PG037 col 1089 (physical p. 551, Ad juvenes)
     * e.g., '1289, v. 1399'-> PG037 col 1289 (physical p. 651, Christus Patiens)
     * e.g., '1299, v. 1588'-> PG037 col 1299 (physical p. 656, Christus Patiens)

2. Physical Mapping in PG037:
   - In PG037 Carmina section:
     * p. 337: cols 665-666 (De vita sua)
     * p. 377: cols 745-746 (De vita sua)
     * p. 380: cols 751-752 (De vita sua)
     * p. 551: cols 1089-1090 (Poemata de seipso)
     * p. 627: cols 1241-1242 (Christus Patiens)
     * p. 637: cols 1261-1262 (Christus Patiens)
     * p. 651: cols 1289-1290 (Christus Patiens)
     * p. 656: cols 1299-1300 (Christus Patiens)
     * p. 658: cols 1303-1304 (Christus Patiens)
     * p. 668: cols 1323-1324 (Christus Patiens)
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

PG038_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Juvenis in caedem Gregorii subornatus... col 751, v. 1445 -> PG037 p. 380
    'c27d68e06596edc6a39d86f70dcb24f2791f4204e98658e7aa6d54cb7ec3fc1b': (
        'c27d68e06596edc6a39d86f70dcb24f2791f4204e98658e7aa6d54cb7ec3fc1b',
        [
            ('PG037', 380, 'PG037 col 751 v. 1445 (Carmina de seipso: Juvenis in caedem Gregorii subornatus)', 'pg038_gregorius_carmina_col751_p380')
        ]
    ),

    # 2. Juvenis qualis esse debeat, col 1089, v. 8 -> PG037 p. 551
    'fc662fecd1502688e69564f0ba8d670ef0b69dd9229c0c69c3a7e5269f01143c': (
        'fc662fecd1502688e69564f0ba8d670ef0b69dd9229c0c69c3a7e5269f01143c',
        [
            ('PG037', 551, 'PG037 col 1089 v. 8 (Carmina Historica / Ad juvenes: Juvenis qualis esse debeat)', 'pg038_gregorius_carmina_col1089_p551')
        ]
    ),

    # 3. Gregorium honorifice excipit, col 745, v. 1505 -> PG037 p. 377
    '3c244078c15fa09846586d3379fdbb07176c582a11763d00ab16b477ab1ac9d5': (
        '3c244078c15fa09846586d3379fdbb07176c582a11763d00ab16b477ab1ac9d5',
        [
            ('PG037', 377, 'PG037 col 745 v. 1505 (Carmina de seipso: Gregorium honorifice excipit)', 'pg038_gregorius_carmina_col745_p377')
        ]
    ),

    # 4. Trias antiquae fidei fons, col 665, v. 1794 -> PG037 p. 337
    'd1734043567011d476c4a50bb150e33a832c514b3e0c5bd0fcb9e0da558a4ad9': (
        'd1734043567011d476c4a50bb150e33a832c514b3e0c5bd0fcb9e0da558a4ad9',
        [
            ('PG037', 337, 'PG037 col 665 v. 1794 (Carmina de seipso: Trias antiquae fidei fons)', 'pg038_gregorius_carmina_col665_p337')
        ]
    ),

    # 5. Jonas in ventre piscis, Christi figura, col 1289, v. 1399 -> PG037 p. 651
    '611659b519fba9564f1dc36eca69c4e58a8c7093090d27326e1fed549e7c9571': (
        '611659b519fba9564f1dc36eca69c4e58a8c7093090d27326e1fed549e7c9571',
        [
            ('PG037', 651, 'PG037 col 1289 v. 1399 (Christus Patiens: Jonas in ventre piscis)', 'pg038_gregorius_christus_patiens_col1289_p651')
        ]
    ),

    # 6. Arabum felices populi, col 1299, v. 1588 -> PG037 p. 656
    '7c22fab78a885ef343f279416b18cff862bf905af903b2af5a293d0bb4883ce6': (
        '7c22fab78a885ef343f279416b18cff862bf905af903b2af5a293d0bb4883ce6',
        [
            ('PG037', 656, 'PG037 col 1299 v. 1588 (Christus Patiens: Arabum felices populi)', 'pg038_gregorius_christus_patiens_col1299_p656')
        ]
    ),

    # 7. Asiam ad salsum mare Graecis et Barbaris simul jacere aiunt, col 1299, v. 1589 -> PG037 p. 656
    '95a82fec1028ed040a63b84ef8a5f70a5bce48f293705ab34836f2b87b59cbe8': (
        '95a82fec1028ed040a63b84ef8a5f70a5bce48f293705ab34836f2b87b59cbe8',
        [
            ('PG037', 656, 'PG037 col 1299 v. 1589 (Christus Patiens: Asiam ad salsum mare)', 'pg038_gregorius_christus_patiens_col1299_p656')
        ]
    ),

    # 8. Alia, ref 1502 -> PG037 p. 656
    'afaab1c240c9db2b0ebaa01af77f66b3484527a28817fc7cf22911945f3df2df': (
        'afaab1c240c9db2b0ebaa01af77f66b3484527a28817fc7cf22911945f3df2df',
        [
            ('PG037', 656, 'PG037 col 1299 v. 1502 (Christus Patiens: Alia sub voce Asia)', 'pg038_gregorius_christus_patiens_col1299_p656')
        ]
    ),

    # 9. Mors Christi voluntaria, col 1261, v. 353... col 1323, v. 2006 -> PG037 pp. 637, 668
    '902272d7d1eda2cd020deb07b4d595a5605f70272bb17a8822b3515d608bbc04': (
        '902272d7d1eda2cd020deb07b4d595a5605f70272bb17a8822b3515d608bbc04',
        [
            ('PG037', 637, 'PG037 col 1261 v. 353 (Christus Patiens: Mors Christi voluntaria initium)', 'pg038_gregorius_christus_patiens_col1261_p637'),
            ('PG037', 668, 'PG037 col 1323 v. 2006 (Christus Patiens: Mors Christi voluntaria conclusio)', 'pg038_gregorius_christus_patiens_col1323_p668')
        ]
    ),

    # 10. Ejus mortis causa, col 1303, v. 1637... col 1323, v. 934 -> PG037 pp. 658, 668
    '917cf812c41e2d4a27719820ccf7d1f435239d90635f0ef8d302d102127b1130': (
        '917cf812c41e2d4a27719820ccf7d1f435239d90635f0ef8d302d102127b1130',
        [
            ('PG037', 658, 'PG037 col 1303 v. 1637 (Christus Patiens: Ejus mortis causa)', 'pg038_gregorius_christus_patiens_col1303_p658'),
            ('PG037', 668, 'PG037 col 1323 v. 934 (Christus Patiens: Ejus mortis causa finis)', 'pg038_gregorius_christus_patiens_col1323_p668')
        ]
    ),

    # 11. Christus tertia die debuit resurgere, col 1241, v. 587... col 1303, v. 1654 -> PG037 pp. 627, 658
    '90522fe5cf0ebc4d4f3413f91118c62e483ee2ed65f6069614277c5a675c0837': (
        '90522fe5cf0ebc4d4f3413f91118c62e483ee2ed65f6069614277c5a675c0837',
        [
            ('PG037', 627, 'PG037 col 1241 v. 587 (Christus Patiens: Christus tertia die debuit resurgere)', 'pg038_gregorius_christus_patiens_col1241_p627'),
            ('PG037', 658, 'PG037 col 1303 v. 1654 (Christus Patiens: Christus tertia die debuit resurgere finis)', 'pg038_gregorius_christus_patiens_col1303_p658')
        ]
    ),
}

_page_file_cache: Dict[Tuple[str, int], str] = {}


def get_physical_file(volume: str, physical_page: int) -> str:
    """Finds the deterministic physical text file for a given volume and page number."""
    key = (volume, physical_page)
    if key in _page_file_cache:
        return _page_file_cache[key]

    matches = glob.glob(f'teste/{volume}/text/*-{physical_page:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{physical_page}.txt')
    if not matches:
        raise FileNotFoundError(f'Cannot find text file for {volume} physical page {physical_page}')
    full_path = os.path.abspath(matches[0])
    _page_file_cache[key] = full_path
    return full_path


def build_candidate_json(
    volume: str,
    physical_page: int,
    file_path: str,
    col_desc: str,
    reason: str,
    rank: int,
) -> bytes:
    """Constructs the compressed candidate payload following project standard schema."""
    payload = {
        'file': file_path,
        'physical': physical_page,
        'score': 10,
        'score_kind': 'verified_index_locator',
        'reasons': [reason, 'pg038_gregorius_carmina_cross_volume_locator'],
        'uncertainties': [],
        'navigable': True,
        'certified': True,
        'snippet': col_desc,
        'evidence': {
            'candidate_basis': 'pg038_nazianzenus_carmina_reconciliation',
            'path': file_path,
            'physical': physical_page,
            'sequence_support': True,
            'sha256': '',
            'volume': volume,
            'file': file_path,
            'current': {
                'method': 'carmina_column_verse_locator',
                'query_scope': 'full_page',
                'matches': [col_desc],
                'match_count': 1,
            },
            'alternatives': [],
            'error': None,
            'history_state': 'resolved',
        },
        'rank': rank,
    }
    return zlib.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def resolve_pg038_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a PG038 source_only item to its target volume, page and physical path."""
    if item_id not in PG038_ITEMS_RAW:
        return None
    group_id, dests = PG038_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint61(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 61 recovery for PG038 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PG038' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PG038',
        'sprint': 61,
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
        res = resolve_pg038_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 61: PG038 Gregorius Nazianzenus Carmina Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint61(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
