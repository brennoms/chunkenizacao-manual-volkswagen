# Métodos de extração

Este documento apresenta, de forma resumida, os métodos e procedimentos
utilizados para baixar os arquivos e preparar seu conteúdo. A implementação
desse processo está disponível em [src/extracao.py](../../src/extracao.py).

# Download do Manual Técnico da Volkswagen

O documento foi obtido por meio do link:

- [https://www.vw.com.br/idhub/content/dam/onehub_pkw/importers/br/literatura-de-bordo/manual-nivus/21B.5B1.NIV.66%20-%20Manual%20de%20instru%C3%A7%C3%B5es%20-%20NIVUS.pdf](https://www.vw.com.br/idhub/content/dam/onehub_pkw/importers/br/literatura-de-bordo/manual-nivus/21B.5B1.NIV.66%20-%20Manual%20de%20instru%C3%A7%C3%B5es%20-%20NIVUS.pdf).

Após o download, o PDF foi salvo como
`dados_extraidos/manual_tecnico_volkswagen.pdf`. Para evitar o uso de um
arquivo incompleto ou alterado, foi calculado um hash SHA-256 e armazenado em
`dados_extraidos/manual_tecnico_volkswagen.pdf.sha256`. Nas execuções
seguintes, o hash do arquivo é comparado com esse registro antes de seu uso;
se o arquivo não existir ou não corresponder, ele é baixado novamente.

# Estratégia e Modelo de Dados — Extração Bruta de PDFs

## 1. Objetivo

Extrair os elementos presentes no PDF, preservando seus conteúdos, propriedades originais, referências e posições nas páginas.

A extração será realizada sem interpretar o significado semântico dos elementos ou estabelecer relações complexas entre eles.

## 2. Estrutura de armazenamento

```text
conteudo_extraido/
├── metadata.json
├── document.jsonl
└── assets/
    ├── asset_000001.png
    ├── asset_000002.jpeg
    └── ...

```

* `metadata.json`: metadados gerais do PDF e da extração.
* `document.jsonl`: um registro JSON por página.
* `assets/`: arquivos binários extraídos, como imagens.

## 3. Metadados — metadata.json

```json
{
  "filename": "manual_tecnico_volkswagen.pdf",
  "file_size": 15414000,
  "file_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "pdf_version": "1.6",
  "page_count": 309,
  "title": null,
  "author": null,
  "creator": null,
  "producer": null,
  "extraction_date": "2026-11-13T17:00:00-03:00",
  "extractor_version": "1.0.0",
  "schema_version": "1.0.0"
}

```

| Parâmetro | Descrição |
| --- | --- |
| `filename` | Nome do arquivo original. |
| `file_size` | Tamanho do arquivo em bytes. |
| `file_sha256` | Hash SHA-256 do arquivo PDF original para auditoria e rastreabilidade. |
| `pdf_version` | Versão do formato PDF. |
| `page_count` | Quantidade de páginas. |
| `title` | Título declarado no PDF. |
| `author` | Autor declarado no PDF. |
| `creator` | Aplicativo que criou o documento. |
| `producer` | Aplicativo que gerou o PDF. |
| `extraction_date` | Data e hora da extração (ISO 8601). |
| `extractor_version` | Versão do programa extrator. |
| `schema_version` | Versão deste modelo de dados. |

Informações indisponíveis devem receber `null`.

## 4. Página — document.jsonl

Cada linha representa uma página do PDF.

### Modelo

```json
{
  "page": 1,
  "width": 595.28,
  "height": 841.89,
  "rotation": 0,
  "elements": []
}

```

| Parâmetro | Descrição |
| --- | --- |
| `page` | Número da página, começando em 1. |
| `width` | Largura em pontos tipográficos (pt). |
| `height` | Altura em pontos tipográficos (pt). |
| `rotation` | Rotação da página em graus: 0, 90, 180 ou 270. |
| `elements` | Lista de elementos encontrados na página. |

## 5. Elementos

Cada elemento possui atributos comuns e atributos específicos de seu tipo.

### 5.1. Atributos comuns

```json
{
  "id": "el_000001",
  "type": "text",
  "extraction_order": 1,
  "spatial_order": 1,
  "bbox": {
    "x": 72,
    "y": 50,
    "width": 250,
    "height": 30
  },
  "normalized_bbox": {
    "x": 0.121,
    "y": 0.059,
    "width": 0.420,
    "height": 0.036
  },
  "source_id": null,
  "source": {},
  "extraction_method": "native",
  "rotation": null,
  "transform": null,
  "parent_id": null
}

```

| Parâmetro | Descrição |
| --- | --- |
| `id` | Identificador único da ocorrência no documento. Formato `el_000001`. |
| `type` | Tipo do elemento: `text`, `image`, `vector`, `shape` ou `link`. |
| `extraction_order` | Ordem em que o extrator encontrou o elemento na página. |
| `spatial_order` | Ordem espacial calculada pelas coordenadas. |
| `bbox` | Posição e dimensões do elemento em pontos tipográficos. |
| `normalized_bbox` | Mesmas coordenadas normalizadas entre 0 e 1, em relação à página. |
| `source_id` | Identificador da origem do elemento no PDF, quando disponível. |
| `source` | Informações técnicas sobre o objeto original. |
| `extraction_method` | Método de extração: `native`, `ocr` ou `rendered`. |
| `rotation` | Rotação do elemento em graus, quando disponível. |
| `transform` | Matriz de transformação original `[a,b,c,d,e,f]`, quando disponível. |
| `parent_id` | ID do elemento que contém estruturalmente este elemento, quando essa informação existir no PDF. |

**Coordenadas:** a origem é o canto superior esquerdo; X cresce para a direita e Y cresce para baixo. `bbox` utiliza pontos tipográficos. `normalized_bbox` divide X e largura pela largura da página e Y e altura pela altura da página.

**Ordenação:** `extraction_order` registra a sequência de extração. `spatial_order` ordena os elementos de cima para baixo e, em posições verticais próximas, da esquerda para a direita. Nenhum dos dois representa a ordem definitiva de leitura.

### 5.2. Origem — source

```json
{
  "xref": 45,
  "smask_xref": null,
  "object_number": 45,
  "generation": 0,
  "content_stream_xref": 12,
  "resource_name": "Im1"
}

```

| Parâmetro | Descrição |
| --- | --- |
| `xref` | Referência do objeto indireto no PDF. |
| `smask_xref` | Referência do objeto de máscara de transparência (`SMask`) associado, quando aplicável. |
| `object_number` | Número do objeto PDF. |
| `generation` | Número de geração do objeto. |
| `content_stream_xref` | Referência do fluxo de conteúdo que contém o elemento. |
| `resource_name` | Nome do recurso no contexto da página. |

Todos são opcionais e devem receber `null` quando indisponíveis. O `xref` não é um identificador universal de elementos.

## 6. Atributos específicos por tipo

### 6.1. Texto — type: text

```json
{
  "type": "text",
  "content": "Manual Técnico",
  "font": "VWHead-Bold",
  "font_size": 24,
  "color": {
    "space": "rgb",
    "components": [0, 0, 0],
    "alpha": 1
  },
  "style": ["bold"]
}

```

| Parâmetro | Descrição |
| --- | --- |
| `content` | Conteúdo textual extraído, agrupado por frase ou parágrafo. |
| `font` | Nome da fonte predominante no bloco. |
| `font_size` | Tamanho da fonte em pt. |
| `color` | Cor original do texto. |
| `style` | Estilos identificados. |

O objeto `color` contém:

* `space`: espaço de cores (`gray`, `rgb`, `cmyk` ou `other`).
* `components`: componentes numéricos da cor.
* `alpha`: opacidade entre 0 e 1, quando disponível.

Valores de `style`: `bold`, `italic`, `underline`, `strike`, `superscript`, `subscript` e `unknown`.

Os textos devem ser extraídos agrupados em blocos contíguos (frases ou parágrafos fornecidos nativamente pela estrutura de blocos do extrator), evitando a fragmentação em caracteres ou palavras isoladas. Caso um mesmo parágrafo possua variações internas de estilo ou fonte, registram-se as propriedades predominantes no bloco.

### 6.2. Imagem — type: image

```json
{
  "type": "image",
  "asset_id": "asset_000001",
  "format": "png",
  "original_width": 800,
  "original_height": 600,
  "path": "assets/asset_000001.png",
  "content_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}

```

| Parâmetro | Descrição |
| --- | --- |
| `asset_id` | Identificador do recurso binário armazenado. |
| `format` | Formato do arquivo extraído. |
| `original_width` | Largura original em pixels. |
| `original_height` | Altura original em pixels. |
| `path` | Caminho relativo do arquivo. |
| `content_hash` | Hash SHA-256 dos bytes do arquivo final armazenado. |

O `id` identifica cada ocorrência da imagem; o `asset_id` identifica o arquivo armazenado. Se uma imagem aparecer em várias páginas, cada ocorrência terá seu próprio `id`, mas compartilhará o mesmo `asset_id`. A referência ao objeto interno do PDF (`xref` e eventual `smask_xref`) fica registrada dentro do objeto comum `source`.

Quando uma imagem possuir máscara de transparência (`SMask`) separada no PDF, o extrator deve recompor o canal alfa antes de salvar o arquivo em `assets/` e calcular o `content_hash` sobre o resultado recomposto, garantindo que ícones e gráficos preservem a transparência original.

### 6.3. Vetor — type: vector

```json
{
  "type": "vector",
  "geometry": {
    "commands": []
  },
  "fill_color": null,
  "stroke_color": {
    "space": "rgb",
    "components": [0, 0, 0],
    "alpha": 1
  },
  "stroke_width": 1
}

```

| Parâmetro | Descrição |
| --- | --- |
| `geometry` | Dados geométricos originais do caminho vetorial. |
| `fill_color` | Cor de preenchimento. |
| `stroke_color` | Cor do contorno. |
| `stroke_width` | Espessura do contorno em pt. |

`geometry` deve preservar os comandos e as coordenadas disponibilizados pelo extrator (como operadores de linha, curvas de Bézier e subcaminhos complexos), sem interpretação semântica.

### 6.4. Forma — type: shape

```json
{
  "type": "shape",
  "shape_type": "rectangle",
  "geometry": {
    "x": 100,
    "y": 300,
    "width": 150,
    "height": 80
  },
  "fill_color": null,
  "stroke_color": {
    "space": "rgb",
    "components": [0, 0, 0],
    "alpha": 1
  },
  "stroke_width": 1
}

```

| Parâmetro | Descrição |
| --- | --- |
| `shape_type` | Tipo de forma primitiva: `rectangle` ou `line`. |
| `geometry` | Coordenadas e propriedades geométricas originais. |
| `fill_color` | Cor de preenchimento. |
| `stroke_color` | Cor do contorno. |
| `stroke_width` | Espessura do contorno em pt. |

Para evitar inferência geométrica prematura na etapa bruta, `shape` é reservado às primitivas declaradas explicitamente pelo fluxo do PDF ou pelo extrator (como retângulos e segmentos de linha simples). Construções gráficas compostas por múltiplos segmentos ou curvas devem ser registradas diretamente como `vector`.

### 6.5. Link — type: link

```json
{
  "type": "link",
  "link_type": "uri",
  "uri": "[https://example.com](https://example.com)",
  "destination": null
}

```

| Parâmetro | Descrição |
| --- | --- |
| `link_type` | Tipo: `uri`, `goto`, `launch`, `named` ou `unknown`. |
| `uri` | Endereço externo, quando aplicável. |
| `destination` | Destino interno do PDF, quando aplicável. |

`destination` pode conter `page`, `x` e `y`, representando a página e as coordenadas
de destino em pontos tipográficos, no referencial do schema (origem no canto superior
esquerdo). O `y` é convertido usando a altura da página de destino; se a página não
puder ser resolvida, ele será `null`.

## 7. Regras de extração

1. Cada página deve gerar exatamente um registro JSON no JSONL.
2. Cada ocorrência deve possuir um `id` único em todo o documento.
3. `extraction_order` e `spatial_order` devem começar em 1 e não se repetir dentro da página.
4. Textos contíguos devem ser agrupados em nível de frase ou parágrafo, conforme os blocos de texto fornecidos pelo extrator.
5. Os recursos binários devem ser armazenados uma única vez quando seus bytes forem idênticos.
6. Quando uma imagem possuir máscara de opacidade (`SMask`), ela deve ser recomposta antes do armazenamento e do cálculo do hash.
7. As informações indisponíveis devem receber `null`, quando permitido.
8. Não descartar elementos por serem repetidos, decorativos ou aparentemente irrelevantes.
9. Preservar os dados originais de geometria, fonte, cor e transformação sempre que disponíveis.
10. Não substituir a extração nativa por OCR quando o texto nativo estiver disponível; OCR deve complementar conteúdos que não possam ser extraídos nativamente.
11. Elementos vetoriais e formas devem preservar seus dados geométricos, mesmo quando não for possível classificá-los.

## 8. Limites desta etapa

A extração bruta não deve realizar:

* Classificação funcional dos blocos de texto (ex.: distinguir títulos, corpo de texto, notas de rodapé ou legendas).
* Reconstrução de tabelas.
* Associação entre imagens e textos.
* Identificação de colunas e seções.
* Determinação da ordem definitiva de leitura.
* Inferência de relações entre elementos.

Essas operações pertencem às etapas posteriores de análise do documento.

**Princípio fundamental:** registrar e preservar os elementos e suas propriedades originais, sem interpretar prematuramente a organização semântica do PDF.
