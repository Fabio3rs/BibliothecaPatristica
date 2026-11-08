"""Locate internal index divisions inside an explicitly bounded work."""
from collections import defaultdict
from difflib import SequenceMatcher
import hashlib
import re
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.internal_index_formats import LETTER, letter_key, language, normalized, greek_number
from tools.indexing.epistle_index_locators import roman_to_int

BOOK=re.compile(r'\bLIBER\s+(PRIMUS|SECUNDUS)\b|\bLIB[.:]\s*(I{1,2})[.]',re.I)
BOOK_OPENING=re.compile(r'(?m)^[ \t]*(?:[a-z]\s+)?LIBER\s+(PRIMUS|SECUNDUS)[ \t]*$',re.I)
QUESTION=re.compile(r'(?m)^[ \t]*(?:[a-z]\s+)?(?:EX [A-Z ]{3,80}[.]\s*)?INTERROGATIO\s+(PRIMA|[IVXLCDM]+|\d+)(?=\s|[.(]|$)',re.I)
TITLE=re.compile(r'(?m)^[ \t]*(?:\d{3,4}\s+)?(?:Tit(?:ulus|[.])?|Τ[ίι]τλ(?:ος|[.])?)\s+(?P<n>primus|πρῶτος|[IVXLCDM]+|[Α-Ωα-ωϚϛϟ]+)[\x27΄ʹʹ’]?[.]\s*[—–-]',re.I)
RULE_HEADING=re.compile(r"(?m)^[ \t]*(?:\d{3,4}\s+)?(?:REGULA\s+(?P<latin>[IVXLCDM]+)|[ΟὍὉ]ΡΟΣ\s+(?P<greek>[Α-Ωα-ωϚϛϟ]+))['΄ʹʹ’᾽᾿′]?[.]",re.I)


def scan_targets(pages, family, diagnostics=None):
    """pages: ordered dicts with source, raw, physical and optional start/end."""
    if family in {'title_number', 'book_number', 'manuscript_homily'}:
        from tools.indexing.named_division_formats import scan_named_targets
        return scan_named_targets(pages, family, diagnostics)
    if family in {'rule_number','section_number'}:
        return scan_ordinal_targets(pages,family)
    targets=defaultdict(list);book=None;letters={};highest_letter=-1
    alphabet='αβγδεζηθικλμνξοπρστυφχψω'
    diagnostics=diagnostics if diagnostics is not None else []
    for page in pages:
        raw=page['raw'];source=page['source'];lo=page.get('start',0);hi=page.get('end',len(raw))
        for block in blocks(raw):
            a,b=max(lo,block['body_start']),min(hi,block['body_end'])
            allowed={'texto_principal','cabecalho'}
            if family=='book_question':allowed.add('aparato_critico')
            if a>=b or block['kind'] not in allowed:continue
            text=raw[a:b];lang=language(raw,block)
            events=[]
            if family=='book_question':
                events += [(m.start(),'book',m) for m in (BOOK if block['kind']=='cabecalho' else BOOK_OPENING).finditer(text)]
                if block['kind']!='cabecalho':events += [(m.start(),'question',m) for m in QUESTION.finditer(text)]
            else:
                events += [(m.start(),'letter',m) for m in LETTER.finditer(text)]
                events += [(m.start(),'title',m) for m in TITLE.finditer(text)]
            for _,kind,m in sorted(events,key=lambda x:x[0]):
                proof={'source':source,'span':[a+m.start(),a+m.end()],'raw':m[0]}
                if kind=='book':
                    value=m[1].upper() if m[1] else m[2].upper()
                    book={'key':1 if value in {'PRIMUS','I'} else 2,**proof};continue
                if kind=='letter':
                    key=letter_key(m['letter'])
                    position=alphabet.find(key) if len(key)==1 else -1
                    if position>=0 and position<highest_letter:
                        diagnostics.append({'reason':'letter_sequence_regression','previous_letter':alphabet[highest_letter],**proof})
                        letters.clear()
                        continue
                    if position>highest_letter:
                        # A new section invalidates stale context in the parallel language.
                        letters.clear();highest_letter=position
                    letters[lang]={'key':key,**proof};continue
                context=book if kind=='question' else letters.get(lang)
                if not context:continue
                value=m[1] if kind=='question' else m['n']
                n=1 if normalized(value) in {'prima','primus','πρωτοσ'} else int(value) if value.isdecimal() else roman_to_int(value) if kind=='question' or lang=='lat' else greek_number(value)
                if not n:continue
                key=(context['key'],n)
                targets[key].append({'file':source,'physical':page['physical'],'marker':proof,
                    'context':dict(context),'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                    'language':lang,'block_kind':block['kind'],'snippet':raw[a+m.end():min(b,a+m.end()+600)]})
    return targets


def scan_ordinal_targets(pages,family):
    from tools.indexing.ordinal_index_formats import SECTION
    targets=defaultdict(list)
    for page in pages:
        raw=page['raw'];source=page['source']
        for block in blocks(raw):
            a=max(page.get('start',0),block['body_start'])
            b=min(page.get('end',len(raw)),block['body_end'])
            if a>=b:continue
            text=raw[a:b]
            if family=='rule_number':
                if block['kind'] not in {'texto_principal','cabecalho'}:continue
                matches=list(RULE_HEADING.finditer(text))
            elif block['kind']=='texto_principal':
                matches=list(SECTION.finditer(text))
            elif block['kind'] in {'nota','nota_marginal'} and re.fullmatch(r'\s*[IVXLCDM]+[.]\s*',text):
                matches=list(re.finditer(r'(?P<roman>[IVXLCDM]+)[.]',text))
            else:continue
            for m in matches:
                groups=m.groupdict();token=next(v for v in groups.values() if v)
                number=greek_number(token) if groups.get('greek') else roman_to_int(token)
                if not number:continue
                targets[(family,number)].append({'file':source,'physical':page['physical'],
                    'marker':{'source':source,'span':[a+m.start(),a+m.end()],'raw':m[0]},
                    'context':{'key':family,'basis':'explicit_work_bounds'},
                    'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                    'language':language(raw,block),'block_kind':block['kind'],
                    'snippet':raw[a+m.end():min(b,a+m.end()+600)]})
    return targets


def add_bounded_section_candidates(targets,pages,max_pages=3):
    """Retain short physical windows between observed narrative section numbers."""
    if max_pages<=0:return
    observed={n:rows for (family,n),rows in targets.items() if family=='section_number'}
    by_physical={page['physical']:page for page in pages}
    for lower,upper in zip(sorted(observed),sorted(observed)[1:]):
        if upper-lower<=1:continue
        left={r['physical'] for r in observed[lower]}
        right={r['physical'] for r in observed[upper]}
        # Ambiguous or reversed flanks cannot bound a sequence reliably.
        if len(left)!=1 or len(right)!=1:continue
        start,end=next(iter(left)),next(iter(right))
        if not 0<=end-start<max_pages:continue
        if any(n not in by_physical for n in range(start,end+1)):continue
        bounds=[{'number':n,'anchors':observed[n]} for n in (lower,upper)]
        for number in range(lower+1,upper):
            for physical in range(start,end+1):
                page=by_physical[physical];raw=page['raw']
                targets[('section_number',number)].append({'file':page['source'],'physical':physical,
                    'marker':None,'context':{'key':'section_number','basis':'bounded_observed_sections'},
                    'bounds':bounds,'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                    'language':'unknown','block_kind':'bounded_window','snippet':'',
                    'localization_method':'bounded_neighbor_sections'})


def locate(item,metadata,targets):
    if metadata.get('grammar') == 'manuscript_homily':
        from tools.indexing.named_division_formats import locate_incipit
        return locate_incipit(metadata, targets)
    binding=metadata.get('internal_binding',{})
    first=binding.get('division') or (binding.get('book') if metadata.get('reference_namespace')=='book_question' else (binding.get('letter') or {}).get('key'))
    matches={};missing=[]
    for number in dict.fromkeys(binding.get('numbers',[])+binding.get('alternative_numbers',[])):
        rows=targets.get((first,number),[]) if first is not None else []
        if not rows:missing.append(number)
        for target in rows:
            bounded=target.get('localization_method')=='bounded_neighbor_sections'
            candidate=matches.setdefault(target['file'],{'file':target['file'],'physical':target['physical'],
                'navigable':True,'certified':False,'score':20.0 if bounded else 40.0,
                'tier':'bounded_internal_sequence' if bounded else 'internal_division_candidate',
                'uncertainties':['source_ocr_not_certified','target_page_in_bounded_section_window' if bounded else 'target_is_division_opening_not_all_continuation_pages'],
                'evidence':{'family':metadata['grammar'],'binding':binding,'anchors':[]}})
            candidate['evidence']['anchors'].append({'number':number,**target})
            warnings=[]
            if not bounded and len({r['file'] for r in rows})>1:warnings.append('same_internal_key_in_multiple_files')
            if target['block_kind']=='aparato_critico':warnings.append('division_heading_in_ocr_apparatus_block')
            if target['block_kind'] in {'nota','nota_marginal'}:warnings.append('standalone_section_number_on_page_not_exact_paragraph')
            if binding.get('inferred'):warnings.append('index_number_inferred_from_bounded_sequence')
            if binding.get('alternative_numbers'):warnings.append('index_ordinal_conflict_alternatives_preserved')
            warnings.extend(target.get('uncertainties', []))
            for warning in warnings:
                if warning not in candidate['uncertainties']:candidate['uncertainties'].append(warning)
            if target['language']==metadata.get('language'):
                similarity=SequenceMatcher(None,normalized(item['label']),normalized(target['snippet'])).ratio()
                candidate['score']=max(candidate['score'],40+10*similarity)
    result=sorted(matches.values(),key=lambda c:(-c['score'],c['file']))
    for rank,c in enumerate(result,1):
        c['rank']=rank
        if missing:c['uncertainties'].append('some_requested_internal_numbers_unmapped')
    return result,missing
