# Chunkenização de Manual Técnico Volkswagen

## Sobre o projeto

Este projeto tem como objetivo transformar um manual técnico em uma **base de conhecimento textual estruturada**, organizada e preparada para utilização por sistemas de Inteligência Artificial.

O processo envolve a extração do conteúdo do PDF, sua preparação e organização textual e, por fim, a divisão do conteúdo em *chunks* (fragmentos), preservando informações relevantes e facilitando sua recuperação por sistemas de IA.

## Como executar

### 1. Clone o repositório

```bash
git clone https://github.com/brennoms/chunkenizacao-manual-volkswagen.git
cd chunkenizacao-manual-volkswagen
```

### 2. Crie e ative o ambiente virtual

**Linux / macOS:**

```bash
python -m venv venv
source venv/bin/activate
```

**Windows:**

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Instale as dependências

Com o ambiente virtual ativado, execute:

```bash
pip install -e .
```

### 4. Execute o projeto

```bash
python src/main.py
```

## Estrutura dos resultados

Após a execução, os arquivos gerados estarão disponíveis na pasta `OUTPUT/`, organizados conforme a seguinte estrutura:

```text
OUTPUT/
│
└── 03_resultado/
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

### Arquivos gerados

| Arquivo | Descrição |
|---|---|
| `manual_tecnico_volkswagen.pdf` | Manual técnico original utilizado como entrada. |
| `manual_tecnico_volkswagen_ocr.jsonl` | Conteúdo extraído do PDF, organizado em registros JSONL. |
| `manual_tecnico_volkswagen_texto_bruto.txt` | Conteúdo textual extraído, em formato de texto simples. |
| `chunks.jsonl` | Fragmentos textuais resultantes do processo de chunkenização, acompanhados de seus respectivos metadados. |
| `manifest.json` | Arquivo de referência com informações e estatísticas sobre o processamento e os chunks gerados. |

## Documentação

As etapas do processamento e as decisões metodológicas adotadas estão documentadas na pasta `docs/`:

- [Metodologia de extração](docs/metodologia_extracao.md) — descreve o processo de extração de conteúdo do PDF.
- [Metodologia de preparação](docs/metodologia_preparacao.md) — apresenta as etapas de tratamento, limpeza e organização do texto extraído.
- [Metodologia de chunkenização](docs/metodologia_chunkenizacao.md) — explica os critérios de segmentação, a estrutura dos chunks e seus metadados.

Consulte esses documentos para compreender o funcionamento de cada etapa e os critérios utilizados na construção da base de conhecimento.

---

**Resultado esperado:** uma base de conhecimento textual estruturada, com fragmentos e metadados organizados, pronta para ser utilizada em aplicações de Inteligência Artificial, especialmente sistemas de recuperação de informações (RAG).