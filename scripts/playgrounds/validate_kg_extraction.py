#!/usr/bin/env python3
"""Validate the starter contract using the playground's full, source-aware validator."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.knowledge_graph.playground.validation import evaluate


def validate(source: dict, result: dict, schema: dict | None = None) -> list[str]:
    checked = evaluate(source, result, schema)
    return checked['structural_errors'] + checked['source_errors']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--schema', type=Path)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding='utf-8'))
    raw = args.result.read_text(encoding='utf-8')
    schema = json.loads(args.schema.read_text(encoding='utf-8')) if args.schema else None
    checked = evaluate(source, raw, schema)
    for problem in checked['structural_errors'] + checked['source_errors']:
        print('ERROR:', problem)
    if not checked['valid']:
        raise SystemExit(1)
    print('OK: schema, local IDs, references and located evidence. Semantic review pending; not gold.')


if __name__ == '__main__':
    main()
