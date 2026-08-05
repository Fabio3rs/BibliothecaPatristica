#!/usr/bin/env python3
"""Usage: assemble one final alphabetical payload from per-volume intermediate JSON fragments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TOP_LEVEL_LIST_KEYS = [
    "sections",
    "nodes",
    "entries",
    "refs",
    "scripture_refs",
    "notes",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_optional_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return read_json(path)


def build_payload(intermediate_dir: Path, *, generated_at: str | None = None) -> dict[str, Any]:
    manifest = read_optional_json(intermediate_dir / "manifest.json", {})
    volume = read_json(intermediate_dir / "volume.json")
    coverage = read_optional_json(intermediate_dir / "coverage.json", {})
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at or manifest.get("updated_at") or manifest.get("generated_at"),
        "volume": volume,
        "coverage": coverage,
    }
    for key in TOP_LEVEL_LIST_KEYS:
        payload[key] = read_optional_json(intermediate_dir / f"{key}.json", [])
    if payload["generated_at"] is None:
        raise SystemExit("generated_at is required via manifest.json or --generated-at")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Assemble a canonical alphabetical payload from per-volume intermediate fragments.")
    ap.add_argument("--intermediate-dir", type=Path, required=True, help="Directory with intermediate JSON fragments for one volume")
    ap.add_argument("--output", type=Path, required=True, help="Final payload JSON path")
    ap.add_argument("--generated-at", help="Optional generated_at override")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print the output JSON")
    args = ap.parse_args()

    payload = build_payload(args.intermediate_dir, generated_at=args.generated_at)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    args.output.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


if __name__ == "__main__":
    main()
