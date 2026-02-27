#!/usr/bin/env python3
import argparse
import sys
import os
import glob
from pathlib import Path
from typing import Iterable, List, Optional

from PIL import Image
try:
    # Kraken imports
    from kraken import binarization
    from kraken.lib import models
    from kraken import rpred
except Exception as e:
    print("[ERRO] Não foi possível importar Kraken. Certifique-se de que o pacote 'kraken' está instalado.", file=sys.stderr)
    raise

def iter_images(globs: List[str]) -> Iterable[Path]:
    exts = (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".PNG", ".TIF", ".TIFF", ".JPG", ".JPEG")
    for g in globs:
        paths = glob.glob(g, recursive=True)
        for p in sorted(paths):
            path = Path(p)
            if path.is_file() and path.suffix in exts:
                yield path

def default_out_for(img_path: Path, ocr_subdir: Optional[str]) -> Path:
    # ocr_out ao lado de 'images' (ex.: teste/<livro>/ocr_out/<nome>.txt)
    if ocr_subdir:
        book_dir = img_path.parent.parent  # .../<livro>/images -> .../<livro>
        out_dir = book_dir / ocr_subdir
    else:
        out_dir = img_path.parent  # mesma pasta da imagem
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / (img_path.stem + ".txt")

def ocr_image(net, img_path: Path, binarize: bool) -> List[str]:
    im = Image.open(img_path)
    if binarize:
        # nlbin: binarização robusta para material histórico
        im = binarization.nlbin(im)
    # rpred.rpred realiza segmentação + reconhecimento de linhas internamente
    preds = rpred.rpred(network=net, im=im)
    return [ln.prediction for ln in preds]

def main():
    ap = argparse.ArgumentParser(description="OCR em lote com Kraken (modelo carregado uma vez).")
    ap.add_argument("--model", "-m", required=True, help="Caminho para o modelo Kraken (.mlmodel)")
    ap.add_argument("--glob", "-g", action="append", default=["teste/*/images/*"], 
                    help="Glob de imagens (pode repetir). Default: teste/*/images/*")
    ap.add_argument("--ocr-subdir", default="ocr_out",
                    help="Subpasta ao lado de 'images' onde salvar .txt (default: ocr_out). Use vazio ('') para salvar na mesma pasta da imagem.")
    ap.add_argument("--no-binarize", action="store_true", help="Desativa binarização nlbin (por padrão está ativada).")
    ap.add_argument("--skip-existing", action="store_true", default=True,
                    help="Pula arquivos .txt que já existem (default: True).")
    ap.add_argument("--list-only", action="store_true", help="Só lista o que seria processado, sem OCR.")
    args = ap.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"[ERRO] Modelo não encontrado: {model_path}", file=sys.stderr)
        sys.exit(2)

    # Coletar imagens
    images = list(iter_images(args.glob))
    if not images:
        print("[AVISO] Nenhuma imagem encontrada para os padrões informados.", file=sys.stderr)
        sys.exit(0)

    # Carrega modelo UMA vez
    print(f"[INFO] Carregando modelo: {model_path}")
    net = models.load_any(str(model_path))
    net.eval()
    print(f"[OK] Modelo carregado.")

    total = 0
    skipped = 0
    done = 0
    errors = 0

    for img in images:
        out_txt = default_out_for(img, args.ocr_subdir if args.ocr_subdir != "" else None)
        if args.skip_existing and out_txt.exists() and out_txt.stat().st_size > 0:
            print(f"[SKIP] {out_txt} (já existe)")
            skipped += 1
            total += 1
            continue

        print(f"[OCR] {img} -> {out_txt}")
        if args.list_only:
            total += 1
            continue

        try:
            lines = ocr_image(net, img, binarize=not args.no_binarize)
            out_txt.parent.mkdir(parents=True, exist_ok=True)
            with open(out_txt, "w", encoding="utf-8") as f:
                for ln in lines:
                    f.write(ln + "\n")
            done += 1
        except Exception as e:
            print(f"[ERRO] Falha ao processar {img}: {e}", file=sys.stderr)
            errors += 1
        finally:
            total += 1

    print(f"[RESUMO] total={total}  feitos={done}  pulados={skipped}  erros={errors}")

if __name__ == "__main__":
    main()
