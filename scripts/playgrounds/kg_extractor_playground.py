#!/usr/bin/env python3
"""Local page-atomic historical KG research playground. No API calls on startup."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.knowledge_graph.playground.common import DEFAULT_DB, dumps, read_json
from tools.knowledge_graph.playground.comparison import compare_runs
from tools.knowledge_graph.playground.experiment_runner import execute_experiment, plan_experiment
from tools.knowledge_graph.playground.reporting import csv_report, export_jsonl, markdown, report
from tools.knowledge_graph.playground.service import Playground
from tools.knowledge_graph.playground.providers import DEFAULT_MAX_OUTPUT_TOKENS
from tools.knowledge_graph.playground.validation import evaluate_response


def parser():
    shared = argparse.ArgumentParser(add_help=False)
    for name, default in [('db', DEFAULT_DB), ('summaries-db', ROOT / 'data/patristica_resumos.db'),
                          ('indices-db', ROOT / 'data/patristic_indices.db'), ('corpus-root', ROOT / 'teste'),
                          ('benchmark-dir', ROOT / '.work/kg_benchmark_v1'), ('ocr-versions-db', ROOT / 'data/ocr_versions.db')]:
        shared.add_argument('--' + name, type=Path, default=default)
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve', parents=[shared], help='Local UI; never performs inference on startup')
    serve.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1', 'localhost'])
    serve.add_argument('--port', type=int, default=8766)
    run = sub.add_parser('run', parents=[shared], help='Prepare or execute page/model/prompt matrix')
    run.add_argument('--pages', nargs='+', default=[])
    run.add_argument('--benchmark-ids', nargs='+', default=[])
    run.add_argument('--generation-id', type=int)
    run.add_argument('--run-id', type=int)
    run.add_argument('--evaluation', action='store_true')
    run.add_argument('--allow-source-drift', action='store_true')
    run.add_argument('--demo', action='store_true', help='Explicitly synthetic starter-kit source')
    run.add_argument('--provider', choices=['ollama', 'openai', 'compatible', 'meta', 'fixture'], default='ollama')
    run.add_argument('--model')
    run.add_argument('--base-url')
    run.add_argument('--api-key-env')
    run.add_argument('--model-configs', type=Path, help='JSON array of complete provider profiles')
    run.add_argument('--reasoning-effort')
    run.add_argument('--temperature', type=float)
    run.add_argument('--max-output-tokens', type=int, default=DEFAULT_MAX_OUTPUT_TOKENS,
                     help='Output budget, including reasoning where reported (default: %(default)s)')
    run.add_argument('--timeout', type=int, default=180)
    run.add_argument('--retries', type=int, default=1)
    run.add_argument('--retry-backoff-base', type=float, default=1)
    run.add_argument('--retry-backoff-max', type=float, default=60)
    run.add_argument('--retry-after-max', type=float, default=300)
    run.add_argument('--num-ctx', type=int)
    run.add_argument('--vision', action='store_true', default=None)
    run.add_argument('--reasoning-support', action='store_true', default=None)
    run.add_argument('--temperature-support', action='store_true', default=None)
    run.add_argument('--structured-output', choices=['schema', 'json', 'none'])
    run.add_argument('--token-parameter', choices=['max_tokens', 'max_completion_tokens', 'num_predict'])
    run.add_argument('--prices', type=Path, help='Override catalog rates with JSON (USD per million tokens); {} disables estimates')
    run.add_argument('--prompts', nargs='+', default=['concepts-v1'])
    run.add_argument('--mode', nargs='+', default=['concepts_relations'], choices=['concepts_only', 'concepts_relations', 'full_multimodal'])
    run.add_argument('--facsimile', nargs='+', default=['off'], choices=['off', 'on', 'auto'])
    run.add_argument('--system-prompt-file', type=Path, help='New prompt revision, preserved verbatim plus mode instructions')
    run.add_argument('--repetitions', type=int, default=1)
    run.add_argument('--concurrency', type=int, default=1)
    run.add_argument('--bypass-cache', action='store_true')
    run.add_argument('--dry-run', action='store_true')
    run.add_argument('--name', default='KG experiment')
    resume = sub.add_parser('resume', parents=[shared])
    resume.add_argument('--experiment', required=True)
    resume.add_argument('--concurrency', type=int, default=1)
    resume.add_argument('--retry-errors', action='store_true')
    rep = sub.add_parser('report', parents=[shared])
    rep.add_argument('--experiment', required=True)
    rep.add_argument('--format', choices=['markdown', 'json', 'csv'], default='markdown')
    rep.add_argument('--corpus-pages', type=int, default=288000)
    rep.add_argument('--output', type=Path)
    export = sub.add_parser('export', parents=[shared])
    export.add_argument('--experiment', required=True)
    export.add_argument('--output', type=Path, required=True)
    compare = sub.add_parser('compare', parents=[shared])
    compare.add_argument('--runs', nargs='+', type=int, required=True)
    compare.add_argument('--output', type=Path)
    validate = sub.add_parser('validate', parents=[shared])
    validate.add_argument('--run', type=int, required=True)
    validate.add_argument('--matching-policy', choices=['local-quote-v2','local-quote-v3','local-quote-v4'],
                          help='Explicitly append a versioned validation; never changes raw output or original run status')
    inspect = sub.add_parser('inspect', parents=[shared])
    inspect.add_argument('--run', type=int, required=True)
    inspect.add_argument('--output', type=Path)
    create = sub.add_parser('corpus-create', parents=[shared], help='Save a frozen corpus campaign; no inference')
    create.add_argument('--provider', choices=['ollama', 'openai', 'compatible', 'meta'], required=True)
    create.add_argument('--model', required=True)
    create.add_argument('--base-url')
    create.add_argument('--api-key-env')
    create.add_argument('--reasoning-effort')
    create.add_argument('--temperature', type=float)
    create.add_argument('--num-ctx', type=int)
    create.add_argument('--vision', action='store_true', default=None)
    create.add_argument('--reasoning-support', action='store_true', default=None)
    create.add_argument('--temperature-support', action='store_true', default=None)
    create.add_argument('--structured-output', choices=['schema', 'json', 'none'])
    create.add_argument('--token-parameter', choices=['max_tokens', 'max_completion_tokens', 'num_predict'])
    create.add_argument('--prices', type=Path)
    create.add_argument('--max-output-tokens', type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    create.add_argument('--timeout', type=int, default=180)
    create.add_argument('--retries', type=int, default=3)
    create.add_argument('--retry-backoff-base', type=float, default=1)
    create.add_argument('--retry-backoff-max', type=float, default=60)
    create.add_argument('--retry-after-max', type=float, default=300)
    create.add_argument('--prompt', default='concepts-v2.2-confidence')
    create.add_argument('--mode', choices=['concepts_only', 'concepts_relations', 'full_multimodal'], default='concepts_relations')
    create.add_argument('--facsimile', choices=['off', 'on', 'auto'], default='off')
    create.add_argument('--volumes', nargs='+', default=[])
    create.add_argument('--collections', nargs='+', default=[])
    create.add_argument('--page-from', type=int, default=1)
    create.add_argument('--page-to', type=int)
    create.add_argument('--max-pages', type=int)
    create.add_argument('--evaluation', action='store_true')
    create.add_argument('--allow-source-drift', action='store_true', help='Use current OCR and record differences from the generation OCR')
    create.add_argument('--bypass-cache', action='store_true')
    create.add_argument('--name', default='KG corpus')
    corpus_run = sub.add_parser('corpus-run', parents=[shared], help='Discover and drain/resume a campaign with spawn workers')
    corpus_run.add_argument('--campaign', required=True)
    corpus_run.add_argument('--workers', type=int, default=2)
    corpus_run.add_argument('--dry-run', action='store_true')
    corpus_run.add_argument('--retry-errors', action='store_true')
    corpus_run.add_argument('--allow-source-drift', action=argparse.BooleanOptionalAction, default=None,
                            help='Persist this source policy and retry pages blocked only by OCR drift')
    corpus_run.add_argument('--follow', action='store_true')
    corpus_run.add_argument('--poll-interval', type=int, default=60)
    corpus_run.add_argument('--max-jobs', type=int)
    corpus_status = sub.add_parser('corpus-status', parents=[shared], help='Read persistent progress and recent attempts')
    corpus_status.add_argument('--campaign', required=True)
    corpus_status.add_argument('--output', type=Path)
    replay = sub.add_parser('locator-replay', parents=[shared], help='Read-only old/new evidence validation; zero inference')
    replay.add_argument('--campaign', default='EXP-76db1db8b118')
    replay.add_argument('--expected-runs', type=int, default=1031)
    replay.add_argument('--output', type=Path, default=ROOT/'.work/kg_campaign_prompt_audit/locator_comparison.md')
    ab_prepare = sub.add_parser('ab-prepare', parents=[shared], help='Freeze an A/B, A/B/C or A/B/C/D study; zero inference')
    ab_prepare.add_argument('--audit-dir', type=Path, default=ROOT/'.work/kg_campaign_prompt_audit')
    ab_prepare.add_argument('--study', choices=['navigation-v23', 'stance-v24', 'semantics-v25'], default='navigation-v23')
    ab_prepare.add_argument('--source-experiment', default='EXP-13073b566bc1', help='Completed navigation A/B for stance regression pages')
    ab_prepare.add_argument('--from-prepared', help='Unexecuted stance-v24 experiment to preserve as the source for semantics-v25')
    ab_prepare.add_argument('--extra-pages', type=int, default=8, help='New diagnostic pages for stance study; excludes audit pages and immediate neighbors')
    ab_prepare.add_argument('--seed', type=int, default=20261102)
    ab_prepare.add_argument('--name')
    ab_prepare.add_argument('--output', type=Path)
    ab_run = sub.add_parser('ab-run', parents=[shared], help='Explicitly execute/resume prepared paid A/B calls')
    ab_run.add_argument('--experiment', required=True)
    ab_run.add_argument('--concurrency', type=int, default=2)
    ab_run.add_argument('--max-calls', type=int)
    ab_run.add_argument('--retry-interrupted', action='store_true', help='Allow another paid attempt after a hard interruption with unknown remote outcome')
    ab_report = sub.add_parser('ab-report', parents=[shared], help='Read-only paired measurements and review packets')
    ab_report.add_argument('--experiment', required=True)
    ab_report.add_argument('--output', type=Path, help='Defaults to the selected study directory')
    ab_report.add_argument('--reviews', type=Path, help='Independent adjudications copied from the exported review template')
    search = sub.add_parser('ab-search-report', parents=[shared], help='Diagnostic BM25 retrieval on independently judged page/query pairs')
    search.add_argument('--experiment', required=True)
    search.add_argument('--judgments', type=Path, required=True)
    search.add_argument('--output', type=Path, default=ROOT/'.work/kg_ab_navigation_v23/search_report.json')
    search.add_argument('--k', type=int, default=5)
    search_prepare = sub.add_parser('ab-search-prepare', parents=[shared], help='Export independent relevance-review form; zero inference')
    search_prepare.add_argument('--experiment', required=True)
    search_prepare.add_argument('--output', type=Path, default=ROOT/'.work/kg_ab_navigation_v23/search_judgments.json')
    search_prepare.add_argument('--query-file', type=Path, help='Versioned query proposals; relevance stays pending')
    search_prepare.add_argument('--arms', nargs='+', help='Extraction arms to compare in addition to summary')
    search_prepare.add_argument('--missing-output-policy', choices=['strict', 'summary_only'], default='strict',
                                help='Explicitly retain summary-only pages when an extraction is truncated')
    vp = sub.add_parser('verify-prepare', parents=[shared], help='Freeze 24 checks of existing D outputs; zero inference')
    vp.add_argument('--source-experiment', default='EXP-55b6938cd135')
    vp.add_argument('--seed', type=int, default=20261103)
    vp.add_argument('--name', default='KG D + source verification v1')
    vp.add_argument('--output', type=Path, default=ROOT/'.work/kg_verify_stance_v1/preflight.json')
    vr = sub.add_parser('verify-run', parents=[shared], help='Explicitly execute/resume only the prepared second-pass calls')
    vr.add_argument('--experiment', required=True)
    vr.add_argument('--concurrency', type=int, default=2)
    vr.add_argument('--max-calls', type=int)
    vr.add_argument('--retry-interrupted', action='store_true')
    report_v = sub.add_parser('verify-report', parents=[shared], help='Offline paired results, blinded packets and adjudications')
    report_v.add_argument('--experiment', required=True)
    report_v.add_argument('--output', type=Path, default=ROOT/'.work/kg_verify_stance_v1/report.md')
    report_v.add_argument('--reviews', type=Path, help='Independent decisions copied from the blinded template')
    report_v.add_argument('--matching-policy', choices=['local-quote-v4'],
                          help='Compare the micro-OCR policy offline; does not write validation history')
    return ap


def provider_config(args):
    keys = ['provider', 'model', 'base_url', 'api_key_env', 'reasoning_effort', 'temperature',
            'max_output_tokens', 'timeout', 'retries', 'num_ctx', 'vision', 'reasoning_support',
            'temperature_support', 'structured_output', 'token_parameter',
            'retry_backoff_base', 'retry_backoff_max', 'retry_after_max']
    config = {k: getattr(args, k) for k in keys if getattr(args, k) is not None}
    if args.prices:
        config['prices'] = read_json(args.prices)
    return config


def output(value, path=None):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')
    else:
        print(text)


def main(argv=None):
    args = parser().parse_args(argv)
    # Read-only diagnostics must never initialize Store or touch production databases.
    if args.command == 'verify-report':
        from tools.knowledge_graph.playground.verification_reporting import write_verification_report
        output(write_verification_report(args.db,args.experiment,args.output,reviews=args.reviews,
                                         matching_policy=args.matching_policy))
        return 0
    if args.command in {'verify-prepare','verify-run'}:
        from tools.knowledge_graph.playground.storage import Store
        store = Store(args.db)
        if args.command == 'verify-prepare':
            from tools.knowledge_graph.playground.verification_study import prepare_verification
            data = prepare_verification(store,source_experiment=args.source_experiment,seed=args.seed,
                                        name=args.name,output=args.output,benchmark_dir=args.benchmark_dir)
            output({k:data[k] for k in ('experiment','size','status','inference_calls','estimated_calls_usd','checks')})
            return 0
        from tools.knowledge_graph.playground.verification_runner import execute_verification
        result = execute_verification(store,args.experiment,concurrency=args.concurrency,max_calls=args.max_calls,
                                      retry_interrupted=args.retry_interrupted,
                                      progress=lambda event: print(dumps(event),flush=True))
        output(result)
        return 130 if result['stopped'] else (1 if any(result['states'].get(s) for s in ('invalid','error','interrupted')) else 0)
    if args.command == 'locator-replay':
        from tools.knowledge_graph.playground.validation_replay import replay
        data = replay(args.db,args.campaign,args.output,expected_runs=args.expected_runs,
                      progress=lambda event: print(dumps(event),flush=True))
        output({k:v for k,v in data.items() if k!='examples'})
        return 0
    if args.command == 'ab-report':
        from tools.knowledge_graph.playground.ab_reporting import write_report
        output(write_report(args.db,args.experiment,args.output,reviews=args.reviews))
        return 0
    if args.command == 'ab-search-report':
        from tools.knowledge_graph.playground.retrieval_diagnostic import evaluate_search
        output(evaluate_search(args.db,args.experiment,args.judgments,k=args.k),args.output)
        return 0
    if args.command == 'ab-search-prepare':
        from tools.knowledge_graph.playground.retrieval_diagnostic import prepare_judgments
        output(prepare_judgments(args.db,args.experiment,args.output,query_file=args.query_file,
                                 arms=args.arms,missing_output_policy=args.missing_output_policy))
        return 0
    if args.command in {'ab-prepare','ab-run'}:
        from tools.knowledge_graph.playground.ab_runner import prepare_ab, execute_ab
        from tools.knowledge_graph.playground.storage import Store
        store = Store(args.db)
        if args.command == 'ab-prepare':
            if args.from_prepared and args.study != 'semantics-v25':
                raise ValueError('--from-prepared applies only to --study semantics-v25')
            if args.study == 'semantics-v25':
                if not args.from_prepared:
                    raise ValueError('--study semantics-v25 requires --from-prepared EXP_ID')
                from tools.knowledge_graph.playground.semantics_study import prepare_semantics_study
                data = prepare_semantics_study(store,args.from_prepared,
                    name=args.name or 'KG object semantics v2.5 A/B/C/D',
                    output=args.output or ROOT/'.work/kg_ab_semantics_v25/preflight.json',benchmark_dir=args.benchmark_dir)
            elif args.study == 'stance-v24':
                from tools.knowledge_graph.playground.stance_study import prepare_stance_study
                data = prepare_stance_study(store,args.audit_dir,source_experiment=args.source_experiment,
                    extra_pages=args.extra_pages,seed=args.seed,name=args.name or 'KG stance v2.4 A/B/C',
                    output=args.output or ROOT/'.work/kg_ab_stance_v24/preflight.json',benchmark_dir=args.benchmark_dir)
            else:
                data = prepare_ab(store,args.audit_dir,name=args.name or 'KG navigation v2.3 A/B',
                    output=args.output or ROOT/'.work/kg_ab_navigation_v23/preflight.json',benchmark_dir=args.benchmark_dir)
            output({k:data[k] for k in ('experiment','size','status','inference_calls','estimated_96_calls_usd','estimated_calls_usd','checks') if k in data})
        else:
            result = execute_ab(store,args.experiment,concurrency=args.concurrency,max_calls=args.max_calls,
                                retry_interrupted=args.retry_interrupted,
                                progress=lambda event: print(dumps(event),flush=True))
            output(result)
            return 130 if result['stopped'] else (1 if any(result['states'].get(s) for s in ('invalid','error','interrupted')) else 0)
        return 0
    app = Playground(**{k: getattr(args, k) for k in ('db','summaries_db','indices_db','corpus_root','benchmark_dir','ocr_versions_db')})
    store = app.store
    if args.command == 'serve':
        from tools.knowledge_graph.playground.web_app import serve
        serve(app, args.host, args.port)
    elif args.command == 'run':
        if args.generation_id and len(args.pages) != 1:
            raise ValueError('--generation-id requires exactly one physical page')
        cases = [app.load(page=p, generation_id=args.generation_id, run_id=args.run_id,
                          evaluation=args.evaluation, allow_source_drift=args.allow_source_drift) for p in args.pages]
        cases += [app.load(benchmark_id=i, evaluation=args.evaluation) for i in args.benchmark_ids]
        if args.demo:
            cases.append(app.load(demo=True))
        if args.model_configs:
            configs = read_json(args.model_configs)
        else:
            if not args.model:
                raise ValueError('--model is required (use starter-demo for --provider fixture)')
            configs = [provider_config(args)]
        exp = plan_experiment(store, cases, configs, variants=args.prompts, modes=args.mode,
                              facsimiles=args.facsimile, repetitions=args.repetitions, name=args.name,
                              evaluation=args.evaluation, bypass_cache=args.bypass_cache,
                              system_override=args.system_prompt_file.read_text(encoding='utf-8') if args.system_prompt_file else None)
        print(dumps({'experiment': exp, 'dry_run': args.dry_run, 'runs': [r['id'] for r in store.list_runs(exp)]}))
        if not args.dry_run:
            runs = execute_experiment(store, exp, concurrency=args.concurrency, progress=lambda event: print(dumps(event), flush=True))
            if any(r['status'] in {'error', 'invalid', 'interrupted'} for r in runs):
                return 1
    elif args.command == 'corpus-create':
        from tools.knowledge_graph.playground.corpus_queue import create_campaign
        campaign = create_campaign(app, provider_config(args), variant=args.prompt, mode=args.mode,
                                   facsimile=args.facsimile, volumes=args.volumes, collections=args.collections,
                                   max_pages=args.max_pages, page_from=args.page_from, page_to=args.page_to,
                                   evaluation=args.evaluation, name=args.name, bypass_cache=args.bypass_cache,
                                   allow_source_drift=args.allow_source_drift)
        output({'campaign': campaign, 'inference_performed': False})
    elif args.command == 'corpus-run':
        from tools.knowledge_graph.playground.corpus_runner import run_campaign
        result = run_campaign(store, args.campaign, workers=args.workers, dry_run=args.dry_run,
                              follow=args.follow, poll_interval=args.poll_interval, retry_errors=args.retry_errors,
                              allow_source_drift=args.allow_source_drift,
                              max_jobs=args.max_jobs, progress=lambda event: print(dumps(event), flush=True))
        output(result)
        if result['stopped']:
            return 130
        if any(result['states'].get(s) for s in ('error', 'source_error', 'invalid')):
            return 1
    elif args.command == 'corpus-status':
        from tools.knowledge_graph.playground.corpus_queue import status
        output(status(store, args.campaign), args.output)
    elif args.command == 'resume':
        runs = execute_experiment(store, args.experiment, concurrency=args.concurrency, resume=True,
                                  retry_errors=args.retry_errors, progress=lambda event: print(dumps(event), flush=True))
        if any(r['status'] in {'error', 'invalid'} and r['id'] not in store.retried_runs(args.experiment) for r in runs):
            return 1
    elif args.command == 'report':
        data = report(store, args.experiment, args.corpus_pages)
        output(markdown(data) if args.format == 'markdown' else csv_report(data) if args.format == 'csv' else data, args.output)
    elif args.command == 'compare':
        output(compare_runs(store, args.runs), args.output)
    elif args.command == 'validate':
        run = store.get_run(args.run)
        if args.matching_policy == 'local-quote-v4':
            from tools.knowledge_graph.playground import validation_micro
            evaluator = (validation_micro.evaluate_review if run['prepared'].get('task') == 'stance_review'
                         else validation_micro.evaluate_response)
            result = evaluator(store.get_case(run['case_id'])['snapshot'], run['result'] or {}, run['prepared'])
        elif run['prepared'].get('task') == 'stance_review':
            from tools.knowledge_graph.playground.verification_contract import evaluate_review
            if args.matching_policy and args.matching_policy != run['prepared']['validation_policy']:
                raise ValueError('Review benchmark revalidation must preserve its frozen matching policy')
            result = evaluate_review(store.get_case(run['case_id'])['snapshot'], run['result'] or {}, run['prepared'])
        else:
            result = evaluate_response(store.get_case(run['case_id'])['snapshot'], run['result'] or {}, run['prepared'],
                                       matching_policy=args.matching_policy)
        store.add_validation(args.run, result)
        output(result)
        return 0 if result['valid'] else 1
    elif args.command == 'inspect':
        run = store.get_run(args.run)
        output({'run': run, 'case': store.get_case(run['case_id'])}, args.output)
    elif args.command == 'export':
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('w', encoding='utf-8') as stream:
            stream.writelines(export_jsonl(store, args.experiment))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(2)
