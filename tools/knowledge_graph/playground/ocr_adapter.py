"""Lossless visible block text over the repository's tolerant OCR diagnostics.

The source snapshot retains all visible text, including notes and unknown types.
Model preparation dehyphenates a separate copy of each block's text only.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from html.parser import HTMLParser

from tools.ocr_xml_utils import join_linebreak_hyphenation, parse_ocr_xml_page
from .common import digest

PARSER_VERSION = 'kg-ocr-blocks-1'
MODEL_TEXT_VERSION = 'kg-ocr-model-text-1'


def prepare_model_ocr(ocr):
    """Keep block structure/metadata; join line-break hyphenation inside text.

    The original XML and parsed source stay in the case snapshot. Auditing is
    stored separately from the model payload, without duplicating source text.
    """
    prepared = deepcopy(ocr)
    audit = {'version': MODEL_TEXT_VERSION,
             'operation': 'tools.ocr_xml_utils.join_linebreak_hyphenation',
             'scope': 'within_each_block', 'changed_blocks': 0, 'blocks': []}
    for block in prepared['blocks']:
        original = block['text']
        block['text'] = join_linebreak_hyphenation(original)
        changed = block['text'] != original
        audit['changed_blocks'] += int(changed)
        audit['blocks'].append({'id': block['id'], 'changed': changed,
                                'source_text_sha256': digest(original),
                                'model_text_sha256': digest(block['text']),
                                'removed_characters': len(original) - len(block['text'])})
    return prepared, audit


def bbox_value(value):
    try:
        values = [float(x) for x in re.split(r'[,\s]+', value.strip(' []()')) if x]
    except (ValueError, AttributeError):
        return None
    if len(values) != 4 or not all(math.isfinite(x) for x in values):
        return None
    x1, y1, x2, y2 = values
    return values if 0 <= x1 < x2 and 0 <= y1 < y2 else None


class TolerantBlocks(HTMLParser):
    """Retain every visible text chunk, even under broken/unknown markup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks, self.stack = [], []
        self.owner = None

    def handle_starttag(self, tag, attrs):
        self.stack.append((tag, dict(attrs)))
        self.owner = None

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        self.owner = None

    def handle_data(self, data):
        if not data.strip() and self.owner is None:
            return
        if self.owner is None:
            tag, attrs = self.stack[-1] if self.stack else ('legacy', {})
            self.chunks.append([tag, attrs, ''])
            self.owner = len(self.chunks) - 1
        self.chunks[self.owner][2] += data


def parse_blocks(raw):
    diagnostics = parse_ocr_xml_page(raw, repair_non_xml_ampersands=True)
    warnings, chunks, attrs = [], [], {}
    if raw.lstrip().startswith('<'):
        try:
            root = ET.fromstring(raw.strip())
            attrs = dict(root.attrib)

            def visit(node):
                tag = node.tag.rsplit('}', 1)[-1]
                # Containers hold block children; inline markup remains in its block.
                children = list(node)
                has_blocks = any(c.tag.rsplit('}', 1)[-1] in {'bloco', 'cabecalho', 'rodape', 'notas'} for c in children)
                if tag == 'pagina' or has_blocks:
                    if node.text and node.text.strip():
                        chunks.append((tag, dict(node.attrib), node.text))
                    for child in children:
                        visit(child)
                        if child.tail and child.tail.strip():
                            chunks.append((tag, dict(node.attrib), child.tail))
                else:
                    chunks.append((tag, dict(node.attrib), ''.join(node.itertext())))
            visit(root)
        except ET.ParseError:
            fallback = TolerantBlocks()
            fallback.feed(raw)
            fallback.close()
            chunks = fallback.chunks
            attrs = diagnostics.attrs
            warnings.append('malformed_xml: tolerant chunks; block boundaries require review')
    else:
        chunks = [('legacy', {}, raw)] if raw.strip() else []
        warnings.append('legacy_plain_text: no source block geometry')
    blocks = []
    for tag, meta, text in chunks:
        if not text.strip():
            continue
        bbox = bbox_value(meta.get('bbox', ''))
        if meta.get('bbox') and bbox is None:
            warnings.append(f'invalid_bbox:{len(blocks) + 1}')
        blocks.append({'id': f'b{len(blocks) + 1}', 'type': meta.get('tipo') or tag,
                       'tag': tag, 'script': meta.get('script', ''), 'bbox': bbox,
                       'bbox_raw': meta.get('bbox', ''), 'text': text, 'attrs': meta})
    if not blocks:
        warnings.append('empty_ocr')
    return {'blocks': blocks, 'page_attributes': attrs, 'warnings': warnings,
            'parser_version': PARSER_VERSION,
            'bbox_coordinate_system': 'corpus_normalized_1000',
            'bbox_note': 'Repository OCR convention (main2.py); verify geometry against image.'}
