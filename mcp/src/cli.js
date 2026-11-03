#!/usr/bin/env node
import { StdioServerTransport } from '@modelcontextprotocol/server/stdio';
import { createServer } from './server.js';
import { VERSION } from './version.js';

try {
  if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('Node.js 24+ required.');
  if (process.argv.includes('--version')) console.log(VERSION);
  else if (process.argv.includes('--help')) console.log('bibliotheca-patristica-mcp [--version|--help]\nLocal MCP server over stdio. Configuration: BIBLIOTHECA_BASE_URL, BIBLIOTHECA_CACHE_DIR, BIBLIOTHECA_CACHE_MAX_BYTES, BIBLIOTHECA_TIMEOUT_MS, BIBLIOTHECA_GITHUB_TOKEN (optional, OCR only).');
  else {
    if (process.argv.length > 2) throw new Error('Unknown CLI argument. Use --help.');
    const server = createServer();
    const close = async () => { await server.close(); process.exit(0); };
    process.once('SIGINT', close);
    process.once('SIGTERM', close);
    await server.connect(new StdioServerTransport());
  }
} catch (error) {
  console.error(`bibliotheca-patristica-mcp: ${error.message}`);
  process.exitCode = 1;
}
