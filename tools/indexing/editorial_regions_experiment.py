"""Conservative, source-anchored editorial region identification; no inference."""
import argparse
import copy
import gzip
import inspect
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import html
import json
from pathlib import Path
import re
import unicodedata
import uuid

from scripts.scan_editorial_ocr import BLOCK, ATTR
from tools.corpus_utils import page_files, page_number
from tools.indexing.entry_parser_bulk import open_experiment
from tools.indexing.parser_target_exports import write_jsonl
from tools.indexing.title_page_experiment import VOLUME
from tools.indexing import editorial_ocr_blocks

VERSION = 'editorial-regions-v2'


def sha(value):
    return hashlib.sha256(value).hexdigest()


def fold(value):
    # Legacy OCR sometimes preserves typographic tags as [span] rather than XML.
    # Strip only known markup for recognition; raw text and offsets stay intact.
    value = re.sub(r'\[/?(?:span|p|b|i|strong|em)\]', ' ', value, flags=re.I)
    value = html.unescape(html.unescape(re.sub(r'<[^>]+>', ' ', value)))
    value = value.replace('æ', 'ae').replace('Æ', 'AE').replace('œ', 'oe').replace('Œ', 'OE')
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value)
                           if not unicodedata.combining(c)).upper().split())


def material_kind(text):
    """Classify an anchored title, never an arbitrary mention in prose."""
    t = re.sub(r'^(?:[IVXLCDM]+[.]\s+|\[?\d+\]?\s+)', '', fold(text))
    rules = [
        (r'^ORDO RERUM\b|^TABLE (?:ANALYTIQUE DES MATIERES|DES MATIERES|OF CONTENTS)\b|^SYLLABUS RERUM QUAE IN HOC VOLUMINE INCLUDUNTUR\b', 'contents_catalog'),
        (r'^FINIS (?:TOMI|VOLUMINIS|OPERIS)\b', 'terminal_boundary'),
        (r'^(?:ERRATA|CORRIGENDA|ADDENDA)\b', 'editorial_closure'),
        (r'^INDEX CAPITUM\b|^CAPITA\b|^ΚΕΦΑΛΑΙΑ\b|^ΕΚΘΕΣΙΣ ΤΩΝ ΚΕΦΑΛΑΙΩΝ\b', 'capitula'),
        (r'^ARGUMENTUM\b|^ΥΠΟΘΕΣΙΣ\b', 'work_summary'),
        (r'^INDEX ARGUMENTORUM\b', 'numbered_arguments'),
        (r'^INDEX SPURIORUM\b|^(?:INDEX|ELENCHUS) OPERUM\b|^ELENCHUS[.]?$', 'work_catalog'),
        (r'^TABLE (?:DE TOUS LES MOTS|DES (?:MOTS|EXPRESSIONS))\b|^INDEX (?:GRAECITATIS|GRAMMATICUS|VOCABULORUM|VERBORUM ET PHRASE)', 'lexical_index'),
        (r'^INDEX AUCTORUM\b', 'author_index'),
        (r'^INDEX (?:ONOMASTICUS\b|DES NOMS PROPRES\b)|^TABLE (?:(?:ALPHABETIQUE|GRECQUE|FRANCAISE|ETHIOPIENNE|SYRIAQUE|ARMENIENNE|ARABE) DES NOMS|DES NOMS)', 'onomastic_index'),
        (r'^TABLE ALPHABETIQUE DES MATIERES\b|^INDEX (?:RERUM|ANALYTICUS|VERBORUM,? SENTENTIARUM|IN VITAM|IN S[.]|HISTORICUS)', 'thematic_index'),
        (r'^TABLE DES (?:CITATIONS|PERICOPES|RENVOIS A L.ECRITURE|PASSAGES DE LA (?:BIBLE|SAINTE ECRITURE))|^INDEX (?:SCRIPTUR|LOCORUM SCRIPT|DES PASSAGES DE LA SAINTE ECRITURE)', 'scripture_index'),
        (r'^TABLE DE CONCORDANCE\b|^CONCORDIA EDITIONIS\b', 'concordance'),
        (r'^INDEX\b|^INDICES\b|^TABLE ALPHABETIQUE\b|^ΠΙΝΑΞ\b', 'index_unspecified'),
    ]
    return next((kind for pattern, kind in rules if re.search(pattern, t)), None)


def owner(kind):
    if kind in {'contents_catalog', 'editorial_closure', 'capitula', 'numbered_arguments', 'work_catalog', 'work_summary'}:
        return 'general'
    if kind and kind.endswith('_index'):
        return 'alphabetical'
    return 'uncertain'


def literal(raw, a, b):
    while a < b and raw[a].isspace():
        a += 1
    while b > a and raw[b-1].isspace():
        b -= 1
    return {'span': [a, b], 'quote': raw[a:b]}


def units(raw):
    """Preserve raw string offsets even when XML contains entities or inline tags."""
    rows = []
    for bi, block in enumerate(editorial_ocr_blocks.blocks(raw)):
        for line in re.finditer(r'[^\n]+', block['body']):
            item = literal(raw, block['body_start']+line.start(), block['body_start']+line.end())
            if not item['quote']:
                continue
            rows.append({**item, 'block': bi, 'block_type': block['kind'], 'source_format': block['format']})
    return rows


def header_opening_evidence(rows, index):
    """An unnumbered descriptive title plus a list can open a section.

    XML `cabecalho` describes placement, not editorial function. Join only
    consecutive heading rows, never principal prose. Bare INDEX/ΠΙΝΑΞ and
    numbered running heads remain insufficient, even above a dense list.
    """
    row = rows[index]
    if row['block_type'] != 'cabecalho':
        return None
    if any(r['block_type'] == 'texto_principal' for r in rows[:index]):
        return None
    heading = []
    for candidate in rows[index:]:
        if candidate['block_type'] != 'cabecalho':
            break
        heading.append(candidate)
    if not heading:
        return None
    title = ' '.join(fold(r['quote']) for r in heading)
    if re.search(r'\d', title):
        return None
    kind = material_kind(title)
    if title.startswith('ΠΙΝΑΞ') and 'ΚΑΤΑ ΑΛΦΑΒΗΤΟΝ' in title:
        kind = 'thematic_index'
    elif kind == 'index_unspecified':
        return None
    if kind not in {'thematic_index', 'onomastic_index', 'lexical_index',
                    'scripture_index', 'contents_catalog', 'work_catalog'}:
        return None
    body = []
    for candidate in rows[index + len(heading):]:
        if candidate['block_type'] == 'cabecalho':
            break
        if candidate['block_type'] == 'texto_principal':
            body.append(candidate)
        if len(body) >= 30:
            break
    locators = [r for r in body if re.search(r'\d[\d\s,.;:–—()\[\]_-]*$', fold(r['quote']))]
    if len(locators) < 2 or len(locators) / max(1, len(body)) < .15:
        return None
    return {'kind': kind, 'title_spans': [r['span'] for r in heading],
            'list_spans': [r['span'] for r in locators[:8]]}


def signals(raw):
    rows = units(raw)
    events = []
    for i, row in enumerate(rows):
        text = fold(row['quote'])
        # Titles can span several OCR lines. Use only the same block as context.
        same_block = [r['quote'] for r in rows[i:i+4] if r['block']==row['block']]
        kind = material_kind(text)
        if kind == 'index_unspecified' or text.rstrip('.') == 'SYLLABUS RERUM':
            kind = material_kind(' '.join(same_block))
        work_opening = is_work_opening(row)
        mentioned = re.search(r'\b(?:INDEX|INDICES|ORDO RERUM)\b', text)
        if not kind and not mentioned and not work_opening:
            continue
        before = [r for r in rows[:i] if r['block_type'] == 'texto_principal']
        after = rows[i+1:i+5]
        # Printed page numbers attached to a running title are a strong negative cue.
        numbered_header = row['block_type'] == 'cabecalho' and bool(re.search(r'\d', text))
        if row['block_type']=='cabecalho' and not before:
            numbered_header |= any(re.fullmatch(r'\d+[.]?',fold(r['quote']))
                                   for r in rows[max(0,i-2):i+3])
        # Bare titles at the top can be running titles. A subtitle/notice helps,
        # but only a later internal title can override a numbered running header.
        top_bare = (row['block_type'] == 'cabecalho' and not before
                    and len(text.split()) <= 5
                    and not any(re.search(r'^(?:QU[AE]|IN |RERUM |OPERUM |REVOCATUR|NUMER)', fold(r['quote']))
                                for r in after))
        catalog_locator = bool(re.search(r'\d[\d\s,–—-]*[.]?$', text))
        if not catalog_locator and kind:
            following = [r for r in after[:2] if r['block']==row['block']]
            catalog_locator = any(re.fullmatch(r'\d+(?:[-–]\d+)?[.]?',fold(r['quote'])) for r in following)
            if text.endswith(',') or (row['block_type']=='texto_principal' and not row['quote'].isupper()):
                catalog_locator |= any(re.search(r'\d+[.]?$',fold(r['quote'])) for r in following[:1])
        title_case = is_typographic_title(row['quote'])
        opening = header_opening_evidence(rows, i) if kind and not numbered_header else None
        if row['block_type'] in {'nota', 'aparato_critico'}:
            role, reason = 'mention', 'note_or_apparatus_mention'
        elif work_opening:
            role, reason = 'work_opening', 'explicit_work_opening_candidate'
        elif kind == 'terminal_boundary' and title_case:
            role, reason = 'section_end', 'explicit_terminal_title'
        elif opening:
            kind = opening['kind']
            role, reason = 'section_start', 'descriptive_unnumbered_header_with_reference_list'
        elif numbered_header or top_bare:
            role, reason = 'running_header', 'top_or_numbered_heading_is_not_a_section_boundary'
        elif not kind:
            role, reason = 'mention', 'unanchored_prose_mention'
        elif catalog_locator:
            role, reason = 'catalog_pointer', 'heading_like_line_ends_in_locator'
        elif not title_case and row['block_type'] not in {'cabecalho'}:
            role, reason = 'mention', 'ordinary_word_not_typographic_title'
        else:
            role, reason = 'section_start', 'anchored_internal_title_candidate'
        events.append({**row, 'kind': kind, 'role': role, 'reason': reason,
                       **({'opening_evidence': opening} if opening else {})})
    return rows, events


def is_typographic_title(text):
    letters = [c for c in html.unescape(re.sub(r'<[^>]+>', '', text)) if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > .65


def is_work_opening(row):
    text = fold(row['quote'])
    if row['block_type'] in {'nota', 'nota_marginal', 'aparato_critico', 'rodape'}:
        return False
    if not is_typographic_title(row['quote']) or len(text) > 160:
        return False
    # Exact titles avoid treating chapter citations in prose/running heads as starts.
    return bool(re.fullmatch(
        r'(?:MONITUM|ADMONITIO|PRAEFATIO|PROOEMIUM|PREFACE|INTRODUCTION|'
        r'DIALOGUS (?:PRIMUS|SECUNDUS|TERTIUS|[IVX]+)|'
        r'EPISTOLA (?:CATHOLICA|SECUNDA)|ΕΠΙΣΤΟΛΗ (?:ΚΑΘΟΛΙΚΗ|ΔΕΥΤΕΡΑ))[. ]*', text))


def continuation_evidence(rows, events, state):
    """Only the prefix before a new opening can support an inherited section."""
    stop = min((e['span'][0] for e in events
                if e['role'] in {'section_start', 'section_end', 'work_opening'}), default=float('inf'))
    prefix = [r for r in rows if r['span'][0] < stop]
    body = [r for r in prefix if r['block_type'] in {'texto_principal', 'outro'}]
    compatible = [e for e in events if e['span'][0] < stop and e['role'] == 'running_header'
                  and e['kind'] == state['kind']]
    locators = [r for r in body if re.search(r'\d[\d\s,.;:–—()\[\]a-d-]*$', fold(r['quote']))]
    numbered = [r for r in body if re.match(r'^(?:\d+|[IVXLCDM]+)[.)]\s+\S', fold(r['quote']))]
    list_rows = numbered if state['kind'] in {'capitula', 'numbered_arguments'} else locators
    if compatible and body:
        return {'reason': 'compatible_running_header', 'spans': [r['span'] for r in compatible]}
    generic_header = [e for e in events if e['span'][0] < stop and e['role'] == 'running_header'
                      and e['kind'] == 'index_unspecified']
    if generic_header and (state['kind'].endswith('_index') or state['kind'] == 'index_unspecified'):
        dense = [r for r in body if len(re.findall(r',\s*\d+(?:[ ,–-]+\d+)*[.;]', r['quote'])) >= 3]
        if dense:
            return {'reason': 'generic_index_header_with_inline_references',
                    'spans': [r['span'] for r in generic_header + dense[:3]]}
    if list_rows and len(list_rows) / max(1, len(body)) >= .15:
        if len(list_rows) >= 2 or len(body) <= 4:
            return {'reason': 'compatible_list_structure', 'spans': [r['span'] for r in list_rows[:8]]}
    return None


def local_facts(raw):
    rows, events = signals(raw)
    notices = []
    for block in editorial_ocr_blocks.blocks(raw):
        match = re.search(r'(?im)^\s*\(?(?:(?:Revocatur\b|Numerales\b|Numeri\b)[^.]{10,600}[.]|Hujusce\b[^\n]{10,600}\))', block['body'])
        if match and (notice := numbering_notice(match[0])):
            notices.append({**literal(raw, block['body_start']+match.start(), block['body_start']+match.end()),
                            'body_span': [block['body_start'], block['body_end']], 'namespace_hint': notice})
    return {'rows': rows, 'events': events, 'notices': notices,
            'empty_reason': editorial_ocr_blocks.empty_reason(raw) if not rows else None}


def numbering_notice(text):
    t = fold(text)
    if not re.search(r'EDITION|TEXTUI|TEXTU |TYPIS|LECTOR', t):
        return None
    if not re.search(r'\b(?:NUMER\w*|PAGIN\w*|CIFR\w*)\b', t):
        return None
    if not re.search(r'\b(?:REVOC\w*|RESPICI\w*|REPRAESENT\w*|INSERT\w*|INTERMIST\w*|EXPR\w*)\b', t):
        return None
    if 'SYLBURG' in t:
        return 'Sylburgiana'
    if 'PARISIENS' in t:
        return 'Parisiensis'
    if 'PETROPOL' in t:
        return 'Petropolitana'
    if 'VERON' in t:
        return 'Verona'
    if re.search(r'CRASSIOR|GRANDIOR', t):
        return 'internal_emphasized_numbers_edition_unknown'
    return 'numbering_declaration_unclassified'


def analyze_page(raw, source, volume, physical, state=None, facts=None):
    checksum = sha(raw.encode())
    facts = facts if facts is not None else local_facts(raw)
    rows, events = facts['rows'], copy.deepcopy(facts['events'])
    by_start = {e['span'][0]: e for e in events}
    regions, declarations = [], []
    state = dict(state) if state else None
    transitions = []
    if state:
        support = continuation_evidence(rows, events, state)
        reason = ('physical_gap' if state['last_physical'] != physical-1 else
                  'continuation_not_supported' if not support else None)
        if reason:
            transitions.append({'section_id': state['section_id'], 'reason': reason,
                                'at_span': rows[0]['span'] if rows else None, 'verified': False})
            state = None
        else:
            transitions.append({'section_id': state['section_id'], **support, 'verified': False})
    for row in rows:
        event = by_start.get(row['span'][0])
        if event and event['role'] in {'section_end', 'work_opening'}:
            transitions.append({'section_id': state['section_id'] if state else None,
                                'reason': event['reason'], 'at_span': row['span'], 'verified': False})
            state = None
        if event and event['role'] == 'section_start':
            alias = state and state['kind']==event['kind'] and not state.get('has_content',True)
            section_id = sha(f'{volume}:{source}:{checksum}:{row["span"][0]}'.encode())[:24]
            if alias:
                event['role']='section_title_alias'
            else:
                state = {'section_id': section_id, 'kind': event['kind'], 'heading': row['quote'],
                         'opening_source': source, 'opening_span': row['span'],
                         'last_physical': physical, 'namespace': None, 'has_content':False}
        is_context = (row['block_type'] in {'nota_marginal', 'rodape', 'cabecalho'}
                      and not (event and event['role'] in {'section_start','section_title_alias'}))
        # Some OCRs put the actual contents section in a footer block (PG100/777).
        if state and state['kind'] == 'contents_catalog' and row['block_type'] == 'rodape':
            is_context = False
        region = {**row, 'section_id': state['section_id'] if state and not is_context else None,
                  'kind': state['kind'] if state and not is_context else 'context' if is_context else 'unclassified',
                  'role': 'context' if is_context else 'candidate' if state else 'uncertain',
                  'pipeline_hint': owner(state['kind']) if state and not is_context else 'uncertain',
                  'namespace': state['namespace'] if state and not is_context else None,
                  'evidence': 'internal_title' if event and event['role']=='section_start' else
                              'sequence_from_title' if state and not is_context else 'insufficient',
                  'verified': False}
        regions.append(region)
        if state and not event and not is_context and row['block_type']=='texto_principal':
            state['has_content']=True
        # Inspect whole block for split-line editorial declarations; never map destinations.
    for declaration in facts['notices']:
        notice = declaration['namespace_hint']
        evidence = {k: declaration[k] for k in ['span', 'quote']}
        linked = [r for r in regions if declaration['body_span'][0] <= r['span'][0] < declaration['body_span'][1] and r['section_id']]
        if not linked:
            declarations.append({**evidence,
                                 'section_id':None,'namespace_hint':notice,'status':'scope_unassigned'})
            continue
        sid = linked[0]['section_id']
        namespaces = {r['namespace'] for r in regions if r['section_id']==sid and r['namespace']}
        value = notice if not namespaces or namespaces == {notice} else 'conflicting_declarations'
        declarations.append({**evidence,
                             'section_id': sid, 'namespace_hint':notice, 'status':'declaration_candidate'})
        for region in regions:
            if region['section_id']==sid:
                region['namespace']=value
        if state and state['section_id']==sid:
            state['namespace']=value
    if not rows:
        state = None
    if state:
        state['last_physical'] = physical
    return {'version': VERSION, 'source':source, 'source_sha256':checksum, 'volume':volume,
            'physical':physical, 'signals':events, 'regions':regions, 'declarations':declarations,
            'transitions':transitions, 'source_formats':sorted({r['source_format'] for r in rows}),
            'empty_reason':facts['empty_reason'],
            'issues': ['no_parseable_xml_blocks'] if not rows else [],
            'coverage_claim':'present_OCR_only_not_visual_completeness', 'import_ready':False}, state


def local_fingerprint():
    functions = [fold, material_kind, literal, units, header_opening_evidence, signals, is_typographic_title,
                 is_work_opening, numbering_notice, local_facts]
    code = '\n'.join(inspect.getsource(f) for f in functions)
    code += Path(editorial_ocr_blocks.__file__).read_text()
    code += BLOCK.pattern + ATTR.pattern
    return sha(code.encode())


def scan_volume(task):
    volume, paths = task[:2]
    options = task[2] if len(task) > 2 else {}
    cache_file, cache_issue, cached = None, None, {}
    fingerprint = local_fingerprint()
    if options.get('cache_dir'):
        cache_dir = Path(options['cache_dir'])
        cache_dir.mkdir(parents=True, exist_ok=True)
        identity = str(paths[0].parent.resolve()) if paths else volume
        cache_file = cache_dir / f'{volume}-{sha(identity.encode())[:16]}.json.gz'
        if options.get('resume') and cache_file.exists():
            try:
                with gzip.open(cache_file, 'rt', encoding='utf-8') as stream:
                    saved = json.load(stream)
                if saved.get('fingerprint') == fingerprint:
                    cached = saved['pages']
            except (OSError, EOFError, ValueError, KeyError) as exc:
                cache_issue = f'cache_unreadable:{type(exc).__name__}'
    prepared, new_cache = [], {}
    for path in paths:
        try:
            raw = path.read_bytes().decode('utf-8')
            source = str(path.resolve())
            checksum = sha(raw.encode())
            prior = cached.get(source)
            hit = bool(prior and prior.get('sha256') == checksum and isinstance(prior.get('facts'), dict))
            facts = prior['facts'] if hit else local_facts(raw)
            new_cache[source] = {'sha256': checksum, 'facts': facts}
            prepared.append((path, raw, facts, hit))
        except (OSError, UnicodeError) as exc:
            prepared.append((path, None, str(exc), False))
    if cache_file and new_cache:
        cached.update(new_cache)
        temporary = cache_file.with_name(cache_file.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with gzip.open(temporary, 'wt', encoding='utf-8', compresslevel=1) as stream:
                json.dump({'fingerprint': fingerprint, 'pages': cached}, stream, ensure_ascii=False)
            temporary.replace(cache_file)
        finally:
            temporary.unlink(missing_ok=True)
    # Second pass rebuilds all dependencies in order, including after a cached page changes.
    records, state = [], None
    for path, raw, facts, hit in prepared:
        if raw is None:
            records.append({'volume':volume, 'source':str(path.resolve()), 'physical':page_number(path),
                            'issues':[facts], 'regions':[], 'signals':[], 'declarations':[]})
            state = None
        else:
            record, state = analyze_page(raw, str(path.resolve()), volume, page_number(path), state, facts)
            record['local_cache_hit'] = hit
            record['local_fingerprint'] = fingerprint
            if cache_issue:
                record['cache_issue'] = cache_issue
            records.append(record)
    return records


def inventory(args):
    selected = set(args.volume or [])
    if getattr(args, 'all_volumes', False):
        selected.update(p.name for p in args.root.iterdir() if p.is_dir() and VOLUME.fullmatch(p.name))
    explicit = {}
    if args.sample_manifest:
        selected.update(json.loads(args.sample_manifest.read_text())['selected'])
    for spec in args.page or []:
        volume, number = spec.split(':')
        explicit.setdefault(volume, set()).add(int(number))
        selected.add(volume)
    if not selected:
        raise ValueError('Select --volume, --page or --sample-manifest; no implicit corpus-wide scan')
    tasks, issues = [], []
    for volume in sorted(selected):
        if not VOLUME.fullmatch(volume):
            raise ValueError(f'Invalid volume: {volume}')
        by_number = page_files(args.root/volume/'text', suffixes=['.txt'])
        numbers = sorted(n for n in by_number if n is not None)
        if not numbers:
            issues.append({'volume':volume,'issue':'no_ocr_files'})
        wanted = set(numbers) if getattr(args, 'all_pages', False) else set(numbers[-args.tail_pages:]) if args.tail_pages else set()
        for n in [] if getattr(args,'all_pages',False) else explicit.get(volume, []):
            wanted.update(range(max(1,n-args.context_pages),n+args.context_pages+1))
        if wanted and args.expand_before and not getattr(args,'all_pages',False):
            wanted.update(range(max(1,min(wanted)-args.expand_before),min(wanted)))
        good = []
        for n in sorted(wanted):
            matches = by_number.get(n, [])
            if len(matches)!=1:
                issues.append({'volume':volume,'physical':n,'issue':'missing_or_ambiguous_source'})
            else:
                good.append(matches[0])
        tasks.append((volume, good))
    return tasks, issues


def completed_volumes(tasks, workers, executor, options=None):
    """Bound submitted work as well as resident results; preserve order within a volume."""
    cls = ProcessPoolExecutor if executor == 'process' else ThreadPoolExecutor
    pending_tasks = iter((v, paths, options or {}) for v, paths in tasks)
    with cls(max_workers=workers) as pool:
        pending = {}
        for task in pending_tasks:
            pending[pool.submit(scan_volume, task)] = task[0]
            if len(pending) == workers:
                break
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                volume = pending.pop(future)
                yield volume, future.result()
                task = next(pending_tasks, None)
                if task is not None:
                    pending[pool.submit(scan_volume, task)] = task[0]


def group_sections(pages):
    sections = {}
    for page in pages:
        for region in page['regions']:
            sid = region['section_id']
            if not sid:
                continue
            sec = sections.setdefault(sid, {'section_id':sid,'volume':page['volume'],
                'kind':region['kind'],'pipeline_hint':region['pipeline_hint'],'spans':[],
                'namespace_hints':[], 'verified':False, 'import_ready':False,
                'end_status':'last_observed_span_not_verified_section_end'})
            sec['spans'].append({'source':page['source'],'source_sha256':page['source_sha256'],
                                 'physical':page['physical'],'span':region['span']})
            if region['namespace'] and region['namespace'] not in sec['namespace_hints']:
                sec['namespace_hints'].append(region['namespace'])
    return sections


def reconstruction_fingerprint():
    code = '\n'.join(inspect.getsource(f) for f in
                     [analyze_page, continuation_evidence, owner, group_sections])
    return sha(code.encode())


def run(args):
    if args.workers < 1 or any(getattr(args,k)<0 for k in ['tail_pages','context_pages','expand_before']):
        raise ValueError('Workers must be positive and windows nonnegative')
    tasks, issues = inventory(args)
    args.out.mkdir(parents=True,exist_ok=True)
    if any((args.out/name).exists() for name in ['summary.json','progress.json','pages.jsonl']):
        raise ValueError('Output already contains a run; use a new directory')
    run_id = uuid.uuid4().hex
    summary = {'version':VERSION,'run_id':run_id,'status':'running',
               'scope':'all_pages' if getattr(args,'all_pages',False) else 'bounded',
               'volumes':len(tasks),'completed_volumes':0,'pages':0,
               'planned_pages':sum(len(paths) for _,paths in tasks),
               'section_candidates':0,'kinds':{},'signal_roles':{},
               'read_errors':0, 'pages_without_xml_blocks':0,
               'local_cache_hits':0,'source_formats':{},'empty_reasons':{},'transition_reasons':{},
               'local_fingerprint':local_fingerprint(),
               'reconstruction_fingerprint':reconstruction_fingerprint(),
               'inventory_issues':len(issues),
               'db_updates_production':0,'import_ready':False,
               'limitations':['OCR-only identification; missing visual content cannot be detected.',
                              'Candidate spans and continuity, not certified ownership or destinations.',
                              'Selection does not certify section endpoints or visual completeness.']}
    # Shared experiment marker rejects production and unrelated SQLite databases.
    con = open_experiment(args.experiment_db, args.source_db)
    try:
        con.executescript('''CREATE TABLE IF NOT EXISTS editorial_region_runs
            (run_id TEXT PRIMARY KEY, summary_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS editorial_region_pages
            (run_id TEXT, source TEXT, result_json TEXT NOT NULL, PRIMARY KEY(run_id,source));''')
        con.execute('INSERT INTO editorial_region_runs VALUES (?,?)',(run_id,json.dumps(summary)))
        con.commit()
        write_jsonl(args.out/'issues.jsonl',issues)
        (args.out/'config.json').write_text(json.dumps(vars(args),default=str,ensure_ascii=False,indent=2)+'\n')
        kinds, roles, report_rows, volume_summaries = Counter(), Counter(), [], []
        formats, empty_reasons, transitions = Counter(), Counter(), Counter()
        with (args.out/'pages.jsonl').open('w',encoding='utf-8') as page_stream, \
             (args.out/'sections.jsonl').open('w',encoding='utf-8') as section_stream, \
             (args.out/'page_issues.jsonl').open('w',encoding='utf-8') as issue_stream:
            cache_options = {'cache_dir':getattr(args,'cache_dir',None), 'resume':getattr(args,'resume',False)}
            for volume, pages in completed_volumes(tasks,args.workers,getattr(args,'executor','thread'),cache_options):
                sections = group_sections(pages)
                record = {'volume':volume,'pages':len(pages),'section_candidates':len(sections),
                          'read_errors':sum('source_sha256' not in p for p in pages),
                          'pages_without_xml_blocks':sum('no_parseable_xml_blocks' in p['issues'] for p in pages),
                          'local_cache_hits':sum(p.get('local_cache_hit',False) for p in pages),
                          'kinds':dict(Counter(s['kind'] for s in sections.values()))}
                volume_summaries.append(record)
                for k in ['pages','section_candidates','read_errors','pages_without_xml_blocks','local_cache_hits']:
                    summary[k] += record[k]
                kinds.update(record['kinds'])
                with con:
                    for page in pages:
                        serialized = json.dumps(page,ensure_ascii=False)
                        page_stream.write(serialized+'\n')
                        con.execute('INSERT INTO editorial_region_pages VALUES (?,?,?)',(run_id,page['source'],serialized))
                        roles.update(e['role'] for e in page['signals'])
                        formats.update(page.get('source_formats',[]))
                        if page.get('empty_reason'):
                            empty_reasons.update([page['empty_reason']])
                        transitions.update(t['reason'] for t in page.get('transitions',[]))
                        if page['issues']:
                            issue_stream.write(json.dumps({k:page[k] for k in ['volume','physical','source','issues']},ensure_ascii=False)+'\n')
                    summary.update(completed_volumes=summary['completed_volumes']+1,kinds=dict(kinds),signal_roles=dict(roles))
                    summary.update(source_formats=dict(formats),empty_reasons=dict(empty_reasons),transition_reasons=dict(transitions))
                    con.execute('UPDATE editorial_region_runs SET summary_json=? WHERE run_id=?',(json.dumps(summary),run_id))
                for section in sections.values():
                    section_stream.write(json.dumps(section,ensure_ascii=False)+'\n')
                    report_rows.append((volume,section['spans'][0]['physical'],section['spans'][0]['span'][0],
                        f"| {volume} | {section['kind']} | {section['spans'][0]['physical']}–{section['spans'][-1]['physical']} | {', '.join(section['namespace_hints']) or 'desconhecido'} |"))
                for stream in [page_stream,section_stream,issue_stream]:
                    stream.flush()
                (args.out/'progress.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
                if getattr(args,'verbose',False):
                    print(json.dumps({'progress':True,'volume':volume,'completed_volumes':summary['completed_volumes'],
                                      'volumes':len(tasks),'pages':summary['pages'],'planned_pages':summary['planned_pages'],
                                      'read_errors':summary['read_errors'],'local_cache_hits':summary['local_cache_hits']}),flush=True)
        summary['status']='completed'
        with con:
            con.execute('UPDATE editorial_region_runs SET summary_json=? WHERE run_id=?',(json.dumps(summary),run_id))
    except BaseException:
        summary['status']='interrupted_or_failed'
        with con:
            con.execute('UPDATE editorial_region_runs SET summary_json=? WHERE run_id=?',(json.dumps(summary),run_id))
        (args.out/'progress.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        raise
    finally:
        con.close()
    write_jsonl(args.out/'volumes.jsonl', sorted(volume_summaries,key=lambda r:r['volume']))
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (args.out/'progress.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines=['# Identificação mecânica de regiões editoriais', '',
           'Candidatos textuais; nenhuma certificação visual, resolução de destino ou importação.', '',
           '| Volume | Tipo | Primeiro–último arquivo observado | Namespace candidato |',
           '|---|---|---|---|']
    lines.extend(row[-1] for row in sorted(report_rows))
    (args.out/'report.md').write_text('\n'.join(lines)+'\n')
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('teste'))
    p.add_argument('--volume',action='append')
    p.add_argument('--all-volumes',action='store_true',help='All PG/PL/PO volume directories')
    p.add_argument('--all-pages',action='store_true',help='All available physical OCR files; overrides bounded windows')
    p.add_argument('--sample-manifest',type=Path)
    p.add_argument('--page',action='append',help='VOLUME:PHYSICAL, repeatable')
    p.add_argument('--tail-pages',type=int,default=24)
    p.add_argument('--context-pages',type=int,default=1)
    p.add_argument('--expand-before',type=int,default=0,help='Explicit bounded expansion before selected window')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--executor',choices=['thread','process'],default='thread')
    p.add_argument('--verbose',action='store_true')
    p.add_argument('--cache-dir',type=Path,default=Path('data/index_entry_parser/editorial_region_cache'))
    p.add_argument('--resume',action='store_true',help='Reuse compatible local facts; rebuild regions in a NEW output directory')
    p.add_argument('--source-db',type=Path,default=Path('data/alphabetical_indices.db'),help='Protected path; never opened for writing')
    p.add_argument('--experiment-db',type=Path,default=Path('data/index_entry_parser/editorial_regions.sqlite'))
    p.add_argument('--out',type=Path,required=True)
    args = p.parse_args()
    try:
        print(json.dumps(run(args),ensure_ascii=False,indent=2))
    except ValueError as exc:
        p.error(str(exc))


if __name__ == '__main__':
    main()
