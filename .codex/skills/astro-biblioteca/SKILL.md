---
name: astro-biblioteca
description: >
  Design, build, and evolve the BibliothecaPatristica Astro site (and similar static corpus sites).
  Trigger this skill whenever the user asks about: Astro components, PageFind search, JSON shard
  fetching, dataCache patterns, filter performance, keyword chips, lay-user UX, i18n routing,
  GitHub Pages deployment, build_pagefind_from_shards, tokens.css design changes, or any
  feature for the pdfocr-site / BibliothecaPatristica codebase.
  Also trigger for: pagefind engasgo/lag, chunk download optimization, filter state in URL,
  making patristic content accessible to non-specialist users, or adding new pages/components
  in this project's established patterns.
---

# Astro Biblioteca Skill

This skill is grounded in the **actual codebase** of `pdfocr-site` / BibliothecaPatristica.
Always reason from what exists — don't suggest patterns the code already implements.

## Project Reality Snapshot

| Layer | What exists |
|---|---|
| Framework | Astro 5.x, `output: 'static'`, `base: '/BibliothecaPatristica'` |
| i18n | Astro native i18n, `defaultLocale: 'pt-br'`, `prefixDefaultLocale: false`, `/en/` prefix for English |
| Styles | CSS custom properties in `tokens.css`; `Source Serif 4` (serif) + `Inter` (sans); earthy palette |
| Data layer | `utils/dataCache.ts` — promise-memoizing fetch cache (see §3) |
| Search | PageFind raw API (`pagefind.js`), **not** PagefindUI widget |
| Index build | `build_pagefind_from_shards.mjs` — `createIndex` + `addCustomRecord` per page |
| Filters | `collection` + `volume` as structural PageFind filters; keywords as **text search** to avoid 7MB chunk |
| PageFind init | `ensurePagefind()` singleton in `SearchPage.astro` inline `<script>` |
| trailingSlash | `'never'` — internal links must NOT have trailing slash |

---

## 1. Core Conventions

### BASE_URL pattern
```astro
const base = import.meta.env.BASE_URL || '/';
const baseWithSlash = base.endsWith('/') ? base : `${base}/`;
// Use baseWithSlash for hrefs; basePath (no trailing slash) for pagefind import path
const basePath = base.endsWith('/') ? base.slice(0, -1) : base;
```

### i18n in components
```astro
import { useTranslations } from '../i18n';
const t = useTranslations(Astro.currentLocale);
```
Translations live in `src/i18n/pt-br.ts` and `src/i18n/en.ts`.
To add a string: add key to both files, then use `t.yourKey` in templates.

### Passing data to inline scripts
```astro
<script type="module" define:vars={{ baseUrl: baseWithSlash, uiStrings, uiLocale }}>
  // define:vars serializes scalars/plain objects; functions are NOT serializable
  // Reconstruct closures inside the script block
  const isEn = uiLocale === 'en';
  const paginationShowing = isEn
    ? (s, e, t) => `Showing ${s}–${e} of ${t}`
    : (s, e, t) => `Mostrando ${s}–${e} de ${t}`;
</script>
```

### CSS Tokens (from `tokens.css`)
```css
/* Always reference tokens, never hardcode colors */
var(--color-bg)           /* #f7f5f2 light / #0f1114 dark */
var(--color-surface)      /* card backgrounds */
var(--color-surface-2)    /* slightly recessed surfaces */
var(--color-ink)          /* body text */
var(--color-muted)        /* secondary text */
var(--color-accent)       /* #1f6b7a light / #5fb0c2 dark */
var(--color-border)
var(--font-serif)         /* 'Source Serif 4', Georgia */
var(--font-sans)          /* 'Inter', Helvetica Neue */
var(--space-2..8)         /* 0.5rem → 2rem */
var(--radius-s)  var(--radius-m)
var(--shadow-soft)
```

---

## 2. PageFind Architecture

Read `references/pagefind.md` for full details.

### What already works — DO NOT re-implement

- **`ensurePagefind()` singleton** in `SearchPage.astro` — already prevents re-init
- **`debouncedSearch(term, {filters}, 300)`** — already debounced at 300ms for filters, 0ms anti-cancel pattern for empty state
- **Keywords as text search** — intentional; avoids the 7MB keyword filter chunk

### Where the engasgo actually comes from

The code already avoids the main cause (re-init). Remaining causes:
1. **`r.data()` calls on large result sets** — `.data()` fetches the index chunk for that result lazily; calling `Promise.all(allResults.map(r => r.data()))` on a full page of 25 results is fine, but if `pageSize` is 100, it's 100 parallel fetches
2. **Switching between filter combinations** causes different index chunks to be needed — this is structural to pagefind's design; mitigate with browser cache headers on `pagefind/*`
3. **GitHub Pages cannot set `Cache-Control: immutable`** on pagefind assets — each revisit revalidates chunks

### Index build (`build_pagefind_from_shards.mjs`)

- Reads `public/volumes.json` → `public/{vol.meta_url}` → page blocks
- Enriches title: `"PG059 p.535 — Graça • Trindade • Batismo"`
- Content = `summary_page + kwLabels + bookNames` (cleaned)
- Filters indexed: `collection`, `volume`, `book` (when `iscit` keywords present)
- Language: `'pt'` — important for pagefind's Portuguese stemmer

---

## 3. Data Cache (`utils/dataCache.ts`)

**Existing implementation** — promise-memoizing, retry-on-error:
```ts
const cache = new Map<string, Promise<unknown>>();

export function getJsonOnce<T>(url: string): Promise<T> {
  if (!cache.has(url)) {
    const p = fetch(url).then(async (res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json() as Promise<T>;
    }).catch((err) => {
      cache.delete(url);  // allows retry on failure
      throw err;
    });
    cache.set(url, p);
  }
  return cache.get(url) as Promise<T>;
}
```

**Key property:** caches the in-flight *promise*, not the resolved value. This means concurrent callers for the same URL share one network request. Always use `getJsonOnce` for JSON fetches — never raw `fetch()` for data that might be requested twice.

**When to extend:** if you need shard versioning (bust cache on deploy), clear with `clearJsonCache()` after comparing `manifest.version` against `sessionStorage`.

---

## 4. Filter Architecture

See `references/filter-architecture.md` for implementation details.

### Current design rationale

```
Structural filters (pagefind): collection, volume
  → Small filter chunks (~KB each)
  → Indexed with addCustomRecord filters field
  → Rendered as checkboxes in FiltersPanel

Keyword filter (text search):
  → keywords.json is ~2–3MB; its pagefind chunk would be ~7MB
  → Solution: keywords toggle searchBox.value instead of state.filters
  → Result: same UX, no extra pagefind chunk downloaded
```

### Extending filters

To add a new structural filter (e.g., `century`):
1. Add to `addCustomRecord` in `build_pagefind_from_shards.mjs`: `filters: { ..., century: [vol.century] }`
2. Rebuild pagefind index
3. Add to `sections` array in `loadFilters()` in `SearchPage.astro`
4. Add `'century'` to the `['collection', 'volume', 'century']` list in `readStateFromUrl()`

**Do NOT** add filters with high cardinality (hundreds of distinct values) as structural pagefind filters — they produce large chunks. Use text search instead.

---

## 5. Component Patterns

### New Astro component checklist
```astro
---
import { useTranslations } from '../i18n';
// Props interface always first
interface Props { label: string; items?: string[] }
const { label, items = [] } = Astro.props;
const t = useTranslations(Astro.currentLocale);
// base only if component has links/hrefs
const base = import.meta.env.BASE_URL || '/';
const baseWithSlash = base.endsWith('/') ? base : `${base}/`;
---
<!-- Style scoped to component -->
<style>
  /* Use tokens only */
</style>
<!-- Markup using t.* for all user-visible strings -->
```

### Existing components and their roles

- `Layout.astro` — shell: topbar, skip link, lang switch, footer, disclaimer
- `FiltersPanel.astro` — SSR skeleton; client JS in SearchPage replaces innerHTML after data load
- `ResultCard.astro` — title + snippet + badges (chips); receives pre-processed hit data
- `PageMetadata.astro` — viewer sidebar: summary, excerpt, keywords/entities/segments chips
- `RawToggle.astro` — link to raw OCR text, with fallback message
- `Breadcrumbs.astro` — `{label, href?}[]` → `<nav aria-label="Breadcrumb">`

### Adding a new page

1. Create `src/pages/new-page.astro` AND `src/pages/en/new-page.astro`
2. Both import from `src/components/pages/NewPage.astro`
3. Add i18n keys for the page to both `pt-br.ts` and `en.ts`
4. Add nav link in `Layout.astro` if it should be in the topbar

---

## 6. Lay-User UX

See `references/lay-ux.md` for copy, structure, and visual patterns.

### Current gap analysis (from real site visit)

The landing page already has "Pontos de Partida" but still leads with the description rather than significance. The hero says what it is, not why it matters. A first-time lay visitor (Catholic, no academic background) does not know what "Patrologia Graeca" means.

### Highest-impact changes (implementation cost vs. lay engagement)

1. **Hero headline rewrite** (30 min, no code): change from description to mission statement
2. **Inline author context** in search results: "João Crisóstomo (séc. IV) — Bispo de Constantinopla" before the volume reference
3. **First-visit overlay** (2–3h): one-time modal explaining what the Patrologia is; stored in `sessionStorage`
4. **Persona entry points** on home: 3 cards "Sou devoto / Estudo teologia / Sou pesquisador"

---

## 7. Known Patterns & Gotchas

### `trailingSlash: 'never'` + GitHub Pages
GH Pages serves `search/index.html` but the URL `/search/` (with slash) — this combination requires the `.nojekyll` file and careful link construction. Internal links: always `${baseWithSlash}search` (with slash from baseWithSlash, no trailing slash from the page name).

### `normalizeUrl(raw)` in SearchPage
Pagefind returns URLs that may double-include the base path. The existing `normalizeUrl()` strips repeated base prefixes. If you change `base` in `astro.config.mjs`, the logic still works because it reads from `import.meta.env.BASE_URL`. Don't hardcode `/BibliothecaPatristica/` anywhere.

### Inline scripts and Astro's `define:vars`
`define:vars` does JSON-serialization. Functions, Dates, Maps, Sets are lost. Always re-create locale-aware functions inside the script block (see §1).

### FiltersPanel double-render
`FiltersPanel.astro` renders a loading skeleton on the server. The client-side `loadFilters()` in SearchPage replaces `filtersRoot.innerHTML` entirely. Don't add persistent DOM elements inside `FiltersPanel.astro` that you need to survive — they'll be wiped. Attach event listeners only after the `innerHTML` replacement.

### `pagefind.yml` chunk_size
Currently `10000` (vs. default ~400). This produces fewer, larger chunk files. Good for total file count (GH Pages has limits); means each chunk download is larger but there are fewer of them. Don't reduce this without measuring index file count.
