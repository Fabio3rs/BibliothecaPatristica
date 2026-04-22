/**
 * Strings de interface — Português do Brasil (idioma padrão)
 *
 * NOTA SOBRE OS DADOS: resumos, palavras-chave e entidades gerados por IA
 * estão em português do Brasil, independentemente do idioma da UI.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const ptBR: Record<string, any> = {
  // ─── Layout / Topbar ───────────────────────────────────────────────────────
  skipToContent: 'Pular para o conteúdo',
  logoLabel: 'Bibliotheca Patristica — página inicial',
  logoText: 'Bibliotheca',
  tagline: 'Acervo digital aberto da literatura patrística',
  navSearch: 'Busca',
  navViewer: 'Leitura',
  navMenuLabel: 'Menu de navegação',
  /** Botão de troca de idioma na topbar */
  langSwitchLabel: 'Switch to English',
  langSwitchCurrent: 'PT',
  langSwitchOther: 'EN',

  // ─── Disclaimer (aviso IA) ─────────────────────────────────────────────────
  disclaimerTitle: '⚠️ Sobre resumos e palavras‑chave',
  disclaimerLine1:
    'Usamos IA para gerar resumos e termos em português e ajudar a localizar rapidamente temas no corpus patrístico.',
  disclaimerLine2:
    'O acervo ainda está em expansão e pode conter erros de OCR. Se um tema não aparecer na busca, ele pode existir; se uma palavra‑chave surgir, confirme no texto antes de usar academicamente.',
  disclaimerLine3:
    'Resultados são auxiliares: leitura do original e revisão humana são sempre necessárias.',
  /** Aviso específico: dados gerados em PT-BR mesmo com UI em outro idioma */
  disclaimerDataLang:
    '🌐 Os resumos e palavras-chave foram gerados em português do Brasil e não variam conforme o idioma da interface.',
  disclaimerAriaLabel: 'Aviso sobre o conteúdo gerado por IA',

  // ─── Footer ────────────────────────────────────────────────────────────────
  footerLeft: 'Projeto colaborativo • código aberto',
  footerRight: 'GitHub Pages',

  // ─── Página inicial (index) ────────────────────────────────────────────────
  homeTitle: 'Bibliotheca Patristica Digital',
  homeSubtitle:
    'Uma interface de pesquisa e exploração para as coleções da <strong>Patrologia Graeca (PG)</strong>, <strong>Latina (PL)</strong> e <strong>Orientalis (PO)</strong> de J.P. Migne. Navegue por um dos maiores acervos da literatura cristã antiga através de uma busca textual poderosa e metadados enriquecidos por IA.',
  homeCtaSearch: 'Iniciar Pesquisa',
  homeCtaExample: 'Ver Página de Exemplo (Crisóstomo)',

  statVolumes: 'Volumes Cobertos',
  statSeries: '3 Séries',
  statSeriesLabel: 'Graeca, Latina & Orientalis',
  statPages: 'Páginas Indexadas',
  statTokens: '1B+',
  statTokensLabel: 'Tokens Processados',

  startPointsTitle: 'Pontos de Partida',
  startPointsDesc:
    'Não sabe por onde começar? Explore o acervo a partir destes autores e temas fundamentais.',

  tagCloudTitle: 'Nuvem de Palavras-Chave',
  tagCloudSubtitle: 'Explorar Temas',
  tagCloudNote: 'Baseada em extração automática de termos.',
  tagCloudLoading: 'Carregando tags...',
  tagCloudEmpty: 'Nenhuma palavra-chave encontrada.',
  tagCloudError: 'Falha ao carregar as palavras-chave.',
  statsLoadError: 'Stats não carregadas:',

  // ─── Personas ──────────────────────────────────────────────────────────────
  personasTitle: 'Para quem é este acervo?',
  personasDesc: 'Escolha o seu perfil e descubra como a Bibliotheca Patristica pode ajudar.',
  personaDevotoTitle: 'Sou devoto',
  personaDevotoDesc:
    'Quero conhecer os Pais da Igreja, suas cartas e sermões. Comece pelos autores mais lidos e pelos temas espirituais.',
  personaDevotoCta: 'Explorar autores',
  personaTheologyTitle: 'Estudo teologia',
  personaTheologyDesc:
    'Preciso localizar rapidamente tratados sobre Trindade, Cristologia ou Escritura. Use a busca avançada e os filtros por coleção.',
  personaTheologyCta: 'Ir para a busca',
  personaResearcherTitle: 'Sou pesquisador',
  personaResearcherDesc:
    'Busco fontes primárias para artigos ou dissertações. Navegue por volume, autor e palavras-chave com metadados enriquecidos.',
  personaResearcherCta: 'Pesquisar no acervo',

  // ─── Busca (search) ────────────────────────────────────────────────────────
  searchTitle: 'Resultados',
  searchSubtitle: 'Busca textual no acervo patrístico',
  searchPlaceholder: 'Buscar...',
  searchItemsLabel: 'Itens:',
  searchLoading: 'Carregando índice...',
  searchBusy: 'Buscando...',
  searchNoResults: 'Nenhum resultado encontrado',
  searchNoResultsDesc:
    'Não encontramos páginas que correspondam aos seus critérios de busca e filtros.',
  searchClearFilters: 'Limpar todos os filtros',
  searchIndexUnavailable: 'Índice Pagefind não disponível. Gere o índice e recarregue.',
  searchIndexEmpty: 'Índice Pagefind não disponível ou vazio.',
  searchIndexError: 'Erro ao buscar no índice.',
  searchNoTitle: 'Sem título',
  searchNoSnippet: 'Sem trecho.',

  // ─── Filtros (FiltersPanel) ────────────────────────────────────────────────
  filtersTitle: 'Filtros',
  filtersLoading: 'Carregando filtros...',
  filtersUnavailable: 'Filtros indisponíveis.',
  filtersNoOptions: 'Sem opções',
  filterCollection: 'Coleção',
  filterVolume: 'Volume',
  filterKeywords: 'Keywords',
  filterKeywordsHint: 'Clique para buscar por palavra-chave.',
  filterKeywordsLoading: 'Carregando…',
  filterKeywordsUnavailable: 'Indisponível.',

  paginationShowing: (start: number, end: number, total: number) =>
    `Mostrando ${start}–${end} de ${total} resultados`,
  paginationPage: (page: number, total: number) => `Página ${page} / ${total}`,
  paginationPrev: '← Anterior',
  paginationNext: 'Próxima →',
  paginationPrevLabel: 'Página anterior',
  paginationNextLabel: 'Próxima página',

  // ─── Viewer ────────────────────────────────────────────────────────────────
  viewerLoading: 'Carregando página...',
  viewerVolume: (id: string, title: string) =>
    `Volume ${id}${title ? ' — ' + title : ''}`,
  viewerPage: (n: number) => `Página ${n}`,
  viewerPrev: '← Anterior',
  viewerNext: 'Próxima →',
  viewerFirst: 'Primeira',
  viewerLast: 'Última',
  viewerGo: 'Ir',
  viewerPageInputLabel: 'Número da página',
  viewerFocusMode: 'Modo foco',
  viewerNormalMode: 'Modo normal',
  viewerDecreaseFont: 'A-',
  viewerIncreaseFont: 'A+',
  viewerContinueReading: 'Continuar lendo',
  viewerProgress: 'Progresso',
  viewerSearchInVolume: 'Buscar neste volume…',
  viewerIndex: 'Índice',
  viewerNoIdentity: 'Informação do volume não disponível',
  viewerBack: '↩ Voltar à busca',
  viewerSummaryPage: 'Resumo da página',
  viewerSummaryGlobal: 'Resumo global',
  viewerExcerpt: 'Trecho Notável',
  viewerNoContent: 'Sem conteúdo disponível.',
  viewerOcrTitle: 'OCR Original',
  viewerOcrLoading: 'Carregando…',
  viewerOcrError: (msg: string) => `Erro ao carregar OCR: ${msg}`,
  viewerOcrOpen: '↗ abrir arquivo',
  viewerOcrStructured: 'Visualização estruturada',
  viewerOcrPlain: 'Texto bruto',
  viewerOcrBlockType: 'Tipo',
  viewerOcrBlockScript: 'Script',
  viewerOcrNoBlocks: 'Nenhum bloco estruturado encontrado.',
  viewerMetaKeywords: 'Keywords',
  viewerMetaEntities: 'Entidades',
  viewerMetaSegments: 'Segmentos',
  viewerMetaSnapshots: 'Snapshots',
  viewerRelated: 'Páginas relacionadas',
  viewerRelatedNote: '(Distância semântica por LLM)',
  viewerRelatedSim: (sim: number) => `similaridade ${sim.toFixed(2)}`,
  viewerRelatedTitle: (doc: string, title: string, page: number) =>
    `${doc}${title ? ' — ' + title : ''} · pág. ${page}`,
  viewerError: (msg: string) => `Erro ao carregar viewer: ${msg}`,

  // ─── PageMetadata (componente) ─────────────────────────────────────────────
  metaPageLabel: 'Página',
  metaOpenOcr: 'Abrir OCR bruto',
  metaSummaryTitle: 'Resumo',
  metaSummaryEmpty: 'Sem resumo disponível.',
  metaExcerptTitle: 'Trecho',
  metaExcerptEmpty: 'Sem trecho fornecido.',
  metaKeywords: 'Keywords',
  metaEntities: 'Entidades',
  metaSegments: 'Segmentos',
  metaSnapshots: 'Snapshots',

  // ─── RawToggle ─────────────────────────────────────────────────────────────
  rawOpen: 'Abrir OCR bruto',
  rawUnavailable: 'OCR bruto não publicado neste espelho.',

  // ─── Breadcrumbs ───────────────────────────────────────────────────────────
  breadcrumbNav: 'Breadcrumb',
  breadcrumbHome: 'Início',
  breadcrumbSearch: 'Busca',
  breadcrumbViewer: 'Leitura',

  // ─── Acessibilidade / ARIA ─────────────────────────────────────────────────
  ariaMetadata: 'Metadados',
  ariaResults: 'Resultados',
  ariaFilterBy: (cat: string, val: string) => `Filtrar por ${cat}: ${val}`,
  ariaOccurrences: (n: number) => `${n} ocorrência(s)`,
};

export type Translations = typeof ptBR;
