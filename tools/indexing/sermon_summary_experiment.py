"""Literal structured sermon synopses, isolated from alphabetical references."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import uuid

from scripts.scan_editorial_ocr import BLOCK, ATTR
from tools.indexing.entry_parser_bulk import open_experiment
from tools.indexing.parser_target_exports import write_jsonl

VERSION = 'sermon-summary-literal-v1'
HEADING = re.compile(r'(?m)(?:^|(?<=[—–]))[ \t]*SERM(?:O)?\.?\s+(?P<roman>[IVXLCDM]+)\.')
ITEM = re.compile(r'(?<!\w)(?P<number>[0-9]{1,2})\.\s+(?=[A-ZÆŒÀ-Ü])')
LOCATOR = re.compile(r'(?<![\w.,])(?P<locator>[0-9]{3,4}(?:\s*[-–]\s*[0-9]{3,4})?)\.?\s*$')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def literal(raw, a, b):
    while a < b and raw[a].isspace():
        a += 1
    while b > a and raw[b-1].isspace():
        b -= 1
    return {'raw': raw[a:b], 'span': [a,b]}


def extract(raw, source, volume, physical):
    checksum = digest(raw.encode())
    blocks = list(BLOCK.finditer(raw))
    index_context = any(dict(ATTR.findall(m['attrs'])).get('tipo') == 'cabecalho'
                        and re.search(r'\bINDEX\b', m['body'], re.I) for m in blocks)
    records, residuals = [], []
    for block in blocks:
        if dict(ATTR.findall(block['attrs'])).get('tipo') != 'texto_principal':
            continue
        body, base = block['body'], block.start('body')
        headings = list(HEADING.finditer(body)) if index_context else []
        if not headings:
            residuals.append({**literal(raw,base,block.end('body')),
                'reason':'unclassified_or_continuation_fragment'})
            continue
        if body[:headings[0].start()].strip():
            residuals.append({**literal(raw,base,base+headings[0].start()),
                'reason':'leading_fragment_requires_context'})
        for i, heading in enumerate(headings):
            end = base + (headings[i+1].start() if i+1 < len(headings) else len(body))
            if i+1 < len(headings) and raw[end-1] in '—–':
                end -= 1
            start = base+heading.start()
            entry = literal(raw,start,end)
            start,end = entry['span']
            after_heading = base+heading.end()
            separator = re.search(r'\s[—–]\s',raw[after_heading:end])
            tail = LOCATOR.search(raw[after_heading:end])
            locator = literal(raw,after_heading+tail.start('locator'),after_heading+tail.end('locator')) if tail else None
            content_end = locator['span'][0] if locator else end
            flags = ['experimental_material_type', 'editorial_locator_semantics_unverified']
            items = []
            title = None
            summary = None
            unassigned = []
            if separator and after_heading+separator.end() <= content_end:
                title = literal(raw,after_heading,after_heading+separator.start())
                summary_start = after_heading+separator.end()
                summary = literal(raw,summary_start,content_end)
                numbers = list(ITEM.finditer(raw,summary_start,content_end))
                for j, number in enumerate(numbers):
                    stop = numbers[j+1].start() if j+1 < len(numbers) else content_end
                    items.append({'number_raw':number['number'],
                        'number_span':[number.start('number'),number.end('number')],
                        'text':literal(raw,number.end(),stop),
                        'role':'summary_item_not_page', 'verified':False})
                if numbers:
                    if [int(m['number']) for m in numbers] != list(range(1,len(numbers)+1)):
                        flags.append('irregular_item_numbering')
                    if raw[summary_start:numbers[0].start()].strip():
                        unassigned.append(literal(raw,summary_start,numbers[0].start()))
                # Unnumbered synopses remain available as summary.raw.
            else:
                flags.append('title_summary_separator_missing')
                unassigned.append(literal(raw,after_heading,content_end))
            if not locator:
                flags.append('possible_continuation_or_missing_locator')
            record = {'id':digest(f'{source}:{checksum}:{start}:{end}'.encode())[:24],
                'version':VERSION,
                'material_kind':'sermon_summary' if summary else 'sermon_catalog_or_unseparated_summary',
                'pipeline_hint':'general',
                'source':source, 'source_sha256':checksum, 'volume':volume,'physical':physical,
                'entry':entry, 'sermon_number_raw':heading['roman'],
                'sermon_number_span':[base+heading.start('roman'),base+heading.end('roman')],
                'title':title, 'summary':summary, 'items':items,
                'trailing_locator_candidate':locator, 'unassigned':unassigned,
                'flags':flags, 'verified':False, 'import_ready':False, 'target_files':[]}
            records.append(record)
    return {'records':records, 'residuals':residuals, 'index_header_observed':index_context}


def work(item):
    try:
        source=Path(item['source'])
        data=source.read_bytes()
        if digest(data) != item['sha256']:
            raise ValueError('Source changed since discovery')
        result=extract(data.decode('utf-8'),str(source),item['volume'],item['physical'])
        return {**item, **result, 'error':None}
    except (OSError, UnicodeError, ValueError) as exc:
        return {**item, 'records':[], 'residuals':[], 'error':f'{type(exc).__name__}: {exc}'}


def run(args):
    if args.workers < 1 or args.workers > 64 or args.out.exists():
        raise ValueError('Require workers 1..64 and a new output directory')
    items=[]
    seen=set()
    for line in args.source_manifest.read_text().splitlines():
        row=json.loads(line)
        if args.volume and row['volume'] not in args.volume:
            continue
        if row['source'] in seen:
            raise ValueError('Duplicate manifest source')
        seen.add(row['source'])
        items.append({k:row[k] for k in ['source','sha256','volume','physical']})
    con=open_experiment(args.experiment_db,args.source_db)
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pages=list(pool.map(work,items))
        records=[r for p in pages for r in p['records']]
        run_id=uuid.uuid4().hex
        summary={'run_id':run_id,'version':VERSION,'pages_examined':len(pages),
            'pages_with_candidates':sum(bool(p['records']) for p in pages),
            'records':len(records),'numbered_items':sum(len(r['items']) for r in records),
            'by_volume':dict(Counter(r['volume'] for r in records)),
            'by_kind':dict(Counter(r['material_kind'] for r in records)),
            'flags':dict(Counter(f for r in records for f in r['flags'])),
            'errors':sum(p['error'] is not None for p in pages), 'inference_calls':0,
            'source_db_updates':0,'import_ready':False}
        args.out.mkdir(parents=True)
        config={'source_manifest':str(args.source_manifest.resolve()),
            'manifest_sha256':digest(args.source_manifest.read_bytes()), 'volume':args.volume,
            'workers':args.workers, 'code_sha256':digest(Path(__file__).read_bytes())}
        for name,value in [('summary',summary),('config',config)]:
            (args.out/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        write_jsonl(args.out/'materials.jsonl',records)
        write_jsonl(args.out/'pages.jsonl',pages)
        con.executescript('''CREATE TABLE IF NOT EXISTS structured_material_runs(
            run_id TEXT PRIMARY KEY, config_json TEXT, summary_json TEXT);
            CREATE TABLE IF NOT EXISTS structured_material_candidates(
            run_id TEXT, material_id TEXT, kind TEXT, source TEXT, record_json TEXT,
            PRIMARY KEY(run_id, material_id));''')
        with con:
            con.execute('INSERT INTO structured_material_runs VALUES(?,?,?)',
                        (run_id,json.dumps(config),json.dumps(summary)))
            con.executemany('INSERT INTO structured_material_candidates VALUES(?,?,?,?,?)',
                [(run_id,r['id'],r['material_kind'],r['source'],json.dumps(r,ensure_ascii=False)) for r in records])
        return summary
    finally:
        con.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-manifest',type=Path,required=True)
    ap.add_argument('--volume',action='append',default=[])
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--source-db',type=Path,default=Path('data/alphabetical_indices.db'))
    ap.add_argument('--experiment-db',type=Path,default=Path('data/index_entry_parser/experiments.sqlite'))
    ap.add_argument('--out',type=Path,required=True)
    print(json.dumps(run(ap.parse_args()),ensure_ascii=False,indent=2))
