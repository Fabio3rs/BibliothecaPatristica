#!/usr/bin/env python3
"""Verify one existing index payload against the physical OCR evidence it declares.

The verifier was created to catch page-system confusion and unsupported transcriptions after
extraction. It uses exact and OCR-tolerant fuzzy matching as evidence checks; it does not decide the
editorial semantics or silently rewrite payload data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.index_payload_evidence import verify_index_payload_evidence


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Verify index payload transcriptions against their declared physical OCR source files."
    )
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--sample-size", type=int, default=200, help="Deterministic sample; use 0 for all entries")
    ap.add_argument("--fuzzy-threshold", type=float, default=0.78)
    ap.add_argument(
        "--fail-unverified-ratio",
        type=float,
        help="Exit nonzero when the unverified share of applicable sampled entries exceeds this value",
    )
    args = ap.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    report = verify_index_payload_evidence(
        payload,
        sample_size=None if args.sample_size == 0 else args.sample_size,
        fuzzy_threshold=args.fuzzy_threshold,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")

    if args.fail_unverified_ratio is not None:
        sampled = int(report.get("sampled_entry_count") or 0)
        not_applicable = int((report.get("counts") or {}).get("not_applicable") or 0)
        applicable = sampled - not_applicable
        unverified = sum(
            int((report.get("counts") or {}).get(key) or 0)
            for key in ("unverified", "missing_physical_source")
        )
        if applicable and unverified / applicable > args.fail_unverified_ratio:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
