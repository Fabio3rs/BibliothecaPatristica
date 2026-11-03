"""Opt-in presentation order, preserving the frozen legacy provider implementation."""
import json
import urllib.request

from . import providers
from .common import digest, dumps

VERSION = 'kg-request-presentation-1'


def _order(schema, order):
    if set(order) != set(schema['properties']) or len(set(order)) != len(order):
        raise ValueError('Presentation order must list every response property exactly once')
    schema['properties'] = {k: schema['properties'][k] for k in order}


def payload_text(payload, order=None):
    if not order:
        return dumps(payload)
    data = json.loads(dumps(payload))
    _order(data['response_schema'], order)
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def wire_text(request):
    presentation = request.get('presentation')
    if not presentation:
        return dumps(request['body'])
    if presentation['version'] != VERSION:
        raise ValueError('Unknown request presentation version')
    body = json.loads(dumps(request['body']))
    schema = body.get('format')
    if not isinstance(schema, dict):
        schema = body.get('response_format', {}).get('json_schema', {}).get('schema')
    if schema:
        _order(schema, presentation['response_property_order'])
    return json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def request_spec(config, prompt, image=None):
    request = providers.request_spec(config, prompt, image)
    order = prompt.get('variant', {}).get('response_property_order')
    if order:
        if 'json_schema' in request['body'].get('response_format', {}):
            request['body']['response_format']['json_schema']['name'] = prompt['schema_version'].replace('-', '_').replace('.', '_')
        request['presentation'] = {'version': VERSION, 'response_property_order': list(order)}
        request['presentation']['wire_sha256'] = digest(wire_text(request))
    return request


def send(config, request, *, transport=None, **kwargs):
    if not request.get('presentation'):
        return providers.send(config, request, transport=transport, **kwargs)
    wire = wire_text(request).encode('utf-8', errors='strict')
    if digest(wire) != request['presentation']['wire_sha256']:
        raise ValueError('Frozen request presentation hash mismatch')
    original_transport = transport or urllib.request.build_opener(providers.NoRedirect()).open

    def ordered_transport(req, **options):
        ordered = urllib.request.Request(req.full_url, data=wire, headers=dict(req.header_items()),
                                         method=req.get_method())
        return original_transport(ordered, **options)

    return providers.send(config, request, transport=ordered_transport, **kwargs)
