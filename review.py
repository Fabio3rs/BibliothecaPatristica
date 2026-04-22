#!/usr/bin/env python3
"""
review.py — interface Flask para revisão humana dos pares linha-imagem/texto

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
import json
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request, url_for

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

  .empty { padding: 40px; text-align: center; color: #555; }

  /* diff highlight */
  .diff-added   { background: rgba(80,200,80,0.15); }
  .diff-removed { background: rgba(200,80,80,0.15); text-decoration: line-through; }
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
  <a href="{{ url_for('review', offset=offset-1, status=filter_status, volume=filter_volume) }}">← anterior</a>
  {% endif %}
  <a href="{{ url_for('review', offset=offset+1, status=filter_status, volume=filter_volume) }}">próxima →</a>
  <span class="current-info">{{ offset + 1 }} / {{ total }}</span>
</div>

<div class="main">
  <!-- Painel esquerdo: imagem -->
  <div class="img-panel">
    <h2>Crop da linha</h2>
    <div class="line-crop">
      <img src="data:image/png;base64,{{ line_b64 }}" alt="linha">
    </div>
    <div class="meta">
      <b>{{ line.page_id }}</b> — linha {{ line.line_index }}<br>
      volume: {{ line.volume }} | lang: {{ line.detected_lang or '?' }}<br>
      bbox: {{ line.bbox }}<br>
      status: <span class="badge badge-{{ line.status }}">{{ line.status }}</span>
    </div>

    {% if line.tesseract_text %}
    <h2>Tesseract</h2>
    <div class="text-block clickable" onclick="copyTo(this)" title="Clica para copiar para editor">{{ line.tesseract_text }}</div>
    {% endif %}

    {% if line.qwen_text %}
    <h2>Qwen</h2>
    <div class="text-block clickable" onclick="copyTo(this)" title="Clica para copiar para editor">{{ line.qwen_text }}</div>
    {% endif %}

    {% if line.agreement_score is not none %}
    <h2>Agreement score: {{ "%.2f"|format(line.agreement_score) }}</h2>
    <div class="score-bar">
      <div class="score-fill" style="width:{{ (line.agreement_score*100)|int }}%; background: {{ '#4caf50' if line.agreement_score >= 0.8 else ('#ff9800' if line.agreement_score >= 0.5 else '#f44336') }};"></div>
    </div>
    {% endif %}
  </div>

  <!-- Painel direito: edição -->
  <div class="edit-panel">
    <h2>Texto revisado</h2>
    <textarea id="reviewed_text" rows="4">{{ line.reviewed_text or line.qwen_text or line.tesseract_text or '' }}</textarea>
    <p class="hint">Clique no texto Tesseract ou Qwen à esquerda para copiar para cá. Edite livremente.</p>

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
  <input type="hidden" name="volume"        value="{{ filter_volume }}">
</form>

<script>
function submitAction(action) {
  document.getElementById('form_reviewed_text').value = document.getElementById('reviewed_text').value;
  document.getElementById('form_action').value = action;
  document.getElementById('action-form').submit();
}

function skip() {
  window.location = "{{ url_for('review', offset=offset+1, status=filter_status, volume=filter_volume) }}";
}

function copyTo(el) {
  document.getElementById('reviewed_text').value = el.textContent.trim();
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


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_stats() -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) as n FROM lines GROUP BY status"
    ).fetchall()
    stats = {r["status"]: r["n"] for r in rows}
    return {
        "pending":   stats.get("pending", 0),
        "inferred":  stats.get("inferred", 0),
        "approved":  stats.get("approved", 0),
        "corrected": stats.get("corrected", 0),
        "rejected":  stats.get("rejected", 0),
        "error":     stats.get("error", 0),
    }


def get_volumes() -> list[str]:
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT volume FROM lines ORDER BY volume").fetchall()
    return [r["volume"] for r in rows]


@app.route("/")
def review():
    offset        = int(request.args.get("offset", 0))
    filter_status = request.args.get("status", "inferred")
    filter_volume = request.args.get("volume", "")

    conn  = get_conn()
    where = []
    params = []

    if filter_status != "all":
        where.append("status = ?")
        params.append(filter_status)
    if filter_volume:
        where.append("volume = ?")
        params.append(filter_volume)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM lines {where_clause}", params
    ).fetchone()[0]

    line = conn.execute(
        f"SELECT * FROM lines {where_clause} ORDER BY agreement_score ASC LIMIT 1 OFFSET ?",
        params + [offset],
    ).fetchone()

    line_b64 = ""
    if line and line["line_image"]:
        line_b64 = base64.b64encode(line["line_image"]).decode("utf-8")

    return render_template_string(
        TEMPLATE,
        line=line,
        line_b64=line_b64,
        offset=offset,
        total=total,
        stats=get_stats(),
        volumes=get_volumes(),
        filter_status=filter_status,
        filter_volume=filter_volume,
    )


@app.route("/submit", methods=["POST"])
def submit_review():
    line_id       = int(request.form["line_id"])
    reviewed_text = request.form["reviewed_text"].strip()
    action        = request.form["action"]   # approved | corrected | rejected
    next_offset   = int(request.form.get("next_offset", 0))
    status        = request.form.get("status", "inferred")
    volume        = request.form.get("volume", "")

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
    parser.add_argument("--db",   default="ocr.db",    help="Caminho do SQLite")
    parser.add_argument("--host", default="127.0.0.1", help="Host Flask")
    parser.add_argument("--port", type=int, default=5000, help="Porta Flask")
    args = parser.parse_args()

    DB_PATH = args.db
    print(f"[INFO] Abrindo DB: {DB_PATH}")
    print(f"[INFO] Interface em http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
