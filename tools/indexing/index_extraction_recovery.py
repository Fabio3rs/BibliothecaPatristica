"""Offline clause refinement, residual triage and reviewed multi-source assembly."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import re
import sqlite3

from tools.indexing import entry_parser_literal_rows as parser
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import source_span, display, digest, ROOT
from tools.indexing.structured_index_experiment import (
    read_selection, resolve, check_span, key, encoded, consolidate, SELECTION_VERSION,
)

VERSION = 'index-extraction-recovery-v1'


def refine_page(record):
    page = deepcopy(record['page'])
    comparisons = []
    for entry in page['entries']:
        old = entry['reference_groups']
        new = parser.attributed_reference_groups(record['raw'], entry['char_start'],
                                                  entry['char_end'], page['profile'])
        if not new or any(not g['owner_text_raw'].strip() for g in new):
            raise ValueError('Refinement removed the owner or all reference groups')
        # Every old number must still be represented as a reference, even if a
        # faulty punctuation boundary is corrected. Never silently lose a locator.
        for group in old:
            for match in re.finditer(r'\d+', group['raw']):
                a, b = group['char_start']+match.start(), group['char_start']+match.end()
                if not any(g['char_start'] <= a < b <= g['char_end'] for g in new):
                    raise ValueError('Refinement lost a previously extracted numeric occurrence')
        if old != new:
            comparisons.append({'source':page['source'], 'entry_id':entry['id'],
                                'entry_span':[entry['char_start'],entry['char_end']],
                                'before':old, 'after':new})
        entry['reference_groups'] = new
        entry['references_raw'] = new[0]['raw'] if len(new)==1 else None
        entry['reference_span'] = ({k:new[0][k] for k in source_span(record['raw'],0,0)}
                                   if len(new)==1 else None)
        entry['text_raw'] = new[0]['owner_text_raw']
        entry['text_display'] = display(entry['text_raw'])
    page['clause_refinement'] = {'version':VERSION, 'parser_version':parser.VERSION,
                                'parser_sha256':digest(Path(parser.__file__).read_bytes()),
                                'original_page_sha256':digest(encoded(record['page']).encode())}
    return page, comparisons


def residual_cause(item):
    raw, reason = item['raw'].strip(), item['reason']
    if re.match(r'^(?:INDEX ANALYTICUS|RERUM ET VERBORUM|Quæ in S\.|Numerus Romanus)', raw):
        return 'editorial_title_or_numbering_notice'
    if re.fullmatch(r'\d+[.]?',raw):
        return 'unowned_number_or_page_marker'
    if 'displaced' in reason:
        return 'displaced_locator'
    if (re.search(r'(?:Supplem?\w*[.]?|Registr[.].*imp[.])[, .]*$',raw)
            or (not re.search(r'\d',raw) and re.search(r'\b[IVXLCDM]+[, .]*$',raw))):
        return 'interrupted_locator'
    if raw.endswith('-') or reason == 'open_fragment_at_region_or_column_end':
        return 'column_or_page_continuation'
    if re.search(r'\b[IVXLCDM]+,\s*[A-D](?:[., ]|$)',raw):
        return 'missing_numeric_anchor'
    if re.search(r'\w\s+\d+[.]?$',raw) or re.search(r'\b[IVXLCDM]+\s+\d',raw):
        return 'missing_locator_delimiter'
    if reason == 'incomplete_or_unsupported_locator' and not re.search(r'\d',raw):
        return 'open_text_fragment'
    return 'unsupported_or_corrupt_locator'


def account_source(record):
    """Exhaustive character partition, including XML and unresolved source text."""
    raw, page = record['raw'], record['page']
    labels = ['unparsed_source_text'] * len(raw)
    for block in blocks(raw):
        if block['kind'] != 'texto_principal':
            labels[block['body_start']:block['body_end']] = ['editorial_or_other_block_candidate'] * (
                block['body_end']-block['body_start'])
    for name, items in [('parser_residual',page['residuals']), ('structured_unit',page['entries'])]:
        for item in items:
            a,b = item['char_start'],item['char_end']
            labels[a:b] = [name]*(b-a)
    for m in re.finditer(r'<[^>]*>',raw):
        labels[m.start():m.end()] = ['xml_markup']*len(m[0])
    for i,c in enumerate(raw):
        if c.isspace(): labels[i] = 'whitespace'
    runs=[]
    for i,category in enumerate(labels):
        if runs and runs[-1]['category']==category: runs[-1]['end']=i+1
        else:runs.append({'start':i,'end':i+1,'category':category})
    counts=Counter(labels)
    assert sum(counts.values())==len(raw)
    return {'source':page['source'],'source_sha256':page['sha256'], 'characters':len(raw),
            'categories':dict(counts),'runs':runs,'all_characters_categorized':True,
            'printed_completeness_verified':False}


LOGICAL_SCHEMA = '''
CREATE TABLE logical_entries(logical_id TEXT PRIMARY KEY,display_text TEXT NOT NULL,
 virtual_text TEXT NOT NULL,continuity_status TEXT NOT NULL,reference_reading TEXT NOT NULL,evidence_json TEXT NOT NULL);
CREATE TABLE logical_fragments(logical_id TEXT REFERENCES logical_entries,ordinal INTEGER,
 source_id TEXT REFERENCES sources,char_start INTEGER,char_end INTEGER,literal TEXT NOT NULL,
 PRIMARY KEY(logical_id,ordinal));
CREATE TABLE logical_references(reference_id TEXT PRIMARY KEY,logical_id TEXT REFERENCES logical_entries,
 owner_text TEXT NOT NULL,literal TEXT NOT NULL,owner_fragments_json TEXT NOT NULL,
 reference_fragments_json TEXT NOT NULL,context_json TEXT NOT NULL);
CREATE TABLE reference_claims(reference_id TEXT PRIMARY KEY REFERENCES reference_groups,
 logical_reference_id TEXT REFERENCES logical_references);
CREATE VIEW effective_reference_groups AS
 SELECT r.reference_id,r.unit_id AS owner_id,'physical_unit' AS owner_kind,
 c.owner_raw AS owner_text,r.literal FROM reference_groups r JOIN clauses c USING(clause_id)
 WHERE NOT EXISTS(SELECT 1 FROM reference_claims x WHERE x.reference_id=r.reference_id)
 UNION ALL SELECT reference_id,logical_id,'logical_entry',owner_text,literal FROM logical_references;
'''


def project(parts, start, end):
    result=[]
    for part in parts:
        a,b=max(start,part['virtual_start']),min(end,part['virtual_end'])
        if a<b:
            x=part['char_start']+a-part['virtual_start']
            result.append({'source_id':part['source_id'],'char_start':x,'char_end':x+b-a,
                           'raw':part['raw'][a-part['virtual_start']:b-part['virtual_start']]})
    return result


def assemble(con, pages, reviews, root):
    by_path={record['path']:record for record in pages}
    claimed=[]
    result=[]
    for review in reviews:
        if review.get('decision')!='assemble_layout_only' or len(review.get('fragments',[]))<2:
            raise ValueError('Assembly requires an explicit reviewed layout decision')
        fragments=[];virtual=''; previous=None
        for item in review['fragments']:
            path=resolve(item['source'],root);record=by_path.get(path)
            if not record or item['source_sha256']!=record['page']['sha256']:
                raise ValueError('Assembly source missing or stale')
            identity=(record['volume'],record['page']['physical_page'])
            if previous and (identity[0]!=previous[0] or identity[1]-previous[1] not in (0,1)):
                raise ValueError('Continuation must stay in the same volume and adjacent pages')
            previous=identity
            a,b=check_span(record['raw'],item,item['raw'])
            image=resolve(item['image'],root)
            if digest(image.read_bytes())!=item['image_sha256']:
                raise ValueError('Assembly image evidence is stale')
            if any(p==path and a<y and x<b for p,x,y in claimed):
                raise ValueError('Overlapping logical assembly claims')
            claimed.append((path,a,b))
            sid=key('source',str(path),item['source_sha256'])
            if virtual:virtual+='\n'
            start=len(virtual);virtual+=item['raw']
            fragments.append({**item,'source_id':sid,'virtual_start':start,'virtual_end':len(virtual)})
        groups=parser.attributed_reference_groups(virtual,0,len(virtual),'analytic-tome-rows-v1')
        if len(groups)!=1 or virtual[groups[-1]['char_end']:].strip():
            raise ValueError('Initial assembly supports one complete literal locator only')
        lid=key('logical',[(p['source_id'],p['char_start'],p['char_end']) for p in fragments])
        con.execute('INSERT INTO logical_entries VALUES(?,?,?,?,?,?)',
                    (lid,display(virtual),virtual,'visually_supported_layout',
                     'not_certified',encoded(review)))
        for n,part in enumerate(fragments):
            con.execute('INSERT INTO logical_fragments VALUES(?,?,?,?,?,?)',
                        (lid,n,part['source_id'],part['char_start'],part['char_end'],part['raw']))
        group=groups[0]
        owner=project(fragments,group['owner_span']['char_start'],group['owner_span']['char_end'])
        refs=project(fragments,group['char_start'],group['char_end'])
        rid=key('logical_ref',lid,refs)
        con.execute('INSERT INTO logical_references VALUES(?,?,?,?,?,?,?)',
                    (rid,lid,display(group['owner_text_raw']),group['raw'],encoded(owner),
                     encoded(refs),encoded({'inheritance':'not_inferred'})))
        old_claims=[]
        for part in refs:
            matches=con.execute('''SELECT r.* FROM reference_groups r JOIN units u USING(unit_id)
                WHERE u.source_id=? AND r.char_start<? AND ?<r.char_end''',
                (part['source_id'],part['char_end'],part['char_start'])).fetchall()
            for old in matches:
                if old['char_start']!=part['char_start'] or old['char_end']!=part['char_end']:
                    raise ValueError('Partial reference overlap requires separate review')
                con.execute('INSERT INTO reference_claims VALUES(?,?)',(old['reference_id'],rid))
                old_claims.append(old['reference_id'])
        result.append({'logical_id':lid,'display_text':display(virtual),'reference':group['raw'],
                       'owner_fragments':owner,'reference_fragments':refs,'replaced_reference_ids':old_claims,
                       'reference_reading':'not_certified'})
    return result,claimed


def remainder(raw,a,b,claims):
    intervals=[(a,b)]
    for x,y in claims:
        intervals=[part for l,r in intervals for part in [(l,min(r,x)),(max(l,y),r)]
                   if part[0]<part[1]] if any(x<r and l<y for l,r in intervals) else intervals
    return [{'raw':raw[l:r],**source_span(raw,l,r)} for l,r in intervals if raw[l:r].strip()]


def _run(manifest_path,out,assembly_path=None,root=ROOT):
    root=Path(root).resolve();manifest_path=Path(manifest_path);out=Path(out)
    manifest,records=read_selection(manifest_path,root)
    out.mkdir(parents=True,exist_ok=False);(out/'pages').mkdir()
    new_sources=[];diffs=[];accounts=[]
    for record in records:
        page,changes=refine_page(record);diffs.extend(changes)
        record['page']=page
        path=out/'pages'/f"{record['volume']}-{page['physical_page']}.json"
        path.write_text(encoded(page)+'\n')
        new_sources.append({'source':page['source'],'sha256':page['sha256'],'physical':page['physical_page'],
                            'artifact':str(path.resolve()),'units':len(page['entries'])})
        accounts.append(account_source(record))
    selected=out/'selection.json'
    selected.write_text(encoded({'version':SELECTION_VERSION,'sources':new_sources,
                       'continuation_artifacts':manifest.get('continuation_artifacts',[])})+'\n')
    summary=consolidate(selected,out/'structured',root,16,42)
    assembly_manifest=json.loads(Path(assembly_path).read_text()) if assembly_path else {'version':'logical-continuity-review-v1','reviews':[]}
    if assembly_manifest.get('version')!='logical-continuity-review-v1':
        raise ValueError('Unknown assembly review version')
    con=sqlite3.connect(out/'structured/index.sqlite');con.row_factory=sqlite3.Row
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.executescript(LOGICAL_SCHEMA)
        with con:
            logical,claims=assemble(con,records,assembly_manifest['reviews'],root)
            if con.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Invalid logical relations')
            effective=con.execute('SELECT count(*) FROM effective_reference_groups').fetchone()[0]
            con.execute('INSERT INTO metadata VALUES(?,?)',('extraction_recovery',encoded({
                'version':VERSION,'code_sha256':digest(Path(__file__).read_bytes()),
                'input_manifest_sha256':digest(manifest_path.read_bytes()),'assembly_reviews':assembly_manifest})))
    finally:con.close()
    queue=[]
    for record in records:
        for item in record['page']['residuals']:
            remaining=remainder(record['raw'],item['char_start'],item['char_end'],
                                [(a,b) for p,a,b in claims if p==record['path']])
            queue.append({'source':str(record['path']),'source_sha256':record['page']['sha256'],
                          'volume':record['volume'],'physical':record['page']['physical_page'],
                          **item,'cause':residual_cause(item),'remaining_after_assembly':remaining,
                          'nonspace_characters':sum(not c.isspace() for c in item['raw']),
                          'numeric_tokens':len(re.findall(r'\d+',item['raw']))})
    for name,items in [('clause_changes',diffs),('source_accounting',accounts),('residual_queue',queue),('logical_entries',logical)]:
        (out/f'{name}.jsonl').write_text(''.join(encoded(item)+'\n' for item in items))
    causes={}
    for item in queue:
        s=causes.setdefault(item['cause'],{'records':0,'nonspace_characters':0,'numeric_tokens':0})
        for field in ['nonspace_characters','numeric_tokens']:s[field]+=item[field]
        s['records']+=1
    summary.update(changed_units=len(diffs),logical_entries=len(logical),
                   effective_reference_groups=effective,residual_causes=causes,
                   residual_records_fully_used_by_continuity=sum(not q['remaining_after_assembly'] for q in queue),
                   remaining_residual_characters=sum(sum(not c.isspace() for c in p['raw']) for q in queue for p in q['remaining_after_assembly']),
                   accounting_categories=dict(sum((Counter(a['categories']) for a in accounts),Counter())),
                   rule_potential_scope='selected 23 pages only; no corpus-wide extrapolation')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


def run(manifest_path,out,assembly_path=None,root=ROOT):
    out=Path(out)
    existed=out.exists()
    try:
        return _run(manifest_path,out,assembly_path,root)
    except BaseException:
        if not existed and out.exists():
            (out/'FAILED.txt').write_text('Recovery incomplete; use only a successful root summary.\n')
        raise


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--manifest',type=Path,required=True)
    cli.add_argument('--out',type=Path,required=True)
    cli.add_argument('--assembly-reviews',type=Path)
    cli.add_argument('--root',type=Path,default=ROOT)
    args=cli.parse_args()
    print(json.dumps(run(args.manifest,args.out,args.assembly_reviews,args.root),ensure_ascii=False,indent=2))
