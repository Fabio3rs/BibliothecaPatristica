"""
PG104 Photius Bibliotheca Locator
Sprint 77 of Onomastic Catalog Recovery

Resolves all 7 residual source_only items from PG104:
1. Editorial Context:
   - PG103 and PG104 contain the Bibliotheca (Myriobiblon) of Saint Photius, Patriarch of Constantinople:
     * PG103: Bibliotheca Codices I-CCLVVI (Bekker pages 1-439)
     * PG104: Bibliotheca Codices CCLVII-CCLXXX (Bekker pages 440-549, physical pp. 1-196)
     * PG104: Notae Davidis Hoeschelii & Scholia (physical pp. 197-235)
     * PG104: Epistolae & Amphilochia (cols 585-1300)
     * PG104: INDEX ANALYTICUS IN BIBLIOTHECAM (cols 1507-1560, physical pp. 801-819)
   - Citations in the INDEX ANALYTICUS cite the pages and columns (a/b) of Immanuel Bekker's edition (Berlin, 1824):
     * Bekker pp. 1-439 are printed in PG103
     * Bekker pp. 440-549 are printed in PG104
     * 'Civium numerus, Ol CLXXVII, 84 a.' -> Bekker 84 a in PG103, physical p. 186
     * 'Φθορά, 105 a, 106 a.' -> Bekker 105 a, 106 a in PG103, physical pp. 233, 234
     * 'Spectrorum pugnæ, 335 b, 340 a.' -> Bekker 335 b, 340 a in PG103, physical pp. 631, 639
     * 'Salvius s. Tryphon fugitivorum dux, 388 a, 389 b.' -> Bekker 388 a, 389 b in PG103, physical pp. 714, 715
     * 'Superstitiones variæ, 432-434' -> Bekker 432-434 in PG103, physical pp. 783, 786
     * 'Festi ludi vetantur, 461, 462' -> Bekker 461, 462 in PG104, physical pp. 44, 46
     * 'Severus patricius, 335 b, 549 b.' -> Bekker 335 b in PG103 (p. 631) & Bekker 549 b in PG104 (p. 196)
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

PG104_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Civium numerus, Ol CLXXVII, 84 a. -> PG103 p. 186
    'opening:4037c6e2bb459ab5f426eb8fe60da8a5ca18246d2cddd0f61ad984ac6853e18f': (
        'opening:4037c6e2bb459ab5f426eb8fe60da8a5ca18246d2cddd0f61ad984ac6853e18f',
        [
            ('PG103', 186, 'PG103 cols 361-362 (Photii Bibliotheca cod. XCVII, Bekker 84 a: Civium numerus Ol. CLXXVII)', 'pg104_photius_civium_p186')
        ]
    ),

    # 2. Φθορά -> 105 a, 106 a. (PG103 pp. 233, 234)
    'opening:74fe742cef097d0ba4875dc9d65a8f5aa2a08b7eab0a848a4498537f3bbdd4c3': (
        'opening:74fe742cef097d0ba4875dc9d65a8f5aa2a08b7eab0a848a4498537f3bbdd4c3',
        [
            ('PG103', 233, 'PG103 cols 455-456 (Photii Bibliotheca cod. CXV, Bekker 105 a: De corruptione / Phthora)', 'pg104_photius_phthora_p233'),
            ('PG103', 234, 'PG103 cols 457-458 (Photii Bibliotheca cod. CXV, Bekker 106 a: De corruptione / Phthora)', 'pg104_photius_phthora_p234')
        ]
    ),

    # 3. Spectrorum pugnæ -> 335 b, 340 a. (PG103 pp. 631, 639)
    'opening:9fc7f0f82b1428d8eaaa7aed32b3e1c102ff17d8db734b862e87e17e019bdd54': (
        'opening:9fc7f0f82b1428d8eaaa7aed32b3e1c102ff17d8db734b862e87e17e019bdd54',
        [
            ('PG103', 631, 'PG103 cols 1245-1246 (Photii Bibliotheca cod. CCXLII, Bekker 335 b: Spectrorum pugnae initium)', 'pg104_photius_spectrorum_p631'),
            ('PG103', 639, 'PG103 cols 1261-1262 (Photii Bibliotheca cod. CCXLII, Bekker 340 a: Spectrorum pugnae continuatio)', 'pg104_photius_spectrorum_p639')
        ]
    ),

    # 4. Salvius s. Tryphon fugitivorum dux -> 388 a, 389 b. (PG103 pp. 714, 715)
    'opening:90057293b4886c03819f423f2051aaa8ecdb9bbffb3979d149671e3db5f64824': (
        'opening:90057293b4886c03819f423f2051aaa8ecdb9bbffb3979d149671e3db5f64824',
        [
            ('PG103', 714, 'PG103 cols 1411-1412 (Photii Bibliotheca cod. CCXLIV, Bekker 388 a: Salvius qui et Tryphon dux fugitivorum)', 'pg104_photius_salvius_p714'),
            ('PG103', 715, 'PG103 cols 1413-1414 (Photii Bibliotheca cod. CCXLIV, Bekker 389 b: Salvius qui et Tryphon dux fugitivorum)', 'pg104_photius_salvius_p715')
        ]
    ),

    # 5. Severus patricius -> 335 b (PG103 p. 631) & 549 b (PG104 p. 196)
    'opening:daa14c06f3f4d530997029cdf9eede3a16f4a343d90e4ab48b7428eeca299627': (
        'opening:daa14c06f3f4d530997029cdf9eede3a16f4a343d90e4ab48b7428eeca299627',
        [
            ('PG103', 631, 'PG103 cols 1245-1246 (Photii Bibliotheca cod. CCXLII, Bekker 335 b: Severus patricius)', 'pg104_photius_severus_p631'),
            ('PG104', 196, 'PG104 cols 353-354 (Photii Bibliotheca cod. CCLXXX, Bekker 549 b: Severus patricius)', 'pg104_photius_severus_p196')
        ]
    ),

    # 6. Superstitiones variæ -> 432-434 (PG103 pp. 783, 786)
    'opening:c6e2f63b9b37fb1944d1b050ccd529f18d468d4de0775ef2f4e84103c62c67a8': (
        'opening:c6e2f63b9b37fb1944d1b050ccd529f18d468d4de0775ef2f4e84103c62c67a8',
        [
            ('PG103', 783, 'PG103 cols 1549-1550 (Photii Bibliotheca cod. CCLXXIX, Bekker 432: Superstitiones variae)', 'pg104_photius_superstitiones_p783'),
            ('PG103', 786, 'PG103 cols 1555-1556 (Photii Bibliotheca cod. CCLXXIX, Bekker 434: Superstitiones variae)', 'pg104_photius_superstitiones_p786')
        ]
    ),

    # 7. Festi ludi vetantur -> 461, 462 (PG104 pp. 44, 46)
    'opening:fbab0abb1159b8578fdc80d75dbf236dba339c6adff7903e957509f16bc13a7f': (
        'opening:fbab0abb1159b8578fdc80d75dbf236dba339c6adff7903e957509f16bc13a7f',
        [
            ('PG104', 44, 'PG104 cols 77-78 (Photii Bibliotheca cod. CCLVII, Bekker 461: Festi ludi vetantur)', 'pg104_photius_ludi_p44'),
            ('PG104', 46, 'PG104 cols 81-82 (Photii Bibliotheca cod. CCLVII, Bekker 462: Festi ludi vetantur)', 'pg104_photius_ludi_p46')
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


def resolve_pg104_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PG104 item to physical destination files."""
    if item_id not in PG104_ITEMS_RAW:
        return None
    group_id, dests = PG104_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint77(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 77 recovery for PG104 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PG104' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PG104',
        'sprint': 77,
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
        res = resolve_pg104_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 77: PG104 Photius Bibliotheca Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint77(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
