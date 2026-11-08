"""Audit OCR conservation independently of candidate enumeration; no imports or inference."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import uuid

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.editorial_regions_experiment import fold, sha, signals
from tools.indexing.entry_parser_bulk import readonly, open_experiment

VERSION = 'index-conservation-audit-v1'
NUMERAL = re.compile(r'\d+(?:[₀-₉]+)?|(?<!\w)[IVXLCDM]+(?!\w)')
CATEGORIES = ['outside_region', 'region_overlap', 'duplicate_consumption',
              'entry_editorial_overlap', 'consumed_by_candidate', 'editorial_candidate', 'residual']


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def mark(mask, start, end):
    for i in range(max(0, start), min(len(mask), end)):
        mask[i] = min(2, mask[i] + 1)


def valid_span(raw, item):
    a, b = item.get('char_start', -1), item.get('char_end', -1)
    return (isinstance(a, int) and isinstance(b, int) and 0 <= a < b <= len(raw)
            and sha(raw[a:b].encode()) == item.get('text_sha256'))


def reference_spans(entry):
    # New row profiles preserve multiple local clause owners; do not flatten them.
    groups = entry.get('reference_groups')
    return groups if groups is not None else ([entry['reference_span']] if entry.get('reference_span') else [])


def coverage(raw, scopes, entries):
    """Count original non-whitespace OCR characters, not reconstructed parser text."""
    size = len(raw)
    visible, scoped, consumed, reference, editorial = [bytearray(size) for _ in range(5)]
    known = bytearray(size)
    for block in blocks(raw):
        a, b = block['body_start'], block['body_end']
        visible[a:b] = b'\1' * (b-a)
        known[a:b] = b'\1' * (b-a)
        if block['kind'] in {'cabecalho', 'nota_marginal', 'nota', 'aparato_critico', 'rodape'}:
            mark(editorial, a, b)
    for match in re.finditer(r'<[^>]*>|\s+', raw):
        visible[match.start():match.end()] = b'\0' * len(match[0])
    _, events = signals(raw)
    for event in events:
        if event['role'] in {'section_start', 'running_header', 'section_end', 'work_opening'}:
            mark(editorial, *event['span'])
    for scope in scopes:
        mark(scoped, *scope['span'])
    invalid = []
    for entry in entries:
        if not valid_span(raw, entry):
            invalid.append(entry['id'])
            continue
        mark(consumed, entry['char_start'], entry['char_end'])
        for ref in reference_spans(entry):
            if valid_span(raw, ref) and entry['char_start'] <= ref['char_start'] < ref['char_end'] <= entry['char_end']:
                mark(reference, ref['char_start'], ref['char_end'])
    totals, runs, numbers = Counter({k:0 for k in CATEGORIES}), [], []
    current = None
    for i, char in enumerate(raw):
        if not visible[i]:
            continue
        category = ('outside_region' if not scoped[i] else 'region_overlap' if scoped[i] > 1 else
                    'duplicate_consumption' if consumed[i] > 1 else
                    'entry_editorial_overlap' if consumed[i] and editorial[i] else
                    'consumed_by_candidate' if consumed[i] else 'editorial_candidate' if editorial[i] else 'residual')
        totals[category] += 1
        if current and current['category'] == category:
            current['span'][1] = i+1
            current['nonspace_characters'] += 1
        else:
            current = {'category': category, 'span': [i, i+1], 'nonspace_characters': 1}
            runs.append(current)
    for match in NUMERAL.finditer(raw):
        a, b = match.span()
        if not all(visible[a:b]) or not any(scoped[a:b]):
            continue
        status = ('duplicate_candidate_consumption' if any(x > 1 for x in consumed[a:b]) else
                  'reference_group' if all(reference[a:b]) else
                  'inside_entry_outside_reference_group' if all(consumed[a:b]) else
                  'editorial_candidate' if all(editorial[a:b]) else 'residual_or_partial')
        numbers.append({'span': [a, b], 'literal': match[0], 'status': status,
                        'context': raw[max(0, a-70):min(size, b+70)], 'interpretation': 'unassigned_numeric_token'})
    outside_known, unclassified = 0, []
    plain = re.sub(r'<[^>]*>', lambda m: ' ' * len(m[0]), raw)
    unknown_span = None
    for i, char in enumerate(plain):
        if known[i] or char == '\n':
            if unknown_span:
                unclassified.append({'span':unknown_span})
                unknown_span = None
        if not known[i] and not char.isspace():
            outside_known += 1
            if unknown_span is None:
                unknown_span = [i,i+1]
            else:
                unknown_span[1] = i+1
    if unknown_span:
        unclassified.append({'span':unknown_span})
    for item in unclassified:
        a,b = item['span']
        item.update(snippet=raw[a:b][:500], text_sha256=sha(raw[a:b].encode()),
                    status='unrecognized_or_metadata_text_not_assumed_printed')
    for item in runs:
        item['text_sha256'] = sha(raw[slice(*item['span'])].encode())
        item['snippet'] = raw[slice(*item['span'])][:500]
    return {'characters': dict(totals), 'runs': runs, 'numeric_tokens': numbers,
            'unclassified_markup': unclassified,
            'outside_known_blocks_characters': outside_known, 'invalid_candidate_ids': invalid,
            'count_basis': 'non-whitespace characters of known OCR block bodies, XML tags excluded; entities remain literal'}


def anomalies(raw, entry, all_ids):
    findings = []

    def add(kind, severity='review', **details):
        findings.append({'kind': kind, 'severity': severity, **details})

    if not valid_span(raw, entry):
        add('invalid_source_span', 'blocking')
        return findings
    a, b = entry['char_start'], entry['char_end']
    text = raw[a:b]
    if len(text) >= 2000:
        add('long_entry', characters=len(text))
    numerals = list(NUMERAL.finditer(text))
    if len(numerals) >= 50:
        add('dense_numeric_entry', numeric_tokens=len(numerals))
    starts = list(re.finditer(r'(?:^|\n|[.;]\s+)([A-ZÀ-ÖØ-ÞΑ-Ω][^\n.;]{1,85}?,\s*(?:\d|[IVX]+\s*,))', text))
    if len(starts) >= 3:
        add('multiple_entry_start_shapes', occurrences=len(starts), examples=[m[1] for m in starts[:5]])
    refs = reference_spans(entry)
    for ref in refs:
        owner = ref.get('owner_span')
        if owner is not None and (not valid_span(raw,owner) or not a <= owner['char_start'] < owner['char_end'] <= ref['char_start'] <= b):
            add('reference_owner_span_invalid_or_outside_entry','blocking')
        if not valid_span(raw, ref) or not a <= ref['char_start'] < ref['char_end'] <= b:
            add('reference_span_invalid_or_outside_entry', 'blocking')
        else:
            owner = ref.get('owner_span')
            owner_start = owner['char_start'] if owner and valid_span(raw, owner) else a
            before = [m[0] for m in numerals if owner_start <= a+m.start() and a + m.end() <= ref['char_start']]
            if before:
                add('numeric_tokens_before_reference_group', examples=before[:10], count=len(before),
                    arabic_or_subscript_count=sum(any(c.isdigit() for c in token) for token in before),
                    roman_count=sum(not any(c.isdigit() for c in token) for token in before))
    if not refs and entry.get('references_raw'):
        add('literal_reference_without_verified_span', 'blocking')
    parent = entry.get('context_candidate_id')
    if parent and parent not in all_ids:
        add('context_not_found_on_current_page', context_candidate_id=parent,
            requires_cross_page_lookup=True)
    if not parent and re.match(r'^\s*[—–]', text):
        add('continuation_without_parent')
    return findings


def audit_page(task):
    source, scopes, expected_hash, parsed, cohort = task
    data = Path(source).read_bytes()
    raw = data.decode('utf-8')
    if sha(data) != expected_hash:
        return {'source': source, 'status': 'source_changed', 'entries': []}
    if parsed is not None and parsed['sha256'] != expected_hash:
        return {'source': source, 'status': 'parser_source_changed', 'entries': []}
    entries = parsed['entries'] if parsed else []
    result = coverage(raw, scopes, entries)
    local_ids = {e['id'] for e in entries}
    checked, previous = [], {}
    for entry in sorted(entries, key=lambda e:e['char_start']):
        if not any(entry['char_start'] < s['span'][1] and entry['char_end'] > s['span'][0] for s in scopes):
            continue
        findings = anomalies(raw, entry, local_ids)
        affected = [s for s in scopes if entry['char_start'] < s['span'][1] and entry['char_end'] > s['span'][0]]
        if len(affected) == 1 and affected[0]['kind'] in {'onomastic_index', 'lexical_index'}:
            lemma = fold(entry.get('text_raw') or '')
            if re.match(r'^[A-Z]', lemma) and len(lemma) < 120 and not entry.get('context_candidate_id'):
                key = (affected[0]['section_id'], entry.get('block'))
                if key in previous and lemma[0] < previous[key]:
                    findings.append({'kind': 'alphabetic_initial_reversal', 'severity': 'review',
                                     'before': previous[key], 'after': lemma[0],
                                     'caveat': 'Only a Latin-initial observation; XML order and taxonomy are unverified.'})
                previous[key] = lemma[0]
        checked.append({'candidate_id': entry['id'], 'source': source, 'source_sha256': expected_hash,
                        'span': [entry['char_start'], entry['char_end']], 'snippet': entry['entry_raw'][:400],
                        'target_cohort': entry['id'] in cohort, 'findings': findings,
                        'quality': {'region': 'candidate_unknown_opening' if any(s['opening_status'] == 'unknown_first_observed' for s in affected) else 'candidate_opening_evidence',
                                    'segmentation': 'suspect' if findings else 'not_validated',
                                    'lemma_context': 'pending', 'printed_reference': 'raw_not_validated',
                                    'edition_mapping': 'pending', 'destination': 'not_attempted', 'semantic': 'not_executed'},
                        'eligible_for_destination_promotion': False})
    return {'source': source, 'source_sha256': expected_hash, 'status': 'audited',
            'parser_page_present': parsed is not None, 'parser_entries_on_page': len(entries),
            'scopes': scopes, 'entries': checked, **result}


def inventory(recovery):
    sections = [json.loads(line) for line in (recovery/'sections.jsonl').read_text().splitlines()]
    pages = {p['source']: p for p in map(json.loads, (recovery/'pages.jsonl').read_text().splitlines()) if 'source' in p}
    scoped = defaultdict(list)
    for section in sections:
        per_source = defaultdict(set)
        for span in section['spans']:
            per_source[span['source']].add(tuple(span['span']))
            page = pages[span['source']]
            if span['source_sha256'] != page['source_sha256']:
                raise ValueError('Conflicting recovery source hashes')
        for source, owned in per_source.items():
            found = False
            for piece in pages[source]['pieces']:
                if any(tuple(row['span']) in owned for row in piece['rows']):
                    found = True
                    scoped[source].append({'section_id': section['section_id'], 'span': piece['span'],
                                           'opening_status': section['opening_status'], 'kind': section['kind']})
            if not found:
                raise ValueError('No source envelope for recovered region')
    return sections, pages, scoped


def boundary_audit(sections, pages):
    result = []
    for section in sections:
        if section['opening_status'] != 'unknown_first_observed':
            continue
        volume, physical = section['volume'], section['physical_start']
        nearby = sorted([p for p in pages.values() if p['volume'] == volume and abs(p['physical']-physical) <= 2], key=lambda p:p['physical'])
        headers = []
        for page in nearby:
            for event in page['signals']:
                if event['role'] in {'section_start', 'running_header'}:
                    headers.append({'physical': page['physical'], 'source': page['source'],
                                    'source_sha256': page['source_sha256'], **event})
        same_volume = [s for s in sections if s['volume'] == volume and s['section_id'] != section['section_id']]
        adjacent = [s['section_id'] for s in same_volume if s['physical_end'] + 1 == physical or section['physical_end'] + 1 == s['physical_start']]
        by_page = defaultdict(set)
        for h in headers:
            if h['role'] == 'running_header':
                by_page[h['physical']].add(re.sub(r'\d+|[^\w ]', ' ', fold(h['quote'])).strip())
        alternating = any(by_page[n] & by_page[n+2] and not by_page[n] & by_page[n+1]
                          for n in sorted(list(by_page)) if n+2 in by_page and n+1 in by_page)
        result.append({'section_id': section['section_id'], 'volume': volume, 'physical_start': physical,
                       'physical_end': section['physical_end'], 'adjacent_region_ids': adjacent,
                       'reason': 'alternating_headers_suspected' if alternating else 'adjacent_fragment_needs_review' if adjacent else 'opening_requires_source_review',
                       'evidence': headers, 'opening_verified': False, 'automatic_merge_allowed': False})
    return result


def old_snapshot(path, source_keys):
    """Source provenance first: no lemma-only fallback across unrelated pages."""
    con = readonly(path)
    found, volumes, total, originless = [], Counter(), 0, 0
    try:
        con.execute('BEGIN')
        for row in con.execute('''SELECT e.entry_key,e.entry_raw,e.lemma_raw,e.index_entry_source_ocr_file,
                   e.resolved_target_ocr_file,e.target_file_best,s.volume_id
                   FROM alphabetical_entries e JOIN alphabetical_sections s USING(section_key)'''):
            total += 1
            volumes[row['volume_id']] += 1
            origin = row['index_entry_source_ocr_file']
            if not origin:
                originless += 1
            if origin and (row['volume_id'], Path(origin).name) in source_keys:
                found.append(dict(row))
        selected = {r['entry_key']: r for r in found}
        if selected:
            for row in con.execute('''SELECT entry_key,ref_id,ref_raw,locator_status,target_file,resolved_target_ocr_file
                                      FROM alphabetical_refs'''):
                if row['entry_key'] in selected:
                    selected[row['entry_key']].setdefault('references', []).append(dict(row))
        return {'database': str(path.resolve()), 'entries_in_snapshot': total, 'originless_entries': originless,
                'by_volume': dict(volumes), 'overlapping_source_entries': found,
                'comparison_basis': 'exact volume and OCR basename; old entries are not ground truth',
                'production_updates': 0}
    finally:
        con.close()


def run(args):
    if args.workers < 1:
        raise ValueError('workers must be positive')
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Use a new output directory')
    for path in [args.parser_db, args.source_db]:
        if args.experiment_db.resolve() == path.resolve() or (args.experiment_db.exists() and path.exists() and args.experiment_db.samefile(path)):
            raise ValueError('Audit DB must differ from input DBs')
    sections, pages, scoped = inventory(args.recovery)
    cohorts = {a['candidate_id'] for a in map(json.loads, (args.recovery/'associations.jsonl').read_text().splitlines()) if a['region']}
    con = readonly(args.parser_db)
    tasks = []
    try:
        con.execute('BEGIN')
        if not con.execute('SELECT 1 FROM runs WHERE run_id=?', (args.parser_run,)).fetchone():
            raise ValueError('Unknown parser run')
        for source in sorted(scoped):
            row = con.execute('''SELECT t.result_json FROM tasks t JOIN run_pages p USING(fingerprint)
                                  WHERE p.run_id=? AND p.source=?''', (args.parser_run, source)).fetchone()
            parsed = json.loads(row[0]).get('page') if row else None
            tasks.append((source, scoped[source], pages[source]['source_sha256'], parsed, cohorts))
    finally:
        con.close()
    old = old_snapshot(args.source_db, {(pages[s]['volume'], Path(s).name) for s in scoped})
    boundaries = boundary_audit(sections, pages)
    args.out.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    config = {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()}
    config.update(version=VERSION, code_sha256=sha(Path(__file__).read_bytes()),
                  recovery_manifest_sha256=sha((args.recovery/'manifest.json').read_bytes()))
    db = open_experiment(args.experiment_db, args.source_db)
    db.executescript('''CREATE TABLE IF NOT EXISTS conservation_runs(run_id TEXT PRIMARY KEY,created_at TEXT,
                       config_json TEXT,status TEXT,summary_json TEXT);
                       CREATE TABLE IF NOT EXISTS conservation_pages(run_id TEXT,source TEXT,result_json TEXT,
                       PRIMARY KEY(run_id,source));''')
    db.execute('INSERT INTO conservation_runs VALUES(?,?,?,?,NULL)',
               (run_id, datetime.now(timezone.utc).isoformat(), encode(config), 'running'))
    db.commit()
    totals, chars, nums, findings, cohort_findings, numeric_contexts = [Counter() for _ in range(6)]
    page_summaries, all_entries, errors = [], [], []
    (args.out/'manifest.json').write_text(encode({'run_id': run_id, **config})+'\n')
    streams = {key: (args.out/(key+'.jsonl')).open('w') for key in ['coverage', 'entry_quality', 'numeric_tokens', 'residuals', 'unclassified_markup']}
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for result in pool.map(audit_page, tasks):
                totals['pages'] += 1
                if result['status'] != 'audited':
                    errors.append({k: v for k,v in result.items() if k != 'entries'})
                    continue
                source = result['source']
                page = pages[source]
                meta = {k: page[k] for k in ['source', 'source_sha256', 'volume', 'physical']}
                totals['pages_without_parser_result'] += not result['parser_page_present']
                totals['pages_without_candidates'] += result['parser_entries_on_page'] == 0
                totals['outside_known_blocks_characters'] += result['outside_known_blocks_characters']
                chars.update(result['characters'])
                nums.update(n['status'] for n in result['numeric_tokens'])
                for entry in result['entries']:
                    totals['entries_examined'] += 1
                    totals['cohort_examined'] += entry['target_cohort']
                    totals['entries_flagged'] += bool(entry['findings'])
                    totals['cohort_flagged'] += entry['target_cohort'] and bool(entry['findings'])
                    findings.update(f['kind'] for f in entry['findings'])
                    for finding in entry['findings']:
                        if finding['kind'] == 'numeric_tokens_before_reference_group':
                            numeric_contexts['includes_arabic_or_subscript' if finding['arabic_or_subscript_count'] else 'roman_only'] += 1
                    if entry['target_cohort']:
                        cohort_findings.update(f['kind'] for f in entry['findings'])
                    streams['entry_quality'].write(encode({**meta, **entry})+'\n')
                for item in result['runs']:
                    record = {**meta, **item}
                    streams['coverage'].write(encode(record)+'\n')
                    if item['category'] in {'residual', 'duplicate_consumption', 'entry_editorial_overlap', 'region_overlap'}:
                        streams['residuals'].write(encode(record)+'\n')
                for item in result['numeric_tokens']:
                    streams['numeric_tokens'].write(encode({**meta, **item})+'\n')
                for item in result['unclassified_markup']:
                    streams['unclassified_markup'].write(encode({**meta, **item})+'\n')
                page_summaries.append({**meta, 'characters': result['characters'],
                                       'parser_page_present': result['parser_page_present'],
                                       'parser_entries': result['parser_entries_on_page'],
                                       'numeric_tokens': dict(Counter(n['status'] for n in result['numeric_tokens']))})
                all_entries.extend({**meta, **e} for e in result['entries'])
                with db:
                    db.execute('INSERT INTO conservation_pages VALUES(?,?,?)', (run_id, source, encode(result)))
                if args.verbose and totals['pages'] % 50 == 0:
                    print(encode({'progress': True, **totals}), flush=True)
        selected = []
        rng = random.Random(args.seed)
        for collection in ['PG', 'PL', 'PO']:
            eligible = [p for p in page_summaries if p['volume'].startswith(collection)
                        and p['volume'] not in ['PG033','PL003','PL205','PO006','PL218','PL219']]
            if eligible:
                # Source population, never the parser's list of entries or flagged examples.
                selected.append(rng.choice(sorted(eligible, key=lambda p:(p['volume'],p['physical'],p['source']))))
        reconciliation = reconcile(old, all_entries)
        totals['cohort_not_examined'] = len(cohorts) - totals['cohort_examined']
        summary = {'run_id': run_id, 'version': VERSION, 'totals': dict(totals), 'characters': dict(chars),
                   'numeric_tokens': dict(nums), 'anomalies': dict(findings), 'cohort_anomalies': dict(cohort_findings),
                   'numbers_before_reference_contexts': dict(numeric_contexts),
                   'cohort_expected': len(cohorts), 'unknown_openings': len(boundaries),
                   'boundary_reasons': dict(Counter(b['reason'] for b in boundaries)), 'errors': errors,
                   'reconciliation': reconciliation['summary'], 'inference_calls': 0, 'production_updates': 0,
                   'entry_recall': None, 'reference_recall': None, 'printed_completeness': None,
                   'quality_certified': False,
                   'limitations': ['OCR character coverage cannot detect text missing from OCR.',
                                   'Numeric tokens are not interpreted reference occurrences.',
                                   'Editorial roles come from OCR/heading heuristics and can be wrong.',
                                   'Anomalies are review leads; long entries may be legitimate.']}
        for name, value in [('summary.json', summary), ('page_summary.json', page_summaries),
                            ('boundary_review.json', boundaries), ('independent_sample.json', selected),
                            ('old_snapshot.json', old), ('reconciliation.json', reconciliation)]:
            (args.out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
        write_report(args.out, summary, page_summaries)
        with db:
            db.execute('UPDATE conservation_runs SET status=?,summary_json=? WHERE run_id=?',
                       ('completed_with_source_errors' if errors else 'completed', encode(summary), run_id))
        return summary
    except BaseException:
        with db:
            db.execute('UPDATE conservation_runs SET status=? WHERE run_id=?', ('failed', run_id))
        raise
    finally:
        for stream in streams.values():
            stream.close()
        db.close()


def reconcile(old, entries):
    """Exact-literal evidence only. Missing old provenance is not proof of a new occurrence."""
    records = []
    by_source = defaultdict(list)
    for entry in entries:
        by_source[(entry['volume'], Path(entry['source']).name)].append(entry)
    for entry in old['overlapping_source_entries']:
        candidates = by_source[(entry['volume_id'], Path(entry['index_entry_source_ocr_file']).name)]
        # Match against source spans, not the abbreviated diagnostic snippet.
        raw = Path(candidates[0]['source']).read_bytes().decode() if candidates else ''
        matches = [c['candidate_id'] for c in candidates if raw[slice(*c['span'])] == entry['entry_raw']]
        repeated = bool(entry['entry_raw']) and raw.count(entry['entry_raw']) > 1
        records.append({'old_entry_key': entry['entry_key'], 'candidate_ids': matches,
                        'status': 'same_literal_occurrence_candidate' if len(matches) == 1 and not repeated else
                                  'ambiguous_repeated_literal' if matches else 'unmatched_old_entry_requires_review',
                        'previous_evidence': entry, 'verified': False})
    return {'summary': {'old_entries_on_audited_sources': len(old['overlapping_source_entries']),
                         'statuses': dict(Counter(r['status'] for r in records)),
                         'interpretation_agreement_measured': False,
                         'absence_means': 'No registered matching source, not proof of improvement or valid deletion.'},
            'records': records, 'automatic_changes': 0}


def write_report(out, summary, pages):
    lines = ['# Auditoria de conservação do OCR e segmentação', '',
             'Cobertura de caracteres e sinais heurísticos; não é recall do impresso.', '',
             '| Medida | Total |', '|---|---:|']
    lines.extend(f'| {k} | {v} |' for k,v in summary['totals'].items())
    lines.extend(['', '## Caracteres não brancos dos blocos OCR', '', '| Categoria | Total |', '|---|---:|'])
    lines.extend(f'| {k} | {v} |' for k,v in summary['characters'].items())
    lines.extend(['', '## Sinais de segmentação', '', '| Sinal | Ocorrências |', '|---|---:|'])
    lines.extend(f'| {k} | {v} |' for k,v in summary['anomalies'].items())
    lines.extend(['', '## Páginas com mais resíduo', '', '| Volume | Físico | Caracteres residuais | Candidatos |', '|---|---:|---:|---:|'])
    lines.extend(f"| {p['volume']} | {p['physical']} | {p['characters'].get('residual',0)} | {p['parser_entries']} |"
                 for p in sorted(pages, key=lambda p:-p['characters'].get('residual',0))[:20])
    lines.extend(['', 'Aberturas desconhecidas e fragmentação estão em boundary_review.json.',
                  'Estados de qualidade por camada estão em entry_quality.jsonl. Nenhuma promoção é autorizada.',
                  'O confronto com o banco principal é somente leitura e não o considera verdade-terreno.',
                  'A amostra independente é selecionada por fonte, com seed, antes de usar anomalias como critério.',
                  'Não há transcrição humana de referência; precisão/recall editorial permanecem não medidos.'])
    (out/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery', type=Path, required=True)
    parser.add_argument('--parser-run', required=True)
    parser.add_argument('--parser-db', type=Path, default=Path('data/index_entry_parser/experiments.sqlite'))
    parser.add_argument('--source-db', type=Path, default=Path('data/alphabetical_indices.db'))
    parser.add_argument('--experiment-db', type=Path, default=Path('data/index_entry_parser/conservation_audit.sqlite'))
    parser.add_argument('--workers', type=int, default=20)
    parser.add_argument('--seed', type=int, default=20261105)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
