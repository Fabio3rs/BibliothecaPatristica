import type { APIRoute } from 'astro';
import { buildCanonicalUrl, listIndexVolumes } from '../lib/seoIndices';

export const prerender = true;
const LOCALE_PREFIXES = ['', '/en', '/it', '/fr'] as const;

export const GET: APIRoute = async ({ site }) => {
  const volumes = await listIndexVolumes();
  const urls = volumes.flatMap((volume) => LOCALE_PREFIXES.map((prefix) => ({
    loc: buildCanonicalUrl(site, `${prefix}/seo/volumes/${volume.volume_id}`),
    lastmod: volume.updated_at || '',
  })));
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((item) => `  <url><loc>${item.loc}</loc>${item.lastmod ? `<lastmod>${item.lastmod}</lastmod>` : ''}</url>`).join('\n')}
</urlset>`;
  return new Response(body, {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
