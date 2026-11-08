"""Conservative PO index grammars. Literal staging only; never resolve target files."""
from __future__ import annotations

import re
import unicodedata

from scripts.scan_editorial_ocr import BLOCK, ATTR
from tools.indexing.entry_parser_experiment import digest, display, source_span

VERSION = 'po-literal-v1'
SUB = '₀₁₂₃₄₅₆₇₈₉₋'
LEADER = re.compile(r'(?:\.[ \t]*){3,}|…{2,}')
DIGIT = re.compile(r'[0-9]')
DASH = re.compile(r'^[—–]')
QUAL = re.compile(r'^(?:cf\.|voir\b|v\.)\s*', re.I)
# Match cross-page underscore intervals before single page/subscript tokens.
TOKEN = re.compile(
    rf'(?P<cross>[0-9]+_[0-9]+-[0-9]+_[0-9]+)|'
    rf'(?P<page>[0-9]+)(?P<sub>[{SUB}]+(?:[ \t]+[{SUB}]+)*|_[0-9]+(?:-[0-9]+)*)?'
    rf'(?P<note>[ \t]*n(?:ote)?\.?[ \t]*[0-9]+)?', re.I)
NOTE = re.compile(r'n(?:ote)?\.?[ \t]*[0-9]+', re.I)


def family(title):
    title = display(title).upper()
    if re.search(r'\b(?:ERRATA|ADDENDA|CORRIGENDA|ORDO RERUM)\b', title):
        return 'excluded_editorial'
    if 'TABLE ANALYTIQUE' in title or 'TABLE DES MATIÈRES' in title or 'TABLE DU TOME' in title:
        return 'excluded_contents'
    if 'CITATIONS' in title and ('BIBLIQUES' in title or 'CRITURE' in title):
        return 'po-biblical-leaders'
    if 'TABLE DES NOMS' in title or 'TABLE DES MOTS' in title:
        return 'po-names-lines'
    if 'INDEX TO THE LETTERS' in title:
        return 'po-names-lists'
    if 'TABLE ALPHABÉTIQUE DES MATIÈRES' in title or 'CALENDRIER' in title or 'MÉNOLOG' in title:
        return 'unsupported_calendar'
    return None


def lines(body, origin):
    offset = origin
    for line in body.splitlines(keepends=True):
        value = line.strip()
        left = len(line) - len(line.lstrip())
        if value:
            yield value, offset + left, offset + left + len(value)
        else:
            yield '', offset, offset
        offset += len(line)


def is_letter(value):
    letters = [ch for ch in value if unicodedata.category(ch).startswith('L')]
    return len(letters) == 1 and all(ch.isalpha() or unicodedata.category(ch).startswith('M') or ch == '.' for ch in value)


def split_name(value):
    """First ASCII digit outside parentheses; do not flatten sub/superscripts."""
    depth = 0
    for i, ch in enumerate(value):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif ch in '0123456789' and not depth:
            lhs = value[:i].rstrip()
            q = re.search(r'\b(?:cf\.|voir|v\.)\s*$', lhs, re.I)
            start = q.start() if q else i
            name = value[:start].rstrip(' ,.;:\r\n\t')
            if name and any(c.isalpha() for c in name):
                return name, start
            return None
    return None


def locator_parts(refs, base, raw, style):
    """Return traceable syntax, never claim an unproved page/line convention."""
    parts, pending = [], []
    if style == 'opaque':
        # Commas may mean page+line(s), lists, or damaged separators; keep each
        # semicolon group whole, including embedded remissions and superscripts.
        offset = 0
        for group in refs.split(';'):
            left = len(group) - len(group.lstrip())
            value = group.strip()
            if value:
                parts.append({'kind': 'numeric_group_uninterpreted', 'raw': value,
                              'page_raw': None, 'subscript_raw': None, 'note_raw': None,
                              **source_span(raw, base + offset + left, base + offset + left + len(value))})
            offset += len(group) + 1
        return parts, ['locator_semantics_unverified']
    pos, qualifier = 0, None
    while pos < len(refs):
        if refs[pos].isspace() or refs[pos] in ',;.':
            pos += 1
            continue
        q = QUAL.match(refs[pos:])
        if q:
            qualifier = q.group().strip()
            pos += q.end()
            continue
        match = TOKEN.match(refs, pos)
        note = NOTE.match(refs, pos)
        if note:
            end = note.end()
            fields = {'kind': 'unattached_note', 'page_raw': None, 'subscript_raw': None, 'note_raw': note.group()}
            pending.append('note_attachment_requires_review')
        elif match:
            end = match.end()
            if match['cross']:
                fields = {'kind': 'compound_locator_range', 'page_raw': None, 'subscript_raw': None, 'note_raw': None}
                pending.append('compound_range_preserved')
            else:
                sub = match['sub']
                fields = {'kind': 'page_subscript' if sub else 'page_note' if match['note'] else 'page',
                          'page_raw': match['page'], 'subscript_raw': sub, 'note_raw': match['note']}
                if sub:
                    pending.append('subscript_semantics_unverified')
                    if sub.endswith(('₋', '-')):
                        pending.append('open_subscript_range')
            # Plain range in a page-list: keep both endpoints in one occurrence.
            if style == 'pages' and not fields['subscript_raw']:
                tail = re.match(r'[-–]\s*[0-9]+', refs[end:])
                if tail:
                    end += tail.end()
                    fields.update(kind='page_range', page_raw=None)
        else:
            pending.append('unparsed_locator_tail')
            parts.append({'kind': 'unresolved', 'raw': refs[pos:], 'page_raw': None,
                          'subscript_raw': None, 'note_raw': None,
                          **source_span(raw, base + pos, base + len(refs))})
            break
        parts.append(dict(fields, raw=refs[pos:end], qualifier_raw=qualifier,
                          **source_span(raw, base + pos, base + end)))
        qualifier = None
        pos = end
    if qualifier:
        pending.append('dangling_locator_qualifier')
    return parts, sorted(set(pending))


def parse_po_page(raw, *, source='memory', physical=None, volume='PO022', context=None):
    if not volume.startswith('PO'):
        raise ValueError('PO profile requires a PO volume')
    entries, residuals, events, rules = [], [], [], []
    active, section, letter = None, None, None
    eligible = consumed = 0
    sha = digest(raw.encode())
    context = context or []

    def context_family(body):
        # Require a real preceding title, same file series (checked by the
        # inventory), and local tabular evidence. This is never a global rule.
        previous = next((c for c in context if c['relation'] == 'previous'), None)
        if not previous:
            return None
        names = {family(t) for t in previous['titles']} - {None}
        values = [v for v, _, _ in lines(body, 0) if v and not is_letter(v)]
        if len(values) < 3:
            return None
        if 'po-biblical-leaders' in names and sum(bool(LEADER.search(v)) for v in values) / len(values) >= .8:
            return 'po-biblical-leaders'
        following = next((c for c in context if c['relation'] == 'next'), None)
        if following and 'po-names-lines' in names and any(family(t) == 'po-names-lines' for t in following['titles']):
            recognized = sum(bool(split_name(v)) or bool(re.match(r'^[0-9]', v)) for v in values)
            if recognized / len(values) >= .8:
                return 'po-names-lines'
        return None

    def residual(start, end, reason, block):
        if raw[start:end].strip():
            residuals.append({'raw': raw[start:end], 'reason': reason, 'block': block,
                              **source_span(raw, start, end)})

    def emit(start, end, ref_start, concept, block, parent, entry_type='material', style='opaque'):
        nonlocal consumed
        entry_raw = raw[start:end]
        refs = raw[ref_start:end] if ref_start is not None else None
        flags = ['experimental_po_profile']
        if section == 'neighbor_title_candidate':
            flags.append('ownership_inferred_from_neighbor_titles')
        if DASH.match(concept):
            flags.append('explicit_repetition_requires_review')
            if parent is None:
                flags.append('missing_parent_context')
        if refs and refs.rstrip().endswith((',', ';', '-', '₋')):
            flags.append('possible_trailing_continuation')
        parts, warnings = locator_parts(refs, ref_start, raw, style) if refs else ([], [])
        flags += warnings
        key = digest(f'{source}:{sha}:{start}:{end}'.encode())[:20]
        record = {'id': key, 'source': source, 'physical_page': physical, 'block': block,
                  'section_candidate': section, 'observed_letter': letter, 'letter_status': 'xml_order_unverified',
                  'entry_type': entry_type, 'entry_raw': entry_raw, 'text_raw': concept,
                  'text_display': display(concept), 'references_raw': refs, 'locator_parts': parts,
                  'context_candidate_id': parent if DASH.match(concept) else None,
                  'flags': flags, 'status': 'candidate_only', 'format_profile': active,
                  **source_span(raw, start, end)}
        if ref_start is not None:
            record['reference_span'] = source_span(raw, ref_start, end)
        if entry_type == 'scripture_citation':
            record['scripture_locus_raw'] = concept
            record['flags'].append('scripture_context_not_expanded')
        entries.append(record)
        consumed += sum(not ch.isspace() for ch in entry_raw)
        return key

    for bi, block in enumerate(BLOCK.finditer(raw)):
        attrs = dict(ATTR.findall(block['attrs']))
        kind = attrs.get('tipo', attrs.get('type', ''))
        body, origin = block['body'], block.start('body')
        if kind == 'cabecalho':
            detected = family(body)
            if detected:
                active, section, letter = detected, display(body), None
                events.append({'kind': 'ownership', 'format_profile': active, 'text': body,
                               **source_span(raw, origin, block.end('body'))})
            continue
        if kind == 'nota' and active and not active.startswith(('excluded', 'unsupported')):
            rules.append({'raw': body, 'section_candidate': section, 'interpretation': 'explicit_note_uninterpreted',
                          **source_span(raw, origin, block.end('body'))})
            continue
        if kind == 'nota_marginal':
            if is_letter(body.strip()):
                letter = body.strip()
                events.append({'kind': 'observed_letter', 'letter': letter,
                               **source_span(raw, origin, block.end('body'))})
            continue
        if kind != 'texto_principal':
            if active and kind in {'rodape', 'outro'} and DIGIT.search(body) and re.search(r'[\u0700-\u074f]', body):
                residual(origin, block.end('body'), 'possible_index_text_in_nonbody_block', bi)
            continue
        if '<' in body or '&' in body:
            residual(origin, block.end('body'), 'markup_requires_alignment', bi)
            continue
        if active is None:
            inferred = context_family(body)
            if inferred:
                active, section = inferred, 'neighbor_title_candidate'
                events.append({'kind': 'ownership_context_candidate', 'format_profile': inferred,
                               'evidence': context, 'status': 'requires_review'})
        # No state or inferred inheritance passes between body blocks or pages.
        buffered = []
        parent = None

        def flush():
            nonlocal parent
            if not buffered:
                return
            start, end = buffered[0][1], buffered[-1][2]
            value = raw[start:end]
            found = split_name(value)
            if found:
                concept, rstart = found
                refs = value[rstart:]
                simple_notes = re.fullmatch(r'(?:(?:cf\.\s*)?[0-9]+(?:\s+n(?:ote)?\.?\s*[0-9]+)?[.;\s]*)+', refs, re.I)
                style = 'subscripts' if re.search(r'[₀-₉]|[0-9]_', refs) or simple_notes else 'pages' if active == 'po-names-lists' else 'opaque'
                key = emit(start, end, start + rstart, concept, bi, parent, style=style)
                if not DASH.match(concept):
                    parent = key
            else:
                remission = re.search(r'\b(?:voir|cf\.|v\.)\s+\S', value, re.I)
                if remission and not DIGIT.search(value):
                    emit(start, end, None, value, bi, parent, entry_type='cross_reference')
                else:
                    residual(start, end, 'unclosed_name_or_unsupported_locator', bi)
            buffered.clear()

        for value, start, end in lines(body, origin):
            if not value:
                flush()
                continue
            detected = family(value) if value.isupper() or value.startswith('TABLE ') else None
            if detected:
                flush()
                active, section, letter, parent = detected, value, None, None
                events.append({'kind': 'ownership', 'format_profile': active, 'text': value,
                               **source_span(raw, start, end)})
                continue
            if not active or active.startswith(('excluded', 'unsupported')):
                residual(start, end, active or 'ownership_not_established', bi)
                continue
            if is_letter(value) or value in {'Pages.', 'Pages'}:
                flush()
                letter, parent = value, None
                events.append({'kind': 'observed_letter_or_label', 'text': value, **source_span(raw, start, end)})
                continue
            eligible += sum(not ch.isspace() for ch in value)
            if active == 'po-biblical-leaders':
                leader = LEADER.search(value)
                if not leader:
                    residual(start, end, 'missing_biblical_locator_separator', bi)
                    continue
                ref_offset = leader.end()
                while ref_offset < len(value) and value[ref_offset].isspace():
                    ref_offset += 1
                if ref_offset == len(value) or not DIGIT.match(value[ref_offset:]):
                    residual(start, end, 'unsupported_biblical_locator', bi)
                    continue
                concept = value[:leader.start()].rstrip(' .')
                key = emit(start, end, start + ref_offset, concept, bi, parent,
                           entry_type='scripture_citation', style='opaque')
                if not DASH.match(concept):
                    parent = key
                continue
            numeric_continuation = bool(re.match(r'^(?:[0-9]|n\.?\s*[0-9]|cf\.\s*[0-9])', value, re.I))
            if numeric_continuation:
                if buffered:
                    buffered.append((value, start, end))
                else:
                    residual(start, end, 'orphan_locator_continuation', bi)
                continue
            # Name-wrapping without references is accepted only while the previous
            # line ends in a comma/hyphen; uncertain textual continuations stay residual.
            if buffered and not split_name(raw[buffered[0][1]:buffered[-1][2]]) and buffered[-1][0].endswith((',', '-')):
                buffered.append((value, start, end))
                continue
            flush()
            buffered.append((value, start, end))
        flush()
    return {'source': source, 'physical_page': physical, 'sha256': sha, 'profile': 'po-auto-v1',
            'entries': entries, 'residuals': residuals, 'events': events, 'editorial_rules': rules,
            'coverage': {'eligible_nonspace_chars': eligible, 'consumed_nonspace_chars': consumed,
                         'syntactic_fraction': consumed / eligible if eligible else None},
            'format_profiles': sorted({e['format_profile'] for e in events if 'format_profile' in e}),
            'import_ready': False}
