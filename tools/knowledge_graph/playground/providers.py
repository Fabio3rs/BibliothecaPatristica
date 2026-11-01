"""Explicit capability profiles and side-effect-free request construction.

No provider discovery or inference at import time. API keys are read only by send.
"""
from __future__ import annotations

import json
import math
import os
import re
import random
import time
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy

from .common import dumps
from .pricing import estimate_cost, lookup

ADAPTER_VERSION = 'kg-providers-1'
DEFAULT_MAX_OUTPUT_TOKENS = 32768
DEFAULTS = {
    'ollama': {'base_url': 'http://127.0.0.1:11434', 'api_key_env': 'OLLAMA_API_KEY', 'structured_output': 'schema', 'vision': False, 'reasoning_support': False, 'temperature_support': True, 'token_parameter': 'num_predict'},
    'openai': {'base_url': 'https://api.openai.com/v1', 'api_key_env': 'OPENAI_API_KEY', 'structured_output': 'schema', 'vision': False, 'reasoning_support': False, 'temperature_support': False, 'token_parameter': 'max_completion_tokens'},
    'compatible': {'base_url': 'http://127.0.0.1:8000/v1', 'api_key_env': '', 'structured_output': 'json', 'vision': False, 'reasoning_support': False, 'temperature_support': False, 'token_parameter': 'max_tokens'},
    'meta': {'base_url': 'https://api.meta.ai/v1', 'api_key_env': 'META_API_KEY', 'structured_output': 'schema', 'vision': True, 'reasoning_support': True, 'temperature_support': True, 'token_parameter': 'max_completion_tokens'},
    'fixture': {'base_url': '', 'api_key_env': '', 'structured_output': 'schema', 'vision': False, 'reasoning_support': False, 'temperature_support': False, 'token_parameter': ''},
}
ALLOWED = {'provider', 'model', 'base_url', 'api_key_env', 'structured_output', 'vision',
           'reasoning_support', 'temperature_support', 'token_parameter', 'reasoning_effort',
           'temperature', 'max_output_tokens', 'timeout', 'num_ctx', 'retries', 'prices', 'seed',
           'retry_backoff_base', 'retry_backoff_max', 'retry_after_max'}


def normalize_config(value):
    if not isinstance(value, dict) or set(value) - ALLOWED:
        raise ValueError('Unknown provider settings; use api_key_env, never store a secret')
    provider = value.get('provider', 'ollama')
    if provider not in DEFAULTS:
        raise ValueError('Unknown provider')
    config = dict(DEFAULTS[provider], provider=provider, model='', timeout=180, retries=1,
                  max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, reasoning_effort=None, temperature=None,
                  num_ctx=None, seed=None, prices={}, retry_backoff_base=1,
                  retry_backoff_max=60, retry_after_max=300)
    config.update(value)
    if 'prices' not in value:
        config['prices'] = (lookup(config) or {}).get('prices', {})
    if not isinstance(config['model'], str) or not config['model'].strip():
        raise ValueError('An explicit model identifier is required')
    url = urllib.parse.urlsplit(config['base_url'])
    if provider != 'fixture' and (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment):
        raise ValueError('Base URL must be HTTP(S), without credentials, query or fragment')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*|', config['api_key_env']):
        raise ValueError('api_key_env must name an environment variable')
    for key, low, high in [('timeout', 1, 3600), ('max_output_tokens', 1, 262144), ('retries', 0, 4)]:
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f'Invalid {key}')
    for key in ('vision', 'reasoning_support', 'temperature_support'):
        if type(config[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    for key in ('retry_backoff_base', 'retry_backoff_max', 'retry_after_max'):
        if type(config[key]) not in (int, float) or not math.isfinite(config[key]) or not 0 < config[key] <= 3600:
            raise ValueError(f'Invalid {key}')
    if config['retry_backoff_base'] > config['retry_backoff_max']:
        raise ValueError('retry_backoff_base exceeds retry_backoff_max')
    if config['structured_output'] not in {'schema', 'json', 'none'}:
        raise ValueError('structured_output must be schema, json or none')
    if provider == 'ollama' and (url.hostname == 'ollama.com' or 'cloud' in config['model'].lower()):
        # Cloud docs explicitly exclude structured outputs. Prompt carries the schema.
        config['structured_output'] = 'none'
    if config['reasoning_effort'] is not None:
        if not config['reasoning_support']:
            raise ValueError('Reasoning effort requires an explicitly capable model profile')
        choices = {'low', 'medium', 'high', 'true', 'false'} if provider == 'ollama' else {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
        if config['reasoning_effort'] not in choices:
            raise ValueError('Unsupported reasoning effort')
        if provider == 'meta' and (config['reasoning_effort'] == 'none' or (config['reasoning_effort'] == 'max' and config['model'] != 'muse-spark-1.3')):
            raise ValueError('Unsupported reasoning effort for this Muse profile')
    if config['temperature'] is not None:
        if not config['temperature_support'] or not isinstance(config['temperature'], (int, float)) or not 0 <= config['temperature'] <= 2:
            raise ValueError('Temperature unsupported or out of range')
    if config['num_ctx'] is not None and (provider != 'ollama' or type(config['num_ctx']) is not int or config['num_ctx'] < 512):
        raise ValueError('num_ctx is a positive Ollama context size (minimum 512)')
    if config['seed'] is not None and (provider != 'ollama' or type(config['seed']) is not int):
        raise ValueError('Seed is only supported by the Ollama profile')
    tokens = {'ollama': {'num_predict'}, 'fixture': {''}, 'openai': {'max_completion_tokens', 'max_tokens'},
              'meta': {'max_completion_tokens'}, 'compatible': {'max_completion_tokens', 'max_tokens'}}
    if config['token_parameter'] not in tokens[provider]:
        raise ValueError('Unsupported output token parameter')
    if not isinstance(config['prices'], dict) or set(config['prices']) - {'input_per_million', 'output_per_million', 'cached_input_per_million'}:
        raise ValueError('Invalid prices; rates are USD per million tokens')
    for price in config['prices'].values():
        if type(price) not in (int, float) or not math.isfinite(price) or price < 0:
            raise ValueError('Prices must be finite and nonnegative')
    return config


def request_spec(config, prompt, image=None):
    """Return exactly the body that will be sent, without authentication headers."""
    provider = config['provider']
    user = {'role': 'user', 'content': prompt['user_prompt']}
    if image:
        if not config['vision']:
            raise ValueError('Image attachment requested for a non-vision profile')
        if provider == 'ollama':
            user['images'] = [image['base64']]
        else:
            user['content'] = [{'type': 'text', 'text': prompt['user_prompt']},
                               {'type': 'image_url', 'image_url': {'url': f"data:{image['mime']};base64,{image['base64']}"}}]
    role = 'developer' if provider == 'meta' else 'system'
    body = {'model': config['model'], 'messages': [{'role': role, 'content': prompt['system_prompt']}, user], 'stream': False}
    schema = deepcopy(prompt['schema'])
    # OpenAI strict subset: singleton enum is equivalent to the starter's const.
    if provider in {'openai', 'compatible'}:
        schema['properties']['schema_version'] = {'type': 'string', 'enum': [prompt['schema']['properties']['schema_version']['const']]}
        schema.pop('$schema', None)
    structured = config['structured_output']
    if provider == 'ollama':
        if structured != 'none':
            body['format'] = schema if structured == 'schema' else 'json'
        body['options'] = {'num_predict': config['max_output_tokens']}
        for key in ('temperature', 'num_ctx', 'seed'):
            if config[key] is not None:
                body['options'][key] = config[key]
        if config['reasoning_effort'] is not None:
            effort = config['reasoning_effort']
            body['think'] = {'true': True, 'false': False}.get(effort, effort)
        endpoint = config['base_url'].rstrip('/') + '/api/chat'
    else:
        if structured == 'schema':
            body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': prompt['schema']['properties']['schema_version']['const'].replace('-', '_'), 'strict': True, 'schema': schema}}
        elif structured == 'json':
            body['response_format'] = {'type': 'json_object'}
        if config['token_parameter']:
            body[config['token_parameter']] = config['max_output_tokens']
        for key in ('temperature', 'reasoning_effort'):
            if config[key] is not None:
                body[key] = config[key]
        endpoint = config['base_url'].rstrip('/') + '/chat/completions'
    return {'adapter_version': ADAPTER_VERSION, 'url': endpoint, 'body': body}


def redact(text, config):
    secret = os.getenv(config.get('api_key_env', ''), '')
    return text.replace(secret, '[REDACTED]') if secret else text


class ProviderError(RuntimeError):
    def __init__(self, message, *, response='', attempts=0, retry_log=None):
        super().__init__(message)
        self.response = response
        self.attempts = attempts
        self.retry_log = retry_log or []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def retry_after_seconds(value, current_time):
    if not value:
        return 0
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - current_time
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


def send(config, request, *, transport=None, sleeper=time.sleep, uniform=random.uniform, clock=time.time):
    if config['provider'] == 'fixture':
        raise ValueError('Fixture responses are replayed only by the synthetic runner')
    secret = os.getenv(config['api_key_env'], '') if config['api_key_env'] else ''
    if config['provider'] in {'openai', 'meta'} and not secret:
        raise ProviderError('Required API key environment variable is not set')
    headers = {'Content-Type': 'application/json'}
    if secret:
        headers['Authorization'] = 'Bearer ' + secret
    transport = transport or urllib.request.build_opener(NoRedirect()).open
    started = time.perf_counter()
    retry_log = []
    for attempt in range(config['retries'] + 1):
        server_delay, status = 0, None
        req = urllib.request.Request(request['url'], data=dumps(request['body']).encode(), headers=headers, method='POST')
        try:
            with transport(req, timeout=config['timeout']) as response:
                response_text = redact(response.read(32_000_001).decode('utf-8'), config)
            if len(response_text) > 32_000_000:
                raise ProviderError('Provider response exceeded limit', attempts=attempt + 1, retry_log=retry_log)
            try:
                data = json.loads(response_text)
                if config['provider'] == 'ollama':
                    raw = data['message']['content']
                    usage = {'input_tokens': data.get('prompt_eval_count'), 'output_tokens': data.get('eval_count'), 'cached_input_tokens': None}
                    finish = data.get('done_reason')
                else:
                    choice = data['choices'][0]
                    raw = choice['message'].get('content') or ''
                    usage_raw = data.get('usage') or {}
                    usage = {'input_tokens': usage_raw.get('prompt_tokens'), 'output_tokens': usage_raw.get('completion_tokens'),
                             'cached_input_tokens': (usage_raw.get('prompt_tokens_details') or {}).get('cached_tokens'),
                             'reasoning_tokens': (usage_raw.get('completion_tokens_details') or {}).get('reasoning_tokens')}
                    finish = choice.get('finish_reason')
                if not isinstance(raw, str):
                    raise ValueError('content is not text')
                usage = {key: value if type(value) is int and value >= 0 else None
                         for key, value in usage.items()}
            except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
                raise ProviderError(f'Unrecognized response envelope: {type(exc).__name__}', response=response_text, attempts=attempt + 1, retry_log=retry_log) from None
            return {'raw_response': raw, 'provider_response': response_text, 'usage': usage,
                    'finish_reason': finish, 'latency_seconds': time.perf_counter() - started,
                    'attempts': attempt + 1, 'retry_log': retry_log}
        except urllib.error.HTTPError as exc:
            body = redact(exc.read(8000).decode('utf-8', errors='replace'), config)
            status = exc.code
            server_delay = retry_after_seconds(exc.headers.get('Retry-After') if exc.headers else None, clock())
            exc.close()
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == config['retries']:
                raise ProviderError(f'Provider HTTP {exc.code}', response=body, attempts=attempt + 1, retry_log=retry_log) from None
            if server_delay > config.get('retry_after_max', 300):
                raise ProviderError('Retry-After exceeds configured wait budget; no early retry was sent',
                                    response=body, attempts=attempt + 1, retry_log=retry_log) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == config['retries']:
                raise ProviderError('Provider connection/timeout failure; remote billing may have occurred',
                                    attempts=attempt + 1, retry_log=retry_log) from None
        cap = min(config.get('retry_backoff_base', 1) * 2 ** attempt, config.get('retry_backoff_max', 60))
        delay = server_delay + uniform(0, cap)
        retry_log.append({'attempt': attempt + 1, 'http_status': status,
                          'retry_after_seconds': server_delay, 'delay_seconds': delay})
        sleeper(delay)
    raise AssertionError('unreachable')
