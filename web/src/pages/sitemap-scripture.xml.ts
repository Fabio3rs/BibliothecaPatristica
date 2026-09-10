import type { APIRoute } from 'astro';
import { listAlphaScriptureBooks } from '../lib/alphaStaticPaths';
import { buildCanonicalUrl } from '../lib/seoIndices';

export const prerender = true;

const LOCALE_PREFIXES = ['', '/en', '/it', '/fr'] as const;

export const GET: APIRoute = async ({ site }) => {
  const books = await listAlphaScriptureBooks();
  const routes = [
    '/indices-alfabeticos/scripture',
    ...books.map((book) => `/indices-alfabeticos/scripture/${book.slug}`),
  ];
  const urls = routes.flatMap((route) => LOCALE_PREFIXES.map((prefix) => (
    buildCanonicalUrl(site, `${prefix}${route}/`)
  )));
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((loc) => `  <url><loc>${loc}</loc></url>`).join('\n')}
</urlset>`;
  return new Response(body, {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
