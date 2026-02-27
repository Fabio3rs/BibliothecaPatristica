#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kraken OCR pipeline (Latin/Greek) com segmentação configurável:
- Reconhecimento com modelo .mlmodel carregado uma única vez
- Segmentação: BLLA (baselines) ou legacy (bbox)
- Binarização opcional
- Cache: pula .txt existentes
- (Opcional) Fine-tuning com ketos (compile/train/test) – igual à versão anterior

Requer:
  pip install kraken pillow
  (e ketos se for usar fine-tuning)

Doc refs (API):
- rpred bounds, blla.segment, pageseg.segment.  Ver README/links na conversa.
"""

import argparse
import sys
import os
import glob
import json
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PIL import Image

# Kraken APIs
try:
    from kraken import binarization
    from kraken.lib import models
    from kraken import rpred
    from kraken import pageseg
    from kraken import blla
    from kraken.lib import vgsl
except Exception as e:
    print("[ERRO] Kraken não está instalado. Rode: pip install kraken", file=sys.stderr)
    raise

# ---------- Defaults ----------
DEFAULT_GLOB = "teste/*/images/*"
DEFAULT_OCR_SUBDIR = "ocr_out"

# ---------- Utils ----------
def run(cmd: List[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    print("[CMD]", " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=False, **kw)

def run_capture(cmd: List[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        print(f"[ERRO] comando falhou: {' '.join(cmd)}\n{e.output}", file=sys.stderr)
        raise

def iter_images(globs: List[str]) -> Iterable[Path]:
    exts = (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".PNG", ".TIF", ".TIFF", ".JPG", ".JPEG")
    for g in globs:
        for p in sorted(glob.glob(g, recursive=True)):
            path = Path(p)
            if path.is_file() and path.suffix in exts:
                yield path

def default_out_for(img_path: Path, ocr_subdir: Optional[str]) -> Path:
    # .../<livro>/images/<file>  ->  .../<livro>/<ocr_subdir>/<file>.txt
    if ocr_subdir is None:
        out_dir = img_path.parent
    else:
        out_dir = img_path.parent.parent / ocr_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / (img_path.stem + ".txt")

# ---------- OCR core ----------
def ocr_page_with_model(
    model_path: Path,
    images: List[Path],
    ocr_subdir: Optional[str],
    page_seg: str = "blla",              # "blla" | "legacy"
    seg_model_path: Optional[str] = None,  # necessário se page_seg="blla"
    binarize_flag: bool = True,
    skip_existing: bool = True,
) -> Tuple[int, int, int]:

    # Carrega recognizer (TorchSeqRecognizer)
    net = models.load_any(str(model_path))

    # Carrega segmentador conforme modo
    seg_model = None
    if page_seg == "blla":
        if not seg_model_path:
            raise SystemExit("[ERRO] --page-seg blla requer --seg-model /caminho/para/blla.mlmodel")
        seg_model = vgsl.TorchVGSLModel.load_model(seg_model_path)

    done = skipped = errors = 0

    for img in images:
        out_txt = default_out_for(img, ocr_subdir)
        if skip_existing and out_txt.exists() and out_txt.stat().st_size > 0:
            print(f"[SKIP] {out_txt}")
            skipped += 1
            continue

        print(f"[OCR] {img} -> {out_txt}")
        try:
            with Image.open(img) as im:
                # A/B: teste com/sem binarização conforme necessidade
                if binarize_flag:
                    im = binarization.nlbin(im)

                # Segmentação
                if page_seg == "blla":
                    bounds = blla.segment(im, model=seg_model)   # baselines
                else:
                    bounds = pageseg.segment(im)                 # bbox (legacy)

                # Reconhecimento: NOTE o uso de bounds= (não 'segmentation')
                preds = rpred.rpred(network=net, im=im, bounds=bounds)

                out_txt.parent.mkdir(parents=True, exist_ok=True)
                with open(out_txt, "w", encoding="utf-8") as f:
                    for rec in preds:
                        f.write(rec.prediction + "\n")

            done += 1

        except Exception as e:
            print(f"[ERRO] {img}: {e}", file=sys.stderr)
            errors += 1

    return done, skipped, errors

# ---------- (Opcional) Fine-tuning com ketos ----------
def compile_dataset(gt_dir: Path, dataset_bin: Path, fmt: str = "page") -> None:
    if dataset_bin.exists() and dataset_bin.stat().st_size > 0:
        print(f"[SKIP] dataset já existe: {dataset_bin}")
        return
    xmls = [str(p) for p in Path(gt_dir).rglob("*.xml")]
    if not xmls:
        raise FileNotFoundError(f"Nenhum XML encontrado em {gt_dir}. Exporte GT (PAGE/ALTO).")
    dataset_bin.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ketos", "compile", "-f", fmt, "--random-split", "0.8", "0.1", "0.1", *xmls, "-o", str(dataset_bin)]
    run(cmd)

def train_recognizer(dataset_bin: Path, out_prefix: Path, base_model: Optional[Path] = None,
                     device: Optional[str] = None, min_epochs: int = 10, lag: int = 5,
                     resize_add: bool = True, augment: bool = True) -> Path:
    best = Path(out_prefix).parent / (Path(out_prefix).name + "_best.mlmodel")
    if best.exists():
        print(f"[SKIP] modelo já existe: {best}")
        return best
    args = ["ketos", "train", "-f", "binary", str(dataset_bin), "-o", str(out_prefix)]
    if base_model:
        args += ["-i", str(base_model)]
        if resize_add:
            args += ["--resize", "add"]
    else:
        if resize_add:
            args += ["--resize", "add"]
    if augment:
        args += ["--augment"]
    args += ["--min-epochs", str(min_epochs), "--lag", str(lag)]
    if device:
        args += ["-d", device]
    run(args)
    if not best.exists():
        raise FileNotFoundError("Treino terminou mas *_best.mlmodel não encontrado.")
    return best

def test_recognizer(dataset_bin: Path, model_path: Path) -> None:
    try:
        run(["ketos", "test", "-f", "binary", "-m", str(model_path), str(dataset_bin)], check=False)
    except Exception as e:
        print(f"[AVISO] teste falhou: {e}", file=sys.stderr)

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Kraken OCR pipeline com segmentação BLLA/legacy e cache.")
    # OCR
    ap.add_argument("--glob", "-g", action="append", default=[DEFAULT_GLOB],
                    help=f"Globs de imagens (default: {DEFAULT_GLOB})")
    ap.add_argument("--ocr-subdir", default=DEFAULT_OCR_SUBDIR,
                    help="Subpasta ao lado de 'images' para .txt (default: ocr_out; use '' para salvar na mesma pasta)")
    ap.add_argument("--page-seg", choices=["blla", "legacy"], default="blla",
                    help="Segmentador de página: 'blla' (baselines) ou 'legacy' (bbox). Default: blla")
    ap.add_argument("--seg-model", type=str,
                    help="Caminho para o modelo de segmentação BLLA (.mlmodel). Obrigatório se --page-seg blla")
    ap.add_argument("--no-binarize", action="store_true",
                    help="Desativa binarização nlbin (ligada por padrão).")
    ap.add_argument("--skip-existing", action="store_true", default=True,
                    help="Pula .txt existentes (default True).")
    ap.add_argument("--model", required=True,
                    help="Caminho para o modelo de reconhecimento (.mlmodel). Recomenda-se um modelo treinado com o mesmo tipo de segmentação escolhido.")

    # Fine-tuning (opcional; igual versão anterior)
    ap.add_argument("--finetune", action="store_true",
                    help="Ativa fine-tuning com ketos (PAGE/ALTO).")
    ap.add_argument("--gt-dir", type=str, default="gt_page",
                    help="Diretório com PAGE/ALTO (default: gt_page).")
    ap.add_argument("--dataset-bin", type=str, default="datasets/latin_greek.arrow",
                    help="Dataset .arrow (default: datasets/latin_greek.arrow).")
    ap.add_argument("--out-prefix", type=str, default="models/latin_greek_ft",
                    help="Prefixo saída do modelo treinado (default: models/latin_greek_ft).")
    ap.add_argument("--base-model", type=str,
                    help="Modelo base para fine-tuning (se omitido, usa --model).")
    ap.add_argument("--device", type=str,
                    help="Ex.: 'cuda' ou 'cuda:0'")
    ap.add_argument("--fmt", choices=["page", "alto", "xml"], default="page",
                    help="Formato dos XMLs de GT (default: page).")

    args = ap.parse_args()

    ocr_subdir = None if args.ocr_subdir == "" else args.ocr_subdir
    model_path = Path(args.model).expanduser()
    if not model_path.exists():
        print(f"[ERRO] --model não encontrado: {model_path}", file=sys.stderr)
        sys.exit(2)

    # (Opcional) fine-tuning
    if args.finetune:
        gt_dir = Path(args.gt_dir)
        dataset_bin = Path(args.dataset_bin)
        out_prefix = Path(args.out_prefix)
        base = Path(args.base_model).expanduser() if args.base_model else model_path

        print("[FT] Compilando dataset (cache-aware) ...")
        compile_dataset(gt_dir, dataset_bin, fmt=args.fmt)

        print("[FT] Treinando recognizer (cache-aware) ...")
        best = train_recognizer(dataset_bin, out_prefix, base_model=base, device=args.device)
        print(f"[FT] Modelo treinado: {best}")
        print("[FT] Testando recognizer ...")
        test_recognizer(dataset_bin, best)
        model_path = best  # usa o best no OCR

    images = list(iter_images(args.glob))
    if not images:
        print("[AVISO] Nenhuma imagem encontrada para os padrões fornecidos.", file=sys.stderr)
        sys.exit(0)

    print(f"[INFO] Usando recognizer: {model_path}")
    if args.page_seg == "blla":
        if not args.seg_model:
            print("[ERRO] --page-seg blla requer --seg-model", file=sys.stderr)
            sys.exit(2)
        print(f"[INFO] Usando segmentador BLLA: {args.seg_model}")
    else:
        print("[INFO] Usando segmentação legacy (bbox).")

    print(f"[INFO] Iniciando OCR em {len(images)} imagens...")
    done, skipped, errors = ocr_page_with_model(
        model_path=model_path,
        images=images,
        ocr_subdir=ocr_subdir,
        page_seg=args.page_seg,
        seg_model_path=args.seg_model,
        binarize_flag=not args.no_binarize,
        skip_existing=args.skip_existing,
    )
    print(f"[RESUMO] feitos={done} pulados={skipped} erros={errors}")

if __name__ == "__main__":
    main()
