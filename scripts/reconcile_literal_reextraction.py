#!/usr/bin/env python3
"""Anchor saved literal extractions offline, preserving the paid run and SQLite."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.multimodal.literal_reconcile import reconcile_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = reconcile_run(args.run, args.out)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'tasks'}, ensure_ascii=False, indent=2))
    return int(any(t['status'] != 'reconciled_for_review' for t in result['tasks']))


if __name__ == '__main__':
    raise SystemExit(main())
