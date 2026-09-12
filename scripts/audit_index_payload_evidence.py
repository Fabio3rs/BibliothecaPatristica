#!/usr/bin/env python3
"""Batch the deterministic evidence verifier over existing index payloads.

This produces per-payload reports and a compact review queue, allowing legacy payloads to be
checked for missing sources, unsupported text, suspicious segmentation, and unjustified empty
sections without loading the corpus into an agent context.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.index_payload_evidence import verify_index_payload_evidence


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _payload_files(collection: str) -> list[Path]:
    files: list[Path] = []
    if collection in {"all", "general"}:
        files.extend(sorted((PROJECT_ROOT / "data" / "index_payloads").glob("*_indices.json")))
    if collection in {"all", "alphabetical"}:
        files.extend(
            sorted(
                (PROJECT_ROOT / "data" / "alphabetical_index_payloads").glob(
                    "*_alphabetical_indices.json"
                )
            )
        )
    return files


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Audit every existing general/alphabetical index payload against physical OCR evidence."
    )
    ap.add_argument("--collection", choices=("all", "general", "alphabetical"), default="all")
    ap.add_argument("--sample-size", type=int, default=50, help="Per payload; use 0 for every entry")
    ap.add_argument("--fuzzy-threshold", type=float, default=0.78)
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "index_payload_audits",
    )
    ap.add_argument("--summary", type=Path)
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    payload_files = _payload_files(args.collection)
    for order, payload_file in enumerate(payload_files, start=1):
        if order == 1 or order % 25 == 0 or order == len(payload_files):
            print(
                f"[INFO] audit {order}/{len(payload_files)} {payload_file.name}",
                file=sys.stderr,
                flush=True,
            )
        try:
            payload = json.loads(payload_file.read_text(encoding="utf-8"))
            report = verify_index_payload_evidence(
                payload,
                sample_size=None if args.sample_size == 0 else args.sample_size,
                fuzzy_threshold=args.fuzzy_threshold,
            )
            report["payload_file"] = str(payload_file)
            report_path = args.output_dir / f"{payload_file.stem}_evidence.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            records.append(
                {
                    "payload_file": str(payload_file),
                    "report_file": str(report_path),
                    "volume_id": report.get("volume_id"),
                    "pipeline_kind": report.get("pipeline_kind"),
                    "status": "audited",
                    "payload_entry_count": report.get("payload_entry_count"),
                    "sampled_entry_count": report.get("sampled_entry_count"),
                    "verified_ratio": report.get("verified_ratio"),
                    "segmentation_suspect_count": report.get("segmentation_suspect_count"),
                    "empty_list_section_count": report.get("empty_list_section_count"),
                    "unjustified_empty_list_section_count": report.get(
                        "unjustified_empty_list_section_count"
                    ),
                    "counts": report.get("counts"),
                }
            )
        except Exception as exc:
            records.append(
                {
                    "payload_file": str(payload_file),
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    audited = [item for item in records if item.get("status") == "audited"]
    errors = [item for item in records if item.get("status") == "error"]
    review_queue = [
        item
        for item in audited
        if item.get("verified_ratio") is None
        or float(item.get("verified_ratio") or 0) < 0.8
        or int(item.get("segmentation_suspect_count") or 0) > 0
        or int(item.get("unjustified_empty_list_section_count") or 0) > 0
    ]
    summary = {
        "schema_version": 1,
        "collection": args.collection,
        "sample_size_per_payload": args.sample_size,
        "fuzzy_threshold": args.fuzzy_threshold,
        "payload_count": len(payload_files),
        "audited_count": len(audited),
        "error_count": len(errors),
        "review_queue_count": len(review_queue),
        "review_queue": review_queue,
        "records": records,
    }
    summary_path = args.summary or args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
