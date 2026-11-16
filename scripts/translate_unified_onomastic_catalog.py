#!/usr/bin/env python3
"""Inventory, translate, review and export unified onomastic catalog entries."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.index_translation.onomastic_catalog import (
    DEFAULT_ALPHABETICAL, DEFAULT_CATALOG, DEFAULT_GENERAL, DEFAULT_LANGUAGES,
    DEFAULT_STORE, export, inventory, review, run,
)
from scripts.index_translation.provider import normalize_language_list


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "run", "export"):
        command = sub.add_parser(name)
        command.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
        command.add_argument("--store", type=Path, default=DEFAULT_STORE)
        command.add_argument("--languages", default=DEFAULT_LANGUAGES)
        command.add_argument("--volume")
        command.add_argument("--item-id")
        if name == "inventory":
            command.add_argument("--out", type=Path, help="Optional JSONL report of pending occurrences")
        elif name == "run":
            command.add_argument("--max-api-calls", type=int, required=True)
            command.add_argument("--provider", default="openai-compatible")
            command.add_argument("--model")
            command.add_argument("--base-url", default="https://api.openai.com/v1")
            command.add_argument("--api-key-env", default="OPENAI_API_KEY")
            command.add_argument("--timeout", type=int, default=180)
            command.add_argument("--alphabetical-db", type=Path, default=DEFAULT_ALPHABETICAL)
            command.add_argument("--general-db", type=Path, default=DEFAULT_GENERAL)
        elif name == "export":
            command.add_argument("--out", type=Path, required=True)
            command.add_argument("--limit", type=int, default=20)
            command.add_argument("--translated-only", action="store_true")
    command = sub.add_parser("review")
    command.add_argument("--store", type=Path, default=DEFAULT_STORE)
    command.add_argument("--item-id", required=True)
    command.add_argument("--language", required=True)
    command.add_argument("--state", choices=("approved", "rejected"), required=True)
    command.add_argument("--reviewer", required=True)
    command.add_argument("--note")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "review":
            result = review(args.store, item_id=args.item_id, language=args.language,
                            state=args.state, reviewer=args.reviewer, note=args.note)
        else:
            languages = normalize_language_list(args.languages)
            if not languages:
                raise ValueError("at least one language is required")
            if args.command == "inventory":
                result = inventory(args.catalog, args.store, languages=languages,
                                   volume=args.volume, item_id=args.item_id, out=args.out)
            elif args.command == "run":
                result = run(args.catalog, args.store, languages=languages,
                             volume=args.volume, item_id=args.item_id,
                             max_api_calls=args.max_api_calls, model=args.model,
                             provider=args.provider, base_url=args.base_url,
                             api_key_env=args.api_key_env, timeout=args.timeout,
                             alphabetical_path=args.alphabetical_db,
                             general_path=args.general_db)
            else:
                result = export(args.catalog, args.store, languages=languages,
                                out=args.out, volume=args.volume, item_id=args.item_id,
                                limit=args.limit, translated_only=args.translated_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
