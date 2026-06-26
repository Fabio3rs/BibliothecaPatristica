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
import type { Translations } from './pt-br';

export type { Translations };

/** Locales suportados */
export type SupportedLocale = 'pt-br' | 'en' | 'it';

const dict: Record<SupportedLocale, Translations> = {
  'pt-br': ptBR,
  en,
  it,
};

/** Normaliza o locale recebido do Astro para nosso conjunto suportado */
function normalizeLocale(locale: string | undefined): SupportedLocale {
  if (!locale) return 'pt-br';
  const lower = locale.toLowerCase();
  if (lower === 'en' || lower.startsWith('en-')) return 'en';
  if (lower === 'it' || lower.startsWith('it-')) return 'it';
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
  if (normalizedPath === '/en' || normalizedPath === '/it') return '/';
  if (normalizedPath.startsWith('/en/')) return normalizedPath.slice(3) || '/';
  if (normalizedPath.startsWith('/it/')) return normalizedPath.slice(3) || '/';
  return normalizedPath || '/';
}

export function getLocalizedPath(
  targetLocale: SupportedLocale,
  currentPath: string,
  base: string,
): string {
  const localeBase = getLocaleBase(targetLocale, base).replace(/\/$/, '');
  const strippedPath = stripLocaleFromPath(currentPath, base);
  const cleanPath = strippedPath === '/' ? '' : strippedPath;
  return `${localeBase}${cleanPath}` || '/';
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
    : `/${String(route).replace(/^\/+/, '')}`;
  return `${localeBase}${cleanRoute}${search}${hash}` || '/';
}

/**
 * Serializa apenas as chaves de string/number do objeto de traduções para
 * injeção segura via `define:vars` nos scripts cliente.
 * Funções (ex: paginationShowing, viewerError) são excluídas aqui e
 * recriadas no cliente por `clientTranslations()`.
 */
export function serializeTranslations(
  t: Translations,
): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(t)) {
    if (typeof v === 'string' || typeof v === 'number') {
      out[k] = v;
    }
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
 *
 * @param currentLocale  locale atual ('pt-br' | 'en' | 'it')
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
  const locales: SupportedLocale[] = ['pt-br', 'en', 'it'];
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
  return {
    ...s,
    paginationShowing: isEn
      ? (start: number, end: number, total: number) =>
          `Showing ${start}–${end} of ${total} results`
      : isIt
        ? (start: number, end: number, total: number) =>
            `Mostrando ${start}–${end} di ${total} risultati`
      : (start: number, end: number, total: number) =>
          `Mostrando ${start}–${end} de ${total} resultados`,
    paginationPage: isEn
      ? (page: number, total: number) => `Page ${page} / ${total}`
      : isIt
        ? (page: number, total: number) => `Pagina ${page} / ${total}`
      : (page: number, total: number) => `Página ${page} / ${total}`,
    viewerVolume: isEn
      ? (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`
      : isIt
        ? (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`
      : (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`,
    viewerPage: isEn
      ? (n: number) => `Page ${n}`
      : isIt
        ? (n: number) => `Pagina ${n}`
      : (n: number) => `Página ${n}`,
    viewerPageOfTotal: isEn
      ? (n: number, total: number) => `Page ${n} of ${total}`
      : isIt
        ? (n: number, total: number) => `Pagina ${n} di ${total}`
      : (n: number, total: number) => `Página ${n} de ${total}`,
    viewerFirst: isEn ? 'First' : isIt ? 'Prima' : 'Primeira',
    viewerLast: isEn ? 'Last' : isIt ? 'Ultima' : 'Última',
    viewerGo: isEn ? 'Go' : isIt ? 'Vai' : 'Ir',
    viewerPageInputLabel: isEn ? 'Page number' : isIt ? 'Numero di pagina' : 'Número da página',
    viewerFocusMode: isEn ? 'Focus mode' : isIt ? 'Modalità focus' : 'Modo foco',
    viewerNormalMode: isEn ? 'Normal mode' : isIt ? 'Modalità normale' : 'Modo normal',
    viewerDecreaseFont: 'A-',
    viewerIncreaseFont: 'A+',
    viewerContinueReading: isEn ? 'Continue reading' : isIt ? 'Continua la lettura' : 'Continuar lendo',
    viewerProgress: isEn ? 'Progress' : isIt ? 'Progresso' : 'Progresso',
    viewerSearchInVolume: isEn ? 'Search in this volume…' : isIt ? 'Cerca in questo volume…' : 'Buscar neste volume…',
    viewerIndex: isEn ? 'Index' : isIt ? 'Indice' : 'Índice',
    viewerNoIdentity: isEn
      ? 'Volume information not available'
      : isIt
        ? 'Informazioni sul volume non disponibili'
        : 'Informação do volume não disponível',
    viewerOcrError: isEn
      ? (msg: string) => `Error loading OCR: ${msg}`
      : isIt
        ? (msg: string) => `Errore nel caricamento dell'OCR: ${msg}`
      : (msg: string) => `Erro ao carregar OCR: ${msg}`,
    viewerRelatedSim: isEn
      ? (sim: number) => `similarity ${sim.toFixed(2)}`
      : isIt
        ? (sim: number) => `somiglianza ${sim.toFixed(2)}`
      : (sim: number) => `similaridade ${sim.toFixed(2)}`,
    viewerRelatedTitle: isEn
      ? (doc: string, title: string, page: number) =>
          `${doc}${title ? ' — ' + title : ''} · p. ${page}`
      : isIt
        ? (doc: string, title: string, page: number) =>
            `${doc}${title ? ' — ' + title : ''} · pag. ${page}`
      : (doc: string, title: string, page: number) =>
          `${doc}${title ? ' — ' + title : ''} · pág. ${page}`,
    viewerError: isEn
      ? (msg: string) => `Error loading viewer: ${msg}`
      : isIt
        ? (msg: string) => `Errore nel caricamento del visualizzatore: ${msg}`
      : (msg: string) => `Erro ao carregar viewer: ${msg}`,
    ariaFilterBy: isEn
      ? (cat: string, val: string) => `Filter by ${cat}: ${val}`
      : isIt
        ? (cat: string, val: string) => `Filtra per ${cat}: ${val}`
      : (cat: string, val: string) => `Filtrar por ${cat}: ${val}`,
    ariaOccurrences: isEn
      ? (n: number) => `${n} occurrence(s)`
      : isIt
        ? (n: number) => (n === 1 ? '1 occorrenza' : `${n} occorrenze`)
      : (n: number) => `${n} ocorrência(s)`,
  };
}
