"""
PL132 Regino Prumiensis et Alii Locator
Sprint 71 of Onomastic Catalog Recovery

Resolves all 8 residual source_only items from PL132:
1. Editorial Context:
   - PL132 contains:
     * Cols 1-174: Reginonis Prumiensis Chronicon & Continuator Chronici
     * Cols 175-400: De Ecclesiasticis Disciplinis et Religione Christiana Libri II
     * Cols 401-478: Hucbaldus Elnonensis (Vita S. Lebuini, Carmina)
     * Cols 477-504: De Harmonica Institutione & Miscellanea
     * Cols 529-536: Roberti Metensis Episcopi Diplomata et Epistolae
     * Cols 645-660: Instrumenta Historica & Hucbaldi Opera
     * Cols 1045-1080 (physical pp. 530-548): INDEX GENERALIS
   - Citations in the INDEX GENERALIS cite Migne column numbers within PL132 directly:
     * 'Venditiones honorum ecclesiasticorum vetitae, 161 et seqq' -> col 161, physical p. 85
     * 'Quomodo fiendae, 166 et seqq' -> col 166, physical p. 87
     * 'Adulterium qui accusare possint, 266 et seq' -> col 266, physical p. 137
     * 'Exoris sororem memeh sibi adauct sociare, 284 et seqq' -> col 284, physical p. 146
     * 'Clericus transfuga fugitivus, 490 et seq' -> col 490, physical p. 249
     * 'Si velata ad nuptias transeerit, 491 et seq' -> col 491, physical p. 250
     * 'Inquisitio in visitatione diœceseos, 530 et seq' -> col 530, physical p. 269
     * 'Hunbaldus Uriacensis dominus, 646 et seqq' -> col 646, physical p. 327
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

PL132_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Venditiones honorum ecclesiasticorum vetitae -> 161 et seqq (p. 85)
    'group:fee412f10d1a84d43cc0af6a5bcf6449e0e74d542f4860f025364fa9853bb7f8': (
        'group:fee412f10d1a84d43cc0af6a5bcf6449e0e74d542f4860f025364fa9853bb7f8',
        [
            ('PL132', 85, 'PL132 col 161 (Continuator Chronici Reginonis: Venditiones honorum ecclesiasticorum vetitae)', 'pl132_regino_col161_p85')
        ]
    ),

    # 2. Quomodo fiendae -> 166 et seqq (p. 87)
    'group:e0f8f5d158acfeee22de97a586ed7f9563b568c01a925cda3fadeb0f730bda92': (
        'group:e0f8f5d158acfeee22de97a586ed7f9563b568c01a925cda3fadeb0f730bda92',
        [
            ('PL132', 87, 'PL132 col 166 (Continuator Chronici Reginonis: Quomodo fiendae)', 'pl132_regino_col166_p87')
        ]
    ),

    # 3. Adulterium qui accusare possint -> 266 et seq (p. 137)
    'group:236d0dc63919887866a1509dc38da7a9678d78c73660925594f8825c424daf7e': (
        'group:236d0dc63919887866a1509dc38da7a9678d78c73660925594f8825c424daf7e',
        [
            ('PL132', 137, 'PL132 col 266 (De Ecclesiasticis Disciplinis lib. I: Adulterium qui accusare possint)', 'pl132_regino_col266_p137')
        ]
    ),

    # 4. Exoris sororem memeh sibi adauct sociare -> 284 et seqq (p. 146)
    'group:36d049f12b54f584ca9c45f3df5573ec9e823eb77608a3eb097d5f1ba7605dfd': (
        'group:36d049f12b54f584ca9c45f3df5573ec9e823eb77608a3eb097d5f1ba7605dfd',
        [
            ('PL132', 146, 'PL132 col 284 (De Ecclesiasticis Disciplinis lib. II: Uxoris sororem audet sociare)', 'pl132_regino_col284_p146')
        ]
    ),

    # 5. Clericus transfuga fugitivus -> 490 et seq (p. 249)
    'group:e2604a58e94e48affca77dee3a95048c2a614eb65bfdf2be4cc113dec4848d7f': (
        'group:e2604a58e94e48affca77dee3a95048c2a614eb65bfdf2be4cc113dec4848d7f',
        [
            ('PL132', 249, 'PL132 col 490 (De Harmonica Institutione / Canones: Clericus transfuga fugitivus)', 'pl132_regino_col490_p249')
        ]
    ),

    # 6. Si velata ad nuptias transeerit -> 491 et seq (p. 250)
    'group:380edc94bdb7e21492f6c1285d5d96399e3e2dc30519cdef62657d6aa8dbf6f9': (
        'group:380edc94bdb7e21492f6c1285d5d96399e3e2dc30519cdef62657d6aa8dbf6f9',
        [
            ('PL132', 250, 'PL132 col 491 (De Harmonica Institutione / Canones: Si velata ad nuptias transeerit)', 'pl132_regino_col491_p250')
        ]
    ),

    # 7. Inquisitio in visitatione diœceseos -> 530 et seq (p. 269)
    'group:8a8e69182e3f56006035df1a30a4e815d351e12b1f1fb67c0a2cd45c6dfa2899': (
        'group:8a8e69182e3f56006035df1a30a4e815d351e12b1f1fb67c0a2cd45c6dfa2899',
        [
            ('PL132', 269, 'PL132 col 530 (Roberti Metensis Episcopi Diplomata: Inquisitio in visitatione dioeceseos)', 'pl132_regino_col530_p269')
        ]
    ),

    # 8. Hunbaldus Uriacensis dominus -> 646 et seqq (p. 327)
    'group:4c127fa7df43634180c6c8a7b4c32916002d35b5db98af1828187150e20f40f1': (
        'group:4c127fa7df43634180c6c8a7b4c32916002d35b5db98af1828187150e20f40f1',
        [
            ('PL132', 327, 'PL132 col 646 (Instrumenta Historica / Hucbaldi Opera: Hunbaldus Uriacensis dominus)', 'pl132_regino_col646_p327')
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


def resolve_pl132_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL132 item to physical destination files."""
    if item_id not in PL132_ITEMS_RAW:
        return None
    group_id, dests = PL132_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint71(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 71 recovery for PL132 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL132' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL132',
        'sprint': 71,
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
        res = resolve_pl132_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 71: PL132 Regino Prumiensis Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint71(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
