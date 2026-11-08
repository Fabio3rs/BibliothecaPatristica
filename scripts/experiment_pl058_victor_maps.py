#!/usr/bin/env python3
"""Replay bounded Victor profiles and preserve duplicate-number alternatives, offline."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.indexing.pl_section_map_experiment import run

NAMES = ('passio', 'admonitio', 'notitia', 'notes')
PROFILE_VERSIONS = {'passio': 1, 'admonitio': 2, 'notitia': 2, 'notes': 1}


def profile_path(name):
    return REPO / f'docs/data/pl058_victor_{name}_v{PROFILE_VERSIONS[name]}.json'


def consolidate(out):
    merged = {}
    summaries = {}
    for name in NAMES:
        directory = out / name
        summaries[name] = json.loads((directory / 'summary.json').read_text())
        for link in json.loads((directory / 'links.json').read_text()):
            occurrence = link['occurrence']
            row = merged.setdefault(occurrence['id'], {
                'occurrence': occurrence, 'owner_raw': link['owner_raw'],
                'branches': [], 'verified': False, 'import_ready': False,
                'source_evidence_valid': True,
            })
            assert row['occurrence'] == occurrence, 'Incompatible source snapshots'
            row['source_evidence_valid'] &= link['status'] != 'stale_index_extraction'
            if link['map']:
                row['branches'].append({'segment': name, 'mapping': link['map']})
    for row in merged.values():
        number = row['occurrence']['number']
        files = {f['file']: f for b in row['branches'] for f in b['mapping']['files']}
        row['candidate_files'] = sorted(files.values(), key=lambda f: f['physical'])
        if not row['source_evidence_valid']:
            row['candidate_files'] = []
            row['status'] = 'stale_index_extraction'
        elif 55 <= number <= 64:
            row['status'] = 'duplicated_number_alternatives_pending'
        elif files:
            row['status'] = 'bounded_window_candidate'
        elif 91 <= number <= 98:
            row['status'] = 'numbering_gap_near_editorially_omitted_scripture_index'
        else:
            row['status'] = 'outside_bounded_pilot_or_missing_boundary'
    rows = list(merged.values())
    summary = {
        'source_occurrences': len(rows),
        'source_groups': len({x['occurrence']['group_id'] for x in rows}),
        'occurrences_with_any_candidate_window': sum(bool(x['candidate_files']) for x in rows),
        'duplicated_number_occurrences': sum(55 <= x['occurrence']['number'] <= 64 for x in rows),
        'duplicated_number_occurrences_with_multiple_bounded_branches': sum(
            55 <= x['occurrence']['number'] <= 64 and
            sum(bool(b['mapping']['files']) for b in x['branches']) > 1 for x in rows),
        'statuses': dict(Counter(x['status'] for x in rows)),
        'segments': summaries,
        'import_ready': False, 'db_updates': 0,
        'warning': 'A single populated branch does not prove that another duplicate location is absent.',
    }
    assert all(not x['verified'] and not x['import_ready'] for x in rows)
    assert all(x['status'] == 'duplicated_number_alternatives_pending'
               for x in rows if x['source_evidence_valid'] and 55 <= x['occurrence']['number'] <= 64)
    (out/'combined_links.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    return summary


def compare_campaigns(before, after):
    """Compare only identical source occurrences; never add gains across snapshots."""
    def load(directory):
        rows = json.loads((directory/'combined_links.json').read_text())
        result = {r['occurrence']['id']: r for r in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate occurrence IDs in campaign')
        return result

    old, new = load(before), load(after)
    changed_sources, changes = [], []
    comparable = gained = lost = 0
    for key in sorted(old.keys() & new.keys()):
        a, b = old[key], new[key]
        if a['occurrence'] != b['occurrence'] or a['owner_raw'] != b['owner_raw']:
            changed_sources.append(key)
            continue
        comparable += 1
        af = [f['file'] for f in a['candidate_files']]
        bf = [f['file'] for f in b['candidate_files']]
        gained += not af and bool(bf)
        lost += bool(af) and not bf
        if af != bf or a['status'] != b['status']:
            changes.append({'occurrence_id': key, 'number': b['occurrence']['number'],
                            'before_files': af, 'after_files': bf,
                            'before_status': a['status'], 'after_status': b['status']})
    result = {'before': str(before.resolve()), 'after': str(after.resolve()),
              'comparable_occurrences': comparable, 'changed_sources': changed_sources,
              'added_occurrences': sorted(new.keys() - old.keys()),
              'removed_occurrences': sorted(old.keys() - new.keys()),
              'gained_candidate_window': gained, 'lost_candidate_window': lost,
              'changes': changes, 'verified': False, 'db_updates': 0}
    (after/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=REPO/'teste')
    parser.add_argument('--survey', type=Path, default=REPO/'data/index_entry_parser/global_targets_001/volumes/PL058')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--compare-to', type=Path, help='Previous campaign; compare matching source occurrences')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Use a new output directory')
    args.out.mkdir(parents=True)
    (args.out/'campaign.json').write_text(json.dumps({
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'profiles': [str(profile_path(n)) for n in NAMES],
        'status': 'running'}, indent=2)+'\n')
    for name in NAMES:
        run(profile_path(name), args.survey, args.root, args.out/name)
    summary = consolidate(args.out)
    if args.compare_to:
        compare_campaigns(args.compare_to, args.out)
    campaign = json.loads((args.out/'campaign.json').read_text())
    campaign['status'] = 'completed'
    (args.out/'campaign.json').write_text(json.dumps(campaign, indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k != 'segments'}, indent=2))


if __name__ == '__main__':
    main()
