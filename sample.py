#!/usr/bin/env python3
"""
sample.py — amostragem uniforme por volume + segmentação Tesseract → SQLite

Uso:
    python sample.py --root ./teste --db ocr.db --session "amostra inicial" --per-volume 10
    python sample.py --root ./teste --db ocr.db --resample   # força nova amostragem

Estrutura esperada:
    {root}/P{G|L}{vol}/images/*-{page}.png
    {root}/P{G|L}{vol}/text/*-{page}.txt
"""

import argparse
import io
import json
import os
import random
import re
import sqlite3
import sys
from pathlib import Path
import multiprocessing as mp

from PIL import Image
import pytesseract

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SUPPORTED_PREFIXES = ("PG", "PL")   # PO fica para fase 2
TESS_LANG = "lat+grc"
MIN_LINES = 10
DEFAULT_PER_VOLUME = 10             # páginas por volume

# ---------------------------------------------------------------------------
# Schema SQLite
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now')),
    description TEXT,
    sample_size INTEGER,
    volumes     TEXT   -- JSON list
);

CREATE TABLE IF NOT EXISTS lines (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER REFERENCES sessions(id),
    page_id         TEXT,      -- "PG009-217"
    volume          TEXT,
    image_path      TEXT,
    line_index      INTEGER,
    bbox            TEXT,      -- JSON {x, y, w, h}
    line_image      BLOB,      -- crop PNG bytes
    tesseract_text  TEXT,
    qwen_text       TEXT,
    detected_lang   TEXT,
    agreement_score REAL,
    reviewed_text   TEXT,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_lines_session  ON lines(session_id);
CREATE INDEX IF NOT EXISTS idx_lines_status   ON lines(status);
CREATE INDEX IF NOT EXISTS idx_lines_page     ON lines(page_id);
"""

# ---------------------------------------------------------------------------
# Helpers: filesystem
# ---------------------------------------------------------------------------

def find_volumes(root: Path) -> list[Path]:
    """Retorna diretórios PG*/PL* dentro de root."""
    vols = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and any(d.name.upper().startswith(p) for p in SUPPORTED_PREFIXES):
            vols.append(d)
    return vols


def find_page_pairs(vol: Path) -> list[tuple[Path, Path, str]]:
    """
    Retorna lista de (img_path, txt_path, page_id) onde ambos existem.
    page_id = "{volume}-{pagenum}"
    """
    img_dir = vol / "images"
    txt_dir = vol / "text"
    if not img_dir.exists() or not txt_dir.exists():
        return []

    # índice txt por número de página
    txt_by_page: dict[str, Path] = {}
    for f in txt_dir.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", f.name)
        if m:
            txt_by_page[m.group(1)] = f

    pairs = []
    for img in sorted(img_dir.glob("*.png")):
        m = re.search(r"-(\d+)\.png$", img.name)
        if m:
            pnum = m.group(1)
            if pnum in txt_by_page:
                page_id = f"{vol.name}-{pnum}"
                pairs.append((img, txt_by_page[pnum], page_id))

    return pairs

# ---------------------------------------------------------------------------
# Helpers: Tesseract
# ---------------------------------------------------------------------------

def tesseract_lines(img_path: Path) -> list[dict]:
    """
    Roda Tesseract em modo TSV e retorna lista de linhas com bbox + texto.
    Agrupa word-level TSV em linhas pelo campo line_num.
    Descarta linhas com texto vazio.
    """
    tsv = pytesseract.image_to_data(
        str(img_path),
        lang=TESS_LANG,
        output_type=pytesseract.Output.DICT,
    )

    # Agrupa por (block_num, par_num, line_num)
    line_map: dict[tuple, dict] = {}
    n = len(tsv["text"])
    for i in range(n):
        text = tsv["text"][i].strip()
        if not text:
            continue
        key = (tsv["block_num"][i], tsv["par_num"][i], tsv["line_num"][i])
        if key not in line_map:
            line_map[key] = {
                "text": [],
                "x": tsv["left"][i],
                "y": tsv["top"][i],
                "x2": tsv["left"][i] + tsv["width"][i],
                "y2": tsv["top"][i] + tsv["height"][i],
            }
        else:
            line_map[key]["x"]  = min(line_map[key]["x"],  tsv["left"][i])
            line_map[key]["y"]  = min(line_map[key]["y"],  tsv["top"][i])
            line_map[key]["x2"] = max(line_map[key]["x2"], tsv["left"][i] + tsv["width"][i])
            line_map[key]["y2"] = max(line_map[key]["y2"], tsv["top"][i] + tsv["height"][i])
        line_map[key]["text"].append(text)

    # Converte para lista ordenada por posição vertical
    result = []
    for key in sorted(line_map, key=lambda k: (line_map[k]["y"], line_map[k]["x"])):
        d = line_map[key]
        result.append({
            "text": " ".join(d["text"]),
            "bbox": {
                "x": d["x"],
                "y": d["y"],
                "w": d["x2"] - d["x"],
                "h": d["y2"] - d["y"],
            },
        })

    return result


def crop_line(img: Image.Image, bbox: dict) -> bytes:
    """Recorta a linha da imagem e retorna PNG em bytes."""
    x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    # margem de 2px
    x1 = max(0, x - 2)
    y1 = max(0, y - 2)
    x2 = min(img.width,  x + w + 2)
    y2 = min(img.height, y + h + 2)
    crop = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()

# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

def open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=3000;")
    

    conn.executescript(DDL)
    conn.commit()
    return conn


def session_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    return row[0] > 0


def create_session(conn: sqlite3.Connection, description: str, sample_size: int, volumes: list[str]) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (description, sample_size, volumes) VALUES (?, ?, ?)",
        (description, sample_size, json.dumps(volumes)),
    )
    conn.commit()
    return cur.lastrowid


def insert_line(conn: sqlite3.Connection, session_id: int, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO lines
            (session_id, page_id, volume, image_path, line_index,
             bbox, line_image, tesseract_text, detected_lang)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            row["page_id"],
            row["volume"],
            row["image_path"],
            row["line_index"],
            json.dumps(row["bbox"]),
            row["line_image"],
            row["tesseract_text"],
            row["detected_lang"],
        ),
    )

# Multiprocessing

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


def _process_page(img_path_str: str, txt_path_str: str, page_id: str, volume: str) -> dict:
    """Processa uma página: roda tesseract, recorta linhas e retorna dados serializáveis."""
    try:
        img_path = Path(img_path_str)
        # Executa segmentação (TSV -> linhas)
        lines = tesseract_lines(img_path)
    except Exception as e:
        return {"status": "error", "page_id": page_id, "message": f"tesseract_lines failed: {e}"}

    if len(lines) < MIN_LINES:
        return {"status": "skip", "page_id": page_id, "num_lines": len(lines)}

    # Detecta língua dominante (heurística simples)
    try:
        osd = pytesseract.image_to_osd(str(img_path), output_type=pytesseract.Output.DICT)
        detected_lang = osd.get("script", "unknown")
    except Exception:
        detected_lang = "unknown"

    try:
        img = Image.open(img_path)
    except Exception as e:
        return {"status": "error", "page_id": page_id, "message": f"Image.open failed: {e}"}

    rows = []
    for idx, line in enumerate(lines):
        try:
            line_png = crop_line(img, line["bbox"])
        except Exception as e:
            return {"status": "error", "page_id": page_id, "message": f"crop_line failed: {e}"}

        rows.append({
            "line_index": idx,
            "bbox": line["bbox"],
            "line_image": line_png,
            "tesseract_text": line["text"],
        })

    return {
        "status": "ok",
        "page_id": page_id,
        "num_lines": len(rows),
        "detected_lang": detected_lang,
        "image_path": str(img_path.resolve()),
        "volume": volume,
        "rows": rows,
    }


def _process_page_wrapper(task_tuple: tuple) -> dict:
    """Wrapper para Pool.map — recebe uma tupla e repassa para _process_page."""
    try:
        img_path_str, txt_path_str, page_id, volume = task_tuple
        return _process_page(img_path_str, txt_path_str, page_id, volume)
    except Exception as e:
        return {"status": "error", "page_id": task_tuple[2] if len(task_tuple) > 2 else "?", "message": str(e)}


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def sample_and_ingest(
    root: Path,
    conn: sqlite3.Connection,
    per_volume: int,
    description: str,
    seed: int | None = None,
) -> None:
    if seed is not None:
        random.seed(seed)

    volumes = find_volumes(root)
    if not volumes:
        print(f"[ERRO] Nenhum volume PG/PL encontrado em {root}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] {len(volumes)} volumes encontrados: {[v.name for v in volumes]}")

    all_lines_count = 0
    included_volumes = []

    # Cria sessão antes de processar
    session_id = create_session(conn, description, 0, [])  # sample_size atualizado ao final

    # Configuração de multiprocessing
    cpu_threads = max(1, mp.cpu_count() // 2)
    omp_threads = 2  # default solicitado
    print(f"[INFO] Multiprocessing: workers={cpu_threads}, omp_threads={omp_threads}")

    for vol in volumes:
        pairs = find_page_pairs(vol)
        if not pairs:
            print(f"[WARN] {vol.name}: nenhum par img+txt encontrado, pulando")
            continue

        # Amostra aleatória de páginas do volume
        sample = random.sample(pairs, min(per_volume, len(pairs)))
        vol_lines = 0
        # Preparar tarefas para pool: cada tarefa é (img_path_str, txt_path_str, page_id, volume_name)
        tasks = [(str(img_path), str(txt_path), page_id, vol.name) for img_path, txt_path, page_id in sample]

        # Processar páginas em paralelo em subprocessos; inserção no DB é feita no processo principal
        with mp.Pool(processes=cpu_threads, initializer=_init_omp_env, initargs=(omp_threads,)) as pool:
            results = pool.map(_process_page_wrapper, tasks)

        for res in results:
            # res: dict with keys status, page_id, num_lines, detected_lang, image_path, volume, rows (list)
            if res.get("status") == "error":
                print(f"[WARN] {res.get('page_id')}: erro - {res.get('message')}")
                continue
            if res.get("status") == "skip":
                print(f"[SKIP] {res.get('page_id')}: apenas {res.get('num_lines')} linhas, abaixo do mínimo {MIN_LINES}")
                continue

            rows = res.get("rows", [])
            for row in rows:
                insert_line(conn, session_id, {
                    "page_id":        res.get("page_id"),
                    "volume":         res.get("volume"),
                    "image_path":     res.get("image_path"),
                    "line_index":     row["line_index"],
                    "bbox":           row["bbox"],
                    "line_image":     row["line_image"],
                    "tesseract_text": row["tesseract_text"],
                    "detected_lang":  res.get("detected_lang"),
                })
                vol_lines += 1

            print(f"[OK] {res.get('page_id')}: {res.get('num_lines')} linhas inseridas")

        conn.commit()
        all_lines_count += vol_lines
        included_volumes.append(vol.name)
        print(f"[VOL] {vol.name}: {vol_lines} linhas totais de {len(sample)} páginas")

    # Atualiza sessão com totais reais
    conn.execute(
        "UPDATE sessions SET sample_size=?, volumes=?, updated_at=datetime('now') WHERE id=?",
        (all_lines_count, json.dumps(included_volumes), session_id),
    )
    conn.commit()
    print(f"\n[DONE] Sessão {session_id}: {all_lines_count} linhas de {len(included_volumes)} volumes")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Amostragem e segmentação para finetuning Tesseract")
    parser.add_argument("--root",        required=True,                  help="Diretório raiz com volumes PG*/PL*")
    parser.add_argument("--db",          default="ocr.db",               help="Caminho do SQLite (default: ocr.db)")
    parser.add_argument("--session",     default="amostra",              help="Descrição da sessão")
    parser.add_argument("--per-volume",  type=int, default=DEFAULT_PER_VOLUME, help="Páginas por volume")
    parser.add_argument("--seed",        type=int, default=None,         help="Seed aleatória para reprodutibilidade")
    parser.add_argument("--resample",    action="store_true",            help="Força nova amostragem mesmo se já existir sessão")
    args = parser.parse_args()

    conn = open_db(args.db)

    if session_exists(conn) and not args.resample:
        count = conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
        print(f"[INFO] Sessão existente com {count} linhas. Use --resample para nova amostragem.")
        return

    if args.resample:
        print("[INFO] --resample: limpando dados anteriores...")
        conn.executescript("DELETE FROM lines; DELETE FROM sessions;")
        conn.commit()

    sample_and_ingest(
        root=Path(args.root),
        conn=conn,
        per_volume=args.per_volume,
        description=args.session,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
