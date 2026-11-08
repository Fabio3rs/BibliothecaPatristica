"""Repeatable read-only review of candidate losses, scripture ownership and a bounded map."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.footer_marker_experiment import dump, rows, visible_map
from tools.indexing.scripture_comparison import readonly
from scripts.audit_alphabetical_numbering import build_series


def fingerprint(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def dotted_columns(line):
    parts = re.split(r'\.{2,}', line.strip())
    if len(parts) != 2 or not re.fullmatch(r'\s*\d+(?:\s*,\s*\d+)*\s*', parts[1]):
        return None
    return {'scripture_raw': parts[0].strip(), 'material_raw': parts[1].strip(),
            'material_numbers': [int(n) for n in re.findall(r'\d+', parts[1])]}


def compare_runs(before, after, out):
    counts = Counter()
    changes = []
    loss_pairs = {}
    for path in sorted(before.glob('*/candidate_diff.jsonl')):
        current = {r['occurrence_id']: r for r in rows(after/path.parent.name/path.name)}
        for old in rows(path):
            new = current.pop(old['occurrence_id'], None)
            if new is None:
                counts['not_comparable_missing_occurrence'] += 1
                continue
            restored = set(new['proposed'])-set(old['proposed'])
            removed = set(old['proposed'])-set(new['proposed'])
            counts['compared_occurrences'] += 1
            counts['restored_edges'] += len(restored)
            counts['newly_withheld_edges'] += len(removed)
            if restored or removed:
                changes.append({'occurrence_id': old['occurrence_id'], 'number': old['number'],
                    'volume': path.parent.name, 'restored': sorted(restored), 'newly_withheld': sorted(removed),
                    'current_source': new['source_current'], 'verified': False})
            for evidence in old['removed_evidence']:
                if evidence['reason'] != 'sequence_support_lost':
                    continue
                key = (path.parent.name, old['number'], evidence['path'])
                loss_pairs.setdefault(key, {'volume': key[0], 'number': key[1], 'path': key[2],
                    'affected_occurrences': 0, 'now_proposed': evidence['path'] in new['proposed']})
                loss_pairs[key]['affected_occurrences'] += 1
        counts['not_comparable_new_occurrence'] += len(current)
    dump(out/'run_changes.json', changes)
    dump(out/'sequence_loss_pairs.json', list(loss_pairs.values()))
    counts['old_sequence_loss_distinct_pairs'] = len(loss_pairs)
    counts['old_sequence_loss_pairs_restored'] = sum(r['now_proposed'] for r in loss_pairs.values())
    return dict(counts)


def review_scripture(db_path, volume, source_paths, out):
    known_lines = []
    for path in source_paths:
        evidence = fingerprint(path)
        raw = path.read_text()
        for block in blocks(raw):
            for match in re.finditer(r'[^\n]+', block['body']):
                line = match[0].strip()
                parsed = dotted_columns(line)
                if parsed:
                    known_lines.append({**evidence, **parsed, 'line_raw': line,
                        'char_start': block['body_start']+match.start(),
                        'char_end': block['body_start']+match.end(),
                        'span_raw': match[0]})
    counts, reviews = Counter(), []
    with closing(readonly(db_path)) as db:
        refs = db.execute('''SELECT sr.*,e.entry_raw,e.index_entry_source_ocr_file
            FROM alphabetical_scripture_refs sr JOIN alphabetical_entries e USING(entry_key)
            JOIN alphabetical_sections s USING(section_key) WHERE s.volume_id=? ORDER BY scripture_ref_id''', (volume,))
        for ref in refs:
            material = [dict(r) for r in db.execute('''SELECT ref_id,ref_raw,page_ref_int FROM alphabetical_refs
                WHERE entry_key=? AND scripture_ref_order=? ORDER BY ref_id''', (ref['entry_key'], ref['ref_order']))]
            if ref['verse_start'] is None or len(material) < 2 or not any(r['page_ref_int'] == ref['verse_start'] for r in material):
                continue
            parsed = dotted_columns(ref['entry_raw'])
            matches = [line for line in known_lines if parsed and
                       re.sub(r'\s+', '', line['scripture_raw']) == re.sub(r'\s+', '', parsed['scripture_raw']) and
                       line['material_numbers'] == parsed['material_numbers']]
            # Equal verse/page numbers alone are not grounds for rejecting a material reference.
            conflicting = [r['ref_id'] for r in material if parsed and r['page_ref_int'] == ref['verse_start']
                           and r['page_ref_int'] not in parsed['material_numbers']]
            origin = ref['index_entry_source_ocr_file']
            origin_path = Path(origin) if origin else None
            origin_evidence = fingerprint(origin_path) if origin_path and origin_path.is_file() else None
            origin_text = ''
            if origin_evidence:
                origin_text = '\n'.join(visible_map(b['body'])[0] for b in blocks(origin_path.read_text()))
            origin_line_present = bool(parsed and any(dotted_columns(line) == parsed for line in origin_text.splitlines()))
            status = ('single_line_match_with_material_conflict' if len(matches) == 1 and conflicting else
                      'material_conflict_needs_source' if conflicting else 'review_without_proven_conflict')
            counts[status] += 1
            counts['flagged_references'] += 1
            counts['original_source_line_absent'] += not origin_line_present
            reviews.append({'scripture_ref_id': ref['scripture_ref_id'], 'entry_key': ref['entry_key'],
                'book_raw': ref['book_raw'], 'entry_raw': ref['entry_raw'], 'verse_start': ref['verse_start'],
                'material': material, 'conflicting_material_ids': conflicting, 'status': status,
                'stored_source': origin_evidence, 'stored_source_line_present': origin_line_present,
                'line_matches_in_explicit_review_pages': matches,
                'warning': 'Line match is local to supplied pages; book context/printed source still requires verification.',
                'automatic_resolution': False})
    dump(out/'scripture_material_review.json', reviews)
    return dict(counts)


def review_internal(probe_path, first, last, physical_first, physical_last, out):
    probe = json.loads(probe_path.read_text())
    records = []
    checks = {}
    for number in range(first, last+1):
        candidates = []
        for physical in probe['supported'].get(str(number), []):
            if not physical_first <= physical <= physical_last:
                continue
            page = probe['pages'][str(physical)]
            path = Path(page['file'])
            if str(path) not in checks:
                checks[str(path)] = fingerprint(path) if path.is_file() else {'path': str(path), 'sha256': None}
            candidates.append({'physical': physical, 'source': checks[str(path)],
                'probe_sha256': page['sha256'], 'current': checks[str(path)]['sha256'] == page['sha256'],
                'marker_evidence': page['occurrences'].get(str(number), [])})
        records.append({'number': number, 'candidates': candidates, 'verified': False})
    dump(out/'internal_number_review.json', records)
    return {'numbers_in_scope': len(records), 'numbers_with_one_candidate': sum(len(r['candidates']) == 1 for r in records),
            'numbers_without_candidate': sum(not r['candidates'] for r in records),
            'distinct_files': len(checks), 'stale_candidates': sum(not c['current'] for r in records for c in r['candidates']),
            'note': 'Existing probe audited, not rebuilt; rerun its map after changed OCR.'}


def rebuild_internal(profile, corpus_root, out):
    spec = json.loads(profile.read_text())
    series = build_series(spec, corpus_root)
    supported = defaultdict(list)
    for number, physical in series['supported']:
        supported[number].append(physical)
    for path, checksum in series['sources']:
        if fingerprint(Path(path))['sha256'] != checksum:
            raise ValueError(f'OCR changed during map rebuild: {path}')
    path = out/'rebuilt_internal_probe.json'
    dump(path, {'profile': spec, 'profile_fingerprint': fingerprint(profile),
        'supported': {n: sorted(ps) for n,ps in supported.items()},
        'markers': {n: sorted(ps) for n,ps in series['nodes'].items()},
        'pages': {p: {k:v for k,v in r.items() if k != 'body'} for p,r in series['pages'].items()},
        'warning': 'Diagnostic numeric probe; chapter/date contamination possible. No certified namespace.'})
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', type=Path, required=True)
    p.add_argument('--after', type=Path, required=True)
    p.add_argument('--index-db', type=Path, default=Path('data/alphabetical_indices.db'))
    p.add_argument('--volume', default='PO022')
    p.add_argument('--index-page', action='append', type=Path, required=True)
    internal = p.add_mutually_exclusive_group(required=True)
    internal.add_argument('--internal-probe', type=Path)
    internal.add_argument('--internal-profile', type=Path, help='Rebuild the numeric probe mechanically from current OCR')
    p.add_argument('--corpus-root', type=Path, default=Path('teste'))
    p.add_argument('--number-range', default='369:472')
    p.add_argument('--physical-range', default='197:249')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    first, last = map(int, a.number_range.split(':'))
    pf, pl = map(int, a.physical_range.split(':'))
    if not first <= last or not pf <= pl:
        p.error('Reversed range')
    for path in [a.index_db, a.internal_probe or a.internal_profile, *a.index_page]:
        if not path.is_file():
            p.error(f'Missing input: {path}')
    before_files = sorted(a.before.glob('*/candidate_diff.jsonl'))
    if not before_files:
        p.error('No before candidate diffs found')
    for path in before_files:
        if not (a.after/path.parent.name/path.name).is_file():
            p.error(f'Missing after candidate diff for {path.parent.name}')
    a.out.mkdir(parents=True, exist_ok=False)
    dump(a.out/'config.json', {**{k: str(v) if isinstance(v, Path) else [str(x) for x in v] if isinstance(v, list) else v
         for k,v in vars(a).items()}, 'implementation': fingerprint(Path(__file__)),
         'probe_or_profile': fingerprint(a.internal_probe or a.internal_profile)})
    probe = rebuild_internal(a.internal_profile, a.corpus_root, a.out) if a.internal_profile else a.internal_probe
    summary = {'candidate_diff': compare_runs(a.before, a.after, a.out),
        'scripture': review_scripture(a.index_db, a.volume, a.index_page, a.out),
        'internal_map': review_internal(probe, first, last, pf, pl, a.out),
        'production_updates': 0, 'inference_calls': 0}
    if a.internal_profile:
        summary['internal_map']['note'] = 'Rebuilt mechanically from current OCR; diagnostic, not certified.'
    dump(a.out/'summary.json', summary)
    (a.out/'report.md').write_text('# Revisão dos próximos alvos\n\n'
        'As propostas continuam sem promoção. Fonte alterada requer reconstrução da respectiva camada.\n\n'
        '```json\n'+json.dumps(summary, ensure_ascii=False, indent=2)+'\n```\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
