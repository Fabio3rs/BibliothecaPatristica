#!/usr/bin/env python3
"""Compare a regional recovery campaign with the current experimental catalog."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.unrepresented_region_recovery import INDEX_KINDS
from tools.indexing.search_catalog_consolidation import stats


def report(catalog, campaign, survey, regions):
    extraction=json.loads((campaign/'summary.json').read_text())
    origin=str((campaign/'search.sqlite').resolve())
    volumes=defaultdict(Counter); namespaces=Counter(); reasons=Counter(); kinds=Counter()
    with closing(readonly(catalog)) as db:
        current=stats(db)
        represented={r[0] for r in db.execute('SELECT DISTINCT volume FROM items')}
        for row in db.execute('''SELECT i.*,l.evidence_json FROM items i JOIN item_origins o ON o.item_id=i.id
             LEFT JOIN localization_results l ON l.item_id=i.id WHERE o.origin=?''',(origin,)):
            meta=json.loads(row['metadata_json'])
            volumes[row['volume']]['records']+=1
            volumes[row['volume']][row['state']]+=1
            namespaces[meta.get('reference_namespace','unknown')]+=1
            kinds[meta.get('entry_kind',meta.get('region_kind','unknown'))]+=1
            if row['evidence_json']:
                result=json.loads(row['evidence_json'])
                if result.get('abstention'):
                    reasons[result['abstention']]+=1
        imports=[json.loads(r[0]) for r in db.execute("SELECT json FROM metadata WHERE key LIKE 'supplement_import:%'")]
        baseline=next(r['before'] for r in imports if r['supplement']==origin and r['added'])
    summary={'campaign':str(campaign),'catalog':str(catalog),'before':baseline,'current':current,
             'campaign_records':sum(v['records'] for v in volumes.values()),
             'campaign_with_candidates':sum(v['with_destinations'] for v in volumes.values()),
             'campaign_without_candidates':sum(v['source_only'] for v in volumes.values()),
             'namespaces':dict(namespaces),'entry_kinds':dict(kinds),'pending_reasons':dict(reasons),
             'volumes_with_records':len(volumes),'volumes_examined':extraction['volumes_examined'],
             'source_files_examined':extraction['source_files_examined'],
             'zero_record_volumes':[r['volume'] for r in extraction['by_volume'] if not r.get('entries')],
             'canonical_updates':0,'inference_calls':0,
             'quality':'Literal extraction candidates; physical header matches are approximate, not certified.'}
    (campaign/'localization_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    with (campaign/'by_volume.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['volume','records','with_destinations','source_only'])
        w.writeheader()
        for v in sorted(volumes):w.writerow({'volume':v,**{k:volumes[v][k] for k in w.fieldnames[1:]}})
    signals=defaultdict(Counter)
    for line in regions.read_text().splitlines():
        s=json.loads(line);signals[s['volume']][s['kind']]+=1
    remaining=[]
    all_volumes=list(csv.DictReader(survey.open()))
    for v in all_volumes:
        volume=v['volume']
        if volume in represented:continue
        index={k:n for k,n in signals[volume].items() if k in INDEX_KINDS}
        other={k:n for k,n in signals[volume].items() if k not in INDEX_KINDS}
        remaining.append({'volume':volume,'collection':volume[:2],
                          'extraction_status':'selected_without_reference_groups' if int(v['selected_source_pages']) else 'no_selected_extraction',
                          'selected_source_pages':int(v['selected_source_pages']),'experimental_units':int(v['units']),
                          'index_region_signals':sum(index.values()),'index_types':json.dumps(index),
                          'other_region_types':json.dumps(other)})
    with catalog.with_name('remaining_volumes.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['volume','collection','extraction_status','selected_source_pages',
                                     'experimental_units','index_region_signals','index_types','other_region_types'])
        w.writeheader();w.writerows(remaining)
    missing={'corpus_volumes':len(all_volumes),'catalog_volumes':len(represented),'remaining_volumes':len(remaining),
             'with_index_region_signals':sum(bool(r['index_region_signals']) for r in remaining),
             'with_only_other_signals':sum(not r['index_region_signals'] and r['other_region_types']!='{}' for r in remaining),
             'without_region_signals':sum(not r['index_region_signals'] and r['other_region_types']=='{}' for r in remaining),
             'inputs':{'catalog':str(catalog),'survey':str(survey),'regions':str(regions)},
             'caution':'No records does not establish absence of printed indices.'}
    catalog.with_name('remaining_volumes_summary.json').write_text(json.dumps(missing,indent=2)+'\n')
    lines=['# Recuperação de regiões sem registros no catálogo','',
           f"Examinados {extraction['volumes_examined']} volumes, {extraction['regions_examined']} regiões e {extraction['source_files_examined']} arquivos, com 20 workers.",
           f"Novos registros: {summary['campaign_records']}; com candidatos físicos: {summary['campaign_with_candidates']}; sem candidatos: {summary['campaign_without_candidates']}.",
           'Referências literais e spans preservados. Os destinos desta campanha são coincidências aproximadas de cabeçalhos, sem confirmação editorial.',
           '', '| Volume | Registros | Com candidato | Sem candidato |','| --- | ---: | ---: | ---: |']
    for v in sorted(volumes,key=lambda v:-volumes[v]['records']):
        n=volumes[v];lines.append(f"| {v} | {n['records']} | {n['with_destinations']} | {n['source_only']} |")
    lines+=['','Sem registros: '+', '.join(summary['zero_record_volumes'])+'.',
            'Diagnósticos e evidências: `zero_record_findings.json`; são formatos ainda não suportados, não ausência comprovada de material.',
            '', 'PO003/129–130: títulos locais interrompem a notação página/linha do índice anterior. A revisão mantém histórico no catálogo e preserva os candidatos existentes.',
            'Reprodução: `docs/unrepresented_index_regions.md`. Nenhuma escrita canônica ou inferência.']
    (campaign/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'campaign':summary,'remaining':missing},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','campaign','survey','regions'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();report(a.catalog,a.campaign,a.survey,a.regions)
