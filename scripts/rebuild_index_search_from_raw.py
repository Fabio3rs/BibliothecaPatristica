#!/usr/bin/env python3
"""Rebuild the generic index search layer from raw OCR and a frozen source recipe.

Historical structured overrides are explicitly outside this recipe. No old index
database or lexical audit is opened. An optional baseline is comparison-only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.indexing.candidate_search import build
from tools.indexing.editorial_relation_recovery import recover
from tools.indexing.search_catalog_consolidation import consolidate
from scripts.compare_index_target_surveys import compare
from scripts.rebuild_editorial_index_pages import compare_rebuilds


def compare_sources(left,right):
    def signatures(folder):
        paths=sorted((folder/'volumes').glob('*/inventory.jsonl'))
        paths+=sorted((folder/'volumes').glob('*/raw_pages.jsonl'))
        return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    a,b=signatures(left),signatures(right)
    result={'status':'identical' if a and a==b else 'different',
            'artifacts':len(a),'left':a,'right':b}
    (left/'source_reproducibility.json').write_text(json.dumps(result,indent=2)+'\n')
    if result['status']!='identical':raise ValueError('Raw extraction or source inventories differ')
    return result


def finish(out, baseline=None):
    survey=json.loads((out/'summary.json').read_text())
    if survey['status']!='completed':
        raise ValueError('Incomplete raw survey; inspect sources and errors before comparing reproduction')
    base=out/'base/search.sqlite'
    base_summary=build(None,out/'survey.sqlite',out/'base')
    relations=recover(base,out,out/'relations',catalog_context=True,only_ibid=True)
    consolidate(base,[out/'relations/relations.sqlite'],[],out/'catalog')
    stats=json.loads((out/'catalog/baseline.json').read_text())['baseline']
    config=json.loads((out/'config.json').read_text())
    result={'version':'raw-generic-search-rebuild-v1','status':'completed',
        'search_catalog':str((out/'catalog/search.sqlite').resolve()),
        'selected_pages':survey['counts'].get('selected_source_pages',0),
        'parsed_units':survey['counts'].get('units',0),'reference_groups':sum(survey['group_statuses'].values()),
        'units_without_reference_groups':survey['counts'].get('units_without_reference_groups',0),
        'baseline':base_summary['counts'],'catalog':stats,
        'ibid_recovery':relations['counts'],'source_database_dependencies':config['source_db_stats'],
        'input_recipe_sha256':config['input_files'],
        'omitted_legacy_layers':survey['omitted_legacy_layers'],
        'entire_historical_catalog_rebuilt':False,'canonical_updates':0,'inference_calls':0}
    if baseline:
        volumes=[p.name for p in (out/'volumes').iterdir() if p.is_dir()]
        result['historical_comparison']=compare(baseline,out,out/'historical_comparison',volumes)['counts']
    (out/'rebuild.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (out/'rebuild.md').write_text(
        '# Reconstrução da camada genérica a partir do OCR\n\n'
        'Seleção, perfis documentados, OCR atual, candidatos numéricos e herança editorial. '
        'Nenhum banco antigo é insumo desta receita; o baseline opcional serve somente à comparação.\n\n'
        f"- Páginas selecionadas: {result['selected_pages']}.\n"
        f"- Unidades extraídas: {result['parsed_units']}; grupos de referências: {result['reference_groups']}.\n"
        f"- Registros pesquisáveis: {stats['items']}; com candidatos: {stats['with_destinations']}; sem candidatos: {stats['pending']}.\n"
        f"- Unidades sem grupos de referências: {result['units_without_reference_groups']}, preservadas em volumes/*/raw_pages.jsonl junto dos resíduos.\n\n"
        'As contagens de registros, grupos e unidades são diferentes: listas podem gerar várias ocorrências. '
        'Cobertura de candidatos não mede acerto. As camadas estruturadas posteriores ainda não foram reaplicadas '
        'e a saída não substitui o catálogo histórico. Consulte rebuild.json e historical_comparison/ quando presente.\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--source-aliases',type=Path)
    p.add_argument('--root',type=Path,default=Path('teste'))
    p.add_argument('--workers',type=int,default=20)
    p.add_argument('--volume',action='append',default=[])
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--baseline-survey',type=Path,help='Optional comparison only, never an extraction input')
    p.add_argument('--compare-to',type=Path,help='Compare complete logical catalog with a previous fresh run')
    args=p.parse_args()
    if args.workers<1:p.error('workers must be positive')
    volumes=args.volume or sorted({r['volume'] for r in json.loads(args.manifest.read_text())['pages']})
    command=[sys.executable,str(ROOT/'scripts/survey_global_index_targets.py'),
        '--raw-selection',str(args.manifest.resolve()),'--root',str(args.root.resolve()),
        '--workers',str(args.workers),'--out',str(args.out.resolve())]
    if args.source_aliases:command+=['--source-aliases',str(args.source_aliases.resolve())]
    for volume in volumes:command+=['--volume',volume]
    subprocess.run(command,cwd=ROOT,check=True)
    result=finish(args.out,args.baseline_survey)
    if args.compare_to:
        result['source_reproducibility']=compare_sources(args.out,args.compare_to)
        result['reproducibility']=compare_rebuilds(args.out,args.compare_to)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
