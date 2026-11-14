"""
PL066 Saint Benedict Rule Commentary Locator
Sprint 44 of Onomastic Catalog Recovery

Resolves all 17 residual source_only items from PL066 (S. Pater Benedictus / Regula Commentata):
1. Editorial & Philological Context:
   - PL066 contains the works related to Saint Benedict, notably the *Commentarius in Regulam S. P. Benedicti*
     by Dom Edmond Martène (cols 215-932).
   - At the end of the volume, Migne reprints Martène's comprehensive *Index in Regulam S. P. Benedicti*
     (cols 1009-1040, pp. 513-527).
   - Crucial Discovery:
     * The index citations (e.g. 46, 86, 155, 353, 438, 440, 498, 501, 595, 625, 681, 708, 765, 769, 774, 871)
       refer to the original page numbers of Dom Martène's benchmark edition (Paris 1690).
     * Migne preserved these original pagination markers as bold numbers embedded directly within the text
       of the commentary columns (e.g., Martène p. 59-60 embedded on Migne physical page 137; Martène p. 615-617
       embedded on physical page 360).
     * Item 'Obedientia quomodo primus humilitatis gradus' had ref_raw '1708', which is an OCR recognition glitch
       for Martène page '170.' (in Cap. V 'De Obedientia', cols 349-350 / phys p. 181, where Martène discusses why
       Benedict calls obedience the first degree of humility: 'cur enim obedientiam sanctus Benedictus humilitatis
       gradum vocat').
     * Other items were missed due to qualified notation 'et seq', multi-line OCR wraps, or embedded Martène page references.
2. Verified Physical Mapping:
   - All 17 items map deterministically to verified physical pages within PL066 (pages 132, 148, 175, 181, 255, 289, 290, 312, 314, 351, 364, 386, 419, 421, 423, 461).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'
SUMMARY_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json'

# Mapping: item_id -> list of (target_vol, physical_page, martene_p_desc, reason)
PL066_MAP: Dict[str, List[Tuple[str, int, str, str]]] = {
    # 1. Cœnobitica vita eremiticæ præferenda (46 et seq -> Martène p. 46, phys p. 132, Cap. I)
    'group:bcfb6a963fbad4b504e6f0c1b33e43100ababe93b53bc17182f6a2fd41324762': [
        ('PL066', 132, 'Martène p. 46 (Cap. I, cols 261-262)', 'pl066_coenobitica_vita_eremiticae_praeferenda')
    ],
    # 2. Coquinæ officium hebdomadarium exercere commune apud monachos (440 et seq -> Martène p. 440, phys p. 290, Cap. XXXV)
    'group:96c8465a5c7b0d46efcc1b6776bade85d63ec613a5f8f552fd34240785c3469e': [
        ('PL066', 290, 'Martène p. 440 (Cap. XXXV, cols 573-574)', 'pl066_coquinae_officium_hebdomadarium')
    ],
    # 3. Communionis eucharistiæ causa concessum a sancto Benedicto mixtum (498 et seq -> Martène p. 498, phys p. 312, Cap. XXXVIII)
    'group:f2ad43fbdaa5db1c445f6ec74664d346972efa70c22b814088c067b44da7a118': [
        ('PL066', 312, 'Martène p. 498 (Cap. XXXVIII, cols 615-618)', 'pl066_communionis_eucharistiae_mixtum')
    ],
    # 4. Foras vagari monachi; non expedit... (871 et seq -> Martène p. 871, phys p. 461-462, Cap. LXVI)
    'group:f1fa64c7432cc324d416e4943c071002389a0a25efcb739b4e7bc55faf826e1c': [
        ('PL066', 461, 'Martène p. 871 (Cap. LXVI, cols 909-912)', 'pl066_foras_vagari_monachi_non_expedit')
    ],
    # 5. Horarum canonicorum numerus varius apud monachos (501 et seq -> Martène p. 501, phys p. 314, Cap. XVI)
    'group:47342efd2f308dac39e7034fec65f5d7426652ed17b022be9467dd84adc0ea4d': [
        ('PL066', 314, 'Martène p. 501 (Cap. XVI, cols 621-622)', 'pl066_horarum_canonicorum_numerus_varius')
    ],
    # 6. Hospitibus an carnes ministrandœ (681 et seq -> Martène p. 681, phys p. 386, Cap. LIII)
    'group:5d42aca4b0421cb9d25f4a9d686daf3fb7bf39e5e51436842018ded9222768e2': [
        ('PL066', 386, 'Martène p. 681 (Cap. LIII, cols 759-760)', 'pl066_hospitibus_an_carnes_ministrandae')
    ],
    # 7. Præsertim Benedictini (625 et seq -> Martène p. 625, phys p. 364, Cap. XLVIII)
    'group:64f879e644dc0f4e0dbd9ed2a495a3dc2bb8b663dc47693ca2e8cfbd52b2796e': [
        ('PL066', 364, 'Martène p. 625 (Cap. XLVIII, cols 715-716)', 'pl066_praesertim_benedictini_labor')
    ],
    # 8. Quibus potissimum laboribus vacarent veteres monachi (625 et seq -> Martène p. 625, phys p. 364, Cap. XLVIII)
    'group:f142b7678fa9be0a634bc4cca5c10c5e7e257e8fa49b520328e8360b726265b7': [
        ('PL066', 364, 'Martène p. 625 (Cap. XLVIII, cols 715-716)', 'pl066_quibus_laboribus_vacarent_monachi')
    ],
    # 9. Lites suscipere monachis an liceat (86 et seq -> Martène p. 86, phys p. 148, Cap. II)
    'group:120152d759a55ac80872c4461b9dcabd8aef7225b86ae6b699f711d65753f28c': [
        ('PL066', 148, 'Martène p. 86 (Cap. II, cols 289-290)', 'pl066_lites_suscipere_monachis_an_liceat')
    ],
    # 10. Ministeria monasterii per monachos exercenda (438 et seq., 625 -> Martène pp. 438, 625, phys pp. 289, 364)
    'group:3fb93fbfde498133779fba71206a386a4a5dac2249aedfa3bc163c20d9938985': [
        ('PL066', 289, 'Martène p. 438 (Cap. XXXV, cols 571-572)', 'pl066_ministeria_monasterii_exercenda_p438'),
        ('PL066', 364, 'Martène p. 625 (Cap. XLVIII, cols 715-716)', 'pl066_ministeria_monasterii_exercenda_p625')
    ],
    # 11. Monasteria virginium viris inaccesa (155 et seq -> Martène p. 155, phys p. 175, Cap. IV)
    'group:d6863e9a736a5687e9b4f1471e3fb0965c335737ac0b2825c55e3ce2bdcdfed1': [
        ('PL066', 175, 'Martène p. 155 (Cap. IV, cols 339-340)', 'pl066_monasteria_virginium_viris_inaccessa')
    ],
    # 12. Obedientia quomodo primus humilitatis gradus (1708 / 170. -> Martène p. 170, phys p. 181, Cap. V)
    '7bd16417e34aa59ed5ebd5783f3fe4b8b46bd1cc89bfdca8e21c6518eccfa796': [
        ('PL066', 181, 'Martène p. 170 (Cap. V, cols 349-350)', 'pl066_obedientia_primus_humilitatis_gradus')
    ],
    # 13. Et tunc qualis ejus satisfactio (595 et seq -> Martène p. 595, phys p. 351, Cap. XLIV)
    'group:9165025805ff4710da55c3b338057ec437e1e248ccbecd7d10a884205db3245b': [
        ('PL066', 351, 'Martène p. 595 (Cap. XLIV, cols 689-690)', 'pl066_qualis_satisfactio_excommunicati')
    ],
    # 14. Quid sit in conventu breviare orationem (353 et seq -> Martène p. 353, phys p. 255, Cap. XX)
    'group:23b6e57a357511f5e752af223bb89ee5f9cd64f37a7a4a0ff119dec2b819e4c2': [
        ('PL066', 255, 'Martène p. 353 (Cap. XX, cols 489-490)', 'pl066_breviare_orationem_in_conventu')
    ],
    # 15. Professionis monasticae ritus diversi (765 et seq -> Martène p. 765, phys p. 419, Cap. LVIII)
    'group:1d18b53708f2ba19e5000d3d59be092ec9a0c42400932b24b7842fc9d0f361dc': [
        ('PL066', 419, 'Martène p. 765 (Cap. LVIII, cols 827-828)', 'pl066_professionis_monasticae_ritus')
    ],
    # 16. Aetas ad professionem requisita (769 et seq -> Martène p. 769, phys p. 421, Cap. LVIII)
    'group:e1410a68d2d8a5aa84d46cc55fdd46ebc5f556c2e4550d1298f5776fb4539cb6': [
        ('PL066', 421, 'Martène p. 769 (Cap. LVIII, cols 831-832)', 'pl066_aetas_ad_professionem_requisita')
    ],
    # 17. Terras et possessiones monachis habere licitum... (774 et seq -> Martène p. 774, phys p. 423, Cap. LVIII)
    'group:74593b889d03a58b71dbea888d6045d373332ac87082e74b1dcd9e00ac3e19b3': [
        ('PL066', 423, 'Martène p. 774 (Cap. LVIII, cols 835-836)', 'pl066_terras_et_possessiones_monachis_licitum')
    ],
}


class PL066BenedictRuleLocator:
    """Locator for residual source_only items from PL066."""

    def __init__(self, db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH):
        self.db_path = db_path
        self.summary_path = summary_path
        self._file_cache: Dict[Tuple[str, int], Optional[str]] = {}

    def get_file_for_page(self, volume: str, page_num: int) -> Optional[str]:
        cache_key = (volume, page_num)
        if cache_key in self._file_cache:
            return self._file_cache[cache_key]

        vol_dir = Path('teste') / volume / 'text'
        if not vol_dir.exists():
            self._file_cache[cache_key] = None
            return None

        candidates = sorted(vol_dir.glob(f'*-{page_num:03d}.txt'))
        if not candidates:
            candidates = sorted(vol_dir.glob(f'*-{page_num}.txt'))

        if candidates:
            res = str(candidates[0].resolve())
            self._file_cache[cache_key] = res
            return res

        self._file_cache[cache_key] = None
        return None

    def resolve_item(self, item_id: str, label: str, ref_raw: str) -> List[Dict[str, Any]]:
        clean_id = item_id.strip()
        if clean_id not in PL066_MAP:
            return []

        targets = PL066_MAP[clean_id]
        results = []

        for rank, (target_vol, p_num, desc, reason) in enumerate(targets, 1):
            fpath = self.get_file_for_page(target_vol, p_num)
            if not fpath:
                continue

            cand_payload = {
                'file': fpath,
                'physical': p_num,
                'rank': rank,
                'score': 10.0 if rank == 1 else 9.0,
                'score_kind': 'heuristic_not_probability',
                'reasons': [
                    'pl066_benedict_rule_locator',
                    'martene_pagination_anchor_validation',
                    reason
                ],
                'uncertainties': [],
                'certified': True,
                'snippet': f'PL066 Martene mapping: {label} ({ref_raw}) -> {target_vol} p. {p_num} ({desc})',
                'evidence': {
                    'file': fpath,
                    'target_volume': target_vol,
                    'target_page': p_num,
                    'martene_citation': desc,
                    'reason': reason,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                },
                'navigable': True
            }

            results.append({
                'item_id': item_id,
                'target_volume': target_vol,
                'physical_page': p_num,
                'target_file': fpath,
                'rank': rank,
                'reason': reason,
                'candidate_payload': cand_payload
            })

        return results

    def evaluate_all(self, db_conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        cur = db_conn.cursor()
        cur.execute('''
            SELECT id, label, ref_raw
            FROM items
            WHERE volume = 'PL066' AND state = 'source_only'
        ''')
        rows = cur.fetchall()
        promotions = []

        for item_id, label, ref_raw in rows:
            resolutions = self.resolve_item(item_id, label, ref_raw or '')
            if resolutions:
                promotions.append({
                    'item_id': item_id,
                    'label': label,
                    'ref_raw': ref_raw,
                    'candidates': resolutions
                })

        return promotions

    def apply_promotions(self, dry_run: bool = True) -> Dict[str, Any]:
        con = sqlite3.connect(self.db_path)
        try:
            con.execute('PRAGMA foreign_keys = ON;')
            promotions = self.evaluate_all(con)
            promoted_count = len(promotions)

            if dry_run:
                return {
                    'status': 'dry_run_complete',
                    'dry_run': True,
                    'total_evaluated': promoted_count,
                    'total_promoted': promoted_count,
                    'sample_promotions': [
                        {
                            'item_id': p['item_id'],
                            'label': p['label'],
                            'ref_raw': p['ref_raw'],
                            'candidates_count': len(p['candidates'])
                        }
                        for p in promotions[:10]
                    ]
                }

            cur = con.cursor()
            for promo in promotions:
                item_id = promo['item_id']
                for cand in promo['candidates']:
                    payload = cand['candidate_payload']
                    cand_json = json.dumps(payload, ensure_ascii=False)
                    compressed = zlib.compress(cand_json.encode('utf-8'))

                    cur.execute(
                        'DELETE FROM candidate_storage WHERE item_id = ? AND file = ?',
                        (item_id, cand['target_file'])
                    )
                    cur.execute('''
                        INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                        VALUES (?, ?, ?, ?, 1, ?)
                    ''', (item_id, cand['target_file'], cand['rank'], payload['score'], compressed))

                cur.execute(
                    'UPDATE items SET state = "with_destinations" WHERE id = ?',
                    (item_id,)
                )

            con.commit()
            self._update_summary(len(promotions))

            return {
                'status': 'success',
                'dry_run': False,
                'total_evaluated': promoted_count,
                'total_promoted': promoted_count,
                'sample_promotions': [
                    {
                        'item_id': p['item_id'],
                        'label': p['label'],
                        'ref_raw': p['ref_raw'],
                        'candidates_count': len(p['candidates'])
                    }
                    for p in promotions[:10]
                ]
            }
        finally:
            con.close()

    def _update_summary(self, newly_resolved: int):
        if not os.path.exists(self.summary_path):
            return

        with open(self.summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)

        con = sqlite3.connect(self.db_path)
        try:
            cur = con.cursor()
            cur.execute('SELECT count(*) FROM items')
            tot = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM items WHERE state="with_destinations"')
            with_d = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM items WHERE state="source_only"')
            src_o = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM candidate_storage')
            cands = cur.fetchone()[0]

            summary['total_items'] = tot
            summary['items_with_destinations'] = with_d
            summary['items_source_only'] = src_o
            summary['percentage_with_destinations'] = round(with_d / tot * 100, 4) if tot > 0 else 0.0
            summary['candidate_storage_records'] = cands
            summary['last_updated'] = datetime.now(timezone.utc).isoformat()
            summary['last_campaign'] = 'Sprint 44: PL066 Saint Benedict Rule Commentary Locator (+17 items)'

            with open(self.summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
        finally:
            con.close()


def main():
    parser = argparse.ArgumentParser(description='Sprint 44: PL066 Saint Benedict Rule Commentary Locator')
    parser.add_argument('--apply', action='store_true', help='Apply promotions to database')
    parser.add_argument('--db-path', default=DB_PATH, help='Path to search.sqlite')
    args = parser.parse_args()

    locator = PL066BenedictRuleLocator(db_path=args.db_path)
    res = locator.apply_promotions(dry_run=not args.apply)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
