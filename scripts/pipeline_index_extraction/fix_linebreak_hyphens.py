#!/usr/bin/env python3
"""Join likely OCR line-break hyphen artifacts in alphabetical index payloads.

Usage:
  python scripts/pipeline_index_extraction/fix_linebreak_hyphens.py INPUT_JSON OUTPUT_JSON

The script only cleans canonical text fields such as entry_raw and lemma_raw.
It leaves raw_json and path-like fields untouched.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


LETTER_HYPHEN_WRAP = re.compile(r"(?<=[^\W\d_])-\s+(?=[^\W\d_])", re.UNICODE)

TEXT_FIELDS = {
    "entry_raw",
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "context_raw",
    "ref_raw",
    "book_raw",
    "book_norm",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
}


def clean_text(value: str) -> str:
    previous = None
    cleaned = value
    while previous != cleaned:
        previous = cleaned
        cleaned = LETTER_HYPHEN_WRAP.sub("", cleaned)
    return cleaned


def clean_payload(payload: dict) -> dict:
    for entry in payload.get("entries", []):
        for key in TEXT_FIELDS:
            if isinstance(entry.get(key), str):
                entry[key] = clean_text(entry[key])

    for ref in payload.get("refs", []):
        for key in TEXT_FIELDS:
            if isinstance(ref.get(key), str):
                ref[key] = clean_text(ref[key])

    for ref in payload.get("scripture_refs", []):
        for key in TEXT_FIELDS:
            if isinstance(ref.get(key), str):
                ref[key] = clean_text(ref[key])

    return payload


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    with input_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    clean_payload(payload)

    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
