#!/usr/bin/env python3
"""
infer.py — inferência Qwen via Ollama em batch, atualiza SQLite

Uso:
    python infer.py --db ocr.db
    python infer.py --db ocr.db --model qwen3.5:9b --base-url http://localhost:11434
    python infer.py --db ocr.db --limit 100   # processa só 100 linhas (teste)
"""

import argparse
import base64
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_MODEL    = "qwen3.5:9b"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TOP_P    = 0.9
DEFAULT_TEMP     = 0.1   # baixo: queremos transcrição determinística

SYSTEM_PROMPT = """You are a precise OCR post-processor specializing in classical Latin and Ancient Greek manuscripts and printed editions. 
Your task: transcribe EXACTLY what you see in the image — a single line of text from a historical printed book.

Rules:
- Output ONLY the transcribed text, nothing else. No explanations, no punctuation added, no commentary.
- Preserve original spelling, ligatures, abbreviations, diacritics.
- For Latin: preserve macrons, cedillas, and any special characters visible.
- For Greek: preserve all accents (acute, grave, circumflex), breathings (smooth, rough), and subscripts.
- Do NOT modernize spelling or correct what appear to be errors — transcribe what is printed.
- If the line is completely illegible, output exactly: [ILLEGIBLE]
"""

USER_PROMPT = "Transcribe this line of text exactly as printed:"

# ---------------------------------------------------------------------------
# Levenshtein normalizado (agreement score)
# ---------------------------------------------------------------------------

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


def agreement_score(a: str, b: str) -> float:
    """Similaridade 0.0–1.0 entre dois textos (1.0 = idênticos)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    a, b = a.strip(), b.strip()
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    dist = levenshtein(a, b)
    return round(1.0 - dist / max_len, 4)

# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def call_ollama_vision(
    image_bytes: bytes,
    model: str,
    base_url: str,
) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT,
                "images": [b64],
            },
        ],
        "stream": False,
        "options": {
            "top_p":        DEFAULT_TOP_P,
            "temperature":  DEFAULT_TEMP,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "").strip()

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def process_pending(
    conn: sqlite3.Connection,
    model: str,
    base_url: str,
    limit: int | None,
    delay: float,
) -> None:
    query = "SELECT id, line_image, tesseract_text FROM lines WHERE status = 'pending'"
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    total = len(rows)
    print(f"[INFO] {total} linhas pendentes para inferência")

    for i, (line_id, line_image, tesseract_text) in enumerate(rows, 1):
        try:
            qwen_text = call_ollama_vision(line_image, model, base_url)
        except Exception as e:
            print(f"[WARN] linha {line_id}: erro Ollama — {e}")
            conn.execute(
                "UPDATE lines SET status='error', updated_at=datetime('now') WHERE id=?",
                (line_id,),
            )
            conn.commit()
            continue

        score = agreement_score(tesseract_text or "", qwen_text)

        conn.execute(
            """UPDATE lines
               SET qwen_text=?, agreement_score=?, status='inferred',
                   updated_at=datetime('now')
               WHERE id=?""",
            (qwen_text, score, line_id),
        )
        conn.commit()

        marker = "✓" if score >= 0.8 else ("△" if score >= 0.5 else "✗")
        print(f"[{i:4d}/{total}] id={line_id} score={score:.2f} {marker}  tess={repr(tesseract_text[:40] if tesseract_text else '')}  qwen={repr(qwen_text[:40])}")

        if delay > 0:
            time.sleep(delay)

    errors = conn.execute("SELECT COUNT(*) FROM lines WHERE status='error'").fetchone()[0]
    done   = conn.execute("SELECT COUNT(*) FROM lines WHERE status='inferred'").fetchone()[0]
    print(f"\n[DONE] inferred={done}  errors={errors}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Inferência Qwen batch via Ollama")
    parser.add_argument("--db",       default="ocr.db",        help="Caminho do SQLite")
    parser.add_argument("--model",    default=DEFAULT_MODEL,   help="Modelo Ollama")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="URL base do Ollama")
    parser.add_argument("--limit",    type=int, default=None,  help="Limita N linhas (teste)")
    parser.add_argument("--delay",    type=float, default=0.0, help="Delay entre chamadas em segundos")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    pending = conn.execute("SELECT COUNT(*) FROM lines WHERE status='pending'").fetchone()[0]

    if pending == 0:
        inferred = conn.execute("SELECT COUNT(*) FROM lines WHERE status='inferred'").fetchone()[0]
        print(f"[INFO] Nenhuma linha pendente. {inferred} já inferidas. Rode sample.py primeiro ou verifique o DB.")
        return

    process_pending(conn, args.model, args.base_url, args.limit, args.delay)


if __name__ == "__main__":
    main()
