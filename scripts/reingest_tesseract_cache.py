#!/usr/bin/env python3
"""Re-ingestão: importa resultados do ``tesseract_cache`` para ``ocr_versions.db``.

O ``tesseract.db`` (tabela ``tesseract_cache``) guarda resultados OCR do Tesseract
vinculados por ``image_hash``. Esses resultados intermediários são usados em fluxos
de reprocess/comparação, mas nunca foram versionados.

Este script cria uma versão ``engine="tesseract"`` para cada entrada do cache,
permitindo rastreabilidade completa e diffs entre Tesseract e LLM.

Uso:
    python scripts/reingest_tesseract_cache.py [--base-dir teste/] [--db data/ocr_versions.db] [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_versions_db as vdb


# ---------------------------------------------------------------------------
# Helpers de parsing (mesmos algoritmos de main2.py / backfill)
# ---------------------------------------------------------------------------


def parse_page_num_from_imgpath(imgpath: str) -> int | None:
    """Extrai página do campo ``imgpath`` do cache. Ex.: ``*-076.png`` → 76."""
    m = re.search(r"-(\d{1,4})\.\w+$", imgpath)
    return int(m.group(1)) if m else None


def infer_volume_from_imgpath(imgpath: str, base_dir: Path) -> str | None:
    """Infere volume a partir do path da imagem.

    ``teste/PL001/images/PL001-001.png`` → ``PL001``

    Tenta primeiro o diretório pai (``images/`` → volume), depois
    o prefixo do nome do arquivo (``PL001-001.png`` → ``PL001``).
    """
    p = Path(imgpath)

    # Convenção: .../VOLUME/images/FILE.png
    if p.parent.name == "images":
        return p.parent.parent.name

    # Fallback: prefixo antes do último "-NNN.ext"
    m = re.match(r"^(.+?)-\d{1,4}\.\w+$", p.name)
    if m:
        return m.group(1)

    return None


def resolve_image_path(imgpath: str, base_dir: Path) -> Path | None:
    """Tenta localizar o arquivo de imagem real no filesystem."""
    # Caminho absoluto direto
    p = Path(imgpath)
    if p.is_absolute() and p.exists():
        return p

    # Relativo ao base_dir
    rel = base_dir / imgpath
    if rel.exists():
        return rel

    # Tentar reconstruir: base_dir / VOLUME / images / FILE
    volume = infer_volume_from_imgpath(imgpath, base_dir)
    if volume:
        page_num = parse_page_num_from_imgpath(imgpath)
        if page_num is not None:
            # Nome estável
            candidate = base_dir / volume / "images" / f"{volume}-{page_num:03d}.png"
            if candidate.exists():
                return candidate
            # UUID no nome original
            candidate = base_dir / volume / "images" / Path(imgpath).name
            if candidate.exists():
                return candidate

    return None


# ---------------------------------------------------------------------------
# Re-ingestão principal
# ---------------------------------------------------------------------------


def reingest(
    tesseract_db_path: Path,
    versions_db_path: Path,
    base_dir: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    """Importa todas as entradas do ``tesseract_cache`` para ``ocr_versions.db``.

    Retorna estatísticas: ``{imported, skipped, no_volume, no_page, errors}``.
    """
    stats = {
        "imported": 0,
        "skipped": 0,
        "no_volume": 0,
        "no_page": 0,
        "errors": 0,
        "total": 0,
    }

    # Abrir tesseract cache (read-only)
    tess_con = sqlite3.connect(str(tesseract_db_path), timeout=10.0)
    tess_con.row_factory = sqlite3.Row
    rows = tess_con.execute(
        "SELECT image_hash, imgpath, lang, result, created_at FROM tesseract_cache"
    ).fetchall()
    stats["total"] = len(rows)

    if dry_run:
        print(f"[DRY-RUN] {len(rows)} entradas no tesseract_cache")
        # Contar quantas conseguimos mapear
        mapped = 0
        for r in rows:
            vol = infer_volume_from_imgpath(r["imgpath"], base_dir)
            pn = parse_page_num_from_imgpath(r["imgpath"])
            if vol and pn is not None:
                mapped += 1
        print(f"[DRY-RUN] {mapped}/{len(rows)} mapeáveis (volume + page_num)")
        tess_con.close()
        stats["imported"] = mapped
        return stats

    # Abrir/criar versions db
    ver_con = vdb.open_versions_db(versions_db_path)

    # Agrupar por lang para criar engine_versions de forma eficiente
    ev_cache: dict[str, int] = {}

    for i, r in enumerate(rows):
        try:
            volume = infer_volume_from_imgpath(r["imgpath"], base_dir)
            if not volume:
                stats["no_volume"] += 1
                continue

            page_num = parse_page_num_from_imgpath(r["imgpath"])
            if page_num is None:
                stats["no_page"] += 1
                continue

            lang = r["lang"]

            # Engine version por lang (cache local)
            if lang not in ev_cache:
                ev_cache[lang] = vdb.get_or_create_engine_version(
                    ver_con,
                    engine="tesseract",
                    model=lang,  # lang como "model" — identifica a combinação de traineddata
                    prompt_key="tesseract_direct",
                    system_prompt="",  # Tesseract não tem prompt
                )

            ev_id = ev_cache[lang]

            # Tentar encontrar a imagem para path real
            real_img = resolve_image_path(r["imgpath"], base_dir)
            if real_img is not None:
                image_path_str = str(real_img)
            else:
                image_path_str = r["imgpath"]

            result_id = vdb.record_ocr_result(
                ver_con,
                volume_id=volume,
                page_num=page_num,
                image_path=image_path_str,
                engine_version_id=ev_id,
                text_content=r["result"],
                reprocess_reason="legacy_unknown",
                image_hash=r["image_hash"],  # Já temos o hash, evita re-leitura
                meta_json=None,
            )
            stats["imported"] += 1

        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 10:
                print(f"  [ERROR] {r['imgpath']}: {e}")

        if (i + 1) % 5000 == 0:
            print(f"  [{i+1}/{len(rows)}] imported={stats['imported']} errors={stats['errors']}")

    ver_con.close()
    tess_con.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-ingere resultados do tesseract_cache para ocr_versions.db"
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("teste"),
        help="Diretório base com os volumes (default: teste/)",
    )
    parser.add_argument(
        "--tesseract-db",
        type=Path,
        default=Path("data/tesseract.db"),
        help="Caminho do tesseract cache (default: data/tesseract.db)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=vdb.DEFAULT_DB_PATH,
        help=f"Caminho do banco de versões (default: {vdb.DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas conta sem gravar no banco",
    )
    args = parser.parse_args()

    if not args.tesseract_db.exists():
        print(f"tesseract.db não encontrado: {args.tesseract_db}")
        sys.exit(1)

    print(f"Re-ingestão: {args.tesseract_db} → {args.db}")
    if args.dry_run:
        print("[DRY-RUN]\n")

    t0 = time.time()
    stats = reingest(args.tesseract_db, args.db, args.base_dir, args.dry_run)
    elapsed = time.time() - t0

    print(f"\n{'='*60}")
    print(f"Re-ingestão concluída em {elapsed:.1f}s")
    print(f"  Total no cache:  {stats['total']}")
    print(f"  Importados:      {stats['imported']}")
    print(f"  Skipped (dedup): {stats['skipped']}")
    print(f"  Sem volume:      {stats['no_volume']}")
    print(f"  Sem page_num:    {stats['no_page']}")
    print(f"  Erros:           {stats['errors']}")
    if args.dry_run:
        print("  (DRY-RUN — nada foi gravado)")


if __name__ == "__main__":
    main()
