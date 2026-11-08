#!/usr/bin/env python3
"""Audit and revalidate an archived multimodal run offline; never calls an API."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.indexing.multimodal.audit import replay_run


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--review-file', type=Path, help='Explicit hash-bound visual review; original responses remain unchanged')
    p.add_argument('--db', type=Path, default=ROOT/'data/alphabetical_indices.db')
    a = p.parse_args()
    result = replay_run(a.run, a.out, a.db, a.review_file)
    print(json.dumps({k: v for k, v in result.items() if k != 'tasks'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
