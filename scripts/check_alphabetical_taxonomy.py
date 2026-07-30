#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.common import parse_volume_info, page_sort_key

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEVANTAMENTO_DOC = PROJECT_ROOT / "docs" / "levantamento_indices_alfabeticos.md"
TAXONOMY_FIXTURES = PROJECT_ROOT / "docs" / "alphabetical_taxonomy_samples.json"

IGNORED_MARKER_SNIPPETS = (
    "sumário final",
)


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


def extract_volume_markers(doc_path: Path) -> dict[str, list[str]]:
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    current_volume: str | None = None
    markers: dict[str, list[str]] = defaultdict(list)
    section_re = re.compile(r"^###\s+5\.\d+\s+`(?P<volume>[A-Z0-9]+)`")
    marker_re_template = r"^-\s+\[{volume},\s*(?P<title>[^\]]+)\]\("

    for line in lines:
        section_match = section_re.match(line)
        if section_match:
            current_volume = section_match.group("volume")
            continue
        if line.startswith("### 6.") or line.startswith("## 6."):
            current_volume = None
            continue
        if current_volume is None or not line.startswith("- ["):
            continue
        marker_re = re.compile(marker_re_template.format(volume=re.escape(current_volume)))
        marker_match = marker_re.match(line)
        if not marker_match:
            continue
        title = marker_match.group("title").strip()
        if " / " in title:
            title = title.split(" / ")[-1].strip()
        title_norm = title.lower()
        if any(snippet in title_norm for snippet in IGNORED_MARKER_SNIPPETS):
            continue
        if title not in markers[current_volume]:
            markers[current_volume].append(title)
    return dict(markers)


def get_volume_files(volume_root: Path) -> list[Path]:
    text_root = volume_root / "text"
    if not text_root.exists():
        return []
    return sorted(text_root.glob("*.txt"), key=page_sort_key)


def find_marker_hits(files: list[Path], marker: str) -> list[dict[str, Any]]:
    marker_norm = normalize_for_match(marker)
    hits: list[dict[str, Any]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text_norm = normalize_for_match(text)
        if marker_norm not in text_norm:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        line_no = None
        line_text = None
        for idx, line in enumerate(lines, start=1):
            if marker_norm in normalize_for_match(line):
                line_no = idx
                line_text = line[:240]
                break
        hits.append(
            {
                "file": str(path),
                "line": line_no,
                "text": line_text,
            }
        )
    return hits


def get_tail_snippets(files: list[Path], tail_count: int, *, max_lines: int) -> list[dict[str, Any]]:
    tail_files = files[-tail_count:] if tail_count > 0 else []
    snippets: list[dict[str, Any]] = []
    for path in tail_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        non_empty = [line.strip() for line in text.splitlines() if line.strip()]
        snippets.append(
            {
                "file": str(path),
                "head_lines": non_empty[:max_lines],
            }
        )
    return snippets


def evaluate_volume(volume_id: str, root: Path, expected_markers: list[str], *, tail_count: int, max_tail_lines: int) -> dict[str, Any]:
    volume_root = root / volume_id
    info = parse_volume_info(volume_root)
    if info is None:
        return {
            "volume_id": volume_id,
            "status": "error",
            "error": f"Could not parse volume info from {volume_root}",
        }
    files = get_volume_files(volume_root)
    if not files:
        return {
            "volume_id": volume_id,
            "collection": info.series,
            "status": "error",
            "error": f"Text directory not found or empty: {volume_root / 'text'}",
        }

    expectations: list[dict[str, Any]] = []
    missing_markers: list[str] = []
    for marker in expected_markers:
        hits = find_marker_hits(files, marker)
        matched = bool(hits)
        if not matched:
            missing_markers.append(marker)
        expectations.append(
            {
                "marker": marker,
                "matched": matched,
                "hit_count": len(hits),
                "hits": hits[:5],
            }
        )

    status = "ok"
    if not expected_markers:
        status = "manual_review"
    elif missing_markers:
        status = "partial"

    return {
        "volume_id": volume_id,
        "collection": info.series,
        "status": status,
        "expected_markers": expected_markers,
        "missing_markers": missing_markers,
        "expectations": expectations,
        "tail_snippets": get_tail_snippets(files, tail_count, max_lines=max_tail_lines),
    }


def load_taxonomy_fixtures(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("taxonomy fixture must be a JSON array")
    required = {
        "volume_id",
        "sample_file",
        "heading_patterns",
        "section_kind",
        "pipeline_owner",
        "material_reference_mode",
        "scripture_mode",
        "expected_role",
    }
    fixtures: list[dict[str, Any]] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict) or not required.issubset(raw):
            missing = sorted(required - set(raw) if isinstance(raw, dict) else required)
            raise ValueError(f"invalid taxonomy fixture[{index}], missing={missing}")
        if not isinstance(raw["heading_patterns"], list) or not raw["heading_patterns"]:
            raise ValueError(f"taxonomy fixture[{index}].heading_patterns must be non-empty")
        fixtures.append(raw)
    return fixtures


def evaluate_taxonomy_fixtures(
    fixtures: list[dict[str, Any]],
    root: Path,
    selected_volumes: set[str] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fixture in fixtures:
        volume_id = str(fixture["volume_id"]).upper()
        if selected_volumes is None or volume_id in selected_volumes:
            grouped[volume_id].append(fixture)

    report: list[dict[str, Any]] = []
    for volume_id in sorted(grouped):
        expectations: list[dict[str, Any]] = []
        for fixture in grouped[volume_id]:
            path = root / volume_id / "text" / str(fixture["sample_file"])
            text = (
                path.read_text(encoding="utf-8", errors="replace")
                if path.is_file()
                else ""
            )
            text_norm = normalize_for_match(text)
            patterns = [str(item) for item in fixture["heading_patterns"]]
            matched_patterns = [
                pattern
                for pattern in patterns
                if normalize_for_match(pattern) in text_norm
            ]
            expectations.append(
                {
                    "marker": " | ".join(patterns),
                    "matched": bool(matched_patterns),
                    "hit_count": len(matched_patterns),
                    "hits": [{"file": str(path), "text": pattern} for pattern in matched_patterns],
                    "sample_file": str(path),
                    "section_kind": fixture["section_kind"],
                    "pipeline_owner": fixture["pipeline_owner"],
                    "material_reference_mode": fixture["material_reference_mode"],
                    "scripture_mode": fixture["scripture_mode"],
                    "expected_role": fixture["expected_role"],
                }
            )
        missing = [item["marker"] for item in expectations if not item["matched"]]
        report.append(
            {
                "volume_id": volume_id,
                "collection": volume_id[:2],
                "status": "ok" if not missing else "partial",
                "expected_markers": [item["marker"] for item in expectations],
                "missing_markers": missing,
                "expectations": expectations,
                "fixture_mode": True,
            }
        )
    return report


def print_report(report: list[dict[str, Any]]) -> None:
    total_ok = 0
    total_partial = 0
    total_manual = 0
    total_error = 0
    for item in report:
        status = item.get("status")
        if status == "ok":
            total_ok += 1
        elif status == "partial":
            total_partial += 1
        elif status == "manual_review":
            total_manual += 1
        else:
            total_error += 1
        print(f"{item.get('volume_id')} [{item.get('collection')}] {status}")
        if item.get("missing_markers"):
            print(f"  missing: {item['missing_markers']}")
        if item.get("expected_markers"):
            for exp in item["expectations"]:
                flag = "ok" if exp["matched"] else "missing"
                print(f"  - {flag}: {exp['marker']} ({exp['hit_count']} hits)")
        else:
            print("  no explicit markers extracted from levantamento doc")
        if status != "ok":
            for snippet in item.get("tail_snippets", []):
                print(f"  tail: {snippet['file']}")
                for line in snippet.get("head_lines", [])[:4]:
                    print(f"    {line}")
        print()
    print(f"TOTAL ok={total_ok} partial={total_partial} manual_review={total_manual} error={total_error}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compara a taxonomia esperada dos volumes com os marcadores OCR reais."
    )
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT / "teste", help="Root dos volumes OCR")
    ap.add_argument(
        "--docs",
        type=Path,
        default=LEVANTAMENTO_DOC,
        help="Documento de levantamento usado para extrair os marcadores esperados",
    )
    ap.add_argument(
        "--fixtures",
        type=Path,
        default=TAXONOMY_FIXTURES,
        help="Fixture JSON with exact OCR samples and expected taxonomy",
    )
    ap.add_argument(
        "--legacy-doc-markers",
        action="store_true",
        help="Use broad markers extracted from the levantamento document instead of exact fixtures",
    )
    ap.add_argument(
        "--volumes",
        help="Lista de volumes separados por vírgula. Se omitido, usa os volumes com marcadores explícitos no documento.",
    )
    ap.add_argument("--tail-count", type=int, default=4, help="Número de arquivos finais a incluir para revisão manual")
    ap.add_argument("--max-tail-lines", type=int, default=6, help="Máximo de linhas exibidas por arquivo final")
    ap.add_argument("--json", action="store_true", help="Emite o relatório em JSON")
    args = ap.parse_args()

    selected = (
        {item.strip().upper() for item in args.volumes.split(",") if item.strip()}
        if args.volumes
        else None
    )
    if args.legacy_doc_markers:
        markers_by_volume = extract_volume_markers(args.docs)
        volume_ids = sorted(selected or set(markers_by_volume))
        report = [
            evaluate_volume(
                volume_id,
                args.root,
                markers_by_volume.get(volume_id, []),
                tail_count=max(1, args.tail_count),
                max_tail_lines=max(1, args.max_tail_lines),
            )
            for volume_id in volume_ids
        ]
    else:
        report = evaluate_taxonomy_fixtures(
            load_taxonomy_fixtures(args.fixtures),
            args.root,
            selected,
        )

    if not report:
        raise SystemExit("No volumes selected for taxonomy check.")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print_report(report)


if __name__ == "__main__":
    main()
