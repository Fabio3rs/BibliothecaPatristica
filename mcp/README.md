# Bibliotheca Patristica MCP

`@fabiors/bibliotheca-patristica-mcp` **0.1.0** — Node.js **24+**.

Servidor MCP local por **stdio**, com sete ferramentas de pesquisa da Bibliotheca
Patristica. Não hospeda backend, não faz inferência e não exige chave de API do
mantenedor. O cliente MCP escolhe o modelo. Executáveis JS e WASM são locais;
catálogos, índices e documentos são buscados sob demanda.

**Esta versão não está publicada no npm.** A distribuição inicial usa tarballs
em GitHub Actions Artifacts e GitHub Releases. Nenhuma instalação executa scripts
de download de dados; não há `postinstall`, `prepare` ou publicação automática.

## Instalar um tarball de Release

Requer Node 24+ e npm. Baixe o `.tgz` e seu `.sha256` na
[Release `mcp/v0.1.0`](https://github.com/Fabio3rs/BibliothecaPatristica/releases/tag/mcp/v0.1.0)
**quando ela existir**. Os mesmos arquivos estão no artifact do workflow MCP
(é necessário baixar e extrair o ZIP do artifact primeiro).

Execute no diretório dos arquivos baixados, em Linux/macOS:

```sh
sha256sum --check fabiors-bibliotheca-patristica-mcp-0.1.0.tgz.sha256
# No macOS, use: shasum -a 256 -c fabiors-bibliotheca-patristica-mcp-0.1.0.tgz.sha256
npm install --prefix "$HOME/.local/share/bibliotheca-mcp" --ignore-scripts \
  ./fabiors-bibliotheca-patristica-mcp-0.1.0.tgz
"$HOME/.local/share/bibliotheca-mcp/node_modules/.bin/bibliotheca-patristica-mcp" --version
```

O tarball contém o servidor e o WASM; npm ainda instala as dependências públicas
do SDK, caso elas não estejam em cache. Não precisa do checkout deste repositório.
O teste `test:install` instala o tarball em um diretório temporário, com cache npm
vazio e scripts desativados. A instalação acessa o registry para obter metadados
e dependências do SDK; não depende do cache deixado por `npm ci`. Depois verifica
CLI, handshake, sete ferramentas registradas e buscas WASM reais por stdio usando
dados locais de teste. No Windows, o executável instalado é o shim `.cmd`.

## Cadastrar nos clientes

Use um caminho absoluto para o executável instalado. Os comandos abaixo usam a
instalação anterior e escrevem a configuração do cliente quando você os executar:

```sh
codex mcp add bibliotheca -- \
  "$HOME/.local/share/bibliotheca-mcp/node_modules/.bin/bibliotheca-patristica-mcp"

gemini mcp add bibliotheca \
  "$HOME/.local/share/bibliotheca-mcp/node_modules/.bin/bibliotheca-patristica-mcp"

claude mcp add --transport stdio bibliotheca -- \
  "$HOME/.local/share/bibliotheca-mcp/node_modules/.bin/bibliotheca-patristica-mcp"
```

Confira com `codex mcp list`, `gemini mcp list` ou `claude mcp get bibliotheca`.
A sintaxe Codex foi conferida em `codex mcp add --help`; veja também as
documentações oficiais de [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/)
e [Claude Code](https://code.claude.com/docs/en/mcp).

**Somente após uma eventual publicação no npm**, será possível usar:

```sh
codex mcp add bibliotheca -- npx -y @fabiors/bibliotheca-patristica-mcp
```

Esse comando **não funciona antes da publicação no npm**. Não confunda instalação
de um arquivo `.tgz` com publicação no registry.

## Desenvolvimento e validação

Na branch de desenvolvimento `codex`, sem criar outro repositório ou submódulo:

```sh
cd mcp
npm ci --ignore-scripts
npm run validate
npm run pack:check
npm run test:install
node dist/cli.js --help
```

O build copia a interface `createAgentTools(options)` e seu fechamento de imports
de `web/` para `dist/shared/`; não mantém uma segunda implementação das ferramentas.
O pacote instalado usa exclusivamente `dist/`, `vendor/` e dependências npm.
Os testes de `npm test` são determinísticos e offline; um servidor HTTP temporário vinculado
a `127.0.0.1` verifica rede local e transporte stdio. No ambiente Native Lab,
execute esses comandos na mesma sessão de loopback.

Se o Native Lab ocultar `.git`, obtenha `git rev-parse HEAD` fora dele e passe
`MCP_BUILD_COMMIT=<SHA>` ao comando `pack:check`. O manifesto local fica marcado
`dirty: true`; ele não é aceito pelo job de Release. Na CI o SHA e o estado limpo
são sempre obtidos diretamente do checkout da tag. Configure também
`npm_config_cache` para um diretório gravável se o cache padrão estiver protegido.

O comando opcional abaixo acessa a publicação real e **falha** se `compat.json`
ainda não tiver sido publicado, se os formatos forem incompatíveis ou se alguma
das sete ferramentas não funcionar:

```sh
npm run smoke:live
```

As fixtures pequenas foram construídas pelo C++ original v0.8.5, pelos builders
existentes e pelo gerador de sidecars. Não são mocks da busca. Para regenerá-las,
use explicitamente `INDEXADOR_BINARY=/caminho/indexador node scripts/generate-fixtures.mjs`.
Essa operação não é necessária para testes, instalação ou CI normal.

## Ferramentas

| Ferramenta | Resultado |
| --- | --- |
| `list_volumes` | Volumes, coleções, intervalos de páginas e paginação |
| `search_corpus` | Busca WASM em resumos automatizados, keywords e metadados; DSL, filtros, sugestões e escopo bíblico |
| `search_scripture` | Associações entre referências bíblicas e páginas físicas, evidências e cursores |
| `get_page_metadata` | Metadados de uma página física e proveniência |
| `get_page_ocr` | OCR por janelas/cursor ou parágrafo relevante; não é leitura do fac-símile |
| `search_indices` | Busca WASM nos índices editoriais |
| `get_volume_index` | Obras, seções e entradas; separa página física e paginação editorial |

Os JSON Schemas originais são registrados com `fromJsonSchema()` do SDK oficial
MCP v2, preservando limites e validações. A única adaptação textual é remover a
instrução de citações `[sN]` própria do chat do site. Resultados preservam
`source_key`, fontes, evidências, cursores, sugestões e URLs canônicas, como JSON
em `structuredContent` e representação textual equivalente. Dados remotos são
conteúdo não confiável, nunca instruções para o cliente. Não há citações inventadas.
Em modo servidor, stdout contém apenas o protocolo; diagnósticos vão para stderr.

## WASM e upstream

Usa os mesmos C++/WASM e runtime do navegador, v0.8.5, commit
`028cd943561c546974ad78eefc32e33eb6b5a1a6`. Os arquivos empacotados têm hashes em
`vendor/indexador/provenance.json`, verificados em cada build.

Apesar de Emscripten ter compilado com `-sENVIRONMENT=web`, esta versão funciona
no Node com `wasmBinary` local e `wasmModuleLoader`. O loader tem um export CommonJS
já gerado pelo Emscripten; o arquivo foi renomeado para `.cjs` sem mudar seus bytes.
Não há eval, importação de código remoto nem simulação do DOM. `DecompressionStream`
e as APIs Web padrão usadas pelo runtime estão disponíveis no Node 24. Assim,
**não é necessário recompilar o C++ para esta versão**. Os testes reais são a
garantia de compatibilidade; versões futuras do loader precisam ser revalidadas.

Uma alteração upstream pequena em `wasmsrc/indexador-lib.js` permite injetar
`fetchImpl`, mantendo `globalThis.fetch` como padrão. O patch exato está em
`vendor/indexador/node-fetch.patch` e também foi aplicado ao checkout existente
em `.work/PatrologiaIndexer`. Não foi criado commit, tag ou publicação upstream.
O runtime do site permanece intacto. Uma futura variante oficialmente Node pode
usar `-sENVIRONMENT=web,node`, preservando `-sMODULARIZE=1`; não é dependência desta entrega.

## Compatibilidade dos dados

O Pages gera `/BibliothecaPatristica/compat.json` **depois do build e das validações**,
a partir de `dist/`. O contrato 1 contém commit/build, versão/commit do indexador,
schemas, endereços dos manifests SHA-versionados e relações `build_id` entre
índices e sidecars. O `build_id` editorial é o SHA-256 do `index.map` efetivo.
O inventário `compat/<SHA>/integrity.json` contém tamanho e SHA-256 dos arquivos
de metadados, dicionários, referências e índices efetivamente publicados.

| Formato | Suportado |
| --- | --- |
| Contrato MCP / inventário | 1 / 1 |
| Catálogo / metadados / blocos de páginas | 1 / 1 / 2 |
| Busca de corpus / índice editorial WASM | 2 / 1 |
| JSON editorial | 3 |
| Referências bíblicas | `bibliotheca-scripture-book-shards-v3` |
| Sidecars bíblicos | `bibliotheca-scripture-docids-v1` |
| Família de formato PatrologiaIndexer | 1 (v0.8.5) |

Handshake e listagem de ferramentas não fazem downloads. A primeira execução
descobre a publicação; o processo mantém esse build fixo até reiniciar. Cada
download é validado contra seu hash. Mesmo arquivos de metadados que mantêm URLs
não versionadas não podem ser misturados silenciosamente. Se um deploy substituir
esses arquivos, a sessão antiga recusa o conteúdo divergente: reinicie o MCP.
O GitHub Pages não garante retenção dos diretórios de publicações anteriores.

OCR usa as URLs raw já fornecidas pelo catálogo, restritas ao diretório `teste/`
do repositório BibliothecaPatristica; referências de branch são fixadas no commit
publicado. As URLs retornadas também ficam fixadas nesse commit. OCR não passa
pelo inventário do Pages e recebe verificação de integridade local no cache.
Não há fallback para uma branch mutável. Um novo deploy compatível não requer
nova versão do pacote. Contratos desconhecidos produzem erro explícito.

## Configuração e cache

| Variável | Padrão |
| --- | --- |
| `BIBLIOTHECA_BASE_URL` | `https://fabio3rs.github.io/BibliothecaPatristica/` |
| `BIBLIOTHECA_CACHE_DIR` | `$XDG_CACHE_HOME/bibliotheca-patristica-mcp` ou `~/.cache/bibliotheca-patristica-mcp` |
| `BIBLIOTHECA_CACHE_MAX_BYTES` | `536870912` (512 MiB) |
| `BIBLIOTHECA_TIMEOUT_MS` | `30000` por operação/download |
| `BIBLIOTHECA_GITHUB_TOKEN` | Ausente; opcional, autentica somente downloads de OCR pela API do GitHub |

O cache usa identidade de dataset, build, versão de formato, URL e hash.
Gravações usam arquivo temporário e rename atômico; requisições simultâneas são
deduplicadas, corrupção local provoca novo download e falhas não ficam em cache.
Entradas menos recentes são removidas para respeitar o limite total do diretório.
O diretório é verificado antes das gravações, na primeira leitura de um cache
existente e, depois, no máximo uma vez por minuto durante leituras. Acertos
consecutivos não provocam uma varredura completa a cada arquivo.
Arquivos maiores que o limite podem ser usados sem serem persistidos. O limite
por download é 64 MiB e por descompressão 128 MiB. Cancelar um consumidor não
interrompe outros consumidores do mesmo download; cancelar o último aborta a rede.

Use um diretório dedicado. Para limpar o cache, pare o processo e remova esse
diretório. Não há download do corpus completo na instalação ou inicialização.
Busca carrega apenas mapas, shards e documentos necessários à consulta. A API
JavaScript interna permite injetar `fetchImpl` para testes e outros ambientes Node.
URLs fora da lista de dados publicada, redirects, caminhos com traversal,
credenciais e código remoto são recusados. HTTP só é aceito em loopback explícito.

### Token opcional e rate limiting

Sem token, o OCR continua sendo baixado de `raw.githubusercontent.com`. Definir
`BIBLIOTHECA_GITHUB_TOKEN` no ambiente do processo MCP ativa a
[Contents API](https://docs.github.com/en/rest/repos/contents#get-repository-content)
para esses arquivos, com `Accept: application/vnd.github.raw+json` e `ref` fixado
no commit do manifesto. A API interna também aceita a opção `githubToken`; uma
string vazia desativa a autenticação mesmo quando a variável estiver definida.
Não há leitura automática de `GITHUB_TOKEN`, `GH_TOKEN` ou credenciais do `gh`.

Use um token pessoal com acesso de leitura ao conteúdo público. A Contents API
documenta a permissão fina `Contents: read` para acesso a conteúdo protegido;
este corpus é público e não exige permissões de escrita ou acesso a repositórios
privados. O token pertence ao usuário, não é uma chave fornecida pelo mantenedor.
Exemplo em Bash, sem colocar o valor do token no histórico:

```bash
read -r -s -p 'GitHub token: ' BIBLIOTHECA_GITHUB_TOKEN
export BIBLIOTHECA_GITHUB_TOKEN
"$HOME/.local/share/bibliotheca-mcp/node_modules/.bin/bibliotheca-patristica-mcp"
```

Ao usar um cliente MCP, disponibilize essa variável ao processo que ele inicia
e reinicie o servidor. O segredo não é argumento de linha de comando. Ele só é
enviado a `api.github.com` para OCR do repositório BibliothecaPatristica, nunca ao
Pages, ao raw ou a uma URL base personalizada. Redirects são recusados. O token
não integra chaves/arquivos de cache, resultados ou diagnósticos. As URLs de
proveniência continuam sendo as URLs raw canônicas e o cache é reutilizado entre
execuções autenticadas e anônimas.

Em respostas 429 ou 403 identificadas como rate limiting, o MCP informa quando
tentar novamente, respeitando `Retry-After` e `x-ratelimit-reset`; na ausência de
um prazo válido, aplica um minuto. Durante esse intervalo, novas requisições à
mesma origem falham imediatamente com o prazo, sem repetir downloads. Acertos
no cache continuam disponíveis. Esse controle dura até reiniciar o processo;
não há espera automática longa nem fallback anônimo após rejeição do token.
Erros de autenticação/permissão também não são repetidos automaticamente.

As cotas de 60 requisições/h sem autenticação e normalmente 5.000/h com token
pessoal são da [API REST](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api),
não do raw nem do Pages. Limites secundários ainda se aplicam. O token não altera
o acesso aos índices hospedados no Pages. Os testes usam tokens fictícios e
fixtures offline; não exigem credenciais nem consomem a quota de uma conta real.

## GitHub Actions e Releases

`.github/workflows/mcp.yml` é independente de Pages e usa Node 24,
`actions/setup-node@v7` e runners GitHub-hosted Ubuntu 24.04. Runners próprios
precisam de versão 2.327.1 ou superior, conforme
[setup-node](https://github.com/actions/setup-node).

Pushes relevantes em `codex`, PRs e disparos manuais executam `npm ci`, build,
testes offline/WASM/stdio, testes compartilhados do site, `npm pack --dry-run`,
`npm pack`, inspeção do conteúdo e instalação externa. O artifact
`bibliotheca-patristica-mcp-<versão>-<run_id>-<attempt>` contém somente tarball,
SHA-256 e manifesto de versão/commit/Node, com retenção de sete dias.

Para distribuir uma versão, o mantenedor cria manualmente a tag `mcp/v0.1.0`
no commit desejado e publica a Release correspondente. O evento `release.published`
reconstrói e testa **a tag**, confere a versão e então anexa os arquivos à Release
existente. Se ela não existir mais, permanecem os artifacts. Conflitos de nomes
falham explicitamente; não há `--clobber`. Apenas o job de anexação recebe
`contents: write`; os outros jobs são somente leitura. Nenhum job cria tags,
Releases, configura credenciais npm ou publica no npm.

## Publicação manual futura no npm

Este procedimento é apenas documentação; **não foi executado**. Resolva primeiro
qualquer pendência de licença descrita em `THIRD_PARTY_NOTICES.md`, confira acesso
ao escopo `@fabiors` e ajuste a versão intencionalmente. Em um checkout limpo da
tag, rode a validação completa, empacote e teste a instalação. Somente quando
uma publicação for autorizada separadamente:

```sh
npm login
npm publish ./artifacts/fabiors-bibliotheca-patristica-mcp-0.1.0.tgz --access public
```

## Limitações

- O primeiro uso contra produção depende do deploy que introduz `compat.json`.
- OCR e resumos podem conter erros; associações bíblicas não comprovam o texto
  de uma passagem. Leia o OCR quando necessário e confira o fac-símile separadamente.
- Operações WASM são serializadas por processo, pois o motor possui sessões
  mutáveis. Cancelamento interrompe I/O e espera assíncrona, mas não preempta uma
  instrução C++ síncrona em execução. Engines são liberados após cada ferramenta.
- O limite de cache é aplicado por processo; múltiplos processos compartilhando
  um diretório podem excedê-lo temporariamente. Use diretórios separados quando
  precisar de um teto estrito entre clientes.
- As versões de dados e do pacote são independentes. Não há pin automático de
  versão npm baseado na data do deploy.
