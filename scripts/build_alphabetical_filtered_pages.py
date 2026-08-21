#!/usr/bin/env python3
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
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
    "INDEX GENERALIS",
    "INDEX ALPHABETICUS",
    "INDEX SECTIONUM",
    "INDEX GRAECITATIS",
    "INDEX IN",
    "INDICES",
    "INDEX",
    "ORDO RERUM",
)

PO_MARKERS = (
    "INDEX DES CITATIONS DES ECRITURES",
    "TABLE DES CITATIONS DE LA BIBLE",
    "TABLE DES CITATIONS BIBLIQUES",
    "TABLE DES CITATIONS DE L ECRITURE",
    "TABLE DES CITATIONS DE LA SAINTE ECRITURE",
    "TABLE DES PASSAGES DE LA BIBLE",
    "TABLE DES RENVOIS A L ECRITURE",
    "TABLE DES CITATIONS DES PERES DE L EGLISE",
    "TABLE DES PERICOPES DE L ECRITURE",
    "TABLE DES NOMS PROPRES",
    "TABLE DES NOMS PROPRES SYRIAQUES",
    "TABLE DE TOUS LES NOMS PROPRES",
    "SECONDE TABLE DES NOMS PROPRES",
    "NOMS PROPRES ET PARTICULARITES REMARQUABLES",
    "TABLE FRANCAISE DES NOMS PROPRES",
    "TABLE GRECQUE DES NOMS PROPRES",
    "TABLE ETHIOPIENNE DES NOMS PROPRES",
    "TABLE DES MOTS SYRIAQUES ETRANGERS OU REMARQUABLES",
    "TABLE DES MOTS SYRIAQUES ETRANGERS",
    "TABLE DES MOTS SYRIAQUES",
    "TABLE DES MOTS GRECS CITES DANS LES MSS",
    "TABLE DES MOTS GRECS",
    "TABLE DES MOTS REMARQUABLES",
    "TABLE DES PERICOPES",
    "TABLE DE CONCORDANCE",
    "TABLE ALPHABETIQUE",
    "TABLE ANALYTIQUE",
    "INDEX ANALYTIQUE",
    "INDEX DES NOMS PROPRES",
    "INDEX DES TABLES PARTICULIERES",
    "TABLE",
    "INDEX",
)

GENERAL_PG_PL_MARKERS = (
    "CONSPECTUS TOMI",
    "SYLLABUS AUCTORUM",
    "SYLLABUS RERUM",
    "AUCTORUM ET OPERUM",
    "ELENCHUS AUCTORUM",
    "ELENCHUS OPERUM",
    "ELENCHUS RERUM",
    "INDEX CAPITUM",
    "ORDO OPERUM",
    "ORDO RERUM",
)
GENERAL_PO_MARKERS = (
    "TABLE DU TOME",
    "TABLE DES MATIERES",
    "TABLE GENERALE",
    "TABLE OF CONTENTS",
)
PG_PL_ALPHABETICAL_BOUNDARY_MARKERS = (
    "AUCTORUM ET OPERUM",
    "CONSPECTUS TOMI",
    "SYLLABUS AUCTORUM",
    "ELENCHUS OPERUM",
    "INDEX CAPITUM",
    "ORDO OPERUM",
    "ORDO RERUM",
)
PO_ALPHABETICAL_BOUNDARY_MARKERS = (
    "TABLE DU TOME",
    "TABLE OF CONTENTS",
)
AMBIGUOUS_GENERAL_FRONT_MARKERS = {"ELENCHUS RERUM", "SYLLABUS RERUM"}
ALL_MARKERS = tuple(
    sorted({*PG_PL_MARKERS, *PO_MARKERS, *GENERAL_PG_PL_MARKERS, *GENERAL_PO_MARKERS})
)
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


def visible_line_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def marker_offset(normalized_text: str, normalized_marker: str) -> int:
    match = re.search(
        rf"(?<!\w){re.escape(normalized_marker)}(?!\w)",
        normalized_text,
    )
    return match.start() if match else -1


OCR_HEADING_TRANSLATION = str.maketrans(
    {
        "0": "O",
        "1": "I",
        "|": "I",
        "5": "S",
        "8": "B",
    }
)


def normalize_heading_for_cer(text: str) -> str:
    text = re.sub(
        r"(?<=[^\W\d_])[-‐‑]\s+(?=[^\W\d_])",
        "",
        text,
        flags=re.UNICODE,
    )
    normalized = normalize_for_match(text).translate(OCR_HEADING_TRANSLATION)
    normalized = re.sub(r"\bL(?=NDEX\b)", "I", normalized)
    return normalized


def _marker_match_quality(text: str, marker: str) -> tuple[bool, str]:
    normalized = normalize_heading_for_cer(text)
    marker_norm = normalize_heading_for_cer(marker)
    if marker_offset(normalized, marker_norm) >= 0:
        return True, "normalized_exact"
    marker_tokens = marker_norm.split()
    text_tokens = normalized.split()
    if len(marker_tokens) >= 2 and len(marker_norm) >= 10:
        compact_marker = "".join(marker_tokens)
        compact_text = "".join(text_tokens)
        if compact_marker in compact_text:
            return True, "ocr_split_joined"
    if len(marker_tokens) < 2 or not text_tokens:
        return False, "none"
    anchor = marker_tokens[0]
    if not any(
        token[:1] == anchor[:1] and abs(len(token) - len(anchor)) <= 2
        for token in text_tokens
    ):
        return False, "none"
    for marker_token in marker_tokens:
        if not any(
            SequenceMatcher(None, marker_token, text_token).ratio() >= 0.72
            for text_token in text_tokens
        ):
            return False, "none"
    window_min = max(1, len(marker_tokens) - 1)
    window_max = min(len(text_tokens), len(marker_tokens) + 1)
    best = 0.0
    for width in range(window_min, window_max + 1):
        for start in range(0, len(text_tokens) - width + 1):
            candidate = " ".join(text_tokens[start : start + width])
            best = max(best, SequenceMatcher(None, marker_norm, candidate).ratio())
    threshold = 0.80 if len(marker_norm) >= 14 else 0.86
    return best >= threshold, f"cer_fuzzy:{best:.3f}"


def _best_marker_match(text: str, markers: tuple[str, ...]) -> tuple[str, str]:
    normalized = normalize_heading_for_cer(text)
    for marker in markers:
        marker_norm = normalize_heading_for_cer(marker)
        if marker_offset(normalized, marker_norm) >= 0:
            return marker, "normalized_exact"
    best_marker = ""
    best_quality = 0.0
    for marker in markers:
        matched, quality = _marker_match_quality(text, marker)
        if not matched:
            continue
        score = (
            0.995
            if quality == "ocr_split_joined"
            else float(quality.split(":", 1)[1])
            if quality.startswith("cer_fuzzy:")
            else 0.0
        )
        if score > best_quality:
            best_marker = marker
            best_quality = score
    if best_marker:
        return best_marker, (
            "ocr_split_joined"
            if best_quality == 0.995
            else f"cer_fuzzy:{best_quality:.3f}"
        )
    return "", "none"


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


def _looks_like_heading_shape(stripped: str, collection: str) -> bool:
    if not stripped:
        return False
    if len(stripped) > 280:
        return False
    if any(stripped.upper().startswith(prefix) for prefix in NOTE_PREFIXES):
        return False
    if not re.match(r"^[\[\]\(\)\d\s\*\.\-–—]*[A-ZÆŒ]", stripped):
        return False
    letters = [c for c in stripped if c.isalpha()]
    uppercase_letters = [c for c in letters if c.isupper()]
    uppercase_ratio = (len(uppercase_letters) / len(letters)) if letters else 0.0
    if collection in {"PG", "PL"}:
        return uppercase_ratio >= 0.72
    return uppercase_ratio >= 0.58


def _looks_like_heading_candidate(stripped: str, marker: str, collection: str) -> bool:
    if not _looks_like_heading_shape(stripped, collection):
        return False
    matched, _ = _marker_match_quality(stripped, marker)
    return matched


def build_fallback_filtered_pages(
    volume_id: str,
    text_root: Path,
    collection: str,
    profile: str = "alphabetical",
) -> dict[str, Any]:
    if profile not in {"alphabetical", "general"}:
        raise ValueError(f"Unsupported filtered-pages profile: {profile}")
    files = sorted(text_root.glob("*.txt"), key=page_sort_key)
    if profile == "general":
        markers = GENERAL_PO_MARKERS if collection == "PO" else GENERAL_PG_PL_MARKERS
        boundary_markers: set[str] = set()
    else:
        owned_markers = PO_MARKERS if collection == "PO" else PG_PL_MARKERS
        boundary_marker_values = (
            PO_ALPHABETICAL_BOUNDARY_MARKERS
            if collection == "PO"
            else PG_PL_ALPHABETICAL_BOUNDARY_MARKERS
        )
        markers = tuple(
            sorted(
                {*owned_markers, *boundary_marker_values},
                key=lambda value: (-len(normalize_for_match(value)), value),
            )
        )
        boundary_markers = set(boundary_marker_values)
    candidate_sections: list[dict[str, Any]] = []
    candidate_files_seen: set[str] = set()
    candidate_files: list[str] = []
    all_hits: list[dict[str, Any]] = []

    window_count = min(32, len(files))
    head_files = files[:window_count] if profile == "general" else []
    tail_files = files[-window_count:] if profile == "alphabetical" else []
    seed_files = head_files or tail_files
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

    for path in seed_files:
        add_candidate_file(path)

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        visible_lines = [
            (line_no, visible_line_text(line))
            for line_no, line in enumerate(text.splitlines(), start=1)
        ]
        visible_lines = [(line_no, line) for line_no, line in visible_lines if line]
        path_str = str(path)
        for visible_index, (line_no, stripped) in enumerate(visible_lines):
            matched_marker = ""
            matched_text = ""
            match_quality = "none"
            matched_line_end = line_no
            best_score: tuple[int, int, int] | None = None
            for width in (1, 2, 3, 4, 5):
                window = visible_lines[visible_index : visible_index + width]
                if len(window) != width:
                    continue
                candidate_text = " ".join(item[1] for item in window)
                if not _looks_like_heading_shape(candidate_text, collection):
                    continue
                marker, quality = _best_marker_match(candidate_text, markers)
                if not marker:
                    continue
                score = (
                    len(normalize_for_match(marker)),
                    1 if quality == "normalized_exact" else 0,
                    -width,
                )
                if best_score is None or score > best_score:
                    best_score = score
                    matched_marker = marker
                    matched_text = candidate_text
                    match_quality = quality
                    matched_line_end = window[-1][0]
            if not matched_text:
                continue
            marker = matched_marker
            if (
                profile == "general"
                and marker in AMBIGUOUS_GENERAL_FRONT_MARKERS
                and file_to_index[path_str] >= max(1, int(len(files) * 0.35))
            ):
                continue
            if any(
                item["file"] == path_str
                and item["marker"] == marker
                and line_no <= int(item.get("line_end") or item["line"])
                and matched_line_end >= int(item["line"])
                for item in candidate_sections
            ):
                continue
            hit = {
                "file": path_str,
                "file_seq": page_number(path),
                "line": line_no,
                "line_end": matched_line_end,
                "text": matched_text[:240],
                "marker": marker,
                "match_quality": match_quality,
            }
            all_hits.append(hit)
            add_neighbors(path)
            reason = "tail_heading_match" if path_str in tail_set else "heading_match"
            candidate_sections.append(
                {
                    "heading": matched_text[:240],
                    "file": path_str,
                    "file_seq": page_number(path),
                    "line": line_no,
                    "line_end": matched_line_end,
                    "reason": reason,
                    "marker": marker,
                    "match_quality": match_quality,
                    "role": (
                        "alphabetical_stop_boundary"
                        if profile == "alphabetical" and marker in boundary_markers
                        else "section_heading"
                    ),
                }
            )

    if not candidate_sections:
        for path in seed_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            normalized = normalize_for_match(text[:4000])
            if any(marker_offset(normalized, normalize_for_match(marker)) >= 0 for marker in markers):
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
        "profile": profile,
        "source": "fallback_internal",
        "file_count": len(files),
        "head_files": [str(path) for path in head_files],
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
    profile: str = "alphabetical",
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
        external = load_external_filtered_pages(
            external_path,
            volume_id=volume_id,
            text_root=text_root,
        )
        external_profile = external.get("profile")
        if external_profile == profile or (
            profile == "alphabetical" and external_profile is None
        ):
            external["profile"] = profile
            return external
        fallback = build_fallback_filtered_pages(
            volume_id,
            text_root,
            info.series,
            profile,
        )
        fallback["ignored_legacy_external_file"] = str(external_path)
        return fallback
    return build_fallback_filtered_pages(volume_id, text_root, info.series, profile)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build filtered-pages JSON for alphabetical index extraction.")
    ap.add_argument("--volume", required=True, help="Volume id, e.g. PG003 or PO025")
    ap.add_argument("--root", type=Path, default=Path("teste"), help="Root directory containing volume folders")
    ap.add_argument("--filtered-pages-json", type=Path, help="External filtered-pages JSON for one volume")
    ap.add_argument("--filtered-pages-dir", type=Path, help="Directory with <VOLUME>_filtered_pages.json files")
    ap.add_argument("--output", type=Path, help="Optional output path for the canonical JSON artifact")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    ap.add_argument("--profile", choices=("alphabetical", "general"), default="alphabetical")
    args = ap.parse_args()

    payload = resolve_filtered_pages(
        volume_id=args.volume,
        root=args.root,
        filtered_pages_json=args.filtered_pages_json,
        filtered_pages_dir=args.filtered_pages_dir,
        profile=args.profile,
    )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output is not None:
        args.output.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
