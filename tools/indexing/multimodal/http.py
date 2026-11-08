"""OpenAI-compatible HTTP transports; one bounded HTTP attempt per worker call."""
import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests

from .contract import digest, encoded
from .reading import SCHEMA, VERSION, editorial_hint

PRICING_SOURCE = 'https://developers.openai.com/api/docs/pricing'
PRICING_CHECKED = '2026-11-03'


@dataclass
class Config:
    provider: str = 'openai'
    model: str = 'gpt-5.4-mini'
    base_url: str = 'https://api.openai.com/v1'
    api_mode: str = 'chat'
    image_detail: str = 'original'
    service_tier: str | None = 'flex'
    reasoning_effort: str | None = 'medium'
    api_key_env: str | None = None
    structured_output: str = 'json-schema'
    reserve_output_tokens: int = 131072
    timeout: float = 900
    input_price: float | None = None
    output_price: float | None = None
    cached_input_price: float | None = None

    def public(self):
        return asdict(self)

    def key(self):
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise ValueError(f'Missing environment variable {self.api_key_env}')
            return key
        key = os.environ.get('LLM_API_KEY') or os.environ.get('OPENAI_API_KEY')
        if not key:
            raise ValueError('Set LLM_API_KEY or OPENAI_API_KEY (or --api-key-env)')
        return key

    def validate(self):
        parsed = urlsplit(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('base-url must not contain credentials, query or fragment')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('base-url requires HTTPS, except localhost test servers')
        if self.provider == 'openai' and self.service_tier != 'flex':
            raise ValueError('This pipeline uses OpenAI Flex only; no standard/priority fallback')
        if self.reasoning_effort not in {None, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}:
            raise ValueError('Unsupported reasoning effort')
        if self.image_detail == 'original' and self.provider == 'openai' and re.match(r'^(gpt-4|gpt-5\.[12](?:-|$)|gpt-5-mini$|o[134])', self.model):
            raise ValueError('Model does not support original; explicitly select a supported model/detail')
        if self.reserve_output_tokens < 1 or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('Positive budget reservation and timeout required')
        for price in (self.input_price, self.output_price, self.cached_input_price):
            if price is not None and finite_number(price) is None:
                raise ValueError('Prices must be finite and nonnegative')
        if self.provider == 'openai' and self.base_url.rstrip('/') == 'https://api.openai.com/v1':
            if self.model in {'gpt-5.4-mini', 'gpt-5.4-mini-2026-03-17'}:
                if self.input_price is None:
                    self.input_price = .375
                if self.output_price is None:
                    self.output_price = 2.25
                if self.cached_input_price is None:
                    self.cached_input_price = .0375


def content(task, materialize=False):
    primary = task['pages'][0]['physical_sequence']
    blocks = [] if task.get('extraction_profile') == 'literal-v1' else [
        {'type': 'text', 'text': editorial_hint(task['volume'])}]
    image_number = 0
    for page in task['pages']:
        number = page['physical_sequence']
        role = ('PRIMARY PAGE' if page['role'] == 'primary' else
                'PREVIOUS PAGE — CONTEXT ONLY' if number == primary-1 else
                'NEXT PAGE — CONTEXT ONLY' if number == primary+1 else
                'EDITORIAL DECLARATION — CONTEXT ONLY')
        label = f"{role} | {page['volume']} | physical file {number}"
        blocks.append({'type': 'text', 'text': label + '\nOCR XML:\n' + page['ocr_xml']})
        for region in page['regions']:
            image_number += 1
            view = 'FULL PAGE' if region['transformation'] == 'none' else (
                'LEFT CROP' if region['crop_box'][0] == 0 else 'RIGHT CROP')
            blocks.append({'type': 'text', 'text': f'IMAGE {image_number} | {label} | {view}'})
            if materialize:
                blob = Path(region['path']).read_bytes()
                if digest(blob) != region['sha256']:
                    raise ValueError('Image hash changed before HTTP request')
                url = 'data:image/png;base64,' + base64.b64encode(blob).decode()
            else:
                url = f"local-image:{region['sha256']}:{region['id']}"
            blocks.append({'type': 'image_url', 'image_url': {'url': url}})
    return blocks


def payload(task, config, prompt, materialize=False):
    blocks = content(task, materialize)
    for block in blocks:
        if block['type'] == 'image_url':
            block['image_url']['detail'] = config.image_detail
    schema, name = SCHEMA, 'alphabetical_reading_v2'
    if task.get('extraction_profile') == 'literal-v1':
        from .literal import SCHEMA as schema
        name = 'alphabetical_literal_v1'
    schema_format = {'type': 'json_schema', 'name': name,
                     'schema': schema, 'strict': True}
    result = {'model': config.model, 'stream': False}
    if config.service_tier:
        result['service_tier'] = config.service_tier
    if config.api_mode == 'chat':
        if config.reasoning_effort is not None:
            result['reasoning_effort'] = config.reasoning_effort
        result.update(messages=[{'role': 'system', 'content': prompt}, {'role': 'user', 'content': blocks}],
                      response_format={'type': 'json_schema', 'json_schema': {k: v for k, v in schema_format.items() if k != 'type'}}
                      if config.structured_output == 'json-schema' else {'type': 'json_object'})
    else:
        if config.reasoning_effort is not None:
            result['reasoning'] = {'effort': config.reasoning_effort}
        converted = [({'type': 'input_text', 'text': b['text']} if b['type'] == 'text' else
                      {'type': 'input_image', 'image_url': b['image_url']['url'], 'detail': config.image_detail}) for b in blocks]
        result.update(instructions=prompt, input=[{'role': 'user', 'content': converted}],
                      store=False,
                      text={'format': schema_format if config.structured_output == 'json-schema' else {'type': 'json_object'}})
    return result


def finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def usage_cost(body, config):
    usage = body.get('usage') or {}
    input_tokens = finite_number(usage.get('input_tokens', usage.get('prompt_tokens')))
    output_tokens = finite_number(usage.get('output_tokens', usage.get('completion_tokens')))
    cached = finite_number((usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}).get('cached_tokens')) or 0
    gateway = ((body.get('providerMetadata') or {}).get('gateway') or {})
    reported = finite_number(gateway.get('cost', usage.get('cost')))
    estimated = None
    actual_tier = body.get('service_tier')
    actual_model = body.get('model')
    model_compatible = actual_model in (None, config.model) or (
        config.model == 'gpt-5.4-mini' and actual_model == 'gpt-5.4-mini-2026-03-17')
    if (input_tokens is not None and output_tokens is not None and
            config.input_price is not None and config.output_price is not None and
            actual_tier in (None, config.service_tier) and model_compatible):
        cached = min(cached, input_tokens)
        estimated = ((input_tokens-cached)*config.input_price +
                     cached*(config.cached_input_price if config.cached_input_price is not None else config.input_price) +
                     output_tokens*config.output_price)/1_000_000
    return {'input_tokens': input_tokens, 'output_tokens': output_tokens, 'cached_input_tokens': cached,
            'reported_cost_usd': reported, 'estimated_cost_usd': estimated,
            'requested_model': config.model, 'returned_model': actual_model,
            'provider': config.provider, 'requested_service_tier': config.service_tier,
            'returned_service_tier': actual_tier, 'pricing_checked': PRICING_CHECKED,
            'pricing_source': PRICING_SOURCE if config.provider == 'openai' else 'explicit_cli_rates'}


def reservation(task, config, prompt):
    if config.input_price is None or config.output_price is None:
        return None
    # Deliberately conservative planning bound, not a billed token count. UTF-8
    # bytes upper-bound text token estimates; up to 30k image patches * multiplier.
    n_images = sum(len(p['regions']) for p in task['pages'])
    input_bound = len(encoded(payload(task, config, prompt))) + n_images * 90000
    return (input_bound*config.input_price + config.reserve_output_tokens*config.output_price)/1_000_000


def retry_after(value):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


def scrub(value, key):
    if isinstance(value, dict):
        return {k: scrub(v, key) for k, v in value.items() if k.lower() not in {'authorization', 'api_key', 'api-key'}}
    if isinstance(value, list):
        return [scrub(v, key) for v in value]
    if isinstance(value, str):
        value = value.replace(key, '[REDACTED]') if key else value
        return re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', '[image data omitted]', value)
    return value


def call(task, config, prompt, key, timeout):
    started = time.monotonic()
    result = {'status': 'api_error', 'retryable': False, 'retry_after': 0,
              'requested_image_detail': config.image_detail, 'effective_image_detail': None,
              'requested_reasoning_effort': config.reasoning_effort,
              'requested_service_tier': config.service_tier, 'provider': config.provider,
              'reported_cost_usd': None, 'estimated_cost_usd': None}
    endpoint = config.base_url.rstrip('/') + ('/chat/completions' if config.api_mode == 'chat' else '/responses')
    try:
        data = payload(task, config, prompt, materialize=True)
        if len(encoded(data)) > 45_000_000:
            raise ValueError('request exceeds 45 MB; reduce images/context explicitly')
        # Disable redirects: never forward credentials or silently change the endpoint.
        response = requests.post(endpoint, headers={'Authorization': 'Bearer ' + key,
            'Content-Type': 'application/json'}, json=data, timeout=timeout, allow_redirects=False)
        result['http_status'] = response.status_code
        result['request_id'] = response.headers.get('x-request-id')
        try:
            body = response.json()
        except ValueError:
            body = {'error': {'message': response.text[:2000]}}
        result['response'] = scrub(body, key)
        if not isinstance(body, dict):
            raise ValueError('response must be a JSON object')
        result.update(usage_cost(body, config))
        if response.status_code >= 400 or response.status_code < 200 or response.status_code >= 300:
            result.update(error='http_error', retryable=response.status_code in {408, 429, 500, 502, 503, 504},
                          retry_after=retry_after(response.headers.get('Retry-After')))
            msg = str(body.get('error', ''))
            if response.status_code in (400, 422):
                if 'original' in msg or 'detail' in msg:
                    result['error'] = 'image_detail_rejected_explicit_configuration_required'
                elif 'schema' in msg or 'response_format' in msg:
                    result['error'] = 'structured_output_rejected_explicit_configuration_required'
                elif 'reasoning' in msg:
                    result['error'] = 'reasoning_effort_rejected_explicit_configuration_required'
            return result
        if config.service_tier and body.get('service_tier') not in (None, config.service_tier):
            result.update(error='unexpected_service_tier', retryable=False)
            return result
        result['effective_image_detail'] = config.image_detail if any(p['regions'] for p in task['pages']) else None
        if config.api_mode == 'chat':
            choice = body['choices'][0]
            message = choice['message']
            if message.get('refusal'):
                result.update(status='refused', error='model_refusal')
                return result
            if choice.get('finish_reason') != 'stop':
                result.update(status='incomplete', error='finish_reason_' + str(choice.get('finish_reason')))
                return result
            text = message['content']
        else:
            if body.get('status') != 'completed':
                result.update(status='incomplete', error='response_not_completed')
                return result
            parts = [c for item in body.get('output', []) if item.get('type') == 'message' for c in item.get('content', [])]
            if any(c.get('type') == 'refusal' for c in parts):
                result.update(status='refused', error='model_refusal')
                return result
            text = ''.join(c['text'] for c in parts if c.get('type') == 'output_text')
        import json
        result['dto'] = scrub(json.loads(text), key)
        result['status'] = 'received'
    except (requests.Timeout, requests.ConnectionError) as exc:
        result.update(error=type(exc).__name__, retryable=True)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, OverflowError, OSError, requests.RequestException) as exc:
        result.update(error=scrub(str(exc), key), status='invalid_response')
    finally:
        result['latency_ms'] = (time.monotonic()-started)*1000
    return result
