import test from 'node:test';
import assert from 'node:assert/strict';

import { buildRouteContext } from '../src/scripts/agent-chat.js';

function contextPayload(context) {
  return JSON.parse(context.split('\n').slice(1).join('\n'));
}

test('declares an exact viewer page context without exposing the full query string', () => {
  const payload = contextPayload(buildRouteContext({
    href: 'https://site.test/BibliothecaPatristica/viewer?doc=PL016.03&page=100&irrelevant=secret',
    locale: 'pt-br',
  }));
  assert.deepEqual(payload.page_context, { kind: 'viewer', volume_id: 'PL016.03', viewer_page: 100 });
  assert.equal(JSON.stringify(payload).includes('irrelevant'), false);
});

test('declares that no viewer page exists on indices and after user removal', () => {
  const outside = contextPayload(buildRouteContext({
    href: 'https://site.test/BibliothecaPatristica/indices?volume=PG001',
    locale: 'en',
  }));
  assert.deepEqual(outside.page_context, { kind: 'none', reason: 'current_route_has_no_viewer_page' });
  assert.deepEqual(outside.site_context, { locale: 'en', route: 'indices', selected_volume: 'PG001' });

  const disabled = contextPayload(buildRouteContext({
    href: 'https://site.test/BibliothecaPatristica/viewer?doc=PG001&page=18',
    include: false,
  }));
  assert.deepEqual(disabled.page_context, { kind: 'none', reason: 'user_disabled' });
});
