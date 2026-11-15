# Método de Chunkenização — Manual Técnico Volkswagen

## 1. Objetivo

Transformar os artefatos consolidados na etapa de extração (`manual_tecnico_volkswagen_ocr.jsonl` e `manual_tecnico_volkswagen_texto_bruto.txt`) em um corpus de chunks semanticamente organizados, adequado à indexação e recuperação por sistemas RAG.

A chunkenização deve preservar o significado técnico, a hierarquia documental, o vínculo multimodal (imagens, legendas e ícones) e a rastreabilidade de cada informação.

O processo não deve resumir, reinterpretar ou acrescentar informações ao conteúdo original.

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
    └── manifest.json

```

**Descrição dos diretórios e arquivos:**

* `01_raw/`: armazena uma cópia do PDF original, preservando a fonte primária utilizada no projeto.
* `02_extracted/`: reúne os artefatos estruturados e textuais gerados a partir do processamento e reconstrução semântica (Fase 3), servindo como base única e reprodutível para a etapa de chunking:
* `manual_tecnico_volkswagen_ocr.jsonl`: representação estruturada canônica das unidades semânticas do documento (cópia direta da saída da Fase 3), mantendo todos os metadados, hierarquia de seções, descrições de imagens/ícones, itens de legenda, alertas e rastreabilidade (`source_element_ids`). Apesar da nomenclatura padronizada `_ocr.jsonl`, consolida a extração vetorial/textual e o enriquecimento visual previamente realizados, sem executar um novo OCR óptico.
* `manual_tecnico_volkswagen_texto_bruto.txt`: consolidação sequencial em texto corrido/Markdown gerada diretamente a partir do `manual_tecnico_volkswagen_ocr.jsonl`, seguindo estritamente a ordem de leitura (`reading_order`), com marcadores de página (`<!-- Página X -->`), listas formatadas, alertas estruturados, descrições visuais e tabelas convertidas para Markdown.


* `03_chunks/`: armazena os resultados da segmentação.
* `chunks.jsonl`: contém os chunks produzidos, incluindo seus textos em Markdown e metadados completos para recuperação.
* `manifest.json`: registra informações gerais do processamento, como parâmetros utilizados, quantidade de chunks, estatísticas de tokens e validações de integridade.



---

## 3. Entrada e saída

### Entrada

`chunks/02_extracted/manual_tecnico_volkswagen_ocr.jsonl` (e complementarmente `manual_tecnico_volkswagen_texto_bruto.txt` para estratégias baseadas em texto contínuo).

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

`chunks/03_chunks/chunks.jsonl`

Cada linha representa um chunk independente, contendo texto formatado em Markdown e metadados estruturados de classificação e rastreabilidade.

---

## 4. Estratégia de chunkenização

A divisão será baseada em unidades semânticas, respeitando a estrutura hierárquica do documento consolidada em `manual_tecnico_volkswagen_ocr.jsonl`.

A ordem de prioridade será:

1. Preservar a unidade de conhecimento.
2. Respeitar os limites de seções e subseções (`section_path` e `parent_section_id`).
3. Agrupar elementos que tratem do mesmo assunto.
4. Controlar o tamanho dos chunks dentro da janela de tokens desejada.

Não utilizar divisão exclusivamente por quantidade de caracteres ou tokens quando a estrutura semântica estiver disponível.

### 4.1. Agrupamento hierárquico

Os elementos devem ser processados estritamente na ordem de leitura global (`reading_order`).

A hierarquia será obtida diretamente pelos campos `section_path` e `parent_section_id`.

Cada elemento deve ser associado à sua seção correspondente, respeitando as fronteiras semânticas das seções e subseções. Não unir assuntos de seções distintas apenas para atingir um tamanho mínimo ou médio desejado.

O caminho hierárquico (`section_path`) deve ser incorporado ao cabeçalho contextual do chunk em Markdown, garantindo que seu conteúdo seja plenamente compreensível quando recuperado isoladamente.

### 4.2. Classificação e tratamento dos elementos

Os elementos de `manual_tecnico_volkswagen_ocr.jsonl` devem receber tratamento específico conforme seu `type`:

| Tipo de elemento | Tratamento |
| --- | --- |
| `heading` | Define ou atualiza o contexto hierárquico (`section_path`). Não gera chunk isoladamente. |
| `paragraph` | Agrupado com parágrafos, listas ou imagens semanticamente relacionados dentro da mesma subseção. |
| `list` | Mantida como unidade coesa (incluindo seu `lead_text` e todos os `items` em formato `-`, `1.` ou `- [x]`), preservando a ordem original. |
| `table` | Mantida em formato Markdown (`markdown`), preservando cabeçalhos, linhas, valores e tokens `<icon:icon_XXXX>`. |
| `alert_box` | Preservado como unidade indivisível (`**[{alert_level}]**` + `lead_text` + `items`), mantendo o vínculo com os blocos referenciados (`refers_to_ids`). |
| `image` | Integrada ao chunk do tópico correspondente (ou constituindo uma unidade de identificação visual quando possuir `caption`, `description` e `legend_items` extensos, como nas vistas gerais do veículo e do painel). |
| `icon` | Quando inline (`<icon:icon_XXXX>`), permanece ancorado ao texto; quando isolado (`type: "icon"`), é associado ao bloco adjacente da mesma seção com sua respectiva `description`. |

Elementos sem conteúdo recuperável não devem gerar chunks vazios.

### 4.3. Regras específicas por estrutura

**Parágrafos**

* Agrupar parágrafos consecutivos que desenvolvam o mesmo assunto dentro do mesmo `parent_section_id`.
* Não separar uma explicação de sua condição de funcionamento, limitação ou consequência.
* Evitar misturar conteúdos de subseções diferentes.

**Listas e Checklists**

* Preservar o `lead_text` colado aos `items`, bem como a estilização (`unordered`, `ordered` ou `checklist` `- [x]`).
* Não dividir uma lista entre chunks, salvo quando ela sozinha ultrapassar o limite máximo de tokens.
* Se fragmentada, repetir o `lead_text` e o cabeçalho hierárquico em cada fragmento e sinalizar a continuidade.

**Tabelas**

* Utilizar integralmente a tabela Markdown já reconstruída e unificada no campo `markdown`.
* Preservar cabeçalhos, unidades de medida, valores, referências de página e ícones inline.
* Não separar a tabela do parágrafo introdutório que a contextualiza quando ambos couberem no limite de tokens.
* Se uma tabela exceder o limite máximo (ex.: tabela de luzes de advertência ou de menus do rádio), fragmentar por grupos de linhas completas, repetindo obrigatoriamente o cabeçalho Markdown (`| Coluna | ... |` e `| --- | ... |`) em cada fragmento.

Exemplo:

| Componente | Especificação |
| --- | --- |
| Comprimento | 4.266 mm |
| Largura | 1.976 mm |
| Entre-eixos | 2.566 mm |

**Alertas (`alert_box`)**

* Não separar o `alert_level` (`PERIGO`, `ATENÇÃO`, `CUIDADO`, `NOTA`) de seu `lead_text` e de seus `items`.
* Quando o alerta tiver `alert_scope: "local_topic"` e couber junto ao procedimento ou explicação a que se refere (`refers_to_ids`), mantê-lo no mesmo chunk ou registrar o vínculo direto em `chunks_relacionados`.
* Quando houver múltiplos alertas introdutórios (`alert_scope: "chapter_intro"`), agrupá-los respeitando o limite de tokens sem quebrar um `alert_box` ao meio.

**Imagens, Legendas e Ícones**

* Renderizar `caption` (ou `figure_label`), `description` (`*[Imagem: ...]*`) e todos os `legend_items` do objeto `image`.
* Figuras panorâmicas com `legend_items` detalhados (ex.: `Fig. 3` a `Fig. 11`) constituem unidades ricas de identificação de componentes e devem manter imagem, descrição e itens de legenda unidos.
* Preservar os marcadores `<icon:icon_XXXX>` e registrar as referências visuais (`inline_icons`, `asset_id`) nos metadados.
* Não inventar descrições ou significados além dos presentes no `manual_tecnico_volkswagen_ocr.jsonl`.

---

## 5. Dimensionamento

Os seguintes valores serão utilizados inicialmente como parâmetros experimentais:

| Parâmetro | Valor |
| --- | --- |
| Tamanho desejado | 350–650 tokens |
| Limite máximo | 850 tokens |
| Tamanho mínimo | 80 tokens |
| Overlap | Semântico / Contextual |
| Unidade de agrupamento | Unidade de conhecimento |

O tamanho deve ser calculado por um tokenizador compatível com o modelo de embeddings utilizado.

### 5.1. Quando o chunk ultrapassar o limite

A divisão deve ocorrer em uma fronteira semântica limpa (entre blocos `blk_XXXXXX`), seguindo esta prioridade:

1. Entre subseções ou assuntos independentes.
2. Entre grupos de parágrafos ou entre texto explicativo e caixa de alerta.
3. Entre etapas completas de um procedimento ou grupos de itens de uma legenda extensa.
4. Entre grupos de linhas de uma tabela (repetindo o cabeçalho da tabela).

Não dividir frases, itens individuais de lista, `alert_box` únicos ou linhas de tabela ao meio.

Quando uma unidade atômica não puder ser dividida sem perda de significado, ela poderá ultrapassar o limite máximo, registrando `"excecao_tamanho": true` nos metadados.

### 5.2. Overlap

Nas estratégias estruturadas, não utilizar overlap cego por contagem fixa de caracteres quando a divisão ocorrer entre blocos semânticos independentes.

A continuidade contextual entre chunks resultantes da divisão de uma mesma seção ou tabela é garantida pela repetição do cabeçalho hierárquico (`section_path`), do `lead_text` (em listas/legendas divididas) ou do cabeçalho de colunas (em tabelas divididas), registrando `"overlap": true` e vinculando os IDs em `chunks_relacionados`.

---

## 6. Estrutura textual do chunk

Todo chunk deve possuir contexto suficiente para ser interpretado isoladamente no momento da busca e da geração de resposta pelo LLM.

Formato recomendado:

```markdown
# [Capítulo (Nível 1)]

## [Seção (Nível 2)]

### [Subseção (Nível 3)]

**Assunto:** [Tema principal]

[Conteúdo original organizado em Markdown]

```

Os títulos (`#`, `##`, `###`, `####`) devem refletir fielmente os níveis presentes no `section_path` do bloco no `manual_tecnico_volkswagen_ocr.jsonl`.

O campo `text` deve conter apenas o conteúdo textual e estrutural em Markdown, mantendo os identificadores técnicos de rastreabilidade (`blk_XXXXXX`, `el_XXXXXX`) exclusivamente no objeto `metadata`.

---

## 7. Classificação dos chunks

Cada chunk deve receber classificações nos metadados que facilitem a recuperação híbrida e a filtragem.

### 7.1. Tipo de conhecimento (`tipo_conhecimento`)

* `explanation`: explicações e conceitos gerais.
* `procedure`: procedimentos e instruções passo a passo ou checklists.
* `specification`: características, capacidades, dimensões e valores técnicos.
* `table`: conteúdo originado predominantemente de tabelas.
* `warning`: advertências e caixas de alerta (`PERIGO`, `ATENÇÃO`, `CUIDADO`, `NOTA`).
* `troubleshooting`: soluções de problemas, falhas, causas e ações corretivas.
* `operation`: instruções de operação de comandos, botões e menus.
* `maintenance`: manutenção, conservação, verificação e inspeção.
* `identification`: vistas gerais, diagramas e identificação de componentes/símbolos.
* `reference`: sumário, índice, garantia e informações complementares.

### 7.2. Dimensão funcional

Identificar, a partir do `section_path` e do conteúdo efetivamente presente:

* `sistema`: sistema automotivo principal (ex.: `informacoes_ao_condutor`, `seguranca`, `abrir_e_fechar`, `iluminacao`, `visibilidade`, `climatizacao`, `conducao`, `assistencia_ao_condutor`).
* `componente`: componente ou subsistema específico (ex.: `instrumento_combinado`, `cinto_de_seguranca`, `airbag`, `cadeira_de_crianca`, `chave_ do_veiculo`, `transmissao_automatica`).
* `tema`: assunto principal abordado no chunk.

Não inferir componentes ou sistemas que não estejam sustentados pelo conteúdo ou pelo seu `section_path`.

---

## 8. Formato JSON de saída

Cada linha de `chunks/03_chunks/chunks.jsonl` deve seguir este modelo:

```json
{
  "chunk_id": "vw_000001",
  "text": "# Sistemas de assistência ao condutor\n\n## Sistema regulador de velocidade (GRA)\n\n### ⚠️ Introdução ao tema\n\n**Assunto:** Funcionamento e faixa de velocidades do GRA\n\nO sistema regulador de velocidade (GRA) auxilia o condutor a manter constante uma velocidade configurada...",
  "metadata": {
    "tipo_conhecimento": "explanation",
    "sistema": "assistencia_ao_condutor",
    "componente": "sistema_regulador_de_velocidade_gra",
    "tema": "Introdução e funcionamento do GRA",
    "contexto_hierarquico": [
      "Sistemas de assistência ao condutor",
      "Sistema regulador de velocidade (GRA)",
      "⚠️ Introdução ao tema"
    ],
    "tipo_estrutura": "paragraph",
    "pagina_inicio": 121,
    "pagina_fim": 121,
    "elementos_origem": [
      "blk_000910",
      "blk_000911"
    ],
    "source_element_ids": [
      "el_007810",
      "el_007811"
    ],
    "inline_icons": [],
    "referenced_alerts": [
      "blk_000918"
    ],
    "chunks_relacionados": [],
    "tokens": 385,
    "overlap": false,
    "excecao_tamanho": false
  }
}

```

### 8.1. Significado dos metadados

| Campo | Significado |
| --- | --- |
| `chunk_id` | Identificador único sequencial do chunk (ex.: `vw_000001`). |
| `tipo_conhecimento` | Classificação semântica do conteúdo. |
| `sistema` | Sistema automotivo relacionado. |
| `componente` | Componente ou funcionalidade relacionada. |
| `tema` | Assunto principal do chunk. |
| `contexto_hierarquico` | Caminho hierárquico (`section_path`) de origem. |
| `tipo_estrutura` | Estrutura predominante (`paragraph`, `list`, `table`, `alert_box`, `image`, `mixed`). |
| `pagina_inicio` | Menor `page_start` entre os blocos que compõem o chunk. |
| `pagina_fim` | Maior `page_end` entre os blocos que compõem o chunk. |
| `elementos_origem` | Lista de IDs semânticos (`blk_XXXXXX`) do `manual_tecnico_volkswagen_ocr.jsonl` que compõem o chunk. |
| `source_element_ids` | Lista consolidada de IDs primitivos (`el_XXXXXX`) para auditoria direta até a extração da Fase 2. |
| `inline_icons` | Lista de `icon_id` presentes no chunk. |
| `referenced_alerts` | Lista de IDs de `alert_box` referenciados pelos blocos do chunk. |
| `chunks_relacionados` | IDs de outros chunks semanticamente relacionados (ex.: continuação de tabela/lista ou alerta vinculado). |
| `tokens` | Quantidade de tokens do campo `text`. |
| `overlap` | Indica se houve repetição contextual/estrutural entre chunks adjacentes. |
| `excecao_tamanho` | Indica se o chunk ultrapassou o limite máximo de 850 tokens para preservar uma unidade indivisível. |

Campos sem informação suficiente devem receber `null` ou listas vazias `[]`. Não preencher metadados por suposição.

---

## 9. Validação

Antes de homologar o `chunks.jsonl` e gerar o `manifest.json`, executar as seguintes verificações:

1. Todos os chunks possuem `chunk_id` únicos e sequenciais.
2. Nenhum chunk possui `text` vazio.
3. Todo chunk possui rastreabilidade completa em `elementos_origem` (`blk_XXXXXX`) e `source_element_ids` (`el_XXXXXX`).
4. Os intervalos `pagina_inicio` e `pagina_fim` são válidos e consistentes (`pagina_inicio <= pagina_fim`).
5. A hierarquia documental (`contexto_hierarquico`) foi preservada no texto e nos metadados.
6. Tabelas mantêm cabeçalhos Markdown válidos, valores e unidades em todos os fragmentos.
7. Caixas de alerta (`alert_box`) mantêm seus níveis de severidade e instruções completas.
8. Imagens mantêm suas descrições visuais e seus `legend_items` associados.
9. Nenhum bloco de conteúdo do `manual_tecnico_volkswagen_ocr.jsonl` (exceto `heading` puros, que viram cabeçalhos) foi omitido ou duplicado indevidamente.
10. Todos os registros possuem texto válido em Markdown e JSON válido codificado em UTF-8.

---

## 10. Recuperação híbrida

O corpus será preparado para busca híbrida, combinando:

* **Busca vetorial (Dense Retrieval):** recuperação por similaridade semântica sobre o campo `text` enriquecido com cabeçalhos hierárquicos e descrições visuais.
* **Busca lexical (BM25 / Sparse Retrieval):** recuperação exata por termos técnicos, siglas (`ACC`, `GRA`, `ESC`, `ASR`, `ISOFIX`, `LATCH`, `Tiptronic`, `Keyless Access`), códigos de figuras (`Fig. 11`, `B6C-0155`), tokens de ícones (`<icon:icon_XXXX>`) e valores numéricos.
* **Filtros por metadados:** restrição por `sistema`, `componente`, `tipo_conhecimento`, `contexto_hierarquico` e intervalo de páginas.

---

## 11. Resultado esperado

O resultado será um corpus canônico de chunks em `chunks/03_chunks/chunks.jsonl`:

* semanticamente completos e autocontidos;
* organizados segundo a hierarquia real do manual;
* com tabelas de uma ou múltiplas páginas representadas em Markdown;
* com alertas (`PERIGO`, `ATENÇÃO`, `CUIDADO`, `NOTA`), checklists e procedimentos preservados;
* com imagens, descrições visuais e legendas numeradas integradas;
* 100% rastreáveis até os blocos de `manual_tecnico_volkswagen_ocr.jsonl` (`blk_XXXXXX`) e elementos primitivos (`el_XXXXXX`);
* prontos para indexação e avaliação comparativa em sistemas RAG.
