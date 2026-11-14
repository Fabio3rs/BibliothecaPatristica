"""
PL145 S. Petrus Damianus Locator
Sprint 87 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PL145:
1. Editorial Context:
   - PL145 contains S. Petrus Damianus: Opera Omnia, Tomus III (Opuscula, cols 1-858) and Tomus IV (Collectanea in V. T. et N. T., etc., cols 859-1224).
   - The 'Index Rerum Præcipuarum' at the end of PL145 (cols 1187-1224, physical pp. 595-614) indexes Gaetani's Roman edition:
     * 't. III' references -> Tomus III (Opuscula in PL145), cited by column numbers
     * 't. IV' references -> Tomus IV (Collectanea in PL145), cited by column/chapter numbers
   - The 5 residual items are compound multi-reference blocks resulting from line concatenations in early extraction:
     1) 't. III. Odor sterilitate non brutis, Collect. V. T.' -> 220 et seq (Collect. in V. T. in Tomus IV, physical pp. 520, 521, and Tomus III col 625, physical p. 317)
     2) 'quorumdam de gradibus consanguinitatis, 179, t. III...' -> 444 et seq (Tomus III cols 179 and 444, physical pp. 95, 227, 80)
     3) 'Privilegium concessum Henrico imperatori... 55, t. III' -> 571, t. III (Tomus III cols 55 and 571, physical pp. 33, 291)
     4) 'Professio, sive promissio servorum qualis, 556, t. III...' -> 87, t. IV (Tomus III cols 556, 292, physical pp. 283, 151, and Tomus IV Collect. V. T., physical p. 504)
     5) 't. IV. Septem lucernæ candelabri... Collect. V. T., 116, t. IV...' -> 158 et seq (Tomus IV Collect. V. T., physical p. 520, and Tomus III cols 187, 158, physical pp. 99, 84)
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

PL145_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Odor sterilitate non brutis, Collect. V. T. -> 220 et seq (PL145 pp. 520, 521, 317)
    'group:c6cf2d33a3bd2a2c12663c4c61b246c333e0fa0f1ba058400c05076bb362ddb4': (
        'c6cf2d33a3bd2a2c12663c4c61b246c333e0fa0f1ba058400c05076bb362ddb4',
        [
            ('PL145', 520, 'PL145 cols 1029-1030 / Collect. in V. T. (Ex operibus S. Petri Damiani: odor et sacrificia)', 'pl145_odor_sterilitate_collect_vt_p520'),
            ('PL145', 521, 'PL145 cols 1031-1032 / Collect. in V. T. continuatio', 'pl145_odor_sterilitate_collect_vt_p521'),
            ('PL145', 317, 'PL145 cols 625-626 / Tomus III col 625 (Opusc. XXXVI: Nihil bonum nisi ab eo)', 'pl145_odor_sterilitate_collect_vt_p317'),
        ]
    ),

    # 2. quorumdam de gradibus consanguinitatis, 179, t. III... -> 444 et seq (PL145 pp. 95, 227, 80)
    'group:ce7bf5616ac6045bdd3d7afa75f7bea8700ab288042c238684d3989d1de25964': (
        'ce7bf5616ac6045bdd3d7afa75f7bea8700ab288042c238684d3989d1de25964',
        [
            ('PL145', 95, 'PL145 cols 179-180 / Tomus III col 179 (Opusc. VIII: De parentelae gradibus et consanguinitate)', 'pl145_gradibus_consanguinitatis_p95'),
            ('PL145', 227, 'PL145 cols 443-444 / Tomus III col 444 (Opusc. XIX: Consuetudinis antiquae necessitas aliquando excusat)', 'pl145_gradibus_consanguinitatis_p227'),
            ('PL145', 80, 'PL145 cols 149-150 / Tomus III col 149 (Opusc. VI: Consecrari denuo nefas est)', 'pl145_gradibus_consanguinitatis_p80'),
        ]
    ),

    # 3. Privilegium concessum Henrico imperatori... 55, t. III -> 571, t. III (PL145 pp. 33, 291)
    '51944e0e2c8e464852170ea82f4e98f45cadcf4797de715f9ec0a64eb766e75e': (
        '659659a3541ac21bc2d5b741e7d2a1fa3e61896eea23d4a08a1a02218fc8b99a',
        [
            ('PL145', 33, 'PL145 cols 55-56 / Tomus III col 55 (Opusc. IV: Privilegium concessum Henrico imperatori in pontifice eligendo)', 'pl145_privilegium_henrico_imperatori_p33'),
            ('PL145', 291, 'PL145 cols 571-572 / Tomus III col 571 (Opusc. XXXIII: Probatio ad religionem venientium cur exigatur)', 'pl145_privilegium_henrico_imperatori_p291'),
        ]
    ),

    # 4. Professio, sive promissio servorum qualis, 556, t. III... -> 87, t. IV (PL145 pp. 283, 151, 504)
    'ed259cbcf5087e8a0de77526368400cdc7d5ef476067f4f10ae3a2352b2c0265': (
        '659659a3541ac21bc2d5b741e7d2a1fa3e61896eea23d4a08a1a02218fc8b99a',
        [
            ('PL145', 283, 'PL145 cols 555-556 / Tomus III col 556 (Opusc. XXXII: Professio sive promissio servorum)', 'pl145_professio_servorum_p283'),
            ('PL145', 151, 'PL145 cols 291-292 / Tomus III col 292 (Opusc. XII: Professio religiosa cur dicatur secundus baptismus)', 'pl145_professio_servorum_p151'),
            ('PL145', 504, 'PL145 cols 997-998 / Tomus IV cap. 87 (Collect. in V. T.: Prudentia mundi inimica Deo)', 'pl145_professio_servorum_p504'),
        ]
    ),

    # 5. Septem lucernæ candelabri... Collect. V. T., 116, t. IV -> 158 et seq (PL145 pp. 520, 99, 84)
    'group:912a41205d969f3c344fcd244d1f7cb507a1cb0882d921ecbe11595939856bdc': (
        '912a41205d969f3c344fcd244d1f7cb507a1cb0882d921ecbe11595939856bdc',
        [
            ('PL145', 520, 'PL145 cols 1029-1030 / Tomus IV Collect. V. T. (Septem lucernae candelabri dona Spiritus sancti)', 'pl145_septem_lucernae_candelabri_p520'),
            ('PL145', 99, 'PL145 cols 187-188 / Tomus III col 187 (Opusc. VIII: Septima generatio in pronepotibus non habetur)', 'pl145_septem_lucernae_candelabri_p99'),
            ('PL145', 84, 'PL145 cols 157-158 / Tomus III col 158 (Opusc. VI: Simoniacis bona ipsa nocent)', 'pl145_septem_lucernae_candelabri_p84'),
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


def resolve_pl145_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL145 item to physical destination files."""
    if item_id not in PL145_ITEMS_RAW:
        return None
    group_id, dests = PL145_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint87(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 87 recovery for PL145 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL145' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL145',
        'sprint': 87,
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
        res = resolve_pl145_item(item_id)
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
    parser = argparse.ArgumentParser(description="PL145 S. Petrus Damianus Locator (Sprint 87)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint87(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
