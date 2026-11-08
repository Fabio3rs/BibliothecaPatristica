#!/usr/bin/env python3
"""Close one bounded name index using existing parser/maps and explicit visual evidence.

Offline; no production writes. Export a searchable SQLite and linked Markdown,
keeping transcription, numeric location and visual checking as separate states.
"""
import argparse
from collections import Counter
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.indexing.entry_parser_po import parse_po_page
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.parser_target_experiment import build_header_series, locate_header
from tools.indexing.po_locator_candidates import parse_po_locators, locate_po_candidates


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def search_name(raw):
    return ''.join(c for c in unicodedata.normalize('NFD', raw.casefold())
                   if not unicodedata.combining(c))


def parse_group(raw):
    """PO015 only: semicolons separate pages; comma lists are lines, not pages."""
    value = ' '.join(raw.split()).strip(' ;.')
    note = None
    cf = re.search(r'\(?\bcf\.\s*(?=[^\d\s])', value)
    if cf:
        note, value = value[cf.start():], value[:cf.start()].strip()
    else:
        annotation = re.search(r'\([^)]*\)\.?$', value)
        if annotation:
            note, value = annotation[0], value[:annotation.start()].strip()
    value = re.sub(r'^cf\.\s*', '', value)
    m = re.fullmatch(r'([0-9]+)(?:\s*[,。.]\s*|\s+)([0-9]+(?:\s*,\s*[0-9]+)*)', value)
    if not m:
        return {'status': 'notation_pending', 'annotation_raw': note}
    page, lines = int(m[1]), [int(n) for n in re.findall(r'[0-9]+', m[2])]
    # A descending sequence may hide a missing page separator (33,14,34,8).
    if page <= 0 or any(n <= 0 for n in lines) or lines != sorted(set(lines)):
        return {'status': 'notation_pending', 'annotation_raw': note}
    return {'status': 'interpreted_page_line', 'page': page, 'lines': lines,
            'annotation_raw': note}


def groups(fragment, raw):
    a, b = fragment['char_start'], fragment['char_end']
    for m in re.finditer(r'[^;]+', raw[a:b]):
        text = m[0]
        left = len(text) - len(text.lstrip())
        text = text.strip()
        if text:
            start = a + m.start() + left
            parsed = parse_group(text)
            if parsed['status'] == 'notation_pending':
                candidates = parse_po_locators(text, 'page_line', base=start)
                if len(candidates['locators']) == 1:
                    candidate = candidates['locators'][0]
                    parsed.update(status='page_candidate_detail_pending', page=candidate['page'],
                                  lines=[], detail_raw=candidate['detail_raw'],
                                  uncertainties=candidate['warnings'], grammar_version=candidates['version'])
            yield {'source': fragment['source'], 'char_start': start,
                   'char_end': start + len(text), 'ref_raw': text, **parsed}


def baseline_comparison(directory, entries):
    from tools.indexing.global_target_survey import load_pages
    config = json.loads((directory / 'config.json').read_text())
    pages, _, _ = load_pages(config['experiment_db'], config['parser_runs'], config['structured_db'], 'PO015')
    sources = {e['source'] for e in entries}
    old = {e['id']: e for source, p in pages.items() if source in sources
           for e in (p['page'] or {}).get('entries', [])}
    new = {e['id']: e for e in entries}
    with closing(sqlite3.connect((directory / 'survey.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as con:
        con.execute('PRAGMA query_only=ON')
        records = [json.loads(row[0]) for row in con.execute("SELECT evidence_json FROM groups WHERE volume='PO015'")]
        records = [r for r in records if r['source'] in sources]
        links = sum(con.execute('SELECT count(DISTINCT o.id) FROM occurrences o JOIN candidates c '
                                'ON c.occurrence_id=o.id WHERE o.group_id=?', (r['id'],)).fetchone()[0] for r in records)
    return {'survey': str(directory.resolve()), 'before_entries': len(old), 'after_entries': len(new),
            'preserved_entry_ids': len(old.keys() & new.keys()),
            'added_entry_ids': sorted(new.keys() - old.keys()), 'removed_entry_ids': sorted(old.keys() - new.keys()),
            'name_changes': [k for k in old.keys() & new.keys() if old[k]['text_raw'] != new[k]['name_raw']],
            'baseline_group_statuses': dict(Counter(r['status'] for r in records)),
            'baseline_occurrences_with_files': links,
            'note': 'Original groups cover whole entry suffixes; new groups separate page/line lists. Counts are not directly subtractable.'}


def run(review_path, out, root=ROOT, baseline=None):
    review = json.loads(review_path.read_text())
    # Review decisions are valid for these exact images and OCRs only.
    for proof in [*review['sources'].values(), *review['images'].values()]:
        if sha(root / proof['path']) != proof['sha256']:
            raise ValueError(f"Reviewed source changed; revalidate: {proof['path']}")
    paths = {int(n): root / p['path'] for n, p in review['sources'].items()}
    texts = {n: p.read_text() for n, p in paths.items()}
    pages, entries, owners = {}, [], {}
    for number in (297, 298, 299):
        context = []
        for neighbor, relation in [(number - 1, 'previous'), (number + 1, 'next')]:
            context.append({'relation': relation, 'source': str(paths[neighbor]),
                            'titles': [b['body'].strip() for b in blocks(texts[neighbor])
                                       if b['kind'] == 'cabecalho']})
        p = parse_po_page(texts[number], source=str(paths[number]), physical=number,
                          volume='PO015', context=context)
        pages[number] = p
        for ordinal, original in enumerate(p['entries'], 1):
            e = {'id': original['id'], 'physical': number, 'ordinal': ordinal,
                 'name_raw': original['text_raw'], 'name_search': search_name(original['text_raw']),
                 'source': str(paths[number]), 'source_sha256': p['sha256'],
                 'entry_fragments': [{'source': str(paths[number]), 'char_start': original['char_start'],
                                      'char_end': original['char_end'], 'raw': original['entry_raw']}],
                 'reference_fragments': [{'source': str(paths[number]), **original['reference_span']}],
                 'references': [], 'original_candidate': original,
                 'quality': {'section': 'assistant_visual_scope_review',
                             'segmentation': 'candidate_with_visual_count_check',
                             'name_reading': 'not_individually_verified', 'human_reviewed': False}}
            entries.append(e)
            owners[number, ordinal] = e
    continuation = review['continuation']
    n = continuation['source_page']
    raw = texts[n]
    for text in (continuation['start'], continuation['end']):
        if raw.count(text) != 1:
            raise ValueError('Continuation evidence is not unique')
    a, b = raw.index(continuation['start']), raw.index(continuation['end']) + len(continuation['end'])
    owner = owners[continuation['owner_page'], continuation['owner_ordinal']]
    if not owner['original_candidate']['references_raw'].rstrip().endswith(';'):
        raise ValueError('Owner no longer has an open reference list')
    if b > pages[n]['entries'][0]['char_start']:
        raise ValueError('Continuation overlaps the following entry')
    fragment = {'source': str(paths[n]), 'char_start': a, 'char_end': b, 'raw': raw[a:b]}
    owner['entry_fragments'].append(fragment)
    owner['reference_fragments'].append(fragment)
    owner['quality']['continuation'] = 'assistant_visual_review'
    for issue in review['known_lemma_reading_issues']:
        e = owners[issue['page'], issue['ordinal']]
        if e['name_raw'] != issue['ocr']:
            raise ValueError('Lemma review no longer matches entry')
        e['quality']['name_reading'] = 'known_ocr_disagreement'
        e['reading_proposal'] = issue
    profile_path = root / 'docs/data/po_fascicle_section_maps_v1.json'
    spec = json.loads(profile_path.read_text())['sections'][0]['series']
    series = build_header_series(spec, root / 'teste')
    visual_anchors = {p['number']: p for p in review['visual_page_anchors']}
    checks = {(p['page'], p['ordinal'], p['number']): p for p in review['target_checks']}
    by_source = {str(paths[n]): texts[n] for n in (297, 298, 299)}
    references = []
    for e in entries:
        for f in e['reference_fragments']:
            for ref in groups(f, by_source[f['source']]):
                ref.update(id=f"{e['id']}:r{len(e['references']) + 1}", entry_id=e['id'],
                           target_files=[], visual_pair_confirmed=False, human_reviewed=False)
                ref['source_sha256'] = sha(Path(ref['source']))
                if ref['status'] in {'interpreted_page_line', 'page_candidate_detail_pending'}:
                    ref['notation_status'] = ref['status']
                    location = locate_po_candidates(series, ref['page'])
                    ref['mechanical_location_status'] = location['status']
                    ref['target_files'] = location['files']
                    ref['status'] = 'mapped_candidate' if location['files'] else 'missing_page_marker'
                    if ref['page'] in visual_anchors:
                        anchor = visual_anchors[ref['page']]
                        target = paths[anchor['physical']]
                        existing = {x['file'] for x in ref['target_files']}
                        if existing and existing != {str(target)}:
                            raise ValueError('Visual and mechanical map conflict')
                        ref['target_files'] = [{'file': str(target), 'physical': anchor['physical'],
                                                'sha256': sha(target), 'basis': 'assistant_visual_header',
                                                'image': review['images'][str(anchor['physical'])]}]
                        ref['status'] = 'mapped_visual_header'
                    check = checks.get((e['physical'], e['ordinal'], ref['page']))
                    if check and any(t['physical'] == check['physical'] for t in ref['target_files']):
                        ref['visual_pair_confirmed'] = True
                        ref['target_review'] = check
                e['references'].append(ref)
                references.append(ref)
    coverage = []
    for n, p in pages.items():
        raw = texts[n]
        used = Counter(i for e in entries for f in e['entry_fragments'] if f['source'] == str(paths[n])
                       for i in range(f['char_start'], f['char_end']) if not raw[i].isspace())
        body = {i for b in blocks(raw) if b['kind'] == 'texto_principal'
                for i in range(b['body_start'], b['body_end']) if not raw[i].isspace()}
        note = set()
        if n == 297:
            notice = "On n'indique pas dans cette table les noms propres qui figurent dans les citations bibliques."
            start = raw.index(notice)
            note = {i for i in range(start, start + len(notice)) if not raw[i].isspace()}
        residual = body - set(used) - note
        coverage.append({'physical': n, 'body_nonspace': len(body), 'entry_nonspace': len(body & set(used)),
                         'editorial_note_nonspace': len(note), 'residual_nonspace': len(residual),
                         'overlap_nonspace': sum(v > 1 for v in used.values()),
                         'residual_text': ''.join(raw[i] for i in sorted(residual))})
    samples = []
    for sample in review['samples']:
        e = owners[sample['page'], sample['ordinal']]
        observed = [[r['page'], r['lines']] for r in e['references'] if 'page' in r]
        samples.append({**sample, 'entry_id': e['id'], 'name_raw': e['name_raw'],
                        'observed': observed, 'matches': observed == sample['expected_page_line_groups']})
    for path, checksum in series['sources']:
        if sha(Path(path)) != checksum:
            raise ValueError('Map source changed during run')
    comparison = baseline_comparison(baseline, entries) if baseline else None
    out.mkdir(parents=True, exist_ok=False)
    if comparison:
        dump(out / 'baseline_comparison.json', comparison)
    dump(out / 'entries.json', entries)
    dump(out / 'coverage.json', coverage)
    dump(out / 'sample_checks.json', samples)
    dump(out / 'review.json', review)
    with sqlite3.connect(out / 'catalog.sqlite') as con:
        con.executescript('''PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
            CREATE TABLE entries(id TEXT PRIMARY KEY,name_raw TEXT,name_search TEXT,evidence_json TEXT);
            CREATE TABLE refs(id TEXT PRIMARY KEY,entry_id TEXT REFERENCES entries,page INTEGER,
                              status TEXT,evidence_json TEXT);
            CREATE TABLE targets(ref_id TEXT REFERENCES refs,path TEXT,physical INTEGER,
                                 PRIMARY KEY(ref_id,path));
            CREATE VIRTUAL TABLE names_fts USING fts5(entry_id UNINDEXED,name_raw,name_search);
        ''')
        for e in entries:
            con.execute('INSERT INTO entries VALUES(?,?,?,?)', (e['id'], e['name_raw'], e['name_search'], json.dumps(e, ensure_ascii=False)))
            con.execute('INSERT INTO names_fts VALUES(?,?,?)', (e['id'], e['name_raw'], e['name_search']))
        for r in references:
            con.execute('INSERT INTO refs VALUES(?,?,?,?,?)', (r['id'], r['entry_id'], r.get('page'), r['status'], json.dumps(r, ensure_ascii=False)))
            for t in r['target_files']:
                con.execute('INSERT INTO targets VALUES(?,?,?)', (r['id'], t['file'], t['physical']))
        assert con.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    with (out / 'references.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=['entry_id', 'name_raw', 'ref_raw', 'page', 'lines', 'status', 'files', 'visual_pair_confirmed'])
        writer.writeheader()
        for e in entries:
            for r in e['references']:
                writer.writerow({'entry_id': e['id'], 'name_raw': e['name_raw'], 'ref_raw': r['ref_raw'],
                                 'page': r.get('page'), 'lines': r.get('lines'), 'status': r['status'],
                                 'files': json.dumps([t['file'] for t in r['target_files']]),
                                 'visual_pair_confirmed': r['visual_pair_confirmed']})
    summary = {'section': 'PO015:Les Trophees de Damas:Table des noms propres',
               'entries': len(entries), 'printed_entry_starts_assistant_count': sum(review['entry_starts_by_page'].values()),
               'entry_counts_by_page': dict(Counter(e['physical'] for e in entries)),
               'material_groups_including_pending': len(references),
               'reference_statuses': dict(Counter(r['status'] for r in references)),
               'entries_with_any_target': sum(any(r['target_files'] for r in e['references']) for e in entries),
               'entries_with_all_material_groups_mapped': sum(bool(e['references']) and all(r['target_files'] for r in e['references']) for e in entries),
               'references_with_target': sum(bool(r['target_files']) for r in references),
               'pending_by_editorial_page': dict(Counter(str(r.get('page', 'notation_pending'))
                                                        for r in references if not r['target_files'])),
               'references_with_mechanical_target_before_visual_anchors': sum(r.get('mechanical_location_status') == 'header_page_candidate' for r in references),
               'visual_source_target_pairs_confirmed': sum(r['visual_pair_confirmed'] for r in references),
               'fixed_sample_entries': len(samples), 'fixed_sample_matching_entries': sum(s['matches'] for s in samples),
               'fixed_sample_expected_material_groups': sum(len(s['expected_page_line_groups']) for s in samples),
               'continuations_reconstructed': 1, 'known_lemma_ocr_issues': len(review['known_lemma_reading_issues']),
               'coverage': coverage, 'human_reviewed': 0, 'import_ready': False,
               'production_updates': 0, 'inference_calls': 0,
               'warning': 'Count agreement is not exact transcription recall; directed assistant review is not human gold or blind precision.'}
    dump(out / 'summary.json', summary)
    dump(out / 'manifest.json', {'version': 'po015-names-catalog-v1', 'root': str(root),
         'review': str(review_path.resolve()), 'review_sha256': sha(review_path),
         'baseline_config_sha256': sha(baseline / 'config.json') if baseline else None,
         'profile_sha256': sha(profile_path), 'target_sources': dict(series['sources']),
         'code_sha256': {str(p): sha(p) for p in [Path(__file__), ROOT/'tools/indexing/entry_parser_po.py',
                                               ROOT/'tools/indexing/parser_target_experiment.py']}})
    lines = ['# PO015 — Table des noms propres', '',
             'Catálogo local de revisão. Grafias OCR preservadas; links candidatos indicam arquivo, não certificação semântica.', '',
             'SQLite pesquisável: catalog.sqlite. Dados completos: entries.json; referências: references.csv.', '']
    for e in entries:
        lines += [f"## {e['name_raw']}", '', f"Origem: [OCR {e['physical']}]({e['source']}) · "
                  f"[fac-símile]({root / review['images'][str(e['physical'])]['path']})", '']
        if e.get('reading_proposal'):
            lines += [f"Leitura em revisão: {e['reading_proposal']['printed']} (OCR original preservado).", '']
        for r in e['references']:
            targets = ', '.join(f"[arquivo {t['physical']}]({t['file']})" for t in r['target_files']) or 'pendente'
            lines.append(f"- {' '.join(r['ref_raw'].split())} → {targets}; {r['status']}" +
                         ('; par conferido visualmente pelo assistente' if r['visual_pair_confirmed'] else ''))
        lines.append('')
    (out / 'catalog.md').write_text('\n'.join(lines))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', type=Path, default=ROOT / 'docs/data/po015_names_visual_review_v1.json')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--baseline', type=Path, help='Existing global survey, read only; compare the same entry IDs')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.review, args.out, args.root, args.baseline), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
