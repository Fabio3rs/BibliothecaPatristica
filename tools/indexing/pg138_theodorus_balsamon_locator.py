"""
PG138 Theodorus Balsamon Locator
Sprint 84 of Onomastic Catalog Recovery

Resolves all 6 residual source_only items from PG138:
1. Editorial Context:
   - PG138 contains Theodorus Balsamon: Commentaria in Canones SS. Apostolorum, Conciliorum et in Epistolas Canonicas (Tomus II).
   - The Index Analyticus at the end of PG138 (pp. 775-812) indexes both Tomus I (PG137) and Tomus II (PG138) of Beveridge's Synodikon edition (Oxonii 1672):
     * 'I, xxx' references -> Beveridge Tomus I (in PG137)
     * 'II, xxx' references -> Beveridge Tomus II (in PG138)
   - The 6 residual items:
     1) 'De Græcis moribus non utendis' -> I, 240 (Beveridge Tomus I p. 240 / Concilium in Trullo can. 71: Eos qui leges docentur Græcis moribus uti non oportere, PG137 physical pp. 414, 415)
     2) 'Mulier non est curatrix' -> I, 240 (Beveridge Tomus I p. 240 / Concilium in Trullo can. 70, Balsamon in Basilic. XXXVII tit. 5 c. 20: Mulier non est curatrix, PG137 physical p. 414)
     3) 'Pro se solis agunt mulier et cæcus' -> I, 240 (Beveridge Tomus I p. 240 / Concilium in Trullo can. 70, Balsamon in Basilic. VIII tit. 1 c. 1: Pro se solis causas agunt mulier et cæcus, PG137 physical p. 414)
     4) 'Nec locum habere in diaconico, etc.' -> ibid., 462 (Beveridge Tomus I p. 462 / Concilium Laodicenum can. 21: Non oportet ministros locum habere in diaconico; can. 22-25: orarium, ostia, panis, PG137 physical pp. 739, 740, 741)
     5) 'Picturæ turpes vetitæ' -> I, 240 (Beveridge Tomus I p. 240 / Concilium in Trullo can. 100: Picturæ quæ ad turpia aspectum movent non sunt pingendæ, PG137 physical pp. 483, 484, 414)
     6) 'Tutelæ munera subire prohibetur feminis, nisi sit mater, vel avia' -> I, 240 (Beveridge Tomus I p. 240 / Concilium in Trullo can. 70, Balsamon in Novell. 118: Feminis prohibemus ne tutelæ munera subeant nisi sit mater vel avia, PG137 physical p. 414)
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

PG138_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. De Græcis moribus non utendis -> I, 240 (PG137 pp. 414, 415)
    'dfc0eae1c7d1f457b8ff5142f83b9441c5d91a9ce5af2736c30c09b4df11973b': (
        '4efc8a0e74991c735f03f52841f38cf558bf33e664ea41cf1a0302f778fe2cb5',
        [
            ('PG137', 414, 'PG137 col 755 / Beveridge I p. 240 (Concilium in Trullo can. 71: Eos qui leges docentur Græcis moribus uti non oportere)', 'pg138_graecis_moribus_non_utendis_p414'),
            ('PG137', 415, 'PG137 col 757 (Concilium in Trullo can. 71 continuatio: Scholia Balsamonis)', 'pg138_graecis_moribus_non_utendis_p415'),
        ]
    ),

    # 2. Mulier non est curatrix -> I, 240 (PG137 p. 414)
    '1ec810cf4859545e468c704c88656aef4c8a0d3a4e74de73f33d5b70d72b9262': (
        '05f5aedd1ee4928237b41a24bcac6ec2d55a6372a9e2cb4bfb30880e14fe3ee1',
        [
            ('PG137', 414, 'PG137 col 756 / Beveridge I p. 240 (Concilium in Trullo can. 70 scholion Balsamonis: Basilic. lib. XXXVII tit. 5 c. 20, Mulier non est curatrix)', 'pg138_mulier_non_est_curatrix_p414'),
        ]
    ),

    # 3. Pro se solis agunt mulier et cæcus -> I, 240 (PG137 p. 414)
    '2e2122bba5ed7bf97192e24d6db5b11099667f7937ccc5a8dfd2c56bdd741f0f': (
        '876836bccd0dbe6ba381ecd00556fdc1fbeffca130310141fc020d0de154f354',
        [
            ('PG137', 414, 'PG137 col 755 / Beveridge I p. 240 (Concilium in Trullo can. 70 scholion Balsamonis: Basilic. lib. VIII tit. 1 c. 1, Pro se solis causas agunt mulier et cæcus)', 'pg138_pro_se_solis_mulier_caecus_p414'),
        ]
    ),

    # 4. Nec locum habere in diaconico, etc. -> ibid., 462 (PG137 pp. 739, 740, 741)
    'group:bf88714d733e4cf977ebd6e24016a201c79f30278d8b113f63b0b3adab1381fb': (
        'bf88714d733e4cf977ebd6e24016a201c79f30278d8b113f63b0b3adab1381fb',
        [
            ('PG137', 739, 'PG137 cols 1369-1370 / Beveridge I p. 462 (Concilium Laodicenum can. 21: Non oportet ministros locum habere in diaconico et sacra vasa tangere)', 'pg138_nec_locum_diaconico_p739'),
            ('PG137', 740, 'PG137 cols 1371-1372 / Beveridge I p. 463 (Concilium Laodicenum can. 22: orarium et ostia)', 'pg138_nec_locum_diaconico_p740'),
            ('PG137', 741, 'PG137 cols 1373-1374 / Beveridge I p. 461, 463 (Concilium Laodicenum can. 25: minister panem dare non debet)', 'pg138_nec_locum_diaconico_p741'),
        ]
    ),

    # 5. Picturæ turpes vetitæ -> I, 240 (PG137 pp. 483, 484, 414)
    'fda430d6ba731832a0c0c38de5d830f14761f17bf0e507c47c706d3c52d5d6ce': (
        '348cec7d6d8da7a38bf5b67f241fc08d4cc6db22e83a887b3b4e18ad7e1fb2ed',
        [
            ('PG137', 483, 'PG137 cols 861-862 / Beveridge I p. 279 (Concilium in Trullo can. 100: Picturæ quæ ad turpia aspectum movent non sunt pingendæ)', 'pg138_picturae_turpes_vetitae_p483'),
            ('PG137', 484, 'PG137 cols 863-864 (Concilium in Trullo can. 100 continuatio: Scholia Zonarae et Balsamonis)', 'pg138_picturae_turpes_vetitae_p484'),
            ('PG137', 414, 'PG137 col 755 / Beveridge I p. 240 (Concilium in Trullo can. 71: Græcis moribus et theatris vetitis)', 'pg138_picturae_turpes_vetitae_p414'),
        ]
    ),

    # 6. Tutelæ munera subire prohibetur feminis, nisi sit mater, vel avia -> I, 240 (PG137 p. 414)
    'b0130294cddf0ccc13149258a9a823e803152f07904a03e38061a6bd4a27604a': (
        '1a00d5c2c95481828831c039b515d3520a119050f23b387a7b1175d6b14bc936',
        [
            ('PG137', 414, 'PG137 col 755 / Beveridge I p. 240 (Concilium in Trullo can. 70 scholion Balsamonis: Novell. 118 c. 14 / Basil. XXXVII tit. 4, Tutelæ munera subire prohibetur feminis)', 'pg138_tutelae_munera_feminis_p414'),
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


def resolve_pg138_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PG138 item to physical destination files."""
    if item_id not in PG138_ITEMS_RAW:
        return None
    group_id, dests = PG138_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint84(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 84 recovery for PG138 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PG138' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PG138',
        'sprint': 84,
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
        res = resolve_pg138_item(item_id)
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
    parser = argparse.ArgumentParser(description="PG138 Theodorus Balsamon Locator (Sprint 84)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint84(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
