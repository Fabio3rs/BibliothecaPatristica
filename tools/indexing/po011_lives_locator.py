"""
PO011 Hagiographical & Ascetic Collections Locator
Sprint 31 of Onomastic Catalog Recovery

Resolves the 27 residual source_only items from PO011:
1. Vie de saint Luc le Stylite (pages 151-299):
   - Internal bracket pagination: bracket [1] to [143] = physical pages 151 to 293 (offset +150).
   - Greek & onomastic index entries citing bracket pages: 9, 10, 11, 33, 35, 52, 67, 91, 100, 101, 111, 125.
2. Ammonas, successeur de saint Antoine (pages 399-505):
   - Plain pagination: pages 391 to 487 = physical pages 399 to 495 (offset +8).
   - Subject & onomastic index citations: 400, 410, 424, 435, 438, 472, 482, 483.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'

PO011_EXACT_MAP: Dict[str, Tuple[int, str]] = {
    # 8 items of Ammonas:
    'region-entry:36c5320552160f9bb6d1b3775d477d7e2cd512942dadf99444a973e644fa4ebf': (408, 'ammonas_plain_400_antioche'),
    'region-entry:4198ada4129c04d41e7d4af9e95bce659dd3002c84b992fa8a126dee88920802': (490, 'ammonas_plain_482_heritage'),
    'region-entry:4e97b7e501ca37aeba4ceb2ccbfc8524c1fe6cf60325c28eab58788e8d76a1e3': (443, 'ammonas_remissio_ange_gardien_435'),
    'region-entry:98412b71369f5a05414828f147c59fd9f113414b372ec7577a8348c765d69009': (491, 'ammonas_plain_483_monde_fin'),
    'region-entry:998326417f18e29052616065d7eeed05c938456bcb810e4ad7d8079d32afbcec': (480, 'ammonas_plain_472_profondeurs_72_3'),
    'region-entry:520975a9718f998e9de6dcdacceed93295dfbb1df79c7f96b3d90cd5d7dc49cd': (432, 'ammonas_plain_424_thebaide_124_2'),
    'region-entry:b94adf91b74c46119df52f673034b3fd827edb7a36ff0ca5552495dce6303cb3': (446, 'ammonas_remissio_discernement_438'),
    'region-entry:3b5c6e42600ca8170c16e005f2e49a9bdec1d356f29b8c7727a0ef5ccb332e43': (418, 'ammonas_plain_410_monachos'),
    # 19 items of Vie de Saint Luc:
    'region-entry:0494fb3e288bdc4b7eb96b2d27606996127ad94e8ac5a52384537e7eca2f46e8': (241, 'saint_luc_bracket_91_theodora'),
    'region-entry:3a209639dd2d175aae0958ddbecb6bb03609cb4e54d8b947020aec5fc550b0eb': (183, 'saint_luc_bracket_33_bandon'),
    'region-entry:4fb302bf673a3fbb575ab6fb9f45a19a8d9bff93e93ddb6e6e9b59e210d2198c': (241, 'saint_luc_bracket_91_helene'),
    'region-entry:6fe80f05be3cb4ce9d06eead88828c6dfb8e4685511329cc1e8e75d060107ff2': (183, 'saint_luc_bracket_33_attikom'),
    'region-entry:73cd48df79276fe23cffa3225458dad6a41a73bb1d9174cb2b6a1483b22f9d3c': (251, 'saint_luc_bracket_101_eudolos'),
    'region-entry:08bb3091614ecb7abb053797fe9cfa4234401572ee9699b9d7e84f984703714f': (185, 'saint_luc_bracket_35_olympos'),
    'region-entry:b641f8e6e424dbf767ea981b63c17c3110b6929e277e0b73b504765918f7fb36': (183, 'saint_luc_bracket_33_lampe'),
    'region-entry:1b2bb0d49e937b46129c556ebb213d17673dbb9a6cd1a4a2a3b0f843def5d929': (183, 'saint_luc_bracket_33_christophoros_pere'),
    'region-entry:67f51d8b9d57bc19a4e9c986285130cff9c0b56cf298e03af4ea0eff1d0892c4': (241, 'saint_luc_bracket_91_christophoros_sophie'),
    'region-entry:78fa3bf3260237656d41907d3d0716fb2b0418c7b73d21955b47b3800310099d': (250, 'saint_luc_bracket_100_edaphous'),
    'region-entry:7985f912f3ad83925ee462225ad77f853be92f3049c91b15f30870a7ab73f199': (185, 'saint_luc_bracket_35_chalkedoneis'),
    'region-entry:c785e453f4dfe32b267b77500606697b648bcca8e765846e97bdbb59b1aa34e2': (202, 'saint_luc_bracket_52_andrianou'),
    'region-entry:db0b6157cf3b1b709e9af3cd118c357c1c3ab2ac47082086c43174b56682e651': (261, 'saint_luc_bracket_111_amphaton'),
    'region-entry:de00542711e67ea2abd3e7cbb0f4df1621a53e4bdc3c25ac6a0e2b6355120da9': (275, 'saint_luc_bracket_125_amphilotimos'),
    'region-entry:507a16d798ac56730c88e49971b9e266fe04359b3320c5bb62f82ebde4dda369': (161, 'saint_luc_bracket_11_documentation'),
    'region-entry:54208067ede6f0173f362be7e91eb74b5d1c5f08197081ae1b414a2b9fbb8fc2': (217, 'saint_luc_bracket_67_bey_bazar'),
    'region-entry:be3156879266686ac668dffaef2ba40c5594c0b008793510c787cccb73ce6cce': (160, 'saint_luc_bracket_10_eclectisme'),
    'region-entry:fbc37effd04913278f7110fe0324f4d41d5a988f1fba21bc71b823d19f89b776': (159, 'saint_luc_bracket_9_groupement'),
    'region-entry:7c755e508670dc2cd2bfc84f5bf7b321e605516579b93859b5e18cb467369c33': (160, 'saint_luc_bracket_10_lacunes'),
}


class PO011LivesLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self._page_file_cache: Dict[int, str] = {}
        self._init_file_cache()

    def _init_file_cache(self):
        text_dir = self.root_dir / 'teste' / 'PO011' / 'text'
        if not text_dir.exists():
            return
        for f in text_dir.glob('*.txt'):
            try:
                p_num = int(f.stem.split('-')[-1])
                self._page_file_cache[p_num] = str(f.resolve())
            except ValueError:
                continue

    def get_file_for_page(self, physical_page: int) -> Optional[str]:
        return self._page_file_cache.get(physical_page)

    def resolve_item(self, item_id: str, label: str, ref_raw: str, metadata_json: str = '') -> List[Dict[str, Any]]:
        if item_id in PO011_EXACT_MAP:
            p_num, reason = PO011_EXACT_MAP[item_id]
            fpath = self.get_file_for_page(p_num)
            if fpath:
                return [{
                    'target_volume': 'PO011',
                    'physical_page': p_num,
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
            WHERE volume = 'PO011' AND state = 'source_only'
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
                        "source_type": f"po011_lives_{cand['reason']}",
                        "confidence": "high",
                        "target_volume": cand["target_volume"],
                        "target_page": cand["physical_page"],
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
                meta["target_page"] = best["physical_page"]
                meta["matched_file"] = best["target_file"]
                meta["locator"] = f"po011_lives_{best['reason']}"
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
                    "physical_page": p[1][0]["physical_page"],
                    "target_file": p[1][0]["target_file"],
                    "reason": p[1][0]["reason"]
                }
                for p in promotions[:10]
            ]
        }


def main():
    parser = argparse.ArgumentParser(description="Locate destinations for PO011 Hagiographical & Ascetic indices.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PO011LivesLocator(db_path=args.db)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
