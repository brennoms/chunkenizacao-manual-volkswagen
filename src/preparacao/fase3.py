"""Reconstrução semântica, ordem de leitura, alertas, tabelas e imagens (Fase 3)."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf as fitz


LIMITE_COLUNA_X = 210.0
LARGURA_ZONA_INTEIRA = 215.0
TOLERANCIA_GEOMETRICA = 2.0
TOLERANCIA_LINHA_Y = 3.5

NIVEIS_ALERTA = ("PERIGO", "ATENÇÃO", "CUIDADO", "NOTA", "AMBIENTAL", "INFO")
REGEX_CABECALHO_ALERTA = re.compile(
    r"^\s*(?:[⚠️\s]*)(PERIGO|ATENÇÃO|CUIDADO|NOTA|AMBIENTAL|INFO)\b[:\s\-–—]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
REGEX_ITEM_LISTA = re.compile(
    r"^\s*(?:([•●▪◦‣⁃✓✔–—-])|(\d+[.)]))\s+(.+)$",
    re.DOTALL,
)
REGEX_FIGURA = re.compile(r"\bFig\.?\s*(\d+)\b", re.IGNORECASE)
REGEX_LEGENDA_FIGURA = re.compile(
    r"^(?:Legenda|Explicaç(?:ões|ão)\s+sobre\s+os\s+instrumentos)\s*(?:para\s*)?→?\s*Fig\.?\s*(\d+)",
    re.IGNORECASE,
)
REGEX_PAGINA = re.compile(r"(?:página|pág\.?)\s*(\d+)", re.IGNORECASE)
REGEX_CHAMADA_ALERTA = re.compile(
    r"(?:observe|ver|consulte|veja|atentar|orientações\s+de\s+segurança)",
    re.IGNORECASE,
)

CORES_DEBUG_FASE3: Dict[str, Tuple[float, float, float]] = {
    "heading": (0.05, 0.25, 0.90),
    "paragraph": (0.10, 0.55, 0.20),
    "list": (0.55, 0.20, 0.75),
    "alert_box": (0.90, 0.10, 0.10),
    "table": (0.95, 0.50, 0.05),
    "image": (0.00, 0.55, 0.70),
    "icon": (0.30, 0.30, 0.30),
}


def _bbox(elemento: Dict[str, Any]) -> Tuple[float, float, float, float]:
    if "_bbox" in elemento:
        caixa = elemento["_bbox"]
        return float(caixa[0]), float(caixa[1]), float(caixa[2]), float(caixa[3])
    bbox = elemento.get("bbox") or {}
    x0 = float(bbox.get("x", 0.0))
    y0 = float(bbox.get("y", 0.0))
    return x0, y0, x0 + float(bbox.get("width", 0.0)), y0 + float(bbox.get("height", 0.0))


def _largura(elemento: Dict[str, Any]) -> float:
    x0, _, x1, _ = _bbox(elemento)
    return max(0.0, x1 - x0)


def _altura(elemento: Dict[str, Any]) -> float:
    _, y0, _, y1 = _bbox(elemento)
    return max(0.0, y1 - y0)


def _centro(elemento: Dict[str, Any]) -> Tuple[float, float]:
    x0, y0, x1, y1 = _bbox(elemento)
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def _unir_caixas(
    caixas: Sequence[Tuple[float, float, float, float]],
) -> Tuple[float, float, float, float]:
    if not caixas:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(c[0] for c in caixas),
        min(c[1] for c in caixas),
        max(c[2] for c in caixas),
        max(c[3] for c in caixas),
    )


def _area_intersecao(
    primeira: Tuple[float, float, float, float],
    segunda: Tuple[float, float, float, float],
) -> float:
    x0 = max(primeira[0], segunda[0])
    y0 = max(primeira[1], segunda[1])
    x1 = min(primeira[2], segunda[2])
    y1 = min(primeira[3], segunda[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _caixa_contem(
    externa: Tuple[float, float, float, float],
    interna: Tuple[float, float, float, float],
    margem: float = TOLERANCIA_GEOMETRICA,
) -> bool:
    return (
        interna[0] >= externa[0] - margem
        and interna[1] >= externa[1] - margem
        and interna[2] <= externa[2] + margem
        and interna[3] <= externa[3] + margem
    )


def _texto_limpo(elemento: Dict[str, Any]) -> str:
    bruto = str(elemento.get("content") or "")
    sem_hifen_quebra = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", bruto)
    return re.sub(r"\s+", " ", sem_hifen_quebra).strip()


def _deduplicar_ids(sequencia: Iterable[Optional[str]]) -> List[str]:
    return list(dict.fromkeys(str(item) for item in sequencia if item))


def _fontes_do_elemento(elemento: Dict[str, Any]) -> List[str]:
    if elemento.get("type") == "icon":
        fontes = list(elemento.get("source_element_ids") or [])
        if not fontes and elemento.get("id"):
            fontes = [str(elemento["id"])]
        return _deduplicar_ids(fontes)
    return [str(elemento["id"])] if elemento.get("id") else []


def _carregar_json_auxiliar(caminho: Optional[Path]) -> Dict[str, Any]:
    if caminho is None or not caminho.exists():
        return {}
    return json.loads(caminho.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pré-processamento: Divisão de elementos que cruzam colunas no Índice
# ---------------------------------------------------------------------------

def _desmembrar_textos_transversais_indice(
    textos: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    No Índice (ex.: pág. 7), o PDF ocasionalmente junta o número de página do
    final da coluna esquerda (x ~ 202) com o item da coluna direita (x ~ 229),
    ex.: '194 – Orientações sobre os dados técnicos 281'. Divide-os em dois.
    """
    resultado: List[Dict[str, Any]] = []
    padrao_duplo = re.compile(r"^(\d{1,3})\s+((?:[–—-]\s*.+|[A-ZÁÉÍÓÚÂÊÔÃÕÇ].+))$")

    for txt in textos:
        x0, y0, x1, y1 = _bbox(txt)
        conteudo = _texto_limpo(txt)
        if 180.0 <= x0 <= 212.0 and x1 >= 250.0:
            match = padrao_duplo.match(conteudo)
            if match:
                num_esq, texto_dir = match.group(1), match.group(2)
                esq = dict(txt)
                esq["id"] = f"{txt['id']}__L"
                esq["_source_id_real"] = txt["id"]
                esq["content"] = num_esq
                esq["bbox"] = {"x": x0, "y": y0, "width": 13.5, "height": y1 - y0}

                dir_el = dict(txt)
                dir_el["id"] = f"{txt['id']}__R"
                dir_el["_source_id_real"] = txt["id"]
                dir_el["content"] = texto_dir
                dir_el["bbox"] = {
                    "x": 229.6,
                    "y": y0,
                    "width": max(20.0, x1 - 229.6),
                    "height": y1 - y0,
                }
                resultado.extend([esq, dir_el])
                continue
        resultado.append(txt)
    return resultado


def _fontes_reais(elemento: Dict[str, Any]) -> List[str]:
    if "_source_id_real" in elemento:
        return [str(elemento["_source_id_real"])]
    return _fontes_do_elemento(elemento)


# ---------------------------------------------------------------------------
# Classificação tipográfica e hierarquia de títulos
# ---------------------------------------------------------------------------

def _eh_titulo_sumario_ou_chamada(texto: str) -> bool:
    if not texto:
        return True
    if REGEX_FIGURA.match(texto) or REGEX_LEGENDA_FIGURA.match(texto):
        return True
    if REGEX_CHAMADA_ALERTA.match(texto) and "página" in texto.lower():
        return True
    if re.match(r"^[–—•●▪]\s*", texto):
        return True
    if re.match(r"^[_.\s]{4,}", texto):
        return True
    return False


def _classificar_nivel_titulo(
    elemento: Dict[str, Any],
    shapes_pagina: Sequence[Dict[str, Any]],
    num_pagina: int,
) -> Optional[int]:
    if elemento.get("type") != "text":
        return None
    conteudo = _texto_limpo(elemento)
    if not conteudo or len(conteudo) > 120 or _eh_titulo_sumario_ou_chamada(conteudo):
        return None
    if REGEX_CABECALHO_ALERTA.match(conteudo):
        return None

    fonte = str(elemento.get("font") or "").lower()
    tamanho = float(elemento.get("font_size") or 0.0)
    cor = (elemento.get("color") or {}).get("components") or []
    eh_azul = len(cor) >= 3 and cor[2] >= 130 and cor[0] < 50 and cor[1] < 150

    # Nível 1: Títulos de capítulo (VWHead-Bold >= 13pt, exceto capa na pág. 1)
    if "vwhead-bold" in fonte and tamanho >= 13.0 and num_pagina > 1:
        return 1

    caixa_el = _bbox(elemento)
    em_faixa_destaque = any(
        sh.get("shape_type") == "rectangle"
        and sh.get("fill_color") is not None
        and _caixa_contem(_bbox(sh), caixa_el, margem=3.0)
        and _largura(sh) >= 140.0
        for sh in shapes_pagina
    )
    # Nível 2: Seções principais
    if ("vwhead-bold" in fonte and 10.5 <= tamanho < 13.0) or (
        em_faixa_destaque and "bold" in fonte and 9.0 <= tamanho < 13.0
    ):
        return 2

    # Nível 3: Subseções
    if "vwhead-bold" in fonte and 8.5 <= tamanho < 10.5:
        return 3

    # Nível 4: Tópico local em negrito isolado
    if (
        "vwtext-bold" in fonte
        and 7.3 <= tamanho <= 8.2
        and _altura(elemento) <= 14.0
        and not conteudo.endswith((".", ":", ";", ",", "/"))
        and not re.match(r"^\d+\s", conteudo)
        and len(conteudo) <= 65
    ):
        return 4

    return None


# ---------------------------------------------------------------------------
# Associação de ícones inline a linhas de texto
# ---------------------------------------------------------------------------

def _associar_icones_aos_textos(
    textos: Sequence[Dict[str, Any]],
    icones: Sequence[Dict[str, Any]],
    ids_icones_reservados: set[str],
) -> Tuple[Dict[str, Tuple[str, List[str], List[str]]], set[str]]:
    """
    Associa ícones na faixa vertical de uma linha de texto (inclusive números de
    chamadas de figuras na margem esquerda da coluna) diretamente na string.
    """
    icones_por_texto: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    icones_consumidos: set[str] = set()

    for icone in icones:
        id_icone = str(icone.get("id") or "")
        if not id_icone or id_icone in ids_icones_reservados:
            continue
        if not icone.get("icon_id"):
            continue
        ix0, iy0, ix1, iy1 = _bbox(icone)
        icx, icy = (ix0 + ix1) / 2.0, (iy0 + iy1) / 2.0
        largura_ic, altura_ic = ix1 - ix0, iy1 - iy0
        if largura_ic > 32.0 or altura_ic > 26.0:
            continue

        melhor_texto: Optional[Dict[str, Any]] = None
        menor_distancia = float("inf")

        for txt in textos:
            tx0, ty0, tx1, ty1 = _bbox(txt)
            # Verifica alinhamento vertical com o bloco de texto
            if not (ty0 - 4.5 <= icy <= ty1 + 4.5):
                continue
            # Mesma coluna (quando o texto não atravessa a página)
            if _largura(txt) <= LARGURA_ZONA_INTEIRA:
                if (icx < LIMITE_COLUNA_X) != (((tx0 + tx1) / 2.0) < LIMITE_COLUNA_X):
                    continue
            # Aceita ícone dentro do texto ou até 32pt à esquerda/direita (ex: círculos 1..8)
            if not (tx0 - 32.0 <= icx <= tx1 + 28.0):
                continue

            dist_x = 0.0 if tx0 <= icx <= tx1 else min(abs(icx - tx0), abs(icx - tx1))
            dist_y = abs(icy - (ty0 + ty1) / 2.0)
            dist = dist_x + dist_y * 1.5
            if dist < menor_distancia:
                menor_distancia = dist
                melhor_texto = txt

        if melhor_texto is not None:
            icones_por_texto[str(melhor_texto["id"])].append(icone)
            icones_consumidos.add(id_icone)

    resultado: Dict[str, Tuple[str, List[str], List[str]]] = {}
    for txt in textos:
        tid = str(txt["id"])
        conteudo = _texto_limpo(txt)
        associados = icones_por_texto.get(tid, [])
        if not associados:
            resultado[tid] = (conteudo, [], _fontes_reais(txt))
            continue

        tx0, _, tx1, _ = _bbox(txt)
        largura_txt = max(1.0, tx1 - tx0)
        insercoes: List[Tuple[int, str]] = []
        inline_ids: List[str] = []
        fontes = list(_fontes_reais(txt))

        for ic in sorted(associados, key=lambda item: (_bbox(item)[1], _bbox(item)[0])):
            icon_id = str(ic["icon_id"])
            ix0 = _bbox(ic)[0]
            proporcao = (ix0 - tx0) / largura_txt
            if proporcao <= 0.08:
                pos = 0
            elif proporcao >= 0.92:
                pos = len(conteudo)
            else:
                pos_aprox = int(round(proporcao * len(conteudo)))
                espaco_esq = conteudo.rfind(" ", 0, pos_aprox)
                espaco_dir = conteudo.find(" ", pos_aprox)
                candidatos_pos = [p for p in (espaco_esq, espaco_dir) if p != -1]
                pos = (
                    min(candidatos_pos, key=lambda p: abs(p - pos_aprox))
                    if candidatos_pos
                    else pos_aprox
                )
            insercoes.append((pos, f" <icon:{icon_id}> "))
            inline_ids.append(icon_id)
            fontes.extend(_fontes_reais(ic))

        texto_modificado = conteudo
        for pos, token in sorted(insercoes, key=lambda item: item[0], reverse=True):
            texto_modificado = texto_modificado[:pos] + token + texto_modificado[pos:]

        texto_modificado = re.sub(r"\s+", " ", texto_modificado).strip()
        resultado[tid] = (
            texto_modificado,
            _deduplicar_ids(inline_ids),
            _deduplicar_ids(fontes),
        )

    return resultado, icones_consumidos


# ---------------------------------------------------------------------------
# Detecção e reconstrução de Caixas de Alerta (alert_box)
# ---------------------------------------------------------------------------

def _eh_retangulo_cabecalho_alerta(shape: Dict[str, Any]) -> bool:
    if shape.get("type") != "shape" or shape.get("shape_type") != "rectangle":
        return False
    cor = (shape.get("fill_color") or {}).get("components") or []
    if len(cor) < 3:
        return False
    r, g, b = cor[:3]
    saturacao = max(r, g, b) - min(r, g, b)
    return (saturacao > 40 and max(r, g, b) > 120) or (
        215 <= r <= 245 and 220 <= g <= 248 and 225 <= b <= 250
    )


def _detectar_alertas_pagina(pagina: Dict[str, Any]) -> List[Dict[str, Any]]:
    elementos = pagina.get("elements", [])
    textos = [e for e in elementos if e.get("type") == "text"]
    icones = [e for e in elementos if e.get("type") == "icon"]
    shapes = [e for e in elementos if e.get("type") == "shape"]
    altura_pag = float(pagina.get("height", 595.28))

    candidatos_cabecalho: List[Tuple[Dict[str, Any], str, str, Optional[Dict[str, Any]]]] = []
    for txt in textos:
        conteudo = _texto_limpo(txt)
        match = REGEX_CABECALHO_ALERTA.match(conteudo)
        if not match:
            continue
        nivel = match.group(1).upper()
        resto_texto = match.group(2).strip()
        caixa_txt = _bbox(txt)

        retangulos = [
            sh for sh in shapes
            if _eh_retangulo_cabecalho_alerta(sh)
            and _caixa_contem(_bbox(sh), caixa_txt, margem=6.0)
        ]
        retangulos.sort(key=lambda sh: _area_intersecao(_bbox(sh), caixa_txt), reverse=True)
        banda = retangulos[0] if retangulos else None

        fonte = str(txt.get("font") or "").lower()
        # Exige que o cabeçalho de alerta seja negrito e que não seja uma frase comum como "Perigo de uma postura..."
        if "bold" not in fonte:
            continue
        if resto_texto and resto_texto[0].islower():
            continue

        candidatos_cabecalho.append((txt, nivel, resto_texto, banda))

    candidatos_cabecalho.sort(key=lambda item: (_bbox(item[0])[1], _bbox(item[0])[0]))
    alertas_detectados: List[Dict[str, Any]] = []

    for head, nivel, resto_head, banda in candidatos_cabecalho:
        hx0, hy0, hx1, hy1 = _bbox(head)
        eh_col_esquerda = ((hx0 + hx1) / 2.0) < LIMITE_COLUNA_X

        # Limites laterais completos da coluna (pois em NOTA a banda começa à direita do ícone)
        col_x0 = 18.0 if eh_col_esquerda else LIMITE_COLUNA_X + 2.0
        col_x1 = LIMITE_COLUNA_X - 2.0 if eh_col_esquerda else 405.0

        if banda is not None:
            bx0, by0, bx1, by1 = _bbox(banda)
            bx0 = min(bx0, col_x0 if eh_col_esquerda else bx0 - 25.0)
            bx1 = max(bx1, hx1 + 20.0)
        else:
            bx0, by0, bx1, by1 = col_x0, hy0 - 4.0, col_x1, hy1 + 4.0

        # Teto vertical máximo dado pelo próximo alerta ou título na mesma coluna
        y_max_coluna = altura_pag - 24.0
        for outro_txt in textos:
            if outro_txt["id"] == head["id"]:
                continue
            ox0, oy0, ox1, _ = _bbox(outro_txt)
            if oy0 <= by1 + 2.0:
                continue
            mesma_col = (((ox0 + ox1) / 2.0) < LIMITE_COLUNA_X) == eh_col_esquerda
            if not mesma_col and _largura(outro_txt) <= LARGURA_ZONA_INTEIRA:
                continue
            outro_limpo = _texto_limpo(outro_txt)
            eh_outro_alerta = bool(
                REGEX_CABECALHO_ALERTA.match(outro_limpo)
                and "bold" in str(outro_txt.get("font") or "").lower()
                and not (REGEX_CABECALHO_ALERTA.match(outro_limpo).group(2).strip()[:1].islower())
            )
            eh_titulo = _classificar_nivel_titulo(outro_txt, shapes, int(pagina["page"])) in (1, 2, 3)
            if eh_outro_alerta or eh_titulo:
                y_max_coluna = min(y_max_coluna, oy0 - 2.0)

        # Procura linha horizontal inferior de fechamento do quadro na mesma coluna
        linhas_fechamento: List[float] = []
        for sh in shapes:
            if sh.get("shape_type") != "line":
                continue
            lx0, ly0, lx1, ly1 = _bbox(sh)
            if abs(ly1 - ly0) <= 1.5 and 120.0 <= (lx1 - lx0) <= 200.0:
                mesma_col_linha = (((lx0 + lx1) / 2.0) < LIMITE_COLUNA_X) == eh_col_esquerda
                if mesma_col_linha and (by1 + 6.0 <= ly0 <= y_max_coluna + 5.0):
                    linhas_fechamento.append((ly0 + ly1) / 2.0)

        if linhas_fechamento:
            y_fechamento = min(linhas_fechamento)
        else:
            y_fechamento = min(y_max_coluna, by1 + 165.0)

        quadro = (min(bx0, col_x0), by0 - 2.0, max(bx1, col_x1), y_fechamento)

        membros_texto: List[Dict[str, Any]] = []
        for txt in textos:
            if txt["id"] == head["id"]:
                continue
            tb = _bbox(txt)
            tcx, tcy = _centro(txt)
            if quadro[0] - 6.0 <= tcx <= quadro[2] + 6.0 and hy0 - 2.0 <= tcy <= quadro[3] + 3.0:
                # Não absorve parágrafo de nota informativa com ícone 'i' fora do quadro
                membros_texto.append(txt)
        membros_texto.sort(key=lambda e: (round(_bbox(e)[1], 1), _bbox(e)[0]))

        # Ícone do cabeçalho (busca na faixa do cabeçalho, inclusive à esquerda da banda de NOTA)
        icones_cabecalho = [
            ic for ic in icones
            if (quadro[0] - 8.0 <= _centro(ic)[0] <= quadro[2] + 8.0)
            and (hy0 - 8.0 <= _centro(ic)[1] <= hy1 + 12.0)
        ]
        icones_cabecalho.sort(key=lambda ic: math.dist(_centro(ic), _centro(head)))
        icon_id_principal = (
            str(icones_cabecalho[0]["icon_id"])
            if icones_cabecalho and icones_cabecalho[0].get("icon_id")
            else None
        )

        icones_internos = [
            ic for ic in icones
            if (quadro[0] - 6.0 <= _centro(ic)[0] <= quadro[2] + 6.0)
            and (quadro[1] - 4.0 <= _centro(ic)[1] <= quadro[3] + 4.0)
        ]

        partes_lead: List[str] = [resto_head] if resto_head else []
        itens_alerta: List[str] = []
        for membro in membros_texto:
            texto_m = _texto_limpo(membro)
            if not texto_m:
                continue
            m_item = REGEX_ITEM_LISTA.match(texto_m)
            if m_item:
                itens_alerta.append(m_item.group(3).strip())
            elif itens_alerta:
                if itens_alerta[-1].endswith("-"):
                    itens_alerta[-1] = itens_alerta[-1][:-1] + texto_m
                else:
                    itens_alerta[-1] = f"{itens_alerta[-1]} {texto_m}"
            else:
                if partes_lead and partes_lead[-1].endswith("-"):
                    partes_lead[-1] = partes_lead[-1][:-1] + texto_m
                else:
                    partes_lead.append(texto_m)

        lead_text = re.sub(r"\s+", " ", " ".join(partes_lead)).strip()

        shapes_internos = [
            sh for sh in shapes if _caixa_contem(quadro, _bbox(sh), margem=4.0)
        ]
        fontes: List[str] = list(_fontes_do_elemento(head))
        if banda is not None:
            fontes.extend(_fontes_do_elemento(banda))
        for sh in shapes_internos:
            fontes.extend(_fontes_do_elemento(sh))
        for m in membros_texto:
            fontes.extend(_fontes_do_elemento(m))
        for ic in icones_internos:
            fontes.extend(_fontes_do_elemento(ic))

        inline_secundarios = _deduplicar_ids(
            ic.get("icon_id")
            for ic in icones_internos
            if ic.get("icon_id") and ic.get("icon_id") != icon_id_principal
        )

        todas_caixas = [_bbox(head)] + [_bbox(m) for m in membros_texto] + [_bbox(ic) for ic in icones_internos]
        if banda is not None:
            todas_caixas.append(_bbox(banda))

        alertas_detectados.append({
            "header_id": str(head["id"]),
            "member_ids": {str(m["id"]) for m in membros_texto} | {str(head["id"])},
            "icon_element_ids": {str(ic["id"]) for ic in icones_internos},
            "alert_level": nivel,
            "icon_id": icon_id_principal,
            "lead_text": lead_text,
            "items": itens_alerta,
            "inline_icons": inline_secundarios,
            "source_element_ids": _deduplicar_ids(fontes),
            "_bbox": _unir_caixas(todas_caixas),
        })

    return alertas_detectados


# ---------------------------------------------------------------------------
# Detecção e reconstrução de Tabelas (table)
# ---------------------------------------------------------------------------

def _agrupar_coordenadas(valores: Sequence[float], tolerancia: float = 2.5) -> List[float]:
    if not valores:
        return []
    ordenados = sorted(valores)
    grupos: List[List[float]] = [[ordenados[0]]]
    for valor in ordenados[1:]:
        if abs(valor - (sum(grupos[-1]) / len(grupos[-1]))) <= tolerancia:
            grupos[-1].append(valor)
        else:
            grupos.append([valor])
    return [sum(g) / len(g) for g in grupos]


def _detectar_tabelas_pagina(
    pagina: Dict[str, Any],
    caixas_alertas: Sequence[Tuple[float, float, float, float]],
    ids_textos_excluidos: set[str],
) -> List[Dict[str, Any]]:
    """
    Reconstrói tabelas estritamente a partir de grades de linhas (`shape_type == 'line'`),
    mantendo tabelas da coluna esquerda separadas das tabelas da coluna direita.
    """
    elementos = pagina.get("elements", [])
    shapes_linha = [
        e for e in elementos
        if e.get("type") == "shape"
        and e.get("shape_type") == "line"
        and not any(_caixa_contem(alerta_box, _bbox(e), margem=3.0) for alerta_box in caixas_alertas)
    ]

    linhas_h: List[Tuple[float, float, float, str]] = []
    linhas_v: List[Tuple[float, float, float, str]] = []

    for sh in shapes_linha:
        sx0, sy0, sx1, sy1 = _bbox(sh)
        if abs(sy1 - sy0) <= 1.5 and (sx1 - sx0) >= 15.0:
            linhas_h.append(((sy0 + sy1) / 2.0, sx0, sx1, str(sh["id"])))
        elif abs(sx1 - sx0) <= 1.5 and (sy1 - sy0) >= 9.0:
            linhas_v.append(((sx0 + sx1) / 2.0, sy0, sy1, str(sh["id"])))

    if len(linhas_h) < 3 or len(linhas_v) < 2:
        return []

    # 1. Agrupa linhas horizontais por Y, mas só une segmentos que se tocam (tolerância 4pt)
    # Isso impede unir uma tabela na coluna esquerda com outra na coluna direita!
    linhas_h.sort(key=lambda item: (item[0], item[1]))
    grupos_por_y: List[List[Tuple[float, float, float, str]]] = []
    for seg in linhas_h:
        if not grupos_por_y or abs(seg[0] - (sum(x[0] for x in grupos_por_y[-1]) / len(grupos_por_y[-1]))) > 2.2:
            grupos_por_y.append([seg])
        else:
            grupos_por_y[-1].append(seg)

    faixas_horizontais: List[Dict[str, Any]] = []
    for grupo_y in grupos_por_y:
        y_medio = sum(x[0] for x in grupo_y) / len(grupo_y)
        segs_ordenados = sorted(grupo_y, key=lambda x: x[1])
        mesclados: List[Dict[str, Any]] = []
        for _, x0, x1, sid in segs_ordenados:
            if mesclados and x0 <= mesclados[-1]["x1"] + 4.0:
                mesclados[-1]["x1"] = max(mesclados[-1]["x1"], x1)
                mesclados[-1]["ids"].append(sid)
            else:
                mesclados.append({"y": y_medio, "x0": x0, "x1": x1, "ids": [sid]})
        for m in mesclados:
            if (m["x1"] - m["x0"]) >= 65.0:
                faixas_horizontais.append(m)

    if len(faixas_horizontais) < 3:
        return []

    # 2. Agrupa faixas horizontais que pertencem à mesma tabela (mesmo intervalo X e conectadas por linhas V)
    grupos_grade: List[List[Dict[str, Any]]] = []
    for faixa in sorted(faixas_horizontais, key=lambda f: (f["y"], f["x0"])):
        melhor_grupo: Optional[List[Dict[str, Any]]] = None
        for grupo in grupos_grade:
            ultima = grupo[-1]
            # Devem ter quase os mesmos limites X0 e X1
            if abs(faixa["x0"] - ultima["x0"]) > 15.0 or abs(faixa["x1"] - ultima["x1"]) > 15.0:
                continue
            if (faixa["y"] - ultima["y"]) > 85.0:
                continue
            tem_conexao_v = any(
                min(faixa["x0"], ultima["x0"]) - 3.0 <= vx <= max(faixa["x1"], ultima["x1"]) + 3.0
                and vy0 <= faixa["y"] + 3.0
                and vy1 >= ultima["y"] - 3.0
                for vx, vy0, vy1, _ in linhas_v
            )
            if tem_conexao_v:
                melhor_grupo = grupo
                break
        if melhor_grupo is not None:
            melhor_grupo.append(faixa)
        else:
            grupos_grade.append([faixa])

    tabelas_reconstruidas: List[Dict[str, Any]] = []
    for grupo in grupos_grade:
        ys_distintos = _agrupar_coordenadas([f["y"] for f in grupo], tolerancia=2.5)
        if len(ys_distintos) < 3:
            continue
        gx0 = min(f["x0"] for f in grupo)
        gx1 = max(f["x1"] for f in grupo)
        gy0, gy1 = min(ys_distintos), max(ys_distintos)
        if (gx1 - gx0) < 65.0 or (gy1 - gy0) < 24.0:
            continue

        v_internas = [
            (vx, sid)
            for vx, vy0, vy1, sid in linhas_v
            if gx0 - 3.0 <= vx <= gx1 + 3.0
            and max(vy0, gy0 - 2.0) < min(vy1, gy1 + 2.0)
        ]
        xs_cols = _agrupar_coordenadas([gx0, gx1] + [vx for vx, _ in v_internas], tolerancia=3.0)
        colunas = [(xa, xb) for xa, xb in zip(xs_cols, xs_cols[1:]) if (xb - xa) >= 14.0]
        linhas_tab = [(ya, yb) for ya, yb in zip(ys_distintos, ys_distintos[1:]) if (yb - ya) >= 6.0]
        # Exige pelo menos 2 colunas reais (portanto pelo menos 1 divisória vertical interna)
        if len(colunas) < 2 or len(linhas_tab) < 2:
            continue

        shape_ids = _deduplicar_ids(
            [sid for f in grupo for sid in f["ids"]] + [sid for _, sid in v_internas]
        )
        matriz_celulas: List[List[str]] = []
        inline_icons_tabela: List[str] = []
        elementos_consumidos_ids: set[str] = set()
        fontes_tabela: List[str] = list(shape_ids)

        for ya, yb in linhas_tab:
            linha_md: List[str] = []
            for xa, xb in colunas:
                itens_celula: List[Dict[str, Any]] = []
                for el in elementos:
                    if el.get("type") not in {"text", "icon"}:
                        continue
                    if str(el.get("id")) in ids_textos_excluidos or str(el.get("id")) in elementos_consumidos_ids:
                        continue
                    ecx, ecy = _centro(el)
                    if xa - 1.5 <= ecx <= xb + 1.5 and ya - 1.5 <= ecy <= yb + 1.5:
                        itens_celula.append(el)

                itens_celula.sort(key=lambda item: (round(_bbox(item)[1], 1), _bbox(item)[0]))
                partes_celula: List[str] = []
                for item in itens_celula:
                    elementos_consumidos_ids.add(str(item["id"]))
                    fontes_tabela.extend(_fontes_do_elemento(item))
                    if item["type"] == "text":
                        partes_celula.append(_texto_limpo(item))
                    elif item.get("icon_id"):
                        icon_id = str(item["icon_id"])
                        partes_celula.append(f"<icon:{icon_id}>")
                        inline_icons_tabela.append(icon_id)

                texto_celula = re.sub(r"\s+", " ", " ".join(p for p in partes_celula if p)).strip()
                texto_celula = texto_celula.replace("|", "\\|")
                linha_md.append(texto_celula)

            if any(celula for celula in linha_md):
                matriz_celulas.append(linha_md)

        if len(matriz_celulas) < 2:
            continue

        ncols = len(colunas)
        linhas_markdown = [
            "| " + " | ".join(matriz_celulas[0]) + " |",
            "| " + " | ".join(["---"] * ncols) + " |",
        ]
        for linha in matriz_celulas[1:]:
            linhas_markdown.append("| " + " | ".join(linha) + " |")

        tabelas_reconstruidas.append({
            "type": "table",
            "page_start": int(pagina["page"]),
            "page_end": int(pagina["page"]),
            "markdown": "\n".join(linhas_markdown),
            "inline_icons": _deduplicar_ids(inline_icons_tabela),
            "referenced_alerts": [],
            "source_element_ids": _deduplicar_ids(fontes_tabela),
            "_bbox": (gx0, gy0, gx1, gy1),
            "_consumed_ids": elementos_consumidos_ids,
        })

    return tabelas_reconstruidas


# ---------------------------------------------------------------------------
# Ordem de Leitura por Zonas e Colunas (Por Página)
# ---------------------------------------------------------------------------

def _ordenar_blocos_por_zonas(
    blocos: Sequence[Dict[str, Any]], largura_pagina: float
) -> List[Dict[str, Any]]:
    """
    Segmenta horizontalmente a página por elementos que atravessam as duas colunas
    (width > 215 pt e que começam na coluna esquerda e terminam na direita).
    Nos intervalos entre eles, percorre toda a Coluna Esquerda antes da Direita.
    """
    if not blocos:
        return []

    saida: List[Dict[str, Any]] = []
    visitados: set[int] = set()

    for bloco in blocos:
        if bloco.get("type") == "image" and _largura(bloco) >= largura_pagina * 0.90:
            saida.append(bloco)
            visitados.add(id(bloco))

    def _eh_ancora_largura_total(bloco: Dict[str, Any]) -> bool:
        x0, _, x1, _ = _bbox(bloco)
        return (x1 - x0) > LARGURA_ZONA_INTEIRA and x0 < 140.0 and x1 > 260.0

    ancoras_largas = [
        b for b in blocos
        if id(b) not in visitados and _eh_ancora_largura_total(b)
    ]
    ancoras_largas.sort(key=lambda b: (round(_bbox(b)[1], 1), _bbox(b)[0]))
    ids_ancoras = {id(b) for b in ancoras_largas}

    def _ordenar_intervalo_colunas(candidatos: Sequence[Dict[str, Any]]) -> None:
        coluna_esquerda = [b for b in candidatos if _centro(b)[0] < LIMITE_COLUNA_X]
        coluna_direita = [b for b in candidatos if _centro(b)[0] >= LIMITE_COLUNA_X]
        coluna_esquerda.sort(key=lambda b: (round(_bbox(b)[1], 1), _bbox(b)[0]))
        coluna_direita.sort(key=lambda b: (round(_bbox(b)[1], 1), _bbox(b)[0]))
        for item in coluna_esquerda + coluna_direita:
            saida.append(item)
            visitados.add(id(item))

    for ancora in ancoras_largas:
        if id(ancora) in visitados:
            continue
        topo_ancora = _bbox(ancora)[1]
        anteriores = [
            b for b in blocos
            if id(b) not in visitados
            and id(b) not in ids_ancoras
            and _centro(b)[1] < topo_ancora
        ]
        _ordenar_intervalo_colunas(anteriores)
        saida.append(ancora)
        visitados.add(id(ancora))

    restantes = [b for b in blocos if id(b) not in visitados]
    _ordenar_intervalo_colunas(restantes)
    return saida


# ---------------------------------------------------------------------------
# Processamento de Página e Unificação Pós-Página
# ---------------------------------------------------------------------------

def _obter_descricao_icone(icon_id: Optional[str], descr_icons: Dict[str, str]) -> str:
    if not icon_id:
        return ""
    if icon_id in descr_icons:
        return str(descr_icons[icon_id])
    match = re.search(r"(\d+)$", str(icon_id))
    if match:
        chave_num = str(int(match.group(1)))
        if chave_num in descr_icons:
            return str(descr_icons[chave_num])
    return ""


def reconstruir_pagina(
    pagina: Dict[str, Any],
    descr_images: Dict[str, str],
    descr_icons: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Converte os elementos da Fase 2 de uma página em blocos semânticos ordenados."""
    num_pagina = int(pagina["page"])
    largura_pagina = float(pagina.get("width", 419.53))
    elementos = pagina.get("elements", [])

    shapes = [e for e in elementos if e.get("type") == "shape"]
    textos_brutos = [e for e in elementos if e.get("type") == "text"]
    textos = _desmembrar_textos_transversais_indice(textos_brutos)
    icones = [e for e in elementos if e.get("type") == "icon"]
    imagens = [e for e in elementos if e.get("type") == "image"]

    # 1. Detecta Caixas de Alerta
    alertas = _detectar_alertas_pagina(pagina)
    ids_texto_em_alerta = {tid for a in alertas for tid in a["member_ids"]}
    ids_icone_em_alerta = {iid for a in alertas for iid in a["icon_element_ids"]}
    caixas_alertas = [a["_bbox"] for a in alertas]

    # 2. Detecta e reconstrói Tabelas
    tabelas = _detectar_tabelas_pagina(pagina, caixas_alertas, ids_texto_em_alerta)
    ids_em_tabela = {eid for t in tabelas for eid in t["_consumed_ids"]}

    # 3. Associa ícones inline aos textos remanescentes
    textos_livres = [
        t for t in textos
        if str(t["id"]) not in ids_texto_em_alerta
        and str(t.get("_source_id_real") or t["id"]) not in ids_texto_em_alerta
        and str(t["id"]) not in ids_em_tabela
        and str(t.get("_source_id_real") or t["id"]) not in ids_em_tabela
    ]
    icones_reservados = ids_icone_em_alerta | ids_em_tabela
    mapa_texto_icones, ids_icones_inline_usados = _associar_icones_aos_textos(
        textos_livres, icones, icones_reservados
    )

    blocos_pagina: List[Dict[str, Any]] = []

    for alerta in alertas:
        blocos_pagina.append({
            "type": "alert_box",
            "alert_level": alerta["alert_level"],
            "alert_scope": "local_topic",
            "icon_id": alerta["icon_id"],
            "refers_to_ids": [],
            "page_start": num_pagina,
            "page_end": num_pagina,
            "lead_text": alerta["lead_text"],
            "items": alerta["items"],
            "inline_icons": alerta["inline_icons"],
            "referenced_alerts": [],
            "source_element_ids": alerta["source_element_ids"],
            "_bbox": alerta["_bbox"],
        })

    for tabela in tabelas:
        tabela.pop("_consumed_ids", None)
        blocos_pagina.append(tabela)

    for txt in textos_livres:
        tid = str(txt["id"])
        conteudo, inline_icons, fontes = mapa_texto_icones.get(
            tid, (_texto_limpo(txt), [], _fontes_reais(txt))
        )
        if not conteudo:
            continue

        nivel = _classificar_nivel_titulo(txt, shapes, num_pagina)
        if nivel is not None:
            blocos_pagina.append({
                "type": "heading",
                "level": nivel,
                "page_start": num_pagina,
                "page_end": num_pagina,
                "content": conteudo,
                "inline_icons": inline_icons,
                "referenced_alerts": [],
                "source_element_ids": fontes,
                "_bbox": _bbox(txt),
            })
        else:
            blocos_pagina.append({
                "type": "paragraph",
                "page_start": num_pagina,
                "page_end": num_pagina,
                "content": conteudo,
                "inline_icons": inline_icons,
                "referenced_alerts": [],
                "source_element_ids": fontes,
                "_bbox": _bbox(txt),
            })

    for img in imagens:
        asset_id = str(img.get("asset_id") or "")
        match_num = re.search(r"(\d+)$", asset_id)
        id_image = int(match_num.group(1)) if match_num else None
        blocos_pagina.append({
            "type": "image",
            "id_image": id_image,
            "page_start": num_pagina,
            "page_end": num_pagina,
            "asset_id": asset_id or None,
            "image_path": img.get("path"),
            "figure_label": None,
            "caption": None,
            "description": descr_images.get(str(id_image), "") if id_image is not None else "",
            "legend_items": [],
            "inline_icons": [],
            "referenced_alerts": [],
            "source_element_ids": _fontes_do_elemento(img),
            "_bbox": _bbox(img),
        })

    for ic in icones:
        iid = str(ic.get("id") or "")
        if iid in icones_reservados or iid in ids_icones_inline_usados:
            continue
        icon_id = ic.get("icon_id")
        blocos_pagina.append({
            "type": "icon",
            "icon_id": icon_id,
            "icon_order_id": ic.get("icon_order_id"),
            "page_start": num_pagina,
            "page_end": num_pagina,
            "image_path": f"assets/icons/{icon_id}.png" if icon_id else None,
            "description": _obter_descricao_icone(icon_id, descr_icons),
            "inline_icons": [],
            "referenced_alerts": [],
            "source_element_ids": _fontes_do_elemento(ic),
            "_bbox": _bbox(ic),
        })

    return _ordenar_blocos_por_zonas(blocos_pagina, largura_pagina)


def _unificar_linhas_indice_e_legendas(blocos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Une pares de parágrafos na mesma linha horizontal e coluna (ex: item de índice
    ou legenda de figura à esquerda e o número da página alinhado à direita).
    """
    saida: List[Dict[str, Any]] = []
    for bloco in blocos:
        if (
            saida
            and bloco["type"] == "paragraph"
            and saida[-1]["type"] == "paragraph"
            and bloco["page_start"] == saida[-1]["page_end"]
        ):
            anterior = saida[-1]
            ax0, ay0, ax1, ay1 = _bbox(anterior)
            bx0, by0, bx1, by1 = _bbox(bloco)
            mesma_coluna = ((ax0 + ax1) / 2.0 < LIMITE_COLUNA_X) == (
                (bx0 + bx1) / 2.0 < LIMITE_COLUNA_X
            )
            mesma_linha = abs(ay0 - by0) <= TOLERANCIA_LINHA_Y or (
                re.fullmatch(r"[\d,\s]+", bloco["content"])
                and ay0 - TOLERANCIA_LINHA_Y <= (by0 + by1) / 2.0 <= ay1 + TOLERANCIA_LINHA_Y
            )
            if mesma_coluna and mesma_linha and bx0 >= ax0:
                anterior["content"] = f"{anterior['content']} {bloco['content']}".strip()
                anterior["inline_icons"] = _deduplicar_ids(
                    anterior["inline_icons"] + bloco["inline_icons"]
                )
                anterior["source_element_ids"] = _deduplicar_ids(
                    anterior["source_element_ids"] + bloco["source_element_ids"]
                )
                anterior["_bbox"] = _unir_caixas([_bbox(anterior), _bbox(bloco)])
                continue
        saida.append(bloco)
    return saida


def _unificar_tabelas_continuadas(blocos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Une tabelas consecutivas (mesma página na coluna seguinte ou página seguinte) com mesmo cabeçalho."""
    saida: List[Dict[str, Any]] = []
    for bloco in blocos:
        if (
            saida
            and bloco["type"] == "table"
            and saida[-1]["type"] == "table"
            and bloco["page_start"] in (saida[-1]["page_end"], saida[-1]["page_end"] + 1)
        ):
            anterior = saida[-1]
            cabecalho_ant = (anterior["markdown"].splitlines() or [""])[0].strip()
            cabecalho_atu = (bloco["markdown"].splitlines() or [""])[0].strip()
            if cabecalho_ant and cabecalho_ant == cabecalho_atu:
                linhas_ant = anterior["markdown"].splitlines()
                linhas_atu = bloco["markdown"].splitlines()
                anterior["markdown"] = "\n".join(linhas_ant + linhas_atu[2:])
                anterior["page_end"] = bloco["page_end"]
                anterior["inline_icons"] = _deduplicar_ids(
                    anterior["inline_icons"] + bloco["inline_icons"]
                )
                anterior["source_element_ids"] = _deduplicar_ids(
                    anterior["source_element_ids"] + bloco["source_element_ids"]
                )
                continue
        saida.append(bloco)
    return saida


def _costurar_paragrafos_continuados(
    blocos: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Costura blocos de texto consecutivos quando o primeiro termina com palavra
    hifenizada ou sem pontuação final (., !, ?, :) e o seguinte é a continuação
    da mesma sentença (inclusive dentro da mesma coluna após linhas com ícones).
    """
    saida: List[Dict[str, Any]] = []
    for bloco in blocos:
        if (
            saida
            and saida[-1]["type"] == "paragraph"
            and bloco["type"] == "paragraph"
            and bloco["page_start"] in (saida[-1]["page_end"], saida[-1]["page_end"] + 1)
        ):
            anterior = saida[-1]
            texto_a = anterior["content"].rstrip()
            texto_b = bloco["content"].lstrip()
            texto_a_sem_icone = re.sub(r"<icon:icon_\d+>\s*$", "", texto_a).rstrip()

            nao_eh_novo_item = not REGEX_ITEM_LISTA.match(texto_b)
            termina_hifen = texto_a_sem_icone.endswith("-")
            sem_pontuacao_final = bool(texto_a_sem_icone) and not texto_a_sem_icone.endswith(
                (".", "!", "?", ":", ";")
            )
            comeca_continuidade = bool(texto_b) and (
                texto_b[0].islower()
                or texto_b.startswith(("→", "/", "(", "<icon:"))
            )

            # Evita fundir linhas independentes do Índice
            eh_linha_indice = bool(re.search(r"\s\d{1,3}$", texto_a_sem_icone))

            if nao_eh_novo_item and not eh_linha_indice and (
                termina_hifen or (sem_pontuacao_final and comeca_continuidade)
            ):
                if termina_hifen and texto_a.endswith("-"):
                    anterior["content"] = texto_a[:-1] + texto_b
                else:
                    anterior["content"] = f"{texto_a} {texto_b}"
                anterior["page_end"] = bloco["page_end"]
                anterior["inline_icons"] = _deduplicar_ids(
                    anterior["inline_icons"] + bloco["inline_icons"]
                )
                anterior["source_element_ids"] = _deduplicar_ids(
                    anterior["source_element_ids"] + bloco["source_element_ids"]
                )
                anterior["_bbox"] = _unir_caixas([_bbox(anterior), _bbox(bloco)])
                continue
        saida.append(bloco)
    return saida


def _agrupar_listas(blocos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converte parágrafos consecutivos iniciados por marcadores em elementos `list`."""
    saida: List[Dict[str, Any]] = []
    indice = 0
    while indice < len(blocos):
        bloco = blocos[indice]
        if bloco["type"] != "paragraph":
            saida.append(bloco)
            indice += 1
            continue

        match_inicial = REGEX_ITEM_LISTA.match(bloco.get("content", ""))
        if not match_inicial:
            saida.append(bloco)
            indice += 1
            continue

        marcador_simbolo, marcador_num, _ = match_inicial.groups()
        if marcador_num:
            estilo = "ordered"
        elif marcador_simbolo in {"✓", "✔"}:
            estilo = "checklist"
        else:
            estilo = "unordered"

        lead_text: Optional[str] = None
        fontes_lista: List[str] = []
        inline_lista: List[str] = []
        page_start = bloco["page_start"]
        bbox_inicial = _bbox(bloco)

        if (
            saida
            and saida[-1]["type"] == "paragraph"
            and saida[-1]["page_end"] == bloco["page_start"]
            and saida[-1]["content"].endswith(":")
            and not REGEX_LEGENDA_FIGURA.match(saida[-1]["content"])
        ):
            lead_bloco = saida.pop()
            lead_text = lead_bloco["content"]
            fontes_lista.extend(lead_bloco["source_element_ids"])
            inline_lista.extend(lead_bloco["inline_icons"])
            page_start = lead_bloco["page_start"]
            bbox_inicial = _bbox(lead_bloco)

        itens: List[str] = []
        page_end = bloco["page_end"]
        caixas_lista = [bbox_inicial]

        while indice < len(blocos):
            atual = blocos[indice]
            if atual["type"] != "paragraph":
                break
            m = REGEX_ITEM_LISTA.match(atual.get("content", ""))
            if not m:
                break
            itens.append(m.group(3).strip())
            fontes_lista.extend(atual["source_element_ids"])
            inline_lista.extend(atual["inline_icons"])
            page_end = max(page_end, int(atual["page_end"]))
            caixas_lista.append(_bbox(atual))
            indice += 1

        saida.append({
            "type": "list",
            "list_style": estilo,
            "page_start": page_start,
            "page_end": page_end,
            "lead_text": lead_text,
            "items": itens,
            "inline_icons": _deduplicar_ids(inline_lista),
            "referenced_alerts": [],
            "source_element_ids": _deduplicar_ids(fontes_lista),
            "_bbox": _unir_caixas(caixas_lista),
        })

    return saida


def _vincular_legendas_imagens(blocos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Vincula legendas (`Fig. X ...`) e itens explicativos (`Legenda para -> Fig. X`) às imagens."""
    imagens_por_fig: Dict[int, Dict[str, Any]] = {}
    consumidos: set[int] = set()

    for idx, bloco in enumerate(blocos):
        if bloco["type"] != "image":
            continue
        for offset in (1, -1, 2, -2, 3):
            viz_idx = idx + offset
            if not (0 <= viz_idx < len(blocos)) or viz_idx in consumidos:
                continue
            viz = blocos[viz_idx]
            if viz["page_start"] != bloco["page_start"]:
                continue
            if viz["type"] in {"paragraph", "heading"}:
                m_fig = re.match(r"^\s*Fig\.?\s*(\d+)\b(.*)$", viz.get("content", ""), re.I)
                if m_fig:
                    num_fig = int(m_fig.group(1))
                    bloco["figure_label"] = f"Fig. {num_fig}"
                    bloco["caption"] = viz["content"].strip()
                    bloco["inline_icons"] = _deduplicar_ids(
                        bloco["inline_icons"] + viz.get("inline_icons", [])
                    )
                    bloco["source_element_ids"] = _deduplicar_ids(
                        bloco["source_element_ids"] + viz.get("source_element_ids", [])
                    )
                    imagens_por_fig[num_fig] = bloco
                    consumidos.add(viz_idx)
                    break

    idx = 0
    while idx < len(blocos):
        if idx in consumidos:
            idx += 1
            continue
        bloco = blocos[idx]
        if bloco["type"] == "paragraph":
            m_leg = REGEX_LEGENDA_FIGURA.match(bloco.get("content", ""))
            if m_leg:
                num_fig = int(m_leg.group(1))
                img_alvo = imagens_por_fig.get(num_fig)
                if img_alvo is not None:
                    consumidos.add(idx)
                    img_alvo["source_element_ids"] = _deduplicar_ids(
                        img_alvo["source_element_ids"] + bloco["source_element_ids"]
                    )
                    idx += 1
                    while idx < len(blocos):
                        item_leg = blocos[idx]
                        if item_leg["type"] not in {"paragraph", "list"}:
                            break
                        if item_leg["page_start"] > img_alvo["page_end"] + 1:
                            break
                        texto_item = item_leg.get("content", "")
                        if item_leg["type"] == "paragraph" and (
                            REGEX_LEGENDA_FIGURA.match(texto_item)
                            or re.match(r"^\s*Fig\.?\s*\d+", texto_item, re.I)
                            or REGEX_CHAMADA_ALERTA.match(texto_item)
                        ):
                            break
                        if item_leg["type"] == "paragraph":
                            img_alvo["legend_items"].append(texto_item)
                        elif item_leg["type"] == "list":
                            if item_leg.get("lead_text"):
                                img_alvo["legend_items"].append(item_leg["lead_text"])
                            img_alvo["legend_items"].extend(item_leg.get("items", []))

                        img_alvo["page_end"] = max(
                            int(img_alvo["page_end"]), int(item_leg["page_end"])
                        )
                        img_alvo["inline_icons"] = _deduplicar_ids(
                            img_alvo["inline_icons"] + item_leg.get("inline_icons", [])
                        )
                        img_alvo["source_element_ids"] = _deduplicar_ids(
                            img_alvo["source_element_ids"] + item_leg.get("source_element_ids", [])
                        )
                        consumidos.add(idx)
                        idx += 1
                    continue
        idx += 1

    return [b for i, b in enumerate(blocos) if i not in consumidos]


def _atribuir_hierarquia_e_referencias(blocos: List[Dict[str, Any]]) -> None:
    """
    Preenche `id`, `reading_order`, `read_order`, `section_path`, `parent_section_id`,
    `alert_scope`, `refers_to_ids` e `referenced_alerts`.
    """
    pilha_secoes: List[Dict[str, Any]] = []
    contagem_por_pagina: Dict[int, int] = defaultdict(int)
    ultimo_id_significativo: Optional[str] = None

    for ordem_global, bloco in enumerate(blocos, start=1):
        bloco["id"] = f"blk_{ordem_global:06d}"
        bloco["reading_order"] = ordem_global
        pag_ini = int(bloco["page_start"])
        contagem_por_pagina[pag_ini] += 1
        bloco["read_order"] = contagem_por_pagina[pag_ini]

        if bloco["type"] == "heading":
            nivel = int(bloco["level"])
            while pilha_secoes and pilha_secoes[-1]["level"] >= nivel:
                pilha_secoes.pop()
            bloco["section_path"] = [h["content"] for h in pilha_secoes]
            bloco["parent_section_id"] = pilha_secoes[-1]["id"] if pilha_secoes else None
            pilha_secoes.append({
                "id": bloco["id"],
                "content": bloco["content"],
                "level": nivel,
            })
            ultimo_id_significativo = bloco["id"]
        else:
            bloco["section_path"] = [h["content"] for h in pilha_secoes]
            bloco["parent_section_id"] = pilha_secoes[-1]["id"] if pilha_secoes else None

            if bloco["type"] == "alert_box":
                eh_intro = any(
                    "introdução" in s.lower() for s in bloco["section_path"]
                ) or (pilha_secoes and pilha_secoes[-1]["level"] <= 2 and bloco["read_order"] <= 4)
                bloco["alert_scope"] = "chapter_intro" if eh_intro else "local_topic"
                alvo_ref = (
                    bloco["parent_section_id"]
                    if eh_intro and bloco["parent_section_id"]
                    else (ultimo_id_significativo or bloco["parent_section_id"])
                )
                bloco["refers_to_ids"] = [alvo_ref] if alvo_ref else []
            elif bloco["type"] in {"paragraph", "list", "table"}:
                ultimo_id_significativo = bloco["id"]

    alertas_por_pagina: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for bloco in blocos:
        if bloco["type"] == "alert_box":
            for p in range(int(bloco["page_start"]), int(bloco["page_end"]) + 1):
                alertas_por_pagina[p].append(bloco)

    for bloco in blocos:
        textos_busca: List[str] = []
        if bloco.get("content"):
            textos_busca.append(str(bloco["content"]))
        if bloco.get("lead_text"):
            textos_busca.append(str(bloco["lead_text"]))
        for item in bloco.get("items") or []:
            textos_busca.append(str(item))
        texto_completo = " ".join(textos_busca)

        if not texto_completo or not REGEX_CHAMADA_ALERTA.search(texto_completo):
            continue
        paginas_citadas = [int(m.group(1)) for m in REGEX_PAGINA.finditer(texto_completo)]
        refs_encontradas: List[str] = []
        for pag_impressa in paginas_citadas:
            for offset_pdf in (2, 0, 1, -1):
                candidatos = alertas_por_pagina.get(pag_impressa + offset_pdf, [])
                if candidatos:
                    refs_encontradas.extend(
                        a["id"] for a in candidatos if a["id"] != bloco["id"]
                    )
                    break
        bloco["referenced_alerts"] = _deduplicar_ids(refs_encontradas)


def _formatar_registro_saida(bloco: Dict[str, Any]) -> Dict[str, Any]:
    """Remove metadados geométricos internos e ordena as chaves segundo o modelo de dados."""
    comuns = {
        "id": bloco["id"],
        "reading_order": bloco["reading_order"],
        "read_order": bloco["read_order"],
        "type": bloco["type"],
        "page_start": bloco["page_start"],
        "page_end": bloco["page_end"],
        "section_path": bloco.get("section_path", []),
        "parent_section_id": bloco.get("parent_section_id"),
    }
    tipo = bloco["type"]
    especificos: Dict[str, Any] = {}

    if tipo == "heading":
        especificos = {
            "level": bloco["level"],
            "content": bloco["content"],
        }
    elif tipo == "paragraph":
        especificos = {
            "content": bloco["content"],
        }
    elif tipo == "list":
        especificos = {
            "list_style": bloco["list_style"],
            "lead_text": bloco.get("lead_text"),
            "items": bloco.get("items", []),
        }
    elif tipo == "alert_box":
        especificos = {
            "alert_level": bloco["alert_level"],
            "alert_scope": bloco["alert_scope"],
            "icon_id": bloco.get("icon_id"),
            "refers_to_ids": bloco.get("refers_to_ids", []),
            "lead_text": bloco.get("lead_text", ""),
            "items": bloco.get("items", []),
        }
    elif tipo == "table":
        especificos = {
            "markdown": bloco["markdown"],
        }
    elif tipo == "image":
        especificos = {
            "id_image": bloco.get("id_image"),
            "asset_id": bloco.get("asset_id"),
            "image_path": bloco.get("image_path"),
            "figure_label": bloco.get("figure_label"),
            "caption": bloco.get("caption"),
            "description": bloco.get("description", ""),
            "legend_items": bloco.get("legend_items", []),
        }
    elif tipo == "icon":
        especificos = {
            "icon_id": bloco.get("icon_id"),
            "icon_order_id": bloco.get("icon_order_id"),
            "image_path": bloco.get("image_path"),
            "description": bloco.get("description", ""),
        }

    rastreabilidade = {
        "inline_icons": bloco.get("inline_icons", []),
        "referenced_alerts": bloco.get("referenced_alerts", []),
        "source_element_ids": bloco.get("source_element_ids", []),
    }
    return {**comuns, **especificos, **rastreabilidade}


# ---------------------------------------------------------------------------
# Execução Principal e Homologação Visual (PDF de Debug da Fase 3)
# ---------------------------------------------------------------------------

def executar_fase3_reconstrucao(
    jsonl_entrada: Path,
    jsonl_saida: Path,
    arquivo_descr_images: Optional[Path] = None,
    arquivo_descr_icons: Optional[Path] = None,
    arquivo_catalogo_icons: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Counter]:
    """Executa a Fase 3 completa e grava `document_preparado.jsonl`."""
    jsonl_saida.parent.mkdir(parents=True, exist_ok=True)
    pasta_base = jsonl_saida.parent

    descr_images = _carregar_json_auxiliar(
        arquivo_descr_images or (pasta_base / "descricao_images.json")
    )
    caminho_icons = arquivo_descr_icons or (pasta_base / "descricao_icons.json")
    if not caminho_icons.exists():
        caminho_icons = pasta_base / "descricao_icones.json"
    descr_icons = _carregar_json_auxiliar(caminho_icons)

    paginas_fase2: List[Dict[str, Any]] = []
    blocos_brutos: List[Dict[str, Any]] = []
    estatisticas = Counter()

    with jsonl_entrada.open("r", encoding="utf-8") as entrada:
        for linha in entrada:
            if not linha.strip():
                continue
            pagina = json.loads(linha)
            paginas_fase2.append(pagina)
            blocos_pagina = reconstruir_pagina(pagina, descr_images, descr_icons)
            blocos_brutos.extend(blocos_pagina)
            estatisticas["paginas"] += 1

    blocos = _unificar_linhas_indice_e_legendas(blocos_brutos)
    blocos = _unificar_tabelas_continuadas(blocos)
    blocos = _costurar_paragrafos_continuados(blocos)
    blocos = _agrupar_listas(blocos)
    blocos = _vincular_legendas_imagens(blocos)
    _atribuir_hierarquia_e_referencias(blocos)

    registros_finais = [_formatar_registro_saida(b) for b in blocos]
    for r in registros_finais:
        estatisticas[r["type"]] += 1

    with jsonl_saida.open("w", encoding="utf-8") as saida:
        for registro in registros_finais:
            saida.write(json.dumps(registro, ensure_ascii=False) + "\n")

    print(
        f"Fase 3: {len(registros_finais)} unidades semânticas geradas "
        f"(headings={estatisticas['heading']}, paragraphs={estatisticas['paragraph']}, "
        f"lists={estatisticas['list']}, alerts={estatisticas['alert_box']}, "
        f"tables={estatisticas['table']}, images={estatisticas['image']}, "
        f"icons={estatisticas['icon']})."
    )
    return paginas_fase2, registros_finais, estatisticas


def _desenhar_seta_fluxo(
    canvas: fitz.Shape,
    inicio: fitz.Point,
    fim: fitz.Point,
    cor: Tuple[float, float, float] = (0.85, 0.10, 0.10),
) -> None:
    """Desenha uma linha direcionada com ponta de seta ligando dois blocos consecutivos."""
    canvas.draw_line(inicio, fim)
    canvas.finish(color=cor, width=0.65)

    dx = fim.x - inicio.x
    dy = fim.y - inicio.y
    comprimento = math.hypot(dx, dy)
    if comprimento < 4.0:
        return
    ux, uy = dx / comprimento, dy / comprimento
    tamanho_seta = 4.0
    largura_seta = 1.8

    base_x = fim.x - ux * tamanho_seta
    base_y = fim.y - uy * tamanho_seta
    esq = fitz.Point(base_x - uy * largura_seta, base_y + ux * largura_seta)
    dir_pt = fitz.Point(base_x + uy * largura_seta, base_y - ux * largura_seta)

    canvas.draw_polyline([esq, fim, dir_pt])
    canvas.finish(color=cor, fill=cor, width=0.65, closePath=True)


def gerar_debug_fase3_pdf(
    pdf_original: Path,
    paginas_fase2: Sequence[Dict[str, Any]],
    registros: Sequence[Dict[str, Any]],
    pdf_saida: Path,
) -> Path:
    """
    Gera `debug_fase3_ordem_leitura.pdf` demarcando as caixas semânticas coloridas
    por tipo, o identificador `#reading_order` e o grafo direcionado de leitura.
    """
    pdf_saida.parent.mkdir(parents=True, exist_ok=True)

    origem_por_id: Dict[str, Tuple[int, Tuple[float, float, float, float]]] = {}
    for pagina in paginas_fase2:
        num_pag = int(pagina["page"])
        for elemento in pagina.get("elements", []):
            caixa = _bbox(elemento)
            if elemento.get("id"):
                origem_por_id[str(elemento["id"])] = (num_pag, caixa)
            for sid in elemento.get("source_element_ids") or []:
                origem_por_id[str(sid)] = (num_pag, caixa)

    blocos_por_pagina: Dict[int, List[Tuple[int, int, str, Tuple[float, float, float, float]]]] = (
        defaultdict(list)
    )
    for reg in registros:
        caixas_por_pag: Dict[int, List[Tuple[float, float, float, float]]] = defaultdict(list)
        for sid in reg.get("source_element_ids", []):
            info = origem_por_id.get(str(sid))
            if info is not None:
                caixas_por_pag[info[0]].append(info[1])

        for num_pag, lista_caixas in caixas_por_pag.items():
            envolvente = _unir_caixas(lista_caixas)
            blocos_por_pagina[num_pag].append(
                (int(reg["reading_order"]), int(reg["read_order"]), str(reg["type"]), envolvente)
            )

    documento = fitz.open(str(pdf_original))
    total_demarcados = 0

    for num_pag, itens_pagina in blocos_por_pagina.items():
        if not (1 <= num_pag <= len(documento)):
            continue
        itens_pagina.sort(key=lambda item: item[0])
        canvas = documento[num_pag - 1].new_shape()
        centros: List[fitz.Point] = []

        for reading_order, read_order, tipo, (x0, y0, x1, y1) in itens_pagina:
            cor = CORES_DEBUG_FASE3.get(tipo, (0.2, 0.2, 0.2))
            retangulo = fitz.Rect(x0, y0, x1, y1)
            canvas.draw_rect(retangulo)
            canvas.finish(color=cor, width=0.75, fill_opacity=0)
            canvas.insert_text(
                fitz.Point(retangulo.x0, max(6.0, retangulo.y0 - 1.5)),
                f"#{reading_order} ({read_order}:{tipo})",
                fontsize=4.5,
                color=cor,
            )
            centros.append(fitz.Point((x0 + x1) / 2.0, (y0 + y1) / 2.0))
            total_demarcados += 1

        for p_atual, p_prox in zip(centros, centros[1:]):
            _desenhar_seta_fluxo(canvas, p_atual, p_prox)

        if centros:
            canvas.commit()

    documento.save(str(pdf_saida), garbage=3, deflate=True)
    documento.close()
    print(f"PDF de debug da Fase 3 gerado: {pdf_saida} ({total_demarcados} blocos)")
    return pdf_saida


if __name__ == "__main__":
    from utils.caminhos import raiz

    dados_prep = raiz["dados_preparados"]
    caminho_saida = dados_prep.get(
        "document_preparado",
        dados_prep["path"] / "document_preparado.jsonl",
    )
    caminho_debug_pdf = dados_prep["debug_visual"].get(
        "fase3_ordem_leitura",
        dados_prep["path"] / "debug_visual" / "debug_fase3_ordem_leitura.pdf",
    )

    if caminho_saida.exists():
        caminho_saida.unlink()
    if caminho_debug_pdf.exists():
        caminho_debug_pdf.unlink()

    paginas_f2, registros_f3, _estatisticas = executar_fase3_reconstrucao(
        jsonl_entrada=dados_prep["document_fase2"],
        jsonl_saida=caminho_saida,
        arquivo_catalogo_icons=dados_prep["icons_catalog"],
    )
    gerar_debug_fase3_pdf(
        pdf_original=raiz["dados_extraidos"]["pdf_original"],
        paginas_fase2=paginas_f2,
        registros=registros_f3,
        pdf_saida=caminho_debug_pdf,
    )
