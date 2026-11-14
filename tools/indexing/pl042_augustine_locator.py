"""
PL042 Augustine De Trinitate & Appendix Locator
Sprint 37 of Onomastic Catalog Recovery

Resolves all 22 residual source_only items from PL042 (St. Augustine, Tomus Octavus):
1. Editorial Context:
   - PL042 contains:
     * De Trinitate libri quindecim (cols 819-1098, pp. 413-552)
     * Appendix Tomi Octavi:
       - Adversus Quinque Haereses Tractatus (cols 1101-1116, pp. 554-561)
       - Sermo adversus Judaeos, Paganos et Arianos (cols 1117-1130, pp. 562-568)
     * Elenchus Capitum in Libros De Trinitate (cols 1227-1234, pp. 617-620)
   - The Elenchus Capitum provides chapter-by-chapter summaries citing exact Migne column numbers.
   - All 22 unresolved entries from PL042 contain citations by 'ibid.' or column ranges ('985-984'
     for 983-984) that inherit the column cited in the preceding entry, mapping to verified physical
     pages in PL042 (pages 430, 459, 460, 469, 473, 487, 495, 496, 502, 503, 506, 513, 515,
     521, 523, 531, 532, 543, 554, 564).
2. Verified physical mappings:
   - All 22 items mapped deterministically to verified physical pages in PL042.
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

# Mapping: item_id -> (physical_page, chapter_or_col, reason)
PL042_MAP: Dict[str, Tuple[int, Optional[str], str]] = {
    # 1. Liber II, cap. IX: Contra eos qui credebant solum Patrem immortalem (col 854 -> p. 430)
    'region-entry:e441afec269bf40b7aa753d10ab02fce72e287f7b6aac52c7b824fe234676f62': (
        430, 'col 854', 'augustine_trinitate_lib2_cap9_col854'
    ),
    # 2. Liber V, cap. II: Arianorum argumentum ex voce geniti (cols 911-912 -> p. 459)
    'region-entry:5153745806035bc46454e1351c32e6dfe7eb916b2e2bb3c5c3cc2e79b385330e': (
        459, 'cols 911-912', 'augustine_trinitate_lib5_cap2_cols911_912'
    ),
    # 3. Liber V, cap. IV: In Deo nihil secundum accidens (col 913 -> p. 460)
    'region-entry:bda2c355910e0b93420e0b6094dffec6e3a733b217ba001754809360b0285901': (
        460, 'col 913', 'augustine_trinitate_lib5_cap4_col913'
    ),
    # 4. Liber VII, cap. I: Rediit ad quaestionem an qualibet persona sit sapientia (cols 931-932 -> p. 469)
    'region-entry:343ff765326181151f2f25a0d2c546a427978b3d770855f6adbe690637ec959a': (
        469, 'cols 931-932', 'augustine_trinitate_lib7_cap1_cols931_932'
    ),
    # 5. Liber VIII, cap. I: De Trinitate quomodo inquirendum (cols 959-960 -> p. 473)
    'region-entry:85589b84b39497561945f695b781272a7fe36873de25f85ff30e840a48d1e6b8': (
        473, 'cols 959-960', 'augustine_trinitate_lib8_cap1_cols959_960'
    ),
    # 6. Liber IX, cap. VII: Cupiditas et charitas quo differunt (col 967 -> p. 487)
    'region-entry:42aec851c0596dc6843bd352df1e4e55e43a0564f3e010a18358ecee0dcf674b': (
        487, 'col 967', 'augustine_trinitate_lib9_cap7_col967'
    ),
    # 7. Liber XI, cap. I: Vestigia Trinitatis in exteriore homine (cols 983-984 -> p. 495)
    'region-entry:86d0ff7d7b48a945e91f7edd8ccb1a6cdfde8634ac55e2da63ceec726e86ea8e': (
        495, 'cols 983-984', 'augustine_trinitate_lib11_cap1_cols983_984'
    ),
    # 8. Liber XI, cap. II: Trinitas quaedam in visione (cols 985-986 -> p. 496)
    'region-entry:1f02ffff6a012ea2e9cc75908a7a8a1321e20a12f78b922ba6e2edbbc4c08890': (
        496, 'cols 985-986', 'augustine_trinitate_lib11_cap2_cols985_986'
    ),
    # 9. Liber XII, cap. I: Homo exterior et interior qualis (cols 997-998 -> p. 502)
    'region-entry:30fc61b853d8104ddd92c2724a6c577c3e8c16f5e6d857d900cb8ce9b7a78530': (
        502, 'cols 997-998', 'augustine_trinitate_lib12_cap1_cols997_998'
    ),
    # 10. Liber XII, cap. III: Ratio superior quae ad contemplationem pertinet (col 999 -> p. 503)
    'region-entry:53b9e8c21e0a0c58ecdda90653a9487e990cddf01b3a5ae9170384a7e1008044': (
        503, 'col 999', 'augustine_trinitate_lib12_cap3_col999'
    ),
    # 11. Liber XII, cap. IX: Sequitur de eodem argumento (col 1005 -> p. 506)
    'region-entry:f71136242f057a5e8ffbbe964f53cea8a6b9183ddf6cfdb4c57ee78c95311c51': (
        506, 'col 1005', 'augustine_trinitate_lib12_cap9_col1005'
    ),
    # 12. Liber XIII, cap. VII: Fides necessaria ut sit homo beatus (col 1020 -> p. 513)
    'region-entry:3c77f22bcd6435ad1e509f1b0436d24fae996180806d01db5532ee15d5fd7009': (
        513, 'col 1020', 'augustine_trinitate_lib13_cap7_col1020'
    ),
    # 13. Liber XIII, cap. XIII: Non potentia sed justitia eruendus homo (col 1024 -> p. 515)
    'region-entry:75cedb1dc68163b58134ef99e906cbe2c4bd7aeb31e276e77436fee8dd0f5c1c': (
        515, 'col 1024', 'augustine_trinitate_lib13_cap13_col1024'
    ),
    # 14. Liber XIV, cap. I: Quae sit sapientia de qua hic agendum (cols 1035-1036 -> p. 521)
    'region-entry:4aa641f4171e1ad8bf1d08519574e11c1287c30c22a2248b1ec417cdf5e8424f': (
        521, 'cols 1035-1036', 'augustine_trinitate_lib14_cap1_cols1035_1036'
    ),
    # 15. Liber XIV, cap. V: Infantium mens an se noverit (col 1040 -> p. 523)
    'region-entry:cb35c1dea7691fced88d247070f4a7105537f82c1b91e743dab0e87861ad1128': (
        523, 'col 1040', 'augustine_trinitate_lib14_cap5_col1040'
    ),
    # 16. Liber XIV, cap. XIX: Similitudine cum Trinitate in vita aeterna (col 1055 -> p. 531)
    'c987f120f48a1a8d70e630924820dfc7c93861b32a5dcab73973887110718893': (
        531, 'col 1055', 'augustine_trinitate_lib14_cap19_similitudo'
    ),
    # 17. Liber XIV, cap. XIX: Joannes potius intelligendus (col 1055 -> p. 531)
    'd42e8410e33a5c42f7383f5d9db3fbf451281f565ecaf766bfe9f6589cf32706': (
        531, 'col 1055', 'augustine_trinitate_lib14_cap19_joannes'
    ),
    # 18. Liber XV, cap. II: Deus incomprehensibilis semper quaerendus (cols 1057-1058 -> p. 532)
    'region-entry:87eefa40e4f5fc46331f49c878f53b5aedf50f14732ef12cb11cbe857e694d77': (
        532, 'cols 1057-1058', 'augustine_trinitate_lib15_cap2_cols1057_1058'
    ),
    # 19. Liber XV, cap. XVII: Quomodo Spiritus sanctus dicatur charitas (col 1079 -> p. 543)
    'region-entry:87954480c503ade658877e90cf0b0c62de9f4f471f5cda5d9fce9a36237f677d': (
        543, 'col 1079', 'augustine_trinitate_lib15_cap17_col1079'
    ),
    # 20. Adversus Quinque Haereses, cap. II: Fiducia adversus haereticos (cols 1101-1103 -> p. 554)
    'region-entry:b4cc7b2e0362decc08e6107c74388db0c95b65ce0b191d2d964688627866ee71': (
        554, 'cols 1101-1103', 'augustine_quinque_haereses_cap2'
    ),
    # 21. Adversus Quinque Haereses, cap. III: Ex Hermete et Sibylla adversus Paganos (cols 1101-1103 -> p. 554)
    'region-entry:cc12e91eef06df48dcfd1a2fac06b305caadf1238d7d628fc42c07555da8a48b': (
        554, 'cols 1101-1103', 'augustine_quinque_haereses_cap3'
    ),
    # 22. Sermo adversus Judaeos, Paganos et Arianos, cap. X: Crudelitas Herodis (col 1122 -> p. 564)
    'region-entry:82fe400161913b9dbaa9a80e313d03c77b652f213b2cb607102931ff03561db7': (
        564, 'col 1122', 'augustine_sermo_contra_judaeos_cap10_col1122'
    ),
}


class PL042AugustineLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self.summary_path = summary_path
        self._page_file_cache: Dict[int, str] = {}
        self._init_file_cache()

    def _init_file_cache(self):
        text_dir = self.root_dir / 'teste' / 'PL042' / 'text'
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
        if item_id in PL042_MAP:
            p_num, cap_or_col, reason = PL042_MAP[item_id]
            fpath = self.get_file_for_page(p_num)
            if fpath:
                return [{
                    'target_volume': 'PL042',
                    'physical_page': p_num,
                    'target_cap_or_col': cap_or_col,
                    'target_file': fpath,
                    'reason': reason,
                    'confidence': 99.0
                }]

        return []

    def run(self, dry_run: bool = True) -> Dict[str, Any]:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        cur.execute(
            """
            SELECT id, volume, section, label, ref_raw, source, metadata_json
            FROM items
            WHERE volume = 'PL042' AND state = 'source_only'
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
                        "source_type": f"augustine_trinitate_{cand['reason']}",
                        "confidence": "high",
                        "target_volume": cand["target_volume"],
                        "target_page": cand["physical_page"],
                        "target_file": cand["target_file"],
                    }
                    if cand.get("target_cap_or_col"):
                        cand_payload["target_cap_or_col"] = cand["target_cap_or_col"]

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
                if best.get("target_cap_or_col"):
                    meta["target_cap_or_col"] = best["target_cap_or_col"]
                meta["locator"] = f"augustine_trinitate_{best['reason']}"
                updated_meta = json.dumps(meta, ensure_ascii=False)

                cur.execute(
                    'UPDATE items SET state="with_destinations", metadata_json=? WHERE id=?',
                    (updated_meta, item_id),
                )

            con.commit()

            # 3. Update catalog_summary.json
            if os.path.exists(self.summary_path):
                with open(self.summary_path, 'r', encoding='utf-8') as sf:
                    summary = json.load(sf)
                prev_with = summary.get("items_with_destinations", 0)
                prev_src = summary.get("items_source_only", 0)
                prev_cand = summary.get("candidate_storage_records", 0)
                promoted = len(promotions)

                summary["items_with_destinations"] = prev_with + promoted
                summary["items_source_only"] = prev_src - promoted
                summary["candidate_storage_records"] = prev_cand + promoted
                summary["candidate_storage_count"] = prev_cand + promoted
                total = summary.get("total_items", 344276)
                summary["destination_coverage"] = round((prev_with + promoted) / total, 4)
                summary["coverage_percentage"] = round((prev_with + promoted) / total * 100, 4)
                summary["updated_at"] = datetime.now(timezone.utc).isoformat()

                with open(self.summary_path, 'w', encoding='utf-8') as sf:
                    json.dump(summary, sf, indent=2, ensure_ascii=False)

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
    parser = argparse.ArgumentParser(description="Locate destinations for PL042 Augustine De Trinitate & Appendix.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--summary", default=SUMMARY_PATH, help="Path to catalog_summary.json")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PL042AugustineLocator(db_path=args.db, summary_path=args.summary)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
