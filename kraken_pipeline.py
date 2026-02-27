#!/usr/bin/env python3
import argparse
import os
import sys
import glob
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional

from PIL import Image

try:
    from kraken import binarization
    from kraken.lib import models
    from kraken import rpred
except Exception as e:
    print("[ERRO] Kraken não está instalado nesta env. Instale com `pip install kraken`.", file=sys.stderr)
    raise

DEFAULT_GLOB = "teste/*/images/*"
DEFAULT_MODEL_NAME = "default"
DEFAULT_MODEL_HOME = Path.home() / ".kraken" / "models"

def iter_images(globs: List[str]) -> Iterable[Path]:
    exts = (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".PNG", ".TIF", ".TIFF", ".JPG", ".JPEG")
    for g in globs:
        for p in sorted(glob.glob(g, recursive=True)):
            path = Path(p)
            if path.is_file() and path.suffix in exts:
                yield path

def default_out_for(img_path: Path, ocr_subdir: Optional[str]) -> Path:
    # Escreve ao lado de 'images' como <livro>/ocr_out/<base>.txt
    if ocr_subdir is None:
        out_dir = img_path.parent
    else:
        out_dir = img_path.parent.parent / ocr_subdir  # .../<livro>/ocr_out
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / (img_path.stem + ".txt")

def ensure_model(model_path: Optional[str]) -> Path:
    """Resolve o caminho do modelo:
    1. Se foi passado --model e existe, usa.
    2. Se não foi passado, tenta usar ~/.kraken/models/default.mlmodel
    3. Se não existir, tenta baixar via `kraken get default`.
    """
    if model_path:
        mp = Path(model_path).expanduser()
        if mp.exists():
            return mp
        print(f"[AVISO] Modelo não encontrado em {mp}. Vou tentar localizar um modelo padrão.", file=sys.stderr)

    # Tenta default em ~/.kraken/models/default.mlmodel
    default_mp = DEFAULT_MODEL_HOME / f"{DEFAULT_MODEL_NAME}.mlmodel"
    if default_mp.exists():
        return default_mp

    # Tenta baixar com `kraken get default`
    print("[INFO] Baixando modelo padrão com `kraken get default`...", file=sys.stderr)
    try:
        subprocess.run(["kraken", "get", DEFAULT_MODEL_NAME], check=True)
    except Exception as e:
        print(f"[ERRO] Falha ao baixar modelo padrão: {e}", file=sys.stderr)
        raise

    if default_mp.exists():
        return default_mp

    raise FileNotFoundError("Não foi possível localizar ou baixar um modelo Kraken (.mlmodel).")

def ocr_image(net, img_path: Path, binarize_flag: bool) -> List[str]:
    im = Image.open(img_path)
    if binarize_flag:
        im = binarization.nlbin(im)
    preds = rpred.rpred(network=net, im=im)
    return [ln.prediction for ln in preds]

def main():
    ap = argparse.ArgumentParser(description="Pipeline Kraken: OCR em lote com modelo carregado uma única vez, sem segmentação externa.")
    ap.add_argument("--glob", "-g", action="append", default=[DEFAULT_GLOB],
                    help=f"Globs de imagens (pode repetir). Default: {DEFAULT_GLOB!r}")
    ap.add_argument("--model", "-m", default=None,
                    help="Caminho para um modelo .mlmodel. Se omitido, tentará ~/.kraken/models/default.mlmodel; se não houver, irá baixar `kraken get default`.")
    ap.add_argument("--ocr-subdir", default="ocr_out",
                    help="Subpasta ao lado de 'images' onde salvar .txt (default: ocr_out). Use vazio ('') para salvar na mesma pasta da imagem.")
    ap.add_argument("--no-binarize", action="store_true",
                    help="Desativa binarização nlbin (ativada por padrão).")
    ap.add_argument("--skip-existing", action="store_true", default=True,
                    help="Pula .txt que já existem (default True)." )
    ap.add_argument("--list-only", action="store_true",
                    help="Só lista o que faria, sem executar OCR.")
    args = ap.parse_args()

    ocr_subdir = None if args.ocr_subdir == "" else args.ocr_subdir

    try:
        model_path = ensure_model(args.model)
        print(f"[INFO] Usando modelo: {model_path}")
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    images = list(iter_images(args.glob))
    if not images:
        print("[AVISO] Nenhuma imagem encontrada para os padrões fornecidos.", file=sys.stderr)
        sys.exit(0)

    print("[INFO] Carregando modelo em memória...")
    net = models.load_any(str(model_path))
    net.eval()
    print("[OK] Modelo carregado. Iniciando OCR.")

    total = 0
    done = 0
    skipped = 0
    errors = 0

    for img in images:
        out_txt = default_out_for(img, ocr_subdir)
        if args.skip_existing and out_txt.exists() and out_txt.stat().st_size > 0:
            print(f"[SKIP] {out_txt}")
            skipped += 1
            total += 1
            continue

        print(f"[OCR] {img} -> {out_txt}")
        if args.list_only:
            total += 1
            continue

        try:
            lines = ocr_image(net, img, binarize_flag=not args.no_binarize)
            out_txt.parent.mkdir(parents=True, exist_ok=True)
            with open(out_txt, "w", encoding="utf-8") as f:
                for ln in lines:
                    f.write(ln + "\n")
            done += 1
        except Exception as e:
            print(f"[ERRO] {img}: {e}", file=sys.stderr)
            errors += 1
        finally:
            total += 1

    print(f"[RESUMO] total={total} feitos={done} pulados={skipped} erros={errors}")

if __name__ == "__main__":
    main()
