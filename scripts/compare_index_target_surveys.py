#!/usr/bin/env python3
"""Compare candidate recovery by unchanged source groups, without certification."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path


def read_groups(directory):
    groups = {}
    path = directory / 'groups.jsonl'
    if not path.exists():
        return groups
    for line in path.open():
        row = json.loads(line)
        # Source identity and literal offsets survive reordering of parser refs.
        identity = (row['source'], row['source_sha256'], row['char_start'], row['char_end'], row['raw'])
        groups[row['id']] = {'identity': identity, 'row': row, 'files': set(), 'occurrences': 0}
    for line in (directory / 'occurrences.jsonl').open():
        row = json.loads(line)
        group = groups[row['group_id']]
        group['occurrences'] += 1
        group['files'].update(f.get('file', f.get('path')) for f in row.get('files', []))
    return {r['identity']: r for r in groups.values()}


def compare(before, after, out, volumes=()):
    out.mkdir(parents=True, exist_ok=False)
    selected_volumes=set(volumes)
    volumes = sorted({p.name for base in (before, after) for p in (base/'volumes').iterdir()
                      if p.is_dir() and (not selected_volumes or p.name in selected_volumes)})
    totals, rows = Counter(), []
    removed_selections,added_selections=Counter(),Counter()
    with (out/'changes.jsonl').open('w') as changes:
        for volume in volumes:
            old, new = (read_groups(base/'volumes'/volume) for base in (before, after))
            counts = Counter(before_groups=len(old), after_groups=len(new),
                             before_with_files=sum(bool(r['files']) for r in old.values()),
                             after_with_files=sum(bool(r['files']) for r in new.values()),
                             same_source_groups=len(old.keys() & new.keys()),
                             added_source_groups=len(new.keys() - old.keys()),
                             removed_source_groups=len(old.keys() - new.keys()))
            for key in sorted(old.keys() - new.keys()):
                removed_selections[old[key]['row'].get('selection','unknown')]+=1
                changes.write(json.dumps({'volume':volume,'change':'removed_source_group',
                    'before':old[key]['row'],'before_files':sorted(old[key]['files'])},ensure_ascii=False)+'\n')
            for key in sorted(new.keys() - old.keys()):
                added_selections[new[key]['row'].get('selection','unknown')]+=1
                changes.write(json.dumps({'volume':volume,'change':'added_source_group',
                    'after':new[key]['row'],'after_files':sorted(new[key]['files'])},ensure_ascii=False)+'\n')
            for key in sorted(old.keys() & new.keys()):
                a, b = old[key], new[key]
                gained, lost = b['files'] - a['files'], a['files'] - b['files']
                counts['gained_first_candidate'] += not a['files'] and bool(b['files'])
                counts['lost_all_candidates'] += bool(a['files']) and not b['files']
                counts['groups_with_removed_candidate_files'] += bool(lost)
                counts['added_group_file_pairs'] += len(gained)
                counts['removed_group_file_pairs'] += len(lost)
                changed_owner=a['row'].get('owner_raw')!=b['row'].get('owner_raw')
                counts['groups_with_changed_owner']+=changed_owner
                if gained or lost or changed_owner:
                    changes.write(json.dumps({'volume': volume, 'group_id': b['row']['id'],
                        'source': key[0], 'source_sha256': key[1], 'source_span': list(key[2:4]),
                        'ref_raw': key[4], 'before_status': a['row']['status'],
                        'after_status': b['row']['status'], 'added_files': sorted(gained),
                        'before_owner':a['row'].get('owner_raw'),'after_owner':b['row'].get('owner_raw'),
                        'removed_files': sorted(lost)}, ensure_ascii=False)+'\n')
            totals.update(counts)
            rows.append({'volume': volume, **counts})
    fields = ['volume', *sorted(totals)]
    with (out/'by_volume.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    summary = {'before': str(before.resolve()), 'after': str(after.resolve()), 'counts': dict(totals),
               'removed_by_selection':dict(removed_selections),'added_by_selection':dict(added_selections),
               'unit': 'source-hash/span/literal reference group; not certified entry or link',
               'warning': 'Alternative parses can increase occurrence rows without adding printed references.',
               'top_gains': sorted(rows, key=lambda r: -r.get('gained_first_candidate', 0))[:20]}
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--volume',action='append',default=[])
    args = parser.parse_args()
    print(json.dumps(compare(args.before, args.after, args.out,args.volume), ensure_ascii=False, indent=2))
