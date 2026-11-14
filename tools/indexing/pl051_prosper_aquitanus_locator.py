"""
PL051 S. Prosper Aquitanus Locator
Sprint 91 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PL051:
1. Editorial Context:
   - PL051 contains S. Prosper Aquitanus: Opera Omnia.
   - The 'Index Rerum et Sententiarum in S. Prosperum' (cols 931-974, physical pp. 493-514)
     indexes columns and verse numbers of poetic works, especially 'Carmen de Ingratis' (cols 91-148, physical pp. 59-77):
     1) 'Quod preces Ecclesiæ non exaudiantur pro omnibus...' -> 865 et seq (Carmen de Ingratis vers. 865 et seq., col 140, physical p. 74)
     2) 'Egregia casus hominis descriptio... vers. 575 et seq... Peccati originalis effectus' -> 853 et seq (Carmen de Ingratis vers. 853 et seq. on p. 74, and vers. 575 et seq. on p. 69)
     3) 'Pelagii dogmata, 115, vers' -> 1 et seqq (Carmen de Ingratis col 115, vers. 1 et seqq., physical p. 60)
     4) 'Pelagius quid senserit de mortalitate primorum hominum, 116, vers' -> 5 (Carmen de Ingratis col 116, vers. 5, physical p. 60)
     5) 'Virtus sine gratia nulla vera est' -> 871 et seq (Carmen de Ingratis vers. 871 et seq., col 140, physical p. 74)
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

PL051_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Quod preces Ecclesiæ non exaudiantur pro omnibus... -> 865 et seq (PL051 p. 74)
    'group:50132f54ea8962ff3ef8e8d3954a49b7b8c81b71fb160fdb5ec6f785e7dce33e': (
        '50132f54ea8962ff3ef8e8d3954a49b7b8c81b71fb160fdb5ec6f785e7dce33e',
        [
            ('PL051', 74, 'PL051 col 140 / Carmen de Ingratis vers. 865 et seq. (Credit, amatque suum mundi sapientia sensum: ad occulta Dei judicia referendum)', 'pl051_preces_ecclesiae_judicia_dei_p074'),
        ]
    ),

    # 2. Egregia casus hominis descriptio... vers. 575 et seq... Peccati originalis effectus -> 853 et seq (PL051 pp. 74, 69)
    'group:eac3f9e93bcb60733da1e8e4d9cf233989e888824556a533a2a20f5ff6f4a500': (
        'eac3f9e93bcb60733da1e8e4d9cf233989e888824556a533a2a20f5ff6f4a500',
        [
            ('PL051', 74, 'PL051 col 139 / Carmen de Ingratis vers. 853 et seq. (Nemo etenim est qui non cum vulnere primi sit patris genitus: effectus peccati originalis)', 'pl051_peccati_originalis_effectus_p074'),
            ('PL051', 69, 'PL051 cols 129-130 / Carmen de Ingratis vers. 575 et seq. (Casus hominis descriptio et mentis caecitas)', 'pl051_peccati_originalis_effectus_p069'),
        ]
    ),

    # 3. Pelagii dogmata, 115, vers -> 1 et seqq (PL051 p. 60)
    'e5105ef01c8b6b28f55bf28fcfcae1eeb8bf30b569a37fb08fd23d63f4c6a011': (
        '519599032874dfb62b5facb7d006fd493b8902cac6b72d3fc02ec741f5bd750a',
        [
            ('PL051', 60, 'PL051 col 115 / Carmen de Ingratis vers. 1 et seqq. (Sine gratia Dei: Pelagii dogmata)', 'pl051_pelagii_dogmata_p060'),
        ]
    ),

    # 4. Pelagius quid senserit de mortalitate primorum hominum, 116, vers -> 5 (PL051 p. 60)
    'e11160168f4272a48c74f5a6801105ba8ac1d118e1d79d19eb02fd46ccd7477e': (
        '519599032874dfb62b5facb7d006fd493b8902cac6b72d3fc02ec741f5bd750a',
        [
            ('PL051', 60, 'PL051 col 116 / Carmen de Ingratis vers. 5 (Pelagius de mortalitate primorum hominum)', 'pl051_pelagius_mortalitate_p060'),
        ]
    ),

    # 5. Virtus sine gratia nulla vera est -> 871 et seq (PL051 p. 74)
    'group:c331dd26a37e979e1d2de1ddd601351af61240b91dcf3792ad4b97f108695988': (
        'c331dd26a37e979e1d2de1ddd601351af61240b91dcf3792ad4b97f108695988',
        [
            ('PL051', 74, 'PL051 col 140 / Carmen de Ingratis vers. 871 et seq. (Virtus sine gratia nulla vera est)', 'pl051_virtus_sine_gratia_p074'),
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


def resolve_pl051_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL051 item to physical destination files."""
    if item_id not in PL051_ITEMS_RAW:
        return None
    group_id, dests = PL051_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint91(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 91 recovery for PL051 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL051' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL051',
        'sprint': 91,
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
        res = resolve_pl051_item(item_id)
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
    parser = argparse.ArgumentParser(description="PL051 S. Prosper Aquitanus Locator (Sprint 91)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint91(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
