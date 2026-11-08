#!/usr/bin/env python3
"""Rebuild a reviewed PL section-map diagnostic from current OCR, offline."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.indexing.pl_section_map_experiment import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=ROOT/'docs/data/pl058_gennadius_section_map_v1.json')
    parser.add_argument('--survey', type=Path, default=ROOT/'data/index_entry_parser/global_targets_001/volumes/PL058')
    parser.add_argument('--root', type=Path, default=ROOT/'teste')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.profile, args.survey, args.root, args.out), indent=2))


if __name__ == '__main__':
    main()
