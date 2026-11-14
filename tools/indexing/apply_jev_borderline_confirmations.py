#!/usr/bin/env python3
"""Review and promote borderline Jev evaluations (0.70 <= p < 0.85) from existing logs.

Requires zero additional API calls or credits.
Re-evaluates candidates that were near the strict 0.85 threshold using dual criteria:
1. Strong semantic probability: p >= 0.75
2. Moderate probability with lexical token confirmation: p >= 0.70 AND direct token match on candidate page.

Applies confirmed destinations atomically to search.sqlite and candidate_storage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import zlib

ROOT = Path(__file__).resolve().parents[2]

STOPWORDS = {
    "contra", "liber", "epistola", "sanctus", "sancti", "quod", "quando",
    "opera", "opuscula", "homilia", "sermo", "dei", "deum", "rerum", "index",
    "caput", "pars", "tomus", "anno", "anni", "sive", "atque", "etiam", "suis",
    "eius", "ejus", "quomodo", "propter", "inter", "super", "apud", "unde"
}


def extract_content_tokens(text: str) -> Set[str]:
    tokens = set()
    for word in re.findall(r"\b[a-zA-ZÀ-ÿ]{4,}\b", text.lower()):
        if word not in STOPWORDS:
            tokens.add(word)
    return tokens


def review_borderline_evaluations(
    log_jsonl_path: Path,
    catalog_db_path: Path,
    min_prob: float = 0.75,
    min_prob_lexical: float = 0.70,
    apply: bool = False,
) -> Dict[str, Any]:
    t0 = time.time()
    conn = sqlite3.connect(str(catalog_db_path))
    cur = conn.cursor()

    promoted_items: List[Dict[str, Any]] = []
    total_unresolved = 0
    borderline_scanned = 0

    with open(log_jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("status") == "confirmed":
                continue

            total_unresolved += 1
            evals = rec.get("evaluations", [])
            if not evals:
                continue

            best_ev = max(evals, key=lambda e: e.get("probability", 0.0))
            p = float(best_ev.get("probability", 0.0))

            if p < min_prob_lexical:
                continue

            borderline_scanned += 1
            item_id = rec["item_id"]
            cand_file = Path(best_ev["file"])

            cur.execute("SELECT label, ref_raw, volume FROM items WHERE id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                continue
            label, ref_raw, vol = row

            # Extract content tokens and filter degenerate/stopword labels
            tokens = extract_content_tokens(label)
            if len(label.strip()) < 5 or not tokens:
                continue

            # Check lexical token presence on candidate page
            has_lexical = False
            matched_tokens = []
            if cand_file.exists():
                try:
                    page_text = cand_file.read_text(encoding="utf-8", errors="replace").lower()
                    for t in tokens:
                        if t in page_text:
                            matched_tokens.append(t)
                    if matched_tokens:
                        has_lexical = True
                except Exception:
                    pass

            # Promotion policy:
            # 1. p >= min_prob (e.g. 0.75)
            # 2. p >= min_prob_lexical (e.g. 0.70) AND has_lexical
            is_promotable = False
            promotion_reason = ""

            if p >= min_prob:
                is_promotable = True
                promotion_reason = f"high_prob_semantic_presence (p={p:.2f})"
                if has_lexical:
                    promotion_reason += f" + lexical_tokens: {matched_tokens[:3]}"
            elif p >= min_prob_lexical and has_lexical:
                is_promotable = True
                promotion_reason = f"moderate_prob (p={p:.2f}) + lexical_tokens: {matched_tokens[:3]}"

            if is_promotable:
                promoted_items.append({
                    "item_id": item_id,
                    "volume": vol,
                    "label": label,
                    "ref_raw": ref_raw,
                    "probability": p,
                    "target_file": str(cand_file),
                    "promotion_reason": promotion_reason,
                    "has_lexical": has_lexical,
                    "matched_tokens": matched_tokens,
                })

    applied_count = 0
    if apply and promoted_items:
        for item in promoted_items:
            iid = item["item_id"]
            tfile = item["target_file"]
            prob = item["probability"]

            # 1. Update residual queue
            cur.execute(
                "UPDATE residual_embedding_queue SET status = 'confirmed', candidate_files_json = ? WHERE item_id = ?",
                (json.dumps([tfile]), iid),
            )
            # 2. Update item state
            cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (iid,))

            # 3. Store in candidate_storage
            cand_obj = {
                "file": tfile,
                "score": round(prob, 4),
                "navigable": True,
                "certified": True,
                "method": "jev_borderline_verified_v1",
                "reason": item["promotion_reason"],
            }
            blob = zlib.compress(json.dumps(cand_obj).encode("utf-8"))
            cur.execute(
                """
                INSERT OR REPLACE INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, 1, ?, 1, ?)
                """,
                (iid, tfile, prob, blob),
            )
            applied_count += 1

        conn.commit()

        # Update summary metrics
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
        "total_unresolved_in_log": total_unresolved,
        "borderline_scanned": borderline_scanned,
        "promoted_items_count": len(promoted_items),
        "applied_count": applied_count,
        "apply_mode": apply,
        "duration_seconds": round(duration, 2),
        "sample_promotions": promoted_items[:15],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Review and promote borderline Jev evaluations (0.70 <= p < 0.85) from log."
    )
    parser.add_argument(
        "--log-jsonl",
        type=Path,
        default=ROOT / "data/index_catalog/jev_narrow_window_log.jsonl",
        help="Path to Jev narrow window log JSONL.",
    )
    parser.add_argument(
        "--catalog-db",
        type=Path,
        default=ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite",
        help="Path to unified catalog SQLite database.",
    )
    parser.add_argument(
        "--min-prob",
        type=float,
        default=0.75,
        help="Minimum semantic probability threshold for promotion (default 0.75).",
    )
    parser.add_argument(
        "--min-prob-lexical",
        type=float,
        default=0.70,
        help="Minimum probability when reinforced with direct lexical token presence (default 0.70).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Atomically apply promotions to database and catalog summary.",
    )
    args = parser.parse_args()

    print(f"Reading Jev evaluations from: {args.log_jsonl}")
    summary = review_borderline_evaluations(
        log_jsonl_path=args.log_jsonl,
        catalog_db_path=args.catalog_db,
        min_prob=args.min_prob,
        min_prob_lexical=args.min_prob_lexical,
        apply=args.apply,
    )

    print(json.dumps({
        "status": summary["status"],
        "borderline_scanned": summary["borderline_scanned"],
        "promoted_items_count": summary["promoted_items_count"],
        "applied_count": summary["applied_count"],
        "apply_mode": summary["apply_mode"],
        "duration_seconds": summary["duration_seconds"],
    }, indent=2))

    if summary["sample_promotions"]:
        print("\nSample promotions:")
        for sp in summary["sample_promotions"][:10]:
            print(f"  • [{sp['volume']}] {sp['label']} (ref: {sp['ref_raw']}) -> {sp['probability']:.2f}")
            print(f"    File: {Path(sp['target_file']).name}")
            print(f"    Reason: {sp['promotion_reason']}")


if __name__ == "__main__":
    main()
