/**
 * UI strings — Italiano
 *
 * NOTA SUI DATI: i riassunti, le parole chiave e le entità generate dall'IA
 * restano in portoghese brasiliano (pt-BR), indipendentemente dalla lingua
 * dell'interfaccia. Il messaggio di avviso lo chiarisce agli utenti.
 */
import type { Translations as BaseTranslations } from './pt-br';

export const it: BaseTranslations = {
  // ─── Layout / Topbar ───────────────────────────────────────────────────────
  skipToContent: 'Vai al contenuto',
  logoLabel: 'Bibliotheca Patristica — pagina iniziale',
  logoText: 'Bibliotheca',
  tagline: 'Archivio digitale aperto della letteratura patristica',
  navSearch: 'Ricerca',
  navViewer: 'Lettura',
  navMenuLabel: 'Menu di navigazione',
  langSwitchLabel: 'Cambia lingua',
  langSwitchCurrent: 'IT',
  langSwitchOther: 'PT',

  // ─── Disclaimer (avviso IA) ─────────────────────────────────────────────────
  disclaimerTitle: '⚠️ Informazioni su riassunti e parole chiave',
  disclaimerLine1:
    "Usiamo l'IA per generare riassunti e termini, così da aiutare a individuare rapidamente i temi nel corpus patristico.",
  disclaimerLine2:
    "La collezione è ancora in espansione e può contenere errori di OCR. Se un tema non compare nella ricerca, potrebbe comunque esistere; se una parola chiave appare, verificare sempre il testo originale prima di un uso accademico.",
  disclaimerLine3:
    "I risultati sono strumenti di supporto: la lettura dell'originale e la revisione umana restano sempre necessarie.",
  disclaimerDataLang:
    "🌐 I riassunti e le parole chiave sono stati generati in portoghese brasiliano e non cambiano in base alla lingua dell'interfaccia.",
  disclaimerAriaLabel: 'Avviso sui contenuti generati dall IA',

  // ─── Footer ────────────────────────────────────────────────────────────────
  footerLeft: 'Progetto collaborativo • <a href="https://github.com/Fabio3rs/BibliothecaPatristica" target="_blank" rel="noopener noreferrer">codice aperto</a>',
  footerRight: 'GitHub Pages',

  // ─── Pagina iniziale (index) ───────────────────────────────────────────────
  homeTitle: 'Bibliotheca Patristica Digital',
  homeSubtitle:
    "Un'interfaccia di ricerca ed esplorazione per le collezioni <strong>Patrologia Graeca (PG)</strong>, <strong>Latina (PL)</strong> e <strong>Orientalis (PO)</strong> di J.P. Migne. I testi originali restano in greco, latino e altre lingue orientali; ciò che forniamo in portoghese sono <strong>riassunti, parole chiave e metadati arricchiti con tecniche di elaborazione del testo</strong>, per aiutarti a localizzare e comprendere il corpus.",
  homeCtaSearch: 'Avvia la ricerca',
  homeCtaExample: 'Visualizza pagina di esempio (Crisostomo)',

  // ─── Informazioni generali ────────────────────────────────────────────────
  aboutTitle: "Che cos'è la Bibliotheca Patristica?",
  aboutP1: "Per i cattolici e gli studiosi che desiderano approfondire la propria fede, entrare nel mondo della Patristica e della Patrologia è come scoprire le radici profonde dell'albero che oggi ci nutre con i frutti della dottrina cristiana. Il progetto <strong>Bibliotheca Patristica</strong> nasce con l'obiettivo di facilitare questo recupero storico, intellettuale e spirituale, rendendo più accessibile il tesoro della Tradizione.",
  aboutP2: "Anche se i termini sono spesso usati come sinonimi nella lingua comune, esiste una distinzione tecnica importante tra queste due aree di studio. La <strong>Patristica</strong> si dedica al pensiero e alla teologia dei Padri della Chiesa, concentrandosi sul ricchissimo contenuto dottrinale e filosofico che essi produssero. La <strong>Patrologia</strong>, invece, si occupa della storia e della letteratura di questi autori, indagandone le biografie, il contesto storico in cui vissero e l'autenticità dei loro preziosi scritti.",
  aboutP3: "Ma, in fondo, chi sono i cosiddetti «Padri della Chiesa»? Sono i grandi leader, pastori e teologi cristiani vissuti tra il I e l'VIII secolo d.C. Perché un autore riceva questo titolo da parte della Chiesa, deve soddisfare quattro criteri fondamentali: l'<em>antichità</em> (aver vissuto nell'epoca della Chiesa primitiva), la <em>santità di vita</em> (una testimonianza esemplare secondo il Vangelo), l'<em>ortodossia</em> (aver insegnato in piena comunione con la fede cattolica) e, infine, l'<em>approvazione ecclesiastica</em> (il riconoscimento ufficiale del suo lascito).",
  aboutP4: "Lo studio di queste opere va ben oltre la curiosità storica; è una necessità per la solidità della fede. Fu proprio in questo periodo formativo che dottrine centrali del cristianesimo, come il mistero della Santissima Trinità e la divinità di Cristo, vennero definite e difese con forza contro le eresie del tempo. Inoltre, i primi Padri — detti «Apostolici» — furono discepoli diretti degli Apostoli o vissero a stretto contatto con persone vicine a loro. Bere a questa fonte è il modo più vicino per arrivare alla purezza e al fervore del cristianesimo delle origini.",
  aboutP5: "Questi primi maestri ci hanno anche lasciato la chiave per la corretta interpretazione della Bibbia, insegnandoci a leggere le Sacre Scritture non in modo isolato, ma sempre illuminate dalla Tradizione. Sorprendentemente, molte delle sfide che affrontiamo nel mondo moderno — dalle crisi di fede agli attacchi alla moralità — erano già state affrontate con brillantezza da questi autori secoli fa.",
  aboutP6: "In questa biblioteca, il lettore è invitato a incontrare giganti della nostra fede. Troverà echi di menti come sant'Agostino, il «Dottore della Grazia», la cui opera è essenziale per comprendere il peccato originale e l'amore di Dio; sant'Ireneo di Lione, instancabile difensore dell'unità della fede contro il gnosticismo; sant'Ignazio di Antiochia, discepolo dell'apostolo san Giovanni, le cui lettere rivelano la struttura della Chiesa già nel II secolo; e san Girolamo, il monumentale traduttore della Bibbia in latino (la Vulgata).",
  aboutP7: "La <strong>Bibliotheca Patristica</strong> è un progetto <strong>open source</strong>, frutto di uno sforzo collaborativo per preservare e diffondere questo lascito. Puoi accedere al codice sorgente, segnalare problemi o contribuire al suo sviluppo nel nostro repository su <a href=\"https://github.com/Fabio3rs/BibliothecaPatristica\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub</a>.",

  statVolumes: 'Volumi coperti',
  statSeries: '3 serie',
  statSeriesLabel: 'Graeca, Latina e Orientalis',
  statPages: 'Pagine indicizzate',
  statTokens: '1B+',
  statTokensLabel: 'Token elaborati',

  startPointsTitle: 'Punti di partenza',
  startPointsDesc:
    'Non sai da dove cominciare? Esplora il corpus a partire da questi autori e temi fondamentali.',

  tagCloudTitle: 'Nuvola di parole chiave',
  tagCloudSubtitle: 'Esplora i temi',
  tagCloudNote: "Basata sull'estrazione automatica dei termini.",
  tagCloudLoading: 'Caricamento tag...',
  tagCloudEmpty: 'Nessuna parola chiave trovata.',
  tagCloudError: 'Impossibile caricare le parole chiave.',
  statsLoadError: 'Statistiche non caricate:',

  // ─── Personas ──────────────────────────────────────────────────────────────
  aboutP8:
    'Importante: la maggior parte del corpus resta nelle lingue originali — greco, latino, siriaco, copto e altre lingue orientali. Ciò che trovi in portoghese su questa piattaforma sono <strong>riassunti, parole chiave e metadati estratti automaticamente</strong> tramite elaborazione del testo e intelligenza artificiale, che fungono da ponte per navigare e comprendere il corpus.',

  personasTitle: 'A chi è destinato questo archivio?',
  personasDesc: 'Scegli il tuo profilo e scopri come la Bibliotheca Patristica può aiutarti.',
  personaDevotoTitle: 'Sono un devoto',
  personaDevotoDesc:
    'Voglio conoscere i Padri della Chiesa, le loro lettere e i loro sermoni. Inizia dagli autori più letti e dai temi spirituali.',
  personaDevotoCta: 'Esplora gli autori',
  personaTheologyTitle: 'Studio teologia',
  personaTheologyDesc:
    'Devo localizzare rapidamente trattati sulla Trinità, la Cristologia o la Scrittura. Usa la ricerca avanzata e i filtri per collezione.',
  personaTheologyCta: 'Vai alla ricerca',
  personaResearcherTitle: 'Sono un ricercatore',
  personaResearcherDesc:
    'Cerco fonti primarie per articoli o tesi. Esplora per volume, autore e parole chiave con metadati arricchiti.',
  personaResearcherCta: 'Cerca nel corpus',

  // ─── Ricerca ───────────────────────────────────────────────────────────────
  searchTitle: 'Risultati',
  searchSubtitle: 'Ricerca testuale nel corpus patristico',
  searchPlaceholder: 'Cerca...',
  searchItemsLabel: 'Elementi:',
  searchLoading: 'Caricamento dell indice...',
  searchBusy: 'Ricerca in corso...',
  searchNoResults: 'Nessun risultato trovato',
  searchNoResultsDesc:
    'Non abbiamo trovato pagine che corrispondono ai criteri di ricerca e ai filtri selezionati.',
  searchClearFilters: 'Cancella tutti i filtri',
  searchIndexUnavailable: "Indice Pagefind non disponibile. Genera l'indice e ricarica.",
  searchIndexEmpty: 'Indice Pagefind non disponibile o vuoto.',
  searchIndexError: "Errore nella ricerca dell'indice.",
  searchNoTitle: 'Senza titolo',
  searchNoSnippet: 'Nessun estratto.',

  // ─── Filtri (FiltersPanel) ────────────────────────────────────────────────
  filtersTitle: 'Filtri',
  filtersLoading: 'Caricamento filtri...',
  filtersUnavailable: 'Filtri non disponibili.',
  filtersNoOptions: 'Nessuna opzione',
  filterCollection: 'Collezione',
  filterVolume: 'Volume',
  filterKeywords: 'Parole chiave',
  filterKeywordsHint: 'Fai clic per cercare per parola chiave.',
  filterKeywordsLoading: 'Caricamento…',
  filterKeywordsUnavailable: 'Non disponibile.',

  paginationShowing: (start: number, end: number, total: number) =>
    `Mostrando ${start}–${end} di ${total} risultati`,
  paginationPage: (page: number, total: number) => `Pagina ${page} / ${total}`,
  paginationPrev: '← Precedente',
  paginationNext: 'Successiva →',
  paginationPrevLabel: 'Pagina precedente',
  paginationNextLabel: 'Pagina successiva',

  // ─── Viewer ────────────────────────────────────────────────────────────────
  viewerLoading: 'Caricamento pagina...',
  viewerVolume: (id: string, title: string) =>
    `Volume ${id}${title ? ' — ' + title : ''}`,
  viewerPage: (n: number) => `Pagina ${n}`,
  viewerPrev: '← Precedente',
  viewerNext: 'Successiva →',
  viewerFirst: 'Prima',
  viewerLast: 'Ultima',
  viewerGo: 'Vai',
  viewerPageInputLabel: 'Numero di pagina',
  viewerFocusMode: 'Modalità focus',
  viewerNormalMode: 'Modalità normale',
  viewerDecreaseFont: 'A-',
  viewerIncreaseFont: 'A+',
  viewerContinueReading: 'Continua la lettura',
  viewerProgress: 'Progresso',
  viewerSearchInVolume: 'Cerca in questo volume…',
  viewerIndex: 'Indice',
  viewerNoIdentity: 'Informazioni sul volume non disponibili',
  viewerBack: '↩ Torna alla ricerca',
  viewerSummaryPage: 'Riassunto della pagina',
  viewerSummaryGlobal: 'Riassunto globale',
  viewerExcerpt: 'Estratto notevole',
  viewerNoContent: 'Nessun contenuto disponibile.',
  viewerOcrTitle: 'OCR originale',
  viewerOcrLoading: 'Caricamento…',
  viewerOcrError: (msg: string) => `Errore nel caricamento dell'OCR: ${msg}`,
  viewerOcrOpen: '↗ apri file',
  viewerOcrStructured: 'Vista strutturata',
  viewerOcrPlain: 'Testo grezzo',
  viewerOcrVisual: 'Layout visuale',
  viewerOcrBlockType: 'Tipo',
  viewerOcrBlockScript: 'Script',
  viewerOcrNoBlocks: 'Nessun blocco strutturato trovato.',
  viewerMetaKeywords: 'Parole chiave',
  viewerMetaEntities: 'Entità',
  viewerMetaSegments: 'Segmenti',
  viewerMetaSnapshots: 'Snapshot',
  viewerRelated: 'Pagine correlate',
  viewerRelatedNote: '(Distanza semantica tramite LLM)',
  viewerRelatedSim: (sim: number) => `somiglianza ${sim.toFixed(2)}`,
  viewerRelatedTitle: (doc: string, title: string, page: number) =>
    `${doc}${title ? ' — ' + title : ''} · pag. ${page}`,
  viewerError: (msg: string) => `Errore nel caricamento del visualizzatore: ${msg}`,

  // ─── PageMetadata (componente) ─────────────────────────────────────────────
  metaPageLabel: 'Pagina',
  metaOpenOcr: 'Apri OCR grezzo',
  metaSummaryTitle: 'Riassunto',
  metaSummaryEmpty: 'Nessun riassunto disponibile.',
  metaExcerptTitle: 'Estratto',
  metaExcerptEmpty: 'Nessun estratto fornito.',
  metaKeywords: 'Parole chiave',
  metaEntities: 'Entità',
  metaSegments: 'Segmenti',
  metaSnapshots: 'Snapshot',

  // ─── RawToggle ─────────────────────────────────────────────────────────────
  rawOpen: 'Apri OCR grezzo',
  rawUnavailable: 'OCR grezzo non pubblicato in questo mirror.',

  // ─── Breadcrumbs ───────────────────────────────────────────────────────────
  breadcrumbNav: 'Percorso',
  breadcrumbHome: 'Home',
  breadcrumbSearch: 'Ricerca',
  breadcrumbViewer: 'Lettura',

  // ─── Accessibilità / ARIA ──────────────────────────────────────────────────
  ariaMetadata: 'Metadati',
  ariaResults: 'Risultati',
  ariaFilterBy: (cat: string, val: string) => `Filtra per ${cat}: ${val}`,
  ariaOccurrences: (n: number) => (n === 1 ? '1 occorrenza' : `${n} occorrenze`),
};

export type Translations = BaseTranslations;
