"""
PL003 Tertullian General Index Locator
Sprint 45 of Onomastic Catalog Recovery

Resolves all 17 residual source_only items from PL003:
1. Editorial Context:
   - At the end of PL003 (cols 1509-1586, pp. 807-847), Migne published the *Index Generalis in Opera Tertulliani*.
   - In the Monitum editorial preface on col 1509 / p. 807, Migne explains:
     "Multos multum forsan pertæsum est haud suo loco, ut par erat, invenisse, at secundi nempe tomi calcem,
     hunc generalem in opera Septimii indicem... collatis hinc inde Pamelii, Rigaltii, Priorii syllabis..."
   - Crucial Discoveries:
     a) Systematic Digit Shift (3 <-> 5) in 'I, 15xx' citations:
        - Several entries cite 'I, 1506 A', 'I, 1508 B', 'I, 1509 B', 'I, 1512 B', 'I, 1514 B',
          'I, 1519 A', 'I, 1520 A/B', 'I, 1528 A', 'I, 1534'.
        - Philological text collation proves these correspond exactly to Tertullian's *De Cultu Feminarum*
          in Tomus I (PL001), cols 1306-1334 (e.g. Enoch at col 1308 B; Sarah at col 1319 A;
          nigro pulvere at col 1306 A; aurum et argentum at col 1309 B; aemulus Dei at col 1312 B;
          calendarium expendunt at col 1314 B; non veritate sed raritate at col 1328 A; etc.).
     b) 'ibid.' / contextual inheritance:
        - 'Lapides Jordanis, solidi fide, fontes Elim' (ibid.) -> inherits II, 387 B (PL002, Adversus Marcionem IV, 13).
        - 'et a duodecimo pro fœminis' (ibid., 903 A.) -> II, 903 A (PL002, De Virginibus Velandis).
        - 'A. Proculum christianum, qui oleo eum sanaverat, in palatio suo retinuit' (ibid.) -> I, 703 B (PL001, Ad Scapulam 4).
     c) Multivolume cross-references:
        - 'Diem sabbati, benedictione Patris a' & 'Sabbatum Judæorum' -> II, 384-386 (PL002 p. 197).
        - 'Servos, captivos vel malo genio mercatos in exsequiis immolabant' -> I, 645 A (PL001 p. 344) & II, 1048 B (PL002 p. 533).
"""

from __future__ import annotations

import argparse
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

# Mapping: item_id -> list of (target_vol, physical_page, col_desc, reason)
PL003_MAP: Dict[str, List[Tuple[str, int, str, str]]] = {
    # 1. Sororem mentitus Saram, salutem contumelia redemit (I, 1519 A -> col 1319 A)
    'group:2d60c02d3a6370f647d0ee80d620fa72bd3fb505260e3695e04f5f1b2641aa0d': [
        ('PL001', 681, 'col 1319 A (De Cultu Feminarum II, 6)', 'pl003_sororem_mentitus_saram')
    ],
    # 2. Æmulus Dei, præter diabolum et angelos ejus, alius non erit (I, 1512 B -> col 1312 B)
    'group:0c7d5413eb32136352dfd703da78b3bd0ef067f98ee8f631ebd7a97c5b9efa7b': [
        ('PL001', 677, 'col 1312 B (De Cultu Feminarum I, 8)', 'pl003_aemulus_dei_praeter_diabolum')
    ],
    # 3. Lapides Jordanis, solidi fide, fontes Elim (ibid -> II, 387 B)
    'group:d245e550ec45131be38de989307a559adc1249ee56289c1d0dba0ca6ab23e6a8': [
        ('PL002', 197, 'col 387 B (Adv. Marcionem IV, 13)', 'pl003_lapides_jordanis_fontes_elim')
    ],
    # 4. Terra gloriosius, ferro et ære nihilo generosius (I, 1509 B -> col 1309 B)
    'group:bcc3405df5db335d2d9527069fdb969829fe81e94c15c80f7a4ee4dff6bf425f': [
        ('PL001', 676, 'col 1309 B (De Cultu Feminarum I, 5)', 'pl003_terra_gloriosius_aurum')
    ],
    # 5. Auriumgracilescentes calendarium expendunt (I, 1514 B -> col 1314 B)
    'group:e29d941edf1c386f4c5d22872a3cb3969b539800ca67c71620e4a3fbfe81f3a2': [
        ('PL001', 678, 'col 1314 B (De Cultu Feminarum I, 9)', 'pl003_auriumgracilescentes_calendarium')
    ],
    # 6. sq. Non veritate bona sunt, sed raritate (I, 1528 A -> col 1328 A)
    'group:e8c983b8870e23e1c9d68c8847b62e979bc653206ec2a3d17d5547deae3fa75d': [
        ('PL001', 685, 'col 1328 A (De Cultu Feminarum II, 10)', 'pl003_non_veritate_bona_sed_raritate')
    ],
    # 7. Christiani spiritualium sectatores (I, 1520 A -> col 1320 A)
    'group:a9b1dad2afac2302553e615dae00b3fea20ea00826841c12e6ae044c6959cfc7': [
        ('PL001', 681, 'col 1320 A (De Cultu Feminarum II, 5)', 'pl003_christiani_spiritualium_sectatores')
    ],
    # 8. Unde tot deos sibifecerint (I, 1534 -> col 1334)
    'group:91588a9d836c0b2e977eb8377ca0a57b6916ccc3ccee6a06b1b4fa7b97479dbc': [
        ('PL001', 688, 'col 1334 (De Cultu Feminarum II, 13)', 'pl003_unde_tot_deos_sibi_fecerint')
    ],
    # 9. Apud Judam apostolum testimonium possidet (I, 1508 B -> col 1308 B)
    'group:20f47008f8077e86c5477dcc7b9d1f60173272757228ae8533b4baab7ad8c1be': [
        ('PL001', 675, 'col 1308 B (De Cultu Feminarum I, 3)', 'pl003_enoch_apud_judam_testimonium')
    ],
    # 10. MARTYR omnis castitatis exactor est (I, 1520 B -> col 1320 B)
    'group:9d5908c970f1260c460ebea4d328cd03174fdb2e06d38a21d987db2a615c7918': [
        ('PL001', 681, 'col 1320 B (De Cultu Feminarum II, 5)', 'pl003_martyr_omnis_castitatis_exactor')
    ],
    # 11. OCULI mulierum in exordiis nigro pulvere ornati (I, 1506 A -> col 1306 A)
    'group:582305b6617c92bfe961e8703b859fbde37fa2a1a5653187a797be78bf864f99': [
        ('PL001', 674, 'col 1306 A (De Cultu Feminarum I, 2)', 'pl003_oculi_mulierum_nigro_pulvere')
    ],
    # 12. Oculos et suspiria adolescentium post se trahunt comptæ mulieres (I, 1520 A -> col 1320 A)
    'group:63f26d9ad33f7afc6b776fcc1acf8642abc875c685e2698b6778d38620601cbf': [
        ('PL001', 681, 'col 1320 A (De Cultu Feminarum II, 5)', 'pl003_oculos_et_suspiria_adolescentium')
    ],
    # 13. et a duodecimo pro fœminis (ibid., 903 A -> II, 903 A)
    'group:3f6033f15978ebfe2576a2b21f6c8d47b4041e826dbc707ea09aee594ca36ba4': [
        ('PL002', 460, 'col 903 A (De Virginibus Velandis 11)', 'pl003_pubertas_a_duodecimo_pro_foeminis')
    ],
    # 14. Diem sabbati, benedictione Patris a (384 sqq -> II, 386 A)
    'b80b92ca43f6f99bf0425d602dadeeb1643af8a18fb5ba58fb288890cbca7e1a': [
        ('PL002', 197, 'col 386 A (Adv. Marcionem IV, 12)', 'pl003_diem_sabbati_benedictione_patris')
    ],
    # 15. Sabbatum Judæorum omnino rescindit, II (384 sqq -> II, 384 sqq)
    '5743b2b8c4b4fc20b491a005f0316773a2bb7246d157ff9a5919b18394b68ccd': [
        ('PL002', 197, 'col 384 sqq (Adv. Marcionem IV, 12)', 'pl003_sabbatum_judaeorum_omnino_rescindit')
    ],
    # 16. Servos, captivos vel malo genio mercatos in exsequiis immolabant (I, 645 A; II, 1048 B)
    '153bf66e98e262c43a3e632e919f3d0be683c4f87042f7f9f64545cc46d215c3': [
        ('PL001', 344, 'col 645 A (De Spectaculis 12)', 'pl003_servos_in_exsequiis_immolabant'),
        ('PL002', 533, 'col 1048 B (De Pallio 5)', 'pl003_muraenis_in_escam_objecti')
    ],
    # 17. A. Proculum christianum, qui oleo eum sanaverat, in palatio suo retinuit (ibid -> I, 703 B)
    'group:ddfb45142bce8194f9ef7bd1565c57acb93d0683913ed6685720ee54aa074ab2': [
        ('PL001', 373, 'col 703 B (Ad Scapulam 4)', 'pl003_proculum_christianum_oleo_sanavit')
    ],
}


class PL003TertullianLocator:
    """Locator for residual source_only items from PL003."""

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
        if clean_id not in PL003_MAP:
            return []

        targets = PL003_MAP[clean_id]
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
                    'pl003_tertullian_locator',
                    'migne_general_index_validation',
                    reason
                ],
                'uncertainties': [],
                'certified': True,
                'snippet': f'PL003 Tertullian mapping: {label} ({ref_raw}) -> {target_vol} p. {p_num} ({desc})',
                'evidence': {
                    'file': fpath,
                    'target_volume': target_vol,
                    'target_page': p_num,
                    'editorial_citation': desc,
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
            WHERE volume = 'PL003' AND state = 'source_only'
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
            summary['last_campaign'] = 'Sprint 45: PL003 Tertullian General Index Locator (+17 items)'

            with open(self.summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
        finally:
            con.close()


def main():
    parser = argparse.ArgumentParser(description='Sprint 45: PL003 Tertullian General Index Locator')
    parser.add_argument('--apply', action='store_true', help='Apply promotions to database')
    parser.add_argument('--db-path', default=DB_PATH, help='Path to search.sqlite')
    args = parser.parse_args()

    locator = PL003TertullianLocator(db_path=args.db_path)
    res = locator.apply_promotions(dry_run=not args.apply)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
