#!/usr/bin/env python3
"""Offline lexical evidence for PO015's existing name-to-page candidates.

Never changes mappings or source text. FTS is a retrieval aid; token matches,
qualifiers, repeated occurrences and line evidence have independent states.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.catalog_po015_names import dump, sha
from scripts.scan_editorial_ocr import ATTR
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly
from tools.corpus_utils import page_files

VERSION = 'po015-lexical-v2'
FIELDS = ('body', 'parallel', 'notes', 'apparatus', 'other')
PRIORITY = {'exact': 0, 'normalized': 1, 'fuzzy': 2, 'absent': 3}


def history_page_identity(volume, physical, recorded_hash, local_hash):
    """Page registration permits retrieval; byte identity is only provenance.

    Structural exceptions: docs/recorte_pl020_tomo_xxi_2026-09-04.md and
    data/volume_similarity_audit/defective_volume_cleanup_manifest.json.
    Replacement history remains searchable, but needs source-generation review.
    """
    if volume == 'PL020' and 612 <= physical <= 1223:
        return 'excluded_removed_tome_xxi'
    if local_hash and recorded_hash == local_hash:
        return 'same_image_bytes'
    if volume in {'PG024', 'PG031', 'PG084', 'PG116', 'PL124'}:
        return 'replacement_source_needs_review'
    return 'volume_page_registration'


def normalize(text):
    # Decompose BEFORE casefold: Greek combining iota must not become a new letter.
    text = ''.join(c for c in unicodedata.normalize('NFKD', text)
                   if not unicodedata.combining(c)).casefold()
    return text.translate(str.maketrans({'œ': 'oe', 'æ': 'ae', 'ϲ': 'σ'}))


def tokens(text):
    """Letter tokens with original character spans, including decomposed accents."""
    start = None
    for i, ch in enumerate(text + ' '):
        category = unicodedata.category(ch)
        if category.startswith('L') or (start is not None and category.startswith('M')):
            if start is None:
                start = i
        elif start is not None:
            raw = text[start:i]
            yield {'raw': raw, 'norm': normalize(raw), 'start': start, 'end': i}
            start = None


def normalized_text(text):
    return ' '.join(t['norm'] for t in tokens(text))


def edit_distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        nxt = [i]
        for j, y in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = nxt
    return row[-1]


def script_of(text):
    scripts = set()
    for ch in text:
        if ch.isalpha():
            name = unicodedata.name(ch, '')
            scripts.add('greek' if 'GREEK' in name else 'latin' if 'LATIN' in name else 'other')
    return scripts


def match_tokens(query, document, threshold=0.8):
    """Contiguous token matches; no substring acceptance and no guessed aliases."""
    q = list(tokens(query))
    if not q:
        return []
    found = []
    for i in range(len(document) - len(q) + 1):
        window = document[i:i + len(q)]
        if all(x['raw'] == y['raw'] for x, y in zip(q, window)):
            method, score = 'exact', 1.0
        elif all(x['norm'] == y['norm'] for x, y in zip(q, window)):
            method, score = 'normalized', 1.0
        else:
            scores = []
            for x, y in zip(q, window):
                a, b = x['norm'], y['norm']
                if a == b:
                    scores.append(1.0)
                    continue
                if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 2 or script_of(a) != script_of(b):
                    break
                distance = edit_distance(a, b)
                similarity = 1 - distance / max(len(a), len(b))
                if distance > 2 or similarity < threshold:
                    break
                scores.append(similarity)
            if len(scores) != len(q):
                continue
            method, score = 'fuzzy', min(scores)
        found.append({'start': window[0]['start'], 'end': window[-1]['end'],
                      'method': method, 'score': score})
    return found


def read_page(path):
    raw = path.read_text()
    return read_text(raw)


def read_text(raw):
    result = []
    greek_line = 0
    for ordinal, b in enumerate(blocks(raw), 1):
        if b['kind'] == 'cabecalho':
            continue
        attrs = dict(ATTR.findall(raw[b['start']:b['body_start']]))
        script = attrs.get('script', '')
        if b['kind'] == 'texto_principal':
            role = 'body' if script == 'grego' else 'parallel' if script == 'latino' else 'other'
        elif b['kind'] == 'aparato_critico':
            role = 'apparatus'
        elif b['kind'] in ('nota', 'rodape', 'nota_rodape', 'nota_marginal'):
            role = 'notes'
        else:
            role = 'other'
        # This bounded experiment rejects nested markup rather than searching tag names.
        if re.search(r'<[^>]+>|\[(?:/?br|/?span)\b', b['body']):
            raise ValueError('Nested markup needs an offset-preserving reader')
        line_spans = []
        offset = 0
        for line in b['body'].splitlines(keepends=True):
            if line.strip() and role == 'body':
                greek_line += 1
                line_spans.append((offset, offset + len(line), greek_line))
            offset += len(line)
        result.append({**b, 'ordinal': ordinal, 'role': role, 'script': script,
                       'tokens': list(tokens(b['body'])), 'greek_line_spans': line_spans})
    return {'raw': raw, 'blocks': result, 'sha256': hashlib.sha256(raw.encode()).hexdigest()}


def read_history_text(raw):
    page = read_text(raw)
    if not blocks(raw):
        # Plain Tesseract frequently mistakes guillemets for isolated < and >.
        if re.search(r'</?[A-Za-z][A-Za-z0-9_:.-]*(?:\s+[^<>\n]*)?/?>|\[(?:/?bloco|/?texto|/?br|/?span)\b', raw):
            raise ValueError('Unrecognized markup in historical OCR')
        page['blocks'] = [{'ordinal': 1, 'role': 'unsegmented', 'script': 'unknown',
                           'kind': 'plain_text', 'body': raw, 'body_start': 0,
                           'body_end': len(raw), 'tokens': list(tokens(raw)), 'greek_line_spans': []}]
    return page


def audit_versions(db, rows, out, root=ROOT, scope='absent', threshold=0.8):
    """Search readings registered to the same physical page; keep each source."""
    selected = [r for r in rows if scope == 'all' or r['method'] == 'absent']
    images = page_files(root / 'teste/PO015/images', suffixes=['.png'])
    targets = {t['physical']: t for r in selected for t in r['target_files']}
    inventory, documents, exclusions = [], {}, []
    snapshot_dir = out / 'version_sources'
    snapshot_dir.mkdir()
    con = readonly(db)
    try:
        con.execute('BEGIN')
        for physical, target in sorted(targets.items()):
            candidates = images.get(physical, [])
            image_path = candidates[0] if len(candidates) == 1 else None
            image_sha = sha(image_path) if image_path else None
            documents[physical] = []
            for row in con.execute('''SELECT r.id,r.volume_id,r.page_num,r.image_hash,r.text_hash,
                    r.text_content,r.is_current,r.created_at,r.engine_version_id,e.engine,e.model
                    FROM ocr_results r JOIN ocr_engine_versions e ON e.id=r.engine_version_id
                    WHERE r.volume_id=? AND r.page_num=? ORDER BY r.id''', ('PO015', physical)):
                meta = dict(row)
                raw = meta.pop('text_content')
                computed = hashlib.sha256(raw.encode()).hexdigest()
                identity = history_page_identity('PO015', physical, meta['image_hash'], image_sha)
                meta.update(image=str(image_path.resolve()) if image_path else None,
                            local_image_sha256=image_sha, page_identity=identity,
                            computed_text_sha256=computed)
                if identity.startswith('excluded_'):
                    meta['status'] = identity
                elif computed != meta['text_hash']:
                    meta['status'] = 'excluded_text_hash_mismatch'
                elif computed == target['sha256']:
                    meta['status'] = 'identical_to_catalog_target'
                else:
                    try:
                        page = read_history_text(raw)
                    except ValueError as exc:
                        meta.update(status='excluded_unsupported_format', reason=str(exc))
                    else:
                        snapshot = snapshot_dir / f'PO015-{physical}-version-{meta["id"]}.txt'
                        snapshot.write_bytes(raw.encode())
                        meta.update(status='searched', snapshot=str(snapshot.resolve()))
                        documents[physical].append((meta, page))
                inventory.append(meta)
    finally:
        con.close()
    records = []
    for r in selected:
        alternatives = []
        for t in r['target_files']:
            for meta, page in documents.get(t['physical'], []):
                hits = page_matches(page, r['name_raw'], 'full_label', threshold)
                ts = list(tokens(r['name_raw']))
                if not hits and len(ts) > 1:
                    hits = page_matches(page, ts[0]['raw'], 'head_only', threshold)
                if hits:
                    alternatives.append({'version_id': meta['id'], 'engine': meta['engine'],
                        'model': meta['model'], 'is_current': meta['is_current'],
                        'physical': t['physical'], 'snapshot': meta['snapshot'],
                        'text_sha256': meta['text_hash'], 'image_sha256': meta['image_hash'],
                        'page_identity': meta['page_identity'],
                        'method': hits[0]['method'], 'matches': hits})
        records.append({'ref_id': r['ref_id'], 'entry_id': r['entry_id'], 'name_raw': r['name_raw'],
                        'ref_raw': r['ref_raw'], 'baseline_method': r['method'],
                        'alternatives': alternatives, 'certified': False,
                        'status': 'alternative_lexical_evidence' if alternatives else 'no_alternative_match'})
    recovered = [r for r in records if r['baseline_method'] == 'absent' and r['alternatives']]
    summary = {'scope': scope, 'references_examined': len(selected), 'target_pages': len(targets),
               'version_records': len(inventory), 'version_statuses': dict(Counter(v['status'] for v in inventory)),
               'baseline_absent_with_alternative_evidence': len(recovered),
               'baseline_absent_still_without_match': sum(r['baseline_method'] == 'absent' and not r['alternatives'] for r in records),
               'best_recovered_methods': dict(Counter(min((a['method'] for a in r['alternatives']), key=PRIORITY.get) for r in recovered)),
               'recovered_full_label': sum(any(h['query_scope'] == 'full_label' for a in r['alternatives'] for h in a['matches']) for r in recovered),
               'exclusions': exclusions, 'source_db': str(db.resolve()), 'source_db_mode': 'ro/query_only',
               'name_queries': 'unchanged_catalog_labels', 'ocr_updates': 0, 'mapping_updates': 0,
               'inference_calls': 0, 'note': 'Alternative evidence only; versions are correlated, not votes. Plain OCR has no certified body/note/line separation.'}
    dump(out / 'version_inventory.json', inventory)
    dump(out / 'version_evidence.json', records)
    dump(out / 'version_summary.json', summary)
    return summary


def note_leads(body):
    """Starts of explicitly numbered notes only; not an entity extractor."""
    return [' '.join(t['norm'] for t in list(tokens(m[1]))[:6]) for m in
            re.finditer(r'(?:^|[\n—])\s*\d+\.\s*([^\n—]+)', body)]


def create_search(con, pages):
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE pages(id INTEGER PRIMARY KEY, file TEXT UNIQUE, sha256 TEXT)')
    con.execute('CREATE TABLE passages(id INTEGER PRIMARY KEY,page_id INTEGER,role TEXT,kind TEXT,'
                'char_start INTEGER,char_end INTEGER,text_raw TEXT,text_norm TEXT)')
    columns = [f'{f}_{v}' for f in FIELDS for v in ('raw', 'norm')] + ['note_leads_norm']
    con.execute('CREATE VIRTUAL TABLE pages_fts USING fts5(' + ','.join(columns) +
                ", tokenize='unicode61 remove_diacritics 0')")
    con.execute("CREATE VIRTUAL TABLE pages_trigram USING fts5(text_norm,tokenize='trigram')")
    ids = {}
    for pid, (path, page) in enumerate(sorted(pages.items()), 1):
        ids[path] = pid
        con.execute('INSERT INTO pages VALUES(?,?,?)', (pid, path, page['sha256']))
        by_role = defaultdict(list)
        leads = []
        for b in page['blocks']:
            by_role[b['role']].append(b['body'])
            if b['role'] in ('notes', 'apparatus'):
                leads.extend(note_leads(b['body']))
            con.execute('INSERT INTO passages VALUES(NULL,?,?,?,?,?,?,?)',
                        (pid, b['role'], b['kind'], b['body_start'], b['body_end'],
                         b['body'], normalized_text(b['body'])))
        values = []
        for field in FIELDS:
            raw = '\n'.join(by_role[field])
            values.extend((raw, normalized_text(raw)))
        values.append('\n'.join(leads))
        con.execute('INSERT INTO pages_fts(rowid,' + ','.join(columns) + ') VALUES(' +
                    ','.join('?' for _ in range(len(values) + 1)) + ')', [pid, *values])
        con.execute('INSERT INTO pages_trigram(rowid,text_norm) VALUES(?,?)',
                    (pid, '\n'.join(values[1::2])))
    return ids


def fts_pages(con, query):
    text = normalized_text(query)
    if not text:
        return []
    columns = ' '.join(f'{f}_norm' for f in FIELDS)
    expression = '{' + columns + '} : "' + text.replace('"', '""') + '"'
    return [r[0] for r in con.execute('SELECT rowid FROM pages_fts WHERE pages_fts MATCH ? '
                                     'ORDER BY bm25(pages_fts),rowid', (expression,))]


def page_matches(page, query, scope, threshold):
    found = []
    for b in page['blocks']:
        for hit in match_tokens(query, b['tokens'], threshold):
            a, z = hit['start'], hit['end']
            line_ids = [n for start, end, n in b['greek_line_spans'] if a < end and z > start]
            found.append({**hit, 'query_scope': scope, 'block': b['ordinal'], 'role': b['role'],
                          'kind': b['kind'], 'char_start': b['body_start'] + a,
                          'char_end': b['body_start'] + z, 'text_raw': b['body'][a:z],
                          'snippet': ' '.join(b['body'][max(0, a - 85):z + 85].split()),
                          'ocr_greek_line_ordinals': line_ids})
    return sorted(found, key=lambda h: (PRIORITY[h['method']], -h['score'], h['char_start']))


def run(catalog, out, seed=20261106, sample_size=6, threshold=0.8, versions_db=None, version_scope='absent'):
    source = catalog / 'entries.json'
    entries = json.loads(source.read_text())
    target_proofs = {}
    source_proofs = {}
    for e in entries:
        source_proofs[e['source']] = e['source_sha256']
        for r in e['references']:
            for t in r['target_files']:
                if t['file'] in target_proofs and target_proofs[t['file']] != t['sha256']:
                    raise ValueError('Conflicting target hashes')
                target_proofs[t['file']] = t['sha256']
    for path, expected in {**source_proofs, **target_proofs}.items():
        if sha(Path(path)) != expected:
            raise ValueError(f'Stale catalog source: {path}; rebuild catalog before auditing')
    pages = {p: read_page(Path(p)) for p in sorted(target_proofs)}
    head_ids = defaultdict(list)
    for e in entries:
        ts = list(tokens(e['name_raw']))
        if ts:
            head_ids[ts[0]['norm']].append(e['id'])
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    with sqlite3.connect(out / 'lexical.sqlite') as con:
        ids = create_search(con, pages)
        con.execute('CREATE TABLE evidence(ref_id TEXT PRIMARY KEY,entry_id TEXT,method TEXT,evidence_json TEXT)')
        for e in entries:
            full = e['name_raw']
            ts = list(tokens(full))
            head = ts[0]['raw'] if ts else ''
            fts = fts_pages(con, full)
            for r in e['references']:
                if not r['target_files']:
                    continue
                hits = []
                full_count = 0
                for t in r['target_files']:
                    page = pages[t['file']]
                    full_hits = page_matches(page, full, 'full_label', threshold)
                    full_count += len(full_hits)
                    local_hits = full_hits
                    if len(ts) > 1 and not full_hits:
                        local_hits = page_matches(page, head, 'head_only', threshold)
                    for h in local_hits:
                        h.update(file=t['file'], source_sha256=page['sha256'], physical=t['physical'])
                        h['ocr_line_coincides_requested'] = bool(set(h['ocr_greek_line_ordinals']) & set(r.get('lines', [])))
                    hits.extend(local_hits)
                hits.sort(key=lambda h: (PRIORITY[h['method']], -h['score'], h['file'], h['char_start']))
                method = hits[0]['method'] if hits else 'absent'
                strongest = [h for h in hits if h['method'] == method]
                flags = []
                if hits and not full_count:
                    flags.append('qualifiers_not_matched')
                if ts and len(head_ids[ts[0]['norm']]) > 1:
                    flags.append('head_shared_by_multiple_entries')
                if len(strongest) > 1:
                    flags.append('multiple_occurrences')
                if len({h['file'] for h in strongest}) > 1:
                    flags.append('multiple_target_files')
                if hits and not any(h['role'] == 'body' for h in strongest):
                    flags.append('outside_greek_body')
                if e.get('quality', {}).get('name_reading') == 'known_ocr_disagreement':
                    flags.append('known_index_ocr_disagreement')
                if len(normalized_text(head)) < 5:
                    flags.append('short_name_no_fuzzy_expansion')
                row = {'ref_id': r['id'], 'entry_id': e['id'], 'name_raw': full,
                       'index_source': e['source'], 'index_physical': e['physical'],
                       'index_ordinal': e['ordinal'], 'ref_raw': r['ref_raw'],
                       'editorial_page': r.get('page'), 'editorial_lines': r.get('lines', []),
                       'method': method, 'matches': hits, 'ambiguities': flags,
                       'full_label_match': bool(full_count),
                       'query_tokens': [t['raw'] for t in ts],
                       'name_reading_status': e.get('quality', {}).get('name_reading', 'unknown'),
                       'fts_full_label_candidate_pages': [t['physical'] for t in r['target_files'] if ids[t['file']] in fts],
                       'fts_full_label_pages_in_index': len(fts),
                       'line_status': 'not_certified_ocr_line_is_not_printed_line',
                       'previous_visual_pair': r.get('visual_pair_confirmed', False),
                       'semantic_status': 'not_evaluated', 'certified': False,
                       'target_files': r['target_files']}
                rows.append(row)
                con.execute('INSERT INTO evidence VALUES(?,?,?,?)',
                            (r['id'], e['id'], method, json.dumps(row, ensure_ascii=False)))
        con.commit()
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Experiment database failed quick_check')
    # Recheck the exact snapshot; concurrent OCR edits cannot silently validate old links.
    for path, expected in {**source_proofs, **target_proofs}.items():
        if sha(Path(path)) != expected:
            raise ValueError(f'Source changed during audit: {path}')
    dump(out / 'evidence.json', rows)
    columns = ['ref_id', 'entry_id', 'name_raw', 'ref_raw', 'editorial_page', 'method',
               'full_label_match', 'ambiguities', 'fts_full_label_pages_in_index', 'previous_visual_pair']
    with (out / 'evidence.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows({k: json.dumps(r[k], ensure_ascii=False) if isinstance(r[k], list) else r[k]
                          for k in columns} for r in rows)
    sample = []
    rng = random.Random(seed)
    for method in PRIORITY:
        eligible = sorted((r for r in rows if r['method'] == method and not r['previous_visual_pair']),
                          key=lambda r: r['ref_id'])
        for r in rng.sample(eligible, min(sample_size, len(eligible))):
            sample.append({'ref_id': r['ref_id'], 'name_raw': r['name_raw'], 'method': method,
                           'ref_raw': r['ref_raw'], 'target_files': r['target_files'],
                           'index_source': r['index_source'], 'review_status': 'pending',
                           'selection': 'seeded_by_match_method_excluding_previous_visual_pairs'})
    dump(out / 'review_sample.json', sample)
    summary = {'version': VERSION, 'entries_total': len(entries), 'references_with_targets': len(rows),
               'target_pages': len(pages), 'methods': dict(Counter(r['method'] for r in rows)),
               'full_label_matches': sum(r['full_label_match'] for r in rows),
               'head_only_matches': sum(bool(r['matches']) and not r['full_label_match'] for r in rows),
               'methods_full_label': dict(Counter(r['method'] for r in rows if r['full_label_match'])),
               'methods_head_only': dict(Counter(r['method'] for r in rows if r['matches'] and not r['full_label_match'])),
               'ambiguity_flags': dict(Counter(f for r in rows for f in r['ambiguities'])),
               'strongest_roles': dict(Counter(h['role'] for r in rows for h in r['matches'][:1])),
               'ocr_line_coincidences_not_certifications': sum(any(h['ocr_line_coincides_requested'] for h in r['matches']) for r in rows),
               'editorial_lines_certified_by_this_run': 0, 'new_page_links': 0,
               'production_updates': 0, 'inference_calls': 0, 'certified_references': 0,
               'seed': seed, 'fuzzy_threshold': threshold, 'sample_size_per_method': sample_size,
               'source_sha256': sha(source), 'script_sha256': sha(Path(__file__)),
               'sqlite_version': sqlite3.sqlite_version,
               'note': 'Lexical evidence is not identity, printed-line or editorial-target certification.'}
    if versions_db:
        summary['historical_ocr'] = audit_versions(versions_db, rows, out, scope=version_scope, threshold=threshold)
    dump(out / 'summary.json', summary)
    dump(out / 'manifest.json', {'catalog': str(catalog.resolve()), 'catalog_sha256': sha(source),
                               'sources': source_proofs, 'targets': target_proofs, 'parameters': summary})
    report = ['# Evidência lexical nos destinos onomásticos de PO015', '',
              f"{len(rows)} grupos com destino, {len(pages)} arquivos; nenhuma ligação alterada.", '',
              '| Correspondência | Grupos |', '|---|---:|']
    report += [f'| {k} | {summary["methods"].get(k, 0)} |' for k in PRIORITY]
    report += ['', f'Rótulo completo: {summary["full_label_matches"]}; somente núcleo: {summary["head_only_matches"]}.',
               '', 'Ausência não invalida o destino. Fuzzy não comprova identidade. Linhas OCR são apenas diagnósticas.',
               'A amostra sorteada está em review_sample.json, sem declarar revisão automática.', '',
               '| Nome | Ref. | Método | Limites | Melhor trecho |', '|---|---|---|---|---|']
    for r in rows:
        snippet = r['matches'][0]['snippet'] if r['matches'] else '—'
        values = [r['name_raw'], r['ref_raw'], r['method'], ', '.join(r['ambiguities']), snippet]
        report.append('| ' + ' | '.join(x.replace('|', '\\|').replace('\n', ' ') for x in values) + ' |')
    (out / 'report.md').write_text('\n'.join(report) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('data/index_catalog/po015_names_002'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20261106)
    parser.add_argument('--sample-size', type=int, default=6, help='Per lexical method')
    parser.add_argument('--fuzzy-threshold', type=float, default=0.8)
    parser.add_argument('--versions-db', type=Path, help='Optional OCR history; read-only, matched by volume and physical page')
    parser.add_argument('--version-scope', choices=['absent', 'all'], default='absent')
    args = parser.parse_args()
    if not 0 < args.fuzzy_threshold <= 1 or args.sample_size < 0:
        parser.error('threshold must be in (0,1], sample size nonnegative')
    print(json.dumps(run(args.catalog, args.out, args.seed, args.sample_size, args.fuzzy_threshold,
                         args.versions_db, args.version_scope), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
