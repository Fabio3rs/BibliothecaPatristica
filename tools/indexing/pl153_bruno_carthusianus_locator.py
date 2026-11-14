"""
PL153 S. Bruno Carthusianus Locator
Sprint 86 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PL153:
1. Editorial Context:
   - PL153 contains S. Bruno Carthusianus: Opera Omnia, Pars II (Expositio in omnes Epistolas Pauli, Statuta, etc.).
   - The 'Index in S. Brunonem' at the end of PL153 (cols 1151-1160, physical pp. 580-584) indexes the Cologne 1611 edition by Theodor Petreius:
     * 'I, xxx' references -> Tomus I (Expositio in Psalmos in PL152), Petreius marginal column numbers
     * 'II, xxx' references -> Tomus II (Expositio in Epistolas Pauli in PL153), Petreius marginal column numbers
   - The 5 residual items:
     1) 'Baptismus tripliciter dicitur' -> II, 377 (In Epist. ad Hebr. c. 6 v. 2, Petreius col 377: aqua, lacrymae, sanguis, PL153 physical pp. 262, 263)
     2) 'Vir dicitur in Psalmis primo' -> I, 9 (In Psalmum I v. 1, Petreius col 2: Beatus vir, viriliter et mature, PL152 physical p. 324)
     3) 'Canticum quid sit' -> I, 9 (Prologus in Psalmos / In Psalmum I, Petreius col 1-2, PL152 physical pp. 324, 323)
     4) 'Cauterizare quid' -> I, 321 (In Psalmum LXXIX, Petreius col 321: ignis, ultio, operatio, PL152 physical p. 535)
     5) 'Obstinatæ excommunia‑ tiones' -> I, 590 (In Psalmum CXLIX v. 6-8, Petreius col 590: gladii ancipites contra obstinatos, PL152 physical pp. 713, 712)
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

PL153_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Baptismus tripliciter dicitur -> II, 377 (PL153 pp. 262, 263)
    '2c5c2222288ab8df451db6493ee54ca3b3d0134ff53ee51bd809fde0721d9906': (
        '8930c730313d7473bc5d3ed286d81a13ff74768c3ec9bcb889bbb4ee5da4a8ec',
        [
            ('PL153', 262, 'PL153 cols 515-516 / Petreius II col 377 (In Epist. ad Hebr. c. 6 v. 2: Baptismus tripliciter dicitur: in aqua, lacrymis, sanguine)', 'pl153_baptismus_tripliciter_p262'),
            ('PL153', 263, 'PL153 cols 517-518 (In Epist. ad Hebr. c. 6 continuatio)', 'pl153_baptismus_tripliciter_p263'),
        ]
    ),

    # 2. Vir dicitur in Psalmis primo -> I, 9 (PL152 p. 324)
    '45c129c6bdc9c96e1a63ca04ab090a5a84aad260693c927f9ae78c1a265ac996': (
        '259b5069da88f7d42e25d0446a2cc652ddbb8d144c81dfd3dc75d37f77462a22',
        [
            ('PL152', 324, 'PL152 cols 639-640 / Petreius I col 2 (In Psalmum I v. 1: Beatus vir: virum autem dicit non ad sexum sed ad sensuum maturitatem)', 'pl153_vir_dicitur_in_psalmis_p324'),
        ]
    ),

    # 3. Canticum quid sit -> I, 9 (PL152 pp. 324, 323)
    '689aa6888bd31e54484e67612ce4a3339f95404a54e41dc7657709247ec172f6': (
        'fc162b4bcde61b06ce6c059d08e40d79d63e16912c463b2d26d712ea5319840e',
        [
            ('PL152', 324, 'PL152 cols 639-640 / Petreius I col 1-2 (Prologus in Psalmos: Theoria vs ethica, Cantica canticorum et psalmi)', 'pl153_canticum_quid_sit_p324'),
            ('PL152', 323, 'PL152 cols 637-638 (Prologus in Psalmos continuatio)', 'pl153_canticum_quid_sit_p323'),
        ]
    ),

    # 4. Cauterizare quid -> I, 321 (PL152 p. 535)
    'df3ea59a49737fdb554433862c95a5d857483e2bef54926fe871097a4deb7c19': (
        '2ce7c09b8f9b52bd4340ba332530ae724d9a042276ab4ac1d1b3a0f00b691129',
        [
            ('PL152', 535, 'PL152 cols 1061-1062 / Petreius I col 321 (In Psalmum LXXIX v. 17: Ultio, ignis et ustio cordis et operis)', 'pl153_cauterizare_quid_p535'),
        ]
    ),

    # 5. Obstinatæ excommunia‑ tiones -> I, 590 (PL152 pp. 713, 712)
    '3f0c16602e499ec6e7513a6a61e01249a381cbc12c89bf4511590126d3b6df50': (
        '328c7bc8c076bcd2c62f657f696feab10378f1aeea42173cc993d80dd887efbf',
        [
            ('PL152', 713, 'PL152 cols 1117-1118 / Petreius I col 590 (In Psalmum CXLIX v. 6-8: Gladii ancipites et increpationes in populis contra obstinatos)', 'pl153_obstinatae_excommunicationes_p713'),
            ('PL152', 712, 'PL152 cols 1115-1116 (In Psalmum CXLIX continuatio)', 'pl153_obstinatae_excommunicationes_p712'),
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


def resolve_pl153_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL153 item to physical destination files."""
    if item_id not in PL153_ITEMS_RAW:
        return None
    group_id, dests = PL153_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint86(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 86 recovery for PL153 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL153' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL153',
        'sprint': 86,
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
        res = resolve_pl153_item(item_id)
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
    parser = argparse.ArgumentParser(description="PL153 S. Bruno Carthusianus Locator (Sprint 86)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint86(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
