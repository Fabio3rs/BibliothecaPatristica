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
  langSwitchLabel: 'Switch language',
  langSwitchCurrent: 'EN',
  langSwitchOther: 'IT',

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
  footerLeft: 'Collaborative project • <a href="https://github.com/Fabio3rs/BibliothecaPatristica" target="_blank" rel="noopener noreferrer">open source</a>',
  footerRight: 'GitHub Pages',

  // ─── Home page (index) ─────────────────────────────────────────────────────
  homeTitle: 'Bibliotheca Patristica Digital',
  homeSubtitle:
    'A research and exploration interface for J.P. Migne\'s <strong>Patrologia Graeca (PG)</strong>, <strong>Latina (PL)</strong> and <strong>Orientalis (PO)</strong> collections. The original texts remain in Greek, Latin and other eastern languages; what we provide are <strong>AI-generated summaries, keywords and metadata in Brazilian Portuguese</strong> to help locate and understand the corpus.',
  homeCtaSearch: 'Start Research',
  homeCtaExample: 'View Example Page (Chrysostom)',

  // ─── About (about section) ─────────────────────────────────────────────────
  aboutTitle: 'What is Bibliotheca Patristica?',
  aboutP1: 'For those who wish to deepen their understanding of the Christian faith, entering the world of Patristics and Patrology is like discovering the deep roots of the tree that nourishes us today with the fruits of Christian doctrine. The <strong>Bibliotheca Patristica</strong> project was born with the aim of facilitating this historical, intellectual, and spiritual recovery, making the treasure of Tradition more accessible.',
  aboutP2: 'Although the terms are often used synonymously, there is an important technical distinction between these two areas of study. <strong>Patristics</strong> is dedicated to the thought and theology of the Church Fathers, focusing on the rich doctrinal and philosophical content they produced. <strong>Patrology</strong>, on the other hand, focuses on the history and literature of these authors, investigating their biographies, the historical context in which they lived, and the authenticity of their writings.',
  aboutP3: 'But who are the "Church Fathers"? They are the great Christian leaders, pastors, and theologians who lived between the 1st and 8th centuries AD. For an author to receive this title, they must fulfill four fundamental criteria: <em>antiquity</em> (having lived in the era of the early Church), <em>holiness of life</em> (an exemplary testimony according to the Gospel), <em>orthodoxy</em> (having taught in full communion with the faith), and finally, <em>ecclesiastical approval</em> (official recognition of their legacy).',
  aboutP4: 'The study of these works goes far beyond historical curiosity; it is a necessity for the solidity of faith. It was precisely during this formative period that central Christian doctrines, such as the mystery of the Holy Trinity and the divinity of Christ, were defined and defended against the heresies of the time. Furthermore, the first Fathers — known as "Apostolic Fathers" — were direct disciples of the Apostles or lived close to them. Drinking from this source is as close as we can get to the purity and fervor of early Christianity.',
  aboutP5: 'These first masters also left us the key to the correct interpretation of the Bible, teaching us to read the Holy Scriptures not in isolation, but always illuminated by Tradition. Surprisingly, many of the challenges we face in the modern world — from crises of faith to attacks on morality — were already answered with brilliance by these authors centuries ago.',
  aboutP6: 'In this library, the reader is invited to connect with giants of the faith. You will find echoes of minds such as Saint Augustine, the "Doctor of Grace," whose work is essential for understanding original sin and the love of God; Saint Irenaeus of Lyons, a tireless defender of the unity of faith against Gnosticism; Saint Ignatius of Antioch, a student of the Apostle Saint John, whose letters reveal the structure of the Church as early as the 2nd century; and Saint Jerome, the monumental translator of the Bible into Latin (the Vulgate).',
  aboutP7: '<strong>Bibliotheca Patristica</strong> is an <strong>open-source</strong> project, the result of a collaborative effort to preserve and disseminate this legacy. You can access the source code, report issues, or contribute to its development in our repository on <a href="https://github.com/Fabio3rs/BibliothecaPatristica" target="_blank" rel="noopener noreferrer">GitHub</a>.',

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
  aboutP8:
    'Important: most of the corpus remains in its original languages — Greek, Latin, Syriac, Coptic and other eastern languages. What you find on this platform are <strong>summaries, keywords and metadata automatically extracted</strong> through text processing and artificial intelligence, serving as bridges to navigate and understand the corpus.',

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
  viewerOcrVisual: 'Visual layout',
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
