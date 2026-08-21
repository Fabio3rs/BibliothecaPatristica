#!/usr/bin/env python3
"""Locate the page image paired with one physical OCR text file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PHYSICAL_SUFFIX_RE = re.compile(r"-(?P<sequence>\d{1,4})$")


def parse_physical_sequence(path: Path) -> int | None:
    """Copy of the filename rule used by ``main2.py`` for page images."""

    match = PHYSICAL_SUFFIX_RE.search(path.stem)
    return int(match.group("sequence")) if match else None


def find_images_by_page(images_dir: Path, page_num: int) -> list[Path]:
    """Adapt ``main2.find_image_by_page`` without silently choosing a tie."""

    padded = sorted(images_dir.glob(f"*-{page_num:03d}.png"))
    unpadded = sorted(images_dir.glob(f"*-{page_num}.png"))
    return sorted({*padded, *unpadded})


def locate_page_image(ocr_file: Path) -> dict[str, object]:
    ocr_file = ocr_file.expanduser().resolve()
    result: dict[str, object] = {
        "ocr_file": str(ocr_file),
        "status": "missing",
        "physical_sequence": None,
        "image_path": None,
        "candidates": [],
        "pairing_basis": "physical_sequence_suffix",
        "editorial_page_inferred": False,
    }
    if not ocr_file.is_file():
        result["reason"] = "OCR file does not exist"
        return result

    sequence = parse_physical_sequence(ocr_file)
    if sequence is None:
        result["reason"] = "OCR filename has no terminal physical sequence"
        return result

    result["physical_sequence"] = sequence

    text_dir = ocr_file.parent
    volume_dir = text_dir.parent if text_dir.name == "text" else text_dir
    images_dir = volume_dir / "images"
    result["images_dir"] = str(images_dir.resolve())
    if not images_dir.is_dir():
        result["reason"] = "Sibling images directory does not exist"
        return result

    candidates = find_images_by_page(images_dir, sequence)
    exact_names = {
        (images_dir / f"{volume_dir.name}-{sequence:03d}.png").resolve(),
        (images_dir / f"{volume_dir.name}-{sequence}.png").resolve(),
    }
    ordered = sorted(
        (candidate.resolve() for candidate in candidates if candidate.is_file()),
        key=lambda candidate: (
            candidate not in exact_names,
            candidate.name,
        ),
    )
    result["candidates"] = [str(candidate) for candidate in ordered]
    if not ordered:
        result["reason"] = "No PNG matches the OCR physical sequence"
    elif len(ordered) == 1:
        result["status"] = "found"
        result["image_path"] = str(ordered[0])
    else:
        exact = [
            candidate
            for candidate in ordered
            if candidate in exact_names
        ]
        if len(exact) == 1:
            result["status"] = "found"
            result["image_path"] = str(exact[0])
            result["reason"] = "Selected the exact volume-name match"
        else:
            result["status"] = "ambiguous"
            result["reason"] = "Multiple PNGs match the physical sequence"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Locate a sibling page PNG from an OCR .txt filename. The physical "
            "suffix is used only for pairing and never as editorial pagination."
        )
    )
    parser.add_argument("--ocr-file", required=True, type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    print(
        json.dumps(
            locate_page_image(args.ocr_file),
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
