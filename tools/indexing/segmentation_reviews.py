"""Exact-source paragraph partitions reviewed on a facsimile, for staging only."""
import json
from pathlib import Path

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import digest, paragraphs, source_span

VERSION = 'index-segmentation-reviews-v1'


def load_reviews(path):
    data = json.loads(Path(path).read_text())
    if data.get('version') != VERSION or not isinstance(data.get('reviews'),list):
        raise ValueError('Invalid segmentation review manifest')
    return data['reviews']


def validate_reviews(raw, source, scopes, reviews):
    parents = set()
    for block in blocks(raw):
        if block['kind'] != 'texto_principal':
            continue
        for scope in scopes:
            a,b = max(block['body_start'],scope['span'][0]),min(block['body_end'],scope['span'][1])
            if a<b:
                parents.update((a+off,a+off+len(text)) for off,text in paragraphs(raw[a:b]))
    result = {}
    for review in reviews:
        if review['source']!=source or review['source_sha256']!=digest(raw.encode()):
            raise ValueError('Stale segmentation review source')
        parent=review['parent']; a,b=parent['char_start'],parent['char_end']
        if (a,b) not in parents or (a,b) in result:
            raise ValueError('Duplicate or unscoped paragraph review')
        if parent != {'raw':raw[a:b],**source_span(raw,a,b)}:
            raise ValueError('Stale segmentation parent span')
        visual=review['visual_review']
        if (visual.get('reviewer') not in {'assistant','human'}
                or visual.get('human_reviewed') is not (visual.get('reviewer')=='human')
                or visual.get('status')!='group_boundaries_supported'
                or not visual.get('image_locator') or not visual.get('note')
                or digest(Path(visual['image']).read_bytes())!=visual['image_sha256']):
            raise ValueError('Invalid segmentation visual evidence')
        cuts=review['starts']
        if len(cuts)<2 or [c['char_start'] for c in cuts]!=sorted(set(c['char_start'] for c in cuts)):
            raise ValueError('Partition starts must be sorted and unique')
        if cuts[0]['char_start']!=a:
            raise ValueError('Partition must preserve the whole paragraph')
        for cut in cuts:
            x,y=cut['char_start'],cut['char_end']
            if not a<=x<y<=b or cut!={'raw':raw[x:y],**source_span(raw,x,y)}:
                raise ValueError('Invalid segmentation start evidence')
            if x!=a and raw[raw.rfind('\n',a,x)+1:x].strip():
                raise ValueError('Reviewed starts must be at an OCR line start')
        result[a,b]=review
    return result


def partition(raw, review):
    starts=[c['char_start'] for c in review['starts']]+[review['parent']['char_end']]
    for a,b in zip(starts,starts[1:]):
        while b>a and raw[b-1].isspace():
            b-=1
        yield a,b
