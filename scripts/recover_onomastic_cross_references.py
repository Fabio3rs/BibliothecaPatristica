#!/usr/bin/env python3
"""Recover section-scoped vide candidates into a separate experimental overlay."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.onomastic_cross_references import run, SECTION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extraction', required=True, type=Path)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--section', default=SECTION)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.extraction, args.catalog, args.out, args.section), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
