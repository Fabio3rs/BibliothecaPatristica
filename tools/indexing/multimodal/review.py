"""Explicit, hash-bound offline annotations; preserve the paid response verbatim."""
from copy import deepcopy
import re

from .contract import digest


def reviewed_response(response, task, response_sha256, review):
    if review.get('version') != 1 or review.get('task_id') != task['task_id']:
        raise ValueError('Review version/task mismatch')
    if review.get('source_response_sha256') != response_sha256:
        raise ValueError('Review response hash mismatch')
    primary = task['pages'][0]
    if review.get('ocr_sha256') != primary['ocr_sha256']:
        raise ValueError('Review OCR hash mismatch')
    full = next((r for r in primary['regions'] if r['transformation'] == 'none'), None)
    if not full or review.get('image_sha256') != full['sha256']:
        raise ValueError('Review image hash mismatch')
    if review.get('basis') not in {'assistant_visual_inspection', 'human_visual_review'} or not review.get('reviewer'):
        raise ValueError('Review requires an identified visual reviewer')
    for ev in review.get('visual_evidence', []):
        box = ev.get('crop_box', [])
        if (len(box) != 4 or not all(isinstance(n, int) for n in box) or
                not 0 <= box[0] < box[2] <= full['width'] or not 0 <= box[1] < box[3] <= full['height']):
            raise ValueError('Review crop outside supplied primary image')
    if not review.get('visual_evidence'):
        raise ValueError('Review needs visual evidence')
    result = deepcopy(response)
    seen = set()
    for operation in review.get('operations', []):
        path = operation['path']
        if path in seen or not re.fullmatch(r'/(?:uncertainties|sections/\d+/entries/\d+(?:/children/\d+)*/(?:continuation|uncertainties))', path):
            raise ValueError('Duplicate or unsupported review operation')
        seen.add(path)
        node = result['dto']
        parts = path.strip('/').split('/')
        try:
            for part in parts[:-1]:
                node = node[int(part)] if isinstance(node, list) else node[part]
            if node[parts[-1]] != operation['before']:
                raise ValueError('Review precondition differs from original reading')
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError('Review path missing from original reading') from exc
        if not operation.get('reason'):
            raise ValueError('Review operation requires a reason')
        node[parts[-1]] = deepcopy(operation['after'])
    result['offline_review'] = {'review_sha256': digest(review), 'source_response_sha256': response_sha256,
                                'basis': review['basis'], 'reviewer': review['reviewer']}
    return result
