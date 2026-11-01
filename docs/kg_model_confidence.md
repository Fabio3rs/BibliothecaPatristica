# Confiança autodeclarada no playground KG

Pesquisa consultada em 2026-11-01. A variante `concepts-v2-confidence` implementa uma hipótese experimental de elicitação. Não demonstramos calibração para Muse Spark, Gemma4, OCR patrístico ou extração de grafos. Nenhuma chamada de modelo foi necessária para esta implementação.

## O que os estudos sustentam

| Trabalho | Achado relevante e limite da aplicação |
|---|---|
| [On Verbalized Confidence Scores for LLMs — revisão de maio de 2026](https://arxiv.org/html/2412.14737v2) | Compara escalas e formulações. Pedir a probabilidade de correção mostrou vantagens sobre pedidos genéricos de confiança; modelos pequenos e grandes responderam de forma diferente à complexidade do prompt. Os experimentos usam perguntas objetivas sem contexto documental, não KG histórico. O trabalho começou em 2024; a versão consultada é de 2026. |
| [Asking Is Not Enough — maio de 2026, preprint](https://arxiv.org/abs/2605.27752) | A comparação entre confiança verbalizada e probabilidades dos tokens depende do protocolo. Respostas fornecidas plausíveis, mas erradas, receberam notas próximas das corretas. É necessário registrar qual resposta foi avaliada e com qual contexto; a nota não é uma leitura direta da incerteza interna. |
| [ConfidenceBench — julho de 2026, preprint](https://arxiv.org/abs/2607.20526) | Avalia 15 modelos em 200 questões de múltipla escolha, com três repetições, usando Brier e outras métricas. Acurácia e qualidade da confiança divergem. O benchmark é pequeno, de outro domínio, e não valida nosso prompt ou provedores. |
| [CALIBER — Findings EMNLP, outubro de 2026](https://aclanthology.org/2026.findings-emnlp.2420/) | Distingue prever o sucesso antes de resolver e avaliar a resposta efetivamente produzida. Seus ganhos envolvem supervisão com alvos apropriados a cada momento. Acrescentar uma frase ao prompt não reproduz esse treinamento. |
| [Atomic Calibration — Findings IJCNLP, 2025](https://aclanthology.org/2025.findings-ijcnlp.9/) | Uma nota global pode esconder afirmações corretas e incorretas na mesma resposta. A avaliação por afirmação revela falhas que desaparecem na média. A adoção por nó/relação aqui é uma adaptação de projeto, não uma conclusão experimental sobre este corpus. |
| [Can LLMs Express Their Uncertainty? — ICLR 2024](https://arxiv.org/abs/2306.13063) | Observa excesso de confiança, sensibilidade ao prompt e ausência de estratégia universalmente superior. Concordância entre amostras pode ajudar a detectar falhas, mas requer múltiplas execuções. |

O resultado positivo inicial de [Just Ask for Calibration, 2023](https://aclanthology.org/2023.emnlp-main.330/) vale para os modelos e tarefas avaliados; não assegura que qualquer porcentagem declarada seja confiável. [SaySelf, 2024](https://aclanthology.org/2024.emnlp-main.343/) acrescenta justificativas de incerteza com treinamento supervisionado e por reforço. Aqui a justificativa curta serve para inspeção da evidência; não alegamos obter os ganhos de SaySelf por prompting.

## Decisão para o corpus

Escolhemos um primeiro experimento barato, em uma única chamada, com confiança por conceito, entidade, proposição, evento e relação. A escala 0–1 facilita análise posterior; não é comprovadamente superior à escala 0–100 para estes modelos. A justificativa curta torna a interpretação inspecionável, sem pedir um raciocínio extenso. Não acrescentamos exemplos com notas arbitrárias ao prompt, nem listas de hipóteses alternativas para aumentar a saída.

A pergunta operacional, escrita em inglês no prompt, é: qual a probabilidade estimada de que um revisor qualificado, lendo as mesmas fontes, aceite **este item inteiro** como uma extração fiel e suficientemente apoiada, sem corrigir substantivamente significado, identidade, atribuição, modalidade ou direção da relação?

O alvo é fidelidade da extração. É possível extrair com alta confiança uma opinião refutada, um evento narrado como incerto ou um argumento hipotético, desde que essas qualificações estejam preservadas. Uma citação literalmente correta não garante atribuição correta. A confiança da relação também não é a média da confiança dos seus nós.

Fidelidade não exige que o rótulo de um conceito apareça literalmente na fonte. Uma interpretação contextual bem sustentada pode ser fiel e útil para o objetivo de navegação do grafo, inclusive com `grounding=inferred`. As [orientações de revisão do playground](kg_extractor_playground.md#review-goal-navigation-through-patristics) aceitam aproximações terminológicas úteis e distinguem refinamentos de erros que mudam autoria, posição ou assunto. A utilidade para navegação é um julgamento separado: as notas já armazenadas continuam respondendo ao protocolo `source-fidelity-v1`, sem serem reinterpretadas como probabilidades de utilidade.

Exemplo de formato, inteiramente ilustrativo:

```json
"model_confidence": {
  "score": 0.72,
  "rationale": "O trecho atribui a opinião ao grupo, mas o antecedente do pronome permanece ambíguo."
}
```

O número acima não corresponde a uma run nem a uma medição. Em `concepts-v2-confidence`, o campo é obrigatório em cada objeto extraído. Não existe nota geral, nem confiança para evidências e avisos. O protocolo está em `tools/knowledge_graph/prompts/model_confidence_v2.txt`; a versão é `source-fidelity-v1`. Schema, instruções e payload completos ficam congelados e recebem hashes por execução. O esquema v1 e as runs existentes permanecem compatíveis.

## Como avaliar antes de confiar nas notas

1. Comparar `concepts-v1.2` e `concepts-v2-confidence` nas mesmas páginas de calibração: mesma base de instruções, configuração, imagem e contexto. Medir também se pedir confiança muda cobertura, atribuição ou quantidade de relações.
2. Obter julgamentos independentes por item, preferencialmente sem mostrar a nota do extrator. Revisão de outro LLM é provisória; concordância automática não cria gold. Separar decisões incertas e parciais, com uma regra explícita de adjudicação, antes de atribuir rótulos binários.
3. Medir Brier, diagrama de confiabilidade e ECE com tamanhos dos grupos e incerteza amostral. Medir também discriminação entre erros/acertos e acurácia versus cobertura ao encaminhar itens para revisão. Uma nota constante pode parecer calibrada na média e ser inútil para encontrar erros individuais.
4. Estratificar por modelo, prompt, modalidade, tipo de objeto, script e qualidade do OCR. Vários itens de uma página não são observações independentes; usar a página ou obra como unidade de reamostragem e separação dos conjuntos.
5. Se houver dados suficientes, ajustar uma transformação pós-hoc em um conjunto separado e avaliar no holdout por obra. Preservar tanto o valor bruto quanto o calibrado e a versão do calibrador. Não escolher limiares ou ajustar o prompt olhando o holdout.
6. Mais tarde, comparar a elicitação em uma chamada com uma verificação posterior que leia cada item e sua fonte. Medir separadamente custo, consistência e qualidade. Repetir a mesma interpretação não comprova sua correção.

Essas métricas e transformações são um plano de avaliação, não funcionalidades já implementadas. A implementação atual armazena, valida o formato, mostra e exporta as notas brutas. Não aceita/rejeita afirmações por limiar de confiança e não usa confiança alta para suprimir erros de evidência ou revisão semântica pendente.
