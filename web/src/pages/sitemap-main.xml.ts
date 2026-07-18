import type { APIRoute } from 'astro';
import { buildCanonicalUrl } from '../lib/seoIndices';

export const prerender = true;

const STATIC_PATHS = [
  '/',
  '/search',
  '/indices',
  '/viewer',
  '/en',
  '/en/search',
  '/en/indices',
  '/en/viewer',
  '/it',
  '/it/search',
  '/it/indices',
  '/it/viewer',
] as const;

export const GET: APIRoute = async ({ site }) => {
  const urls = STATIC_PATHS.map((path) => buildCanonicalUrl(site, path));
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((loc) => `  <url><loc>${loc}</loc></url>`).join('\n')}
</urlset>`;
  return new Response(body, {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
