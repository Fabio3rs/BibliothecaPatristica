# Licenças e proveniência

O código deste pacote e os módulos reutilizados de BibliothecaPatristica seguem
a licença MIT em `LICENSE` (Patrística Digital contributors, 2026).

## PatrologiaIndexer

`vendor/indexador/` contém o runtime JS e WASM de
https://github.com/Fabio3rs/PatrologiaIndexer, v0.8.5, commit
`028cd943561c546974ad78eefc32e33eb6b5a1a6`.
`provenance.json` registra hashes, origem e alterações. O loader foi renomeado
para `.cjs`, mantendo bytes; o runtime recebeu somente injeção de fetch.

**Pendência de distribuição:** o checkout upstream não traz uma licença para
seu código próprio. A autorização do titular para distribuição MIT foi solicitada
e precisa ser registrada antes de qualquer publicação externa. As licenças dos
componentes abaixo não substituem a licença do código próprio do indexador.

## Componentes do binário e do runtime

Textos integrais preservados em `licenses/`:

- utf8proc: MIT e licença dos dados Unicode (`utf8proc.txt`).
- cereal: BSD-3-Clause (`cereal.txt`).
- cereal/base64, René Nyffenegger: licença permissiva própria (`cereal-base64.txt`).
- RapidJSON e msinttypes: avisos do cereal preservados em `rapidjson.txt` e `msinttypes.txt`.
- nlohmann/json: MIT (`nlohmann-json.txt`).
- Emscripten 5.0.6: MIT e University of Illinois/NCSA (`emscripten.txt`).
- libc++ / libc++abi: Apache-2.0 com LLVM exception e avisos históricos (`libcxx.txt`, `libcxxabi.txt`).
- compiler-rt / libunwind: Apache-2.0 com LLVM exception e avisos históricos (`compiler-rt.txt`, `libunwind.txt`).
- musl libc: MIT e avisos internos (`musl.txt`).

O SDK oficial `@modelcontextprotocol/server` e suas dependências são instalados
pelo npm, não incorporados aos arquivos WASM/JS vendorizados. O SDK v2.4.0 usa
Apache-2.0 e preserva sua licença no pacote instalado. O cliente MCP é apenas
dependência de desenvolvimento/testes.

O tarball não contém o corpus, PDFs ou imagens. OCR, resumos e metadados remotos
seguem a política CC0 do repositório BibliothecaPatristica; os fac-símiles de
terceiros têm seus próprios direitos. Consulte `LICENSE-NOTICE.md` do repositório.
