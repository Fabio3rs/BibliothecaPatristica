# Scripture keyword validation

Este diretório contém os helpers de leitura da Vulgata Clementina e a auditoria
das keywords bíblicas usadas para embeddings. Os comandos são somente leitura,
exceto pela criação explícita do arquivo passado em `--out`.

Auditoria rápida das keywords já marcadas como bíblicas:

```bash
python3.11 -m scripts.scripture_keywords.audit_keyword_scripture_enrichment \
  --db data/patristica_keywords.db \
  --only-flagged \
  --out /tmp/scripture-keyword-failures.jsonl
```

Auditoria completa, incluindo divergências entre o parser e a flag persistida:

```bash
python3.11 -m scripts.scripture_keywords.audit_keyword_scripture_enrichment \
  --db data/patristica_keywords.db \
  --out /tmp/scripture-keyword-failures.jsonl \
  --fail-on-failure
```

A validação da estrutura produzida pelo `keywords_serial.py` continua no próprio
script:

```bash
python3.11 keywords_serial.py --verify --doc PG001 \
  --no-integrity-check \
  --verify-report /tmp/keywords-serial-PG001.jsonl \
  --fail-on-issues
```

Use `--verify-fix` no lugar de `--verify` apenas depois de revisar o relatório.
Essa opção altera `resumos.keywords_json`; a auditoria bíblica não altera o banco.
