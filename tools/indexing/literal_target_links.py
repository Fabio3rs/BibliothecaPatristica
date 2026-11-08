"""Bound existing literal references to internal numbered text, without extraction or inference."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import re
import random
import sqlite3

from scripts.audit_alphabetical_numbering import supported_sequence_nodes
from scripts.scan_editorial_ocr import ATTR
from tools.corpus_utils import page_files, page_number
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import digest, source_span
from tools.indexing.structured_index_experiment import check_span, encoded, key

VERSION = 'literal-target-links-v1'
LOCATION_POLICY = 'page-first-v2'
MARKER = re.compile(r'(?<!\w)(?P<number>[1-9][0-9]{0,2})\s*(?P<letter>[ab])(?!\w)')
TOKEN = re.compile(r'(?:(?P<number>[1-9][0-9]{0,2})\s*)?(?P<letter>[ab])(?!\w)')


def rank(number, letter):
    return 2*number + (letter == 'b')


def interpret_group(raw, offset):
    """Parse only complete lists. Bare letters remain unresolved, never inherited."""
    pos, result = 0, []
    while pos < len(raw):
        while pos < len(raw) and raw[pos].isspace():
            pos += 1
        token = TOKEN.match(raw, pos)
        if not token:
            return None
        number = int(token['number']) if token['number'] else None
        result.append({'raw': token[0], 'number': number, 'letter_raw': token['letter'],
                       'char_start': offset+token.start(), 'char_end': offset+token.end()})
        pos = token.end()
        rest = raw[pos:]
        if re.fullmatch(r'\s*\.?\s*', rest):
            return result
        separator = re.match(r'\s*[,;]\s*', rest)
        if not separator:
            return None
        pos += separator.end()
        if pos == len(raw):
            return None
    return None


def validate_proofs(profile, root):
    proofs = profile.get('evidence', [])
    roles = {p['role'] for p in proofs}
    if not {'index_notice','target_work_title','target_continuation_title'} <= roles:
        raise ValueError('Require editorial notice and work/continuation evidence')
    inventory = []
    for proof in proofs:
        path = (root/proof['path']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Evidence outside corpus root')
        data = path.read_bytes()
        if digest(data) != proof['sha256'] or not proof.get('quote') or proof['quote'] not in data.decode():
            raise ValueError('Editorial evidence changed or quote absent')
        inventory.append({'path': str(path), 'sha256': digest(data), 'role': proof['role']})
    return inventory


def build_map(profile, root):
    """Recognize literal a/b markers only in explicitly Latin main-text blocks."""
    pages, markers, nodes, inventory = [], [], defaultdict(set), []
    skipped = Counter()
    seen = set()
    for spec in profile['targets']:
        grouped = page_files(root/spec['volume']/'text', suffixes=['.txt'])
        for physical in range(spec['first'], spec['last']+1):
            paths = grouped.get(physical, [])
            if len(paths) != 1:
                raise ValueError(f"Missing or ambiguous physical file: {spec['volume']}/{physical}")
            path = paths[0].resolve()
            if str(path) in seen:
                raise ValueError('Overlapping target scopes')
            seen.add(str(path))
            data = path.read_bytes(); raw = data.decode('utf-8')
            ordinal = len(pages)
            page = {'ordinal': ordinal, 'volume': spec['volume'], 'physical': physical,
                    'path': str(path), 'sha256': digest(data)}
            pages.append(page); inventory.append(page)
            for block in blocks(raw):
                attrs = dict(ATTR.findall(raw[block['start']:block['body_start']]))
                if block['kind'] != 'texto_principal' or attrs.get('script') != 'latino':
                    skipped[block['kind']+':'+attrs.get('script','unknown')] += 1
                    continue
                for match in MARKER.finditer(raw, block['body_start'], block['body_end']):
                    number, letter = int(match['number']), match['letter']
                    marker_rank = rank(number, letter)
                    item = {**page, 'number': number, 'letter_raw': letter, 'rank': marker_rank,
                            'raw': match[0], **source_span(raw, *match.span()),
                            'excerpt': raw[max(block['body_start'],match.start()-80):min(block['body_end'],match.end()+120)]}
                    item['id'] = key(str(path),page['sha256'],*match.span())
                    markers.append(item); nodes[marker_rank].add(ordinal)
    support = supported_sequence_nodes(nodes, **profile.get('sequence', {}))
    for marker in markers:
        marker['sequence_support'] = support.get((marker['rank'], marker['ordinal']))
    return pages, markers, inventory, dict(skipped)


def locate(token, pages, by_rank, max_files):
    result = {'status':'missing_supported_boundary','files':[],
              'start_markers':[], 'end_markers':[],
              'location_policy':LOCATION_POLICY, 'extent_status':'unknown',
              'file_evidence':'none'}
    if token['number'] is None:
        result['status']='context_required'
        return result
    r = rank(token['number'], token['letter_raw'])
    starts, ends = by_rank.get(r, []), by_rank.get(r+1, [])
    result.update(start_markers=[m['id'] for m in starts],
                  end_markers=[m['id'] for m in ends])
    if not starts:
        return result
    ordinals = sorted({m['ordinal'] for m in starts})
    if len(ordinals)>1:
        result['status']='ambiguous_markers'
        result['extent_status']='ambiguous_start_files'
        return result
    if len(starts)==1 and len(ends)==1:
        first, last = starts[0], ends[0]
        a,b = first['ordinal'],last['ordinal']
        if b<a or (a==b and first['char_end']>last['char_start']):
            result.update(status='reversed_markers',extent_status='conflicting_boundaries')
            return result
        if b-a+1<=max_files:
            result['files']=pages[a:b+1]
            result['status']='unique_file_window_candidate' if a==b else 'bounded_window_candidate'
            result['extent_status']='bounded'
            result['file_evidence']='ordered_marker_window'
            result['window']={'start':{'path':first['path'],'offset':first['char_start']},
                              'end_exclusive':{'path':last['path'],'offset':last['char_start']},
                              'file_boundary_policy':'end_file_included_conservatively'}
            return result
        reason='window_exceeds_limit'
    elif len(starts)>1:
        reason='ambiguous_start_position_same_file'
    elif not ends:
        reason='missing_supported_end'
    else:
        reason='ambiguous_end'
    # This identifies the printed marker's file, not all pages of its passage.
    result.update(status='marker_file_candidate',files=[pages[ordinals[0]]],
                  extent_status=reason,file_evidence='supported_marker_same_file')
    return result


def run(proposals_path, profile_path, root, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Use a new output directory')
    profile_bytes = Path(profile_path).read_bytes()
    profile = json.loads(profile_bytes)
    if profile.get('version') != VERSION or profile.get('notation') != 'explicit_number_ab_list':
        raise ValueError('Unsupported profile')
    max_files = profile.get('max_files',4)
    if not isinstance(max_files,int) or max_files<1:
        raise ValueError('max_files must be positive')
    proofs = validate_proofs(profile,root)
    pages, markers, inventory, skipped = build_map(profile,root)
    by_rank = defaultdict(list)
    for marker in markers:
        if marker['sequence_support']:
            by_rank[marker['rank']].append(marker)
    map_id = key(profile,inventory,markers)
    data = Path(proposals_path).read_bytes()
    records, excluded, source_cache, group_count = [], Counter(), {}, 0
    scope = profile['index_scope']
    for line in data.decode().splitlines():
        proposal = json.loads(line)
        if proposal['volume']!=scope['volume'] or not scope['first']<=proposal['physical']<=scope['last']:
            excluded['outside_profile']+=1
            continue
        path = proposal['source']
        source_path = Path(path).resolve()
        if (not source_path.is_relative_to(root/proposal['volume']/'text')
                or page_number(source_path) != proposal['physical']):
            raise ValueError('Source path does not match declared physical identity')
        if path not in source_cache:
            source_data=Path(path).read_bytes()
            source_cache[path]=(digest(source_data),source_data.decode())
        checksum, raw = source_cache[path]
        if checksum!=proposal['source_sha256']:
            raise ValueError('Reference OCR changed; realign sources before locating')
        check_span(raw,proposal,proposal['entry_raw'])
        for group in proposal['reference_groups']:
            group_count += 1
            bounds = (proposal['char_start'],proposal['char_end'])
            check_span(raw,group,group['raw'],bounds)
            check_span(raw,group['owner_span'],group['owner_text_raw'],bounds)
            base={'source':path, 'source_sha256':checksum,'source_physical':proposal['physical'],
                  'source_volume':proposal['volume'],'proposal_id':proposal['id'],
                  'section_id':proposal['section_id'], 'owner_raw':group['owner_text_raw'],
                  'owner_span':group['owner_span'], 'group_raw':group['raw'],
                  'group_span':[group['char_start'],group['char_end']], 'map_id':map_id,
                  'source_assignment_status':'not_certified','semantic_validation':'not_executed',
                  'verified':False, 'import_ready':False}
            parsed = interpret_group(group['raw'],group['char_start'])
            if parsed is None:
                records.append({**base,'id':key(proposal['id'],group['char_start'],'pending'),
                                'status':'notation_pending','files':[],'reference':None})
                continue
            for token in parsed:
                records.append({**base,'id':key(proposal['id'],token['char_start'],token['char_end']),
                                'reference':token, **locate(token,pages,by_rank,max_files)})
    # Detect concurrent source changes, including pages without usable markers.
    for item in inventory+proofs+[{'path':p,'sha256':v[0]} for p,v in source_cache.items()]:
        if digest(Path(item['path']).read_bytes())!=item['sha256']:
            raise ValueError('Source changed during mapping')
    counts=Counter(r['status'] for r in records)
    if len({r['id'] for r in records})!=len(records):
        raise ValueError('Duplicate input reference occurrence')
    observed_ranks = {m['rank'] for m in markers}
    missing = Counter()
    for record in records:
        if record['status'] not in {'missing_supported_boundary','marker_file_candidate'}:
            continue
        token=record['reference']; marker_rank=rank(token['number'],token['letter_raw'])
        reasons=[]
        for label,k in [('start',marker_rank),('end',marker_rank+1)]:
            reasons.append(label+('_absent' if k not in observed_ranks else
                                  '_supported' if k in by_rank else '_without_sequence_support'))
        missing[' / '.join(reasons)]+=1
    summary={'version':VERSION,'location_policy':LOCATION_POLICY,
             'extent_statuses':dict(Counter(r.get('extent_status','not_parsed') for r in records)),
             'occurrences_with_file_candidates':sum(bool(r['files']) for r in records),'input_groups':group_count,'occurrences_and_pending_groups':len(records),
             'statuses':dict(counts),'excluded_proposals':dict(excluded),'target_files':len(pages),
             'observed_markers':len(markers),'supported_markers':sum(bool(m['sequence_support']) for m in markers),
             'supported_distinct_labels':len(by_rank),
             'explicit_number_letter_occurrences':sum(r['reference'] is not None and r['reference']['number'] is not None for r in records),
             'missing_boundary_reasons':dict(missing),
             'distinct_bounded_labels':len({(r['reference']['number'],r['reference']['letter_raw']) for r in records if r.get('extent_status')=='bounded'}),
             'candidate_file_counts':dict(Counter(len(r['files']) for r in records if r['files'])),
             'cross_volume_candidate_occurrences':sum(any(f['volume']!=r['source_volume'] for f in r['files']) for r in records),
             'source_updates':0,'production_updates':0,'inference_calls':0,'verified_links':0,'import_ready':False}
    manifest={'version':VERSION,'location_policy':LOCATION_POLICY,'profile':profile,'profile_sha256':digest(profile_bytes),
              'input_proposals':str(Path(proposals_path).resolve()),'input_sha256':digest(data),
              'code_sha256':digest(Path(__file__).read_bytes()),
              'sequence_code_sha256':digest(Path(__file__).parents[2].joinpath('scripts/audit_alphabetical_numbering.py').read_bytes()),
              'map_id':map_id,'proofs':proofs,'reference_sources':[{'path':p,'sha256':v[0]} for p,v in source_cache.items()],
              'ignored_blocks':skipped,'limitations':['Sequence is supporting evidence, not semantic validation.',
                'Marker-only candidates identify the marker file; passage continuation remains unknown.',
                'Unmarked continuations, OCR omissions and alternate readings remain pending.',
                'Only explicitly Latin main text is indexed; no header numbers, notes or fuzzy digit repair.',
                'No inference of ownership or bare-letter numerical inheritance.']}
    out.mkdir(parents=True)
    with closing(sqlite3.connect(out/'links.sqlite')) as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''PRAGMA foreign_keys=ON;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
            CREATE TABLE target_files(path TEXT PRIMARY KEY,volume TEXT,physical INTEGER,sha256 TEXT);
            CREATE TABLE markers(id TEXT PRIMARY KEY,path TEXT REFERENCES target_files,number INTEGER,letter TEXT,evidence_json TEXT);
            CREATE TABLE occurrences(id TEXT PRIMARY KEY,source TEXT,owner_raw TEXT,ref_raw TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE candidates(occurrence_id TEXT REFERENCES occurrences,path TEXT REFERENCES target_files,
                PRIMARY KEY(occurrence_id,path));''')
        with con:
            con.executemany('INSERT INTO target_files VALUES(?,?,?,?)',[(p['path'],p['volume'],p['physical'],p['sha256']) for p in pages])
            con.executemany('INSERT INTO markers VALUES(?,?,?,?,?)',[(m['id'],m['path'],m['number'],m['letter_raw'],encoded(m)) for m in markers])
            for record in records:
                con.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?)',(record['id'],record['source'],record['owner_raw'],
                    record['reference']['raw'] if record['reference'] else record['group_raw'],record['status'],encoded(record)))
                con.executemany('INSERT INTO candidates VALUES(?,?)',[(record['id'],p['path']) for p in record['files']])
            con.executemany('INSERT INTO metadata VALUES(?,?)',[('manifest',encoded(manifest)),('summary',encoded(summary))])
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Invalid links')
    for name,items in [('links',records),('markers',markers),('target_inventory',inventory)]:
        (out/(name+'.jsonl')).write_text(''.join(encoded(x)+'\n' for x in items))
    for name,item in [('manifest',manifest),('summary',summary)]:
        (out/(name+'.json')).write_text(json.dumps(item,ensure_ascii=False,indent=2)+'\n')
    eligible=[r for r in records if r['files']]
    sample={'seed':73,'reviewed':False,'records':random.Random(73).sample(eligible,min(20,len(eligible)))}
    (out/'review_sample.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2)+'\n')
    (out/'report.md').write_text('# Localização mecânica experimental\n\n'+
        '\n'.join(f'- {k}: {v}' for k,v in counts.items())+
        '\n\nResultados candidatos, sem certificação semântica nem alteração das fontes. '
        'Consultar links.jsonl/links.sqlite para todos os arquivos retidos e markers.jsonl para evidências.\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposals',type=Path,required=True)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--root',type=Path,default=Path('teste'))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.proposals,args.profile,args.root,args.out),ensure_ascii=False,indent=2))
