"""Loopback-only workbench; explicit routes, audited edits, no corpus writes."""
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

from .review import RevisionConflict, corrected_xml, latest_revision, refine, save_revision
from .store import connect, digest, get_comparison, list_comparisons

STATIC = Path(__file__).parent / "static"


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, db_path, **kwargs):
        self.db_path = Path(db_path).resolve()
        super().__init__(*args, **kwargs)

    def send(self, status, body, content_type, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def json(self, status, value):
        self.send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def trusted(self):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        return self.headers.get("Host") in hosts and self.headers.get("Origin", "") in {"", *(f"http://{h}" for h in hosts)}

    def do_GET(self):
        if not self.trusted():
            self.json(403, {"error": "Acesso somente por localhost"})
            return
        try:
            self.get()
        except (ValueError, OSError) as exc:
            self.json(400, {"error": str(exc)})

    def get(self):
        url = urlsplit(self.path)
        path = url.path
        if path in ("/", "/app.js", "/style.css"):
            file = STATIC / {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
            self.send(200, file.read_bytes(), mimetypes.guess_type(str(file))[0] + "; charset=utf-8")
            return
        if path == "/favicon.ico":
            self.send(204, b"", "image/x-icon")
            return
        with sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            if path == "/api/comparisons":
                query = parse_qs(url.query)
                self.json(200, list_comparisons(con, query.get("prefix", [""])[0], query.get("after", [None])[0], int(query.get("limit", ["100"])[0])))
                return
            prefix = next((p for p in ("/api/comparisons/", "/image/", "/xml/", "/raster/") if path.startswith(p)), None)
            result = get_comparison(con, path[len(prefix):]) if prefix else None
            if not result:
                self.json(404, {"error": "Comparação não encontrada"})
                return
            revision = latest_revision(con, result["key"])
            if prefix == "/image/":
                try:
                    image = Path(result["image_path"]).read_bytes()
                except OSError:
                    self.json(404, {"error": "Imagem original indisponível"})
                    return
                if digest(image) != result["image_hash"]:
                    self.json(409, {"error": "Imagem alterada desde a ingestão"})
                    return
                self.send(200, image, mimetypes.guess_type(result["image_path"])[0] or "application/octet-stream")
            elif prefix == "/xml/":
                self.send(200, corrected_xml(result, revision).encode(), "text/plain; charset=utf-8", "ocr-corrigido.txt")
            elif prefix == "/raster/":
                from .raster import render
                query = parse_qs(url.query, keep_blank_values=True)
                layers = query.get("layers", ["blocks,anchors,differences,corrections"])[0].split(",")
                self.send(200, render(result, revision, [x for x in layers if x], float(query.get("opacity", [".4"])[0])), "image/png", "ocr-overlay.png")
            else:
                result["revision"] = revision
                result["history"] = [dict(r) for r in con.execute("SELECT id,parent_id,created_at,payload FROM revisions WHERE comparison_key=? ORDER BY id", (result["key"],))]
                self.json(200, result)

    def do_POST(self):
        if not self.trusted() or self.headers.get_content_type() != "application/json":
            self.json(403, {"error": "Origem ou tipo de conteúdo inválido"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise ValueError("Tamanho do ajuste inválido")
            body = json.loads(self.rfile.read(length))
            parts = urlsplit(self.path).path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "comparisons"] or parts[3] not in ("revisions", "refine"):
                self.json(404, {"error": "Rota não encontrada"})
                return
            con = connect(self.db_path)
            try:
                if parts[3] == "refine":
                    self.json(200, {"key": refine(con, parts[2], expected_revision=body.get("base_id"))})
                else:
                    self.json(200, save_revision(con, parts[2], body.get("base_id"), body.get("words", {}), body.get("boxes", {}), body.get("note", "")))
            finally:
                con.close()
        except RevisionConflict as exc:
            self.json(409, {"error": str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            self.json(400, {"error": str(exc)})


def serve(db_path, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, db_path=db_path))
    print(f"OCR offline: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
