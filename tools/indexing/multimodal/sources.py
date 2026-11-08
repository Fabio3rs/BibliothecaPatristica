"""Physical pairing, lossless OCR anchors and explicitly measured image regions."""
from bisect import bisect_right
from pathlib import Path
import re

from PIL import Image
from tools.ocr_xml_utils import _BLOCK_RE, _ATTR_RE, parse_ocr_xml_page
from tools.indexing.alphabetical_mechanical_analysis import detect_heading, detect_notations
from .contract import PILOT_DECLARATION_CONTEXT, digest

from .pairing import locate_page_image, parse_physical_sequence as sequence


def find_ocr(root, volume, number):
    directory = (Path(root) / volume / 'text').resolve()
    matches = sorted({*directory.glob(f'*-{number:03d}.txt'), *directory.glob(f'*-{number}.txt')})
    if len(matches) != 1:
        raise ValueError(f'OCR pairing requires exactly one file: {volume}/{number}: {len(matches)}')
    if not matches[0].resolve().is_relative_to(directory):
        raise ValueError('OCR symlink escapes volume')
    return matches[0]


def read_page(path):
    raw_bytes = path.read_bytes()
    raw = raw_bytes.decode('utf-8')
    parsed = parse_ocr_xml_page(raw)
    pid = f'{path.parent.parent.name}:physical:{sequence(path)}:{digest(raw_bytes)[:12]}'
    line_starts = [0] + [m.end() for m in re.finditer('\n', raw)]
    blocks, lines, covered = [], [], []
    matches = list(_BLOCK_RE.finditer(raw))
    spans = [(m.start('content'), m.end('content'), dict(_ATTR_RE.findall(m['attrs']))) for m in matches]
    # XML notes/content outside <bloco> are retained as separate orphan blocks.
    cursor = 0
    for m in matches:
        if m.start() > cursor:
            spans.append((cursor, m.start(), {'tipo': 'outside_block'}))
        cursor = m.end()
    if cursor < len(raw):
        spans.append((cursor, len(raw), {'tipo': 'outside_block'}))
    for a, b, attrs in sorted(spans):
        bid = f'{pid}:b{len(blocks)+1}'
        bids = []
        for match in re.finditer(r'[^\n]+', raw[a:b]):
            chunk = match.group()
            # Preserve literal text and character offsets, without collapsing spaces,
            # unescaping XML or joining hyphens. Separate inline markup text runs.
            for text_match in re.finditer(r'[^<>]+(?=<|$)', chunk):
                start = a + match.start() + text_match.start()
                end = a + match.start() + text_match.end()
                if chunk[:text_match.start()].count('<') != chunk[:text_match.start()].count('>'):
                    continue
                if not raw[start:end].strip():
                    continue
                while raw[start:start+1] in (' ', '\t', '\r'):
                    start += 1
                while end > start and raw[end-1:end] in (' ', '\t', '\r'):
                    end -= 1
                lid = f'{bid}:l{len(bids)+1}'
                text = raw[start:end]
                lines.append({'id': lid, 'page_id': pid, 'block_id': bid,
                              'file_line': bisect_right(line_starts, start),
                              'char_start': start, 'char_end': end, 'text': text,
                              'heading_hint': detect_heading(text), 'notations': detect_notations(text)})
                bids.append(lid)
                covered.append((start, end))
        if bids:
            blocks.append({'id': bid, 'type': attrs.get('tipo', ''), 'script': attrs.get('script', ''),
                           'bbox_raw': attrs.get('bbox'), 'bbox_space': 'unverified_ocr_coordinates',
                           'line_ids': bids})
    return {'page_id': pid, 'volume': path.parent.parent.name, 'physical_sequence': sequence(path),
            'ocr_file': str(path.resolve()), 'ocr_sha256': digest(raw_bytes), 'ocr_xml': raw,
            'parse_ok': parsed.parse_ok, 'blocks': blocks, 'lines': lines, 'regions': []}


def add_images(page, out, crops=False):
    pairing = locate_page_image(Path(page['ocr_file']))
    page['image_pairing'] = pairing
    if pairing['status'] != 'found':
        raise ValueError(f"image_{pairing['status']}: {page['ocr_file']}")
    path = Path(pairing['image_path'])
    if not path.is_relative_to(Path(page['ocr_file']).parent.parent / 'images'):
        raise ValueError('image escapes sibling images directory')
    with Image.open(path) as im:
        im.load()
        width, height = im.size
        full = {'id': page['page_id'] + ':full', 'path': str(path), 'sha256': digest(path.read_bytes()),
                'width': width, 'height': height, 'original_width': width, 'original_height': height,
                'crop_box': [0, 0, width, height], 'transformation': 'none',
                'pairing_verified_visually': False}
        page['regions'].append(full)
        if crops:
            # Fixed overlapping halves are measured in native pixels. We deliberately
            # do not interpret OCR bboxes as pixels or claim these are detected columns.
            for label, box in [('left', (0, 0, min(width, int(width*.55)), height)),
                               ('right', (int(width*.45), 0, width, height))]:
                crop = im.crop(box)
                target = out / 'crops' / f"{page['volume']}-{page['physical_sequence']}-{full['sha256'][:12]}-{label}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    crop.save(target)
                page['regions'].append({**full, 'id': page['page_id'] + ':' + label,
                    'path': str(target.resolve()), 'sha256': digest(target.read_bytes()),
                    'width': crop.width, 'height': crop.height, 'crop_box': list(box),
                    'transformation': 'native_pixel_crop_overlapping_half', 'parent_sha256': full['sha256']})
    return page


def edge_lines(page):
    types = {b['id']: b['type'] for b in page['blocks']}
    return [l for l in page['lines'] if types[l['block_id']] not in
            {'outside_block', 'cabecalho', 'rodape', 'nota', 'aparato_critico'}
            and l['text'].strip() != 'Digitized by Google']


def neighbor_image_reasons(primary, context):
    """Bounded OCR heuristics; decisions are clues, never continuity confirmation."""
    direction = context['physical_sequence'] - primary['physical_sequence']
    lines, neighbor = edge_lines(primary), edge_lines(context)
    if abs(direction) != 1 or not lines or not neighbor:
        return []
    reasons = []
    if direction < 0:
        first = lines[0]['text'].lstrip()
        if first[:1].islower() or re.match(r'^[\d,;.(]', first):
            reasons.append('primary_starts_with_possible_fragment')
        if not any(l['heading_hint'] for l in primary['lines'][:8]) and any(l['heading_hint'] for l in context['lines']):
            reasons.append('previous_page_may_supply_section_heading')
        if re.search(r'[-,;:]$', neighbor[-1]['text'].rstrip()):
            reasons.append('previous_page_ends_with_open_text')
    else:
        if re.search(r'[-,;:]$', lines[-1]['text'].rstrip()):
            reasons.append('primary_ends_with_open_text')
        first = neighbor[0]['text'].lstrip()
        if (first[:1].islower() or re.match(r'^[\d,;.(]', first)) and not neighbor[0]['heading_hint']:
            reasons.append('next_page_starts_with_possible_fragment')
    return reasons


def prepare_task(root, volume, number, out, mode, neighbors=1, context_images='auto', line_selection=None):
    primary = read_page(find_ocr(root, volume, number))
    primary['role'] = 'primary'
    primary['fragment_only'] = line_selection is not None
    if line_selection is not None:
        keep = set(line_selection)
        if not keep or not keep <= {l['id'] for l in primary['lines']}:
            raise ValueError('fragment source IDs changed or empty')
        primary['lines'] = [l for l in primary['lines'] if l['id'] in keep]
        primary['ocr_xml'] = '\n'.join(l['text'] for l in primary['lines'])
        primary['blocks'] = [{**b, 'line_ids': [lid for lid in b['line_ids'] if lid in keep]}
                             for b in primary['blocks'] if keep.intersection(b['line_ids'])]
    if mode != 'ocr-only':
        add_images(primary, out, mode == 'ocr-image-crops')
    pages = [primary]
    context_numbers = {number+offset for offset in range(-neighbors, neighbors+1) if offset and number+offset > 0}
    declarations = PILOT_DECLARATION_CONTEXT.get((volume, number), []) if neighbors else []
    context_numbers.update(declarations)
    for context_number in sorted(context_numbers):
        try:
            path = find_ocr(root, volume, context_number)
        except ValueError as exc:
            if str(exc).endswith(': 0'):
                continue
            raise
        context = read_page(path)
        context['role'] = 'context'
        context['context_reason'] = ('candidate editorial declaration; verify section scope before inheritance'
                                    if context_number in declarations else 'adjacent physical page')
        policy = {True: 'always', False: 'never'}.get(context_images, context_images)
        reasons = neighbor_image_reasons(primary, context)
        needed = policy == 'always' or (policy == 'auto' and bool(reasons))
        decision = {'policy': policy, 'needed': needed, 'reasons': reasons, 'status': 'not_requested'}
        if needed and mode != 'ocr-only':
            try:
                add_images(context, out)
                decision['status'] = 'included'
            except ValueError as exc:
                decision.update(status='unavailable', error=str(exc))
        elif mode == 'ocr-only':
            decision['status'] = 'ocr_only'
        context['context_image_decision'] = decision
        pages.append(context)
    return {'task_id': f'{volume}-{number:04d}-{mode}', 'volume': volume, 'mode': mode,
            'primary_page_id': primary['page_id'], 'pages': pages,
            'scope': 'fragment' if line_selection else 'physical_page',
            'context_policy': {'neighbors': neighbors, 'context_images': context_images}}


def check_sources(task):
    for page in task['pages']:
        if digest(Path(page['ocr_file']).read_bytes()) != page['ocr_sha256']:
            raise ValueError('OCR changed after preparation')
        for region in page['regions']:
            if digest(Path(region['path']).read_bytes()) != region['sha256']:
                raise ValueError('image changed after preparation')
