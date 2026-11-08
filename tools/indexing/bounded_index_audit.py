"""Audit index readings against lexical evidence in editorially bounded pages."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import statistics

from tools.corpus_utils import page_number
from tools.indexing.bounded_lexical_match import match_text
from tools.indexing.scoped_body_numbering import load_series, locate, reference_numbers

VERSION = 'bounded-index-lexical-v1'
PRIORITY = {'exact': 0, 'normalized': 1, 'fuzzy': 2, 'partial': 3, 'absent': 4}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def expected_range(number, pages, supported, profile, max_number_gap=8, max_pages=6):
    direct, _ = locate([number], pages, supported, profile)
    if direct:
        return {'basis': 'observed_number', 'number': number,
                'pages': sorted({c['physical'] for c in direct}),
                'markers': [c['evidence'] for c in direct]}
    lower = max((n for n, p in supported if n < number), default=None)
    upper = min((n for n, p in supported if n > number), default=None)
    result = {'basis': 'no_bounded_range', 'number': number, 'pages': [],
              'lower_number': lower, 'upper_number': upper}
    if lower is None or upper is None or upper - lower > max_number_gap:
        return result
    pairs = sorted((a, b) for n, a in supported if n == lower
                   for m, b in supported if m == upper and 0 <= b - a < max_pages)
    chosen = set()
    for a, b in pairs:
        first = max(profile['body_pages'][0], a - profile.get('previous_page_context', 0))
        chosen.update(range(first, b + 1))
    if not chosen or len(chosen) > max_pages:
        result['reason'] = 'ambiguous_or_wide_brackets'
        return result
    result.update(basis='bracketed_numbering_range', pages=sorted(chosen & pages.keys()),
                  physical_bracket_alternatives=pairs, missing_files=sorted(chosen - pages.keys()),
                  lower_evidence=[{'physical': p, 'file': pages[p]['file'], 'sequence': supported[n,p]}
                                  for n,p in sorted(supported) if n == lower],
                  upper_evidence=[{'physical': p, 'file': pages[p]['file'], 'sequence': supported[n,p]}
                                  for n,p in sorted(supported) if n == upper],
                  uncertainty='Missing number bracketed, not observed in the proposed destination.')
    return result


def query_variants(row, raw):
    variants = [{'kind': 'current_owner', 'text': row['owner_raw']}]
    flags = []
    owner = row['owner_raw']
    if re.search(r'\d', owner):
        flags.append('numeric_material_inside_owner')
    span, ref_span = row.get('owner_span'), row.get('reference_span')
    tail = re.fullmatch(r'(.+?)\s+(\d+(?:\s*[,;]\s*\d+)*)\s*', owner, re.S)
    if (tail and span and ref_span and span[1] <= ref_span[0]
            and re.fullmatch(r'\s*[,;]\s*', raw[span[1]:ref_span[0]])):
        variants.append({'kind': 'possible_embedded_reference_removed', 'text': tail[1],
                         'possible_reference_numbers': re.findall(r'\d+', tail[2]),
                         'canonical_repair': False})
        flags.append('possible_reference_list_swallowed_by_owner')
    if len(owner) > 600:
        flags.append('long_owner_possible_concatenation')
    return variants, flags


def numeric_alternative(check):
    """Aligned flanks include punctuation outside the original numeric field."""
    quote = check.get('alternative_quote', '').strip(' \n\r\t,;.')
    if (check.get('field') != 'reference' or check.get('status') != 'numeric_reading_disagreement'
            or check.get('page_identity') in {'replacement_source_needs_review','excluded_removed_tome_xxi'}
            or not re.fullmatch(r'\d+(?:\s*[,;]\s*\d+)*', quote)):
        return None
    return {'kind': 'historical_reference_reading', 'quote': check['alternative_quote'],
            'numbers': list(dict.fromkeys(map(int, re.findall(r'\d+', quote)))),
            'version_id': check['version_id'], 'model': check.get('model')}


def relocate_row(row, raw):
    """Relocate an intact owner and its adjacent reference, never a naked number."""
    from tools.indexing.index_version_evidence import resolve_quote
    result = dict(row)
    old_owner, old_ref = row['owner_span'], row['reference_span']
    owner = resolve_quote(raw, row['owner_raw'], old_owner)
    if not owner:
        return result
    shift = owner[0] - old_owner[0]
    reference = [old_ref[0] + shift, old_ref[1] + shift]
    if reference[0] >= owner[1] and raw[reference[0]:reference[1]] == row['reference_raw']:
        result.update(owner_span=owner, reference_span=reference,
                      source_span_relocated=bool(shift),
                      original_owner_span=old_owner, original_reference_span=old_ref)
    return result


def reading_key(reading):
    evidence = reading['lexical']
    hit = evidence['matches'][0] if evidence['matches'] else {}
    width = hit.get('end',0) - hit.get('start',0) if evidence['method']=='partial' else 0
    return (PRIORITY[evidence['method']], -evidence['coverage'], -hit.get('score',0), width,
            reading['query_variant']['kind'], reading['document_kind'])


def write_report(out):
    rows = [json.loads(line) for line in (out/'results.jsonl').open()]
    pending = [r for r in rows if r['cohort']=='unlinked_input']
    counts, divergences, flags = Counter(), Counter(), Counter()
    widths = []
    for row in pending:
        current_pages = {p for window in row['ranges']
                         if window['reference_reading']['kind']=='current_reference' for p in window['pages']}
        current_hits = [reading for c in row['candidates'] if c['physical'] in current_pages
                        for reading in c['readings'] if reading['document_kind']=='current_ocr'
                        and reading['query_variant']['kind']=='current_owner'
                        and reading['lexical']['method']!='absent']
        any_hit = any(c['method']!='absent' for c in row['candidates'])
        counts['records'] += 1
        counts['current_reference_ranges'] += bool(current_pages)
        counts['any_reference_ranges'] += bool(row['candidates'])
        counts['base_current_lexical'] += bool(current_hits)
        counts['any_lexical'] += any_hit
        counts['added_lexical_records'] += any_hit and not current_hits
        counts[row['status']] += 1
        flags.update(row['flags'])
        for status in ('numeric_reading_disagreement','lexical_reading_disagreement'):
            divergences[status] += any(c['status']==status for c in row['index_version_checks'])
        if row['candidates']:
            widths.append(len(row['candidates']))
    diagnostics = dict(counts=dict(counts), index_divergence_records=dict(divergences), flags=dict(flags),
        candidate_files=dict(min=min(widths,default=0),median=statistics.median(widths) if widths else 0,
                             max=max(widths,default=0),over_six=sum(n>6 for n in widths)))
    (out/'diagnostics.json').write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2)+'\n')
    lines = ['# Busca lexical em faixas editoriais', '',
        f'{len(pending)} registros sem candidatos na entrada; {len(rows)-len(pending)} comparações já vinculadas.',
        'Amostra delimitada a perfis conhecidos; não extrapolar ao corpus inteiro.', '',
        '| Resultado dos registros sem link | Registros |','| --- | ---: |']
    for key,label in [('any_reference_ranges','Com arquivos na faixa esperada'),
                      ('range_exact','Expressão inteira exata'),('range_normalized','Expressão inteira normalizada'),
                      ('range_fuzzy','Expressão inteira fuzzy'),('range_partial','Somente termos informativos'),
                      ('range_absent','Faixa disponível sem correspondência lexical'),
                      ('no_bounded_range','Sem faixa delimitada'),('notation_requires_other_grammar','Outra gramática necessária')]:
        lines.append(f'| {label} | {counts[key]} |')
    lines += ['', f"Alternativas acrescentaram evidência lexical a {counts['added_lexical_records']} registros.",
        f"Número de arquivos por registro com faixa: mediana {diagnostics['candidate_files']['median']}, máximo {diagnostics['candidate_files']['max']}.",
        'Cada faixa numérica é limitada; referências múltiplas ou leituras alternativas podem produzir uma união maior.', '',
        'Correspondência parcial e divergência OCR não certificam erro, identidade ou destino. Não há referência humana independente para medir Precision/Recall.',
        'OCRs, bancos fonte e catálogo consolidado permanecem inalterados. O SQLite desta auditoria contém resultados pesquisáveis e suas evidências.', '',
        '## Exemplos de candidatos', '']
    for status in ('range_exact','range_fuzzy','range_partial','range_absent'):
        for row in [r for r in pending if r['status']==status][:2]:
            candidate=row['candidates'][0]
            hits=candidate['best_reading']['lexical']['matches']
            snippet=hits[0]['snippet'] if hits else '(sem correspondência lexical)'
            lines += [f"- `{row['item_id']}` — {row['owner_raw'].replace(chr(10),' ')} — referência `{row['reference_raw']}`.",
                      f"  Arquivo: `{candidate['file']}`; método: `{status}`.",
                      '  Trecho: '+ ' '.join(snippet.split())[:500], '']
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    return diagnostics


def select_rows(rows, controls, seed, limit, include_items=()):
    selected = []
    for profile in sorted({r['profile'] for r in rows}):
        group = sorted((r for r in rows if r['profile'] == profile), key=lambda r: r['item_id'])
        pending = [r for r in group if not r['candidates']]
        if limit:
            pending = random.Random(seed).sample(pending, min(limit, len(pending)))
        selected.extend((r, 'unlinked_input') for r in pending)
        linked = [r for r in group if r['candidates']]
        selected.extend((r, 'linked_comparison') for r in random.Random(seed).sample(linked, min(controls, len(linked))))
    included = {r['item_id'] for r, cohort in selected}
    for row in rows:
        if row['item_id'] in include_items and row['item_id'] not in included:
            selected.append((row, 'targeted_comparison'))
            included.add(row['item_id'])
    if set(include_items) - included:
        raise ValueError('Explicit comparison item absent from input')
    return sorted(selected, key=lambda pair: (pair[0]['profile'], pair[0]['item_id']))


def run(args):
    from tools.indexing.index_version_evidence import audit_local_readings, read_page_versions

    rows = [json.loads(line) for line in args.input.open()]
    selected = select_rows(rows, args.controls, args.seed, args.limit, args.include_item)
    profiles = {p['id']: p for p in json.loads(args.plan.read_text())['profiles']}
    args.out.mkdir(parents=True, exist_ok=False)
    raw_sources = {r['source']: Path(r['source']).read_text() for r, cohort in selected}
    selected = [(relocate_row(r, raw_sources[r['source']]), cohort) for r,cohort in selected]
    history = defaultdict(list)
    if args.versions_db:
        grouped = defaultdict(list)
        for row, cohort in selected:
            grouped[row['source']].append(row)
        with (args.out / 'index_history.jsonl').open('w') as handle:
            for source, group in sorted(grouped.items()):
                profile = profiles[group[0]['profile']]
                regions = [dict(id=r['item_id'], owner_quote=r['owner_raw'], owner_span=r['owner_span'],
                                reference_quote=r['reference_raw'], reference_span=r['reference_span']) for r in group]
                result = audit_local_readings(source, profile['volume'], page_number(Path(source)),
                                             regions, args.versions_db, max_versions=args.max_versions)
                handle.write(encoded({'source': source, 'result': result}) + '\n')
                for check in result.get('checks', []):
                    history[check['id']].append(check)
                print(encoded({'index_history_pages': len(grouped), 'source': source}), flush=True)
    totals, by_profile, history_states, improvements = Counter(), defaultdict(Counter), Counter(), Counter()
    target_inventory = {}

    @lru_cache(maxsize=128)
    def target_versions(path, volume, physical):
        result = read_page_versions(path, volume, physical, args.versions_db, max_versions=args.max_versions)
        target_inventory[path] = {**result, 'versions': [
            {k:v for k,v in version.items() if k != 'text_content'} for version in result['versions']]}
        return [v for v in result['versions'] if v.get('text_content') and not v.get('text_matches_current')]
    matrices = {}
    for key in sorted({r['profile'] for r, cohort in selected}):
        matrices[key] = load_series(profiles[key], args.root.resolve())
    with closing(sqlite3.connect(args.out / 'audit.sqlite')) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''CREATE TABLE evidence(item_id TEXT PRIMARY KEY,profile TEXT,cohort TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,method TEXT,evidence_json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        with (args.out / 'results.jsonl').open('w') as output:
            for index, (row, cohort) in enumerate(selected, 1):
                profile = profiles[row['profile']]
                pages, supported, inventory = matrices[row['profile']]
                raw = raw_sources[row['source']]
                meta = {k: row[k] for k in ('owner_raw', 'owner_span', 'reference_raw', 'reference_span')}
                numbers, source_issue = reference_numbers({}, meta, raw)
                variants, flags = query_variants(row, raw)
                checks = history[row['item_id']]
                for check in checks:
                    history_states[check['status']] += 1
                    if (check['field'] == 'owner' and check['status'] == 'lexical_reading_disagreement'
                            and check.get('alternative_quote') and check.get('page_identity') not in
                            {'replacement_source_needs_review','excluded_removed_tome_xxi'}):
                        variants.append({'kind': 'historical_owner_reading', 'text': check['alternative_quote'],
                                         'version_id': check['version_id'], 'model': check.get('model'),
                                         'canonical_repair': False})
                variants = list({(v['kind'],v['text']):v for v in variants}.values())
                reference_readings = [{'kind': 'current_reference', 'quote': row['reference_raw'], 'numbers': numbers}]
                for check in checks:
                    alternative = numeric_alternative(check)
                    if alternative:
                        reference_readings.append(alternative)
                ranges = []
                if not source_issue:
                    for reading in reference_readings:
                        for number in reading['numbers']:
                            window = expected_range(number, pages, supported, profile, args.max_number_gap, args.max_pages)
                            window['reference_reading'] = reading
                            ranges.append(window)
                candidates = []
                for physical in sorted({p for r in ranges for p in r['pages']}):
                    page = pages[physical]
                    readings = []
                    for variant in variants:
                        evidence = match_text(variant['text'], page['raw'], max_matches=3)
                        readings.append({'query_variant': variant, 'document_kind': 'current_ocr', 'lexical': evidence})
                    current_method = min((r['lexical']['method'] for r in readings), key=PRIORITY.get)
                    if args.target_history and current_method in {'absent', 'partial', 'fuzzy'}:
                        for version in target_versions(page['file'], profile['volume'], physical):
                            for variant in variants:
                                evidence = match_text(variant['text'], version['text_content'], max_matches=3)
                                if evidence['method'] != 'absent':
                                    readings.append({'query_variant': variant, 'document_kind': 'historical_ocr',
                                        'document_version': {k:v for k,v in version.items() if k != 'text_content'},
                                        'lexical': evidence})
                    best = min(readings, key=reading_key)
                    candidates.append({'file': page['file'], 'physical': physical, 'sha256': page['sha256'],
                        'method': best['lexical']['method'], 'best_reading': best, 'readings': readings,
                        'current_best_method': current_method,
                        'supporting_range_indices': [i for i,r in enumerate(ranges) if physical in r['pages']],
                        'localization_level': 'ocr_page_file', 'passage_verified': False, 'certified': False})
                candidates.sort(key=lambda c: (reading_key(c['best_reading']), c['physical']))
                status = source_issue or ('range_' + candidates[0]['method'] if candidates else 'no_bounded_range')
                totals[cohort + ':' + status] += 1
                by_profile[row['profile']][cohort + ':' + status] += 1
                if candidates:
                    best = candidates[0]['best_reading']
                    improvements[cohort + ':best_query:' + best['query_variant']['kind']] += 1
                    improvements[cohort + ':best_document:' + best['document_kind']] += 1
                evidence = dict(item_id=row['item_id'], profile=row['profile'], cohort=cohort,
                    source=row['source'], source_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                    owner_raw=row['owner_raw'], reference_raw=row['reference_raw'], flags=flags,
                    owner_span=row['owner_span'], reference_span=row['reference_span'],
                    source_span_relocated=row.get('source_span_relocated',False),
                    original_owner_span=row.get('original_owner_span',row['owner_span']),
                    original_reference_span=row.get('original_reference_span',row['reference_span']),
                    status=status, ranges=ranges, index_version_checks=checks, candidates=candidates)
                for rank, candidate in enumerate(candidates, 1):
                    candidate['rank'] = rank
                    db.execute('INSERT INTO candidates VALUES(?,?,?,?,?)',
                               (row['item_id'],candidate['file'],rank,candidate['method'],encoded(candidate)))
                db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',
                           (row['item_id'],row['profile'],cohort,status,encoded(evidence)))
                output.write(encoded(evidence) + '\n')
                if index % 50 == 0:
                    db.commit()
                    print(encoded({'completed':index,'total':len(selected),'statuses':dict(totals)}), flush=True)
        summary = dict(version=VERSION, selected=len(selected), seed=args.seed, controls_per_profile=args.controls,
            input=str(args.input.resolve()), input_sha256=digest(args.input), plan=str(args.plan.resolve()),
            plan_sha256=digest(args.plan), max_number_gap=args.max_number_gap, max_pages=args.max_pages,
            versions_db=str(args.versions_db.resolve()) if args.versions_db else None,
            max_versions=args.max_versions, statuses=dict(totals), by_profile={k:dict(v) for k,v in by_profile.items()},
            target_history=args.target_history, include_items=args.include_item, best_evidence_sources=dict(improvements),
            index_history_states=dict(history_states), canonical_updates=0, catalog_updates=0, inference_calls=0,
            note='Lexical evidence in bounded ranges; comparison sample is not human gold or a precision estimate.')
        summary['implementation_sha256'] = {name:digest(Path(__file__).with_name(name))
            for name in ('bounded_index_audit.py','bounded_lexical_match.py','index_version_evidence.py','scoped_body_numbering.py')}
        summary['logical_sha256'] = hashlib.sha256('\n'.join(r[0] for r in db.execute(
            'SELECT evidence_json FROM evidence ORDER BY item_id')).encode()).hexdigest()
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary',encoded(summary)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with (args.out/'candidates.csv').open('w') as handle:
            writer = csv.writer(handle)
            writer.writerow(['item_id','file','rank','method'])
            writer.writerows(db.execute('SELECT item_id,file,rank,method FROM candidates ORDER BY item_id,rank'))
    (args.out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (args.out/'target_history_inventory.json').write_text(encoded(target_inventory)+'\n')
    write_report(args.out)
    print(encoded(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='results.jsonl from recover_scoped_body_links.py')
    parser.add_argument('--report-only', action='store_true', help='Rebuild Markdown/diagnostics from an existing output directory')
    parser.add_argument('--plan', type=Path, default=Path('docs/fixtures/scoped_body_numbering.json'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--versions-db', type=Path, help='Optional readonly history lookup for index readings')
    parser.add_argument('--target-history', action='store_true', help='Search historical destination OCR when current full-label match is unavailable')
    parser.add_argument('--include-item', action='append', default=[], help='Explicit case review ID, separate from random comparisons')
    parser.add_argument('--max-versions', type=int, default=5)
    parser.add_argument('--max-number-gap', type=int, default=8)
    parser.add_argument('--max-pages', type=int, default=6)
    parser.add_argument('--controls', type=int, default=30, help='Linked comparison records per profile; not gold')
    parser.add_argument('--limit', type=int, default=0, help='Unlinked sample per profile; zero selects all')
    parser.add_argument('--seed', type=int, default=711)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.report_only:
        write_report(args.out)
        return
    if not args.input:
        parser.error('--input is required except with --report-only')
    if min(args.max_versions,args.max_number_gap,args.max_pages) < 1 or min(args.controls,args.limit) < 0:
        parser.error('Bounds must be positive and sample sizes nonnegative')
    if args.target_history and not args.versions_db:
        parser.error('--target-history requires --versions-db')
    run(args)
