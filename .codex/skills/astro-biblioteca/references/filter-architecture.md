# Filter Architecture Reference

## Design Rationale

The filter system separates concerns between two fetch budgets:

```
Structural filters → pagefind filter API
  • collection (PG / PL / PO) — 3 values, tiny chunk
  • volume (PG001…PL999) — ~400 values, moderate chunk
  • book (Apocalipse, Salmos…) — moderate, only on iscit pages

Keyword filter → text search (term in searchBox)
  • ~8,000 keyword labels, pagefind filter chunk would be ~7MB
  • Solution: clicking a keyword sets searchBox.value = keyword.label
  • Same search result quality, zero extra network cost
```

## State Machine

```ts
type SearchState = {
  term: string;       // searchBox.value — also absorbs keyword "filters"
  pageSize: number;   // from select, default 25
  page: number;       // current result page
  filters: {          // ONLY structural filters here
    collection?: string[];
    volume?: string[];
    // never: keyword
  };
};
```

State is the source of truth. URL is derived from state via `syncUrl()`.
URL is read into state via `readStateFromUrl()` on load and `popstate`.

## URL Serialization

```
/search?term=graça&collection=PG&collection=PL&page=2
```

- `term` → text search (includes keywords converted to text)
- `collection` / `volume` → structural pagefind filters (multi-value via `params.getAll`)
- `keyword` in URL → converted to `term` (backward compat from external links like the tag cloud)
- `pageSize` → omitted when 25 (default), present otherwise

## `toggleFilter(name, value)` — the dual-mode function

```js
function toggleFilter(name, value) {
  // Keywords bypass filters entirely — become text search
  if (name === 'keyword') {
    state.term = state.term === value ? '' : value;
    searchBox.value = state.term;
    state.page = 1;
    syncUrl();
    renderTimer = setTimeout(render, 50);
    return;
  }
  // Structural filters: toggle in array
  if (!state.filters[name]) state.filters[name] = [];
  const idx = state.filters[name].indexOf(value);
  if (idx === -1) {
    state.filters[name].push(value);
  } else {
    state.filters[name].splice(idx, 1);
    if (state.filters[name].length === 0) delete state.filters[name];
  }
  // Sync checkbox DOM state
  checkboxNodes.forEach(cb => {
    if (cb.name === name && cb.value === value) cb.checked = !cb.checked;
  });
  state.page = 1;
  syncUrl();
  renderTimer = setTimeout(render, 50); // 50ms for checkbox (already debounced by user action)
}
```

## Adding a New Structural Filter

### Step 1: Index it

In `build_pagefind_from_shards.mjs`, add to the `filters` field in `addCustomRecord`:
```js
filters: {
  collection: [vol.collection_id],
  volume: [vid],
  century: [vol.century ?? 'unknown'],  // ← new
}
```

### Step 2: Rebuild the pagefind index

```bash
node tools/build_pagefind_from_shards.mjs \
  --public web/public \
  --out web/public/pagefind \
  --base /BibliothecaPatristica
```

### Step 3: Add to URL reading

In `readStateFromUrl()`:
```js
['collection', 'volume', 'century'].forEach((key) => {  // add 'century'
  const values = params.getAll(key);
  if (values.length) filters[key] = values;
});
```

### Step 4: Add to filter panel rendering

In `loadFilters()`, add to the `sections` array:
```js
const centuries = Array.from(new Set(volumes.map(v => v.century).filter(Boolean))).sort();
const sections = [
  { key: 'collection', label: s.filterCollection, items: collections },
  { key: 'volume',     label: s.filterVolume,     items: volumeIds  },
  { key: 'century',    label: s.filterCentury,    items: centuries  }, // ← new
];
```

### Step 5: Add i18n strings

In `src/i18n/pt-br.ts` and `src/i18n/en.ts`:
```ts
filterCentury: 'Século',   // pt-br
filterCentury: 'Century',  // en
```

## Cardinality Guidelines

| Cardinality | Strategy |
|---|---|
| < 20 distinct values | Structural pagefind filter — checkbox list |
| 20–200 distinct values | Structural pagefind filter — consider searchable select or virtual scroll |
| 200+ distinct values | Text search — convert to term, never structural filter |
| Free-form tags (keywords) | Always text search |

## FiltersPanel DOM Lifecycle

`FiltersPanel.astro` renders a loading skeleton (`{loading ? ... : ...}`).
On page load, `loadFilters()` in SearchPage replaces the aside's `innerHTML` completely.

```
SSR render → aside with "Carregando filtros..."
     ↓
loadFilters() fetches volumes.json
     ↓
filtersRoot.innerHTML = `<h2>...</h2><div class="filters">...</div>`
     ↓
checkboxNodes = Array.from(filtersRoot.querySelectorAll('input[type="checkbox"]'))
     ↓ (attach listeners)
fetch dict/keywords.json (background, lazy)
     ↓ (fill keyword chips)
```

**Never** add elements to `FiltersPanel.astro` that need to survive the innerHTML replacement.
Add persistent elements to `SearchPage.astro` itself, outside the `<aside>`.
