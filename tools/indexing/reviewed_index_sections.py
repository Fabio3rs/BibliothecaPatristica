"""Rebuild bounded index selections from literal opening/closing evidence.

The checked-in plan is editorial provenance; spans and file identities are
recomputed from current OCR. Outputs stay experimental. This runner does not
write canonical databases or source OCR and never calls an inference service.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re

from tools.corpus_utils import page_files
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.unrepresented_region_recovery import run, VERSION, INDEX_KINDS, PARSER_MODULES
from tools.indexing.entry_parser_bulk import readonly


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+'\n')


def literal_offset(raw, quote, source):
    if not quote or raw.count(quote) != 1:
        raise ValueError(f'Boundary must occur exactly once in {source}: {quote!r}')
    return raw.index(quote)


def build_sections(plans, root):
    inventories, sections, identifiers = {}, [], set()
    for plan in plans:
        config = json.loads(plan.read_text())
        for spec in config['sections']:
            volume, sid = spec['volume'], spec['id']
            if spec['kind'] not in INDEX_KINDS:
                raise ValueError(f'Unsupported index kind (would be omitted): {sid}: {spec["kind"]}')
            if spec.get('parser') not in PARSER_MODULES:
                raise ValueError(f'Unsupported parser: {sid}: {spec.get("parser")}')
            if sid in identifiers:
                raise ValueError(f'Duplicate section id: {sid}')
            identifiers.add(sid)
            if volume not in inventories:
                inventories[volume] = page_files(root/'teste'/volume/'text', suffixes=['.txt'])
            inventory = inventories[volume]
            first, last = spec['first'], spec['last']
            if not 0 < first <= last:
                raise ValueError(f'Invalid section range: {sid}')
            section = {'section_id': sid, 'volume': volume, 'kind': spec['kind'],
                       'parser': spec.get('parser'), 'spans': [],
                       'reviewed_opening': spec.get('opening_quote'),
                       'selection_plan': str(plan.resolve()),
                       'end_status': 'reviewed_literal_boundary' if spec.get('end_quote') else 'reviewed_physical_range',
                       'parser_context': spec.get('context', {}), 'notice_evidence': []}
            for key in ('family','namespace'):
                if key in spec:
                    section[key] = spec[key]
            for physical in range(first, last+1):
                paths = inventory.get(physical, [])
                if len(paths) != 1:
                    raise ValueError(f'Missing or ambiguous OCR page {volume}:{physical}: {paths}')
                path = paths[0].resolve()
                raw = path.read_text()
                a = literal_offset(raw,spec['opening_quote'],path) if physical==first and spec.get('opening_quote') else 0
                b = literal_offset(raw,spec['end_quote'],path) if physical==last and spec.get('end_quote') else len(raw)
                if a >= b:
                    raise ValueError(f'Reversed bounds: {sid}:{physical}')
                sha = hashlib.sha256(raw.encode()).hexdigest()
                for block in blocks(raw):
                    lo, hi = max(a,block['body_start']), min(b,block['body_end'])
                    if lo < hi and raw[lo:hi].strip():
                        section['spans'].append({'source':str(path),'physical':physical,
                            'span':[lo,hi], 'source_sha256':sha})
            for notice in spec.get('notice_quotes', []):
                paths = inventory.get(notice['physical'], [])
                if len(paths) != 1:
                    raise ValueError(f'Missing/ambiguous notice page: {sid}')
                path = paths[0].resolve();raw = path.read_text()
                offset = literal_offset(raw,notice['quote'],path)
                section['notice_evidence'].append({'source':str(path),'physical':notice['physical'],
                    'quote':notice['quote'],'span':[offset,offset+len(notice['quote'])],
                    'role':'reviewed_editorial_notice'})
            if not section['spans']:
                raise ValueError(f'No OCR body within section: {sid}')
            sections.append(section)
    return sorted(sections,key=lambda s:(s['volume'],s['spans'][0]['physical'],s['spans'][0]['span'],s['section_id']))


def union(intervals):
    merged=[]
    for a,b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1][1]=max(b,merged[-1][1])
        else:
            merged.append([a,b])
    return merged


def audit_material(sections, extraction, out):
    """Account for every selected body character, independently of parser residuals."""
    selected, consumed = {}, {}
    for section in sections:
        for span in section['spans']:
            selected.setdefault(span['source'],[]).append(span['span'])
    logical=hashlib.sha256()
    with closing(readonly(extraction/'search.sqlite')) as db:
        for row in db.execute('SELECT id,metadata_json FROM items ORDER BY id'):
            logical.update(row['metadata_json'].encode())
            meta=json.loads(row['metadata_json'])
            consumed.setdefault(meta['source'],[]).append(meta['entry_span'])
    totals=Counter();pages=[]
    with (out/'unconsumed_body.jsonl').open('w') as residuals:
        for source,spans in sorted(selected.items()):
            raw=Path(source).read_text();body=[];structural=[]
            for block in blocks(raw):
                for a,b in union(spans):
                    lo,hi=max(a,block['body_start']),min(b,block['body_end'])
                    if lo < hi:
                        (body if block['kind']=='texto_principal' else structural).append([lo,hi])
            consumed_union=union(consumed.get(source,[]));counts=Counter()
            for a,b in union(body):
                counts['body_chars']+=b-a
                cursor=a
                for x,y in consumed_union:
                    lo,hi=max(a,x),min(b,y)
                    if lo>=hi:continue
                    if cursor<lo:
                        residuals.write(json.dumps(dict(source=source,span=[cursor,lo],raw=raw[cursor:lo]),ensure_ascii=False)+'\n')
                        counts['residual_numeric_occurrences']+=len(re.findall(r'\d+',raw[cursor:lo]))
                    counts['consumed_body_chars']+=hi-lo
                    cursor=hi
                if cursor<b:
                    residuals.write(json.dumps(dict(source=source,span=[cursor,b],raw=raw[cursor:b]),ensure_ascii=False)+'\n')
                    counts['residual_numeric_occurrences']+=len(re.findall(r'\d+',raw[cursor:b]))
            counts['residual_body_chars']=counts['body_chars']-counts['consumed_body_chars']
            counts['structural_chars']=sum(b-a for a,b in union(structural))
            original=consumed.get(source,[])
            counts['overlapping_entry_chars']=sum(b-a for a,b in original)-sum(b-a for a,b in consumed_union)
            totals.update(counts);pages.append(dict(source=source,**counts))
    result={'counts':dict(totals),'pages':pages,'logical_sha256':logical.hexdigest(),
            'interpretation':'Consumed spans are candidate extraction, not verified accuracy. Residue includes legitimate headings and unparsed material.'}
    write_json(out/'coverage.json',result)
    return result


def execute(plans, root, out, catalog=None, workers=20):
    sections = build_sections(plans, root)
    out.mkdir(parents=True,exist_ok=False)
    inventory = out/'sections.jsonl'
    inventory.write_text(''.join(json.dumps(s,ensure_ascii=False,sort_keys=True)+'\n' for s in sections))
    selection = out/'selection.json'
    write_json(selection,{'version':VERSION,'by_volume':[{'volume':v} for v in sorted({s['volume'] for s in sections})]})
    write_json(out/'rebuild.json',{'version':'reviewed-index-sections-v1',
        'plans':[{'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in plans],
        'plan_contents':[json.loads(p.read_text()) for p in plans],
        'code_sha256': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (Path(__file__), Path(__file__).with_name('editorial_ocr_blocks.py'))},
        'section_counts':dict(Counter(s['volume'] for s in sections)),
        'root':str(root),'canonical_updates':0,'inference_calls':0})
    run(catalog,inventory,out/'extraction',workers=workers,selection_manifest=selection)
    audit_material(sections,out/'extraction',out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,action='append',required=True)
    parser.add_argument('--root',type=Path,default=Path('.'))
    parser.add_argument('--catalog',type=Path,help='Readonly input identity for later experimental add-supplement; optional for standalone rebuild')
    parser.add_argument('--workers',type=int,default=20)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    execute(args.plan,args.root.resolve(),args.out,args.catalog,args.workers)
