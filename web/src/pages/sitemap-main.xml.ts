import type { APIRoute } from 'astro';
import { buildCanonicalUrl } from '../lib/seoIndices';

export const prerender = true;

const LOCALE_PREFIXES = ['', '/en', '/it', '/fr'] as const;
const STATIC_ROUTES = ['', '/search', '/indices', '/viewer'] as const;

export const GET: APIRoute = async ({ site }) => {
  const paths = LOCALE_PREFIXES.flatMap((prefix) =>
    STATIC_ROUTES.map((route) => `${prefix}${route}` || '/')
  );
  const urls = paths.map((path) => buildCanonicalUrl(site, path));
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((loc) => `  <url><loc>${loc}</loc></url>`).join('\n')}
</urlset>`;
  return new Response(body, {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
