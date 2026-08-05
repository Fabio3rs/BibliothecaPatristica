#!/usr/bin/env python3
"""Repair OCR line-break hyphen artifacts in an alphabetical payload JSON.

Usage:
  python scripts/pipeline_index_extraction/repair_alphabetical_payload_hyphens.py \
    --input data/alphabetical_index_payloads/PL006_alphabetical_indices.json \
    --output /tmp/PL006_alphabetical_indices.fixed.json

The script only normalizes visible entry text fields. It leaves raw helper
evidence untouched so OCR decisions remain auditable.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


HYPHEN_WRAP_RE = re.compile(r"([A-Za-zÀ-ÿÆŒæœ])-\s+([A-Za-zÀ-ÿÆŒæœ])")
SPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^\wÀ-ÿÆŒæœ]+", re.UNICODE)


def merge_linebreak_hyphens(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = HYPHEN_WRAP_RE.sub(r"\1\2", text)
    return text


def normalize_lemma_text(text: str) -> str:
    text = merge_linebreak_hyphens(text)
    text = PUNCT_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text)
    return text.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Input payload JSON")
    parser.add_argument("--output", required=True, help="Output payload JSON")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    payload = json.loads(input_path.read_text())
    for entry in payload.get("entries", []):
        cleaned_lemma = None
        for key in ("lemma_raw", "lemma_display", "entry_raw", "context_raw"):
            value = entry.get(key)
            if isinstance(value, str):
                cleaned = merge_linebreak_hyphens(value)
                entry[key] = cleaned
                if key == "lemma_raw":
                    cleaned_lemma = cleaned
        if cleaned_lemma is not None:
            entry["lemma_norm"] = normalize_lemma_text(cleaned_lemma.lower())
            entry["lemma_sort"] = entry["lemma_norm"]

    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
