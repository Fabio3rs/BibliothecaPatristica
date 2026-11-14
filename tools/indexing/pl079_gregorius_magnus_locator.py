"""
PL079 S. Gregorius Magnus Locator
Sprint 89 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PL079:
1. Editorial Context:
   - PL079 contains S. Gregorius Magnus: In Primum Regum Expositiones (cols 9-468), In Canticum Canticorum (cols 471-548), etc.
   - The 'Index in S. Gregorii Commentarios super Scripturas' (cols 1427-1472, physical pp. 720-748)
     indexes the Migne columns with slight OCR digit duplications in the index:
     1) 'Amor proximi... 2352' -> col 235 (In I Reg. lib. III c. 1, physical p. 147)
     2) 'Asqeli reprobi nomine Philisthhim exprimuntur... 1605' -> cols 165-205 (In I Reg. lib. II-III, physical pp. 109, 89)
     3) 'Altare Dei est cor electi auditoris... 2774' -> col 377 (In I Reg. lib. IV c. 5, physical p. 231)
     4) 'est dæmonum timent electi, ut eorum posteriora aliquando non timeant... 168' -> col 168 (In I Reg. lib. II c. 2, physical p. 111)
     5) 'Spicæ hac in vita colligantur, postmodum panis manducabitur... 1835' -> col 193 (In I Reg. lib. II c. 3, physical p. 103)
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

PL079_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Amor proximi... -> 2352 (PL079 p. 147)
    '1264aa2cd85a05f2a388429be5b5846569dedb9edc2060ae08061f4617c32967': (
        'b5c94259ccc2a21340ec3396a5f4579a8cf33e78f21f71b7d7cc584ff2571e3c',
        [
            ('PL079', 147, 'PL079 col 235 / In I Reg. lib. III c. 1 (Deus noster nos quasi filios diligit, cum et subditos nostros tanquam fratres diligimus)', 'pl079_amor_proximi_subditos_p147'),
        ]
    ),

    # 2. Asqeli reprobi nomine Philisthhim exprimuntur -> 1605 (PL079 pp. 109, 89)
    '27b2bb07f7848a0a165c07c2f9b807a0a7d8b9c090d4434a315143ab11c159bf': (
        '246c744f5526f96f7010e3bf7bf85f5777c5c6d86b069a8a59e3550ed0003fa9',
        [
            ('PL079', 109, 'PL079 col 205 / In I Reg. lib. III c. 1 (Philisthiim reprobi maligni spiritus designantur)', 'pl079_philisthiim_reprobi_p109'),
            ('PL079', 89, 'PL079 col 165 / In I Reg. lib. II c. 2 (Philisthiim poculo cadentes pugnant contra catholicos)', 'pl079_philisthiim_reprobi_p089'),
        ]
    ),

    # 3. Altare Dei est cor electi auditoris -> 2774 (PL079 p. 231)
    '3fc54de2606e0672ab3367dd95ad84cae639ecba5c617d2df18db227a36d8cfe': (
        'eb9e6dafd7d80c8e332816bd6f5d750c834dcf5c2b2ce0faaa7824f5b4c07ba5',
        [
            ('PL079', 231, 'PL079 col 377 / In I Reg. lib. IV c. 5 (Quid est enim cor electi auditoris nisi altare Dei?)', 'pl079_altare_dei_cor_electi_p231'),
        ]
    ),

    # 4. est dæmonum timent electi, ut eorum posteriora aliquando non timeant -> 168 (PL079 p. 111)
    'beef35041b504ba956aabb65f2af5bed6f5086de0953aaf29ba42885ae8524c1': (
        '3fbaf6e07d78b80ee79dfa8093f2a7e239125a42591bcdb8c9970fb494c9ba81',
        [
            ('PL079', 111, 'PL079 col 168 / In I Reg. lib. II c. 2 (A facie Philisthinorum id est daemonum timent electi ut eorum posteriora non timeant)', 'pl079_daemonum_timent_electi_p111'),
        ]
    ),

    # 5. Spicæ hac in vita colligantur, postmodum panis manducabitur -> 1835 (PL079 p. 103)
    'd5a79fd9bdaa49801b7f6955345b6397e06102b9ed22eac01c31d4fdeaa02862': (
        'a3b3e4bee580377de32b8d44c68f30d5f69bd4adf3b90f7a4dd01fce90f42f86',
        [
            ('PL079', 103, 'PL079 col 193 / In I Reg. lib. II c. 3 (In ista vita non sumimus panem sed spicas metimus, in illa autem panem manducamus)', 'pl079_spicae_in_vita_colligantur_p103'),
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


def resolve_pl079_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL079 item to physical destination files."""
    if item_id not in PL079_ITEMS_RAW:
        return None
    group_id, dests = PL079_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint89(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 89 recovery for PL079 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL079' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL079',
        'sprint': 89,
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
        res = resolve_pl079_item(item_id)
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
    parser = argparse.ArgumentParser(description="PL079 S. Gregorius Magnus Locator (Sprint 89)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint89(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
