"""
PL078 Liturgy and Ordines Romani Locator
Sprint 33 of Onomastic Catalog Recovery

Resolves all 24 residual source_only items from PL078 (St. Gregory the Great / Liturgical Works & Ordines Romani):
1. Editorial Context:
   - PL078 contains:
     a) Liber Sacramentorum (cols 1-240)
     b) Notae et Observationes (cols 241-600+)
     c) Liber Antiphonarius (cols 641-724)
     d) Liber Responsalis (cols 725-850)
     e) Mabillon's Museum Italicum: Ordines Romani I to XVI (cols 937-1406)
     f) Three distinct indexes:
        - Index Rerum in Libro Sacramentorum (cols 1415-1460, pp. 715-736) citing columns
        - Index in Commentarium Praevium (cols 1461-1466, pp. 737-741)
        - Index Libellorum Ordinis Romani (cols 1467-1490, pp. 741-751)
   - "Numeri respondent his crassioribus qui videntur in texto a col. 936 hujus voluminis usque ad 1406":
     In Index Libellorum Ordinis Romani, numbers are continuous paragraph/section numbers
     (1 through ~585) embedded directly inline in the Ordines Romani text.
2. Verified physical mappings:
   - All 24 items mapped deterministically to verified physical pages (pp. 69-707).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'

# Mapping: item_id -> (physical_page, column_or_section, reason)
PL078_MAP: Dict[str, Tuple[int, Optional[int], str]] = {
    # 1. Per omnia saecula saeculorum (Notes col 294, page 148)
    '58a3a8f1e3ab893283bbb32ec28d64015be0b12f3031e6bbb96a3b0cc7c187c5': (
        148, 294, 'sacramentorum_notae_col294_per_omnia_saecula'
    ),
    # 2. Kal. Augusti ad sanctum Petrum ad Vincula (Sacramentary col 128, page 69; note 437 col 409)
    'a281664c2b30786ae06e81ea804a4c3f7fb0574e5cbba61188e8a13926650fa0': (
        69, 128, 'sacramentorum_col128_petrus_ad_vincula'
    ),
    # 3. Quatuor poenitentiae celebres gradus (Notes col 461, page 238)
    'cf823581f628edfd622b073b26679ab90e4155b25d9fc27a12c7f687cfe77c23': (
        238, 461, 'sacramentorum_notae_col461_poenitentiae_gradus'
    ),
    # 4. Baptismus in vigiliis Paschae et Pentecostes (Notes cols 400-401, page 203)
    'group:1d93aef01dc4f0c670223e85f73e081225ccb846cfd9e269c1274b41d786837d': (
        203, 400, 'sacramentorum_notae_col400_pentecostes_baptismus'
    ),
    # 5. Apud Gothos ritus poenitentibus / In sudore (Notes col 451, page 227)
    'd74bb9499e0bc4f0afb8809327b9b2ac576b22e5d72bed7df04a3886c81d8cd0': (
        227, 451, 'sacramentorum_notae_col451_apud_gothos_sudore'
    ),
    # 6. Competentes omnia retroactae vitae peccata confitebantur (Notes col 381, page 191)
    'a2568ef3707e807d8eda563eaf0f91b723d7bfc47ec3b85b070affd0979d4883': (
        191, 381, 'sacramentorum_notae_col381_competentes_confessio'
    ),
    # 7. Philippus Augustus rex a Guillelmo Rhemensi sacratus (Notes col 591, page 295)
    '34f6df0cc914bc42b05174e20e9f5faaf0366389840402e8e13ac56ab8d34e1f': (
        295, 591, 'sacramentorum_notae_col591_philippus_augustus_coronatus'
    ),
    # 8. Sensus cap. 11 regulae S. Benedicti / Dedicatio (Antiphonary col 714, page 362)
    'group:9395c37ed47696de8a3f434339e9573f42e3c8949976a27c198a7471e5380682': (
        362, 714, 'antiphonarius_col714_regula_benedicti_dedicatio'
    ),
    # 9. Simonis et Judae apostolorum festum (Antiphonary col 711-712, page 360)
    '3c45cba545eaf4b9557cf053f8f763ad1ff98db8e91dab7ea658d71cea71c598': (
        360, 711, 'antiphonarius_col711_simon_juda'
    ),
    # 10. Aquaricia clareti (Ordines Romani sec. 162, page 538)
    'group:069ebab63f26c2ead3ec5f6dec7bb276603f0ce55cd29831881802ab48b7dcda': (
        538, 162, 'ordines_romani_sec162_aquaricia_clareti'
    ),
    # 11. Augustini Patritii Picolominei epistola ad Innocentium VIII (Ordines Romani sec. 584, page 707)
    'group:4908f60ce8288af2695311eacae72dcbd18658ec07d3d7c003723b774499a3bf': (
        707, 584, 'ordines_romani_sec584_picolomineus_epistola'
    ),
    # 12. Aquaricia (Ordines Romani sec. 162, page 538)
    'group:6fabd25a52d83a5b83a8baeaceb16c2d2ed1836a6c9074254ec657576895066e': (
        538, 162, 'ordines_romani_sec162_aquaricia'
    ),
    # 13. Bononiensis cardinalis (Ordines Romani sec. 433-437, page 641)
    'facf82318e58f8104213821f425a5ac1e1800b54eb75ebc322f41c6372495b11': (
        641, 433, 'ordines_romani_sec433_bononiensis_cardinalis'
    ),
    # 14. Clareti aquarius (Ordines Romani sec. 162, page 538)
    'group:0f28cc4f1e6f6b8461ec06242dce51ed9000211afecfc28d2aa7e51bed398e6c': (
        538, 162, 'ordines_romani_sec162_clareti_aquarius'
    ),
    # 15. Cardinalium exsequiae (Ordines Romani sec. 541 cap. CLXVI, page 690)
    'group:3bb68281c8bafef64692aa4bff261e4f859d25df0346d046b9ac107984073a52': (
        690, 541, 'ordines_romani_sec541_cardinalium_exsequiae'
    ),
    # 16. Communio papae non ad altare sed ad sedem (Ordines Romani sec. 226, page 562)
    'group:cb90f9ae317d022b6f3ead84c9d3737adce1f33d842eec4f0caf2e24b73490a7': (
        562, 226, 'ordines_romani_sec226_communio_ad_sedem'
    ),
    # 17. Epistolae duae Latina et Graeca in solemnibus sacris (Ordines Romani sec. 168, page 539)
    'group:27298b32af70eadb226a39f690a48e8b9dc77b83015187c1f62e9fc723177559': (
        539, 168, 'ordines_romani_sec168_epistolae_graeca_latina'
    ),
    # 18. Legatorum cardinalium vel nuntiorum creatio (Ordines Romani sec. 438 cap. CXVIII, page 642)
    'group:478d3ee9bdfdc4e61b7e7944758b9e3ef406c80e0313ba97fec6785641a2c476': (
        642, 438, 'ordines_romani_sec438_legatorum_creatio'
    ),
    # 19. Processus generales die Coenae Domini (Ordines Romani sec. 373/382 cap. XCII, page 613)
    '0ac985e1d6f57ad4c53900ffcaa93a518c94ac8bb16259f68dba48a958c8f3a2': (
        613, 382, 'ordines_romani_sec382_processus_coenae_domini'
    ),
    # 20. Ordo ad visitandum et ungendum infirmum (Ordines Romani sec. 111, page 518)
    'group:2bb7dde96eb343eee411d73dfed7544bece98ec626db8883ab4594949e863eb6': (
        518, 111, 'ordines_romani_sec111_ungendum_infirmum'
    ),
    # 21. Silvae Candidae episcopi praerogativae (Ordines Romani sec. 454, page 647)
    'group:6fa48ec7bb476c94685f89b8be1fcde3e7e7544b13d8e23b713dca3617fa0410': (
        647, 454, 'ordines_romani_sec454_silvae_candidae'
    ),
    # 22. Unctionis extremae forma (Ordines Romani sec. 111, page 518)
    'group:70f12857fb762c407fb00b0bf83aa75b0f4b17ff319c5363b08cf00a28086061': (
        518, 111, 'ordines_romani_sec111_unctionis_forma'
    ),
    # 23. Scriniarius seu scribarius (Ordines Romani sec. 168, page 539)
    'group:d173a56f06ceb5f0b6b59290e973014ec8792b29eec0ae0ae59d68f18b63934e': (
        539, 168, 'ordines_romani_sec168_scriniarius'
    ),
    # 24. Scrutinii ordo (Ordines Romani sec. 77, page 504)
    'group:e1ba77f57b4c15de914a228e546856535f7449fd8e3809f0878aef3be61606c1': (
        504, 77, 'ordines_romani_sec77_scrutinii_ordo'
    ),
}


class PL078LiturgyLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self._page_file_cache: Dict[int, str] = {}
        self._init_file_cache()

    def _init_file_cache(self):
        text_dir = self.root_dir / 'teste' / 'PL078' / 'text'
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
        if item_id in PL078_MAP:
            p_num, col_or_sec, reason = PL078_MAP[item_id]
            fpath = self.get_file_for_page(p_num)
            if fpath:
                return [{
                    'target_volume': 'PL078',
                    'physical_page': p_num,
                    'target_column_or_sec': col_or_sec,
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
            WHERE volume = 'PL078' AND state = 'source_only'
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
                        "source_type": f"pl078_liturgy_{cand['reason']}",
                        "confidence": "high",
                        "target_volume": cand["target_volume"],
                        "target_page": cand["physical_page"],
                        "target_file": cand["target_file"],
                    }
                    if cand.get("target_column_or_sec"):
                        cand_payload["target_column_or_sec"] = cand["target_column_or_sec"]

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
                if best.get("target_column_or_sec"):
                    meta["target_column_or_sec"] = best["target_column_or_sec"]
                meta["locator"] = f"pl078_liturgy_{best['reason']}"
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
                    "physical_page": p[1][0]["physical_page"],
                    "target_file": p[1][0]["target_file"],
                    "reason": p[1][0]["reason"]
                }
                for p in promotions[:10]
            ]
        }


def main():
    parser = argparse.ArgumentParser(description="Locate destinations for PL078 Liturgy and Ordines Romani.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PL078LiturgyLocator(db_path=args.db)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
