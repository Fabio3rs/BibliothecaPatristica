#!/usr/bin/env python3
"""
review.py - interface Flask para revisão humana dos pares linha-imagem/texto

Uso:
  python review.py --db ocr.db
  python review.py --db ocr.db --port 5001 --host 0.0.0.0

Fluxo:
  - Lista linhas por agreement_score ASC (mais problemáticas primeiro)
  - Mostra imagem do crop + texto Tesseract + texto Qwen
  - Permite aprovar, corrigir ou rejeitar cada linha
  - Filtra por status, volume, score mínimo/máximo
"""

import argparse
import base64
import html
import json
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, redirect, render_template_string, request, url_for

# imagem e preprocessamento
import cv2
import io
from PIL import Image
import numpy as np

try:
    # prefer local import when running script directly
    from sample import preprocess_image
except Exception:
    # fallback to package-relative import
    try:
        from .sample import preprocess_image
    except Exception:
        preprocess_image = None


def deskew_like_sample_color(img: np.ndarray, border_size: int = 50) -> np.ndarray:
    """Aplica o mesmo deskew do modo sample.py, mas preserva as cores originais."""
    arr = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    thresh = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
    dilate = cv2.dilate(thresh, kernel, iterations=2)

    contours, _ = cv2.findContours(dilate, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    angles = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if 500 < area < 50000:
            rect = cv2.minAreaRect(cnt)
            angle = rect[-1]
            rw, rh = rect[1]
            if rw < rh:
                angle = angle + 90
            if abs(angle) <= 10:
                angles.append(angle)

    median_angle = np.median(angles) if angles else 0.0
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

TEMPLATE = """
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OCR Review — {{ page_id }}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: monospace; background: #1a1a1a; color: #e0e0e0; }

  .topbar {
    display: flex; align-items: center; gap: 16px;
    padding: 10px 20px; background: #111; border-bottom: 1px solid #333;
    flex-wrap: wrap;
  }
  .topbar h1 { font-size: 14px; color: #aaa; }
  .topbar .stats { font-size: 12px; color: #666; }
  .topbar select, .topbar input { background: #222; color: #ccc; border: 1px solid #444; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
  .topbar button { background: #333; color: #ccc; border: 1px solid #555; padding: 5px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
  .topbar button:hover { background: #444; }

  .nav-bar {
    display: flex; gap: 8px; padding: 8px 20px; background: #161616; border-bottom: 1px solid #2a2a2a;
    align-items: center; flex-wrap: wrap;
  }
  .nav-bar a { color: #888; text-decoration: none; font-size: 12px; padding: 3px 8px; border-radius: 3px; border: 1px solid #333; }
  .nav-bar a:hover { background: #2a2a2a; color: #ccc; }
  .nav-bar .current-info { font-size: 12px; color: #555; margin-left: auto; }

  .main { display: flex; height: calc(100vh - 90px); }

  .img-panel {
    flex: 1; min-width: 0; padding: 20px;
    display: flex; flex-direction: column; gap: 12px; overflow-y: auto;
    border-right: 1px solid #2a2a2a;
  }
  .img-panel h2 { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 1px; }
  .line-crop {
    background: #fff; padding: 8px; border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
    max-height: 120px; overflow: hidden;
  }
  .line-crop img { max-width: 100%; max-height: 100px; image-rendering: pixelated; }
  .meta { font-size: 11px; color: #555; }
  .path-meta {
    font-size: 11px;
    color: #777;
    background: #151515;
    border: 1px solid #2a2a2a;
    border-radius: 4px;
    padding: 8px 10px;
    line-height: 1.5;
    word-break: break-all;
  }
  .path-meta b { color: #aaa; }

  .edit-panel {
    width: 420px; flex-shrink: 0; padding: 20px;
    display: flex; flex-direction: column; gap: 14px; overflow-y: auto;
  }
  .edit-panel h2 { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 1px; }

  .score-bar { height: 4px; border-radius: 2px; background: #333; margin-bottom: 4px; }
  .score-fill { height: 100%; border-radius: 2px; }

  .text-block {
    background: #222; border: 1px solid #333; border-radius: 4px;
    padding: 10px; font-size: 13px; line-height: 1.6;
    color: #ccc; white-space: pre-wrap; word-break: break-all;
    min-height: 40px;
  }
  .text-block.clickable { cursor: pointer; }
  .text-block.clickable:hover { border-color: #555; background: #282828; }
  .muted { color: #666; }

  .raw-view {
    background: #191919;
    border: 1px solid #333;
    border-radius: 6px;
    padding: 12px;
    font-size: 12px;
    line-height: 1.55;
    color: #cfcfcf;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 420px;
    overflow: auto;
  }
  .raw-view .match-exact {
    background: rgba(92, 184, 92, 0.22);
    color: #efffee;
    border-radius: 3px;
    padding: 0 2px;
  }
  .raw-view .match-normalized {
    background: rgba(240, 173, 78, 0.22);
    color: #fff6df;
    border-radius: 3px;
    padding: 0 2px;
  }
  .raw-view .match-fuzzy {
    background: rgba(217, 83, 79, 0.18);
    color: #ffe2e2;
    border-radius: 3px;
    padding: 0 2px;
  }
  .match-badge {
    display: inline-block;
    margin-left: 8px;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    vertical-align: middle;
  }
  .match-high { background: #1d4f1d; color: #a6e3a6; }
  .match-med  { background: #5a4500; color: #ffd76a; }
  .match-low  { background: #5a1f1f; color: #ff9d9d; }

  .version-list { display: flex; flex-direction: column; gap: 8px; }
  .version-item { background: #1d1d1d; border: 1px solid #333; border-radius: 4px; padding: 8px; }
  .version-head { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; color: #888; margin-bottom: 6px; }
  .version-meta { color: #777; }

  textarea {
    width: 100%; background: #222; border: 1px solid #555; border-radius: 4px;
    padding: 10px; font-size: 13px; line-height: 1.6; color: #e0e0e0;
    resize: vertical; min-height: 60px; font-family: monospace;
  }
  textarea:focus { outline: none; border-color: #888; }

  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  .btn { padding: 8px 18px; border-radius: 5px; border: none; cursor: pointer; font-size: 13px; font-weight: bold; transition: opacity .15s; }
  .btn:hover { opacity: .85; }
  .btn-approve  { background: #2d6a2d; color: #9ddb9d; }
  .btn-correct  { background: #4a3a00; color: #f0c040; }
  .btn-reject   { background: #5a1a1a; color: #e08080; }
  .btn-skip     { background: #333; color: #888; }

  .hint { font-size: 11px; color: #555; }

  .badge {
    display: inline-block; padding: 2px 7px; border-radius: 10px; font-size: 11px; font-weight: bold;
  }
  .badge-pending  { background: #333; color: #888; }
  .badge-inferred { background: #1a3a5a; color: #6ab0f5; }
  .badge-approved { background: #1a4a1a; color: #6adb6a; }
  .badge-corrected{ background: #3a2a00; color: #f0c040; }
  .badge-rejected { background: #3a1010; color: #e08080; }
  .badge-error    { background: #4a1a1a; color: #ff6060; }
  .badge-empty    { background: #4a2f1a; color: #ffbf80; }

  .empty { padding: 40px; text-align: center; color: #555; }

  /* diff highlight */
  .diff-added   { background: rgba(80,200,80,0.15); }
  .diff-removed { background: rgba(200,80,80,0.15); text-decoration: line-through; }

  @media (max-width: 1100px) {
    .main { flex-direction: column; height: auto; }
    .img-panel {
      border-right: none;
      border-bottom: 1px solid #2a2a2a;
      max-height: none;
    }
    .edit-panel {
      width: 100%;
    }
  }
</style>
</head>
<body>

<div class="topbar">
  <h1>OCR Review</h1>
  <span class="stats">
    pendente: <b>{{ stats.pending }}</b> |
    inferido: <b>{{ stats.inferred }}</b> |
    aprovado: <b>{{ stats.approved }}</b> |
    corrigido: <b>{{ stats.corrected }}</b> |
    rejeitado: <b>{{ stats.rejected }}</b>
  </span>

  <form method="get" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    <select name="status" onchange="this.form.submit()">
      <option value="inferred" {% if filter_status=='inferred' %}selected{% endif %}>inferred</option>
      <option value="pending"  {% if filter_status=='pending'  %}selected{% endif %}>pending</option>
      <option value="approved" {% if filter_status=='approved' %}selected{% endif %}>approved</option>
      <option value="corrected"{% if filter_status=='corrected'%}selected{% endif %}>corrected</option>
      <option value="rejected" {% if filter_status=='rejected' %}selected{% endif %}>rejected</option>
      <option value="all"      {% if filter_status=='all'      %}selected{% endif %}>all</option>
    </select>
    <select name="tesseract" onchange="this.form.submit()">
      <option value="all" {% if filter_tesseract=='all' %}selected{% endif %}>tesseract all</option>
      <option value="empty" {% if filter_tesseract=='empty' %}selected{% endif %}>tesseract empty</option>
      <option value="filled" {% if filter_tesseract=='filled' %}selected{% endif %}>tesseract filled</option>
    </select>
    <select name="volume" onchange="this.form.submit()">
      <option value="">todos volumes</option>
      {% for v in volumes %}
      <option value="{{ v }}" {% if filter_volume==v %}selected{% endif %}>{{ v }}</option>
      {% endfor %}
    </select>
    <input type="hidden" name="offset" value="0">
  </form>
</div>

{% if line %}
<div class="nav-bar">
  {% if offset > 0 %}
  <a href="{{ url_for('review', offset=offset-1, status=filter_status, tesseract=filter_tesseract, volume=filter_volume) }}">← anterior</a>
  {% endif %}
  <a href="{{ url_for('review', offset=offset+1, status=filter_status, tesseract=filter_tesseract, volume=filter_volume) }}">próxima →</a>
  <span class="current-info">{{ offset + 1 }} / {{ total }}</span>
</div>

<div class="main">
  <!-- Painel esquerdo: imagem -->
  <div class="img-panel">
    <h2>Crop da linha</h2>
    <div style="display:flex;gap:12px;flex-wrap:wrap;">
      <div style="flex:1;min-width:160px;">
        <h3 style="font-size:11px;color:#555;margin-bottom:6px;">Tesseract crop</h3>
        <div class="line-crop">
          <img src="data:image/png;base64,{{ line_b64 }}" alt="linha-tess">
        </div>
      </div>
      <div style="flex:1;min-width:160px;">
        <h3 style="font-size:11px;color:#555;margin-bottom:6px;">LLM crop (preprocessed)</h3>
        <div class="line-crop">
          {% if llm_b64 %}
          <img src="data:image/png;base64,{{ llm_b64 }}" alt="linha-llm">
          {% else %}
          <div style="padding:10px;color:#666;font-size:12px;">nenhum recorte LLM disponível</div>
          {% endif %}
        </div>
      </div>
    </div>
    <div class="meta">
      <b>{{ line.page_id }}</b> — linha {{ line.line_index }}<br>
      volume: {{ line.volume }} | lang: {{ line.detected_lang or '?' }}<br>
      bbox: {{ line.bbox }}<br>
      status: <span class="badge badge-{{ line.status }}">{{ line.status }}</span>
      {% if not (line.tesseract_text or '').strip() %}
      <span class="badge badge-empty">tesseract vazio</span>
      {% endif %}
    </div>
    <div class="path-meta">
      <div><b>Imagem real:</b> {{ image_path or 'indisponível' }}</div>
      <div><b>Texto raw:</b> {{ txt_path or 'indisponível' }}</div>
    </div>

    {% if line.tesseract_text %}
      <h2>Tesseract</h2>
      <div class="text-block clickable" onclick="copyTo(this)" title="Clica para copiar para editor">{{ line.tesseract_text }}</div>
    {% endif %}

    {% if line.qwen_text %}
      <h2>Qwen</h2>
      <div class="text-block clickable" onclick="copyTo(this)" title="Clica para copiar para editor">{{ line.qwen_text }}</div>
    {% endif %}

    {% if version_choices %}
    <h2>Versões do modelo</h2>
    <div class="version-list">
      {% for v in version_choices %}
      <div class="version-item">
        <div class="version-head">
          <span><b>{{ v.provider }}</b> / {{ v.model }}</span>
          <span class="version-meta">
            {% if v.source_score is not none %}score={{ "%.2f"|format(v.source_score) }}{% endif %}
            {% if v.is_current %} · atual{% endif %}
            {% if v.origin %} · {{ v.origin }}{% endif %}
          </span>
        </div>
        <div class="text-block clickable" onclick="copyTo(this)" title="Clica para copiar para editor">{{ v.text_content }}</div>
      </div>
      {% endfor %}
    </div>
    {% endif %}

    {% if line.agreement_score is not none %}
    <h2>Agreement score: {{ "%.2f"|format(line.agreement_score) }}</h2>
    <div class="score-bar">
      <div class="score-fill" style="width:{{ (line.agreement_score*100)|int }}%; background: {{ '#4caf50' if line.agreement_score >= 0.8 else ('#ff9800' if line.agreement_score >= 0.5 else '#f44336') }};"></div>
    </div>
    {% endif %}
    <h2>Raw txt</h2>
    {% if raw_view %}
    <div class="raw-view">{{ raw_view|safe }}</div>
    {% else %}
    <div class="raw-view muted">Arquivo txt raw indisponível para esta linha.</div>
    {% endif %}
  </div>

  <!-- Painel direito: edição -->
  <div class="edit-panel">
    <h2>Texto revisado</h2>
    <textarea id="reviewed_text" rows="4">{{ line.reviewed_text or line.qwen_text or line.tesseract_text or '' }}</textarea>
    <textarea id="suggested_text" style="display:none;">{{ suggested_text }}</textarea>
    <p class="hint">Clique no texto Tesseract ou Qwen à esquerda para copiar para cá. Edite livremente.</p>
    {% if match_info %}
    <div class="text-block">
      match: <b>{{ match_info.label }}</b>
      <span class="match-badge match-{{ match_info.badge }}">{{ match_info.badge_text }}</span><br>
      {{ match_info.detail }}
    </div>
    {% endif %}

    {% if suggested_text %}
    <div class="actions">
      <button class="btn btn-skip" type="button" onclick="applySuggestion()">Aplicar sugestão</button>
    </div>
    {% endif %}

    <div class="actions">
      <button class="btn btn-approve"  onclick="submitAction('approved')">✓ Aprovar</button>
      <button class="btn btn-correct"  onclick="submitAction('corrected')">✎ Corrigir</button>
      <button class="btn btn-reject"   onclick="submitAction('rejected')">✗ Rejeitar</button>
      <button class="btn btn-skip"     onclick="skip()">→ Pular</button>
    </div>

    <p class="hint">Atalhos: <b>A</b> aprovar · <b>R</b> rejeitar · <b>→</b> pular</p>

    {% if line.reviewed_text %}
    <h2>Revisão anterior</h2>
    <div class="text-block">{{ line.reviewed_text }}</div>
    {% endif %}
  </div>
</div>

{% else %}
<div class="empty">
  <p>Nenhuma linha encontrada para os filtros selecionados.</p>
</div>
{% endif %}

<form id="action-form" method="post" action="{{ url_for('submit_review') }}" style="display:none;">
  <input type="hidden" name="line_id"       value="{{ line.id if line else '' }}">
  <input type="hidden" name="reviewed_text" id="form_reviewed_text">
  <input type="hidden" name="action"        id="form_action">
  <input type="hidden" name="next_offset"   value="{{ offset + 1 }}">
  <input type="hidden" name="status"        value="{{ filter_status }}">
  <input type="hidden" name="tesseract"     value="{{ filter_tesseract }}">
  <input type="hidden" name="volume"        value="{{ filter_volume }}">
</form>

<script>
function submitAction(action) {
  document.getElementById('form_reviewed_text').value = document.getElementById('reviewed_text').value;
  document.getElementById('form_action').value = action;
  document.getElementById('action-form').submit();
}

function skip() {
  window.location = "{{ url_for('review', offset=offset+1, status=filter_status, tesseract=filter_tesseract, volume=filter_volume) }}";
}

function copyTo(el) {
  document.getElementById('reviewed_text').value = el.textContent.trim();
  document.getElementById('reviewed_text').focus();
}

function applySuggestion() {
  const suggestion = document.getElementById('suggested_text');
  if (!suggestion) return;
  document.getElementById('reviewed_text').value = suggestion.value;
  document.getElementById('reviewed_text').focus();
}

document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'a' || e.key === 'A') submitAction('approved');
  if (e.key === 'r' || e.key === 'R') submitAction('rejected');
  if (e.key === 'ArrowRight') skip();
});
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
DB_PATH = "ocr.db"


def get_conn(path: str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def get_stats() -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) as n FROM lines GROUP BY status"
    ).fetchall()
    stats = {r["status"]: r["n"] for r in rows}
    return {
        "pending": stats.get("pending", 0),
        "inferred": stats.get("inferred", 0),
        "approved": stats.get("approved", 0),
        "corrected": stats.get("corrected", 0),
        "rejected": stats.get("rejected", 0),
        "error": stats.get("error", 0),
    }


def get_volumes() -> list[str]:
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT volume FROM lines ORDER BY volume").fetchall()
    return [r["volume"] for r in rows]


def get_line_versions(conn: sqlite3.Connection, line_id: int) -> list[sqlite3.Row]:
    try:
        return conn.execute(
            """
            SELECT * FROM line_versions
            WHERE line_id = ?
            ORDER BY is_current DESC, created_at DESC, id DESC
            """,
            (line_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def build_version_choices(line: sqlite3.Row, versions: list[sqlite3.Row]) -> list[dict]:
    choices: list[dict] = []
    seen = set()

    for v in versions:
        text = (v["text_content"] or "").strip()
        if not text:
            continue
        key = (v["provider"], v["model"], text)
        if key in seen:
            continue
        seen.add(key)
        choices.append(
            {
                "provider": v["provider"],
                "model": v["model"],
                "source_score": v["source_score"],
                "is_current": bool(v["is_current"]),
                "text_content": text,
                "origin": "line_versions",
            }
        )

    base_text = (line["tesseract_text"] or "").strip() if line else ""
    if base_text:
        if not any(item["text_content"] == base_text for item in choices):
            choices.append(
                {
                    "provider": "lines",
                    "model": "tesseract_text",
                    "source_score": None,
                    "is_current": True,
                    "text_content": base_text,
                    "origin": "lines.tesseract_text",
                }
            )

    return choices


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


def normalize_text(text: str):
    normalized_chars = []
    raw_map = []
    prev_space = False
    for idx, ch in enumerate(text):
        # collapse whitespace and strip accents for comparison
        if ch.isspace():
            if prev_space:
                continue
            ch = " "
            prev_space = True
        else:
            prev_space = False
        decomposed = unicodedata.normalize("NFKD", ch)
        stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
        for out_ch in stripped.lower():
            normalized_chars.append(out_ch)
            raw_map.append(idx)
    return "".join(normalized_chars), raw_map


def escape_and_highlight(text: str, spans: list[tuple[int, int, str]]) -> str:
    if not text:
        return ""
    pieces = []
    last = 0
    for start, end, cls in sorted(spans, key=lambda item: item[0]):
        if start > last:
            pieces.append(html.escape(text[last:start]))
        pieces.append(f'<span class="{cls}">{html.escape(text[start:end])}</span>')
        last = end
    if last < len(text):
        pieces.append(html.escape(text[last:]))
    return "".join(pieces)


def wrap_span(text: str, start: int, end: int, cls: str) -> str:
    return escape_and_highlight(text, [(start, end, cls)])


def find_match_view(raw_text: str, suggestion: str):
    suggestion = (suggestion or "").strip()
    if not raw_text:
        return None, ""
    if not suggestion:
        return None, html.escape(raw_text)

    exact_spans = []
    start = 0
    while True:
        idx = raw_text.find(suggestion, start)
        if idx < 0:
            break
        exact_spans.append((idx, idx + len(suggestion)))
        start = idx + 1
    if len(exact_spans) == 1:
        s, e = exact_spans[0]
        match = {
            "label": "exact",
            "badge": "high",
            "badge_text": "alta confiança",
            "detail": "ocorrência exata e única no txt raw",
            "span": (s, e),
            "mode": "exact",
        }
        return match, wrap_span(raw_text, s, e, "match-exact")

    normalized_suggestion, _ = normalize_text(suggestion)
    normalized_raw, raw_map = normalize_text(raw_text)
    if normalized_suggestion and normalized_raw:
        normalized_spans = []
        start = 0
        while True:
            idx = normalized_raw.find(normalized_suggestion, start)
            if idx < 0:
                break
            normalized_spans.append((idx, idx + len(normalized_suggestion)))
            start = idx + 1
        if len(normalized_spans) == 1:
            ns, ne = normalized_spans[0]
            rs = raw_map[ns]
            re = raw_map[ne - 1] + 1
            match = {
                "label": "normalized",
                "badge": "med",
                "badge_text": "média confiança",
                "detail": "encontrado após normalização de caixa/acentos/espaços",
                "span": (rs, re),
                "mode": "normalized",
            }
            return match, wrap_span(raw_text, rs, re, "match-normalized")

    best_ratio = 0.0
    window = max(20, min(len(suggestion) + 30, 240))
    for i in range(0, max(1, len(normalized_raw) - window + 1), max(1, window // 3)):
        chunk = normalized_raw[i : i + window]
        ratio = SequenceMatcher(None, normalized_suggestion, chunk).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
    if best_ratio >= 0.72:
        match = {
            "label": "fuzzy",
            "badge": "low",
            "badge_text": "baixa confiança",
            "detail": f"aproximação por similaridade; revisar com cuidado (score {best_ratio:.2f})",
            "span": None,
            "mode": "fuzzy",
        }
        return match, html.escape(raw_text)

    match = {
        "label": "none",
        "badge": "low",
        "badge_text": "sem match",
        "detail": "nenhuma correspondência confiável no txt raw",
        "span": None,
        "mode": "none",
    }
    return match, html.escape(raw_text)


@app.route("/")
def review():
    offset = int(request.args.get("offset", 0))
    filter_status = request.args.get("status", "inferred")
    filter_tesseract = request.args.get("tesseract", "all")
    filter_volume = request.args.get("volume", "")

    conn = get_conn()
    where = []
    params = []

    if filter_status != "all":
        where.append("status = ?")
        params.append(filter_status)
    if filter_volume:
        where.append("volume = ?")
        params.append(filter_volume)
    if filter_tesseract == "empty":
        where.append("TRIM(IFNULL(tesseract_text, '')) = ''")
    elif filter_tesseract == "filled":
        where.append("TRIM(IFNULL(tesseract_text, '')) <> ''")

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM lines {where_clause}", params
    ).fetchone()[0]

    line = conn.execute(
        f"SELECT * FROM lines {where_clause} ORDER BY agreement_score ASC LIMIT 1 OFFSET ?",
        params + [offset],
    ).fetchone()
    versions = get_line_versions(conn, line["id"]) if line else []
    version_choices = build_version_choices(line, versions) if line else []
    suggested_text = ""
    match_info = None
    raw_view = ""
    image_path = ""
    txt_path = ""

    line_b64 = ""
    if line and line["line_image"]:
        line_b64 = base64.b64encode(line["line_image"]).decode("utf-8")

    llm_b64 = ""
    text_context = None
    if line and line["image_path"]:
        try:
            img = None
            img_path = Path(line["image_path"])
            image_path = str(img_path)
            volume_path = img_path.parent.parent
            txt_path = txt_path_for_image(img_path, volume_path / "text")
            txt_path = str(txt_path)

            if Path(txt_path).exists():
                with open(txt_path, "r", encoding="utf-8") as f:
                    text_context = f.read()
                suggested_text = (
                    line["reviewed_text"]
                    or line["qwen_text"]
                    or (version_choices[0]["text_content"] if version_choices else "")
                    or line["tesseract_text"]
                    or ""
                ).strip()
                match_info, raw_view = find_match_view(text_context, suggested_text)
            else:
                suggested_text = (
                    line["reviewed_text"]
                    or line["qwen_text"]
                    or (version_choices[0]["text_content"] if version_choices else "")
                    or line["tesseract_text"]
                    or ""
                ).strip()
                raw_view = html.escape(text_context or "")

            # Match sample.py's geometry: deskew/rotate the whole page first, then
            # crop using the bbox returned by Tesseract on that transformed page.
            # Keep the review crop in color; do not apply thresholding here.
            if line["bbox"] and preprocess_image is not None:
                img = cv2.imread(line["image_path"])

            if img is not None:
                img_proc = deskew_like_sample_color(img)
                # parse bbox JSON
                try:
                    bbox = (
                        json.loads(line["bbox"])
                        if isinstance(line["bbox"], str)
                        else line["bbox"]
                    )
                    x = int(bbox.get("x", 0))
                    y = int(bbox.get("y", 0))
                    w = int(bbox.get("w", 0))
                    h = int(bbox.get("h", 0))
                    x1 = max(0, x - 2)
                    y1 = max(0, y - 2)
                    x2 = min(img_proc.shape[1], x + w + 2)
                    y2 = min(img_proc.shape[0], y + h + 2)
                    crop_proc = img_proc[y1:y2, x1:x2]
                    buf = io.BytesIO()
                    if len(crop_proc.shape) == 3:
                        pil_img = Image.fromarray(cv2.cvtColor(crop_proc, cv2.COLOR_BGR2RGB))
                    else:
                        pil_img = Image.fromarray(crop_proc)
                    pil_img.save(buf, format="PNG")
                    llm_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                except Exception:
                    llm_b64 = ""
        except Exception:
            llm_b64 = ""

    return render_template_string(
        TEMPLATE,
        line=line,
        line_b64=line_b64,
        llm_b64=llm_b64,
        offset=offset,
        total=total,
        stats=get_stats(),
        volumes=get_volumes(),
        filter_status=filter_status,
        filter_tesseract=filter_tesseract,
        filter_volume=filter_volume,
        text_context=text_context,
        versions=versions,
        version_choices=version_choices,
        suggested_text=suggested_text,
        match_info=match_info,
        raw_view=raw_view,
        image_path=image_path,
        txt_path=txt_path,
    )


@app.route("/submit", methods=["POST"])
def submit_review():
    line_id = int(request.form["line_id"])
    reviewed_text = request.form["reviewed_text"].strip()
    action = request.form["action"]  # approved | corrected | rejected
    next_offset = int(request.form.get("next_offset", 0))
    status = request.form.get("status", "inferred")
    volume = request.form.get("volume", "")

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """UPDATE lines
           SET reviewed_text=?, status=?, updated_at=datetime('now')
           WHERE id=?""",
        (reviewed_text, action, line_id),
    )
    conn.commit()

    return redirect(url_for("review", offset=next_offset, status=status, volume=volume))


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    global DB_PATH
    parser = argparse.ArgumentParser(description="Interface de revisão OCR")
    parser.add_argument("--db", default="ocr.db", help="Caminho do SQLite")
    parser.add_argument("--host", default="127.0.0.1", help="Host Flask")
    parser.add_argument("--port", type=int, default=5000, help="Porta Flask")
    args = parser.parse_args()

    DB_PATH = args.db
    print(f"[INFO] Abrindo DB: {DB_PATH}")
    print(f"[INFO] Interface em http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
