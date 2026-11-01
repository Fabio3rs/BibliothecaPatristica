"""Editable user-supplied tariffs and explicitly identified cost estimates.

Catalog estimates for old unpriced runs are read-time projections. They never
rewrite historical responses, configurations or request hashes.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from .common import digest, read_json

CATALOG_PATH = Path(__file__).with_name('prices.json')


def catalog():
    data = read_json(CATALOG_PATH)
    return dict(data, sha256=digest(data))


def lookup(config):
    data = catalog()
    for entry in data['models']:
        if config.get('provider') != entry['provider'] or config.get('model') not in entry['models']:
            continue
        if 'cloud_models' in entry and config['model'] not in entry['cloud_models']:
            if urlsplit(config.get('base_url', '')).hostname != 'ollama.com':
                continue
        return {**{k: data[k] for k in ('version', 'currency', 'unit', 'source', 'recorded_on', 'sha256')},
                'label': entry['label'], 'prices': entry['prices']}
    return None


def reference(config):
    prices = config.get('prices') or {}
    matched = lookup(config)
    if matched and prices == matched['prices']:
        return matched
    return {'source': 'explicit_rates' if prices else 'unpriced', 'currency': 'USD',
            'unit': 'million_tokens', 'prices': prices}


def estimate_cost(usage, prices):
    inp, out = usage.get('input_tokens'), usage.get('output_tokens')
    if inp is None or out is None or not {'input_per_million', 'output_per_million'} <= prices.keys():
        return None
    cached = min(inp, max(0, usage.get('cached_input_tokens') or 0))
    cached_rate = prices.get('cached_input_per_million', prices['input_per_million'])
    return ((inp - cached) * prices['input_per_million'] + cached * cached_rate + out * prices['output_per_million']) / 1_000_000


def run_estimate(run):
    result = run.get('result') or {}
    saved = result.get('cost_usd')
    ref = run['prepared'].get('pricing_reference')
    if saved is not None:
        return {'cost_usd': saved, 'basis': 'stored_estimate',
                'pricing': ref or {'source': 'original_config', 'currency': 'USD',
                                   'unit': 'million_tokens', 'prices': run['config'].get('prices', {})}}
    # New explicit empty prices disable estimation. Only older unpriced calls
    # receive the current catalog projection, including invalid/truncated output.
    if ref is None and result.get('inference_performed') and not run.get('cached_from'):
        ref = (reference(run['config']) if run['config'].get('prices') else lookup(run['config']))
        cost = estimate_cost(result.get('usage') or {}, ref['prices']) if ref else None
        if cost is not None:
            return {'cost_usd': cost, 'basis': 'catalog_reestimate' if not run['config'].get('prices') else 'original_rates_reestimate',
                    'pricing': ref}
    return {'cost_usd': None, 'basis': 'unavailable', 'pricing': ref}
