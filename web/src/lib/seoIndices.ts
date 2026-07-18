// @ts-nocheck
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { gunzipSync } from 'node:zlib';

export type IndexManifestEntry = {
  volume_id: string;
  collection?: string;
  path?: string;
  works_total?: number;
  sections_total?: number;
  entries_total?: number;
  entries_with_page_ref?: number;
  completeness?: number;
  updated_at?: string;
};

type IndexEntry = {
  id?: string | number;
  entry_order?: number;
  entry_raw?: string;
  target_raw?: string;
  target_display?: { original?: string };
  reference_page?: number | string | null;
  page_ref_int?: number | string | null;
  normalized_target?: string;
  note_raw?: string | string[] | null;
};

type IndexSection = {
  section_key?: string;
  work_key?: string;
  index_kind?: string;
  heading_raw?: string;
  heading_norm?: string;
  heading_display?: { original?: string };
  reference_page_start?: number | string | null;
  reference_page_end?: number | string | null;
  page_start?: number | string | null;
  page_end?: number | string | null;
  entries?: IndexEntry[];
};

type IndexWork = {
  work_key?: string;
  author_raw?: string;
  title_raw?: string;
  title_display?: { original?: string };
  reference_start_page?: number | string | null;
  reference_end_page?: number | string | null;
};

type IndexVolumeDoc = {
  volume?: {
    volume_id?: string;
    collection?: string;
    volume_label?: string;
    display?: { original?: string };
  };
  coverage?: {
    works_total?: number;
    sections_total?: number;
    entries_total?: number;
    completeness?: number;
  };
  works?: IndexWork[];
  sections?: IndexSection[];
  relations?: unknown;
  render_hints?: unknown;
};

export type SeoEntry = {
  id: string;
  title: string;
  subtitle: string;
  normalizedTarget: string;
  note: string;
  page: number | null;
  viewerHref: string | null;
  fallbackHref: string;
  anchorId: string;
};

export type SeoSection = {
  sectionKey: string;
  kind: string;
  title: string;
  titleSecondary: string;
  pageStart: number | null;
  pageEnd: number | null;
  viewerHref: string | null;
  sectionHref: string;
  anchorId: string;
  entries: SeoEntry[];
};

export type SeoWork = {
  key: string;
  author: string;
  title: string;
  pageStart: number | null;
  pageEnd: number | null;
  viewerHref: string | null;
  fallbackHref: string;
};

export type SeoVolumePayload = {
  volumeId: string;
  collection: string;
  title: string;
  heading: string;
  completeness: number;
  worksTotal: number;
  sectionsTotal: number;
  entriesTotal: number;
  works: SeoWork[];
  sections: SeoSection[];
  hasThematicSections: boolean;
};

type ManifestFile = {
  schema_version?: string;
  generated_at?: string;
  volumes?: IndexManifestEntry[];
};

const PUBLIC_DIR = join(process.cwd(), 'public');
const INDICES_DIR = join(PUBLIC_DIR, 'indices');

const THEMATIC_KINDS = new Set([
  'INDEX ANALYTICUS',
  'INDEX RERUM',
  'INDEX RERUM ET VERBORUM',
  'ORDO RERUM',
  'ORDO RERUM QUAE IN HOC TOMO CONTINENTUR',
  'ORDO RERUM / INDICES',
]);

const MIN_THEMATIC_VALID_ENTRIES = 3;
const MIN_THEMATIC_PAGE_ENTRIES = 2;

function normalizeWhitespace(value: unknown): string {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function normalizeKind(value: unknown): string {
  return normalizeWhitespace(value)
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/æ/gi, 'ae')
    .replace(/œ/gi, 'oe')
    .toUpperCase();
}

function slugify(value: unknown): string {
  return normalizeWhitespace(value)
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase() || 'item';
}

function toDedupKey(value: unknown): string {
  return slugify(value).replace(/-/g, '');
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const text = normalizeWhitespace(value);
  if (!text) return null;
  const match = text.match(/\d+/);
  return match ? Number(match[0]) : null;
}

function pickFirstPage(...values: unknown[]): number | null {
  for (const value of values) {
    const parsed = asNumber(value);
    if (parsed !== null) return parsed;
  }
  return null;
}

function buildViewerHref(volumeId: string, page: number | null): string | null {
  if (!page) return null;
  const params = new URLSearchParams({ doc: volumeId, page: String(page) });
  return `/viewer?${params.toString()}`;
}

function buildIndicesHref(volumeId: string, sectionKey?: string): string {
  const params = new URLSearchParams({ volume: volumeId });
  if (sectionKey) params.set('section', sectionKey);
  return `/indices?${params.toString()}`;
}

function compactEntryText(value: unknown): string {
  const text = normalizeWhitespace(value);
  if (!text) return '';
  return text
    .replace(/\s+\.\.\.\s*$/g, '')
    .replace(/\s+\d+(?:,\s*\d+)*\.?$/g, '')
    .trim();
}

async function readJsonMaybeGz(basePath: string): Promise<unknown> {
  const jsonPath = basePath.endsWith('.json') ? basePath : `${basePath}.json`;
  const gzPath = `${jsonPath}.gz`;
  try {
    const compressed = await readFile(gzPath);
    return JSON.parse(gunzipSync(compressed).toString('utf8'));
  } catch (error) {
    const content = await readFile(jsonPath, 'utf8');
    return JSON.parse(content);
  }
}

export async function readIndicesManifest(): Promise<ManifestFile> {
  const content = await readFile(join(INDICES_DIR, 'manifest.json'), 'utf8');
  return JSON.parse(content) as ManifestFile;
}

export async function listIndexVolumes(): Promise<IndexManifestEntry[]> {
  const manifest = await readIndicesManifest();
  return Array.isArray(manifest.volumes) ? manifest.volumes : [];
}

async function readVolumeDoc(volumeId: string): Promise<IndexVolumeDoc> {
  return readJsonMaybeGz(join(INDICES_DIR, volumeId)) as Promise<IndexVolumeDoc>;
}

function toSeoWork(volumeId: string, work: IndexWork): SeoWork {
  const pageStart = pickFirstPage(work.reference_start_page);
  const workKey = normalizeWhitespace(work.work_key);
  return {
    key: workKey,
    author: normalizeWhitespace(work.author_raw),
    title: normalizeWhitespace(work.title_display?.original || work.title_raw || work.work_key),
    pageStart,
    pageEnd: pickFirstPage(work.reference_end_page),
    viewerHref: buildViewerHref(volumeId, pageStart),
    fallbackHref: buildIndicesHref(volumeId),
  };
}

function selectEntryTitle(entry: IndexEntry, idx: number): string {
  const targetTitle = normalizeWhitespace(entry.target_display?.original || entry.target_raw);
  if (targetTitle) return targetTitle;
  const compactRaw = compactEntryText(entry.entry_raw);
  if (compactRaw) return compactRaw;
  return normalizeWhitespace(entry.id || `Entrada ${idx + 1}`);
}

function selectEntrySubtitle(entry: IndexEntry, title: string): string {
  const raw = normalizeWhitespace(entry.entry_raw);
  const target = normalizeWhitespace(entry.target_raw);
  if (raw && raw !== title) return raw;
  if (target && target !== title) return target;
  return '';
}

function buildEntryDedupKey(entry: IndexEntry, title: string, page: number | null): string {
  const semanticKey = normalizeWhitespace(entry.normalized_target) || title;
  return `${toDedupKey(semanticKey)}::${page || ''}`;
}

function isUsefulEntry(entry: IndexEntry, title: string): boolean {
  return !!(normalizeWhitespace(title) || normalizeWhitespace(entry.entry_raw) || normalizeWhitespace(entry.normalized_target));
}

function toSeoEntry(volumeId: string, sectionKey: string, sectionTitle: string, entry: IndexEntry, idx: number): SeoEntry | null {
  const page = pickFirstPage(entry.reference_page, entry.page_ref_int);
  const title = selectEntryTitle(entry, idx);
  if (!isUsefulEntry(entry, title)) return null;
  const subtitle = selectEntrySubtitle(entry, title);
  const note = Array.isArray(entry.note_raw)
    ? entry.note_raw.map((item) => normalizeWhitespace(item)).filter(Boolean).join(' · ')
    : normalizeWhitespace(entry.note_raw);
  return {
    id: String(entry.id ?? `${sectionKey}-${idx + 1}`),
    title,
    subtitle,
    normalizedTarget: normalizeWhitespace(entry.normalized_target),
    note,
    page,
    viewerHref: buildViewerHref(volumeId, page),
    fallbackHref: buildIndicesHref(volumeId, sectionKey),
    anchorId: `${slugify(sectionTitle)}-${slugify(title)}-${idx + 1}`,
  };
}

function dedupeSeoEntries(entries: SeoEntry[]): SeoEntry[] {
  const seen = new Set<string>();
  const out: SeoEntry[] = [];
  for (const entry of entries) {
    const key = buildEntryDedupKey(
      {
        normalized_target: entry.normalizedTarget,
      },
      entry.title,
      entry.page,
    );
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(entry);
  }
  return out;
}

function buildSectionSignature(section: SeoSection): string {
  const entrySig = section.entries
    .slice(0, 5)
    .map((entry) => `${toDedupKey(entry.normalizedTarget || entry.title)}:${entry.page || ''}`)
    .join('|');
  return `${toDedupKey(section.kind)}::${toDedupKey(section.title)}::${entrySig}`;
}

function toSeoSection(volumeId: string, section: IndexSection, idx: number): SeoSection | null {
  const title = normalizeWhitespace(section.heading_display?.original || section.heading_raw || section.section_key || `Seção ${idx + 1}`);
  const kind = normalizeWhitespace(section.index_kind);
  const titleSecondary = kind && kind !== title ? kind : '';
  const sectionKey = normalizeWhitespace(section.section_key || `section-${idx + 1}`);
  const pageStart = pickFirstPage(section.reference_page_start, section.page_start);
  const entries = dedupeSeoEntries(
    (Array.isArray(section.entries) ? section.entries : [])
      .map((entry, entryIdx) => toSeoEntry(volumeId, sectionKey, title, entry, entryIdx))
      .filter(Boolean) as SeoEntry[],
  );
  if (!entries.length) return null;
  return {
    sectionKey,
    kind,
    title,
    titleSecondary,
    pageStart,
    pageEnd: pickFirstPage(section.reference_page_end, section.page_end),
    viewerHref: buildViewerHref(volumeId, pageStart),
    sectionHref: buildIndicesHref(volumeId, sectionKey),
    anchorId: `${slugify(kind || title)}-${idx + 1}`,
    entries,
  };
}

function dedupeSeoSections(sections: SeoSection[]): SeoSection[] {
  const seen = new Set<string>();
  const out: SeoSection[] = [];
  for (const section of sections) {
    const signature = buildSectionSignature(section);
    if (seen.has(signature)) continue;
    seen.add(signature);
    out.push(section);
  }
  return out;
}

function thematicStats(sections: SeoSection[]): { validEntries: number; withPage: number } {
  let validEntries = 0;
  let withPage = 0;
  for (const section of sections) {
    validEntries += section.entries.length;
    withPage += section.entries.filter((entry) => entry.page !== null).length;
  }
  return { validEntries, withPage };
}

export function isThematicSection(section: IndexSection): boolean {
  return THEMATIC_KINDS.has(normalizeKind(section.index_kind));
}

export async function getSeoVolumePayload(volumeId: string): Promise<SeoVolumePayload> {
  const manifestVolumes = await listIndexVolumes();
  const manifestEntry = manifestVolumes.find((item) => item.volume_id === volumeId);
  const doc = await readVolumeDoc(volumeId);
  const title = normalizeWhitespace(doc.volume?.display?.original || doc.volume?.volume_label || doc.volume?.volume_id || volumeId);
  const works = Array.isArray(doc.works) ? doc.works.map((work) => toSeoWork(volumeId, work)) : [];
  const candidateSections = Array.isArray(doc.sections)
    ? doc.sections
        .filter(isThematicSection)
        .map((section, idx) => toSeoSection(volumeId, section, idx))
        .filter(Boolean) as SeoSection[]
    : [];
  const thematicSections = dedupeSeoSections(candidateSections);
  const thematic = thematicStats(thematicSections);
  const publishThematicRoute =
    thematic.validEntries >= MIN_THEMATIC_VALID_ENTRIES &&
    thematic.withPage >= MIN_THEMATIC_PAGE_ENTRIES;
  return {
    volumeId,
    collection: normalizeWhitespace(doc.volume?.collection || manifestEntry?.collection),
    title,
    heading: `${volumeId} — ${title}`,
    completeness: Number(doc.coverage?.completeness ?? manifestEntry?.completeness ?? 0),
    worksTotal: Number(doc.coverage?.works_total ?? manifestEntry?.works_total ?? works.length ?? 0),
    sectionsTotal: Number(doc.coverage?.sections_total ?? manifestEntry?.sections_total ?? 0),
    entriesTotal: Number(doc.coverage?.entries_total ?? manifestEntry?.entries_total ?? 0),
    works,
    sections: publishThematicRoute ? thematicSections : [],
    hasThematicSections: publishThematicRoute,
  };
}

export async function listThematicVolumes(): Promise<IndexManifestEntry[]> {
  const volumes = await listIndexVolumes();
  const out: IndexManifestEntry[] = [];
  for (const volume of volumes) {
    const payload = await getSeoVolumePayload(volume.volume_id);
    if (payload.hasThematicSections) out.push(volume);
  }
  return out;
}

export function buildCanonicalUrl(site: URL | undefined, pathname: string): string {
  if (!site) return pathname;
  const siteHref = site.toString().endsWith('/') ? site.toString() : `${site.toString()}/`;
  return new URL(pathname.replace(/^\//, ''), siteHref).toString();
}
