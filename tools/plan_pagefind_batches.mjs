#!/usr/bin/env node

import path from 'path';
import { buildPagefindManifest, loadEnrichmentDocIds, planPagefindBatches, writeJSON } from './pagefind_batches.mjs';

function parseArgs() {
  const args = process.argv.slice(2);
  const params = {
    indexPath: path.resolve('data/shards/enrichment/index.json'),
    batchSize: 25,
    format: 'matrix',
    manifestOut: null,
  };

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--index') params.indexPath = path.resolve(args[++i]);
    else if (a === '--batch-size') params.batchSize = parseInt(args[++i], 10) || 25;
    else if (a === '--format') params.format = args[++i] || 'matrix';
    else if (a === '--manifest-out') params.manifestOut = path.resolve(args[++i]);
  }

  return params;
}

async function main() {
  const params = parseArgs();
  const docIds = await loadEnrichmentDocIds(params.indexPath);
  const batches = planPagefindBatches(docIds, params.batchSize);
  const manifest = buildPagefindManifest(batches, params.batchSize);

  if (params.manifestOut) {
    await writeJSON(params.manifestOut, manifest, true);
  }

  if (params.format === 'manifest') {
    process.stdout.write(`${JSON.stringify(manifest)}\n`);
    return;
  }

  if (params.format !== 'matrix') {
    throw new Error(`Formato desconhecido: ${params.format}`);
  }

  const matrix = {
    include: batches.map((batch) => ({
      batch_index: batch.batch_index,
      batch_id: batch.batch_id,
      batch_path: batch.batch_path,
      batch_dir: batch.batch_dir,
      volume_count: batch.volume_count,
      doc_first: batch.doc_first,
      doc_last: batch.doc_last,
    })),
  };

  process.stdout.write(`${JSON.stringify(matrix)}\n`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
