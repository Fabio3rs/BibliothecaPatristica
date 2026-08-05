#!/usr/bin/env python3
"""Usage: run index_target_locator.py over one volume request in chunks and merge the output.

Run from the repository root:
  python scripts/pipeline_index_extraction/run_index_target_locator_in_chunks.py \
    --input data/alphabetical_index_payloads/PG155_helper_request.json \
    --output data/alphabetical_index_payloads/PG155_helper_output.json \
    --chunk-size 80
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def chunked(seq: list[dict], size: int) -> list[list[dict]]:
    return [seq[idx : idx + size] for idx in range(0, len(seq), size)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Run index_target_locator.py in chunks and merge the results.")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--chunk-size", type=int, default=80)
    ap.add_argument(
        "--locator-script",
        type=Path,
        default=Path("/homessddata/Projects/pdfocr/scripts/index_target_locator.py"),
    )
    args = ap.parse_args()

    request = read_json(args.input)
    entries = request.get("entries", [])
    if not entries:
        write_json(
            args.output,
            {
                "volume_id": request.get("volume_id"),
                "source_root": request.get("source_root"),
                "options_used": request.get("options", {}),
                "entries": [],
            },
        )
        return

    merged_entries: list[dict] = []
    options_used = None

    with tempfile.TemporaryDirectory(prefix="idx-locator-chunks-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        for chunk_no, entry_chunk in enumerate(chunked(entries, args.chunk_size), start=1):
            chunk_request = {
                "volume_id": request.get("volume_id"),
                "source_root": request.get("source_root"),
                "options": request.get("options", {}),
                "entries": entry_chunk,
            }
            chunk_input = tmpdir_path / f"chunk_{chunk_no:03d}_input.json"
            chunk_output = tmpdir_path / f"chunk_{chunk_no:03d}_output.json"
            write_json(chunk_input, chunk_request)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(args.locator_script),
                    "--input",
                    str(chunk_input),
                    "--output",
                    str(chunk_output),
                    "--pretty",
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise SystemExit(
                    f"index_target_locator.py failed on chunk {chunk_no}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
                )
            chunk_payload = read_json(chunk_output)
            options_used = options_used or chunk_payload.get("options_used")
            merged_entries.extend(chunk_payload.get("entries", []))

    write_json(
        args.output,
        {
            "volume_id": request.get("volume_id"),
            "source_root": request.get("source_root"),
            "options_used": options_used or request.get("options", {}),
            "entries": merged_entries,
        },
    )


if __name__ == "__main__":
    main()
