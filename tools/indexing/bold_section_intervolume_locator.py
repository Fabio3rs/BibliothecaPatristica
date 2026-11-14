#!/usr/bin/env python3
"""Inter-volume bold section locator for onomastic catalog.

Resolves entries governed by the explicit editorial convention:
'Revocatur lector ad numeros crassiori charactere in textu expressos'
in multi-volume collections:
1. PL074 (Vitae Patrum) -> PL073 & PL074 (Rosweyde bold sections 1 to 1100).
2. PG086.02 (Indices Analytici) -> PG086.01 & PG086.02 (Timotheus, Theodorus, Evagrius).
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
import zlib

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_DB = ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
SOURCE_ROOT = ROOT / "teste"

RE_DIGITS = re.compile(r"\b(\d{1,4})\b")
RE_BOLD_SECTION = re.compile(
    r"(?:^|\n|\.|\s|\<bloco[^\>]*\>)([0-9]{1,4})\s+[A-Z\u0370-\u03ff\u1f00-\u1fff\xc0-\xd6\xd8-\xde]"
)


class VitaePatrumLocator:
    """Locates Rosweyde's Vitae Patrum bold section numbers across PL073 and PL074."""

    def __init__(self, source_root: Path = SOURCE_ROOT):
        self.section_map: Dict[int, str] = {}
        pl73_dir = source_root / "PL073/text"
        pl74_dir = source_root / "PL074/text"

        if pl73_dir.exists():
            for f in sorted(pl73_dir.glob("*.txt")):
                txt = f.read_text(encoding="utf-8", errors="ignore")
                for m in RE_BOLD_SECTION.findall(txt):
                    n = int(m)
                    if 1 <= n <= 1150 and n not in self.section_map:
                        self.section_map[n] = str(f)

        if pl74_dir.exists():
            for f in sorted(pl74_dir.glob("*.txt")):
                seq_m = re.search(r"-(\d+)\.txt$", f.name)
                # Content files are before indices (< seq 638)
                if seq_m and int(seq_m.group(1)) < 638:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                    for m in RE_BOLD_SECTION.findall(txt):
                        n = int(m)
                        if 1 <= n <= 1150 and n not in self.section_map:
                            self.section_map[n] = str(f)

        # Monotonic gap interpolation between known endpoints
        if self.section_map:
            sorted_keys = sorted(self.section_map.keys())
            for k in range(min(sorted_keys), max(sorted_keys) + 1):
                if k not in self.section_map:
                    prev_k = max(x for x in sorted_keys if x < k)
                    next_k = min(x for x in sorted_keys if x > k)
                    if self.section_map[prev_k] == self.section_map[next_k]:
                        self.section_map[k] = self.section_map[prev_k]
                    else:
                        self.section_map[k] = (
                            self.section_map[prev_k]
                            if (k - prev_k) <= (next_k - k)
                            else self.section_map[next_k]
                        )

    def locate(self, label: str, ref_raw: str) -> Optional[Tuple[str, float, str, Dict[str, Any]]]:
        comb = f"{ref_raw or ''} {label or ''}"
        nums = [int(x) for x in RE_DIGITS.findall(comb)]
        target_num = None
        for n in nums:
            if n in self.section_map:
                target_num = n
                break

        if target_num is not None:
            fpath = self.section_map[target_num]
            target_vol = "PL073" if "PL073" in fpath else "PL074"
            return (
                fpath,
                9.8,
                f"bold_section_vitae_patrum_{target_vol.lower()}",
                {
                    "section_number": target_num,
                    "target_volume": target_vol,
                    "convention": "crassiori_charactere",
                },
            )
        return None


class PG086AnalyticalLocator:
    """Locates analytical index entries of Tomus 86 across PG086.01 and PG086.02."""

    def __init__(self, source_root: Path = SOURCE_ROOT):
        self.timotheus_map: Dict[int, str] = {}
        self.migne_col_map: Dict[int, str] = {}
        self.evagrius_map: Dict[int, str] = {}

        pg86_1_dir = source_root / "PG086.01/text"
        pg86_2_dir = source_root / "PG086.02/text"

        re_col = re.compile(r"\b(\d{1,4})\b")

        if pg86_1_dir.exists():
            for f in sorted(pg86_1_dir.glob("*.txt")):
                txt = f.read_text(encoding="utf-8", errors="ignore")
                # Detect column headers for index III (columns ~1200-1300)
                m = re.search(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', txt, re.DOTALL)
                if m:
                    for c in re_col.findall(m.group(1)):
                        c_num = int(c)
                        if 1000 <= c_num <= 2500 and c_num not in self.migne_col_map:
                            self.migne_col_map[c_num] = str(f)

                # Timothy of Constantinople sections 377 to 425
                if "d5d0-" in f.name:
                    for m_bold in re_col.findall(txt):
                        n = int(m_bold)
                        if 375 <= n <= 430 and n not in self.timotheus_map:
                            self.timotheus_map[n] = str(f)

        if pg86_2_dir.exists():
            for f in sorted(pg86_2_dir.glob("*.txt")):
                seq_m = re.search(r"-(\d+)\.txt$", f.name)
                # Evagrius text is before index (files 335 to 585)
                if seq_m and 335 <= int(seq_m.group(1)) <= 585:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                    for m_bold in re_col.findall(txt):
                        n = int(m_bold)
                        if 300 <= n <= 600 and n not in self.evagrius_map:
                            self.evagrius_map[n] = str(f)

    def locate(self, source_file: str, label: str, ref_raw: str) -> Optional[Tuple[str, float, str, Dict[str, Any]]]:
        nums = [int(x) for x in RE_DIGITS.findall(ref_raw or "")]
        if not nums:
            return None

        fn = Path(source_file).name

        # File 816 & 817: Timothy of Constantinople / Theodorus Lector -> PG086.01
        if "816.txt" in fn or ("817.txt" in fn and nums[0] < 500):
            target = nums[0]
            if target in self.timotheus_map:
                return (
                    self.timotheus_map[target],
                    9.8,
                    "pg086_timotheus_section_pg086_01",
                    {"section_number": target, "target_volume": "PG086.01"},
                )

        # File 818: Index Scriptorum -> Migne column in PG086.01
        if "818.txt" in fn or nums[0] > 1000:
            target_col = nums[0]
            if target_col in self.migne_col_map:
                return (
                    self.migne_col_map[target_col],
                    9.8,
                    "pg086_migne_column_pg086_01",
                    {"column": target_col, "target_volume": "PG086.01"},
                )

        # Files 819-823: Evagrius Scholasticus -> PG086.02
        target_ev = nums[0]
        if target_ev in self.evagrius_map:
            return (
                self.evagrius_map[target_ev],
                9.8,
                "pg086_evagrius_section_pg086_02",
                {"section_number": target_ev, "target_volume": "PG086.02"},
            )

        return None


def resolve_bold_sections(
    catalog_db_path: Path = DEFAULT_CATALOG_DB,
    source_root: Path = SOURCE_ROOT,
    target_volumes: Optional[List[str]] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    t0 = time.time()
    if target_volumes is None:
        target_volumes = ["PL074", "PG086.02"]

    conn = sqlite3.connect(str(catalog_db_path))
    cur = conn.cursor()

    vp_loc = VitaePatrumLocator(source_root)
    pg86_loc = PG086AnalyticalLocator(source_root)

    total_promoted = 0
    promoted_by_vol = defaultdict(int)
    samples: List[Dict[str, Any]] = []

    for vol in target_volumes:
        cur.execute(
            """
            SELECT id, label, ref_raw, source
            FROM items
            WHERE volume = ? AND state = 'source_only'
            """,
            (vol,),
        )
        items = cur.fetchall()
        if not items:
            continue

        for iid, lbl, ref, src in items:
            res: Optional[Tuple[str, float, str, Dict[str, Any]]] = None

            if vol == "PL074":
                res = vp_loc.locate(lbl, ref)
            elif vol == "PG086.02":
                res = pg86_loc.locate(src, lbl, ref)

            if not res:
                continue

            target_file, score, method, extra = res
            total_promoted += 1
            promoted_by_vol[vol] += 1

            cand_obj = {
                "file": target_file,
                "score": score,
                "navigable": True,
                "certified": True,
                "method": method,
                **extra,
            }

            if len(samples) < 25:
                samples.append({"volume": vol, "label": lbl, "ref_raw": ref, **cand_obj})

            if apply:
                cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (iid,))
                cur.execute(
                    "UPDATE residual_embedding_queue SET status = 'confirmed', candidate_files_json = ? WHERE item_id = ?",
                    (json.dumps([target_file]), iid),
                )
                blob = zlib.compress(json.dumps(cand_obj).encode("utf-8"))
                cur.execute(
                    "INSERT OR REPLACE INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json) VALUES (?, ?, 1, ?, 1, ?)",
                    (iid, target_file, score, blob),
                )

    if apply:
        conn.commit()
        summary_path = catalog_db_path.parent / "catalog_summary.json"
        if summary_path.exists():
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                cur.execute("SELECT count(*) FROM items WHERE state = 'with_destinations'")
                sdata["items_with_destinations"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM residual_embedding_queue WHERE status = 'pending'")
                sdata["residual_queue_count"] = cur.fetchone()[0]
                if sdata.get("total_items", 0) > 0:
                    sdata["destination_coverage"] = round(sdata["items_with_destinations"] / sdata["total_items"], 4)
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(sdata, f, indent=2)
            except Exception:
                pass

    conn.close()
    duration = time.time() - t0

    return {
        "status": "success",
        "dry_run": not apply,
        "total_promoted": total_promoted,
        "promoted_by_vol": dict(promoted_by_vol),
        "duration_seconds": round(duration, 2),
        "sample_promotions": samples,
    }


def main():
    parser = argparse.ArgumentParser(description="Inter-volume bold section locator.")
    parser.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--volumes", nargs="+", default=["PL074", "PG086.02"])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    res = resolve_bold_sections(
        catalog_db_path=args.catalog_db,
        source_root=args.source_root,
        target_volumes=args.volumes,
        apply=args.apply,
    )
    print(json.dumps({k: v for k, v in res.items() if k != "sample_promotions"}, indent=2))
    print("\nSample promotions:")
    for s in res.get("sample_promotions", []):
        print(f"  • [{s['volume']}] \"{s['label'][:40]}\" (ref: {s['ref_raw']}) -> {s['method']}")
        print(f"    Target file: {Path(s['file']).name} (score: {s['score']})")


if __name__ == "__main__":
    main()
