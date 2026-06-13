#!/usr/bin/env python3
"""
infer.py — inferência via Ollama/OpenAI em batch, atualiza SQLite
Suporta paralelismo via --jobs (workers independentes, lotes atômicos via WAL)
"""

import argparse
import base64
import json
import os
import re
import socket
import sqlite3
import time
from typing import Optional
import unicodedata
import urllib.request
from pathlib import Path

import requests
import urllib3.connection as _uc
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import multiprocessing as mp

from sample import preprocess_image
import cv2
import numpy as np
import io
from PIL import Image

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TOP_P = 0.9
DEFAULT_TEMP = 0.1
DEFAULT_BATCH_SIZE = 50

SYSTEM_PROMPT = """You are a precise OCR post-processor specializing in classical Latin and Ancient Greek manuscripts and printed editions.
Your task: transcribe EXACTLY what you see in the image — a single line of text from a historical printed book.

Rules:
- Output ONLY the transcribed text, nothing else. No explanations, no punctuation added, no commentary.
- Preserve original spelling, ligatures, abbreviations, diacritics.
- For Latin: preserve macrons, cedillas, and any special characters visible.
- For Greek: preserve all accents (acute, grave, circumflex), breathings (smooth, rough), and subscripts.
- Do NOT modernize spelling or correct what appear to be errors — transcribe what is printed.
- For partially legible text, transcribe what you can see and use [?] for individual characters you cannot make out.
"""

USER_PROMPT = "Transcribe:"

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DEFAULT_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def connect_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def claim_batch(db: str, batch_size: int) -> list[tuple]:
    """
    Reserva atomicamente até `batch_size` linhas pending → processing.
    Retorna lista de (id, line_image, tesseract_text).
    Funciona com SQLite 3.35+ (RETURNING). Para versões anteriores,
    usa fallback com SELECT + UPDATE separados dentro de BEGIN IMMEDIATE.
    """
    conn = connect_db(db)
    try:
        rows = conn.execute(
            """
            UPDATE lines
            SET status = 'processing', updated_at = datetime('now')
            WHERE id IN (
                SELECT id FROM lines WHERE status = 'pending' LIMIT ?
            )
            RETURNING id, image_path, bbox, line_image, tesseract_text
        """,
            (batch_size,),
        ).fetchall()
        conn.commit()
        return [
            (
                r["id"],
                r["image_path"],
                r["bbox"],
                r["line_image"],
                r["tesseract_text"],
            )
            for r in rows
        ]
    except sqlite3.OperationalError:
        # Fallback para SQLite < 3.35
        conn.execute("BEGIN IMMEDIATE")
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM lines WHERE status='pending' LIMIT ?", (batch_size,)
            ).fetchall()
        ]
        if not ids:
            conn.commit()
            return []
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE lines SET status='processing', updated_at=datetime('now') WHERE id IN ({placeholders})",
            ids,
        )
        rows = conn.execute(
            f"SELECT id, image_path, bbox, line_image, tesseract_text FROM lines WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        conn.commit()
        return [
            (
                r["id"],
                r["image_path"],
                r["bbox"],
                r["line_image"],
                r["tesseract_text"],
            )
            for r in rows
        ]
    finally:
        conn.close()


def save_result(
    db: str, line_id: int, qwen_text: str, score: float, status: str = "inferred"
) -> None:
    conn = connect_db(db)
    conn.execute(
        """
        UPDATE lines
        SET qwen_text=?, agreement_score=?, status=?, updated_at=datetime('now')
        WHERE id=?
    """,
        (qwen_text, score, status, line_id),
    )
    conn.commit()
    conn.close()


def ensure_runs_column(db: str) -> None:
    """Garante que a coluna `runs` exista na tabela `lines`. Se faltar, adiciona com valor default 0."""
    conn = connect_db(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(lines)").fetchall()]
    if "runs" not in cols:
        # ALTER TABLE ADD COLUMN é seguro para SQLite — adiciona coluna com default 0
        conn.execute("ALTER TABLE lines ADD COLUMN runs INTEGER DEFAULT 0")
        conn.commit()
    conn.close()


def increment_runs(db: str, line_id: int) -> None:
    """Incrementa o contador `runs` para a linha especificada."""
    conn = connect_db(db)
    conn.execute(
        "UPDATE lines SET runs = IFNULL(runs,0) + 1, updated_at = datetime('now') WHERE id = ?",
        (line_id,),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_orig_connect = _uc.HTTPConnection.connect


def make_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=Retry(total=0))

    def _connect_with_keepalive(self):
        _orig_connect(self)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)

    _uc.HTTPConnection.connect = _connect_with_keepalive
    session.mount("https://", adapter)
    return session


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def levenshtein(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def unicode_block_diff(a: str, b: str) -> int:
    """Conta caracteres que diferem dentro do bloco politônico grego."""
    a = unicodedata.normalize("NFC", a)
    b = unicodedata.normalize("NFC", b)
    diffs = sum(
        1 for ca, cb in zip(a, b) if ca != cb and (ord(ca) > 0x1F00 or ord(cb) > 0x1F00)
    )
    diffs += abs(len(a) - len(b))  # diferença de comprimento
    return diffs


def has_diacritic_diff(a: str, b: str) -> bool:
    """Detecta diferenças em caracteres com diacríticos mesmo com score alto."""

    def normalize(s):
        # NFC garante representação consistente de compostos Unicode
        return unicodedata.normalize("NFC", s)

    a, b = normalize(a), normalize(b)
    if a == b:
        return False

    # Compara caractere a caractere procurando diferenças em codepoints altos
    for ca, cb in zip(a, b):
        if ca != cb:
            # Diferença em bloco grego politônico (U+1F00–U+1FFF)
            if ord(ca) > 0x1F00 or ord(cb) > 0x1F00:
                return True
    return False


def agreement_score(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    a, b = a.strip(), b.strip()
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    return round(1.0 - levenshtein(a, b) / max_len, 4)


def parse_page_num_from_filename(image_path: Path) -> Optional[int]:
    """
    Extrai o sufixo numérico final da imagem, ex.: foo-076.png -> 76.
    """
    m = re.search(r"-([0-9]{1,4})$", image_path.stem)
    return int(m.group(1)) if m else None


def infer_volume_id(image_path: Path) -> Optional[str]:
    """
    Considera a convenção teste/<VOL>/images/<file>.png → retorna <VOL>.
    """
    try:
        return image_path.parent.parent.name
    except Exception:
        return None


def txt_path_for_image(img_path: Path, txt_dir: Path) -> Path:
    """
    Seleciona o txt associado a uma imagem:
    - Usa o nome estável se existir.
    - Caso contrário, procura qualquer txt que termine com o número da página.
    - Fallback: path estável mesmo que ainda não exista (para escrita).
    """
    stable = txt_dir / (img_path.stem + ".txt")
    if stable.exists():
        return stable

    page_num = parse_page_num_from_filename(img_path)
    if page_num is not None:
        # prioriza zero-padding, depois sem padding
        candidates = sorted(txt_dir.glob(f"*-{page_num:03d}.txt"))
        if candidates:
            return candidates[0]
        candidates = sorted(txt_dir.glob(f"*-{page_num}.txt"))
        if candidates:
            return candidates[0]

    return stable


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def call_ollama_vision(
    image_bytes: bytes,
    model: str,
    base_url: str,
    user_prompt: str = USER_PROMPT,
) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt, "images": [b64]},
        ],
        "stream": False,
        "options": {"top_p": DEFAULT_TOP_P, "temperature": DEFAULT_TEMP},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=130) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "").strip()


def openai_process_image(
    image_bytes: bytes,
    model: str = DEFAULT_OPENAI_MODEL,
    base_url: str = DEFAULT_OPENAI_BASE_URL,
    api_key: str | None = None,
    mime: str = "image/png",
    reprocess: bool = False,
    temperature: float = DEFAULT_TEMP,
    top_p: float = DEFAULT_TOP_P,
    user_prompt: str = USER_PROMPT,
) -> str:
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não encontrado no ambiente.")

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_url = {"url": f"data:{mime};base64,{img_b64}"}
    if "gpt-5" in model:
        image_url["detail"] = "high"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": USER_PROMPT},
                {"type": "image_url", "image_url": image_url},
            ],
        },
    ]

    payload = {
        "model": model,
        "messages": messages,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    }
    timeout = 120

    if "gpt-5" not in model:
        payload["temperature"] = temperature
        payload["top_p"] = top_p
    else:
        payload["reasoning_effort"] = "high" if reprocess else "medium"
        payload["service_tier"] = "flex"
        if reprocess:
            timeout = 1200

    with make_session() as session:
        r = session.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            data=json.dumps(payload),
            timeout=timeout,
        )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def worker(args: dict) -> None:
    """
    Processo filho: fica pegando lotes do banco até não haver mais pending.
    Cada worker tem sua própria conexão — sem estado compartilhado.
    """
    db = args["db"]
    model = args["model"]
    base_url = args["base_url"]
    service = args["service"]
    delay = args["delay"]
    batch_size = args["batch_size"]
    worker_id = args["worker_id"]

    prefix = f"[W{worker_id}]"
    processed = 0

    def crop_from_preprocessed(img: np.ndarray, bbox_json: str) -> bytes:
        """Crop bbox from preprocessed (rotated) image and return PNG bytes."""
        try:
            bbox = json.loads(bbox_json) if isinstance(bbox_json, str) else bbox_json
            x = int(bbox.get("x", 0))
            y = int(bbox.get("y", 0))
            w = int(bbox.get("w", 0))
            h = int(bbox.get("h", 0))
        except Exception:
            raise

        x1 = max(0, x - 2)
        y1 = max(0, y - 2)
        x2 = min(img.shape[1], x + w + 2)
        y2 = min(img.shape[0], y + h + 2)
        crop = img[y1:y2, x1:x2]
        buf = io.BytesIO()
        if len(crop.shape) == 3:
            pil_img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        else:
            pil_img = Image.fromarray(crop)
        pil_img.save(buf, format="PNG")
        return buf.getvalue()

    while True:
        batch = claim_batch(db, batch_size)
        if not batch:
            print(f"{prefix} Sem mais linhas. Encerrando ({processed} processadas).")
            break

        for line_id, image_path, bbox, line_image, tesseract_text in batch:
            try:
                # incrementa contador de tentativas antes de cada passagem pela LLM
                try:
                    increment_runs(db, line_id)
                except Exception:
                    # não deve bloquear o processamento se increment falhar
                    pass

                context: str|None = None

                image_path = Path(image_path) if image_path else None

                if image_path:
                    volume_path = image_path.parent.parent
                    txt_path = txt_path_for_image(image_path, volume_path / "text")

                    print(txt_path)

                    if txt_path.exists():
                        with open(txt_path, "r", encoding="utf-8") as f:
                            context = f.read()

                # Prefer crop from original preprocessed image; fallback to stored line_image bytes
                cropped_bytes = None
                try:
                    if image_path:
                        img = cv2.imread(str(image_path))
                        if img is not None:
                            _, img_proc = preprocess_image(img)
                            try:
                                cropped_bytes = crop_from_preprocessed(img_proc, bbox)
                            except Exception:
                                cropped_bytes = None
                except Exception as e:
                    print(
                        f"{prefix} warning: unable to crop from original image id={line_id}: {e}"
                    )

                to_send = cropped_bytes if cropped_bytes is not None else line_image

                user_prompt = USER_PROMPT

                if context:
                    context = unicodedata.normalize("NFC", context)
                    user_prompt = f"PAGE CONTEXT: <context>\n{context}\n</context>\nTranscribe only the image:"

                if service == "ollama":
                    raw = call_ollama_vision(to_send, model, base_url, user_prompt=user_prompt)
                else:
                    raw = openai_process_image(
                        image_bytes=to_send, model=model, base_url=base_url, user_prompt=user_prompt
                    )
                qwen_text = strip_think(raw)
                score = agreement_score(tesseract_text or "", qwen_text)
                save_result(db, line_id, qwen_text, score, "inferred")
                marker = "✓" if score >= 0.8 else ("△" if score >= 0.5 else "✗")
                print(
                    f"{prefix} id={line_id} score={score:.2f} {marker}  "
                    f"tess={repr((tesseract_text or '')[:64])}  qwen={repr(qwen_text[:64])}"
                )
            except Exception as e:
                print(f"{prefix} ERRO id={line_id}: {e}")
                save_result(db, line_id, "", 0.0, "error")

            processed += 1
            if delay > 0:
                time.sleep(delay)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Inferência batch via Ollama/OpenAI")
    parser.add_argument("--db", default="ocr.db")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--jobs", type=int, default=1, help="Workers paralelos")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Linhas por lote por worker",
    )
    parser.add_argument("--service", default="ollama", choices=["ollama", "openai"])
    parser.add_argument(
        "--reprocess-below",
        type=float,
        default=None,
        help=(
            "Marca como pending entradas cujo agreement_score < VAL (0.0-1.0) "
            "antes de iniciar os workers, para que sejam reprocessadas"
        ),
    )
    args = parser.parse_args()

    # Garantir que a coluna `runs` exista antes de tocar no banco
    ensure_runs_column(args.db)

    conn = connect_db(args.db)
    pending = conn.execute(
        "SELECT COUNT(*) FROM lines WHERE status='pending'"
    ).fetchone()[0]
    conn.close()

    # Se for pedido reprocessar abaixo de um limiar, atualiza o banco
    if args.reprocess_below is not None:
        if not (0.0 <= args.reprocess_below <= 1.0):
            print("[ERROR] --reprocess-below deve estar entre 0.0 e 1.0")
            return
        conn = connect_db(args.db)
        # Conta quantas linhas qualificam
        to_requeue = conn.execute(
            "SELECT COUNT(*) FROM lines WHERE (status='inferred' OR status='error') AND IFNULL(agreement_score,0) < ?",
            (args.reprocess_below,),
        ).fetchone()[0]
        if to_requeue:
            conn.execute(
                "UPDATE lines SET status='pending', updated_at=datetime('now') WHERE (status='inferred' OR status='error') AND IFNULL(agreement_score,0) < ?",
                (args.reprocess_below,),
            )
            conn.commit()
        conn.close()
        print(
            f"[INFO] {to_requeue} linhas com agreement_score < {args.reprocess_below} marcadas como pending para reprocessamento"
        )

        # Recalcula pendentes para informar o usuário
        conn = connect_db(args.db)
        pending = conn.execute(
            "SELECT COUNT(*) FROM lines WHERE status='pending'"
        ).fetchone()[0]
        conn.close()

    if pending == 0:
        print("[INFO] Nenhuma linha pendente.")
        return

    # Aplica --limit: marca excedente como fora do escopo atual
    # (simples: apenas os primeiros `limit` serão pegos pelos workers)
    effective = min(pending, args.limit) if args.limit else pending
    print(f"[INFO] {effective} linhas para processar com {args.jobs} worker(s)")

    worker_args = [
        {
            "db": args.db,
            "model": args.model,
            "base_url": args.base_url,
            "service": args.service,
            "delay": args.delay,
            "batch_size": args.batch_size,
            "worker_id": i,
        }
        for i in range(args.jobs)
    ]

    if args.jobs == 1:
        worker(worker_args[0])
    else:
        with mp.Pool(processes=args.jobs) as pool:
            pool.map(worker, worker_args)

    conn = connect_db(args.db)
    done = conn.execute(
        "SELECT COUNT(*) FROM lines WHERE status='inferred'"
    ).fetchone()[0]
    errors = conn.execute("SELECT COUNT(*) FROM lines WHERE status='error'").fetchone()[
        0
    ]
    conn.close()
    print(f"\n[DONE] inferred={done}  errors={errors}")


if __name__ == "__main__":
    main()
