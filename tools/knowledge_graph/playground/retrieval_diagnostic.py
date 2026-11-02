"""Small fixed BM25 diagnostic, not the production Indexador or a corpus-wide eval."""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
import json

from .ab_reporting import label_key, load_ab
from .common import digest, read_json

ENGINE = {'name':'kg-diagnostic-bm25-v1','k1':1.2,'b':.75,'min_score':0,
          'tokenization':'Unicode alphanumeric, NFKD accent folding, casefold, fixed Portuguese stopwords',
          'stopwords':'a o as os de do da dos das e em um uma para por com que no na nos nas ao aos'.split(),
          'concept_projection':'label + meaning, exactly once; no generated synonyms/queries',
          'tie_break':'physical_page_lexicographic'}


def search_design(fixture):
    return {**{k:fixture.get(k) for k in ('experiment','pages','arms','missing_output_policy','engine_hash','source_plan_hash')},
            'queries':[{k:q[k] for k in ('id','text','intent')} for q in fixture['queries']]}


def prepare_judgments(db,experiment,output, *, query_file=None, arms=None, missing_output_policy='strict'):
    target=Path(output)
    if target.exists():raise ValueError('Judgments file already exists; never overwrite reviewer decisions')
    plan,runs=load_ab(db,experiment)
    proposals=read_json(query_file or Path(__file__).parent/'fixtures/navigation_queries.json')
    arms = list(plan['arms']) if arms is None else list(arms)
    if not arms or len(arms) != len(set(arms)) or not set(arms) <= set(plan['arms']):
        raise ValueError('Select distinct existing extraction arms')
    if missing_output_policy not in ('strict','summary_only'):
        raise ValueError('Unknown missing-output policy')
    records=[]
    query_ids=[q['id'] for q in proposals['queries']]
    if not query_ids or len(query_ids)!=len(set(query_ids)):
        raise ValueError('Query proposals must have distinct nonempty IDs')
    for q in proposals['queries']:
        judgments=[]
        for case in plan['cases']:
            example=next((x for x in q.get('examples',[]) if x['page']==case['page'] and x['snapshot_hash']==case['snapshot_hash']),None)
            judgments.append({'page':case['page'],'snapshot_hash':case['snapshot_hash'],
                              'baseline_run_id':case['run_id'],'status':'pending','grade':None,
                              'reviewer':'','source_checked':False,'independent_of_generator':False,
                              'rationale':'','provisional_example':example})
        records.append({'id':q['id'],'text':q['text'],'intent':q['intent'],'judgments':judgments})
    data={'experiment':experiment,'pages':[c['page'] for c in plan['cases']],
          'arms':arms,'missing_output_policy':missing_output_policy,'engine_hash':digest(ENGINE),
          'source_plan_hash':digest(plan),'query_proposals_hash':digest(proposals),
          'instructions':proposals['instruction'],'grades':{'0':'not relevant','1':'useful secondary discussion','2':'substantial relevant discussion'},
          'queries':records}
    data['design_hash']=digest(search_design(data))
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    snapshots = {r['page']:r['snapshot'] for r in runs}
    with target.with_name(target.stem+'.sources.jsonl').open('w',encoding='utf-8') as stream:
        for page in data['pages']:
            stream.write(json.dumps({'page':page,'snapshot':snapshots[page]},ensure_ascii=False)+'\n')
    return {'experiment':experiment,'output':str(target),'queries':len(records),'pending_judgments':len(records)*len(plan['cases'])}


def tokenize(text):
    return [t for t in re.findall(r'[^\W_]+',label_key(text)) if t not in ENGINE['stopwords']]


def rank(documents, query):
    bags={page:Counter(tokenize(text)) for page,text in documents.items()}
    n=len(bags);average=sum(sum(x.values()) for x in bags.values())/max(n,1) or 1
    df=Counter(word for bag in bags.values() for word in bag)
    terms=set(tokenize(query));scores=[]
    for page,bag in bags.items():
        length=sum(bag.values());score=0.
        for term in terms:
            tf=bag[term]
            if tf:
                idf=math.log(1+(n-df[term]+.5)/(df[term]+.5))
                score+=idf*tf*(ENGINE['k1']+1)/(tf+ENGINE['k1']*(1-ENGINE['b']+ENGINE['b']*length/average))
        if score>ENGINE['min_score']:scores.append({'page':page,'score':score})
    return sorted(scores,key=lambda x:(-x['score'],x['page']))


def retrieval_metrics(ranking, grades, k):
    if k<1:raise ValueError('k must be positive')
    relevant={p for p,g in grades.items() if g>0}
    if not relevant:return None
    pages=[x['page'] for x in ranking];top=pages[:k]
    hit=len(set(top)&relevant)
    dcg=sum((2**grades[p]-1)/math.log2(i+2) for i,p in enumerate(top))
    ideal=sum((2**g-1)/math.log2(i+2) for i,g in enumerate(sorted(grades.values(),reverse=True)[:k]))
    return {'precision_at_k':hit/k,'recall_at_k':hit/len(relevant),
            'reciprocal_rank':next((1/(i+1) for i,p in enumerate(pages) if p in relevant),0.),
            'ndcg_at_k':dcg/ideal if ideal else None}


def evaluate_search(db,experiment,judgments,k=5):
    if type(k) is not int or k<1:raise ValueError('k must be positive')
    plan,runs=load_ab(db,experiment);fixture=read_json(judgments)
    if 'design_hash' in fixture and fixture['design_hash']!=digest(search_design(fixture)):
        raise ValueError('Query wording or frozen search design changed; prepare a new diagnostic')
    arms=fixture.get('arms',list(plan['arms']))
    missing_policy=fixture.get('missing_output_policy','strict')
    if not arms or len(arms)!=len(set(arms)) or not set(arms)<=set(plan['arms']):
        raise ValueError('Unknown or duplicate search arm')
    if missing_policy not in ('strict','summary_only'):
        raise ValueError('Unknown missing-output policy')
    if fixture.get('engine_hash',digest(ENGINE))!=digest(ENGINE) or fixture.get('source_plan_hash',digest(plan))!=digest(plan):
        raise ValueError('Frozen retrieval engine or source plan changed')
    if fixture.get('experiment') not in (None,experiment):raise ValueError('Judgments belong to another experiment')
    pages={c['page'] for c in plan['cases']}
    cases={r['page']:r for r in runs}
    if set(fixture['pages'])!=pages:raise ValueError('Judgment pool must match all frozen physical pages')
    query_ids=[q['id'] for q in fixture['queries']]
    if len(query_ids)!=len(set(query_ids)):raise ValueError('Duplicate query IDs')
    judgments_by_query={}
    for q in fixture['queries']:
        records={}
        for j in q['judgments']:
            page=j['page']
            if page not in pages or page in records:raise ValueError('Unknown or duplicate page/query judgment')
            if j['snapshot_hash']!=cases[page]['snapshot_hash']:raise ValueError('Judgment source hash mismatch')
            if j.get('status')=='adjudicated':
                if not j.get('reviewer') or not j.get('source_checked') or not j.get('independent_of_generator'):
                    raise ValueError('Relevance requires independent named source review')
                if type(j.get('grade')) is not int or j['grade'] not in (0,1,2):raise ValueError('Relevance grade must be 0, 1 or 2')
                if not j.get('rationale'):raise ValueError('Relevance judgment needs a source-based rationale')
                records[page]=j['grade']
        judgments_by_query[q['id']]=records
    latest={}
    for r in runs:latest[(r['page'],r['ab']['arm'],r['repetition'])]=r
    summary={page:'\n'.join(s['summary'] for s in cases[page]['snapshot']['v2']['segments']) for page in pages}
    results=[]
    for rep in sorted({s['repetition'] for s in plan['schedule']}):
        for arm in ['summary',*arms]:
            documents=dict(summary);missing=[]
            if arm!='summary':
                for page in pages:
                    r=latest.get((page,arm,rep))
                    if r is None or not (r['validation'] or {}).get('structural_valid'):
                        missing.append(page);continue
                    extraction=(r['result'] or {}).get('parsed_json') or {}
                    documents[page]+='\n'+'\n'.join(' '.join(str(c.get(k,'')) for k in ('label','meaning'))
                                                   for c in extraction.get('concepts',[]))
            for q in fixture['queries']:
                grades=judgments_by_query[q['id']]
                available = not missing or missing_policy=='summary_only'
                ranking=rank(documents,q['text']) if available else []
                complete=len(grades)==len(pages)
                measured=retrieval_metrics(ranking,grades,k) if complete and available else None
                results.append({'query_id':q['id'],'query':q['text'],'arm':arm,'repetition':rep,
                                'ranking':ranking,'metrics':measured,'adjudicated_pages':len(grades),
                                'missing_outputs':sorted(missing),
                                'summary_only_fallback_pages':sorted(missing) if missing_policy=='summary_only' else [],
                                'metric_status':'computed' if measured else 'withheld: incomplete judgments, unavailable outputs under selected policy, or no relevant page',
                                'document_hashes':{p:digest(t) for p,t in documents.items()} if available else {}})
    return {'experiment':experiment,'engine':ENGINE,'engine_hash':digest(ENGINE),'k':k,'missing_output_policy':missing_policy,
            'judgment_file_hash':digest(fixture),'results':results,
            'limits':[f'{len(pages)}-page diagnostic; not a corpus-wide retrieval estimate',
                      'Same engine and configuration in all arms; IDF reflects each arm document collection',
                      f'No unjudged pages are assumed negative; all {len(pages)} need independent adjudication for metrics',
                      'Query proposals and assistant-provisional relevance are not independent ground truth',
                      'Evidence-invalid but structurally valid extractions remain visible for diagnostic retrieval; semantic review is separate',
                      'No stemming, embeddings, production Indexador integration, or global aliases']}
