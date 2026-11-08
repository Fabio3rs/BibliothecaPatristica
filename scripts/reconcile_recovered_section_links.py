#!/usr/bin/env python3
"""Reconcile recovered index rows and test their section maps without production writes."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.indexing.recovered_section_links import run


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--recovery', type=Path, default=ROOT/'data/index_entry_parser/pl058_boundary_entries_002')
    cli.add_argument('--baseline-config', type=Path, default=ROOT/'data/index_entry_parser/global_targets_001/config.json')
    cli.add_argument('--routes', type=Path, default=ROOT/'docs/data/pl058_recovery_routes_v1.json')
    cli.add_argument('--root', type=Path, default=ROOT)
    cli.add_argument('--out', type=Path, required=True)
    a = cli.parse_args()
    print(json.dumps(run(a.recovery, a.baseline_config, a.routes, a.out, a.root), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
