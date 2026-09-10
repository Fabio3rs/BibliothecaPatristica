function asPage(value) {
  const page = Number(value);
  return Number.isInteger(page) && page > 0 ? page : null;
}

function normalizeLabel(value) {
  return String(value || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLocaleLowerCase();
}

export function localizedDisplay(display, locale = 'pt-br', fallback = '') {
  const original = String(display?.original || fallback || '').trim();
  const localized = String(display?.translation?.[locale] || '').trim();
  return {
    primary: localized || original,
    original: localized && original && localized !== original ? original : '',
  };
}

function workContext(works, currentPage, locale) {
  const normalized = (Array.isArray(works) ? works : [])
    .map((work) => {
      const startPage = asPage(work?.reference_start_page);
      const endPage = asPage(work?.reference_end_page);
      if (!startPage && !endPage) return null;
      const first = startPage || endPage;
      const last = endPage || startPage;
      const title = localizedDisplay(work?.title_display, locale, work?.title_raw);
      const author = localizedDisplay(work?.author_display, locale, work?.author_raw);
      return {
        key: String(work?.work_key || ''),
        startPage: Math.min(first, last),
        endPage: Math.max(first, last),
        title,
        author,
      };
    })
    .filter(Boolean);

  const containing = normalized
    .filter((work) => currentPage >= work.startPage && currentPage <= work.endPage)
    .sort((a, b) => {
      const aSpan = a.endPage - a.startPage;
      const bSpan = b.endPage - b.startPage;
      return aSpan - bSpan || b.startPage - a.startPage;
    });

  const selected = containing[0] || null;
  if (!selected) return null;
  const span = Math.max(1, selected.endPage - selected.startPage);
  return {
    ...selected,
    progress: Math.max(0, Math.min(100, Math.round(((currentPage - selected.startPage) / span) * 100))),
  };
}

function entryContexts(sections, locale) {
  const entries = [];
  const seen = new Set();

  for (const section of Array.isArray(sections) ? sections : []) {
    const sectionLabel = localizedDisplay(
      section?.heading_display,
      locale,
      section?.heading_raw || section?.index_kind,
    );
    for (const entry of Array.isArray(section?.entries) ? section.entries : []) {
      const page = asPage(entry?.reference_page);
      if (!page) continue;
      const target = localizedDisplay(
        entry?.target_display,
        locale,
        entry?.target_raw || entry?.entry_raw,
      );
      if (!target.primary) continue;
      const signature = `${page}:${normalizeLabel(target.primary)}`;
      if (seen.has(signature)) continue;
      seen.add(signature);
      entries.push({
        id: String(entry?.id || ''),
        page,
        order: Number(entry?.entry_order) || 0,
        target,
        section: sectionLabel,
      });
    }
  }

  return entries.sort((a, b) => a.page - b.page || a.order - b.order || a.target.primary.localeCompare(b.target.primary));
}

function nearestGroup(entries, currentPage, direction) {
  const candidates = direction === 'previous'
    ? entries.filter((entry) => entry.page < currentPage)
    : entries.filter((entry) => entry.page > currentPage);
  if (!candidates.length) return [];
  const nearestPage = direction === 'previous'
    ? candidates[candidates.length - 1].page
    : candidates[0].page;
  return candidates.filter((entry) => entry.page === nearestPage).slice(0, 2);
}

export function buildViewerIndexContext(payload, currentPage, locale = 'pt-br') {
  const page = asPage(currentPage);
  if (!page) throw new TypeError('currentPage must be a positive integer');

  const entries = entryContexts(payload?.sections, locale);
  const current = entries.filter((entry) => entry.page === page);
  return {
    work: workContext(payload?.works, page, locale),
    current: current.slice(0, 5),
    currentTotal: current.length,
    previous: nearestGroup(entries, page, 'previous'),
    next: nearestGroup(entries, page, 'next'),
    resolvedEntries: entries.length,
  };
}
