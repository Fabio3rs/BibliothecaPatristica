"""Audit global header coincidences against editorial notices and literal context.

This is a separate rejection/review layer, not a destination certifier.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import json
from pathlib import Path
import random
import re
import sqlite3

from tools.corpus_utils import page_files
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.structured_index_experiment import encoded

VERSION='header-target-audit-v2'
ROMAN_SUFFIX=re.compile(r'[,;]\s*(?P<roman>[IVXLCDM]+)\s*[,.]?\s*$')
NOTICE=re.compile(r'(?:revocatur.{0,100}numer|numer.{0,180}(?:crass|grand|denot|indicant|correspond)|pagin.{0,100}edition|numero romano.{0,50}tom)',re.I|re.S)


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def stratum(size):
    return 'none' if not size else 'single' if size==1 else '2_4' if size<=4 else '5_20' if size<=20 else 'over20'


def validate_registry(path):
    registry=json.loads(Path(path).read_text())
    if registry.get('version')!='header-target-audit-rules-v1':
        raise ValueError('Unsupported rule version')
    checked={}
    def read(proof):
        source=proof['path']
        if source not in checked:
            data=Path(source).read_bytes();checked[source]=(digest(data),data.decode('utf-8'))
        checksum,raw=checked[source]
        if checksum!=proof['sha256']:
            raise ValueError(f'Stale rule evidence: {source}')
        a,b=proof['char_start'],proof['char_end']
        if not 0<=a<b<=len(raw):
            raise ValueError('Invalid evidence span')
        if 'quote' in proof and raw[a:b]!=proof['quote']:
            raise ValueError('Evidence quote mismatch')
        if proof.get('visual_review') and digest(Path(proof['image_path']).read_bytes())!=proof['image_sha256']:
            raise ValueError('Changed visual evidence')
    for rule in registry['rules']:
        if not rule['proofs'] or not rule['source_regions']:
            raise ValueError('Rule requires evidence and explicit source regions')
        for proof in rule['proofs']+rule['source_regions']:
            read(proof)
    for proof in registry.get('observations',[]):
        read(proof)
    return registry,{k:v[0] for k,v in checked.items()}


def classify(group, rules, source_current=True, targets_current=True):
    issues=[];matched=[];column_namespace=False
    if not source_current:
        issues.append('source_changed')
    if not targets_current:
        issues.append('target_changed')
    for rule,region in rules:
        if region['char_start']<=group['char_start'] and group['char_end']<=region['char_end']:
            matched.append(rule['id'])
            if rule['kind']=='current_edition_columns':
                column_namespace=True
            elif rule['kind']=='internal_text_numbering':
                issues.append('internal_numbering_conflicts_with_header_route')
            else:
                issues.append('tome_column_context_required')
    roman=ROMAN_SUFFIX.search(group.get('owner_raw',''))
    if roman:
        issues.append('roman_locator_in_owner_requires_review')
    issues=list(dict.fromkeys(issues))
    return {'issues':issues,'rule_ids':matched,'roman_context_raw':roman['roman'] if roman else None,
            'status':'review_required' if issues else 'documented_column_namespace' if column_namespace else 'namespace_unverified',
            'verified':False,'import_ready':False,'automatic_destination_mapping':False}


def column_evidence(group, occurrence, markers):
    """Retain all alternatives; a documented namespace does not certify ownership."""
    candidates=[]
    for file in occurrence['files']:
        evidence=[m for m in markers.get((occurrence['number'],file['path']),[])
                  if m['zone']=='cabecalho' and m.get('sequence_support')
                  and m['sha256']==file['sha256']]
        candidates.append({**file,'header_evidence':evidence})
    # More complex ranges, inherited locators and parallel editions need their own grammar.
    simple=bool(re.fullmatch(r'\s*\d+[\d\s,;.a-dA-D]*',group['raw']))
    status=('documented_namespace_notation_pending' if not simple else
            'documented_namespace_no_candidate' if not candidates else
            'documented_namespace_marker_review' if any(not c['header_evidence'] for c in candidates) else
            'documented_namespace_candidate')
    return {'status':status,'column_candidates':candidates,
            'source_assignment_status':group.get('source_assignment_status','not_certified'),
            'source_flags':group.get('flags',[]),'reference_group_raw':group['raw']}


def discover_notices(survey,root,out):
    """Local clues only: never propagate a discovered notice across pages."""
    visited=set();counts=Counter();files={}
    with (out/'notice_candidates.jsonl').open('w') as handle:
        for source_file in sorted((survey/'volumes').glob('*/sources.jsonl')):
            volume=source_file.parent.name
            if volume not in files:
                files[volume]=page_files(root/volume/'text',suffixes=['.txt'])
            for line in source_file.read_text().splitlines():
                source=json.loads(line)['source']
                # Physical ordinals are only for neighboring source-file discovery.
                match=re.search(r'-(\d+)\.txt$',source)
                if not match:
                    continue
                n=int(match[1])
                for i in range(max(1,n-1),n+2):
                    for path in files[volume].get(i,[]):
                        if str(path) in visited:
                            continue
                        visited.add(str(path));data=path.read_bytes();raw=data.decode('utf-8');checksum=digest(data)
                        counts['pages_examined']+=1
                        for block in blocks(raw):
                            for found in NOTICE.finditer(block['body']):
                                a=block['body_start']+max(0,found.start()-60)
                                b=min(block['body_end'],block['body_start']+found.end()+200)
                                handle.write(encoded({'volume':volume,'physical':i,'path':str(path.resolve()),
                                    'sha256':checksum,'char_start':a,'char_end':b,'quote':raw[a:b],
                                    'block_kind':block['kind'],'status':'unreviewed_clue_no_scope_propagation'})+'\n')
                                counts['clues']+=1
    return dict(counts)


def run(survey,registry_path,root,out,discover=True):
    survey,out,root=Path(survey).resolve(),Path(out).resolve(),Path(root).resolve()
    if out.exists():
        raise ValueError('Use a new output directory')
    registry,proof_hashes=validate_registry(registry_path)
    rules=defaultdict(list)
    for rule in registry['rules']:
        for region in rule['source_regions']:
            rules[region['path']].append((rule,region))
    out.mkdir(parents=True)
    hashes={};totals=Counter();by_stratum=defaultdict(Counter);by_volume=defaultdict(Counter);reasons=Counter()
    column_markers=defaultdict(list)
    for volume in {r['volume'] for r in registry['rules'] if r['kind']=='current_edition_columns'}:
        for line in (survey/'volumes'/volume/'header_markers.jsonl').open():
            marker=json.loads(line)
            column_markers[(marker['number'],marker['path'])].append(marker)
    samples=defaultdict(list);seen=Counter();rng=random.Random(73)
    def current(path,expected):
        if path not in hashes:
            try: hashes[path]=digest(Path(path).read_bytes())
            except OSError: hashes[path]=None
        return hashes[path]==expected
    with closing(readonly(survey/'survey.sqlite')) as source, closing(sqlite3.connect(out/'audit.sqlite')) as con, (out/'audit.jsonl').open('w') as handle, (out/'column_candidates.jsonl').open('w') as columns_handle:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''CREATE TABLE audit(occurrence_id TEXT PRIMARY KEY,volume TEXT,source TEXT,
            candidate_count INTEGER,status TEXT,issues_json TEXT,evidence_json TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT);''')
        query='''SELECT g.evidence_json AS group_json,o.evidence_json AS occurrence_json,
                  (SELECT COUNT(*) FROM candidates c WHERE c.occurrence_id=o.id) AS n
                  FROM occurrences o JOIN groups g ON g.id=o.group_id
                  WHERE o.tier='unmapped_namespace_screen'
                  ORDER BY CASE WHEN n=1 THEN 0 WHEN n>1 THEN 1 ELSE 2 END,g.volume,o.id'''
        for row in source.execute(query):
            g,o=json.loads(row['group_json']),json.loads(row['occurrence_json'])
            targets_current=all(current(f['path'],f['sha256']) for f in o['files'])
            result=classify(g,rules.get(g['source'],[]),current(g['source'],g['source_sha256']),targets_current)
            if result['status']=='documented_column_namespace':
                result.update(column_evidence(g,o,column_markers))
            bucket=stratum(row['n'])
            record={'occurrence_id':o['id'],'group_id':o['group_id'],'volume':g['volume'],
                    'source':g['source'],'source_sha256':g['source_sha256'],
                    'reference_span':[o['char_start'],o['char_end']],'number':o['number'],
                    'owner_raw':g['owner_raw'],'candidate_count':row['n'],'candidate_stratum':bucket,
                    'previous_candidates':o['files'],**result}
            counts=by_stratum[bucket];counts['total']+=1;counts[result['status']]+=1
            by_volume[g['volume']][bucket+'_'+result['status']]+=1
            totals[result['status']]+=1;reasons.update(result['issues'])
            con.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?)',(o['id'],g['volume'],g['source'],row['n'],result['status'],encoded(result['issues']),encoded(record)))
            handle.write(encoded(record)+'\n')
            if result['status']=='documented_namespace_candidate':
                columns_handle.write(encoded(record)+'\n')
            sample_key=bucket+':'+result['status'];seen[sample_key]+=1
            if len(samples[sample_key])<8:
                samples[sample_key].append(record)
            else:
                pos=rng.randrange(seen[sample_key])
                if pos<8: samples[sample_key][pos]=record
            if sum(totals.values())%10000==0:
                con.commit();print(encoded({'audited':sum(totals.values()),'stratum':bucket}),flush=True)
        con.commit()
        summary={'version':VERSION,'occurrences_audited':sum(totals.values()),'totals':dict(totals),
                 'by_candidate_count':{k:dict(v) for k,v in by_stratum.items()},'reasons':dict(reasons),
                 'rules':len(registry['rules']),'distinct_source_and_target_files_checked':len(hashes),
                 'source_updates':0,'previous_survey_updates':0,'verified_links':0,'inference_calls':0}
        for path,checksum in proof_hashes.items():
            if digest(Path(path).read_bytes())!=checksum:
                raise ValueError('Rule evidence changed during audit')
        config={'survey':str(survey),'rules_path':str(Path(registry_path).resolve()),
                'rules_sha256':digest(Path(registry_path).read_bytes()),'seed':73,
                'code_sha256':digest(Path(__file__).read_bytes()),'source_survey_config':json.loads((survey/'config.json').read_text())}
        if discover:
            summary['notice_discovery']=discover_notices(survey,root,out)
        with con:
            con.executemany('INSERT INTO metadata VALUES(?,?)',[('summary',encoded(summary)),('config',encoded(config))])
        assert con.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    write_json(out/'summary.json',summary);write_json(out/'config.json',config)
    write_json(out/'rules.json',registry);write_json(out/'samples.json',{'seed':73,'reviewed':False,'samples':dict(samples)})
    columns=sorted({k for c in by_volume.values() for k in c})
    with (out/'by_volume.csv').open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=['volume',*columns]);writer.writeheader()
        for volume,c in sorted(by_volume.items()): writer.writerow({'volume':volume,**c})
    (out/'report.md').write_text('# Auditoria das coincidências de cabeçalhos\n\n'+
        'Nenhuma coincidência é promovida a destino confirmado. Falta de conflito detectado não valida o namespace.\n\n'+
        '```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n')
    return summary


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--survey',type=Path,default=Path('data/index_entry_parser/global_targets_001'))
    ap.add_argument('--rules',type=Path,default=Path('docs/data/header_target_audit_rules_20261105_v2.json'))
    ap.add_argument('--root',type=Path,default=Path('teste'))
    ap.add_argument('--no-notice-discovery',action='store_true')
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    print(json.dumps(run(args.survey,args.rules,args.root,args.out,not args.no_notice_discovery),ensure_ascii=False,indent=2))
