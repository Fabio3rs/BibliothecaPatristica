import type { APIRoute } from 'astro';
import { buildCanonicalUrl } from '../lib/seoIndices';

export const prerender = true;

export const GET: APIRoute = async ({ site }) => {
  const sitemaps = [
    buildCanonicalUrl(site, '/sitemap-main.xml'),
    buildCanonicalUrl(site, '/sitemap-seo-volumes.xml'),
    buildCanonicalUrl(site, '/sitemap-seo-volume-thematic.xml'),
  ];
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${sitemaps.map((loc) => `  <sitemap><loc>${loc}</loc></sitemap>`).join('\n')}
</sitemapindex>`;
  return new Response(body, {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
