# PageFind Reference

## Architecture Overview

The project uses the **PageFind raw JS API** (`pagefind.js`), not the `PagefindUI` widget.
This gives full control over search calls, filter state, and rendering.

Index is built offline via `build_pagefind_from_shards.mjs` using `createIndex` + `addCustomRecord`.

---

## `ensurePagefind()` — The Existing Singleton

From `SearchPage.astro` (do not re-implement, just understand):

```js
let pagefindInstance = null;
let pagefindPromise = null;

async function ensurePagefind() {
  if (pagefindInstance) return pagefindInstance;
  if (pagefindPromise) return pagefindPromise;
  pagefindPromise = (async () => {
    try {
      const mod = await import(`${assetBase}pagefind/pagefind.js`);
      // Handles multiple export shapes pagefind uses across versions
      if (mod && typeof mod.search === 'function') return mod;
      const pfModule = mod?.default || mod?.pagefind || mod;
      if (pfModule && typeof pfModule.search === 'function') return pfModule;
      if (pfModule && typeof pfModule.init === 'function') {
        const pf = await pfModule.init({ basePath: `${assetBase}pagefind/` });
        if (pf && typeof pf.search === 'function') return pf;
      }
      throw new Error('Bundle Pagefind sem search/init utilizável');
    } catch (err) {
      console.error(err);
      resultsEl.textContent = s.searchIndexUnavailable;
      return null;
    }
  })();
  pagefindInstance = await pagefindPromise;
  return pagefindInstance;
}
```

**Why the multiple export shape checks:** PageFind's JS API changed export shape between versions. The three-way check (`mod.search`, `mod.default.search`, `mod.init`) is defensive against version upgrades.

---

## `debouncedSearch` — The Right API

```js
// Correct: debouncing built into pagefind, 300ms delay
const searchResult = await pf.debouncedSearch(
  searchTerm,                                    // null = no text filter
  { filters: Object.keys(filters).length ? filters : undefined },
  300                                            // debounce ms
);

// searchResult === null means a newer call was enqueued — abandon silently
if (searchResult === null) return;
```

**Anti-cancel pattern for empty state:**
```js
// When noCriteria (no term, no filters) AND pagefind already initialized:
// debouncedSearch(null, {}) would scan all 287k records and lock the thread.
// Solution: call with '' (empty string) to increment pagefind's internal searchID
// which causes the pending call to be discarded.
if (noCriteria && pagefindInstance) {
  pagefindInstance.debouncedSearch('', {}, 0);
}
```

---

## Lazy `.data()` Pattern

PageFind results are lazy objects. Calling `.data()` triggers a network fetch for that result's chunk:

```js
// Correct: slice first, then resolve only the visible page
const hits = await Promise.all(
  allResults.slice(start, end).map((r) => r.data())
);
// With pageSize=25, this is 25 parallel fetches of small chunks — acceptable
// With pageSize=100+, consider sequential batching to avoid browser saturation
```

**Hit shape returned by `.data()`:**
```ts
{
  url: string,        // e.g. "/BibliothecaPatristica/viewer?doc=PG059&page=535"
  meta: {
    title: string,    // enriched: "PG059 p.535 — Graça • Trindade • Batismo"
    volume: string,   // "PG059"
    page: string,     // "535"
    collection: string, // "PG"
    books: string[],  // ["Apocalipse", "Salmos"] — iscit keywords
  },
  excerpt: string,    // pagefind-generated excerpt with <mark> highlights
  filters: { collection: string[], volume: string[] },
  // ...
}
```

Note: `excerpt` already contains `<mark>` tags. Render with `innerHTML`, not `textContent`.

---

## Building the Index

### `build_pagefind_from_shards.mjs` flow

```
public/volumes.json
  └─ volumes[]: { id, collection_id, meta_url, page_first }
      └─ public/{vol.meta_url} → { page_blocks: [{ file }] }
          └─ public/{pb.file} → { pages: [{ page, summary_page, keyword_ids }] }
              └─ public/dict/keywords.json → { items: [{ id, label, iscit, count }] }
```

Each page becomes one `addCustomRecord` call:
```js
{
  url: `${base}/viewer?doc=${vid}&page=${p.page}`,
  content: `${p.summary_page} ${kwLabels.join(' ')} ${bookNames.join(' ')}`,
  meta: {
    title: `${vid} p.${p.page} — ${topKeywords.join(' • ')}`,
    volume: vid,
    page: String(p.page),
    collection: vol.collection_id,
    books: bookNames,
  },
  filters: {
    collection: [vol.collection_id],
    volume: [vid],
    ...(bookNames.length ? { book: bookNames } : {}),
  },
  language: 'pt',
}
```

### Adding a new field to the index

1. Add to `meta` object in `build_pagefind_from_shards.mjs` — meta fields are searchable text
2. Add to `filters` object if you want structured filter support (check cardinality first)
3. Rebuild: `node tools/build_pagefind_from_shards.mjs --public web/public --out web/public/pagefind --base /BibliothecaPatristica`
4. The `--concurrency` flag defaults to `min(4, cpu_count)` — increase for faster machines

### `pagefind.yml` — current settings
```yaml
chunk_size: 10000     # large chunks = fewer files, smaller total count vs. GH Pages limits
exclude_selectors:
  - "nav"
  - "footer"
  - ".sidebar"
```
The `chunk_size: 10000` (vs. default ~400) reduces index file count significantly.
This matters because GitHub Pages has limits on repository file count and total size.

---

## Performance Diagnostics

Open DevTools → Network → filter `pagefind`:

| What you see | Cause | Fix |
|---|---|---|
| `pagefind-entry.json` on every filter click | Container unmounted (not this codebase — uses singleton) | N/A |
| `*.pf_index` re-fetching (not from cache) | Missing `Cache-Control` on CDN | Set `immutable` headers on pagefind host |
| `*.pf_filter` large file on first filter | Filter chunk for a high-cardinality filter | Move to text search (see keyword pattern) |
| `r.data()` storms on pagination | pageSize too large | Cap pageSize or batch `.data()` calls |

### GitHub Pages cache header limitation
GH Pages cannot serve custom `Cache-Control` headers. Pagefind's index chunks will always revalidate (304 responses) rather than serving from disk cache with `immutable`. To fix this:
- Host `pagefind/` on Cloudflare R2 or Cloudflare Pages (separate static site)
- Set `Cache-Control: public, max-age=31536000, immutable` on the CDN
- Point the import URL in `ensurePagefind()` to the CDN base URL

---

## Pre-warming

The home page already pre-warms pagefind.js:
```astro
<link rel="prefetch" href={`${baseWithSlash}pagefind/pagefind.js`} />
```
This starts downloading the main JS bundle before the user navigates to `/search`. The index chunks are not pre-fetched (too many files); they load on first search.
