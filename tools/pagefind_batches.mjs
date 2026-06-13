import fs from 'fs';
import path from 'path';
import { readJsonMaybeGz } from './json_io.mjs';

export async function readJSON(filePath) {
  return readJsonMaybeGz(filePath);
}

export function nowIso() {
  return new Date().toISOString();
}

export function formatBatchId(batchIndex) {
  return batchIndex === 1 ? 'main' : `batch-${String(batchIndex).padStart(3, '0')}`;
}

export async function loadEnrichmentDocIds(indexPath) {
  const index = await readJSON(indexPath);
  const volumes = Array.isArray(index.volumes) ? index.volumes : [];
  return volumes
    .map((volume) => volume?.id)
    .filter((id) => typeof id === 'string' && id.length > 0);
}

export function planPagefindBatches(docIds, batchSize) {
  const size = Number.isFinite(batchSize) && batchSize > 0 ? Math.floor(batchSize) : 25;
  const batches = [];

  for (let i = 0; i < docIds.length; i += size) {
    const docs = docIds.slice(i, i + size);
    const batchIndex = batches.length + 1;
    const batchId = formatBatchId(batchIndex);
    batches.push({
      batch_index: batchIndex,
      batch_id: batchId,
      batch_path: `pagefind/${batchId}`,
      batch_dir: batchId,
      doc_first: docs[0] || null,
      doc_last: docs[docs.length - 1] || null,
      docs,
      volume_count: docs.length,
    });
  }

  return batches;
}

export function buildPagefindManifest(batches, batchSize) {
  return {
    schema_version: 1,
    generated_at: nowIso(),
    batch_size: batchSize,
    indexes: batches.map((batch) => ({
      id: batch.batch_id,
      path: batch.batch_path,
      batch_index: batch.batch_index,
      volume_count: batch.volume_count,
      doc_first: batch.doc_first,
      doc_last: batch.doc_last,
      docs: batch.docs,
    })),
  };
}

export async function writeJSON(filePath, data, pretty = true) {
  await fs.promises.mkdir(path.dirname(filePath), { recursive: true });
  const text = JSON.stringify(data, null, pretty ? 2 : 0);
  await fs.promises.writeFile(filePath, text, 'utf-8');
}
