#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/PG099_fix_ref_kinds.py --input data/alphabetical_index_payloads/PG099_alphabetical_indices.json --output data/alphabetical_index_payloads/PG099_alphabetical_indices.json
"""Repair invalid PG099 ref_kind values using the current alphabetical schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def infer_ref_kind(ref: dict) -> str:
    if ref.get("line_ref_raw"):
        return "editorial_page_line"
    if ref.get("range_end_raw"):
        return "editorial_range"
    if ref.get("page_ref_int") is not None and ref.get("page_ref_col"):
        return "editorial_page_column"
    if ref.get("page_ref_int") is not None or ref.get("page_ref_raw"):
        return "editorial_page"
    if ref.get("page_ref_col"):
        return "editorial_column"
    if ref.get("target_file"):
        return "target_locator"
    return "unresolved"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    with input_path.open(encoding="utf-8") as fh:
        payload = json.load(fh)

    for ref in payload.get("refs", []):
        ref["ref_kind"] = infer_ref_kind(ref)

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
