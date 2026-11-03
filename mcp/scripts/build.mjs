import { cp, mkdir, readFile, writeFile, chmod, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('Node.js 24+ required.');
await rm(path.join(root, 'dist'), { recursive: true, force: true });
await cp(path.join(root, 'src'), path.join(root, 'dist'), { recursive: true });
const shared = ['agent-chat-tools.js', 'agent-chat-core.js', 'agent-chat-context.js',
  'agent-chat-citations.js', 'agent-chat-resilient-fetch.js', 'agent-query-strategies.js',
  'scripture-index.js', 'scripture-docids.js'];
await mkdir(path.join(root, 'dist/shared'), { recursive: true });
for (const file of shared) await cp(path.join(root, '../web/src/scripts', file), path.join(root, 'dist/shared', file));
await cp(path.join(root, '../web/public/scripts/ocr-text.mjs'), path.join(root, 'dist/shared/ocr-text.mjs'));
const vendor = JSON.parse(await readFile(path.join(root, 'vendor/indexador/provenance.json')));
for (const [file, hash] of Object.entries(vendor.sha256)) {
  const actual = createHash('sha256').update(await readFile(path.join(root, 'vendor/indexador', file))).digest('hex');
  if (hash !== actual) throw new Error(`Vendored runtime integrity failure: ${file}`);
}
await writeFile(path.join(root, 'dist/version.js'), `export const VERSION = ${JSON.stringify(JSON.parse(await readFile(path.join(root, 'package.json'))).version)};\n`);
await chmod(path.join(root, 'dist/cli.js'), 0o755);
console.log('Built local modules, shared tools and verified WASM; no downloads.');
