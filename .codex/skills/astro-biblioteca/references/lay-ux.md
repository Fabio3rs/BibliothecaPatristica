# Lay-User UX Reference

## Diagnosis: What the Current Site Gets Right and Wrong

### Gets right
- "Pontos de Partida" — curated entry points by author/theme
- Tag cloud with clickable keyword chips
- Bilingual (PT/EN)
- Source Serif 4 body font — communicates historical weight
- Earthy palette (`#f7f5f2` bg, `#1f1b16` text, `#1f6b7a` accent) — appropriate tone

### Gets wrong (for lay visitors)
- Hero subtitle describes the technology ("interface de pesquisa para as coleções da Patrologia Graeca, Latina e Orientalis de J.P. Migne") — a lay visitor does not know what any of those words mean
- Stats section shows `…` while loading — first impression is a broken site
- Search page opens with no guidance — "what should I search for?"
- Result titles are `"PG059 p.535 — Graça • Trindade • Batismo"` — the `PG059 p.535` part is meaningless to a lay user
- No author context in results — "João Crisóstomo" appears but without "Bispo de Constantinopla, séc. IV"
- No onboarding for "what is this material and why does it matter to my faith"

---

## Priority Changes by Implementation Cost

### ① Hero text rewrite — 20 minutes, zero code

Current: "Uma interface de pesquisa e exploração para as coleções da Patrologia Graeca (PG), Latina (PL) e Orientalis (PO) de J.P. Migne."

Suggested (i18n key `homeSubtitle` in `pt-br.ts`):

```
Os escritos dos primeiros cristãos — os Padres da Igreja — formaram a fé que
chegou até hoje. Este acervo reúne mais de 380 volumes com esses textos,
agora pesquisáveis e gratuitos para todos.
```

English version (key `homeSubtitle` in `en.ts`):
```
The writings of the early Christians — the Church Fathers — shaped the faith
that reaches us today. This archive brings together over 380 volumes of these
texts, now searchable and freely available to all.
```

### ② Result card human labels — 3–4 hours

Enrich `buildCard(hit)` in `SearchPage.astro` with author-level metadata.

The index already stores `meta.volume` (e.g. `"PG059"`) and `meta.collection` (`"PG"`).
The `volumes.json` already loaded for filters contains author/title metadata.

Pattern: build a `volumeMap: Map<string, VolumeInfo>` from the loaded volumes data,
then look up `hit.meta.volume` to get the human label.

```js
// In loadFilters(), after loading volumesObj:
const volumeMap = new Map(volumes.map(v => [v.id, v]));
window.__volumeMap = volumeMap; // make available to buildCard

// In buildCard(hit, term):
const vol = window.__volumeMap?.get(hit.meta?.volume);
const authorLabel = vol?.author_label ?? hit.meta?.volume ?? '';
const volTitle = vol?.title_short ?? '';
```

Result card (before/after):

```
BEFORE:
PG059 p.535 — Graça • Trindade • Batismo
[excerpt with highlight]

AFTER:
João Crisóstomo  •  séc. IV
Sobre o Sacerdócio — p. 535
[excerpt with highlight]
PG059  (small, muted)
```

For this to work, `volumes.json` needs to include `author_label`, `century`, and `title_short` per volume.
If those fields aren't in the data pipeline yet, add them in the pipeline that generates `volumes.json`.

### ③ First-visit onboarding overlay — 4–6 hours

New component `src/components/PatristicaIntro.astro`:

```astro
---
import { useTranslations } from '../i18n';
const t = useTranslations(Astro.currentLocale);
---
<div id="intro-overlay" role="dialog" aria-modal="true" aria-labelledby="intro-title"
     style="display:none; position:fixed; inset:0; z-index:100;
            background:color-mix(in srgb, var(--color-bg) 95%, transparent);
            backdrop-filter:blur(4px);
            display:flex; align-items:center; justify-content:center; padding:var(--space-6);">
  <div style="max-width:560px; background:var(--color-surface);
              border:1px solid var(--color-border); border-radius:var(--radius-m);
              padding:var(--space-6); box-shadow:var(--shadow-soft);">
    <h2 id="intro-title" style="margin-top:0; font-family:var(--font-serif);">{t.introTitle}</h2>
    <p set:html={t.introBody}></p>
    <div style="display:flex; gap:var(--space-3); flex-wrap:wrap; margin-top:var(--space-5);">
      <a class="button" href="..." id="intro-explore">{t.introCtaExplore}</a>
      <button class="button secondary" id="intro-dismiss">{t.introCtaDismiss}</button>
    </div>
  </div>
</div>
<script>
  const overlay = document.getElementById('intro-overlay');
  const seen = sessionStorage.getItem('patristica-intro-seen');
  // Show on first visit OR if arrived at /search directly from outside
  const fromExternal = !document.referrer.includes(window.location.hostname);
  if (!seen && (window.location.pathname.includes('/search') && fromExternal || !seen)) {
    overlay.style.display = 'flex';
  }
  document.getElementById('intro-dismiss')?.addEventListener('click', () => {
    sessionStorage.setItem('patristica-intro-seen', '1');
    overlay.style.display = 'none';
  });
</script>
```

i18n keys to add:
```ts
introTitle: 'O que é a Patrística?',
introBody: `Os <strong>Padres da Igreja</strong> são os teólogos e santos dos
  primeiros séculos do Cristianismo — de Inácio de Antioquia (séc. I)
  a João Damasceno (séc. VIII). Seus escritos formam a base da teologia cristã.
  <br><br>
  A <em>Patrologia</em> de J.P. Migne reúne mais de 380 volumes com esses textos.
  Este acervo os torna pesquisáveis e gratuitos.`,
introCtaExplore: 'Explorar o acervo',
introCtaDismiss: 'Entendi, ir para a busca',
```

### ④ Persona entry points on home — 3 hours

Replace the two-column "Autores / Temas" grid with three persona cards:

```
┌─────────────────────────┬──────────────────────────┬─────────────────────────┐
│  📖 Sou devoto          │  🎓 Estudo teologia       │  🔬 Sou pesquisador     │
│  Quero ler um santo     │  Preciso citar um Padre   │  Quero o texto original │
│  da Igreja              │  da Igreja                │  em latim ou grego      │
│                         │                           │                         │
│  → Agostinho            │  → Por tema               │  → Por volume PG/PL     │
│  → Crisóstomo           │  → Linha do tempo         │  → Busca avançada       │
│  → Jerônimo             │  → Doutor da Igreja       │  → Baixar acervo        │
└─────────────────────────┴──────────────────────────┴─────────────────────────┘
```

Implement as three `.card` elements in a 3-column `auto-fit minmax(220px, 1fr)` grid.

---

## Copy Guidelines

### Words to avoid → preferred alternatives

| Avoid | Use instead |
|---|---|
| corpus | acervo, conjunto de textos |
| Migne (first mention) | J.P. Migne (com breve contexto) |
| OCR | (never mention to lay users) |
| shard, index | (never mention) |
| Patrologia Graeca, PG | escritos dos Padres gregos |
| col. 535 | página 535 (or: coluna 535, with tooltip) |
| collection_id | coleção (PG / PL / PO, always spelled out first) |

### Tone for lay users
- First person plural: "Encontre os textos que formaram a fé"
- Active, present tense: "Agostinho escreve sobre..." not "Escrito por..."
- Avoid superlatives ("maior acervo") — let the material speak
- The material has 1,700 years of authority — don't oversell it

---

## Accessibility Notes

These complement the existing `a11y.css`:
- `<abbr title="Patrologia Graeca">PG</abbr>` on first use per page
- Result list: existing `aria-live="polite"` on `#results` is correct — keep it
- Overlay: trap focus inside dialog when open (`tabindex=-1` on overlay, focus first button)
- Keyword chips: `role="list"` + `role="listitem"` already exists on `.chips` — preserve in new components
