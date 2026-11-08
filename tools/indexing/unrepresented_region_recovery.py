"""Recover selected editorial regions absent from the experimental search catalog.

Sources and existing catalogs are read-only. Every selected page has an outcome;
this campaign does not claim that regional envelopes cover the complete indices.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
import hashlib
import importlib
import json
from pathlib import Path
import re
import sqlite3

from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.candidate_search import normalize

VERSION = 'unrepresented-regions-v1'
INDEX_KINDS = {'index_unspecified', 'lexical_index', 'onomastic_index', 'thematic_index',
               'scripture_index', 'author_index', 'concordance'}
PARSER_MODULES = {
    'latin': 'missing_latin_region_parser', 'po': 'missing_po_region_parser',
    'pg114': 'pg114_index_grammar', 'po_named': 'po_named_index_grammar',
    'lexical_letter': 'lexical_letter_index_grammar',
    'roman_pair': 'roman_pair_index_grammar',
    'analytic_inherited': 'analytic_inherited_index_grammar',
    'pl_corpus': 'pl_corpus_index_grammar',
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def collect_sections(catalog, regional_inventory, volumes=(), replay_volumes=()):
    represented = set()
    if not replay_volumes:
        if catalog is None:
            raise ValueError('Catalog-free extraction requires an explicit replay selection')
        with closing(readonly(catalog)) as db:
            represented = {r[0] for r in db.execute('SELECT DISTINCT volume FROM items')}
    selected = defaultdict(list)
    with regional_inventory.open() as handle:
        for line in handle:
            section = json.loads(line)
            if section['kind'] not in INDEX_KINDS:
                continue
            if replay_volumes:
                if section['volume'] not in replay_volumes:
                    continue
            elif section['volume'] in represented:
                continue
            if volumes and section['volume'] not in volumes:
                continue
            selected[section['volume']].append(section)
    return selected


def section_context(section, sources):
    """Retain literal opening/notation evidence with exact current-source spans."""
    evidence, seen = [], set()
    first_source = section['spans'][0]['source']
    # A displayed opening may span adjacent XML header blocks. These subtitles
    # are context even when the regional body excludes ordinary running heads.
    first_raw = sources.get(first_source)
    if first_raw is not None:
        opening = section['spans'][0]['span'][0]
        collecting = False
        for block in blocks(first_raw):
            a, b = block['body_start'], block['body_end']
            if a <= opening < b and block['kind'] == 'cabecalho':
                collecting = True
            if collecting:
                if block['kind'] != 'cabecalho':
                    break
                seen.add((first_source, a, b))
                evidence.append({'source': first_source, 'span': [a, b],
                                 'quote': first_raw[a:b], 'role': 'opening_heading'})
    for position, span in enumerate(section['spans']):
        source = span['source']
        if source != first_source:
            continue
        raw = sources.get(source)
        if raw is None:
            continue
        for block in blocks(raw):
            a, b = block['body_start'], block['body_end']
            if not (a < span['span'][1] and span['span'][0] < b):
                continue
            text = raw[a:b]
            opening = position == 0 and a <= span['span'][0] < b
            notice = len(text) < 1400 and re.search(
                r'numer|renvoy|revocatur|indicantur|indiqu|variantes|chapitres|paginas|page et', text, re.I)
            heading = block['kind'] in {'cabecalho','titulo'} and len(text) < 700
            if opening and len(text) > 1400:
                a,b=span['span']
                text=raw[a:b]
            if (opening or notice or heading) and (source,a,b) not in seen:
                seen.add((source,a,b))
                evidence.append({'source':source,'span':[a,b],'quote':text,
                                 'role':'opening' if opening else 'notation_or_heading'})
    title = evidence[0]['quote'] if evidence else ''
    result = {'title':title,'notice':'\n'.join(e['quote'] for e in evidence[1:]),
            'text':'\n'.join(e['quote'] for e in evidence), 'kind':section['kind'],
            **({'internal_index_family':section['internal_index_family']} if section.get('internal_index_family') else {}),
            'evidence':evidence}
    # Reviewed plans select a grammar, not inferred content. Preserve explicit
    # namespace notices separately from the body supplied to that grammar.
    result.update(section.get('parser_context', {}))
    for key in ('family', 'namespace'):
        if section.get(key):
            result[key] = section[key]
    for notice in section.get('notice_evidence', []):
        result['evidence'].append(notice)
        result['text'] += '\n' + notice['quote']
    return result


def valid_entry(raw, entry):
    for raw_key, span_key in [('owner_raw','owner_span'),('reference_raw','reference_span')]:
        value, span = entry.get(raw_key), entry.get(span_key)
        if value is None and span is None and raw_key == 'reference_raw':
            continue
        if not (isinstance(span,list) and len(span)==2 and all(isinstance(x,int) for x in span)
                and 0 <= span[0] < span[1] <= len(raw) and raw[span[0]:span[1]] == value):
            return False
    a,b=entry['entry_span']
    for fragment in entry.get('owner_fragments',[]):
        span=fragment.get('span')
        if not (isinstance(span,list) and len(span)==2 and all(isinstance(x,int) for x in span)
                and a<=span[0]<span[1]<=b<=len(raw)
                and raw[span[0]:span[1]]==fragment.get('raw')
                and fragment.get('source')==entry.get('source')):
            return False
    return 0 <= a < b <= len(raw) and all(
        span is None or a <= span[0] < span[1] <= b
        for span in [entry.get('owner_span'),entry.get('reference_span')])


def recover_volume(volume, sections):
    from tools.indexing.missing_po_region_parser import coalesce_region_scopes
    sources, errors = {}, {}
    for source in {p['source'] for s in sections for p in s['spans']}:
        try:
            sources[source] = Path(source).read_text()
        except (OSError,UnicodeError) as exc:
            errors[source] = str(exc)
    entries, pages, region_records, residuals = {}, [], [], []
    for section in sections:
        route = section.get('parser') or ('po' if volume.startswith('PO') else 'latin')
        if route not in PARSER_MODULES:
            raise ValueError('Unsupported regional parser: ' + route)
        parser = importlib.import_module('tools.indexing.' + PARSER_MODULES[route]).parse_region_page
        sid=section['section_id']
        context=section_context(section,sources)
        region_records.append({'section_id':sid,'volume':volume,'kind':section['kind'],
                               'context':context,'end_status':section.get('end_status')})
        by_page=defaultdict(list)
        for span in section['spans']:
            by_page[(span['physical'],span['source'])].append(span)
        rolling_context={sid:context}
        for (physical,source), spans in sorted(by_page.items()):
            base={'section_id':sid,'volume':volume,'source':source,'physical':physical}
            if source in errors:
                pages.append({**base,'status':'source_unavailable','error':errors[source]})
                continue
            raw=sources[source]; checksum=digest(raw)
            scopes=[{'span':sp['span'],'section_id':sid,'kind':section['kind']} for sp in spans
                    if 0 <= sp['span'][0] < sp['span'][1] <= len(raw)]
            scopes=coalesce_region_scopes(raw,scopes)
            changed=any(sp.get('source_sha256') and sp['source_sha256']!=checksum for sp in spans)
            if not scopes:
                pages.append({**base,'status':'invalid_region_bounds','source_sha256':checksum})
                continue
            result=parser(raw,source=source,volume=volume,physical=physical,
                          scopes=scopes,section_context=rolling_context)
            # Stateful parsers return only explicit editorial context, never destinations.
            if result.get('context'):
                rolling_context=result['context']
            accepted, invalid=0,0
            for entry in result['entries']:
                if not valid_entry(raw,entry):
                    invalid+=1
                    residuals.append({**base,'reason':'invalid_parser_literal','proposal':entry})
                    continue
                a,b=entry['entry_span']
                if not any(s['span'][0]<=a<b<=s['span'][1] for s in scopes):
                    invalid+=1
                    residuals.append({**base,'reason':'parser_entry_outside_region','proposal':entry})
                    continue
                key=digest(encoded([source,checksum,entry['entry_span'],entry.get('reference_span')]))
                item={**entry,'id':'region-entry:'+key,'group_id':'region-group:'+key,
                      'volume':volume,'source':source,'physical':physical,'source_sha256':checksum,
                      'source_span':entry.get('reference_span'), 'section_id':sid,
                      'region_kind':section['kind'],
                      'regional_source_changed':changed,'version':VERSION,
                      'canonical_write_allowed':False,'certified':False}
                if section.get('selection_plan'):
                    item['reviewed_source_section'] = {
                        'section_id': sid, 'kind': section['kind'],
                        'opening_raw': section.get('reviewed_opening'),
                        'family': section.get('family'),
                        'plan': section['selection_plan'],
                        'notice_evidence': section.get('notice_evidence', []),
                        'editorial_context': section.get('parser_context', {}),
                    }
                    if any(proof.get('source') == source and
                           proof['span'][0] <= a < b <= proof['span'][1]
                           for proof in section.get('notice_evidence', [])):
                        item['source_role'] = 'editorial_notation_notice'
                        item['entry_kind'] = 'editorial_note'
                        item['flags'] = item.get('flags', []) + ['within_declared_editorial_notice']
                item['region_component_spans']=next(s.get('component_spans',[s['span']])
                    for s in scopes if s['span'][0]<=a<b<=s['span'][1])
                old=entries.get(key)
                if old:
                    old.setdefault('additional_section_ids',[]).append(sid)
                    if old.get('reference_namespace') != item.get('reference_namespace'):
                        old['reference_namespace']='namespace_conflict'
                        old.setdefault('flags',[]).append('conflicting_region_namespace')
                else:
                    entries[key]=item
                    accepted+=1
            pages.append({**base,'status':'processed_with_entries' if accepted else 'processed_without_new_entries',
                          'entries_added':accepted,'parser_entries':len(result['entries']),
                          'invalid_proposals':invalid,'source_sha256':checksum,
                          'regional_source_changed':changed})
            residuals.extend({**base,**r} for r in result.get('residuals',[]))
    return {'volume':volume,'entries':list(entries.values()),'pages':pages,
            'regions':region_records,'residuals':residuals}


def run(catalog, regional_inventory, out, workers=20, volumes=(), selection_manifest=None):
    replay=[]
    if selection_manifest:
        manifest=json.loads(selection_manifest.read_text())
        if manifest.get('version') != VERSION:
            raise ValueError('Unsupported selection manifest')
        replay=[r['volume'] for r in manifest['by_volume']]
        if not replay:
            raise ValueError('Empty replay selection')
    selected=collect_sections(catalog,regional_inventory,volumes,replay)
    out.mkdir(parents=True,exist_ok=False)
    counts=Counter(); by_volume=[]; page_sources=set(); unavailable_sources=set()
    with sqlite3.connect(out/'search.sqlite') as db:
        db.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE items(id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,label TEXT,ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT);
          CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
          CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
          CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume,tokenize='unicode61 remove_diacritics 2');''')
        with (out/'execution.jsonl').open('w') as execution, (out/'regions.jsonl').open('w') as regions_file, (out/'residuals.jsonl').open('w') as residual_file, ProcessPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(recover_volume,v,ss):v for v,ss in selected.items()}
            for future in as_completed(futures):
                result=future.result(); vc=Counter()
                for entry in result['entries']:
                    # Keep context for display separate from the literal owner evidence.
                    label=entry.get('search_label') or entry['owner_raw']
                    refs=entry.get('reference_raw') or ''
                    meta={**entry,'entry':{k:entry[k] for k in ('entry_span','owner_span','owner_raw','reference_span','reference_raw') if k in entry},
                          'group_flags':entry.get('flags',[])+['regional_extraction_candidate']}
                    row=(entry['id'],entry['group_id'],entry['volume'],label,refs,entry['source'],'source_only',encoded(meta))
                    cur=db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',row)
                    db.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)',(cur.lastrowid,normalize(label),normalize(refs),entry['volume']))
                    vc['entries']+=1
                    vc['with_reference' if refs else 'without_reference']+=1
                    vc['namespace:'+entry.get('reference_namespace','unknown')]+=1
                for page in result['pages']:
                    execution.write(encoded(page)+'\n'); vc['page_region:'+page['status']]+=1
                    page_sources.add(page['source'])
                    if page['status'] in {'source_unavailable','invalid_region_bounds'}:
                        unavailable_sources.add(page['source'])
                    vc['invalid_proposals']+=page.get('invalid_proposals',0)
                for region in result['regions']:regions_file.write(encoded(region)+'\n')
                for residue in result['residuals']:residual_file.write(encoded(residue)+'\n')
                db.commit();counts.update(vc);by_volume.append({'volume':result['volume'],**dict(vc)})
                print(encoded({'volume':result['volume'],**dict(vc)}),flush=True)
        summary={'version':VERSION,'catalog':str(catalog.resolve()) if catalog else None,'regional_inventory':str(regional_inventory.resolve()),
                 'volumes_examined':len(selected),'regions_examined':sum(map(len,selected.values())),
                 'source_files_examined':len(page_sources),'source_files_unavailable_or_invalid':len(unavailable_sources),
                 'selection_manifest':str(selection_manifest.resolve()) if selection_manifest else None,
                 'volumes_with_entries':sum(r.get('entries',0)>0 for r in by_volume),'counts':dict(counts),
                 'by_volume':sorted(by_volume,key=lambda r:r['volume']),'workers':workers,
                 'code_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [Path(__file__),Path(__file__).with_name('missing_latin_region_parser.py'),Path(__file__).with_name('missing_po_region_parser.py'),
                     *[Path(__file__).with_name(name + '.py') for name in sorted(set(PARSER_MODULES.values()))
                       if Path(__file__).with_name(name + '.py').exists()]]},
                 'canonical_updates':0,'inference_calls':0,'quality':'Literal candidates from unverified regional envelopes; not complete historical indices.'}
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(encoded({k:v for k,v in summary.items() if k!='by_volume'}))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--regions',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,default=20)
    p.add_argument('--volume',action='append',default=[])
    p.add_argument('--selection-manifest',type=Path,help='Replay the volumes from a previous summary.json, including volumes now in the catalog')
    a=p.parse_args();run(a.catalog,a.regions,a.out,a.workers,a.volume,a.selection_manifest)
