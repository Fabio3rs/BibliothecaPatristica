"""
PL221 Index Methodicus / Indices Speciales Locator
Sprint 30 of Onomastic Catalog Recovery

Resolves the 31 residual source_only items from PL221 (Index Methodicus et Chronologicus
and Indices Patrologiae Speciales, Index CCXXXV, pages 607 to 646).

Key editorial characteristics handled:
1. Appendix / Supplement volume errata:
   - Paschasius Radbertus, Heribert of Eichstett, and Poppo of Trier cited with 'CXL'
     actually reside in PL141 (supplement, pp. 688-705) and PL120 (cols 1351-1557).
2. Concordatum Wormatiense (col 1359):
   - Located in PL163 page 682 (file 04a3d694-325c-4a8e-a7db-bf8ff07dfbed-682.txt).
3. Ambrose excidium Hierosolymitanae (cols 2205, 2219):
   - Located in PL015 pages 510 and 518 (Anacephaleosis and Concordantiae).
4. Jordanus of Limoges & Pope John XIX (col 1549):
   - Located in PL142 page 680 (aa439810-35f8-4f93-96f2-de76157d249a-680.txt).
5. Roziere Strasbourg formulas (col 1427):
   - Located in PL161 page 764 (1c0895cd-5206-42c9-823f-4393ce030be6-764.txt).
6. Bruno of Segni Sacramentis (col 1089, printed CXLV 1809):
   - Typo for CLXV (PL165) col 1089, page 559 (99212b95-cdaa-4e44-a8a9-7194a4a690d0-559.txt).
7. Mozarabic Breviary Ximenes (col 1515, printed 1515 for 1315):
   - Located in PL086 page 686 (2bd5ec2a-d6e8-4c8a-a68c-0b083ff56e80-686.txt).
8. Paul the Deacon homilies (cols 1519, 1900):
   - Located in PL095 page 884 (191abcac-312c-4538-8e7e-1b4551dff124-884.txt).
9. Suger of Saint-Denis (cols 1445, 1461, 1547):
   - Located in PL186 pages 768, 735, 678.
10. Henricus Parmensis Easter sermon (col 1529, printed CXXXIV 1539):
    - Typo for CXXXIX (PL139) col 1529, page 769 (91c90ce1-03e2-4d18-98e3-6aaaa4c80280-769.txt).
11. Philippus de Harveng hagiographical collection (cols 1525, 1557, 1559, 1575, 1588, 1591):
    - Located in PL203 pages 676, 684, 696, 702, 708, 709.
12. Radulphus Ardens homilies (col 1299, printed CIV 4299):
    - Typo for CLV (PL155) col 1299, page 653 (PL155-653.txt).
13. Remigius Curiensis (col 1095, printed CII 1995):
    - Located in PL102 page 550 (3075140d-d30f-4238-ab39-3a7e602d640a-550.txt).
14. Regula ad servos Dei (col 1377, printed XXX 1377):
    - Typo for XXXII (PL032) col 1377, page 695 (b64d938d-d5dc-4495-8a91-ce9f2fe1815c-695.txt).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'

PL221_EXACT_MAP: Dict[str, Tuple[str, int, str, str]] = {
    # item_id: (target_vol, target_col, fname, reason)
    'e59e743ea6e61dad51d5a73f966f097dbbb6df72d44094f48a1c729a9bac5e7a': (
        'PL141', 1569, '3db4fee2-5a08-42ac-bd40-31116c6b1681-689.txt',
        'heribert_eichstett_hymni_pl141_appendix'
    ),
    'c1477e44d473f85786cb4b5e30ebfa2a414174393a97ba8c8f7bfef08fe468bf': (
        'PL163', 1359, '04a3d694-325c-4a8e-a7db-bf8ff07dfbed-682.txt',
        'concordatum_wormatiense_pl163_col1359'
    ),
    '1731f74a15e994bc6182dd96250da8daa22a3e1f05c7191a74f0bcf7f0498f1e': (
        'PL015', 2205, '40e853d6-4504-4238-b1e5-47170bb119ca-510.txt',
        'ambrose_anacephaleosis_pl015_col2205'
    ),
    'b8bcf7f01a5004d309213790f7d5f7794fc99138a05519902ee3d20d2429124e': (
        'PL015', 2219, '0333a527-f935-4155-8c37-45cef4662344-518.txt',
        'ambrose_concordantiae_pl015_col2219'
    ),
    'fd74bdd22be753a20fdd09aadea62ddf8760cd1e1f47ba127d605ee272cb9f08': (
        'PL142', 1549, 'aa439810-35f8-4f93-96f2-de76157d249a-680.txt',
        'jordani_epistola_ad_benedictum_joannes_xix_pl142_col1549'
    ),
    '84cbf00d522f13236a4640b9326fd8a6c4ee35e986d7147233d2de6ec1e70a64': (
        'PL161', 1427, '1c0895cd-5206-42c9-823f-4393ce030be6-764.txt',
        'roziere_strasbourg_formules_pl161_col1427'
    ),
    '7b29b72166a8aaee68cc59f4bfb10fd721cd0cc982c33814364b6d3da07e589b': (
        'PL165', 1089, '99212b95-cdaa-4e44-a8a9-7194a4a690d0-559.txt',
        'bruno_segni_sacramentis_pl165_col1089_cxlv_errata'
    ),
    'a258f7f8fcf3e73cf246670740dc89802c24aaecc4080b658484eb564d538864': (
        'PL086', 1515, '2bd5ec2a-d6e8-4c8a-a68c-0b083ff56e80-686.txt',
        'mozarabic_breviary_ximenes_pl086_col1315_1515'
    ),
    '112de2b9b48d67b8087af0843b77581cff6721e6c6552e3cb243f725ff9f6979': (
        'PL120', 1365, '1c768847-890b-4c88-8343-13e6b4a2cf6b-687.txt',
        'paschasius_radbertus_partu_virginis_pl120_col1365'
    ),
    '666200696e0b47ee1b2cc9e7ccda933909b1a6f919828e93d2cf4a7dd4cf9b89': (
        'PL095', 1900, '191abcac-312c-4538-8e7e-1b4551dff124-884.txt',
        'pauli_diaconi_homiliae_elenchus_pl095_p884'
    ),
    'd4e1b1a6af050794c1167a36c3bc7330dc339f0e78b531ad56883e2f8e9dfc07': (
        'PL095', 1519, '191abcac-312c-4538-8e7e-1b4551dff124-884.txt',
        'pauli_diaconi_homilia_nativitate_pl095_col1519'
    ),
    '3b0c43960ad1b32e2f635af7e5fae0de5ae5bc40d0fdeed3b426b77022027fb4': (
        'PL186', 1445, 'b51b981d-5bcb-4c04-8044-d5e63457937f-768.txt',
        'constitutiones_sugerii_abbatis_pl186_col1445'
    ),
    '4a17db2db558a13c1fbd6c8fe752ef02e75c64e0c894baeea4b3f3b2ddd51c28': (
        'PL186', 1461, 'b51b981d-5bcb-4c04-8044-d5e63457937f-735.txt',
        'privilegia_s_dionysio_sugerii_pl186_col1461'
    ),
    '1b3fc9f45052ae36a7c6dc031bdcdbf6eb2d5f3d8f2816d417beedd83e85de44': (
        'PL120', 1498, '1ec0b842-2238-43de-9466-7da6416d73d6-611.txt',
        'passio_ss_rufini_et_valerii_radbertus_pl120_col1498'
    ),
    '469ed4187c534ecc3497a11d2278ebb0d217a3460c74aed220b85bfdc6fea8f5': (
        'PL120', 1507, '1ec0b842-2238-43de-9466-7da6416d73d6-614.txt',
        'vita_s_adahardi_radbertus_pl120_col1507'
    ),
    '5a2518cfb74efe1b41840fa944e4d7bdc64c90fcac00e400ce13c98401eb4375': (
        'PL120', 1351, '245e34f4-28e3-418d-aad7-86f18891f1a9-552.txt',
        'epistola_ad_frudegardum_radbertus_pl120_col1351'
    ),
    '82008544c357c249d68e448356c796e89faf25f8d9298563587bd4584f659e94': (
        'PL120', 1387, '1ec0b842-2238-43de-9466-7da6416d73d6-566.txt',
        'de_fide_spe_et_charitate_radbertus_pl120_col1387'
    ),
    '956df1a32d21dd7270ec2ec8cd5378063158099f820067a05080be2ade60dc48': (
        'PL139', 1529, '91c90ce1-03e2-4d18-98e3-6aaaa4c80280-769.txt',
        'henricus_parmensis_sermo_paschate_pl139_col1529_cxxxiv_errata'
    ),
    'd643c4775eb02b911b76afaeb7931db6cc1f24b197addd2f2051d17b0ef64911': (
        'PL120', 1365, '1c768847-890b-4c88-8343-13e6b4a2cf6b-687.txt',
        'liber_de_partu_virginis_radbertus_pl120_col1365'
    ),
    '20dfe850ec20967ae408b268e19e027f12f257f172ae3e9536f8d9dbf4a8685b': (
        'PL203', 1557, '4fa252c0-f0be-4a3c-b8f0-6408bc2bd27d-684.txt',
        'vita_b_gisleni_philippus_harveng_pl203_col1557'
    ),
    '22cd8f0955da815783565cd2a68f663f93d3ef1263a257fc6d68bb6e363bece1': (
        'PL203', 1588, '4fa252c0-f0be-4a3c-b8f0-6408bc2bd27d-708.txt',
        'passio_s_agnetis_philippus_harveng_pl203_col1588'
    ),
    '2e206d0043e06c3e8381421e26f9af33e11ab20a4fd69026eeb955e9afaeca02': (
        'PL203', 1559, '4fa252c0-f0be-4a3c-b8f0-6408bc2bd27d-696.txt',
        'vita_b_odae_philippus_harveng_pl203_col1559'
    ),
    '3f65cf2980a61762c16de6dcd97620de27bdf2389ff274a8800b5852600e8b76': (
        'PL203', 1575, '4fa252c0-f0be-4a3c-b8f0-6408bc2bd27d-702.txt',
        'vita_s_waldeirudis_philippus_harveng_pl203_col1575'
    ),
    '5470f3daf3cc8f2067c4c42fc8bc29fbe6a14ff9ca871900d59b390c70fbcf0d': (
        'PL203', 1525, '4fa252c0-f0be-4a3c-b8f0-6408bc2bd27d-676.txt',
        'vita_s_foillani_philippus_harveng_pl203_col1525'
    ),
    '6376534dd4ef289252a08964677201b31166d135284d7638460c45d229832835': (
        'PL203', 1591, '4fa252c0-f0be-4a3c-b8f0-6408bc2bd27d-709.txt',
        'carmina_varia_philippus_harveng_pl203_col1591'
    ),
    '6f156d53e2702ae6fe75ed630e32a593c3c3d43c88a429d2ab8e70980ed59e56': (
        'PL141', 1597, '3db4fee2-5a08-42ac-bd40-31116c6b1681-703.txt',
        'poppo_trevirensis_notitia_pl141_appendix'
    ),
    'a551e6f3b0f6d1229bb3020e4efe30ea0918bc3a71cf95689ebb6bf1c64e78e1': (
        'PL141', 1601, '3db4fee2-5a08-42ac-bd40-31116c6b1681-705.txt',
        'popponis_epistola_ad_benedictum_pl141_appendix'
    ),
    '8465ff6245d0e6e7b72e41da0afb66dd84965c34d7aee717bbbdfcc407b60993': (
        'PL155', 1299, 'PL155-653.txt',
        'radulphus_ardens_homiliae_sanctorum_pl155_col1299_4299_errata'
    ),
    '8e217b527fbbb16abdae808fbde31066d7c6058211bda964077bad1e6c025090': (
        'PL102', 1095, '3075140d-d30f-4238-ab39-3a7e602d640a-550.txt',
        'remigius_curiensis_notitia_pl102_col1095_1995_errata'
    ),
    '193b83a5aa383cfbf0d9e5aab430886e39c4af1b0eedfb9c9702665a22513f31': (
        'PL186', 1547, 'c6065d03-f954-4de1-b304-9b78460f697a-678.txt',
        'epistolae_sugerii_abbatis_pl186_col1547'
    ),
    '70dfb89910265d9cce6a650b034bff8df95ee027927b7ef92f0305c61dee28c4': (
        'PL032', 1377, 'b64d938d-d5dc-4495-8a91-ce9f2fe1815c-695.txt',
        'augustine_regula_ad_servos_dei_pl032_col1377_xxx_errata'
    ),
}


class PL221MethodicusLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path

    def resolve_item(self, item_id: str, label: str, ref_raw: str, metadata_json: str = '') -> List[Dict[str, Any]]:
        if item_id in PL221_EXACT_MAP:
            target_vol, target_col, fname, reason = PL221_EXACT_MAP[item_id]
            fpath = str((self.root_dir / 'teste' / target_vol / 'text' / fname).resolve())
            return [{
                'target_volume': target_vol,
                'target_column': target_col,
                'target_file': fpath,
                'reason': reason,
                'confidence': 98.0
            }]

        return []

    def run(self, dry_run: bool = True) -> Dict[str, Any]:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        cur.execute(
            """
            SELECT id, volume, section, label, ref_raw, source, metadata_json
            FROM items
            WHERE volume = 'PL221' AND state = 'source_only'
            ORDER BY id
            """
        )
        rows = cur.fetchall()

        promotions = []
        for r in rows:
            item_id, vol, section, label, ref_raw, src, meta_json = r
            cands = self.resolve_item(item_id, label or '', ref_raw or '', meta_json or '')
            if cands:
                promotions.append((r, cands))

        if not dry_run and promotions:
            cur.execute("BEGIN TRANSACTION")
            for r, cands in promotions:
                item_id, vol, section, label, ref_raw, src, meta_json = r
                best = cands[0]

                # 1. Update candidate_storage
                for rank, cand in enumerate(cands, start=1):
                    cand_payload = {
                        "source_type": f"pl221_methodicus_{cand['reason']}",
                        "confidence": "high",
                        "target_volume": cand["target_volume"],
                        "target_column": cand["target_column"],
                        "target_file": cand["target_file"],
                    }
                    compressed = zlib.compress(json.dumps(cand_payload).encode("utf-8"))

                    cur.execute(
                        """
                        INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                        VALUES (?, ?, ?, ?, 1, ?)
                        ON CONFLICT(item_id, file) DO UPDATE SET
                            rank=excluded.rank,
                            score=excluded.score,
                            candidate_json=excluded.candidate_json
                        """,
                        (item_id, cand["target_file"], rank, cand["confidence"], compressed),
                    )

                # 2. Update items metadata_json and state
                meta = json.loads(meta_json) if meta_json else {}
                meta["state"] = "with_destinations"
                meta["target_volume"] = best["target_volume"]
                meta["target_column"] = best["target_column"]
                meta["matched_file"] = best["target_file"]
                meta["locator"] = f"pl221_methodicus_{best['reason']}"
                updated_meta = json.dumps(meta, ensure_ascii=False)

                cur.execute(
                    'UPDATE items SET state="with_destinations", metadata_json=? WHERE id=?',
                    (updated_meta, item_id),
                )

            con.commit()

        con.close()

        return {
            "status": "success",
            "dry_run": dry_run,
            "total_evaluated": len(rows),
            "total_promoted": len(promotions),
            "sample_promotions": [
                {
                    "item_id": p[0][0],
                    "label": p[0][3],
                    "ref_raw": p[0][4],
                    "target_volume": p[1][0]["target_volume"],
                    "target_column": p[1][0]["target_column"],
                    "target_file": p[1][0]["target_file"],
                    "reason": p[1][0]["reason"]
                }
                for p in promotions[:10]
            ]
        }


def main():
    parser = argparse.ArgumentParser(description="Locate destinations for PL221 Index Methodicus / Speciales.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PL221MethodicusLocator(db_path=args.db)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
