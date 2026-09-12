#!/usr/bin/env python3
"""Build, but do not execute, a deterministic index-extraction workplan.

Use this review/debug entry point to inspect candidate sections, physical traversal direction,
editorial-header evidence, chunk boundaries, and approximate entry counts before spending an agent
run. The resulting JSON is the shared state used by fresh-context chunk execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.index_workplan import build_index_workplan


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build a deterministic section manifest and physical-file chunk plan for index extraction."
    )
    ap.add_argument("--volume", required=True)
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--collection", required=True, choices=("PG", "PL", "PO"))
    ap.add_argument("--pipeline-kind", required=True, choices=("general", "alphabetical"))
    ap.add_argument("--filtered-pages-json", type=Path, required=True)
    ap.add_argument("--chunk-output-dir", type=Path, required=True)
    ap.add_argument("--max-files-per-chunk", type=int, default=6)
    ap.add_argument("--chunk-overlap", type=int, default=1)
    ap.add_argument("--context-window", type=int, default=1)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    filtered_pages = json.loads(args.filtered_pages_json.read_text(encoding="utf-8"))
    args.chunk_output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_index_workplan(
        volume_id=args.volume,
        source_root=args.source_root,
        collection=args.collection,
        filtered_pages=filtered_pages,
        pipeline_kind=args.pipeline_kind,
        chunk_output_dir=args.chunk_output_dir,
        max_files_per_chunk=args.max_files_per_chunk,
        chunk_overlap=args.chunk_overlap,
        context_window=args.context_window,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
