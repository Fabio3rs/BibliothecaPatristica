#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np
import re
import cv2
from PIL import Image
import pytesseract
from pdf2image import convert_from_path

# ============ CONFIGS PADRÃO ============
DEFAULT_DPI = 300  # 300dpi é o "doce" do Tesseract; subir só se necessário
DEFAULT_LANG = "lat"  # requer pacotes traineddata do Tesseract para latim
USE_PDFTOCAIRO = True  # geralmente mais estável / eficiente
IMAGE_FMT = "png"  # png ou jpeg (evitar PPM para não inflar memória)
MAX_THREADS_CONVERT = 12  # ajuste conforme seus núcleos

# Opcional: se precisar apontar para o executável do Tesseract explicitamente
# pytesseract.pytesseract.tesseract_cmd = r"/usr/bin/tesseract"

# Opcional: evitar oversubscription quando você já paraleliza por fora
# (Tesseract usa OpenMP; limitar as threads internas ajuda quando há várias páginas)
os.environ.setdefault("OMP_THREAD_LIMIT", "4")  # ajuste conforme seus núcleos


def preprocess_image(img_bgr: np.ndarray) -> np.ndarray:
    """Pré-processamento simples para OCR: grayscale + threshold."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    # threshold suave; ajuste 160~190 conforme qualidade do scan
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    # Opcional: denoise leve
    # thresh = cv2.medianBlur(thresh, 3)
    return thresh


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def list_existing_images(images_dir: Path) -> List[Path]:
    return sorted(images_dir.glob(f"*.{IMAGE_FMT}"))


NUM_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)


def page_number(p: Path) -> int | None:
    m = NUM_RE.search(p.name)
    if m:
        return int(m.group(1))
    # fallback: último bloco numérico antes da extensão
    m2 = re.search(r"(\d+)(?=\.[^.]+$)", p.name)
    return int(m2.group(1)) if m2 else None


def pages_to_images(
    pdf_path: Path,
    images_dir: Path,
    dpi: int = DEFAULT_DPI,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
) -> List[Path]:
    """
    Converte páginas do PDF em imagens no disco e retorna a lista de caminhos.
    Usa paths_only=True para não manter PIL Images em RAM.
    Ordena pelo código numérico no final do nome do arquivo.
    """
    ensure_dir(images_dir)

    def sort_key(p: Path):
        m = re.search(r"-(\d+)\.[^.]+$", p.name)
        return int(m.group(1)) if m else 0

    # se já existem imagens, reaproveita
    existing = list_existing_images(images_dir)
    if existing:
        return [p.resolve() for p in sorted(existing, key=sort_key)]

    # senão, converte agora
    paths = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        fmt=IMAGE_FMT,
        output_folder=str(images_dir),
        paths_only=True,  # retorna só caminhos (sem PIL em memória)
        use_pdftocairo=USE_PDFTOCAIRO,  # backend pdftocairo (bom em PDFs complexos)
        thread_count=MAX_THREADS_CONVERT,
        first_page=first_page,
        last_page=last_page,
    )
    # ordena pelo código numérico no final do nome
    path_objs = sorted((Path(p) for p in paths), key=sort_key)
    return [p.resolve() for p in path_objs]


def ocr_images_to_text(
    images: List[Path],
    txt_dir: Path,
    lang: str = DEFAULT_LANG,
    save_all_text_path: Optional[Path] = None,
) -> None:
    """
    Faz OCR página-a-página e salva um .txt por página em txt_dir.
    Opcionalmente concatena tudo em um único arquivo em save_all_text_path.
    """
    ensure_dir(txt_dir)
    all_text_chunks: List[str] = []

    for i, img_path in enumerate(images, start=1):
        print(f"[OCR] Página {i}/{len(images)}: {img_path.name}")

        # se já existe o txt desta página, reaproveita
        page_txt_path = txt_dir / (img_path.stem + ".txt")
        if page_txt_path.exists():
            txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")
            all_text_chunks.append(txt)
            continue

        # leitura streaming, garantindo liberação de memória
        with Image.open(img_path) as pil_im:
            # converter para cv2 BGR, pré-processar e voltar para PIL
            im_bgr = cv2.cvtColor(np.array(pil_im), cv2.COLOR_RGB2BGR)
            im_pre = preprocess_image(im_bgr)
            pil_pre = Image.fromarray(im_pre)

            # OCR
            txt = pytesseract.image_to_string(pil_pre, lang=lang)

        # salva o txt da página
        page_txt_path.write_text(txt, encoding="utf-8")
        all_text_chunks.append(txt)

    # arquivo único concatenado (opcional)
    if save_all_text_path:
        save_all_text_path.write_text("\n\n".join(all_text_chunks), encoding="utf-8")


import multiprocessing as mp
from functools import partial
import time


"""
Examples:  

 
setenv OMP_PLACES threads 
setenv OMP_PLACES "threads(4)" 
setenv OMP_PLACES "{0,1,2,3},{4,5,6,7},{8,9,10,11},{12,13,14,15}" 
setenv OMP_PLACES "{0:4},{4:4},{8:4},{12:4}" 
setenv OMP_PLACES "{0:4}:4:4"
"""
# 12 cores, 24 threads CPU, OMP_PLACES
places = [
    "{0,1}",
    "{2,3}",
    "{4,5}",
    "{6,7}",
    "{8,9}",
    "{10,11}",
    "{12,13}",
    "{14,15}",
    "{16,17}",
    "{18,19}",
    "{20,21}",
    "{22,23}",
]


def return_place_by_process_index(i: int) -> str:
    return places[i % len(places)]


def apply_to_env_by_process_index(i: int) -> None:
    os.environ["OMP_PLACES"] = return_place_by_process_index(i)
    print(f"Process {i} applying OMP_PLACES={os.environ['OMP_PLACES']}")


# ---- pool helpers ----
# Variável global para rastrear os processos inicializados
_process_counter_lock = mp.Lock()
_process_counter = mp.Value("i", 0)
_process_index_map = {}


def _init_omp_env(omp_threads: int = 2):
    """Inicializado no início de cada worker do Pool"""
    global _process_index_map

    # Obter PID atual
    current_pid = os.getpid()

    # Atribuir um índice sequencial para este processo
    with _process_counter_lock:
        if current_pid not in _process_index_map:
            process_index = _process_counter.value
            _process_index_map[current_pid] = process_index
            _process_counter.value += 1
        else:
            process_index = _process_index_map[current_pid]

    # Aplicar configurações de ambiente baseadas no índice do processo
    apply_to_env_by_process_index(process_index)

    # Configurações adicionais do OpenMP
    os.environ["OMP_THREAD_LIMIT"] = str(omp_threads)
    os.environ["OMP_NUM_THREADS"] = str(omp_threads)
    os.environ["OMP_DYNAMIC"] = "TRUE"
    os.environ.setdefault("OMP_PROC_BIND", "spread")

    print(f"Worker PID {current_pid} inicializado com índice {process_index}")
    print(f"OMP_PLACES={os.environ.get('OMP_PLACES', 'not set')}")
    print(f"OMP_THREAD_LIMIT={os.environ.get('OMP_THREAD_LIMIT')}")

    return process_index


def get_current_process_index() -> int:
    """Retorna o índice do processo atual baseado no PID"""
    current_pid = os.getpid()
    return _process_index_map.get(current_pid, -1)


def _ocr_one(img_path: Path, txt_dir: Path, lang: str) -> str:
    start_total = time.time()

    # Obter informações do processo atual
    current_pid = os.getpid()
    process_index = get_current_process_index()
    omp_places = os.environ.get("OMP_PLACES", "not set")

    print(
        f"[{time.strftime('%H:%M:%S')}] [START] {img_path.name} (PID: {current_pid}, Index: {process_index}, OMP_PLACES: {omp_places})"
    )

    page_txt_path = txt_dir / (img_path.stem + ".txt")
    if page_txt_path.exists():
        txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")

        if len(txt.strip()) == 0:
            print(f"[{time.strftime('%H:%M:%S')}] [WARN] {img_path.name} (txt vazio)")
        else:
            print(
                f"[{time.strftime('%H:%M:%S')}] [SKIP] {img_path.name} (txt já existe)"
            )
            return txt

    # leitura
    t0 = time.time()
    with Image.open(img_path) as pil_im:
        im_bgr = cv2.cvtColor(np.array(pil_im), cv2.COLOR_RGB2BGR)
    t1 = time.time()
    print(
        f"[{time.strftime('%H:%M:%S')}] {img_path.name} — leitura+conversão: {t1 - t0:.3f}s"
    )

    # pré-processamento
    t2 = time.time()
    im_pre = preprocess_image(im_bgr)
    t3 = time.time()
    print(
        f"[{time.strftime('%H:%M:%S')}] {img_path.name} — preprocessamento: {t3 - t2:.3f}s"
    )

    # OCR
    t4 = time.time()
    pil_pre = Image.fromarray(im_pre)
    txt = pytesseract.image_to_string(
        pil_pre, lang=lang
    )  # opcional: passa config se quiser
    t5 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] {img_path.name} — OCR: {t5 - t4:.3f}s")

    # salvar
    page_txt_path.write_text(txt, encoding="utf-8")

    total = time.time() - start_total
    print(
        f"[{time.strftime('%H:%M:%S')}] {img_path.name} — total: {total:.3f}s, text tamanho: {len(txt)}"
    )

    return txt


def ocr_images_to_text_parallel(
    images: List[Path],
    txt_dir: Path,
    lang: str = DEFAULT_LANG,
    processes: int = 4,
    omp_threads_per_proc: int = 2,
    chunksize: int = 2,
    maxtasksperchild: int = 1000,
    save_all_text_path: Optional[Path] = None,
) -> None:
    ensure_dir(txt_dir)

    # Reinicializar contadores globais para cada execução
    global _process_counter, _process_index_map
    with _process_counter_lock:
        _process_counter.value = 0
        _process_index_map.clear()

    ctx = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    with ctx.Pool(
        processes=processes,
        initializer=_init_omp_env,
        initargs=(omp_threads_per_proc,),
        maxtasksperchild=maxtasksperchild,
    ) as pool:
        worker = partial(_ocr_one, txt_dir=txt_dir, lang=lang)
        # imap_unordered tende a dar melhor throughput geral
        all_text_chunks = list(pool.imap_unordered(worker, images, chunksize=chunksize))

    if save_all_text_path:
        save_all_text_path.write_text("\n\n".join(all_text_chunks), encoding="utf-8")


def main():
    import argparse

    ap = argparse.ArgumentParser(description="PDF → imagens → OCR (paralelo com Pool).")
    ap.add_argument("pdf", type=str)
    ap.add_argument("--out", type=str, default="out")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--lang", type=str, default=DEFAULT_LANG)
    ap.add_argument("--first", type=int, default=None)
    ap.add_argument("--last", type=int, default=None)
    ap.add_argument("--concat", action="store_true")
    ap.add_argument("--procs", type=int, default=12, help="processos no Pool")
    ap.add_argument(
        "--omp-threads", type=int, default=2, help="threads OpenMP por processo"
    )
    ap.add_argument("--chunksize", type=int, default=2)
    ap.add_argument("--maxtasksperchild", type=int, default=1000)
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"PDF não encontrado: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    base_out = Path(args.out) / pdf_path.stem
    images_dir = base_out / "images"
    text_dir = base_out / "text"
    ensure_dir(base_out)

    print("Convertendo páginas para imagens (com cache em disco)...")
    images = pages_to_images(
        pdf_path, images_dir, dpi=args.dpi, first_page=args.first, last_page=args.last
    )
    print(f"Total de imagens: {len(images)}")

    concat_path = (base_out / "texto_extraido.txt") if args.concat else None
    print("OCR paralelo (Pool)...")
    ocr_images_to_text_parallel(
        images,
        text_dir,
        lang=args.lang,
        processes=args.procs,
        omp_threads_per_proc=args.omp_threads,
        chunksize=args.chunksize,
        maxtasksperchild=args.maxtasksperchild,
        save_all_text_path=concat_path,
    )
    print("Concluído.")


if __name__ == "__main__":
    main()
