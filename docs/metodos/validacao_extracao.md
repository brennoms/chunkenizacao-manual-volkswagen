# Validação da Extração Bruta

Este documento descreve os procedimentos de verificação utilizados para garantir que os artefatos gerados pela extração (`metadata.json`, `document.jsonl` e `assets/`) cumpram integralmente o modelo de dados e as regras de integridade estabelecidas. A implementação está disponível em [src/validar_extracao.py](../../src/validar_extracao.py).

## 1. Objetivo

Assegurar a conformidade estrutural, matemática e posicional dos dados extraídos antes das etapas de análise semântica, dividindo a verificação em duas frentes complementares:

1. **Validação Automatizada de Invariantes:** checagem determinística de todas as páginas, elementos e arquivos binários.
2. **Inspeção Visual por Sobreposição (*Visual Debugging*):** verificação amostral do alinhamento geométrico das coordenadas extraídas contra a renderização original do PDF.

---

## 2. Validação Automatizada de Invariantes

O validador percorre sequencialmente os arquivos de saída e classifica qualquer desvio em **Erro** (violação de regra que invalida a extração) ou **Aviso** (anomalia geométrica ou estrutural que requer atenção, mas não corrompe o modelo).

### 2.1. Regras Verificadas

| Regra | Escopo | Critério de Aceite | Severidade |
|---|---|---|---|
| **Completude de Páginas** | Documento | O número total de linhas em `document.jsonl` deve ser estritamente igual ao atributo `page_count` de `metadata.json`. | Erro |
| **Sequencialidade de Páginas** | Documento | O atributo `page` da linha $N$ no arquivo `.jsonl` deve ser igual a $N$ (iniciando em 1, sem saltos ou duplicatas). | Erro |
| **Unicidade Global de IDs** | Documento | O atributo `id` (`el_000001`, ...) não pode se repetir em nenhum outro elemento de qualquer página do documento. | Erro |
| **Permutação de Ordenação** | Página | Em uma página com $K$ elementos, os conjuntos de valores de `extraction_order` e `spatial_order` devem formar exatamente a sequência $\{1, 2, \dots, K\}$. | Erro |
| **Dimensões Positivas** | Elemento | Os atributos `width` e `height` de `bbox` e `normalized_bbox` devem ser maiores ou iguais a zero ($\ge 0$). | Erro |
| **Limites Normalizados** | Elemento | As coordenadas `x` e `y` de `normalized_bbox` devem estar contidas no intervalo tolerado $[-0.5, 1.5]$ (considerando margens de sangria do PDF). | Aviso |
| **Existência de Binários** | Imagem | Todo elemento com `type: "image"` deve apontar em `path` para um arquivo existente em `assets/` com tamanho maior que zero ($> 0$ bytes). | Erro |
| **Integridade Criptográfica** | Imagem | O hash SHA-256 calculado sobre os bytes do arquivo físico em `assets/` deve ser idêntico ao campo `content_hash` do elemento. | Erro |
| **Consistência de Diretório** | Diretório | O total de arquivos físicos presentes na pasta `assets/` deve corresponder exatamente ao total de hashes únicos referenciados no `document.jsonl` (ausência de arquivos órfãos). | Aviso |

---

## 3. Inspeção Visual (*Visual Debugging*)

A validação matemática não detecta desvios sistemáticos de referencial (ex.: inversão do eixo Y ou falha na recomposição de transparência visual). Para validar a fidelidade espacial, o script renderiza páginas de amostra do PDF original (150 DPI) e desenha sobre elas as caixas delimitadoras (`bbox`) extraídas do `document.jsonl`.

### 3.1. Codificação Visual

Os artefatos de inspeção são salvos em `documento/debug_visual/debug_pagina_XXX.png` utilizando o seguinte padrão cromático:

| Tipo (`type`) | Cor da Caixa (`bbox`) | Anotação Adicional |
|---|---|---|
| `text` | Azul | Rótulo `#N` no canto superior esquerdo indicando o `spatial_order`. |
| `image` | Verde | — |
| `shape` | Laranja | — |
| `vector` | Vermelho | — |
| `link` | Roxo | — |

### 3.2. Critérios de Homologação Visual

Durante a inspeção das imagens geradas em `debug_visual/` e dos arquivos em `assets/`, devem ser confirmados quatro pontos:

1. **Alinhamento de Caixas:** os retângulos coloridos devem envolver com precisão os textos, imagens e formas gráficas da página, sem deslocamento vertical ou horizontal.
2. **Coesão de Parágrafos:** blocos de texto contíguos devem estar agrupados sob um único retângulo azul, sem fusão indevida entre colunas paralelas.
3. **Fluxo de Ordenação Espacial (`#N`):** a numeração `#1, #2, ..., #N` nos blocos de texto deve progredir de cima para baixo e, em elementos alinhados horizontalmente, da esquerda para a direita.
4. **Preservação de Canal Alfa (`SMask`):** ícones e pictogramas extraídos na pasta `assets/` devem apresentar fundo transparente quando aplicável, sem artefatos de fundo preto sólido.

---

## 4. Execução

Para executar a validação completa e gerar as páginas de inspeção visual:

```bash
python src/validar_extracao.py