#!/usr/bin/env python3
"""Recover reviewed index regions into an isolated experiment, offline."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.indexing.bounded_index_recovery import run


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--profile', type=Path, default=ROOT/'docs/data/pl058_boundary_entry_recovery_v1.json')
    cli.add_argument('--root', type=Path, default=ROOT/'teste')
    cli.add_argument('--survey', type=Path, default=ROOT/'data/index_entry_parser/global_targets_001/volumes/PL058')
    cli.add_argument('--out', type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(run(args.profile, args.root, args.survey, args.out), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
