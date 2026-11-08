"""Compare experimental regions by their source-anchored opening, without quality claims."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random


def load_sections(path):
    result = {}
    with path.open() as stream:
        for line in stream:
            section = json.loads(line)
            spans = section['spans']
            if not spans:
                continue
            signature = hashlib.sha256()
            for span in spans:
                signature.update(json.dumps(span, sort_keys=True).encode())
            first, last = spans[0], spans[-1]
            result[section['section_id']] = {
                'section_id': section['section_id'], 'volume': section['volume'], 'kind': section['kind'],
                'first': first, 'last': last, 'files': len({s['source'] for s in spans}),
                'extent': last['physical'] - first['physical'] + 1,
                'characters': sum(s['span'][1]-s['span'][0] for s in spans),
                'spans_sha256': signature.hexdigest(), 'verified': False}
    return result


def stats(items):
    values = list(items)
    long = [s for s in values if s['extent'] > 100]
    return {'regions': len(values), 'extent_sum': sum(s['extent'] for s in values),
            'characters_sum': sum(s['characters'] for s in values), 'long_regions': len(long),
            'long_extent_sum': sum(s['extent'] for s in long),
            'kinds': dict(Counter(s['kind'] for s in values)),
            'long_kinds': dict(Counter(s['kind'] for s in long))}


def compare(before, after):
    changes = []
    for key in sorted(before.keys() | after.keys()):
        a, b = before.get(key), after.get(key)
        if a is None:
            status = 'new_opening'
        elif b is None:
            status = 'removed_opening'
        elif a['kind'] != b['kind']:
            status = 'reclassified'
        elif a['spans_sha256'] == b['spans_sha256']:
            status = 'unchanged'
        elif b['characters'] < a['characters']:
            status = 'less_assigned_text'
        elif b['characters'] > a['characters']:
            status = 'more_assigned_text'
        else:
            status = 'changed_spans'
        changes.append({'section_id': key, 'change': status, 'before': a, 'after': b,
                        'quality_verdict': None})
    return changes


def run(before_dir, after_dir, out, seed=73):
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a new comparison directory')
    before = load_sections(before_dir/'sections.jsonl')
    after = load_sections(after_dir/'sections.jsonl')
    changes = compare(before, after)
    summary = {'before': stats(before.values()), 'after': stats(after.values()),
               'changes': dict(Counter(r['change'] for r in changes)), 'seed': seed,
               'quality_measured': False, 'db_updates': 0,
               'limitations': ['Extents overlap; their sum is not unique corpus coverage.',
                              'Changed source hashes create different opening identities.',
                              'Less assigned text can remove false positives or lose valid continuations.',
                              'Splits/merges are not inferred solely from overlapping file ranges.']}
    groups = {}
    for row in changes:
        sec = row['after'] or row['before']
        key = (sec['volume'][:2], sec['kind'], row['change'])
        groups.setdefault(key, []).append(row)
    rng = random.Random(seed)
    sample = [rng.choice(groups[key]) for key in sorted(groups)]
    out.mkdir(parents=True, exist_ok=True)
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    for name, rows in [('changes.jsonl', changes), ('review_sample.jsonl', sample)]:
        with (out/name).open('w') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False)+'\n')
    lines = ['# Comparação mecânica de regiões', '',
             'Alterações não são aprovação de qualidade. A amostra permanece sem avaliação.', '',
             '| Medida | Antes | Depois |', '|---|---:|---:|']
    for name in ['regions', 'long_regions', 'extent_sum', 'long_extent_sum', 'characters_sum']:
        lines.append(f"| {name} | {summary['before'][name]} | {summary['after'][name]} |")
    lines.extend(['', '## Alterações', ''])
    lines.extend(f'- {key}: {value}' for key, value in sorted(summary['changes'].items()))
    lines.extend(['', f'Amostra estratificada pendente: {len(sample)} regiões; seed {seed}.',
                  'Não somar extensões como cobertura única nem considerar redução como precisão.'])
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=73)
    args = parser.parse_args()
    print(json.dumps(run(args.before, args.after, args.out, args.seed), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
