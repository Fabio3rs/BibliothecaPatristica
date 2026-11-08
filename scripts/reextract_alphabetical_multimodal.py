#!/usr/bin/env python3
"""HTTP-only page re-extraction. Default and --dry-run never modify SQLite."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.indexing.multimodal.contract import PILOT, digest
from tools.indexing.multimodal.http import Config
from tools.indexing.multimodal.runner import run
from tools.indexing.multimodal.sources import sequence


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--volume')
    p.add_argument('--page', type=int, action='append', default=[])
    p.add_argument('--pages', help='Inclusive physical range, e.g. 651-654')
    p.add_argument('--pilot', choices=['problematic-12', 'literal-20'])
    p.add_argument('--extraction-profile', choices=['reading-v2', 'literal-v1'], default='reading-v2')
    p.add_argument('--exclude-page', action='append', default=[], metavar='VOLUME:PHYSICAL',
                   help='Exclude an already examined physical page from the selection; repeatable')
    p.add_argument('--tail-pages', type=int, default=8)
    p.add_argument('--root', type=Path, default=ROOT/'teste')
    p.add_argument('--db', type=Path, default=ROOT/'data/alphabetical_indices.db')
    p.add_argument('--provider', default='openai')
    p.add_argument('--model', default=os.getenv('LLM_MODEL', 'gpt-5.4-mini'))
    p.add_argument('--base-url', default=os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1'))
    p.add_argument('--api-key-env')
    p.add_argument('--api-mode', choices=['chat', 'responses'], default='chat')
    p.add_argument('--service-tier', choices=['flex', 'omit'], default='flex')
    p.add_argument('--reasoning-effort', choices=['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'omit'], default='medium',
                   help='Reasoning effort; omit explicitly for providers without this capability')
    p.add_argument('--image-detail', choices=['original', 'high', 'low', 'auto'], default='original')
    p.add_argument('--structured-output', choices=['json-schema', 'json-object'], default='json-schema',
                   help='json-object is an explicit provider compatibility choice; local schema validation remains mandatory')
    p.add_argument('--mode', choices=['ocr-only', 'ocr-image', 'ocr-image-crops'], default='ocr-image')
    p.add_argument('--neighbors', type=int, choices=[0, 1], default=1)
    p.add_argument('--context-images', nargs='?', const='always', choices=['auto', 'always', 'never'], default='auto',
                   help='Auto selects neighbor images from OCR edge/heading evidence; bare flag means always')
    p.add_argument('--fragment-request', type=Path, help='One generated fragment request; no whole-page re-extraction')
    p.add_argument('--expansion-request', type=Path, help='Follow one bounded discovery request explicitly')
    p.add_argument('--limit', type=int, default=12, help='New inference tasks, excluding cached responses; retries counted separately')
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--max-rps', type=float, default=1)
    p.add_argument('--max-seconds', type=float, default=7200)
    p.add_argument('--max-cost-usd', type=float, default=2)
    p.add_argument('--reserve-output-tokens', type=int, default=131072,
                   help='Budget estimate only; no output token cap is sent to the API')
    p.add_argument('--timeout', type=float, default=900, help='Flex-compatible per-attempt timeout, also bounded by run deadline')
    p.add_argument('--max-retries', type=int, default=2)
    p.add_argument('--input-usd-per-million', type=float)
    p.add_argument('--output-usd-per-million', type=float)
    p.add_argument('--cached-input-usd-per-million', type=float)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--no-api', action='store_true')
    p.add_argument('--dry-run', action='store_true', help='Explicit staging mode; DB is read-only even without this flag')
    p.add_argument('--show-payload', action='store_true', help='Print request preview with image hashes instead of base64')
    p.add_argument('--verbose', action='store_true')
    p.add_argument('--out', type=Path, required=True)
    return p


def selections(args):
    if args.expansion_request:
        if args.fragment_request or args.pilot or args.volume or args.pages or args.page:
            raise ValueError('expansion-request cannot be combined with page selectors')
        request = json.loads(args.expansion_request.read_text())
        if digest(Path(request['anchor_ocr_file']).read_bytes()) != request['anchor_sha256']:
            raise ValueError('expansion anchor has changed')
        if not 1 <= len(request['pages']) <= 2 or not re.fullmatch(r'(?:PG|PL|PO)\d{3}(?:\.\d{2})?', request['volume']):
            raise ValueError('Invalid bounded expansion request')
        return [(request['volume'], n) for n in request['pages'] if isinstance(n, int) and n > 0]
    if args.fragment_request:
        if args.pilot or args.volume or args.pages or args.page:
            raise ValueError('fragment-request cannot be combined with page selectors')
        fragment = json.loads(args.fragment_request.read_text())
        args.fragment_line_ids = fragment['line_ids']
        return [(fragment['volume'], fragment['physical_page'])]
    if args.pilot:
        if args.volume or args.page or args.pages:
            raise ValueError('pilot cannot be combined with page selectors')
        pilot = PILOT
        if args.pilot == 'literal-20':
            from tools.indexing.multimodal.literal import PILOT as pilot
        return [(volume, number) for volume, numbers in pilot.items() for number in sorted(numbers, reverse=True)]
    if not args.volume or not re.fullmatch(r'(?:PG|PL|PO)\d{3}(?:\.\d{2})?', args.volume):
        raise ValueError('Select --pilot or a valid --volume')
    numbers = set(args.page)
    if args.pages:
        match = re.fullmatch(r'(\d+)-(\d+)', args.pages)
        if not match or int(match[1]) > int(match[2]):
            raise ValueError('--pages requires an ascending inclusive physical range')
        numbers.update(range(int(match[1]), int(match[2])+1))
    if not numbers:
        numbers = {sequence(p) for p in (args.root/args.volume/'text').glob('*.txt')}
        numbers.discard(None)
        numbers = set(sorted(numbers, reverse=True)[:args.tail_pages])
    if not numbers or min(numbers) < 1 or len(numbers) > 200:
        raise ValueError('Select between 1 and 200 positive physical pages per invocation')
    return [(args.volume, n) for n in sorted(numbers, reverse=True)]


def exclude_pages(selected, exclusions):
    excluded = set()
    for value in exclusions:
        match = re.fullmatch(r'((?:PG|PL|PO)\d{3}(?:\.\d{2})?):([1-9]\d*)', value)
        if not match:
            raise ValueError('--exclude-page requires VOLUME:PHYSICAL, e.g. PG113:653')
        excluded.add((match[1], int(match[2])))
    if excluded - set(selected):
        raise ValueError('--exclude-page must name a page in the current selection')
    retained = [item for item in selected if item not in excluded]
    if not retained:
        raise ValueError('No pages remain after --exclude-page')
    return retained


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if args.pilot == 'literal-20' and args.extraction_profile != 'literal-v1':
            raise ValueError('--pilot literal-20 requires --extraction-profile literal-v1')
        if args.extraction_profile == 'literal-v1':
            if args.mode != 'ocr-image' or args.neighbors != 0 or args.context_images != 'never':
                raise ValueError('literal-v1 requires --mode ocr-image --neighbors 0 --context-images never')
            if args.fragment_request or args.expansion_request:
                raise ValueError('literal-v1 transcribes complete primary pages; fragment/expansion requests are separate work')
        if args.limit < 0 or not 1 <= args.workers <= 32 or not 0 <= args.max_retries <= 5:
            raise ValueError('limit >= 0, workers 1..32, retries 0..5 required')
        if (not all(math.isfinite(v) for v in (args.max_seconds, args.max_cost_usd, args.max_rps))
                or args.max_seconds <= 0 or args.max_cost_usd < 0 or args.max_rps < 0 or args.tail_pages < 1):
            raise ValueError('Invalid time, budget, rate or tail size')
        for value in (args.input_usd_per_million, args.output_usd_per_million, args.cached_input_usd_per_million):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError('Token prices must be nonnegative')
        config = Config(provider=args.provider, model=args.model, base_url=args.base_url,
            api_mode=args.api_mode, image_detail=args.image_detail,
            service_tier=None if args.service_tier == 'omit' else args.service_tier,
            reasoning_effort=None if args.reasoning_effort == 'omit' else args.reasoning_effort,
            api_key_env=args.api_key_env, structured_output=args.structured_output,
            reserve_output_tokens=args.reserve_output_tokens, timeout=args.timeout,
            input_price=args.input_usd_per_million, output_price=args.output_usd_per_million,
            cached_input_price=args.cached_input_usd_per_million)
        config.validate()
        selected = exclude_pages(selections(args), args.exclude_page)
        summary = run(args, config, selected)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        failures = {'validation_failed', 'partial_extraction', 'canonical_validation_failed', 'staging_error', 'source_changed', 'api_error', 'invalid_response', 'refused', 'incomplete'}
        return 1 if summary.get('preparation_errors') or failures.intersection(summary.get('statuses', {})) else 0
    except (ValueError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
