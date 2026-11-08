"""Literal grammars for paired columns, textual cross-references and ibid dependencies."""
import re

from tools.indexing.entry_parser_experiment import source_span
from tools.indexing.editorial_ocr_blocks import blocks

CELL = r'(?:\d{1,4}(?:[-–]\d{1,4})?[½¼¾]?|[—–-])'
PAIR = re.compile(rf'(?<!\S)(?P<first>{CELL})[ \t]+(?P<second>{CELL})[ \t]*[.]?[ \t]*$')
HEADERS = re.compile(r'(?m)^[ \t]*(?P<first>F[.]?)[ \t]+(?P<second>C[.]?)[ \t]*$')
XREF = re.compile(r'(?<!\w)(?P<marker>Vide|[Cc]f\.|V\.|v\.)[ \t]+(?P<target>[^\n]+)')
IBID = re.compile(r'(?<!\w)(?:ibid\.|ibidem\.?|ib\.)(?:[ \t]+[a-d]\.)?',re.I)
ROMAN_LOCATOR = re.compile(r'(?<!\w)[IVXLCDM]+(?:\s*[,.;](?!\w)|$)',re.I)
BOUNDARY = re.compile(r'^(?:INDEX|ORDO RERUM|CONSONANTIA|CONCILIA|Cap\.|F[.]?\s+C[.]?|[A-ZΑ-Ω])\s*$',re.I)


def make(raw, a, b, oa, ob, ra, rb, kind):
    return {'entry_raw':raw[a:b], **source_span(raw,a,b),
        'reference_groups':[{'raw':raw[ra:rb], **source_span(raw,ra,rb),
            'owner_text_raw':raw[oa:ob], 'owner_span':source_span(raw,oa,ob),
            'reference_kind':kind, 'namespace_status':'literal_uninterpreted',
            'inheritance_status':'not_inferred'}],
        'quality':{'segmentation':'mechanical_candidate','lemma_context':'unresolved',
            'region':'candidate','source_fidelity':'not_visually_verified',
            'edition_mapping':'not_inferred','destination':'not_attempted','continuity':'not_inferred'}}


def lines(raw,start,end):
    cursor=start
    for line in raw[start:end].splitlines(keepends=True):
        a=cursor+len(line)-len(line.lstrip()); b=cursor+len(line.rstrip())
        cursor+=len(line)
        yield a,b,raw[a:b]


def paired_compatible(signature,raw):
    return bool(HEADERS.search(raw)) and sum(bool(PAIR.search(t)) and any(c.isalpha() for c in t)
        for _,_,t in lines(signature,0,len(signature)))>=3


def paired_candidates(raw,start,end):
    header=HEADERS.search(raw)
    if not header:return [],[]
    headers=[{'slot':i,'raw':header[name],**source_span(raw,*header.span(name))}
             for i,name in enumerate(('first','second'),1)]
    # Running headers can announce the next section before the actual boundary.
    # Only a heading inside the page body stops the preceding thematic rows.
    boundaries = [b['body_start']+m.start() for b in blocks(raw) if b['kind']=='texto_principal'
        for m in re.finditer(r'(?m)^[ \t]*(?:INDEX ALTER|CONSONANTIA CAPITULORUM|CONCILIA\s+Cap\.)',
                             raw[b['body_start']:b['body_end']])]
    if boundaries:
        end=min(end,min(boundaries))
    if start>=end:return [],[]
    added,rejected=[],[];pending=None
    for a,b,text in lines(raw,start,end):
        if not text or BOUNDARY.fullmatch(text) or text.startswith(('INDEX ','ORDO RERUM')):
            if pending:rejected.append({'span':[pending[0],pending[1]],'literal':raw[pending[0]:pending[1]],'reason':'unfinished_row_before_boundary'})
            pending=None;continue
        if pending:
            previous=raw[pending[0]:pending[1]]
            if previous.endswith('-') or (previous[-1] not in '.?!;:' and text[0].islower()):
                a=pending[0];text=raw[a:b]
            else:
                rejected.append({'span':list(pending),'literal':previous,'reason':'unfinished_row_before_new_start'})
            pending=None
        match=PAIR.search(text)
        if match:
            ra=a+match.start('first');rb=a+match.end('second')
            ob=ra
            while ob>a and raw[ob-1].isspace():ob-=1
            leader=re.search(r'[.·]{2,}$',raw[a:ob])
            if leader:ob=a+leader.start()
            while ob>a and raw[ob-1].isspace():ob-=1
            owner=raw[a:ob]
            reason=None
            if not owner or not owner[0].isupper() or not any(c.isalpha() for c in owner):reason='owner_not_established'
            elif re.search(r'(?:\d|[—–-])\s*$',owner):reason='extra_or_displaced_numeric_cell'
            elif re.search(r'\b(?:Vide|Vite|Cf|ibid)\b',owner,re.I):reason='textual_cross_reference_in_row'
            elif '<' in owner or '>' in owner:reason='markup_in_owner'
            if reason:rejected.append({'span':[a,b],'literal':raw[a:b],'reason':reason});continue
            item=make(raw,a,b,a,ob,ra,rb,'paired_editorial_cells')
            group=item['reference_groups'][0]
            group['column_headers']=headers
            group['cells']=[{'slot':i,'raw':match[name],**source_span(raw,a+match.start(name),a+match.end(name)),
                'status':'printed_dash_uninterpreted' if match[name] in '—–-' else 'printed_numeric_literal',
                'column_label_raw':headers[i-1]['raw']}
                for i,name in enumerate(('first','second'),1)]
            added.append(item)
        elif text[-1] not in '.?!;:' and not re.search(r'\d|[—–]',text[-12:]):
            pending=(a,b)
        else:rejected.append({'span':[a,b],'literal':raw[a:b],'reason':'not_a_complete_two_cell_row'})
    if pending:rejected.append({'span':list(pending),'literal':raw[slice(*pending)],'reason':'unfinished_row_at_end'})
    return added,rejected


def xref_candidates(raw,start,end):
    added,rejected=[],[]
    for a,b,text in lines(raw,start,end):
        match=XREF.search(text)
        if not match:continue
        ob=a+match.start()
        while ob>a and raw[ob-1] in ' ,.;:\t':ob-=1
        owner=raw[a:ob];target=match['target'].strip()
        reason=None
        if not owner or not owner[0].isupper() or len(owner)<2:reason='cross_reference_owner_missing'
        elif re.search(r'\d|[<>]',owner+target):reason='mixed_material_locator_or_markup'
        elif ROMAN_LOCATOR.search(owner) or ROMAN_LOCATOR.search(target):reason='unassigned_roman_locator'
        elif not text.endswith('.') or not target.rstrip('.').strip():reason='open_cross_reference'
        elif IBID.search(owner+target) or XREF.search(owner) or XREF.search(target):reason='multiple_or_dependent_cross_references'
        elif BOUNDARY.fullmatch(owner):reason='editorial_boundary'
        if reason:rejected.append({'span':[a,b],'literal':text,'reason':reason});continue
        item=make(raw,a,b,a,ob,a+match.start(),b,'textual_cross_reference')
        group=item['reference_groups'][0]
        group.update(marker_raw=match['marker'],target_raw=raw[a+match.start('target'):b],
                     target_span=source_span(raw,a+match.start('target'),b),
                     namespace_status='not_applicable',target_entry_status='not_resolved')
        added.append(item)
    return added,rejected


def abbreviation_candidates(raw,start,end):
    # Explicit locators are separators only; never exported by this grammar.
    from tools.indexing.subdivision_clause_recovery import LOCATOR
    text=raw[start:end]
    events=sorted([(m.start(),m.end(),'explicit',m) for m in LOCATOR.finditer(text)]+
                  [(m.start(),m.end(),'ibid',m) for m in IBID.finditer(text)])
    cursor=0;added=[];rejected=[]
    for x,y,kind,match in events:
        if x<cursor:continue
        if kind=='explicit':cursor=y;continue
        a=cursor
        while a<x and text[a] in ' ,;:\t\n':a+=1
        ob=x
        while ob>a and text[ob-1] in ' ,;:\t\n':ob-=1
        owner=text[a:ob];reason=None
        if not owner or not owner[0].isupper() or sum(c.isalpha() for c in owner)<2:reason='dependent_owner_not_established'
        elif re.search(r'\d|[<>]',owner):reason='unassigned_number_or_markup_in_owner'
        elif ROMAN_LOCATOR.search(owner):reason='unassigned_roman_locator'
        elif re.search(r'\n\s*\n',owner):reason='paragraph_boundary_in_owner'
        elif XREF.search(owner):reason='textual_cross_reference_in_owner'
        elif re.search(r'\b(?:cod|cap|lib|tom|vers)\.?\s*$',owner,re.I):reason='other_namespace_dependency'
        elif re.search(r'[,;]\s*[IVXLCDM]+[,.]',owner):reason='interrupted_namespace'
        elif re.match(r'\s*[,;]\s*(?:\d|[a-d]\b|ibid|ib\.)',text[y:],re.I):reason='mixed_or_open_locator_list'
        elif BOUNDARY.fullmatch(owner):reason='editorial_boundary'
        if reason:rejected.append({'span':[start+a,start+y],'literal':text[a:y],'reason':reason})
        else:
            item=make(raw,start+a,start+y,start+a,start+ob,start+x,start+y,'context_dependent_locator')
            item['reference_groups'][0]['context_dependency']={'kind':'printed_ibid','status':'unresolved','target':None}
            added.append(item)
        cursor=y
    return added,rejected


def grammar(family):
    if family=='paired-columns':return paired_compatible,paired_candidates
    if family=='cross-references':return lambda text,raw: bool(XREF.search(text)),xref_candidates
    if family=='abbreviations':return lambda text,raw: bool(IBID.search(text)),abbreviation_candidates
    raise ValueError('Unsupported extension grammar')
