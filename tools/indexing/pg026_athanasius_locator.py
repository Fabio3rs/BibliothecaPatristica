"""
PG026 S. Athanasius Locator
Sprint 81 of Onomastic Catalog Recovery

Resolves all 6 residual source_only items from PG026:
1. Editorial Context:
   - PG025 and PG026 contain the works of St. Athanasius of Alexandria reprinted from the Benedictine edition of Montfaucon (Paris, 1698, Tomus I):
     * Montfaucon pp. 1-320 are in PG025 (Contra Gentes, De Incarnatione Verbi, Apologia contra Arianos, Apologia de fuga sua, Apologia ad Constantium, etc.)
     * Montfaucon pp. 321-760 are in PG026 (Orationes contra Arianos I-III, Epistolae, Vita S. Antonii, etc.)
     * At the end of PG026 (cols 1461-1500, physical pp. 737-765) is the general INDEX ANALYTICUS.
     * All page numbers in this index refer to Montfaucon's pages, which Migne faithfully printed inside the text.
   - The 6 residual items:
     1) 'Etsi servus vocatur est tamen natura Filius' -> 372 et seqq (Montfaucon p. 372 in Oratio II contra Arianos, PG026 physical p. 82 and p. 83)
     2) 'Quo sensu dicatur fidelis' -> 374 et seqq (Montfaucon p. 374 in Oratio II contra Arianos, PG026 physical p. 85 and p. 86)
     3) 'Non est angelus' -> 367 et seqq (Montfaucon p. 367 in Oratio II contra Arianos, PG026 physical p. 75 and p. 76)
     4) 'Fugere licet in persecutione, in Apologia de fuga' -> 255 et seqq (Montfaucon p. 255 in Apologia de fuga sua, PG025 physical p. 608 and p. 610)
     5) 'Id solus Christus delere potuit' -> 40 passim (Montfaucon p. 40 in De Incarnatione Verbi Dei, PG025 physical p. 336 and p. 337)
     6) 'In persecutione fugiendum' -> 256 et seqq (Montfaucon p. 256 in Apologia de fuga sua, PG025 physical p. 610 and p. 611)
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

PG026_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Etsi servus vocatur est tamen natura Filius -> 372 et seqq (PG026 pp. 82, 83)
    'group:610effbde6d379f2d5c276ed91e779074d3195e1092236f0d47f6d5f1974b0b0': (
        'group:610effbde6d379f2d5c276ed91e779074d3195e1092236f0d47f6d5f1974b0b0',
        [
            ('PG026', 82, 'PG026 cols 153-154 / Montfaucon p. 372 (Oratio II contra Arianos: Etsi servus vocatur, est tamen natura Filius)', 'pg026_athanasius_servus_filius_p82'),
            ('PG026', 83, 'PG026 cols 155-156 / Montfaucon p. 373 (Oratio II contra Arianos continuatio)', 'pg026_athanasius_servus_filius_p83'),
        ]
    ),

    # 2. Quo sensu dicatur fidelis -> 374 et seqq (PG026 pp. 85, 86)
    'group:985adf51de0afb016ee70d8ad4e94e3621bf325515f4615afbaf8066dbff4a79': (
        'group:985adf51de0afb016ee70d8ad4e94e3621bf325515f4615afbaf8066dbff4a79',
        [
            ('PG026', 85, 'PG026 cols 159-160 / Montfaucon p. 374 (Oratio II contra Arianos: Quo sensu dicatur fidelis)', 'pg026_athanasius_fidelis_sensu_p85'),
            ('PG026', 86, 'PG026 cols 161-162 / Montfaucon p. 375 (Oratio II contra Arianos continuatio)', 'pg026_athanasius_fidelis_sensu_p86'),
        ]
    ),

    # 3. Non est angelus -> 367 et seqq (PG026 pp. 75, 76)
    'group:c8b19e788202018033e95000ed71fb424bee4cebb4a11c52ec09d71563b10b67': (
        'group:c8b19e788202018033e95000ed71fb424bee4cebb4a11c52ec09d71563b10b67',
        [
            ('PG026', 75, 'PG026 cols 139-140 / Montfaucon p. 367 (Oratio II contra Arianos: Christus non est angelus sed Dominus)', 'pg026_athanasius_non_est_angelus_p75'),
            ('PG026', 76, 'PG026 cols 141-142 / Montfaucon p. 368 (Oratio II contra Arianos continuatio)', 'pg026_athanasius_non_est_angelus_p76'),
        ]
    ),

    # 4. Fugere licet in persecutione, in Apologia de fuga -> 255 et seqq (PG025 pp. 608, 610)
    'group:d7fc65aad015a6b4d2f45d4a1ccd5c246235e8a4abf03eb61b37012880c03a06': (
        'group:d7fc65aad015a6b4d2f45d4a1ccd5c246235e8a4abf03eb61b37012880c03a06',
        [
            ('PG025', 608, 'PG025 cols 647-648 / Montfaucon p. 255 (Apologia de fuga sua: Fugere licet in persecutione)', 'pg026_athanasius_apologia_fuga_p608'),
            ('PG025', 610, 'PG025 cols 651-652 / Montfaucon p. 256 (Apologia de fuga sua continuatio)', 'pg026_athanasius_apologia_fuga_p610'),
        ]
    ),

    # 5. Id solus Christus delere potuit -> 40 passim (PG025 pp. 336, 337)
    'group:715eefd2f1bf40a39b507bfbd2e4555804e7159ed5b45c74dcd3e00e437846ce': (
        'group:715eefd2f1bf40a39b507bfbd2e4555804e7159ed5b45c74dcd3e00e437846ce',
        [
            ('PG025', 336, 'PG025 cols 103-104 / Montfaucon p. 40 (De Incarnatione Verbi c. 4-5: Peccatum hominem ad naturae conditionem revocavit, id solus Christus delere potuit)', 'pg026_athanasius_solus_christus_delere_p336'),
            ('PG025', 337, 'PG025 cols 105-106 / Montfaucon p. 41 (De Incarnatione Verbi c. 6)', 'pg026_athanasius_solus_christus_delere_p337'),
        ]
    ),

    # 6. In persecutione fugiendum -> 256 et seqq (PG025 pp. 610, 611)
    'group:c4037de1ede6f352d4049f0f324d1a3600aa418b2d8730ffa387f6ffb2250da1': (
        'group:c4037de1ede6f352d4049f0f324d1a3600aa418b2d8730ffa387f6ffb2250da1',
        [
            ('PG025', 610, 'PG025 cols 651-652 / Montfaucon p. 256 (Apologia de fuga sua: In persecutione fugiendum)', 'pg026_athanasius_persecutione_fugiendum_p610'),
            ('PG025', 611, 'PG025 cols 653-654 / Montfaucon p. 257 (Apologia de fuga sua continuatio)', 'pg026_athanasius_persecutione_fugiendum_p611'),
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


def resolve_pg026_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PG026 item to physical destination files."""
    if item_id not in PG026_ITEMS_RAW:
        return None
    group_id, dests = PG026_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint81(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 81 recovery for PG026 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PG026' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PG026',
        'sprint': 81,
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
        res = resolve_pg026_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 81: PG026 S. Athanasius Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    rep = execute_sprint81(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
