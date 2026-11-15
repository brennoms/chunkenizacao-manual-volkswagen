# Relatório Técnico — Construção de Base de Conhecimento a partir do Manual Técnico Volkswagen Nivus

**Disciplina:** Extração e Preparação de Dados  
**Professor:** Alvaro Riz de Barros  
**Aluno:** Brenno Marques Silva  
**Fonte de Dados:** Manual de Instruções Volkswagen Nivus (`21B.5B1.NIV.66`, 309 páginas)  

---

## Sumário Executivo

Este relatório documenta a arquitetura, as decisões de engenharia e os resultados alcançados na transformação do Manual de Instruções do Volkswagen Nivus em uma base de conhecimento estruturada e otimizada para sistemas de Inteligência Artificial, motores de *Retrieval-Augmented Generation* (RAG) e assistentes personalizados (Gems/GPTs).

O pipeline desenvolvido foi dividido em três macroetapas auditáveis:
1. **Extração Bruta Determinística:** Captura posicional, tipográfica e vetorial de 100% dos elementos das 309 páginas do PDF, com validação criptográfica (`SHA-256`) e homologação visual.
2. **Preparação e Reconstrução Semântica (3 Fases):** Saneamento espacial de ruídos de impressão, consolidação e desduplicação global de ícones via *Perceptual Hashing* (`pHash` de 64 bits), reconstrução de ordem de leitura em múltiplas colunas, costura de parágrafos/tabelas entre páginas e enriquecimento visual de figuras e ícones.
3. **Chunking Híbrido Semântico-Hierárquico (`semantic_hierarchical_units_v2_refined`):** Fragmentação inteligente em **537 chunks** (514 técnicos e 23 de índices de navegação), armazenados em formato `.jsonl` com metadados ricos e dupla representação textual (`text` e `embedding_text`).

---

## 1. Ferramentas Utilizadas

Para garantir reprodutibilidade, precisão geométrica e controle total sobre estruturas complexas de engenharia automotiva, optou-se por construir um pipeline em **Python** estruturado em módulos especializados, combinado com enriquecimento multimodal assistido por IA:

* **Extração Física e Renderização (`PyMuPDF` / `fitz`):** Utilizado na extração nativa de blocos de texto (preservando fontes proprietárias `VWHead` e `VWText`, tamanhos, cores RGB/CMYK e matrizes de transformação), extração de primitivas vetoriais (`shape` e `vector`), links internos e recomposição de máscaras de transparência (`SMask`) em imagens PNG.
* **OCR Complementar (`RapidOCR` e OCR nativo do `PyMuPDF`):** Configurado como *fallback* automático exclusivamente para imagens que ocupam mais de 35% da página e não possuem camada de texto nativo sobreposta.
* **Agrupamento Espacial e Desduplicação de Ícones (`Union-Find` + `pHash` de 64 bits com Distância de Hamming):** Algoritmos em Python para fundir primitivas gráficas sobrepostas em ícones únicos e calcular hashes perceptuais sobre recortes renderizados, permitindo agrupar ocorrências visualmente idênticas sob um mesmo identificador global (`icon_id`).
* **Enriquecimento Multimodal (Modelos Visuais de IA / LLMs):** Utilizado de forma controlada para gerar descrições textuais ricas e objetivas para o catálogo de imagens (`descricao_images.json`) e para o catálogo de ícones (`descricao_icons.json`), ancorando elementos puramente visuais ao fluxo textual sem alterar o texto original do fabricante.
* **Auditoria e Inspeção Visual (*Visual Debugging* via `PyMuPDF`):** Geração automatizada de PDFs de homologação com sobreposição vetorial colorida (`debug_documento_completo.pdf`, `debug_fase1_removidos.pdf`, `debug_fase2_icones.pdf` e `debug_fase3_ordem_leitura.pdf`), permitindo auditar visualmente o descarte de ruídos, a fusão de ícones e o grafo direcionado da ordem de leitura.

---

## 2. Etapa 2 — Definição e Aplicação da Técnica de Chunking

Abaixo detalha-se a abordagem escolhida para a Etapa 2 do trabalho, respondendo diretamente às **6 questões norteadoras** da especificação.

### 2.1. Qual técnica foi utilizada? (Pergunta 1)
Foi implementada uma técnica de **Chunking Híbrido (Estrutural-Hierárquico + Semântico + Controle por Janela de Tokens)**, denominada internamente no pipeline como `semantic_hierarchical_units_v2_refined`. 

Essa abordagem opera sobre as 4.511 unidades semânticas previamente reconstruídas (`heading`, `paragraph`, `list`, `table`, `alert_box`, `image` e `icon`), combinando três camadas de decisão:
1. **Camada Estrutural:** Respeito estrito à árvore hierárquica do manual (`section_path` de níveis 1 a 4: Capítulo > Seção > Subseção > Tópico Local) e segregação entre conteúdo técnico operacional e estruturas de navegação (Sumário e Índice Remissivo).
2. **Camada Semântica (Micro-unidades indivisíveis):** Agrupamento prévio de elementos fortemente acoplados (ex.: subtítulos, chamadas de alerta, listas passo a passo, caixas de advertência e figuras com suas respectivas legendas numeradas) em micro-unidades atômicas.
3. **Camada de Orçamento de Tokens:** Empacotamento guloso com pós-processamento de caudas para manter os fragmentos dentro de uma janela ideal de **350 a 650 tokens** (com piso de 80 e teto estrito de 850 tokens).

### 2.2. Por que essa técnica foi considerada adequada ao manual? (Pergunta 2)
Um manual automotivo como o do Volkswagen Nivus não é uma narrativa linear contínua; trata-se de um documento técnico altamente modular, multimodal e cheio de restrições de segurança. 

* Um **chunking puramente por caracteres ou tamanho fixo de tokens** cortaria procedimentos de segurança ao meio, separaria o cabeçalho `**[PERIGO]**` de suas instruções de prevenção ou dividiria tabelas de luzes de advertência deixando linhas sem cabeçalho.
* Um **chunking puramente estrutural por subseção** geraria centenas de fragmentos minúsculos (< 50 tokens, como pequenos avisos introdutórios `"⚠️ Introdução ao tema"`) e alguns fragmentos gigantescos (> 1.500 tokens, como tabelas de fusíveis ou painéis gerais do veículo).

Portanto, a abordagem **híbrida** é a única capaz de garantir, simultaneamente, que nenhum procedimento ou alerta crítico seja mutilado e que os chunks resultantes tenham densidade informacional uniforme (média alcançada de **501,18 tokens**), ideal para modelos de *embedding* modernos.

### 2.3. Quais critérios determinaram o início e o término de cada chunk? (Pergunta 3)
O início e o encerramento de cada chunk são governados por quatro critérios objetivos e determinísticos:

1. **Fronteira de Capítulo e Sistema (`section_path[0]`):** Mudanças de capítulo principal forçam o encerramento imediato do chunk atual e o início de um novo chunk. Chunks nunca misturam capítulos distintos (exceto em páginas preliminares de expediente/garantia).
2. **Fusão Controlada de Subseções Adjacentes:** Subseções curtas (< 350 tokens) que compartilham o mesmo capítulo e componente (`section_path[:2]`) são fundidas em um único chunk, preservando seus subtítulos internos em negrito no corpo do texto. Quando uma nova subseção trata de outro componente e já possui volume próprio suficiente, inicia-se um novo chunk.
3. **Orçamento de Tokens (Janela Alvo de 350–650 tokens e Teto de 850 tokens):** Durante o empacotamento das micro-unidades de uma seção, o algoritmo acumula blocos enquanto a soma permanecer próxima à janela alvo (até 650 tokens), podendo estender-se até o limite rígido de 850 tokens para evitar quebrar uma micro-unidade coesa ou absorver uma cauda pequena.
4. **Rebalanceamento Reverso de Caudas (`TOKENS_FUSAO_CAUDA = 240`):** Ao finalizar uma seção, se o último chunk gerado for uma "cauda" pequena (< 240 tokens), o algoritmo tenta fundi-lo ao chunk imediatamente anterior da mesma seção (desde que a soma não ultrapasse 835 tokens) ou transfere a última micro-unidade do chunk anterior para equilibrar ambos dentro da faixa ideal.

### 2.4. Foi utilizada sobreposição (overlap) entre os chunks? Se sim, qual foi o critério? (Pergunta 4)
**Sim, foi utilizado um Overlap Seletivo e Contextual (Estrutural/Semântico)** em vez de um overlap mecânico de janela deslizante fixa (como repetir cegamente 15% dos tokens finais em todo chunk).

* **Por que não usar overlap fixo em todo o manual?** Em manuais técnicos, quando um chunk termina a explicação do *"Espelho retrovisor interno"* e o próximo inicia *"Espelhos retrovisores externos"*, repetir o final do texto anterior no novo chunk introduz ruído semântico ("poluição de *embedding*"), fazendo o chunk de retrovisor externo ser recuperado erroneamente em buscas sobre o retrovisor interno.
* **Critério do Overlap Seletivo:** O overlap foi ativado exclusivamente quando há **dependência contextual ou continuidade física real** entre dois chunks da mesma seção, ocorrendo em exatamente **37 chunks (6,9% do corpus)**. Cada ocorrência é documentada no metadado auditável `motivo_overlap`:
  1. `continuidade_tabela_dividida`: Repetição integral do cabeçalho Markdown (`| Coluna | ... |`) e do subtítulo com sufixo `*(continuação)*` quando uma tabela ultrapassa 850 tokens.
  2. `continuidade_procedimento_ou_lista`: Repetição do texto introdutório (`lead_text`) com sufixo `(continuação)` e manutenção da numeração sequencial quando uma lista extensa é dividida.
  3. `continuidade_legenda_figura`: Repetição da referência visual (`**Referência visual:** Fig. X *(continuação dos perfis e legendas)*`) quando uma figura possui legendas extremamente longas (ex.: *Active Info Display*).
  4. `alerta_complementar_ao_procedimento_anterior` / `continuacao_textual_direta`: Injeção explícita de marcador contextual `*[Contexto anterior: <subtítulo/tópico>]*` e vínculo bidirecional no campo `chunks_relacionados`.

### 2.5. Como foram tratados procedimentos, listas, tabelas e trechos que não podem ser divididos sem perda de contexto? (Pergunta 5)
A preservação de estruturas indivisíveis foi resolvida combinando a reconstrução semântica da Fase 3 com regras de atomicidade no empacotador de chunks:

* **Caixas de Alerta (`alert_box` — `PERIGO`, `ATENÇÃO`, `CUIDADO`, `NOTA`, `AMBIENTAL`, `INFO`):** Foram reconstruídas geometricamente na preparação (unindo cabeçalho colorido, ícone de severidade, `lead_text` e todos os itens de recomendação, mesmo quando atravessam colunas) e tratadas como **blocos atômicos indivisíveis** no chunking. Sempre que o orçamento de tokens permite, o alerta permanece no mesmo chunk do procedimento ao qual se refere (`refers_to_ids`).
* **Procedimentos e Listas (`list`):** O texto introdutório terminado em dois-pontos (`lead_text`) é fundido estruturalmente aos seus itens (`items` ordenados, não ordenados ou *checklists*). Uma lista inteira forma uma única micro-unidade. Caso uma lista excepcionalmente longa exceda 850 tokens, a função `_dividir_lista_grande` realiza o corte apenas entre itens completos, repetindo o `lead_text (continuação)` na segunda parte.
* **Tabelas (`table`):** Linhas e colunas desenhadas com primitivas gráficas no PDF foram convertidas para sintaxe Markdown padronizada, e tabelas que continuavam na página seguinte foram costuradas sob cabeçalho único. No chunking, tabelas até 850 tokens permanecem inteiras. Tabelas maiores são divididas pela função `_dividir_tabela_grande` apenas entre linhas completas, **replicando obrigatoriamente o cabeçalho Markdown** em todos os fragmentos.
* **Imagens, Diagramas e Ícones (`image` e `icon`):** 
  * Nenhuma figura ficou "muda": cada bloco `image` agrega seu rótulo (`figure_label`, ex.: `"Fig. 10"`), sua legenda oficial (`caption`), todos os itens numerados da legenda (`legend_items`, ex.: `"1. Tacômetro..."`) e uma descrição visual detalhada (`description` vinda de `descricao_images.json`). Tudo isso viaja junto para o mesmo chunk.
  * Ícones inseridos no meio de frases (como símbolos de botões ou luzes espia) foram substituídos por tokens rastreáveis `<icon:icon_XXXX>` acompanhados de suas descrições semânticas (`descricao_icons.json`), impedindo frases lacunares como *"Pressione a tecla [vazio] para ativar"*.

### 2.6. Como a técnica escolhida contribui para a recuperação de informações por sistemas de IA? (Pergunta 6)
A arquitetura implementada potencializa diretamente a precisão (*Precision@K*) e a revocação (*Recall@K*) em sistemas de IA através de cinco mecanismos:

1. **Separação entre `embedding_text` e `text`:** Cada chunk possui um campo `embedding_text` que injeta no topo um cabeçalho estruturado com o caminho hierárquico, sistema automotivo, componente, assunto, tipo de conhecimento e páginas antes do conteúdo Markdown. Isso garante que o vetor gerado capture o contexto global (ex.: saber que um parágrafo sobre "troca de lâmpada" pertence ao sistema de iluminação do Nivus), enquanto o campo `text` entrega um Markdown limpo ao prompt do LLM.
2. **Despoluição do Espaço Vetorial (Segregação de Índices):** Os 23 chunks do Sumário e do Índice Remissivo (que contêm dezenas de palavras-chave soltas seguidas de números de páginas) foram isolados na coleção `indices_navegacao` (`chunks_indices.jsonl`). Isso evita o problema clássico em que uma busca por *"Freio de estacionamento"* retorna a página do Índice Remissivo no topo do ranking em vez do procedimento técnico real (`chunks_tecnicos.jsonl`).
3. **Suporte Nativo a Busca Híbrida (Vetorial + Lexical BM25) e *Metadata Filtering*:** A presença de metadados normalizados (`sistema`, `componente`, `tipo_conhecimento`, `inline_icons`, `pagina_inicio`) permite que o motor de busca aplique filtros pré-recuperação (ex.: filtrar apenas `tipo_conhecimento == "troubleshooting"` ou `warning`) e combine similaridade de cosseno com busca exata por siglas (`ACC`, `ISOFIX`, `ESC`) e códigos de ícones (`icon_0008`).
4. **Expansão de Contexto via Grafo (`chunks_relacionados` e `referenced_alerts`):** Quando o manual diz *"Observe o aviso no início deste capítulo na página 17"*, o pipeline resolve a referência cruzada e grava o ID exato do alerta em `referenced_alerts` e o ID do chunk correspondente em `chunks_relacionados`. O sistema RAG pode buscar automaticamente o chunk de segurança vinculado em segundo plano.
5. **Prevenção de Alucinações Visuais:** Ao converter diagramas numerados e ícones de painel em descrições textuais explícitas ancoradas aos códigos `<icon:icon_XXXX>`, o LLM compreende exatamente qual luz de advertência é vermelha ou amarela e qual componente corresponde ao número "2" de uma figura.

---

## 3. Critérios de Fragmentação e Preparação Estrutural

A qualidade da fragmentação depende diretamente do rigor da preparação (Fases 1, 2 e 3) e das regras de empacotamento:

### 3.1. Limpeza, Costura e Enriquecimento na Preparação
* **Saneamento Espacial (Fase 1):** Remoção determinística de marcas de corte (*crop marks* com interseção $< 5\%$ da página), códigos verticais de literatura na margem esquerda ($x < 25\text{ pt}$ ou rotação $270^\circ$, como `"21B.5B1.NIV.66"`), rodapés repetitivos ($y > 562\text{ pt}$) e glifos soltos de controle tipográfico (`▸`, `▪`, `◁`).
* **Desduplicação Global de Ícones (Fase 2):** Consolidação de vetores, formas compactas ($\le 40\text{ pt}$) e textos curtos sobrepostos via *Union-Find*, seguida de agrupamento visual por `pHash` de 64 bits em um catálogo único (`icons_catalog.json` e `assets/icons/icon_XXXX.png`).
* **Ordem de Leitura por Zonas e Costura (Fase 3):** Segmentação horizontal da página por elementos de largura total ($\text{width} > 210\text{ pt}$) e leitura sequencial da coluna esquerda ($\text{centro\_x} < 210\text{ pt}$) antes da coluna direita. Parágrafos quebrados sem pontuação final ou com hifenização entre colunas/páginas foram costurados automaticamente.

### 3.2. Parâmetros de Dimensionamento dos Chunks

| Parâmetro de Fragmentação | Valor Definido | Resultado Real Obtido (`manifest.json`) |
| :--- | :--- | :--- |
| **Janela Ideal de Tamanho** | $350\text{ a }650\text{ tokens}$ | Média global de **501,18 tokens** ($60,71\%$ na faixa ideal; $95,65\%$ nos índices) |
| **Limite Máximo Estrito (Teto)** | $850\text{ tokens}$ | Máximo de **832 tokens** ($0\text{ chunks} > 850\text{ tokens}$) |
| **Limite Mínimo Estrito (Piso)** | $80\text{ tokens}$ | Mínimo de **98 tokens** ($0\text{ chunks} < 80\text{ tokens}$) |
| **Limiar de Fusão de Cauda** | $240\text{ tokens}$ | Caudas $< 240\text{ tokens}$ absorvidas ou rebalanceadas quando $\le 835\text{ tokens}$ |
| **Sobreposição (Overlap)** | Seletiva / Contextual | **37 chunks** ($6,9\%$ do corpus), 100% justificados em `motivo_overlap` |

---

## 4. Etapa 3 — Organização dos Arquivos e Justificativa do Formato `.jsonl` vs. `.txt`

### 4.1. Arquitetura de Diretórios de Saída e Nomenclatura

```text
OUTPUT/
|
├── dados_extraidos/
|   ├── manual_tecnico_volkswagen.pdf              # Cópia íntegra do manual original
|   ├── manual_tecnico_volkswagen.pdf.sha256       # Hash SHA-256 do PDF original
│   └── conteudo_extraido/                         # Saída da Extração Bruta (Etapa 1A)
│       ├── metadata.json                                # Metadados do PDF + Hash SHA-256
│       ├── document.jsonl                               # 309 linhas (1 registro JSON por página)
│       ├── assets/                                      # Imagens extraídas desduplicadas por hash
│       └── debug_visual/                                # PDFs de auditoria geométrica da extração
│
├── dados_preparados/                              # Saída da Preparação Semântica (Etapa 1B)
│   ├── document_fase1.jsonl                             # Após remoção de cabeçalhos/rodapés/ruídos
│   ├── document_fase2.jsonl                             # Após consolidação e pHash de ícones
│   ├── document_preparado.jsonl                         # 4.511 blocos semânticos ordenados
│   ├── icons_catalog.json                               # Catálogo global de ícones e matriz de paridade
│   ├── descricao_images.json                            # Descrições enriquecidas das figuras
│   ├── descricao_icons.json                             # Descrições enriquecidas dos ícones
│   ├── assets/icons/                                    # Recortes PNG únicos por icon_id
│   └── debug_visual/                                    # PDFs de homologação das Fases 1, 2 e 3
│
└── chunks/                                        # Pipeline Final de Chunking (Etapas 2 e 3)
   ├── 01_raw/
   │   └── manual_tecnico_volkswagen.pdf                 # Cópia íntegra do manual original
   ├── 02_extracted/
   │   ├── manual_tecnico_volkswagen_ocr.jsonl           # Base canônica de blocos semânticos (Fase 3)
   │   └── manual_tecnico_volkswagen_texto_bruto.txt     # Texto sequencial completo em Markdown
   └── 03_chunks/
      ├── chunks.jsonl                                   # Corpus completo unificado (537 chunks)
      ├── chunks_tecnicos.jsonl                          # Coleção principal técnica (514 chunks)
      ├── chunks_indices.jsonl                           # Coleção de Sumário e Índice Remissivo (23 chunks)
      └── manifest.json                                  # Estatísticas globais e relatório de 10 validações
```

Cada chunk recebe um identificador único, sequencial e determinístico no formato `vw_000001` até `vw_000537`, mantendo vínculo explícito com sua localização original por meio dos atributos `contexto_hierarquico`, `sistema`, `componente`, `pagina_inicio`, `pagina_fim`, `elementos_origem` (`blk_XXXXXX`) e `source_element_ids` (`el_XXXXXX`).

---

### 4.2. Justificativa Técnica: Por que utilizar `.jsonl` (JSON Lines) em vez de múltiplos arquivos `.txt` atualmente?

Embora a especificação inicial mencione a possibilidade tradicional de salvar fragmentos em arquivos `.txt` individuais dentro de pastas (ao mesmo tempo em que permite no **Entregável 2** o uso de *"outra extensão se for o caso"* e destaca na página 6 as limitações práticas de importação de arquivos em plataformas de IA), na engenharia de dados moderna para LLMs e sistemas RAG **o padrão da indústria consolidou-se no formato `.jsonl` (JSON Lines)** aliado a Markdown estruturado.

A escolha de armazenar o corpus final em `.jsonl` (`chunks.jsonl`, `chunks_tecnicos.jsonl` e `chunks_indices.jsonl`), mantendo também uma versão consolidada em texto corrido (`manual_tecnico_volkswagen_texto_bruto.txt`), fundamenta-se em **7 vantagens técnicas decisivas**:

1. **Acoplamento Nativo e Imutável entre Texto e Metadados Ricos:**
   Em arquivos `.txt` puros, não há separação estrutural nativa entre o conteúdo que o modelo deve ler e os metadados de controle (como números de página, lista de IDs de alertas referenciados, lista de ícones presentes, contagem de tokens e flags de overlap). Tentar embutir 18 campos de metadados dentro de um `.txt` exige cabeçalhos improvisados (como *frontmatter* YAML) que poluem a leitura ou exigem *parsers* frágeis na hora da ingestão. No `.jsonl`, cada linha é um objeto JSON autossuficiente que isola perfeitamente os metadados (`metadata`) do conteúdo textual.
2. **Dupla Representação Textual no Mesmo Registro (`text` vs. `embedding_text`):**
   Arquiteturas RAG de alta performance utilizam textos diferentes para a **indexação vetorial** e para a **geração de resposta pelo LLM**. No arquivo `.jsonl`, armazenamos simultaneamente o campo `embedding_text` (enriquecido com cabeçalho compacto de metadados para maximizar a qualidade do vetor) e o campo `text` (em Markdown limpo para injeção no prompt do LLM). Com arquivos `.txt` simples, seria necessário duplicar o número de arquivos em disco (1.074 arquivos `.txt`) para obter o mesmo efeito.
3. **Eliminação do *Overhead* de Sistema de Arquivos (*I/O Bottleneck*):**
   Criar 537 arquivos `.txt` espalhados em dezenas de subpastas gera centenas de chamadas de sistema (`open`, `read`, `close`) e alocação de *inodes* durante a leitura para geração de *embeddings*. O formato `.jsonl` permite leitura em **streaming sequencial linha a linha ($O(1)$ em memória por registro)** a partir de um único descritor de arquivo, sendo ordens de grandeza mais rápido e seguro em pipelines de dados em nuvem (AWS S3, GCP Cloud Storage, HuggingFace Datasets).
4. **Superação dos Limites de Upload em Assistentes Personalizados (Gemini Gems e OpenAI Custom GPTs):**
   Conforme observado no próprio enunciado do trabalho (página 6), plataformas como **OpenAI Custom GPTs** (limite típico de 20 arquivos no *Knowledge*) e **Google Gemini Gems** impõem restrições severas à quantidade de arquivos simultâneos enviados. Uma pasta com 537 arquivos `.txt` individuais é impossível de ser importada diretamente na interface de um Gem ou Custom GPT. Em contrapartida, fornecer 1 ou 2 arquivos consolidados (`chunks_tecnicos.jsonl` e `manual_tecnico_volkswagen_texto_bruto.txt`) permite importar **100% do conhecimento do manual em um único upload**, respeitando integralmente os limites das plataformas.
5. **Preservação de Relações em Grafo (`chunks_relacionados` e `referenced_alerts`):**
   Um manual técnico possui referências cruzadas constantes (ex.: um procedimento na página 140 exige a leitura de um alerta na página 17). No `.jsonl`, essas relações são representadas nativamente por listas tipadas de IDs (`"chunks_relacionados": ["vw_000020", "vw_000021"]`), permitindo consultas diretas por chave em bancos NoSQL/vetoriais. Em diretórios de arquivos `.txt`, a navegação cruzada entre pastas diferentes torna-se complexa e propensa a quebra de caminhos relativos.
6. **Compatibilidade Imediata com Frameworks de IA e Bancos Vetoriais:**
   Ferramentas modernas como **LangChain** (`JSONLoader`), **LlamaIndex**, **Haystack**, **Qdrant**, **Milvus**, **Weaviate** e **Pinecone** consomem `.jsonl` nativamente, mapeando o dicionário `metadata` diretamente para os índices de *Payload Filtering* sem necessidade de scripts intermediários de extração de texto.
7. **Conversibilidade Trivial ($1\text{ linha JSONL} \rightarrow 1\text{ arquivo TXT}$):**
   O formato `.jsonl` é um **superconjunto estrito** da organização em pastas `.txt`. Como cada linha já contém `metadata.sistema`, `metadata.componente` e `chunk_id`, caso qualquer sistema legado exija arquivos `.txt` separados em pastas, um script de 5 linhas em Python materializa instantaneamente a árvore de diretórios `<sistema>/<componente>/<chunk_id>.txt` a partir do `chunks.jsonl`, enquanto o caminho inverso (reconstruir metadados tipados a partir de `.txt` puros) seria inviável sem perda de precisão.

---

## 5. Resultados Alcançados

O processamento completo das **309 páginas** do Manual de Instruções do Volkswagen Nivus produziu resultados auditáveis, registrados automaticamente em `chunks/03_chunks/manifest.json`:

### 5.1. Volumetria Geral do Pipeline
* **Páginas processadas:** 309 páginas (100% do PDF original).
* **Blocos semânticos extraídos e reconstruídos (Fase 3):** 4.511 unidades lógicas (`heading`, `paragraph`, `list`, `table`, `alert_box`, `image`, `icon`), com 100% de cobertura nos chunks finais.
* **Total de Chunks gerados (`chunks.jsonl`):** **537 chunks**.
  * **Coleção Conteúdo Técnico (`chunks_tecnicos.jsonl`):** **514 chunks** ($95,7\%$ do corpus).
  * **Coleção Índices de Navegação (`chunks_indices.jsonl`):** **23 chunks** ($4,3\%$ do corpus — Sumário e Índice Remissivo).
* **Estatísticas de Tamanho:**
  * **Média global:** $501,18\text{ tokens}$ por chunk.
  * **Menor chunk:** $98\text{ tokens}$ (acima do limite mínimo de $80\text{ tokens}$).
  * **Maior chunk:** $832\text{ tokens}$ (abaixo do limite máximo de $850\text{ tokens}$).
  * **Exceções de tamanho (`< 80` ou `> 850` tokens):** **0 ocorrências** ($100\%$ de conformidade).

### 5.2. Distribuição Semântica por Tipo de Conhecimento (`tipo_conhecimento`)

| Tipo de Conhecimento (`tipo_conhecimento`) | Quantidade de Chunks | Proporção (%) | Papel na Base de Conhecimento |
| :--- | :---: | :---: | :--- |
| `explanation` (Explicações conceituais) | 154 | 28,68% | Funcionamento de sistemas, sensores e recursos do veículo |
| `procedure` (Procedimentos passo a passo) | 106 | 19,74% | Instruções operacionais sequenciais e *checklists* |
| `warning` (Alertas e segurança crítica) | 67 | 12,48% | Precauções de `PERIGO`, `ATENÇÃO`, `CUIDADO` e `NOTA` |
| `maintenance` (Manutenção e conservação) | 42 | 7,82% | Fluidos, bateria, fusíveis, pneus, limpeza e conservação |
| `operation` (Operação de comandos) | 40 | 7,45% | Botões, alavancas, volante multifunções, ar-condicionado e VW Play |
| `troubleshooting` (Diagnóstico e falhas) | 38 | 7,08% | Luzes de advertência acesas, mensagens de erro e `"Não prosseguir!"` |
| `identification` (Identificação visual) | 23 | 4,28% | Vistas gerais numeradas da carroceria, painel e símbolos |
| `index` (Índices de navegação) | 23 | 4,28% | Entradas do Sumário (`Índice`) e `Índice remissivo` |
| `table` (Tabelas funcionais) | 18 | 3,35% | Quadros comparativos e configurações sistematizadas |
| `reference` (Referência e garantia) | 18 | 3,35% | Expediente, termos de garantia, abreviaturas e dados legais |
| `specification` (Especificações técnicas) | 8 | 1,49% | Dimensões, capacidades de reservatórios, pesos e motores |
| **Total Global** | **537** | **100,00%** | **Cobertura integral do manual** |

### 5.3. Problemas Encontrados Durante o Processo e Soluções Aplicadas

1. **Glifos na *Private Use Area* (PUA) de Fontes Proprietárias Volkswagen:**
   * *Problema:* Fontes internas da VW utilizavam códigos Unicode privados para símbolos e marcadores, gerando caracteres ilegíveis na extração simples.
   * *Solução:* Mapeamento e normalização de caracteres da PUA e conversão automática de sobrescritos ordinais (`1o`, `No`) para `1º`, `Nº` já na etapa de extração (`extracao_pdf.py`).
2. **Ícones Vetoriais Fragmentados em Dezenas de Primitivas:**
   * *Problema:* Um único ícone de alerta ou botão no painel não era uma imagem pronta no PDF, mas sim um conjunto de 5 a 20 curvas vetoriais (`vector`), retângulos (`shape`) e letras curtas (`!`, `ABS`, `OFF`) desenhados separadamente na mesma coordenada.
   * *Solução:* Desenvolvimento da **Fase 2**, utilizando algoritmo *Union-Find* espacial com tolerância de $1,5\text{ pt}$ para fundir primitivas sobrepostas em um único objeto `"type": "icon"`, seguido de renderização e cálculo de `pHash` de 64 bits para desduplicar ícones iguais ao longo das 309 páginas.
3. **Caixas de Alerta Abertas ou Divididas entre Colunas:**
   * *Problema:* Alguns quadros de `ATENÇÃO` e `PERIGO` iniciavam no final da coluna esquerda e terminavam no topo da coluna direita, ou utilizavam apenas fios horizontais superior e inferior em vez de um retângulo fechado.
   * *Solução:* Algoritmo de reconstrução geométrica combinando cabeçalhos em retângulos coloridos, busca de fios delimitadores (`shape_type == "line"`) com tolerância de $2\text{ pt}$ e regras estritas de continuidade sintática/espacial entre colunas.
4. **Legendas Gigantescas em Vistas Gerais (ex.: *Active Info Display*, `Fig. 11`):**
   * *Problema:* Algumas figuras possuíam listas de legendas e perfis explicativos que, sozinhas, ultrapassavam 900 tokens.
   * *Solução:* Implementação da rotina `_dividir_imagem_grande`, que identifica subtítulos internos dentro da legenda (como `"Perfis de informação"`), mantendo a imagem e os itens principais na primeira parte e criando um segundo chunk conectado por overlap (`continuidade_legenda_figura`) com cabeçalho explícito de referência visual.

---

## 6. Avaliação da Qualidade e Validação de Integridade

A garantia de que nenhum conteúdo técnico foi omitido, corrompido ou descontextualizado baseou-se em **três camadas de verificação independentes**:

### 6.1. Validação Determinística da Extração Bruta (`validar_extracao.py`)
Checagem automatizada de 9 invariantes matemáticos e estruturais sobre `document.jsonl` e `metadata.json`:
* Completude e sequencialidade estrita das 309 páginas;
* Unicidade global de todos os IDs primitivos (`el_XXXXXX`);
* Permutação exata de `extraction_order` ($1 \dots K$) em cada página;
* Integridade criptográfica (`SHA-256`) e existência física de 100% dos arquivos binários em `assets/`.

### 6.2. Homologação Visual por Sobreposição Vetorial (*Visual Debugging*)
Geração de 4 relatórios visuais em PDF desenhados diretamente sobre as páginas originais do manual:
1. `debug_documento_completo.pdf`: Verifica o alinhamento geométrico exato de caixas de texto (azul), imagens (verde), formas (laranja), vetores (roxo) e links (vermelho).
2. `debug_fase1_removidos.pdf`: Demarca com retângulos vermelhos tracejados tudo o que foi descartado na Fase 1, comprovando visualmente que apenas marcas de corte, códigos laterais e rodapés foram removidos.
3. `debug_fase2_icones.pdf` + Matriz de Paridade: Confirma que cada ícone composto foi unificado sob o `icon_id` correto.
4. `debug_fase3_ordem_leitura.pdf`: Desenha um **grafo direcionado contínuo** (vetores com setas ligando o centro do elemento `#1` ao `#2`, `#2` ao `#3`, etc.) sobre todas as páginas, comprovando visualmente que o fluxo respeita títulos de largura total, desce pela coluna esquerda e sobe para a coluna direita sem saltos horizontais indevidos.

### 6.3. Bateria Automatizada de 10 Regras de Validação nos Chunks (`manifest.json`)
Antes de homologar a pasta `03_chunks/`, a função `validar_corpus_chunks` executou 10 testes formais sobre os 537 chunks, obtendo **100% de aprovação (`status: "PASSED"`)**:
1. `1_ids_unicos`: Todos os 537 `chunk_id` (`vw_000001` a `vw_000537`) são únicos e não nulos.
2. `2_conteudo_nao_vazio`: Nenhum chunk possui `text` ou `embedding_text` vazio.
3. `3_referencia_elementos_origem`: 100% dos chunks possuem rastreabilidade preenchida em `elementos_origem` (`blk_XXXXXX`) e `source_element_ids` (`el_XXXXXX`).
4. `4_paginas_origem_validas`: Invariante $1 \le \text{pagina\_inicio} \le \text{pagina\_fim} \le 309$ verdadeiro em todos os registros.
5. `5_hierarquia_documental_preservada`: Todos os níveis de `contexto_hierarquico` constam nos cabeçalhos Markdown (`#`, `##`, `###`) do campo `text`.
6. `6_tabelas_markdown_integras`: 100% das tabelas contêm cabeçalho e linha separadora `| --- |` válidos (inclusive partes divididas).
7. `7_alertas_nivel_e_instrucoes_preservados`: Todas as caixas `alert_box` preservam seu marcador de severidade (`**[PERIGO]**`, `**[ATENÇÃO]**`, `**[CUIDADO]**`, `**[NOTA]**`).
8. `8_cobertura_total_sem_duplicacao_indevida`: **100% dos 4.511 blocos semânticos** da entrada estão presentes nos chunks, sem nenhuma omissão ou duplicação acidental.
9. `9_excecoes_tamanho_identificadas`: Verificação dos limites $[80, 850]\text{ tokens}$ (0 exceções registradas).
10. `10_markdown_e_json_utf8_validos`: Serialização UTF-8 íntegra e sem caracteres corrompidos.

---

## 7. Utilização Futura em Sistemas RAG e Assistentes Personalizados

A base de conhecimento produzida está pronta para uso imediato em dois cenários principais de Inteligência Artificial:

### 7.1. Arquitetura RAG em Produção (Bancos Vetoriais + Busca Híbrida)
Em um pipeline RAG corporativo (utilizando Qdrant, Milvus, Weaviate, PGVector ou Pinecone):
1. **Ingestão e Indexação Dupla:** 
   * Utiliza-se preferencialmente o arquivo `chunks_tecnicos.jsonl` como índice primário.
   * O campo `embedding_text` é enviado para o modelo de *embedding* denso (ex.: `text-embedding-3-large`, `BAAI/bge-m3` ou `gemini-embedding-001`) para gerar o vetor semântico, e simultaneamente indexado em um motor lexical esparso (**BM25** / *SPLADE*) para capturar termos exatos como códigos de fusíveis, siglas (`ACC`, `ASR`, `ISOFIX`, `Tiptronic`) e tokens `<icon:icon_XXXX>`.
2. **Filtragem por Metadados (*Pre-filtering*):**
   * O dicionário `metadata` é salvo como *payload* no banco vetorial. Quando o usuário pergunta *"Qual a capacidade do tanque ou especificação do óleo?"*, um roteador leve pode filtrar `tipo_conhecimento IN ["specification", "maintenance"]`.
3. **Expansão de Contexto Segura (*Graph-Augmented Retrieval*):**
   * Ao recuperar os $K$ melhores chunks, a aplicação verifica os campos `metadata.chunks_relacionados` e `metadata.referenced_alerts`. Caso o chunk recuperado possua `overlap: true` ou cite um alerta de segurança de início de capítulo, o sistema busca automaticamente o chunk irmão pelo `chunk_id`, garantindo que o usuário nunca receba um procedimento mecânico/operacional sem o respectivo aviso de `**[PERIGO]**` ou `**[ATENÇÃO]**`.
4. **Montagem do Prompt e Citação Auditável:**
   * Apenas o campo limpo `text` (em Markdown) é injetado no contexto do LLM, acompanhado de `pagina_inicio` e `pagina_fim` para que o assistente cite a página exata do manual da Volkswagen na resposta final, podendo inclusive renderizar na interface do usuário a imagem de `assets/` ou o ícone de `assets/icons/icon_XXXX.png` citado no chunk.

### 7.2. Uso Direto em Assistentes Personalizados (Google Gemini Gems e OpenAI Custom GPTs)
Para criação rápida de assistentes sem infraestrutura de banco de dados própria:
* Basta importar diretamente os arquivos `chunks_tecnicos.jsonl`, `chunks_indices.jsonl` (ou `manual_tecnico_volkswagen_texto_bruto.txt`) junto aos catálogos `descricao_icons.json` e `descricao_images.json` na aba de **Conhecimento (*Knowledge*)** do Gemini Gem ou do Custom GPT.
* Como o conteúdo já está completamente higienizado, com tabelas em Markdown íntegro, procedimentos numerados, ícones descritos textualmente e cabeçalhos hierárquicos explícitos em cada bloco, o indexador interno dessas plataformas consegue segmentar e recuperar as passagens com altíssima fidelidade sem sofrer com os erros clássicos de leitura de PDFs em duas colunas.