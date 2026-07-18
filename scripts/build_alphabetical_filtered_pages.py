#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.common import page_number, page_sort_key, parse_volume_info


PG_PL_MARKERS = (
    "INDEX ANALYTICUS",
    "INDEX RERUM ET VERBORUM",
    "INDEX ONOMASTICUS",
    "ELENCHUS PERSONARUM",
    "ELENCHUS ONOMASTICUS",
    "INDEX SCRIPTORUM",
    "INDEX RERUM",
    "ORDO RERUM",
)

PO_MARKERS = (
    "INDEX DES CITATIONS DES ECRITURES",
    "TABLE DES CITATIONS DE LA BIBLE",
    "TABLE DES PERICOPES DE L ECRITURE",
    "TABLE DES NOMS PROPRES",
    "TABLE DES NOMS PROPRES SYRIAQUES",
    "TABLE DES PERICOPES",
    "TABLE DE CONCORDANCE",
    "TABLE ALPHABETIQUE",
    "TABLE ANALYTIQUE",
    "INDEX ANALYTIQUE",
)

ALL_MARKERS = tuple(sorted({*PG_PL_MARKERS, *PO_MARKERS}))
NOTE_PREFIXES = (
    "- ",
    "TRANSCRI",
    "A PAGINA",
    "A PÁGINA",
    "PAGINA ",
    "PÁGINA ",
    "NOTAS",
    "OBSERVA",
    "OCR ",
    "DIGITIZED BY",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = (
        text.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
    )
    text = text.casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().upper()


def load_external_filtered_pages(
    path: Path,
    *,
    volume_id: str,
    text_root: Path,
) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"Filtered pages payload must be a JSON object: {path}")
    data.setdefault("volume_id", volume_id)
    data.setdefault("source_root", str(text_root))
    data["source"] = "external"
    data["source_file"] = str(path)
    if data.get("volume_id") != volume_id:
        raise SystemExit(
            f"Filtered pages volume_id mismatch in {path}: expected {volume_id}, got {data.get('volume_id')}"
        )
    candidate_files = data.get("candidate_files")
    candidate_sections = data.get("candidate_sections")
    if candidate_files is not None and not isinstance(candidate_files, list):
        raise SystemExit(f"'candidate_files' must be a list in {path}")
    if candidate_sections is not None and not isinstance(candidate_sections, list):
        raise SystemExit(f"'candidate_sections' must be a list in {path}")
    return data


def _looks_like_heading_candidate(stripped: str, marker: str, collection: str) -> bool:
    if not stripped:
        return False
    if len(stripped) > 140:
        return False
    if any(stripped.upper().startswith(prefix) for prefix in NOTE_PREFIXES):
        return False
    normalized = normalize_for_match(stripped)
    marker_norm = normalize_for_match(marker)
    if marker_norm not in normalized:
        return False
    if not re.match(r"^[\[\]\(\)\d\s\*\.\-–—]*[A-ZÆŒ]", stripped):
        return False
    marker_idx = normalized.find(marker_norm)
    if marker_idx < 0 or marker_idx > 12:
        return False

    letters = [c for c in stripped if c.isalpha()]
    uppercase_letters = [c for c in letters if c.isupper()]
    uppercase_ratio = (len(uppercase_letters) / len(letters)) if letters else 0.0
    if collection in {"PG", "PL"}:
        return uppercase_ratio >= 0.72
    return uppercase_ratio >= 0.58


def build_fallback_filtered_pages(volume_id: str, text_root: Path, collection: str) -> dict[str, Any]:
    files = sorted(text_root.glob("*.txt"), key=page_sort_key)
    markers = PO_MARKERS if collection == "PO" else PG_PL_MARKERS
    marker_norms = [normalize_for_match(marker) for marker in markers]
    candidate_sections: list[dict[str, Any]] = []
    candidate_files_seen: set[str] = set()
    candidate_files: list[str] = []
    all_hits: list[dict[str, Any]] = []

    tail_count = min(32, len(files))
    tail_files = files[-tail_count:]
    tail_set = {str(path) for path in tail_files}
    file_to_index = {str(path): idx for idx, path in enumerate(files)}

    def add_candidate_file(path: Path) -> None:
        key = str(path)
        if key not in candidate_files_seen:
            candidate_files_seen.add(key)
            candidate_files.append(key)

    def add_neighbors(path: Path) -> None:
        idx = file_to_index[str(path)]
        for neighbor_idx in (idx - 1, idx, idx + 1):
            if 0 <= neighbor_idx < len(files):
                add_candidate_file(files[neighbor_idx])

    for path in tail_files:
        add_candidate_file(path)

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        path_str = str(path)
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            for marker, marker_norm in zip(markers, marker_norms, strict=True):
                if marker_norm not in normalize_for_match(stripped):
                    continue
                if not _looks_like_heading_candidate(stripped, marker, collection):
                    continue
                hit = {
                    "file": path_str,
                    "file_seq": page_number(path),
                    "line": line_no,
                    "text": stripped[:240],
                    "marker": marker,
                }
                all_hits.append(hit)
                add_neighbors(path)
                reason = "tail_heading_match" if path_str in tail_set else "heading_match"
                candidate_sections.append(
                    {
                        "heading": stripped[:240],
                        "file": path_str,
                        "file_seq": page_number(path),
                        "line": line_no,
                        "reason": reason,
                        "marker": marker,
                    }
                )
                break

    if not candidate_sections:
        for path in tail_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            normalized = normalize_for_match(text[:4000])
            if any(normalize_for_match(marker) in normalized for marker in ALL_MARKERS):
                candidate_sections.append(
                    {
                        "heading": path.name,
                        "file": str(path),
                        "file_seq": page_number(path),
                        "line": None,
                        "reason": "tail_text_match",
                        "marker": "fallback_body_match",
                    }
                )
                add_candidate_file(path)

    return {
        "volume_id": volume_id,
        "source_root": str(text_root),
        "collection": collection,
        "source": "fallback_internal",
        "file_count": len(files),
        "tail_files": [str(path) for path in tail_files],
        "candidate_files": candidate_files,
        "candidate_sections": candidate_sections,
        "all_hits": all_hits,
    }


def resolve_filtered_pages(
    *,
    volume_id: str,
    root: Path,
    filtered_pages_json: Path | None,
    filtered_pages_dir: Path | None,
) -> dict[str, Any]:
    volume_root = root / volume_id
    text_root = volume_root / "text"
    if not text_root.exists():
        raise SystemExit(f"Text directory not found: {text_root}")

    info = parse_volume_info(volume_root)
    if info is None:
        raise SystemExit(f"Could not parse volume info from {volume_root}")

    external_path: Path | None = None
    if filtered_pages_json is not None:
        external_path = filtered_pages_json
    elif filtered_pages_dir is not None:
        candidate = filtered_pages_dir / f"{volume_id}_filtered_pages.json"
        if candidate.exists():
            external_path = candidate

    if external_path is not None:
        return load_external_filtered_pages(external_path, volume_id=volume_id, text_root=text_root)
    return build_fallback_filtered_pages(volume_id, text_root, info.series)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build filtered-pages JSON for alphabetical index extraction.")
    ap.add_argument("--volume", required=True, help="Volume id, e.g. PG003 or PO025")
    ap.add_argument("--root", type=Path, default=Path("teste"), help="Root directory containing volume folders")
    ap.add_argument("--filtered-pages-json", type=Path, help="External filtered-pages JSON for one volume")
    ap.add_argument("--filtered-pages-dir", type=Path, help="Directory with <VOLUME>_filtered_pages.json files")
    ap.add_argument("--output", type=Path, help="Optional output path for the canonical JSON artifact")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = ap.parse_args()

    payload = resolve_filtered_pages(
        volume_id=args.volume,
        root=args.root,
        filtered_pages_json=args.filtered_pages_json,
        filtered_pages_dir=args.filtered_pages_dir,
    )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output is not None:
        args.output.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
