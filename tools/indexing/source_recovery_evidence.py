"""Find evidence for known OCR defects in snapshots of the exact same image."""
import argparse
from collections import Counter
import json
from pathlib import Path

from tools.indexing.entry_parser_experiment import digest
from tools.indexing.ocr_variant_audit import tokens
from tools.indexing.source_quality_issues import DEFAULT_REGISTRY, inspect_registry


def find_anchor(raw, anchor):
    source, needle = tokens(raw), [t['token'] for t in tokens(anchor)]
    if not needle:
        return []
    result=[]
    for i in range(len(source)-len(needle)+1):
        if [t['token'] for t in source[i:i+len(needle)]]!=needle:
            continue
        a,b=source[i]['span'][0],source[i+len(needle)-1]['span'][1]
        left,right=max(0,a-120),min(len(raw),b+240)
        result.append({'span':[a,b],'quote':raw[a:b],'text_sha256':digest(raw[a:b].encode()),
                       'context_span':[left,right],'context':raw[left:right],
                       'context_sha256':digest(raw[left:right].encode())})
    return result


def compare_anchor(current, alternative, anchor, kind):
    left,right=find_anchor(current,anchor),find_anchor(alternative,anchor)
    if not left and right:
        status='missing_anchor_present_in_alternative'
    elif kind=='duplicated_transcription' and len(left)>len(right)>0:
        status='fewer_anchor_occurrences_in_alternative'
    elif right:
        status='context_available_for_review'
    else:
        status='anchor_not_found_in_alternative'
    return {'anchor':anchor,'current_matches':left,'alternative_matches':right,
            'status':status,'automatic_repair_allowed':False,'complete_entry_recovered':False}


def run(registry, audits, out):
    out=Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a new output directory')
    issues=inspect_registry(registry)
    inventories={}
    inputs={str(Path(registry).resolve()):digest(Path(registry).read_bytes())}
    for audit in audits:
        path=Path(audit)/'inventory.json'
        inputs[str(path.resolve())]=digest(path.read_bytes())
        for page in json.loads(path.read_text()):
            key=str(Path(page['source']).resolve())
            if key in inventories:
                raise ValueError('Duplicate alternative inventory for source')
            inventories[key]=page
    rows, exclusions, pending = [],[],[]
    for page in issues:
        inventory=inventories.get(page['source'])
        if page['evidence_status']!='current' or not inventory:
            pending.append({'source':page['source'],'reason':'stale_evidence_or_missing_inventory'})
            continue
        if (inventory['source_sha256']!=page['source_sha256'] or
                inventory['image_sha256']!=page['image_sha256']):
            raise ValueError('Alternative inventory has incompatible source/image hash')
        current=Path(page['source']).read_text()
        for issue in page['issues']:
            anchors=issue.get('search_anchors',[])
            if not anchors:
                pending.append({'issue_id':issue['id'],'reason':'no_reliable_search_anchor'})
            for version in inventory['versions']:
                if version['comparison_status']!='compared' or version['matches_current_file']:
                    continue
                text=Path(version['snapshot']).read_text()
                if (digest(text.encode())!=version['computed_text_sha256'] or
                        digest(text.encode())!=version['text_hash'] or
                        version['image_hash']!=page['image_sha256']):
                    exclusions.append({'version_id':version['id'],'reason':'snapshot_hash_mismatch'})
                    continue
                for anchor in anchors:
                    rows.append({'issue_id':issue['id'],'kind':issue['kind'],
                        'volume':page['volume'],'physical':page['physical'],
                        'source':page['source'],'source_sha256':page['source_sha256'],
                        'image':page['image'],'image_sha256':page['image_sha256'],
                        'version_id':version['id'],'engine':version['engine'],'model':version['model'],
                        'alternative_snapshot':version['snapshot'],
                        'alternative_sha256':version['computed_text_sha256'],
                        **compare_anchor(current,text,anchor,issue['kind'])})
    out.mkdir(parents=True,exist_ok=True)
    (out/'evidence.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    summary={'pages_registered':len(issues),'comparisons':len(rows),
        'statuses':dict(Counter(r['status'] for r in rows)), 'pending':pending,'exclusions':exclusions,
        'issues_with_missing_anchor_evidence':sorted({r['issue_id'] for r in rows
            if r['status']=='missing_anchor_present_in_alternative'}),
        'automatic_repairs':0,'source_updates':0,'inference_calls':0,
        'limitations':['Anchor presence does not certify the whole entry, reading, numbers or insertion point.',
                       'OCR versions of the same engine can be correlated; no majority vote is used.',
                       'Tesseract cache snapshots remain raw evidence; only typed ocr_results texts are searched.']}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({'version':'index-source-recovery-evidence-v1',
        'inputs':inputs,'code_sha256':digest(Path(__file__).read_bytes())},indent=2)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry',type=Path,default=DEFAULT_REGISTRY)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--variant-audit',type=Path,action='append')
    mode.add_argument('--versions-db',type=Path,help='Read history directly; create searchable fragment candidates')
    mode.add_argument('--search-db',type=Path,help='Search an existing candidate experiment, read-only')
    parser.add_argument('--root',type=Path,default=Path('.'))
    parser.add_argument('--volume',action='append',default=[])
    parser.add_argument('--query')
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.search_db:
        if not args.query:
            parser.error('--search-db requires --query')
        from tools.indexing.historical_source_candidates import search
        result=search(args.search_db,args.query,args.limit)
    else:
        if not args.out:
            parser.error('Recovery requires --out')
        if args.versions_db:
            from tools.indexing.historical_source_candidates import run_versions
            result=run_versions(args.registry,args.versions_db,args.out,args.root,args.volume)
        else:
            if args.volume:
                parser.error('--volume applies to --versions-db')
            result=run(args.registry,args.variant_audit,args.out)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
