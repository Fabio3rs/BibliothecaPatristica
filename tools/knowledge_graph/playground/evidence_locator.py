"""Versioned lexical location. Original strings and Unicode offsets are never edited.

v2 remains in evidence_matching.py, including its historical behavior. v3 does
not equate textual location, unique position, and semantic entailment.
"""
from __future__ import annotations

import re
import unicodedata as ud
from collections import defaultdict

from rapidfuzz.distance import Levenshtein
from . import evidence_matching as legacy

POLICY = 'local-quote-v3'
POLICIES = (legacy.MATCHING_POLICY, POLICY)
SUPERSCRIPTS = '⁰¹²³⁴⁵⁶⁷⁸⁹'
NOTE = re.compile(r'\([0-9]{1,3}\)|[' + SUPERSCRIPTS + r']{1,3}')
DEFINITION = re.compile(r'(?m)(?:^|\n)\s*(\([0-9]{1,3}\)|[' + SUPERSCRIPTS + r']{1,3}|[0-9]{1,3}[.)])\s+(?=\S)')
NUMERIC_CONTEXT = {'annorum','anno','annos','annus','dies','diebus','horas','horae','cap',
                   'caput','capite','lib','libro','versus','versu','psalm','numero','numerus',
                   'olympiade','ετη','ετων','ωρων','ημεραι','chapter','verse','year','years'}
CONFUSABLES = str.maketrans({'Α':'A','Β':'B','Ε':'E','Ζ':'Z','Η':'H','Ι':'I','Κ':'K','Μ':'M',
                            'Ν':'N','Ο':'O','Ρ':'P','Τ':'T','Υ':'Y','Χ':'X','ο':'o','ρ':'p',
                            'а':'a','е':'e','о':'o','р':'p','с':'c','х':'x','і':'i','у':'y'})


def _fold(text):
    return ''.join(c for c in ud.normalize('NFKD', text.casefold()).replace('œ','oe').replace('æ','ae')
                   if not ud.category(c).startswith('M'))


def _letter(char):
    return bool(char) and ud.category(char).startswith('L')


def editorial_notes(blocks):
    """Only explicit definitions in parser-identified note/apparatus blocks."""
    definitions = defaultdict(list)
    for block in blocks:
        kind = ' '.join(str(block.get(k, '')) for k in ('type','tag')).casefold()
        if not any(term in kind for term in ('nota','note','apparat','rodape','footnote')):
            continue
        for match in DEFINITION.finditer(block.get('text', '')):
            marker = match.group(1)
            number = _fold(marker).strip('().')
            definitions[number].append({'locator':block['id'],'start':match.start(1),
                                        'end':match.end(1),'text':marker})
    return dict(definitions)


def note_markers(text, definitions):
    found = []
    for m in NOTE.finditer(text):
        number = _fold(m.group()).strip('()')
        left = text[:m.start()].rstrip()
        words = re.findall(r'[^\W\d_]+', _fold(left[-70:]), re.UNICODE)
        prose = bool(left and (_letter(left[-1]) or ud.category(left[-1]).startswith('M')))
        unsafe = not prose or not words or len(words[-1])<2 or bool(set(words[-2:]) & NUMERIC_CONTEXT)
        unsafe |= bool(re.search(r'[=<>+*/^−]|\d\s*$', left[-12:]))
        found.append({'start':m.start(),'end':m.end(),'text':m.group(),'number':number,
                      'safe_editorial':bool(definitions.get(number)) and not unsafe,
                      'basis':'same_page_note_definition_and_prose_context' if definitions.get(number) and not unsafe else 'unconfirmed_or_numeric_context',
                      'definitions':definitions.get(number, [])})
    return found


def tokens(text, ignored=()):
    omitted = {i for a,b in ignored for i in range(a,b)}
    for m in re.finditer(r'[-‐]\s*\n\s*', text):
        p = m.start()-1
        while p >= 0 and ud.category(text[p]).startswith('M'):
            p -= 1
        if p >= 0 and _letter(text[p]) and m.end() < len(text) and _letter(text[m.end()]):
            omitted.update(range(m.start(),m.end()))
    chars, offsets = [], []
    for i, c in enumerate(text):
        if i in omitted or c == '\u00ad':
            continue
        folded = _fold(c)
        if not folded and offsets:
            offsets[-1] = (offsets[-1][0], i+1)
        for f in folded:
            chars.append(f); offsets.append((i,i+1))
    normalized = ''.join(chars)
    result = []
    for m in legacy.TOKEN.finditer(normalized):
        w = m.group()
        numeric = (m.start()>0 and m.end()<len(normalized)
                   and normalized[m.start()-1].isdigit() and normalized[m.end()].isdigit())
        if ud.category(w[0]).startswith('P') and not numeric and w not in {'<','>','=','+','-','−','/'}:
            continue
        result.append((w, offsets[m.start()][0], offsets[m.end()-1][1]))
    return result


def _boundaries(text, start, end, quote):
    # An exact substring cut inside a word is not a faithful complete quotation.
    return not ((quote and _letter(quote[0]) and start and (_letter(text[start-1]) or ud.category(text[start-1]).startswith('M')))
                or (quote and _letter(quote[-1]) and end < len(text) and (_letter(text[end]) or ud.category(text[end]).startswith('M'))))


def _raw_diff(quote, text, start):
    return [{'operation':o.tag,'quote_start':o.src_start,'quote_end':o.src_end,
             'source_start':start+o.dest_start,'source_end':start+o.dest_end,
             'quote_text':quote[o.src_start:o.src_end],'source_text':text[o.dest_start:o.dest_end]}
            for o in Levenshtein.opcodes(quote, text) if o.tag != 'equal']


def _scripts(text):
    return {next((s for s in ('LATIN','GREEK','CYRILLIC','SYRIAC','ARABIC') if s in ud.name(c,'')), 'OTHER')
            for c in text if _letter(c)}


def _extra_guard(quote, text, query, source, names):
    qw, sw = [t[0] for t in query], [t[0] for t in source]
    for op in Levenshtein.opcodes(qw, sw):
        if op.tag == 'equal':
            continue
        qpart, spart = query[op.src_start:op.src_end], source[op.dest_start:op.dest_end]
        if any(t[0] in names or (t[1] < len(raw) and raw[t[1]].isupper())
               for ts,raw in ((qpart,quote),(spart,text)) for t in ts):
            return 'name_or_capitalized_word_changed'
        if _scripts(' '.join(t[0] for t in qpart)) != _scripts(' '.join(t[0] for t in spart)) and qpart and spart:
            return 'mixed_script_difference'
        if 'GREEK' in _scripts(' '.join(t[0] for t in qpart+spart)):
            return 'greek_lexical_difference'
        # Retain the old bound but review arbitrary replacements of whole words.
        # Common OCR l/t and rn/m confusions are diagnostics, not proof of meaning.
        if qpart and spart and len(qpart)==len(spart):
            for q,s in zip(qpart,spart):
                if q[0] != s[0] and Levenshtein.distance(q[0],s[0]) > 1:
                    return 'substantive_lexical_difference'
    return None


def locate_quote(quote, text, *, policy=POLICY, definitions=None, names=()):
    if policy == legacy.MATCHING_POLICY:
        return legacy.locate_quote(quote,text)
    if policy != POLICY:
        raise ValueError('Unknown evidence matching policy')
    base = {'policy':POLICY,'accepted':False,'located':False,'position_resolved':False,
            'semantic_support':'not_evaluated','status':'not_found','method':'not_found',
            'score':None,'start':None,'end':None,'matched_quote':None,'candidate_count':0,
            'offset_unit':'unicode_codepoints','edit_operations':[], 'ignored_note_markers':[],
            'diagnostics':[], 'candidates':[]}
    if text is None:
        return dict(base,method='unknown_locator',reason='Source locator does not exist')
    if not quote or not quote.strip():
        return dict(base,reason='Empty quotation')
    if len(quote) > 16384:
        return dict(base,reason='Quotation exceeds bounded locator input')
    def detail(a,b,method,score,ignored=(),guard=None):
        return {'start':a,'end':b,'matched_quote':text[a:b],'method':method,'score':round(score,6),
                'edit_operations':_raw_diff(quote,text[a:b],a),
                'ignored_note_markers':[m for m in ignored if a <= m['start'] and m['end'] <= b],
                'guard':guard}
    def finish(matches):
        matches = sorted(matches,key=lambda x:(-x['score'],x['start'],x['end']))
        # Only almost-identical window boundaries at the same occurrence collapse.
        distinct=[]
        for x in matches:
            tolerance=min(12,max(4,int((x['end']-x['start'])*.1)))
            if not any((x['start'],x['end'])==(y['start'],y['end']) or
                       (x['score']<1 and y['score']<1 and abs(x['start']-y['start'])<=tolerance
                        and abs(x['end']-y['end'])<=tolerance) for y in distinct):
                distinct.append(x)
        best=distinct[0]
        near=[m for m in distinct if best['score']-m['score'] < legacy.AMBIGUITY_MARGIN]
        if len(near)>1:
            return dict(base,located=True,method='ambiguous',status='review',candidate_count=len(near),
                        candidates=near[:20],reason='Repeated or competing occurrences; no unique source position')
        if best.get('guard'):
            return dict(base,located=True,method='review_only',status='review',candidate_count=len(distinct),
                        review_candidate=best,candidates=distinct[:20],reason=best['guard'],
                        diagnostics=[best['guard']])
        return dict(base,**{k:v for k,v in best.items() if k!='guard'},accepted=True,located=True,
                    position_resolved=True,status='accepted',candidate_count=1)

    exact=[]; index=0
    while len(exact)<=legacy.MAX_CANDIDATES:
        index=text.find(quote,index)
        if index<0:break
        guard=None if _boundaries(text,index,index+len(quote),quote) else 'truncated_word'
        if re.search(r'[^\W\d_][-‐]\s*$',quote):
            guard='truncated_word'
        exact.append(detail(index,index+len(quote),'exact',1.,guard=guard));index+=1
    if exact:
        return finish(exact)
    query=tokens(quote); wanted=[t[0] for t in query]
    if not wanted or sum(map(len,wanted))<3:
        return dict(base,reason='Insufficient quote context')
    markers=note_markers(text,definitions or {})
    safe=[m for m in markers if m['safe_editorial'] and m['number'] not in wanted]
    variants=[([],tokens(text))]
    if safe:variants.append((safe,tokens(text,[(m['start'],m['end']) for m in safe])))
    names={t[0] for name in names for t in tokens(name) if len(t[0])>2}
    query_text=' '.join(wanted)
    matches=[]
    for ignored,source in variants:
        words=[t[0] for t in source]; positions=defaultdict(list)
        for i,w in enumerate(words):positions[w].append(i)
        normalized=[i for i in positions[wanted[0]] if words[i:i+len(wanted)]==wanted]
        if normalized:
            for i in normalized:
                a,b=source[i][1],source[i+len(wanted)-1][2]
                used=[m for m in ignored if a<=m['start'] and m['end']<=b]
                guard='truncated_word_or_operator' if re.search(r'[^\W\d_][-‐]\s*$',quote) else None
                # Accents/breathings can distinguish Greek negatives from other words.
                for q,s in zip(query,source[i:i+len(wanted)]):
                    if q[0] in legacy.PROTECTED and 'GREEK' in _scripts(q[0]):
                        qa=ud.normalize('NFC',quote[q[1]:q[2]].casefold())
                        sa=ud.normalize('NFC',text[s[1]:s[2]].casefold())
                        if qa!=sa:guard='greek_protected_diacritic_difference'
                matches.append(detail(a,b,'note_marker' if used else 'normalized',1.,used,guard))
    if matches:return finish(matches)
    if not 3<=len(wanted)<=legacy.MAX_FUZZY_TOKENS or len(query_text)>legacy.MAX_FUZZY_CHARS or sum(map(len,wanted))<20:
        return dict(base,reason='Insufficient context or quotation exceeds fuzzy-search bound')
    slack=min(4,max(2,len(wanted)//20+1))
    for ignored,source in variants:
        words=[t[0] for t in source]; positions=defaultdict(list)
        for i,w in enumerate(words):positions[w].append(i)
        anchors=sorted(((i,w) for i,w in enumerate(wanted) if positions[w] and not legacy._protected(w)),
                       key=lambda x:(len(positions[x[1]]),-len(x[1]),x[0]))[:8]
        starts=set()
        for i,w in anchors:
            for pos in positions[w]:
                starts.update(pos-i+shift for shift in range(-slack,slack+1) if 0<=pos-i+shift<len(words))
                if len(starts)>legacy.MAX_CANDIDATES:
                    return dict(base,reason='Too many candidate locations; use a more specific quote')
        for start in sorted(starts):
            for size in range(max(3,len(wanted)-slack),len(wanted)+slack+1):
                end=start+size
                if end>len(words):break
                window=words[start:end]; window_text=' '.join(window)
                length=max(len(query_text),len(window_text)); cutoff=int(length*(1-legacy.MIN_SCORE)+1e-9)
                distance=Levenshtein.distance(query_text,window_text,score_cutoff=cutoff)
                if distance>cutoff:continue
                guard=legacy._alignment_guard(wanted,window)
                guard=guard or _extra_guard(quote,text,query,source[start:end],names)
                a,b=source[start][1],source[end-1][2]
                if re.search(r'[-‐]\s*$',quote):guard='truncated_word_or_operator'
                used=[m for m in ignored if a<=m['start'] and m['end']<=b]
                candidate=detail(a,b,'note_marker' if used else 'fuzzy',1-distance/length,used,guard)
                candidate.update(legacy._edit_details(query_text,window_text,distance))
                matches.append(candidate)
    if matches:return finish(matches)
    # Visual equivalence never authorizes acceptance, even when the skeleton matches.
    skeleton_quote=_fold(quote.translate(CONFUSABLES))
    skeleton_text=_fold(text.translate(CONFUSABLES))
    diagnostic=[]
    if len(_scripts(quote))>1:diagnostic.append('mixed_script_quote')
    if skeleton_quote in skeleton_text and skeleton_quote != _fold(quote):
        diagnostic.append('visual_confusable_candidate_requires_review')
    return dict(base,diagnostics=diagnostic,reason='No sufficiently similar contiguous passage in the referenced source')
