# Método de Chunkenização — Manual Técnico Volkswagen

## 1. Objetivo

Transformar o `document_preparado.jsonl` em um corpus de chunks semanticamente organizados, adequado à indexação e recuperação por sistemas RAG.

A chunkenização deve preservar o significado técnico, a hierarquia documental e a rastreabilidade de cada informação.

O processo não deve resumir, reinterpretar ou acrescentar informações ao conteúdo original.

### 2. **Estrutura de diretórios**
```text
chunks/
│
├── 01_raw/
│   └── manual_tecnico_volkswagen.pdf
│
├── 02_extracted/
│   ├── document_preparado.jsonl
│   ├── manual_tecnico_volkswagen_ocr.jsonl
│   └── manual_tecnico_volkswagen_texto_bruto.txt
│
└── 03_chunks/
    ├── chunks.jsonl
    └── manifest.json
```

**Descrição dos diretórios e arquivos:**

- `01_raw/`: armazena uma cópia do PDF original, preservando a fonte utilizada no projeto.
- `02_extracted/`: reúne os dados produzidos a partir de `document_preparado.jsonl`.
  - `document_preparado.jsonl`: cópia do arquivo de entrada da etapa de chunking, garantindo a reprodutibilidade e a independência do processamento anterior.
  - `manual_tecnico_volkswagen_ocr.jsonl`: representação estruturada dos elementos textuais, mantendo seus metadados e informações de origem. Apesar do nome, não representa uma nova execução de OCR.
  - `manual_tecnico_volkswagen_texto_bruto.txt`: consolidação textual dos elementos na ordem de leitura, com as tabelas convertidas para Markdown.
- `03_chunks/`: armazena os resultados da segmentação.
  - `chunks.jsonl`: contém os chunks produzidos, incluindo seus textos e metadados para recuperação.
  - `manifest.json`: registra informações gerais do processamento, como parâmetros utilizados, quantidade de chunks, estatísticas e validações.

## 3. Entrada e saída

### Entrada

`document_preparado.jsonl`

Cada registro representa um elemento documental previamente identificado, podendo conter:

- `id`: identificador do elemento.
- `type`: tipo de conteúdo.
- `content`: conteúdo textual, quando aplicável.
- `section_path`: caminho hierárquico da seção.
- `parent_section_id`: identificador da seção-pai.
- `page_start` e `page_end`: paginação de origem.
- `source_element_ids`: identificadores dos elementos originais.
- `inline_icons`: ícones associados ao conteúdo.
- `referenced_alerts`: alertas relacionados.

Outros campos específicos de cada tipo devem ser preservados quando forem relevantes.

### Saída

`chunks.jsonl`

Cada linha representa um chunk independente, contendo texto em Markdown e metadados de classificação e rastreabilidade.

## 4. Estratégia de chunkenização

A divisão será baseada em unidades semânticas, respeitando a estrutura hierárquica do documento.

A ordem de prioridade será:

1. Preservar a unidade de conhecimento.
2. Respeitar os limites de seções e subseções.
3. Agrupar elementos que tratem do mesmo assunto.
4. Controlar o tamanho dos chunks.

Não utilizar divisão exclusivamente por quantidade de caracteres ou tokens.

### 4.1. Agrupamento hierárquico

Os elementos devem ser processados na ordem de leitura (`reading_order`).

A hierarquia será obtida por `section_path` e `parent_section_id`.

Cada elemento deve ser associado à sua seção correspondente.

Os agrupamentos devem respeitar as fronteiras semânticas das seções. Não unir assuntos diferentes apenas para atingir um tamanho desejado.

O caminho hierárquico deve ser incorporado ao contexto textual do chunk, para que seu conteúdo continue compreensível quando recuperado isoladamente.

### 4.2. Classificação dos elementos

Os elementos devem receber um tratamento específico conforme seu `type`.

| Tipo de elemento | Tratamento |
|---|---|
| `heading` | Define ou atualiza o contexto hierárquico. Não gera chunk isoladamente. |
| `paragraph` | Agrupado com parágrafos semanticamente relacionados. |
| `list` | Mantida como unidade, preservando a ordem dos itens. |
| `table` | Convertida para tabela Markdown, preservando cabeçalhos, linhas e valores. |
| `alert_box` | Preservado como unidade, incluindo nível, descrição e instruções associadas. |
| `image` | Não gera chunk textual isoladamente. Descrição e legenda podem integrar o contexto relacionado. |
| `icon` | Associado ao texto correspondente, preservando seu significado quando disponível. |

Elementos sem conteúdo recuperável não devem gerar chunks vazios.

### 4.3. Regras específicas

**Parágrafos**

- Agrupar parágrafos consecutivos que desenvolvam o mesmo assunto.
- Não separar uma explicação de sua definição, condição ou consequência.
- Evitar misturar conteúdos de subseções diferentes.

**Listas**

- Preservar a ordem e a estrutura dos itens.
- Não dividir uma lista entre chunks, salvo quando ultrapassar o limite máximo.
- Se fragmentada, repetir o contexto e identificar a continuidade.

**Tabelas**

- Converter integralmente para Markdown.
- Preservar cabeçalhos, unidades de medida, valores e observações.
- Manter a relação entre cada linha e suas colunas.
- Não separar a tabela de sua introdução ou explicação quando forem necessárias para compreendê-la.
- Se exceder o limite máximo, fragmentar por grupos de linhas, repetindo os cabeçalhos em cada fragmento.

Exemplo:

| Componente | Especificação |
|---|---|
| Comprimento | 4.266 mm |
| Largura | 1.976 mm |
| Entre-eixos | 2.566 mm |

**Alertas**

- Não separar o nível de risco de sua descrição.
- Preservar o texto de orientação e as instruções associadas.
- Não misturar alertas diferentes em um único bloco sem relação semântica.
- Quando um alerta estiver associado a um procedimento, manter a relação entre eles por metadados.

**Imagens e ícones**

- Utilizar descrições e legendas disponíveis no documento preparado.
- Associar imagens e ícones ao assunto correspondente quando a relação estiver identificada.
- Não inventar descrições ou significados.
- Registrar as referências dos elementos visuais nos metadados.

## 5. Dimensionamento

Os seguintes valores serão utilizados inicialmente como parâmetros experimentais:

| Parâmetro | Valor |
|---|---:|
| Tamanho desejado | 350–650 tokens |
| Limite máximo | 850 tokens |
| Tamanho mínimo | 80 tokens |
| Overlap | Semântico |
| Unidade de agrupamento | Unidade de conhecimento |

O tamanho deve ser calculado por um tokenizador compatível com o modelo de embeddings utilizado.

### 5.1. Quando o chunk ultrapassar o limite

A divisão deve ocorrer em uma fronteira semântica, seguindo esta prioridade:

1. Entre assuntos independentes.
2. Entre grupos de parágrafos.
3. Entre etapas completas de um procedimento.
4. Entre grupos de linhas de uma tabela.

Não dividir frases, itens de lista ou linhas de tabela arbitrariamente.

Quando uma unidade não puder ser dividida sem perda de significado, ela poderá ultrapassar o limite máximo. Essa exceção deverá ser registrada nos metadados.

### 5.2. Overlap

Não utilizar overlap fixo por quantidade de tokens.

Quando a divisão de uma unidade exigir continuidade entre chunks, incluir apenas o contexto necessário para preservar o significado.

O overlap deve ser registrado nos metadados, identificando os chunks relacionados.

## 6. Estrutura textual do chunk

Todo chunk deve possuir contexto suficiente para ser interpretado isoladamente.

Formato recomendado:

```markdown
# [Capítulo]

## [Seção]

### [Subseção]

**Assunto:** [Tema principal]

[Conteúdo original organizado em Markdown]
```

Os títulos devem ser utilizados somente quando houver informação correspondente na hierarquia documental.

O campo `text` deve conter o conteúdo textual final, sem informações técnicas de rastreabilidade que pertencem aos metadados.

## 7. Classificação dos chunks

Cada chunk deve receber classificações que facilitem a recuperação híbrida.

### 7.1. Tipo de conhecimento

Valores recomendados:

- `explanation`: explicações e conceitos.
- `procedure`: procedimentos e instruções passo a passo.
- `specification`: características e valores técnicos.
- `table`: conteúdo originado de tabelas.
- `warning`: advertências e alertas.
- `troubleshooting`: problemas, causas e soluções.
- `operation`: instruções de operação.
- `maintenance`: manutenção e inspeção.
- `identification`: identificação de componentes.
- `reference`: informações complementares.

A classificação deve ser atribuída conforme o conteúdo efetivamente presente.

### 7.2. Dimensão funcional

Identificar, quando possível:

- Sistema do veículo.
- Componente.
- Funcionalidade.
- Tema principal.

Não inferir componentes ou sistemas que não estejam identificados no conteúdo ou em seu contexto hierárquico.

## 8. Formato JSON de saída

Cada linha de `chunks.jsonl` deve seguir este modelo:

```json
{
  "chunk_id": "vw_000001",
  "text": "# Sistemas de assistência\n\nConteúdo...",
  "metadata": {
    "tipo_conhecimento": "explanation",
    "sistema": "assistencia",
    "componente": "controle_de_velocidade",
    "tema": "Funcionamento do sistema",
    "contexto_hierarquico": [
      "Sistemas de assistência",
      "Controle de velocidade"
    ],
    "tipo_estrutura": "paragraph",
    "pagina_inicio": 120,
    "pagina_fim": 121,
    "elementos_origem": [
      "blk_000120",
      "blk_000121"
    ],
    "chunks_relacionados": [],
    "tokens": 420,
    "overlap": false,
    "excecao_tamanho": false
  }
}
```

### 8.1. Significado dos metadados

| Campo | Significado |
|---|---|
| `chunk_id` | Identificador único do chunk. |
| `tipo_conhecimento` | Classificação semântica do conteúdo. |
| `sistema` | Sistema automotivo relacionado. |
| `componente` | Componente ou funcionalidade relacionada. |
| `tema` | Assunto principal. |
| `contexto_hierarquico` | Hierarquia documental de origem. |
| `tipo_estrutura` | Estrutura predominante do conteúdo. |
| `pagina_inicio` | Primeira página de origem. |
| `pagina_fim` | Última página de origem. |
| `elementos_origem` | IDs dos registros que compõem o chunk. |
| `chunks_relacionados` | IDs de outros chunks semanticamente relacionados. |
| `tokens` | Quantidade de tokens do texto final. |
| `overlap` | Indica se existe sobreposição contextual. |
| `excecao_tamanho` | Indica se o chunk ultrapassou o limite máximo. |

Campos sem informação suficiente devem receber `null`, listas vazias ou ser omitidos conforme o esquema definido. Não preencher metadados por suposição.

## 9. Validação

Antes de gerar o corpus definitivo, executar as seguintes verificações:

1. Todos os chunks possuem identificadores únicos.
2. Nenhum chunk possui conteúdo vazio.
3. Todo chunk possui referência aos elementos de origem.
4. As páginas de origem são válidas.
5. A hierarquia documental foi preservada.
6. Tabelas mantêm cabeçalhos, valores e unidades.
7. Alertas mantêm seus níveis e instruções.
8. Nenhum conteúdo foi duplicado indevidamente.
9. Exceções de tamanho estão identificadas.
10. Todos os chunks possuem texto válido em Markdown e JSON válido em UTF-8.

Também deve ser possível reconstruir a relação entre cada chunk e os registros do `document_preparado.jsonl`.

## 10. Recuperação híbrida

O corpus será preparado para busca híbrida, combinando:

- **Busca vetorial:** recuperação por similaridade semântica.
- **Busca lexical:** recuperação por termos, expressões, códigos e nomes técnicos.
- **Filtros por metadados:** restrição por sistema, componente, tipo de conhecimento e contexto hierárquico.

Os metadados não substituem o conteúdo textual. Eles complementam a recuperação.

Valores técnicos, códigos, siglas, nomes de componentes e unidades de medida devem ser preservados para favorecer a busca lexical.

A etapa de indexação deverá utilizar o campo `text` como conteúdo principal e os metadados como atributos de filtragem.

## 11. Resultado esperado

O resultado será um corpus canônico de chunks:

- semanticamente completos;
- organizados hierarquicamente;
- com tabelas representadas em Markdown;
- com alertas e procedimentos preservados;
- rastreáveis até os elementos e páginas de origem;
- preparados para busca híbrida e sistemas RAG.

A chunkenização deve priorizar a integridade do conhecimento técnico, não a produção de uma quantidade predeterminada de chunks.