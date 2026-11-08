"""Conservative, opt-in row grammars for independently inspected index formats."""
import re

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import digest, display, source_span, paragraphs
from tools.indexing.continuation_reviews import match_reviews
from tools.indexing.segmentation_reviews import validate_reviews, partition

VERSION = 'literal-index-rows-v7'
PROFILES = {'PG046': 'analytic-tome-rows-v1', 'PL217': 'epistle-incipit-rows-v1'}
ROMAN = r'[IVXLCDM]+'
START = re.compile(rf',\s*(?P<start>(?:{ROMAN}\s*,\s*)?(?:\d{{1,4}}\b|ibid\b))', re.I)
ANALYTIC_START = re.compile(rf'[,.;]\s*(?P<start>(?:{ROMAN}\s*,\s*)?(?:\d{{1,4}}\b|ibid\b))', re.I)
TOKEN = re.compile(r'\s+|\d{1,4}\b|[IVXLCDM]+\b|[A-D]\b|et\b(?=\s+(?:\d|seqq?\b|sqq\b))|seqq?\b|sqq\b|ibid\b|passim\b|fin\b|[.,;–—-]')
APPENDIX = r'(?i:append(?:ix)?|appen-[ \t]*\n[ \t]*dix)[.]?'
SUPPLEMENT = r'(?i:suppl(?:em(?:ent(?:um)?)?)?)[.,]?'
REGISTER = r'(?i:registr)[.,]?\s*(?i:ad|de)\s+(?:(?i:neg)\s*[.]?\s*)?(?i:imp)[.,]?'
EPISTLE = re.compile(
    rf'(?P<refs>(?:\b{ROMAN}[., ]\s*(?:{APPENDIX}\s*)?\d{{1,4}}\b|'
    rf'\b{SUPPLEMENT}\s*,?\s*\d{{1,4}}\b|\b{REGISTER}\s*,?\s*\d{{1,4}}\b)'
    r'(?:\s+(?:bis|ter))?[.]?)\s*$')


def epistle_namespace_wrap(text, next_line_offset):
    """Accept only a locator spanning adjacent OCR lines, not a new incipit.

    Bare numeric lines still need layout review: proximity alone cannot prove
    ownership in OCR that has displaced numbers.
    """
    if re.search(r'\n\s*\n', text) or re.fullmatch(r'\d+[.]?', text[next_line_offset:].strip()):
        return False
    match = EPISTLE.search(text)
    return bool(match and match.start('refs') < next_line_offset
                and not EPISTLE.search(text[:next_line_offset]))


def reference_groups(text, profile):
    """Return literal intervals only. No inheritance, edition conversion or targets."""
    if profile == 'epistle-incipit-rows-v1':
        match = EPISTLE.search(text)
        return [match.span('refs')] if match else []
    result, cursor = [], 0
    matches = list(START.finditer(text)) + [m for m in ANALYTIC_START.finditer(text)
                                          if text[m.start()] != ',']
    for match in sorted(matches, key=lambda m: m.start()):
        a = match.start('start')
        if a < cursor:
            continue
        # OCR may replace a clause comma with a period/semicolon. Require an
        # already observed locator and textual ownership; never open a row this way.
        if text[match.start()] != ',' and (
                not result or not any(c.isalpha() for c in text[cursor:match.start()])
                or re.search(r'\b(?:cap|lib|ann|anno|tom|vers)\.?\s*$',
                             text[cursor:match.start()], re.I)):
            continue
        end = a
        while token := TOKEN.match(text, end):
            end = token.end()
        end = a + len(text[a:end].rstrip())
        if end > a:
            result.append((a, end))
            cursor = end
    return result


def attributed_reference_groups(raw, a, b, profile):
    """Literal clause owners; ibid links are evidence of dependency, never resolved values."""
    text = raw[a:b]
    clauses, cursor = [], 0
    for x, y in reference_groups(text, profile):
        left = cursor
        while left < x and text[left].isspace():
            left += 1
        owner_end = x
        delimiters = ' ,.;' if profile == 'analytic-tome-rows-v1' else ' ,'
        while owner_end > left and text[owner_end-1] in delimiters:
            owner_end -= 1
        item = {'raw': text[x:y], **source_span(raw, a+x, a+y),
                'owner_text_raw': text[left:owner_end],
                'owner_span': source_span(raw, a+left, a+owner_end),
                'namespace_status': 'literal_uninterpreted', 'inheritance_status': 'not_inferred'}
        if re.match(r'ibid\b', text[x:y], re.I):
            item['context_dependency'] = {
                'kind': 'printed_ibid', 'status': 'requires_context_review',
                'previous_reference_span': ({k: clauses[-1][k] for k in
                    ('char_start', 'char_end', 'text_sha256')} if clauses else None)}
        clauses.append(item)
        cursor = y
    return clauses


def propose_numeric_joins(raw, residuals, *, source, scopes):
    """Queue adjacent fragment/number pairs without assigning their references."""
    proposals = []
    ordered = sorted(residuals, key=lambda r: r['char_start'])
    for left, right in zip(ordered, ordered[1:]):
        if left['reason'] != 'fragment_before_unowned_number' or right['reason'] != 'locator_without_owner_or_page_marker':
            continue
        a, split, b = left['char_start'], right['char_start'], right['char_end']
        if left['block'] != right['block'] or not any(s['span'][0] <= a < b <= s['span'][1] for s in scopes):
            continue
        if left['char_end'] > split or raw[left['char_end']:split].strip():
            continue
        text = raw[a:b]
        if re.search(r'\n\s*\n', text) or not re.fullmatch(r'\d+[.]?',raw[split:b]):
            continue
        match = EPISTLE.search(text)
        if not match or match.start('refs') >= split-a or not any(c.isalpha() for c in text[:match.start('refs')]):
            continue
        proposals.append({
            'id':digest(f'{VERSION}:{source}:{digest(raw.encode())}:join:{a}:{b}'.encode())[:24],
            'source':source, 'source_sha256':digest(raw.encode()),
            'kind':'adjacent_incipit_numeric_locator', 'status':'requires_layout_review',
            'applied':False, 'reason':'contiguous_same_block_and_complete_literal_namespace',
            'fragment':{'raw':raw[a:split],**source_span(raw,a,split)},
            'number':{'raw':raw[split:b],**source_span(raw,split,b)},
            'proposed_entry_raw':text, 'proposed_reference_raw':match['refs'],
            'reference_span':source_span(raw,a+match.start('refs'),a+match.end('refs')),
        })
    return proposals


def propose_analytic_column_links(raw, source, scopes):
    """Keep two source fragments separate; a hyphen suggests, never proves, continuity."""
    result=[]
    all_blocks=blocks(raw)
    for left,right in zip(all_blocks,all_blocks[1:]):
        if left['kind']!='texto_principal' or right['kind']!='texto_principal':
            continue
        a,b=left['body_start'],left['body_end']
        c,d=right['body_start'],right['body_end']
        if not any(s['span'][0]<=a<d<=s['span'][1] for s in scopes):
            continue
        left_text,right_text=raw[a:b],raw[c:d]
        tail=re.search(r'([^\W\d_]+)-\s*$',left_text)
        head=re.match(r'\s*([^\W\d_]+)',right_text)
        if not tail or not head or not head[1][0].islower():
            continue
        previous=reference_groups(left_text,'analytic-tome-rows-v1')
        following=reference_groups(right_text,'analytic-tome-rows-v1')
        if not previous or not following:
            continue
        x=a+previous[-1][1]
        while x<b and raw[x].isspace(): x+=1
        y=a+tail.end(1)+1
        z=c+head.start(1);end=c+following[0][1]
        if x>=y:
            continue
        result.append({'source':source,'source_sha256':digest(raw.encode()),
            'kind':'hyphenated_column_continuation','status':'requires_visual_review',
            'applied':False,'left':{'raw':raw[x:y],**source_span(raw,x,y)},
            'right':{'raw':raw[z:end],**source_span(raw,z,end)},
            'word_candidate':tail[1]+head[1],
            'reference_literal':right_text[slice(*following[0])],
            'reference_attribution':'pending_cross_column_review'})
    return result


def parse_page(raw, *, source, physical, volume, scopes, reviewed_joins=(), segmentation_reviews=()):
    profile = PROFILES.get(volume)
    if profile is None:
        raise ValueError(f'No audited row grammar for {volume}')
    intervals = sorted((s['span'][0], s['span'][1], s['section_id']) for s in scopes)
    for i, (a, b, _) in enumerate(intervals):
        if not 0 <= a < b <= len(raw) or (i and a < intervals[i-1][1]):
            raise ValueError('Invalid or overlapping region envelopes')
    if segmentation_reviews and profile!='analytic-tome-rows-v1':
        raise ValueError('Segmentation reviews require the analytic profile')
    partitions=validate_reviews(raw,source,scopes,segmentation_reviews)
    used_partitions=set()
    entries, residuals, events = [], [], []
    eligible = 0

    def residual(a, b, reason, block):
        if raw[a:b].strip():
            residuals.append({'raw': raw[a:b], 'reason': reason, 'block': block,
                              **source_span(raw, a, b)})

    def emit(a, b, block, sid, letter, paragraph=False):
        text = raw[a:b]
        groups = reference_groups(text, profile)
        if not groups or text[groups[-1][1]:].strip():
            residual(a, b, 'incomplete_or_unsupported_locator', block)
            return
        if not any(c.isalpha() for c in text[:groups[0][0]]):
            residual(a, b, 'locator_without_owner', block)
            return
        key = digest(f'{VERSION}:{source}:{digest(raw.encode())}:{a}:{b}'.encode())[:24]
        clauses = attributed_reference_groups(raw,a,b,profile)
        flags = ['unvalidated_profile', 'row_segmentation_candidate']
        if not text[0].isupper():
            flags.append('leading_continuation_requires_review')
        if len(clauses) > 1:
            flags.append('internal_clauses_preserved_not_independent_lemmas')
        if '\n' in text:
            flags.append('joined_ocr_lines_require_segmentation_review')
        if paragraph:
            flags.append('ocr_paragraph_not_confirmed_printed_entry')
        entry = {'id': key, 'source': source, 'physical_page': physical, 'block': block,
                 'section_candidate': sid, 'observed_letter': letter,
                 'letter_status': 'ocr_order_unverified', 'entry_type': 'material',
                 'entry_raw': text, 'text_raw': clauses[0]['owner_text_raw'],
                 'text_display': display(clauses[0]['owner_text_raw']),
                 'reference_groups': clauses,
                 'references_raw': clauses[0]['raw'] if len(clauses) == 1 else None,
                 'reference_span': {k: clauses[0][k] for k in source_span(raw, a, b)} if len(clauses) == 1 else None,
                 'context_candidate_id': None, 'flags': flags, 'status': 'candidate_only',
                 'quality': {'segmentation': 'candidate', 'reference_attribution': 'clause_candidate',
                             'edition_mapping': 'not_attempted', 'destination': 'not_attempted'},
                 **source_span(raw, a, b)}
        entry['unit_kind'] = 'ocr_paragraph_group' if paragraph else 'row_candidate'
        if paragraph:
            entry['quality']['segmentation'] = 'pending_printed_boundaries'
        entries.append(entry)

    for index, block in enumerate(blocks(raw)):
        if block['kind'] != 'texto_principal':
            continue
        for start, end, sid in intervals:
            a, b = max(start, block['body_start']), min(end, block['body_end'])
            if a >= b:
                continue
            eligible += sum(not c.isspace() for c in raw[a:b])
            # A blank-line group is a recoverable textual unit, not proof of one
            # printed lemma. Preserve all clauses without splitting physical wraps.
            if profile == 'analytic-tome-rows-v1' and len(re.findall(r'\w-\s*\n',raw[a:b])) >= 2:
                groups = list(paragraphs(raw[a:b]))
                if len(groups) < 2:
                    residual(a,b,'wrapped_layout_requires_paragraph_segmentation',index)
                    continue
                letter = None
                for offset,text in groups:
                    x,y = a+offset,a+offset+len(text)
                    if (x,y) in partitions:
                        review=partitions[x,y]
                        used_partitions.add((x,y))
                        for left,right in partition(raw,review):
                            if '<' in raw[left:right] or '[' in raw[left:right]:
                                residual(left,right,'inline_markup_requires_review',index)
                                continue
                            count=len(entries)
                            emit(left,right,index,sid,letter)
                            if len(entries)>count:
                                entry=entries[-1]
                                entry['unit_kind']='visually_delimited_group'
                                entry['quality']['segmentation']='visual_group_boundaries_reviewed'
                                entry['quality']['reference_reading']='not_verified'
                                entry['visual_segmentation_review']=review['visual_review']
                                entry['parent_ocr_paragraph']=review['parent']
                                entry['flags'].append('reference_digits_and_subdivisions_not_certified')
                    elif re.fullmatch('[A-ZÆŒ]',text):
                        letter = text
                        events.append({'kind':'observed_letter','letter':text,**source_span(raw,x,y)})
                    elif '<' in text or '[' in text:
                        residual(x,y,'inline_markup_requires_review',index)
                    else:
                        emit(x,y,index,sid,letter,paragraph=True)
                continue
            pending, letter = None, None
            for match in re.finditer(r'[^\n]+', raw[a:b]):
                line = match[0].strip()
                if not line:
                    continue
                x = a+match.start()+len(match[0])-len(match[0].lstrip())
                y = x+len(line)
                if profile == 'epistle-incipit-rows-v1' and pending is not None and re.search(r'\n\s*\n',raw[pending:x]):
                    residual(pending,x,'blank_line_breaks_continuation',index)
                    pending = None
                if profile == 'epistle-incipit-rows-v1' and re.match(r'^\d+[.]?\s+\S',line):
                    if pending is not None:
                        residual(pending,x,'fragment_before_displaced_locator',index)
                        pending = None
                    residual(x,y,'displaced_locator_and_incipit_require_review',index)
                    continue
                if re.fullmatch('[A-ZÆŒ]', line) or '<' in line or '[' in line:
                    if pending is not None:
                        residual(pending, x, 'interrupted_fragment', index)
                        pending = None
                    if re.fullmatch('[A-ZÆŒ]', line):
                        letter = line
                        events.append({'kind': 'observed_letter', 'letter': line, **source_span(raw,x,y)})
                    else:
                        residual(x,y,'inline_markup_requires_review',index)
                    continue
                numeric_continuation = (profile == 'analytic-tome-rows-v1' and pending is not None
                    and raw[pending:x].rstrip().endswith(',') and bool(reference_groups(raw[pending:x],profile)))
                if re.fullmatch(r'\d+[.]?', line) and not numeric_continuation:
                    if pending is not None:
                        residual(pending,x,'fragment_before_unowned_number',index)
                        pending = None
                    residual(x,y,'locator_without_owner_or_page_marker',index)
                    continue
                # Join wraps only while the preceding line has no complete locator.
                # A finished row is never attached to the next independent lemma.
                namespace_wrap = (profile == 'epistle-incipit-rows-v1' and pending is not None
                    and epistle_namespace_wrap(raw[pending:y], x-pending))
                if profile == 'epistle-incipit-rows-v1' and pending is not None and line[0].isupper() and reference_groups(line,profile) and not namespace_wrap:
                    residual(pending,x,'incomplete_previous_incipit_not_transferred',index)
                    pending = None
                if pending is not None and line[0].isupper() and raw[pending:x].rstrip().endswith('.') and not namespace_wrap:
                    residual(pending,x,'unsupported_finished_row_before_new_start',index)
                    pending = None
                pending = x if pending is None else pending
                groups = reference_groups(raw[pending:y], profile)
                complete = groups and not raw[pending:y][groups[-1][1]:].strip()
                if profile == 'analytic-tome-rows-v1':
                    complete = complete and line.endswith('.')
                if complete:
                    emit(pending,y,index,sid,letter)
                    pending = None
            if pending is not None:
                residual(pending,b,'open_fragment_at_region_or_column_end',index)
    if used_partitions!=partitions.keys():
        raise ValueError('Segmentation review was not consumed by the wrapped paragraph parser')
    proposals = (propose_numeric_joins(raw,residuals,source=source,scopes=scopes)
                 if profile == 'epistle-incipit-rows-v1' else [])
    reviews = match_reviews(raw,source,proposals,reviewed_joins)
    for proposal in proposals:
        a,b = proposal['fragment']['char_start'],proposal['number']['char_end']
        review = reviews.get((a,b))
        if review is None:
            continue
        visual = review['visual_review']
        proposal['visual_review'] = visual
        if visual['layout_status'] != 'supported' or visual['reference_reading'] != 'matches_ocr':
            proposal['status'] = 'reviewed_reading_pending'
            continue
        block = next(r['block'] for r in residuals if r['char_start']==a)
        sid = next(s['section_id'] for s in scopes if s['span'][0]<=a<b<=s['span'][1])
        count = len(entries)
        emit(a,b,block,sid,None)
        if len(entries)!=count+1:
            raise ValueError('Reviewed continuation could not be emitted')
        entries[-1]['flags'].append('scoped_visual_continuation_review')
        entries[-1]['visual_review'] = visual
        entries[-1]['quality']['reference_attribution'] = 'visually_reviewed_local_join'
        entries[-1]['quality']['reference_reading'] = 'visually_matches_current_ocr'
        residuals = [r for r in residuals if not a<=r['char_start']<r['char_end']<=b]
        proposal.update(status='joined_in_experimental_staging',applied=True)
    entries.sort(key=lambda e:e['char_start'])
    consumed = sum(sum(not c.isspace() for c in e['entry_raw']) for e in entries)
    return {'source': source, 'physical_page': physical, 'sha256': digest(raw.encode()),
            'profile': profile, 'entries': entries, 'residuals': residuals, 'events': events,
            'continuation_proposals':proposals,
            'fragment_links':propose_analytic_column_links(raw,source,scopes)
                if profile=='analytic-tome-rows-v1' else [],
            'coverage': {'eligible_nonspace_chars': eligible, 'consumed_nonspace_chars': consumed,
                         'syntactic_fraction': consumed/eligible if eligible else 0},
            'import_ready': False}
