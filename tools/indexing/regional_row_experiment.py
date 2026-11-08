"""Reextract audited row formats inside recovered regions, preserving prior runs."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from tools.indexing import entry_parser_literal_rows as parser
from tools.indexing import continuation_reviews
from tools.indexing import segmentation_reviews
from tools.indexing.entry_parser_bulk import encoded, fingerprint, open_experiment, readonly, save
from tools.indexing.entry_parser_experiment import compare_rows, digest
from tools.indexing.index_conservation_audit import coverage, inventory


def material_comparison(before, after):
    """Compare printed occurrences and attribution independently of versioned IDs."""
    fields = ('entry_raw','text_raw','references_raw','reference_span','reference_groups','unit_kind')
    key = lambda e: (e['char_start'],e['char_end'])
    old, new = {key(e):e for e in before}, {key(e):e for e in after}
    changed = []
    for span in sorted(old.keys() & new.keys()):
        differences = [field for field in fields if old[span].get(field)!=new[span].get(field)]
        if differences:
            changed.append({'span':list(span),'fields':differences})
    return {'retained_unchanged':len(old.keys() & new.keys())-len(changed),
            'changed':changed, 'not_retained_spans':[list(k) for k in sorted(old.keys()-new.keys())],
            'added_spans':[list(k) for k in sorted(new.keys()-old.keys())]}


def work(task):
    raw = Path(task['source']).read_bytes().decode('utf-8')
    if digest(raw.encode()) != task['source_sha256']:
        raise ValueError(f"Stale regional source: {task['source']}")
    page = parser.parse_page(raw, source=task['source'], physical=task['physical'],
                             volume=task['volume'], scopes=task['scopes'],
                             reviewed_joins=task.get('reviewed_joins',[]),
                             segmentation_reviews=task.get('segmentation_reviews',[]))
    old = task['baseline']
    if old and old['sha256'] != page['sha256']:
        raise ValueError('Baseline source hash mismatch')
    audit = {}
    for label, entries in [('before', old['entries'] if old else []), ('after', page['entries'])]:
        result = coverage(raw,task['scopes'],entries)
        audit[label] = {'characters': result['characters'],
                        'numeric_tokens': dict(Counter(n['status'] for n in result['numeric_tokens'])),
                        'invalid_candidate_ids': result['invalid_candidate_ids'],
                        'entries': len(entries)}
    result = {'status': 'parsed' if page['entries'] else 'no_candidates', 'page': page,
              'diffs': compare_rows(page, []), 'old_rows': [], 'error': None}
    comparison = {k:task[k] for k in ['source','volume','physical','source_sha256']}
    comparison.update(baseline_page_present=old is not None, **audit)
    comparison['material_comparison'] = material_comparison(old['entries'] if old else [], page['entries'])
    return result, comparison


def run(args):
    if args.workers < 1:
        raise ValueError('workers must be positive')
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Use a new output directory')
    volumes = args.volume or list(parser.PROFILES)
    if set(volumes) - parser.PROFILES.keys():
        raise ValueError('Only explicitly audited volume grammars are enabled')
    for protected in [args.baseline_db,args.source_db]:
        if args.experiment_db.resolve() == protected.resolve() or (
                args.experiment_db.exists() and protected.exists() and args.experiment_db.samefile(protected)):
            raise ValueError('Output experiment DB must differ from all source databases')
    _, pages, scopes = inventory(args.recovery)
    review_path = getattr(args,'continuation_reviews',None)
    review_bytes = Path(review_path).read_bytes() if review_path else None
    reviews = continuation_reviews.load_reviews(review_path) if review_path else []
    segmentation_path=getattr(args,'segmentation_reviews',None)
    segmentation_bytes=Path(segmentation_path).read_bytes() if segmentation_path else None
    partitions=segmentation_reviews.load_reviews(segmentation_path) if segmentation_path else []
    selected_pages=getattr(args,'page',None)
    if selected_pages and any(p<1 for p in selected_pages):
        raise ValueError('Physical pages must be positive')
    con = readonly(args.baseline_db)
    tasks = []
    try:
        if not con.execute('SELECT 1 FROM runs WHERE run_id=?',(args.baseline_run,)).fetchone():
            raise ValueError('Unknown baseline run')
        for source, regions in sorted(scopes.items()):
            info = pages[source]
            if info['volume'] not in volumes:
                continue
            if selected_pages and info['physical'] not in selected_pages:
                continue
            row = con.execute('''SELECT t.result_json FROM run_pages p JOIN tasks t USING(fingerprint)
                WHERE p.run_id=? AND p.source=?''',(args.baseline_run,source)).fetchone()
            task = {'source':source,'volume':info['volume'],'physical':info['physical'],
                    'source_sha256':info['source_sha256'],'profile':parser.PROFILES[info['volume']],
                    'scopes':regions,'version':parser.VERSION,
                    'code_sha256':digest(Path(parser.__file__).read_bytes()),
                    'review_code_sha256':digest(Path(continuation_reviews.__file__).read_bytes()),
                    'review_manifest_sha256':digest(review_bytes) if review_bytes is not None else None,
                    'reviewed_joins':[r for r in reviews if r['source']==source],
                    'segmentation_code_sha256':digest(Path(segmentation_reviews.__file__).read_bytes()),
                    'segmentation_manifest_sha256':digest(segmentation_bytes) if segmentation_bytes is not None else None,
                    'segmentation_reviews':[r for r in partitions if r['source']==source],
                    'baseline_run':args.baseline_run,
                    'baseline':json.loads(row[0]).get('page') if row else None}
            task['fingerprint'] = fingerprint({k:v for k,v in task.items() if k != 'baseline'})
            tasks.append(task)
    finally:
        con.close()
    if not tasks:
        raise ValueError('No recovered pages for the selected profiles')
    if any(r['source'] not in {t['source'] for t in tasks} for r in reviews+partitions):
        raise ValueError('Review source outside the selected regional tasks')
    args.out.mkdir(parents=True,exist_ok=True)
    if review_bytes is not None:
        (args.out/'continuation_reviews.json').write_bytes(review_bytes)
    if segmentation_bytes is not None:
        (args.out/'segmentation_reviews.json').write_bytes(segmentation_bytes)
    db = open_experiment(args.experiment_db,args.source_db)
    run_id = uuid.uuid4().hex
    config = {k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}
    config.update(run_id=run_id,version=parser.VERSION,
                  parser_sha256=digest(Path(parser.__file__).read_bytes()),
                  review_manifest_sha256=digest(review_bytes) if review_bytes is not None else None,
                  segmentation_manifest_sha256=digest(segmentation_bytes) if segmentation_bytes is not None else None,
                  recovery_sections_sha256=digest((args.recovery/'sections.jsonl').read_bytes()))
    (args.out/'manifest.json').write_text(encoded(config)+'\n')
    db.execute('INSERT INTO runs VALUES(?,?,?,?)',
               (run_id,datetime.now(timezone.utc).isoformat(),encoded(config),'running'))
    db.commit()
    comparisons = []
    proposal_count = 0
    applied_joins = 0
    fragment_link_count=0
    try:
        with (ThreadPoolExecutor(max_workers=args.workers) as pool,
              (args.out/'comparisons.jsonl').open('w') as stream,
              (args.out/'entries.jsonl').open('w') as entries_file,
              (args.out/'residuals.jsonl').open('w') as residual_file,
              (args.out/'continuation_proposals.jsonl').open('w') as proposal_file,
              (args.out/'fragment_links.jsonl').open('w') as link_file):
            for task, (result, comparison) in zip(tasks,pool.map(work,tasks)):
                save(db,run_id,{k:v for k,v in task.items() if k != 'baseline'},result)
                stream.write(encoded(comparison)+'\n')
                for entry in result['page']['entries']:
                    entries_file.write(encoded(entry)+'\n')
                for residual in result['page']['residuals']:
                    residual_file.write(encoded({'source':task['source'],'volume':task['volume'],
                        'physical':task['physical'],'source_sha256':task['source_sha256'],**residual})+'\n')
                for proposal in result['page'].get('continuation_proposals',[]):
                    proposal_file.write(encoded({'volume':task['volume'],'physical':task['physical'],**proposal})+'\n')
                    proposal_count += 1
                    applied_joins += bool(proposal.get('applied'))
                for link in result['page'].get('fragment_links',[]):
                    link_file.write(encoded({'volume':task['volume'],'physical':task['physical'],**link})+'\n')
                    fragment_link_count+=1
                comparisons.append(comparison)
                if args.verbose:
                    print(encoded({'volume':task['volume'],'physical':task['physical'],
                                   'entries':len(result['page']['entries'])}),flush=True)
        totals = {}
        for label in ['before','after']:
            counts, numbers = Counter(), Counter()
            for item in comparisons:
                counts.update(item[label]['characters'])
                numbers.update(item[label]['numeric_tokens'])
            totals[label] = {'characters':dict(counts),'numeric_tokens':dict(numbers),
                             'entries':sum(x[label]['entries'] for x in comparisons)}
        summary = {'run_id':run_id,'pages':len(tasks),'volumes':volumes,**totals,
                   'previously_unparsed_pages':sum(not x['baseline_page_present'] for x in comparisons),
                   'import_ready':False,'quality_certified':False,'db_updates':0,'api_calls':0,
                   'continuation_proposals':proposal_count,
                   'reviewed_joins_staged':applied_joins,
                   'continuation_proposals_pending':proposal_count-applied_joins,
                   'fragment_links_pending':fragment_link_count,
                   'experiment_db':str(args.experiment_db)}
        summary['material_comparison'] = {
            'retained_unchanged':sum(c['material_comparison']['retained_unchanged'] for c in comparisons),
            **{k:sum(len(c['material_comparison'][k]) for c in comparisons)
               for k in ['changed','not_retained_spans','added_spans']}}
        (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        report = ['# Reextração regional experimental', '',
                  'Comparação de consumo literal do OCR; não mede precisão ou recall editorial.',
                  'Fontes e banco principal somente leitura. Nenhuma localização de destino.', '',
                  '| Volume | Arquivo físico | Parser anterior presente | Unidades antes | Unidades depois | Resíduo antes | Resíduo depois |',
                  '|---|---:|---|---:|---:|---:|---:|']
        for item in comparisons:
            before,after = item['before'],item['after']
            report.append(f"| {item['volume']} | {item['physical']} | {item['baseline_page_present']} | "
                f"{before['entries']} | {after['entries']} | {before['characters']['residual']} | {after['characters']['residual']} |")
        report += ['', 'Unidades continuam candidatas. Números romanos, subdivisões e namespaces são literais.',
                   'Comparação por spans e atribuição literal (não por IDs): '+encoded(summary['material_comparison']),
                   f'{proposal_count} propostas de continuidade: {applied_joins} incorporadas apenas ao staging com revisão visual explícita; {proposal_count-applied_joins} permanecem fora das entradas e do material consumido.',
                   'reference_groups mantém cada trecho e o texto da cláusula proprietária com spans e hashes.',
                   'references_raw/reference_span ficam nulos quando existem vários grupos, para evitar achatamento.',
                   'Grupos separados por linhas em branco em layouts quebrados são unidades textuais; limites de verbete permanecem pendentes.',
                   'Colunas/páginas interrompidas não são unidas automaticamente. Não usar estes candidatos como gold.',
                   'O aumento de consumo inclui páginas ausentes da execução anterior; confira a coluna correspondente.']
        (args.out/'report.md').write_text('\n'.join(report)+'\n')
        with db:
            db.execute('UPDATE runs SET status=? WHERE run_id=?',('completed',run_id))
        return summary
    except BaseException:
        with db:
            db.execute('UPDATE runs SET status=? WHERE run_id=?',('failed',run_id))
        raise
    finally:
        db.close()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--recovery',type=Path,required=True)
    cli.add_argument('--baseline-run',required=True)
    cli.add_argument('--baseline-db',type=Path,default=Path('data/index_entry_parser/experiments.sqlite'))
    cli.add_argument('--source-db',type=Path,default=Path('data/alphabetical_indices.db'))
    cli.add_argument('--experiment-db',type=Path,default=Path('data/index_entry_parser/regional_rows.sqlite'))
    cli.add_argument('--volume',action='append',choices=sorted(parser.PROFILES))
    cli.add_argument('--page',type=int,action='append',help='Select physical pages within the selected volumes')
    cli.add_argument('--segmentation-reviews',type=Path,
                     help='Exact-source visual paragraph partitions; does not correct OCR readings')
    cli.add_argument('--continuation-reviews',type=Path,
                     help='Explicit visual reviews tied to exact OCR spans and image hashes; staging only')
    cli.add_argument('--workers',type=int,default=20)
    cli.add_argument('--verbose',action='store_true')
    cli.add_argument('--out',type=Path,required=True)
    print(json.dumps(run(cli.parse_args()),ensure_ascii=False,indent=2))
