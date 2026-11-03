export const DEFAULT_BASE = 'https://fabio3rs.github.io/BibliothecaPatristica/';
export const SCHEMAS = Object.freeze({ catalog: 1, metadata: 1, pages: 2,
  search: 2, indices: 1, editorial: 3,
  scripture: 'bibliotheca-scripture-book-shards-v3', sidecars: 'bibliotheca-scripture-docids-v1' });
export function relativePath(value) {
  if (typeof value !== 'string' || !value || value.length > 1024 ||
      !/^[A-Za-z0-9_./-]+$/.test(value) || value.startsWith('/') ||
      value.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error(`Unsafe remote path: ${String(value).slice(0, 120)}`);
  }
  return value;
}
export function baseUrl(value = DEFAULT_BASE) {
  const url = new URL(value);
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if ((url.protocol !== 'https:' && !(loopback && url.protocol === 'http:')) || url.username || url.password || url.search || url.hash) {
    throw new Error('Dataset URL must use HTTPS (HTTP is allowed only on loopback).');
  }
  if (!url.pathname.endsWith('/')) url.pathname += '/';
  return url;
}
export function validateCompatibility(m) {
  if (m?.contract_version !== 1 || m.dataset !== 'bibliotheca-patristica') throw new Error('Unsupported Bibliotheca compatibility contract. Upgrade the MCP package.');
  if (!/^[a-f0-9]{40}$/.test(m.build) || m.commit !== m.build) throw new Error('Invalid published commit/build.');
  for (const [name, version] of Object.entries(SCHEMAS)) {
    if (m.schemas?.[name] !== version) throw new Error(`Unsupported ${name} schema: ${m.schemas?.[name]}. Upgrade the MCP package.`);
  }
  if (m.indexador?.format !== 1 || typeof m.indexador.version !== 'string') throw new Error('Unsupported PatrologiaIndexer format.');
  if (m.search?.path !== `indexador/search/${m.build}/manifest.json` ||
      m.indices?.path !== `indexador/indices/${m.build}/manifest.json` ||
      m.sidecars?.path !== `indexador/search/${m.build}/scripture-docids/manifest.json` ||
      m.integrity?.path !== `compat/${m.build}/integrity.json`) throw new Error('Mixed publication builds in compatibility manifest.');
  if (!/^[a-f0-9]{64}$/.test(m.integrity.sha256)) throw new Error('Missing integrity digest.');
  if (!m.search.indexes?.length) throw new Error('Missing search index identities.');
  for (const index of m.search.indexes) {
    relativePath(index.path);
    if (!/^sha256:[a-f0-9]{64}$/.test(index.build_id) || m.sidecars.indexes?.[index.id] !== index.build_id) throw new Error('Search/Scripture sidecar build_id mismatch.');
  }
  if (!/^sha256:[a-f0-9]{64}$/.test(m.indices.build_id)) throw new Error('Missing editorial index build_id.');
  return m;
}
export function validateIntegrity(payload, compatibility) {
  if (payload?.schema !== 1 || payload.build !== compatibility.build || !payload.assets || typeof payload.assets !== 'object') throw new Error('Integrity inventory belongs to another build or format.');
  for (const [name, entry] of Object.entries(payload.assets)) {
    relativePath(name);
    if (!/^[a-f0-9]{64}$/.test(entry.sha256) || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0) throw new Error(`Invalid integrity entry: ${name}`);
  }
  for (const name of ['volumes.json', 'indices/manifest.json', 'scripture/v3/manifest.json', compatibility.search.path, compatibility.indices.path, compatibility.sidecars.path]) {
    if (!payload.assets[name]) throw new Error(`Missing publication asset: ${name}`);
  }
  const editorialMap = `indexador/indices/${compatibility.build}/index.map`;
  if (`sha256:${payload.assets[editorialMap]?.sha256}` !== compatibility.indices.build_id) throw new Error('Editorial index build_id does not match inventory.');
  return payload;
}
