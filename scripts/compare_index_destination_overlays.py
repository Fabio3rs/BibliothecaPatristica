#!/usr/bin/env python3
"""Compare two candidate overlays without changing either database or the catalog."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def snapshot(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        records = {}
        for raw, in con.execute('SELECT evidence_json FROM relations'):
            evidence = json.loads(raw)
            ident = evidence.get('item_id', evidence.get('group_id'))
            records[ident] = {'evidence': evidence, 'files': [], 'lexical': False}
        for ident, file, raw in con.execute('SELECT item_id,file,candidate_json FROM candidates WHERE navigable=1 ORDER BY rank,file'):
            row = records.setdefault(ident, {'evidence': {}, 'files': [], 'lexical': False})
            candidate = json.loads(raw)
            row['files'].append(file)
            row['lexical'] |= any(r in {'incipit_normalized', 'incipit_fuzzy'} for r in candidate.get('reasons', []))
        return records


def compare(before, after):
    counts, changes, groups = Counter(), [], Counter()
    for ident in sorted(before.keys() | after.keys()):
        old, new = before.get(ident, {}), after.get(ident, {})
        old_files, new_files = old.get('files', []), new.get('files', [])
        category = 'gained' if new_files and not old_files else 'lost' if old_files and not new_files else 'retained' if new_files else 'still_pending'
        counts[category] += 1
        counts['top_candidate_changed'] += bool(old_files and new_files and old_files[0] != new_files[0])
        counts['gained_lexical_support'] += not old.get('lexical', False) and new.get('lexical', False)
        counts['lost_lexical_support'] += old.get('lexical', False) and not new.get('lexical', False)
        evidence = new.get('evidence') or old.get('evidence') or {}
        for loc in evidence.get('parsed', {}).get('locators', []):
            groups[f"{loc['series']}:{loc.get('book')}:{category}"] += 1
        if old_files != new_files or old.get('lexical') != new.get('lexical'):
            changes.append({'item_id': ident, 'label': evidence.get('label'), 'ref_raw': evidence.get('ref_raw'),
                            'category': category, 'before': old_files, 'after': new_files,
                            'lexical_before': old.get('lexical', False), 'lexical_after': new.get('lexical', False)})
    return {'counts': dict(counts), 'by_locator': dict(sorted(groups.items())), 'changes': changes,
            'quality': 'Candidate availability and ranking changes; not a correctness judgment.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = compare(snapshot(args.before), snapshot(args.after))
    result.update(before=str(args.before.resolve()), after=str(args.after.resolve()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['counts'], ensure_ascii=False, indent=2))
