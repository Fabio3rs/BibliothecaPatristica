"""Bounded name + numeric-list extraction, preserving literal occurrences.

A reviewed in-body title enables the grammar; a running header does not open a
section. Numeric runs end before a new name, including inline dash homonyms.
Source order and offsets are retained. Unsupported text is exported as residue.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from tools.corpus_utils import page_files
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly

VERSION = 'onomastic-numeric-lists-v1'
NUMBER_LIST = re.compile(r'(?<!\w)\d{1,4}(?:(?:\s*[,;]\s*|[ \t]+)\d{1,4})*(?!\d)')
LETTER = re.compile(r'(?m)^\s*[A-ZÆŒ]\s*$')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def trim(raw, a, b, punctuation=False):
    chars = ' \n\t\r' + ('.,;:—–-' if punctuation else '')
    while a < b and raw[a] in chars:
        a += 1
    while b > a and raw[b-1] in chars:
        b -= 1
    return a, b


def parse_block(raw, start, end, source):
    """Read a compact list of named entries; do not infer orphan numbers."""
    entries, cursor = [], start
    for match in NUMBER_LIST.finditer(raw, start, end):
        a, b = trim(raw, cursor, match.start(), True)
        # An explicit homonym separator also terminates unsupported intervening text.
        dividers = list(re.finditer(r'[—–]|--', raw[a:b]))
        if dividers:
            a += dividers[-1].end()
            a, b = trim(raw, a, b, True)
        # A standalone alphabet letter is structure, never part of the name.
        letters = list(LETTER.finditer(raw[a:b]))
        if letters:
            a += letters[-1].end()
            a, b = trim(raw, a, b, True)
        owner = raw[a:b]
        if not owner or not owner[0].isupper() or not any(c.isalpha() for c in owner):
            cursor = match.end()
            continue
        if re.search(r'\d|<|>|\[Manuscripta\]', owner):
            cursor = match.end()
            continue
        flags = ['literal_candidate_not_certified']
        if re.search(r'\b[Vv]ide\b', owner):
            flags.append('textual_cross_reference_with_numeric_locator')
        if re.search(r'-\s*\n', owner):
            flags.append('literal_soft_wrap_preserved')
        if re.search(r'\b(?:Presbyter|Abbas|Episcopus)\b', owner) and len(owner.split()) == 1:
            flags.append('possible_inherited_name_context')
        numbers = [{'raw': n[0], 'span': [n.start(), n.end()], 'value': int(n[0]), 'source': source}
                   for n in re.finditer(r'\d+', raw[match.start():match.end()])]
        for n in numbers:
            n['span'] = [v+match.start() for v in n['span']]
        entries.append({'owner_raw': owner, 'owner_span': [a, b],
                        'reference_raw': match[0], 'reference_span': list(match.span()),
                        'entry_span': [a, match.end()], 'locators': numbers, 'flags': flags})
        cursor = match.end()
    return entries


def extract_page(task):
    source, physical, spec, first, last = task
    raw = Path(source).read_text()
    opening = spec['opening_quote']
    lo, hi = 0, len(raw)
    if physical == first:
        title_blocks = [b for b in blocks(raw) if b['body'].strip() == opening.strip()]
        if len(title_blocks) != 1:
            raise ValueError(f'Opening must occur in one body block: {source}')
        lo = title_blocks[0]['body_end']
    if physical == last and spec.get('end_quote'):
        closing = spec['end_quote']
        if raw.count(closing) != 1:
            raise ValueError(f'Closing must be unique: {source}')
        hi = raw.index(closing)
    entries, residues, counts = [], [], Counter()
    for block in blocks(raw):
        a, b = max(lo, block['body_start']), min(hi, block['body_end'])
        if a >= b:
            continue
        counts['selected_chars'] += b-a
        found = parse_block(raw, a, b, source) if block['kind'] == 'texto_principal' else []
        entries.extend(found)
        cursor = a
        for entry in found:
            ea, eb = entry['entry_span']
            if cursor < ea:
                residues.append({'source': source, 'physical': physical, 'span': [cursor, ea], 'raw': raw[cursor:ea],
                                 'reason': 'unconsumed_body' if block['kind'] == 'texto_principal' else 'nonbody'})
            counts['consumed_chars'] += eb-ea
            cursor = eb
        if cursor < b:
            residues.append({'source': source, 'physical': physical, 'span': [cursor, b], 'raw': raw[cursor:b],
                             'reason': 'unconsumed_body' if block['kind'] == 'texto_principal' else 'nonbody'})
    counts['residual_chars'] = sum(len(r['raw']) for r in residues)
    counts['residual_nonwhitespace_chars'] = sum(len(re.sub(r'\s', '', r['raw'])) for r in residues)
    counts['residual_numeric_occurrences'] = sum(len(re.findall(r'\d+', r['raw'])) for r in residues if r['reason'] == 'unconsumed_body')
    assert counts['selected_chars'] == counts['consumed_chars'] + counts['residual_chars']
    for entry in entries:
        for k in ('owner','reference'):
            a,b = entry[k+'_span']
            assert raw[a:b] == entry[k+'_raw']
        ident = hashlib.sha256(encoded([spec['id'], source, entry['entry_span'], entry['owner_raw'], entry['reference_raw']]).encode()).hexdigest()
        entry.update(id='onomastic-entry:'+ident, group_id='onomastic-group:'+ident,
                     volume=spec['volume'], source=source, physical=physical,
                     source_sha256=hashlib.sha256(raw.encode()).hexdigest(), grammar=VERSION,
                     reference_namespace='printed_number', entry_kind='name_entry', section_id=spec['id'],
                     reviewed_source_section={'section_id': spec['id'], 'kind': 'onomastic_index',
                                              'title_raw': opening, 'opening_raw': opening, 'first': first, 'last': last,
                                              'family': VERSION, 'plan': spec.get('plan'),
                                              'opening_evidence': spec.get('opening_evidence'),
                                              'namespace_note': 'Printed numbers retained; target edition requires independent mapping.'})
    return {'source': source, 'physical': physical, 'entries': entries, 'residuals': residues, 'counts': dict(counts)}


def run(plan, root, out, catalog=None, workers=1):
    spec = json.loads(plan.read_text())['section']
    spec['plan'] = str(plan.resolve())
    inv = page_files(root/'teste'/spec['volume']/'text', suffixes=['.txt'])
    tasks = []
    for n in range(spec['first'], spec['last']+1):
        paths = inv.get(n, [])
        if len(paths) != 1:
            raise ValueError(f'Missing/ambiguous physical file: {n}')
        tasks.append((str(paths[0].resolve()), n, spec, spec['first'], spec['last']))
    first_source = Path(tasks[0][0])
    first_raw = first_source.read_text()
    opening = spec['opening_quote']
    if first_raw.count(opening) != 1:
        raise ValueError('Opening quote must be unique')
    start = first_raw.index(opening)
    spec['opening_evidence'] = {'source': str(first_source), 'span': [start,start+len(opening)], 'raw': opening}
    # The next editorial opening is evidence for the last physical boundary.
    witness = spec['closing_witness']
    paths = inv.get(witness['physical'], [])
    if len(paths) != 1 or witness['quote'] not in paths[0].read_text():
        raise ValueError('Closing witness unavailable in current OCR')
    existing = Counter()
    if catalog:
        with readonly(catalog) as db:
            for row in db.execute('SELECT source,count(*) AS n FROM items WHERE volume=? GROUP BY source',(spec['volume'],)):
                existing[row['source']] = row['n']
    out.mkdir(parents=True, exist_ok=False)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(extract_page, tasks))
    all_entries = [e for result in results for e in result['entries']]
    counts = Counter()
    for result in results:
        counts.update(result['counts'])
    counts.update(entries=len(all_entries), numeric_occurrences=sum(len(e['locators']) for e in all_entries),
                  pages=len(results), previous_entries_on_selected_pages=sum(existing[t[0]] for t in tasks))
    summary = {'version': VERSION, 'catalog': str(catalog.resolve()) if catalog else None,
               'counts': dict(counts), 'workers': workers, 'section': spec,
               'source_hashes': {t[0]:hashlib.sha256(Path(t[0]).read_bytes()).hexdigest() for t in tasks},
               'plan_sha256': hashlib.sha256(plan.read_bytes()).hexdigest(),
               'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'logical_sha256': hashlib.sha256(encoded(all_entries).encode()).hexdigest(),
               'canonical_updates': 0, 'inference_calls': 0,
               'quality': 'Literal occurrence candidates; neither complete printed transcription nor certified identity.'}
    with sqlite3.connect(out/'search.sqlite') as db:
        db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE items(id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,label TEXT,ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT);
        CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
        CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
        CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume,tokenize='unicode61 remove_diacritics 2');''')
        for e in all_entries:
            meta={**e,'entry':{k:e[k] for k in ('entry_span','owner_span','owner_raw','reference_span','reference_raw')}}
            cur=db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',(e['id'],e['group_id'],e['volume'],e['owner_raw'],e['reference_raw'],e['source'],'source_only',encoded(meta)))
            db.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)',(cur.lastrowid,e['owner_raw'],e['reference_raw'],e['volume']))
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    for name,rows in [('entries',all_entries),('residuals',[r for p in results for r in p['residuals']]),('pages',[{k:v for k,v in p.items() if k not in ('entries','residuals')} for p in results])]:
        (out/(name+'.jsonl')).write_text(''.join(encoded(r)+'\n' for r in rows))
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--catalog',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=1)
    args=parser.parse_args()
    if args.workers < 1:
        parser.error('--workers must be positive')
    print(json.dumps(run(args.plan,args.root,args.out,args.catalog,args.workers),ensure_ascii=False,indent=2))
