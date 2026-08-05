#!/usr/bin/env python3
"""Repair OCR split-word hyphen artifacts in the PG142 alphabetical payload.

Usage:
  python scripts/pipeline_index_extraction/repair_pg142_hyphen_artifacts.py \
    --input data/alphabetical_index_payloads/PG142_alphabetical_indices.json \
    --output data/alphabetical_index_payloads/PG142_alphabetical_indices.json

The script removes line-break hyphen artifacts from entry text fields only.
It preserves the rest of the payload structure and metadata.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


LETTER = r"[^\W\d_]"
HYPHEN_SPLIT_RE = re.compile(rf"(?<={LETTER})-\s+(?={LETTER})", re.UNICODE)
TRAILING_HYPHEN_RE = re.compile(rf"(?<={LETTER})-$", re.UNICODE)
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:])")
MULTISPACE_RE = re.compile(r" {2,}")


def clean_text(value: str) -> str:
    value = HYPHEN_SPLIT_RE.sub("", value)
    value = TRAILING_HYPHEN_RE.sub("", value)
    value = SPACE_BEFORE_PUNCT_RE.sub(r"\1", value)
    value = MULTISPACE_RE.sub(" ", value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8") as fh:
      data = json.load(fh)

    changed = 0
    for entry in data.get("entries", []):
        for field in ("lemma_raw", "lemma_display", "entry_raw"):
            value = entry.get(field)
            if isinstance(value, str):
                cleaned = clean_text(value)
                if cleaned != value:
                    entry[field] = cleaned
                    changed += 1

    with args.output.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"updated_fields={changed}")


if __name__ == "__main__":
    main()
