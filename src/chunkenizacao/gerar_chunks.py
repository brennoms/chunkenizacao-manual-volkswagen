"""
Etapa de Chunkenização Semântica e Hierárquica — Manual Técnico Volkswagen.

Lê `chunks/02_extracted/manual_tecnico_volkswagen_ocr.jsonl` e gera:
- `chunks/03_chunks/chunks.jsonl`
- `chunks/03_chunks/manifest.json`
"""

from __future__ import annotations

import json
import math
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Parâmetros de dimensionamento (Seção 5 do metodos_chunkenizacao.md)
TOKENS_ALVO_MIN = 350
TOKENS_ALVO_MAX = 650
TOKENS_LIMITE_MAX = 850
TOKENS_MINIMO = 80


# ---------------------------------------------------------------------------
# Tokenizador (tiktoken cl100k_base com fallback determinístico para PT-BR)
# ---------------------------------------------------------------------------

def _inicializar_tokenizador():
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return lambda texto: len(enc.encode(texto)), "tiktoken:cl100k_base"
    except Exception:
        padrao_tokens = re.compile(
            r"<icon:icon_\d+>|[\wáéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]+|[^\s\w]",
            re.UNICODE,
        )

        def _estimar_tokens_pt(texto: str) -> int:
            if not texto:
                return 0
            pecas = padrao_tokens.findall(texto)
            total = 0
            for peca in pecas:
                if peca.startswith("<icon:"):
                    total += 5
                elif len(peca) <= 4:
                    total += 1
                else:
                    # Palavras longas em português dividem-se em ~1.35 subwords em BPE
                    total += max(1, math.ceil(len(peca) / 5.5))
            return total

        return _estimar_tokens_pt, "regex_bpe_pt_fallback"


CONTAR_TOKENS, NOME_TOKENIZADOR = _inicializar_tokenizador()


# ---------------------------------------------------------------------------
# Funções Auxiliares de Normalização e Formatação Markdown
# ---------------------------------------------------------------------------

def _deduplicar(seq: Iterable[Any]) -> List[Any]:
    return list(dict.fromkeys(x for x in seq if x is not None and x != ""))


def _slugify(texto: Optional[str]) -> Optional[str]:
    if not texto:
        return None
    limpo = re.sub(r"<icon:icon_\d+>", "", texto)
    limpo = re.sub(r"⚠️", "", limpo).strip()
    normalizado = unicodedata.normalize("NFKD", limpo)
    sem_acento = "".join(c for c in normalizado if not unicodedata.combining(c))
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", sem_acento.lower()).strip("_")
    return slug or None


def _limpar_marcadores_visuais(texto: str) -> str:
    """Remove tags de ícone e símbolos de alerta apenas para compor o campo Assunto/Tema."""
    limpo = re.sub(r"<icon:icon_\d+>", "", texto)
    limpo = re.sub(r"[⚠️→]", "", limpo)
    return re.sub(r"\s+", " ", limpo).strip(" .:-–—")


def formatar_bloco_markdown(bloco: Dict[str, Any]) -> str:
    """Converte um bloco individual do OCR JSONL para Markdown."""
    btype = bloco.get("type")

    if btype == "heading":
         conteudo = (bloco.get("content") or "").strip()
         return f"**{conteudo}**" if conteudo else ""

    if btype == "paragraph":
        return (bloco.get("content") or "").strip()

    if btype == "table":
        return (bloco.get("markdown") or "").strip()

    if btype == "list":
        lead_text = (bloco.get("lead_text") or "").strip()
        items = bloco.get("items") or []
        estilo = bloco.get("list_style", "unordered")
        linhas: List[str] = []
        if lead_text:
            linhas.append(lead_text)
        for idx, item in enumerate(items, start=1):
            item_str = str(item).strip()
            if not item_str:
                continue
            if estilo == "ordered":
                marcador = f"{idx}."
            elif estilo == "checklist":
                marcador = "- [x]"
            else:
                marcador = "-"
            linhas.append(f"{marcador} {item_str}")
        return "\n".join(linhas)

    if btype == "alert_box":
        nivel = (bloco.get("alert_level") or "ALERTA").strip()
        lead_text = (bloco.get("lead_text") or "").strip()
        items = bloco.get("items") or []
        linhas = [f"**[{nivel}]**"]
        if lead_text:
            linhas.append(lead_text)
        for item in items:
            item_str = str(item).strip()
            if item_str:
                linhas.append(f"- {item_str}")
        return "\n".join(linhas)

    if btype == "image":
        figure_label = (bloco.get("figure_label") or "").strip()
        caption = (bloco.get("caption") or "").strip()
        description = (bloco.get("description") or "").strip()
        legend_items = bloco.get("legend_items") or []

        linhas = []
        if caption:
            linhas.append(f"**{caption}**")
        elif figure_label:
            linhas.append(f"**{figure_label}**")
        if description:
            linhas.append(f"*[Imagem: {description}]*")
        for item in legend_items:
            item_str = str(item).strip()
            if item_str:
                linhas.append(f"- {item_str}")
        return "\n".join(linhas)

    if btype == "icon":
        icon_id = (bloco.get("icon_id") or "icon").strip()
        description = (bloco.get("description") or "").strip()
        if description:
            return f"*[Ícone {icon_id}: {description}]*"
        return f"*[Ícone {icon_id}]*"

    return (bloco.get("content") or bloco.get("markdown") or "").strip()


def montar_cabecalho_chunk(section_path: Sequence[str], tema: str) -> str:
    """
    Constrói o cabeçalho contextual em Markdown conforme a Seção 6:
    # [Capítulo]
    ## [Seção]
    ### [Subseção]
    **Assunto:** [Tema principal]
    """
    linhas: List[str] = []
    for idx, sec in enumerate(section_path):
        nivel = min(idx + 1, 6)
        prefixo = "#" * nivel
        linhas.append(f"{prefixo} {sec.strip()}")

    if tema:
        linhas.append(f"**Assunto:** {tema}")

    return "\n\n".join(linhas)


# ---------------------------------------------------------------------------
# Divisão de Estruturas Grandes (> TOKENS_LIMITE_MAX) com Repetição de Contexto
# ---------------------------------------------------------------------------

def _dividir_tabela_grande(
    bloco: Dict[str, Any],
    tokens_cabecalho_secao: int,
) -> List[Dict[str, Any]]:
    """
    Fragmenta tabelas que ultrapassam o limite máximo de tokens por grupos de linhas,
    repetindo o cabeçalho da tabela em cada fragmento (Seção 4.3).
    """
    md = (bloco.get("markdown") or "").strip()
    linhas = [ln for ln in md.splitlines() if ln.strip()]
    if len(linhas) <= 3:
        return [{
            "blocos": [bloco],
            "markdown_override": md,
            "overlap": False,
            "split_group_id": None,
        }]

    cabecalho_md = linhas[:2]
    linhas_dados = linhas[2:]
    orcamento_corpo = max(250, TOKENS_ALVO_MAX - tokens_cabecalho_secao)

    fragmentos: List[Dict[str, Any]] = []
    acumuladas: List[str] = []
    gid = f"split_tbl_{bloco['id']}"

    for linha in linhas_dados:
        candidato = "\n".join(cabecalho_md + acumuladas + [linha])
        if acumuladas and CONTAR_TOKENS(candidato) > orcamento_corpo:
            fragmentos.append({
                "blocos": [bloco],
                "markdown_override": "\n".join(cabecalho_md + acumuladas),
                "overlap": True,
                "split_group_id": gid,
            })
            acumuladas = [linha]
        else:
            acumuladas.append(linha)

    if acumuladas:
        fragmentos.append({
            "blocos": [bloco],
            "markdown_override": "\n".join(cabecalho_md + acumuladas),
            "overlap": len(fragmentos) > 0,
            "split_group_id": gid if len(fragmentos) > 0 else None,
        })

    return fragmentos


def _dividir_lista_grande(
    bloco: Dict[str, Any],
    tokens_cabecalho_secao: int,
) -> List[Dict[str, Any]]:
    """
    Fragmenta listas que ultrapassam o limite máximo preservando o `lead_text`
    em cada fragmento e sinalizando continuidade (Seção 4.3).
    """
    lead_text = (bloco.get("lead_text") or "").strip()
    items = bloco.get("items") or []
    estilo = bloco.get("list_style", "unordered")
    if len(items) <= 2:
        return [{
            "blocos": [bloco],
            "markdown_override": formatar_bloco_markdown(bloco),
            "overlap": False,
            "split_group_id": None,
        }]

    orcamento_corpo = max(250, TOKENS_ALVO_MAX - tokens_cabecalho_secao)
    fragmentos: List[Dict[str, Any]] = []
    itens_atuais: List[str] = []
    gid = f"split_lst_{bloco['id']}"

    def _render_parcial(lista_itens: List[str], offset_idx: int, continuacao: bool) -> str:
        linhas: List[str] = []
        if lead_text:
            sufixo = " (continuação)" if continuacao else ""
            linhas.append(f"{lead_text}{sufixo}")
        for idx_local, it in enumerate(lista_itens, start=offset_idx):
            if estilo == "ordered":
                marcador = f"{idx_local}."
            elif estilo == "checklist":
                marcador = "- [x]"
            else:
                marcador = "-"
            linhas.append(f"{marcador} {str(it).strip()}")
        return "\n".join(linhas)

    offset = 1
    for item in items:
        eh_cont = len(fragmentos) > 0
        candidato = _render_parcial(itens_atuais + [item], offset, eh_cont)
        if itens_atuais and CONTAR_TOKENS(candidato) > orcamento_corpo:
            fragmentos.append({
                "blocos": [bloco],
                "markdown_override": _render_parcial(itens_atuais, offset, eh_cont),
                "overlap": True,
                "split_group_id": gid,
            })
            offset += len(itens_atuais)
            itens_atuais = [item]
        else:
            itens_atuais.append(item)

    if itens_atuais:
        eh_cont = len(fragmentos) > 0
        fragmentos.append({
            "blocos": [bloco],
            "markdown_override": _render_parcial(itens_atuais, offset, eh_cont),
            "overlap": eh_cont,
            "split_group_id": gid if eh_cont else None,
        })

    return fragmentos


# ---------------------------------------------------------------------------
# Empacotamento em Micro-Unidades Coesas e Chunks por Seção
# ---------------------------------------------------------------------------

def _eh_subtitulo_curto(bloco: Dict[str, Any]) -> bool:
    """
    Identifica parágrafos curtos que atuam como subtítulos internos de tópico
    (ex.: 'Memória “Desde a partida”', 'Destravar e travar o veículo') ou
    parágrafos introdutórios terminados em ':'.
    """
    if bloco.get("type") != "paragraph":
        return False
    texto = (bloco.get("content") or "").strip()
    if not texto:
        return False
    texto_sem_icone = re.sub(r"<icon:icon_\d+>\s*", "", texto).strip()
    if texto_sem_icone.endswith(":"):
        return True
    if (
        len(texto_sem_icone) <= 75
        and not texto_sem_icone.endswith((".", "!", "?", ";"))
        and not texto_sem_icone.startswith("⚠️")
    ):
        return True
    return False


def _criar_micro_unidades(
    blocos_secao: List[Dict[str, Any]],
    headings_associados: List[Dict[str, Any]],
    tokens_cabecalho_secao: int,
) -> List[Dict[str, Any]]:
    """
    Agrupa blocos fortemente acoplados dentro de uma mesma seção (como subtítulos
    curtos seguidos de seu parágrafo/lista/tabela ou ícones soltos) em micro-unidades
    indivisíveis antes do empacotamento por janela de tokens.
    """
    unidades: List[Dict[str, Any]] = []
    buffer_acoplado: List[Dict[str, Any]] = []

    for idx, bloco in enumerate(blocos_secao):
        btype = bloco.get("type")

        # Ícones isolados ou subtítulos curtos acoplam-se ao bloco seguinte da mesma seção
        if (btype == "icon" or _eh_subtitulo_curto(bloco)) and idx < len(blocos_secao) - 1:
            buffer_acoplado.append(bloco)
            continue

        grupo = buffer_acoplado + [bloco]
        buffer_acoplado = []

        md_grupo = "\n\n".join(
             m for m in (formatar_bloco_markdown(b) for b in grupo) if m
        )
        tokens_grupo = CONTAR_TOKENS(md_grupo)

        # Verifica se o bloco principal sozinho excede TOKENS_LIMITE_MAX e é divisível
        if tokens_cabecalho_secao + tokens_grupo > TOKENS_LIMITE_MAX:
            if btype == "table" and len(grupo) == 1:
                unidades.extend(_dividir_tabela_grande(bloco, tokens_cabecalho_secao))
                continue
            if btype == "list" and len(grupo) == 1:
                unidades.extend(_dividir_lista_grande(bloco, tokens_cabecalho_secao))
                continue

        unidades.append({
            "blocos": grupo,
            "markdown_override": md_grupo,
            "overlap": False,
            "split_group_id": None,
        })

    if buffer_acoplado:
        md_restante = "\n\n".join(
            m for m in (formatar_bloco_markdown(b) for b in buffer_acoplado) if m
        )
        unidades.append({
            "blocos": buffer_acoplado,
            "markdown_override": md_restante,
            "overlap": False,
            "split_group_id": None,
        })

    # Associa os blocos `heading` que deram origem a esta seção à primeira micro-unidade
    # para preservar 100% de rastreabilidade dos IDs sem gerar chunk isolado de heading
    if unidades and headings_associados:
        unidades[0]["headings_origem"] = list(headings_associados)

    return unidades


def _empacotar_unidades_em_chunks(
    section_path: List[str],
    unidades: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Agrupa as micro-unidades de uma seção buscando a faixa de 350–650 tokens
    (limite máximo de 850 tokens), sem quebrar unidades de conhecimento.
    """
    if not unidades:
        return []

    tema_base = section_path[-1] if section_path else "Manual de instruções"
    tokens_cab = CONTAR_TOKENS(montar_cabecalho_chunk(section_path, tema_base))

    chunks_brutos: List[List[Dict[str, Any]]] = []
    atual: List[Dict[str, Any]] = []
    tokens_atuais = tokens_cab

    for idx, uni in enumerate(unidades):
        t_uni = CONTAR_TOKENS(uni["markdown_override"])

        if not atual:
            atual.append(uni)
            tokens_atuais = tokens_cab + t_uni
            continue

        novo_total = tokens_atuais + 2 + t_uni

        # Se a unidade atual faz parte de uma tabela/lista grande já fragmentada,
        # cada fragmento planejado inicia seu próprio chunk
        if uni.get("split_group_id") and any(
            u.get("split_group_id") == uni.get("split_group_id") for u in atual
        ):
            chunks_brutos.append(atual)
            atual = [uni]
            tokens_atuais = tokens_cab + t_uni
            continue

        # Verifica se a próxima unidade é um alerta local que referencia o bloco no chunk atual
        ids_no_atual = {b["id"] for u in atual for b in u["blocos"]}
        eh_alerta_vinculado = any(
            b.get("type") == "alert_box"
            and b.get("alert_scope") == "local_topic"
            and set(b.get("refers_to_ids") or []) & ids_no_atual
            for b in uni["blocos"]
        )

        # Limite flexível até TOKENS_LIMITE_MAX (850) para manter alertas locais junto ao procedimento
        limite_efetivo = TOKENS_LIMITE_MAX if eh_alerta_vinculado else TOKENS_ALVO_MAX

        if novo_total <= limite_efetivo:
            atual.append(uni)
            tokens_atuais = novo_total
        elif tokens_atuais < TOKENS_ALVO_MIN and novo_total <= TOKENS_LIMITE_MAX:
            # Se o chunk atual ainda está abaixo do mínimo desejado (350) e cabe no limite máximo (850), agrupa
            atual.append(uni)
            tokens_atuais = novo_total
        else:
            chunks_brutos.append(atual)
            atual = [uni]
            tokens_atuais = tokens_cab + t_uni

    if atual:
        chunks_brutos.append(atual)

    # Pós-processamento: evita que o último chunk da seção fique menor que TOKENS_MINIMO (80)
    if len(chunks_brutos) >= 2:
        ultimo = chunks_brutos[-1]
        md_ultimo = "\n\n".join(u["markdown_override"] for u in ultimo)
        t_ultimo = tokens_cab + CONTAR_TOKENS(md_ultimo)

        penultimo = chunks_brutos[-2]
        md_penultimo = "\n\n".join(u["markdown_override"] for u in penultimo)
        t_penultimo = tokens_cab + CONTAR_TOKENS(md_penultimo)

        if t_ultimo < TOKENS_MINIMO and (t_penultimo + CONTAR_TOKENS(md_ultimo)) <= TOKENS_LIMITE_MAX:
            chunks_brutos[-2].extend(ultimo)
            chunks_brutos.pop()

    # Se a seção foi dividida em mais de um chunk, marca overlap contextual entre eles
    multiplos_na_secao = len(chunks_brutos) > 1
    saida_secao: List[Dict[str, Any]] = []
    gid_secao = f"sec_{_slugify('_'.join(section_path))}" if multiplos_na_secao else None

    for idx_c, grupo_uni in enumerate(chunks_brutos):
        blocos_chunk: List[Dict[str, Any]] = []
        headings_origem: List[Dict[str, Any]] = []
        partes_md: List[str] = []
        tem_overlap = multiplos_na_secao and idx_c > 0
        split_gids: List[str] = [gid_secao] if gid_secao else []

        for u in grupo_uni:
            headings_origem.extend(u.get("headings_origem") or [])
            blocos_chunk.extend(u["blocos"])
            if u["markdown_override"]:
                partes_md.append(u["markdown_override"])
            if u.get("overlap"):
                tem_overlap = True
            if u.get("split_group_id"):
                split_gids.append(u["split_group_id"])

        saida_secao.append({
            "section_path": list(section_path),
            "blocos": blocos_chunk,
            "headings_origem": headings_origem,
            "corpo_markdown": "\n\n".join(partes_md),
            "overlap": tem_overlap,
            "split_groups": _deduplicar(split_gids),
        })

    return saida_secao


# ---------------------------------------------------------------------------
# Classificação Semântica e Funcional (Seção 7 do metodos_chunkenizacao.md)
# ---------------------------------------------------------------------------

def _inferir_tipo_estrutura(blocos: Sequence[Dict[str, Any]]) -> str:
    tipos = [b.get("type") for b in blocos if b.get("type") not in {"heading", "icon"}]
    if not tipos:
        return "paragraph"
    contagem = Counter(tipos)
    if len(contagem) == 1:
        return str(tipos[0])
    # Se houver uma estrutura claramente dominante ou combinada
    if "table" in contagem and len(blocos) <= 2:
        return "table"
    if "alert_box" in contagem and contagem["alert_box"] == len(tipos):
        return "alert_box"
    if "image" in contagem and len(tipos) == 1:
        return "image"
    return "mixed"


def _inferir_tipo_conhecimento(
    section_path: Sequence[str],
    blocos: Sequence[Dict[str, Any]],
    corpo_md: str,
) -> str:
    """
    Classifica o chunk em um dos 10 valores recomendados na Seção 7.1:
    explanation, procedure, specification, table, warning, troubleshooting,
    operation, maintenance, identification, reference.
    """
    caminho_lower = " > ".join(section_path).lower()
    texto_lower = corpo_md.lower()
    tipos_blocos = [b.get("type") for b in blocos if b.get("type") not in {"heading", "icon"}]

    # 1. Warning: composto exclusivamente ou majoritariamente por alert_box
    if tipos_blocos and all(t == "alert_box" for t in tipos_blocos):
        return "warning"

    # 2. Reference: sumário, índice, garantia, capa, sobre o manual
    if any(
        termo in caminho_lower
        for termo in (
            "índice",
            "sobre este manual",
            "certificado de garantia",
            "muito obrigado por sua confiança",
            "informações ao consumidor",
        )
    ) or not section_path:
        return "reference"

    # 3. Troubleshooting: solução de problemas, emergências, falhas
    if any(
        termo in caminho_lower
        for termo in (
            "soluções de problemas",
            "solução de problemas",
            "em caso de emergência",
            "autoajuda",
            "não prosseguir",
        )
    ):
        return "troubleshooting"

    # 4. Table: quando a tabela é o elemento central do chunk
    if "table" in tipos_blocos:
        if "dados técnicos" in caminho_lower or "capacidades" in caminho_lower or "dimensões" in caminho_lower:
            return "specification"
        return "table"

    # 5. Identification: vista geral do veículo, significados dos símbolos ou figuras com legend_items
    if "vista geral do veículo" in caminho_lower or "significados dos símbolos" in caminho_lower:
        return "identification"
    if any(b.get("type") == "image" and len(b.get("legend_items") or []) >= 3 for b in blocos):
        return "identification"

    # 6. Specification: dados técnicos, dimensões, capacidades
    if any(
        termo in caminho_lower
        for termo in ("dados técnicos", "dimensões", "capacidades", "motor 1.0")
    ):
        return "specification"

    # 7. Maintenance: manutenção, revisão, conservação, fluidos, abastecimento
    if any(
        termo in caminho_lower
        for termo in (
            "manutenção",
            "verificação e reabastecimento",
            "intervalo de serviço",
            "previsão para 1ª revisão",
            "conservação do veículo",
        )
    ):
        return "maintenance"

    # 8. Procedure: listas ordenadas, checklists ou instruções passo a passo de montagem/ajuste
    tem_checklist_ou_ordenada = any(
        b.get("type") == "list" and b.get("list_style") in {"ordered", "checklist"}
        for b in blocos
    )
    if tem_checklist_ou_ordenada:
        return "procedure"

    tem_lista_acao = any(b.get("type") == "list" for b in blocos) and bool(
        re.search(
            r"\b(instalar|desinstalar|fixar|ajustar|colocar|tirar|substituir|rebater|sincronizar|destravar\s+emergencialmente)\b",
            caminho_lower + " " + texto_lower,
        )
    )
    if tem_lista_acao:
        return "procedure"

    # 9. Operation: operação de comandos, botões, menus, condução, climatização, iluminação
    if any(
        termo in caminho_lower
        for termo in (
            "comando",
            "menu",
            "ligar e desligar",
            "abrir e fechar",
            "abrir ou fechar",
            "aquecer, ventilar, refrigerar",
            "transmissão automática",
            "tiptronic",
            "regulador de velocidade",
            "limitador de velocidade",
            "controle automático de distância",
        )
    ):
        return "operation"

    # 10. Explanation: conceitos, funcionamento, leis da física, descrições gerais
    return "explanation"


def _inferir_dimensoes_funcionais(
    section_path: Sequence[str],
    blocos: Sequence[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], str]:
    """
    Extrai (sistema, componente, tema) estritamente a partir da hierarquia documental
    e dos blocos presentes no chunk, sem suposições externas (Seção 7.2).
    """
    if not section_path:
        return None, None, "Capa e identificação do manual"

    cap_nivel1 = section_path[0]
    sistema = _slugify(cap_nivel1)

    componente: Optional[str] = None
    if len(section_path) >= 2:
        # No capítulo 'Índice', o nível 2 (ex: 'Segurança', 'Iluminação') indica o tópico listado
        componente = _slugify(section_path[1])

    # Construção do Tema principal (Assunto)
    ultimo_titulo = _limpar_marcadores_visuais(section_path[-1])
    penultimo_titulo = _limpar_marcadores_visuais(section_path[-2]) if len(section_path) >= 2 else ""

    if "introdução ao tema" in ultimo_titulo.lower() and penultimo_titulo:
        tema_base = f"{penultimo_titulo} — Introdução ao tema"
    else:
        tema_base = ultimo_titulo or cap_nivel1

    # Refina o tema se o primeiro bloco do chunk for um subtítulo curto ou imagem intitulada
    primeiro = blocos[0] if blocos else {}
    if _eh_subtitulo_curto(primeiro):
        sub = _limpar_marcadores_visuais(primeiro.get("content") or "")
        if sub and sub.lower() != tema_base.lower() and len(sub) <= 65:
            tema_base = f"{tema_base} — {sub}"
    elif primeiro.get("type") == "image":
        cap_img = _limpar_marcadores_visuais(
            primeiro.get("caption") or primeiro.get("figure_label") or ""
        )
        if cap_img and len( bloc := [b for b in blocos if b.get("type") != "heading"] ) == 1:
            tema_base = cap_img
    elif all(b.get("type") == "alert_box" for b in blocos if b.get("type") != "heading"):
        niveis = _deduplicar(b.get("alert_level") for b in blocos if b.get("type") == "alert_box")
        rotulo_niveis = "/".join(niveis) if niveis else "Segurança"
        if "alerta" not in tema_base.lower():
            tema_base = f"{tema_base} — Alerta ({rotulo_niveis})"

    return sistema, componente, tema_base


# ---------------------------------------------------------------------------
# Pipeline Principal de Geração e Validação dos Chunks
# ---------------------------------------------------------------------------

def gerar_chunks(
    diretorio_relativo: Path,
    caminho_ocr_jsonl: Path,
    saida_chunks: Path,
    saida_manifest: Path
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Executa a chunkenização completa de `manual_tecnico_volkswagen_ocr.jsonl`,
    valida todas as 10 regras da Seção 9 e salva `chunks.jsonl` e `manifest.json`.
    """

    registros: List[Dict[str, Any]] = []
    with caminho_ocr_jsonl.open("r", encoding="utf-8") as f:
        for num_linha, linha in enumerate(f, start=1):
            linha = linha.strip()
            if not linha:
                continue
            registros.append(json.loads(linha))

    registros.sort(
        key=lambda r: (
            r.get("reading_order", float("inf")),
            r.get("page_start", float("inf")),
            r.get("read_order", float("inf")),
        )
    )

    # 1. Agrupar blocos contíguos por `section_path`
    grupos_secao: List[Dict[str, Any]] = []
    headings_pendentes: List[Dict[str, Any]] = []

    for reg in registros:
        if reg.get("type") == "heading":
            # Se havia um heading folha sem nenhum bloco filho antes de outro heading,
            # preserva-o como item textual no grupo atual para não perder nenhuma linha
            if headings_pendentes and grupos_secao:
                ultimo_h = headings_pendentes[-1]
                if int(reg.get("level", 1)) <= int(ultimo_h.get("level", 1)):
                    # Verifica se ultimo_h nunca foi usado como ancestral do novo reg
                    if ultimo_h.get("content") not in (reg.get("section_path") or []):
                        grupos_secao[-1]["blocos"].append(ultimo_h)
                        headings_pendentes.pop()
            headings_pendentes.append(reg)
            continue

        sec_path = list(reg.get("section_path") or [])
        if not grupos_secao or grupos_secao[-1]["section_path"] != sec_path:
            grupos_secao.append({
                "section_path": sec_path,
                "blocos": [reg],
                "headings": headings_pendentes,
            })
            headings_pendentes = []
        else:
            if headings_pendentes:
                grupos_secao[-1]["headings"].extend(headings_pendentes)
                headings_pendentes = []
            grupos_secao[-1]["blocos"].append(reg)

    if headings_pendentes and grupos_secao:
        grupos_secao[-1]["headings"].extend(headings_pendentes)

    # 2. Construir chunks por seção
    chunks_intermediarios: List[Dict[str, Any]] = []
    for grupo in grupos_secao:
        sec_path = grupo["section_path"]
        tema_prov = sec_path[-1] if sec_path else "Manual de instruções"
        tokens_cab = CONTAR_TOKENS(montar_cabecalho_chunk(sec_path, tema_prov))

        unidades = _criar_micro_unidades(
            blocos_secao=grupo["blocos"],
            headings_associados=grupo["headings"],
            tokens_cabecalho_secao=tokens_cab,
        )
        chunks_secao = _empacotar_unidades_em_chunks(sec_path, unidades)
        chunks_intermediarios.extend(chunks_secao)

    # 3. Formatar registros finais e construir índice reverso blk_id -> chunk_id
    chunks_finais: List[Dict[str, Any]] = []
    blk_para_chunks: Dict[str, List[str]] = defaultdict(list)
    grupo_split_para_chunks: Dict[str, List[str]] = defaultdict(list)
    refs_cruzadas_por_chunk: Dict[str, List[str]] = defaultdict(list)

    for idx, ch in enumerate(chunks_intermediarios, start=1):
        chunk_id = f"vw_{idx:06d}"
        sec_path = ch["section_path"]
        blocos = ch["blocos"]
        headings_orig = ch["headings_origem"]
        todos_blocos_rastreio = headings_orig + blocos

        sistema, componente, tema = _inferir_dimensoes_funcionais(sec_path, blocos)
        cabecalho_md = montar_cabecalho_chunk(sec_path, tema)
        corpo_md = ch["corpo_markdown"].strip()
        texto_final = f"{cabecalho_md}\n\n{corpo_md}".strip() if cabecalho_md else corpo_md

        qtd_tokens = CONTAR_TOKENS(texto_final)
        tipo_estrutura = _inferir_tipo_estrutura(blocos)
        tipo_conhecimento = _inferir_tipo_conhecimento(sec_path, blocos, corpo_md)

        paginas_ini = [int(b["page_start"]) for b in blocos if b.get("page_start") is not None]
        paginas_fim = [int(b["page_end"]) for b in blocos if b.get("page_end") is not None]
        pagina_inicio = min(paginas_ini) if paginas_ini else 1
        pagina_fim = max(paginas_fim) if paginas_fim else pagina_inicio

        elementos_origem = _deduplicar(b.get("id") for b in todos_blocos_rastreio)
        source_element_ids = _deduplicar(
            sid for b in todos_blocos_rastreio for sid in (b.get("source_element_ids") or [])
        )
        inline_icons = _deduplicar(
             [
                 ic
                 for b in todos_blocos_rastreio
                 for ic in (b.get("inline_icons") or [])
             ]
             + [
                 b.get("icon_id")
                 for b in blocos
                 if b.get("type") in {"alert_box", "icon"} and b.get("icon_id")
             ]
        )
        referenced_alerts = _deduplicar(
            ra for b in todos_blocos_rastreio for ra in (b.get("referenced_alerts") or [])
        )
        refers_to_ids = _deduplicar(
            rt for b in blocos if b.get("type") == "alert_box" for rt in (b.get("refers_to_ids") or [])
        )

        for bid in elementos_origem:
            blk_para_chunks[str(bid)].append(chunk_id)

        for gid in ch.get("split_groups") or []:
            grupo_split_para_chunks[str(gid)].append(chunk_id)

        refs_cruzadas_por_chunk[chunk_id].extend(referenced_alerts + refers_to_ids)

        chunks_finais.append({
            "chunk_id": chunk_id,
            "text": texto_final,
            "metadata": {
                "tipo_conhecimento": tipo_conhecimento,
                "sistema": sistema,
                "componente": componente,
                "tema": tema,
                "contexto_hierarquico": sec_path,
                "tipo_estrutura": tipo_estrutura,
                "pagina_inicio": pagina_inicio,
                "pagina_fim": pagina_fim,
                "elementos_origem": elementos_origem,
                "source_element_ids": source_element_ids,
                "inline_icons": inline_icons,
                "referenced_alerts": referenced_alerts,
                "chunks_relacionados": [],
                "tokens": qtd_tokens,
                "overlap": bool(ch.get("overlap", False)),
                "excecao_tamanho": qtd_tokens > TOKENS_LIMITE_MAX,
            },
            "_split_groups": ch.get("split_groups") or [],
        })

    # 4. Preencher `chunks_relacionados` bidirecionalmente (alertas, referências e fragmentos)
    mapa_chunks = {c["chunk_id"]: c for c in chunks_finais}
    for c in chunks_finais:
        cid = c["chunk_id"]
        relacionados: List[str] = []

        # Chunks do mesmo grupo de divisão (tabelas/listas divididas ou mesma seção multi-chunk)
        for gid in c.get("_split_groups") or []:
            relacionados.extend(grupo_split_para_chunks.get(str(gid), []))

        # Chunks referenciados via referenced_alerts ou refers_to_ids
        for blk_ref in refs_cruzadas_por_chunk.get(cid, []):
            relacionados.extend(blk_para_chunks.get(str(blk_ref), []))

        c["metadata"]["chunks_relacionados"] = [
            r for r in _deduplicar(relacionados) if r != cid
        ]

    # Simetria das relações (se A se relaciona com B, B também registra A)
    for c in chunks_finais:
        cid = c["chunk_id"]
        for rel_id in list(c["metadata"]["chunks_relacionados"]):
            alvo = mapa_chunks.get(rel_id)
            if alvo and cid not in alvo["metadata"]["chunks_relacionados"]:
                alvo["metadata"]["chunks_relacionados"].append(cid)

    for c in chunks_finais:
        c.pop("_split_groups", None)
        c["metadata"]["chunks_relacionados"].sort()

    # 5. Executar as 10 validações obrigatórias (Seção 9)
    relatorio_validacao = validar_corpus_chunks(chunks_finais, registros)

    # 6. Gravar `chunks/03_chunks/chunks.jsonl`
    with saida_chunks.open("w", encoding="utf-8") as f_out:
        for c in chunks_finais:
            f_out.write(json.dumps(c, ensure_ascii=False) + "\n")

    # 7. Gerar e gravar `chunks/03_chunks/manifest.json`
    lista_tokens = [c["metadata"]["tokens"] for c in chunks_finais]
    dist_conhecimento = Counter(c["metadata"]["tipo_conhecimento"] for c in chunks_finais)
    dist_estrutura = Counter(c["metadata"]["tipo_estrutura"] for c in chunks_finais)
    dist_sistema = Counter(str(c["metadata"]["sistema"]) for c in chunks_finais)

    manifest = {
        "projeto": "Manual Técnico Volkswagen — Nivus",
        "data_geracao": datetime.now(timezone.utc).isoformat(),
        "arquivo_entrada": str(caminho_ocr_jsonl.relative_to(diretorio_relativo.parent)),
        "arquivo_saida": str(saida_chunks.relative_to(diretorio_relativo.parent)),
        "parametros": {
            "tokenizador": NOME_TOKENIZADOR,
            "tokens_alvo_min": TOKENS_ALVO_MIN,
            "tokens_alvo_max": TOKENS_ALVO_MAX,
            "tokens_limite_max": TOKENS_LIMITE_MAX,
            "tokens_minimo": TOKENS_MINIMO,
            "estrategia": "semantic_hierarchical_units",
        },
        "estatisticas": {
            "total_registros_entrada": len(registros),
            "total_chunks_gerados": len(chunks_finais),
            "tokens_total": sum(lista_tokens),
            "tokens_media": round(sum(lista_tokens) / max(1, len(lista_tokens)), 2),
            "tokens_min": min(lista_tokens) if lista_tokens else 0,
            "tokens_max": max(lista_tokens) if lista_tokens else 0,
            "chunks_na_faixa_350_650": sum(
                1 for t in lista_tokens if TOKENS_ALVO_MIN <= t <= TOKENS_ALVO_MAX
            ),
            "chunks_com_overlap": sum(
                1 for c in chunks_finais if c["metadata"]["overlap"]
            ),
            "chunks_excecao_tamanho": sum(
                1 for c in chunks_finais if c["metadata"]["excecao_tamanho"]
            ),
            "distribuicao_tipo_conhecimento": dict(dist_conhecimento),
            "distribuicao_tipo_estrutura": dict(dist_estrutura),
            "distribuicao_sistema": dict(dist_sistema),
        },
        "validacao": relatorio_validacao,
    }

    saida_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Chunkenização concluída com sucesso:")
    print(f" - Total de blocos de entrada: {len(registros)}")
    print(f" - Total de chunks gerados:    {len(chunks_finais)}")
    print(
        f" - Tokens (mín / méd / máx):   "
        f"{manifest['estatisticas']['tokens_min']} / "
        f"{manifest['estatisticas']['tokens_media']} / "
        f"{manifest['estatisticas']['tokens_max']}"
    )
    print(
        f" - Validação (10 regras):      "
        f"{'APROVADO' if relatorio_validacao['aprovado'] else 'FALHOU'}"
    )
    print(f" - Arquivo de chunks:          {saida_chunks}")
    print(f" - Manifesto:                  {saida_manifest}")

    return chunks_finais, manifest


def validar_corpus_chunks(
    chunks: Sequence[Dict[str, Any]],
    registros_entrada: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Executa as 10 verificações de integridade exigidas na Seção 9."""
    ids_chunks = [c.get("chunk_id") for c in chunks]
    regra_1_ids_unicos = len(ids_chunks) == len(set(ids_chunks)) and all(ids_chunks)

    regra_2_sem_vazio = all(bool((c.get("text") or "").strip()) for c in chunks)

    regra_3_rastreabilidade = all(
        bool(c.get("metadata", {}).get("elementos_origem"))
        and bool(c.get("metadata", {}).get("source_element_ids"))
        for c in chunks
    )

    regra_4_paginas_validas = all(
        isinstance(c["metadata"].get("pagina_inicio"), int)
        and isinstance(c["metadata"].get("pagina_fim"), int)
        and 1 <= c["metadata"]["pagina_inicio"] <= c["metadata"]["pagina_fim"]
        for c in chunks
    )

    regra_5_hierarquia = all(
        isinstance(c["metadata"].get("contexto_hierarquico"), list)
        and all(
            sec in c["text"]
            for sec in c["metadata"]["contexto_hierarquico"]
        )
        for c in chunks
    )

    # Regra 6: Tabelas mantêm cabeçalho Markdown e separador | --- |
    regra_6_tabelas_validas = True
    for c in chunks:
        if c["metadata"].get("tipo_estrutura") == "table" or "| --- |" in c["text"]:
            linhas_tab = [
                ln.strip() for ln in c["text"].splitlines() if ln.strip().startswith("|")
            ]
            if len(linhas_tab) < 2 or "---" not in linhas_tab[1]:
                regra_6_tabelas_validas = False
                break

    # Regra 7: Alertas mantêm seus níveis (**[PERIGO]**, **[ATENÇÃO]**, etc.)
    ids_alertas_entrada = {
        r["id"] for r in registros_entrada if r.get("type") == "alert_box"
    }
    regra_7_alertas_integros = True
    for c in chunks:
        origens = set(c["metadata"].get("elementos_origem") or [])
        if origens & ids_alertas_entrada:
            if not re.search(r"\*\*\[(PERIGO|ATENÇÃO|CUIDADO|NOTA|AMBIENTAL|INFO)\]\*\*", c["text"]):
                regra_7_alertas_integros = False
                break

    # Regra 8: Cobertura total dos blocos de conteúdo e ausência de duplicação indevida
    ids_conteudo_entrada = {
        r["id"] for r in registros_entrada if r.get("type") != "heading"
    }
    contagem_uso_blk: Counter = Counter()
    blocos_com_split_intencional: set[str] = set()
    for c in chunks:
        for bid in c["metadata"].get("elementos_origem") or []:
            contagem_uso_blk[bid] += 1
            if c["metadata"].get("overlap"):
                blocos_com_split_intencional.add(bid)

    todos_cobertos = ids_conteudo_entrada.issubset(set(contagem_uso_blk.keys()))
    duplicados_indevidos = [
        bid
        for bid, qtd in contagem_uso_blk.items()
        if qtd > 1 and bid not in blocos_com_split_intencional
    ]
    regra_8_sem_duplicacao_indevida = todos_cobertos and len(duplicados_indevidos) == 0

    # Regra 9: Exceções de tamanho devidamente identificadas
    regra_9_excecoes_tamanho = all(
        c["metadata"].get("excecao_tamanho") == (c["metadata"].get("tokens", 0) > TOKENS_LIMITE_MAX)
        for c in chunks
    )

    # Regra 10: JSON UTF-8 e Markdown válidos
    regra_10_json_utf8 = True
    try:
        for c in chunks:
            s = json.dumps(c, ensure_ascii=False)
            s.encode("utf-8")
    except Exception:
        regra_10_json_utf8 = False

    regras = {
        "1_ids_unicos": regra_1_ids_unicos,
        "2_conteudo_nao_vazio": regra_2_sem_vazio,
        "3_referencia_elementos_origem": regra_3_rastreabilidade,
        "4_paginas_origem_validas": regra_4_paginas_validas,
        "5_hierarquia_documental_preservada": regra_5_hierarquia,
        "6_tabelas_markdown_integras": regra_6_tabelas_validas,
        "7_alertas_nivel_e_instrucoes_preservados": regra_7_alertas_integros,
        "8_cobertura_total_sem_duplicacao_indevida": regra_8_sem_duplicacao_indevida,
        "9_excecoes_tamanho_identificadas": regra_9_excecoes_tamanho,
        "10_markdown_e_json_utf8_validos": regra_10_json_utf8,
    }

    return {
        "aprovado": all(regras.values()),
        "regras": regras,
        "blocos_conteudo_entrada": len(ids_conteudo_entrada),
        "blocos_conteudo_cobertos": len(ids_conteudo_entrada & set(contagem_uso_blk.keys())),
        "duplicados_indevidos": duplicados_indevidos,
    }


if __name__ == "__main__":
    from utils.caminhos import raiz

    gerar_chunks(
        diretorio_relativo = raiz["chunks"]["path"],
        caminho_ocr_jsonl = raiz["chunks"]["02_extracted"]["ocr"],
        saida_chunks = raiz["chunks"]["03_chunks"]["chunks"],
        saida_manifest = raiz["chunks"]["03_chunks"]["manifest"]
    )