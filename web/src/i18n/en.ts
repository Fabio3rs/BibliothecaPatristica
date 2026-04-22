/**
 * UI strings — English
 *
 * DATA NOTE: AI-generated summaries, keywords and entities are always in
 * Brazilian Portuguese (pt-BR), regardless of the UI language. A notice is
 * shown to English-speaking users so they are aware of this.
 */
import type { Translations } from './pt-br';

export const en: Translations = {
  // ─── Layout / Topbar ───────────────────────────────────────────────────────
  skipToContent: 'Skip to content',
  logoLabel: 'Bibliotheca Patristica — home page',
  logoText: 'Bibliotheca',
  tagline: 'Open digital corpus of patristic literature',
  navSearch: 'Search',
  navViewer: 'Reader',
  navMenuLabel: 'Navigation menu',
  langSwitchLabel: 'Mudar para Português',
  langSwitchCurrent: 'EN',
  langSwitchOther: 'PT',

  // ─── Disclaimer (AI notice) ────────────────────────────────────────────────
  disclaimerTitle: '⚠️ About summaries and keywords',
  disclaimerLine1:
    'We use AI to generate summaries and terms in order to help quickly locate themes in the patristic corpus.',
  disclaimerLine2:
    'The collection is still expanding and may contain OCR errors. If a theme does not appear in the search it may still exist; if a keyword appears, please verify it in the original text before using it academically.',
  disclaimerLine3:
    'Results are aids: reading the original and human review are always necessary.',
  disclaimerDataLang:
    '🌐 Summaries and keywords were generated in Brazilian Portuguese (pt-BR) and do not change according to the UI language.',
  disclaimerAriaLabel: 'Notice about AI-generated content',

  // ─── Footer ────────────────────────────────────────────────────────────────
  footerLeft: 'Collaborative project • open source',
  footerRight: 'GitHub Pages',

  // ─── Home page (index) ─────────────────────────────────────────────────────
  homeTitle: 'Bibliotheca Patristica Digital',
  homeSubtitle:
    'A research and exploration interface for J.P. Migne\'s <strong>Patrologia Graeca (PG)</strong>, <strong>Latina (PL)</strong> and <strong>Orientalis (PO)</strong> collections. Browse one of the largest repositories of early Christian literature through a powerful full-text search and AI-enriched metadata.',
  homeCtaSearch: 'Start Research',
  homeCtaExample: 'View Example Page (Chrysostom)',

  statVolumes: 'Volumes Covered',
  statSeries: '3 Series',
  statSeriesLabel: 'Graeca, Latina & Orientalis',
  statPages: 'Indexed Pages',
  statTokens: '1B+',
  statTokensLabel: 'Tokens Processed',

  startPointsTitle: 'Starting Points',
  startPointsDesc:
    "Don't know where to start? Explore the collection from these fundamental authors and themes.",

  tagCloudTitle: 'Keyword Cloud',
  tagCloudSubtitle: 'Explore Topics',
  tagCloudNote: 'Based on automatic term extraction.',
  tagCloudLoading: 'Loading tags...',
  tagCloudEmpty: 'No keywords found.',
  tagCloudError: 'Failed to load keywords.',
  statsLoadError: 'Stats not loaded:',

  // ─── Personas ──────────────────────────────────────────────────────────────
  personasTitle: 'Who is this corpus for?',
  personasDesc: 'Choose your profile and discover how Bibliotheca Patristica can help.',
  personaDevotoTitle: 'I am a devotee',
  personaDevotoDesc:
    'I want to know the Church Fathers, their letters and sermons. Start with the most-read authors and spiritual themes.',
  personaDevotoCta: 'Explore authors',
  personaTheologyTitle: 'I study theology',
  personaTheologyDesc:
    'I need to quickly locate treatises on the Trinity, Christology or Scripture. Use advanced search and collection filters.',
  personaTheologyCta: 'Go to search',
  personaResearcherTitle: 'I am a researcher',
  personaResearcherDesc:
    'I am looking for primary sources for articles or dissertations. Browse by volume, author and keywords with enriched metadata.',
  personaResearcherCta: 'Search the corpus',

  // ─── Search ────────────────────────────────────────────────────────────────
  searchTitle: 'Results',
  searchSubtitle: 'Full-text search in the patristic corpus',
  searchPlaceholder: 'Search...',
  searchItemsLabel: 'Items:',
  searchLoading: 'Loading index...',
  searchBusy: 'Searching...',
  searchNoResults: 'No results found',
  searchNoResultsDesc:
    'We could not find any pages matching your search criteria and filters.',
  searchClearFilters: 'Clear all filters',
  searchIndexUnavailable: 'Pagefind index not available. Build the index and reload.',
  searchIndexEmpty: 'Pagefind index not available or empty.',
  searchIndexError: 'Error searching the index.',
  searchNoTitle: 'Untitled',
  searchNoSnippet: 'No excerpt.',

  // ─── Filters (FiltersPanel) ────────────────────────────────────────────────
  filtersTitle: 'Filters',
  filtersLoading: 'Loading filters...',
  filtersUnavailable: 'Filters unavailable.',
  filtersNoOptions: 'No options',
  filterCollection: 'Collection',
  filterVolume: 'Volume',
  filterKeywords: 'Keywords',
  filterKeywordsHint: 'Click to search by keyword.',
  filterKeywordsLoading: 'Loading…',
  filterKeywordsUnavailable: 'Unavailable.',

  paginationShowing: (start: number, end: number, total: number) =>
    `Showing ${start}–${end} of ${total} results`,
  paginationPage: (page: number, total: number) => `Page ${page} / ${total}`,
  paginationPrev: '← Previous',
  paginationNext: 'Next →',
  paginationPrevLabel: 'Previous page',
  paginationNextLabel: 'Next page',

  // ─── Viewer ────────────────────────────────────────────────────────────────
  viewerLoading: 'Loading page...',
  viewerVolume: (id: string, title: string) =>
    `Volume ${id}${title ? ' — ' + title : ''}`,
  viewerPage: (n: number) => `Page ${n}`,
  viewerPrev: '← Previous',
  viewerNext: 'Next →',
  viewerFirst: 'First',
  viewerLast: 'Last',
  viewerGo: 'Go',
  viewerPageInputLabel: 'Page number',
  viewerFocusMode: 'Focus mode',
  viewerNormalMode: 'Normal mode',
  viewerDecreaseFont: 'A-',
  viewerIncreaseFont: 'A+',
  viewerContinueReading: 'Continue reading',
  viewerProgress: 'Progress',
  viewerSearchInVolume: 'Search in this volume…',
  viewerIndex: 'Index',
  viewerNoIdentity: 'Volume information not available',
  viewerBack: '↩ Back to search',
  viewerSummaryPage: 'Page Summary',
  viewerSummaryGlobal: 'Global Summary',
  viewerExcerpt: 'Notable Excerpt',
  viewerNoContent: 'No content available.',
  viewerOcrTitle: 'Original OCR',
  viewerOcrLoading: 'Loading…',
  viewerOcrError: (msg: string) => `Error loading OCR: ${msg}`,
  viewerOcrOpen: '↗ open file',
  viewerOcrStructured: 'Structured view',
  viewerOcrPlain: 'Plain text',
  viewerOcrBlockType: 'Type',
  viewerOcrBlockScript: 'Script',
  viewerOcrNoBlocks: 'No structured blocks found.',
  viewerMetaKeywords: 'Keywords',
  viewerMetaEntities: 'Entities',
  viewerMetaSegments: 'Segments',
  viewerMetaSnapshots: 'Snapshots',
  viewerRelated: 'Related pages',
  viewerRelatedNote: '(Semantic distance by LLM)',
  viewerRelatedSim: (sim: number) => `similarity ${sim.toFixed(2)}`,
  viewerRelatedTitle: (doc: string, title: string, page: number) =>
    `${doc}${title ? ' — ' + title : ''} · p. ${page}`,
  viewerError: (msg: string) => `Error loading viewer: ${msg}`,

  // ─── PageMetadata (component) ──────────────────────────────────────────────
  metaPageLabel: 'Page',
  metaOpenOcr: 'Open raw OCR',
  metaSummaryTitle: 'Summary',
  metaSummaryEmpty: 'No summary available.',
  metaExcerptTitle: 'Excerpt',
  metaExcerptEmpty: 'No excerpt provided.',
  metaKeywords: 'Keywords',
  metaEntities: 'Entities',
  metaSegments: 'Segments',
  metaSnapshots: 'Snapshots',

  // ─── RawToggle ─────────────────────────────────────────────────────────────
  rawOpen: 'Open raw OCR',
  rawUnavailable: 'Raw OCR not published in this mirror.',

  // ─── Breadcrumbs ───────────────────────────────────────────────────────────
  breadcrumbNav: 'Breadcrumb',
  breadcrumbHome: 'Home',
  breadcrumbSearch: 'Search',
  breadcrumbViewer: 'Reader',

  // ─── Accessibility / ARIA ──────────────────────────────────────────────────
  ariaMetadata: 'Metadata',
  ariaResults: 'Results',
  ariaFilterBy: (cat: string, val: string) => `Filter by ${cat}: ${val}`,
  ariaOccurrences: (n: number) => `${n} occurrence(s)`,
};
