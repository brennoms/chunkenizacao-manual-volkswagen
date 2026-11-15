import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymupdf as fitz  # PyMuPDF

from utils.funcoes import calcular_hash_sha256

EXTRACTOR_VERSION = "1.2.0"
SCHEMA_VERSION = "1.0.0"

# Mapeamento de glifos da Private Use Area (PUA) das fontes proprietárias VW
MAPA_PUA_VW = {
    "\uf0a3": "▸",       # Continua na página seguinte
    "\uf0cc": "▪",       # Fim de um trecho de texto
    "\uf0e4": "⚠️",      # Símbolo de atenção / consultar início do capítulo
    "\uf03a": "[MAX]",   # Desembaçamento máximo Climatronic
    "\uf05c": "[DEF]",   # Desembaçamento para-brisa
    "\uf05d": "[REAR]",  # Desembaçador traseiro
    "\uf04f": "[REC]",   # Recirculação de ar
    "\uf03b": "[REC]",   # Recirculação de ar
    "\uf041": "A",
    "\uf055": "U",
    "\uf054": "T",
    "\uf043": "C",
    "\uf0c8": "/",
    "\uf031": "1",
    "\uf032": "2",
}


# =====================================================================
# Funções Auxiliares de Formatação, Geometria e Cores
# =====================================================================

def _calcular_bboxes(
    rect: fitz.Rect, page_width: float, page_height: float
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Calcula o bbox em pontos tipográficos e o normalized_bbox (0 a 1)."""
    x = float(rect.x0)
    y = float(rect.y0)
    w = float(max(0.0, rect.width))
    h = float(max(0.0, rect.height))

    bbox = {
        "x": round(x, 2),
        "y": round(y, 2),
        "width": round(w, 2),
        "height": round(h, 2),
    }

    norm_bbox = {
        "x": round(x / page_width, 6) if page_width else 0.0,
        "y": round(y / page_height, 6) if page_height else 0.0,
        "width": round(w / page_width, 6) if page_width else 0.0,
        "height": round(h / page_height, 6) if page_height else 0.0,
    }
    return bbox, norm_bbox


def _cor_int_para_objeto(cor_int: Optional[int], alpha: float = 1.0) -> Optional[Dict[str, Any]]:
    """Converte inteiro sRGB do PyMuPDF (0xRRGGBB) para o modelo de cor padronizado."""
    if cor_int is None:
        return None
    r = (cor_int >> 16) & 255
    g = (cor_int >> 8) & 255
    b = cor_int & 255
    return {
        "space": "rgb",
        "components": [r, g, b],
        "alpha": round(float(alpha), 3) if alpha is not None else 1.0,
    }


def _cor_tupla_para_objeto(
    cor_seq: Optional[Tuple[float, ...]], alpha: Optional[float] = 1.0
) -> Optional[Dict[str, Any]]:
    """Converte tupla de floats (0.0 a 1.0) de vetores/formas para o modelo de cor."""
    if not cor_seq:
        return None
    n = len(cor_seq)
    opacidade = round(float(alpha), 3) if alpha is not None else 1.0

    if n == 1:
        return {"space": "gray", "components": [round(cor_seq[0], 4)], "alpha": opacidade}
    elif n == 3:
        return {
            "space": "rgb",
            "components": [int(round(c * 255)) for c in cor_seq],
            "alpha": opacidade,
        }
    elif n == 4:
        return {
            "space": "cmyk",
            "components": [round(c, 4) for c in cor_seq],
            "alpha": opacidade,
        }
    return {
        "space": "other",
        "components": [round(c, 4) for c in cor_seq],
        "alpha": opacidade,
    }


def _extrair_estilos_span(flags: int, font_name: str) -> List[str]:
    """Identifica estilos de texto a partir das flags PDF e do nome da fonte."""
    estilos = []
    font_lower = (font_name or "").lower()

    # Flags de texto do PyMuPDF: bit 0 = superscript, bit 1 = italic, bit 4 = bold
    if (flags & 16) or any(k in font_lower for k in ("bold", "black", "heavy", "demi")):
        estilos.append("bold")
    if (flags & 2) or any(k in font_lower for k in ("italic", "oblique")):
        estilos.append("italic")
    if flags & 1:
        estilos.append("superscript")

    return estilos


def _criar_source(
    xref: Optional[int] = None,
    smask_xref: Optional[int] = None,
    content_stream_xref: Optional[int] = None,
    resource_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Monta o dicionário padronizado de origem (source)."""
    return {
        "xref": xref,
        "smask_xref": smask_xref,
        "object_number": xref,
        "generation": 0 if xref is not None else None,
        "content_stream_xref": content_stream_xref,
        "resource_name": resource_name,
    }


# =====================================================================
# Limpeza de Texto, Barreiras Geométricas e Heurísticas de Agrupamento
# =====================================================================

def _normalizar_texto_span(
    texto: str, is_superscript: bool, texto_anterior: str
) -> str:
    """
    Corrige caracteres da Private Use Area (PUA) e converte letras 'o'/'a'
    sobrescritas em indicadores ordinais Unicode ('º'/'ª') quando precedidas
    por dígitos ou 'N'/'n' (ex.: 'No PR' -> 'Nº PR', '1a revisão' -> '1ª revisão').
    """
    if not texto:
        return ""

    partes = []
    for ch in texto:
        if ch in MAPA_PUA_VW:
            partes.append(MAPA_PUA_VW[ch])
        elif 0xE000 <= ord(ch) <= 0xF8FF:
            continue
        else:
            partes.append(ch)
    texto_limpo = "".join(partes)

    if is_superscript and texto_anterior:
        ultimo_char = texto_anterior[-1]
        txt_str = texto_limpo.strip()
        if txt_str in ("o", "o.", "os") and (ultimo_char.isdigit() or ultimo_char in ("N", "n")):
            texto_limpo = texto_limpo.replace("o", "º", 1)
        elif txt_str in ("a", "a.", "as") and ultimo_char.isdigit():
            texto_limpo = texto_limpo.replace("a", "ª", 1)
        elif re.match(r"^[a-z0-9]\)$", txt_str) and ultimo_char == "_":
            texto_limpo = " " + texto_limpo.lstrip()

    return texto_limpo


def _limpar_linha_texto(texto_linha: str) -> str:
    """Comprime múltiplos espaços de tabulação e remove pontilhados de legenda."""
    # Remove linhas pontilhadas longas de legendas ("Maçanetas....... 68" ou "Maçanetas.......")
    texto_linha = re.sub(r"\.{4,}\s*", " ", texto_linha)
    # Reduz sequências de 3+ espaços seguidos por número no final da linha (típico do Índice)
    texto_linha = re.sub(r"\s{3,}(\d+)$", r" \1", texto_linha.rstrip())
    # Reduz múltiplos espaços internos excessivos
    texto_linha = re.sub(r" {2,}", " ", texto_linha)
    return texto_linha.strip()


def _obter_barreiras(
    elementos_formas: List[Dict[str, Any]]
) -> Tuple[List[Tuple[float, float, float]], List[Tuple[float, float, float]]]:
    """
    Extrai barreiras físicas a partir de shapes e vetores:
      - barreiras_h: (y, x0, x1) para fios horizontais e bordas superiores/inferiores de caixas.
      - barreiras_v: (x, y0, y1) para fios verticais e bordas laterais de tabelas.
    """
    barreiras_h: List[Tuple[float, float, float]] = []
    barreiras_v: List[Tuple[float, float, float]] = []

    for el in elementos_formas:
        b = el["bbox"]
        x0, y0, w, h = b["x"], b["y"], b["width"], b["height"]
        el_type = el.get("type")
        sh_type = el.get("shape_type")

        if el_type == "shape" and sh_type == "line":
            if h <= 2.0 and w >= 12.0:
                barreiras_h.append((y0 + h / 2.0, x0, x0 + w))
            elif w <= 2.0 and h >= 10.0:
                barreiras_v.append((x0 + w / 2.0, y0, y0 + h))
        elif el_type == "shape" and sh_type == "rectangle":
            if w >= 12.0 and h >= 6.0:
                barreiras_h.append((y0, x0, x0 + w))
                barreiras_h.append((y0 + h, x0, x0 + w))
                barreiras_v.append((x0, y0, y0 + h))
                barreiras_v.append((x0 + w, y0, y0 + h))
        elif el_type == "vector":
            if h <= 2.0 and w >= 15.0:
                barreiras_h.append((y0 + h / 2.0, x0, x0 + w))
            elif w <= 2.0 and h >= 12.0:
                barreiras_v.append((x0 + w / 2.0, y0, y0 + h))

    return barreiras_h, barreiras_v


def _existe_barreira_horizontal_entre(
    rect_a: fitz.Rect, rect_b: fitz.Rect, barreiras_h: List[Tuple[float, float, float]]
) -> bool:
    """
    Verifica se há uma linha horizontal desenhada entre o centro vertical de rect_a
    e o centro vertical de rect_b. Usar o centro vertical garante detecção mesmo quando
    as caixas de texto tocam a linha divisória.
    """
    yc_a = (rect_a.y0 + rect_a.y1) / 2.0
    yc_b = (rect_b.y0 + rect_b.y1) / 2.0
    y_sup = min(yc_a, yc_b)
    y_inf = max(yc_a, yc_b)

    x_min = min(rect_a.x0, rect_b.x0)
    x_max = max(rect_a.x1, rect_b.x1)

    for y_bar, bx0, bx1 in barreiras_h:
        if y_sup <= y_bar <= y_inf:
            intersecao_x = min(x_max, bx1) - max(x_min, bx0)
            if intersecao_x >= 8.0:
                return True
    return False


def _existe_barreira_vertical_entre(
    rect_esq: fitz.Rect, rect_dir: fitz.Rect, barreiras_v: List[Tuple[float, float, float]]
) -> bool:
    """Verifica se há um fio vertical separando duas caixas na mesma linha horizontal."""
    xc_e = (rect_esq.x0 + rect_esq.x1) / 2.0
    xc_d = (rect_dir.x0 + rect_dir.x1) / 2.0
    x_min = min(xc_e, xc_d)
    x_max = max(xc_e, xc_d)

    y_min = min(rect_esq.y0, rect_dir.y0)
    y_max = max(rect_esq.y1, rect_dir.y1)

    for x_bar, by0, by1 in barreiras_v:
        if x_min <= x_bar <= x_max:
            intersecao_y = min(y_max, by1) - max(y_min, by0)
            if intersecao_y >= 4.0:
                return True
    return False


def _mesclar_duas_linhas_horizontais(l_esq: Dict[str, Any], l_dir: Dict[str, Any]) -> Dict[str, Any]:
    """Funde dois fragmentos que pertencem à mesma linha física horizontal."""
    precisa_espaco = not l_esq["text"].endswith(" ") and not l_dir["text"].startswith((" ", ",", ".", ":", ";", ")"))
    novo_texto = _limpar_linha_texto(
        f"{l_esq['text']}{' ' if precisa_espaco else ''}{l_dir['text']}"
    )

    novo_rect = fitz.Rect(
        min(l_esq["rect"].x0, l_dir["rect"].x0),
        min(l_esq["rect"].y0, l_dir["rect"].y0),
        max(l_esq["rect"].x1, l_dir["rect"].x1),
        max(l_esq["rect"].y1, l_dir["rect"].y1),
    )

    c_fontes = l_esq["contador_fontes"] + l_dir["contador_fontes"]
    c_tams = l_esq["contador_tamanhos"] + l_dir["contador_tamanhos"]
    c_cores = l_esq["contador_cores"] + l_dir["contador_cores"]
    c_ests = l_esq["contador_estilos"] + l_dir["contador_estilos"]
    peso_tot = l_esq["total_peso"] + l_dir["total_peso"]

    return {
        "block_idx": l_esq["block_idx"],
        "rect": novo_rect,
        "text": novo_texto,
        "font": c_fontes.most_common(1)[0][0],
        "font_size": c_tams.most_common(1)[0][0],
        "color_int": c_cores.most_common(1)[0][0],
        "contador_fontes": c_fontes,
        "contador_tamanhos": c_tams,
        "contador_cores": c_cores,
        "contador_estilos": c_ests,
        "total_peso": peso_tot,
        "rotation": l_esq["rotation"],
    }


def _extrair_linhas_atomicas(
    raw_dict: Dict[str, Any], barreiras_v: List[Tuple[float, float, float]]
) -> List[Dict[str, Any]]:
    """
    Extrai todas as linhas do PyMuPDF preservando espaços entre spans e realiza
    coalescência horizontal de fragmentos na mesma linha de base (como 'Observe [ícone] no início...'
    ou botões inline '[0.0/SET]', '[OK]', '[CAR]').
    """
    linhas_brutas: List[Dict[str, Any]] = []

    for block_idx, block in enumerate(raw_dict.get("blocks", [])):
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            # Mantém todos os spans não vazios (inclusive espaços simples entre palavras)
            spans = [s for s in line.get("spans", []) if s.get("text", "") != ""]
            if not spans:
                continue

            dir_vec = line.get("dir", (1.0, 0.0))
            dx, dy = dir_vec
            rotacao = round(math.degrees(math.atan2(dy, dx)) % 360, 2)

            # Subdivide spans caso haja salto grande para número de página no rodapé ou legenda
            grupos_spans: List[List[Dict[str, Any]]] = [[spans[0]]]
            for sp in spans[1:]:
                sp_ant = grupos_spans[-1][-1]
                gap_x = float(sp["bbox"][0]) - float(sp_ant["bbox"][2])
                y_medio = float(sp["bbox"][1])
                txt_atual = sp.get("text", "").strip()

                # Separa número de página no rodapé (y > 555) ou número de página à direita de legenda (x > 345)
                if (y_medio > 555.0 and gap_x > 20.0 and txt_atual.isdigit()) or (
                    float(sp["bbox"][0]) > 345.0 and gap_x > 18.0 and re.match(r"^[\d,\s]+$", txt_atual)
                ):
                    grupos_spans.append([sp])
                else:
                    grupos_spans[-1].append(sp)

            for grupo in grupos_spans:
                acumulado_txt = ""
                contador_fontes: Counter = Counter()
                contador_tamanhos: Counter = Counter()
                contador_cores: Counter = Counter()
                contador_estilos: Counter = Counter()
                total_peso = 0
                ultimo_x1: Optional[float] = None

                spans_com_texto = [s for s in grupo if s.get("text", "").strip()]
                if not spans_com_texto:
                    continue

                x0 = min(float(s["bbox"][0]) for s in spans_com_texto)
                y0 = min(float(s["bbox"][1]) for s in spans_com_texto)
                x1 = max(float(s["bbox"][2]) for s in spans_com_texto)
                y1 = max(float(s["bbox"][3]) for s in spans_com_texto)

                for sp in grupo:
                    raw_txt = sp.get("text", "")
                    flags = sp.get("flags", 0)
                    font_name = sp.get("font") or "unknown"
                    estilos_sp = _extrair_estilos_span(flags, font_name)
                    is_sup = "superscript" in estilos_sp

                    # Insere espaço se houver distância física entre o fim do span anterior e o início deste
                    sp_x0 = float(sp["bbox"][0])
                    sp_x1 = float(sp["bbox"][2])
                    if (
                        ultimo_x1 is not None
                        and (sp_x0 - ultimo_x1) > 1.2
                        and acumulado_txt
                        and not acumulado_txt.endswith(" ")
                        and not raw_txt.startswith(" ")
                        and not is_sup
                    ):
                        acumulado_txt += " "

                    txt_norm = _normalizar_texto_span(raw_txt, is_sup, acumulado_txt)
                    if not txt_norm:
                        continue
                    acumulado_txt += txt_norm
                    if raw_txt.strip():
                        ultimo_x1 = sp_x1

                    peso = len(txt_norm.strip())
                    if peso == 0:
                        continue

                    total_peso += peso
                    font_size = round(float(sp.get("size", 0.0)), 2)
                    color_int = sp.get("color", 0)

                    contador_fontes[font_name] += peso
                    contador_tamanhos[font_size] += peso
                    contador_cores[color_int] += peso
                    for est in estilos_sp:
                        contador_estilos[est] += peso

                texto_limpo = _limpar_linha_texto(acumulado_txt)
                if not texto_limpo or total_peso == 0:
                    continue

                linhas_brutas.append({
                    "block_idx": block_idx,
                    "rect": fitz.Rect(x0, y0, x1, y1),
                    "text": texto_limpo,
                    "font": contador_fontes.most_common(1)[0][0],
                    "font_size": contador_tamanhos.most_common(1)[0][0],
                    "color_int": contador_cores.most_common(1)[0][0],
                    "contador_fontes": contador_fontes,
                    "contador_tamanhos": contador_tamanhos,
                    "contador_cores": contador_cores,
                    "contador_estilos": contador_estilos,
                    "total_peso": total_peso,
                    "rotation": rotacao,
                })

    if not linhas_brutas:
        return []

    # Passo de Coalescência Horizontal: une fragmentos na mesma linha de base (ex.: 'Observe' + 'no início...', ou texto + botão '[0.0/SET]')
    # Ordena preliminarmente por faixa vertical (~3pt) e x0 dentro da mesma coluna
    linhas_ordenadas = sorted(
        linhas_brutas,
        key=lambda l: (
            0 if l["rect"].x0 < 200.0 else 1,
            round(((l["rect"].y0 + l["rect"].y1) / 2.0) / 3.0),
            l["rect"].x0,
        ),
    )

    linhas_coalescidas: List[Dict[str, Any]] = [linhas_ordenadas[0]]
    for cand in linhas_ordenadas[1:]:
        atual = linhas_coalescidas[-1]
        r_esq = atual["rect"]
        r_dir = cand["rect"]

        yc_esq = (r_esq.y0 + r_esq.y1) / 2.0
        yc_dir = (r_dir.y0 + r_dir.y1) / 2.0
        mesma_altura = abs(yc_esq - yc_dir) <= 3.5
        gap_x = r_dir.x0 - r_esq.x1

        # Evita mesclar se forem colunas diferentes, rodapé ou números de página de legenda à direita
        mesma_coluna = (r_esq.x1 < 218.0 and r_dir.x0 < 218.0) or (r_esq.x0 >= 200.0 and r_dir.x0 >= 200.0)
        eh_rodape = r_esq.y0 > 555.0 or r_dir.y0 > 555.0
        eh_num_legenda_direita = r_dir.x0 > 345.0 and re.match(r"^[\d,\s]+$", cand["text"])
        eh_num_item_isolado = len(atual["text"]) <= 2 and atual["text"].isdigit()

        if (
            mesma_altura
            and mesma_coluna
            and not eh_rodape
            and not eh_num_legenda_direita
            and not eh_num_item_isolado
            and abs(atual["rotation"] - cand["rotation"]) <= 1.0
            and -4.0 <= gap_x <= 24.0
            and not _existe_barreira_vertical_entre(r_esq, r_dir, barreiras_v)
        ):
            linhas_coalescidas[-1] = _mesclar_duas_linhas_horizontais(atual, cand)
        else:
            linhas_coalescidas.append(cand)

    # Restaura a ordem próxima à sequência original de blocos após coalescer fragmentos inline
    linhas_coalescidas.sort(key=lambda l: (l["block_idx"], l["rect"].y0, l["rect"].x0))
    return linhas_coalescidas


def _deve_unir_linhas(
    grupo_atual: List[Dict[str, Any]],
    prox_linha: Dict[str, Any],
    barreiras_h: List[Tuple[float, float, float]],
    barreiras_v: List[Tuple[float, float, float]],
) -> bool:
    """
    Decide se `prox_linha` pertence ao mesmo bloco textual (parágrafo, item de lista
    ou item de definição) de `grupo_atual`.
    """
    ultima = grupo_atual[-1]
    r1: fitz.Rect = ultima["rect"]
    r2: fitz.Rect = prox_linha["rect"]

    # 1. Rotações diferentes nunca se unem
    if abs(ultima["rotation"] - prox_linha["rotation"]) > 1.0:
        return False

    # 2. Nunca unir texto do corpo com rodapé (y > 558) ou elementos afastados no rodapé
    if (r1.y0 < 558.0 <= r2.y0) or (r1.y0 >= 558.0 and abs(r2.x0 - r1.x0) > 15.0):
        return False

    # 3. Se houver uma linha horizontal ou vertical de tabela/quadro entre as duas linhas, separa!
    if _existe_barreira_horizontal_entre(r1, r2, barreiras_h):
        return False
    if _existe_barreira_vertical_entre(r1, r2, barreiras_v):
        return False

    txt1 = ultima["text"].strip()
    txt2 = prox_linha["text"].strip()

    # 4. Campos de formulário com '___' (ex.: 'Dia___', 'Nome do cliente: ___') não se fundem a outros campos
    if "___" in txt1 or "___" in txt2:
        # Só une se a segunda linha for exclusivamente uma linha de sublinhados continuando o campo
        if re.match(r"^_+$", txt2) and (r2.y0 - r1.y1) <= 2.5:
            return True
        return False

    # 5. Verifica distância vertical máxima de entrelinha do mesmo parágrafo (~2.4pt para texto normal)
    gap_y = r2.y0 - r1.y1
    max_gap = max(2.4, ultima["font_size"] * 0.30)
    if gap_y < -3.0 or gap_y > max_gap:
        return False

    # 6. Verifica se estão na mesma coluna horizontal
    sobreposicao_x = min(r1.x1, r2.x1) - max(r1.x0, r2.x0)
    deslocamento_x0 = r2.x0 - r1.x0
    if sobreposicao_x < -2.0 or abs(deslocamento_x0) > 75.0:
        return False

    # 7. Diferença de tamanho de fonte ou cor (exceto quando envolve número/botão inline na linha)
    if abs(ultima["font_size"] - prox_linha["font_size"]) > 1.2:
        return False
    if ultima["color_int"] != prox_linha["color_int"]:
        return False

    # 8. Continuação de item de Sumário/Índice quebrado em 2 linhas (ex.: '– Sistema de travamento e de partida sem' -> 'chave “Keyless Access” 66')
    if txt1.startswith("– ") and not re.search(r"\b\d{1,3}$", txt1) and not txt2.startswith("– "):
        if -1.0 <= deslocamento_x0 <= 15.0:
            return True

    # 9. Novo item de lista ('– ', '• ', '✓') ou item numerado inicia um novo bloco
    if txt2.startswith(("– ", "• ", "✓")) or re.match(r"^\d+\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]", txt2):
        return False

    # 10. Se no Sumário a linha atual já terminou com o número da página (ex.: '... 64'), não une com o próximo item
    if txt1.startswith("– ") and re.search(r"\s\d{1,3}$", txt1):
        return False

    # 11. Tratamento de transição Negrito <-> Normal
    is_bold_1 = ultima["contador_estilos"]["bold"] > (ultima["total_peso"] * 0.5)
    is_bold_2 = prox_linha["contador_estilos"]["bold"] > (prox_linha["total_peso"] * 0.5)

    if is_bold_1 != is_bold_2:
        # Permite unir quando é um item de definição com início em negrito que continua na linha abaixo
        # (ex.: 'Tacômetro (conta-giros) (rotações x 1000' -> 'por minuto...' ou '-> Página 19.')
        parenteses_abertos = txt1.count("(") > txt1.count(")")
        continua_frase = (
            txt1.endswith("-")
            or parenteses_abertos
            or txt2.startswith(("→", "Página"))
            or (len(txt2) > 0 and txt2[0].islower())
        )
        if not continua_frase:
            return False

    # 12. Continuação de item de lista com recuo pendente (Hanging Indent: x0 avança até ~14pt)
    if txt1.startswith(("– ", "• ", "✓")) and (0.0 <= deslocamento_x0 <= 14.5):
        return True

    # 13. Linhas do mesmo bloco nativo que não terminaram em dois-pontos de cabeçalho (ex.: 'Região para:')
    if ultima["block_idx"] == prox_linha["block_idx"]:
        if txt1.endswith(":"):
            return False
        return True

    # 14. Parágrafo centralizado ou justificado quebrado em blocos distintos pelo PyMuPDF (ex.: pág 2 e 'Observe ... na pági-\nna 17.')
    centro_1 = (r1.x0 + r1.x1) / 2.0
    centro_2 = (r2.x0 + r2.x1) / 2.0
    alinhado_centro = abs(centro_1 - centro_2) <= 6.0
    alinhado_esq = abs(deslocamento_x0) <= 2.5

    if (alinhado_centro or alinhado_esq) and not txt1.endswith((".", "!", ":", "?")):
        return True

    return False


def _extrair_textos_da_pagina(
    raw_dict: Dict[str, Any],
    page_w: float,
    page_h: float,
    content_xref: Optional[int],
    elementos_formas: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Extrai blocos de texto agrupados de forma inteligente:
      - Coalesce fragmentos horizontais separados por ícones/botões inline.
      - Respeita barreiras horizontais e verticais de tabelas/quadros.
      - Reagrupa listas com recuo pendente, itens de definição e parágrafos centralizados.
      - Atribui 'style' apenas quando predominante (>50% dos caracteres do bloco).
    """
    barreiras_h, barreiras_v = _obter_barreiras(elementos_formas)
    linhas = _extrair_linhas_atomicas(raw_dict, barreiras_v)
    if not linhas:
        return []

    grupos: List[List[Dict[str, Any]]] = [[linhas[0]]]
    for linha in linhas[1:]:
        if _deve_unir_linhas(grupos[-1], linha, barreiras_h, barreiras_v):
            grupos[-1].append(linha)
        else:
            grupos.append([linha])

    textos_extraidos: List[Dict[str, Any]] = []
    for grupo in grupos:
        textos_linhas = [l["text"] for l in grupo]
        conteudo_final = "\n".join(textos_linhas).strip()
        if not conteudo_final:
            continue

        x0 = min(l["rect"].x0 for l in grupo)
        y0 = min(l["rect"].y0 for l in grupo)
        x1 = max(l["rect"].x1 for l in grupo)
        y1 = max(l["rect"].y1 for l in grupo)

        contador_fontes: Counter = Counter()
        contador_tamanhos: Counter = Counter()
        contador_cores: Counter = Counter()
        contador_estilos: Counter = Counter()
        peso_total_bloco = 0

        for l in grupo:
            contador_fontes.update(l["contador_fontes"])
            contador_tamanhos.update(l["contador_tamanhos"])
            contador_cores.update(l["contador_cores"])
            contador_estilos.update(l["contador_estilos"])
            peso_total_bloco += l["total_peso"]

        fonte_pred = contador_fontes.most_common(1)[0][0] if contador_fontes else None
        tam_pred = contador_tamanhos.most_common(1)[0][0] if contador_tamanhos else None
        cor_pred = contador_cores.most_common(1)[0][0] if contador_cores else None

        estilos_predominantes = sorted([
            est for est, qtd in contador_estilos.items()
            if qtd >= (peso_total_bloco * 0.5)
        ])

        bbox, norm_bbox = _calcular_bboxes(fitz.Rect(x0, y0, x1, y1), page_w, page_h)

        textos_extraidos.append({
            "type": "text",
            "bbox": bbox,
            "normalized_bbox": norm_bbox,
            "source_id": None,
            "source": _criar_source(content_stream_xref=content_xref),
            "extraction_method": "native",
            "rotation": grupo[0]["rotation"],
            "transform": None,
            "parent_id": None,
            "content": conteudo_final,
            "font": fonte_pred,
            "font_size": tam_pred,
            "color": _cor_int_para_objeto(cor_pred, alpha=1.0),
            "style": estilos_predominantes,
        })

    return textos_extraidos


# =====================================================================
# Fallback de OCR para Imagens de Página Inteira (Regra 10)
# =====================================================================

def _executar_ocr_em_imagem_pagina(
    page: fitz.Page,
    img_elem: Dict[str, Any],
    img_path_abs: Path,
    page_w: float,
    page_h: float,
    content_xref: Optional[int],
) -> List[Dict[str, Any]]:
    """
    Executa OCR complementar em imagens grandes de conteúdo (como nas páginas 3 e 4)
    quando não há texto nativo extraído sobre a região da imagem.
    """
    ib = img_elem["bbox"]
    img_rect = fitz.Rect(ib["x"], ib["y"], ib["x"] + ib["width"], ib["y"] + ib["height"])
    elementos_ocr: List[Dict[str, Any]] = []

    # 1. Tenta RapidOCR (ONNX, leve e instalável via: pip install rapidocr-onnxruntime)
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        if not hasattr(_executar_ocr_em_imagem_pagina, "_rapid_engine"):
            _executar_ocr_em_imagem_pagina._rapid_engine = RapidOCR()
        engine = _executar_ocr_em_imagem_pagina._rapid_engine

        result, _ = engine(str(img_path_abs))
        if result:
            escala_x = ib["width"] / float(img_elem["original_width"] or 1)
            escala_y = ib["height"] / float(img_elem["original_height"] or 1)

            for box, texto, _conf in result:
                texto_limpo = (texto or "").strip()
                if not texto_limpo:
                    continue
                xs = [pt[0] for pt in box]
                ys = [pt[1] for pt in box]
                rx0 = ib["x"] + min(xs) * escala_x
                ry0 = ib["y"] + min(ys) * escala_y
                rx1 = ib["x"] + max(xs) * escala_x
                ry1 = ib["y"] + max(ys) * escala_y

                bbox, norm_bbox = _calcular_bboxes(fitz.Rect(rx0, ry0, rx1, ry1), page_w, page_h)
                elementos_ocr.append({
                    "type": "text",
                    "bbox": bbox,
                    "normalized_bbox": norm_bbox,
                    "source_id": img_elem.get("source_id"),
                    "source": dict(img_elem.get("source", {})),
                    "extraction_method": "ocr",
                    "rotation": 0.0,
                    "transform": None,
                    "parent_id": img_elem.get("id"),
                    "content": texto_limpo,
                    "font": None,
                    "font_size": None,
                    "color": None,
                    "style": [],
                })
            return elementos_ocr
    except Exception:
        pass

    # 2. Tenta OCR nativo do PyMuPDF (Tesseract integrado)
    try:
        tp_ocr = page.get_textpage_ocr(flags=0, language="por", dpi=300, full=False)
        ocr_dict = page.get_text("dict", textpage=tp_ocr)
        for block in ocr_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            b_rect = fitz.Rect(block["bbox"])
            if not b_rect.intersects(img_rect):
                continue
            linhas = []
            for line in block.get("lines", []):
                txt_l = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if txt_l:
                    linhas.append(txt_l)
            conteudo = "\n".join(linhas).strip()
            if not conteudo:
                continue
            bbox, norm_bbox = _calcular_bboxes(b_rect, page_w, page_h)
            elementos_ocr.append({
                "type": "text",
                "bbox": bbox,
                "normalized_bbox": norm_bbox,
                "source_id": img_elem.get("source_id"),
                "source": dict(img_elem.get("source", {})),
                "extraction_method": "ocr",
                "rotation": 0.0,
                "transform": None,
                "parent_id": img_elem.get("id"),
                "content": conteudo,
                "font": None,
                "font_size": None,
                "color": None,
                "style": [],
            })
    except Exception:
        pass

    return elementos_ocr


# =====================================================================
# Extratores de Imagens, Vetores, Formas e Links
# =====================================================================

def _resolver_bytes_imagem(
    doc: fitz.Document, xref: int, smask_xref: Optional[int]
) -> Tuple[bytes, str, int, int]:
    """
    Extrai os bytes da imagem. Se houver SMask (máscara de opacidade),
    recompõe o canal alfa e salva como PNG para preservar a transparência.
    """
    info_base = doc.extract_image(xref)
    if not info_base:
        raise ValueError(f"Não foi possível extrair a imagem de xref={xref}")

    if smask_xref and smask_xref > 0:
        try:
            pix_base = fitz.Pixmap(doc, xref)
            pix_mask = fitz.Pixmap(doc, smask_xref)
            if pix_base.colorspace and pix_base.colorspace.n > 3:
                pix_base = fitz.Pixmap(fitz.csRGB, pix_base)
            pix_composto = fitz.Pixmap(pix_base, pix_mask)
            img_bytes = pix_composto.tobytes("png")
            return img_bytes, "png", pix_composto.width, pix_composto.height
        except Exception:
            pass

    return (
        info_base["image"],
        info_base.get("ext", "png"),
        int(info_base.get("width", 0)),
        int(info_base.get("height", 0)),
    )


def _extrair_imagens_da_pagina(
    doc: fitz.Document,
    page: fitz.Page,
    page_w: float,
    page_h: float,
    content_xref: Optional[int],
    assets_dir: Path,
    registro_hashes: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Extrai ocorrências de imagens na página, desduplicando binários por SHA-256."""
    imagens_extraidas = []
    infos_imagens = page.get_image_info(xrefs=True)

    mapa_recursos: Dict[int, Tuple[Optional[int], Optional[str]]] = {}
    for item in page.get_images(full=True):
        img_xref = item[0]
        smask = item[1] if item[1] > 0 else None
        res_name = item[7] if item[7] else None
        mapa_recursos[img_xref] = (smask, res_name)

    for info in infos_imagens:
        xref = info.get("xref", 0)
        if not xref or xref <= 0:
            continue

        smask_xref, res_name = mapa_recursos.get(xref, (None, None))

        try:
            img_bytes, ext, orig_w, orig_h = _resolver_bytes_imagem(doc, xref, smask_xref)
        except Exception:
            continue

        content_hash = hashlib.sha256(img_bytes).hexdigest()

        if content_hash not in registro_hashes:
            novo_indice = len(registro_hashes) + 1
            asset_id = f"asset_{novo_indice:06d}"
            nome_arquivo = f"{asset_id}.{ext}"
            caminho_completo = assets_dir / nome_arquivo
            caminho_completo.write_bytes(img_bytes)

            registro_hashes[content_hash] = {
                "asset_id": asset_id,
                "format": ext,
                "original_width": orig_w,
                "original_height": orig_h,
                "path": f"assets/{nome_arquivo}",
            }

        meta_asset = registro_hashes[content_hash]
        rect = fitz.Rect(info["bbox"])
        bbox, norm_bbox = _calcular_bboxes(rect, page_w, page_h)
        transform = [round(float(v), 4) for v in info["transform"]] if "transform" in info else None

        imagens_extraidas.append({
            "type": "image",
            "bbox": bbox,
            "normalized_bbox": norm_bbox,
            "source_id": f"xref_{xref}",
            "source": _criar_source(
                xref=xref,
                smask_xref=smask_xref,
                content_stream_xref=content_xref,
                resource_name=res_name,
            ),
            "extraction_method": "native",
            "rotation": None,
            "transform": transform,
            "parent_id": None,
            "asset_id": meta_asset["asset_id"],
            "format": meta_asset["format"],
            "original_width": meta_asset["original_width"],
            "original_height": meta_asset["original_height"],
            "path": meta_asset["path"],
            "content_hash": content_hash,
        })

    return imagens_extraidas


def _serializar_comando_vetorial(item: Tuple[Any, ...]) -> Dict[str, Any]:
    """Converte uma tupla de comando de desenho do PyMuPDF para JSON."""
    op = item[0]
    if op == "l":
        p1, p2 = item[1], item[2]
        return {"op": "l", "p1": [round(p1.x, 2), round(p1.y, 2)], "p2": [round(p2.x, 2), round(p2.y, 2)]}
    elif op == "re":
        r = item[1]
        return {
            "op": "re",
            "rect": [round(r.x0, 2), round(r.y0, 2), round(r.width, 2), round(r.height, 2)],
        }
    elif op == "qu":
        q = item[1]
        return {
            "op": "qu",
            "points": [
                [round(q.ul.x, 2), round(q.ul.y, 2)],
                [round(q.ur.x, 2), round(q.ur.y, 2)],
                [round(q.ll.x, 2), round(q.ll.y, 2)],
                [round(q.lr.x, 2), round(q.lr.y, 2)],
            ],
        }
    elif op == "c":
        pts = [[round(p.x, 2), round(p.y, 2)] for p in item[1:5]]
        return {"op": "c", "points": pts}
    return {"op": str(op), "raw": str(item[1:])}


def _extrair_vetores_e_formas_da_pagina(
    page: fitz.Page, page_w: float, page_h: float, content_xref: Optional[int]
) -> List[Dict[str, Any]]:
    """
    Extrai desenhos da página. Classifica como 'shape' apenas primitivas únicas
    declaradas explicitamente ('re' -> rectangle, 'l' -> line) e o restante como 'vector'.
    """
    elementos = []
    for path in page.get_drawings():
        rect = path.get("rect")
        if not rect:
            continue

        bbox, norm_bbox = _calcular_bboxes(fitz.Rect(rect), page_w, page_h)
        fill_color = _cor_tupla_para_objeto(path.get("fill"), path.get("fill_opacity", 1.0))
        stroke_color = _cor_tupla_para_objeto(path.get("color"), path.get("stroke_opacity", 1.0))
        stroke_width = round(float(path["width"]), 2) if path.get("width") is not None else None

        items = path.get("items", [])

        if len(items) == 1 and items[0][0] == "re":
            r = items[0][1]
            elementos.append({
                "type": "shape",
                "bbox": bbox,
                "normalized_bbox": norm_bbox,
                "source_id": None,
                "source": _criar_source(content_stream_xref=content_xref),
                "extraction_method": "native",
                "rotation": None,
                "transform": None,
                "parent_id": None,
                "shape_type": "rectangle",
                "geometry": {
                    "x": round(r.x0, 2),
                    "y": round(r.y0, 2),
                    "width": round(r.width, 2),
                    "height": round(r.height, 2),
                },
                "fill_color": fill_color,
                "stroke_color": stroke_color,
                "stroke_width": stroke_width,
            })
        elif len(items) == 1 and items[0][0] == "l":
            p1, p2 = items[0][1], items[0][2]
            elementos.append({
                "type": "shape",
                "bbox": bbox,
                "normalized_bbox": norm_bbox,
                "source_id": None,
                "source": _criar_source(content_stream_xref=content_xref),
                "extraction_method": "native",
                "rotation": None,
                "transform": None,
                "parent_id": None,
                "shape_type": "line",
                "geometry": {
                    "x1": round(p1.x, 2),
                    "y1": round(p1.y, 2),
                    "x2": round(p2.x, 2),
                    "y2": round(p2.y, 2),
                },
                "fill_color": fill_color,
                "stroke_color": stroke_color,
                "stroke_width": stroke_width,
            })
        else:
            comandos = [_serializar_comando_vetorial(it) for it in items]
            elementos.append({
                "type": "vector",
                "bbox": bbox,
                "normalized_bbox": norm_bbox,
                "source_id": None,
                "source": _criar_source(content_stream_xref=content_xref),
                "extraction_method": "native",
                "rotation": None,
                "transform": None,
                "parent_id": None,
                "geometry": {"commands": comandos},
                "fill_color": fill_color,
                "stroke_color": stroke_color,
                "stroke_width": stroke_width,
            })

    return elementos


def _extrair_links_da_pagina(
    doc: fitz.Document, page: fitz.Page, page_w: float, page_h: float
) -> List[Dict[str, Any]]:
    """Extrai hiperlinks externos e âncoras internas da página."""
    mapa_tipos = {
        fitz.LINK_URI: "uri",
        fitz.LINK_GOTO: "goto",
        fitz.LINK_LAUNCH: "launch",
        fitz.LINK_NAMED: "named",
        fitz.LINK_GOTOR: "goto",
    }
    links_extraidos = []

    for link in page.get_links():
        rect = fitz.Rect(link["from"])
        bbox, norm_bbox = _calcular_bboxes(rect, page_w, page_h)
        kind = link.get("kind", fitz.LINK_NONE)
        link_type = mapa_tipos.get(kind, "unknown")

        uri = link.get("uri") if link_type == "uri" else None
        destination = None

        if link_type in ("goto", "named"):
            dest_page = link.get("page")
            to_pt = link.get("to")
            pagina_destino = (
                doc[int(dest_page)]
                if dest_page is not None and 0 <= int(dest_page) < doc.page_count
                else None
            )
            altura_destino = float(pagina_destino.rect.height) if pagina_destino else None
            destination = {
                "page": (int(dest_page) + 1) if dest_page is not None and dest_page >= 0 else None,
                "x": round(float(to_pt.x), 2) if to_pt else None,
                "y": (
                    round(altura_destino - float(to_pt.y), 2)
                    if to_pt and altura_destino is not None
                    else None
                ),
            }

        xref_link = link.get("xref")
        links_extraidos.append({
            "type": "link",
            "bbox": bbox,
            "normalized_bbox": norm_bbox,
            "source_id": f"xref_{xref_link}" if xref_link else None,
            "source": _criar_source(xref=xref_link),
            "extraction_method": "native",
            "rotation": None,
            "transform": None,
            "parent_id": None,
            "link_type": link_type,
            "uri": uri,
            "destination": destination,
        })

    return links_extraidos


# =====================================================================
# Função Principal de Extração Bruta
# =====================================================================

def extrair_pdf_bruto(pdf_path: Path, output_dir: Path, file_sha256: Optional[str] = None) -> None:
    """
    Executa a extração bruta completa do PDF, gerando:
      - output_dir/metadata.json
      - output_dir/document.jsonl
      - output_dir/assets/asset_XXXXXX.<ext>
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    if not file_sha256:
        file_sha256 = calcular_hash_sha256(pdf_path)

    doc = fitz.open(pdf_path)

    # 1. Monta e salva metadata.json
    meta_raw = doc.metadata or {}
    versao_pdf = None
    with open(pdf_path, "rb") as f_head:
        cabecalho = f_head.read(20).decode("latin-1", errors="ignore")
        match_ver = re.search(r"%PDF-(\d+\.\d+)", cabecalho)
        if match_ver:
            versao_pdf = match_ver.group(1)

    metadata = {
        "filename": pdf_path.name,
        "file_size": pdf_path.stat().st_size,
        "file_sha256": file_sha256,
        "pdf_version": versao_pdf,
        "page_count": doc.page_count,
        "title": meta_raw.get("title") or None,
        "author": meta_raw.get("author") or None,
        "creator": meta_raw.get("creator") or None,
        "producer": meta_raw.get("producer") or None,
        "extraction_date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "extractor_version": EXTRACTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
    }

    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. Processa página por página em streaming para document.jsonl
    jsonl_path = output_dir / "document.jsonl"
    registro_hashes_imagens: Dict[str, Dict[str, Any]] = {}
    contador_global_elementos = 0

    with open(jsonl_path, "w", encoding="utf-8") as f_jsonl:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_num = page_index + 1
            page_w = round(float(page.rect.width), 2)
            page_h = round(float(page.rect.height), 2)
            page_rot = int(page.rotation)

            contents_xrefs = page.get_contents()
            content_xref = contents_xrefs[0] if contents_xrefs else None

            raw_dict = page.get_text("dict")

            # Extrai vetores/formas primeiro para usar fios horizontais e verticais como barreiras de texto
            elementos_vetores_formas = _extrair_vetores_e_formas_da_pagina(
                page, page_w, page_h, content_xref
            )
            elementos_texto = _extrair_textos_da_pagina(
                raw_dict, page_w, page_h, content_xref, elementos_vetores_formas
            )
            elementos_imagens = _extrair_imagens_da_pagina(
                doc, page, page_w, page_h, content_xref, assets_dir, registro_hashes_imagens
            )
            elementos_links = _extrair_links_da_pagina(doc, page, page_w, page_h)

            # Fallback de OCR para imagens grandes rasterizadas sem texto nativo sobreposto (ex.: páginas 3 e 4)
            textos_ocr: List[Dict[str, Any]] = []
            for img_el in elementos_imagens:
                nb = img_el["normalized_bbox"]
                if nb["x"] >= 0.0 and nb["y"] >= 0.0 and (nb["width"] * nb["height"]) >= 0.35:
                    ib = img_el["bbox"]
                    rect_img = fitz.Rect(ib["x"], ib["y"], ib["x"] + ib["width"], ib["y"] + ib["height"])
                    textos_sobre_imagem = [
                        t for t in elementos_texto
                        if fitz.Rect(
                            t["bbox"]["x"],
                            t["bbox"]["y"],
                            t["bbox"]["x"] + t["bbox"]["width"],
                            t["bbox"]["y"] + t["bbox"]["height"],
                        ).intersects(rect_img)
                    ]
                    if not textos_sobre_imagem:
                        caminho_img_disco = output_dir / img_el["path"]
                        textos_ocr.extend(
                            _executar_ocr_em_imagem_pagina(
                                page, img_el, caminho_img_disco, page_w, page_h, content_xref
                            )
                        )

            elementos_pagina: List[Dict[str, Any]] = []
            elementos_pagina.extend(elementos_texto)
            elementos_pagina.extend(textos_ocr)
            elementos_pagina.extend(elementos_imagens)
            elementos_pagina.extend(elementos_vetores_formas)
            elementos_pagina.extend(elementos_links)

            # Atribui ID global único e extraction_order (1..N na página)
            for ordem_ext, elem in enumerate(elementos_pagina, start=1):
                contador_global_elementos += 1
                elem["id"] = f"el_{contador_global_elementos:06d}"
                elem["extraction_order"] = ordem_ext

            chaves_comuns = [
                "id", "type", "extraction_order",
                "bbox", "normalized_bbox", "source_id", "source",
                "extraction_method", "rotation", "transform", "parent_id",
            ]
            elementos_formatados = []
            for elem in elementos_pagina:
                ordenado = {k: elem[k] for k in chaves_comuns}
                for k, v in elem.items():
                    if k not in ordenado:
                        ordenado[k] = v
                elementos_formatados.append(ordenado)

            registro_pagina = {
                "page": page_num,
                "width": page_w,
                "height": page_h,
                "rotation": page_rot,
                "elements": elementos_formatados,
            }

            f_jsonl.write(json.dumps(registro_pagina, ensure_ascii=False) + "\n")

    doc.close()
    print(
        f"Extração concluída com sucesso!\n"
        f" - Páginas processadas: {metadata['page_count']}\n"
        f" - Elementos extraídos: {contador_global_elementos}\n"
        f" - Assets únicos salvos: {len(registro_hashes_imagens)}\n"
        f" - Diretório de saída: {output_dir}"
    )


# ==============================================================
# Extrai o conteúdo do PDF para um arquivo JSONL parametrizado
# ==============================================================

if __name__ == "__main__":
    from utils.caminhos import raiz

    pdf_path = raiz["dados_extraidos"]["pdf_original"]
    pdf_hash_path = raiz["dados_extraidos"]["pdf_original_hash"]
    diretorio_saida = raiz["dados_extraidos"]["conteudo_extraido"]["path"]
    hash_atual = pdf_hash_path.read_text(encoding="utf-8").strip()

    print(f"Iniciando extração bruta de '{pdf_path.name}'...")

    extrair_pdf_bruto(
        pdf_path = raiz["dados_extraidos"]["pdf_original"],
        output_dir = raiz["dados_extraidos"]["conteudo_extraido"]["path"],
        file_sha256 = raiz["dados_extraidos"]["pdf_original_hash"].read_text(encoding="utf-8").strip(),
    )
