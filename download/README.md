# README – pasta `download/`

Este diretório contém apenas scripts e listas de links para baixar os PDFs originais das coleções Patrística (PG/PL/PO). Os PDFs/imagens **não são versionados** no repositório; apenas os `.txt` de OCR são mantidos. Quem quiser refazer o OCR deve obter os PDFs por aqui.

## Conteúdo
- `download.py`: lê `drivegoogle2.json` e baixa os PDFs do Google Drive. Renomeia automaticamente para `PL###.pdf`, `PG###.pdf` ou `PO###.pdf`. Usa `gdown`; se falhar, tenta abrir o link no Chrome via Selenium.
- `checkfaltantes.py`: escaneia os PDFs presentes na pasta atual, compara com os itens do JSON e lista volumes faltantes por série.
- `drivegoogle2.json`: lista oficial de links do Google Drive (mesmo conteúdo de `drivegoogle2.js` em formato JavaScript/backup).

## Pré-requisitos (Linux)
- Python 3.10+  
- Pacotes pip: `gdown`, `selenium`  
- Google Chrome estável instalado  
- `chromedriver` compatível no `PATH` (em Debian/Ubuntu: pacote `chromium-driver` ou download manual)  
- Acesso à internet/Google Drive

## Instalação rápida
```bash
pip install gdown selenium
chromedriver --version   # verifique se está disponível/compatível
```
Se o comando acima falhar, baixe o chromedriver que corresponde à versão do seu Chrome e coloque-o no `PATH`.

## Uso passo a passo
1. `cd download`
2. (Opcional) Edite/atualize `drivegoogle2.json` mantendo o mesmo formato de título/link.
3. Baixar PDFs:  
   ```bash
   python download.py
   ```  
   - Salva os PDFs no diretório atual.  
   - Pula arquivos já existentes.  
   - Se `gdown` falhar, o script abre o Chrome para tentar o download via página do Drive.
4. Verificar faltantes:  
   ```bash
   python checkfaltantes.py
   ```  
   - Mostra prefixo/numeração encontrada, totais por série e arquivos ausentes em relação ao JSON.

## Convenções de nomes
- `PL` = Patrística Latina, `PG` = Patrística Graeca, `PO` = Patrística Orientalis  
- Zero-fill em 3 dígitos: `PG001.pdf`, `PL023.pdf`, `PO022.pdf`.

## Troubleshooting rápido
- **gdown com erro de autenticação/quota**: peça link direto ou tente novamente mais tarde.  
- **Selenium/Chrome não baixa**: verifique se o chromedriver é da mesma versão do Chrome e está no `PATH`.  
- **PDF parcial/corrompido**: apague o arquivo baixado e execute o script novamente.

## Escopo e limitação
- Os PDFs/imagens originais **não** estão no repositório; apenas os `.txt` de OCR são versionados.  
- Para refazer OCR ou auditoria, baixe os PDFs usando estes scripts.

## Uso responsável
Respeite eventuais direitos sobre os scans. Utilize para fins acadêmicos ou de pesquisa conforme aplicável.

## Verificações sugeridas
- Execute `python download.py` com 1–2 links de teste e confirme a criação de `PL001.pdf` etc.  
- Execute `python checkfaltantes.py` depois do download e confira se os totais e faltantes batem com o JSON.  
- Revise este README para garantir que dependências e comandos refletem o ambiente atual.
