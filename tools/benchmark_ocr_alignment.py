#!/usr/bin/env python3
"""Reproduce PG001:638 and controlled edits without models or production writes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline_ocr.diff_v2 import SUPPORTED, VERSION, compare
from offline_ocr.layout import parse_llm

FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/ocr_diff_v2"


def _statuses(result):
    return {x: link["status"] for link in result["links"] for x in link["reference_ids"]}


def _edit(text, tokens, corruption=False):
    patches = []
    for token in tokens:
        spans = [span for piece in token["pieces"] for span in piece["source_spans"]]
        value = token["text"]
        if corruption:
            middle = len(value) // 2
            replacement = "q" if value[middle] != "q" else "z"
            value = value[:middle] + replacement + value[middle + 1:]
        else:
            value = ""
        patches.extend((a, b, value if index == 0 else "") for index, (a, b) in enumerate(spans))
    for a, b, value in sorted(patches, reverse=True):
        text = text[:a] + value + text[b:]
    return text


def run(fixtures=FIXTURES):
    started = time.perf_counter()
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        if hashlib.sha256((fixtures / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Fixture hash mismatch: {name}")
    tsv = (fixtures / "PG001_638_migne_raw.tsv").read_text(encoding="utf-8")
    reference = (fixtures / "PG001_638_migne_cached.txt").read_text(encoding="utf-8")
    previous = (fixtures / "PG001_638_previous.xml").read_text(encoding="utf-8")
    kwargs = {"reference_tsv": tsv, "image_size": manifest["image_size"]}
    real_cases = []
    for case in manifest["cases"]:
        candidate = (fixtures / case["candidate"]).read_text(encoding="utf-8")
        for mode in ("tsv", "plain_cache"):
            result = compare(candidate, **kwargs) if mode == "tsv" else compare(candidate, reference)
            target = [l for l in result["links"] if any(w in l["reference_text"].split()
                                                      for w in ("adulteros", "homicidas"))]
            real_cases.append({"version_id": case["version_id"], "mode": mode,
                               "expected_target_status": case["target_status"],
                               "passed": len(target) == 2 and all(l["status"] == case["target_status"] for l in target),
                               "target_links": target, "stats": result["stats"]})
    baseline = compare(previous, **kwargs)
    baseline_status = _statuses(baseline)
    linked = {x: link for link in baseline["links"] for x in link["candidate_ids"]}
    tokens = baseline["candidate_tokens"]
    body_blocks = {b["id"] for b in parse_llm(previous)["blocks"] if b["type"] == "texto_principal"}
    selected = []
    # Fixed positions across both main columns; no selection based on mutation results.
    for block in sorted(body_blocks):
        body = [t for t in tokens if t["block_id"] == block]
        for position in (40, 100, 180):
            for length in (1, 2, 5, 10):
                run_tokens = body[position:position + length]
                if len(run_tokens) != length:
                    raise ValueError("Fixture no longer contains the benchmark positions")
                selected.append((f"{block}:{position}+{length}", run_tokens))
    experiments = []
    for name, selection in selected:
        # Some existing words already disagree; report this explicitly in the denominator.
        expected = {r for t in selection for r in linked[t["id"]]["reference_ids"]
                    if linked[t["id"]]["status"] in SUPPORTED}
        result = compare(_edit(previous, selection), **kwargs)
        observed = _statuses(result)
        detected = {r for r in expected if observed[r] == "reference_only"}
        other = {r for r in observed if observed[r] == "reference_only"
                 and baseline_status[r] != "reference_only" and r not in expected}
        experiments.append({"case": f"delete:{name}", "kind": "deletion", "text": " ".join(t["text"] for t in selection),
                            "evaluated_reference_tokens": len(expected), "detected_tokens": len(detected),
                            "additional_unmatched_tokens": len(other),
                            "passed": bool(expected) and detected == expected and not other})
    # Corrupt long, originally paired words: they should stay paired, not become omissions.
    noisy = [t for t in tokens if t["block_id"] in body_blocks and len(t["text"]) >= 7
             and len(t["pieces"]) == 1 and len(t["pieces"][0]["source_spans"]) == 1
             and linked[t["id"]]["status"] in {"equal", "normalized_equal"}]
    for position in (0, 10, 20, 30, 40, 50):
        token = noisy[position]
        result = compare(_edit(previous, [token], corruption=True), **kwargs)
        observed = _statuses(result)
        expected = linked[token["id"]]["reference_ids"]
        experiments.append({"case": f"corrupt:{token['id']}", "kind": "corruption", "text": token["text"],
                            "target_statuses": [observed[r] for r in expected],
                            "passed": all(observed[r] in SUPPORTED for r in expected)})
    deletions = [e for e in experiments if e["kind"] == "deletion"]
    corruptions = [e for e in experiments if e["kind"] == "corruption"]
    reasons = ["Only one real facsimile is annotated, with two target words; this is not corpus-wide accuracy.",
               "Shared layout heuristics still produce unmatched header/apparatus words in the historical control."]
    if not all(c["passed"] for c in real_cases):
        reasons.append("The production text cache interleaves columns and fails the known-omission test without TSV.")
    if not all(e["passed"] for e in experiments):
        reasons.append("Some controlled edits are only partly detected or introduce unmatched neighbors.")
    return {"version": VERSION, "page": manifest["page"], "fixture_manifest": str(fixtures / "manifest.json"),
            "real_cases": real_cases, "controlled_edits": experiments,
            "summary": {"real_tsv_passed": sum(c["passed"] for c in real_cases if c["mode"] == "tsv"),
                        "real_tsv_total": 2, "deletion_cases_passed": sum(e["passed"] for e in deletions),
                        "deletion_cases_total": len(deletions),
                        "deleted_reference_tokens": sum(e["evaluated_reference_tokens"] for e in deletions),
                        "detected_deleted_tokens": sum(e["detected_tokens"] for e in deletions),
                        "corruption_cases_passed": sum(e["passed"] for e in corruptions),
                        "corruption_cases_total": len(corruptions)},
            "integration_ready": False, "integration_blockers": reasons,
            "duration_seconds": round(time.perf_counter() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(FIXTURES.resolve()):
        parser.error("Output must not overwrite benchmark fixtures")
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **report["summary"],
                      "integration_ready": report["integration_ready"],
                      "duration_seconds": report["duration_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
