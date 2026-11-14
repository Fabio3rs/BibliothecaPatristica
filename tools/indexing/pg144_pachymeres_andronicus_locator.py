"""
PG144 Georgius Pachymeres (De Andronico Palaeologo) Locator
Sprint 83 of Onomastic Catalog Recovery

Resolves all 6 residual source_only items from PG144:
1. Editorial Context:
   - PG144 contains Georgius Pachymeres: De Andronico Palaeologo Libri VII.
   - The Index Generalis at the end of the volume (pp. 705-722) cites:
     * Volume I references (I, xxx) -> Tomus I of Bekker's Bonn CSHB edition (in PG143)
     * Volume II references (II, xxx) -> Tomus II of Bekker's Bonn CSHB edition (in PG144)
     * Bonn edition pagination is printed in the margins and in Possinus' Observationes / Notae.
   - The 6 residual items:
     1) 'Abdicatione Joannis Cosmæ conturbatur' -> II, 345 (Bonn Tomus II p. 345 in Lib. IV c. 29-30: Abdicatio patriarchae Joannis Cosmae, PG144 physical pp. 195, 196, 458)
     2) 'Cuximpaxis opera utitur ad coercendos Persas' -> II, 345 (Bonn Tomus II p. 345 in Lib. IV c. 30: De rebus Cuximpaxis Tochari, PG144 physical pp. 195, 196, 458)
     3) 'Tamujinus conditor regni Mogulicorum' -> II, 805 (Bonn Tomus II p. 805 in Possini Observationes: Tamujinus / Cingis-Kan imperii Tocharorum Mogulici conditor, PG144 physical pp. 381, 382)
     4) 'Gregorii patriarchæ abdicationem aliis suspectam pro sufficienti habet' -> II, 152 (Bonn Tomus II p. 152 in Lib. II c. 17: Mors expatriarchae Gregorii Cyprii, PG144 physical pp. 91, 90)
     5) 'Thomas hebdomas quæ sit' -> II, 805 (Bonn Tomus II p. 805 in Possini Observationes: Thomas hebdomas / computus Mogulicus, PG144 physical pp. 381, 382)
     6) 'Ung-chan ultimus Preslejannensium regum' -> II, 805 (Bonn Tomus II p. 805 in Possini Observationes: Ung-Chan ultimus Presbyter Joannes / rex Preslejannensium, PG144 physical pp. 381, 382)
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

PG144_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Abdicatione Joannis Cosmæ conturbatur -> II, 345 (PG144 pp. 195, 196, 458)
    '15b19dbb015af2321b0334cd04d0380700b91b6fff638605ee9ebf1c01d84ff1': (
        '14916f45c0d8859e00fb97f653d757860ff46d441d14e8b5be0d91c6c4ffb9e1',
        [
            ('PG144', 195, 'PG144 cols 377-378 / Bonn II p. 345 (Lib. IV c. 29-30: Abdicatio patriarchae Joannis Cosmae)', 'pg144_abdicatione_joannis_cosmae_p195'),
            ('PG144', 196, 'PG144 cols 379-380 (Lib. IV c. 30 continuatio)', 'pg144_abdicatione_joannis_cosmae_p196'),
            ('PG144', 458, 'PG144 cols 903-904 (Elenchus Lib. IV: Joannes Cosmas patriarchatus abdicationem mittit)', 'pg144_abdicatione_joannis_cosmae_p458'),
        ]
    ),

    # 2. Cuximpaxis opera utitur ad coercendos Persas -> II, 345 (PG144 pp. 195, 196, 458)
    'f1001749c5cc283ba59c2fe2de1f1535a5f49746efc989e2f10af8a2fed47146': (
        'aa1555bd57c288221e810ab2c864c66643e54b6f6fe9589bdd3609b30938062d',
        [
            ('PG144', 195, 'PG144 cols 377-378 / Bonn II p. 345 (Lib. IV c. 30: De rebus Cuximpaxis Tochari)', 'pg144_cuximpaxis_opera_persas_p195'),
            ('PG144', 196, 'PG144 cols 379-380 / Bonn II p. 346 (Lib. IV c. 30: Coercendi Persas opera Cuximpaxis Tochari)', 'pg144_cuximpaxis_opera_persas_p196'),
            ('PG144', 458, 'PG144 cols 903-904 (Elenchus Lib. IV: Cuximpaxis Tocharus et Persae)', 'pg144_cuximpaxis_opera_persas_p458'),
        ]
    ),

    # 3. Tamujinus conditor regni Mogulicorum -> II, 805 (PG144 pp. 381, 382)
    'bc3c83f5b1db57e4b597961c94e086297d22b2bab9896ceeac4b10a07ce4e080': (
        '865bb49bb77b5a863e414c00469b82875b7501a30a1bf6da799298e3b2e56e01',
        [
            ('PG144', 381, 'PG144 cols 751-752 / Bonn II p. 805 (Possini Observationes: Tamujinus seu Cingis-Kan regni Mogulici conditor)', 'pg144_tamujinus_conditor_mogulicorum_p381'),
            ('PG144', 382, 'PG144 cols 753-754 / Bonn II p. 806 (Possini Observationes continuatio)', 'pg144_tamujinus_conditor_mogulicorum_p382'),
        ]
    ),

    # 4. Gregorii patriarchæ abdicationem aliis suspectam pro sufficienti habet -> II, 152 (PG144 pp. 91, 90)
    '2e2360758bb624eb62d3a82d5379b389364ac7ef607ed838016a290c344b954f': (
        '41454556488d08cb5f903a45c8b5df01c13bf69b6e828d09618bf45f061480f2',
        [
            ('PG144', 91, 'PG144 cols 169-170 / Bonn II p. 152 (Lib. II c. 17: Mors expatriarchae Gregorii Cyprii et abdicatio)', 'pg144_gregorii_patriarchae_abdicationem_p91'),
            ('PG144', 90, 'PG144 cols 167-168 / Bonn II p. 151 (Lib. II c. 16: Theoleptus Philadelphiensis et judicium de Gregorio)', 'pg144_gregorii_patriarchae_abdicationem_p90'),
        ]
    ),

    # 5. Thomas hebdomas quæ sit -> II, 805 (PG144 pp. 381, 382)
    '5a8921203232478f3aba3b171671746a9e71d42f87cc256c17afae64388939ca': (
        '442a8b277f30ca84fcbc365e1d53034870f7be2e68be973d4ee711129b0151f1',
        [
            ('PG144', 381, 'PG144 cols 751-752 / Bonn II p. 805 (Possini Observationes: Thomas hebdomas et computus temporum Mogulicorum)', 'pg144_thomas_hebdomas_p381'),
            ('PG144', 382, 'PG144 cols 753-754 / Bonn II p. 806 (Possini Observationes continuatio)', 'pg144_thomas_hebdomas_p382'),
        ]
    ),

    # 6. Ung-chan ultimus Preslejannensium regum -> II, 805 (PG144 pp. 381, 382)
    'd2287b37377b764ee648dfea4b73a858fa0753c0feb30192b04980352137dc39': (
        '25f1b79518d5f071ef0b9008c4e856ae3b923a879c1d47187431de348a1507ae',
        [
            ('PG144', 381, 'PG144 cols 751-752 / Bonn II p. 805 (Possini Observationes: Ung-Chan ultimus Preslejannensium seu Presbyteri Joannis regum)', 'pg144_ung_chan_preslejannensium_p381'),
            ('PG144', 382, 'PG144 cols 753-754 / Bonn II p. 806 (Possini Observationes continuatio)', 'pg144_ung_chan_preslejannensium_p382'),
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


def resolve_pg144_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PG144 item to physical destination files."""
    if item_id not in PG144_ITEMS_RAW:
        return None
    group_id, dests = PG144_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint83(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 83 recovery for PG144 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PG144' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PG144',
        'sprint': 83,
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
        res = resolve_pg144_item(item_id)
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
    parser = argparse.ArgumentParser(description="PG144 Pachymeres Locator (Sprint 83)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint83(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
