"""Paired automated measurements and explicitly separate independent review."""
from __future__ import annotations

import csv
import io
import json
import statistics
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from itertools import combinations

from .common import ROOT, digest, dumps, now, readonly
from .comparison import compare_pair

DIMENSIONS = ('subject_label_usefulness','subject_coverage_omissions','concept_entity_classification',
              'redundant_concepts','supported_modern_abstractions','actor_attribution',
              'stance_and_relation_targets','hypotheses_preserved','bilingual_disagreements_preserved')


def label_key(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',value.casefold())
                           if not unicodedata.category(c).startswith('M')).split())


def load_ab(db, experiment):
    with readonly(db) as con:
        row=con.execute('SELECT plan_json FROM experiments WHERE id=?',(experiment,)).fetchone()
        if row is None: raise ValueError('Experiment not found')
        plan=json.loads(row[0])
        if plan.get('kind')!='ab': raise ValueError('Expected a prepared A/B experiment')
        # Deliberately do not load image data URLs in prepared_json for reports.
        rows=[]
        for row in con.execute('''SELECT r.id,r.case_id,r.repetition,r.status,r.result_json,r.config_json,
            json_extract(r.prepared_json,'$.ab') ab_json,
            json_extract(r.prepared_json,'$.retry_of') retry_of,
            c.page,c.snapshot_hash,c.snapshot_json,
            (SELECT validation_json FROM run_validation v WHERE v.run_id=r.id ORDER BY v.id DESC LIMIT 1) validation_json
            FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.experiment_id=? ORDER BY r.id''',(experiment,)):
            item=dict(row)
            for key in ('result','config','ab','snapshot','validation'):
                item[key]=json.loads(item.pop(key+'_json') or 'null')
            item['reviews']=[dict(r) for r in con.execute('SELECT * FROM reviews WHERE run_id=? ORDER BY id',(item['id'],))]
            rows.append(item)
    return plan,rows


def metrics(run):
    result=run['result'] or {}; validation=run['validation'] or {}
    data=result.get('parsed_json') or validation.get('parsed') or {}
    if not isinstance(data,dict): data={}
    concepts=data.get('concepts',[])
    labels=[c['label'] for c in concepts if isinstance(c,dict) and isinstance(c.get('label'),str)] if isinstance(concepts,list) else []
    usage=result.get('usage') or {}
    counts=validation.get('evidence_counts') or {}
    return {'run_id':run['id'],'page':run['page'],'arm':run['ab']['arm'],
            'repetition':run['repetition'],'schedule_index':run['ab']['schedule_index'],
            'status':run['status'],'schema_valid':validation.get('schema_valid'),
            'structural_valid':validation.get('structural_valid'),
            'source_valid':validation.get('source_valid'), 'evidence_counts':counts,
            'concept_count':len(labels),'relation_count':len(data.get('relations',[])) if isinstance(data.get('relations',[]),list) else 0,
            'duplicate_label_warnings':len(labels)-len({label_key(x) for x in labels}),
            'labels':labels,'output_characters':len(result.get('raw_response') or ''),
            'input_tokens':usage.get('input_tokens'), 'output_tokens':usage.get('output_tokens'),
            'reasoning_tokens':usage.get('reasoning_tokens'),'latency_seconds':result.get('latency_seconds'),
            'cost_usd':result.get('cost_usd'),'finish_reason':result.get('finish_reason'),
            'attempts':result.get('attempts'), 'error':result.get('error'),
            'validation_errors':validation.get('structural_errors',[])+validation.get('source_errors',[]),
            'review_status':'pending_independent_review'}


def adjudications(path, experiment, runs):
    if not path:return []
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data['experiment']!=experiment:raise ValueError('Review file belongs to a different experiment')
    known={r['id']:r for r in runs}; accepted=[];seen=set()
    for record in data['reviews']:
        run=known.get(record.get('run_id'))
        if run is None:raise ValueError('Review references unknown run')
        if record.get('status')!='adjudicated':continue
        if record['run_id'] in seen:raise ValueError('Duplicate adjudication for run')
        if not record.get('reviewer') or not record.get('source_checked') or not record.get('independent_of_generator'):
            raise ValueError('Adjudication requires reviewer, source check and independent review declaration')
        if record.get('raw_response_hash')!=digest((run['result'] or {}).get('raw_response','')):
            raise ValueError('Review response hash differs')
        for dim in DIMENSIONS:
            value=record['dimensions'].get(dim)
            if value is not None and (type(value) is not int or value not in (0,1,2)):
                raise ValueError('Review scores must be 0, 1, 2 or null (not applicable/unresolved)')
        seen.add(record['run_id']);accepted.append(record)
    return accepted


def write_report(db, experiment, output=None, *, reviews=None):
    plan,runs=load_ab(db,experiment)
    if output is None:
        directory={'stance-v24':'kg_ab_stance_v24','semantics-v25':'kg_ab_semantics_v25'}.get(plan.get('study'),'kg_ab_navigation_v23')
        output=ROOT/'.work'/directory/'report.md'
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    if reviews and Path(reviews).resolve()==output.with_name(output.stem+'.review_template.json').resolve():
        raise ValueError('Copy template to a separate decisions file; regenerated templates never hold adjudications')
    arm_names=list(plan['arms'])
    repetitions=sorted({s['repetition'] for s in plan['schedule']})
    strata={c['page']:c.get('stratum','audit') for c in plan['cases']}
    judged=adjudications(reviews,experiment,runs)
    measurements=[metrics(r) for r in runs]
    latest={}
    for r in runs:latest[r['ab']['schedule_index']]=r
    current=[metrics(r) for _,r in sorted(latest.items())]
    paired=defaultdict(list)
    for m in current:
        m['stratum']=strata[m['page']]
        paired[m['page']].append(m)
    comparisons={}
    for page in paired:
        items={(r['ab']['arm'],r['repetition']):r for r in latest.values() if r['page']==page}
        pairs=[((a,rep),(b,rep),f'{b}_vs_{a}_rep{rep}')
               for a,b in combinations(arm_names,2) for rep in repetitions]
        pairs += [((arm,a),(arm,b),f'stability_{arm}_{a}_{b}')
                  for arm in arm_names for a,b in combinations(repetitions,2)]
        comparisons[page]=[dict(compare_pair(items[a],items[b]),comparison=kind)
                           for a,b,kind in pairs if a in items and b in items]
    stability=[]
    for page,items in paired.items():
        for arm in arm_names:
            reps=sorted((x for x in items if x['arm']==arm and x['structural_valid']), key=lambda x:x['repetition'])
            if len(reps)==2:
                a,b=[{label_key(x) for x in r['labels']} for r in reps]
                stability.append({'page':page,'arm':arm,'label_jaccard':len(a&b)/len(a|b) if a|b else 1.,
                                  'only_rep1':sorted(a-b),'only_rep2':sorted(b-a),
                                  'note':'Lexical agreement is not semantic accuracy or canonical equivalence'})
    summary={}
    for arm in arm_names:
        values=[x for x in current if x['arm']==arm]
        complete=[x for x in values if x['status'] in ('completed','invalid')]
        def mean(key):
            numbers=[x[key] for x in complete if isinstance(x.get(key),(int,float))]
            return statistics.mean(numbers) if numbers else None
        summary[arm]={'scheduled':len(values),'terminal_outputs':len(complete),
                      'schema_valid':sum(bool(x['schema_valid']) for x in complete),
                      'structural_valid':sum(bool(x['structural_valid']) for x in complete),
                      'source_valid':sum(bool(x['source_valid']) for x in complete),
                      'evidence_counts':dict(sum((Counter(x['evidence_counts']) for x in complete),Counter())),
                      'means':{k:mean(k) for k in ('concept_count','relation_count','output_characters','input_tokens',
                                                 'output_tokens','reasoning_tokens','latency_seconds','cost_usd')},
                      'duplicate_label_warnings':sum(x['duplicate_label_warnings'] for x in complete)}
    judged_by_id={x['run_id']:x for x in judged}
    semantic_comparisons={}
    for left,right in combinations(arm_names,2):
        key=f'{right}_minus_{left}';semantic_comparisons[key]={}
        for dimension in DIMENSIONS:
            pairs=[]
            for page,items in paired.items():
                for repetition in repetitions:
                    arms={x['arm']:judged_by_id.get(x['run_id']) for x in items if x['repetition']==repetition}
                    if not all(arms.get(arm) for arm in (left,right)):continue
                    a,b=(arms[arm]['dimensions'].get(dimension) for arm in (left,right))
                    if a is not None and b is not None:
                        pairs.append({'page':page,'stratum':strata[page],'repetition':repetition,left:a,right:b,key:b-a})
            semantic_comparisons[key][dimension]={'pairs':pairs,'mean_paired_delta':statistics.mean(p[key] for p in pairs) if pairs else None,
                'by_stratum':{s:{'pairs':sum(p['stratum']==s for p in pairs),
                    'mean_paired_delta':statistics.mean(p[key] for p in pairs if p['stratum']==s)
                        if any(p['stratum']==s for p in pairs) else None} for s in sorted(set(strata.values()))}}
    paired_semantics=semantic_comparisons.get('B_minus_A',{})
    data={'experiment':experiment,'created_at':now(),'plan':plan,'automated':summary,
          'page_pairs':dict(paired),'attempts':measurements,'stability':stability,'object_comparisons':comparisons,
          'adjudicated_reviews':judged,'semantic_dimensions':list(DIMENSIONS),'paired_semantics':paired_semantics,
          'paired_semantics_by_comparison':semantic_comparisons,'strata':dict(Counter(strata.values())),
          'outstanding_reviews':len(current)-len({x['run_id'] for x in judged} & {r['id'] for r in latest.values()}),
          'cost_all_attempts_usd':sum(x['cost_usd'] or 0 for x in measurements),
          'unknown_cost_attempts':sum(x['status'] in ('error','interrupted') and x['cost_usd'] is None for x in measurements),
          'conclusion':'Semantic improvement requires independent page-paired review; node counts do not establish improvement.'}
    output.with_suffix('.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    csv_buffer=io.StringIO();fields=[k for k in measurements[0] if k not in ('labels','evidence_counts','validation_errors')]
    writer=csv.DictWriter(csv_buffer,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(measurements)
    output.with_suffix('.csv').write_text(csv_buffer.getvalue(),encoding='utf-8')
    template={'experiment':experiment,'scale':{'0':'incorrect/missing/unhelpful','1':'partial or mixed','2':'correct, sufficient and useful',
                                             'null':'not applicable or unresolved'},
              'instruction':'Review sources before scores; duplication dimension scores nonredundancy. Confidence is not evidence. Compare wrong stance edges avoided AND supported edges lost; an empty graph is not a semantic improvement. Check atomic targets, hypothetical acts, and positive controls. Keep regression and new-page strata separate.',
              'reviews':[{'run_id':r['id'],'page':r['page'],'raw_response_hash':digest((r['result'] or {}).get('raw_response','')),
                          'status':'pending','reviewer':'','independent_of_generator':False,'source_checked':False,
                          'stratum':strata[r['page']], 'dimensions':dict.fromkeys(DIMENSIONS),
                          'stance_review':{'incorrect_edges':[], 'supported_edges':[], 'missing_supported_edges':[],
                                           'mixed_holder_claims':[], 'hypothetical_scope_errors':[]},
                          'object_contract_review':{'overgeneric_labels':[], 'page_specific_labels_to_revise':[],
                              'concept_entity_confusions':[], 'missing_supported_arguments':[],
                              'misleading_verbalized_relations':[], 'unresolved_voices_invented':[]},
                          'missing_subjects':[], 'unsupported_items':[], 'notes':''}
                         for r in latest.values() if r['result']]}
    template_path=output.with_name(output.stem+'.review_template.json')
    template_path.write_text(json.dumps(template,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    packet_path=output.with_name(output.stem+'.review_packets.jsonl')
    with packet_path.open('w',encoding='utf-8') as stream:
        for page in paired:
            selected=[r for r in latest.values() if r['page']==page]
            stream.write(dumps({'page':page,'stratum':strata[page],'source_snapshot':selected[0]['snapshot'],
                                'criteria':template['instruction'],'dimensions':list(DIMENSIONS),
                                'outputs':[{'run_id':r['id'],'arm':r['ab']['arm'],'repetition':r['repetition'],
                                            'result':r['result'],'validation':r['validation'],'stored_reviews':r['reviews']} for r in selected]})+'\n')
    lines=['# Comparação de extração por prompt','',f'Experimento `{experiment}`. {len(paired)} páginas × {len(arm_names)} braços × {len(repetitions)} repetições = {plan["size"]} chamadas planejadas.',
           '',f'Estratos: {dumps(data["strata"])}.']
    if plan.get('primary_comparison'):
        lines += ['',f'Comparação principal: `{plan["primary_comparison"]}`.',
                  plan['intervention'],plan['comparison_limit']]
    lines += ['', '| Braço | Variante congelada |', '|---|---|']
    for arm in arm_names:lines.append(f'| {arm} | `{plan["arms"][arm]["variant"]}` |')
    lines += ['', '## Medições automáticas', '', '| Medida | '+' | '.join(arm_names)+' |','|---|'+'---:|'*len(arm_names)]
    for key in ('terminal_outputs','schema_valid','structural_valid','source_valid','duplicate_label_warnings'):
        lines.append(f'| {key} | '+' | '.join(str(summary[a][key]) for a in arm_names)+' |')
    for key in summary['A']['means']:
        values=[summary[a]['means'][key] for a in arm_names]
        lines.append(f'| média {key} | '+' | '.join(str(v) if v is not None else 'pendente' for v in values)+' |')
    lines += ['',f"Custo registrado (todas as tentativas): US$ {data['cost_all_attempts_usd']:.6f}; tentativas de custo desconhecido: {data['unknown_cost_attempts']}.",
              'Tokens de raciocínio já estão incluídos na saída total. Retry HTTP pode gerar custo desconhecido.',
              '', 'Métodos de localização por braço:']
    for arm in arm_names:lines.append(f"- {arm}: `{dumps(summary[arm]['evidence_counts'])}`")
    columns=[(a,r) for a in arm_names for r in repetitions]
    lines += ['', '## Pares por página', '', '| Página | '+' | '.join(f'{a}{r}' for a,r in columns)+' |','|---|'+'---|'*len(columns)]
    for page,items in paired.items():
        cells=[]
        for arm,rep in columns:
            x=next(i for i in items if i['arm']==arm and i['repetition']==rep)
            cells.append(f"{x['run_id']} {x['status']}")
        lines.append('| '+page+' | '+' | '.join(cells)+' |')
    lines += ['', '## Revisão semântica independente', '',f'{len(judged)} adjudicações; {data["outstanding_reviews"]} decisões pendentes.',
              'Os escores 0–2 e notas são explícitos no JSON; não são inferidos da validade estrutural nem da confiança do modelo.']
    for comparison,dimensions in semantic_comparisons.items():
        for dim,measured in dimensions.items():
            lines.append(f'- {comparison}, {dim}: pares adjudicados={len(measured["pairs"])}, diferença média={measured["mean_paired_delta"] if measured["pairs"] else "pendente"}')
    lines += ['', '## Repetibilidade e limites', '',f'{len(stability)} pares de repetições com JSON estruturalmente válido; diferenças de rótulos e Jaccard no JSON.',
              'Comparações lexicais não resolvem equivalência conceitual. Mais nós/relações não demonstram qualidade.',
              'object_comparisons no JSON compara conceitos, atores, claims e relações por descrição e contexto, com diferenças de evidência e correspondências incertas; nunca só por ID local.',
              f'A amostra diagnóstica inclui apenas {len(paired)} páginas; não estima ganho no corpus inteiro. Páginas novas não equivalem a obras independentes.',
              f'Copie `{template_path.name}` para um arquivo de decisões e forneça-o com `ab-report --reviews`. O template é regenerado; decisões ficam no arquivo separado.',
              f'Fontes e saídas completas: `{packet_path.name}`. A localização textual não aprova a interpretação histórica.']
    output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'experiment':experiment,'report':str(output),'automated':summary,'adjudicated':len(judged),
            'pending':data['outstanding_reviews'],'inference_performed':False}
