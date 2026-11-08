"""Offline literal candidates for subdivisions, paired cells, remissions and dependencies."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import re
import random
import sqlite3

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import digest, source_span
from tools.indexing.structured_index_experiment import encoded, key, readonly, check_span

VERSION = 'subdivision-clause-recovery-v2'
# Require punctuation after the subdivision: "17 a Phoca" is not a locator.
PAIR = r'(?:[IVXLCDM]+[,.]\s*)?\d{1,4}\s+[ab](?!\w)'
ITEM = rf'(?:{PAIR}|[—–]\s*[ab](?!\w)|[ab](?!\w))'
LOCATOR = re.compile(rf'(?<![\w\d]){PAIR}(?:\s*[,;–—-]\s*{ITEM})*(?:\.(?!\w)|(?=\s*[,;:]|\s*$))')
CROSSREF = re.compile(r'\b(?:ibid|ibidem|cf|vide)\b|\b(?:ib|v)\.', re.I)
SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE pages(source TEXT PRIMARY KEY, volume TEXT, physical INTEGER, source_sha256 TEXT,
 status TEXT NOT NULL, evidence_json TEXT NOT NULL);
CREATE TABLE clauses(id TEXT PRIMARY KEY,source TEXT NOT NULL REFERENCES pages,
 origin_kind TEXT NOT NULL, origin_id TEXT NOT NULL, owner_raw TEXT NOT NULL,
 reference_raw TEXT NOT NULL, char_start INTEGER NOT NULL, char_end INTEGER NOT NULL,
 ref_start INTEGER NOT NULL, ref_end INTEGER NOT NULL, evidence_json TEXT NOT NULL,
 UNIQUE(source,ref_start,ref_end));
CREATE TABLE exclusions(id INTEGER PRIMARY KEY,source TEXT REFERENCES pages,evidence_json TEXT NOT NULL);
CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE VIRTUAL TABLE clause_search USING fts5(id UNINDEXED,owner_raw,reference_raw);
'''


def candidates(raw, start, end):
    """Return partial clause claims, keeping rejected ranges as explicit residues.

    Cursor advances even after a rejected owner. No numerical inheritance or
    lemma hierarchy is reconstructed, and no cross-block continuity is asserted.
    """
    text = raw[start:end]
    proposals, rejected = [], []
    cursor = 0
    for match in LOCATOR.finditer(text):
        x, y = match.span()
        left = cursor
        while left < x and text[left].isspace():
            left += 1
        right = x
        while right > left and text[right-1] in ' ,.;:\t\n':
            right -= 1
        owner = text[left:right]
        reason = None
        if re.match(r'\s*[,;]\s*(?:\d|[ab]\b|—|–|not\b|cf\b|ibid\b|sqq?\b|et\b)', text[y:], re.I):
            reason = 'incomplete_locator_list_or_qualifier'
        elif not owner or sum(c.isalpha() for c in owner) < 2:
            reason = 'empty_or_numeric_owner'
        elif '<' in owner or '>' in owner:
            reason = 'markup_in_owner'
        elif re.search(r'\d', owner):
            reason = 'unassigned_number_in_owner'
        elif CROSSREF.search(owner):
            reason = 'cross_reference_or_inheritance_context'
        elif re.search(r'\b(?:cod|cap|lib|tom|vers|col)\.?\s*$', owner, re.I):
            reason = 'different_locator_namespace'
        elif re.search(r'\b[ivxlcdm]+\s*$', owner):
            reason = 'possible_lowercase_roman_namespace_in_owner'
        elif re.search(r'[,;]\s*[IVXLCDM]+[,.](?:\s|$)', owner):
            reason = 'interrupted_roman_locator_in_owner'
        elif owner.startswith(('INDEX', 'ORDO RERUM')):
            reason = 'editorial_boundary'
        elif not owner[0].isupper():
            reason = 'leading_continuation_or_lowercase_context'
        elif '\n\n' in owner or re.search(r'\n\s*\n', owner):
            reason = 'paragraph_boundary_in_owner'
        elif re.search(r'\b[ab]\s*[,.;]', owner):
            reason = 'unresolved_bare_subdivision_in_owner'
        if reason:
            rejected.append({'span': [start+left, start+y], 'reason': reason,
                             'literal': raw[start+left:start+y]})
        else:
            group = {'raw': text[x:y], **source_span(raw, start+x, start+y),
                     'owner_text_raw': owner, 'owner_span': source_span(raw, start+left, start+right),
                     'namespace_status': 'literal_uninterpreted', 'inheritance_status': 'not_inferred'}
            proposals.append({'entry_raw': raw[start+left:start+y],
                **source_span(raw, start+left, start+y), 'reference_groups': [group],
                'quality': {'segmentation': 'mechanical_clause_candidate',
                    'lemma_context': 'unresolved', 'region': 'candidate',
                    'source_fidelity': 'not_visually_verified', 'edition_mapping': 'not_inferred',
                    'destination': 'not_attempted', 'continuity': 'not_inferred'}})
        cursor = y
    if text[cursor:].strip():
        rejected.append({'span': [start+cursor, end], 'reason': 'unparsed_tail',
                         'literal': text[cursor:]})
    return proposals, rejected


def account_range(raw, left, right, proposals):
    """Partition every character, including punctuation/whitespace, without reconstruction."""
    cursor, remaining = left, []
    for item in sorted(proposals, key=lambda p: p['char_start']):
        a, b = check_span(raw, item, item['entry_raw'], (left, right))
        if a < cursor:
            raise ValueError('Overlapping clause claims')
        if cursor < a:
            remaining.append({'raw': raw[cursor:a], **source_span(raw, cursor, a)})
        cursor = b
    if cursor < right:
        remaining.append({'raw': raw[cursor:right], **source_span(raw, cursor, right)})
    claimed = sum(p['char_end']-p['char_start'] for p in proposals)
    residual = sum(p['char_end']-p['char_start'] for p in remaining)
    if claimed+residual != right-left:
        raise ValueError('Character conservation failed')
    return {'total_characters': right-left, 'claimed_characters': claimed,
            'remaining_characters': residual, 'remaining': remaining,
            'claimed_nonspace_characters': sum(sum(not c.isspace() for c in p['entry_raw']) for p in proposals)}


def run(db, out, family='lowercase-ab', exclude_proposals=()):
    db, out = Path(db).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Use a new output directory')
    if family == 'lowercase-ab':
        compatible = lambda signature, raw: len(list(LOCATOR.finditer(signature))) >= 3
        extract = candidates
    else:
        from tools.indexing.index_literal_extensions import grammar
        compatible, extract = grammar(family)
    compatible_kinds = {'alphabetical_index','analytic_index','onomastic_index',
                        'lexical_index','subject_index','index_unspecified'}
    if family != 'lowercase-ab':
        compatible_kinds.add('thematic_index')
    protected, protected_inputs = defaultdict(list), []
    for filename in exclude_proposals:
        filename = Path(filename).resolve()
        contents = filename.read_bytes()
        protected_inputs.append({'path': str(filename), 'sha256': digest(contents)})
        for line in contents.decode('utf-8').splitlines():
            item = json.loads(line)
            protected[item['source']].append(item)
    proposals, exclusions, page_rows, source_inventory = [], [], [], []
    accounts = []
    rankings = defaultdict(Counter)
    with closing(readonly(db)) as con:
        con.execute('BEGIN')
        configuration = dict(con.execute('SELECT key,value_json FROM metadata'))
        for s in con.execute('SELECT * FROM sources ORDER BY volume,physical'):
            base = {'source': s['path'], 'volume': s['volume'], 'physical': s['physical'],
                    'source_sha256': s['sha256']}
            inventory = {**base, 'extraction_sha256': digest(s['page_json'].encode())}
            source_inventory.append(inventory)
            path = Path(s['path'])
            try:
                data = path.read_bytes()
            except OSError as exc:
                page_rows.append({**base, 'status': 'source_unavailable_deferred', 'error': str(exc)})
                continue
            if digest(data) != s['sha256']:
                page_rows.append({**base, 'status': 'source_changed_deferred'})
                continue
            raw = data.decode('utf-8')
            protected_spans = []
            for item in protected.get(s['path'], []):
                if item['source_sha256'] != s['sha256']:
                    raise ValueError('Protected proposal has incompatible source hash')
                protected_spans.append(check_span(raw, item, item['entry_raw']))
            page = json.loads(s['page_json'])
            scopes = page.get('campaign_accounting', {}).get('section_stats', [])
            # Reviewed local grammars/continuities are deliberately left intact.
            if not scopes:
                page_rows.append({**base, 'status': 'explicit_local_version_preserved'})
                continue
            ranges = [(r['char_start'], r['char_end'], 'residual', r['residual_id'], r['literal'])
                      for r in con.execute('SELECT * FROM residuals WHERE source_id=?', (s['source_id'],))]
            ranges += [(r['char_start'], r['char_end'], 'unit_unassigned_text', r['unit_id'], r['literal'])
                       for r in con.execute('''SELECT f.* FROM unit_unassigned_text f JOIN units u USING(unit_id)
                           WHERE u.source_id=?''', (s['source_id'],))]
            ranges.sort()
            inventory['input_ranges_sha256'] = key(ranges)
            seen, total, qualified = set(), 0, 0
            main_blocks = [b for b in blocks(raw) if b['kind'] == 'texto_principal']
            for a, b, kind, original_id, literal in ranges:
                check_span(raw, {'char_start': a, 'char_end': b}, literal)
                for block in main_blocks:
                    left, right = max(a, block['body_start']), min(b, block['body_end'])
                    if left >= right:
                        continue
                    owned = [scope for scope in scopes if scope['span'][0] <= left < right <= scope['span'][1]]
                    if len(owned) != 1:
                        continue
                    scope = owned[0]
                    if scope['kind'] not in compatible_kinds:
                        continue
                    # A repeated signature must be present within this exact block and scope.
                    signature = raw[max(scope['span'][0], block['body_start']):min(scope['span'][1],block['body_end'])]
                    if not compatible(signature, raw):
                        continue
                    qualified += 1
                    added, rejected = extract(raw, left, right)
                    kept = []
                    for item in added:
                        if any(item['char_start'] < pb and pa < item['char_end'] for pa, pb in protected_spans):
                            rejected.append({'span': [item['char_start'], item['char_end']],
                                'literal': item['entry_raw'], 'reason': 'overlap_with_protected_proposal'})
                        else:
                            kept.append(item)
                    added = kept
                    accounts.append({**base, 'origin_kind': kind, 'origin_id': original_id,
                                     'span': [left,right], **account_range(raw,left,right,added)})
                    exclusions.extend({**base, 'origin_kind': kind, 'origin_id': original_id, **r} for r in rejected)
                    for item in added:
                        group = item['reference_groups'][0]
                        occurrence = group['char_start'], group['char_end']
                        if occurrence in seen:
                            raise ValueError('Competing proposed attribution')
                        seen.add(occurrence)
                        item.update(base, origin_kind=kind, origin_id=original_id,
                                    section_id=scope['section_id'], family=family,
                                    id=key(s['path'], s['sha256'], *occurrence),
                                    source_quality=page.get('source_quality', {}))
                        proposals.append(item); total += 1
                        rankings[s['volume']]['clauses'] += 1
                        rankings[s['volume']]['source_characters'] += sum(not c.isspace() for c in item['entry_raw'])
                        rankings[s['volume']][kind] += 1
            page_rows.append({**base, 'status': 'evaluated', 'qualified_ranges': qualified, 'proposed_clauses': total})
    out.mkdir(parents=True)
    with closing(sqlite3.connect(out/'clauses.sqlite')) as con:
        con.execute('PRAGMA journal_mode=WAL'); con.executescript(SCHEMA)
        with con:
            for page in page_rows:
                con.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)', (page['source'], page['volume'],
                    page['physical'], page['source_sha256'], page['status'], encoded(page)))
            for item in proposals:
                group = item['reference_groups'][0]
                con.execute('INSERT INTO clauses VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (item['id'], item['source'], item['origin_kind'], item['origin_id'], group['owner_text_raw'],
                     group['raw'], item['char_start'], item['char_end'], group['char_start'], group['char_end'], encoded(item)))
                con.execute('INSERT INTO clause_search VALUES(?,?,?)', (item['id'], group['owner_text_raw'], group['raw']))
            for item in exclusions:
                con.execute('INSERT INTO exclusions(source,evidence_json) VALUES(?,?)', (item['source'], encoded(item)))
            summary = {'version': VERSION, 'family': family, 'source_pages': len(page_rows),
                'page_statuses': dict(Counter(p['status'] for p in page_rows)),
                'pages_with_proposals': sum(p.get('proposed_clauses',0)>0 for p in page_rows),
                'proposed_clauses': len(proposals), 'by_origin': dict(Counter(p['origin_kind'] for p in proposals)),
                'by_volume': dict(rankings), 'rejection_reasons': dict(Counter(p['reason'] for p in exclusions)),
                'new_lemmas_certified': 0, 'source_updates': 0, 'production_updates': 0, 'inference_calls': 0,
                'import_ready': False}
            summary['by_reference_kind'] = dict(Counter(p['reference_groups'][0].get(
                'reference_kind', 'literal_locator_group') for p in proposals))
            summary['qualified_ranges'] = len(accounts)
            summary['conservation'] = {k: sum(a[k] for a in accounts) for k in
                ('total_characters','claimed_characters','remaining_characters','claimed_nonspace_characters')}
            summary['scope_note'] = 'Character accounting covers only ranges with a compatible family signature; other input material is unchanged. Denominators of different families can overlap.'
            con.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
            manifest = {'version': VERSION, 'family': family, 'input_db': str(db), 'input_metadata': configuration,
                'protected_inputs': protected_inputs,
                'extension_code_sha256': digest(Path(__file__).with_name('index_literal_extensions.py').read_bytes()),
                'source_inventory_sha256': key(source_inventory), 'code_sha256': digest(Path(__file__).read_bytes())}
            con.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', encoded(manifest)))
            if con.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Invalid relations')
    for name, items in [('proposals',proposals),('exclusions',exclusions),('pages',page_rows),
                        ('accounting',accounts),('source_inventory',source_inventory)]:
        (out/(name+'.jsonl')).write_text(''.join(encoded(item)+'\n' for item in items))
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (out/'manifest.json').write_text(encoded(manifest)+'\n')
    rng = random.Random(73)
    sample = []
    for volume in sorted(rankings):
        pool = [p for p in proposals if p['volume']==volume]
        sample.extend(rng.sample(pool,min(8,len(pool))))
    (out/'review_sample.json').write_text(encoded({'seed':73,'reviewed':False,'proposals':sample})+'\n')
    (out/'report.md').write_text(f'# Recuperação de cláusulas: {family}\n\n'
        f"{len(proposals)} propostas em {summary['pages_with_proposals']} páginas. "
        'São cláusulas candidatas, sem atribuição de lema hierárquico ou destino.\n\n'
        'O banco de entrada permaneceu somente leitura. Mudanças de OCR são adiadas; '
        'as versões locais revisadas não foram reinterpretadas. '
        'Consultar summary.json para distribuição e accounting.jsonl para cada trecho restante. '
        'A amostra seed 73 ainda precisa de conferência editorial independente.\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--family', choices=['lowercase-ab','paired-columns','cross-references','abbreviations'],
                        default='lowercase-ab')
    parser.add_argument('--exclude-proposals', type=Path, action='append', default=[],
                        help='Protect claims from a compatible previous proposals.jsonl; repeatable')
    args=parser.parse_args()
    print(json.dumps(run(args.db,args.out,args.family,args.exclude_proposals),ensure_ascii=False,indent=2))
