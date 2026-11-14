import sqlite3

import numpy as np

from tools.indexing.onomastic_vector_playground import create_app, import_live_cache, prepare, vector_db


def test_prepare_knn_and_references(tmp_path):
    catalog = tmp_path / "catalog.sqlite"
    vectors = tmp_path / "vectors.sqlite"
    conn = sqlite3.connect(catalog)
    conn.executescript("""
        CREATE TABLE items (id TEXT PRIMARY KEY, label TEXT, volume TEXT, section TEXT,
                            state TEXT, ref_raw TEXT, source TEXT);
        CREATE TABLE candidate_storage (item_id TEXT, file TEXT, rank INTEGER,
                                        score REAL, navigable INTEGER);
        CREATE TABLE residual_embedding_queue (item_id TEXT);
        INSERT INTO items VALUES ('a','Augustinus','PL001','NOMINA','with_destinations','12','source');
        INSERT INTO items VALUES ('b','Augustinus','PL002','NOMINA','with_destinations','13','source');
        INSERT INTO items VALUES ('c','Ambrosius','PL001','NOMINA','source_only','','source');
        INSERT INTO candidate_storage VALUES ('a','/nonexistent.txt',1,5,1);
    """)
    conn.commit(); conn.close()
    assert prepare(catalog, vectors) == {"entries": 3, "terms": 2}
    db = vector_db(vectors)
    db.execute("UPDATE terms SET vector=?,dimension=2,model='test' WHERE label='Augustinus'",
               (np.array([1, 0], dtype=np.float32).tobytes(),))
    db.execute("UPDATE terms SET vector=?,dimension=2,model='test' WHERE label='Ambrosius'",
               (np.array([0.8, 0.6], dtype=np.float32).tobytes(),))
    db.commit()
    anchor = db.execute("SELECT id FROM terms WHERE label='Augustinus'").fetchone()[0]
    db.close()
    client = create_app(catalog, vectors).test_client()
    assert client.get('/api/stats').json['linked_entries'] == 1
    assert len(client.get('/api/search?q=August').json) == 1
    detail = client.get(f'/api/term/{anchor}').json
    assert detail['nodes'][0]['occurrence_count'] == 2
    assert detail['neighbors'][0]['label'] == 'Ambrosius'
    assert detail['neighbors'][0]['distance'] == 0.2
    assert detail['nodes'][0]['references'][0]['candidates'][0]['available'] is False
    assert client.get('/api/page?file=/etc/passwd').status_code == 404


def test_import_cache_skips_synthetic(tmp_path):
    vectors = tmp_path / "vectors.sqlite"
    db = vector_db(vectors)
    db.execute("INSERT INTO terms(label) VALUES ('Augustinus')")
    db.commit(); db.close()
    cache = tmp_path / "cache.sqlite"
    conn = sqlite3.connect(cache)
    conn.executescript("""
        CREATE TABLE runner_runs (model_name TEXT,mode TEXT,started_at TEXT);
        CREATE TABLE embeddings (text_raw TEXT,vector_blob BLOB,dimension INTEGER,
                                 model_name TEXT,created_at TEXT,role TEXT);
        INSERT INTO runner_runs VALUES ('qwen3-embedding:8b','synthetic_test','fake');
        INSERT INTO runner_runs VALUES ('qwen3-embedding:8b','ollama_live','real');
    """)
    from tools.indexing.qwen_embedding_batch_runner import format_instruct_query
    query = format_instruct_query('Augustinus')
    blob = np.array([1, 0], dtype=np.float32).tobytes()
    conn.executemany("INSERT INTO embeddings VALUES (?,?,?,?,?,?)", [
        (query, blob, 2, 'qwen3-embedding:8b', 'fake', 'query'),
        (query, blob, 2, 'qwen3-embedding:8b', 'real', 'query'),
    ])
    conn.commit(); conn.close()
    assert import_live_cache(vectors, cache, 'qwen3-embedding:8b') == {"imported": 1}
