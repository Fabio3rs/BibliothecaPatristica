"""Physical pairing ported from the alphabetical skill helper; no agent dependency.

Keep selection semantics compatible with locate-ocr-page-image.py.
"""
from pathlib import Path
import re

PHYSICAL_SUFFIX_RE = re.compile(r'-(?P<sequence>\d{1,4})$')

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
