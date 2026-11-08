"""Bounded, literal title-page catalogue for experimental bibliographic links.

The initial grammar recognizes Latin OPERA OMNIA title pages. Unknown layouts
remain unclassified. OCR supplies evidence; neither images nor production DB are
modified, and bibliographic grouping never resolves an index citation by itself.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import re
import uuid

from scripts.scan_editorial_ocr import BLOCK, ATTR
from tools.corpus_utils import page_files
from tools.indexing.entry_parser_bulk import open_experiment
from tools.indexing.entry_parser_experiment import digest, display, source_span

VERSION = 'title-page-literal-v1'
TOME = re.compile(r'TOMUS\s+([IVXLCDM]+)(?:\s+ET\s+ULTIMUS)?\.', re.I)
VOLUME = re.compile(r'(?:PG|PL|PO)[0-9]{3}(?:[.-][0-9]+)?')


def extract_title_page(raw, *, source, volume, physical):
    blocks = []
    for i, match in enumerate(BLOCK.finditer(raw)):
        a, b = match.start('body'), match.end('body')
        a += len(raw[a:b]) - len(raw[a:b].lstrip())
        b -= len(raw[a:b]) - len(raw[a:b].rstrip())
        if a >= b:
            continue
        attrs = dict(ATTR.findall(match['attrs']))
        blocks.append({'id': f'b{i}', 'kind': attrs.get('tipo', ''), 'raw': raw[a:b],
                       'text': display(raw[a:b]), **source_span(raw, a, b)})
    checksum = digest(raw.encode())
    record = {'version': VERSION, 'record_id': digest(f'{VERSION}:{volume}:{source}:{checksum}'.encode()),
              'collection_volume': volume, 'source': source, 'physical': physical,
              'source_sha256': checksum, 'status': 'unrecognized', 'blocks': blocks,
              'fields': {}, 'work_tome_roman': None, 'collection_tome_roman': None,
              'work_group_key': None, 'pending': [], 'verified': False}
    usable = [b for b in blocks if b['kind'] in {'texto_principal', 'cabecalho', 'rodape'}]
    if any(re.search(r'\bPATROLOGI', b['text'], re.I) for b in usable):
        record.update(status='collection_title_requires_review', pending=['do_not_treat_collection_tome_as_work_tome'])
        return record
    titles = [i for i,b in enumerate(usable) if re.fullmatch(r'OPERA\s+OMNIA[,.]?', b['text'], re.I)]
    tomes = [i for i,b in enumerate(usable) if TOME.fullmatch(b['text'])]
    imprints = [i for i,b in enumerate(usable) if re.search(r'\b(?:EXCUDEBAT|TYPIS|APUD)\b', b['text'], re.I)]
    years = [i for i,b in enumerate(usable) if re.fullmatch(r'[12][0-9]{3}\.', b['text'])]
    if not (len(titles) == len(tomes) == len(imprints) == len(years) == 1):
        record['pending'] = ['unsupported_or_ambiguous_title_layout']
        return record
    title, tome, imprint, year = titles[0], tomes[0], imprints[0], years[0]
    if not (0 < title < tome < imprint < year) or title > 6 or any(len(b['text']) > 160 for b in usable[:title]):
        record['pending'] = ['unsupported_title_order_or_author_region']
        return record

    def field(items):
        return {'text': ' '.join(b['text'] for b in items), 'block_ids': [b['id'] for b in items]}

    record['fields'] = {'author_heading': field(usable[:title]), 'work_title': field([usable[title]]),
                        'work_tome': field([usable[tome]]), 'edition_statement': field(usable[title+1:tome]),
                        'imprint': field(usable[imprint:year]), 'publication_year': field([usable[year]])}
    record['work_tome_roman'] = TOME.fullmatch(usable[tome]['text'])[1].upper()
    record['work_group_key'] = digest(json.dumps([
        record['fields'][k]['text'].casefold().strip(' .,') for k in ['author_heading', 'work_title']
    ], ensure_ascii=False).encode())
    record.update(status='structured_candidate', pending=['bibliographic_identity_requires_review',
                  'cited_pagination_edition_requires_separate_index_evidence'])
    return record


def xref_proposals(records):
    groups = defaultdict(list)
    for record in records:
        if record['work_group_key']:
            groups[record['work_group_key']].append(record)
    proposals = []
    for key, members in groups.items():
        if len({r['collection_volume'] for r in members}) < 2:
            continue
        targets = defaultdict(list)
        for r in members:
            targets[r['work_tome_roman']].append({'volume': r['collection_volume'],
                'record_id': r['record_id'], 'source': r['source'], 'source_sha256': r['source_sha256']})
        proposals.append({'work_group_key': key, 'author_heading': members[0]['fields']['author_heading']['text'],
                          'work_title': members[0]['fields']['work_title']['text'], 'targets_by_work_tome': dict(targets),
                          'ambiguous_tomes': [t for t, rs in targets.items() if len(rs) > 1],
                          'status': 'requires_editorial_scope_review', 'automatic_application': False})
    return proposals


def read_task(task):
    volume, physical, path = task
    try:
        raw = path.read_bytes().decode('utf-8')
        return extract_title_page(raw, source=str(path), volume=volume, physical=physical), None
    except (OSError, UnicodeError, ValueError) as exc:
        return None, {'volume': volume, 'physical': physical, 'source': str(path),
                      'reason': 'source_read_or_parse_error', 'error': str(exc)}


def format_diagnostics(records):
    """Describe literal layout signals; never upgrade extraction or xref status."""
    pages, counts = [], Counter()
    for r in records:
        texts = [b['text'] for b in r['blocks'] if b['kind'] in {'texto_principal', 'cabecalho', 'rodape'}]
        title = [t for t in texts if re.fullmatch(r'OPERA\s+OMNIA[,.]?', t, re.I)]
        tomes = [t for t in texts if re.fullmatch(r'TOMUS\s+[A-ZIVXLCDM ]+[.,]?', t, re.I)]
        imprints = [t for t in texts if re.search(r'\b(?:EXCUDEBAT|EXCUDEBATUR|TYPIS|APUD)\b', t, re.I)]
        if not title and not (tomes and imprints):
            continue
        signals = []
        if title: signals.append('standalone_opera_omnia')
        if not tomes: signals.append('no_standalone_tome')
        elif not any(TOME.fullmatch(t) for t in tomes): signals.append('tome_outside_initial_roman_grammar')
        if any(re.fullmatch(r'[12][0-9]{3}', t) for t in texts): signals.append('year_without_final_period')
        if not any(re.fullmatch(r'[12][0-9]{3}\.', t) for t in texts): signals.append('no_standalone_year_with_period')
        if len(imprints) != 1: signals.append('imprint_not_unique_or_missing')
        if r['status'] == 'collection_title_requires_review': signals.append('patrologia_lexical_guard')
        counts.update(signals)
        pages.append({'volume': r['collection_volume'], 'physical': r['physical'], 'record_id': r['record_id'],
                      'source': r['source'], 'source_sha256': r['source_sha256'], 'status': r['status'],
                      'signals': signals, 'tome_literals': tomes})
    return {'scope': 'literal signals only; overlapping counts; not title-page ground truth',
            'counts': dict(counts), 'pages': pages}


def survey_report(records, volumes, issues, out):
    rows = []
    for volume in volumes:
        selected = [r for r in records if r['collection_volume'] == volume]
        counts = Counter(r['status'] for r in selected)
        rows.append({'volume': volume, 'pages': len(selected),
                     **{key: counts[key] for key in ['structured_candidate', 'collection_title_requires_review', 'unrecognized']},
                     'issues': sum(i['volume'] == volume for i in issues)})
    with (out/'volumes.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# Leitura mecânica de folhas de rosto', '',
             'Recorte inicial por volume. Candidatos estruturados não são identidades bibliográficas certificadas.',
             '“Coleção/revisão” é sinal lexical, não confirmação de uma folha de rosto.', '',
             '| Volume | Páginas lidas | Estruturadas | Coleção/revisão | Não reconhecidas | Problemas |',
             '|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append('| ' + ' | '.join(str(v) for v in r.values()) + ' |')
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    return rows


def run(args):
    if not 1 <= args.front_pages <= 100:
        raise ValueError('front-pages must be between 1 and 100; use explicit --page for other locations')
    if args.page and any(p < 1 for p in args.page):
        raise ValueError('Physical pages must be positive')
    workers = getattr(args, 'workers', 1)
    if workers < 1:
        raise ValueError('workers must be positive')
    if getattr(args, 'all_volumes', False):
        if args.volume:
            raise ValueError('Choose all-volumes or explicit volumes')
        volumes = sorted(p.name for p in args.root.iterdir() if p.is_dir()
                         and VOLUME.fullmatch(p.name) and (p/'text').is_dir())
    else:
        volumes = list(dict.fromkeys(args.volume or []))
    if not volumes:
        raise ValueError('No corpus volumes selected/found')
    tasks, issues = [], []
    for volume in volumes:
        if not VOLUME.fullmatch(volume):
            raise ValueError('Invalid corpus volume')
        listing = page_files(args.root / volume / 'text', suffixes=['.txt'])
        for physical in sorted(set(args.page or range(1, args.front_pages + 1))):
            choices = listing.get(physical, [])
            if len(choices) != 1:
                issues.append({'volume': volume, 'physical': physical, 'reason': 'missing_or_ambiguous_source',
                               'candidates': [str(p) for p in choices]})
                continue
            p = choices[0].resolve()
            tasks.append((volume, physical, p))
    # Reuse the parser experiment's identity guard; an unrelated/production DB is refused.
    con = open_experiment(args.experiment_db, Path('data/alphabetical_indices.db'))
    try:
        args.out.mkdir(parents=True, exist_ok=False)
        records = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for record, issue in pool.map(read_task, tasks):
                if issue:
                    issues.append(issue)
                else:
                    records.append(record)
        for record in records:
            if digest(Path(record['source']).read_bytes()) != record['source_sha256']:
                raise ValueError('Title-page source changed during extraction')
        proposals = xref_proposals(records)
        diagnostics = format_diagnostics(records)
        run_id = uuid.uuid4().hex
        config = {'version': VERSION, 'run_id': run_id, 'volumes': volumes, 'pages': args.page, 'workers': workers,
                  'front_pages': args.front_pages, 'root': str(args.root.resolve()),
                  'code_sha256': digest(Path(__file__).read_bytes())}
        per_volume = survey_report(records, volumes, issues, args.out)
        summary = {'run_id': run_id, 'volumes': len(volumes), 'pages': len(records), 'statuses': dict(Counter(r['status'] for r in records)),
                   'volumes_with_structured_candidates': sum(r['structured_candidate'] > 0 for r in per_volume),
                   'issue_counts': dict(Counter(i['reason'] for i in issues)),
                   'xref_proposals': len(proposals), 'issues': issues, 'production_db_updates': 0, 'inference_calls': 0}
        con.executescript('''CREATE TABLE IF NOT EXISTS title_page_runs(
            run_id TEXT PRIMARY KEY, config_json TEXT, summary_json TEXT);
            CREATE TABLE IF NOT EXISTS title_page_records(
            run_id TEXT, record_id TEXT, result_json TEXT, PRIMARY KEY(run_id,record_id));''')
        with con:
            con.execute('INSERT INTO title_page_runs VALUES(?,?,?)', (run_id,json.dumps(config),json.dumps(summary)))
            con.executemany('INSERT INTO title_page_records VALUES(?,?,?)',
                            [(run_id,r['record_id'],json.dumps(r,ensure_ascii=False)) for r in records])
        for name, value in [('config',config),('summary',summary),('records',records),('xref_proposals',proposals),
                            ('format_diagnostics',diagnostics)]:
            (args.out/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        return summary
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    selection = ap.add_mutually_exclusive_group(required=True)
    selection.add_argument('--volume', action='append')
    selection.add_argument('--all-volumes', action='store_true')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--page', type=int, action='append')
    ap.add_argument('--front-pages', type=int, default=12)
    ap.add_argument('--root', type=Path, default=Path('teste'))
    ap.add_argument('--experiment-db', type=Path, default=Path('data/index_entry_parser/experiments.sqlite'))
    ap.add_argument('--out', type=Path, required=True)
    print(json.dumps(run(ap.parse_args()),ensure_ascii=False,indent=2))
