# fix_time_breaks.py – recomputar volumes com quebras de ordem temporal

## O problema
- Cada linha de `resumos` deve ter `criado_em` não-decrescente ao longo de `pagina_num` do mesmo documento.  
- Se uma página tem `criado_em` anterior ao máximo já visto, significa que ela foi gerada antes de páginas posteriores: o `resumo_global` ficou incoerente dali em diante.
- `find_gaps.py` / `fix_gaps.py` cuidam de páginas ausentes; `resumo_serial.py --fill-gaps` repõe buracos, mas não força reprocessar quando a ordem temporal foi quebrada. `fix_time_breaks.py` cobre esse caso.

## O que o script faz
1. Consulta o SQLite (`data/patristica_resumos.db`) para detectar, por `documento`, a primeira `pagina_num` em que `criado_em` retrocede (CTE `first_break`).
2. Filtra só volumes que têm arquivo dessa página disponível em `teste/<doc>/text/`.
3. (Dry-run) Apenas lista o plano e conta quantas linhas seriam apagadas.  
   (Execução) Apaga `resumos` do volume a partir dessa página (`DELETE ... WHERE pagina_num >= restart_from`) e chama `resumo_serial.py` para refazer o volume inteiro a partir do ponto de quebra.
4. Paraleliza por volume com `ThreadPoolExecutor`; logs vão para `logs/fix_time_breaks/<doc>.log`.

## Uso rápido
- Planejar sem tocar no DB:  
  ```bash
  python fix_time_breaks.py --dry-run --series PG PL --limit 5
  ```
- Reprocessar com OpenAI (6 volumes em paralelo):  
  ```bash
  python fix_time_breaks.py --workers 6 --provider openai --model gpt-5-mini --reasoning-effort low
  ```
- Reprocessar com Ollama local:  
  ```bash
  python fix_time_breaks.py --provider ollama --model qwen3:30b --ollama-url http://localhost:11434
  ```

### Flags principais
- `--series PG PL PO` filtra prefixos de coleção.
- `--limit N` limita quantos volumes processar.
- `--dry-run` não apaga nada; só mostra plano e quantas linhas seriam removidas.
- `--workers` controla paralelismo por volume.
- `--retries`, `--api-key-env`, `--openai-url`, `--ollama-url` repassam configurações para `resumo_serial.py`.

## Como isso se relaciona ao pipeline existente
- **find_gaps.py / fix_gaps.py / resumo_serial.py --fill-gaps**: corrigem páginas faltantes, mantendo o resto intacto.  
- **fix_time_breaks.py**: foca em incoerência temporal; quando há salto, ele refaz da página quebrada até o fim para reconstruir `resumo_global` contínuo.
- Recomendado rodar `fix_time_breaks.py --dry-run` após lotes grandes de resumização para detectar volumes suspeitos; depois, executar sem `--dry-run` nos volumes listados.

## Logs e segurança
- Logs por volume: `logs/fix_time_breaks/<doc>.log`.
- O script sempre verifica se o arquivo de texto da página inicial existe e tem tamanho > 0 antes de reprocessar.
- Em dry-run, nenhuma linha é removida do SQLite.
