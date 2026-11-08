"""Run through Native Lab: actual loopback HTTP, no real inference."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.indexing.semantic_retrieval_experiment import ExperimentDB, OllamaCache, parser
from tools.indexing.retrieval_preparation import prepare_blocks, page_units
from tools.ocr_xml_utils import parse_ocr_xml_page


def test_real_http_batch_cache_loopback(tmp_path):
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.reply({"models": [{"name": "qwen3-embedding:8b", "digest": "test-digest"}]})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests_seen.append(payload)
            self.reply({"model": payload["model"], "embeddings": [[1., 2., 3., 4.] for _ in payload["input"]]})

        def reply(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    db = ExperimentDB(tmp_path / "cache.sqlite")
    try:
        args = parser().parse_args(["--out", str(tmp_path / "out"), "--dimensions", "4", "--batch-size", "2",
                                    "--ollama-url", f"http://127.0.0.1:{server.server_port}"])
        client = OllamaCache(db, "loopback", args)
        page_text, blocks = prepare_blocks(parse_ocr_xml_page(
            '<pagina><bloco tipo="nota_marginal">A</bloco>'
            '<bloco tipo="nota_marginal">IV</bloco>'
            '<bloco tipo="nota_marginal">•</bloco>'
            '<bloco tipo="titulo">LIBER IV.</bloco>'
            '<bloco tipo="texto_principal" script="latino" bbox="123">'
            'Incarna-\ntio &amp; fides.</bloco><notas>GENERATED_METADATA</notas></pagina>'))
        class Chunker:
            def chunks(self, text):
                return [{"text": text, "char_start": 0, "char_end": len(text)}]

        units = page_units({"text": page_text, "blocks": blocks}, Chunker(), "block-context")
        assert len(units) == 1 and units[0]["group"]["title_context"] == "attached"
        client.vectors(["concept", units[0]["text"], "page two"])
        client.vectors(["concept", units[0]["text"], "page two"])
        assert [len(p["input"]) for p in requests_seen] == [2, 1]
        assert requests_seen[0]["input"][1] == "LIBER IV.\n\nIncarnatio & fides."
        assert all(p["truncate"] is False for p in requests_seen)
        assert client.stats["cache_hits"] == 3
    finally:
        db.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
