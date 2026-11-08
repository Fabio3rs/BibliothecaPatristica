#!/usr/bin/env python3
"""Rebuild the available raw-input recipes twice and compare their logical output.

This proves repeatability of these recipes, not regeneration of the entire
consolidated catalog. Existing catalogs and OCR histories are never opened.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_index_regeneration import database_digest
from tools.indexing.reproducible_selection import compare

PLANS = ['internal_index_sections', 'internal_ordinal_sections',
         'named_division_sections', 'pl148_chapter_sections']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_inventory():
    paths = sorted(Path('tools/indexing').glob('*.py')) + [Path('tools/corpus_utils.py')]
    paths += [Path('scripts') / name for name in (
        'verify_index_raw_rebuild.py', 'rebuild_index_parser_selection.py',
        'audit_index_regeneration.py', 'recover_internal_index_sections.py')]
    return {str(p): digest(p) for p in paths}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--root', type=Path, default=Path('teste'))
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--volume', action='append', default=[], help='Restrict only the generic parser selection')
    args = p.parse_args()
    if args.workers < 2:
        p.error('--workers must be >= 2 to compare serial and parallel execution')
    args.out.mkdir(parents=True, exist_ok=False)
    before = code_inventory()
    result = dict(scope='parser_selection_and_four_explicit_section_plans',
                  entire_catalog_verified=False, status='running', runs=[], comparisons=[],
                  python=platform.python_version(), corpus_root=str(args.root.resolve()),
                  dependencies=sorted({(d.metadata['Name'], d.version) for d in importlib.metadata.distributions()}),
                  code_sha256=before)
    report = args.out / 'verification.json'

    def save():
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')

    def run(name, script, extra, workers, seed):
        out = args.out / f'{name}_w{workers}'
        command = [sys.executable, script, *extra, '--root', str(args.root),
                   '--workers', str(workers), '--out', str(out)]
        with (args.out / f'{name}_w{workers}.log').open('w') as log:
            completed = subprocess.run(command, env={**os.environ, 'PYTHONHASHSEED': seed},
                                       stdout=log, stderr=subprocess.STDOUT)
        result['runs'].append(dict(command=command, pythonhashseed=seed, returncode=completed.returncode))
        save()
        completed.check_returncode()
        return out

    save()
    try:
        extra = ['replay', '--manifest', 'docs/fixtures/index_rebuild/parser_selection_20261106.json',
                 '--source-aliases', 'docs/fixtures/index_rebuild/source_aliases.json', '--digest-only']
        for volume in args.volume:
            extra += ['--volume', volume]
        outputs = [run('parser', 'scripts/rebuild_index_parser_selection.py', extra, w, seed)
                   for w, seed in ((1, '1'), (args.workers, '37'))]
        comparison = compare(*(out / 'verification.json' for out in outputs))
        result['comparisons'].append(dict(recipe='parser', **comparison))
        if comparison['status'] != 'identical':
            raise ValueError('Parser replay differs or is incomplete')
        print('Parser replay identical', flush=True)
        for name in PLANS:
            plan = Path('docs/fixtures') / f'{name}.json'
            outputs = [run(name, 'scripts/recover_internal_index_sections.py',
                           ['--plan', str(plan)], w, seed) for w, seed in ((1, '1'), (args.workers, '37'))]
            digests = [database_digest(out / 'extraction/search.sqlite') for out in outputs]
            exports = [{f: digest(out / f) for f in ('entries.jsonl', 'entries.csv')} for out in outputs]
            # Match source inventories too: equal output alone could hide input drift.
            inputs = [{f: digest(out / f) for f in ('regions.jsonl', 'body_manifest.jsonl')} for out in outputs]
            equal = digests[0] == digests[1] and exports[0] == exports[1] and inputs[0] == inputs[1]
            result['comparisons'].append(dict(recipe=name, status='identical' if equal else 'different',
                                              plan_sha256=digest(plan), logical_digests=digests,
                                              export_sha256=exports, input_sha256=inputs))
            save()
            if not equal:
                raise ValueError('Section replay differs: '+name)
            print(name+' identical', flush=True)
        if before != code_inventory():
            raise ValueError('Code changed during verification')
        result['status'] = 'verified_scoped_recipes'
    except Exception as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        save()
    print(json.dumps(dict(status=result['status'], entire_catalog_verified=False, report=str(report))))


if __name__ == '__main__':
    main()
