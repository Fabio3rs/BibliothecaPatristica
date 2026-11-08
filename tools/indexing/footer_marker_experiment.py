"""Classify numeric witnesses and search notes in isolated, reproducible staging."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import hashlib
import html
import json
import multiprocessing
from pathlib import Path
import random
import re
import sqlite3
import unicodedata

from scripts.audit_alphabetical_numbering import supported_sequence_nodes
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.scripture_comparison import readonly
from tools.ocr_xml_utils import normalize_visible_text, parse_ocr_xml_page
from tools.scripture.citation_index import ScanTask, scan_file_task

VERSION = 'footer-marker-experiment-v2'
NUMBER = re.compile(r'(?<!\w)[1-9][0-9]{0,3}(?!\w)')
NOTE_KINDS = {'rodape', 'nota', 'nota_marginal', 'aparato_critico'}
TOKEN = re.compile(r'[^\W\d_]{4,}', re.UNICODE)
STOP = set('quod quae quem quia quam atque etiam autem sunt esse fuit ejus eius enim '
           'unde inde sive sicut ipsum ipsius inter contra apud hunc hanc hujus huius '
           'haec illud ille illa idem ibid vide confer caput capit opus opera liber'.split())


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def rows(path):
    with path.open() as handle:
        for line in handle:
            yield json.loads(line)


def visible_map(body, offset=0):
    """Decoded NFC visible text and its raw XML character intervals; no geometry."""
    chars, spans = [], []
    for match in re.finditer(r'<[^>]*>|&(?:#\d+|#x[0-9a-fA-F]+|\w+);|\r\n|.', body, re.S):
        raw = match[0]
        if raw.startswith('<'):
            continue
        for char in html.unescape(raw).replace('\r\n', '\n').replace('\r', '\n'):
            char = ' ' if char in '\xa0\u2007\u202f' else char
            chars.append(char)
            spans.append((offset + match.start(), offset + match.end()))
    text, mapped = [], []
    i = 0
    while i < len(chars):
        j = i + 1
        while j < len(chars) and unicodedata.combining(chars[j]):
            j += 1
        normalized = unicodedata.normalize('NFC', ''.join(chars[i:j]))
        text.extend(normalized)
        mapped.extend([(spans[i][0], spans[j-1][1])] * len(normalized))
        i = j
    return ''.join(text), mapped


def citation_spans(raw, groups):
    """Only accept an exact unique block alignment with checked citation text."""
    bs = blocks(raw)
    parsed = parse_ocr_xml_page(raw).blocks
    mapped = [visible_map(b['body'], b['body_start']) for b in bs]
    spans = []
    for group in groups:
        i, a, b = group['block_index'], group['source_start'], group['source_end']
        if not 0 <= i < len(parsed):
            continue
        source = normalize_visible_text(parsed[i].content_raw)
        if not 0 <= a < b <= len(source) or source[a:b] != group['citation_raw']:
            continue
        candidates = [(text, offsets) for text, offsets in mapped if text == source]
        if len(candidates) != 1:
            continue
        text, offsets = candidates[0]
        spans.append((offsets[a][0], offsets[b-1][1], 'scripture_locator', group['group_id']))
        evidence = group['evidence_json']
        evidence = json.loads(evidence) if isinstance(evidence, str) else evidence
        note = evidence.get('note_call', {}).get('raw')
        if note:
            prefix = text[max(0, a-32):a]
            matches = list(re.finditer(re.escape(note), prefix))
            if len(matches) == 1 and not prefix[matches[0].end():].strip():
                start = max(0, a-32) + matches[0].start()
                end = start + len(note)
                spans.append((offsets[start][0], offsets[end-1][1], 'note_call', group['group_id']))
    return spans


def classify(marker, raw, parsed_blocks, scripture):
    a, b = marker['char_start'], marker['char_end']
    if raw[a:b] != marker['raw']:
        return {'role': 'invalid_span', 'eligible': False}
    for start, end, role, group_id in scripture:
        if start <= a < b <= end:
            return {'role': role, 'eligible': False, 'citation_group_id': group_id}
    found = [block for block in parsed_blocks if block['body_start'] <= a < b <= block['body_end']]
    if len(found) != 1:
        return {'role': 'unmapped_block', 'eligible': False}
    block = found[0]
    text, offsets = visible_map(block['body'], block['body_start'])
    indexes = [i for i, (start, end) in enumerate(offsets) if start <= a and b <= end]
    # Multi-digit numbers occupy several visible characters.
    indexes = indexes or [i for i, (start, end) in enumerate(offsets) if start == a]
    if not indexes:
        return {'role': 'unmapped_token', 'eligible': False}
    pos = indexes[0]
    # Keep original offsets/text; only mask an exact digitization watermark for classification.
    classification_text = re.sub(r'Digitized\s+by\s+Google', lambda m: ' '*len(m[0]), text, flags=re.I)
    pure = bool(re.fullmatch(r'[\s\d\[\]()·.,–—-]+', classification_text))
    if pure:
        bracketed = any(m.start() <= pos < m.end() for m in re.finditer(r'\[\s*\d+\s*\]', text))
        return {'role': 'isolated_numeric_candidate', 'eligible': True,
                'channel': 'bracketed' if bracketed else 'plain', 'namespace_verified': False,
                'watermark_masked': classification_text != text}
    if block['kind'] == 'cabecalho':
        pair = re.fullmatch(r'\s*(\d+)\s+.+?\s+(\d+)\s*', text, re.S)
        if pair and int(pair[2]) == int(pair[1])+1 and (pos == pair.start(1) or pos == pair.start(2)):
            return {'role': 'header_candidate', 'eligible': True, 'channel': 'plain',
                    'namespace_verified': False, 'basis': 'consecutive_outer_header_pair'}
        for annotation in re.finditer(r'\[[^\[\]]*(?:\]|$)', text):
            if annotation.start() <= pos < annotation.end():
                if re.search(r'\b(?:A\.\s*[CM]\.|IND\.|IMP\.)', annotation[0], re.I):
                    return {'role': 'header_chronology', 'eligible': False}
                if not annotation[0].endswith(']'):
                    return {'role': 'unclosed_header_annotation', 'eligible': False}
    if block['kind'] in NOTE_KINDS:
        apparatus = re.search(r'VARIANTES\s+LECTIONES', text, re.I)
        if apparatus and pos >= apparatus.end():
            return {'role': 'apparatus_number', 'eligible': False}
        line = text[text.rfind('\n', 0, pos)+1:]
        call = re.match(r'\s*\(?\d+\)?[.)]?\s+(?=[^\W\d_])', line)
        if call and pos < text.rfind('\n', 0, pos)+1 + call.end():
            return {'role': 'note_call_pattern', 'eligible': False}
    if block['kind'] == 'cabecalho':
        bracketed = bool(re.search(r'\[[^\]]*$', text[:pos]))
        return {'role': 'isolated_numeric_candidate' if pure else 'header_candidate',
                'eligible': True, 'channel': 'bracketed' if bracketed else 'plain',
                'namespace_verified': False}
    return {'role': 'note_content_unclassified', 'eligible': False}


def refresh_markers(raw, inventory, checksum):
    """Rebuild only numeric header/footer witnesses, with current source offsets."""
    result = []
    for block in blocks(raw):
        if block['kind'] not in {'cabecalho', 'rodape'}:
            continue
        for match in NUMBER.finditer(block['body']):
            if block['body'][:match.start()].rfind('<') > block['body'][:match.start()].rfind('>'):
                continue
            a = block['body_start'] + match.start()
            b = a + len(match[0])
            result.append({'path': inventory['path'], 'physical': inventory['physical'],
                'sha256': checksum, 'number': int(match[0]), 'raw': match[0], 'char_start': a,
                'char_end': b, 'zone': block['kind'], 'sequence_support': None,
                'id': hashlib.sha256(f'{inventory["path"]}:{checksum}:{a}:{b}'.encode()).hexdigest(),
                'excerpt': raw[max(block['body_start'], a-70):min(block['body_end'], b+70)],
                'refreshed_from_ocr': True})
    return result


def refresh_citations(path, volume, checksum):
    result = scan_file_task(ScanTask(volume_id=volume, collection=volume[:2],
        source_root=str(Path(path).parent), file_path=path, physical_index=0,
        is_index_source=False, observed_aliases=(), estimator_pages=(), profile_fingerprint=VERSION))
    if result['file_sha256'] != checksum:
        raise ValueError(f'OCR changed during refresh: {path}')
    return [{**g, 'group_id': g['group_key'], 'file_path': path, 'file_sha256': checksum,
             'detector_version': result['detector_version']} for g in result['groups']]


def tokens(text):
    text = ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if not unicodedata.combining(c))
    return [t for t in dict.fromkeys(TOKEN.findall(text)) if t not in STOP]


def make_notes_db(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE notes(id INTEGER PRIMARY KEY, volume TEXT, path TEXT,
        sha256 TEXT, physical INTEGER, char_start INTEGER, char_end INTEGER, kind TEXT,
        raw_xml TEXT, visible_text TEXT);
        CREATE VIRTUAL TABLE note_search USING fts5(text, tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE queries(group_id TEXT PRIMARY KEY,evidence_json TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT);''')
    return db


def matching_snippet(text, terms, length=700):
    hits = [(m.start(), m.end(), tokens(m[0])[0]) for m in TOKEN.finditer(text)
            if tokens(m[0]) and tokens(m[0])[0] in terms]
    starts = {0} | {max(0, a-100) for a, _, _ in hits}
    start = min(starts, key=lambda a: (-len({t for x, y, t in hits if a <= x and y <= a+length}), a))
    return {'snippet': text[start:start+length], 'snippet_start': start,
            'snippet_end': min(len(text), start+length), 'snippet_coordinates': 'decoded_visible_block'}


def search_notes(db, query, excluded, top_k):
    terms = tokens(query)[:16]
    if not terms:
        return []
    match = ' OR '.join('"' + term + '"' for term in terms)
    results, seen = [], set()
    # Stream in rank order, excluding sources before truncating unique physical files.
    for row in db.execute('''SELECT n.*,bm25(note_search) AS score FROM note_search
            JOIN notes n ON n.id=note_search.rowid WHERE note_search MATCH ?
            ORDER BY score,n.path,n.char_start''', (match,)):
        r = dict(row)
        if r['path'] in excluded or r['path'] in seen:
            continue
        seen.add(r['path'])
        common = sorted(set(tokens(r['visible_text'])) & set(terms))
        r.update(matching_snippet(r.pop('visible_text'), set(terms)))
        r.update(matched_terms=common, verified=False,
                 evidence_kind='lexical_note_overlap')
        r.pop('raw_xml')
        results.append(r)
        if len(results) == top_k:
            break
    return results


def run_volume(job):
    config, volume = job
    survey = Path(config['survey'])/'volumes'/volume
    out = Path(config['out'])/volume
    out.mkdir()
    dump(out/'input_hashes.json', {name: hashlib.sha256((survey/(name+'.jsonl')).read_bytes()).hexdigest()
        for name in ['inventory', 'header_markers', 'groups', 'occurrences']})
    counts = Counter()
    markers_by_path = defaultdict(list)
    for marker in rows(survey/'header_markers.jsonl'):
        markers_by_path[marker['path']].append(marker)
    groups = list(rows(survey/'groups.jsonl'))
    source_paths = {g['source'] for g in groups}
    citations = defaultdict(list)
    with closing(readonly(config['citation_db'])) as citation_db:
        for row in citation_db.execute('''SELECT g.*,f.file_path,f.file_sha256
            FROM citation_files f JOIN citation_groups g USING(file_id) WHERE f.volume_id=?''', (volume,)):
            citations[row['file_path']].append(dict(row))
    classified, channels = [], defaultdict(lambda: defaultdict(set))
    current_hashes = {}
    with closing(make_notes_db(out/'notes.sqlite')) as db, (out/'refreshed_sources.jsonl').open('w') as refreshed_evidence:
        db.row_factory = sqlite3.Row
        for inventory in rows(survey/'inventory.jsonl'):
            path = inventory['path']
            try:
                data = Path(path).read_bytes()
                checksum = hashlib.sha256(data).hexdigest()
                raw = data.decode('utf-8')
            except (OSError, UnicodeError):
                counts['unreadable_sources'] += 1
                raw, checksum = '', None
            current_hashes[path] = checksum
            snapshot_current = bool(checksum and checksum == inventory.get('sha256'))
            counts['current_files' if snapshot_current else 'changed_or_missing_files'] += 1
            refreshed = bool(checksum and not snapshot_current and config.get('refresh_sources'))
            current = snapshot_current or refreshed
            bs = blocks(raw) if current else []
            valid_citations = [r for r in citations[path] if checksum == r['file_sha256']]
            counts['stale_citation_groups'] += len(citations[path]) - len(valid_citations)
            refresh_bible = current and config.get('refresh_sources') and (
                refreshed or len(valid_citations) != len(citations[path]))
            if refresh_bible:
                valid_citations = refresh_citations(path, volume, checksum)
                counts['citation_files_refreshed'] += 1
            page_markers = markers_by_path[path]
            if refreshed:
                page_markers = refresh_markers(raw, inventory, checksum)
                counts['ocr_files_refreshed'] += 1
            if refreshed or refresh_bible:
                refreshed_evidence.write(json.dumps({'path': path, 'snapshot_sha256': inventory.get('sha256'),
                    'current_sha256': checksum, 'markers_refreshed': refreshed,
                    'citations_refreshed': refresh_bible, 'citation_groups': valid_citations,
                    'index_entries_reextracted': False}, ensure_ascii=False)+'\n')
            spans = citation_spans(raw, valid_citations) if current else []
            counts['aligned_citation_and_call_spans'] += len(spans)
            for marker in page_markers:
                decision = (classify(marker, raw, bs, spans) if current and checksum == marker['sha256']
                            else {'role': 'stale_source', 'eligible': False})
                item = {**marker, **decision}
                classified.append(item)
                counts['role:'+item['role']] += 1
                if item['eligible']:
                    channels[item['channel']][item['number']].add(item['physical'])
            for block in bs:
                if block['kind'] not in NOTE_KINDS:
                    continue
                text, _ = visible_map(block['body'])
                if not tokens(text):
                    continue
                cur = db.execute('INSERT INTO notes VALUES(NULL,?,?,?,?,?,?,?,?,?)',
                    (volume, path, checksum, inventory['physical'], block['body_start'], block['body_end'],
                     block['kind'], block['body'], text))
                db.execute('INSERT INTO note_search(rowid,text) VALUES(?,?)', (cur.lastrowid, text))
                counts['note_blocks'] += 1
        support = {channel: supported_sequence_nodes(nodes) for channel, nodes in channels.items()}
        by_number = defaultdict(set)
        pair_roles = defaultdict(set)
        with (out/'markers.jsonl').open('w') as handle:
            for marker in classified:
                marker['clean_sequence_support'] = support.get(marker.get('channel'), {}).get(
                    (marker['number'], marker['physical'])) if marker['eligible'] else None
                if marker['clean_sequence_support']:
                    by_number[marker['number']].add(marker['path'])
                    counts['clean_supported_markers'] += 1
                pair_roles[(marker['number'], marker['path'])].add(marker['role'])
                handle.write(json.dumps(marker, ensure_ascii=False)+'\n')
        unmatched = set()
        with (out/'candidate_diff.jsonl').open('w') as handle:
            for occurrence in rows(survey/'occurrences.jsonl'):
                if occurrence['tier'] != 'unmapped_namespace_screen':
                    continue
                old = {f['path'] for f in occurrence.get('files', [])}
                new = by_number[occurrence['number']] - source_paths - {occurrence['source']}
                source_current = current_hashes.get(occurrence['source']) == occurrence['source_sha256']
                counts['numeric_occurrences'] += 1
                if not source_current:
                    counts['stale_index_occurrences'] += 1
                elif not old:
                    unmatched.add(occurrence['group_id'])
                counts['candidate_edges_removed'] += len(old-new)
                counts['candidate_edges_added'] += len(new-old)
                counts['occurrences_changed'] += old != new
                counts['previously_nonempty_now_empty'] += bool(old) and not new
                counts['previously_unique_now_empty'] += len(old) == 1 and not new
                counts['candidate_sets_unchanged'] += old == new
                removed_evidence = []
                for path in sorted(old-new):
                    roles = pair_roles[(occurrence['number'], path)]
                    reason = ('sequence_support_lost' if roles & {'header_candidate', 'isolated_numeric_candidate'}
                              else 'nonpagination_evidence' if roles and roles <= {
                                  'note_call', 'note_call_pattern', 'scripture_locator', 'apparatus_number',
                                  'header_chronology'}
                              else 'uncertain_or_stale_evidence')
                    counts['removed:'+reason] += 1
                    removed_evidence.append({'path': path, 'roles': sorted(roles), 'reason': reason})
                handle.write(json.dumps({'occurrence_id': occurrence['id'], 'group_id': occurrence['group_id'],
                    'number': occurrence['number'], 'old': sorted(old), 'proposed': sorted(new),
                    'removed': sorted(old-new), 'added': sorted(new-old), 'source_current': source_current,
                    'removed_evidence': removed_evidence,
                    'verified': False, 'namespace_status': 'unmapped',
                    'removed_meaning': 'ineligible, uncertain, stale or lost sequence support; not certified false'},
                    ensure_ascii=False)+'\n')
        eligible = sorted([g for g in groups if g['id'] in unmatched], key=lambda g: g['id'])
        counts['unmatched_groups_available'] = len(eligible)
        selected = random.Random(config['seed']).sample(eligible, min(config['query_limit'], len(eligible)))
        with (out/'note_matches.jsonl').open('w') as handle:
            for group in selected:
                matches = search_notes(db, group['owner_raw'], source_paths, config['top_k'])
                result = {'group': group, 'query_terms': tokens(group['owner_raw'])[:16], 'candidates': matches,
                    'gold_available': False, 'verified': False, 'automatic_resolution': False}
                db.execute('INSERT INTO queries VALUES(?,?)', (group['id'], json.dumps(result, ensure_ascii=False)))
                handle.write(json.dumps(result, ensure_ascii=False)+'\n')
                counts['note_queries'] += 1
                counts['note_queries_with_matches'] += bool(matches)
                counts['note_queries_with_two_term_match'] += any(len(c['matched_terms']) >= 2 for c in matches)
        result = {'volume': volume, 'counts': dict(counts), 'source_updates': 0, 'verified_links': 0}
        db.execute('INSERT INTO metadata VALUES(?,?)', ('config', json.dumps(config)))
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', json.dumps(result)))
        db.commit()
    dump(out/'summary.json', result)
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--survey', type=Path, default=Path('data/index_entry_parser/global_targets_001'))
    parser.add_argument('--citation-db', type=Path, default=Path('data/scripture_citations.db'))
    parser.add_argument('--volume', action='append', required=True)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--query-limit', type=int, default=100)
    parser.add_argument('--top-k', type=int, default=20)
    parser.add_argument('--seed', type=int, default=73)
    parser.add_argument('--refresh-sources', action='store_true',
                        help='Rebuild markers/notes in changed OCR and refresh stale citations in staging; index entries remain frozen')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.workers < 1 or args.query_limit < 0 or args.top_k < 1:
        parser.error('workers/top-k must be positive; query-limit must be nonnegative')
    volumes = sorted(set(args.volume))
    if any(not re.fullmatch(r'(?:PG|PL|PO)\d+[A-Za-z0-9_-]*', v) for v in volumes):
        parser.error('Invalid volume identifier')
    for volume in volumes:
        for name in ['inventory', 'header_markers', 'groups', 'occurrences']:
            if not (args.survey/'volumes'/volume/(name+'.jsonl')).is_file():
                parser.error(f'Missing survey input: {volume}/{name}')
    args.out.mkdir(parents=True, exist_ok=False)
    config = {k: str(v.resolve()) if isinstance(v, Path) else v for k, v in vars(args).items()}
    config['version'] = VERSION
    config['implementation_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    dump(args.out/'config.json', config)
    jobs = [(config, volume) for volume in volumes]
    if args.workers == 1:
        results = [run_volume(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            results = list(pool.map(run_volume, jobs))
    total = Counter()
    for result in results:
        total.update(result['counts'])
    summary = {'version': VERSION, 'volumes': volumes, 'counts': dict(total),
        'verified_links': 0, 'production_updates': 0, 'inference_calls': 0}
    dump(args.out/'summary.json', summary)
    (args.out/'report.md').write_text('# Marcadores e recuperação em notas\n\n'
        'Experimento lexical, sem certificação de destinos. As retiradas incluem abstenções '
        'e perda de apoio de sequência; não são uma contagem de erros comprovados.\n\n'
        'Cada volume contém markers.jsonl, candidate_diff.jsonl, note_matches.jsonl e notes.sqlite (WAL). '
        'Consultas usam apenas grupos sem candidatos no snapshot anterior, com fonte atual. '
        'Com --refresh-sources, marcadores/notas alterados e citações desatualizadas são reconstruídos '
        'em staging; os verbetes de índice não são reextraídos por este script. '
        'Origens cadastradas no experimento são excluídas da busca; a descoberta pode estar incompleta.\n\n'
        '```json\n'+json.dumps(summary, ensure_ascii=False, indent=2)+'\n```\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
