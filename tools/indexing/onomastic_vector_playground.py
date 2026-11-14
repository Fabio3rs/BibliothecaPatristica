"""Local, read-only exploration of onomastic labels and their destinations.

The auxiliary SQLite database is deliberately separate from the catalog. Run
``prepare`` once, then ``embed`` with Ollama running, then ``serve``.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
from flask import Flask, abort, jsonify, render_template_string, request

from tools.corpus_utils import PROJECT_ROOT
from tools.indexing.qwen_embedding_batch_runner import (
    DEFAULT_MODEL, DEFAULT_OLLAMA_URL, DEFAULT_TASK_INSTRUCTION,
    embed_batch_ollama, format_instruct_query,
)

DEFAULT_CATALOG = PROJECT_ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
DEFAULT_VECTORS = PROJECT_ROOT / "data/index_catalog/onomastic_vectors.sqlite"
SCHEMA_VERSION = "1"


def ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def vector_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS terms (
            id INTEGER PRIMARY KEY, label TEXT NOT NULL UNIQUE,
            vector BLOB, dimension INTEGER, model TEXT, input_sha256 TEXT
        );
        CREATE TABLE IF NOT EXISTS occurrences (
            item_id TEXT PRIMARY KEY, term_id INTEGER NOT NULL, volume TEXT,
            section TEXT, state TEXT
        );
        CREATE INDEX IF NOT EXISTS occurrences_term ON occurrences(term_id);
        CREATE INDEX IF NOT EXISTS occurrences_volume ON occurrences(volume);
    """)
    version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if version and version[0] != SCHEMA_VERSION:
        raise ValueError("Versão incompatível do banco auxiliar")
    conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version',?)", (SCHEMA_VERSION,))
    return conn


def prepare(catalog: Path, vectors: Path) -> dict:
    if catalog.resolve() == vectors.resolve():
        raise ValueError("O banco auxiliar deve ser separado do catálogo")
    source = ro(catalog)
    target = vector_db(vectors)
    fingerprint = f"{catalog.resolve()}:{catalog.stat().st_size}:{catalog.stat().st_mtime_ns}"
    previous = target.execute("SELECT value FROM meta WHERE key='catalog_fingerprint'").fetchone()
    if previous and previous[0] != fingerprint:
        raise ValueError("O catálogo mudou; use outro banco auxiliar para evitar referências obsoletas")
    count = 0
    for row in source.execute("SELECT id,label,volume,section,state FROM items ORDER BY rowid"):
        label = (row["label"] or "").strip()
        if not label:
            continue
        target.execute("INSERT OR IGNORE INTO terms(label) VALUES (?)", (label,))
        term_id = target.execute("SELECT id FROM terms WHERE label=?", (label,)).fetchone()[0]
        target.execute("INSERT OR IGNORE INTO occurrences VALUES (?,?,?,?,?)",
                       (row["id"], term_id, row["volume"], row["section"], row["state"]))
        count += 1
        if count % 5000 == 0:
            target.commit()
    target.execute("INSERT OR REPLACE INTO meta VALUES ('catalog_fingerprint',?)", (fingerprint,))
    target.commit()
    result = {"entries": target.execute("SELECT count(*) FROM occurrences").fetchone()[0],
              "terms": target.execute("SELECT count(*) FROM terms").fetchone()[0]}
    source.close(); target.close()
    return result


def embed(vectors: Path, batch_size: int, limit: int | None, model: str, url: str) -> dict:
    if batch_size < 1 or (limit is not None and limit < 1):
        raise ValueError("batch-size e limit devem ser positivos")
    db = vector_db(vectors)
    existing = db.execute("SELECT DISTINCT model,dimension FROM terms WHERE vector IS NOT NULL").fetchall()
    if existing and (len(existing) != 1 or existing[0][0] != model):
        raise ValueError("Modelo inconsistente no banco auxiliar; use outro arquivo")
    total = 0
    while limit is None or total < limit:
        size = min(batch_size, limit - total) if limit is not None else batch_size
        rows = db.execute("SELECT id,label FROM terms WHERE vector IS NULL ORDER BY id LIMIT ?", (size,)).fetchall()
        if not rows:
            break
        inputs = [format_instruct_query(row["label"], DEFAULT_TASK_INSTRUCTION) for row in rows]
        vectors_out = embed_batch_ollama(inputs, ollama_url=url, model=model)
        if len(vectors_out) != len(rows):
            raise ValueError("Ollama retornou quantidade incorreta de vetores")
        for row, query, vec in zip(rows, inputs, vectors_out):
            arr = np.asarray(vec, dtype=np.float32)
            if arr.ndim != 1 or not np.isfinite(arr).all() or not np.linalg.norm(arr):
                raise ValueError(f"Vetor inválido para {row['label']}")
            if existing and len(arr) != existing[0][1]:
                raise ValueError("Dimensão inconsistente no banco auxiliar")
            db.execute("UPDATE terms SET vector=?,dimension=?,model=?,input_sha256=? WHERE id=?",
                       (arr.tobytes(), len(arr), model, hashlib.sha256(query.encode()).hexdigest(), row["id"]))
        db.commit()
        total += len(rows)
        print(f"Embeddados nesta execução: {total}", flush=True)
    remaining = db.execute("SELECT count(*) FROM terms WHERE vector IS NULL").fetchone()[0]
    db.close()
    return {"embedded_now": total, "remaining": remaining}


def import_live_cache(vectors: Path, cache: Path, model: str) -> dict:
    """Reuse only batches identified as real Ollama runs and identical inputs."""
    source = ro(cache)
    target = vector_db(vectors)
    live_times = {r[0] for r in source.execute(
        "SELECT started_at FROM runner_runs WHERE mode='ollama_live' AND model_name=?", (model,))}
    if not live_times:
        raise ValueError("Nenhuma execução Ollama real identificada no cache")
    current = target.execute("SELECT DISTINCT model,dimension FROM terms WHERE vector IS NOT NULL").fetchall()
    if current and (len(current) != 1 or current[0][0] != model):
        raise ValueError("Modelo inconsistente no banco auxiliar")
    imported = 0
    for row in source.execute("SELECT text_raw,vector_blob,dimension,model_name,created_at FROM embeddings WHERE role='query' AND model_name=?", (model,)):
        if row["created_at"] not in live_times:
            continue
        prefix = format_instruct_query("", DEFAULT_TASK_INSTRUCTION)
        if not row["text_raw"].startswith(prefix):
            continue
        label = row["text_raw"][len(prefix):]
        term = target.execute("SELECT id,vector FROM terms WHERE label=?", (label,)).fetchone()
        if not term or term["vector"] is not None:
            continue
        arr = np.frombuffer(row["vector_blob"], dtype=np.float32)
        if len(arr) != row["dimension"] or not np.isfinite(arr).all() or not np.linalg.norm(arr):
            continue
        if current and len(arr) != current[0][1]:
            raise ValueError("Dimensão inconsistente no cache")
        target.execute("UPDATE terms SET vector=?,dimension=?,model=?,input_sha256=? WHERE id=?",
                       (row["vector_blob"], len(arr), model, hashlib.sha256(row["text_raw"].encode()).hexdigest(), term["id"]))
        imported += 1
        if imported % 1000 == 0:
            target.commit()
    target.commit()
    source.close(); target.close()
    return {"imported": imported}


def nearest(db: sqlite3.Connection, term_id: int, top_k: int = 10) -> list[dict]:
    anchor = db.execute("SELECT vector,dimension,model FROM terms WHERE id=?", (term_id,)).fetchone()
    if not anchor or anchor["vector"] is None:
        return []
    query = np.frombuffer(anchor["vector"], dtype=np.float32)
    heap = []
    cursor = db.execute("SELECT id,label,vector FROM terms WHERE vector IS NOT NULL AND id!=? AND model=? AND dimension=?",
                        (term_id, anchor["model"], anchor["dimension"]))
    while True:
        rows = cursor.fetchmany(256)
        if not rows:
            break
        matrix = np.frombuffer(b"".join(row["vector"] for row in rows), dtype=np.float32).reshape(len(rows), -1)
        scores = matrix @ query
        for row, score in zip(rows, scores):
            record = (float(score), int(row["id"]), row["label"])
            if len(heap) < top_k:
                heapq.heappush(heap, record)
            elif record > heap[0]:
                heapq.heapreplace(heap, record)
    return [{"id": id_, "label": label, "similarity": round(score, 4),
             "distance": round(1 - score, 4)} for score, id_, label in sorted(heap, reverse=True)]


def safe_page(file: str) -> Path | None:
    root = (PROJECT_ROOT / "teste").resolve()
    path = Path(file).resolve()
    if path.suffix != ".txt" or not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def create_app(catalog: Path = DEFAULT_CATALOG, vectors: Path = DEFAULT_VECTORS) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def home():
        return render_template_string(HTML)

    @app.get("/api/stats")
    def stats():
        v = ro(vectors)
        c = ro(catalog)
        result = {
            "entries": v.execute("SELECT count(*) FROM occurrences").fetchone()[0],
            "terms": v.execute("SELECT count(*) FROM terms").fetchone()[0],
            "embedded": v.execute("SELECT count(*) FROM terms WHERE vector IS NOT NULL").fetchone()[0],
            "volumes": v.execute("SELECT count(DISTINCT volume) FROM occurrences").fetchone()[0],
            "candidates": c.execute("SELECT count(*) FROM candidate_storage").fetchone()[0],
            "linked_entries": c.execute("SELECT count(DISTINCT item_id) FROM candidate_storage").fetchone()[0],
            "residual": c.execute("SELECT count(*) FROM residual_embedding_queue").fetchone()[0],
        }
        c.close(); v.close()
        return jsonify(result)

    @app.get("/api/search")
    def search():
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify([])
        db = ro(vectors)
        rows = db.execute("SELECT id,label,vector IS NOT NULL AS embedded FROM terms WHERE label LIKE ? ESCAPE '\\' ORDER BY length(label),label LIMIT 30",
                          ("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)).fetchall()
        result = [dict(row) for row in rows]
        db.close()
        return jsonify(result)

    @app.get("/api/term/<int:term_id>")
    def term(term_id: int):
        v = ro(vectors); c = ro(catalog)
        row = v.execute("SELECT id,label,vector IS NOT NULL AS embedded FROM terms WHERE id=?", (term_id,)).fetchone()
        if not row:
            abort(404)
        neighbors = nearest(v, term_id)
        ids = [term_id] + [n["id"] for n in neighbors]
        nodes = []
        for id_ in ids:
            occurrences = v.execute("SELECT item_id,volume,section,state FROM occurrences WHERE term_id=? LIMIT 12", (id_,)).fetchall()
            refs = []
            for occurrence in occurrences:
                item = c.execute("SELECT ref_raw,source FROM items WHERE id=?", (occurrence["item_id"],)).fetchone()
                candidates = c.execute("SELECT file,rank,score,navigable FROM candidate_storage WHERE item_id=? ORDER BY rank LIMIT 5", (occurrence["item_id"],)).fetchall()
                refs.append({**dict(occurrence), "ref_raw": item["ref_raw"] if item else "",
                             "source": item["source"] if item else None,
                             "candidates": [{**dict(candidate), "available": safe_page(candidate["file"]) is not None} for candidate in candidates]})
            label = row["label"] if id_ == term_id else next(n["label"] for n in neighbors if n["id"] == id_)
            nodes.append({"id": id_, "label": label, "references": refs,
                          "occurrence_count": v.execute("SELECT count(*) FROM occurrences WHERE term_id=?", (id_,)).fetchone()[0]})
        v.close(); c.close()
        edges = [{"source": term_id, "target": n["id"], "kind": "semantic", "distance": n["distance"]} for n in neighbors]
        return jsonify({"term": dict(row), "neighbors": neighbors, "nodes": nodes, "edges": edges})

    @app.get("/api/page")
    def page():
        path = safe_page(request.args.get("file", ""))
        if path is None:
            abort(404)
        return jsonify({"file": str(path), "text": path.read_text(encoding="utf-8", errors="replace")[:30000]})

    return app


HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23253a35'/%3E%3Ctext x='16' y='23' text-anchor='middle' font-size='22' fill='white'%3EA%3C/text%3E%3C/svg%3E">
<title>Atlas onomástico · laboratório</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f1e9;color:#292d27;font:16px/1.5 system-ui,sans-serif}header{background:#253a35;color:#fff;padding:30px max(24px,calc((100vw - 1200px)/2))}h1{font:normal 2.2rem Georgia,serif;margin:0}header p{margin:.4rem 0 0;color:#d5e1d8}main{max-width:1200px;margin:24px auto;padding:0 24px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat,.panel{background:#fff;border:1px solid #dedcd2;border-radius:12px;padding:18px}.stat strong{display:block;font-size:1.65rem;color:#2a5750}.stat span{color:#59655f;font-size:.87rem}.layout{display:grid;grid-template-columns:1fr 1.4fr;gap:18px;margin-top:18px}h2{font:normal 1.4rem Georgia,serif;margin:0 0 14px}input{width:100%;padding:12px;border:1px solid #a9b4ad;border-radius:8px;font:inherit}button{font:inherit;cursor:pointer}.result,.neighbor,.ref{border-top:1px solid #e7e8e2;padding:10px 0}.result button,.neighbor button{border:0;background:none;padding:0;color:#12665d;text-align:left;font-weight:600}.muted{color:#69746d;font-size:.88rem}.tag{display:inline-block;border-radius:20px;background:#e6efeb;color:#315a4d;padding:2px 8px;font-size:.8rem;margin:2px}.warn{background:#fff0d2;color:#805b1f}.graph{display:flex;flex-wrap:wrap;gap:8px;padding:16px 0}.graph button{border:1px solid #b1c6ba;background:#eef5ef;border-radius:30px;padding:7px 12px;color:#255447}.graph .anchor{background:#28574b;color:#fff}.page{white-space:pre-wrap;max-height:420px;overflow:auto;background:#faf9f5;padding:12px;border-radius:8px;font:13px/1.5 ui-monospace,monospace}.link{border:0;background:none;color:#12665d;text-decoration:underline;padding:0;text-align:left;overflow-wrap:anywhere}.refs{max-height:440px;overflow:auto}#notice{min-height:1.4em;color:#8e4819}@media(max-width:750px){header{padding:25px 20px}main{padding:0 16px}.stats{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.stat strong{font-size:1.3rem}}
.layout>*,.panel{min-width:0}.graph{display:block;width:100%;overflow-x:auto;overflow-y:hidden;padding:0}.graph svg{display:block;min-width:540px;width:100%;height:330px}.graph g{cursor:pointer}.graph text{font:12px system-ui,sans-serif;fill:#244b42;pointer-events:none}.graph .anchor text{fill:white}
</style></head><body><header><h1>Atlas onomástico</h1><p>Distâncias semânticas, referências e páginas OCR · estudo local</p></header><main>
<section class="stats" id="stats" aria-label="Cobertura"></section><div class="layout"><section class="panel"><h2>Buscar rótulo</h2><input id="query" type="search" placeholder="Ex.: Augustinus, ἀγάπη" aria-label="Buscar rótulo"><p class="muted">Busca literal no catálogo. Selecione um rótulo para ver suas ocorrências.</p><div id="results"></div></section><section class="panel"><h2>Vizinhança e referências</h2><div id="notice"></div><div id="detail" class="muted">Selecione um rótulo.</div></section></div><section class="panel" style="margin-top:18px"><h2>Página alvo</h2><div id="page" class="muted">Abra uma referência para ler o OCR aqui.</div></section></main>
<script>
const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const num=n=>Number(n).toLocaleString('pt-BR');
async function get(url){const r=await fetch(url);if(!r.ok)throw Error('HTTP '+r.status);return r.json()}
get('/api/stats').then(s=>{$('#stats').innerHTML=[['Entradas',s.entries,''],['Rótulos distintos',s.terms,''],['Vetores prontos',s.embedded,`${(100*s.embedded/s.terms).toFixed(1)}% dos rótulos`],['Entradas com destinos',s.linked_entries,`${(100*s.linked_entries/s.entries).toFixed(1)}% das entradas`]].map(([a,b,c])=>`<div class="stat"><strong>${num(b)}</strong><span>${a}${c?' · '+c:''}</span></div>`).join('');$('#notice').textContent=`${num(s.volumes)} volumes · ${num(s.candidates)} destinos candidatos · ${num(s.residual)} entradas na fila residual`;}).catch(e=>$('#notice').textContent=e.message);
let timer;$('#query').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(search,180)});
async function search(){const q=$('#query').value.trim();if(!q){$('#results').innerHTML='';return}try{const rows=await get('/api/search?q='+encodeURIComponent(q));$('#results').innerHTML=rows.map(r=>`<div class="result"><button data-term="${r.id}">${esc(r.label)}</button> <span class="tag ${r.embedded?'':'warn'}">${r.embedded?'vetor':'sem vetor'}</span></div>`).join('')||'<p>Nenhum rótulo encontrado.</p>'}catch(e){$('#results').textContent=e.message}}
document.addEventListener('click',e=>{const t=e.target.closest('[data-term]');if(t)showTerm(t.dataset.term);const p=e.target.closest('[data-file]');if(p)showPage(p.dataset.file)});
function graphHtml(a,neighbors){if(!neighbors.length)return '';const cx=300,cy=165,rx=220,ry=115;const points=neighbors.map((n,i)=>({n,x:cx+rx*Math.cos(2*Math.PI*i/neighbors.length),y:cy+ry*Math.sin(2*Math.PI*i/neighbors.length)}));return `<div class="graph"><svg viewBox="0 0 600 330" role="img" aria-label="Grafo de ${esc(a.label)} e ${neighbors.length} vizinhos">${points.map(p=>`<line x1="${cx}" y1="${cy}" x2="${p.x}" y2="${p.y}" stroke="#a1b9ad" stroke-width="2"/>`).join('')}${points.map(p=>`<g data-term="${p.n.id}"><title>${esc(p.n.label)} · distância ${p.n.distance}</title><circle cx="${p.x}" cy="${p.y}" r="24" fill="#e1eee6" stroke="#4d8270"/><text x="${p.x}" y="${p.y+42}" text-anchor="middle">${esc(p.n.label.slice(0,16))}</text></g>`).join('')}<g class="anchor" data-term="${a.id}"><circle cx="${cx}" cy="${cy}" r="43" fill="#28574b"/><text x="${cx}" y="${cy+4}" text-anchor="middle">${esc(a.label.slice(0,18))}</text></g></svg></div>`}
async function showTerm(id){try{const d=await get('/api/term/'+id);const a=d.nodes[0];const neighbors=d.neighbors.map(n=>`<div class="neighbor"><button data-term="${n.id}">${esc(n.label)}</button> <span class="muted">cosseno ${n.similarity.toFixed(4)} · distância ${n.distance.toFixed(4)}</span></div>`).join('');const refs=a.references.map(r=>`<div class="ref"><b>${esc(r.volume)}</b> · ${esc(r.section||'sem seção')} · ${esc(r.ref_raw||'sem referência')}<div class="muted">${esc(r.state)} · ${esc(r.item_id)}</div>${r.candidates.map(c=>`<div><button class="link" data-file="${esc(c.file)}" ${c.available?'':'disabled'}>${esc(c.file.split('/').pop())}</button> <span class="muted">posto ${c.rank} · escore editorial ${c.score}</span></div>`).join('')}</div>`).join('');$('#detail').innerHTML=`<h2>${esc(a.label)}</h2><p class="muted">${num(a.occurrence_count)} ocorrência(s) · ${d.term.embedded?'vetor disponível':'vetor pendente'}</p>${graphHtml(a,d.neighbors)}<h2>Vizinhos KNN</h2>${neighbors||'<p class="muted">Embeddar este rótulo e outros para calcular KNN.</p>'}<h2 style="margin-top:20px">Referências do rótulo</h2><div class="refs">${refs||'<p>Sem ocorrências.</p>'}</div>`}catch(e){$('#detail').textContent=e.message}}
async function showPage(file){try{const d=await get('/api/page?file='+encodeURIComponent(file));$('#page').innerHTML=`<p class="muted">${esc(d.file)}</p><div class="page">${esc(d.text)}</div>`}catch(e){$('#page').textContent=e.message}}
</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "import-cache", "embed", "serve"))
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--vectors", type=Path, default=DEFAULT_VECTORS)
    parser.add_argument("--cache", type=Path, default=PROJECT_ROOT / "data/index_catalog/qwen_embeddings.sqlite")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--port", type=int, default=5055)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare(args.catalog, args.vectors), ensure_ascii=False))
    elif args.command == "import-cache":
        print(json.dumps(import_live_cache(args.vectors, args.cache, args.model), ensure_ascii=False))
    elif args.command == "embed":
        print(json.dumps(embed(args.vectors, args.batch_size, args.limit, args.model, args.ollama_url), ensure_ascii=False))
    else:
        create_app(args.catalog, args.vectors).run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
