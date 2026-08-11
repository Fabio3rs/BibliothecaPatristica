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
import io
import json
import math
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import cv2
import numpy as np
from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for
from PIL import Image

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
    _ = border_size
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
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    return cv2.warpAffine(
        img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


BASE_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: monospace; background: #1a1a1a; color: #e0e0e0; }
  a { color: #9fc6ff; }
  .topbar {
    display: flex; align-items: center; gap: 16px;
    padding: 10px 20px; background: #111; border-bottom: 1px solid #333;
    flex-wrap: wrap;
  }
  .topbar h1 { font-size: 14px; color: #aaa; }
  .topbar .stats { font-size: 12px; color: #666; }
  .topbar input, .topbar select, .topbar button,
  .toolbar input, .toolbar select, .toolbar button {
    background: #222; color: #ccc; border: 1px solid #444;
    padding: 6px 8px; border-radius: 4px; font-size: 12px;
  }
  .topbar button, .toolbar button {
    background: #333; color: #ccc; border-color: #555; cursor: pointer;
  }
  .topbar button:hover, .toolbar button:hover { background: #444; }
  .toolbar {
    display: flex; gap: 10px; padding: 10px 20px; background: #161616;
    border-bottom: 1px solid #2a2a2a; flex-wrap: wrap; align-items: center;
  }
  .toolbar a {
    color: #888; text-decoration: none; font-size: 12px; padding: 5px 9px;
    border-radius: 3px; border: 1px solid #333;
  }
  .toolbar a:hover { background: #2a2a2a; color: #ccc; }
  .toolbar .spacer { margin-left: auto; }
  .nav-bar {
    display: flex; gap: 8px; padding: 8px 20px; background: #161616;
    border-bottom: 1px solid #2a2a2a; align-items: center; flex-wrap: wrap;
  }
  .nav-bar a { color: #888; text-decoration: none; font-size: 12px; padding: 3px 8px; border-radius: 3px; border: 1px solid #333; }
  .nav-bar a:hover { background: #2a2a2a; color: #ccc; }
  .nav-bar .current-info { font-size: 12px; color: #555; margin-left: auto; }
  .main { display: flex; min-height: calc(100vh - 140px); }
  .img-panel {
    flex: 1; min-width: 0; padding: 20px; display: flex; flex-direction: column;
    gap: 12px; overflow-y: auto; border-right: 1px solid #2a2a2a;
  }
  .img-panel h2, .edit-panel h2, .search-wrap h2 {
    font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 1px;
  }
  .line-crop {
    background: #fff; padding: 8px; border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
    max-height: 120px; overflow: hidden;
  }
  .line-crop img { max-width: 100%; max-height: 100px; image-rendering: pixelated; }
  .meta { font-size: 11px; color: #555; line-height: 1.5; }
  .path-meta {
    font-size: 11px; color: #777; background: #151515; border: 1px solid #2a2a2a;
    border-radius: 4px; padding: 8px 10px; line-height: 1.5; word-break: break-all;
  }
  .path-meta b { color: #aaa; }
  .edit-panel {
    width: 420px; flex-shrink: 0; padding: 20px; display: flex;
    flex-direction: column; gap: 14px; overflow-y: auto;
  }
  .score-bar { height: 4px; border-radius: 2px; background: #333; margin-bottom: 4px; }
  .score-fill { height: 100%; border-radius: 2px; }
  .text-block {
    background: #222; border: 1px solid #333; border-radius: 4px;
    padding: 10px; font-size: 13px; line-height: 1.6; color: #ccc;
    white-space: pre-wrap; word-break: break-word; min-height: 40px;
  }
  .text-block.clickable { cursor: pointer; }
  .text-block.clickable:hover { border-color: #555; background: #282828; }
  .muted { color: #666; }
  .raw-view {
    background: #191919; border: 1px solid #333; border-radius: 6px; padding: 12px;
    font-size: 12px; line-height: 1.55; color: #cfcfcf; white-space: pre-wrap;
    word-break: break-word; max-height: 420px; overflow: auto;
  }
  .raw-view .match-exact, mark {
    background: rgba(92, 184, 92, 0.22); color: #efffee; border-radius: 3px; padding: 0 2px;
  }
  .raw-view .match-normalized {
    background: rgba(240, 173, 78, 0.22); color: #fff6df; border-radius: 3px; padding: 0 2px;
  }
  .raw-view .match-fuzzy {
    background: rgba(217, 83, 79, 0.18); color: #ffe2e2; border-radius: 3px; padding: 0 2px;
  }
  .diff-view {
    background: #181818; border: 1px dashed #333; border-radius: 4px; padding: 8px;
    font-size: 12px; line-height: 1.5; color: #cfcfcf;
  }
  .diff-line { margin-top: 6px; }
  .diff-label { color: #888; font-weight: bold; margin-right: 8px; }
  .diff-add { background: rgba(92, 184, 92, 0.24); color: #e9ffe9; border-radius: 3px; }
  .diff-del { background: rgba(217, 83, 79, 0.22); color: #ffe5e5; border-radius: 3px; text-decoration: line-through; }
  .diff-repl { background: rgba(240, 173, 78, 0.24); color: #fff4d6; border-radius: 3px; }
  .match-badge, .pill, .badge {
    display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 11px; font-weight: bold;
  }
  .match-high { background: #1d4f1d; color: #a6e3a6; }
  .match-med { background: #5a4500; color: #ffd76a; }
  .match-low { background: #5a1f1f; color: #ff9d9d; }
  .consensus-panel, .legacy-panel {
    background: #1b1b1b; border: 1px solid #333; border-radius: 6px; padding: 12px;
  }
  .consensus-summary {
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 10px;
    font-size: 12px; color: #bbb;
  }
  .consensus-list { display: flex; flex-direction: column; gap: 10px; }
  .consensus-item {
    background: #181818; border: 1px solid #2f2f2f; border-radius: 6px; padding: 10px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .consensus-item.is-top { border-color: #4d5d2f; box-shadow: inset 0 0 0 1px rgba(157, 219, 157, 0.15); }
  .consensus-head, .consensus-meta, .source-list {
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
  }
  .source-list { font-size: 11px; color: #999; }
  .legacy-panel { font-size: 12px; color: #aaa; }
  .legacy-panel ul { margin-left: 18px; }
  .badge-legacy { background: #3b3342; color: #d2b5ff; }
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
  .btn {
    padding: 8px 18px; border-radius: 5px; border: none; cursor: pointer;
    font-size: 13px; font-weight: bold; transition: opacity .15s;
  }
  .btn:hover { opacity: .85; }
  .btn-approve { background: #2d6a2d; color: #9ddb9d; }
  .btn-correct { background: #4a3a00; color: #f0c040; }
  .btn-reject { background: #5a1a1a; color: #e08080; }
  .btn-skip, .btn-link { background: #333; color: #888; }
  .hint { font-size: 11px; color: #555; }
  .badge-pending { background: #333; color: #888; }
  .badge-inferred { background: #1a3a5a; color: #6ab0f5; }
  .badge-approved { background: #1a4a1a; color: #6adb6a; }
  .badge-corrected { background: #3a2a00; color: #f0c040; }
  .badge-rejected { background: #3a1010; color: #e08080; }
  .badge-error, .badge-processing { background: #4a1a1a; color: #ff6060; }
  .badge-empty { background: #4a2f1a; color: #ffbf80; }
  .empty { padding: 40px 20px; text-align: center; color: #555; }
  .search-wrap { padding: 20px; display: flex; flex-direction: column; gap: 16px; }
  .search-results { display: flex; flex-direction: column; gap: 12px; }
  .result-card {
    background: #1d1d1d; border: 1px solid #333; border-radius: 6px; padding: 12px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .result-head, .result-meta, .pager {
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
  }
  .result-head a { font-size: 14px; font-weight: 700; }
  .result-meta { font-size: 12px; color: #888; }
  .pill { background: #242424; color: #bbb; border: 1px solid #333; }
  .snippet { background: #171717; border-radius: 4px; padding: 10px; border: 1px solid #2d2d2d; line-height: 1.6; }
  .pager a {
    color: #888; text-decoration: none; font-size: 12px; padding: 5px 9px; border-radius: 3px; border: 1px solid #333;
  }
  .pager a:hover { background: #2a2a2a; color: #ccc; }
  @media (max-width: 1100px) {
    .main { flex-direction: column; min-height: auto; }
    .img-panel { border-right: none; border-bottom: 1px solid #2a2a2a; max-height: none; }
    .edit-panel { width: 100%; }
  }
</style>
"""


REVIEW_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OCR Review — {{ title }}</title>
""" + BASE_STYLE + """
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

  <form method="get" action="{{ url_for('go_to_line') }}" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    <input type="number" min="1" name="line_id" placeholder="Ir para ID" required>
    <button type="submit">Abrir ID</button>
  </form>

  <a href="{{ url_for('search_lines') }}">Busca</a>
</div>

<div class="toolbar">
  <a href="{{ url_for('review') }}">Fila</a>
  {% if line %}
  <a href="{{ url_for('line_detail', line_id=line.id) }}">Análise direta</a>
  {% endif %}
  <a href="{{ url_for('search_lines') }}">Pesquisar `lines`</a>
  {% if is_queue %}
  <form method="get" action="{{ url_for('review') }}" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    <select name="status" onchange="this.form.submit()">
      <option value="inferred" {% if filter_status=='inferred' %}selected{% endif %}>inferred</option>
      <option value="pending" {% if filter_status=='pending' %}selected{% endif %}>pending</option>
      <option value="approved" {% if filter_status=='approved' %}selected{% endif %}>approved</option>
      <option value="corrected" {% if filter_status=='corrected' %}selected{% endif %}>corrected</option>
      <option value="rejected" {% if filter_status=='rejected' %}selected{% endif %}>rejected</option>
      <option value="all" {% if filter_status=='all' %}selected{% endif %}>all</option>
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
  {% else %}
  <div class="spacer"></div>
  {% endif %}
</div>

{% if line and is_queue %}
<div class="nav-bar">
  {% if offset > 0 %}
  <a href="{{ url_for('review', offset=offset-1, status=filter_status, tesseract=filter_tesseract, volume=filter_volume) }}">← anterior</a>
  {% endif %}
  <a href="{{ url_for('review', offset=offset+1, status=filter_status, tesseract=filter_tesseract, volume=filter_volume) }}">próxima →</a>
  <span class="current-info">{{ offset + 1 }} / {{ total }}</span>
</div>
{% elif line %}
<div class="nav-bar">
  <a href="{{ url_for('review') }}">Voltar para fila</a>
  <a href="{{ url_for('search_lines') }}">Abrir busca</a>
  <span class="current-info">ID {{ line.id }}</span>
</div>
{% endif %}

{% if line %}
<div class="main">
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
      <b>{{ line.page_id }}</b> — linha {{ line.line_index }} — ID {{ line.id }}<br>
      volume: {{ line.volume }} | lang: {{ line.detected_lang or '?' }}<br>
      bbox: {{ line.bbox }}<br>
      status: <span class="badge badge-{{ line.status }}">{{ line.status }}</span>
      {% if not (line.tesseract_text or '').strip() %}
      <span class="badge badge-empty">tesseract vazio</span>
      {% endif %}
      {% if consensus.total_engines %}
      <span class="match-badge match-{{ consensus.badge }}">{{ consensus.label }}</span>
      {% endif %}
    </div>
    <div class="path-meta">
      <div><b>Imagem real:</b> {{ image_path or 'indisponível' }}</div>
      <div><b>Texto raw:</b> {{ txt_path or 'indisponível' }}</div>
    </div>

    {% if consensus.groups %}
    <h2>Consenso entre engines</h2>
    <div class="consensus-panel">
      <div class="consensus-summary">
        <span class="match-badge match-{{ consensus.badge }}">{{ consensus.label }}</span>
        <span>{{ consensus.detail }}</span>
        <span>score {{ "%.3f"|format(consensus.consensus_score) }}</span>
        <span>{{ consensus.total_versions }} versão(ões)</span>
      </div>
      <div class="consensus-list">
        {% for group in consensus.groups %}
        <div class="consensus-item {% if loop.first %}is-top{% endif %}">
          <div class="consensus-head">
            <span class="match-badge match-{{ 'high' if loop.first else group.badge }}">
              {{ group.engine_count }} engine(s)
            </span>
            <span class="pill">{{ group.version_count }} versão(ões)</span>
            {% if group.run_count %}
            <span class="pill">{{ group.run_count }} run(s)</span>
            {% endif %}
            {% if group.best_source_score is not none %}
            <span class="pill">best score {{ "%.2f"|format(group.best_source_score) }}</span>
            {% endif %}
          </div>
          <div class="text-block clickable" onclick="copyTextToEditor(this.dataset.text)" data-text="{{ group.text_content|e }}" title="Clica para copiar para editor">{{ group.text_content }}</div>
          <div class="source-list">
            {% for source in group.sources %}
            <span class="pill">{{ source.provider }}/{{ source.model }}{% if source.legacy %} legado{% endif %}</span>
            {% endfor %}
          </div>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endif %}

    {% if consensus.legacy_sources %}
    <h2>Campos legados</h2>
    <div class="legacy-panel">
      <span class="badge badge-legacy">snapshot legado</span>
      <span>`lines.qwen_text`, `lines.tesseract_text` e `agreement_score` podem estar defasados em relação a `line_versions`.</span>
    </div>
    {% endif %}

    {% if line.tesseract_text %}
    <h2>Tesseract <span class="badge badge-legacy">legado</span></h2>
    <div class="text-block clickable" onclick="copyTo(this)" title="Clica para copiar para editor">{{ line.tesseract_text }}</div>
    {% if tesseract_diff_html %}
    <div class="diff-view">{{ tesseract_diff_html|safe }}</div>
    {% endif %}
    {% endif %}

    {% if line.qwen_text %}
    <h2>Qwen <span class="badge badge-legacy">legado</span></h2>
    <div class="text-block clickable" onclick="copyTo(this)" title="Clica para copiar para editor">{{ line.qwen_text }}</div>
    {% if qwen_diff_html %}
    <div class="diff-view">{{ qwen_diff_html|safe }}</div>
    {% endif %}
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
        {% if v.diff_html %}
        <div class="diff-view">{{ v.diff_html|safe }}</div>
        {% endif %}
      </div>
      {% endfor %}
    </div>
    {% endif %}

    {% if line.agreement_score is not none %}
    <h2>Agreement score <span class="badge badge-legacy">legado</span>: {{ "%.2f"|format(line.agreement_score) }}</h2>
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

  <div class="edit-panel">
    <h2>Texto revisado</h2>
    <textarea id="reviewed_text" rows="4">{{ line.reviewed_text or line.qwen_text or line.tesseract_text or '' }}</textarea>
    <textarea id="suggested_text" style="display:none;">{{ suggested_text }}</textarea>
    <p class="hint">Clique no consenso, Tesseract ou Qwen à esquerda para copiar para cá. Edite livremente.</p>

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
      {% if consensus.winning_engine_count > 0 and consensus.winning_text %}
      <button class="btn btn-skip" type="button" onclick="applyConsensus()">Usar consenso</button>
      {% endif %}
    </div>
    {% endif %}

    <div class="actions">
      <button class="btn btn-approve" onclick="submitAction('approved')">✓ Aprovar</button>
      <button class="btn btn-correct" onclick="submitAction('corrected')">✎ Corrigir</button>
      <button class="btn btn-reject" onclick="submitAction('rejected')">✗ Rejeitar</button>
      {% if is_queue %}
      <button class="btn btn-skip" onclick="skip()">→ Pular</button>
      {% endif %}
    </div>

    <p class="hint">Atalhos: <b>A</b> aprovar · <b>R</b> rejeitar{% if is_queue %} · <b>→</b> pular{% endif %}</p>

    {% if line.reviewed_text %}
    <h2>Revisão anterior</h2>
    <div class="text-block">{{ line.reviewed_text }}</div>
    {% endif %}
  </div>
</div>
{% else %}
<div class="empty">
  <p>{{ empty_message }}</p>
  <p style="margin-top:12px;">
    <a href="{{ url_for('review') }}">Abrir fila</a> |
    <a href="{{ url_for('search_lines') }}">Abrir busca</a>
  </p>
</div>
{% endif %}

<form id="action-form" method="post" action="{{ url_for('submit_review') }}" style="display:none;">
  <input type="hidden" name="line_id" value="{{ line.id if line else '' }}">
  <input type="hidden" name="reviewed_text" id="form_reviewed_text">
  <input type="hidden" name="action" id="form_action">
  <input type="hidden" name="next_offset" value="{{ offset + 1 if offset is not none else 0 }}">
  <input type="hidden" name="status" value="{{ filter_status }}">
  <input type="hidden" name="tesseract" value="{{ filter_tesseract }}">
  <input type="hidden" name="volume" value="{{ filter_volume }}">
  <input type="hidden" name="return_to" value="{{ return_to }}">
</form>

<script>
function submitAction(action) {
  document.getElementById('form_reviewed_text').value = document.getElementById('reviewed_text').value;
  document.getElementById('form_action').value = action;
  document.getElementById('action-form').submit();
}
function skip() {
  if (!{{ 'true' if is_queue else 'false' }}) return;
  window.location = "{{ skip_url }}";
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
function copyTextToEditor(text) {
  document.getElementById('reviewed_text').value = text;
  document.getElementById('reviewed_text').focus();
}
function applyConsensus() {
  const text = {{ consensus.winning_text|tojson }};
  if (!text) return;
  copyTextToEditor(text);
}
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'a' || e.key === 'A') submitAction('approved');
  if (e.key === 'r' || e.key === 'R') submitAction('rejected');
  if ({{ 'true' if is_queue else 'false' }} && e.key === 'ArrowRight') skip();
});
</script>
</body>
</html>
"""


SEARCH_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OCR Review — Busca</title>
""" + BASE_STYLE + """
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
  <form method="get" action="{{ url_for('go_to_line') }}" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    <input type="number" min="1" name="line_id" placeholder="Ir para ID" required>
    <button type="submit">Abrir ID</button>
  </form>
  <a href="{{ url_for('review') }}">Fila</a>
</div>

<div class="toolbar">
  <form method="get" action="{{ url_for('search_lines') }}" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap; width:100%;">
    <input type="text" name="q" value="{{ q }}" placeholder="Pesquisar texto em reviewed / qwen / tesseract" style="min-width:260px;flex:1;">
    <input type="number" min="1" name="line_id" value="{{ line_id or '' }}" placeholder="ID">
    <select name="status">
      <option value="all" {% if filter_status=='all' %}selected{% endif %}>status all</option>
      <option value="inferred" {% if filter_status=='inferred' %}selected{% endif %}>inferred</option>
      <option value="pending" {% if filter_status=='pending' %}selected{% endif %}>pending</option>
      <option value="approved" {% if filter_status=='approved' %}selected{% endif %}>approved</option>
      <option value="corrected" {% if filter_status=='corrected' %}selected{% endif %}>corrected</option>
      <option value="rejected" {% if filter_status=='rejected' %}selected{% endif %}>rejected</option>
    </select>
    <select name="tesseract">
      <option value="all" {% if filter_tesseract=='all' %}selected{% endif %}>tesseract all</option>
      <option value="empty" {% if filter_tesseract=='empty' %}selected{% endif %}>tesseract empty</option>
      <option value="filled" {% if filter_tesseract=='filled' %}selected{% endif %}>tesseract filled</option>
    </select>
    <select name="volume">
      <option value="">todos volumes</option>
      {% for v in volumes %}
      <option value="{{ v }}" {% if filter_volume==v %}selected{% endif %}>{{ v }}</option>
      {% endfor %}
    </select>
    <button type="submit">Pesquisar</button>
  </form>
</div>

<div class="search-wrap">
  <h2>Busca em `lines`</h2>
  {% if message %}
  <div class="text-block">{{ message }}</div>
  {% endif %}

  {% if results %}
  <div class="result-meta">
    <span>{{ total }} resultado(s)</span>
    <span>página {{ page }}</span>
  </div>
  <div class="search-results">
    {% for row in results %}
    <div class="result-card">
      <div class="result-head">
        <a href="{{ url_for('line_detail', line_id=row.id) }}">ID {{ row.id }}</a>
        <span class="pill">{{ row.page_id }}</span>
        <span class="pill">{{ row.volume }}</span>
        <span class="badge badge-{{ row.status }}">{{ row.status }}</span>
        {% if row.consensus_total_engines %}
        <span class="match-badge match-{{ row.consensus_badge }}">{{ row.consensus_label }}</span>
        <span class="pill">{{ row.consensus_engine_count }}/{{ row.consensus_total_engines }} engines</span>
        {% endif %}
        {% if row.tesseract_empty %}
        <span class="badge badge-empty">tesseract vazio</span>
        {% endif %}
      </div>
      <div class="result-meta">
        <span>linha {{ row.line_index }}</span>
        <span>agreement {{ "%.2f"|format(row.agreement_score or 0) if row.agreement_score is not none else "?" }}</span>
        {% if row.consensus_total_engines %}
        <span>consenso {{ "%.3f"|format(row.consensus_score) }}</span>
        {% endif %}
      </div>
      <div class="snippet">{{ row.snippet|safe }}</div>
      <div class="actions">
        <a class="btn btn-link" href="{{ url_for('line_detail', line_id=row.id) }}">Analisar</a>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if total > per_page %}
  <div class="pager">
    {% if page > 1 %}
    <a href="{{ prev_url }}">← página anterior</a>
    {% endif %}
    {% if page * per_page < total %}
    <a href="{{ next_url }}">próxima página →</a>
    {% endif %}
  </div>
  {% endif %}
</div>
</body>
</html>
"""


app = Flask(__name__)
DB_PATH = "ocr.db"
SEARCH_PAGE_SIZE = 50


def get_conn(path: str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_search_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS lines_fts USING fts5(
            line_id UNINDEXED,
            page_id,
            volume,
            search_text,
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS lines_fts_ai AFTER INSERT ON lines BEGIN
            INSERT INTO lines_fts(rowid, line_id, page_id, volume, search_text)
            VALUES (
                new.id,
                new.id,
                COALESCE(new.page_id, ''),
                COALESCE(new.volume, ''),
                trim(
                    COALESCE(new.reviewed_text, '') || ' ' ||
                    COALESCE(new.qwen_text, '') || ' ' ||
                    COALESCE(new.tesseract_text, '')
                )
            );
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS lines_fts_au
        AFTER UPDATE OF page_id, volume, reviewed_text, qwen_text, tesseract_text
        ON lines BEGIN
            DELETE FROM lines_fts WHERE rowid = old.id;
            INSERT INTO lines_fts(rowid, line_id, page_id, volume, search_text)
            VALUES (
                new.id,
                new.id,
                COALESCE(new.page_id, ''),
                COALESCE(new.volume, ''),
                trim(
                    COALESCE(new.reviewed_text, '') || ' ' ||
                    COALESCE(new.qwen_text, '') || ' ' ||
                    COALESCE(new.tesseract_text, '')
                )
            );
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS lines_fts_ad AFTER DELETE ON lines BEGIN
            DELETE FROM lines_fts WHERE rowid = old.id;
        END
        """
    )

    lines_count = conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
    fts_count = conn.execute("SELECT COUNT(*) FROM lines_fts").fetchone()[0]
    if lines_count and fts_count != lines_count:
        conn.execute("DELETE FROM lines_fts")
        conn.execute(
            """
            INSERT INTO lines_fts(rowid, line_id, page_id, volume, search_text)
            SELECT
                id,
                id,
                COALESCE(page_id, ''),
                COALESCE(volume, ''),
                trim(
                    COALESCE(reviewed_text, '') || ' ' ||
                    COALESCE(qwen_text, '') || ' ' ||
                    COALESCE(tesseract_text, '')
                )
            FROM lines
            """
        )
    conn.commit()


def get_stats() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT status, COUNT(*) as n FROM lines GROUP BY status").fetchall()
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


def summarize_consensus_label(engine_count: int, total_engines: int, tie_groups: int) -> tuple[str, str, str]:
    if engine_count <= 0 or total_engines <= 0:
        return ("sem consenso", "low", "sem versões comparáveis")
    if tie_groups and tie_groups > 1:
        return ("conflito", "low", f"empate entre {tie_groups} grupos")
    if engine_count == total_engines:
        return ("alta confiança", "high", f"{engine_count}/{total_engines} engines concordam")
    if engine_count >= max(2, math.ceil(total_engines * 0.6)):
        return ("média confiança", "med", f"{engine_count}/{total_engines} engines concordam")
    return ("baixa confiança", "low", f"{engine_count}/{total_engines} engines concordam")


def render_colored_diff_html(reference_text: str, candidate_text: str) -> str:
    reference_text = (reference_text or "").strip()
    candidate_text = (candidate_text or "").strip()
    if not reference_text or not candidate_text or reference_text == candidate_text:
        return ""

    ref_parts = []
    cand_parts = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, reference_text, candidate_text).get_opcodes():
        ref_chunk = html.escape(reference_text[i1:i2])
        cand_chunk = html.escape(candidate_text[j1:j2])
        if op == "equal":
            ref_parts.append(ref_chunk)
            cand_parts.append(cand_chunk)
        elif op == "delete":
            ref_parts.append(f'<span class="diff-del">{ref_chunk}</span>')
        elif op == "insert":
            cand_parts.append(f'<span class="diff-add">{cand_chunk}</span>')
        else:
            ref_parts.append(f'<span class="diff-repl">{ref_chunk}</span>')
            cand_parts.append(f'<span class="diff-repl">{cand_chunk}</span>')

    return (
        '<div class="diff-line"><span class="diff-label">consenso</span>'
        + "".join(ref_parts)
        + '</div><div class="diff-line"><span class="diff-label">fonte</span>'
        + "".join(cand_parts)
        + "</div>"
    )


def build_consensus_summary(
    line: Optional[sqlite3.Row], versions: list[sqlite3.Row], consensus_row: Optional[sqlite3.Row] = None
) -> dict:
    groups: dict[str, dict] = {}
    legacy_sources: list[dict[str, str]] = []

    for version in versions:
        text = (version["text_content"] or "").strip()
        if not text:
            continue
        group = groups.setdefault(
            text,
            {
                "text_content": text,
                "engine_keys": set(),
                "version_count": 0,
                "best_source_score": None,
                "sources": [],
                "is_current": False,
                "run_ids": set(),
            },
        )
        engine_key = f'{version["provider"]}|{version["model"]}'
        group["engine_keys"].add(engine_key)
        group["version_count"] += 1
        if version["source_score"] is not None:
            current_best = group["best_source_score"]
            group["best_source_score"] = (
                version["source_score"]
                if current_best is None
                else max(current_best, version["source_score"])
            )
        group["is_current"] = group["is_current"] or bool(version["is_current"])
        run_id = (version["run_id"] or "").strip()
        if run_id:
            group["run_ids"].add(run_id)
        group["sources"].append(
            {
                "provider": version["provider"],
                "model": version["model"],
                "source_score": version["source_score"],
                "is_current": bool(version["is_current"]),
                "run_id": run_id,
            }
        )

    for field_name, provider, model in (
        ("qwen_text", "lines", "qwen_text"),
        ("tesseract_text", "lines", "tesseract_text"),
    ):
        text = ((line[field_name] or "").strip() if line else "")
        if not text:
            continue
        legacy_sources.append({"field": field_name, "provider": provider, "model": model, "text": text})
        if text in groups:
            groups[text]["sources"].append(
                {
                    "provider": provider,
                    "model": model,
                    "source_score": None,
                    "is_current": True,
                    "run_id": "",
                    "legacy": True,
                }
            )

    consensus_groups = []
    total_engines = len({engine for group in groups.values() for engine in group["engine_keys"]})
    total_versions = sum(group["version_count"] for group in groups.values())
    for group in groups.values():
        engine_count = len(group["engine_keys"])
        label, badge, detail = summarize_consensus_label(engine_count, max(total_engines, engine_count), 0)
        consensus_groups.append(
            {
                "text_content": group["text_content"],
                "engine_count": engine_count,
                "version_count": group["version_count"],
                "best_source_score": group["best_source_score"],
                "sources": sorted(
                    group["sources"],
                    key=lambda item: (
                        bool(item.get("legacy", False)),
                        item["provider"],
                        item["model"],
                    ),
                ),
                "is_current": group["is_current"],
                "run_count": len(group["run_ids"]),
                "label": label,
                "badge": badge,
                "detail": detail,
            }
        )

    consensus_groups.sort(
        key=lambda item: (
            -item["engine_count"],
            -(item["best_source_score"] if item["best_source_score"] is not None else -1.0),
            -len(item["text_content"]),
            item["text_content"],
        )
    )

    if consensus_groups:
        winning_engine_count = consensus_groups[0]["engine_count"]
        tie_groups = sum(1 for group in consensus_groups if group["engine_count"] == winning_engine_count)
    else:
        winning_engine_count = 0
        tie_groups = 0

    consensus_keys = set(consensus_row.keys()) if consensus_row is not None and hasattr(consensus_row, "keys") else set()
    if consensus_row is not None and "total_engines" in consensus_keys:
        total_engines = consensus_row["total_engines"] or total_engines
        total_versions = consensus_row["total_versions"] or total_versions
        winning_engine_count = consensus_row["consensus_engine_count"] or winning_engine_count
        tie_groups = consensus_row["consensus_tie_groups"] or tie_groups

    summary_label, summary_badge, summary_detail = summarize_consensus_label(
        winning_engine_count,
        total_engines,
        tie_groups,
    )
    consensus_ratio = (winning_engine_count / total_engines) if total_engines else 0.0
    consensus_score = ((winning_engine_count * winning_engine_count) / total_engines) if total_engines else 0.0
    consensus_text = consensus_groups[0]["text_content"] if consensus_groups else ""

    return {
        "groups": consensus_groups,
        "winning_text": consensus_text,
        "winning_engine_count": winning_engine_count,
        "total_engines": total_engines,
        "total_versions": total_versions,
        "tie_groups": tie_groups,
        "consensus_ratio": consensus_ratio,
        "consensus_score": consensus_score,
        "label": summary_label,
        "badge": summary_badge,
        "detail": summary_detail,
        "legacy_sources": legacy_sources,
    }


def build_version_choices(line: sqlite3.Row, versions: list[sqlite3.Row], consensus_text: str = "") -> list[dict]:
    choices: list[dict] = []
    seen = set()

    for version in versions:
        text = (version["text_content"] or "").strip()
        if not text:
            continue
        key = (version["provider"], version["model"], text)
        if key in seen:
            continue
        seen.add(key)
        choices.append(
            {
                "provider": version["provider"],
                "model": version["model"],
                "source_score": version["source_score"],
                "is_current": bool(version["is_current"]),
                "text_content": text,
                "origin": "line_versions",
                "diff_html": render_colored_diff_html(consensus_text, text),
            }
        )

    base_text = (line["tesseract_text"] or "").strip() if line else ""
    if base_text and not any(item["text_content"] == base_text for item in choices):
        choices.append(
            {
                "provider": "lines",
                "model": "tesseract_text",
                "source_score": None,
                "is_current": True,
                "text_content": base_text,
                "origin": "lines.tesseract_text",
                "diff_html": render_colored_diff_html(consensus_text, base_text),
            }
        )

    return choices


def parse_page_num_from_filename(image_path: Path) -> Optional[int]:
    match = re.search(r"-([0-9]{1,4})$", image_path.stem)
    return int(match.group(1)) if match else None


def txt_path_for_image(img_path: Path, txt_dir: Path) -> Path:
    stable = txt_dir / (img_path.stem + ".txt")
    if stable.exists():
        return stable

    page_num = parse_page_num_from_filename(img_path)
    if page_num is not None:
        padded = sorted(txt_dir.glob(f"*-{page_num:03d}.txt"))
        if padded:
            return padded[0]
        plain = sorted(txt_dir.glob(f"*-{page_num}.txt"))
        if plain:
            return plain[0]
    return stable


def normalize_text(text: str) -> tuple[str, list[int]]:
    normalized_chars = []
    raw_map = []
    prev_space = False
    for idx, ch in enumerate(text):
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
        span_start, span_end = exact_spans[0]
        match = {
            "label": "exact",
            "badge": "high",
            "badge_text": "alta confiança",
            "detail": "ocorrência exata e única no txt raw",
            "span": (span_start, span_end),
            "mode": "exact",
        }
        return match, wrap_span(raw_text, span_start, span_end, "match-exact")

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
            norm_start, norm_end = normalized_spans[0]
            raw_start = raw_map[norm_start]
            raw_end = raw_map[norm_end - 1] + 1
            match = {
                "label": "normalized",
                "badge": "med",
                "badge_text": "média confiança",
                "detail": "encontrado após normalização de caixa/acentos/espaços",
                "span": (raw_start, raw_end),
                "mode": "normalized",
            }
            return match, wrap_span(raw_text, raw_start, raw_end, "match-normalized")

    best_ratio = 0.0
    window = max(20, min(len(suggestion) + 30, 240))
    for idx in range(0, max(1, len(normalized_raw) - window + 1), max(1, window // 3)):
        chunk = normalized_raw[idx : idx + window]
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


def parse_int(value: Optional[str], default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value or default))
    except (TypeError, ValueError):
        return default


def build_line_filters(
    filter_status: str, filter_tesseract: str, filter_volume: str, line_id: Optional[int] = None
) -> tuple[list[str], list[object]]:
    where: list[str] = []
    params: list[object] = []
    if filter_status != "all":
        where.append("status = ?")
        params.append(filter_status)
    if filter_volume:
        where.append("lines.volume = ?")
        params.append(filter_volume)
    if filter_tesseract == "empty":
        where.append("TRIM(IFNULL(tesseract_text, '')) = ''")
    elif filter_tesseract == "filled":
        where.append("TRIM(IFNULL(tesseract_text, '')) <> ''")
    if line_id is not None:
        where.append("id = ?")
        params.append(line_id)
    return where, params


def build_where_clause(where: list[str]) -> str:
    return f"WHERE {' AND '.join(where)}" if where else ""


def fetch_line_consensus_map(conn: sqlite3.Connection, line_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not line_ids:
        return {}
    placeholders = ",".join("?" for _ in line_ids)
    rows = conn.execute(
        f"""
        WITH version_groups AS (
            SELECT
                line_id,
                TRIM(text_content) AS text_content,
                COUNT(DISTINCT provider || '|' || model) AS engine_count,
                COUNT(*) AS version_count,
                MAX(source_score) AS best_source_score
            FROM line_versions
            WHERE line_id IN ({placeholders})
              AND TRIM(COALESCE(text_content, '')) <> ''
            GROUP BY line_id, TRIM(text_content)
        ),
        ranked_groups AS (
            SELECT
                line_id,
                text_content,
                engine_count,
                version_count,
                best_source_score,
                COUNT(*) OVER (PARTITION BY line_id) AS group_count,
                SUM(engine_count) OVER (PARTITION BY line_id) AS total_engines,
                SUM(version_count) OVER (PARTITION BY line_id) AS total_versions,
                COUNT(*) OVER (PARTITION BY line_id, engine_count) AS same_engine_count_groups,
                ROW_NUMBER() OVER (
                    PARTITION BY line_id
                    ORDER BY
                        engine_count DESC,
                        COALESCE(best_source_score, -1.0) DESC,
                        LENGTH(text_content) DESC,
                        text_content ASC
                ) AS rn
            FROM version_groups
        ),
        line_consensus AS (
            SELECT
                line_id,
                text_content AS consensus_text,
                engine_count AS consensus_engine_count,
                version_count AS consensus_version_count,
                total_engines,
                total_versions,
                group_count AS consensus_group_count,
                same_engine_count_groups AS consensus_tie_groups,
                CASE
                    WHEN total_engines > 0 THEN CAST(engine_count AS REAL) / total_engines
                    ELSE 0.0
                END AS consensus_ratio,
                CASE
                    WHEN total_engines > 0 THEN
                        (CAST(engine_count * engine_count AS REAL) / total_engines)
                    ELSE 0.0
                END AS consensus_score,
                best_source_score AS consensus_best_source_score
            FROM ranked_groups
            WHERE rn = 1
        )
        SELECT *
        FROM line_consensus
        """,
        line_ids,
    ).fetchall()
    return {row["line_id"]: row for row in rows}


def fetch_queue_line(
    conn: sqlite3.Connection, offset: int, filter_status: str, filter_tesseract: str, filter_volume: str
) -> tuple[Optional[sqlite3.Row], int]:
    where, params = build_line_filters(filter_status, filter_tesseract, filter_volume)
    where_clause = build_where_clause(where)
    total = conn.execute(f"SELECT COUNT(*) FROM lines {where_clause}", params).fetchone()[0]
    candidates = conn.execute(
        f"""
        SELECT * FROM lines
        {where_clause}
        ORDER BY
            COALESCE(score_llm, 0.0) ASC,
            CASE WHEN TRIM(IFNULL(tesseract_text, '')) = '' THEN 0 ELSE 1 END ASC,
            agreement_score ASC,
            id ASC
        LIMIT 250 OFFSET ?
        """,
        params + [offset],
    ).fetchall()
    if not candidates:
        return None, total

    consensus_map = fetch_line_consensus_map(conn, [row["id"] for row in candidates])
    ranked_candidates = []
    for row in candidates:
        row_dict = dict(row)
        consensus = consensus_map.get(row["id"])
        if consensus:
            row_dict.update(dict(consensus))
        else:
            row_dict.update(
                {
                    "consensus_text": "",
                    "consensus_engine_count": 0,
                    "total_engines": 0,
                    "total_versions": 0,
                    "consensus_group_count": 0,
                    "consensus_tie_groups": 0,
                    "consensus_ratio": 0.0,
                    "consensus_score": 0.0,
                    "consensus_best_source_score": None,
                }
            )
        ranked_candidates.append(row_dict)

    ranked_candidates.sort(
        key=lambda item: (
            item["consensus_score"],
            item["consensus_engine_count"],
            item["score_llm"] if item["score_llm"] is not None else 0.0,
            0 if not (item["tesseract_text"] or "").strip() else 1,
            item["agreement_score"] if item["agreement_score"] is not None else 0.0,
            item["id"],
        )
    )
    return ranked_candidates[0], total


def fetch_line_by_id(conn: sqlite3.Connection, line_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM lines WHERE id = ?", (line_id,)).fetchone()


def load_line_context(conn: sqlite3.Connection, line: Optional[sqlite3.Row]) -> dict:
    context = {
        "versions": [],
        "version_choices": [],
        "suggested_text": "",
        "match_info": None,
        "raw_view": "",
        "image_path": "",
        "txt_path": "",
        "line_b64": "",
        "llm_b64": "",
        "text_context": None,
        "consensus": {
            "groups": [],
            "winning_text": "",
            "winning_engine_count": 0,
            "total_engines": 0,
            "total_versions": 0,
            "tie_groups": 0,
            "consensus_ratio": 0.0,
            "consensus_score": 0.0,
            "label": "sem consenso",
            "badge": "low",
            "detail": "sem versões comparáveis",
            "legacy_sources": [],
        },
        "tesseract_diff_html": "",
        "qwen_diff_html": "",
    }
    if not line:
        return context

    versions = get_line_versions(conn, line["id"])
    consensus = build_consensus_summary(line, versions, line)
    version_choices = build_version_choices(line, versions, consensus["winning_text"])
    context["versions"] = versions
    context["version_choices"] = version_choices
    context["consensus"] = consensus
    context["tesseract_diff_html"] = render_colored_diff_html(
        consensus["winning_text"], line["tesseract_text"] or ""
    )
    context["qwen_diff_html"] = render_colored_diff_html(
        consensus["winning_text"], line["qwen_text"] or ""
    )

    if line["line_image"]:
        context["line_b64"] = base64.b64encode(line["line_image"]).decode("utf-8")

    suggested_text = (
        line["reviewed_text"]
        or consensus["winning_text"]
        or line["qwen_text"]
        or (version_choices[0]["text_content"] if version_choices else "")
        or line["tesseract_text"]
        or ""
    ).strip()
    context["suggested_text"] = suggested_text

    if not line["image_path"]:
        return context

    try:
        img = None
        img_path = Path(line["image_path"])
        context["image_path"] = str(img_path)
        volume_path = img_path.parent.parent
        txt_path = txt_path_for_image(img_path, volume_path / "text")
        context["txt_path"] = str(txt_path)

        if txt_path.exists():
            context["text_context"] = txt_path.read_text(encoding="utf-8")
            match_info, raw_view = find_match_view(context["text_context"], suggested_text)
            context["match_info"] = match_info
            context["raw_view"] = raw_view
        else:
            context["raw_view"] = ""

        if line["bbox"] and preprocess_image is not None:
            img = cv2.imread(line["image_path"])

        if img is not None:
            img_proc = deskew_like_sample_color(img)
            bbox = json.loads(line["bbox"]) if isinstance(line["bbox"], str) else line["bbox"]
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
            context["llm_b64"] = base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        context["llm_b64"] = ""

    return context


def safe_return_to(value: str) -> str:
    if value and value.startswith("/"):
        return value
    return ""


def render_review_page(
    *,
    line: Optional[sqlite3.Row],
    is_queue: bool,
    offset: Optional[int],
    total: int,
    filter_status: str,
    filter_tesseract: str,
    filter_volume: str,
    return_to: str,
    empty_message: str,
    status_code: int = 200,
) -> tuple[str, int]:
    conn = get_conn()
    ensure_search_schema(conn)
    detail = load_line_context(conn, line)
    title = line["page_id"] if line else "sem resultado"
    skip_url = (
        url_for(
            "review",
            offset=(offset or 0) + 1,
            status=filter_status,
            tesseract=filter_tesseract,
            volume=filter_volume,
        )
        if is_queue
        else ""
    )
    html_out = render_template_string(
        REVIEW_TEMPLATE,
        title=title,
        line=line,
        is_queue=is_queue,
        offset=offset,
        total=total,
        filter_status=filter_status,
        filter_tesseract=filter_tesseract,
        filter_volume=filter_volume,
        stats=get_stats(),
        volumes=get_volumes(),
        return_to=return_to,
        skip_url=skip_url,
        empty_message=empty_message,
        **detail,
    )
    return html_out, status_code


def search_preview_text(row: sqlite3.Row) -> str:
    for field in ("reviewed_text", "qwen_text", "tesseract_text"):
        value = (row[field] or "").strip()
        if value:
            return value[:300]
    return "(sem texto disponível)"


@app.route("/")
def review():
    offset = parse_int(request.args.get("offset"), 0, 0)
    filter_status = request.args.get("status", "inferred")
    filter_tesseract = request.args.get("tesseract", "all")
    filter_volume = request.args.get("volume", "")

    conn = get_conn()
    ensure_search_schema(conn)
    line, total = fetch_queue_line(conn, offset, filter_status, filter_tesseract, filter_volume)
    return render_review_page(
        line=line,
        is_queue=True,
        offset=offset,
        total=total,
        filter_status=filter_status,
        filter_tesseract=filter_tesseract,
        filter_volume=filter_volume,
        return_to=request.full_path.rstrip("?"),
        empty_message="Nenhuma linha encontrada para os filtros selecionados.",
    )


@app.route("/line/<int:line_id>")
def line_detail(line_id: int):
    conn = get_conn()
    ensure_search_schema(conn)
    line = fetch_line_by_id(conn, line_id)
    if not line:
        return render_review_page(
            line=None,
            is_queue=False,
            offset=None,
            total=0,
            filter_status="all",
            filter_tesseract="all",
            filter_volume="",
            return_to=url_for("line_detail", line_id=line_id),
            empty_message=f"Linha {line_id} não encontrada.",
            status_code=404,
        )

    return render_review_page(
        line=line,
        is_queue=False,
        offset=None,
        total=1,
        filter_status="all",
        filter_tesseract="all",
        filter_volume="",
        return_to=url_for("line_detail", line_id=line_id),
        empty_message="",
    )


@app.route("/goto")
def go_to_line():
    line_id = parse_int(request.args.get("line_id"), 0, 1)
    if line_id < 1:
        return redirect(url_for("search_lines"))
    return redirect(url_for("line_detail", line_id=line_id))


@app.route("/search")
def search_lines():
    conn = get_conn()
    ensure_search_schema(conn)

    q = (request.args.get("q") or "").strip()
    line_id = parse_int(request.args.get("line_id"), 0, 0) or None
    filter_status = request.args.get("status", "all")
    filter_tesseract = request.args.get("tesseract", "all")
    filter_volume = request.args.get("volume", "")
    page = parse_int(request.args.get("page"), 1, 1)
    offset = (page - 1) * SEARCH_PAGE_SIZE

    where, params = build_line_filters(filter_status, filter_tesseract, filter_volume, line_id=line_id)
    message = ""
    results = []
    total = 0

    if q:
        where_clause = build_where_clause(where)
        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM lines_fts
            JOIN lines ON lines.id = lines_fts.line_id
            WHERE lines_fts MATCH ?
            {f"AND {' AND '.join(where)}" if where else ""}
            """,
            [q] + params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT
                lines.*,
                snippet(lines_fts, 3, '<mark>', '</mark>', ' … ', 18) AS snippet_html
            FROM lines_fts
            JOIN lines ON lines.id = lines_fts.line_id
            WHERE lines_fts MATCH ?
            {f"AND {' AND '.join(where)}" if where else ""}
            ORDER BY bm25(lines_fts), lines.id ASC
            LIMIT ? OFFSET ?
            """,
            [q] + params + [SEARCH_PAGE_SIZE, offset],
        ).fetchall()
        consensus_map = fetch_line_consensus_map(conn, [row["id"] for row in rows])
        for row in rows:
            consensus = consensus_map.get(row["id"])
            label, badge, _ = summarize_consensus_label(
                consensus["consensus_engine_count"] if consensus else 0,
                consensus["total_engines"] if consensus else 0,
                consensus["consensus_tie_groups"] if consensus else 0,
            )
            results.append(
                {
                    **dict(row),
                    "snippet": row["snippet_html"] or html.escape(search_preview_text(row)),
                    "tesseract_empty": not (row["tesseract_text"] or "").strip(),
                    "consensus_label": label,
                    "consensus_badge": badge,
                    "consensus_engine_count": consensus["consensus_engine_count"] if consensus else 0,
                    "consensus_total_engines": consensus["total_engines"] if consensus else 0,
                    "consensus_score": consensus["consensus_score"] if consensus else 0.0,
                }
            )
        if not results:
            message = "Nenhum resultado para a pesquisa atual."
    elif where:
        where_clause = build_where_clause(where)
        total = conn.execute(
            f"SELECT COUNT(*) FROM lines {where_clause}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT * FROM lines
            {where_clause}
            ORDER BY
                COALESCE(score_llm, 0.0) ASC,
                agreement_score ASC,
                id ASC
            LIMIT ? OFFSET ?
            """,
            params + [SEARCH_PAGE_SIZE, offset],
        ).fetchall()
        consensus_map = fetch_line_consensus_map(conn, [row["id"] for row in rows])
        for row in rows:
            consensus = consensus_map.get(row["id"])
            label, badge, _ = summarize_consensus_label(
                consensus["consensus_engine_count"] if consensus else 0,
                consensus["total_engines"] if consensus else 0,
                consensus["consensus_tie_groups"] if consensus else 0,
            )
            results.append(
                {
                    **dict(row),
                    "snippet": html.escape(search_preview_text(row)),
                    "tesseract_empty": not (row["tesseract_text"] or "").strip(),
                    "consensus_label": label,
                    "consensus_badge": badge,
                    "consensus_engine_count": consensus["consensus_engine_count"] if consensus else 0,
                    "consensus_total_engines": consensus["total_engines"] if consensus else 0,
                    "consensus_score": consensus["consensus_score"] if consensus else 0.0,
                }
            )
        if not results:
            message = "Nenhum resultado para os filtros atuais."
    else:
        message = "Informe um texto ou ao menos um filtro estruturado para pesquisar."

    base_args = {
        "q": q,
        "line_id": line_id or "",
        "status": filter_status,
        "tesseract": filter_tesseract,
        "volume": filter_volume,
    }
    prev_url = url_for("search_lines") + "?" + urlencode({**base_args, "page": max(1, page - 1)})
    next_url = url_for("search_lines") + "?" + urlencode({**base_args, "page": page + 1})

    return render_template_string(
        SEARCH_TEMPLATE,
        q=q,
        line_id=line_id,
        filter_status=filter_status,
        filter_tesseract=filter_tesseract,
        filter_volume=filter_volume,
        page=page,
        per_page=SEARCH_PAGE_SIZE,
        total=total,
        results=results,
        message=message,
        prev_url=prev_url,
        next_url=next_url,
        stats=get_stats(),
        volumes=get_volumes(),
    )


@app.route("/submit", methods=["POST"])
def submit_review():
    line_id = int(request.form["line_id"])
    # Revisoes alimentam diretamente o ground truth e devem obedecer a mesma
    # forma canonica usada pelas versoes geradas na inferencia.
    reviewed_text = unicodedata.normalize(
        "NFC", request.form["reviewed_text"]
    ).strip()
    action = request.form["action"]
    next_offset = int(request.form.get("next_offset", 0))
    status = request.form.get("status", "inferred")
    tesseract = request.form.get("tesseract", "all")
    volume = request.form.get("volume", "")
    return_to = safe_return_to(request.form.get("return_to", ""))

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        UPDATE lines
        SET reviewed_text = ?, status = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (reviewed_text, action, line_id),
    )
    conn.commit()

    if return_to:
        return redirect(return_to)

    return redirect(
        url_for(
            "review",
            offset=next_offset,
            status=status,
            tesseract=tesseract,
            volume=volume,
        )
    )


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


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
