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
import type { Translations } from './pt-br';

export type { Translations };

/** Locales suportados */
export type SupportedLocale = 'pt-br' | 'en';

const dict: Record<SupportedLocale, Translations> = {
  'pt-br': ptBR,
  en,
};

/** Normaliza o locale recebido do Astro para nosso conjunto suportado */
function normalizeLocale(locale: string | undefined): SupportedLocale {
  if (!locale) return 'pt-br';
  const lower = locale.toLowerCase();
  if (lower === 'en' || lower.startsWith('en-')) return 'en';
  return 'pt-br';
}

/**
 * Retorna o objeto de traduções para o locale ativo.
 * Usa pt-BR como fallback para qualquer locale não suportado.
 */
export function useTranslations(locale: string | undefined): Translations {
  return dict[normalizeLocale(locale)];
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
 *
 * @param currentLocale  locale atual ('pt-br' | 'en')
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
  const baseNoSlash = base.endsWith('/') ? base.slice(0, -1) : base;

  if (locale === 'pt-br') {
    // Vai para EN: injeta /en/ após o base
    // /BibliothecaPatristica/search  →  /BibliothecaPatristica/en/search
    // /BibliothecaPatristica         →  /BibliothecaPatristica/en
    const withoutBase = currentPath.startsWith(baseNoSlash)
      ? currentPath.slice(baseNoSlash.length)
      : currentPath;
    const clean = withoutBase.replace(/^\//, '');
    return `${baseNoSlash}/en${clean ? '/' + clean : ''}${search}`;
  } else {
    // Vai para PT-BR: remove o segmento /en/ do path
    // /BibliothecaPatristica/en/search  →  /BibliothecaPatristica/search
    // /BibliothecaPatristica/en          →  /BibliothecaPatristica/
    const withoutBase = currentPath.startsWith(baseNoSlash)
      ? currentPath.slice(baseNoSlash.length)
      : currentPath;
    const withoutEn = withoutBase.replace(/^\/en(\/|$)/, '/');
    return `${baseNoSlash}${withoutEn || '/'}${search}`;
  }
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
  return {
    ...s,
    paginationShowing: isEn
      ? (start: number, end: number, total: number) =>
          `Showing ${start}–${end} of ${total} results`
      : (start: number, end: number, total: number) =>
          `Mostrando ${start}–${end} de ${total} resultados`,
    paginationPage: isEn
      ? (page: number, total: number) => `Page ${page} / ${total}`
      : (page: number, total: number) => `Página ${page} / ${total}`,
    viewerVolume: isEn
      ? (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`
      : (id: string, title: string) => `Volume ${id}${title ? ' — ' + title : ''}`,
    viewerPage: isEn
      ? (n: number) => `Page ${n}`
      : (n: number) => `Página ${n}`,
    viewerOcrError: isEn
      ? (msg: string) => `Error loading OCR: ${msg}`
      : (msg: string) => `Erro ao carregar OCR: ${msg}`,
    viewerRelatedSim: isEn
      ? (sim: number) => `similarity ${sim.toFixed(2)}`
      : (sim: number) => `similaridade ${sim.toFixed(2)}`,
    viewerRelatedTitle: isEn
      ? (doc: string, title: string, page: number) =>
          `${doc}${title ? ' — ' + title : ''} · p. ${page}`
      : (doc: string, title: string, page: number) =>
          `${doc}${title ? ' — ' + title : ''} · pág. ${page}`,
    viewerError: isEn
      ? (msg: string) => `Error loading viewer: ${msg}`
      : (msg: string) => `Erro ao carregar viewer: ${msg}`,
    ariaFilterBy: isEn
      ? (cat: string, val: string) => `Filter by ${cat}: ${val}`
      : (cat: string, val: string) => `Filtrar por ${cat}: ${val}`,
    ariaOccurrences: isEn
      ? (n: number) => `${n} occurrence(s)`
      : (n: number) => `${n} ocorrência(s)`,
  };
}
