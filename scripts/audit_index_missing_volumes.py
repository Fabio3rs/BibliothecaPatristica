#!/usr/bin/env python3
"""Explain volumes absent from the experimental catalog; all inputs readonly.

Historical extraction coverage and fresh bounded OCR discovery are independent:
neither a zero-row parser result nor a negative bounded scan proves absence of
an index in the printed volume. Outputs are small JSON/CSV/Markdown artifacts.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import csv
import gzip
import hashlib
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.corpus_utils import page_files
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.editorial_regions_experiment import local_facts, local_fingerprint, fold
from tools.indexing.regional_section_provenance import classify_title, title_at
from tools.indexing.unrepresented_region_recovery import INDEX_KINDS


def historical_class(kinds):
    if set(kinds) & INDEX_KINDS:
        return 'index_signals_without_catalog_rows'
    if kinds:
        return 'other_editorial_material_outside_index_filter'
    return 'no_historical_region_signals'


def heading_family(title, detected):
    """Separate a contents/work list from an index of names, words or subjects.

    ELENCHUS AUCTORUM ET OPERUM is the volume's contents list, even though
    it mentions authors. INDEX CAPITUM remains capitula when it names epistles.
    Retain the broader classifier separately, so this triage remains auditable.
    """
    text=fold(title)
    if detected in {'capitula','contents_catalog','work_summary','editorial_closure'}:
        return detected
    if re.match(r'^ELENCHUS\b',text):
        return 'work_catalog'
    if re.match(r'^INDEX\b',text) and 'OPERUM' in text and re.search(r'\bTOMO\b.*\bCONTINENTUR\b',text):
        return 'work_catalog'
    return classify_title(title,detected)[0]


def scan(job):
    volume, root, anchors, front, tail, neighbors, *options = job
    all_pages = bool(options and options[0])
    inventory = page_files(Path(root)/volume/'text', suffixes=['.txt'])
    ordered = sorted(inventory)
    selected = set(ordered[:front] if front else []) | set(ordered[-tail:] if tail else [])
    for p in anchors:
        selected.update(range(max(1,p-neighbors),p+neighbors+1))
    if all_pages:
        selected = set(ordered)
    pages=[]
    for number in sorted(selected):
        paths=inventory.get(number,[])
        if len(paths)!=1:
            pages.append({'physical':number,'status':'missing_or_ambiguous_file','paths':[str(p) for p in paths]})
            continue
        path=paths[0].resolve()
        try:
            raw=path.read_bytes().decode('utf-8')
        except (OSError,UnicodeError) as exc:
            pages.append({'physical':number,'source':str(path),'status':'read_error','error':str(exc)})
            continue
        facts=local_facts(raw)
        headings=[]
        for event in facts['events']:
            if event['role'] not in {'section_start','running_header'} or not event['kind']:
                continue
            title=title_at(raw,facts['rows'],event)
            kind,material=classify_title(title['title_raw'],event['kind'])
            family=heading_family(title['title_raw'],event['kind'])
            headings.append({**title,'kind':family,'title_classifier_kind':kind,
                             'detector_kind':event['kind'],'material':material,'role':event['role'],
                             'index_filter_eligible':family in INDEX_KINDS or family.endswith('_index')})
        pages.append({'physical':number,'source':str(path),'status':'read',
                      'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                      'headings':headings,'recognized_rows':len(facts['rows'])})
    return {'volume':volume,'physical_files':sum(map(len,inventory.values())),
            'selected_files':len(selected),'pages':pages}


def audit(args):
    args.out.mkdir(parents=True,exist_ok=False)
    survey={r['volume']:r for r in csv.DictReader(args.survey.open())}
    with closing(readonly(args.catalog)) as db:
        represented={r[0] for r in db.execute('SELECT DISTINCT volume FROM items')}
    absent=sorted(set(survey)-represented)
    kinds=defaultdict(Counter);anchors=defaultdict(set);region_counts=Counter()
    with args.regions.open() as f:
        for line in f:
            r=json.loads(line);v=r['volume']
            if v not in absent:continue
            kinds[v][r['kind']]+=1;region_counts[r['kind']]+=1
            if r['spans']:
                # Endpoints help detect transitions, without accepting old offsets.
                anchors[v].update([r['spans'][0]['physical'],r['spans'][-1]['physical']])
    attempts={}
    if args.campaign:
        attempts={r['volume']:r for r in json.loads((args.campaign/'summary.json').read_text())['by_volume']}
    all_pages = getattr(args, 'all_pages', False)
    jobs=[(v,str(args.root.resolve()),sorted(anchors[v]),args.front_pages,args.tail_pages,args.neighbors,all_pages) for v in absent]
    rows=[];page_counts=Counter();fresh_types=Counter();all_examples=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool, (args.out/'pages.jsonl').open('w') as f:
        for result in pool.map(scan,jobs):
            v=result['volume'];record=survey[v];examples=[]
            fresh=Counter()
            cached=[]
            if args.region_cache and any(h['index_filter_eligible'] for p in result['pages'] for h in p.get('headings',[])):
                for path in sorted(args.region_cache.glob(v+'-*.json.gz')):
                    cached.append((str(path.resolve()),json.loads(gzip.decompress(path.read_bytes()))['pages']))
            for p in result['pages']:
                prior=[]
                for cache_path,cache_pages in cached:
                    old=cache_pages.get(p.get('source'))
                    if old and old['facts']['events']:
                        prior.append({'cache':cache_path,'same_source_hash':old['sha256']==p['source_sha256'],
                                      'events':[{k:e[k] for k in ('quote','span','kind','role','reason')} for e in old['facts']['events']]})
                if prior:p['historical_signal_evidence']=prior
                f.write(json.dumps({'volume':v,**p},ensure_ascii=False,sort_keys=True)+'\n')
                page_counts[p['status']]+=1
                for h in p.get('headings',[]):
                    fresh[h['kind']]+=1;fresh_types[h['kind']]+=1
                    if h['index_filter_eligible']:
                        examples.append({'source':p['source'],'physical':p['physical'],**h,
                                         'historical_signal_evidence':prior})
            historical=historical_class(kinds[v])
            if historical=='index_signals_without_catalog_rows':
                route='known_index_format_without_extracted_rows'
            elif examples:
                route='fresh_index_heading_candidate_to_review'
            elif kinds[v]:
                route='other_material_not_selected_by_index_campaign'
            else:
                route='no_index_heading_in_full_ocr_scan' if all_pages else 'no_index_heading_in_bounded_scan'
            row={'volume':v,'collection':v[:2],'historical_class':historical,
                 'next_route':route,'historical_selected_pages':int(record['selected_source_pages']),
                 'historical_units':int(record['units']),'historical_reference_groups':int(record['groups']),
                 'historical_region_types':dict(kinds[v]),'prior_regional_attempt':attempts.get(v),
                 'current_physical_files':result['physical_files'],'current_pages_selected':result['selected_files'],
                 'fresh_heading_types':dict(fresh),'fresh_index_heading_count':len(examples),
                 'examples':examples[:8]}
            rows.append(row);all_examples.extend({'volume':v,**e} for e in examples)
            print(json.dumps({'progress': True, 'completed_volumes': len(rows), 'total_volumes': len(absent),
                              'volume': v, 'pages_read': page_counts['read'],
                              'index_heading_candidates': len(examples)}), flush=True)
    json_text=lambda obj:json.dumps(obj,ensure_ascii=False,indent=2)+'\n'
    (args.out/'volumes.json').write_text(json_text(rows))
    (args.out/'index_heading_candidates.json').write_text(json_text(all_examples))
    fields=[k for k in rows[0] if k!='examples'] if rows else ['volume']
    with (args.out/'volumes.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for row in rows:
            w.writerow({k:json.dumps(row[k],ensure_ascii=False,sort_keys=True) if isinstance(row[k],(dict,list)) else row[k] for k in fields})
    filesystem={p.name for p in args.root.iterdir() if p.is_dir() and (p/'text').is_dir()}
    summary={'version':'missing-index-volumes-v2','scan_scope':'full_absent_volumes' if all_pages else 'bounded',
             'corpus_in_survey':len(survey),'represented_in_survey':len(set(survey)&represented),
             'absent':len(absent),'absent_by_collection':dict(Counter(r['collection'] for r in rows)),
             'historical_classes':dict(Counter(r['historical_class'] for r in rows)),
             'current_routes':dict(Counter(r['next_route'] for r in rows)),
             'historical_selected_volumes':sum(r['historical_selected_pages']>0 for r in rows),
             'historical_unselected_volumes':sum(r['historical_selected_pages']==0 for r in rows),
             'historical_units':sum(r['historical_units'] for r in rows),
             'historical_reference_groups':sum(r['historical_reference_groups'] for r in rows),
             'historical_region_counts':dict(region_counts),'fresh_heading_counts':dict(fresh_types),
             'page_statuses':dict(page_counts),'filesystem_only_volumes':sorted(filesystem-set(survey)),
             'survey_only_volumes':sorted(set(survey)-filesystem),'catalog_only_volumes':sorted(represented-set(survey)),
             'inputs':{k:str(getattr(args,k).resolve()) if getattr(args,k) else None for k in ('catalog','survey','regions','root','campaign','region_cache')},
             'parameters':{**{k:getattr(args,k) for k in ('workers','front_pages','tail_pages','neighbors')}, 'all_pages': all_pages},
             'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'recognition_fingerprint':local_fingerprint(),
             'warning':('All available OCR pages in absent volumes examined; recognition can miss titles and OCR can omit print.' if all_pages else
                        'Bounded current OCR scan, not proof of absence or complete printed-index coverage.'),
             'catalog_writes':0,'source_writes':0,'inference_calls':0}
    (args.out/'summary.json').write_text(json_text(summary))
    lines=['# Volumes fora do catálogo experimental','',
           f"{len(absent)} ausentes de {len(survey)} volumes/partes; {len(set(survey)&represented)} representados.",
           'Ausência no catálogo não comprova ausência de índice impresso. A seleção histórica e a sondagem atual têm escopos diferentes.',
           '', '| Motivo no inventário histórico | Volumes |','| --- | ---: |']
    lines += [f'| {k} | {n} |' for k,n in summary['historical_classes'].items()]
    lines += ['',f"No levantamento antigo: {summary['historical_selected_volumes']} com páginas selecionadas e {summary['historical_unselected_volumes']} sem seleção; {summary['historical_units']} unidades e {summary['historical_reference_groups']} grupos de referências.",
              '',(f"Leitura integral dos OCRs dos volumes ausentes: {page_counts['read']} arquivos lidos. Não equivale a conferir o impresso integralmente." if all_pages else
                  f"Sondagem atual: {page_counts['read']} arquivos lidos. Seleção: primeiros {args.front_pages}, últimos {args.tail_pages}, extremos das regiões e ±{args.neighbors} vizinhos. Sem nova leitura integral dos volumes."),
              '', '| Volume | Classe histórica | Próxima rota | Páginas sondadas | Títulos de índice candidatos |','| --- | --- | --- | ---: | ---: |']
    lines += [f"| {r['volume']} | {r['historical_class']} | {r['next_route']} | {r['current_pages_selected']} | {r['fresh_index_heading_count']} |" for r in rows]
    lines += ['','`volumes.csv`/`volumes.json`: lista completa e causas por camada. `pages.jsonl`: execução, títulos literais, spans e hashes atuais. `index_heading_candidates.json`: pistas para revisar antes de escolher o parser.',
              'A campanha regional anterior aceita somente INDEX_KINDS; work_catalog, capitula, work_summary e contents_catalog ficam fora desse filtro. Um tipo histórico pode estar incorreto; sinais atuais permanecem candidatos.',
              'Nenhum registro foi importado, nenhuma fonte alterada e nenhuma inferência executada.']
    (args.out/'report.md').write_text('\n'.join(lines)+'\n')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','survey','regions','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path('teste'))
    p.add_argument('--campaign',type=Path)
    p.add_argument('--region-cache',type=Path,help='Optional prior local-signal cache; hashes compare evidence, never block discovery')
    p.add_argument('--workers',type=int,default=20)
    p.add_argument('--front-pages',type=int,default=8)
    p.add_argument('--tail-pages',type=int,default=24)
    p.add_argument('--neighbors',type=int,default=1)
    p.add_argument('--all-pages',action='store_true',help='Read every available OCR page of volumes absent from the catalog, not just front/tail/region windows')
    a=p.parse_args()
    if a.workers<1 or min(a.front_pages,a.tail_pages,a.neighbors)<0:p.error('Invalid workers/window')
    print(json.dumps(audit(a),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
