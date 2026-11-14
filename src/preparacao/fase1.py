import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymupdf as fitz  # PyMuPDF

# APENAS os glifos que são comprovadamente marcadores de fim/continuação de texto
# (Não inclui outros caracteres da fonte VwNormSymboleA para não apagar ícones de botões/painel!)
MARCADORES_FLUXO_ISOLADOS = {
    "▸", "▪", "◁", "▷", "►", "◄", "◂", "▴", "▾", "◀", "▶", "△", "▽",
    "<", ">", "\uf0a3", "\uf0cc", "", "",
}

# Regex para remover apenas os marcadores de fluxo colados no final de um bloco de texto
REGEX_SUFIXO_FLUXO = re.compile(r"[\s\n]*[▸▪◁▷►◄◂◀▶\uf0a3\uf0cc<]+$")

# Regex para o código vertical da literatura técnica na margem esquerda (ex: 21B.5B1.NIV.66)
REGEX_CODIGO_LITERAL = re.compile(r"^\d{2}[A-Z]\.[0-9A-Z\.]+$")


# =====================================================================
# Funções Geométricas e Regras de Filtragem (Fase 1)
# =====================================================================

def _fracao_dentro_da_pagina(
    bbox: Dict[str, float], page_w: float, page_h: float, margem_borda: float = 0.5
) -> float:
    """
    Calcula a fração do elemento contida dentro da área útil da página [0, 0, page_w, page_h].
    Trata corretamente linhas 1D (width == 0 ou height == 0) usando sobreposição linear.
    """
    x0 = float(bbox["x"])
    y0 = float(bbox["y"])
    w = float(bbox["width"])
    h = float(bbox["height"])
    x1 = x0 + w
    y1 = y0 + h

    lim_x0, lim_y0 = margem_borda, margem_borda
    lim_x1, lim_y1 = page_w - margem_borda, page_h - margem_borda

    # Caso 1: Segmento de linha horizontal (altura ~ 0)
    if h <= 0.5 and w > 0.0:
        if not (lim_y0 <= y0 <= lim_y1):
            return 0.0
        inter_w = max(0.0, min(x1, page_w) - max(x0, 0.0))
        return inter_w / w

    # Caso 2: Segmento de linha vertical (largura ~ 0)
    if w <= 0.5 and h > 0.0:
        if not (lim_x0 <= x0 <= lim_x1):
            return 0.0
        inter_h = max(0.0, min(y1, page_h) - max(y0, 0.0))
        return inter_h / h

    # Caso 3: Ponto degenerado
    if w <= 0.5 and h <= 0.5:
        return 1.0 if (lim_x0 <= x0 <= lim_x1 and lim_y0 <= y0 <= lim_y1) else 0.0

    # Caso 4: Elemento 2D normal (texto, imagem, retângulo, vetor)
    inter_w = max(0.0, min(x1, page_w) - max(x0, 0.0))
    inter_h = max(0.0, min(y1, page_h) - max(y0, 0.0))
    area_inter = inter_w * inter_h
    area_total = w * h
    return (area_inter / area_total) if area_total > 0.0 else 0.0


def _recortar_bbox_na_pagina(
    bbox: Dict[str, float], page_w: float, page_h: float
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    Garante que elementos válidos sangrados (como a foto de capa) tenham seu bbox
    limitado às dimensões físicas da página [0, 0, page_w, page_h].
    """
    x0 = max(0.0, min(float(bbox["x"]), page_w))
    y0 = max(0.0, min(float(bbox["y"]), page_h))
    x1 = max(0.0, min(float(bbox["x"]) + float(bbox["width"]), page_w))
    y1 = max(0.0, min(float(bbox["y"]) + float(bbox["height"]), page_h))

    w = round(max(0.0, x1 - x0), 2)
    h = round(max(0.0, y1 - y0), 2)
    x0 = round(x0, 2)
    y0 = round(y0, 2)

    novo_bbox = {"x": x0, "y": y0, "width": w, "height": h}
    novo_norm = {
        "x": round(x0 / page_w, 6) if page_w else 0.0,
        "y": round(y0 / page_h, 6) if page_h else 0.0,
        "width": round(w / page_w, 6) if page_w else 0.0,
        "height": round(h / page_h, 6) if page_h else 0.0,
    }
    return novo_bbox, novo_norm


def _eh_vetor_seta_continuidade(el: Dict[str, Any]) -> bool:
    """
    Detecta as pequenas setas triangulares vetoriais de continuação/fim de seção (▸ ou ◁)
    que ficam isoladas na margem direita da coluna (x ~ 188..200 ou x ~ 385..402).
    """
    if el.get("type") != "vector":
        return False

    b = el["bbox"]
    w = float(b["width"])
    h = float(b["height"])
    x0 = float(b["x"])

    # As setinhas de fluxo têm entre 3pt e 7.5pt de largura/altura e ficam no fim das colunas
    if 2.8 <= w <= 7.5 and 3.5 <= h <= 8.5:
        na_margem_fim_coluna = (185.0 <= x0 <= 202.0) or (382.0 <= x0 <= 405.0)
        comandos = el.get("geometry", {}).get("commands", [])
        ops = [c.get("op") for c in comandos]
        # Triângulo simples: composto apenas por 3 ou 4 segmentos de linha ('l') sem curvas ('c')
        if na_margem_fim_coluna and 2 <= len(ops) <= 4 and all(op == "l" for op in ops):
            return True

    return False


def _avaliar_descarte_elemento(
    el: Dict[str, Any], page_w: float, page_h: float
) -> Optional[str]:
    """
    Avalia se um elemento deve ser removido na Fase 1.
    Retorna a string do motivo de remoção, ou None se o elemento deve ser mantido.
    """
    bbox = el["bbox"]
    el_type = el.get("type")
    y0 = float(bbox["y"])
    x0 = float(bbox["x"])

    # 1. Regra de Limites da Página (Crop Marks e Bleed externo < 5% de área/comprimento)
    fracao = _fracao_dentro_da_pagina(bbox, page_w, page_h)
    if fracao < 0.05:
        return "fora_da_pagina"

    # 2. Regra de Rodapé Repetitivo e Paginação (faixa inferior y >= 564 pt)
    if y0 >= 564.0:
        return "rodape_paginacao"

    # 3. Regra para setinhas vetoriais de fim/continuação de seção nas margens de coluna
    if _eh_vetor_seta_continuidade(el):
        return "marcador_fluxo"

    # 4. Regras específicas para elementos de texto
    if el_type == "text":
        conteudo = (el.get("content") or "").strip()
        rotacao = float(el.get("rotation") or 0.0)

        # 4a. Código Vertical da Literatura (ex: "21B.5B1.NIV.66" rotacionado a 270°)
        if abs(rotacao - 270.0) <= 5.0 or abs(rotacao - 90.0) <= 5.0:
            return "codigo_lateral"
        if x0 < 28.0 and REGEX_CODIGO_LITERAL.match(conteudo):
            return "codigo_lateral"

        # 4b. Marcadores Tipográficos de Controle de Fluxo isolados (exatamente na lista permitida)
        if conteudo in MARCADORES_FLUXO_ISOLADOS:
            return "marcador_fluxo"

    return None


# =====================================================================
# Processamento Principal da Fase 1 e Geração de Debug Visual
# =====================================================================

def executar_fase1_saneamento(
    jsonl_entrada: Path,
    jsonl_saida: Path,
) -> Tuple[List[Dict[str, Any]], Counter]:
    """
    Filtra os elementos ruidosos de `document.jsonl`, limpa sufixos de fluxo em textos
    válidos e grava `document_fase1.jsonl`.
    """
    jsonl_saida.parent.mkdir(parents=True, exist_ok=True)

    estatisticas = Counter()
    removidos_por_pagina: List[Dict[str, Any]] = []

    with open(jsonl_entrada, "r", encoding="utf-8") as f_in, open(
        jsonl_saida, "w", encoding="utf-8"
    ) as f_out:
        for linha in f_in:
            dados_pagina = json.loads(linha)
            p_num = dados_pagina["page"]
            page_w = float(dados_pagina["width"])
            page_h = float(dados_pagina["height"])

            elementos_mantidos = []
            elementos_removidos = []

            for el in dados_pagina.get("elements", []):
                estatisticas["total_lidos"] += 1
                motivo = _avaliar_descarte_elemento(el, page_w, page_h)

                if motivo is not None:
                    estatisticas[f"removido_{motivo}"] += 1
                    estatisticas["total_removidos"] += 1
                    el_rem = dict(el)
                    el_rem["removal_reason"] = motivo
                    elementos_removidos.append(el_rem)
                else:
                    # Se for texto válido, remove eventual marcador de fluxo colado ao final da frase
                    if el.get("type") == "text" and el.get("content"):
                        texto_original = el["content"]
                        texto_limpo = REGEX_SUFIXO_FLUXO.sub("", texto_original).strip()
                        if not texto_limpo:
                            estatisticas["removido_marcador_fluxo"] += 1
                            estatisticas["total_removidos"] += 1
                            el_rem = dict(el)
                            el_rem["removal_reason"] = "marcador_fluxo"
                            elementos_removidos.append(el_rem)
                            continue
                        el["content"] = texto_limpo

                    # Recorta bbox caso seja um elemento válido sangrado além da borda (ex: capa)
                    novo_bbox, novo_norm = _recortar_bbox_na_pagina(el["bbox"], page_w, page_h)
                    el["bbox"] = novo_bbox
                    el["normalized_bbox"] = novo_norm

                    estatisticas["total_mantidos"] += 1
                    elementos_mantidos.append(el)

            registro_limpo = {
                "page": p_num,
                "width": page_w,
                "height": page_h,
                "rotation": dados_pagina.get("rotation", 0),
                "elements": elementos_mantidos,
            }
            f_out.write(json.dumps(registro_limpo, ensure_ascii=False) + "\n")

            removidos_por_pagina.append({
                "page": p_num,
                "width": page_w,
                "height": page_h,
                "removed_elements": elementos_removidos,
            })

    # Relatório no terminal
    print("=" * 65)
    print("RELATÓRIO DA FASE 1 — SANEAMENTO ESPACIAL E FILTRAGEM DE RUÍDO")
    print("=" * 65)
    print(f"Total de elementos lidos    : {estatisticas['total_lidos']}")
    print(f"Total de elementos mantidos : {estatisticas['total_mantidos']}")
    print(f"Total de elementos removidos: {estatisticas['total_removidos']}")
    print("  ├─ Fora da página (Crop/Bleed) : "
          f"{estatisticas['removido_fora_da_pagina']}")
    print("  ├─ Rodapé e paginação          : "
          f"{estatisticas['removido_rodape_paginacao']}")
    print("  ├─ Código lateral (270°)       : "
          f"{estatisticas['removido_codigo_lateral']}")
    print("  └─ Marcadores de fluxo (▸/▪)   : "
          f"{estatisticas['removido_marcador_fluxo']}")
    print(f"Arquivo salvo em            : {jsonl_saida}")
    print("-" * 65)

    return removidos_por_pagina, estatisticas


def gerar_debug_fase1_pdf(
    pdf_original: Path,
    removidos_por_pagina: List[Dict[str, Any]],
    pdf_saida: Path,
    mostrar_fora_da_pagina: bool = False,
) -> Path:
    """
    Gera um PDF contendo todas as páginas originais (sem alterar o MediaBox/CropBox
    para manter o alinhamento exato das coordenadas!) demarcando os elementos removidos
    com caixas vermelhas tracejadas.
    """
    pdf_saida.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_original)

    siglas_motivo = {
        "fora_da_pagina": "OUT",
        "rodape_paginacao": "ROD",
        "codigo_lateral": "COD",
        "marcador_fluxo": "FLX",
    }
    cor_vermelha = (0.9, 0.05, 0.05)
    total_demarcados = 0

    for info_pag in removidos_por_pagina:
        p_num = info_pag["page"]
        removidos = info_pag["removed_elements"]

        page = doc[p_num - 1]
        shape_canvas = page.new_shape()
        desenhou = False

        for el in removidos:
            motivo = el.get("removal_reason", "")
            # Elementos "fora_da_pagina" ficam fora da área visível da página;
            # ignorá-los no desenho mantém o PDF limpo e focado no conteúdo interno.
            if motivo == "fora_da_pagina" and not mostrar_fora_da_pagina:
                continue

            b = el["bbox"]
            x0, y0 = float(b["x"]), float(b["y"])
            x1, y1 = x0 + float(b["width"]), y0 + float(b["height"])

            # Se for uma linha fina (ex: traço vertical do rodapé), aplica padding para formar caixa visível
            if (x1 - x0) < 2.0:
                x0 -= 1.5
                x1 += 1.5
            if (y1 - y0) < 2.0:
                y0 -= 1.5
                y1 += 1.5

            rect = fitz.Rect(x0, y0, x1, y1)

            # Caixa vermelha tracejada (3pt traço, 2pt espaço) exatamente sobre o elemento
            shape_canvas.draw_rect(rect)
            shape_canvas.finish(
                color=cor_vermelha,
                width=0.9,
                dashes="[3 2] 0",
                stroke_opacity=0.95,
            )

            sigla = siglas_motivo.get(motivo, "REM")
            ordem = el.get("extraction_order", "?")
            shape_canvas.insert_text(
                fitz.Point(rect.x0, max(6.0, rect.y0 - 1.5)),
                f"#{ordem}:{sigla}",
                fontsize=5.0,
                color=cor_vermelha,
            )
            desenhou = True
            total_demarcados += 1

        if desenhou:
            shape_canvas.commit()

    doc.save(str(pdf_saida), garbage=3, deflate=True)
    doc.close()

    print(
        f"🔍 PDF de debug da Fase 1 gerado com sucesso!\n"
        f" - Elementos internos demarcados em vermelho tracejado: {total_demarcados}\n"
        f" - Legenda de rótulos: ROD=Rodapé | COD=Código lateral | FLX=Fluxo\n"
        f" - Arquivo salvo em  : {pdf_saida}"
    )
    return pdf_saida


if __name__ == "__main__":
    from utils.caminhos import raiz

    pdf_original_path = raiz["dados_extraidos"]["pdf_original"]

    # Entrada: document.jsonl da extração bruta
    jsonl_bruto = raiz["dados_extraidos"]["conteudo_extraido"]["document"]

    # Saídas da Fase 1 direcionadas para a estrutura de 'dados_preparados'
    jsonl_fase1 = raiz["dados_preparados"]["document_fase1"]
    pdf_debug_fase1 = raiz["dados_preparados"]["debug_visual"]["fase1_removidos"]

    removidos, _stats = executar_fase1_saneamento(
        jsonl_entrada=jsonl_bruto,
        jsonl_saida=jsonl_fase1,
    )
    gerar_debug_fase1_pdf(
        pdf_original=pdf_original_path,
        removidos_por_pagina=removidos,
        pdf_saida=pdf_debug_fase1,
    )
