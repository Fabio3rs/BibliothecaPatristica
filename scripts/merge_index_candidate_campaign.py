#!/usr/bin/env python3
"""Reaggregate immutable volume results, replacing only explicitly rerun volumes."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def merge(kind, baseline, override, out):
    """Create a new campaign; volume symlinks retain original artifacts unchanged."""
    sources = {p.name: p.resolve() for p in (baseline/'volumes').iterdir() if p.is_dir()}
    replacements = {p.name: p.resolve() for p in (override/'volumes').iterdir() if p.is_dir()}
    if replacements.keys() - sources.keys():
        raise ValueError('Override contains volumes outside baseline')
    sources.update(replacements)
    summaries = []
    for volume, directory in sorted(sources.items()):
        row = json.loads((directory/'summary.json').read_text())
        if row['volume'] != volume or row['status'] != 'completed':
            raise ValueError(f'Incomplete or mismatched volume: {volume}')
        summaries.append(row)
    config = json.loads((baseline/'config.json').read_text())
    config.update(out=str(out.resolve()), baseline=str(baseline.resolve()),
                  override=str(override.resolve()), overridden_volumes=sorted(replacements),
                  override_config=json.loads((override/'config.json').read_text()),
                  volume_artifacts={v:str(p) for v,p in sources.items()},
                  consolidation_policy='explicit volume override; linked inputs retained unchanged')
    out.mkdir(parents=True, exist_ok=False)
    (out/'volumes').mkdir()
    for volume, directory in sources.items():
        (out/'volumes'/volume).symlink_to(directory, target_is_directory=True)
    (out/'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n')
    if kind == 'survey':
        from tools.indexing.global_target_survey import aggregate
        return aggregate(out, config, summaries)
    from scripts.audit_global_index_lexical import aggregate
    return aggregate(out, summaries, config)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind', choices=['survey','lexical'], required=True)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--override', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(merge(a.kind,a.baseline,a.override,a.out),ensure_ascii=False,indent=2))
