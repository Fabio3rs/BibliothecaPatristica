"""Recover searchable index fragments from historical OCR; never edit sources."""
from collections import Counter
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sqlite3

from scripts.audit_po015_names_lexical import history_page_identity
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.entry_parser_literal_rows import reference_groups
from tools.indexing.ocr_variant_audit import tokens
from tools.indexing.source_recovery_evidence import find_anchor

VERSION = 'historical-index-source-candidates-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def prepare(raw):
    """Mask markup and non-body regions, preserving raw character offsets."""
    regions = [b for b in blocks(raw) if b['kind'] == 'texto_principal']
    if not blocks(raw) and not re.search(r'<|\[(?:/?pagina|/?bloco|/?texto_principal)', raw, re.I):
        regions = [{'body_start': 0, 'body_end': len(raw)}]
    masked = [' '] * len(raw)
    for region in regions:
        a, b = region['body_start'], region['body_end']
        masked[a:b] = re.sub(r'<[^>]*>|\[(?:/?span|br/)[^\]]*\]',
                             lambda m: ' ' * len(m[0]), raw[a:b])
    return ''.join(masked), regions


def normal(text):
    return ' '.join(t['token'] for t in tokens(text))


def span(raw, a, b):
    return {'span': [a, b], 'quote': raw[a:b], 'sha256': digest(raw[a:b].encode())}


def align(current, alternative):
    left, right = tokens(current), tokens(alternative)
    matcher = SequenceMatcher(None, [t['token'] for t in left],
                              [t['token'] for t in right], autojunk=False)
    # Short accidental matches (e.g. "I, 1") split an omitted paragraph into
    # many edits. Only longer equal runs delimit a recoverable region.
    ops = []
    for op, a, b, c, d in matcher.get_opcodes():
        if op == 'equal' and b-a < 4:
            op = 'replace'
        if op != 'equal' and ops and ops[-1][0] != 'equal':
            prev = ops.pop()
            ops.append(('replace', prev[1], b, prev[3], d))
        else:
            ops.append((op, a, b, c, d))
    changes = []
    def bounds(items, a, b, size):
        start = items[a]['span'][0] if a < len(items) else size
        end = items[b-1]['span'][1] if a < b else start
        return [start, end]
    for i, (op, a, b, c, d) in enumerate(ops):
        if op == 'equal':
            continue
        before = ops[i-1] if i else None
        after = ops[i+1] if i+1 < len(ops) else None
        flanks = []
        for neighbor in (before, after):
            if neighbor and neighbor[0] == 'equal' and neighbor[2]-neighbor[1] >= 4:
                _, x, y, u, v = neighbor
                # Keep small local anchors, not entire pages of equal text.
                if neighbor is before:
                    x, u = max(x, y-8), max(u, v-8)
                else:
                    y, v = min(y, x+8), min(v, u+8)
                needle = [t['token'] for t in left[x:y]]
                def occurrences(items):
                    values = [t['token'] for t in items]
                    return sum(values[j:j+len(needle)] == needle
                               for j in range(len(values)-len(needle)+1))
                flanks.append({'current_span': bounds(left, x, y, len(current)),
                               'alternative_span': bounds(right, u, v, len(alternative)),
                               'unique_in_both': occurrences(left) == occurrences(right) == 1})
        changes.append({'operation': op, 'current_span': bounds(left, a, b, len(current)),
                        'alternative_span': bounds(right, c, d, len(alternative)),
                        'flanks': flanks, 'bounded_by_text': len(flanks) == 2 and
                        all(f['unique_in_both'] for f in flanks)})
    return {'token_similarity': matcher.ratio(), 'changes': changes,
            'status': 'text_alignment_candidate_not_image_identity'}


def clauses(raw, masked, regions):
    """Use the existing analytic locator grammar; preserve uncertain ownership."""
    result = []
    for block, region in enumerate(regions):
        a, b = region['body_start'], region['body_end']
        cursor = a
        for x, y in reference_groups(masked[a:b], 'analytic-tome-rows-v1'):
            x, y = a+x, a+y
            # A Latin preposition beginning the next clause is not subdivision A.
            tail = re.search(r'\b[A-D]\s*$', masked[x:y])
            if tail and re.match(r'[a-zæœ]', masked[y:b].lstrip()):
                y = x + tail.start()
                while y > x and masked[y-1].isspace():
                    y -= 1
            start = cursor
            while start < x and masked[start].isspace():
                start += 1
            end = x
            while end > start and masked[end-1] in ' ,.;\n\t':
                end -= 1
            if any(c.isalpha() for c in masked[start:end]):
                result.append({'block': block, 'owner': span(raw, start, end),
                               'reference': span(raw, x, y), 'entry': span(raw, start, y)})
            cursor = y
    return result


def recover(current_raw, alternative_raw, issues):
    current, current_regions = prepare(current_raw)
    alternative, regions = prepare(alternative_raw)
    alignment = align(current, alternative)
    units = clauses(alternative_raw, alternative, regions)
    proposals, comparisons = {}, []

    def emit(unit, start, issue, anchor, origin, change=None):
        end = unit['reference']['span'][1]
        owner_end = unit['owner']['span'][1]
        if start >= owner_end or end-start > 800:
            return
        text = alternative[start:end]
        if find_anchor(current, text):
            return
        key = tuple(unit['reference']['span'])
        previous = [u for u in units if u['block'] == unit['block'] and
                    u['entry']['span'][1] <= start]
        following = [u for u in units if u['block'] == unit['block'] and
                     u['entry']['span'][0] >= end][:2]
        if key not in proposals:
            proposals[key] = {'kind': 'entry_fragment_candidate',
                'entry': span(alternative_raw, start, end),
                'owner': span(alternative_raw, start, owner_end),
                'reference': unit['reference'], 'origins': [], 'issue_ids': [], 'anchors': [],
                'current_anchor_evidence': [],
                'alignment_change': change,
                'context': {'previous': previous[-1:] , 'following': following,
                            'status': 'adjacent_clauses_not_proven_same_entry'},
                'quality': {'segmentation': 'anchor_or_clause_candidate',
                            'reference_attribution': 'literal_clause_candidate',
                            'edition_mapping': 'not_attempted', 'destination': 'not_attempted'},
                'warnings': (['numeric_text_in_owner'] if re.search(r'\d', alternative[start:owner_end]) else []),
                'automatic_repair_allowed': False}
        item = proposals[key]
        if change and item['alignment_change'] is None:
            item['alignment_change'] = change
        if origin != 'aligned_missing_region' and start > item['entry']['span'][0]:
            item['entry'] = span(alternative_raw, start, end)
            item['owner'] = span(alternative_raw, start, owner_end)
        owner_text = item['owner']['quote']
        if re.search(r'\b(?:INDEX|PATROL|ORDO RERUM)\b', owner_text):
            item['warnings'].append('editorial_heading_in_owner')
        if owner_text and owner_text[0].islower():
            item['warnings'].append('possible_continuation')
        if re.search(r'\n\s*\n', owner_text):
            item['warnings'].append('multiple_ocr_paragraphs_in_owner')
        if unit['reference']['quote'].rstrip().endswith(','):
            item['warnings'].append('possibly_incomplete_locator')
        item['warnings'] = sorted(set(item['warnings']))
        for field, value in [('issue_ids', issue['id']), ('anchors', anchor), ('origins', origin)]:
            if value not in item[field]:
                item[field].append(value)
        for hit in find_anchor(current, anchor):
            proof = span(current_raw, *hit['context_span'])
            if proof not in item['current_anchor_evidence']:
                item['current_anchor_evidence'].append(proof)

    for issue in issues:
        for anchor in issue.get('search_anchors', []):
            old, new = find_anchor(current, anchor), find_anchor(alternative, anchor)
            comparisons.append({'issue_id': issue['id'], 'anchor': anchor,
                                'current_count': len(old), 'alternative_count': len(new),
                                'current_searchable': bool(current_regions)})
            if issue['kind'] == 'duplicated_transcription':
                if 0 < len(new) < len(old):
                    proposals[('duplicate', issue['id'], anchor)] = {
                        'kind': 'duplication_candidate', 'issue_ids': [issue['id']], 'anchors': [anchor],
                        'current_occurrences': [span(current_raw, *h['span']) for h in old],
                        'alternative_occurrences': [span(alternative_raw, *h['span']) for h in new],
                        'automatic_repair_allowed': False}
                continue
            for hit in new:
                a, b = hit['span']
                for unit in units:
                    x, y = unit['owner']['span']
                    if x <= a < b <= y:
                        emit(unit, a, issue, anchor,
                             ('current_not_searchable' if not current_regions else
                              'missing_anchor' if not old else 'alternative_reading'))
                if old or issue['kind'] != 'missing_printed_entries':
                    continue
                for change in alignment['changes']:
                    x, y = change['alternative_span']
                    if not (change['bounded_by_text'] and x <= a < b <= y):
                        continue
                    for unit in units:
                        u, v = unit['entry']['span']
                        # Token alignment omits final punctuation from the changed span.
                        if x <= u and v <= y+2:
                            emit(unit, u, issue, anchor, 'aligned_missing_region', change)
    return list(proposals.values()), comparisons, alignment


def run_versions(registry, versions_db, out, root, volumes=()):
    registry, versions_db, out, root = map(Path, (registry, versions_db, out, root))
    if out.resolve() == (root/'teste').resolve() or (root/'teste').resolve() in out.resolve().parents:
        raise ValueError('Output must be outside the OCR corpus')
    data = json.loads(registry.read_text())
    selected = [p for p in data['pages'] if not volumes or p['volume'] in volumes]
    out.mkdir(parents=True, exist_ok=False)
    (out/'sources').mkdir()
    pages, readings, proposals, comparisons = [], [], [], []
    inputs = readonly(versions_db)
    try:
        inputs.execute('BEGIN')
        for page in selected:
            page_key = f"{page['volume']}:{page['physical']}"
            source = root / page['source']
            current = source.read_bytes().decode('utf-8')
            image = root / page['image']
            local_image_hash = digest(image.read_bytes()) if image.is_file() else None
            current_snapshot = out/'sources'/f'{page_key.replace(":", "-")}-current.txt'
            current_snapshot.write_text(current)
            pages.append({'page_key': page_key, 'source': str(source.resolve()),
                          'source_sha256': digest(current.encode()),
                          'current_searchable': bool(prepare(current)[1]),
                          'source_changed_since_issue_review': digest(current.encode()) != page.get('source_sha256'),
                          'image_sha256': local_image_hash, 'issues': page['issues'],
                          'current_snapshot': str(current_snapshot.resolve())})
            for row in inputs.execute('''SELECT r.id,r.text_content,r.text_hash,r.image_hash,
                    r.is_current,e.engine,e.model FROM ocr_results r
                    JOIN ocr_engine_versions e ON e.id=r.engine_version_id
                    WHERE r.volume_id=? AND r.page_num=? ORDER BY r.id''',
                    (page['volume'], page['physical'])):
                reading = dict(row)
                raw = reading.pop('text_content')
                reading.update(page_key=page_key, computed_sha256=digest(raw.encode()),
                    page_identity=history_page_identity(page['volume'], page['physical'],
                                                       reading['image_hash'], local_image_hash))
                reading['text_hash_matches_record'] = reading['computed_sha256'] == reading['text_hash']
                snapshot = out/'sources'/f'version-{reading["id"]}.txt'
                snapshot.write_text(raw)
                reading['snapshot'] = str(snapshot.resolve())
                readings.append(reading)
                if reading['page_identity'].startswith('excluded_'):
                    reading['status'] = 'excluded_structural_history'
                    continue
                if raw == current:
                    reading['status'] = 'identical_to_current'
                    continue
                if not prepare(raw)[1]:
                    reading['status'] = 'unsupported_or_no_body'
                    continue
                reading['status'] = 'compared'
                found, checks, alignment = recover(current, raw, page['issues'])
                reading['alignment'] = alignment
                for item in found:
                    item.update(page_key=page_key, version_id=reading['id'],
                                page_identity=reading['page_identity'])
                    item['id'] = digest(encoded(item).encode())[:24]
                    proposals.append(item)
                comparisons.extend(dict(c, page_key=page_key, version_id=reading['id']) for c in checks)
    finally:
        inputs.close()
    db = sqlite3.connect(out/'candidates.sqlite')
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE pages(page_key TEXT PRIMARY KEY,data_json TEXT NOT NULL);
            CREATE TABLE readings(version_id INTEGER PRIMARY KEY,page_key TEXT NOT NULL REFERENCES pages(page_key),data_json TEXT NOT NULL);
            CREATE TABLE proposals(id TEXT PRIMARY KEY,page_key TEXT NOT NULL REFERENCES pages(page_key),version_id INTEGER NOT NULL REFERENCES readings(version_id),
                kind TEXT NOT NULL,owner_raw TEXT,reference_raw TEXT,data_json TEXT NOT NULL);
            CREATE VIRTUAL TABLE proposal_fts USING fts5(id UNINDEXED, text, tokenize='unicode61');''')
        db.executemany('INSERT INTO pages VALUES(?,?)', [(p['page_key'], encoded(p)) for p in pages])
        db.executemany('INSERT INTO readings VALUES(?,?,?)', [(r['id'], r['page_key'], encoded(r)) for r in readings])
        for p in proposals:
            db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?)',
                       (p['id'], p['page_key'], p['version_id'], p['kind'],
                        p.get('owner', {}).get('quote'), p.get('reference', {}).get('quote'), encoded(p)))
            text = p.get('entry', {}).get('quote', ' '.join(p['anchors']))
            db.execute('INSERT INTO proposal_fts VALUES(?,?)', (p['id'], normal(text)))
        missing = sorted({(c['page_key'], normal(c['anchor'])) for c in comparisons
                          if c['current_searchable'] and not c['current_count'] and c['alternative_count']})
        summary = {'version': VERSION, 'pages': len(pages), 'readings': len(readings),
            'reading_statuses': dict(Counter(r['status'] for r in readings)),
            'page_identity_counts': dict(Counter(r['page_identity'] for r in readings)),
            'proposal_rows': len(proposals), 'proposal_kinds': dict(Counter(p['kind'] for p in proposals)),
            'by_page': {page['page_key']: dict(Counter(p['kind'] for p in proposals
                        if p['page_key'] == page['page_key'])) for page in pages},
            'missing_anchor_labels_found': missing,
            'interpretation': 'Rows are versioned hypotheses, not unique printed entries or resolved destinations.',
            'canonical_db_updates': 0, 'ocr_updates': 0, 'inference_calls': 0,
            'global_catalog_updates': 0}
        manifest = {'version': VERSION, 'registry': str(registry.resolve()),
                    'registry_sha256': digest(registry.read_bytes()), 'versions_db': str(versions_db.resolve()),
                    'code_sha256': digest(Path(__file__).read_bytes()), 'volumes': list(volumes)}
        dependency_paths = [Path(__file__).with_name(name) for name in
            ('ocr_variant_audit.py', 'entry_parser_literal_rows.py', 'editorial_ocr_blocks.py',
             'source_recovery_evidence.py')]
        dependency_paths.append(Path(__file__).resolve().parents[2]/'scripts/audit_po015_names_lexical.py')
        manifest['dependencies_sha256'] = {str(p): digest(p.read_bytes()) for p in dependency_paths}
        db.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', encoded(manifest)))
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
        db.commit()
        assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    finally:
        db.close()
    for name, value in [('summary', summary), ('manifest', manifest), ('comparisons', comparisons)]:
        (out/f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    for name, rows in [('pages', pages), ('readings', readings), ('proposals', proposals)]:
        (out/f'{name}.jsonl').write_text(''.join(encoded(row)+'\n' for row in rows))
    return summary


def search(db_path, query, limit=20):
    words = normal(query).split()
    if not words or not 1 <= limit <= 1000:
        raise ValueError('Use a nonempty query and limit between 1 and 1000')
    con = readonly(db_path)
    try:
        return [json.loads(r['data_json']) for r in con.execute('''SELECT p.data_json
            FROM proposal_fts f JOIN proposals p ON p.id=f.id
            WHERE proposal_fts MATCH ? ORDER BY bm25(proposal_fts),p.id LIMIT ?''',
            (' AND '.join('"'+w+'"' for w in words), limit))]
    finally:
        con.close()
