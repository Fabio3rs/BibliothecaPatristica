#!/usr/bin/env python3
"""Offline comparison of independent OCR-only/image/crop runs."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.indexing.multimodal.benchmark import compare
from tools.indexing.multimodal.contract import write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, action='append', required=True)
    p.add_argument('--gold', type=Path, default=ROOT/'tests/fixtures/multimodal_verification_v1.json')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    report = compare(args.run, json.loads(args.gold.read_text()))
    write_json(args.out, report)
    print(json.dumps({'rows': len(report['rows']), 'report': str(args.out), 'calls': 0}))


if __name__ == '__main__':
    main()
