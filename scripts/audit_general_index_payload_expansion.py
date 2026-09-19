#!/usr/bin/env python3
"""Audit large general-index payloads against their validated assemblies.

This is intentionally read-only.  It highlights final-agent output that grew
beyond the deterministic chunk assembly, lost stable keys, or contains section
headings owned by another index pipeline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.indexing.index_pipeline_ownership import general_section_ownership


DEFAULT_PAYLOAD_DIR = ROOT / "data" / "index_payloads"
DEFAULT_ASSEMBLY_ROOT = ROOT / "data" / "index_intermediate_payloads"
DEFAULT_PUBLIC_INDEX_DIR = ROOT / "web" / "public" / "indices"
FILE_SUFFIX_RE = re.compile(r"-(\d+)\.txt$")
SEVERE_FLAGS = {
    "cross_pipeline_sections",
    "duplicate_assembly_span",
    "inflated_vs_assembly",
}


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def entry_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for section in payload.get("sections", [])
        if isinstance(section, dict)
        for entry in section.get("entries", [])
        if isinstance(entry, dict)
    ]


def stable_data(assembly: dict[str, Any]) -> dict[str, Any]:
    data = assembly.get("data")
    if not isinstance(data, dict):
        raise ValueError("Assembly has no object-valued data field")
    return data


def file_suffix(value: Any) -> int | None:
    match = FILE_SUFFIX_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def section_span(section: dict[str, Any]) -> tuple[int, int] | None:
    start = file_suffix(section.get("file_start"))
    end = file_suffix(section.get("file_end"))
    if start is None or end is None:
        return None
    return min(start, end), max(start, end)


def spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return max(left[0], right[0]) <= min(left[1], right[1])


def audit_payload(
    path: Path,
    *,
    assembly_root: Path,
    public_index_dir: Path,
    large_entry_threshold: int,
    expansion_ratio_threshold: float,
    expansion_count_threshold: int,
) -> dict[str, Any]:
    payload = read_json(path)
    volume_id = str(payload.get("volume", {}).get("volume_id") or path.name[:-13])
    entries = entry_list(payload)
    sections = [item for item in payload.get("sections", []) if isinstance(item, dict)]
    works = [item for item in payload.get("works", []) if isinstance(item, dict)]

    entry_keys = [str(item.get("entry_key") or "") for item in entries]
    section_keys = [str(item.get("section_key") or "") for item in sections]
    work_keys = [str(item.get("work_key") or "") for item in works]
    missing_entry_keys = sum(not key for key in entry_keys)

    assembly_path = assembly_root / volume_id / "assembled_fragments.json"
    assembly_counts: dict[str, int] | None = None
    entry_expansion: int | None = None
    entry_expansion_ratio: float | None = None
    missing_stable_entry_keys: int | None = None
    unexpected_entry_keys: int | None = None
    unexpected_section_keys: list[str] | None = None
    unexpected_work_keys: list[str] | None = None
    duplicate_assembly_spans: list[str] | None = None

    non_owned_sections = []
    for section in sections:
        owned, reason = general_section_ownership(section)
        if not owned:
            non_owned_sections.append(
                {
                    "section_key": section.get("section_key"),
                    "index_kind": section.get("index_kind"),
                    "entry_count": len(section.get("entries", [])),
                    "reason": reason,
                }
            )

    if assembly_path.is_file():
        assembled = stable_data(read_json(assembly_path))
        stable_entries = entry_list(assembled)
        stable_entry_keys = {
            str(item.get("entry_key") or "") for item in stable_entries if item.get("entry_key")
        }
        stable_section_keys = {
            str(item.get("section_key") or "")
            for item in assembled.get("sections", [])
            if isinstance(item, dict) and item.get("section_key")
        }
        stable_work_keys = {
            str(item.get("work_key") or "")
            for item in assembled.get("works", [])
            if isinstance(item, dict) and item.get("work_key")
        }
        final_entry_keys = {key for key in entry_keys if key}
        final_section_keys = {key for key in section_keys if key}
        final_work_keys = {key for key in work_keys if key}

        stable_count = len(stable_entries)
        entry_expansion = len(entries) - stable_count
        entry_expansion_ratio = len(entries) / stable_count if stable_count else None
        missing_stable_entry_keys = len(stable_entry_keys - final_entry_keys)
        unexpected_entry_keys = len(final_entry_keys - stable_entry_keys)
        unexpected_section_keys = sorted(final_section_keys - stable_section_keys)
        unexpected_work_keys = sorted(final_work_keys - stable_work_keys)
        stable_spans = [
            (str(item.get("section_key") or ""), section_span(item))
            for item in assembled.get("sections", [])
            if isinstance(item, dict)
        ]
        duplicate_assembly_spans = []
        for section in sections:
            key = str(section.get("section_key") or "")
            if key in stable_section_keys:
                continue
            span = section_span(section)
            if span is None:
                continue
            overlapping_keys = [
                stable_key
                for stable_key, stable_span in stable_spans
                if stable_span is not None and spans_overlap(span, stable_span)
            ]
            if overlapping_keys:
                duplicate_assembly_spans.append(
                    f"{key} overlaps {', '.join(overlapping_keys)}"
                )
        assembly_counts = {
            "works": len(assembled.get("works", [])),
            "sections": len(assembled.get("sections", [])),
            "entries": stable_count,
        }

    public_gzip = public_index_dir / f"{volume_id}.json.gz"
    flags: list[str] = []
    if len(entries) >= large_entry_threshold:
        flags.append("large_payload")
    if missing_entry_keys:
        flags.append("missing_entry_keys")
    if len(entry_keys) != len(set(key for key in entry_keys if key)) + missing_entry_keys:
        flags.append("duplicate_entry_keys")
    if len(section_keys) != len(set(key for key in section_keys if key)):
        flags.append("missing_or_duplicate_section_keys")
    if len(work_keys) != len(set(key for key in work_keys if key)):
        flags.append("missing_or_duplicate_work_keys")
    if (
        entry_expansion is not None
        and entry_expansion >= expansion_count_threshold
        and entry_expansion_ratio is not None
        and entry_expansion_ratio >= expansion_ratio_threshold
    ):
        flags.append("inflated_vs_assembly")
    if missing_stable_entry_keys:
        flags.append("missing_stable_entry_keys")
    if unexpected_section_keys:
        flags.append("unassembled_sections")
    if unexpected_work_keys:
        flags.append("unassembled_works")
    if duplicate_assembly_spans:
        flags.append("duplicate_assembly_span")
    if non_owned_sections:
        flags.append("cross_pipeline_sections")

    return {
        "volume_id": volume_id,
        "payload": str(path),
        "raw_bytes": path.stat().st_size,
        "public_gzip_bytes": public_gzip.stat().st_size if public_gzip.is_file() else None,
        "counts": {
            "works": len(works),
            "sections": len(sections),
            "entries": len(entries),
            "target_files": sum(bool(item.get("target_file")) for item in entries),
            "missing_entry_keys": missing_entry_keys,
        },
        "assembly": assembly_counts,
        "entry_expansion": entry_expansion,
        "entry_expansion_ratio": (
            round(entry_expansion_ratio, 4) if entry_expansion_ratio is not None else None
        ),
        "missing_stable_entry_keys": missing_stable_entry_keys,
        "unexpected_entry_keys": unexpected_entry_keys,
        "unexpected_section_keys": unexpected_section_keys,
        "unexpected_work_keys": unexpected_work_keys,
        "duplicate_assembly_spans": duplicate_assembly_spans,
        "non_owned_sections": non_owned_sections,
        "cross_pipeline_entry_count": sum(
            int(item["entry_count"]) for item in non_owned_sections
        ),
        "flags": flags,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--assembly-root", type=Path, default=DEFAULT_ASSEMBLY_ROOT)
    parser.add_argument("--public-index-dir", type=Path, default=DEFAULT_PUBLIC_INDEX_DIR)
    parser.add_argument("--large-entry-threshold", type=int, default=2_000)
    parser.add_argument("--expansion-ratio-threshold", type=float, default=1.5)
    parser.add_argument("--expansion-count-threshold", type=int, default=100)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--only-flagged", action="store_true")
    parser.add_argument("--only-severe", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows = [
        audit_payload(
            path,
            assembly_root=args.assembly_root,
            public_index_dir=args.public_index_dir,
            large_entry_threshold=args.large_entry_threshold,
            expansion_ratio_threshold=args.expansion_ratio_threshold,
            expansion_count_threshold=args.expansion_count_threshold,
        )
        for path in sorted(args.payload_dir.glob("*_indices.json"))
    ]
    rows.sort(
        key=lambda item: (
            item["counts"]["entries"],
            item["raw_bytes"],
        ),
        reverse=True,
    )
    if args.only_severe:
        selected = [item for item in rows if SEVERE_FLAGS & set(item["flags"])]
    elif args.only_flagged:
        selected = [item for item in rows if item["flags"]]
    else:
        selected = rows
    report = {
        "payload_count": len(rows),
        "flagged_count": sum(bool(item["flags"]) for item in rows),
        "severe_count": sum(bool(SEVERE_FLAGS & set(item["flags"])) for item in rows),
        "thresholds": {
            "large_entry_count": args.large_entry_threshold,
            "expansion_ratio": args.expansion_ratio_threshold,
            "expansion_count": args.expansion_count_threshold,
        },
        "items": selected[: args.limit],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
