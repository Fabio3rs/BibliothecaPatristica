"""Read-only, streaming comparison of frozen model outputs. No Store/provider use."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .common import digest, dumps, now, readonly
from .evidence_locator import POLICY
from .evidence_matching import MATCHING_POLICY
from .validation import evaluate_response


def historical_fingerprint(db, campaign):
    """Same content fingerprint as the audit, independent of unrelated new runs."""
    h=hashlib.sha256()
    with readonly(db) as con:
        for row in con.execute('''SELECT r.id,r.input_hash,r.result_json,c.snapshot_hash,
            (SELECT validation_json FROM run_validation v WHERE v.run_id=r.id ORDER BY v.id DESC LIMIT 1) validation_json
            FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.experiment_id=? ORDER BY r.id''',(campaign,)):
            h.update((str(row['id'])+'\0'+row['input_hash']+'\0'+digest(row['result_json'] or '')+'\0'+row['snapshot_hash']+'\0'+digest(row['validation_json'] or '')+'\n').encode())
    return h.hexdigest()


def replay(db, campaign, output, *, expected_runs=None, progress=None):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    before=historical_fingerprint(db,campaign)
    counts={p:Counter() for p in (MATCHING_POLICY,POLICY)}
    changes=Counter();run_counts=Counter();examples=[];n=0
    regression_reasons=Counter();new_acceptances=[]
    details_path=output.with_suffix('.jsonl')
    temporary=details_path.with_suffix('.jsonl.incomplete')
    with readonly(db) as con, temporary.open('w',encoding='utf-8') as stream:
        con.execute('BEGIN')
        if not con.execute('SELECT 1 FROM experiments WHERE id=?',(campaign,)).fetchone():
            raise ValueError('Campaign not found')
        query='''SELECT r.id,r.status,r.result_json,c.page,c.snapshot_hash,c.snapshot_json,
            json_extract(r.prepared_json,'$.schema') schema_json,
            json_extract(r.prepared_json,'$.mode') mode,
            json_extract(r.prepared_json,'$.config.max_output_tokens') max_output_tokens,
            (SELECT validation_json FROM run_validation v WHERE v.run_id=r.id ORDER BY v.id DESC LIMIT 1) validation_json
            FROM runs r JOIN cases c ON c.id=r.case_id
            WHERE r.experiment_id=? AND r.result_json IS NOT NULL ORDER BY r.id'''
        for row in con.execute(query,(campaign,)):
            source=json.loads(row['snapshot_json']);result=json.loads(row['result_json'])
            prepared={'schema':json.loads(row['schema_json']),'mode':row['mode'],
                      'config':{'max_output_tokens':row['max_output_tokens']}}
            evaluated={p:evaluate_response(source,result,prepared,matching_policy=p) for p in counts}
            old,new=evaluated[MATCHING_POLICY],evaluated[POLICY]
            pairs=[]
            for a,b in zip(old['evidence_matches'],new['evidence_matches']):
                flags=[]
                if b['accepted'] and not a['accepted']:flags.append('newly_accepted')
                if b.get('located') and not a['accepted']:flags.append('newly_located_or_review_candidate')
                if a['accepted'] and not b['accepted']:flags.append('acceptance_regression_requires_review')
                if b['accepted'] and not a['accepted'] and b['method'] in {'fuzzy','note_marker'}:
                    flags.append('new_nonexact_acceptance_audit')
                changes.update(flags)
                if 'acceptance_regression_requires_review' in flags:
                    regression_reasons[b.get('reason','unspecified')]+=1
                if 'newly_accepted' in flags and len(new_acceptances)<200:
                    new_acceptances.append({'run_id':row['id'],'page':row['page'],'evidence_id':a['evidence_id'],
                                            'method':b['method'],'source':b['source'],'locator':b['locator'],
                                            'start':b['start'],'end':b['end'],'quote':b['matched_quote'],
                                            'requires_semantic_review':True})
                quote=next((e['quote'] for e in (old['parsed'] or {}).get('evidence',[]) if e['id']==a['evidence_id']),None)
                pairs.append({'evidence_id':a['evidence_id'],'model_quote':quote,'old':a,'new':b,'flags':flags})
                if flags and len(examples)<40:
                    examples.append({'run_id':row['id'],'page':row['page'],'evidence_id':a['evidence_id'],
                                     'old':a['method'],'new':b['method'],'flags':flags})
            for policy,val in evaluated.items():
                counts[policy].update(val['evidence_counts'])
                run_counts[policy+'/valid']+=bool(val['valid'])
                run_counts[policy+'/structural_valid']+=bool(val['structural_valid'])
            stored=json.loads(row['validation_json'] or '{}')
            if stored.get('valid')!=old['valid'] or stored.get('evidence_counts')!=old['evidence_counts']:
                run_counts['historical_replay_disagreements']+=1
            def summary(val):
                return {k:v for k,v in val.items() if k not in {'parsed','validated_extraction','evidence_matches'}}
            stream.write(dumps({'run_id':row['id'],'page':row['page'],'historical_status':row['status'],
                                'snapshot_hash':row['snapshot_hash'],'raw_response_hash':digest(result.get('raw_response','')),
                                'old':summary(old),'new':summary(new),'evidence':pairs})+'\n')
            n+=1
            if progress and n%25==0:progress({'replayed':n})
    after=historical_fingerprint(db,campaign)
    if before!=after:raise ValueError('Historical data changed during replay; keep output as non-frozen diagnostic only')
    if expected_runs is not None and n!=expected_runs:raise ValueError(f'Expected {expected_runs} results, found {n}')
    temporary.replace(details_path)
    detail_digest=hashlib.sha256()
    with details_path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):detail_digest.update(chunk)
    summary={'campaign':campaign,'created_at':now(),'runs':n,'policies':{k:dict(v) for k,v in counts.items()},
             'run_counts':dict(run_counts),'changes':dict(changes),'examples':examples,
             'regression_reasons':dict(regression_reasons),'new_acceptances':new_acceptances,
             'details_sha256':detail_digest.hexdigest(),
             'implementation_hashes':{name:digest((Path(__file__).parent/name).read_bytes()) for name in
                                      ('evidence_matching.py','evidence_locator.py','validation.py')},
             'history_unchanged':True,'historical_fingerprint':before,'details':str(details_path),
             'semantic_status':'not_evaluated','gold_status':'not_gold'}
    output.with_suffix('.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines=['# Comparação offline dos localizadores', '',f'Campanha `{campaign}`: **{n} respostas**, sem inferência ou escrita na base.',
           '', 'Localizar não confirma a interpretação; review-only/ambiguous não são posições únicas aceitas.',
           '', '| Método | local-quote-v2 | local-quote-v3 |','|---|---:|---:|']
    for method in ('exact','normalized','fuzzy','note_marker','ambiguous','review_only','not_found','unknown_locator'):
        lines.append(f'| {method} | {counts[MATCHING_POLICY][method]} | {counts[POLICY][method]} |')
    lines += ['',f"Páginas válidas: {run_counts[MATCHING_POLICY+'/valid']} → {run_counts[POLICY+'/valid']}.",
              f"Novas aceitações: {changes['newly_accepted']}; regressões de aceitação a revisar: {changes['acceptance_regression_requires_review']}.",
              f"Novas aceitações não exatas para auditoria: {changes['new_nonexact_acceptance_audit']}.",
              f"Divergências entre replay antigo e validação histórica: {run_counts['historical_replay_disagreements']}.",
              '', 'Regressões podem corrigir falsos positivos antigos (p.ex. ocorrência repetida ou palavra truncada).',
              'Chamadas de nota só são ignoradas quando há definição em bloco editorial da mesma página e contexto não numérico.',
              'Nem toda chamada de nota visual é reconhecível com segurança: casos incertos permanecem para revisão.',
              '', '## Novas localizações aceitas (não são gold)', '',
              '| Run | Página | Evidência | Método | Fonte e offsets Unicode |','|---|---|---|---|---|']
    for x in new_acceptances:
        lines.append(f"| {x['run_id']} | {x['page']} | {x['evidence_id']} | {x['method']} | {x['source']}:{x['locator']} [{x['start']}, {x['end']}) |")
    lines += ['', '## Aceitações antigas encaminhadas à revisão', '', '| Motivo | Evidências |','|---|---:|']
    for reason,count in regression_reasons.most_common():lines.append(f'| {reason} | {count} |')
    lines += ['', '## Primeiras mudanças, em ordem de run', '',
              '| Run | Página | Evidência | Antes | Agora | Sinais |','|---|---|---|---|---|---|']
    for x in examples:
        lines.append(f"| {x['run_id']} | {x['page']} | {x['evidence_id']} | {x['old']} | {x['new']} | {', '.join(x['flags'])} |")
    lines += ['',f'Diagnósticos completos: `{details_path.name}` (uma linha JSON por run, com cada evidência).',
              f'Fingerprint histórico preservado: `{before}`.',
              'Nenhuma claim foi aprovada semanticamente e nenhuma validação histórica foi sobrescrita.']
    output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary
