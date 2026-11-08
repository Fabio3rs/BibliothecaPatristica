#!/usr/bin/env python3
"""Replay bounded numbering investigations; source databases are read-only.

The profile contains explicit investigation scopes and prior visual observations,
not automatic resolution rules. Regex hits are evidence, never certifications.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.corpus_utils import page_files
from tools.indexing.editorial_ocr_blocks import blocks

VERSION = "numbering-case-investigation-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_evidence(raw, pattern):
    """Keep raw character offsets; exclude XML attributes and model commentary."""
    regions = blocks(raw)
    if not regions and not re.search(r"<|\[(?:pagina|bloco|texto_principal)", raw):
        regions = [{"body": raw, "body_start": 0, "kind": "unstructured"}]
    found = []
    for block in regions:
        masked = re.sub(r"<[^>]*>|\[[^\]]*/\]", lambda m: " " * len(m[0]), block["body"])
        for match in re.finditer(pattern, masked, re.I | re.S):
            start = block["body_start"] + match.start()
            end = block["body_start"] + match.end()
            found.append({"kind": block["kind"], "span": [start, end],
                          "quote": raw[start:end], "line": raw.count("\n", 0, start) + 1,
                          "snippet": re.sub(r"\s+", " ", masked[max(0, match.start()-90):match.end()+180])})
    return found


def investigate(root, profile_path, audit_path, versions_path, out):
    profile = json.loads(profile_path.read_text())
    cases = profile["cases"]
    wanted = {ident for case in cases for ident in case["item_ids"]}
    items = {}
    with audit_path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row["id"] in wanted:
                items[row["id"]] = row
    missing = wanted - items.keys()
    if missing:
        raise ValueError(f"Items absent from audit: {sorted(missing)}")
    out.mkdir(parents=True, exist_ok=False)
    inventory = {}

    def files(volume):
        if volume not in inventory:
            inventory[volume] = page_files(root / "teste" / volume / "text", suffixes=(".txt",))
        return inventory[volume]

    results = []
    with closing(sqlite3.connect(versions_path.resolve().as_uri()+"?mode=ro", uri=True)) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        for case in cases:
            result = {"case_id": case["case_id"], "diagnosis": case["diagnosis"],
                      "reviewed_candidates": case["reviewed_candidates"],
                      "candidate_origin": "prior_directed_investigation_not_automatic_resolver",
                      "items": [items[i] for i in case["item_ids"]],
                      "probes": [], "history": [], "visual_observations": []}
            for probe in case["probes"]:
                evidence = []
                scanned = 0
                for page, paths in sorted(files(probe["volume"]).items()):
                    if not probe["start"] <= page <= probe["end"]:
                        continue
                    for path in paths:
                        scanned += 1
                        raw = path.read_text()
                        hits = find_evidence(raw, probe["pattern"])
                        if hits:
                            evidence.append({"path": str(path.resolve()), "physical": page,
                                             "sha256": sha(path), "hits": hits})
                result["probes"].append({**probe, "files_scanned": scanned, "evidence": evidence})
            for page in case["history_pages"]:
                rows = con.execute("""SELECT r.id, r.text_content, r.text_hash, r.image_hash,
                    r.is_current, e.engine, e.model FROM ocr_results r
                    JOIN ocr_engine_versions e ON e.id=r.engine_version_id
                    WHERE r.volume_id=? AND r.page_num=? ORDER BY r.id""",
                    (page["volume"], page["physical"]))
                for ident, raw, text_hash, image_hash, current, engine, model in rows:
                    result["history"].append({"volume": page["volume"], "physical": page["physical"],
                        "version_id": ident, "engine": engine, "model": model,
                        "is_current": bool(current), "text_sha256": text_hash,
                        "image_sha256": image_hash, "hits": find_evidence(raw, page["pattern"])})
            for observation in case["visual_observations"]:
                path = root / observation["image"]
                current_hash = sha(path) if path.is_file() else None
                result["visual_observations"].append({**observation,
                    "current_image_sha256": current_hash,
                    "image_bytes_changed_since_review": current_hash != observation["reviewed_sha256"]})
            for item in result["items"]:
                source = Path(item["source"])
                if not source.is_file():
                    item["current_source_state"] = "missing"
                    continue
                raw = source.read_text()
                a, b = item["source_span"]
                item["current_source_state"] = "same_bytes" if sha(source) == item["source_sha256"] else "changed"
                item["saved_span_still_matches"] = raw[a:b] == item["ref_raw"]
            results.append(result)
    summary = {"version": VERSION, "cases": len(results), "audit_items": len(items),
               "case_diagnoses": dict(Counter(r["diagnosis"] for r in results)),
               "canonical_db_updates": 0, "inference_calls": 0,
               "sampling": "directed_five_examples_not_quality_estimate",
               "profile": str(profile_path.resolve()), "profile_sha256": sha(profile_path),
               "audit": str(audit_path.resolve()), "versions_db": str(versions_path.resolve()),
               "candidates_are_prior_review_observations": True}
    (out / "cases.json").write_text(json.dumps(results, ensure_ascii=False, indent=2)+"\n")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--profile", type=Path, default=ROOT/"docs/data/numbering_gap_five_cases.json")
    parser.add_argument("--audit", type=Path, default=ROOT/"data/index_catalog/destination_gap_audit_combined_002/items.jsonl")
    parser.add_argument("--ocr-versions", type=Path, default=ROOT/"data/ocr_versions.db")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(investigate(args.root, args.profile, args.audit, args.ocr_versions, args.out),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
