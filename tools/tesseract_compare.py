#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main2


def read_image_paths(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        p = Path(value)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.png")))
        else:
            paths.append(p)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compara saída do Tesseract em imagens amarelas e não amarelas."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Arquivos PNG ou diretórios com PNGs.",
    )
    parser.add_argument(
        "--lang",
        default=main2.DEFAULT_LANG,
        help=f"Idioma do Tesseract (default: {main2.DEFAULT_LANG}).",
    )
    parser.add_argument(
        "--mode",
        choices=["adaptive_soft", "sample", "legacy"],
        default="adaptive_soft",
        help="Modo de pré-processamento a usar.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=6,
        help="Número máximo de imagens para processar.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Também executa o OCR direto sem pré-processamento.",
    )
    args = parser.parse_args()

    img_paths = read_image_paths(args.paths)[: args.limit]
    if not img_paths:
        print("Nenhuma imagem encontrada.", file=sys.stderr)
        sys.exit(1)

    for img_path in img_paths:
        print(f"\n=== {img_path} ===")
        try:
            txt = main2.ocr_tesseract(
                img_path,
                lang=args.lang,
                preprocess_mode=args.mode,
            )
            print(f"[{args.mode}]")
            print(txt.strip()[:2500] or "[vazio]")
        except Exception as e:
            print(f"[{args.mode}] ERRO: {e}")

        if args.raw:
            try:
                raw = main2.ocr_tesseract_raw(img_path, lang=args.lang)
                print("[raw]")
                print(raw.strip()[:2500] or "[vazio]")
            except Exception as e:
                print(f"[raw] ERRO: {e}")


if __name__ == "__main__":
    main()
