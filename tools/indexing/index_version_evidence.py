"""Bounded, read-only OCR reading comparisons for individual index fields.

Different readings are retrieval alternatives, never automatic OCR corrections.
Image bytes are provenance; known source replacements require explicit review.
"""
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from scripts.audit_po015_names_lexical import history_page_identity
from tools.indexing.editorial_pattern_history import locations
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.ocr_variant_audit import tokens

VERSION = 'index-local-version-evidence-v1'


def _digest(text):
    return sha256(text.encode('utf-8')).hexdigest()


def resolve_quote(raw, quote, span=None):
    """An unchanged local quote survives unrelated OCR edits elsewhere on a page."""
    if not quote:
        return None
    if span and len(span) == 2:
        a, b = span
        if 0 <= a < b <= len(raw) and raw[a:b] == quote:
            return [a, b]
    start = raw.find(quote)
    if start >= 0 and raw.find(quote, start + 1) < 0:
        return [start, start + len(quote)]
    return None


def align_field(current, alternative, span, current_tokens=None, alternative_tokens=None):
    """Require unique normalized flanks; naked matching numbers are insufficient."""
    left = tokens(current) if current_tokens is None else current_tokens
    right = tokens(alternative) if alternative_tokens is None else alternative_tokens
    a, b = span
    selected = [i for i, item in enumerate(left)
                if a <= item['span'][0] and item['span'][1] <= b]
    if not selected:
        return {'status': 'not_aligned'}
    first, last = selected[0], selected[-1] + 1
    source_values = [item['token'] for item in left]
    target_values = [item['token'] for item in right]
    # Four-token anchors first, then three; both must be unique on both pages.
    for width in (4, 3):
        if first < width or last + width > len(left):
            continue
        before, after = source_values[first-width:first], source_values[last:last+width]
        if len(locations(source_values, before)) != 1 or len(locations(source_values, after)) != 1:
            continue
        starts, ends = locations(target_values, before), locations(target_values, after)
        if len(starts) != 1 or len(ends) != 1:
            continue
        x, y = starts[0] + width, ends[0]
        if not 0 <= y-x <= max(20, 3*(last-first)):
            continue
        # Preserve punctuation between anchors, even for an omitted reading.
        lo, hi = right[x-1]['span'][1], right[y]['span'][0]
        old, new = source_values[first:last], target_values[x:y]
        old_numbers = [value for value in old if value.isdigit()]
        new_numbers = [value for value in new if value.isdigit()]
        status = ('numeric_reading_disagreement' if old_numbers != new_numbers else
                  'lexical_reading_disagreement' if old != new else 'same_normalized_reading')
        return {'status': status, 'alternative_span': [lo, hi],
                'alternative_quote': alternative[lo:hi], 'left_anchor': before,
                'right_anchor': after, 'current_tokens': old, 'alternative_tokens': new,
                'current_numbers': old_numbers, 'alternative_numbers': new_numbers,
                'possible_omission': not new and bool(old),
                'automatic_repair_allowed': False}
    return {'status': 'not_aligned', 'automatic_repair_allowed': False}


def read_page_versions(source, volume, physical, versions_db, max_versions=8, image_path=None):
    """Read a bounded page history; admissible versions carry text_content.

    Quarantined source generations retain metadata but expose no text_content.
    Callers may use admitted text as alternative search material, never as gold.
    """
    if max_versions < 1:
        raise ValueError('max_versions must be positive')
    source = Path(source)
    raw = source.read_text(encoding='utf-8')
    if image_path is None:
        image_dir = source.parent.parent / 'images'
        image_path = next((path for path in (image_dir / f'{volume}-{physical:03d}.png',
                                              image_dir / f'{volume}-{physical}.png') if path.exists()), None)
    image_path = Path(image_path) if image_path else None
    image_hash = sha256(image_path.read_bytes()).hexdigest() if image_path and image_path.exists() else None
    result = {'version': VERSION, 'source': str(source.resolve()), 'volume': volume,
              'physical': physical, 'current_sha256': _digest(raw),
              'image_sha256': image_hash, 'max_versions': max_versions,
              'sources_opened_readonly': True, 'automatic_repairs': 0, 'versions': []}
    with closing(readonly(versions_db)) as db:
        db.execute('BEGIN')
        result['available_versions'] = db.execute(
            'SELECT count(*) FROM ocr_results WHERE volume_id=? AND page_num=?',
            (volume, physical)).fetchone()[0]
        rows = db.execute("""SELECT r.id,r.volume_id,r.page_num,r.image_hash,r.image_path,
            r.text_hash,r.text_content,r.is_current,r.created_at,r.engine_version_id,
            e.engine,e.model FROM ocr_results r LEFT JOIN ocr_engine_versions e
            ON e.id=r.engine_version_id WHERE volume_id=? AND page_num=?
            ORDER BY r.is_current DESC,r.id DESC LIMIT ?""", (volume, physical, max_versions))
        for row in rows:
            meta = dict(row)
            alternative = meta.pop('text_content')
            meta['computed_text_sha256'] = _digest(alternative)
            meta['text_matches_current'] = alternative == raw
            meta['recorded_text_hash_matches'] = meta['computed_text_sha256'] == meta['text_hash']
            identity = history_page_identity(volume, physical, meta['image_hash'], image_hash)
            meta['page_identity'] = identity
            eligible = not identity.startswith('excluded_') and identity != 'replacement_source_needs_review'
            meta['comparison_status'] = 'compared' if eligible else 'source_generation_review_required'
            if eligible:
                meta['text_content'] = alternative
            result['versions'].append(meta)
    result['versions_truncated'] = result['available_versions'] > len(result['versions'])
    return result


def audit_local_readings(source, volume, physical, regions, versions_db,
                         max_versions=8, image_path=None):
    """Compare owner/reference quotes in regions against bounded OCR history.

    regions: [{id, owner_quote, owner_span, reference_quote, reference_span}].
    Spans are Python character offsets in the current raw OCR. A stale span can
    be recovered only when its literal quote is unique. No field is inferred.
    Results contain no full OCR copies and no source/database writes occur.
    """
    result = read_page_versions(source, volume, physical, versions_db, max_versions, image_path)
    raw = Path(source).read_text(encoding='utf-8')
    if _digest(raw) != result['current_sha256']:
        raise ValueError('Current OCR changed while reading history; retry this page')
    current_tokens = tokens(raw)
    fields = []
    for region in regions:
        for field in ('owner', 'reference'):
            quote = region.get(f'{field}_quote')
            if not quote:
                continue
            span = resolve_quote(raw, quote, region.get(f'{field}_span'))
            fields.append({'id': region['id'], 'field': field, 'current_quote': quote,
                           'current_span': span,
                           'source_status': 'located' if span else 'current_quote_ambiguous_or_missing'})
    result.update(fields=fields, checks=[])
    for meta in result['versions']:
        alternative = meta.pop('text_content', None)
        if alternative is None:
            continue
        alternative_tokens = tokens(alternative)
        for field in fields:
            check = {'id': field['id'], 'field': field['field'], 'version_id': meta['id'],
                     'engine': meta['engine'], 'model': meta['model'],
                     'page_identity': meta['page_identity'], 'alternative_sha256': meta['computed_text_sha256'],
                     'current_quote': field['current_quote'], 'current_span': field['current_span']}
            check.update(align_field(raw, alternative, field['current_span'], current_tokens,
                                     alternative_tokens) if field['current_span'] else
                         {'status': 'current_quote_ambiguous_or_missing'})
            result['checks'].append(check)
    result['interpretation'] = ('Disagreement is evidence of alternate OCR readings, not proof of an OCR '
                                'error or its correct reading. Not aligned does not establish omission.')
    return result
