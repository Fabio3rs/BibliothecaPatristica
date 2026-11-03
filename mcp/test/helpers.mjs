import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export const fixtureRoot = fileURLToPath(new URL('./fixtures/public/', import.meta.url));
export const fixtureBase = 'https://fixture.test/BibliothecaPatristica/';
export const build = 'a'.repeat(40);
export const calls = [
  ['list_volumes', { limit: 1 }],
  ['search_corpus', { query: 'caridade', limit: 1 }],
  ['search_scripture', { reference: 'João 3:16', locations_per_reference: 1 }],
  ['get_page_metadata', { volume_id: 'PG001', page: 1 }],
  ['get_page_ocr', { volume_id: 'PG001', page: 1, max_chars: 500 }],
  ['search_indices', { query: 'caridade', limit: 1 }],
  ['get_volume_index', { volume_id: 'PG001', section_key: 's1', limit: 1 }],
];
export const ocrText = '<bloco tipo="texto" script="latino">Caritas patiens est.\n\n' + 'Caridade e unidade. '.repeat(90) + '</bloco>';
export async function fixtureFetch(input, { signal } = {}) {
  signal?.throwIfAborted();
  const url = new URL(input);
  if (url.hostname === 'raw.githubusercontent.com') {
    if (!url.pathname.includes(`/${build}/teste/PG001/text/fixture-`)) throw new Error('OCR was not pinned to commit');
    return new Response(ocrText);
  }
  const name = url.pathname.replace(/^\/BibliothecaPatristica\//, '');
  if (name.includes('..')) throw new Error('Invalid fixture path');
  try { return new Response(await readFile(path.join(fixtureRoot, name))); }
  catch (error) { if (error.code !== 'ENOENT') throw error; return new Response('Not found', { status: 404 }); }
}
export async function startFixtureServer() {
  const requests = [];
  const server = createServer(async (request, response) => {
    requests.push(request.url);
    try {
      const result = await fixtureFetch(new URL(request.url, fixtureBase));
      response.writeHead(result.status);
      response.end(Buffer.from(await result.arrayBuffer()));
    } catch (error) { response.writeHead(500); response.end(error.message); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return { base: `http://127.0.0.1:${server.address().port}/BibliothecaPatristica/`, requests,
    close: () => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }) };
}
