/**
 * i18n — entry point
 *
 * Uso no servidor (componentes .astro):
 *   import { useTranslations } from '../i18n';
 *   const t = useTranslations(Astro.currentLocale);
 *
 * Uso no cliente (scripts inline):
 *   <!-- define:vars={{ uiStrings: serializeTranslations(t) }} -->
 *   // uiStrings é um objeto JSON com apenas strings/números (sem funções).
 *   // Funções de formatação são re-criadas no cliente via clientTranslations().
 */

import { ptBR } from './pt-br';
import { en } from './en';
import { it } from './it';
import { fr } from './fr';
import type { Translations } from './pt-br';

export type { Translations };

/** Locales suportados */
export type SupportedLocale = 'pt-br' | 'en' | 'it' | 'fr';

const dict: Record<SupportedLocale, Translations> = {
  'pt-br': ptBR,
  en,
  it,
  fr,
};

/** Normaliza o locale recebido do Astro para nosso conjunto suportado */
function normalizeLocale(locale: string | undefined): SupportedLocale {
  if (!locale) return 'pt-br';
  const lower = locale.toLowerCase();
  if (lower === 'en' || lower.startsWith('en-')) return 'en';
  if (lower === 'it' || lower.startsWith('it-')) return 'it';
  if (lower === 'fr' || lower.startsWith('fr-')) return 'fr';
  return 'pt-br';
}

/**
 * Retorna o objeto de traduções para o locale ativo.
 * Usa pt-BR como fallback para qualquer locale não suportado.
 */
export function useTranslations(locale: string | undefined): Translations {
  return dict[normalizeLocale(locale)];
}

export function getLocaleBase(
  locale: string | undefined,
  base: string,
): string {
  const normalized = normalizeLocale(locale);
  const baseNoSlash = base.endsWith('/') ? base.slice(0, -1) : base;
  return normalized === 'pt-br'
    ? `${baseNoSlash}/`
    : `${baseNoSlash}/${normalized}/`;
}

function detectLocaleFromPath(
  currentPath: string,
  base: string,
): SupportedLocale {
  const baseNoSlash = base.endsWith('/') ? base.slice(0, -1) : base;
  const withoutBase = currentPath.startsWith(baseNoSlash)
    ? currentPath.slice(baseNoSlash.length)
    : currentPath;
  const normalizedPath = withoutBase.startsWith('/') ? withoutBase : `/${withoutBase}`;
  if (normalizedPath === '/en' || normalizedPath.startsWith('/en/')) return 'en';
  if (normalizedPath === '/it' || normalizedPath.startsWith('/it/')) return 'it';
  if (normalizedPath === '/fr' || normalizedPath.startsWith('/fr/')) return 'fr';
  return 'pt-br';
}

function stripLocaleFromPath(
  currentPath: string,
  base: string,
): string {
  const baseNoSlash = base.endsWith('/') ? base.slice(0, -1) : base;
  const withoutBase = currentPath.startsWith(baseNoSlash)
    ? currentPath.slice(baseNoSlash.length)
    : currentPath;
  const normalizedPath = withoutBase.startsWith('/') ? withoutBase : `/${withoutBase}`;
  if (normalizedPath === '/en' || normalizedPath === '/it' || normalizedPath === '/fr') return '/';
  if (normalizedPath.startsWith('/en/')) return normalizedPath.slice(3) || '/';
  if (normalizedPath.startsWith('/it/')) return normalizedPath.slice(3) || '/';
  if (normalizedPath.startsWith('/fr/')) return normalizedPath.slice(3) || '/';
  return normalizedPath || '/';
}

export function getLocalizedPath(
  targetLocale: SupportedLocale,
  currentPath: string,
  base: string,
): string {
  const localeBase = getLocaleBase(targetLocale, base).replace(/\/$/, '');
  const strippedPath = stripLocaleFromPath(currentPath, base);
  const cleanPath = strippedPath === '/'
    ? ''
    : `/${strippedPath.replace(/^\/+|\/+$/g, '')}`;
  const localized = `${localeBase}${cleanPath}` || '/';
  return localized.endsWith('/') ? localized : `${localized}/`;
}

export function getLocaleRouteUrl(
  targetLocale: SupportedLocale,
  route: '' | '/' | string,
  base: string,
  search: string = '',
  hash: string = '',
): string {
  const localeBase = getLocaleBase(targetLocale, base).replace(/\/$/, '');
  const cleanRoute = !route || route === '/'
    ? ''
    : `/${String(route).replace(/^\/+|\/+$/g, '')}`;
  const pathname = `${localeBase}${cleanRoute}` || '/';
  const pathnameWithSlash = pathname.endsWith('/') ? pathname : `${pathname}/`;
  return `${pathnameWithSlash}${search}${hash}`;
}

/**
 * Serializa strings, números, arrays e objetos simples do dicionário para
 * injeção segura via `define:vars`. Funções são excluídas e recriadas no
 * cliente por `clientTranslations()`.
 */
export function serializeTranslations(
  t: Translations,
): Record<string, unknown> {
  const serializable = (value: unknown): unknown => {
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' || value === null) return value;
    if (Array.isArray(value)) return value.map(serializable).filter((item) => item !== undefined);
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.entries(value).map(([key, entry]) => [key, serializable(entry)]).filter(([, entry]) => entry !== undefined),
      );
    }
    return undefined;
  };
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(t)) {
    const value = serializable(v);
    if (value !== undefined) out[k] = value;
  }
  return out;
}

/**
 * Calcula a URL equivalente no outro locale.
 *
 * Regra de roteamento:
 *  - PT-BR (padrão): /search, /viewer, /
 *  - EN:             /en/search, /en/viewer, /en
 *  - IT:             /it/search, /it/viewer, /it
 *  - FR:             /fr/search, /fr/viewer, /fr
 *
 * @param currentLocale  locale atual ('pt-br' | 'en' | 'it' | 'fr')
 * @param currentPath    pathname completo, incluindo o base do Astro (ex: /BibliothecaPatristica/search)
 * @param base           BASE_URL do Astro, ex: /BibliothecaPatristica
 * @param search         query string atual (ex: ?doc=PG144&page=1)
 */
export function getLocaleSwitchUrl(
  currentLocale: string | undefined,
  currentPath: string,
  base: string,
  search: string = '',
): string {
  const locale = normalizeLocale(currentLocale);
  const locales: SupportedLocale[] = ['pt-br', 'en', 'it', 'fr'];
  const nextLocale = locales[(locales.indexOf(locale) + 1) % locales.length];
  return getLocaleUrl(nextLocale, currentPath, base, search);
}

/**
 * Calcula a URL de um locale específico para o caminho atual.
 */
export function getLocaleUrl(
  targetLocale: SupportedLocale,
  currentPath: string,
  base: string,
  search: string = '',
): string {
  const currentLocale = detectLocaleFromPath(currentPath, base);
  const localizedPath = getLocalizedPath(targetLocale, currentPath, base);
  const currentLocaleBase = getLocaleBase(currentLocale, base).replace(/\/$/, '');
  const targetLocaleBase = getLocaleBase(targetLocale, base).replace(/\/$/, '');
  const normalizedPath = localizedPath.startsWith(targetLocaleBase)
    ? localizedPath
    : localizedPath.replace(currentLocaleBase, targetLocaleBase);
  return `${normalizedPath}${search}`;
}

/**
 * Reconstrói o objeto completo de traduções no cliente, recebendo as strings
 * serializadas e o locale como string, e reinjetando as funções de formatação.
 * Chamado dentro dos scripts inline das páginas.
 */
export function buildClientTranslations(
  s: Record<string, string | number>,
  locale: string,
) {
  const isEn = locale === 'en';
  const isIt = locale === 'it';
  const isFr = locale === 'fr';
  return {
    ...s,
    paginationShowing: isEn
      ? (start: number, end: number, total: number) =>
          `Showing ${start}–${end} of ${total} results`
      : isIt
        ? (start: number, end: number, total: number) =>
            `Mostrando ${start}–${end} di ${total} risultati`
        : isFr
          ? (start: number, end: number, total: number) =>
              `Affichage de ${start} à ${end} sur ${total} résultats`
      : (start: number, end: number, total: number) =>
          `Mostrando ${start}–${end} de ${total} resultados`,
    paginationPage: isEn
      ? (page: number, total: number) => `Page ${page} / ${total}`
      : isIt
        ? (page: number, total: number) => `Pagina ${page} / ${total}`
        : isFr
          ? (page: number, total: number) => `Page ${page} / ${total}`
      : (page: number, total: number) => `Página ${page} / ${total}`,
    viewerVolume: isEn
      ? (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`
      : isIt
        ? (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`
        : isFr
          ? (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`
      : (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`,
    viewerPage: isEn
      ? (n: number) => `Page ${n}`
      : isIt
        ? (n: number) => `Pagina ${n}`
        : isFr
          ? (n: number) => `Page ${n}`
      : (n: number) => `Página ${n}`,
    viewerPageOfTotal: isEn
      ? (n: number, total: number) => `Page ${n} of ${total}`
      : isIt
        ? (n: number, total: number) => `Pagina ${n} di ${total}`
        : isFr
          ? (n: number, total: number) => `Page ${n} sur ${total}`
      : (n: number, total: number) => `Página ${n} de ${total}`,
    viewerFirst: isEn ? 'First' : isIt ? 'Prima' : isFr ? 'Première' : 'Primeira',
    viewerLast: isEn ? 'Last' : isIt ? 'Ultima' : isFr ? 'Dernière' : 'Última',
    viewerGo: isEn ? 'Go' : isIt ? 'Vai' : isFr ? 'Aller' : 'Ir',
    viewerPageInputLabel: isEn ? 'Page number' : isIt ? 'Numero di pagina' : isFr ? 'Numéro de page' : 'Número da página',
    viewerFocusMode: isEn ? 'Reading mode' : isIt ? 'Modalità lettura' : isFr ? 'Mode lecture' : 'Modo leitura',
    viewerNormalMode: isEn ? 'Default mode' : isIt ? 'Modalità standard' : isFr ? 'Mode standard' : 'Modo padrão',
    viewerDecreaseFont: 'A-',
    viewerIncreaseFont: 'A+',
    viewerContinueReading: isEn ? 'Continue reading' : isIt ? 'Continua la lettura' : isFr ? 'Continuer la lecture' : 'Continuar lendo',
    viewerProgress: isEn ? 'Progress' : isIt ? 'Progresso' : isFr ? 'Progression' : 'Progresso',
    viewerSearchInVolume: isEn ? 'Search in this volume…' : isIt ? 'Cerca in questo volume…' : isFr ? 'Rechercher dans ce volume…' : 'Buscar neste volume…',
    viewerIndex: isEn ? 'Authors and works' : isIt ? 'Autori e opere' : isFr ? 'Auteurs et œuvres' : 'Autores e obras',
    viewerNoIdentity: isEn
      ? 'Volume information not available'
      : isIt
        ? 'Informazioni sul volume non disponibili'
        : isFr
          ? 'Informations sur le volume indisponibles'
      : 'Informação do volume não disponível',
    viewerOcrError: isEn
      ? () => 'The page text could not be loaded right now. Check your connection and try again. You can also open the transcription file in a new tab.'
      : isIt
        ? () => 'Non è stato possibile caricare il testo della pagina. Controlla la connessione e riprova. Puoi anche aprire il file della trascrizione in una nuova scheda.'
        : isFr
          ? () => 'Le texte de la page n’a pas pu être chargé pour le moment. Vérifiez votre connexion et réessayez. Vous pouvez aussi ouvrir le fichier de transcription dans un nouvel onglet.'
      : () => 'Não foi possível carregar o texto desta página agora. Verifique sua conexão e tente novamente. Se preferir, abra o arquivo da transcrição em uma nova aba.',
    viewerRelatedSim: isEn
      ? (sim: number) => `similarity ${sim.toFixed(2)}`
      : isIt
        ? (sim: number) => `somiglianza ${sim.toFixed(2)}`
        : isFr
          ? (sim: number) => `similarité ${sim.toFixed(2)}`
      : (sim: number) => `similaridade ${sim.toFixed(2)}`,
    viewerRelatedTitle: isEn
      ? (doc: string, title: string, page: number) =>
          `${doc}${title ? ' — ' + title : ''} · p. ${page}`
      : isIt
        ? (doc: string, title: string, page: number) =>
            `${doc}${title ? ' — ' + title : ''} · pag. ${page}`
        : isFr
          ? (doc: string, title: string, page: number) =>
              `${doc}${title ? ' — ' + title : ''} · p. ${page}`
      : (doc: string, title: string, page: number) =>
          `${doc}${title ? ' — ' + title : ''} · pág. ${page}`,
    viewerError: isEn
      ? (msg: string) => `Error loading viewer: ${msg}`
      : isIt
        ? (msg: string) => `Errore nel caricamento del visualizzatore: ${msg}`
        : isFr
          ? (msg: string) => `Erreur lors du chargement du lecteur : ${msg}`
      : (msg: string) => `Erro ao carregar o leitor: ${msg}`,
    ariaFilterBy: isEn
      ? (cat: string, val: string) => `Filter by ${cat}: ${val}`
      : isIt
        ? (cat: string, val: string) => `Filtra per ${cat}: ${val}`
        : isFr
          ? (cat: string, val: string) => `Filtrer par ${cat} : ${val}`
      : (cat: string, val: string) => `Filtrar por ${cat}: ${val}`,
    ariaOccurrences: isEn
      ? (n: number) => `${n} occurrence(s)`
      : isIt
        ? (n: number) => (n === 1 ? '1 occorrenza' : `${n} occorrenze`)
        : isFr
          ? (n: number) => (n === 1 ? '1 occurrence' : `${n} occurrences`)
      : (n: number) => `${n} ocorrência(s)`,
  };
}
