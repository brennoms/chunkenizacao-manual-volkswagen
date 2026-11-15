# Estratégia e Modelo de Dados — Preparação e Reconstrução Semântica

Este documento especifica os métodos e regras utilizados para transformar os dados físicos da extração bruta (`document.jsonl`) em um fluxo textual estruturado, ordenado e enriquecido semanticamente (`document_preparado.jsonl`), pronto para a etapa de *chunking*.

## 1. Objetivo

Converter elementos posicionais brutos (textos, formas, vetores, imagens e links) em unidades lógicas de leitura contínua, eliminando ruídos de paginação, unificando ícones compostos, reconstruindo tabelas em Markdown e vinculando caixas de alerta e imagens aos seus respectivos contextos.

As coordenadas geométricas (`bbox`) e as linhas divisórias (`shape`) são utilizadas internamente durante o processamento das Fases 1, 2 e 3, sendo descartadas na geração do arquivo final em favor da ordem global de leitura (`reading_order`) e da hierarquia de seções (`section_path`).

---

## 2. Arquitetura do Pipeline em Três Fases

### 2.1. Fase 1 — Saneamento Espacial e Filtragem de Ruído

Remove elementos exclusivos da mídia física de impressão ou paginação que interrompem o fluxo textual.

#### Regras de Descarte
1. **Elementos externos à página (*Crop Marks* e *Bleed*):** elementos cuja área de interseção com os limites da página `[0, 0, width, height]` seja inferior a 5% de sua própria área.
2. **Código vertical da literatura:** elementos de texto na margem lateral esquerda ($x < 25\text{ pt}$) ou com rotação vertical (`rotation == 270.0`, ex.: `"21B.5B1.NIV.66"`).
3. **Rodapés repetitivos e paginação:** textos e linhas verticais curtas situados na faixa inferior da página ($y > 562\text{ pt}$) contendo repetição de título de seção ou número de página.
4. **Marcadores tipográficos de controle de fluxo:** elementos compostos exclusivamente por glifos isolados de continuação ou encerramento de seção (`▸`, `▪`, `◁`).

#### Preservação Temporária
Fios horizontais, fios verticais e retângulos internos (`shape`) situados dentro da área útil da página **não** são descartados na Fase 1, pois atuam como delimitadores físicos de tabelas e caixas de alerta na Fase 3.

#### Homologação Visual da Fase 1
Geração do arquivo `debug_visual/debug_fase1_removidos.pdf`, demarcando exclusivamente os elementos descartados com **retângulos vermelhos tracejados** e seus respectivos IDs. Nenhum texto de conteúdo, célula de tabela ou ícone do veículo pode estar contido em caixas vermelhas tracejadas.

---

### 2.2. Fase 2 — Consolidação e Desduplicação Global de Ícones

Agrupa primitivas gráficas sobrepostas que formam um único símbolo visual e atribui um identificador único compartilhado (`icon_id`) para ocorrências equivalentes em todo o documento.

#### Procedimento Técnico
1. **Seleção de Candidatos:** todos os elementos `vector`, elementos `shape` compactos ($\text{width} \le 40\text{ pt}$ e $\text{height} \le 40\text{ pt}$, excluindo linhas longas de grade) e textos curtos ($\le 5$ caracteres, como `!`, `i`, `P`, `ABS`, `OFF`, números de chamadas) sobrepostos a vetores ou formas.
2. **Fusão Espacial (*Union-Find*):** candidatos na mesma página cujas caixas delimitadoras se interceptam (com tolerância de expansão de $1.5\text{ pt}$) são fundidos em um único elemento `"type": "icon"`, assumindo o `bbox` envolvente do conjunto.
3. **Identificadores e Desduplicação:**
   - A região do `bbox` de cada ícone consolidado é recortada da página renderizada e submetida a um cálculo de **Hash Perceptual (`pHash` de 64 bits)**. A distância de Hamming é usada para aceitar pequenas variações de rasterização, em conjunto com a cor predominante e a proporção do `bbox`.
   - As paridades formam componentes transitivos: se A tem paridade com B e B tem paridade com C, os três recebem o mesmo `icon_id`, mesmo que A e C não tenham paridade direta.
   - Cada ocorrência de ícone recebe um `icon_order_id` inteiro sequencial global, começando em `1` no primeiro ícone do PDF e incrementando a cada instância, mesmo quando o ícone é igual a outro.
   - Ícones visualmente equivalentes recebem o mesmo `icon_id` global (ex.: `icon_0001`), gerando uma única imagem de referência em `assets/icons/icon_0001.png` e um registro no catálogo `icons_catalog.json`. Portanto, `icon_id` identifica o modelo visual compartilhado, enquanto `icon_order_id` identifica a instância no PDF.
   - O dicionário `ICONES_SEMELHANTES_MANUAIS`, no início de `src/preparacao/fase2.py`, permite informar grupos de ocorrências semelhantes manualmente, usando o `icon_order_id` mostrado na matriz de paridade. Os itens de cada grupo e quaisquer ocorrências visualmente semelhantes a eles recebem o mesmo `icon_id`.
   - O dicionário `ICONES_SEMELHANTES_MANUAIS_RESTRITOS`, no mesmo arquivo, permite informar grupos que só devem ser comparados entre si. Esses itens recebem o mesmo `icon_id`, mas não são comparados nem associados automaticamente a ocorrências fora do próprio grupo.
4. **Classificação Posicional do Ícone:**
   - **Inline (`inline`):** ícones contidos na faixa vertical de uma linha de texto são convertidos na marcação `<icon:icon_XXXX>` diretamente dentro da string de texto do parágrafo.
   - **Bloco/Coluna (`standalone`):** ícones isolados (como em colunas de tabelas de luzes de advertência ou cabeçalhos de alerta) são vinculados diretamente à estrutura pai (célula da tabela ou caixa de alerta).

#### Captura e reutilização dos ícones

O processamento é controlado pela flag `BUSCAR_ICONES`, definida no início de
`src/preparacao/fase2.py`:

- `BUSCAR_ICONES = True`: renderiza cada ocorrência encontrada e salva a captura
  em `assets/all_icons/icon_XXXX.png`, usando o `icon_order_id` como ordem do
  arquivo. Também atualiza as imagens deduplicadas em `assets/icons/` e a matriz
  de paridade.
- `BUSCAR_ICONES = False`: não renderiza o PDF. Carrega as capturas existentes
  em `assets/all_icons/` pela ordem de `icon_order_id` e executa normalmente o
  cálculo de pHash, a comparação, o agrupamento, a deduplicação e a matriz de
  paridade.

A primeira execução deve ser feita com a flag habilitada para criar
`assets/all_icons/`. Execuções posteriores podem desabilitá-la para reutilizar
as capturas sem gerar novos recortes. Se uma captura esperada não existir, o
processamento é interrompido para evitar comparações incompletas.

#### Homologação Visual da Fase 2
Geração do arquivo `debug_visual/debug_fase2_icones.pdf`, demarcando cada ícone consolidado com uma caixa única e seu respectivo `icon_id` compartilhado. A matriz de paridade exibe o `icon_order_id` acima de cada ocorrência para facilitar a configuração dos grupos manuais.

---

### 2.3. Fase 3 — Reconstrução Semântica, Ordem de Leitura, Alertas, Tabelas e Imagens

Converte os elementos limpos e consolidados na sequência lógica de leitura e estabelece os vínculos estruturais.

#### 1. Ordem de Leitura por Zonas (`reading_order`)
- A página é segmentada horizontalmente por elementos de largura total ($\text{width} > 210\text{ pt}$, como títulos de capítulo ou ilustrações largas).
- Nos intervalos entre elementos de largura total, o fluxo percorre integralmente a **Coluna Esquerda** ($\text{centro\_x} < 210\text{ pt}$) de cima para baixo antes de iniciar a **Coluna Direita** ($\text{centro\_x} \ge 210\text{ pt}$).
- O atributo `reading_order` é um inteiro sequencial global ($1 \dots N$) ao longo de todo o documento.
- O atributo `read_order` registra a ordem de leitura de cada elemento, percorrendo os elementos de cima para baixo e, em seguida, da esquerda para a direita.

#### 2. Hierarquia de Títulos (`heading` e `section_path`)
Os estilos tipográficos brutos são convertidos em níveis hierárquicos (`level` de 1 a 4) e descartados após a classificação:
- **Nível 1 (`level: 1`):** fonte `VWHead-Bold`, tamanho $\ge 13\text{ pt}$, cor azul `[0, 96, 175]` (Capítulo).
- **Nível 2 (`level: 2`):** fonte `VWHead-Bold`, tamanho $10.5\text{–}12.5\text{ pt}$ ou texto inserido em faixa retangular de destaque (Seção).
- **Nível 3 (`level: 3`):** fonte `VWHead-Bold`, tamanho $8.5\text{–}10\text{ pt}$, cor azul (Subseção).
- **Nível 4 (`level: 4`):** fonte `VWText-Bold`, tamanho $7.5\text{ pt}$ em linha isolada (Tópico local).

Cada título atualiza a pilha de contexto ativa, preenchendo o atributo `section_path` (lista ordenada de títulos ancestrais) em todos os elementos subsequentes.

#### 3. Costura de Parágrafos e Listas (*Cross-Column / Cross-Page Stitching*)
Quando o último bloco textual de uma coluna ou página termina sem pontuação final (`.`, `!`, `?`, `:`) ou com palavra hifenizada, ele é fundido ao primeiro bloco textual compatível da coluna ou página seguinte, registrando o intervalo `[page_start, page_end]`.

#### 4. Caixas de Alerta (`alert_box`) e Vínculo de Escopo
- **Delimitação:** inicia em cabeçalhos destacados por retângulo colorido contendo `PERIGO`, `ATENÇÃO`, `CUIDADO` ou `NOTA` (associados ao seu `icon_id`), ou em blocos iniciados por ícones de nota informativa/ambiental. Absorve todos os parágrafos e itens de lista subsequentes (inclusive através de quebras de coluna ou página) até atingir a linha horizontal delimitadora de fechamento (`shape_type == "line"`).
- **Vínculo de Referência (`alert_scope` e `refers_to_ids`):**
  - `chapter_intro`: quando o alerta está situado sob o tópico `"Introdução ao tema"`, vinculando-se ao ID da seção principal.
  - `local_topic`: quando situado no corpo de um procedimento específico, vinculando-se ao ID do subtítulo ou bloco de instruções imediatamente anterior.
  - `cross_reference`: avisos do tipo `"Observe ⚠️ no início desse capítulo na página X"` geram vínculo explícito apontando para os IDs dos elementos `alert_box` localizados na página `X`.

#### 5. Reconstrução de Tabelas em Markdown (`table`)
Gradeamento formado pela interseção de fios horizontais e verticais (`shape`). O conteúdo textual e os `icon_id`s (formatados como `<icon:icon_XXXX>`) contidos em cada célula `[linha, coluna]` são convertidos em sintaxe de tabela Markdown padronizada. Tabelas que continuam na coluna ou página seguinte com o mesmo cabeçalho são unificadas.

#### 6. Estruturação de Imagens e Legendas (`image`)
Cada elemento de imagem recebe:
- `id_image`: número identificador da imagem, utilizado como chave no documento JSON de descrições.
- `image_path`: caminho relativo do arquivo principal na pasta de extração, em `assets/`.
- `caption`: texto da legenda associada (ex.: `"Fig. 20 ..."`).
- `legend_items`: lista ordenada dos itens explicativos numerados associados à figura (`"Legenda para → Fig. X"`), com os números de chamada já concatenados ao texto.
- `description`: descrição da imagem obtida a partir do arquivo `descricao_images.json`, no qual a chave é o número da imagem (`id_image`) e o valor é a descrição correspondente.

O arquivo `descricao_images.json` deve seguir o formato:

```json
{
  "10": "Descrição da imagem do instrumento combinado analógico no painel de instrumentos."
}
```

Durante a preparação, o valor de `id_image` é utilizado para localizar a descrição correspondente nesse documento e preencher o campo `description`.

#### Homologação Visual da Fase 3 (Grafo de Ordem de Leitura)
Antes do descarte final das coordenadas geométricas, é gerado o arquivo `debug_visual/debug_fase3_ordem_leitura.pdf` sobre o documento original:
1. **Caixas Semânticas:** cada unidade consolidada (`heading`, `paragraph`, `list`, `alert_box`, `table`, `image`) é envolvida por um retângulo colorido conforme seu tipo semântico e identificada com seu número de ordem (`#reading_order`).
2. **Trajetória Contínua de Leitura:** é desenhada uma linha direcionada contínua (vetor com seta/marcador de fluxo) partindo do ponto central do 1º elemento (`reading_order = 1` da página) para o 2º elemento, do 2º para o 3º, e assim sucessivamente até o último elemento da página, permitindo inspecionar visualmente a transição entre títulos de largura total, coluna esquerda e coluna direita.

---

## 3. Estrutura de Armazenamento

Os assets das imagens extraídas permanecem na pasta de extração
(`conteudo_extraido/assets/`). O caminho registrado em `image_path` é relativo
ao diretório da extração, normalmente no formato `assets/asset_000010.png`.

```text
dados_preparados/
├── document_fase1.jsonl
├── document_fase2.jsonl
├── document_preparado.jsonl
├── icons_catalog.json
├── descricao_images.json
├── assets/
│   ├── all_icons/
│   │   ├── icon_0001.png
│   │   ├── icon_0002.png
│   │   └── ...
│   ├── icons/
│       ├── icon_0001.png
│       ├── icon_0002.png
│       └── ...
│   └── paridade/
│       ├── icon_0001.png
│       ├── icon_0002.png
│       └── ...
└── debug_visual/
    ├── debug_fase1_removidos.pdf
    ├── debug_fase2_icones.pdf
    └── debug_fase3_ordem_leitura.pdf

```

O catálogo `icons_catalog.json` também possui um resumo com `icones_encontrados` (ocorrências
consolidadas), `icones_diferentes` (identificadores globais distintos) e `paridade_path`.
Esse caminho aponta para uma única imagem com os painéis de todas as matrizes de paridade,
identificados por `icon_id`.

---

## 4. Modelo de Dados — `document_preparado.jsonl`

Cada linha do arquivo representa uma unidade semântica ordenada globalmente. Não há coordenadas geométricas nem atributos de formatação visual.

### 4.1. Atributos Comuns a Todos os Elementos

| Parâmetro | Tipo | Descrição |
| --- | --- | --- |
| `id` | `string` | Identificador único do elemento preparado (`blk_000001`). |
| `reading_order` | `int` | Ordem sequencial global de leitura no documento ($1 \dots N$). |
| `read_order` | `int` | Ordem de leitura do elemento, definida pela posição de cima para baixo e da esquerda para a direita, levando em conta colunas.|
| `type` | `string` | Tipo semântico: `heading`, `paragraph`, `list`, `alert_box`, `table` ou `image`. |
| `page_start` | `int` | Página onde o elemento se inicia. |
| `page_end` | `int` | Página onde o elemento termina. |
| `section_path` | `list[str]` | Caminho hierárquico completo de títulos até este elemento. |
| `parent_section_id` | `string|null` | `id` do elemento `heading` imediatamente superior. |
| `inline_icons` | `list[str]` | Lista de `icon_id`s referenciados no interior do texto deste elemento. |
| `referenced_alerts` | `list[str]` | Lista de `id`s de `alert_box` aos quais este elemento faz referência cruzada. |
| `source_element_ids` | `list[str]` | Lista de `id`s originais do `document.jsonl` que deram origem a este registro (rastreabilidade). |

---

### 4.2. Elemento Ícone — `type: "icon"` (`document_fase2.jsonl`)

Os ícones consolidados na Fase 2 possuem os dois identificadores abaixo:

| Parâmetro | Tipo | Descrição |
| --- | --- | --- |
| `icon_id` | `string` | Identificador global do modelo visual. Ocorrências equivalentes compartilham o mesmo valor. |
| `icon_order_id` | `int` | Identificador da instância no PDF. É único por ocorrência e começa em `1` no primeiro ícone do PDF, sem reiniciar por página e sem deduplicar ícones iguais. |

```json
{
  "id": "icon_source_1_0001",
  "type": "icon",
  "icon_id": "icon_0001",
  "icon_order_id": 1,
  "classification": "standalone",
  "source_element_ids": ["el_000213"]
}
```

### 4.3. Atributos Específicos por Tipo

#### Título — `type: "heading"`

```json
{
  "id": "blk_000120",
  "reading_order": 120,
  "type": "heading",
  "level": 2,
  "page_start": 17,
  "page_end": 17,
  "section_path": ["Informações ao condutor", "Instrumento combinado"],
  "parent_section_id": "blk_000095",
  "content": "Instrumento combinado",
  "inline_icons": [],
  "referenced_alerts": [],
  "source_element_ids": ["el_001042"]
}

```

#### Parágrafo — `type: "paragraph"`

```json
{
  "id": "blk_000125",
  "reading_order": 125,
  "type": "paragraph",
  "page_start": 18,
  "page_end": 18,
  "section_path": ["Informações ao condutor", "Instrumento combinado", "Instrumento combinado analógico"],
  "parent_section_id": "blk_000123",
  "content": "Pressionar o botão <icon:icon_0014> por aproximadamente 2 segundos para zerar o hodômetro parcial.",
  "inline_icons": ["icon_0014"],
  "referenced_alerts": ["blk_000122"],
  "source_element_ids": ["el_001105", "el_001106"]
}

```

#### Lista — `type: "list"`

```json
{
  "id": "blk_000210",
  "reading_order": 210,
  "type": "list",
  "list_style": "checklist",
  "page_start": 32,
  "page_end": 32,
  "section_path": ["Segurança", "Orientações gerais", "Preparativos de viagem e segurança da condução"],
  "parent_section_id": "blk_000208",
  "lead_text": "Os seguintes pontos devem ser observados antes e durante a condução:",
  "items": [
    "Verificar o perfeito funcionamento do sistema de iluminação e dos indicadores de direção.",
    "Controlar a pressão dos pneus (→ Página 246) e o nível de combustível (→ Página 17)."
  ],
  "inline_icons": [],
  "referenced_alerts": [],
  "source_element_ids": ["el_002301", "el_002302", "el_002303"]
}

```

#### Caixa de Alerta — `type: "alert_box"`

```json
{
  "id": "blk_000122",
  "reading_order": 122,
  "type": "alert_box",
  "alert_level": "ATENÇÃO",
  "alert_scope": "chapter_intro",
  "icon_id": "icon_0002",
  "refers_to_ids": ["blk_000120"],
  "page_start": 17,
  "page_end": 17,
  "section_path": ["Informações ao condutor", "Instrumento combinado", "Introdução ao tema"],
  "parent_section_id": "blk_000121",
  "lead_text": "A distração do condutor pode causar acidentes e ferimentos.",
  "items": [
    "Nunca comandar os botões do instrumento combinado durante a condução.",
    "Realizar todas as configurações dos indicadores do display do instrumento combinado e da exibição do display no sistema do rádio apenas com o veículo parado para reduzir o risco de acidentes e de ferimentos."
  ],
  "inline_icons": [],
  "referenced_alerts": [],
  "source_element_ids": ["el_001045", "el_001046", "el_001047", "el_001048"]
}

```

*Valores permitidos para `alert_level`:* `"PERIGO"`, `"ATENÇÃO"`, `"CUIDADO"`, `"NOTA"`, `"AMBIENTAL"`, `"INFO"`.

*Valores permitidos para `alert_scope`:* `"chapter_intro"`, `"local_topic"`.

#### Tabela — `type: "table"`

```json
{
  "id": "blk_000098",
  "reading_order": 98,
  "type": "table",
  "page_start": 15,
  "page_end": 16,
  "section_path": ["Informações ao condutor", "Luzes de advertência e de controle"],
  "parent_section_id": "blk_000096",
  "markdown": "| Símbolo | Significado |\n|---|---|\n| <icon:icon_0008> | Não prosseguir! Freio de estacionamento puxado (→ Página 132). |\n| <icon:icon_0009> | Não prosseguir! Nível do fluido de freio muito baixo (→ Página 234). |",
  "inline_icons": ["icon_0008", "icon_0009"],
  "referenced_alerts": ["blk_000099"],
  "source_element_ids": ["el_000810", "el_000811", "el_000812"]
}

```

#### Imagem — `type: "image"`

```json
{
  "id": "blk_000124",
  "reading_order": 124,
  "type": "image",
  "id_image": 10,
  "page_start": 17,
  "page_end": 18,
  "section_path": ["Informações ao condutor", "Instrumento combinado", "Instrumento combinado analógico"],
  "parent_section_id": "blk_000123",
  "asset_id": "asset_000010",
  "image_path": "assets/asset_000010.png",
  "figure_label": "Fig. 10",
  "caption": "Fig. 10 Instrumento combinado analógico no painel de instrumentos.",
  "description": "Descrição da imagem do instrumento combinado analógico no painel de instrumentos.",
  "legend_items": [
    "1. Tacômetro (conta-giros) (rotações x 1000 por minuto do motor em funcionamento) → Página 19.",
    "2. Botão de ajuste do relógio <icon:icon_0013> → Página 25."
  ],
  "inline_icons": ["icon_0013"],
  "referenced_alerts": ["blk_000122"],
  "source_element_ids": ["el_001050", "el_001051", "el_001101", "el_001102"]
}

```

---

## 5. Regras de Integridade da Preparação

1. **Sequencialidade Estrita:** `reading_order` deve iniciar em `1` e incrementar de `1` em `1` sem lacunas ou repetições até o final do arquivo.
2. **Ausência de Geometria na Saída:** nenhum registro de `document_preparado.jsonl` deve conter atributos `bbox`, `normalized_bbox`, `font`, `font_size`, `color` ou elementos puramente gráficos (`shape`/`vector`).
3. **Rastreabilidade Completa:** todo registro preparado deve listar em `source_element_ids` os identificadores originais de `document.jsonl` que o compõem.
4. **Resolução de Ícones:** todo `icon_id` citado em `inline_icons` ou em `alert_box` deve existir no arquivo `icons_catalog.json` e possuir imagem correspondente em `assets/icons/`.
5. **Integridade de Alertas:** nenhuma caixa de alerta (`PERIGO`, `ATENÇÃO`, `CUIDADO`, `NOTA`) pode ter seus itens de recomendação (`•`) separados do cabeçalho do alerta, mesmo quando houver quebra de coluna ou de página.
