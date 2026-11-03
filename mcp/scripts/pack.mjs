import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { digest } from '../src/cache.js';

const root = fileURLToPath(new URL('../', import.meta.url));
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const pkg = JSON.parse(await readFile(path.join(root, 'package.json')));
if (pkg.private || pkg.engines.node !== '>=24' || pkg.license !== 'MIT' || pkg.publishConfig.access !== 'public') throw new Error('Package metadata is not publish-ready.');
for (const name of ['preinstall', 'install', 'postinstall', 'prepare']) if (pkg.scripts[name]) throw new Error(`Install-time hook forbidden: ${name}`);
const dry = JSON.parse(execFileSync(npm, ['pack', '--dry-run', '--json', '--ignore-scripts'], { cwd: root, encoding: 'utf8' }))[0];
const permitted = /^(package\.json|README\.md|LICENSE|THIRD_PARTY_NOTICES\.md|licenses\/[^/]+|dist\/(?:shared\/)?[a-z0-9.-]+\.(?:js|mjs)|vendor\/indexador\/(?:indexador_wasm\.(?:cjs|wasm)|indexador-lib\.js|indexador-pagefind\.js|query-dsl\.mjs|provenance\.json|node-fetch\.patch))$/;
for (const file of dry.files) {
  if (!permitted.test(file.path)) throw new Error(`Unexpected distributed file: ${file.path}`);
  if (file.size > 2 * 1024 * 1024) throw new Error(`Unexpected large asset: ${file.path}`);
}
for (const file of ['dist/cli.js', 'dist/server.js', 'dist/shared/agent-chat-tools.js', 'vendor/indexador/indexador_wasm.wasm', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md']) if (!dry.files.some(f => f.path === file)) throw new Error(`Missing distributed file: ${file}`);
if (dry.unpackedSize > 3 * 1024 * 1024) throw new Error('Package exceeded 3 MiB unpacked budget.');
await mkdir(path.join(root, 'artifacts'), { recursive: true });
const packed = JSON.parse(execFileSync(npm, ['pack', '--json', '--ignore-scripts', '--pack-destination', 'artifacts'], { cwd: root, encoding: 'utf8' }))[0];
if (JSON.stringify(packed.files) !== JSON.stringify(dry.files)) throw new Error('Dry-run and actual tarball differ.');
const archive = path.join(root, 'artifacts', packed.filename);
const listing = execFileSync('tar', ['-tzf', archive], { encoding: 'utf8' }).trim().split('\n');
if (listing.some(name => !name.startsWith('package/') || !permitted.test(name.slice(8)))) throw new Error('Tarball contains unsafe/unexpected paths.');
const sha256 = digest(await readFile(archive));
await writeFile(`${archive}.sha256`, `${sha256}  ${packed.filename}\n`);
// Native Lab/source archives may intentionally omit .git. CI always resolves
// the checkout itself; explicit local metadata is marked dirty unless attested.
const suppliedCommit = process.env.GITHUB_ACTIONS !== 'true' && process.env.MCP_BUILD_COMMIT;
const commit = suppliedCommit || execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
if (!/^[a-f0-9]{40}$/.test(commit)) throw new Error('Build commit must be a full Git SHA.');
const dirty = suppliedCommit ? process.env.MCP_BUILD_DIRTY !== 'false' : Boolean(execFileSync('git', ['status', '--porcelain', '--', 'mcp', 'web/src/scripts', 'web/public/scripts/ocr-text.mjs'], { cwd: path.join(root, '..'), encoding: 'utf8' }).trim());
const manifest = { name: pkg.name, version: pkg.version, commit, dirty, node: process.version, tarball: packed.filename, sha256, bytes: (await stat(archive)).size, files: packed.files.map(f => f.path) };
await writeFile(path.join(root, 'artifacts/build-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify(manifest, null, 2));
