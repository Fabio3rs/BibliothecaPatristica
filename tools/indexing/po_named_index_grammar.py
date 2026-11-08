"""Deterministic literal grammars for scoped PO indices.

Book/canon/page triples and named-document locators must never enter a generic
page-number resolver. Offsets below refer to unchanged source XML, not a cleaned
copy. The caller supplies independently documented section boundaries.
"""
from __future__ import annotations

import re
from scripts.scan_editorial_ocr import ATTR, BLOCK
from tools.indexing.entry_parser_po import LEADER, lines, split_name
from tools.indexing.missing_po_region_parser import parse_region_page as ordinary_parser

VERSION = 'po-named-indices-v1'
QUALIFIER = re.compile(r'(?<!\w)(II?|Intr)\s*\.')
START = re.compile(r'^(?P<owner>[^\d\n].{0,180}?)[.:]\s*(?=(?:II?\s*\.|Intr\.|Voir\s*:))')
DIVISION = re.compile(r'(?P<name>Amulettes?|Textes divers|Liturgie|Livres canoniques)\s+(?P<codes>[A-Z0-9][A-Z0-9\s,.;]*)$')
BIBLE_CHAPTER = re.compile(r'(?<!\w)([IVXLCDM]+)[,.]\s*\d', re.I)
CANON_NOTICE = 'Les chiffres romains indiquent le livre; les chiffres gras, le numéro du canon; les autres chiffres, la pagination du fascicule.'


def _evidence(raw, a, b):
    return {'raw': raw[a:b], 'span': [a, b]}


def parse_canon_locators(text, *, base=0):
    """Recover comma/dot-separated canon + page groups under a local book.

    The OCR loses boldface. First numbers after a book or semicolon are therefore
    canon *candidates*, justified by the printed notice; remaining comma numbers
    are page candidates. No inheritance crosses an index entry. Prose is kept in
    the reference span and is not assigned to an invented sublemma.
    """
    locators, pending = [], []
    book = None
    # Explicit book changes are recognized even after explanatory prose.
    pattern = re.compile(r'(?P<q>(?<!\w)(?:II?|Intr)\s*\.)\s*|(?P<group>\d+(?:\s*[-–]\s*\d+)?(?:\s*[,\.]\s*\d+(?:\s*[-–]\s*\d+)?)+|\d+)')
    last_end = 0
    for match in pattern.finditer(text):
        if match['q']:
            book = re.sub(r'[\s.]', '', match['q'])
            last_end = match.end()
            continue
        gap = text[last_end:match.start()]
        # Only immediately qualified or punctuation-delimited numeric groups.
        if book is None or gap.strip(' \t\r\n;:,'):
            pending.append({'raw': match[0], 'span': [base+match.start(), base+match.end()], 'reason': 'numeric_prose_or_missing_book'})
            last_end = match.end()
            continue
        nums = list(re.finditer(r'\d+(?:\s*[-–]\s*\d+)?', match[0]))
        if book != 'Intr' and len(nums) < 2:
            pending.append({'raw': match[0], 'span': [base+match.start(), base+match.end()], 'reason': 'canon_without_page'})
            last_end = match.end()
            continue
        pages = nums if book == 'Intr' else nums[1:]
        item = {'book_raw': book, 'canon_raw': None if book == 'Intr' else nums[0][0],
                'page_tokens': [], 'raw': match[0], 'span': [base+match.start(), base+match.end()]}
        for number in pages:
            item['page_tokens'].append({'raw': number[0], 'span': [base+match.start()+number.start(), base+match.start()+number.end()]})
        if book != 'Intr':
            item['canon_span'] = [base+match.start()+nums[0].start(), base+match.start()+nums[0].end()]
        locators.append(item)
        last_end = match.end()
    return {'locators': locators, 'pending': pending}


def parse_region_page(raw, *, source, volume, physical, scopes, section_context=None):
    contexts = {key: dict(value) for key, value in (section_context or {}).items()}
    entries, residuals = [], []
    custom = {'book_canon_page', 'named_document', 'manuscripts', 'names_page_or_line'}
    ordinary = [s for s in scopes if contexts.get(s['section_id'], {}).get('family') not in custom]
    if ordinary:
        result = ordinary_parser(raw, source=source, volume=volume, physical=physical,
                                 scopes=ordinary, section_context=contexts)
        entries.extend(result['entries']); residuals.extend(result['residuals'])
        contexts.update(result['context'])

    def residual(a, b, sid, reason):
        if raw[a:b].strip():
            residuals.append({'raw': raw[a:b], 'span': [a,b], 'section_id': sid, 'reason': reason})

    def emit(a, b, owner_end, ref_a, scope, namespace, flags=()):
        while owner_end > a and raw[owner_end-1] in ' \n\t.,:;':
            owner_end -= 1
        while ref_a < b and raw[ref_a].isspace():
            ref_a += 1
        if not a < owner_end <= ref_a < b:
            residual(a,b,scope['section_id'],'invalid_literal_split'); return None
        entry = {'source': source, 'volume': volume, 'physical': physical,
                 'section_id': scope['section_id'], 'section_kind': scope.get('kind'),
                 'entry_span': [a,b], 'owner_span': [a,owner_end], 'owner_raw': raw[a:owner_end],
                 'reference_span': [ref_a,b], 'reference_raw': raw[ref_a:b],
                 'reference_namespace': namespace, 'parser': VERSION, 'flags': list(flags)}
        entries.append(entry)
        return entry

    for scope in sorted(scopes, key=lambda s:s['span']):
        sid=scope['section_id']; ctx=contexts.setdefault(sid,{})
        family=ctx.get('family')
        if family not in custom:
            continue
        for block in BLOCK.finditer(raw):
            attrs=dict(ATTR.findall(block['attrs']))
            selected=[(value,a,b) for value,a,b in lines(block['body'],block.start('body'))
                      if value and scope['span'][0] <= a < b <= scope['span'][1]]
            if attrs.get('tipo') not in ('texto_principal','tabela'):
                for _,a,b in selected: residual(a,b,sid,'editorial_header_or_note')
                continue
            if family == 'book_canon_page':
                pending=None
                def flush():
                    nonlocal pending
                    if pending is None:return
                    a,b,owner_end,ref_a=pending
                    ns='cross_reference' if raw[ref_a:b].startswith('Voir') else 'book_canon_page'
                    entry=emit(a,b,owner_end,ref_a,scope,ns,['boldface_not_preserved_in_ocr','subclauses_preserved_in_reference_span'])
                    if entry and ns=='book_canon_page':
                        parsed=parse_canon_locators(entry['reference_raw'],base=ref_a)
                        entry['editorial_locators']=parsed['locators']
                        entry['locator_pending']=parsed['pending']
                        entry['namespace_notice']=ctx.get('text','')
                    pending=None
                for value,a,b in selected:
                    if len(value)==1 and value.isalpha():
                        flush();residual(a,b,sid,'alphabetic_heading');continue
                    start=START.search(value)
                    if start and not re.match(r'^(?:II?\.|Intr\.)',value):
                        flush(); pending=(a,b,a+start.end('owner'),a+start.end())
                    elif pending:
                        pending=(pending[0],b,pending[2],pending[3])
                    else:
                        residual(a,b,sid,'orphan_continuation_or_unrecognized_entry')
                flush()
                continue
            for value,a,b in selected:
                if family=='named_document':
                    division=DIVISION.search(value)
                    leader=LEADER.search(value)
                    if not division or not leader or leader.start()>=division.start():
                        residual(a,b,sid,'named_document_row_unparsed');continue
                    entry=emit(a,b,a+leader.start(),a+division.start(),scope,'named_document')
                    if not entry:continue
                    entry['named_locator']={'division_raw': division['name'],
                        'division_span':[a+division.start('name'),a+division.end('name')],
                        'codes':[_evidence(raw,a+division.start('codes')+m.start(),a+division.start('codes')+m.end())
                                 for m in re.finditer(r'[A-Z]|\d+',division['codes'])]}
                    owner=entry['owner_raw']; chapter=BIBLE_CHAPTER.search(owner)
                    if chapter:
                        name=owner[:chapter.start()].strip(' ,.—–')
                        if name:
                            ctx['book']={'raw':name,'source':source,'span':[a+owner.index(name),a+owner.index(name)+len(name)]}
                        entry['scripture_context']={'book_raw':ctx.get('book',{}).get('raw'),
                            'book_evidence':ctx.get('book'),'passage_raw':owner[chapter.start():]}
                        entry['search_label']=' '.join(filter(None,[entry['scripture_context']['book_raw'],entry['scripture_context']['passage_raw']]))
                        entry['label']=entry['search_label']
                        if not name:entry['flags'].append('book_inherited_locally')
                    continue
                if family=='manuscripts':
                    if value.startswith('MANUSCRITS '):
                        ctx.setdefault('manuscript_heading_candidates', []).append(
                            {'raw':value,'source':source,'span':[a,b]})
                        residual(a,b,sid,'manuscript_collection_heading');continue
                    if re.fullmatch(r'\d+[º°] de .+', value):
                        ctx['manuscript_library']={'raw':value,'source':source,'span':[a,b]}
                        ctx.pop('manuscript_prefix',None)
                        residual(a,b,sid,'manuscript_library_heading');continue
                    if value=='Pages.':
                        # A repeated table-column heading ends the preceding
                        # library context; XML order may interleave headings.
                        ctx.pop('manuscript_library',None)
                        ctx.pop('manuscript_prefix',None)
                        residual(a,b,sid,'manuscript_column_heading');continue
                    leader=LEADER.search(value)
                    if not leader:
                        residual(a,b,sid,'manuscript_heading_or_unparsed');continue
                    ref_a=a+leader.end()
                    pages=re.match(r'\s*Pages\.\s*',raw[ref_a:b])
                    if pages:ref_a+=pages.end()
                    entry=emit(a,b,a+leader.start(),ref_a,scope,'page',['manuscript_shelfmark_not_target_page','page_line_typography_preserved'])
                    if entry:
                        entry['manuscript_context']={
                            'collection_heading_candidates':list(ctx.get('manuscript_heading_candidates',[])),
                            'library_evidence':ctx.get('manuscript_library')}
                        owner=entry['owner_raw']
                        if owner.count('(') != owner.count(')'):
                            entry['flags'].append('unbalanced_parenthesis_in_source_owner')
                        prefix=re.match(r'^([^\d]+?)\s+(?=\d)',owner)
                        if prefix:
                            ctx['manuscript_prefix']={'raw':prefix[1],'source':source,
                                'span':[a,a+prefix.end(1)]}
                        elif re.fullmatch(r'\d+',owner) and ctx.get('manuscript_prefix'):
                            entry['manuscript_context']['shelfmark_prefix_evidence']=dict(ctx['manuscript_prefix'])
                            entry['flags'].append('shelfmark_prefix_inherited_candidate')
                            entry['search_label']=ctx['manuscript_prefix']['raw']+' '+owner
                            entry['label']=entry['search_label']
                    if entry and entry['owner_raw']=='—':
                        entry['flags'].append('manuscript_owner_inherited_locally')
                        entry['parent_owner_evidence']=ctx.get('manuscript_owner')
                    elif entry:
                        ctx['manuscript_owner']={'raw':entry['owner_raw'],'source':source,'span':entry['owner_span']}
                    continue
                split=split_name(value)
                if split and any(ch.isalpha() for ch in split[0]):
                    owner,offset=split
                    emit(a,b,a+len(owner),a+offset,scope,'page_line_or_page_list',
                         ['numeric_punctuation_semantics_ambiguous','literal_row_segmentation'])
                else:
                    residual(a,b,sid,'name_without_safe_numeric_tail')
    return {'version':VERSION,'entries':entries,'residuals':residuals,'context':contexts}
