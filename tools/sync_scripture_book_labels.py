#!/usr/bin/env python3
"""Synchronize localized biblical-book labels in the published v3 manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.scripture_keywords.export_summary_scripture_book_shards_v3 import (  # noqa: E402
    localized_book_labels,
)
from tools.scripture.book_catalog import canonical_book_label  # noqa: E402


def sync_manifest(path: Path) -> bool:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    routes = manifest.get("routes")
    if not isinstance(routes, dict):
        raise ValueError(f"invalid Scripture routes in {path}")

    changed = False
    for book_key, route in routes.items():
        if not isinstance(route, dict):
            raise ValueError(f"invalid Scripture route for {book_key}")
        portuguese = canonical_book_label(book_key)
        if not portuguese:
            raise ValueError(f"unknown Scripture book key: {book_key}")
        labels = localized_book_labels(book_key)
        if route.get("label") != portuguese:
            route["label"] = portuguese
            changed = True
        if route.get("labels") != labels:
            route["labels"] = labels
            changed = True

    if changed:
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.public / "scripture" / "v3" / "manifest.json"
    changed = sync_manifest(manifest_path)
    action = "updated" if changed else "already synchronized"
    print(f"Scripture book labels {action}: {manifest_path}")


if __name__ == "__main__":
    main()
