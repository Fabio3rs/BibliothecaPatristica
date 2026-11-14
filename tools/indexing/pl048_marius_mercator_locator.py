"""
PL048 Marius Mercator Locator
Sprint 75 of Onomastic Catalog Recovery

Resolves all 7 residual source_only items from PL048:
1. Editorial Context:
   - PL048 contains the complete works of Marius Mercator, together with the dissertations and notes
     of Jean Garnier (Paris, 1673) and Etienne Baluze (Paris, 1684):
     * Cols 11-172: Marius Mercator: Scripta ad Pelagianam haeresim pertinentia (Commonitorium, Subnotationes)
     * Cols 173-698: Jean Garnier: Dissertationes VII de Haeresi et Libris Pelagianis
     * Cols 699-1070: Scripta ad Nestorianam haeresim pertinentia
     * Cols 1071-1150: Excerpta ex epistolis Theodoreti & Notae
     * Cols 1151-1278 (physical pp. 600-642): INDEX RERUM ET VERBORUM
   - Citations in the INDEX RERUM ET VERBORUM cite Migne columns within PL048 directly:
     * 'Cœlestius damnatur primum Carthagine a concilio episcoporum, 6 seq' -> Commonitorium, physical p. 46
     * 'Contradictio, 135 seqq' -> Liber Subnotationum in verba Juliani, col 135, physical p. 71
     * 'Fuit extraordinaria, 341 seq' -> Dissert. II de Synodis in Causa Pelagianorum, col 341, physical p. 174
     * 'Quinta in causa Pelagianorum Nestorio adhærentium, 572 seqq' -> Dissert. V/VI, col 572, physical p. 289
     * 'Coercendæ remedium, 579 seq' -> Dissert. VI, col 579, physical p. 293
     * 'Aurelius Carthaginensis, 595 seqq' -> Dissert. VI, col 595, physical p. 301
     * 'Episcoporum commutare sedem olim nefas erat, 4077' -> misread for col 1077, physical p. 542
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

PL048_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Cœlestius damnatur primum Carthagine a concilio episcoporum -> 6 seq (p. 46)
    'group:eb38fc865e030feb62a03fb84cd07af7aed479fcfd64cd9c01a1d36389834e11': (
        'group:eb38fc865e030feb62a03fb84cd07af7aed479fcfd64cd9c01a1d36389834e11',
        [
            ('PL048', 46, 'PL048 cols 67-68 (Commonitorium super nomine Coelestii cap. 2-6: Coelestius in judicio Carthaginensi damnatur)', 'pl048_mercator_coelestius_p46')
        ]
    ),

    # 2. Contradictio -> 135 seqq (p. 71)
    'group:7adbe2ae925501e69209a3a3827f20cd2bc5d2934c9bbeccc8f5b8f8ce61b933': (
        'group:7adbe2ae925501e69209a3a3827f20cd2bc5d2934c9bbeccc8f5b8f8ce61b933',
        [
            ('PL048', 71, 'PL048 cols 135-136 (Liber Subnotationum in verba Juliani: Contradictio)', 'pl048_mercator_contradictio_p71')
        ]
    ),

    # 3. Fuit extraordinaria -> 341 seq (p. 174)
    'group:79fb3a9a491642437988d83ff4607afc18204ec9139d841e5a9d517f0133f553': (
        'group:79fb3a9a491642437988d83ff4607afc18204ec9139d841e5a9d517f0133f553',
        [
            ('PL048', 174, 'PL048 cols 341-342 (Dissert. II de Synodis in Causa Pelagianorum: Fuit extraordinaria)', 'pl048_mercator_synodus_p174')
        ]
    ),

    # 4. Quinta in causa Pelagianorum Nestorio adhærentium -> 572 seqq (p. 289)
    'group:c031f11d4304a33916446261b26aa33252e732734a023b9f71b94c3976257ac7': (
        'group:c031f11d4304a33916446261b26aa33252e732734a023b9f71b94c3976257ac7',
        [
            ('PL048', 289, 'PL048 cols 571-572 (Dissertatio in Causa Pelagianorum Nestorio adhaerentium)', 'pl048_mercator_nestorio_p289')
        ]
    ),

    # 5. Coercendæ remedium -> 579 seq (p. 293)
    'group:02f95324e4fe96dd547feec5d520b2ba69fd2701d3b525ccbdc76bed255b9e78': (
        'group:02f95324e4fe96dd547feec5d520b2ba69fd2701d3b525ccbdc76bed255b9e78',
        [
            ('PL048', 293, 'PL048 cols 579-580 (Dissert. VI de Scriptis adv. Haeresim Pelagianam: Coercendae remedium)', 'pl048_mercator_coercendae_p293')
        ]
    ),

    # 6. Aurelius Carthaginensis -> 595 seqq (p. 301)
    'group:43ef53a2ea97596fa17beff0bf41cc6f0cc48a0df83a59497ec062a19ef21629': (
        'group:43ef53a2ea97596fa17beff0bf41cc6f0cc48a0df83a59497ec062a19ef21629',
        [
            ('PL048', 301, 'PL048 cols 595-596 (Dissert. VI: Aurelius Carthaginensis episcopus)', 'pl048_mercator_aurelius_p301')
        ]
    ),

    # 7. Episcoporum commutare sedem olim nefas erat -> 4077 (col 1077, p. 542)
    '97ed16adc97942a9c05c29338519fc4094d8a030315a404b0919222e03baf8f5': (
        '97ed16adc97942a9c05c29338519fc4094d8a030315a404b0919222e03baf8f5',
        [
            ('PL048', 542, 'PL048 col 1077 (Excerpta ex Epistolis Theodoreti: Episcoporum commutare sedem olim nefas erat)', 'pl048_mercator_theodoretus_col1077_p542')
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


def resolve_pl048_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL048 item to physical destination files."""
    if item_id not in PL048_ITEMS_RAW:
        return None
    group_id, dests = PL048_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint75(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 75 recovery for PL048 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL048' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL048',
        'sprint': 75,
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
        res = resolve_pl048_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 75: PL048 Marius Mercator Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint75(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
