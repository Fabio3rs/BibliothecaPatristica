import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { digest } from '../src/cache.js';

export function validateRelease(tag, version, commit, artifact) {
  if (!/^mcp\/v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(tag)) throw new Error('Release tag must start with mcp/v and contain a version.');
  if (tag !== `mcp/v${version}` || artifact.version !== version) throw new Error('Release tag/package version mismatch.');
  if (commit !== artifact.commit) throw new Error('Artifact was not built from this tag commit.');
  if (artifact.dirty !== false) throw new Error('Release requires an artifact from a clean checkout.');
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const pkg = JSON.parse(await readFile(path.join(root, 'package.json')));
  const artifact = JSON.parse(await readFile(path.join(root, 'artifacts/build-manifest.json')));
  const tag = process.env.RELEASE_TAG || '';
  const commit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  validateRelease(tag, pkg.version, commit, artifact);
  if (path.basename(artifact.tarball) !== artifact.tarball) throw new Error('Unsafe artifact filename.');
  const archive = path.join(root, 'artifacts', artifact.tarball);
  if (digest(await readFile(archive)) !== artifact.sha256) throw new Error('Tarball checksum mismatch.');
  if ((await readFile(`${archive}.sha256`, 'utf8')) !== `${artifact.sha256}  ${artifact.tarball}\n`) throw new Error('Checksum file disagrees with build manifest.');
  let release;
  try {
    release = JSON.parse(execFileSync('gh', ['release', 'view', tag, '--repo', process.env.GITHUB_REPOSITORY, '--json', 'tagName,assets'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
  } catch (error) {
    if (/release not found|HTTP 404/.test(String(error.stderr))) {
      console.log('Release no longer exists; retaining Actions artifacts only.');
      process.exit(0);
    }
    throw error;
  }
  if (release.tagName !== tag) throw new Error('Wrong GitHub Release.');
  const names = [artifact.tarball, `${artifact.tarball}.sha256`, 'build-manifest.json'];
  const conflicts = release.assets.filter(a => names.includes(a.name)).map(a => a.name);
  if (conflicts.length) throw new Error(`Release asset conflict: ${conflicts.join(', ')}. Nothing overwritten; inspect/remove conflicting assets manually before retrying.`);
  execFileSync('gh', ['release', 'upload', tag, '--repo', process.env.GITHUB_REPOSITORY, ...names.map(name => path.join(root, 'artifacts', name))], { stdio: 'inherit' });
}
