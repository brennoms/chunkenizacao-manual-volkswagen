# Método de Chunkenização — Manual Técnico Volkswagen

## 1. Objetivo

Transformar os artefatos consolidados na etapa de extração (`manual_tecnico_volkswagen_ocr.jsonl` e `manual_tecnico_volkswagen_texto_bruto.txt`) em um corpus de chunks semanticamente organizados, adequado à indexação e recuperação por sistemas RAG.

A chunkenização deve preservar o significado técnico, a hierarquia documental, o vínculo multimodal (imagens, legendas e ícones) e a rastreabilidade de cada informação, separando conteúdos técnicos operacionais das estruturas de navegação (Índice e Índice Remissivo).

O processo não deve resumir, reinterpretar ou acrescentar informações externas ao conteúdo original.

---

## 2. Estrutura de diretórios

```text
chunks/
│
├── 01_raw/
│   └── manual_tecnico_volkswagen.pdf
│
├── 02_extracted/
│   ├── manual_tecnico_volkswagen_ocr.jsonl
│   └── manual_tecnico_volkswagen_texto_bruto.txt
│
└── 03_chunks/
    ├── chunks.jsonl
    ├── chunks_tecnicos.jsonl
    ├── chunks_indices.jsonl
    └── manifest.json

```

**Descrição dos diretórios e arquivos:**

* `01_raw/`: armazena uma cópia do PDF original, preservando a fonte primária utilizada no projeto.
* `02_extracted/`: reúne os artefatos estruturados e textuais gerados a partir do processamento e reconstrução semântica (Fase 3), servindo como base única e reprodutível para a etapa de chunking:
* `manual_tecnico_volkswagen_ocr.jsonl`: representação estruturada canônica das unidades semânticas do documento (cópia direta da saída da Fase 3), mantendo todos os metadados, hierarquia de seções, descrições de imagens/ícones, itens de legenda, alertas e rastreabilidade (`source_element_ids`). Apesar da nomenclatura padronizada `_ocr.jsonl`, consolida a extração vetorial/textual e o enriquecimento visual previamente realizados, sem executar um novo OCR óptico.
* `manual_tecnico_volkswagen_texto_bruto.txt`: consolidação sequencial em texto corrido/Markdown gerada diretamente a partir do `manual_tecnico_volkswagen_ocr.jsonl`, seguindo estritamente a ordem de leitura (`reading_order`), com marcadores de página (`<!-- Página X -->`), listas formatadas, alertas estruturados, descrições visuais e tabelas convertidas para Markdown.


* `03_chunks/`: armazena os resultados da segmentação e validação:
* `chunks.jsonl`: corpus completo unificado contendo todos os 537 chunks gerados (`conteudo_tecnico` + `indices_navegacao`), incluindo os campos `text`, `embedding_text` e `metadata`.
* `chunks_tecnicos.jsonl`: coleção filtrada exclusivamente com os chunks técnicos do manual (514 chunks, `colecao: "conteudo_tecnico"`), recomendada como índice principal de busca semântica.
* `chunks_indices.jsonl`: coleção dedicada aos chunks do Sumário (`Índice`) e do `Índice remissivo` (23 chunks, `colecao: "indices_navegacao"`), preservando a relação entre termos, assuntos e páginas sem poluir o ranking dos chunks técnicos.
* `manifest.json`: registra informações gerais do processamento, parâmetros utilizados, estatísticas globais e segregadas (conteúdo técnico vs. índices), distribuição por tipo de conhecimento/sistema e o relatório das 10 regras de validação de integridade.



---

## 3. Entrada e saída

### Entrada

`chunks/02_extracted/manual_tecnico_volkswagen_ocr.jsonl`

Cada linha do `manual_tecnico_volkswagen_ocr.jsonl` representa uma unidade semântica reconstruída e validada na Fase 3, contendo:

* `id`: identificador único do bloco semântico (ex.: `blk_000001`).
* `reading_order`: ordem global de leitura no documento.
* `read_order`: ordem de leitura local dentro da página de início.
* `type`: tipo da unidade semântica (`heading`, `paragraph`, `list`, `table`, `alert_box`, `image`, `icon`).
* `page_start` e `page_end`: intervalo de páginas de origem (suportando blocos que atravessam colunas ou páginas, como tabelas, alertas e figuras com legendas extensas).
* `section_path`: lista ordenada com o caminho hierárquico completo da seção (níveis 1 a 4, incluindo subseções como `"⚠️ Introdução ao tema"`).
* `parent_section_id`: `id` do bloco `heading` imediatamente superior ao qual o elemento pertence.
* **Campos específicos por tipo:**
* `heading`: `level` (1 a 4) e `content`.
* `paragraph`: `content` (com palavras hifenizadas entre colunas/páginas já costuradas e números de página pareados).
* `list`: `list_style` (`unordered`, `ordered`, `checklist`), `lead_text` (texto introdutório terminado em `:`) e `items`.
* `table`: `markdown` (com tabelas continuadas entre páginas já unificadas sob cabeçalho único).
* `alert_box`: `alert_level` (`PERIGO`, `ATENÇÃO`, `CUIDADO`, `NOTA`, `AMBIENTAL`, `INFO`), `alert_scope` (`chapter_intro`, `local_topic`), `icon_id`, `refers_to_ids`, `lead_text` e `items`.
* `image`: `id_image`, `asset_id`, `image_path`, `figure_label` (ex.: `"Fig. 6"`), `caption`, `description` (descrição visual detalhada) e `legend_items` (itens numerados/explicativos da figura já absorvidos no bloco).
* `icon`: `icon_id`, `icon_order_id`, `image_path` e `description`.


* **Campos de rastreabilidade:**
* `inline_icons`: lista de `icon_id` presentes no interior do texto, lista, tabela ou legenda (representados inline por `<icon:icon_XXXX>`).
* `referenced_alerts`: lista de `id` de blocos `alert_box` citados por chamadas cruzadas de página no texto.
* `source_element_ids`: lista de identificadores brutos (`el_XXXXXX`) da Fase 2 que deram origem ao bloco.



### Saída

* `chunks/03_chunks/chunks.jsonl` (corpus completo)
* `chunks/03_chunks/chunks_tecnicos.jsonl` (coleção de conteúdo técnico)
* `chunks/03_chunks/chunks_indices.jsonl` (coleção de índices de navegação)
* `chunks/03_chunks/manifest.json` (manifesto estatístico e de validação)

---

## 4. Estratégia de chunkenização (`semantic_hierarchical_units_v2_refined`)

A divisão baseia-se em unidades semânticas e hierárquicas, respeitando a estrutura documental consolidada em `manual_tecnico_volkswagen_ocr.jsonl`.

A ordem de prioridade é:

1. Preservar a unidade de conhecimento e a coesão semântica.
2. Tratar separadamente os capítulos de navegação (`Índice` e `Índice remissivo`).
3. Respeitar os limites de capítulos (`section_path[0]`) e agrupar subseções pequenas fortemente relacionadas dentro do mesmo sistema/componente.
4. Controlar o tamanho dos chunks dentro da janela ideal de 350 a 650 tokens (respeitando o mínimo de 80 e o teto máximo de 850 tokens).

### 4.1. Agrupamento hierárquico e fusão de subseções pequenas

Os elementos são processados estritamente na ordem de leitura global (`reading_order`).

* **Fusão de subseções técnicas adjacentes:** Subseções curtas (como blocos introdutórios `"⚠️ Introdução ao tema"` ou subtópicos menores que 350 tokens) que compartilham o mesmo capítulo e componente (`section_path[:2]`), ou pertencem a capítulos preliminares curtos na mesma página, são unidas ao grupo adjacente compatível desde que a soma respeite o orçamento de tokens. Quando a fusão reduz o `section_path` ao prefixo comum, os subtítulos originais das subseções unidas são preservados em negrito no corpo do chunk para manter 100% da clareza estrutural.
* **Agrupamento dedicado de Índices:** Entradas consecutivas dos capítulos `Índice` e `Índice remissivo` são empacotadas em janelas coesas de até 650 tokens, preservando os subtítulos de seção/letra (`**Seção: [Título]**`) e a relação entre termos e páginas, sendo direcionadas para a coleção `indices_navegacao`.

### 4.2. Classificação e tratamento dos elementos em micro-unidades

Antes do empacotamento por tokens, blocos fortemente acoplados dentro de uma seção (como subtítulos curtos, remissões de início de capítulo `"⚠️ Observe no início desse capítulo..."` e blocos `icon` isolados) são unidos ao bloco subsequente em micro-unidades indivisíveis:

| Tipo de elemento | Tratamento |
| --- | --- |
| `heading` | Define ou atualiza o contexto hierárquico (`section_path`). Quando interno (oriundo de subseção fundida), acopla-se sempre ao bloco seguinte. Não gera chunk isolado. |
| `paragraph` | Agrupado com parágrafos, listas ou imagens semanticamente relacionados. Subtítulos curtos e remissões de capítulo acoplam-se ao bloco seguinte. |
| `list` | Mantida como unidade coesa (incluindo seu `lead_text` e todos os `items` em formato `-`, `1.` ou `- [x]`), preservando a ordem original. |
| `table` | Mantida em formato Markdown (`markdown`), preservando cabeçalhos, linhas, valores e tokens `<icon:icon_XXXX>`. |
| `alert_box` | Preservado como unidade indivisível (`**[{alert_level}]**` + `lead_text` + `items`), priorizando sua manutenção junto ao procedimento referenciado (`refers_to_ids`). |
| `image` | Integrada ao chunk do tópico correspondente com `caption`, `description` (`*[Imagem: ...]*`) e `legend_items`. |
| `icon` | Quando inline (`<icon:icon_XXXX>`), permanece ancorado ao texto; quando isolado (`type: "icon"`), é acoplado ao bloco adjacente da mesma seção (`*[Ícone icon_XXXX: ...]*`). |

### 4.3. Divisão de estruturas grandes (> 850 tokens)

Quando uma micro-unidade (isolada ou precedida por subtítulos acoplados) ultrapassa o limite máximo de 850 tokens, aplica-se a divisão estruturada com preservação de contexto:

* **Tabelas grandes (`_dividir_tabela_grande`):** Fragmentadas por grupos de linhas completas, repetindo obrigatoriamente o cabeçalho Markdown (`| Coluna | ... |` e `| --- | ... |`) e eventual subtítulo introdutório (`*(continuação)*`) em cada parte.
* **Listas extensas (`_dividir_lista_grande`):** Fragmentadas entre itens completos, repetindo o `lead_text` com sufixo `(continuação)` e mantendo a numeração sequencial correta (`offset`).
* **Imagens com legendas extensas (`_dividir_imagem_grande`):** Quando um bloco `image` possui `legend_items` muito longos que excedem 850 tokens (ex.: `Fig. 11 Active Info Display`), o algoritmo identifica pontos naturais de separação (subtítulos temáticos internos dentro da legenda, como `"Perfis de informação"`), mantendo a figura e os itens numerados principais na primeira parte e iniciando a segunda parte com `**Referência visual:** [Título da Figura] *(continuação dos perfis e legendas)*`.

---

## 5. Dimensionamento e Overlap Seletivo

| Parâmetro | Valor Configurado | Resultado Obtido (`manifest.json`) |
| --- | --- | --- |
| Tamanho desejado | 350–650 tokens | Média global: **501,18 tokens** (60,71% na faixa; 95,65% nos índices) |
| Limite máximo | 850 tokens | Máximo registrado: **832 tokens** (0 chunks > 850) |
| Tamanho mínimo | 80 tokens | Mínimo registrado: **98 tokens** (0 chunks < 80) |
| Estratégia de Overlap | Seletiva / Contextual | **37 chunks** (6,9% do corpus) |

### 5.1. Pós-processamento de caudas e rebalanceamento

Para evitar que o último chunk de uma seção fique pequeno, o empacotador executa uma varredura reversa:

1. Caudas menores que `TOKENS_FUSAO_CAUDA` (240 tokens) são absorvidas pelo chunk anterior da mesma seção sempre que a soma permanecer dentro do limite seguro (`<= 835` tokens).
2. Caso a soma ultrapasse o limite, o algoritmo verifica se a última micro-unidade do chunk anterior pode ser transferida para o chunk seguinte de forma que ambos permaneçam equilibrados dentro da faixa alvo.

### 5.2. Overlap seletivo e justificativa (`motivo_overlap`)

O overlap não é aplicado de forma cega a todo segundo chunk de uma mesma seção (evitando marcar overlap entre figuras ou subtópicos independentes). Ele é ativado seletivamente apenas quando há dependência contextual real, preenchendo o metadado `motivo_overlap`:

* `continuidade_tabela_dividida`: continuação de tabela grande com cabeçalho repetido.
* `continuidade_procedimento_ou_lista`: continuação de lista passo a passo dividida.
* `continuidade_legenda_figura`: continuação de legenda extensa de figura técnica.
* `alerta_complementar_ao_procedimento_anterior`: chunk composto por alertas que complementam a instrução imediatamente anterior.
* `continuacao_textual_direta` / `referencia_cruzada_imediata_na_secao`: quando o bloco inicial depende diretamente de elemento ou alerta do chunk anterior, injetando no início do corpo a nota `*[Contexto anterior: <subtítulo/tópico anterior>]*`.

---

## 6. Estrutura textual do chunk (`text` vs. `embedding_text`)

Cada registro JSON possui dois campos textuais com papéis distintos na arquitetura RAG:

1. **`text` (Texto limpo em Markdown para exibição e prompt do LLM):**

```markdown
# [Capítulo (Nível 1)]

## [Seção (Nível 2)]

### [Subseção (Nível 3)]

**Assunto:** [Tema principal]

[Conteúdo original organizado em Markdown]

```

2. **`embedding_text` (Texto enriquecido para geração de embeddings):**
Precede o conteúdo em Markdown com um cabeçalho compacto de metadados hierárquicos, funcionais e de rastreabilidade, maximizando a precisão semântica na busca vetorial:

```text
[Contexto: Informações ao condutor > Horário | Sistema: informacoes_ao_condutor | Componente: horario | Assunto: Horário | Tipo: procedure, explanation | Páginas: pp. 27-28 | Relacionados: vw_000020, vw_000021]

# Informações ao condutor
...

```

---

## 7. Classificação Semântica e Funcional

### 7.1. Coleção (`colecao`)

* `conteudo_tecnico`: 514 chunks de conteúdo informativo, operacional, procedimental, de segurança e especificações do veículo.
* `indices_navegacao`: 23 chunks pertencentes ao Sumário (`Índice`) e ao `Índice remissivo`.

### 7.2. Tipo de conhecimento (`tipo_conhecimento` e `tipos_conhecimento_secundarios`)

Como um mesmo chunk frequentemente combina uma explicação ou procedimento com uma caixa de alerta (`alert_box`) ou tabela, o pipeline atribui o rótulo dominante em `tipo_conhecimento` e registra os demais tipos presentes em `tipos_conhecimento_secundarios`:

* `explanation` (154 chunks): explicações conceituais e descrições de funcionamento.
* `procedure` (106 chunks): instruções passo a passo e checklists operacionais reais (exige pelo menos 2 passos ou verbos imperativos claros, evitando classificar notas de rodapé numeradas como procedimento).
* `warning` (67 chunks): advertências e precauções de segurança (`PERIGO`, `ATENÇÃO`, `CUIDADO`, `NOTA`) que constituem o foco principal do chunk.
* `maintenance` (42 chunks): manutenção, verificação de fluidos, abastecimento, fusíveis, rodas e conservação.
* `operation` (40 chunks): operação de comandos, botões, volante multifunções, climatização e menus do rádio/VW Play.
* `troubleshooting` (38 chunks): diagnóstico de falhas, luzes de advertência acionadas, situações de `"Não prosseguir!"` e soluções de problemas.
* `identification` (23 chunks): vistas gerais do veículo/painel, diagramas numerados e significados de símbolos.
* `index` (23 chunks): entradas estruturadas do Sumário e Índice Remissivo.
* `table` (18 chunks): tabelas funcionais de configurações e referências visuais.
* `reference` (18 chunks): capa, certificado de garantia, informações ao consumidor e abreviaturas.
* `specification` (8 chunks): dados técnicos, dimensões, capacidades e especificações de motor.

---

## 8. Formato JSON de saída

Exemplo real de registro em `chunks/03_chunks/chunks.jsonl` e `chunks_tecnicos.jsonl`:

```json
{
  "chunk_id": "vw_000038",
  "text": "# Informações ao condutor\n\n## Horário\n\n**Assunto:** Horário\n\n⚠️ Observe no <icon:icon_0027> início desse capítulo na página 17.\n\nAjustar o horário através do sistema de rádio...",
  "embedding_text": "[Contexto: Informações ao condutor > Horário | Sistema: informacoes_ao_condutor | Componente: horario | Assunto: Horário | Tipo: procedure, explanation | Páginas: pp. 27-28 | Relacionados: vw_000020, vw_000021]\n\n# Informações ao condutor\n\n## Horário\n\n**Assunto:** Horário...",
  "metadata": {
    "colecao": "conteudo_tecnico",
    "tipo_conhecimento": "procedure",
    "tipos_conhecimento_secundarios": [
      "explanation"
    ],
    "sistema": "informacoes_ao_condutor",
    "componente": "horario",
    "tema": "Horário",
    "contexto_hierarquico": [
      "Informações ao condutor",
      "Horário"
    ],
    "tipo_estrutura": "mixed",
    "pagina_inicio": 27,
    "pagina_fim": 28,
    "elementos_origem": [
      "blk_000304",
      "blk_000305",
      "blk_000306",
      "blk_000307",
      "blk_000308",
      "blk_000309",
      "blk_000310",
      "blk_000311",
      "blk_000312"
    ],
    "source_element_ids": [
      "el_002888",
      "el_002889"
    ],
    "inline_icons": [
      "icon_0027",
      "icon_0071",
      "icon_0089",
      "icon_0083",
      "icon_0084",
      "icon_0069",
      "icon_0070",
      "icon_0080",
      "icon_0086",
      "icon_0076"
    ],
    "referenced_alerts": [
      "blk_000142",
      "blk_000143",
      "blk_000150"
    ],
    "chunks_relacionados": [
      "vw_000020",
      "vw_000021"
    ],
    "tokens": 485,
    "overlap": false,
    "motivo_overlap": null,
    "excecao_tamanho": false,
    "motivo_excecao": null
  }
}

```

### 8.1. Significado dos metadados

| Campo | Significado |
| --- | --- |
| `chunk_id` | Identificador único sequencial do chunk (`vw_000001` a `vw_000537`). |
| `colecao` | Define a partição de destino (`conteudo_tecnico` ou `indices_navegacao`). |
| `tipo_conhecimento` | Classificação semântica principal do conteúdo. |
| `tipos_conhecimento_secundarios` | Lista de classificações semânticas complementares presentes no mesmo chunk. |
| `sistema` | Sistema automotivo ou capítulo principal relacionado. |
| `componente` | Componente ou subseção funcional relacionada. |
| `tema` | Assunto principal do chunk. |
| `contexto_hierarquico` | Caminho hierárquico (`section_path`) comum do chunk. |
| `tipo_estrutura` | Estrutura predominante (`paragraph`, `list`, `table`, `alert_box`, `image`, `mixed`). |
| `pagina_inicio` / `pagina_fim` | Intervalo de páginas do documento de origem. |
| `elementos_origem` | Lista de IDs semânticos (`blk_XXXXXX`) do `manual_tecnico_volkswagen_ocr.jsonl` contidos no chunk. |
| `source_element_ids` | Lista consolidada de IDs primitivos (`el_XXXXXX`) para auditoria direta até a extração da Fase 2. |
| `inline_icons` | Lista de `icon_id` presentes no chunk. |
| `referenced_alerts` | Lista de IDs de `alert_box` referenciados pelos blocos do chunk. |
| `chunks_relacionados` | IDs de outros chunks semanticamente vinculados (simétrico/bidirecional). |
| `tokens` | Quantidade de tokens do campo `text`. |
| `overlap` | Indica se houve continuidade contextual/estrutural em relação ao chunk anterior. |
| `motivo_overlap` | Justificativa explícita quando `overlap: true` (ou `null` quando `false`). |
| `excecao_tamanho` | Indica se o chunk ficou abaixo de 80 tokens ou acima de 850 tokens. |
| `motivo_excecao` | Justificativa auditável da exceção de tamanho (ou `null` quando dentro dos limites). |

---

## 9. Validação de Integridade (10 Regras)

O pipeline executa automaticamente `validar_corpus_chunks` antes de homologar os arquivos, verificando:

1. `1_ids_unicos`: Todos os chunks possuem `chunk_id` únicos e não nulos.
2. `2_conteudo_nao_vazio`: Nenhum chunk possui `text` ou `embedding_text` vazios.
3. `3_referencia_elementos_origem`: Todo chunk possui rastreabilidade completa em `elementos_origem` e `source_element_ids`.
4. `4_paginas_origem_validas`: `1 <= pagina_inicio <= pagina_fim` em 100% dos registros.
5. `5_hierarquia_documental_preservada`: Todos os níveis de `contexto_hierarquico` estão presentes no cabeçalho do campo `text`.
6. `6_tabelas_markdown_integras`: Toda tabela possui cabeçalho e linha separadora `| --- |` íntegros.
7. `7_alertas_nivel_e_instrucoes_preservados`: Todos os blocos `alert_box` preservam seu marcador de severidade (`**[PERIGO]**`, `**[ATENÇÃO]**`, `**[CUIDADO]**`, `**[NOTA]**`).
8. `8_cobertura_total_sem_duplicacao_indevida`: 100% dos 4.511 blocos de conteúdo da entrada estão cobertos nos chunks, sem qualquer duplicação fora de splits intencionais com overlap.
9. `9_excecoes_tamanho_identificadas`: Qualquer chunk `< 80` ou `> 850` tokens é obrigatoriamente sinalizado em `excecao_tamanho` com um `motivo_excecao` válido.
10. `10_markdown_e_json_utf8_validos`: Todos os registros serializam em JSON UTF-8 válido.

---

## 10. Uso na Recuperação Híbrida (RAG)

* **Indexação Vetorial (Dense Retrieval):** Gerar os vetores a partir do campo `embedding_text` sobre a coleção `chunks_tecnicos.jsonl` (ou filtrando `metadata.colecao == "conteudo_tecnico"`).
* **Indexação Lexical (BM25 / Sparse Retrieval):** Indexar `embedding_text` para capturar termos exatos, siglas (`ACC`, `GRA`, `ESC`, `ASR`, `ISOFIX`, `Tiptronic`, `Keyless Access`), números de figuras (`Fig. 11`, `B6C-0155`) e códigos de ícones (`<icon:icon_XXXX>`).
* **Expansão de Contexto por Grafo de Relacionamentos:** Quando um chunk recuperado possuir `overlap: true` ou referências críticas em `referenced_alerts`, buscar complementarmente os IDs listados em `metadata.chunks_relacionados` e entregar o campo `text` ao LLM.

