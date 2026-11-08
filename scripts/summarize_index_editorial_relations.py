#!/usr/bin/env python3
"""Report ibid recovery, remaining textual barriers and overlay overlap (read-only)."""
from collections import Counter, defaultdict
from contextlib import closing
import argparse
import csv
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import readonly


def candidate_ids(directory):
    with closing(readonly(directory/'relations.sqlite')) as db:
        return {r[0] for r in db.execute('SELECT DISTINCT item_id FROM candidates WHERE navigable=1')}


def classify_gap(text):
    if re.match(r'^[ \t]*\.[ \t]*,[ \t]*[A-D](?:[.,;]|\s)',text):
        return 'possible_previous_column_subdivision'
    if re.search(r'<|\[/',text):
        return 'intervening_markup'
    if re.search(r'\bibid(?:em)?\b',text,re.I):
        return 'intervening_unrepresented_ibid'
    if re.search(r'\d',text):
        return 'intervening_number_locator_or_prose'
    if re.search(r'\b[IVXLCDM]+\b',text):
        return 'intervening_roman_or_single_letter'
    return 'other_intervening_material'


def report(directory, baseline=None, prior=None):
    summary=json.loads((directory/'summary.json').read_text())
    recovered=candidate_ids(directory)
    baseline_ids=candidate_ids(baseline) if baseline else set()
    prior_ids=candidate_ids(prior) if prior else set()
    gaps, examples, texts = Counter(), defaultdict(list), {}
    affected=Counter()
    with closing(readonly(directory/'relations.sqlite')) as db, (directory/'pending_relations.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['group_id','volume','status','reason','gap_class','source','source_span','antecedent_raw','excerpt'])
        for group_id,volume,status,payload in db.execute('SELECT group_id,volume,status,evidence_json FROM relations ORDER BY volume,group_id'):
            row=json.loads(payload)
            if status=='candidate_recovered':
                affected[volume]+=1
            for relation in row['relations'] or [{}]:
                reason=relation.get('reason',status)
                if reason in {'antecedent_candidates','candidate_recovered'}:
                    continue
                classification,excerpt='', ''
                if reason=='intervening_locator_or_markup':
                    source=row['source']
                    if source not in texts:
                        texts[source]=Path(source).read_text()
                    a,b=relation['antecedent_span'][1],row['source_span'][0]
                    gap=texts[source][a:b]
                    classification=classify_gap(gap)
                    gaps[classification]+=1
                    excerpt=gap[-600:]
                writer.writerow([group_id,volume,status,reason,classification,row['source'],row['source_span'],relation.get('antecedent_raw'),excerpt])
                key=classification or reason
                if len(examples[key])<3:
                    examples[key].append({'group_id':group_id,'volume':volume,'source':row['source'],'span':row['source_span'],
                                          'reference':row['raw'],'antecedent':relation.get('antecedent_raw'),'excerpt':excerpt})
    with closing(readonly(summary['catalog'])) as base:
        unprocessed=[]
        for ident in summary['unprocessed_ibid_item_ids']:
            row=dict(base.execute('SELECT id,volume,ref_raw,metadata_json FROM items WHERE id=?',(ident,)).fetchone())
            meta=json.loads(row.pop('metadata_json'))
            row['reason']=('contextualized_reference_differs_from_survey' if meta.get('reference_display_is_contextualized') else
                           'no_current_source_span' if not meta.get('source_span') else 'group_not_processed')
            unprocessed.append(row)
    estimator_ids=set()
    if summary.get('estimator_candidates'):
        with closing(readonly(summary['estimator_candidates'])) as db:
            estimator_ids={r[0] for r in db.execute('SELECT DISTINCT item_id FROM candidates')}
    result={'version':'ibid-recovery-comparison-v1','requested_items':summary['requested_ibid_items'],
            'processed_items':summary['processed_ibid_items'],'recovered_items':len(recovered),
            'recovered_without_estimator':len(baseline_ids) if baseline else None,
            'gained_vs_baseline':len(recovered-baseline_ids) if baseline else None,
            'lost_vs_baseline':len(baseline_ids-recovered) if baseline else None,
            'gained_vs_prior_parser':len(recovered-prior_ids) if prior else None,
            'lost_vs_prior_parser':len(prior_ids-recovered) if prior else None,
            'overlap_with_estimator_recovered_items':len(recovered&estimator_ids),
            'new_beyond_estimator':len(recovered-estimator_ids),
            'gap_classes':dict(gaps.most_common()),'examples':dict(examples),'unprocessed':unprocessed,
            'top_recovery_volumes':affected.most_common(15),'quality':'unreviewed_candidates_no_lexical_support',
            'inputs':{'run':str(directory),'baseline':str(baseline) if baseline else None,'prior_parser':str(prior) if prior else None}}
    (directory/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# Análise de herança editorial','',
           f"Examinados {result['processed_items']} de {result['requested_items']} registros pendentes com ibid. Recuperados {len(recovered)} registros com candidatos.",'',
           f"Sem estimador: {result['recovered_without_estimator']}; ganhos líquidos por ID contra baseline: +{result['gained_vs_baseline']}, perdas {result['lost_vs_baseline']}.",
           f"Ganhos da extensão da gramática: +{result['gained_vs_prior_parser']}; perdas {result['lost_vs_prior_parser']}.",
           f"Novos além da recuperação de páginas: {result['new_beyond_estimator']}; sobreposição: {result['overlap_with_estimator_recovered_items']}.",'',
           '| Material intermediário que impede herança direta | Ocorrências |','|---|---:|']
    lines += [f'| {k} | {v} |' for k,v in gaps.most_common()]
    lines += ['', 'Esses padrões são sinais mecânicos, não diagnósticos certificados de erro de OCR ou segmentação.',
              'O recuperador mantém o antecedente imediato e não salta números intermediários. Subdivisões A–D são preservadas; seq./seqq. oferecem apenas a âncora inicial.',
              'Os escores e correspondências lexicais do antecedente não são herdados. Nenhum banco de origem foi modificado.',
              'Veja analysis.json para exemplos com spans e IDs, e pending_relations.csv para a fila completa.']
    (directory/'analysis.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in {'examples','unprocessed'}},ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--baseline',type=Path);p.add_argument('--prior-parser',type=Path)
    args=p.parse_args();report(args.run,args.baseline,args.prior_parser)
