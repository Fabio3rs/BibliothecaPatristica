"""Read known OCR wrappers without rewriting text, offsets or image geometry."""
import re

from scripts.scan_editorial_ocr import ATTR, BLOCK

VERSION = 'editorial-ocr-blocks-v2'
NAMES = ('texto_principal|cabecalho|nota_marginal|nota|aparato_critico|'
         'apparato_critico|rodape|outro')
BRACKET = re.compile(r'\[(?P<tag>' + NAMES + r')\b(?P<attrs>[^\]]*)\]'
                     r'(?P<body>.*?)\[/(?P=tag)\]', re.S | re.I)
DIRECT = re.compile(r'<(?P<tag>' + NAMES + r')\b(?P<attrs>[^>]*)>'
                    r'(?P<body>.*?)</(?P=tag)>', re.S | re.I)


def blocks(raw):
    candidates = []
    for pattern, encoding in [(BLOCK, 'xml_bloco'), (BRACKET, 'bracket'), (DIRECT, 'xml_named')]:
        for match in pattern.finditer(raw):
            attrs = dict(ATTR.findall(match['attrs']))
            fallback = match['tag'] if encoding != 'xml_bloco' else 'unknown'
            # Some corpus pages use the English attribute with the same known values.
            english_type = attrs.get('type', '').lower()
            if re.fullmatch(NAMES, english_type):
                fallback = english_type
            kind = attrs.get('tipo', fallback)
            candidates.append({'start': match.start(), 'end': match.end(),
                               'body_start': match.start('body'), 'body_end': match.end('body'),
                               'body': match['body'], 'kind': kind.lower(), 'format': encoding})
    result = []
    for item in sorted(candidates, key=lambda x: (x['start'], -x['end'])):
        if result and item['start'] < result[-1]['end']:
            continue
        if item['kind'] == 'apparato_critico':
            item['kind'] = 'aparato_critico'
        result.append(item)
    return result


def empty_reason(raw):
    """Describe the source's declaration; never certify a visually empty page."""
    if re.search(r'estado\s*=\s*["\']vazio["\']', raw, re.I):
        return 'source_declares_empty'
    if re.search(r'nenhuma imagem|imagem (?:ausente|foi fornecida)|nao recebi', raw, re.I):
        return 'source_reports_missing_image'
    if re.search(r'em branco|sem texto (?:vis[ií]vel|leg[ií]vel)|n[aã]o h[aá] texto leg[ií]vel', raw, re.I):
        return 'source_reports_blank_or_illegible'
    return 'unrecognized_or_empty_markup'
