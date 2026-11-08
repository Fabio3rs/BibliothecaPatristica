"""Compare independent visual transcriptions without treating agent readings as human gold."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from tools.indexing.editorial_regions_experiment import sha
from tools.indexing.entry_parser_bulk import readonly


def normalized(text):
    """Only markup, whitespace, soft wraps and Latin ligature spelling; never digits."""
    text = re.sub(r'<[^>]*>|-\s*\n\s*', '', text)
    return ''.join(text.split()).replace('æ','ae').replace('Æ','AE').replace('œ','oe').replace('Œ','OE')


def compare(reference, raw, parsed):
    if sha(raw.encode()) != reference['ocr_sha256']:
        raise ValueError('Visual reference OCR hash mismatch')
    if parsed and parsed['sha256'] != reference['ocr_sha256']:
        raise ValueError('Parser source hash mismatch')
    if not reference.get('complete_region'):
        return {'status': 'incomplete_visual_transcription', 'gold_eligible': False, 'items': []}
    region = reference['region']
    compact = normalized(raw)
    start = normalized(region['start_anchor_ocr'])
    end = normalized(region['end_anchor_ocr'])
    if compact.count(start) != 1 or compact.count(end) != 1:
        return {'status': 'ambiguous_visual_region_anchors', 'gold_eligible': False, 'items': []}
    a, b = compact.index(start), compact.index(end) + len(end)
    if b <= a:
        raise ValueError('Reversed visual region')
    source = compact[a:b]
    entries = parsed['entries'] if parsed else []
    candidates = []
    for e in entries:
        left, right = e['char_start'], e['char_end']
        if sha(raw[left:right].encode()) != e['text_sha256']:
            raise ValueError('Invalid candidate span in visual comparison')
        x, y = len(normalized(raw[:left]))-a, len(normalized(raw[:right]))-a
        if x < len(source) and y > 0:
            candidates.append((x, y, e, normalized(raw[left:right])))
    items, cursor = [], 0
    for e in reference['entries']:
        literal = e.get('entry_raw')
        if not literal or e.get('uncertainties'):
            items.append({'ordinal': e['ordinal'], 'status': 'uncertain_visual_reading_excluded',
                          'entry_raw': literal, 'uncertainties': e.get('uncertainties', [])})
            continue
        text = normalized(literal)
        x = source.find(text, cursor)
        if x < 0:
            items.append({'ordinal': e['ordinal'], 'status': 'visual_ocr_disagreement_or_omission',
                          'entry_raw': literal, 'parser_page_present': parsed is not None})
            continue
        y = x + len(text)
        cursor = y
        touched = [(lo, hi, c, norm) for lo,hi,c,norm in candidates if lo < y and hi > x]
        exact = [c for lo,hi,c,norm in touched if norm == text and lo == x and hi == y]
        if parsed is None:
            status = 'page_not_in_parser_run'
        elif len(exact) == 1 and len(touched) == 1:
            status = 'exact_segment_match'
        elif len(touched) > 1:
            status = 'split_or_overlapping_candidates'
        elif touched and touched[0][0] <= x and touched[0][1] >= y:
            status = 'contained_in_larger_candidate'
        else:
            status = 'partially_consumed' if touched else 'omitted_by_parser'
        items.append({'ordinal': e['ordinal'], 'entry_raw': literal, 'status': status,
                      'parser_candidates': [{'candidate_id': c['id'], 'entry_raw': c['entry_raw'],
                                             'references_raw': c.get('references_raw')} for _,_,c,_ in touched]})
    counts = Counter(i['status'] for i in items)
    return {'status': 'compared', 'volume': reference['volume'], 'physical': reference['physical_page'],
            'reference_type': 'independent_agent_visual_reading_not_human_gold', 'gold_eligible': False,
            'parser_page_present': parsed is not None, 'transcribed_units': len(items),
            'counts': dict(counts), 'items': items,
            'interpretation': 'Strict literal/segmentation comparison, not human-validated precision or recall.'}


def run(reference_files, parser_db, parser_run, out):
    if out.exists():
        raise ValueError('Use a new output file')
    con = readonly(parser_db)
    results = []
    try:
        con.execute('BEGIN')
        for file in reference_files:
            ref = json.loads(file.read_text())
            ocr, image = Path(ref['ocr_path']), Path(ref['image_path'])
            if sha(image.read_bytes()) != ref['image_sha256']:
                raise ValueError('Visual image hash mismatch')
            row = con.execute('''SELECT t.result_json FROM tasks t JOIN run_pages p USING(fingerprint)
                                 WHERE p.run_id=? AND p.source=?''', (parser_run, str(ocr.resolve()))).fetchone()
            parsed = json.loads(row[0]).get('page') if row else None
            result = compare(ref, ocr.read_bytes().decode(), parsed)
            results.append({'reference_file': str(file), 'reference_sha256': sha(file.read_bytes()), **result})
    finally:
        con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
    return [{k:v for k,v in r.items() if k != 'items'} for r in results]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference', type=Path, action='append', required=True)
    p.add_argument('--parser-db', type=Path, default=Path('data/index_entry_parser/experiments.sqlite'))
    p.add_argument('--parser-run', required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(args.reference,args.parser_db,args.parser_run,args.out),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
