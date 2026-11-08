"""Literal PO region grammars; offsets always refer to the unmodified XML.

No filesystem, DB or inference operations. ``scopes`` contains span, section_id
and kind. ``section_context`` maps section IDs to a dict with ``text`` (printed
opening/notation notices) and optional book/chapter evidence from a preceding
page. Returned ``context`` can be passed to the next page of the SAME section.
Numeric references are never resolved here. In particular logia, chapters,
subscript lines and bracketed fascicle pages are distinct namespaces.
"""
from __future__ import annotations

import re
import unicodedata

from scripts.scan_editorial_ocr import ATTR, BLOCK
from tools.indexing.entry_parser_po import LEADER, is_letter, lines, split_name

VERSION = 'missing-po-regions-v3'
BOOK_ABBREVIATIONS = re.compile(r'^(?:I{1,3}\s+)?(?:ROM|COR|GAL|EPH|PHIL|COL|THESSAL|TIM|HEBR|ECCLI|PS|IS|ACT|MATTH)\.$')
SUBSCRIPT_LOCATOR = re.compile(r'(?<!\w)[0-9]+(?:_[0-9]+|[₀₁₂₃₄₅₆₇₈₉]+)')
NUMERIC = re.compile(r'^\[?\d+(?:\s*(?:bis|ter|quater|quinquies))?(?:[\d\s,;.\-–_\[\]]|et|bis|ter|quater|quinquies)*\.?$', re.I)
SCRIPTURE = re.compile(r'^(?:(?P<book>(?![IVXLCDM]+\s*,)[^\d\W][\w .’\-]*?)(?:,\s*|\s+))?(?P<chapter>[IVXLCDM]+|\d+|[—–-]|--)(?:[.,]\s*|\s+)?(?P<verse>[\dIVXLCDM,; .\-–]*?(?:et (?:ss\.?|suiv\.?))?)$', re.I)
PAREN = re.compile(r'\((?P<ref>\d[\d,;\s\-–]*)\)\.?\s*$')
REMIT = re.compile(r'\b(?:Vide(?: etiam)?|voir|cf\.|V\.)\s*:?\s*', re.I)
PAGE_TABLE_TITLE = re.compile(r"^TABLE (?:DES CITATIONS DE L['’]ÉCRITURE|DES MATIÈRES)\s*$", re.I)
BOOKS = re.compile(r'^(?:(?:I{1,3}|IV|[1-4])\s+)?(?:GENESE|EXODE|LEVITIQUE|NOMBRES|DEUTERONOME|JOSUE|JUGES|RUTH|ROIS|PARALIPOMENES|ESDRAS|NEHEMIE|TOBIE|JUDITH|ESTHER|JOB|PSAUMES(?: \(VULGATE\))?|PROVERBES|ECCLESIASTE|ECCLESIASTIQUE|SAGESSE|CANTIQUE(?: DES CANTIQUES)?|ISAIE|JEREMIE|LAMENTATIONS|BARUCH|EZECHIEL|DANIEL|OSEE|JOEL|AMOS|ABDIAS|JONAS|MICHEE|NAHUM|HABACUC|SOPHONIE|AGGEE|ZACHARIE|MALACHIE|MACHABEES|MATTHIEU|MARC|LUC|JEAN|ACTES(?: DES APOTRES)?|ROMAINS|CORINTHIENS|GALATES|EPHESIENS|PHILIPPIENS|COLOSSIENS|THESSALONICIENS|TIMOTHEE|TITE|PHILEMON|HEBREUX|JACQUES|PIERRE|JUDE|APOCALYPSE)$')


def _norm(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.upper()) if not unicodedata.combining(c))


def _notice(text):
    low = text.lower()
    if 'numerus paginarum' in low and 'non vero logiorum' in low:
        return 'page'
    if 'logiorum' in low and 'numeri' in low and 'paginas' in low:
        return 'logion'
    if 'entre parenthèses' in low and 'chapitres' in low:
        return 'chapter'
    if 'pagination mise entre crochets' in low:
        return 'bracketed_page'
    if 'page et à la ligne' in low:
        return 'page_line'
    return None


def extract_page_line_tokens(reference_raw, *, base_offset=0):
    """Parse references ONLY after a printed page/line convention is known.

    Underscores encode page + line; commas at the start of a semicolon/newline
    group encode page + one or more lines. Explicit ``n.``, ``var.`` and
    ``titre`` preserve their distinct semantics. Bare/concatenated numbers and
    dangling commas are pending, never split heuristically (171 != 17 + 1).
    Offsets are relative to the original source when ``base_offset`` is given.
    This helper must not be applied to ordinary page lists.
    """
    tokens, pending = [], []
    patterns = [
        ('subscript', re.compile(r'(?<![\w\d])([0-9]+)([₀₁₂₃₄₅₆₇₈₉]+(?:[-–₋][₀₁₂₃₄₅₆₇₈₉]+)*)')),
        ('underscore', re.compile(r'(?<![\w\d])(\d+)_(\d+(?:-\d+)*)')),
        ('comma', re.compile(r'(?<![\w\d])([0-9]+)\s*,\s*([0-9]+(?:\s*[-–,]\s*[0-9]+)*)')),
        ('note', re.compile(r'(?:^|[;\n])\s*(\d+)\s+n(?:ote)?\.?\s*(\d+)', re.I)),
        ('variant', re.compile(r'(?:^|[;\n])\s*(\d+)\s+(var\.)', re.I)),
        ('title', re.compile(r'(?:^|[;\n])\s*(\d+)\s*,\s*(titre)\b', re.I)),
    ]
    for notation, pattern in patterns:
        for match in pattern.finditer(reference_raw):
            a, b = match.start(1), match.end(2)
            if any(t['span'][0] - base_offset < b and a < t['span'][1] - base_offset for t in tokens):
                continue
            tokens.append({'page_raw': match[1],
                           'line_raw': match[2] if notation in ('underscore', 'comma', 'subscript') else None,
                           'note_raw': match[2] if notation == 'note' else None,
                           'qualifier_raw': match[2] if notation in ('variant', 'title') else None,
                           'raw': reference_raw[a:b], 'notation': notation,
                           'span': [base_offset + a, base_offset + b],
                           'page_span': [base_offset + match.start(1), base_offset + match.end(1)]})
    for group in re.finditer(r'(?:^|[;\n])\s*(\d+)([^;\n]*)', reference_raw):
        a = group.start(1)
        if any(t['span'][0] == base_offset + a for t in tokens):
            continue
        pending.append({'raw': reference_raw[a:group.end()],
                        'span': [base_offset + a, base_offset + group.end()],
                        'reason': 'incomplete_line_locator' if group[2].strip().startswith(',') else 'bare_number_page_line_ambiguous'})
    return {'tokens': sorted(tokens, key=lambda t: t['span']), 'pending': pending}


def coalesce_region_scopes(raw, scopes):
    """Join line scopes across whitespace inside one XML block and section.

    This reconciles a line-based regional inventory with wrapped entries. The
    returned scopes cover exactly the original scopes plus whitespace; they
    never bridge markup, non-whitespace text, a block boundary or a section
    boundary. ``component_spans`` preserves the original evidence intervals.
    Callers must pass this SAME list to extraction and containment validation.
    """
    blocks = [(m.start('body'), m.end('body')) for m in BLOCK.finditer(raw)]

    def block_for(a, b):
        return next((i for i, (lo, hi) in enumerate(blocks) if lo <= a < b <= hi), None)

    result = []
    for scope in sorted(scopes, key=lambda item: (item['span'][0], item['span'][1])):
        a, b = scope['span']
        item = dict(scope, span=[a, b], component_spans=[list(x) for x in scope.get('component_spans', [[a, b]])])
        if result:
            previous = result[-1]
            pa, pb = previous['span']
            block = block_for(a, b)
            if (previous.get('section_id') == scope.get('section_id')
                    and previous.get('kind') == scope.get('kind')
                    and block is not None and block_for(pa, pb) == block
                    and not raw[pb:a].strip()):
                previous['span'][1] = max(pb, b)
                previous['component_spans'].extend(item['component_spans'])
                continue
        result.append(item)
    return result


def parse_region_page(raw, *, source, volume, physical, scopes, section_context=None):
    """Extract traceable entries, residuals and explicit scripture context.

    Missing book/chapter context produces a flag, not guessed inheritance.
    Running headers, notes and XML metadata are not entries. Eligibility comes
    from the supplied editorial regions; unsupported material is returned as
    residuals. Continuations cross physical lines only inside one XML block.
    """
    if not volume.startswith('PO'):
        raise ValueError('PO grammar requires a PO volume')
    contexts = {k: dict(v) if isinstance(v, dict) else {'text': str(v)}
                for k, v in (section_context or {}).items()}
    entries, residuals, pending = [], [], None
    assigned = sorted(scopes, key=lambda s: (s['span'][0], s['span'][1]))

    def context(sid):
        return contexts.setdefault(sid, {})

    def residual(a, b, reason, sid):
        if raw[a:b].strip():
            residuals.append({'span': [a, b], 'raw': raw[a:b], 'reason': reason,
                              'section_id': sid})

    def emit(a, b, owner_end, ref_start, ref_end, scope, namespace, flags=(), scripture=None):
        while owner_end > a and raw[owner_end - 1] in ' \n\r\t,.;:':
            owner_end -= 1
        while ref_start < ref_end and raw[ref_start].isspace():
            ref_start += 1
        while ref_end > ref_start and raw[ref_end - 1].isspace():
            ref_end -= 1
        if owner_end <= a or ref_end <= ref_start:
            residual(a, b, 'incomplete_owner_or_reference', scope['section_id'])
            return
        flags = list(flags)
        if re.search(r'\[(?:ilegivel|illegible|illisible)\]', raw[a:owner_end], re.I):
            flags.append('ocr_owner_placeholder_or_partial')
        if namespace == 'page' and SUBSCRIPT_LOCATOR.search(raw[ref_start:ref_end]):
            # Preserve the typographic subdivision instead of flattening 34₁₂
            # into page 3412 or treating line 12 as another page candidate.
            namespace = 'page_line'
            flags.append('page_subdivision_typographic_candidate_line_semantics_unverified')
        entry = {'source': source, 'volume': volume, 'physical': physical,
                 'section_id': scope['section_id'], 'section_kind': scope.get('kind'),
                 'entry_span': [a, b], 'owner_span': [a, owner_end],
                 'owner_raw': raw[a:owner_end], 'reference_span': [ref_start, ref_end],
                 'reference_raw': raw[ref_start:ref_end], 'reference_namespace': namespace,
                'flags': flags, 'parser': VERSION}
        namespace_evidence = context(scope['section_id']).get('namespace_evidence')
        if namespace_evidence:
            entry['namespace_evidence'] = dict(namespace_evidence)
            if isinstance(namespace_evidence.get('span'), list):
                entry['namespace_evidence']['span'] = list(namespace_evidence['span'])
        if context(scope['section_id']).get('non_scripture_citations'):
            entry['citation_kind'] = 'non_scripture_citation'
        if scripture is not None:
            entry['scripture_context'] = scripture
            entry['label'] = ' '.join(x for x in (scripture.get('book_raw'), scripture.get('chapter_raw'), scripture.get('verse_raw')) if x)
            entry['search_label'] = entry['label']
        if namespace == 'page_line':
            locator_text = entry['reference_raw']
            if context(scope['section_id']).get('family') == 'greek_colon_names':
                gloss = re.search(r'\s+[—–]\s+', locator_text)
                if gloss:
                    entry['external_gloss'] = {'raw': locator_text[gloss.end():],
                                               'span': [ref_start + gloss.end(), ref_end]}
                    locator_text = locator_text[:gloss.start()]
                    entry['flags'].append('external_edition_gloss_excluded_from_local_page_tokens')
            locators = extract_page_line_tokens(locator_text, base_offset=ref_start)
            entry['page_line_tokens'] = locators['tokens']
            entry['page_line_pending'] = locators['pending']
            entry['page_candidates_numbers'] = list(dict.fromkeys(int(p['page_raw']) for p in entry['page_line_tokens']))
        ctx = context(scope['section_id'])
        if ctx.get('work_scope'):
            entry['work_scope'] = dict(ctx['work_scope'])
        if ctx.get('family') == 'mixed_volume_words':
            entry['edition_context'] = {'notice': ctx.get('text', ''),
                                       'ranges': {'PO010': [15, 65], 'PO011': [403, 487]}}
            for token in entry.get('page_line_tokens', []):
                number = int(token['page_raw'])
                token['target_volume_candidate'] = ('PO010' if 15 <= number <= 65
                                                    else 'PO011' if 403 <= number <= 487 else None)
                token['target_scope_basis'] = 'printed_cross_volume_notice'
            entry['flags'].append('cross_volume_notice_ranges_preserved')
        entries.append(entry)

    def flush():
        nonlocal pending
        if pending is None:
            return
        a, b, scope, namespace = pending
        value = raw[a:b]
        split = split_name(value)
        if split:
            owner, pos = split
            flags = ['wrapped_entry'] if '\n' in value else []
            # Dates belong to prose, not to the destination list. Recover an
            # explicit p./pp. locator when present; otherwise keep the row as
            # an opaque editorial expression, never a numeric page claim.
            date_prose = re.match(r'[0-9]{1,2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\b', value[pos:], re.I)
            if date_prose:
                page = re.search(r'\bp{1,2}\.\s*(?P<pages>[0-9]+(?:\s*(?:,|;|et|[-–])\s*[0-9]+)*)', value[pos:], re.I)
                if page:
                    ref_start = pos + page.start('pages'); ref_end = pos + page.end('pages')
                    emit(a, a + ref_end, a + pos + page.start(), a + ref_start,
                         a + ref_end, scope, 'page', flags + ['date_prose_before_explicit_page'])
                    residual(a + ref_end, b, 'prose_after_explicit_page_reference', scope['section_id'])
                    pending = None
                    return
                namespace = 'editorial_locator_unparsed'
                flags.append('date_prose_not_page_reference')
            qualifier = re.search(r'\b[IVX]+\s*,\s*$', value[:pos])
            if qualifier:
                pos = qualifier.start()
                owner = value[:pos].rstrip(' ,.;:')
            if namespace == 'page' and re.search(r'\b[IVX]+\s*,\s*\d', value[pos:]):
                namespace = 'qualified_page'
                flags.append('printed_edition_or_part_qualifier')
            if context(scope['section_id']).get('family') in ('names_lines', 'mixed_volume_words', 'greek_colon_names'):
                if re.search(r'[;—]\s*[^\W\d][^;—]*\d', value[pos:]):
                    flags.append('compound_reference_or_parallel_lemma_preserved')
            if REMIT.search(value[pos:]):
                flags.append('mixed_numeric_and_cross_reference')
            emit(a, b, a + len(owner), a + pos, b, scope, namespace, flags)
        else:
            remit = REMIT.search(value)
            if remit and remit.start():
                emit(a, b, a + remit.start(), a + remit.start(), b, scope, 'cross_reference')
            else:
                residual(a, b, 'no_reference_grammar', scope['section_id'])
        pending = None

    for block in BLOCK.finditer(raw):
        flush()
        attrs = dict(ATTR.findall(block['attrs']))
        kind = attrs.get('tipo', '')
        for value, a, b in lines(block['body'], block.start('body')):
            if not value:
                continue
            # Regional inventories sometimes omit the running/title block
            # while carrying the preceding region into the following page.
            # An explicit new table title still limits inherited notation.
            if kind in ('cabecalho', 'titulo') and PAGE_TABLE_TITLE.fullmatch(value):
                flush()
                for sid in {s['section_id'] for s in assigned if s['span'][1] > b}:
                    ctx = context(sid)
                    ctx['namespace'] = 'page'
                    ctx.pop('book', None)
                    ctx.pop('chapter', None)
                    ctx['namespace_evidence'] = {'source': source, 'span': [a, b], 'raw': value}
            scope = next((s for s in assigned if s['span'][0] <= a and b <= s['span'][1]), None)
            if scope is None:
                flush()
                continue
            sid = scope['section_id']
            ctx = context(sid)
            if pending is not None and pending[2]['section_id'] != sid:
                flush()
            noticed = _notice(value)
            if noticed:
                flush()
                ctx['namespace'] = noticed
                ctx['namespace_evidence'] = {'source': source, 'span': [a, b], 'raw': value}
                residual(a, b, 'notation_notice', sid)
                continue
            if PAGE_TABLE_TITLE.fullmatch(value):
                flush()
                ctx['namespace'] = 'page'
                ctx.pop('book', None)
                ctx.pop('chapter', None)
                ctx['namespace_evidence'] = {'source': source, 'span': [a, b], 'raw': value}
                residual(a, b, 'new_editorial_table_page_namespace', sid)
                continue
            if '|' in value:
                flush()
                residual(a, b, 'parallel_columns_interleaved', sid)
                continue
            if _norm(value) == 'AUTRES CITATIONS':
                flush()
                ctx.pop('book', None)
                ctx.pop('chapter', None)
                ctx['non_scripture_citations'] = True
                residual(a, b, 'non_scripture_citation_boundary', sid)
                continue
            if kind in ('cabecalho', 'rodape', 'nota'):
                flush()
                residual(a, b, 'editorial_header_or_note', sid)
                continue
            namespace = ctx.get('namespace') or _notice(ctx.get('text', '')) or 'page'
            if BOOKS.fullmatch(_norm(value).strip(' .')) or (scope.get('kind') == 'scripture_index' and BOOK_ABBREVIATIONS.fullmatch(_norm(value))):
                flush()
                ctx['book'] = {'raw': value, 'source': source, 'span': [a, b]}
                ctx.pop('chapter', None)
                ctx.pop('non_scripture_citations', None)
                residual(a, b, 'scripture_book_heading', sid)
                continue
            if ctx.get('family') == 'inline_scripture':
                flush()
                cursor = 0
                for match in re.finditer(r'(?P<owner>[^\[\]]+?)\[(?P<ref>[0-9]+(?:[,; -]+[0-9]+)*)\]', value):
                    start = match.start('owner')
                    owner = match['owner']
                    trim = len(owner) - len(owner.lstrip(' .;—–'))
                    start += trim
                    owner = owner[trim:].rstrip(' .,;')
                    parsed = SCRIPTURE.fullmatch(owner)
                    if not parsed:
                        continue
                    if parsed['book']:
                        ctx['book'] = {'raw': parsed['book'], 'source': source,
                                       'span': [a + start + parsed.start('book'), a + start + parsed.end('book')]}
                    book = ctx.get('book', {})
                    chapter = parsed['chapter']
                    evidence = {'raw': chapter, 'source': source,
                                'span': [a + start + parsed.start('chapter'), a + start + parsed.end('chapter')]}
                    scripture = {'book_raw': book.get('raw'), 'book_evidence': book or None,
                                 'chapter_raw': chapter, 'chapter_evidence': evidence,
                                 'verse_raw': parsed['verse'].strip(' ,.')} 
                    flags = [] if book else ['missing_book_context']
                    residual(a + cursor, a + start, 'inline_separator_or_unparsed_fragment', sid)
                    emit(a + start, a + match.end(), a + start + len(owner),
                         a + match.start('ref'), a + match.end('ref'), scope,
                         'bracketed_page', flags, scripture)
                    cursor = match.end()
                residual(a + cursor, b, 'inline_scripture_unparsed_tail', sid)
                continue
            leader = LEADER.search(value)
            if leader and len(list(LEADER.finditer(value))) > 1:
                flush()
                ctx.pop('chapter', None)
                ctx.pop('book', None)
                residual(a, b, 'parallel_columns_interleaved', sid)
                continue
            if not leader and ctx.get('family') == 'scripture_single_period':
                # Only a terminal Arabic destination after a period. Chapter
                # commas and book abbreviation periods are not separators.
                leader = re.search(r'\.\s+(?=[0-9]+(?:\s*[,;]\s*[0-9]+)*\.?$)', value)

            if leader and (namespace == 'logion' or ctx.get('family') in ('names_lines', 'mixed_volume_words', 'greek_colon_names')) and not NUMERIC.fullmatch(value[leader.end():].strip()):
                # Ellipsis inside a quoted incipit is not a dotted leader.
                leader = None
            paren = PAREN.search(value) if namespace == 'chapter' else None
            if leader or paren:
                flush()
                sep = leader or paren
                owner = value[:sep.start()].rstrip(' .')
                ref_a = a + (leader.end() if leader else paren.start('ref'))
                ref_b = b if leader else a + paren.end('ref')
                ref = raw[ref_a:ref_b].strip()
                qualified_page = bool(re.search(r'\b[IVX]+\s*,\s*\d', ref))
                if not NUMERIC.fullmatch(ref) and not (namespace == 'page' and qualified_page and re.fullmatch(r'[\dIVX,; .\-–]+', ref)):
                    residual(a, b, 'unsupported_leader_reference', sid)
                    continue
                parsed = None if ctx.get('non_scripture_citations') else SCRIPTURE.fullmatch(owner)
                scripture, flags = None, []
                if parsed:
                    if parsed['book']:
                        book_start = a + parsed.start('book')
                        ctx['book'] = {'raw': parsed['book'], 'source': source,
                                       'span': [book_start, a + parsed.end('book')]}
                    book = ctx.get('book', {})
                    chapter = parsed['chapter']
                    verse = parsed['verse'].strip(' ,.')
                    if (ctx.get('family') in ('scripture_leaders', 'scripture_single_period')
                            and not parsed['book'] and re.match(r'^[0-9]', owner)
                            and re.fullmatch(r'[IVXLCDM]+', ctx.get('chapter', {}).get('raw', ''), re.I)):
                        # In these tables chapters are Roman, Arabic-only rows
                        # continue the verses of the previous Roman chapter.
                        verse = owner.strip(' ,.'); chapter = ctx['chapter']['raw']
                        flags.append('chapter_inherited_from_roman_table')
                    elif chapter in ('—', '–', '-', '--'):
                        # Dash + Roman numeral repeats the book (PO019);
                        # dash + Arabic verse repeats the chapter (PO012/23).
                        roman = re.match(r'^([IVXLCDM]+)[.,]\s*(.*)$', verse, re.I)
                        if roman:
                            chapter, verse = roman.groups()
                            ctx['chapter'] = {'raw': chapter, 'source': source, 'span': [a + owner.index(chapter), a + owner.index(chapter) + len(chapter)]}
                            flags.append('book_inherited_locally')
                        else:
                            chapter = ctx.get('chapter', {}).get('raw')
                            flags.append('chapter_inherited_locally' if chapter else 'missing_chapter_context')
                    else:
                        ctx['chapter'] = {'raw': chapter, 'source': source,
                                          'span': [a + parsed.start('chapter'), a + parsed.end('chapter')]}
                    if not book:
                        flags.append('missing_book_context')
                    scripture = {'book_raw': book.get('raw'), 'book_evidence': book or None,
                                 'chapter_raw': chapter, 'chapter_evidence': ctx.get('chapter'),
                                 'verse_raw': verse}
                elif ctx.get('non_scripture_citations'):
                    flags.append('non_scripture_citation')
                elif scope.get('kind') == 'scripture_index':
                    flags.append('scripture_expression_unparsed')
                ns = 'bracketed_page' if ref.startswith('[') and namespace == 'page' else namespace
                if qualified_page and namespace == 'page':
                    ns = 'qualified_page'
                    flags.append('printed_edition_or_part_qualifier')
                emit(a, b, a + len(owner), ref_a, ref_b, scope, ns, flags, scripture)
                continue
            if is_letter(value) or (kind == 'titulo') or re.match(r'^(?:TABLE |INDEX |ANCIEN TESTAMENT|NOUVEAU TESTAMENT|SACRAE SCRIPTURAE|Pages\.)', value, re.I):
                flush()
                residual(a, b, 'editorial_heading', sid)
                continue
            # Names/subjects: numeric-leading lines continue the preceding
            # reference list. Lowercase wrapped lines continue its prose.
            continuation = value[0].isdigit() or value[0].islower()
            if ctx.get('family') in ('names_lines', 'mixed_volume_words', 'greek_colon_names'):
                # Greek/Coptic lowercase is a lemma start, not a Latin wrapped
                # description. Syriac/Ethiopic lack case. Hyphenated native
                # words can still continue when the preceding line has no ref.
                native = bool(re.match(r"[᾿ʼ'’]*[\u0370-\u03ff\u1f00-\u1fff\u2c80-\u2cff\u0700-\u074f\u1200-\u137f]", value))
                if native:
                    continuation = bool(pending and raw[pending[0]:pending[1]].rstrip().endswith('-')
                                        and split_name(raw[pending[0]:pending[1]]) is None)
                if pending and split_name(raw[pending[0]:pending[1]]) is None and not native:
                    continuation = not value.startswith(('—', '–'))

            if pending is not None and continuation:
                pending = (pending[0], b, scope, pending[3])
            else:
                flush()
                pending = (a, b, scope, namespace)
        flush()
    return {'version': VERSION, 'entries': entries, 'residuals': residuals, 'context': contexts}
