// @ts-nocheck
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import type { SupportedLocale } from '../i18n';

type DisplayText = {
  original?: string;
  translation?: Partial<Record<SupportedLocale, string>>;
  search?: string | string[];
};

export type IndexManifestEntry = {
  volume_id: string;
  collection?: string;
  path?: string;
  works_total?: number;
  sections_total?: number;
  entries_total?: number;
  entries_with_page_ref?: number;
  editorial_reference_coverage?: number;
  target_coverage?: number;
  completeness?: number;
  updated_at?: string;
};

type IndexEntry = {
  id?: string | number;
  entry_order?: number;
  entry_raw?: string;
  target_display?: DisplayText;
  reference_page?: number | string | null;
  editorial_reference_page?: number | string | null;
  normalized_target?: string;
  note_display?: DisplayText & { original?: string | string[] | null };
};

type IndexSection = {
  section_key?: string;
  work_key?: string;
  index_kind?: string;
  heading_raw?: string;
  heading_norm?: string;
  heading_display?: DisplayText;
  reference_page_start?: number | string | null;
  reference_page_end?: number | string | null;
  page_start?: number | string | null;
  page_end?: number | string | null;
  entries?: IndexEntry[];
};

type IndexWork = {
  work_key?: string;
  author_display?: DisplayText;
  title_display?: DisplayText;
  reference_start_page?: number | string | null;
  reference_end_page?: number | string | null;
};

type IndexVolumeDoc = {
  volume?: {
    volume_id?: string;
    collection?: string;
    volume_label?: string;
    display?: DisplayText;
  };
  coverage?: {
    works_total?: number;
    sections_total?: number;
    entries_total?: number;
    target_coverage?: number;
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
  titleOriginal: string;
  subtitle: string;
  normalizedTarget: string;
  note: string;
  page: number | null;
  editorialPage: number | null;
  viewerHref: string | null;
  fallbackHref: string;
  anchorId: string;
};

export type SeoSection = {
  sectionKey: string;
  kind: string;
  title: string;
  titleOriginal: string;
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
  authorOriginal: string;
  title: string;
  titleOriginal: string;
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
  targetCoverage: number;
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
const volumeDocCache = new Map<string, Promise<IndexVolumeDoc>>();

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

function localePrefix(locale: SupportedLocale): string {
  return locale === 'pt-br' ? '' : `/${locale}`;
}

function buildViewerHref(volumeId: string, page: number | null, locale: SupportedLocale): string | null {
  if (!page) return null;
  const params = new URLSearchParams({ doc: volumeId, page: String(page) });
  return `${localePrefix(locale)}/viewer?${params.toString()}`;
}

function buildIndicesHref(volumeId: string, locale: SupportedLocale, sectionKey?: string): string {
  const params = new URLSearchParams({ volume: volumeId });
  if (sectionKey) params.set('section', sectionKey);
  return `${localePrefix(locale)}/indices?${params.toString()}`;
}

function displayText(display: DisplayText | undefined, locale: SupportedLocale, fallback = '') {
  const original = normalizeWhitespace(display?.original || fallback);
  const translated = normalizeWhitespace(display?.translation?.[locale]);
  return {
    text: translated || original,
    original: translated && original && translated !== original ? original : '',
  };
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
  if (!volumeDocCache.has(volumeId)) {
    volumeDocCache.set(
      volumeId,
      readJsonMaybeGz(join(INDICES_DIR, volumeId)) as Promise<IndexVolumeDoc>,
    );
  }
  return volumeDocCache.get(volumeId)!;
}

function toSeoWork(volumeId: string, work: IndexWork, locale: SupportedLocale): SeoWork {
  const pageStart = pickFirstPage(work.reference_start_page);
  const workKey = normalizeWhitespace(work.work_key);
  const author = displayText(work.author_display, locale);
  const title = displayText(work.title_display, locale, work.work_key);
  return {
    key: workKey,
    author: author.text,
    authorOriginal: author.original,
    title: title.text,
    titleOriginal: title.original,
    pageStart,
    pageEnd: pickFirstPage(work.reference_end_page),
    viewerHref: buildViewerHref(volumeId, pageStart, locale),
    fallbackHref: buildIndicesHref(volumeId, locale),
  };
}

function selectEntryTitle(entry: IndexEntry, idx: number, locale: SupportedLocale) {
  const targetTitle = displayText(entry.target_display, locale);
  if (targetTitle.text) return targetTitle;
  const compactRaw = compactEntryText(entry.entry_raw);
  if (compactRaw) return { text: compactRaw, original: '' };
  return { text: normalizeWhitespace(entry.id || `Entrada ${idx + 1}`), original: '' };
}

function selectEntrySubtitle(entry: IndexEntry, title: string): string {
  const raw = normalizeWhitespace(entry.entry_raw);
  if (raw && raw !== title) return raw;
  return '';
}

function buildEntryDedupKey(entry: IndexEntry, title: string, page: number | null): string {
  const search = entry.target_display?.search;
  const searchText = Array.isArray(search) ? search.join(' ') : normalizeWhitespace(search);
  const semanticKey = normalizeWhitespace(entry.normalized_target) || normalizeWhitespace(searchText) || title;
  return `${toDedupKey(semanticKey)}::${page || ''}`;
}

function isUsefulEntry(entry: IndexEntry, title: string): boolean {
  return !!(normalizeWhitespace(title) || normalizeWhitespace(entry.entry_raw) || normalizeWhitespace(entry.normalized_target));
}

function toSeoEntry(volumeId: string, sectionKey: string, sectionTitle: string, entry: IndexEntry, idx: number, locale: SupportedLocale): SeoEntry | null {
  const page = pickFirstPage(entry.reference_page);
  const editorialPage = pickFirstPage(entry.editorial_reference_page);
  const title = selectEntryTitle(entry, idx, locale);
  if (!isUsefulEntry(entry, title.text)) return null;
  const subtitle = selectEntrySubtitle(entry, title.text);
  const noteOriginal = entry.note_display?.original;
  const note = Array.isArray(noteOriginal)
    ? noteOriginal.map((item) => normalizeWhitespace(item)).filter(Boolean).join(' · ')
    : normalizeWhitespace(noteOriginal);
  return {
    id: String(entry.id ?? `${sectionKey}-${idx + 1}`),
    title: title.text,
    titleOriginal: title.original,
    subtitle,
    normalizedTarget: normalizeWhitespace(
      entry.normalized_target
        || (Array.isArray(entry.target_display?.search)
          ? entry.target_display.search.join(' ')
          : entry.target_display?.search),
    ),
    note,
    page,
    editorialPage,
    viewerHref: buildViewerHref(volumeId, page, locale),
    fallbackHref: buildIndicesHref(volumeId, locale, sectionKey),
    anchorId: `${slugify(sectionTitle)}-${slugify(title.text)}-${idx + 1}`,
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
      entry.page ?? entry.editorialPage,
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
    .map((entry) => `${toDedupKey(entry.normalizedTarget || entry.title)}:${entry.page ?? entry.editorialPage ?? ''}`)
    .join('|');
  return `${toDedupKey(section.kind)}::${toDedupKey(section.title)}::${entrySig}`;
}

function toSeoSection(volumeId: string, section: IndexSection, idx: number, locale: SupportedLocale): SeoSection | null {
  const title = displayText(section.heading_display, locale, section.heading_raw || section.section_key || `Seção ${idx + 1}`);
  const kind = normalizeWhitespace(section.index_kind);
  const titleSecondary = kind && kind !== title.text ? kind : '';
  const sectionKey = normalizeWhitespace(section.section_key || `section-${idx + 1}`);
  const pageStart = pickFirstPage(section.reference_page_start);
  const entries = dedupeSeoEntries(
    (Array.isArray(section.entries) ? section.entries : [])
      .map((entry, entryIdx) => toSeoEntry(volumeId, sectionKey, title.text, entry, entryIdx, locale))
      .filter(Boolean) as SeoEntry[],
  );
  if (!entries.length) return null;
  return {
    sectionKey,
    kind,
    title: title.text,
    titleOriginal: title.original,
    titleSecondary,
    pageStart,
    pageEnd: pickFirstPage(section.reference_page_end),
    viewerHref: buildViewerHref(volumeId, pageStart, locale),
    sectionHref: buildIndicesHref(volumeId, locale, sectionKey),
    anchorId: `${slugify(kind || title.text)}-${idx + 1}`,
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
    withPage += section.entries.filter(
      (entry) => entry.page !== null || entry.editorialPage !== null
    ).length;
  }
  return { validEntries, withPage };
}

export function isThematicSection(section: IndexSection): boolean {
  return THEMATIC_KINDS.has(normalizeKind(section.index_kind));
}

export async function getSeoVolumePayload(volumeId: string, locale: SupportedLocale = 'pt-br'): Promise<SeoVolumePayload> {
  const manifestVolumes = await listIndexVolumes();
  const manifestEntry = manifestVolumes.find((item) => item.volume_id === volumeId);
  const doc = await readVolumeDoc(volumeId);
  const title = normalizeWhitespace(doc.volume?.display?.original || doc.volume?.volume_label || doc.volume?.volume_id || volumeId);
  const works = Array.isArray(doc.works) ? doc.works.map((work) => toSeoWork(volumeId, work, locale)) : [];
  const candidateSections = Array.isArray(doc.sections)
    ? doc.sections
        .filter(isThematicSection)
        .map((section, idx) => toSeoSection(volumeId, section, idx, locale))
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
    targetCoverage: Number(
      doc.coverage?.target_coverage ??
      manifestEntry?.target_coverage ??
      doc.coverage?.completeness ??
      manifestEntry?.completeness ??
      0
    ),
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
