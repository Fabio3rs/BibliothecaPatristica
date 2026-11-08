"""Reproducible residual triage and conservative, additive literal recovery.

The input population is an existing conservation audit, including pages where
the parser found nothing. No source database is writable and no inference runs.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import random
import re
import sqlite3

from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.entry_parser_experiment import digest, paragraphs, source_span
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing import index_conservation_audit as conservation
from tools.indexing import entry_parser_literal_rows as literal
from tools.indexing import source_quality_issues as source_quality
from tools.indexing import entry_parser_experiment as generic

VERSION = 'residual-recovery-campaign-v1'
TOME = re.compile(r'\b[IVXLCDM]+,\s*\d{1,4}\b')
ALLOWED_KINDS = {'thematic_index', 'index_unspecified', 'onomastic_index', 'lexical_index'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_jsonl(path):
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def visible_text(text):
    return re.sub(r'<[^>]*>', '', text)


def features(text):
    """Observable signatures, never proof of editorial meaning or OCR damage."""
    text = visible_text(text)
    signals = []
    if len(TOME.findall(text)) >= 2:
        signals.append('roman_namespace_arabic_locator')
    if re.search(r'\bvers[.]\s*\d', text, re.I):
        signals.append('explicit_verse_locator')
    if re.search(r'\bcol[.]\s*\d', text, re.I):
        signals.append('explicit_column_locator')
    if len(re.findall(r',\s*[IVXLCDM]+[.;](?:\s|$)', text)) >= 2:
        signals.append('roman_only_locator')
    if re.search(r'\b\d{1,4}\s+[a-d]\b', text):
        signals.append('lowercase_subdivision')
    if re.search(r'\bibid\b', text, re.I):
        signals.append('printed_context_dependency')
    if re.search(r'\w-\s*\n\s*\w', text):
        signals.append('line_hyphenation')
    if text.rstrip().endswith('-'):
        signals.append('open_hyphenated_fragment')
    if re.search(r'\b(?:INDEX|ORDO RERUM|CAPITULA|ARGUMENTUM)\b', text):
        signals.append('editorial_heading_or_boundary')
    if re.search(r'(?:\b[IVXLCDM]+,|\bcol[.]|\bvers[.])\s*$', text):
        signals.append('interrupted_locator')
    if re.search(r'\ufffd|\[\s*(?:illisible|illegible|ilegível)', text, re.I):
        signals.append('explicit_ocr_uncertainty')
    return signals


def primary_family(signals, text):
    for family in ('editorial_heading_or_boundary', 'explicit_verse_locator',
                   'roman_namespace_arabic_locator', 'explicit_column_locator',
                   'roman_only_locator', 'lowercase_subdivision',
                   'printed_context_dependency', 'interrupted_locator',
                   'open_hyphenated_fragment', 'line_hyphenation', 'explicit_ocr_uncertainty'):
        if family in signals:
            return family
    return 'unclassified_numeric_text' if re.search(r'\d', text) else 'unclassified_text'


def residual_pieces(raw, runs):
    """Coverage runs can cross XML tags/blocks; never parse their snippets as rows."""
    for block_number, block in enumerate(blocks(raw)):
        for run in runs:
            if run['category'] != 'residual':
                continue
            a, b = max(block['body_start'], run['span'][0]), min(block['body_end'], run['span'][1])
            if a >= b:
                continue
            text = raw[a:b]
            chars = sum(not c.isspace() for c in visible_text(text))
            if chars:
                yield {'span': [a, b], 'text_sha256': digest(text.encode()),
                       'nonspace_characters': chars, 'block': block_number,
                       'block_kind': block['kind'], 'text': text}


def repeated_passages(raw):
    """Exact word windows across different OCR blocks; repetitions remain hypotheses."""
    seen, matches = {}, []
    for block_number, block in enumerate(blocks(raw)):
        if block['kind'] != 'texto_principal':
            continue
        words = list(re.finditer(r'\w+', raw[block['body_start']:block['body_end']]))
        for n in range(max(0, len(words)-15)):
            window = words[n:n+16]
            a, b = block['body_start']+window[0].start(), block['body_start']+window[-1].end()
            if b-a < 80:
                continue
            signature = tuple(w[0] for w in window)
            previous = seen.get(signature)
            if previous and previous[0] != block_number:
                _, x, y = previous
                if matches and matches[-1]['blocks']==[previous[0],block_number] and (
                        x <= matches[-1]['left'][1] and a <= matches[-1]['right'][1]):
                    matches[-1]['left'][1] = max(y,matches[-1]['left'][1])
                    matches[-1]['right'][1] = max(b,matches[-1]['right'][1])
                else:
                    matches.append({'blocks':[previous[0],block_number], 'left':[x,y], 'right':[a,b]})
            seen[signature] = (block_number,a,b)
    for index, match in enumerate(matches):
        match.update(id=f'repeat-{index}', status='requires_visual_review', automatically_removed=False)
        for side in ('left','right'):
            text = raw[slice(*match[side])]
            match[side+'_text'] = text
            match[side+'_sha256'] = digest(text.encode())
    return matches


def propose_rows(raw, scopes, runs):
    """Reuse the clause grammar only on whole residual paragraphs in compatible scopes.

    A Roman namespace must be printed in each paragraph. All numbers in that
    paragraph must be represented in reference spans, and there must be no tail.
    Proposals retain paragraph identity; clauses are not declared separate lemmas.
    """
    proposals, rejected = [], Counter()
    residual = [r['span'] for r in runs if r['category'] == 'residual']
    for block in blocks(raw):
        if block['kind'] != 'texto_principal':
            continue
        for offset, text in paragraphs(raw[block['body_start']:block['body_end']]):
            a = block['body_start'] + offset
            b = a + len(text)
            if not any(x <= a < b <= y for x, y in residual):
                continue
            owned = [s for s in scopes if s['span'][0] <= a < b <= s['span'][1]]
            if len(owned) != 1 or owned[0]['kind'] not in ALLOWED_KINDS:
                continue
            scope = owned[0]
            if len(TOME.findall(raw[slice(*scope['span'])])) < 3 or not TOME.search(text):
                continue
            if '<' in text or re.search(r'\b(?:vers|cap|lib)[.]\s*\d', text, re.I):
                rejected['mixed_locator_or_markup'] += 1
                continue
            if not text[0].isupper() or text.startswith(('INDEX', 'ORDO RERUM')):
                rejected['open_fragment_or_heading'] += 1
                continue
            groups = literal.attributed_reference_groups(raw, a, b, 'analytic-tome-rows-v1')
            if not groups or raw[groups[-1]['char_end']:b].strip():
                rejected['unparsed_tail'] += 1
                continue
            if any(not any(c.isalpha() for c in g['owner_text_raw']) for g in groups):
                rejected['empty_owner'] += 1
                continue
            if any(not any(g['char_start'] <= a+m.start() < a+m.end() <= g['char_end']
                           for g in groups) for m in re.finditer(r'\d+', text)):
                rejected['number_outside_reference'] += 1
                continue
            if any(re.search(r'\b(?:col|vers|cap|lib|tom)[.]?$', g['owner_text_raw'], re.I)
                   for g in groups):
                rejected['owner_ends_in_locator_label'] += 1
                continue
            if any(re.search(r'[.;]\s+[A-D]\s*$', g['raw']) for g in groups):
                rejected['ambiguous_subdivision_or_next_clause_initial'] += 1
                continue
            if any(re.search(r'\b[IVXLCDM]+\s*$', g['owner_text_raw']) for g in groups):
                rejected['possible_namespace_left_in_owner'] += 1
                continue
            proposals.append({'entry_raw': text, **source_span(raw, a, b),
                              'section_id': scope['section_id'],
                              'unit_kind': 'paragraph_with_literal_clauses',
                              'family': 'roman_namespace_arabic_locator',
                              'reference_groups': groups,
                              'quality': {'region': 'candidate', 'segmentation': 'mechanical_candidate',
                                          'lemma_context': 'unresolved', 'edition_mapping': 'not_inferred',
                                          'destination': 'not_attempted', 'visual': 'not_reviewed'}})
    return proposals, rejected


def fill_unprocessed(raw, meta, scopes, runs, occupied):
    """Try the existing literal grammar on unprocessed pages, with explicit abstentions.

    Full-source offsets are retained. Scope and residual containment are checked
    after parsing; no candidate crossing an editorial boundary is accepted.
    """
    eligible = [s for s in scopes if s['kind'] in ALLOWED_KINDS]
    if not eligible:
        return [], {'attempted':False, 'reason':'unsupported_region_kind', 'rejected':{}}
    if not any(b['kind']=='texto_principal' for b in blocks(raw)):
        return [], {'attempted':False, 'reason':'no_supported_main_text_blocks', 'rejected':{}}
    page = generic.parse_page(raw, source=meta['source'], physical=meta['physical'],
                              volume=meta['volume'], profile='generic-literal-experimental')
    additions, rejected = [], Counter()
    residual = [r['span'] for r in runs if r['category']=='residual']
    for entry in page['entries']:
        a,b=entry['char_start'],entry['char_end']
        owned = [s for s in eligible if s['span'][0]<=a<b<=s['span'][1]]
        if len(owned)!=1 or not any(x<=a<b<=y for x,y in residual):
            rejected['outside_unique_residual_region']+=1
            continue
        if any(a<e['char_end'] and e['char_start']<b for e in occupied+additions):
            rejected['already_proposed_by_specialized_grammar']+=1
            continue
        flags = generic.SEGMENTATION_WARNINGS.intersection(entry['flags'])
        if flags:
            rejected['parser_warnings:'+'+'.join(sorted(flags))]+=1
            continue
        refs=[]
        if entry['entry_type']=='material':
            ref=entry['reference_span']
            end=ref['char_start']
            while end>a and raw[end-1] in ' ,;':end-=1
            owner=raw[a:end]
            if re.search(r'\b(?:[IVXLCDM]+|col|vers|cap|lib|tom)[.]?\s*$',owner):
                rejected['possible_editorial_namespace_in_owner']+=1
                continue
            if re.match(r'\s*[A-D](?:\W|$)',raw[b:b+20]):
                rejected['ambiguous_trailing_subdivision']+=1
                continue
            if re.match(r'\s*(?:sq|sqq|seq|seqq|not|vers)[.]',raw[b:b+20],re.I):
                rejected['unconsumed_reference_qualifier']+=1
                continue
            if re.search(r'\d',owner) or not any(c.isalpha() for c in owner):
                rejected['owner_without_safe_literal_boundary']+=1
                continue
            refs=[{**ref,'raw':raw[ref['char_start']:ref['char_end']],
                   'owner_text_raw':owner,'owner_span':source_span(raw,a,end),
                   'namespace_status':'literal_uninterpreted','inheritance_status':'not_inferred'}]
        additions.append({'entry_raw':entry['entry_raw'],**source_span(raw,a,b),
            'section_id':owned[0]['section_id'], 'unit_kind':'literal_clause_candidate'
             if refs else 'literal_cross_reference_candidate',
            'family':'generic_literal_fallback','reference_groups':refs,
            'parser_flags':entry['flags'],
            'quality':{'region':'candidate','segmentation':'mechanical_candidate',
                       'lemma_context':'unresolved','edition_mapping':'not_inferred',
                       'destination':'not_attempted','visual':'not_reviewed'}})
    return additions, {'attempted':True,'profile':'generic-literal-experimental',
                       'raw_candidates':len(page['entries']),'accepted_candidates':len(additions),
                       'reason':'candidates_staged' if additions else 'no_safe_candidate',
                       'rejected':dict(rejected)}


def evaluate_page(task):
    meta, baseline, parsed = task
    result = {'source': meta['source'], 'volume': meta['volume'], 'physical': meta['physical'],
              'source_sha256': meta['source_sha256'], 'status': 'evaluated',
              'source_quality':meta.get('source_quality', {'source_issue_status':'not_assessed'})}
    result['execution_before'] = ('not_processed' if parsed is None else
                                  'processed_with_candidates' if parsed['entries'] else 'processed_without_candidates')
    try:
        data = Path(meta['source']).read_bytes()
        if digest(data) != meta['source_sha256']:
            return {**result, 'status': 'source_changed'}
        raw = data.decode('utf-8')
    except (OSError, UnicodeError) as exc:
        return {**result, 'status': 'source_error', 'error': str(exc)}
    if baseline['source_sha256'] != meta['source_sha256'] or (
            parsed and parsed['sha256'] != meta['source_sha256']):
        return {**result, 'status': 'baseline_source_mismatch'}
    entries = parsed['entries'] if parsed else []
    before = conservation.coverage(raw, baseline['scopes'], entries)
    if before['characters'] != baseline['characters'] or before['invalid_candidate_ids']:
        return {**result, 'status': 'baseline_accounting_mismatch'}
    queue = []
    for piece in residual_pieces(raw, before['runs']):
        sig = features(piece['text'])
        affected = [s for s in baseline['scopes'] if s['span'][0] < piece['span'][1]
                    and piece['span'][0] < s['span'][1]]
        queue.append({**piece, 'signals': sig, 'family': primary_family(sig, piece['text']),
                      'numeric_tokens': len(list(conservation.NUMERAL.finditer(visible_text(piece['text'])))),
                      'section_ids': sorted({s['section_id'] for s in affected}),
                      'section_kinds': sorted({s['kind'] for s in affected}),
                      'profile': parsed.get('profile') if parsed else 'no_parser_result',
                      'cause_status': 'heuristic_hypothesis'})
    if sum(p['nonspace_characters'] for p in queue) != before['characters']['residual']:
        return {**result, 'status': 'residual_partition_mismatch'}
    additions, rejected = propose_rows(raw, baseline['scopes'], before['runs'])
    fill = {'attempted':False,'reason':'existing_parser_snapshot' if parsed else 'fill_not_requested',
            'rejected':{}}
    if parsed is None and meta.get('fill_missing'):
        extra,fill = fill_unprocessed(raw,meta,baseline['scopes'],before['runs'],additions)
        additions.extend(extra)
    execution_after = ('processed_with_candidates' if entries or additions else
                       'processed_without_candidates' if parsed or fill['attempted'] else
                       'abstained_profile_unknown' if meta.get('fill_missing') else 'not_processed')
    repeats = repeated_passages(raw)
    for entry in additions:
        entry['id'] = digest(encoded([VERSION, result['source'], result['source_sha256'],
                                      entry['char_start'], entry['char_end']]).encode())[:24]
        entry['source_repeat_ids'] = [r['id'] for r in repeats if any(
            entry['char_start'] < r[side][1] and r[side][0] < entry['char_end'] for side in ('left','right'))]
        entry['quality']['source_fidelity'] = ('possible_repeated_ocr_passage' if entry['source_repeat_ids']
                                               else 'not_visually_verified')
        entry['quality']['registered_source_issues'] = result['source_quality']
    after = conservation.coverage(raw, baseline['scopes'], entries + additions)
    for category, count in before['characters'].items():
        if category not in {'consumed_by_candidate', 'residual'} and after['characters'][category] != count:
            raise ValueError(f'Coverage regression in {result["source"]}: {category}')
    gained = after['characters']['consumed_by_candidate'] - before['characters']['consumed_by_candidate']
    if gained != before['characters']['residual'] - after['characters']['residual'] or gained < 0:
        raise ValueError('Non-conservative recovery')
    previous_refs = {tuple(n['span']) for n in before['numeric_tokens'] if n['status'] == 'reference_group'}
    current_refs = {tuple(n['span']) for n in after['numeric_tokens'] if n['status'] == 'reference_group'}
    if previous_refs - current_refs:
        raise ValueError('Previously consumed numeric occurrence disappeared')
    section_stats = []
    for scope in baseline['scopes']:
        section_stats.append({'section_id': scope['section_id'], 'kind': scope['kind'],
                              'span': scope['span'], 'opening_status': scope['opening_status'],
                              'before': conservation.coverage(raw, [scope], entries)['characters'],
                              'after': conservation.coverage(raw, [scope], entries + additions)['characters'],
                              'printed_section_completeness': 'not_verified'})
    return {**result, 'queue': queue, 'additions': additions, 'rejected': dict(rejected),
            'before': before['characters'], 'after': after['characters'],
            'numeric_before': dict(Counter(n['status'] for n in before['numeric_tokens'])),
            'numeric_after': dict(Counter(n['status'] for n in after['numeric_tokens'])),
            'new_reference_numeric_tokens': len(current_refs - previous_refs),
            'new_reference_arabic_tokens': sum(n['status']=='reference_group' and
                tuple(n['span']) not in previous_refs and any(c.isdigit() for c in n['literal'])
                for n in after['numeric_tokens']),
            'gained_characters': gained, 'old_entries': len(entries),
            'execution_after':execution_after, 'fill_missing':fill,
            'old_entries_sha256': digest(encoded(entries).encode()),
            'section_stats': section_stats, 'repeated_passages':repeats, 'regressions': 0}


def anomaly_pattern(findings):
    kinds = sorted({f['kind'] for f in findings})
    numeric = [f for f in findings if f['kind'] == 'numeric_tokens_before_reference_group']
    if numeric:
        kinds.append('arabic_or_subscript_in_owner' if any(f.get('arabic_or_subscript_count', 0)
                                                         for f in numeric) else 'roman_only_in_owner')
    return '+'.join(kinds)


def run(audit, out, workers=4, seed=73, source_issues=None, fill_missing=False):
    audit, out = Path(audit).resolve(), Path(out).resolve()
    if workers < 1:
        raise ValueError('workers must be positive')
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a new output directory')
    manifest = json.loads((audit/'manifest.json').read_text())
    pages = json.loads((audit/'page_summary.json').read_text())
    issues = ({p['source']:p for p in source_quality.inspect_registry(source_issues)}
              if source_issues is not None else {})
    for meta in pages:
        meta['source_quality'] = source_quality.annotate(meta,issues.get(str(Path(meta['source']).resolve())))
        meta['fill_missing'] = fill_missing
    audit_db, parser_db = readonly(manifest['experiment_db']), readonly(manifest['parser_db'])
    tasks = []
    try:
        audit_db.execute('BEGIN'); parser_db.execute('BEGIN')
        for meta in sorted(pages, key=lambda p: (p['volume'], p['physical'], p['source'])):
            row = audit_db.execute('SELECT result_json FROM conservation_pages WHERE run_id=? AND source=?',
                                   (manifest['run_id'], meta['source'])).fetchone()
            if not row:
                raise ValueError('Audit snapshot missing a selected page')
            baseline = json.loads(row[0])
            row = parser_db.execute('''SELECT t.result_json FROM tasks t JOIN run_pages p USING(fingerprint)
                                      WHERE p.run_id=? AND p.source=?''',
                                    (manifest['parser_run'], meta['source'])).fetchone()
            parsed = json.loads(row[0]).get('page') if row else None
            tasks.append((meta, baseline, parsed))
    finally:
        audit_db.close(); parser_db.close()
    out.mkdir(parents=True, exist_ok=True)
    config = {'version': VERSION, 'audit': str(audit), 'baseline_run': manifest['run_id'],
              'parser_run': manifest['parser_run'], 'workers': workers, 'seed': seed,
              'inputs': {name: digest((audit/name).read_bytes()) for name in
                         ('manifest.json', 'page_summary.json', 'entry_quality.jsonl')},
              'code': {Path(p).name: digest(Path(p).read_bytes()) for p in
                       (__file__, literal.__file__, conservation.__file__, source_quality.__file__,generic.__file__)},
              'fill_missing':fill_missing,
              'source_issues':str(source_issues) if source_issues else None,
              'source_issues_sha256':digest(Path(source_issues).read_bytes()) if source_issues else None}
    (out/'manifest.json').write_text(encoded(config)+'\n')
    db = sqlite3.connect(out/'recovery.sqlite')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''CREATE TABLE pages(source TEXT PRIMARY KEY,result_json TEXT NOT NULL);
        CREATE TABLE residuals(source TEXT,ordinal INTEGER,family TEXT,start INTEGER,end INTEGER,text TEXT,
          evidence_json TEXT,PRIMARY KEY(source,ordinal),FOREIGN KEY(source) REFERENCES pages(source));
        CREATE TABLE units(id TEXT PRIMARY KEY,source TEXT,start INTEGER,end INTEGER,entry_raw TEXT,
          evidence_json TEXT,FOREIGN KEY(source) REFERENCES pages(source));
        CREATE TABLE clauses(unit_id TEXT,ordinal INTEGER,owner_raw TEXT,reference_raw TEXT,
          evidence_json TEXT,PRIMARY KEY(unit_id,ordinal),FOREIGN KEY(unit_id) REFERENCES units(id));''')
    ranking, by_volume, patterns, by_profile, by_collection = {}, {}, {}, {}, {}
    results, samples, errors, by_section = [], defaultdict(list), [], {}
    streams = {name: (out/(name+'.jsonl')).open('w') for name in
               ('residual_queue', 'proposals', 'page_comparison', 'anomaly_queue', 'repeated_passages', 'execution_inventory')}
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(evaluate_page, tasks):
                source = result['source']
                meta = {k: result[k] for k in ('source', 'source_sha256', 'volume', 'physical')}
                streams['execution_inventory'].write(encoded({**meta,
                    'before':result['execution_before'],
                    'after':result.get('execution_after','error'),
                    'reason':result.get('fill_missing',{}).get('reason',result['status']),
                    'source_quality':result['source_quality'],
                    'old_candidates':result.get('old_entries'),
                    'new_candidates':len(result.get('additions',[])),
                    'parser_attempt':result.get('fill_missing')})+'\n')
                db.execute('INSERT INTO pages VALUES(?,?)', (source, encoded(result)))
                if result['status'] != 'evaluated':
                    errors.append(result)
                    db.commit()
                    continue
                for n, item in enumerate(result['queue']):
                    recovered = 0
                    for entry in result['additions']:
                        a,b=max(item['span'][0],entry['char_start']),min(item['span'][1],entry['char_end'])
                        if a < b:
                            recovered += sum(not c.isspace() for c in visible_text(
                                entry['entry_raw'][a-entry['char_start']:b-entry['char_start']]))
                    item['newly_structured_characters'] = recovered
                    row = {**meta, **item}
                    streams['residual_queue'].write(encoded(row)+'\n')
                    db.execute('INSERT INTO residuals VALUES(?,?,?,?,?,?,?)',
                               (source, n, item['family'], *item['span'], item['text'], encoded(row)))
                    for target, key in ((ranking, item['family']), (by_volume, result['volume']),
                                        (by_profile,item['profile']), (by_collection,result['volume'][:2])):
                        stats = target.setdefault(key, Counter())
                        stats.update(records=1, nonspace_characters=item['nonspace_characters'],
                                     numeric_tokens=item['numeric_tokens'], newly_structured_characters=recovered)
                    samples[(result['volume'][:2], item['family'])].append(
                        {**meta, 'span': item['span'], 'text_sha256': item['text_sha256'],
                         'snippet': item['text'][:800], 'family': item['family'], 'verdict': None})
                for item in result['additions']:
                    streams['proposals'].write(encoded({**meta, **item})+'\n')
                    db.execute('INSERT INTO units VALUES(?,?,?,?,?,?)',
                               (item['id'], source, item['char_start'], item['char_end'], item['entry_raw'], encoded(item)))
                    for n, clause in enumerate(item['reference_groups']):
                        db.execute('INSERT INTO clauses VALUES(?,?,?,?,?)',
                                   (item['id'], n, clause['owner_text_raw'], clause['raw'], encoded(clause)))
                for repeated in result['repeated_passages']:
                    streams['repeated_passages'].write(encoded({**meta, **repeated})+'\n')
                compact = {k: v for k, v in result.items() if k not in {'queue', 'additions','repeated_passages'}}
                streams['page_comparison'].write(encoded(compact)+'\n')
                for section in result['section_stats']:
                    total = by_section.setdefault(section['section_id'], {'kind': section['kind'],
                        'volume': result['volume'], 'opening_status': section['opening_status'],
                        'pages': 0, 'before': Counter(), 'after': Counter()})
                    total['pages'] += 1
                    for label in ('before', 'after'):
                        total[label].update({k:v for k,v in section[label].items() if k!='outside_region'})
                results.append(result)
                db.commit()
                if len(results) % 50 == 0:
                    print(encoded({'progress': True, 'pages_evaluated': len(results),
                                   'proposed_units': sum(len(r['additions']) for r in results)}), flush=True)
        good_sources = {r['source'] for r in results}
        for entry in read_jsonl(audit/'entry_quality.jsonl'):
            if not entry['findings'] or entry['source'] not in good_sources:
                continue
            pattern = anomaly_pattern(entry['findings'])
            stats = patterns.setdefault(pattern, Counter())
            stats.update(candidates=1, target_cohort=int(entry['target_cohort']))
            streams['anomaly_queue'].write(encoded({**entry, 'pattern': pattern,
                'diagnosis': 'requires_evidence_review', 'confirmed_segmentation_error': None})+'\n')
    finally:
        for stream in streams.values(): stream.close()
        db.close()
    rng = random.Random(seed)
    selected = [rng.choice(rows) for _, rows in sorted(samples.items())]
    (out/'review_sample.json').write_text(encoded({'seed':seed, 'sampling_unit':'residual_block_piece',
        'is_independent_ground_truth':False, 'items':selected})+'\n')
    (out/'section_comparison.json').write_text(encoded(by_section)+'\n')
    summary = {'version':VERSION, 'pages_selected':len(tasks), 'pages_evaluated':len(results),
               'volumes_evaluated':len({r['volume'] for r in results}), 'errors':errors,
               'family_ranking': sorted([{'family':k, **v} for k,v in ranking.items()],
                                        key=lambda r: -r['nonspace_characters']),
               'volume_ranking': sorted([{'volume':k, **v} for k,v in by_volume.items()],
                                        key=lambda r: -r['nonspace_characters']),
               'anomaly_patterns': sorted([{'pattern':k, **v} for k,v in patterns.items()],
                                         key=lambda r: -r['candidates']),
               'proposed_units':sum(len(r['additions']) for r in results),
               'proposed_clauses':sum(len(e['reference_groups']) for r in results for e in r['additions']),
               'newly_structured_characters':sum(r['gained_characters'] for r in results),
               'new_reference_numeric_tokens':sum(r['new_reference_numeric_tokens'] for r in results),
               'new_reference_arabic_tokens':sum(r['new_reference_arabic_tokens'] for r in results),
               'repeated_passages':sum(len(r['repeated_passages']) for r in results),
               'proposed_units_with_repeat_warning':sum(bool(e['source_repeat_ids']) for r in results for e in r['additions']),
               'residual_characters_before':sum(r['before']['residual'] for r in results),
               'residual_characters_after':sum(r['after']['residual'] for r in results),
               'rejection_reasons':dict(sum((Counter(r['rejected']) for r in results), Counter())),
               'mechanical_regressions':sum(r['regressions'] for r in results),
               'source_issue_statuses':dict(Counter(r['source_quality']['source_issue_status'] for r in results)),
               'execution_before':dict(Counter(r['execution_before'] for r in results+errors)),
               'execution_after':dict(Counter(r.get('execution_after','error') for r in results+errors)),
               'missing_pages_parser_attempted':sum(r['fill_missing']['attempted'] for r in results),
               'by_profile':by_profile, 'by_collection':by_collection,
               'visually_verified_units':None, 'printed_reference_recall':None,
               'production_updates':0, 'inference_calls':0, 'import_ready':False}
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    for filename, rows in [('family_ranking',summary['family_ranking']),
                           ('volume_ranking',summary['volume_ranking']),
                           ('anomaly_patterns',summary['anomaly_patterns'])]:
        with (out/(filename+'.csv')).open('w') as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    report = ['# Campanha mecânica de resíduos', '',
        f"{len(results)}/{len(tasks)} páginas avaliadas; {len(errors)} fontes incompatíveis/erros.",
        f"{summary['proposed_units']} parágrafos candidatos, {summary['proposed_clauses']} cláusulas.",
        f"Caracteres residuais: {summary['residual_characters_before']} → {summary['residual_characters_after']}.",
        '', 'Propostas aditivas em staging. Nenhuma promoção, interpretação de edição ou destino.',
        'Conservação de spans não comprova leitura visual, limites de verbete ou atribuição editorial.',
        'As famílias são hipóteses sintáticas; o ranking é de material observado, não ganho garantido.',
        'As seções são envelopes candidatos da auditoria; completude impressa permanece não medida.',
        '', '| Família primária | Fragmentos | Caracteres não brancos | Tokens numéricos | Novos caracteres estruturados |',
        '|---|---:|---:|---:|---:|']
    report += [f"| {r['family']} | {r['records']} | {r['nonspace_characters']} | {r['numeric_tokens']} | {r['newly_structured_characters']} |"
               for r in summary['family_ranking']]
    (out/'report.md').write_text('\n'.join(report)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--seed', type=int, default=73)
    parser.add_argument('--source-issues',type=Path,default=source_quality.DEFAULT_REGISTRY)
    parser.add_argument('--fill-missing',action='store_true',
                        help='Try the existing generic grammar on pages without a parser snapshot, retaining abstentions.')
    args = parser.parse_args()
    result = run(args.audit, args.out, args.workers, args.seed, args.source_issues,args.fill_missing)
    print(json.dumps({k:v for k,v in result.items() if k not in
                     {'family_ranking','volume_ranking','anomaly_patterns'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
