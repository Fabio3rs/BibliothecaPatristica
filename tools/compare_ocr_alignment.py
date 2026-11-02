#!/usr/bin/env python3
"""Compare two explicit OCR artifacts with the experimental v2 aligner."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline_ocr.diff_v2 import compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True, help="Saved transcription (XML or text)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--reference", type=Path, help="Reference OCR text (reading order unverified)")
    group.add_argument("--tsv", type=Path, help="Tesseract TSV from the original image")
    parser.add_argument("--image", type=Path, help="Original image; required with --tsv")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-hyphens", action="store_true")
    args = parser.parse_args()
    if args.tsv and not args.image:
        parser.error("--tsv requires --image")
    inputs = [args.candidate, args.reference, args.tsv, args.image]
    if any(p is not None and p.resolve() == args.output.resolve() for p in inputs):
        parser.error("Output must not overwrite an input artifact")
    kwargs = {"dehyphenate": not args.keep_hyphens}
    if args.tsv:
        from PIL import Image
        with Image.open(args.image) as image:
            kwargs["image_size"] = image.size
        kwargs["reference_tsv"] = args.tsv.read_text(encoding="utf-8")
    else:
        kwargs["reference_text"] = args.reference.read_text(encoding="utf-8")
    result = compare(args.candidate.read_text(encoding="utf-8"), **kwargs)
    result["paths"] = {"candidate": str(args.candidate), "reference": str(args.tsv or args.reference),
                       "image": str(args.image) if args.image else None}
    if args.image:
        result["image_sha256"] = hashlib.sha256(args.image.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "mode": result["mode"],
                      "stats": result["stats"], "coverage": result["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
