"""Hash-bound source defects, independent of parser execution and destination quality."""
import argparse
from collections import Counter
import json
from pathlib import Path

from tools.indexing.entry_parser_experiment import ROOT, digest

VERSION = 'index-source-quality-issues-v1'
DEFAULT_REGISTRY = ROOT/'data/index_entry_parser/profiles/source_quality_issues_v1.json'
KINDS = {'missing_printed_entries', 'duplicated_transcription', 'merged_entries',
         'orphan_continuation', 'missing_reference_qualifier', 'invented_editorial_material'}


def inspect_registry(path, root=ROOT):
    data = json.loads(Path(path).read_text())
    if data.get('version') != VERSION:
        raise ValueError('Unsupported source quality registry')
    pages, ids, sources = [], set(), set()
    for page in data['pages']:
        source = (root/Path(page['source'])).resolve()
        if source in sources or not page['issues']:
            raise ValueError('Duplicate source or empty source issue page')
        sources.add(source)
        checks, errors = {}, []
        for name in ('source', 'image', 'review_report'):
            file = (root/Path(page[name])).resolve()
            try:
                checks[name] = digest(file.read_bytes()) == page[name+'_sha256']
            except OSError as exc:
                checks[name] = False
                errors.append({'artifact': name, 'error': str(exc)})
        for issue in page['issues']:
            if issue['id'] in ids or issue['kind'] not in KINDS:
                raise ValueError('Duplicate issue ID or unsupported kind')
            ids.add(issue['id'])
            if not issue.get('visual_location') or not issue.get('description'):
                raise ValueError('Issue must have a visual locator and description')
            if issue.get('reviewer') not in ('assistant', 'human') or issue.get('recovery_status') != 'pending':
                raise ValueError('Only evidenced, pending reviews are supported')
        current = all(checks.values())
        kinds = {i['kind'] for i in page['issues']}
        pages.append({**page, 'source':str(source), 'artifact_checks':checks, 'artifact_errors':errors,
                      'evidence_status':'current' if current else 'stale_requires_recheck',
                      'source_completeness': ('known_omission' if 'missing_printed_entries' in kinds
                                              else 'not_established') if current else 'requires_recheck',
                      'source_fidelity':'known_defects' if current else 'requires_recheck',
                      'parser_execution':'independent_not_assessed',
                      'automatic_repair_allowed':False, 'source_updates':0})
    return pages


def annotate(meta, issues):
    """A changed source does not silently resolve a defect or inherit an old review."""
    if issues is None:
        return {'source_issue_status':'not_listed_not_certified', 'source_issue_ids':[]}
    current = issues['evidence_status']=='current' and meta['source_sha256']==issues['source_sha256']
    return {'source_issue_status':'known_defects' if current else 'stale_requires_recheck',
            'source_issue_ids':[i['id'] for i in issues['issues']],
            'source_completeness':issues['source_completeness'] if current else 'requires_recheck',
            'source_recovery_status':'pending', 'source_review_report':issues['review_report']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry',type=Path,default=DEFAULT_REGISTRY)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Use a new output directory')
    pages = inspect_registry(args.registry)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'pages.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2)+'\n')
    summary = {'version':VERSION, 'registry':str(args.registry.resolve()),
               'registry_sha256':digest(args.registry.read_bytes()), 'pages':len(pages),
               'evidence_statuses':dict(Counter(p['evidence_status'] for p in pages)),
               'issues_by_kind':dict(Counter(i['kind'] for p in pages for i in p['issues'])),
               'source_updates':0, 'production_updates':0}
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
