"""Offline, bounded fascicle maps and literal right-column links for PO indices.

No database connection: all inputs are current OCR and a reviewed scope profile.
The existing bracketed-header sequence builder supplies candidate pages only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from tools.corpus_utils import page_files, page_number
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.parser_target_experiment import build_header_series, locate_header

VERSION = 'po-section-map-v1'
DOTS = re.compile(r'(?:\.\s*){3,}')
BRACKET = re.compile(r'\[\s*([0-9]{1,4})\s*\]')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def parse_material(raw, notation):
    """Never interpret the left biblical chapter/verse column as pagination."""
    if notation == 'page_line':
        match = re.fullmatch(r'\s*([0-9]{1,4})\s*,\s*([0-9]+(?:\s*[-–]\s*[0-9]+)?(?:\s*s{1,2}\.)?)\.?\s*', raw)
        if not match or int(match[1]) == 0:
            return None
        line_numbers = [int(n) for n in re.findall(r'[0-9]+', match[2])]
        if not line_numbers[0] or len(line_numbers) == 2 and line_numbers[1] < line_numbers[0]:
            return None
        return [{'page_start': int(match[1]), 'page_end': None,
                 'page_raw': match[1], 'line_raw': match[2]}]
    if notation != 'page_list':
        raise ValueError(f'Unknown notation: {notation}')
    if not re.fullmatch(r'\s*[0-9]+(?:\s*[-–]\s*[0-9]+)?(?:\s*,\s*[0-9]+(?:\s*[-–]\s*[0-9]+)?)*\s*', raw):
        return None
    result = []
    for item in raw.split(','):
        values = [int(n) for n in re.findall(r'[0-9]+', item)]
        if not 0 < values[0] < 10000 or any(n >= 10000 for n in values) or len(values) == 2 and not 0 <= values[1] - values[0] <= 50:
            return None
        result.append({'page_start': values[0], 'page_end': values[1] if len(values) == 2 else None,
                       'page_raw': item.strip(), 'line_raw': None})
    return result


def index_rows(raw, notation):
    for block in blocks(raw):
        if block['kind'] != 'texto_principal':
            continue
        for line in re.finditer(r'[^\n]+', block['body']):
            separators = list(DOTS.finditer(line[0]))
            if not separators:
                continue
            # Multiple dot runs can be OCR fragmentation, never guess the ownership.
            split = separators[0]
            left, right = line[0][:split.start()].strip(), line[0][split.end():].strip()
            parsed = parse_material(right, notation) if len(separators) == 1 else None
            start = block['body_start'] + line.start()
            yield {'source_start': start, 'source_end': start + len(line[0]),
                   'line_raw': line[0], 'owner_raw': left, 'references_raw': right,
                   'material': parsed, 'status': 'parsed_right_column' if parsed else 'notation_pending',
                   'owner_context_status': 'raw_fragment_book_and_chapter_not_reconstructed'}


def check_profile(profile, root):
    checked = []
    if len({s['id'] for s in profile['sections']}) != len(profile['sections']):
        raise ValueError('Duplicate section IDs')
    for section in profile['sections']:
        if section['namespace_status'] not in {'documented_notice', 'contextual_review_required'}:
            raise ValueError('Unknown namespace status')
        start, end = section['index_physical_scope']
        spec = section['series']
        if not 0 < start <= end or not 0 < spec['physical_start'] <= spec['physical_end'] < start:
            raise ValueError('Invalid or overlapping body/index scope')
        if spec['volume'] != section['volume']:
            raise ValueError('This pilot does not support cross-volume routes')
        if section['notation'] not in {'page_line', 'page_list'}:
            raise ValueError('Unknown material notation')
        roles = {item['role'] for item in section['evidence']}
        required = {'title', 'index_opening', 'index_end', 'next_section'}
        if section['namespace_status'] == 'documented_notice':
            required.add('notice')
        if not required <= roles:
            raise ValueError('Missing scope/namespace evidence')
        for proof in section['evidence']:
            path = (root / proof['path']).resolve()
            if not path.is_relative_to((root / section['volume']).resolve()):
                raise ValueError('Evidence outside configured volume')
            expected_physical = {'index_opening': start, 'index_end': end, 'next_section': end + 1}.get(proof['role'])
            if expected_physical is not None and page_number(path) != expected_physical:
                raise ValueError('Scope evidence does not match configured index boundary')
            raw = path.read_text()
            if sha(path) != proof['sha256'] or not proof.get('quote') or raw.count(proof['quote']) != 1:
                raise ValueError(f'Stale or missing profile evidence: {path}')
            if proof.get('image_path'):
                image = (root / proof['image_path']).resolve()
                if not image.is_relative_to((root / section['volume']).resolve()) or sha(image) != proof.get('image_sha256'):
                    raise ValueError(f'Stale visual profile evidence: {image}')
            offset = raw.index(proof['quote'])
            checked.append({**proof, 'path': str(path), 'source_start': offset,
                            'source_end': offset + len(proof['quote'])})
    return checked


def run(profile, root, out):
    root = root.resolve()
    proofs = check_profile(profile, root)
    all_sources = {p['path']: p['sha256'] for p in proofs}
    summaries, maps, links, pending, inventories = [], [], [], [], []
    for section in profile['sections']:
        volume = section['volume']
        grouped = page_files(root / volume / 'text', suffixes=['.txt'])
        spec = section['series']
        a, b = section['index_physical_scope']
        required = set(range(spec['physical_start'], spec['physical_end'] + 1)) | set(range(a, b + 1))
        duplicate = [n for n in required if len(grouped.get(n, [])) > 1]
        if duplicate:
            raise ValueError(f'{volume}: ambiguous physical files: {duplicate}')
        if any(n not in grouped for n in range(a, b + 1)):
            raise ValueError(f'{volume}: missing index source')
        series = build_header_series(spec, root)
        counts = Counter({'index_pages': b - a + 1, 'target_files_read': len(series['pages']),
                          'target_files_missing': len(set(range(spec['physical_start'], spec['physical_end'] + 1)) - set(series['pages']))})
        for path, checksum in series['sources']:
            all_sources[path] = checksum
        for physical, page in series['pages'].items():
            raw = Path(page['file']).read_text()
            evidence = []
            for block in blocks(raw):
                if block['kind'] == 'cabecalho':
                    for m in BRACKET.finditer(block['body']):
                        evidence.append({'number': int(m[1]), 'source_start': block['body_start'] + m.start(),
                                         'source_end': block['body_start'] + m.end(), 'raw': m[0]})
            inventories.append({'section_id': section['id'], 'physical': physical, **page,
                                'raw_header_spans': evidence})
        requested = set()
        rows = []
        for physical in range(a, b + 1):
            source = grouped[physical][0].resolve()
            checksum = sha(source)
            all_sources[str(source)] = checksum
            for record in index_rows(source.read_text(), section['notation']):
                row = {**record, 'section_id': section['id'], 'volume': volume,
                       'source': str(source), 'sha256': checksum, 'physical': physical}
                row['occurrence_id'] = hashlib.sha256(f"{section['id']}:{checksum}:{record['source_start']}".encode()).hexdigest()
                counts['dotted_rows'] += 1
                if row['material'] is None:
                    pending.append(row)
                    counts['notation_pending_rows'] += 1
                    continue
                counts['parsed_rows'] += 1
                for locator in row['material']:
                    requested.update(range(locator['page_start'], (locator['page_end'] or locator['page_start']) + 1))
                rows.append(row)
        numbering = {}
        for number in sorted(requested | set(series['nodes'])):
            record = locate_header(series, number)
            record.update({'section_id': section['id'], 'namespace_status': section['namespace_status'],
                           'requested_by_index': number in requested})
            maps.append(record)
            numbering[number] = record
            if number in requested:
                counts['requested_numbers'] += 1
                counts['number_' + record['status']] += 1
        distinct_files = set()
        for row in rows:
            for order, locator in enumerate(row['material']):
                numbers = list(range(locator['page_start'], (locator['page_end'] or locator['page_start']) + 1))
                candidates = {f['file']: f for n in numbers for f in numbering[n]['files']}
                missing = [n for n in numbers if not numbering[n]['files']]
                ambiguous = [n for n in numbers if len(numbering[n]['files']) > 1]
                status = 'missing_or_partial_map' if missing else 'ambiguous_map' if ambiguous else 'bounded_fascicle_candidate'
                links.append({**row, 'locator_order': order, 'locator': locator,
                              'files': list(candidates.values()), 'missing_numbers': missing,
                              'ambiguous_numbers': ambiguous, 'link_status': status,
                              'namespace_status': section['namespace_status'], 'verified': False})
                counts['material_occurrences'] += 1
                counts['occurrence_' + status] += 1
                distinct_files.update(candidates)
        counts['distinct_candidate_files'] = len(distinct_files)
        summaries.append({'section_id': section['id'], 'volume': volume,
                          'namespace_status': section['namespace_status'], **counts})
    changed = [path for path, checksum in all_sources.items() if sha(Path(path)) != checksum]
    if changed:
        raise ValueError(f'Sources changed during run: {changed}')
    out.mkdir(parents=True, exist_ok=False)
    dump(out / 'profile.json', profile)
    dump(out / 'sources.json', all_sources)
    dump(out / 'scope_evidence.json', proofs)
    for name, records in [('maps', maps), ('links', links), ('notation_pending', pending), ('page_inventory', inventories)]:
        (out / (name + '.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    summary = {'version': VERSION, 'sections': summaries, 'source_files': len(all_sources),
               'map_records': len(maps), 'material_occurrences': len(links),
               'db_updates': 0, 'verified_links': 0, 'inference_calls': 0}
    dump(out / 'summary.json', summary)
    dump(out / 'config.json', {'version': VERSION, 'root': str(root),
         'profile_sha256': hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest(),
         'implementation_sha256': sha(Path(__file__))})
    lines = ['# Mapas por fascículo PO', '', 'Candidatos mecânicos; nenhum vínculo certificado ou banco alterado.', '',
             '| Seção | Páginas lidas | Números pedidos | Números com arquivo único | Ocorrências completas | Pendências de notação |',
             '|---|---:|---:|---:|---:|---:|']
    for s in summaries:
        lines.append(f"| {s['section_id']} | {s['target_files_read']} | {s.get('requested_numbers', 0)} | {s.get('number_header_page_candidate', 0)} | {s.get('occurrence_bounded_fascicle_candidate', 0)} / {s.get('material_occurrences', 0)} | {s.get('notation_pending_rows', 0)} |")
    lines += ['', 'PO016 conserva namespace contextual, sem aviso explícito. Linhas bíblicas mantêm livro/capítulo herdados sem reconstrução. A coluna direita foi isolada; isso não certifica a leitura do OCR nem sua completude.',
              'Não há interpolação pelo nome do arquivo. Página sem marcador apoiado permanece pendente. Os mapas são limitados ao corpo de cada fascículo e não incluem outros fascículos com a mesma paginação.']
    (out / 'report.md').write_text('\n'.join(lines) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path('teste'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(json.loads(args.profile.read_text()), args.root, args.out), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
