"""Bounded Latin internal-number maps; candidates only, never a production resolver."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3

from scripts.audit_alphabetical_numbering import supported_sequence_nodes
from tools.corpus_utils import discover_preferred_pages, page_number
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.footer_marker_experiment import visible_map
from tools.indexing.parser_target_experiment import locate_number
from tools.ocr_xml_utils import parse_ocr_xml_page

VERSION = 'internal-section-map-v2'
ROOT = Path(__file__).resolve().parents[2]
NUMBER = re.compile(r'(?<!\w)[1-9][0-9]{0,3}(?!\w)')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def classify(text, start, end):
    """Reject identifiable counterexamples; an inline integer is only a candidate."""
    before, after = text[:start], text[end:]
    if re.search(r'\[(?:P\.?\s*)?\s*$', before, re.I):
        return 'other_bracket_numbering'
    if re.search(r'(?:cap(?:ut|\.)?|lib(?:er|\.)?|anno|ann(?:is|os)|pag\.|p\.)\s*$', before, re.I):
        return 'explicit_non_marker_context'
    if re.search(r'\(\s*$', before) and re.match(r'\s*\)', after):
        return 'parenthetical_number'
    if re.match(r'\.', after) and (not before.strip() or re.search(r'\n\s*\n\s*$', before)):
        return 'paragraph_number'
    if re.match(r'\s*(?:annos|annis|libras|menses)\b', after, re.I):
        return 'quantity_context'
    return 'inline_number_candidate'


def extract_markers(raw, marker_scripts=('latino',)):
    """Emit raw XML spans; language is a tag hint, never an image coordinate."""
    markers = []
    for i, block in enumerate(blocks(raw)):
        opening = raw[block['start']:block['body_start']]
        script = re.search(r'\bscript=["\']([^"\']+)', opening)
        if block['kind'] != 'texto_principal' or not script or script[1] not in marker_scripts:
            continue
        text, offsets = visible_map(block['body'], block['body_start'])
        for match in NUMBER.finditer(text):
            a, b = offsets[match.start()][0], offsets[match.end()-1][1]
            markers.append({'number': int(match[0]), 'raw': raw[a:b],
                            'char_start': a, 'char_end': b, 'block_index': i,
                            'offset_space': 'raw_xml_characters', 'body_offset': a,
                            'excerpt': text[max(0, match.start()-80):match.end()+100],
                            'role': classify(text, match.start(), match.end())})
    return markers


def build_map(profile, corpus):
    marker_scripts = profile.get('marker_scripts', ['latino'])
    if (not isinstance(marker_scripts, list) or not marker_scripts
            or not all(isinstance(s, str) and s for s in marker_scripts)):
        raise ValueError('marker_scripts must be a non-empty list of script labels')
    for start, end in [('physical_start', 'physical_end'), ('number_start', 'number_end'),
                       ('index_physical_start', 'index_physical_end')]:
        if profile[start] > profile[end]:
            raise ValueError(f'Reversed profile bounds: {start}, {end}')
    nodes, broad_nodes = defaultdict(set), defaultdict(set)
    pages, sources, markers = {}, [], []
    low, high = profile['number_start'], profile['number_end']
    for path in discover_preferred_pages(corpus / profile['volume'] / 'text', volume_id=profile['volume']):
        physical = page_number(path)
        if not profile['physical_start'] <= physical <= profile['physical_end']:
            continue
        data = path.read_bytes()
        raw = data.decode('utf-8')
        filename, digest = str(path.resolve()), sha(data)
        sources.append({'path': filename, 'sha256': digest, 'physical': physical})
        occurrences = defaultdict(list)
        for item in extract_markers(raw, marker_scripts):
            item.update(file=filename, sha256=digest, physical=physical)
            item['in_number_scope'] = low <= item['number'] <= high + 1
            markers.append(item)
            if not item['in_number_scope']:
                continue
            broad_nodes[item['number']].add(physical)
            if item['role'] == 'inline_number_candidate':
                occurrences[item['number']].append(item)
                nodes[item['number']].add(physical)
        pages[physical] = {'file': filename, 'sha256': digest,
                           'header': parse_ocr_xml_page(raw).header_text,
                           'occurrences': dict(occurrences)}
    if not pages:
        raise ValueError('No OCR files in profile bounds')
    anchor_checks = []
    for anchor in profile.get('anchors', []):
        physical, number = anchor['physical'], anchor['number']
        image = corpus / profile['volume'] / 'images' / anchor['image_name']
        if sha(image.read_bytes()) != anchor['image_sha256']:
            raise ValueError(f'Reviewed image changed: {image}')
        options = pages.get(physical, {}).get('occurrences', {}).get(number, [])
        matching = [m for m in options if anchor['context'] in ' '.join(m['excerpt'].split())]
        if len(options) != 1 or len(matching) != 1:
            raise ValueError(f'Anchor {number}@{physical} requires renewed review of OCR context')
        anchor_checks.append({'number': number, 'physical': physical,
                              'current_source_sha256': pages[physical]['sha256'],
                              'image_path': str(image.resolve()),
                              'image_sha256': anchor['image_sha256'],
                              'context': anchor['context']})
    for number in nodes:
        nodes[number] = {p for p in nodes[number] if all(
            p <= a['physical'] if number < a['number'] else
            p >= a['physical'] if number > a['number'] else p == a['physical']
            for a in profile.get('anchors', []))}
    supported = supported_sequence_nodes(nodes)
    for anchor in profile.get('anchors', []):
        key = (anchor['number'], anchor['physical'])
        supported[key] = {'basis': 'reviewed_marker_context', 'chain_length': 0, 'witness': [key]}
    series = {'spec': profile, 'pages': pages, 'nodes': nodes, 'supported': supported}
    results = []
    for number in range(low, high+1):
        record = locate_number(series, number, max_file_gap=profile.get('max_file_gap', 3))
        record['marker_files'] = sorted({x['physical'] for x in record['start_options']})
        record['window_complete'] = record['status'] == 'bounded_window_candidate'
        if not record['window_complete'] and len(record['start_options']) == 1:
            record['marker_file_only'] = record['start_options'][0]
        results.append(record)
    broad_supported = supported_sequence_nodes(broad_nodes)
    summary = {'files_read': len(pages), 'numbers_requested': len(results),
               'number_statuses': dict(Counter(r['status'] for r in results)),
               'numbers_with_marker': sum(bool(r['marker_files']) for r in results),
               'distinct_marker_files': len({p for r in results for p in r['marker_files']}),
               'bounded_windows': sum(r['window_complete'] for r in results),
               'distinct_window_files': len({f['physical'] for r in results for f in r['files']}),
               'automatically_verified_occurrences': 0,
               'marker_roles': dict(Counter(m['role'] for m in markers)),
               'broad_supported_pairs': len(broad_supported), 'classified_supported_pairs': len(supported),
               'removed_supported_pairs': [list(p) for p in sorted(set(broad_supported)-set(supported))],
               'added_supported_pairs': [list(p) for p in sorted(set(supported)-set(broad_supported))]}
    return {'version': VERSION, 'profile': profile, 'sources': sources, 'markers': markers,
            'anchors': anchor_checks, 'numbers': results, 'summary': summary,
            'application_supported': False, 'import_ready': False}


def verify_notice(profile, corpus):
    """Keep the interpretation tied to a current, uniquely matched editorial notice."""
    notice = profile['notice']
    options = [p for p in discover_preferred_pages(corpus / profile['volume'] / 'text',
               volume_id=profile['volume']) if page_number(p) == notice['physical']]
    if len(options) != 1:
        raise ValueError('Editorial notice source missing or ambiguous')
    path = options[0]
    data = path.read_bytes()
    visible = ' '.join(parse_ocr_xml_page(data.decode('utf-8')).all_text.split())
    if visible.count(notice['quote']) != 1:
        raise ValueError('Editorial notice changed; review section interpretation')
    return {'path': str(path.resolve()), 'sha256': sha(data), 'quote': notice['quote'],
            'physical': notice['physical'], 'status': 'unique_current_notice'}


def compare_occurrences(mapping, audit):
    """Preserve printed occurrences and source freshness; do not certify ownership."""
    volume = mapping['profile']['volume']
    by_number = {r['number']: r for r in mapping['numbers']}
    con = sqlite3.connect(audit.resolve().as_uri() + '?mode=ro', uri=True)
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    records, source_cache = [], {}
    try:
        for (encoded,) in con.execute('SELECT evidence_json FROM audit WHERE volume=?', (volume,)):
            old = json.loads(encoded)
            number = old.get('number')
            if number not in by_number:
                continue
            source = Path(old['source'])
            if str(source) not in source_cache:
                source_cache[str(source)] = source.read_bytes() if source.is_file() else None
            data = source_cache[str(source)]
            a, b = old['reference_span']
            raw = data.decode('utf-8') if data is not None else ''
            fresh = data is not None and sha(data) == old['source_sha256']
            span_valid = 0 <= a < b <= len(raw) and str(number) in re.findall(r'\d+', raw[a:b])
            in_section = (mapping['profile']['index_physical_start'] <= page_number(source)
                          <= mapping['profile']['index_physical_end'])
            if mapping['profile'].get('required_audit_rule'):
                in_section = in_section and mapping['profile']['required_audit_rule'] in old.get('rule_ids', [])
            valid = fresh and span_valid and in_section
            mapped = by_number[number]
            records.append({'occurrence_id': old['occurrence_id'], 'owner_raw': old['owner_raw'],
                            'number': number, 'source': str(source), 'source_sha256': old['source_sha256'],
                            'source_current': fresh, 'source_span_valid': span_valid,
                            'reference_span': [a, b], 'in_index_section': in_section,
                            'status': mapped['status'] if valid else 'source_requires_reextraction',
                            'files': mapped['files'] if valid else [],
                            'marker_files': mapped['marker_files'] if valid else [],
                            'previous_files': old.get('previous_candidates', []),
                            'audit_record_sha256': sha(encoded.encode()),
                            'owner_verified': False, 'verified': False})
    finally:
        con.close()
    return records


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def run(args):
    profile = json.loads(args.profile.read_text())
    if args.out.exists():
        raise ValueError('Use a new output directory; previous runs are immutable')
    result = build_map(profile, args.root)
    if profile.get('notice'):
        result['notice_evidence'] = verify_notice(profile, args.root)
    if args.audit:
        result['occurrences'] = compare_occurrences(result, args.audit)
        result['summary']['occurrence_statuses'] = dict(Counter(r['status'] for r in result['occurrences']))
        result['summary']['occurrences'] = len(result['occurrences'])
        bounded = [r for r in result['occurrences'] if r['status'] == 'bounded_window_candidate']
        result['summary']['occurrence_window_file_counts'] = dict(Counter(len(r['files']) for r in bounded))
        result['summary']['bounded_occurrences_without_previous_candidate'] = sum(not r['previous_files'] for r in bounded)
        result['summary']['bounded_occurrences_disjoint_from_previous_candidates'] = sum(
            bool(r['previous_files']) and not ({f['file'] for f in r['files']} &
            {f.get('path', f.get('file')) for f in r['previous_files']}) for r in bounded)
        result['audit_snapshot_sha256'] = sha(''.join(sorted(
            r['audit_record_sha256'] for r in result['occurrences'])).encode())
    verification_sources = list(result['sources'])
    if result.get('notice_evidence'):
        verification_sources.append(result['notice_evidence'])
    verification_sources.extend({'path': r['source'], 'sha256': r['source_sha256']}
                                for r in result.get('occurrences', []) if r['source_current'])
    for source in verification_sources:
        if sha(Path(source['path']).read_bytes()) != source['sha256']:
            raise ValueError('OCR changed during execution; rerun with a stable source')
    population = sorted(s['physical'] for s in result['sources']
                        if profile['physical_start'] < s['physical'] < profile['physical_end'])
    sample = random.Random(args.seed).sample(population, min(args.sample, len(population)))
    result['visual_sample'] = {'seed': args.seed, 'population': population,
                               'selected_physical': sample, 'status': 'awaiting_visual_review'}
    args.out.mkdir(parents=True)
    dump(args.out / 'map.json', result)
    dump(args.out / 'summary.json', result['summary'])
    dump(args.out / 'config.json', {'version': VERSION, 'profile': profile,
         'profile_sha256': sha(args.profile.read_bytes()), 'root': str(args.root.resolve()),
         'audit': str(args.audit.resolve()) if args.audit else None, 'seed': args.seed,
         'audit_snapshot_sha256': result.get('audit_snapshot_sha256'),
         'implementation_sha256': {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in [
             Path(__file__), ROOT / 'scripts/audit_alphabetical_numbering.py',
             ROOT / 'tools/indexing/editorial_ocr_blocks.py',
             ROOT / 'tools/indexing/footer_marker_experiment.py',
             ROOT / 'tools/indexing/parser_target_experiment.py']}})
    con = sqlite3.connect(args.out / 'map.sqlite')
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE numbers(number INTEGER PRIMARY KEY,status TEXT,result_json TEXT)')
        con.execute('CREATE TABLE occurrences(occurrence_id TEXT PRIMARY KEY,number INTEGER,status TEXT,result_json TEXT)')
        con.execute('CREATE TABLE sources(path TEXT PRIMARY KEY,sha256 TEXT,physical INTEGER)')
        con.execute('CREATE TABLE markers(file TEXT,char_start INTEGER,char_end INTEGER,number INTEGER,role TEXT,result_json TEXT,PRIMARY KEY(file,char_start,char_end))')
        con.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT)')
        con.executemany('INSERT INTO sources VALUES(?,?,?)',
            [(r['path'], r['sha256'], r['physical']) for r in result['sources']])
        con.executemany('INSERT INTO markers VALUES(?,?,?,?,?,?)',
            [(r['file'], r['char_start'], r['char_end'], r['number'], r['role'], json.dumps(r, ensure_ascii=False))
             for r in result['markers']])
        con.executemany('INSERT INTO metadata VALUES(?,?)',
            [('version', json.dumps(VERSION)), ('profile', json.dumps(profile, ensure_ascii=False)),
             ('summary', json.dumps(result['summary']))])
        con.executemany('INSERT INTO numbers VALUES(?,?,?)',
            [(r['number'], r['status'], json.dumps(r, ensure_ascii=False)) for r in result['numbers']])
        con.executemany('INSERT INTO occurrences VALUES(?,?,?,?)',
            [(r['occurrence_id'], r['number'], r['status'], json.dumps(r, ensure_ascii=False))
             for r in result.get('occurrences', [])])
        con.commit()
    finally:
        con.close()
    lines = ['# Mapa experimental por seção', '', 'Somente candidatos. Nenhuma promoção automática.', '',
             f"Perfil: `{profile['id']}`. Versão: `{VERSION}`.", '',
             '| Número | Estado | Arquivos do marcador | Janela física |', '|---|---|---|---|']
    for r in result['numbers']:
        lines.append(f"| {r['number']} | {r['status']} | {r['marker_files']} | {[f['physical'] for f in r['files']]} |")
    lines += ['', 'A janela inclui o arquivo com N+1 para preservar continuação anterior ao próximo marcador.',
              'Não certifica o parágrafo grego paralelo, o lema, nem todas as ocorrências do índice.',
              'Amostra visual pendente: ' + str(sample)]
    (args.out / 'report.md').write_text('\n'.join(lines) + '\n')
    return result['summary']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=ROOT / 'teste')
    parser.add_argument('--audit', type=Path)
    parser.add_argument('--seed', type=int, default=109)
    parser.add_argument('--sample', type=int, default=3)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args), ensure_ascii=False, indent=2))
