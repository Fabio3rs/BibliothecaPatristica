"""Audit all volumes, including unselected sections of represented volumes.

Catalog and historical inputs are read-only. Page/character accounting describes
the available OCR, never recall against print. Parser probes are unreviewed and
are not imported. Historical envelopes select pages, not trusted current offsets.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from tools.corpus_utils import page_files
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.editorial_ocr_blocks import blocks, empty_reason
from tools.indexing.editorial_regions_experiment import local_facts, fold, local_fingerprint
from tools.indexing.regional_section_provenance import title_at
from tools.indexing.unrepresented_region_recovery import INDEX_KINDS, valid_entry
from scripts.audit_index_missing_volumes import heading_family

VERSION = 'catalog-coverage-audit-v1'
TITLE_CUE = re.compile(r'INDEX|INDICES|TABLE|TABULA|LEXICON|GLOSSARIUM|ONOMASTIC|NOMINA|ΠΙΝΑΞ|Πίναξ', re.I)
INDEPENDENT_TITLE = re.compile(r'^(?:\d+\s+)?(?:INDEX|INDICES|TABLE|TABULA|LEXICON|GLOSSARIUM|ONOMASTICON|NOMINA|ΠΙΝΑΞ)\b')
NUMBER = re.compile(r'(?<!\w)\d+(?!\w)')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def union(spans):
    result = []
    for a, b in sorted(set(map(tuple, spans))):
        if a >= b:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def uncovered(a, b, spans):
    cursor = a
    for x, y in spans:
        if y <= cursor or x >= b:
            continue
        if x > cursor:
            yield cursor, x
        cursor = max(cursor, min(b, y))
    if cursor < b:
        yield cursor, b


def page_signals(raw):
    if not TITLE_CUE.search(raw):
        return [], []
    facts = local_facts(raw)
    headings, weak = [], []
    for event in facts['events']:
        if not event['kind'] or event['role'] not in {'section_start', 'running_header', 'catalog_pointer', 'mention'}:
            continue
        title = title_at(raw, facts['rows'], event)
        family = heading_family(title['title_raw'], event['kind'])
        if family not in INDEX_KINDS and not family.endswith('_index'):
            continue
        headings.append({**title, 'kind': family, 'role': event['role'], 'reason': event['reason'],
                         'eligible': event['role'] in {'section_start', 'running_header'}})
    known = [h['title_span'] for h in headings]
    for row in facts['rows']:
        text = fold(row['quote'])
        letters = [c for c in row['quote'] if c.isalpha()]
        if (len(text) <= 240 and INDEPENDENT_TITLE.match(text) and letters
                and sum(c.isupper() for c in letters) / len(letters) > .65
                and not any(a <= row['span'][0] < b for a, b in known)):
            # Deliberately broader than the detector; may be contents or prose.
            weak.append({'raw': row['quote'], 'span': row['span'], 'block_type': row['block_type']})
    return headings, weak


def account(raw, entries):
    checksum = hashlib.sha256(raw.encode()).hexdigest()
    consumed, refs, problems = [], [], Counter()
    for e in entries:
        span = e['span']
        refspan, refraw = e['ref_span'], e['ref_raw']
        literal_ok = bool(refspan and refraw and raw[slice(*refspan)] == refraw)
        if literal_ok:
            refs.append(refspan)
        elif refspan:
            problems['reference_span_not_literal'] += 1
        # A changed hash is not a discovery gate. It only makes an unverifiable
        # historical envelope unsuitable for exact current character accounting.
        if span and 0 <= span[0] < span[1] <= len(raw):
            if e['sha256'] == checksum:
                consumed.append(span)
            elif e.get('entry_raw') and raw[slice(*span)] == e['entry_raw']:
                consumed.append(span)
            else:
                problems['envelope_not_revalidated'] += 1
        else:
            problems['no_valid_envelope'] += 1
    consumed, refs = union(consumed), union(refs)
    counts, residue = Counter(), []
    bs = blocks(raw)
    for block in bs:
        a, b = block['body_start'], block['body_end']
        nonspace = sum(not c.isspace() for c in raw[a:b])
        remaining = list(uncovered(a, b, consumed))
        residual_chars = sum(sum(not c.isspace() for c in raw[x:y]) for x, y in remaining)
        category = 'body' if block['kind'] == 'texto_principal' else 'other_blocks'
        counts[category + '_chars'] += nonspace
        counts[category + '_registered_span_chars'] += nonspace - residual_chars
        counts[category + '_residual_chars'] += residual_chars
        nums = [m for m in NUMBER.finditer(raw, a, b)]
        counts[category + '_numeric_occurrences'] += len(nums)
        counts[category + '_numbers_outside_reference_spans'] += sum(
            not any(x <= m.start() and m.end() <= y for x, y in refs) for m in nums)
        for x, y in remaining:
            n = len(NUMBER.findall(raw[x:y]))
            if n:
                residue.append({'span': [x, y], 'kind': block['kind'], 'numeric_occurrences': n,
                                'snippet': raw[x:y][:450]})
    assert counts['body_chars'] == counts['body_registered_span_chars'] + counts['body_residual_chars']
    return dict(counts), dict(problems), sorted(residue, key=lambda r: (-r['numeric_occurrences'], r['span']))[:4]


def probe(raw, source, volume, physical, heading):
    from tools.indexing.missing_latin_region_parser import parse_region_page as latin
    from tools.indexing.missing_po_region_parser import parse_region_page as po
    parser = po if volume.startswith('PO') else latin
    sid = 'audit:' + volume + ':' + str(physical)
    kind = heading.get('kind', 'index_unspecified')
    result = parser(raw, source=source, volume=volume, physical=physical,
                    scopes=[{'span': [0, len(raw)], 'section_id': sid, 'kind': kind}],
                    section_context={sid: {'kind': kind, 'title': heading.get('title_raw', '')}})
    valid = [e for e in result['entries'] if valid_entry(raw, e)]
    return {'parser': parser.__module__, 'valid_literal_proposals': len(valid),
            'invalid_literal_proposals': len(result['entries']) - len(valid),
            'residual_reasons': dict(Counter(r['reason'] for r in result.get('residuals', []))),
            'examples': [{k: e.get(k) for k in ('owner_raw', 'reference_raw', 'entry_span', 'reference_span', 'reference_namespace')}
                         for e in valid[:4]],
            'warning': 'Whole-page diagnostic probe; ownership/boundaries not validated; not imported.'}


def scan_volume(job):
    volume, root, catalog, regions, attempts, probe_limit = job
    inventory = page_files(Path(root) / volume / 'text', suffixes=['.txt'])
    pages, counts, eligible = {}, Counter(), set()
    for physical, paths in sorted(inventory.items()):
        for path in paths:
            source = str(path.resolve())
            base = {'volume': volume, 'physical': physical, 'source': source,
                    'catalog_items': len(catalog.get(source, [])),
                    'historical_region_kinds': sorted(regions.get(source, [])),
                    'execution_evidence': attempts.get(source, [])}
            try:
                raw = path.read_text()
                hs, weak = page_signals(raw)
                bs = blocks(raw)
                base.update(status='read', source_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                            headings=hs, independent_title_leads=weak,
                            recognized_blocks=len(bs), source_format_status='recognized' if bs else empty_reason(raw))
                if any(h['eligible'] for h in hs):
                    eligible.add(physical)
            except (OSError, UnicodeError) as exc:
                base.update(status='read_error', error=str(exc))
            pages[source] = base
            counts[base['status']] += 1
    heading_window = {n + d for n in eligible for d in (-1, 0, 1)}
    selected = {s for s, p in pages.items() if p['catalog_items'] or set(p['historical_region_kinds']) & INDEX_KINDS
                or p['physical'] in heading_window}
    probe_jobs = []
    for source in sorted(selected):
        p = pages[source]
        if p['status'] != 'read':
            continue
        raw = Path(source).read_text()
        p['accounting'], p['span_issues'], p['residual_examples'] = account(raw, catalog.get(source, []))
        p['coverage_scope'] = 'whole_selected_page_not_verified_index_region'
        if p['catalog_items']:
            p['coverage_state'] = 'catalog_occurrences_present'
        elif p['execution_evidence']:
            p['coverage_state'] = 'attempt_recorded_without_catalog_occurrences'
        else:
            p['coverage_state'] = 'no_execution_evidence_in_supplied_campaigns'
        hs = [h for h in p['headings'] if h['eligible']]
        if hs:
            probe_jobs.append((bool(p['catalog_items']), -p['accounting'].get('body_residual_chars', 0), source, hs[0]))
    for _, _, source, heading in sorted(probe_jobs)[:probe_limit]:
        p = pages[source]
        try:
            p['probe'] = probe(Path(source).read_text(), source, volume, p['physical'], heading)
        except Exception as exc:
            p['probe'] = {'error': type(exc).__name__ + ': ' + str(exc)}
    return {'volume': volume, 'pages': list(pages.values()), 'missing_catalog_sources': sorted(set(catalog) - set(pages)),
            'counts': dict(counts)}


def load_catalog(path):
    by_volume = defaultdict(lambda: defaultdict(list))
    with closing(readonly(path)) as db:
        for row in db.execute('SELECT volume,source,metadata_json,ref_raw FROM items ORDER BY id'):
            m = json.loads(row['metadata_json'])
            by_volume[row['volume']][row['source']].append({
                'span': m.get('entry_span') or m.get('group_source_span') or m.get('source_span'),
                'entry_raw': m.get('entry_raw'), 'sha256': m.get('source_sha256'),
                'ref_span': m.get('reference_span') or m.get('source_span'),
                'ref_raw': m.get('reference_raw') or row['ref_raw']})
    return {v: dict(pages) for v, pages in by_volume.items()}


def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    catalog = load_catalog(args.catalog)
    regions = defaultdict(lambda: defaultdict(set))
    if args.regions:
        for line in args.regions.open():
            r = json.loads(line)
            for span in r['spans']:
                regions[r['volume']][span['source']].add(r['kind'])
    attempts = defaultdict(list)
    for path in args.execution:
        for line in path.open():
            row = json.loads(line)
            if row.get('source'):
                attempts[row['source']].append({'manifest': str(path), 'status': row.get('status', row.get('state'))})
    if args.survey:
        for path in sorted(args.survey.glob('volumes/*/sources.jsonl')):
            for line in path.open():
                row = json.loads(line)
                attempts[row['source']].append({'manifest': str(path), 'status': row.get('state')})
    volumes = sorted(p.name for p in args.root.iterdir() if p.is_dir() and (p / 'text').is_dir()
                     and (not args.volume or p.name in args.volume))
    attempts_by_volume = defaultdict(dict)
    for source, evidence in attempts.items():
        attempts_by_volume[Path(source).parent.parent.name][source] = evidence
    jobs = [(v, str(args.root.resolve()), catalog.get(v, {}), dict(regions[v]),
             attempts_by_volume[v], args.probe_pages_per_volume)
            for v in volumes]
    totals, accounting, states, heading_roles, kinds = Counter(), Counter(), Counter(), Counter(), Counter()
    volume_rows, priorities, weak_leads, missing = [], [], [], []
    logical = hashlib.sha256()
    with closing(sqlite3.connect(args.out / 'audit.sqlite')) as db, ProcessPoolExecutor(max_workers=args.workers) as pool:
        db.executescript('PRAGMA journal_mode=WAL; CREATE TABLE pages(source TEXT PRIMARY KEY,volume TEXT,physical INTEGER,evidence_json TEXT); CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);')
        for result in pool.map(scan_volume, jobs):
            vc = Counter(); missing.extend(result['missing_catalog_sources'])
            for p in result['pages']:
                hs = [h for h in p.get('headings', []) if h['eligible']]
                vc['files'] += 1; vc[p['status']] += 1
                vc['pages_with_catalog_occurrences'] += bool(p['catalog_items'])
                vc['index_heading_pages'] += bool(hs)
                vc['index_heading_pages_without_catalog'] += bool(hs) and not p['catalog_items']
                vc['audited_pages'] += 'accounting' in p
                vc['probe_pages'] += 'probe' in p
                vc['probe_proposals'] += p.get('probe', {}).get('valid_literal_proposals', 0)
                accounting.update(p.get('accounting', {})); states.update([p['coverage_state']] if p.get('coverage_state') else [])
                heading_roles.update(h['role'] for h in p.get('headings', [])); kinds.update(h['kind'] for h in hs)
                if hs and (not p['catalog_items'] or p.get('accounting', {}).get('body_numbers_outside_reference_spans', 0) > 20):
                    priorities.append(p)
                if p.get('independent_title_leads'):
                    weak_leads.append({k: p[k] for k in ('source', 'volume', 'physical', 'catalog_items', 'independent_title_leads')})
                serialized = encoded(p); logical.update(serialized.encode())
                db.execute('INSERT INTO pages VALUES(?,?,?,?)', (p['source'], p['volume'], p['physical'], serialized))
            db.commit(); totals.update(vc)
            volume_rows.append({'volume': result['volume'], 'represented': result['volume'] in catalog, **vc})
            print(encoded({'progress': True, 'completed_volumes': len(volume_rows), 'total_volumes': len(volumes), 'files': totals['files']}), flush=True)
        summary = {'version': VERSION, 'volumes': len(volumes), 'represented_volumes': sum(v['represented'] for v in volume_rows),
                   'totals': dict(totals), 'selected_page_accounting': dict(accounting), 'selected_page_states': dict(states),
                   'heading_roles': dict(heading_roles), 'heading_families': dict(kinds),
                   'catalog_sources_missing_on_disk': missing, 'logical_sha256': logical.hexdigest(),
                   'parameters': {k: str(v) if isinstance(v, Path) else [str(x) for x in v] if isinstance(v, list) else v for k,v in vars(args).items()},
                   'recognition_fingerprint': local_fingerprint(), 'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'catalog_writes': 0, 'inference_calls': 0,
                   'warning': 'Not print recall. Whole selected pages may contain non-index text; catalog envelopes can contain concatenations. No execution evidence means only the supplied manifests.'}
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary))); db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    priorities.sort(key=lambda p: (bool(p['catalog_items']), -p.get('probe', {}).get('valid_literal_proposals', 0),
                                   -p.get('accounting', {}).get('body_numbers_outside_reference_spans', 0), p['source']))
    for name, value in [('summary.json', summary), ('volumes.json', volume_rows), ('priority_pages.json', priorities), ('independent_title_leads.json', weak_leads)]:
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    fields = sorted(set().union(*(r.keys() for r in volume_rows)))
    with (args.out / 'volumes.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(volume_rows)
    lines = ['# Cobertura de todos os volumes', '', summary['warning'], '',
             '| Volume | Arquivos | Páginas com registros | Páginas com título de índice | Título sem registros |',
             '| --- | ---: | ---: | ---: | ---: |']
    lines += [f"| {r['volume']} | {r['files']} | {r['pages_with_catalog_occurrences']} | {r['index_heading_pages']} | {r['index_heading_pages_without_catalog']} |" for r in volume_rows]
    (args.out / 'report.md').write_text('\n'.join(lines) + '\n')
    return summary


def report_gaps(out, parser_db=None):
    """Rebuild prioritization from the audit; optional extra execution history.

    Syntax signatures are non-exclusive leads. Roman numbers are deliberately
    not called tomes/books until an opening notice supplies their meaning.
    """
    attempts = set()
    if parser_db:
        with closing(readonly(parser_db)) as db:
            attempts = {r[0] for r in db.execute('SELECT DISTINCT source FROM run_pages')}
    volumes = {v['volume']: v for v in json.loads((out / 'volumes.json').read_text())}
    gaps, counters, signatures, by_volume = [], Counter(), Counter(), defaultdict(Counter)
    patterns = {
        'roman_then_arabic': re.compile(r'\b[IVXLCDM]+\s*[,.:]\s*\d+'),
        'arabic_lowercase_subdivision': re.compile(r'\b\d{1,4}\s+[a-d]\b'),
        'ibid_dependency': re.compile(r'\bib(?:id(?:em)?)?\.', re.I),
        'unicode_subscript': re.compile(r'[₀-₉]'),
        'page_line_or_page_list': re.compile(r'\d{1,4}\s*,\s*\d{1,3}'),
    }
    with closing(readonly(out / 'audit.sqlite')) as db:
        for row in db.execute('SELECT evidence_json FROM pages ORDER BY volume,physical,source'):
            p = json.loads(row[0])
            hs = [h for h in p.get('headings', []) if h['eligible']]
            if not hs or p['catalog_items']:
                continue
            prior = bool(p['execution_evidence']) or p['source'] in attempts
            reason = 'known_attempt_without_catalog_rows' if prior else 'no_attempt_found_in_supplied_history'
            r = {k: p[k] for k in ('volume', 'physical', 'source', 'source_sha256')}
            r.update(titles=[h['title_raw'] for h in hs], heading_roles=sorted({h['role'] for h in hs}),
                     reason=reason, represented_volume=volumes[p['volume']]['represented'],
                     probe=p.get('probe'), historical_region_kinds=p['historical_region_kinds'])
            raw = Path(p['source']).read_text()
            r['source_changed_since_scan'] = hashlib.sha256(raw.encode()).hexdigest() != p['source_sha256']
            body = '\n'.join(b['body'] for b in blocks(raw) if b['kind'] == 'texto_principal')
            r['syntax_signatures'] = {k: len(pattern.findall(body)) for k, pattern in patterns.items() if pattern.search(body)}
            signatures.update(r['syntax_signatures'].keys())
            counters[reason] += 1; counters['pages'] += 1
            counters['represented_volume_pages'] += r['represented_volume']
            counters['probed_pages'] += bool(r['probe'])
            counters['probed_with_literal_proposals'] += bool((r['probe'] or {}).get('valid_literal_proposals'))
            counters['literal_proposals_in_probed_pages'] += (r['probe'] or {}).get('valid_literal_proposals', 0)
            by_volume[p['volume']]['heading_pages_without_rows'] += 1
            by_volume[p['volume']][reason] += 1
            by_volume[p['volume']]['probe_proposals'] += (r['probe'] or {}).get('valid_literal_proposals', 0)
            gaps.append(r)
    ranked = [{'volume': v, **c} for v, c in sorted(by_volume.items(), key=lambda x: (-x[1]['heading_pages_without_rows'], x[0]))]
    summary = {'counts': dict(counters), 'volumes_with_gaps': len(by_volume),
               'represented_volumes_with_gaps': sum(volumes[v]['represented'] for v in by_volume),
               'syntax_signature_pages_nonexclusive': dict(signatures), 'ranking': ranked,
               'parser_history': str(parser_db) if parser_db else None,
               'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'warning': 'Heading candidates are not certified regions; probes do not measure recovered entries or recall.'}
    (out / 'gap_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    with (out / 'gaps.jsonl').open('w') as f:
        for r in gaps:
            f.write(encoded(r) + '\n')
    lines = ['# Lacunas de seleção e gramática', '', summary['warning'], '',
             f"Páginas sinalizadas sem registros: {counters['pages']}, em {len(by_volume)} volumes.",
             f"Destas, {counters['represented_volume_pages']} ficam em volumes já representados.",
             f"Histórico consultado: {counters['known_attempt_without_catalog_rows']} com tentativa conhecida; "
             f"{counters['no_attempt_found_in_supplied_history']} sem tentativa encontrada.", '',
             '| Volume | Páginas sinalizadas sem registros | Tentativa conhecida | Sem tentativa encontrada |',
             '| --- | ---: | ---: | ---: |']
    lines += [f"| {r['volume']} | {r['heading_pages_without_rows']} | {r.get('known_attempt_without_catalog_rows',0)} | {r.get('no_attempt_found_in_supplied_history',0)} |" for r in ranked]
    lines += ['', 'Assinaturas sintáticas não exclusivas (páginas):', encoded(dict(signatures)), '',
              'Páginas completas podem misturar índices e outros materiais. Spans antigos frequentemente cobrem só a referência, '
              'não todo o verbete: caracteres fora desses spans não medem omissões do parser. '
              'Nenhum candidato destas sondagens foi incorporado ao catálogo.']
    (out / 'gap_report.md').write_text('\n'.join(lines) + '\n')
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--root', type=Path, default=Path('teste'))
    p.add_argument('--regions', type=Path)
    p.add_argument('--survey', type=Path)
    p.add_argument('--execution', type=Path, action='append', default=[])
    p.add_argument('--volume', action='append', default=[])
    p.add_argument('--workers', type=int, default=20)
    p.add_argument('--probe-pages-per-volume', type=int, default=6)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--parser-db', type=Path, help='Optional read-only run_pages execution history')
    p.add_argument('--report-only', action='store_true', help='Rebuild gap ranking from the existing audit, without full corpus scan')
    args = p.parse_args()
    if args.workers < 1 or args.probe_pages_per_volume < 0:
        p.error('Invalid worker/probe count')
    if not args.report_only:
        print(encoded(run(args)))
    print(encoded(report_gaps(args.out, args.parser_db)))
