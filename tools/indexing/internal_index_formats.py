"""Literal parsers for book/question and bilingual letter/title indices.

All numbers describe internal editorial divisions, never physical pages.
Callers supply explicit section bounds; no volume identifier selects a grammar.
"""
import re
import unicodedata
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.epistle_index_locators import roman_to_int

VERSION = 'internal-index-formats-v1'
QUESTION = re.compile(r'\((?:Lib\.\s*)?(?P<book>I{1,2})\s*[, ]\s*(?:interrog\.\s*)?(?P<numbers>\d+(?:\s*,\s*\d+)*)[.]?\)',re.I)
GREEK_VALUES = dict(zip('αβγδεϛζηθικλμνξοπϟρστυφχψωϡ',[1,2,3,4,5,6,7,8,9,10,20,30,40,50,60,70,80,90,100,200,300,400,500,600,700,800,900]))
ORDINAL = re.compile(r"(?m)^[ \t]*(?:\d{3,4}\s+)?(?P<number>[IVXLCDM]+|[Α-Ωα-ωϚϛϜϝϞϟϠϡ]{1,4}|\d{1,3})['΄ʹʹ’]?[.]\s+")
LETTER = re.compile(r'(?im)^[ \t]*(?:\*|\d{1,4}\s+)?(?:Littera\s+|Στοιχε[ῖι]ον\s+)(?P<letter>[\wἀ-῿]+)[.]')
CONTROL = re.compile(r'(?im)^\s*(?:[—–-]\s*)?(?:\*?\d*\s*)?(?:Littera\b|Στοιχε[ῖι]ον\b|Sequuntur\b|[ἘΕ]ντ[εα][ῦυ]θεν\b|[ἘΕ]ντα[ῦυ]?θα\b|Hucusque\b|Ἕως\b)')


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFD',text.casefold()) if not unicodedata.combining(c))


def greek_number(text):
    chars=normalized(text).strip(".'΄ʹʹ’ ").replace('ς','σ').replace('ϝ','ϛ')
    values=[GREEK_VALUES.get(c) for c in chars]
    if not values or None in values or any(a<=b for a,b in zip(values,values[1:])):
        return None
    return sum(values)


def letter_key(text):
    value=normalized(text)
    if value in {'alpha','αλφα'}:return 'α'
    # Latin-looking Greek letter headings are retained with explicit normalization.
    return value.translate(str.maketrans({'a':'α','b':'β','e':'ε','h':'η','i':'ι','k':'κ','m':'μ','n':'ν','o':'ο','p':'ρ','t':'τ','y':'υ'})) if len(value)==1 else value


def language(raw,block):
    attrs=raw[block['start']:block['body_start']]
    if 'script="grego"' in attrs:return 'grc'
    if 'script="latino"' in attrs:return 'lat'
    return 'grc' if sum('\u0370'<=c<='\u03ff' or '\u1f00'<=c<='\u1fff' for c in block['body'])>len(re.findall('[A-Za-z]',block['body'])) else 'lat'


def trim(raw,a,b):
    while a<b and raw[a].isspace():a+=1
    while b>a and raw[b-1].isspace():b-=1
    return a,b


def parse_internal_page(raw, *, family, source, volume, physical, scopes, section_context=None):
    if family in {'title_number', 'book_number', 'manuscript_homily'}:
        from tools.indexing.named_division_formats import parse_named_page
        return parse_named_page(raw, family=family, source=source, volume=volume,
                                physical=physical, scopes=scopes, section_context=section_context)
    if family in {'rule_number','section_number'}:
        from tools.indexing.ordinal_index_formats import parse_ordinal_page
        return parse_ordinal_page(raw,family=family,source=source,volume=volume,physical=physical,
                                  scopes=scopes,section_context=section_context)
    if family not in {'book_question','letter_title'}:
        raise ValueError('Unknown internal index family')
    contexts={k:dict(v) for k,v in (section_context or {}).items()}
    entries=[];residuals=[]
    for scope in scopes:
        lo,hi=scope['span'];sid=scope['section_id'];ctx=contexts.setdefault(sid,{})
        letters=ctx.setdefault('letters',{})
        highest=ctx.setdefault('highest_letters',{})
        previous=None
        for block in blocks(raw):
            a,b=max(lo,block['body_start']),min(hi,block['body_end'])
            if a>=b:continue
            text=raw[a:b];lang=language(raw,block)
            def emit(oa,ob,ra,rb,namespace,binding,flags=()):
                oa,ob=trim(raw,oa,ob)
                if oa>=ob:return
                entries.append({'source':source,'volume':volume,'physical':physical,'section_id':sid,
                    'entry_span':[min(oa,ra),max(ob,rb)],'owner_span':[oa,ob],'owner_raw':raw[oa:ob],
                    'reference_span':[ra,rb],'reference_raw':raw[ra:rb],'reference_namespace':namespace,
                    'entry_kind':'thematic_entry','grammar':family,'language':lang,
                    'internal_binding':binding,'flags':['literal_candidate_not_certified',*flags], 'parser':VERSION})
            if family=='book_question':
                if block['kind']!='texto_principal':continue
                matches=list(QUESTION.finditer(text))
                if matches and text[:matches[0].start()].strip():
                    residuals.append({'span':[a,a+matches[0].start()],'raw':text[:matches[0].start()], 'reason':'leading_continuation_or_heading','section_id':sid})
                for i,m in enumerate(matches):
                    end=a+(matches[i+1].start() if i+1<len(matches) else len(text))
                    oa=a+m.end();tail=raw[oa:end]
                    separator=re.search(r'(?m)^\s*(?:[A-Z]|\d{3,4})\s*$',tail)
                    if separator:end=oa+separator.start()
                    emit(oa,end,a+m.start(),a+m.end(),'book_question',
                         {'book':roman_to_int(m['book'].upper()),'numbers':[int(n) for n in re.findall(r'\d+',m['numbers'])]},
                         ['possible_continuation'] if raw[oa:end].rstrip().endswith(':') else [])
                if not matches:
                    residuals.append({'span':[a,b],'raw':text,'reason':'unparsed_or_continuation','section_id':sid})
                continue
            incoming=dict(letters)
            heading_context=[]
            for m in LETTER.finditer(text):
                key=letter_key(m['letter']);alphabet='αβγδεζηθικλμνξοπρστυφχψω'
                position=alphabet.find(key) if len(key)==1 else -1
                proof={'key':key,'source':source,'span':[a+m.start(),a+m.end()],'raw':m[0]}
                if position>=0 and position<highest.get(lang,-1):
                    proof.update(key=None,observed_key=key,uncertainty='letter_sequence_regression')
                elif position>=0:
                    highest[lang]=position
                letters[lang]=proof
                heading_context.append((m.start(),proof))
            if block['kind']=='nota_marginal':
                marker=text.strip();number=greek_number(marker) if lang=='grc' and re.search(r"[.'΄ʹʹ’]",marker) else None
                previous=(block,number,trim(raw,a,b)) if number else None
                continue
            if block['kind']!='texto_principal':
                previous=None;continue
            if previous and lang=='grc':
                marginal,num,(ra,rb)=previous
                # Only adjacent XML blocks: no searching across unrelated paragraphs.
                if not raw[marginal['end']:block['start']].strip() and lo<=ra<rb<=hi and not LETTER.search(text):
                    emit(a,b,ra,rb,'letter_title',{'letter':letters.get(lang),'numbers':[num]},['marginal_ordinal'])
                    previous=None;continue
            previous=None
            matches=list(ORDINAL.finditer(text))
            for i,m in enumerate(matches):
                n=(roman_to_int(m['number'].upper()) if lang=='lat' else greek_number(m['number']))
                if m['number'].isdigit():n=int(m['number'])
                if not n:continue
                oa=a+m.end();end=a+(matches[i+1].start() if i+1<len(matches) else len(text))
                boundary=CONTROL.search(raw[oa:end])
                if boundary:
                    tail=oa+boundary.start()
                    residuals.append({'span':[tail,end],'raw':raw[tail:end],'reason':'editorial_or_crossreference_tail','section_id':sid})
                    end=tail
                # A letter heading after this entry cannot supply its context.
                preceding=[proof for pos,proof in heading_context if pos<m.start()]
                local=incoming.get(lang)
                if preceding:
                    local=preceding[-1]
                emit(oa,end,a+m.start('number'),a+m.end('number'),'letter_title',{'letter':local,'numbers':[n]},
                     ['letter_sequence_regression'] if local and local.get('uncertainty') else [] if local else ['missing_letter_context'])
            if not matches:
                residuals.append({'span':[a,b],'raw':text,'reason':'unparsed_or_editorial','section_id':sid})
    return {'version':VERSION,'entries':entries,'residuals':residuals,'context':contexts}
