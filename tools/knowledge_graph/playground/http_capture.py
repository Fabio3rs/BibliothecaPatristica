"""Bounded HTTP response-body diagnostics, before Unicode or JSON decoding."""
import base64
import hashlib
import json
import os

VERSION = 'kg-http-body-1'
MAX_BODY_BYTES = 32_000_000


def capture_body(body, config, *, complete=True):
    record = {'version': VERSION, 'byte_count': len(body), 'complete': complete,
              'sha256': hashlib.sha256(body).hexdigest(), 'encoding': 'base64'}
    secret = os.getenv(config.get('api_key_env', ''), '')
    # Never archive credentials echoed by a provider, including JSON escapes.
    forms = [] if not secret else [secret.encode('utf-8'),
        json.dumps(secret, ensure_ascii=True)[1:-1].encode('ascii'),
        ''.join('\\u%04x' % ord(c) for c in secret).encode('ascii')]
    if any(form in body for form in forms):
        record['status'] = 'omitted_sensitive_reflection'
    elif not complete:
        record['status'] = 'omitted_incomplete_body'
    else:
        record.update(status='captured', body_base64=base64.b64encode(body).decode('ascii'))
    return record
