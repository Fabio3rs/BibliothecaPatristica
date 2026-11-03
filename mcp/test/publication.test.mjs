import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, cp, readFile, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { buildCompatibility } from '../../tools/build_mcp_compat.mjs';
import { validateRelease } from '../scripts/release.mjs';
import { validateIntegrity } from '../dist/compat.js';
import { digest } from '../dist/cache.js';
import { fixtureRoot, build } from './helpers.mjs';

test('publication generator derives hashes and build identities from actual outputs', async t => {
  const dir = await mkdtemp(path.join(tmpdir(), 'bibliotheca-publish-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  await cp(fixtureRoot, dir, { recursive: true });
  const options = { publicDir: dir, build, indexadorVersion: 'v0.8.5', indexadorCommit: '028cd943561c546974ad78eefc32e33eb6b5a1a6' };
  const manifest = await buildCompatibility(options);
  const raw = await readFile(path.join(dir, manifest.integrity.path));
  assert.equal(digest(raw), manifest.integrity.sha256);
  validateIntegrity(JSON.parse(raw), manifest);
  const sidecar = path.join(dir, manifest.sidecars.path);
  const data = JSON.parse(await readFile(sidecar));
  data.indexes.ALL.build_id = `sha256:${'0'.repeat(64)}`;
  await writeFile(sidecar, JSON.stringify(data));
  await assert.rejects(buildCompatibility(options), /Sidecar build mismatch/);
});
test('Release validation requires MCP tag, matching version and tested commit', () => {
  const artifact = { version: '0.1.0', commit: build, dirty: false };
  assert.doesNotThrow(() => validateRelease('mcp/v0.1.0', '0.1.0', build, artifact));
  assert.throws(() => validateRelease('v0.1.0', '0.1.0', build, artifact));
  assert.throws(() => validateRelease('mcp/v0.2.0', '0.1.0', build, artifact));
  assert.throws(() => validateRelease('mcp/v0.1.0', '0.1.0', 'b'.repeat(40), artifact));
  assert.throws(() => validateRelease('mcp/v0.1.0', '0.1.0', build, { ...artifact, dirty: true }));
});
