"""
PL056 Ballerini Codex Canonum & Quesnel Dissertationes Locator
Sprint 67 of Onomastic Catalog Recovery

Resolves all 9 residual source_only items from PL056:
1. Editorial Context:
   - PL056 contains the Codex Canonum Ecclesiasticorum et Constitutorum Sanctae Sedis Apostolicae,
     together with the five dissertations of Paschasius Quesnel and the critical observations
     of the Ballerini brothers (Pietro and Girolamo Ballerini) on Saint Leo the Great and canon law:
     * Cols 1-746: Codex Canonum Eccles. et Constitutorum S. Sedis Apostolicae
     * Cols 747-854: Antiquissima Canonum Nicaenorum Interpretatio Latina & Documenta Juris Vetustissima
     * Cols 855-1010: Paschasii Quesnelli Quinque Dissertationes in Codicem Canonum
     * Cols 1009-1130: Balleriniorum Observationes in Dissertationes Quesnelli
     * Cols 1131-1188 (physical pp. 570-598): INDEX RERUM ET SENTENTIARUM
   - Citations in the INDEX RERUM ET SENTENTIARUM cite Migne column numbers within PL056 directly:
     * 'Hujus canonis vindiciæ, 822 et seqq' -> col 822 (Antiquissima interpretatio Nicaena, p. 417)
     * 'A S. Athanasio confutati, 376 et seqq' -> col 376 (Codex Canonum, p. 194)
     * 'Codiciæ Theodosiani leges codici canonum insertæ, 393 et seqq' -> col 393 (Codex Canonum, p. 203)
     * 'Expungitur plenarium concilium... Quesnellus putavit, 895 et seqq' -> col 895 (Dissert. Quesnellus, p. 454)
     * 'collectio ante Caroli Magni ætatem recepta fuerit in Gallis, Quesnelli sententia, 1013 et seqq' -> col 1013 (Balleriniorum Observationes, p. 513)
     * 'De variis libellis fidei Quesnelli dissertatio, 919 et seqq' -> col 919 (Dissert. XII Quesnelli, p. 466)
     * 'Filius Dei, de quo quid sit credendum, Damasus exponit, 401 et seqq' -> col 401 (Codex Canonum, p. 207)
     * 'Prisca canonum editio Latina e Justelli codice vulgata... 478 et seqq' -> col 478 (Codex Canonum, p. 245)
     * 'Ejusdæm epistola suppositionis vitio nota a Quesnelio, 978 et seqq' -> col 978 (Dissert. XIII Quesnelli, p. 495)
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

PL056_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Hujus canonis vindiciæ -> 822 et seqq (p. 417)
    'group:400a060afe2e925d9b2d8aefe87dd1614d2e59627712dc9377599c06e796cbf9': (
        'group:400a060afe2e925d9b2d8aefe87dd1614d2e59627712dc9377599c06e796cbf9',
        [
            ('PL056', 417, 'PL056 col 822 (Antiquissima interpretatio Latina canonum Nicaenorum: canonis vindiciae)', 'pl056_ballerini_col822_p417')
        ]
    ),

    # 2. A S. Athanasio confutati -> 376 et seqq (p. 194)
    'group:800eb6e0154bb15601bf20bba5f18368fe6bf53eaca159943bfd45076315ef40': (
        'group:800eb6e0154bb15601bf20bba5f18368fe6bf53eaca159943bfd45076315ef40',
        [
            ('PL056', 194, 'PL056 col 376 (Codex Canonum Eccles. et Constitutorum S. Sedis Apostol.: S. Athanasius confutatio)', 'pl056_ballerini_col376_p194')
        ]
    ),

    # 3. Codiciæ Theodosiani leges codici canonum insertæ -> 393 et seqq (p. 203)
    'group:34a044849e92b0a79381210ef13a236353129a32f505cc5ee6ecef5c018fec8b': (
        'group:34a044849e92b0a79381210ef13a236353129a32f505cc5ee6ecef5c018fec8b',
        [
            ('PL056', 203, 'PL056 col 393 (Codex Canonum Eccles.: Codicis Theodosiani leges canonibus insertae)', 'pl056_ballerini_col393_p203')
        ]
    ),

    # 4. Expungitur plenarium concilium, quod autumno ejusdem anni celebratum Quesnellus putavit -> 895 et seqq (p. 454)
    'group:8be5233b458739d17b1bae411f7ed77bf33faa0fca20c149c4dce816058b12aa': (
        'group:8be5233b458739d17b1bae411f7ed77bf33faa0fca20c149c4dce816058b12aa',
        [
            ('PL056', 454, 'PL056 col 895 (Documenta Juris Vetustissima: Expungitur plenarium concilium a Quesnello fictum)', 'pl056_ballerini_col895_p454')
        ]
    ),

    # 5. collectio ante Caroli Magni ætatem recepta fuerit in Gallis, Quesnelli sententia -> 1013 et seqq (p. 513)
    '548989a0c7d3e12a1096972e5511411aa936edc5bf28ffe88e2ab070666b6455': (
        '548989a0c7d3e12a1096972e5511411aa936edc5bf28ffe88e2ab070666b6455',
        [
            ('PL056', 513, 'PL056 col 1013 (Balleriniorum Observationes in Dissert. XIII Quesnelli: collectio ante Caroli Magni aetatem in Gallis)', 'pl056_ballerini_col1013_p513')
        ]
    ),

    # 6. De variis libellis fidei Quesnelli dissertatio -> 919 et seqq (p. 466)
    'group:5347c4bafe6eaffe8a09c1a61d1295099850c5cea503b3a420da9b1c1381173a': (
        'group:5347c4bafe6eaffe8a09c1a61d1295099850c5cea503b3a420da9b1c1381173a',
        [
            ('PL056', 466, 'PL056 col 919 (Dissert. XII Paschasii Quesnelli: De variis libellis fidei in codice Romano)', 'pl056_ballerini_col919_p466')
        ]
    ),

    # 7. Filius Dei, de quo quid sit credendum, Damasus exponit -> 401 et seqq (p. 207)
    'group:fb110315bed3b31a852e2a7cd3fd4d883fa54f5caa0b3224290b7bdab48850d7': (
        'group:fb110315bed3b31a852e2a7cd3fd4d883fa54f5caa0b3224290b7bdab48850d7',
        [
            ('PL056', 207, 'PL056 col 401 (Codex Canonum Eccles. et Constitutorum: Damasus Papa de Filio Dei)', 'pl056_ballerini_col401_p207')
        ]
    ),

    # 8. Prisca canonum editio Latina e Justelli codice vulgata ad mss. codices recognita, suppleta et recusa -> 478 et seqq (p. 245)
    'group:12c3d793d9a9f87c3015bc1fa7c912dc048ce320c75ab785ad8d2ff8b57d444a': (
        'group:12c3d793d9a9f87c3015bc1fa7c912dc048ce320c75ab785ad8d2ff8b57d444a',
        [
            ('PL056', 245, 'PL056 col 478 (Codex Canonum Eccles.: Prisca canonum editio Latina e Justelli codice recognita)', 'pl056_ballerini_col478_p245')
        ]
    ),

    # 9. Ejusdæm epistola suppositionis vitio nota a Quesnelio -> 978 et seqq (p. 495)
    'group:ba10065ce550c2db3cbda6c92c5ed6bcc67d4f88e82fc9ff578fc49d945b984f': (
        'group:ba10065ce550c2db3cbda6c92c5ed6bcc67d4f88e82fc9ff578fc49d945b984f',
        [
            ('PL056', 495, 'PL056 col 978 (Dissert. XIII Paschasii Quesnelli: epistola suppositionis vitio notata)', 'pl056_ballerini_col978_p495')
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


def resolve_pl056_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL056 item to physical destination files."""
    if item_id not in PL056_ITEMS_RAW:
        return None
    group_id, dests = PL056_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint67(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 67 recovery for PL056 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL056' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL056',
        'sprint': 67,
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
        res = resolve_pl056_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 67: PL056 Ballerini Codex Canonum Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint67(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
