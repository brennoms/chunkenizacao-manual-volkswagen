"""
Etapa de Chunkenização Semântica e Hierárquica Refinada — Manual Técnico Volkswagen.

Lê `chunks/02_extracted/manual_tecnico_volkswagen_ocr.jsonl` e gera:
- `chunks/03_chunks/chunks.jsonl` (corpus completo com metadados enriquecidos)
- `chunks/03_chunks/chunks_tecnicos.jsonl` (coleção exclusiva de conteúdo técnico)
- `chunks/03_chunks/chunks_indices.jsonl` (coleção separada para Índice e Índice Remissivo)
- `chunks/03_chunks/manifest.json` (estatísticas globais, técnicas e de índices + validação)
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Parâmetros de dimensionamento
TOKENS_ALVO_MIN = 350
TOKENS_ALVO_MAX = 650
TOKENS_LIMITE_MAX = 850
TOKENS_MINIMO = 80
TOKENS_FUSAO_CAUDA = 240  # Limiar para absorver sobras pequenas de seção

SISTEMAS_INDICE = {"indice", "indice_remissivo"}


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


def _eh_secao_indice(section_path: Sequence[str]) -> bool:
    if not section_path:
        return False
    raiz_slug = _slugify(section_path[0])
    return raiz_slug in SISTEMAS_INDICE


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
    """Constrói o cabeçalho contextual em Markdown."""
    linhas: List[str] = []
    for idx, sec in enumerate(section_path):
        nivel = min(idx + 1, 6)
        prefixo = "#" * nivel
        linhas.append(f"{prefixo} {sec.strip()}")

    if tema:
        linhas.append(f"**Assunto:** {tema}")

    return "\n\n".join(linhas)


# ---------------------------------------------------------------------------
# CORREÇÃO 3: Divisão Inteligente de Estruturas Grandes (> TOKENS_LIMITE_MAX)
# ---------------------------------------------------------------------------

def _dividir_tabela_grande(
    bloco: Dict[str, Any],
    tokens_cabecalho_secao: int,
    prefixo_blocos: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Fragmenta tabelas que ultrapassam o limite máximo de tokens por grupos de linhas,
    repetindo o cabeçalho da tabela em cada fragmento e preservando subtítulos acoplados.
    """
    prefixo_blocos = prefixo_blocos or []
    md_prefixo = "\n\n".join(
        m for m in (formatar_bloco_markdown(b) for b in prefixo_blocos) if m
    )
    tokens_prefixo = CONTAR_TOKENS(md_prefixo) if md_prefixo else 0

    md = (bloco.get("markdown") or "").strip()
    linhas = [ln for ln in md.splitlines() if ln.strip()]
    if len(linhas) <= 3:
        md_completo = f"{md_prefixo}\n\n{md}".strip() if md_prefixo else md
        return [{
            "blocos": prefixo_blocos + [bloco],
            "markdown_override": md_completo,
            "overlap": False,
            "overlap_reason": None,
            "split_group_id": None,
        }]

    cabecalho_md = linhas[:2]
    linhas_dados = linhas[2:]
    orcamento_max = max(280, TOKENS_LIMITE_MAX - tokens_cabecalho_secao - tokens_prefixo - 25)
    orcamento_alvo = max(240, TOKENS_ALVO_MAX - tokens_cabecalho_secao - tokens_prefixo)

    fragmentos: List[Dict[str, Any]] = []
    acumuladas: List[str] = []
    gid = f"split_tbl_{bloco['id']}"

    def _montar_md_tabela(linhas_corpo: List[str], eh_continuacao: bool) -> str:
        tabela_str = "\n".join(cabecalho_md + linhas_corpo)
        if md_prefixo:
            sufixo = " *(continuação)*" if eh_continuacao else ""
            return f"{md_prefixo}{sufixo}\n\n{tabela_str}"
        return tabela_str

    for linha in linhas_dados:
        eh_cont = len(fragmentos) > 0
        candidato = _montar_md_tabela(acumuladas + [linha], eh_cont)
        tokens_cand = CONTAR_TOKENS(candidato)
        if acumuladas and (tokens_cand > orcamento_alvo or (tokens_cabecalho_secao + tokens_cand) > TOKENS_LIMITE_MAX):
            fragmentos.append({
                "blocos": (prefixo_blocos + [bloco]) if not eh_cont else [bloco],
                "markdown_override": _montar_md_tabela(acumuladas, eh_cont),
                "overlap": eh_cont,
                "overlap_reason": "continuidade_tabela_dividida" if eh_cont else None,
                "split_group_id": gid,
            })
            acumuladas = [linha]
        else:
            acumuladas.append(linha)

    if acumuladas:
        eh_cont = len(fragmentos) > 0
        fragmentos.append({
            "blocos": (prefixo_blocos + [bloco]) if not eh_cont else [bloco],
            "markdown_override": _montar_md_tabela(acumuladas, eh_cont),
            "overlap": eh_cont,
            "overlap_reason": "continuidade_tabela_dividida" if eh_cont else None,
            "split_group_id": gid if eh_cont else None,
        })

    return fragmentos


def _dividir_lista_grande(
    bloco: Dict[str, Any],
    tokens_cabecalho_secao: int,
    prefixo_blocos: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Fragmenta listas que ultrapassam o limite máximo preservando o `lead_text`
    e eventuais blocos introdutórios acoplados (corrige o caso vw_000528).
    """
    prefixo_blocos = prefixo_blocos or []
    md_prefixo = "\n\n".join(
        m for m in (formatar_bloco_markdown(b) for b in prefixo_blocos) if m
    )
    tokens_prefixo = CONTAR_TOKENS(md_prefixo) if md_prefixo else 0

    lead_text = (bloco.get("lead_text") or "").strip()
    items = bloco.get("items") or []
    estilo = bloco.get("list_style", "unordered")
    if len(items) <= 2:
        md_completo = formatar_bloco_markdown(bloco)
        if md_prefixo:
            md_completo = f"{md_prefixo}\n\n{md_completo}".strip()
        return [{
            "blocos": prefixo_blocos + [bloco],
            "markdown_override": md_completo,
            "overlap": False,
            "overlap_reason": None,
            "split_group_id": None,
        }]

    orcamento_alvo = max(250, TOKENS_ALVO_MAX - tokens_cabecalho_secao - tokens_prefixo)
    fragmentos: List[Dict[str, Any]] = []
    itens_atuais: List[str] = []
    gid = f"split_lst_{bloco['id']}"

    def _render_parcial(lista_itens: List[str], offset_idx: int, continuacao: bool) -> str:
        linhas: List[str] = []
        if md_prefixo:
            sufixo_pref = " *(continuação)*" if continuacao and not lead_text else ""
            linhas.append(f"{md_prefixo}{sufixo_pref}")
        if lead_text:
            sufixo = " (continuação)" if continuacao else ""
            linhas.append(f"{lead_text}{sufixo}")
        itens_fmt: List[str] = []
        for idx_local, it in enumerate(lista_itens, start=offset_idx):
            if estilo == "ordered":
                marcador = f"{idx_local}."
            elif estilo == "checklist":
                marcador = "- [x]"
            else:
                marcador = "-"
            itens_fmt.append(f"{marcador} {str(it).strip()}")
        linhas.append("\n".join(itens_fmt))
        return "\n\n".join(linhas)

    offset = 1
    for item in items:
        eh_cont = len(fragmentos) > 0
        candidato = _render_parcial(itens_atuais + [item], offset, eh_cont)
        tokens_cand = CONTAR_TOKENS(candidato)
        if itens_atuais and (
            tokens_cand > orcamento_alvo
            or (tokens_cabecalho_secao + tokens_cand) > (TOKENS_LIMITE_MAX - 20)
        ):
            fragmentos.append({
                "blocos": (prefixo_blocos + [bloco]) if not eh_cont else [bloco],
                "markdown_override": _render_parcial(itens_atuais, offset, eh_cont),
                "overlap": eh_cont,
                "overlap_reason": "continuidade_procedimento_ou_lista" if eh_cont else None,
                "split_group_id": gid,
            })
            offset += len(itens_atuais)
            itens_atuais = [item]
        else:
            itens_atuais.append(item)

    if itens_atuais:
        eh_cont = len(fragmentos) > 0
        fragmentos.append({
            "blocos": (prefixo_blocos + [bloco]) if not eh_cont else [bloco],
            "markdown_override": _render_parcial(itens_atuais, offset, eh_cont),
            "overlap": eh_cont,
            "overlap_reason": "continuidade_procedimento_ou_lista" if eh_cont else None,
            "split_group_id": gid if eh_cont else None,
        })

    return fragmentos


def _dividir_imagem_grande(
    bloco: Dict[str, Any],
    tokens_cabecalho_secao: int,
    prefixo_blocos: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Divide blocos `image` cujos `legend_items` contêm subseções explicativas extensas
    (corrige o caso `vw_000054` com 917 tokens na Fig. 11 Active Info Display).
    Mantém a descrição visual + legendas numeradas na Parte 1 e separa as subseções
    conceituais (ex.: 'Perfis de informação') na Parte 2 com referência contextual à figura.
    """
    prefixo_blocos = prefixo_blocos or []
    md_prefixo = "\n\n".join(
        m for m in (formatar_bloco_markdown(b) for b in prefixo_blocos) if m
    )

    figure_label = (bloco.get("figure_label") or "").strip()
    caption = (bloco.get("caption") or "").strip()
    description = (bloco.get("description") or "").strip()
    legend_items = [str(it).strip() for it in (bloco.get("legend_items") or []) if str(it).strip()]

    titulo_fig = caption or figure_label or "Figura técnica"
    cabecalho_img: List[str] = []
    if md_prefixo:
        cabecalho_img.append(md_prefixo)
    if titulo_fig:
        cabecalho_img.append(f"**{titulo_fig}**")
    if description:
        cabecalho_img.append(f"*[Imagem: {description}]*")

    if len(legend_items) <= 2:
        md_total = formatar_bloco_markdown(bloco)
        if md_prefixo:
            md_total = f"{md_prefixo}\n\n{md_total}".strip()
        return [{
            "blocos": prefixo_blocos + [bloco],
            "markdown_override": md_total,
            "overlap": False,
            "overlap_reason": None,
            "split_group_id": None,
        }]

    # Procura ponto natural de separação (subtítulo interno nos legend_items, ex.: "Perfis de informação")
    ponto_corte_natural: Optional[int] = None
    for idx_it, item in enumerate(legend_items):
        if idx_it >= 3 and len(item) <= 60 and not item.endswith((".", ":", ";")) and not item.startswith("<icon:"):
            ponto_corte_natural = idx_it
            break

    gid = f"split_img_{bloco['id']}"
    orcamento_max = TOKENS_LIMITE_MAX - tokens_cabecalho_secao - 25

    if ponto_corte_natural is not None:
        itens_p1 = legend_items[:ponto_corte_natural]
        itens_p2 = legend_items[ponto_corte_natural:]
        md_p1 = "\n".join(cabecalho_img + [f"- {it}" for it in itens_p1])
        contexto_p2 = f"**Referência visual:** {titulo_fig} *(continuação dos perfis e legendas)*"
        md_p2 = "\n".join([contexto_p2] + [f"- {it}" for it in itens_p2])

        if CONTAR_TOKENS(md_p1) <= orcamento_max and CONTAR_TOKENS(md_p2) <= orcamento_max:
            return [
                {
                    "blocos": prefixo_blocos + [bloco],
                    "markdown_override": md_p1,
                    "overlap": False,
                    "overlap_reason": None,
                    "split_group_id": gid,
                },
                {
                    "blocos": [bloco],
                    "markdown_override": md_p2,
                    "overlap": True,
                    "overlap_reason": "continuidade_legenda_figura",
                    "split_group_id": gid,
                },
            ]

    # Fallback: divisão progressiva por orçamento de tokens
    fragmentos: List[Dict[str, Any]] = []
    acumulados: List[str] = []

    def _montar_frag_img(itens_locais: List[str], eh_cont: bool) -> str:
        if not eh_cont:
            base = list(cabecalho_img)
        else:
            base = [f"**{titulo_fig} (continuação da legenda)**"]
        base.extend(f"- {it}" for it in itens_locais)
        return "\n".join(base)

    for item in legend_items:
        eh_cont = len(fragmentos) > 0
        cand = _montar_frag_img(acumulados + [item], eh_cont)
        if acumulados and CONTAR_TOKENS(cand) > orcamento_max:
            fragmentos.append({
                "blocos": (prefixo_blocos + [bloco]) if not eh_cont else [bloco],
                "markdown_override": _montar_frag_img(acumulados, eh_cont),
                "overlap": eh_cont,
                "overlap_reason": "continuidade_legenda_figura" if eh_cont else None,
                "split_group_id": gid,
            })
            acumulados = [item]
        else:
            acumulados.append(item)

    if acumulados:
        eh_cont = len(fragmentos) > 0
        fragmentos.append({
            "blocos": (prefixo_blocos + [bloco]) if not eh_cont else [bloco],
            "markdown_override": _montar_frag_img(acumulados, eh_cont),
            "overlap": eh_cont,
            "overlap_reason": "continuidade_legenda_figura" if eh_cont else None,
            "split_group_id": gid if eh_cont else None,
        })

    return fragmentos


# ---------------------------------------------------------------------------
# Empacotamento em Micro-Unidades Coesas e Fusão Semântica
# ---------------------------------------------------------------------------

def _eh_subtitulo_curto(bloco: Dict[str, Any]) -> bool:
    """
    Identifica parágrafos curtos que atuam como subtítulos internos de tópico
    ou parágrafos introdutórios terminados em ':'.
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
        len(texto_sem_icone) <= 85
        and not texto_sem_icone.endswith((".", "!", "?", ";"))
        and not texto_sem_icone.startswith("⚠️")
    ):
        return True
    return False


def _eh_remissao_capitulo_curta(bloco: Dict[str, Any]) -> bool:
    """Identifica parágrafos curtos do tipo '⚠️ Observe no início desse capítulo na página X.'"""
    if bloco.get("type") != "paragraph":
        return False
    texto = (bloco.get("content") or "").strip()
    return len(texto) <= 110 and ("observe no" in texto.lower() or "início desse capítulo" in texto.lower())


def _criar_micro_unidades(
    blocos_secao: List[Dict[str, Any]],
    headings_associados: List[Dict[str, Any]],
    tokens_cabecalho_secao: int,
) -> List[Dict[str, Any]]:
    """
    Agrupa blocos fortemente acoplados dentro de uma mesma seção em micro-unidades
    e divide estruturas que excedam `TOKENS_LIMITE_MAX` (tabelas, listas ou imagens longas).
    Garante que blocos `heading` internos nunca fiquem isolados em uma unidade própria.
    """
    unidades: List[Dict[str, Any]] = []
    buffer_acoplado: List[Dict[str, Any]] = []

    for idx, bloco in enumerate(blocos_secao):
        btype = bloco.get("type")

        # Headings internos, ícones isolados, remissões curtas ou subtítulos acoplam-se SEMPRE ao bloco seguinte
        if (
            btype in {"icon", "heading"}
            or _eh_subtitulo_curto(bloco)
            or _eh_remissao_capitulo_curta(bloco)
        ) and idx < len(blocos_secao) - 1:
            buffer_acoplado.append(bloco)
            continue

        grupo = buffer_acoplado + [bloco]
        buffer_acoplado = []

        md_grupo = "\n\n".join(
            m for m in (formatar_bloco_markdown(b) for b in grupo) if m
        )
        tokens_grupo = CONTAR_TOKENS(md_grupo)

        # Verifica se a unidade (isolada ou com prefixo acoplado) excede TOKENS_LIMITE_MAX
        if tokens_cabecalho_secao + tokens_grupo > TOKENS_LIMITE_MAX:
            prefixo = grupo[:-1]
            ultimo_bloco = grupo[-1]
            utype = ultimo_bloco.get("type")

            if utype == "table":
                unidades.extend(_dividir_tabela_grande(ultimo_bloco, tokens_cabecalho_secao, prefixo))
                continue
            if utype == "list":
                unidades.extend(_dividir_lista_grande(ultimo_bloco, tokens_cabecalho_secao, prefixo))
                continue
            if utype == "image":
                unidades.extend(_dividir_imagem_grande(ultimo_bloco, tokens_cabecalho_secao, prefixo))
                continue
            if prefixo:
                # Só separa o prefixo se ele contiver pelo menos um bloco de conteúdo real com ID válido
                tem_id_real = any(
                    b.get("id") and not str(b.get("id")).startswith(("synthetic_", "sub_"))
                    for b in prefixo
                )
                if tem_id_real:
                    md_pref = "\n\n".join(m for m in (formatar_bloco_markdown(b) for b in prefixo) if m)
                    md_ult = formatar_bloco_markdown(ultimo_bloco)
                    unidades.append({
                        "blocos": prefixo,
                        "markdown_override": md_pref,
                        "overlap": False,
                        "overlap_reason": None,
                        "split_group_id": None,
                    })
                    unidades.append({
                        "blocos": [ultimo_bloco],
                        "markdown_override": md_ult,
                        "overlap": False,
                        "overlap_reason": None,
                        "split_group_id": None,
                    })
                    continue

        unidades.append({
            "blocos": grupo,
            "markdown_override": md_grupo,
            "overlap": False,
            "overlap_reason": None,
            "split_group_id": None,
        })

    if buffer_acoplado:
        if unidades:
            ultima = unidades[-1]
            md_extra = "\n\n".join(m for m in (formatar_bloco_markdown(b) for b in buffer_acoplado) if m)
            md_combinado = f"{ultima['markdown_override']}\n\n{md_extra}".strip()
            if tokens_cabecalho_secao + CONTAR_TOKENS(md_combinado) <= TOKENS_LIMITE_MAX:
                ultima["blocos"].extend(buffer_acoplado)
                ultima["markdown_override"] = md_combinado
            else:
                unidades.append({
                    "blocos": buffer_acoplado,
                    "markdown_override": md_extra,
                    "overlap": False,
                    "overlap_reason": None,
                    "split_group_id": None,
                })
        else:
            md_restante = "\n\n".join(
                m for m in (formatar_bloco_markdown(b) for b in buffer_acoplado) if m
            )
            unidades.append({
                "blocos": buffer_acoplado,
                "markdown_override": md_restante,
                "overlap": False,
                "overlap_reason": None,
                "split_group_id": None,
            })

    if unidades and headings_associados:
        unidades[0]["headings_origem"] = list(headings_associados)

    return unidades


# ---------------------------------------------------------------------------
# CORREÇÃO 6: Avaliação Seletiva de Overlap Contextual
# ---------------------------------------------------------------------------

def _extrair_resumo_overlap(unidades_anteriores: List[Dict[str, Any]]) -> Optional[str]:
    """
    Extrai o último subtítulo curto ou lead_text relevante do chunk anterior
    quando o próximo chunk continua o mesmo procedimento ou explicação dependente.
    """
    for uni in reversed(unidades_anteriores):
        for b in reversed(uni.get("blocos") or []):
            if _eh_subtitulo_curto(b):
                txt = _limpar_marcadores_visuais(b.get("content") or "")
                if txt and len(txt) <= 90:
                    return txt
            if b.get("type") == "list" and b.get("lead_text"):
                txt = _limpar_marcadores_visuais(b.get("lead_text") or "")
                if txt and len(txt) <= 90:
                    return txt
    return None


def _precisa_overlap_contextual(
    chunk_anterior: List[Dict[str, Any]],
    chunk_atual: List[Dict[str, Any]],
) -> Tuple[bool, Optional[str]]:
    """
    Avalia se o chunk atual realmente depende do chunk anterior na mesma seção.
    Evita marcar overlap em figuras independentes ou tópicos autossuficientes.
    """
    for u in chunk_atual:
        if u.get("overlap"):
            return True, u.get("overlap_reason") or "estrutura_dividida"

    blocos_ant = [b for u in chunk_anterior for b in u.get("blocos", [])]
    blocos_atu = [b for u in chunk_atual for b in u.get("blocos", [])]
    if not blocos_ant or not blocos_atu:
        return False, None

    # Se ambos são imagens de vista geral independentes (ex.: Fig. 1 e Fig. 2), não há overlap
    if all(b.get("type") == "image" for b in blocos_atu):
        return False, None

    primeiro_bloco = blocos_atu[0]
    primeiro_txt = (
        primeiro_bloco.get("content")
        or primeiro_bloco.get("lead_text")
        or ""
    ).strip()

    # Caso 1: O chunk atual é composto apenas por caixas de alerta que complementam o chunk anterior
    if all(b.get("type") == "alert_box" for b in blocos_atu):
        return True, "alerta_complementar_ao_procedimento_anterior"

    # Caso 2: O primeiro bloco do chunk atual inicia com letra minúscula, item de lista solto ou continuação
    if primeiro_txt and primeiro_txt[0].islower():
        return True, "continuacao_textual_direta"

    # Caso 3: O chunk anterior terminou com lista procedimental e o atual continua com lista ou passos
    if blocos_ant[-1].get("type") == "list" and primeiro_bloco.get("type") == "list":
        if not _eh_subtitulo_curto(primeiro_bloco):
            return True, "continuacao_lista_procedimental"

    # Caso 4: Referência direta a alerta ou elemento presente no chunk anterior
    ids_ant = {b.get("id") for b in blocos_ant if b.get("id")}
    refs_atu = {
        rt
        for b in blocos_atu
        for rt in (b.get("refers_to_ids") or []) + (b.get("referenced_alerts") or [])
    }
    if ids_ant & refs_atu:
        return True, "referencia_cruzada_imediata_na_secao"

    return False, None


def _empacotar_unidades_em_chunks(
    section_path: List[str],
    unidades: List[Dict[str, Any]],
    eh_indice: bool = False,
) -> List[Dict[str, Any]]:
    """
    Agrupa as micro-unidades de uma seção buscando a faixa de 350–650 tokens
    (limite máximo de 850 tokens), com pós-processamento agressivo contra caudas pequenas.
    """
    if not unidades:
        return []

    tema_base = section_path[-1] if section_path else "Manual de instruções"
    tokens_cab = CONTAR_TOKENS(montar_cabecalho_chunk(section_path, tema_base))

    chunks_brutos: List[List[Dict[str, Any]]] = []
    atual: List[Dict[str, Any]] = []
    tokens_atuais = tokens_cab

    for uni in unidades:
        t_uni = CONTAR_TOKENS(uni["markdown_override"])

        if not atual:
            atual.append(uni)
            tokens_atuais = tokens_cab + t_uni
            continue

        novo_total = tokens_atuais + 2 + t_uni

        if uni.get("split_group_id") and any(
            u.get("split_group_id") == uni.get("split_group_id") for u in atual
        ):
            chunks_brutos.append(atual)
            atual = [uni]
            tokens_atuais = tokens_cab + t_uni
            continue

        ids_no_atual = {b["id"] for u in atual for b in u["blocos"] if b.get("id")}
        eh_alerta_vinculado = any(
            b.get("type") == "alert_box"
            and (
                b.get("alert_scope") == "local_topic"
                or bool(set(b.get("refers_to_ids") or []) & ids_no_atual)
            )
            for b in uni["blocos"]
        )

        # Limite flexível para manter alertas ou completar o mínimo de 350 tokens
        if eh_alerta_vinculado:
            limite_efetivo = TOKENS_LIMITE_MAX - 15
        elif tokens_atuais < TOKENS_ALVO_MIN:
            limite_efetivo = TOKENS_ALVO_MAX + 90  # permite chegar a ~740 para evitar deixar o atual < 350
        else:
            limite_efetivo = TOKENS_ALVO_MAX

        if novo_total <= min(limite_efetivo, TOKENS_LIMITE_MAX - 15):
            atual.append(uni)
            tokens_atuais = novo_total
        else:
            chunks_brutos.append(atual)
            atual = [uni]
            tokens_atuais = tokens_cab + t_uni

    if atual:
        chunks_brutos.append(atual)

    # CORREÇÃO 1: Pós-processamento e rebalanceamento de caudas/fragmentos pequenos na mesma seção
    if len(chunks_brutos) >= 2:
        idx_scan = len(chunks_brutos) - 1
        while idx_scan > 0:
            atual_c = chunks_brutos[idx_scan]
            ant_c = chunks_brutos[idx_scan - 1]

            md_atual = "\n\n".join(u["markdown_override"] for u in atual_c)
            t_atual = tokens_cab + CONTAR_TOKENS(md_atual)

            md_ant = "\n\n".join(u["markdown_override"] for u in ant_c)
            t_ant = tokens_cab + CONTAR_TOKENS(md_ant)

            mesmo_split = bool(
                {u.get("split_group_id") for u in atual_c if u.get("split_group_id")}
                & {u.get("split_group_id") for u in ant_c if u.get("split_group_id")}
            )

            # Se o chunk atual for pequeno (< TOKENS_FUSAO_CAUDA) e couber no anterior sem estourar 835 tokens
            if (
                not mesmo_split
                and t_atual < TOKENS_FUSAO_CAUDA
                and (t_ant + 2 + CONTAR_TOKENS(md_atual)) <= (TOKENS_LIMITE_MAX - 15)
            ):
                ant_c.extend(atual_c)
                chunks_brutos.pop(idx_scan)
            # Ou se puder rebalancear a última unidade do anterior para que ambos fiquem >= 300 tokens
            elif (
                not mesmo_split
                and t_atual < 180
                and len(ant_c) >= 2
            ):
                u_candidata = ant_c[-1]
                t_cand = CONTAR_TOKENS(u_candidata["markdown_override"])
                if (t_ant - t_cand) >= TOKENS_ALVO_MIN and (t_atual + t_cand) <= TOKENS_ALVO_MAX:
                    chunks_brutos[idx_scan].insert(0, ant_c.pop())
            idx_scan -= 1

    multiplos_na_secao = len(chunks_brutos) > 1
    saida_secao: List[Dict[str, Any]] = []
    gid_secao = f"sec_{_slugify('_'.join(section_path))}" if multiplos_na_secao else None

    for idx_c, grupo_uni in enumerate(chunks_brutos):
        blocos_chunk: List[Dict[str, Any]] = []
        headings_origem: List[Dict[str, Any]] = []
        partes_md: List[str] = []
        split_gids: List[str] = [gid_secao] if gid_secao else []

        tem_overlap = False
        motivo_overlap: Optional[str] = None
        if idx_c > 0 and not eh_indice:
            tem_overlap, motivo_overlap = _precisa_overlap_contextual(
                chunks_brutos[idx_c - 1],
                grupo_uni,
            )

        for u in grupo_uni:
            headings_origem.extend(u.get("headings_origem") or [])
            blocos_chunk.extend(u["blocos"])
            if u["markdown_override"]:
                partes_md.append(u["markdown_override"])
            if u.get("overlap"):
                tem_overlap = True
                motivo_overlap = u.get("overlap_reason") or motivo_overlap
            if u.get("split_group_id"):
                split_gids.append(u["split_group_id"])

        corpo_md = "\n\n".join(partes_md).strip()

        # Se há overlap contextual sem repetição explícita no texto, injeta nota concisa de continuidade
        if tem_overlap and idx_c > 0 and not any(u.get("overlap") for u in grupo_uni):
            resumo_ant = _extrair_resumo_overlap(chunks_brutos[idx_c - 1])
            if resumo_ant and resumo_ant.lower() not in corpo_md[:120].lower():
                nota_ctx = f"*[Contexto anterior: {resumo_ant}]*"
                if tokens_cab + CONTAR_TOKENS(f"{nota_ctx}\n\n{corpo_md}") <= TOKENS_LIMITE_MAX:
                    corpo_md = f"{nota_ctx}\n\n{corpo_md}"

        saida_secao.append({
            "section_path": list(section_path),
            "blocos": blocos_chunk,
            "headings_origem": headings_origem,
            "corpo_markdown": corpo_md,
            "overlap": tem_overlap,
            "motivo_overlap": motivo_overlap,
            "split_groups": _deduplicar(split_gids),
            "eh_indice": eh_indice,
        })

    return saida_secao


# ---------------------------------------------------------------------------
# CORREÇÕES 1 e 2: Fusão de Subseções Técnicas Pequenas e Agrupamento de Índices
# ---------------------------------------------------------------------------

def _estimar_tokens_grupo_secao(grupo: Dict[str, Any]) -> int:
    sec_path = grupo["section_path"]
    tema = sec_path[-1] if sec_path else "Manual de instruções"
    cab = montar_cabecalho_chunk(sec_path, tema)
    corpo = "\n\n".join(formatar_bloco_markdown(b) for b in grupo["blocos"])
    return CONTAR_TOKENS(f"{cab}\n\n{corpo}")


def _podem_unir_secoes_tecnicas(
    g1: Dict[str, Any],
    g2: Dict[str, Any],
) -> bool:
    """
    Avalia se duas seções técnicas adjacentes podem ser unidas sem perder coerência semântica:
    - Nunca une capítulos diferentes (`section_path[0]`).
    - Nunca une índice com conteúdo técnico.
    - Permite unir `⚠️ Introdução ao tema` ou subseções de 3º nível irmãs que compartilham
      exatamente o mesmo capítulo e componente (`section_path[:2]`), ou subseções curtas
      do mesmo capítulo introdutório (ex.: `Sobre este Manual`, `Significados dos símbolos`).
    """
    p1 = g1["section_path"]
    p2 = g2["section_path"]
    if not p1 or not p2:
        return False
    if _eh_secao_indice(p1) or _eh_secao_indice(p2):
        return False
    if p1[0] != p2[0]:
        return False

    # Mesmo capítulo e mesmo componente (ex.: Nível 3 -> Nível 2 ou Nível 3 -> Nível 3 irmão)
    if len(p1) >= 2 and len(p2) >= 2 and p1[:2] == p2[:2]:
        return True

    # Capítulos preliminares curtos ou vistas diretas sob o mesmo capítulo de 1º nível
    cap_slug = _slugify(p1[0])
    if cap_slug in {
        "muito_obrigado_por_sua_confianca",
        "sobre_este_manual_de_instrucoes",
        "significados_dos_simbolos",
        "volante",
        "aquecimento_e_ar_condicionado",
        "abreviaturas_utilizadas",
    }:
        return True

    # Se um dos grupos for muito pequeno (< 140 tokens) e pertencer ao mesmo capítulo na mesma página
    t1 = _estimar_tokens_grupo_secao(g1)
    t2 = _estimar_tokens_grupo_secao(g2)
    pag_g1_fim = max((int(b.get("page_end") or 0) for b in g1["blocos"]), default=0)
    pag_g2_ini = min((int(b.get("page_start") or 9999) for b in g2["blocos"]), default=9999)
    if (t1 < 140 or t2 < 140) and abs(pag_g2_ini - pag_g1_fim) <= 1:
        return True

    return False


def _fundir_grupos_secao_adjacentes(
    grupos_secao: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Reduz a fragmentação excessiva unindo:
    1. Entradas consecutivas de `Índice` e `Índice remissivo` até a janela de 350–620 tokens.
    2. Subseções técnicas pequenas e fortemente relacionadas (mesmo `sistema` e `componente`)
       quando estão abaixo de `TOKENS_ALVO_MIN` (350 tokens) e a soma cabe no orçamento.
    """
    if not grupos_secao:
        return []

    resultado: List[Dict[str, Any]] = []

    for grupo in grupos_secao:
        if not resultado:
            resultado.append(grupo)
            continue

        anterior = resultado[-1]
        p_ant = anterior["section_path"]
        p_atu = grupo["section_path"]

        # Caso A: Agrupamento dedicado para Índice e Índice Remissivo
        if _eh_secao_indice(p_ant) and _eh_secao_indice(p_atu) and p_ant[0] == p_atu[0]:
            t_ant = _estimar_tokens_grupo_secao(anterior)
            t_atu = _estimar_tokens_grupo_secao(grupo)
            if (t_ant + t_atu) <= TOKENS_ALVO_MAX:
                # Se a 1ª seção do índice ainda tinha subseção no path (ex.: "Rádios"), preserva-a no topo
                if len(p_ant) > 1 and not anterior.get("_idx_topo_preservado"):
                    anterior["blocos"].insert(0, {
                        "id": f"synthetic_idx_ant_{len(anterior['blocos'])}",
                        "type": "heading",
                        "content": f"Seção: {p_ant[-1]}",
                        "page_start": anterior["blocos"][0].get("page_start"),
                        "page_end": anterior["blocos"][0].get("page_end"),
                        "source_element_ids": [],
                    })
                    anterior["_idx_topo_preservado"] = True

                if len(p_atu) > 1:
                    subtitulo_idx = p_atu[-1]
                    anterior["blocos"].append({
                        "id": f"synthetic_idx_{len(anterior['blocos'])}",
                        "type": "heading",
                        "content": f"Seção: {subtitulo_idx}",
                        "page_start": grupo["blocos"][0].get("page_start"),
                        "page_end": grupo["blocos"][0].get("page_end"),
                        "source_element_ids": [],
                    })
                anterior["headings"].extend(grupo["headings"])
                anterior["blocos"].extend(grupo["blocos"])
                if len(anterior["section_path"]) > 1:
                    anterior["section_path"] = [p_ant[0]]
                continue

        # Caso B: Fusão de subseções técnicas pequenas e relacionadas
        if _podem_unir_secoes_tecnicas(anterior, grupo):
            t_ant = _estimar_tokens_grupo_secao(anterior)
            t_atu = _estimar_tokens_grupo_secao(grupo)
            limite_fusao = (
                (TOKENS_LIMITE_MAX - 35)
                if (t_ant < TOKENS_MINIMO or t_atu < TOKENS_MINIMO)
                else (TOKENS_ALVO_MAX + 30)
            )
            if (t_ant < TOKENS_ALVO_MIN or t_atu < 220) and (t_ant + t_atu) <= limite_fusao:
                prefixo_comum = []
                for s1, s2 in zip(p_ant, p_atu):
                    if s1 == s2:
                        prefixo_comum.append(s1)
                    else:
                        break

                # Preserva o subtítulo da 1ª subseção caso ela perca nível hierárquico
                sobra_ant = p_ant[len(prefixo_comum):]
                if sobra_ant and not anterior.get("_subtitulo_topo_injetado"):
                    titulo_ant = " — ".join(_limpar_marcadores_visuais(s) for s in sobra_ant if s)
                    if titulo_ant:
                        anterior["blocos"].insert(0, {
                            "id": f"sub_ant_{len(anterior['blocos'])}",
                            "type": "heading",
                            "content": titulo_ant,
                            "page_start": anterior["blocos"][0].get("page_start"),
                            "page_end": anterior["blocos"][0].get("page_end"),
                            "source_element_ids": [],
                        })
                        anterior["_subtitulo_topo_injetado"] = True

                # Preserva o subtítulo da 2ª subseção antes dos novos blocos
                diferenca_path = [s for s in p_atu if s not in prefixo_comum]
                if diferenca_path:
                    titulo_sub = " — ".join(_limpar_marcadores_visuais(s) for s in diferenca_path if s)
                    if titulo_sub:
                        anterior["blocos"].append({
                            "id": f"sub_atu_{len(anterior['blocos'])}",
                            "type": "heading",
                            "content": titulo_sub,
                            "page_start": grupo["blocos"][0].get("page_start"),
                            "page_end": grupo["blocos"][0].get("page_end"),
                            "source_element_ids": [],
                        })

                anterior["headings"].extend(grupo["headings"])
                anterior["blocos"].extend(grupo["blocos"])
                if prefixo_comum:
                    anterior["section_path"] = prefixo_comum
                continue

        resultado.append(grupo)

    return resultado


# ---------------------------------------------------------------------------
# CORREÇÃO 5: Classificação Semântica Refinada e Multi-Rótulo
# ---------------------------------------------------------------------------

VERBOS_PROCEDIMENTO_RE = re.compile(
    r"\b(pressionar|pressione|girar|gire| puxar|puxe|empurrar|empurre|inserir|insira|"
    r"introduzir|introduza|encaixar|encaixe|desencaixar|abrir|abra|fechar|feche|"
    r"soltar|solte|remover|remova|retirar|retire|instalar|instale|desinstalar|"
    r"ajustar|ajuste|selecionar|selecione|tocar|toque|confirmar|confirme|"
    r"rebater|acionar|acione|posicionar|posicione|desligar|ligar|aguardar|"
    r"verificar|verifique|substituir|substitua|limpar|estacionar|engatar)\b",
    re.IGNORECASE,
)

PADRAO_NOTA_RODAPE_ITEM = re.compile(
    r"^(dependendo da versão|válido para|em alguns países|inmetro:|abnt|ece:|norma\s)",
    re.IGNORECASE,
)


def _eh_lista_procedimental_real(bloco: Dict[str, Any]) -> bool:
    """
    Distingue uma lista de instruções passo a passo real de:
    - notas de rodapé numeradas pelo OCR (como em `vw_000052`: '1. Dependendo da versão...');
    - listas puramente descritivas de componentes ou tópicos.
    """
    if bloco.get("type") != "list":
        return False

    items = [str(it).strip() for it in (bloco.get("items") or []) if str(it).strip()]
    if not items:
        return False

    estilo = bloco.get("list_style", "unordered")
    lead = (bloco.get("lead_text") or "").strip().lower()

    # Uma lista ordenada de apenas 1 item que começa com "Dependendo da versão..." é nota de rodapé!
    if len(items) == 1 and PADRAO_NOTA_RODAPE_ITEM.search(items[0]):
        return False

    qtd_com_verbos = sum(1 for it in items if VERBOS_PROCEDIMENTO_RE.search(it))

    if estilo == "checklist":
        return len(items) >= 2

    if estilo == "ordered":
        return len(items) >= 2 and (qtd_com_verbos >= 1 or bool(VERBOS_PROCEDIMENTO_RE.search(lead)))

    # Lista não ordenada: exige pelo menos 2 itens com verbos de ação claros ou lead_text instrucional
    if len(items) >= 2 and (
        qtd_com_verbos >= max(2, math.ceil(len(items) * 0.5))
        or any(termo in lead for termo in ("proceder da seguinte forma", "executar os seguintes passos"))
    ):
        return True

    return False


def _inferir_tipo_estrutura(blocos: Sequence[Dict[str, Any]]) -> str:
    tipos = [b.get("type") for b in blocos if b.get("type") not in {"heading", "icon"}]
    if not tipos:
        return "paragraph"
    contagem = Counter(tipos)
    if len(contagem) == 1:
        return str(tipos[0])
    if "table" in contagem and len(blocos) <= 2:
        return "table"
    if "alert_box" in contagem and contagem["alert_box"] == len(tipos):
        return "alert_box"
    if "image" in contagem and len(tipos) == 1:
        return "image"
    return "mixed"


def _inferir_tipos_conhecimento(
    section_path: Sequence[str],
    blocos: Sequence[Dict[str, Any]],
    corpo_md: str,
) -> Tuple[str, List[str]]:
    """
    Retorna `(tipo_principal, tipos_secundarios)`.
    Corrige:
    - Separação de `index` para Índice e Índice remissivo (Correção 2).
    - Falsos positivos de `procedure` como `vw_000052` (Correção 5).
    - Detecção precisa de `troubleshooting`, `warning`, `operation`, `procedure` e `explanation`.
    """
    caminho_lower = " > ".join(section_path).lower()
    texto_lower = corpo_md.lower()
    blocos_conteudo = [b for b in blocos if b.get("type") not in {"heading", "icon"}]
    tipos_blocos = [b.get("type") for b in blocos_conteudo]

    # 1. Index: Sumário ("Índice") e "Índice remissivo"
    if _eh_secao_indice(section_path):
        return "index", ["reference"]

    candidatos: List[str] = []

    # 2. Warning: verifica presença de alert_box e se o chunk é majoritariamente alerta
    qtd_alertas = sum(1 for t in tipos_blocos if t == "alert_box")
    if tipos_blocos and qtd_alertas == len(tipos_blocos):
        candidatos.append("warning")
    elif qtd_alertas > 0:
        # Se mais de 65% dos tokens do corpo vêm de alert_box, warning é principal
        tokens_alertas = sum(
            CONTAR_TOKENS(formatar_bloco_markdown(b))
            for b in blocos_conteudo
            if b.get("type") == "alert_box"
        )
        if tokens_alertas >= 0.65 * max(1, CONTAR_TOKENS(corpo_md)):
            candidatos.append("warning")

    # 3. Reference: capa, garantia, sobre o manual, informações ao consumidor, abreviaturas
    if any(
        termo in caminho_lower
        for termo in (
            "sobre este manual",
            "certificado de garantia",
            "muito obrigado por sua confiança",
            "informações ao consumidor",
            "abreviaturas utilizadas",
            "declaração de conformidade",
            "licença de utilização",
        )
    ) or not section_path:
        candidatos.append("reference")

    # 4. Troubleshooting: títulos ou padrões de diagnóstico/falha/solução
    tem_padrao_troubleshooting = any(
        termo in caminho_lower
        for termo in (
            "soluções de problemas",
            "solução de problemas",
            "não prosseguir",
            "auxílio à partida",
            "puxar e rebocar",
        )
    ) or bool(
        re.search(
            r"(não funciona|está avariad[oa]|falha de funcionamento|se a luz de advertência não se apagar|"
            r"procurar uma concessionária volkswagen|causa.*solução|em caso de avaria)",
            texto_lower,
        )
        and any(t in texto_lower for t in ("luz de controle", "luz de advertência", "avaria", "defeito", "não pode ser"))
    )
    if tem_padrao_troubleshooting:
        candidatos.append("troubleshooting")

    # 5. Specification & Table
    if any(
        termo in caminho_lower
        for termo in ("dados técnicos", "dimensões", "capacidades", "motor 1.0", "informações do ar-condicionado")
    ):
        candidatos.append("specification")
    if "table" in tipos_blocos:
        candidatos.append("table")

    # 6. Identification: vistas gerais, significados de símbolos, diagramas com chamadas numeradas
    eh_vista_ou_simbolo = (
        "vista geral" in caminho_lower
        or "significados dos símbolos" in caminho_lower
        or "símbolos no revestimento" in caminho_lower
        or any(
            b.get("type") == "image" and len(b.get("legend_items") or []) >= 3
            for b in blocos_conteudo
        )
    )
    if eh_vista_ou_simbolo and not any(_eh_lista_procedimental_real(b) for b in blocos_conteudo):
        candidatos.append("identification")

    # 7. Procedure: exige lista procedimental real (evita classificar notas de rodapé como procedure)
    tem_procedimento_real = any(_eh_lista_procedimental_real(b) for b in blocos_conteudo)
    if tem_procedimento_real:
        candidatos.append("procedure")

    # 8. Maintenance: conservação, troca de óleo/fluidos/lâmpadas/rodas, revisões
    if any(
        termo in caminho_lower
        for termo in (
            "manutenção",
            "verificação e reabastecimento",
            "intervalo de serviço",
            "conservação do veículo",
            "óleo do motor",
            "líquido de arrefecimento",
            "fluido de freio",
            "bateria do veículo",
            "troca de roda",
            "palhetas dos limpadores",
            "fusíveis",
        )
    ):
        candidatos.append("maintenance")

    # 9. Operation: operação de controles, menus, assistência de condução, mídia, ar-condicionado
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
            "operação",
            "configurações",
        )
    ):
        candidatos.append("operation")

    # 10. Explanation: explicações conceituais, funcionamento e descrições de componentes
    candidatos.append("explanation")

    if qtd_alertas > 0 and "warning" not in candidatos:
        candidatos.append("warning")

    dedup = _deduplicar(candidatos)
    principal = dedup[0]
    secundarios = [t for t in dedup[1:] if t != principal]
    return principal, secundarios


def _inferir_dimensoes_funcionais(
    section_path: Sequence[str],
    blocos: Sequence[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], str]:
    """Extrai (sistema, componente, tema) a partir da hierarquia documental e blocos."""
    if not section_path:
        return None, None, "Capa e identificação do manual"

    cap_nivel1 = section_path[0]
    sistema = _slugify(cap_nivel1)

    componente: Optional[str] = None
    if len(section_path) >= 2:
        componente = _slugify(section_path[1])

    ultimo_titulo = _limpar_marcadores_visuais(section_path[-1])
    penultimo_titulo = _limpar_marcadores_visuais(section_path[-2]) if len(section_path) >= 2 else ""

    if "introdução ao tema" in ultimo_titulo.lower() and penultimo_titulo:
        tema_base = f"{penultimo_titulo} — Introdução ao tema"
    else:
        tema_base = ultimo_titulo or cap_nivel1

    primeiro = blocos[0] if blocos else {}
    if _eh_subtitulo_curto(primeiro):
        sub = _limpar_marcadores_visuais(primeiro.get("content") or "")
        if sub and sub.lower() != tema_base.lower() and len(sub) <= 65:
            tema_base = f"{tema_base} — {sub}"
    elif primeiro.get("type") == "image":
        cap_img = _limpar_marcadores_visuais(
            primeiro.get("caption") or primeiro.get("figure_label") or ""
        )
        if cap_img and len([b for b in blocos if b.get("type") != "heading"]) == 1:
            tema_base = cap_img
    elif all(b.get("type") == "alert_box" for b in blocos if b.get("type") != "heading"):
        niveis = _deduplicar(b.get("alert_level") for b in blocos if b.get("type") == "alert_box")
        rotulo_niveis = "/".join(niveis) if niveis else "Segurança"
        if "alerta" not in tema_base.lower():
            tema_base = f"{tema_base} — Alerta ({rotulo_niveis})"

    return sistema, componente, tema_base


# ---------------------------------------------------------------------------
# CORREÇÃO 4: Avaliação Rigorosa de Exceções de Tamanho
# ---------------------------------------------------------------------------

def _avaliar_excecao_tamanho(
    qtd_tokens: int,
    eh_indice: bool,
    section_path: Sequence[str],
    contagem_chunks_no_topico: int,
) -> Tuple[bool, Optional[str]]:
    """
    Identifica qualquer chunk fora dos limites absolutos (`< 80` ou `> 850` tokens)
    e registra o motivo legítimo da exceção.
    """
    if qtd_tokens > TOKENS_LIMITE_MAX:
        return True, "bloco_atomico_indivisivel_acima_do_limite"

    if qtd_tokens < TOKENS_MINIMO:
        if eh_indice:
            return True, "entrada_indice_compacta"
        if contagem_chunks_no_topico == 1:
            return True, "secao_curta_isolada_sem_adjacente_compativel"
        return True, "unidade_semantica_curta_preservada_por_limite_vizinho"

    return False, None


# ---------------------------------------------------------------------------
# CORREÇÃO 7: Construção do Texto Enriquecido para Embeddings e Recuperação
# ---------------------------------------------------------------------------

def _construir_texto_embedding(
    texto_chunk: str,
    sistema: Optional[str],
    componente: Optional[str],
    tema: str,
    tipo_conhecimento: str,
    tipos_secundarios: Sequence[str],
    section_path: Sequence[str],
    pagina_inicio: int,
    pagina_fim: int,
    chunks_relacionados: Sequence[str],
) -> str:
    """
    Gera uma representação enriquecida com metadados estruturais e rastreabilidade
    para ser enviada ao modelo de embedding na indexação vetorial.
    """
    hierarquia_str = " > ".join(section_path) if section_path else "Manual de Instruções Volkswagen Nivus"
    pags_str = f"p. {pagina_inicio}" if pagina_inicio == pagina_fim else f"pp. {pagina_inicio}-{pagina_fim}"
    tipos_str = ", ".join([tipo_conhecimento] + list(tipos_secundarios))
    rels_str = ", ".join(chunks_relacionados[:6]) if chunks_relacionados else "nenhum"

    cabecalho_recuperacao = (
        f"[Contexto: {hierarquia_str} | "
        f"Sistema: {sistema or 'geral'} | "
        f"Componente: {componente or 'geral'} | "
        f"Assunto: {tema} | "
        f"Tipo: {tipos_str} | "
        f"Páginas: {pags_str} | "
        f"Relacionados: {rels_str}]"
    )
    return f"{cabecalho_recuperacao}\n\n{texto_chunk}"


# ---------------------------------------------------------------------------
# Pipeline Principal de Geração, Refinamento e Validação dos Chunks
# ---------------------------------------------------------------------------

def gerar_chunks(
    diretorio_relativo: Path,
    caminho_ocr_jsonl: Path,
    saida_chunks: Path,
    saida_manifest: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Executa a chunkenização refinada de `manual_tecnico_volkswagen_ocr.jsonl`,
    separa índices das estatísticas técnicas, valida todas as regras e salva os artefatos.
    """
    registros: List[Dict[str, Any]] = []
    with caminho_ocr_jsonl.open("r", encoding="utf-8") as f:
        for linha in f:
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
    grupos_secao_brutos: List[Dict[str, Any]] = []
    headings_pendentes: List[Dict[str, Any]] = []

    for reg in registros:
        if reg.get("type") == "heading":
            if headings_pendentes and grupos_secao_brutos:
                ultimo_h = headings_pendentes[-1]
                if int(reg.get("level", 1)) <= int(ultimo_h.get("level", 1)):
                    if ultimo_h.get("content") not in (reg.get("section_path") or []):
                        grupos_secao_brutos[-1]["blocos"].append(ultimo_h)
                        headings_pendentes.pop()
            headings_pendentes.append(reg)
            continue

        sec_path = list(reg.get("section_path") or [])
        if not grupos_secao_brutos or grupos_secao_brutos[-1]["section_path"] != sec_path:
            grupos_secao_brutos.append({
                "section_path": sec_path,
                "blocos": [reg],
                "headings": headings_pendentes,
            })
            headings_pendentes = []
        else:
            if headings_pendentes:
                grupos_secao_brutos[-1]["headings"].extend(headings_pendentes)
                headings_pendentes = []
            grupos_secao_brutos[-1]["blocos"].append(reg)

    if headings_pendentes and grupos_secao_brutos:
        grupos_secao_brutos[-1]["headings"].extend(headings_pendentes)

    # 2. CORREÇÕES 1 e 2: Fundir subseções técnicas pequenas adjacentes e agrupar entradas de Índice
    grupos_secao = _fundir_grupos_secao_adjacentes(grupos_secao_brutos)

    # 3. Construir chunks por seção com divisão de blocos > 850 tokens (Correção 3)
    chunks_intermediarios: List[Dict[str, Any]] = []
    for grupo in grupos_secao:
        sec_path = grupo["section_path"]
        eh_indice = _eh_secao_indice(sec_path)
        tema_prov = sec_path[-1] if sec_path else "Manual de instruções"
        tokens_cab = CONTAR_TOKENS(montar_cabecalho_chunk(sec_path, tema_prov))

        unidades = _criar_micro_unidades(
            blocos_secao=grupo["blocos"],
            headings_associados=grupo["headings"],
            tokens_cabecalho_secao=tokens_cab,
        )
        chunks_secao = _empacotar_unidades_em_chunks(sec_path, unidades, eh_indice=eh_indice)
        chunks_intermediarios.extend(chunks_secao)

    # Contagem de chunks por tópico (para diagnóstico preciso de exceções de tamanho na Correção 4)
    contagem_por_topico = Counter(
        tuple(ch["section_path"][:2]) for ch in chunks_intermediarios
    )

    # 4. Formatar registros finais e construir índice reverso blk_id -> chunk_id
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
        eh_indice = bool(ch.get("eh_indice", False))

        sistema, componente, tema = _inferir_dimensoes_funcionais(sec_path, blocos)
        cabecalho_md = montar_cabecalho_chunk(sec_path, tema)
        corpo_md = ch["corpo_markdown"].strip()
        texto_final = f"{cabecalho_md}\n\n{corpo_md}".strip() if cabecalho_md else corpo_md

        qtd_tokens = CONTAR_TOKENS(texto_final)
        tipo_estrutura = _inferir_tipo_estrutura(blocos)
        tipo_conhecimento, tipos_secundarios = _inferir_tipos_conhecimento(
            sec_path, blocos, corpo_md
        )

        paginas_ini = [int(b["page_start"]) for b in blocos if b.get("page_start") is not None]
        paginas_fim = [int(b["page_end"]) for b in blocos if b.get("page_end") is not None]
        pagina_inicio = min(paginas_ini) if paginas_ini else 1
        pagina_fim = max(paginas_fim) if paginas_fim else pagina_inicio

        elementos_origem = _deduplicar(
            b.get("id") for b in todos_blocos_rastreio
            if b.get("id") and not str(b.get("id")).startswith(("synthetic_", "sub_"))
        )
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

        excecao_tamanho, motivo_excecao = _avaliar_excecao_tamanho(
            qtd_tokens=qtd_tokens,
            eh_indice=eh_indice,
            section_path=sec_path,
            contagem_chunks_no_topico=contagem_por_topico[tuple(sec_path[:2])],
        )

        chunks_finais.append({
            "chunk_id": chunk_id,
            "text": texto_final,
            "embedding_text": "",  # Preenchido após resolver chunks_relacionados
            "metadata": {
                "colecao": "indices_navegacao" if eh_indice else "conteudo_tecnico",
                "tipo_conhecimento": tipo_conhecimento,
                "tipos_conhecimento_secundarios": tipos_secundarios,
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
                "motivo_overlap": ch.get("motivo_overlap"),
                "excecao_tamanho": excecao_tamanho,
                "motivo_excecao": motivo_excecao,
            },
            "_split_groups": ch.get("split_groups") or [],
        })

    # 5. Preencher `chunks_relacionados` bidirecionalmente e `embedding_text` (Correção 7)
    mapa_chunks = {c["chunk_id"]: c for c in chunks_finais}
    for c in chunks_finais:
        cid = c["chunk_id"]
        relacionados: List[str] = []

        for gid in c.get("_split_groups") or []:
            relacionados.extend(grupo_split_para_chunks.get(str(gid), []))

        for blk_ref in refs_cruzadas_por_chunk.get(cid, []):
            relacionados.extend(blk_para_chunks.get(str(blk_ref), []))

        c["metadata"]["chunks_relacionados"] = [
            r for r in _deduplicar(relacionados) if r != cid
        ]

    for c in chunks_finais:
        cid = c["chunk_id"]
        for rel_id in list(c["metadata"]["chunks_relacionados"]):
            alvo = mapa_chunks.get(rel_id)
            if alvo and cid not in alvo["metadata"]["chunks_relacionados"]:
                alvo["metadata"]["chunks_relacionados"].append(cid)

    for c in chunks_finais:
        c.pop("_split_groups", None)
        c["metadata"]["chunks_relacionados"].sort()
        meta = c["metadata"]
        c["embedding_text"] = _construir_texto_embedding(
            texto_chunk=c["text"],
            sistema=meta["sistema"],
            componente=meta["componente"],
            tema=meta["tema"],
            tipo_conhecimento=meta["tipo_conhecimento"],
            tipos_secundarios=meta["tipos_conhecimento_secundarios"],
            section_path=meta["contexto_hierarquico"],
            pagina_inicio=meta["pagina_inicio"],
            pagina_fim=meta["pagina_fim"],
            chunks_relacionados=meta["chunks_relacionados"],
        )

    # 6. Executar validações de integridade e qualidade
    relatorio_validacao = validar_corpus_chunks(chunks_finais, registros)

    # 7. Gravar arquivos de saída (unificado + coleções separadas para conteúdo técnico e índices)
    saida_chunks.parent.mkdir(parents=True, exist_ok=True)
    caminho_tecnicos = saida_chunks.with_name("chunks_tecnicos.jsonl")
    caminho_indices = saida_chunks.with_name("chunks_indices.jsonl")

    chunks_tecnicos = [c for c in chunks_finais if c["metadata"]["colecao"] == "conteudo_tecnico"]
    chunks_indices = [c for c in chunks_finais if c["metadata"]["colecao"] == "indices_navegacao"]

    with saida_chunks.open("w", encoding="utf-8") as f_out:
        for c in chunks_finais:
            f_out.write(json.dumps(c, ensure_ascii=False) + "\n")

    with caminho_tecnicos.open("w", encoding="utf-8") as f_tec:
        for c in chunks_tecnicos:
            f_tec.write(json.dumps(c, ensure_ascii=False) + "\n")

    with caminho_indices.open("w", encoding="utf-8") as f_idx:
        for c in chunks_indices:
            f_idx.write(json.dumps(c, ensure_ascii=False) + "\n")

    # 8. Gerar e gravar `manifest.json` com métricas separadas para Conteúdo Técnico vs. Índices
    def _resumo_estatistico(lista_c: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        toks = [c["metadata"]["tokens"] for c in lista_c]
        total_c = len(lista_c)
        na_faixa = sum(1 for t in toks if TOKENS_ALVO_MIN <= t <= TOKENS_ALVO_MAX)
        abaixo_80 = sum(1 for t in toks if t < TOKENS_MINIMO)
        acima_850 = sum(1 for t in toks if t > TOKENS_LIMITE_MAX)
        return {
            "total_chunks": total_c,
            "tokens_total": sum(toks),
            "tokens_media": round(sum(toks) / max(1, total_c), 2),
            "tokens_min": min(toks) if toks else 0,
            "tokens_max": max(toks) if toks else 0,
            "chunks_na_faixa_350_650": na_faixa,
            "percentual_na_faixa_350_650": round((na_faixa / max(1, total_c)) * 100, 2),
            "chunks_abaixo_80_tokens": abaixo_80,
            "chunks_acima_850_tokens": acima_850,
            "chunks_com_overlap": sum(1 for c in lista_c if c["metadata"]["overlap"]),
            "chunks_excecao_tamanho": sum(1 for c in lista_c if c["metadata"]["excecao_tamanho"]),
        }

    stats_global = _resumo_estatistico(chunks_finais)
    stats_tecnicos = _resumo_estatistico(chunks_tecnicos)
    stats_indices = _resumo_estatistico(chunks_indices)

    dist_conhecimento = Counter(c["metadata"]["tipo_conhecimento"] for c in chunks_finais)
    dist_estrutura = Counter(c["metadata"]["tipo_estrutura"] for c in chunks_finais)
    dist_sistema = Counter(str(c["metadata"]["sistema"]) for c in chunks_finais)
    dist_motivos_excecao = Counter(
        str(c["metadata"]["motivo_excecao"])
        for c in chunks_finais
        if c["metadata"].get("excecao_tamanho")
    )

    manifest = {
        "projeto": "Manual Técnico Volkswagen — Nivus",
        "data_geracao": datetime.now(timezone.utc).isoformat(),
        "arquivo_entrada": str(caminho_ocr_jsonl.relative_to(diretorio_relativo.parent)),
        "arquivo_saida": str(saida_chunks.relative_to(diretorio_relativo.parent)),
        "arquivos_colecoes": {
            "conteudo_tecnico": str(caminho_tecnicos.relative_to(diretorio_relativo.parent)),
            "indices_navegacao": str(caminho_indices.relative_to(diretorio_relativo.parent)),
        },
        "parametros": {
            "tokenizador": NOME_TOKENIZADOR,
            "tokens_alvo_min": TOKENS_ALVO_MIN,
            "tokens_alvo_max": TOKENS_ALVO_MAX,
            "tokens_limite_max": TOKENS_LIMITE_MAX,
            "tokens_minimo": TOKENS_MINIMO,
            "estrategia": "semantic_hierarchical_units_v2_refined",
        },
        "estatisticas": {
            "total_registros_entrada": len(registros),
            "total_chunks_gerados": stats_global["total_chunks"],
            "tokens_total": stats_global["tokens_total"],
            "tokens_media": stats_global["tokens_media"],
            "tokens_min": stats_global["tokens_min"],
            "tokens_max": stats_global["tokens_max"],
            "chunks_na_faixa_350_650": stats_global["chunks_na_faixa_350_650"],
            "percentual_na_faixa_350_650": stats_global["percentual_na_faixa_350_650"],
            "chunks_com_overlap": stats_global["chunks_com_overlap"],
            "chunks_excecao_tamanho": stats_global["chunks_excecao_tamanho"],
            "motivos_excecao_tamanho": dict(dist_motivos_excecao),
            "estatisticas_conteudo_tecnico": stats_tecnicos,
            "estatisticas_indices": stats_indices,
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

    print("Chunkenização refinada concluída com sucesso:")
    print(f" - Total de blocos de entrada:          {len(registros)}")
    print(f" - Total de chunks gerados (global):    {stats_global['total_chunks']}")
    print(f" - Chunks técnicos (sem índices):       {stats_tecnicos['total_chunks']}")
    print(
        f" - Faixa 350–650 (técnicos):            "
        f"{stats_tecnicos['chunks_na_faixa_350_650']}/{stats_tecnicos['total_chunks']} "
        f"({stats_tecnicos['percentual_na_faixa_350_650']}%)"
    )
    print(f" - Chunks > 850 tokens:                 {stats_global['chunks_acima_850_tokens']}")
    print(f" - Chunks com overlap seletivo:         {stats_global['chunks_com_overlap']}")
    print(
        f" - Validação (10 regras):               "
        f"{'APROVADO' if relatorio_validacao['aprovado'] else 'FALHOU'}"
    )

    if not relatorio_validacao["aprovado"]:
        regras_falhas = [k for k, v in relatorio_validacao["regras"].items() if not v]
        print(f"   [!] Regras que falharam: {regras_falhas}")

    return chunks_finais, manifest


def validar_corpus_chunks(
    chunks: Sequence[Dict[str, Any]],
    registros_entrada: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Executa as 10 verificações de integridade exigidas com validação rigorosa de exceções."""
    ids_chunks = [c.get("chunk_id") for c in chunks]
    regra_1_ids_unicos = len(ids_chunks) == len(set(ids_chunks)) and all(ids_chunks)

    regra_2_sem_vazio = all(
        bool((c.get("text") or "").strip()) and bool((c.get("embedding_text") or "").strip())
        for c in chunks
    )

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

    regra_6_tabelas_validas = True
    for c in chunks:
        if c["metadata"].get("tipo_estrutura") == "table" or "| --- |" in c["text"]:
            linhas_tab = [
                ln.strip() for ln in c["text"].splitlines() if ln.strip().startswith("|")
            ]
            if len(linhas_tab) < 2 or "---" not in linhas_tab[1]:
                regra_6_tabelas_validas = False
                break

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

    ids_conteudo_entrada = {
        r["id"] for r in registros_entrada if r.get("type") != "heading"
    }
    contagem_uso_blk: Counter = Counter()
    blocos_com_split_intencional: set[str] = set()
    for c in chunks:
        for bid in c["metadata"].get("elementos_origem") or []:
            contagem_uso_blk[bid] += 1
            if c["metadata"].get("overlap") or c["metadata"].get("motivo_overlap"):
                blocos_com_split_intencional.add(bid)

    todos_cobertos = ids_conteudo_entrada.issubset(set(contagem_uso_blk.keys()))
    duplicados_indevidos = [
        bid
        for bid, qtd in contagem_uso_blk.items()
        if qtd > 1 and bid not in blocos_com_split_intencional
    ]
    regra_8_sem_duplicacao_indevida = todos_cobertos and len(duplicados_indevidos) == 0

    # CORREÇÃO 4: Regra 9 verifica se TODO chunk < 80 ou > 850 está marcado como exceção
    # E possui um motivo_excecao legítimo (não sendo um fragmento acidental)
    regra_9_excecoes_tamanho = all(
        (
            c["metadata"].get("excecao_tamanho")
            == (
                c["metadata"].get("tokens", 0) > TOKENS_LIMITE_MAX
                or c["metadata"].get("tokens", 0) < TOKENS_MINIMO
            )
        )
        and (
            not c["metadata"].get("excecao_tamanho")
            or (
                bool(c["metadata"].get("motivo_excecao"))
                and c["metadata"].get("motivo_excecao") != "fragmento_abaixo_do_minimo_revisar"
            )
        )
        for c in chunks
    )

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
        diretorio_relativo=raiz["chunks"]["path"],
        caminho_ocr_jsonl=raiz["chunks"]["02_extracted"]["ocr"],
        saida_chunks=raiz["chunks"]["03_chunks"]["chunks"],
        saida_manifest=raiz["chunks"]["03_chunks"]["manifest"],
    )