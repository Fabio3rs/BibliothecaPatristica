#!/usr/bin/env python3
"""Benchmark rápido para funções de limpeza/verify do `keywords_serial.py`.

Usage:
  python tools/bench_keywords.py --input dumptrecho.log --scale 10000

O script:
- Lê `/homessddata/Projects/pdfocr/dumptrecho.log` (pipe-delimited dump gerado pelo sqlite).
- Extrai strings candidatas às keywords (várias colunas) e constrói uma lista.
- Expande (repete/varia) para atingir `--scale` itens.
- Mede tempo para map/reduce das funções alvo e imprime resumo.

Designed for quick local profiling; não altera DB.
"""

from __future__ import annotations
import argparse
import csv
import random
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DUMP_PATH = PROJECT_ROOT / "dumptrecho.log"


def load_from_dump(path: Path, max_lines: int | None = None) -> list[str]:
    items = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            parts = line.strip().split("|")
            # pega algumas colunas candidatas (keyword_norm, title_case, original)
            if len(parts) >= 2 and parts[1].strip():
                items.append(parts[1].strip())
            if len(parts) >= 3 and parts[2].strip():
                items.append(parts[2].strip())
            if len(parts) >= 4 and parts[3].strip():
                items.append(parts[3].strip())
    # dedupe preserving order
    seen = set()
    out = []
    for it in items:
        k = it.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def expand_items(seed: list[str], target: int) -> list[str]:
    if not seed:
        return []
    out = []
    while len(out) < target:
        s = random.choice(seed)
        # introduce minor variation to avoid exact duplicates
        if random.random() < 0.2:
            s2 = s + " " + str(random.randint(1, 999))
        else:
            s2 = s
        out.append(s2)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default=str(DUMP_PATH))
    p.add_argument("--scale", type=int, default=10000, help="Número alvo de keywords para testar")
    p.add_argument("--seed_lines", type=int, default=None)
    args = p.parse_args()

    seed = load_from_dump(Path(args.input), max_lines=args.seed_lines)
    print(f"Seed unique keywords loaded: {len(seed)}")
    items = expand_items(seed, args.scale)
    print(f"Total items for benchmark: {len(items)}")

    # Import alvo (importa as funções do projeto)
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from keywords_serial import (
        _deep_clean_keyword,
        _clean_list,
        _dedupe_preserve_order,
        _dedupe_after_citation_normalization,
        extract_citations,
    )

    # Warmup
    print("Warmup...")
    for _ in range(2):
        _deep_clean_keyword(items[0])
        _clean_list(items[:100])

    results = []

    t0 = time.perf_counter()
    # deep clean map
    t = time.perf_counter()
    cleaned_map = [_deep_clean_keyword(x) for x in items]
    dt = time.perf_counter() - t
    results.append(("_deep_clean_keyword", dt))
    print(f"_deep_clean_keyword: {dt:.3f}s total; {dt/len(items):.6f}s per item")

    # _clean_list on batches
    t = time.perf_counter()
    cleaned_list, issues = _clean_list(items)
    dt = time.perf_counter() - t
    results.append(("_clean_list", dt))
    print(f"_clean_list: {dt:.3f}s total; cleaned={len(cleaned_list)} issues={issues}")

    # dedupe_preserve_order
    dup_source = items + items[: len(items)//3 ]
    t = time.perf_counter()
    deduped = _dedupe_preserve_order(dup_source)
    dt = time.perf_counter() - t
    results.append(("_dedupe_preserve_order", dt))
    print(f"_dedupe_preserve_order: {dt:.3f}s total; input={len(dup_source)} output={len(deduped)}")

    # _dedupe_after_citation_normalization (small wrapper)
    t = time.perf_counter()
    dedup2, changed = _dedupe_after_citation_normalization(items)
    dt = time.perf_counter() - t
    results.append(("_dedupe_after_citation_normalization", dt))
    print(f"_dedupe_after_citation_normalization: {dt:.3f}s total; changed={changed}")

    # extract_citations (heavier) — assemble payload
    payload = {"keywords": items[:1000], "categorias": {"obras_citadas": items[:500]}}
    t = time.perf_counter()
    cleaned_payload, issues = extract_citations(payload)
    dt = time.perf_counter() - t
    results.append(("extract_citations(1k)", dt))
    print(f"extract_citations on 1k keywords: {dt:.3f}s issues={issues}")

    t_total = time.perf_counter() - t0
    print("\nSummary:")
    for name, dt in results:
        print(f" - {name:40s} {dt:.3f}s")
    print(f"Total run time: {t_total:.3f}s")


if __name__ == "__main__":
    main()
