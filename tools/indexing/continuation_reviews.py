"""Validate narrowly scoped visual reviews; never rewrite OCR or numeric readings."""
import json
from pathlib import Path

from tools.indexing.entry_parser_experiment import digest

VERSION = 'index-continuation-reviews-v1'


def load_reviews(path):
    data = json.loads(Path(path).read_text())
    if data.get('version') != VERSION or not isinstance(data.get('reviews'), list):
        raise ValueError('Invalid continuation review manifest')
    return data['reviews']


def match_reviews(raw, source, proposals, reviews):
    """Revalidate exact proposals and image hashes, including deferred reviews."""
    def key(item):
        return item['fragment']['char_start'], item['number']['char_end']

    candidates = {key(p):p for p in proposals}
    matched = {}
    for review in reviews:
        if review['source'] != source or review['source_sha256'] != digest(raw.encode()):
            raise ValueError('Stale or mismatched continuation review source')
        span = key(review)
        if span in matched:
            raise ValueError('Duplicate continuation review')
        proposal = candidates.get(span)
        fields = ('fragment','number','proposed_entry_raw','proposed_reference_raw','reference_span')
        if proposal is None or any(review.get(f) != proposal[f] for f in fields):
            raise ValueError('Review no longer matches a scoped continuation proposal')
        visual = review['visual_review']
        if (visual.get('reviewer') not in {'assistant','human'}
                or visual.get('layout_status') not in {'supported','uncertain','rejected'}
                or visual.get('reference_reading') not in {'matches_ocr','conflicts_with_ocr','uncertain'}
                or visual.get('human_reviewed') is not (visual.get('reviewer') == 'human')
                or not isinstance(visual.get('image_locator'),str) or not visual['image_locator'].strip()
                or not isinstance(visual.get('note'),str) or not visual['note'].strip()):
            raise ValueError('Incomplete visual review evidence')
        image = Path(visual['image'])
        if digest(image.read_bytes()) != visual['image_sha256']:
            raise ValueError('Stale continuation review image')
        matched[span] = review
    return matched
