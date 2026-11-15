#!/usr/bin/env python3
"""
Fase 3 — reconstrução semântica do manual técnico.

Entrada principal: document_fase2.jsonl
Entradas auxiliares: descricao_images.json, descricao_icones.json, icons_catalog.json
Saída: document_preparado.jsonl

Dependências:
    pip install pymupdf

O algoritmo usa as caixas delimitadoras e formas da Fase 2. A geometria é
usada internamente e não é copiada para os registros finais.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import fitz
except ImportError:
    fitz = None


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

COLUNA_X = 210.0
LARGURA_TOTAL_MIN = 210.0
TOL = 2.0
ALERTA_LABELS = ("PERIGO", "ATENÇÃO", "CUIDADO", "NOTA", "AMBIENTAL", "INFO")
ALERTA_RE = re.compile(r"^\s*(PERIGO|ATENÇÃO|CUIDADO|NOTA|AMBIENTAL|INFO)\b", re.I)
ITEM_RE = re.compile(r"^\s*(?:[•●▪◦‣⁃]|\d+[.)])\s*(.+)$", re.S)
FIG_RE = re.compile(r"\bFig\.?\s*(\d+)\b", re.I)
PAGINA_RE = re.compile(r"(?:página|pág\.?)\s*(\d+)", re.I)
REF_ALERTA_RE = re.compile(
    r"(?:observe|ver|consulte|veja).{0,100}(?:símbolo|alerta|aviso|referência).{0,100}"
    r"(?:página|pág\.?)\s*(\d+)", re.I | re.S
)

TIPOS_VALIDOS = {"heading", "paragraph", "list", "alert_box", "table", "image", "icon"}
CORES_DEBUG = {
    "heading": (0.05, 0.25, 0.9), "paragraph": (0.1, 0.55, 0.2),
    "list": (0.55, 0.2, 0.75), "alert_box": (0.9, 0.1, 0.1),
    "table": (0.95, 0.5, 0.05), "image": (0.0, 0.55, 0.7),
    "icon": (0.25, 0.25, 0.25),
}


# ---------------------------------------------------------------------------
# I/O e geometria
# ---------------------------------------------------------------------------

def ler_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def ler_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def gravar_jsonl(path: Path, registros: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in registros:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def texto(e: dict) -> str:
    return re.sub(r"\s+", " ", str(e.get("content") or "")).strip()


def box(e: dict) -> tuple[float, float, float, float]:
    b = e.get("bbox") or {}
    x, y = float(b.get("x", 0)), float(b.get("y", 0))
    return x, y, x + float(b.get("width", 0)), y + float(b.get("height", 0))


def largura(e: dict) -> float:
    x0, _, x1, _ = box(e)
    return max(0.0, x1 - x0)


def altura(e: dict) -> float:
    _, y0, _, y1 = box(e)
    return max(0.0, y1 - y0)


def centro(e: dict) -> tuple[float, float]:
    x0, y0, x1, y1 = box(e)
    return (x0 + x1) / 2, (y0 + y1) / 2


def area_intersecao(a: tuple, b: tuple) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0, x1-x0) * max(0, y1-y0)


def contem(outer: tuple, inner: tuple, margem: float = 2.0) -> bool:
    return (inner[0] >= outer[0]-margem and inner[1] >= outer[1]-margem
            and inner[2] <= outer[2]+margem and inner[3] <= outer[3]+margem)


def fonte_cor(e: dict) -> list[float]:
    return (e.get("color") or {}).get("components") or []


def cor_shape(e: dict) -> list[float]:
    c = e.get("fill_color") or e.get("stroke_color") or {}
    return c.get("components") or []


def source_ids(e: dict) -> list[str]:
    if e.get("type") == "icon":
        return list(e.get("source_element_ids") or [])
    return [e["id"]] if e.get("id") else []


def dedup(seq: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(x for x in seq if x))


def elemento_por_id(paginas: list[dict]) -> tuple[dict, dict]:
    elementos, paginas_por_id = {}, {}
    for p in paginas:
        for e in p.get("elements", []):
            elementos[e["id"]] = e
            paginas_por_id[e["id"]] = int(p["page"])
    return elementos, paginas_por_id


# ---------------------------------------------------------------------------
# Ordem de leitura por zonas e colunas
# ---------------------------------------------------------------------------

def ordenar_elementos_pagina(p: dict) -> list[dict]:
    """
    Títulos/figuras que atravessam a largura dividem a página em faixas.
    Dentro de cada faixa, lê-se coluna esquerda e depois coluna direita.
    Elementos sobrepostos na mesma linha são ordenados por x.
    """
    elementos = [e for e in p.get("elements", [])
                 if e.get("type") in {"text", "image", "icon"}]
    elementos.sort(key=lambda e: (box(e)[1], box(e)[0]))

    full = [e for e in elementos if largura(e) >= LARGURA_TOTAL_MIN]
    # Uma âncora larga divide o fluxo apenas quando não é uma imagem de página inteira.
    limites = [0.0]
    for e in full:
        if e.get("type") == "image" and largura(e) > float(p["width"]) * .9:
            continue
        y = centro(e)[1]
        if all(abs(y-v) > 4 for v in limites):
            limites.append(y)
    limites = sorted(limites) + [float(p["height"]) + 1]
    saida, vistos = [], set()

    for y0, y1 in zip(limites, limites[1:]):
        faixa = [e for e in elementos if e["id"] not in vistos
                 and y0 <= centro(e)[1] < y1]
        # Itens que atravessam as duas colunas são marcos de fluxo.
        largos = [e for e in faixa if largura(e) >= LARGURA_TOTAL_MIN]
        for e in sorted(largos, key=lambda z: (box(z)[1], box(z)[0])):
            saida.append(e); vistos.add(e["id"])
        restantes = [e for e in faixa if e["id"] not in vistos]
        esquerda = [e for e in restantes if centro(e)[0] < COLUNA_X]
        direita = [e for e in restantes if centro(e)[0] >= COLUNA_X]
        for coluna in (esquerda, direita):
            coluna.sort(key=lambda z: (box(z)[1], box(z)[0]))
            for e in coluna:
                saida.append(e); vistos.add(e["id"])

    for e in elementos:
        if e["id"] not in vistos:
            saida.append(e)
    return saida


# ---------------------------------------------------------------------------
# Hierarquia tipográfica
# ---------------------------------------------------------------------------

def nivel_titulo(e: dict) -> int | None:
    if e.get("type") != "text":
        return None
    font = (e.get("font") or "").lower()
    size = float(e.get("font_size") or 0)
    color = fonte_cor(e)
    azul = len(color) >= 3 and color[2] > 130 and color[0] < 40 and color[1] < 150
    content = texto(e)
    if not content:
        return None

    if "vwhead-bold" in font and size >= 13 and azul:
        return 1
    if "vwhead-bold" in font and 10.5 <= size <= 12.5:
        return 2
    if "vwhead-bold" in font and 8.5 <= size <= 10 and azul:
        return 3
    if "vwtext-bold" in font and 7.3 <= size <= 8.2:
        return 4
    return None


# ---------------------------------------------------------------------------
# Ícones inline e elementos de origem
# ---------------------------------------------------------------------------

def marcar_icones_inline(conteudo: str, elemento: dict, icones: list[dict]) -> tuple[str, list[str]]:
    """
    Insere tokens de ícones inline por posição horizontal aproximada.
    Não insere um ícone standalone como texto: ele será anexado à célula,
    alerta ou emitido como bloco.
    """
    x0, y0, x1, y1 = box(elemento)
    candidatos = []
    for ic in icones:
        if ic.get("classification") != "inline":
            continue
        ix0, iy0, ix1, iy1 = box(ic)
        cy = (iy0 + iy1) / 2
        if y0 - 2 <= cy <= y1 + 2 and ix1 >= x0 - 2 and ix0 <= x1 + 2:
            icon_id = ic.get("icon_id")
            if icon_id:
                candidatos.append((ix0, icon_id))
    resultado = conteudo
    ids = []
    largura_texto = max(1.0, x1-x0)
    # Inserções da direita para a esquerda preservam os índices originais.
    for x, icon_id in sorted(candidatos, reverse=True):
        pos = max(0, min(len(resultado), round((x-x0)/largura_texto*len(resultado))))
        resultado = resultado[:pos] + f"<icon:{icon_id}>" + resultado[pos:]
        ids.append(icon_id)
    return resultado, dedup(ids)


# ---------------------------------------------------------------------------
# Reconstrução de tabelas por células retangulares e linhas de grade
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    rect: tuple[float, float, float, float]
    source_id: str


def retangulos_celula(p: dict) -> list[Cell]:
    cells = []
    for s in p.get("elements", []):
        if s.get("type") != "shape" or s.get("shape_type") != "rectangle":
            continue
        b = box(s)
        if b[2]-b[0] >= 18 and b[3]-b[1] >= 8:
            cells.append(Cell(b, s["id"]))
    return cells


def agrupar_componentes_celulas(cells: list[Cell]) -> list[list[Cell]]:
    """Componentes de retângulos que compartilham uma borda, com tolerância."""
    n = len(cells)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    for i in range(n):
        a = cells[i].rect
        for j in range(i+1, n):
            b = cells[j].rect
            vertical_touch = (abs(a[2]-b[0]) <= TOL or abs(b[2]-a[0]) <= TOL)
            vertical_overlap = min(a[3], b[3])-max(a[1], b[1]) > 4
            horizontal_touch = (abs(a[3]-b[1]) <= TOL or abs(b[3]-a[1]) <= TOL)
            horizontal_overlap = min(a[2], b[2])-max(a[0], b[0]) > 4
            if (vertical_touch and vertical_overlap) or (horizontal_touch and horizontal_overlap):
                union(i, j)

    groups = defaultdict(list)
    for i, cell in enumerate(cells):
        groups[find(i)].append(cell)
    return list(groups.values())


def detectar_tabelas_pagina(p: dict) -> list[dict]:
    """Detecta tabelas por células retangulares e por grades de linhas.

    A Fase 2 frequentemente representa tabelas como segmentos de linha, sem
    retângulos de célula. Os segmentos horizontais contíguos são unidos por
    coordenada Y e agrupados por sobreposição horizontal entre linhas.
    """
    candidatas: list[dict] = []

    # 1) Grades de células retangulares (mantém compatibilidade com o método original).
    for grupo in agrupar_componentes_celulas(retangulos_celula(p)):
        if len(grupo) < 4:
            continue
        x0 = min(c.rect[0] for c in grupo); y0 = min(c.rect[1] for c in grupo)
        x1 = max(c.rect[2] for c in grupo); y1 = max(c.rect[3] for c in grupo)
        if x1-x0 < 65 or y1-y0 < 35:
            continue
        candidatas.append({
            "bbox": (x0, y0, x1, y1),
            "cells": sorted(grupo, key=lambda c: (c.rect[1], c.rect[0])),
            "source_shape_ids": [c.source_id for c in grupo],
        })

    # 2) Grade baseada em linhas. Agrupa segmentos horizontais na mesma altura.
    linhas = []
    for sh in p.get("elements", []):
        if sh.get("type") != "shape" or sh.get("shape_type") != "line":
            continue
        x0, y0, x1, y1 = box(sh)
        if abs(y1-y0) <= 1.5 and x1-x0 >= 10:
            linhas.append(((y0+y1)/2, x0, x1, sh["id"]))

    linhas.sort(key=lambda z: (z[0], z[1]))
    por_y: list[list[tuple]] = []
    for ln in linhas:
        if not por_y or abs(ln[0] - sum(x[0] for x in por_y[-1])/len(por_y[-1])) > 2.2:
            por_y.append([ln])
        else:
            por_y[-1].append(ln)

    linhas_grade = []
    for grupo_y in por_y:
        segs = sorted([(x[1], x[2], x[3]) for x in grupo_y])
        unidos = []
        for a, b, sid in segs:
            if unidos and a <= unidos[-1][1] + 3:
                unidos[-1][1] = max(unidos[-1][1], b)
                unidos[-1][2].append(sid)
            else:
                unidos.append([a, b, [sid]])
        for a, b, ids in unidos:
            if b-a >= 55:
                linhas_grade.append({"y": sum(x[0] for x in grupo_y)/len(grupo_y),
                                     "x0": a, "x1": b, "ids": ids})

    grupos_grade: list[list[dict]] = []
    for ln in sorted(linhas_grade, key=lambda z: z["y"]):
        candidatos_grupo = []
        for g in grupos_grade:
            ultimo = g[-1]
            inter = max(0, min(ln["x1"], ultimo["x1"]) - max(ln["x0"], ultimo["x0"]))
            menor = max(1, min(ln["x1"]-ln["x0"], ultimo["x1"]-ultimo["x0"]))
            if ln["y"] - ultimo["y"] <= 85 and inter/menor >= 0.55:
                candidatos_grupo.append(g)
        if candidatos_grupo:
            g = max(candidatos_grupo, key=lambda z: max(0, min(ln["x1"], z[-1]["x1"])-max(ln["x0"], z[-1]["x0"])))
            g.append(ln)
        else:
            grupos_grade.append([ln])

    for g in grupos_grade:
        ys = sorted({round(z["y"], 1) for z in g})
        if len(ys) < 3:
            continue
        x0 = min(z["x0"] for z in g); x1 = max(z["x1"] for z in g)
        y0, y1 = min(ys), max(ys)
        if x1-x0 < 65 or y1-y0 < 30:
            continue
        ids = dedup(sid for z in g for sid in z["ids"])
        candidatas.append({"bbox": (x0, y0, x1, y1), "cells": [],
                           "source_shape_ids": ids})

    finais = []
    for t in sorted(candidatas, key=lambda z: (z["bbox"][1], z["bbox"][0],
                         -((z["bbox"][2]-z["bbox"][0])*(z["bbox"][3]-z["bbox"][1])))):
        if any(contem(f["bbox"], t["bbox"], 3) for f in finais):
            continue
        finais.append(t)
    return finais


def texto_na_celula(e: dict, cell: Cell) -> bool:
    return area_intersecao(box(e), cell.rect) > 0


def montar_markdown_tabela(tabela: dict, elementos: list[dict]) -> tuple[str, list[str], list[str]]:
    """Reconstrói células por bandas horizontais e limites de coluna."""
    x0, y0, x1, y1 = tabela["bbox"]
    shapes = [e for e in elementos if e.get("type") == "shape"]
    horizontais = []
    verticais = []
    for sh in shapes:
        if sh.get("shape_type") != "line":
            continue
        sx0, sy0, sx1, sy1 = box(sh)
        if sy1-sy0 <= 1.5 and sx1-sx0 >= 18 and sy0 >= y0-3 and sy0 <= y1+3:
            if sx1 >= x0-3 and sx0 <= x1+3:
                horizontais.append((sy0+sy1)/2)
        elif sx1-sx0 <= 1.5 and sy1-sy0 >= 10 and sx0 >= x0-3 and sx0 <= x1+3:
            if sy1 >= y0-3 and sy0 <= y1+3:
                verticais.append((sx0+sx1)/2)

    # Limites também podem estar registrados como bordas de retângulos.
    # A Fase 2 usa retângulos preenchidos para representar células sem linhas verticais.
    rects = [c.rect for c in tabela.get("cells", [])]
    rects.extend(box(sh) for sh in shapes
                 if sh.get("shape_type") == "rectangle"
                 and area_intersecao(box(sh), (x0-3, y0-3, x1+3, y1+3)) > 0)
    for a, b, d, f in rects:
        if y0-3 <= b <= y1+3: horizontais.append(b)
        if y0-3 <= f <= y1+3: horizontais.append(f)
        if x0-3 <= a <= x1+3: verticais.append(a)
        if x0-3 <= d <= x1+3: verticais.append(d)

    def cluster(vals, tol=2.2):
        vals = sorted(vals)
        out = []
        for v in vals:
            if not out or abs(v-out[-1][-1]) > tol:
                out.append([v])
            else:
                out[-1].append(v)
        return [sum(g)/len(g) for g in out]

    ys = cluster([y0, y1] + horizontais)
    xs = cluster([x0, x1] + verticais)
    # Uma tabela de duas colunas pode ter uma única divisória vertical detectada.
    if len(xs) < 3:
        col_edges = sorted({round(c.rect[0], 1) for c in tabela["cells"]} |
                           {round(c.rect[2], 1) for c in tabela["cells"]})
        if len(col_edges) >= 2:
            interior = [v for v in col_edges if x0+3 < v < x1-3]
            xs = cluster([x0] + interior + [x1])
    if len(xs) < 3 or len(ys) < 3:
        return "", [], []

    # Linhas muito próximas são ruído de duplicação da extração.
    rows = []
    for ya, yb in zip(ys, ys[1:]):
        if yb-ya >= 5:
            rows.append((ya, yb))
    cols = [(xa, xb) for xa, xb in zip(xs, xs[1:]) if xb-xa >= 12]
    if len(rows) < 2 or len(cols) < 2:
        return "", [], []

    data, source_ids, icon_ids = [], list(tabela["source_shape_ids"]), []
    for ri, (ra, rb) in enumerate(rows):
        row = []
        for ca, cb in cols:
            cell_box = (ca, ra, cb, rb)
            inside = []
            for e in elementos:
                if e.get("type") not in {"text", "icon"}:
                    continue
                eb = box(e)
                cx, cy = centro(e)
                if ca-1 <= cx <= cb+1 and ra-1 <= cy <= rb+1:
                    inside.append(e)
            inside.sort(key=lambda e: (box(e)[1], box(e)[0]))
            parts = []
            for e in inside:
                if e["type"] == "text":
                    parts.append(texto(e))
                    source_ids.extend(source_ids_fn(e))
                elif e.get("icon_id"):
                    parts.append(f"<icon:{e['icon_id']}>")
                    icon_ids.append(e["icon_id"])
                    source_ids.extend(source_ids_fn(e))
            row.append(re.sub(r"\s+", " ", " ".join(x for x in parts if x)).strip())
        data.append(row)

    ncols = len(cols)
    if not data or not any(any(v for v in row) for row in data):
        return "", [], []
    # A primeira linha geométrica é o cabeçalho. Linhas sem conteúdo são preservadas
    # somente quando há conteúdo em linhas vizinhas (células mescladas/ícones).
    md = ["| " + " | ".join(data[0]) + " |", "| " + " | ".join(["---"]*ncols) + " |"]
    md.extend("| " + " | ".join(row) + " |" for row in data[1:])
    return "\n".join(md), dedup(icon_ids), dedup(source_ids)


def source_ids_fn(e: dict) -> list[str]:
    return source_ids(e)


# ---------------------------------------------------------------------------
# Detecção e reconstrução de alertas
# ---------------------------------------------------------------------------

def label_alerta(e: dict) -> str | None:
    m = ALERTA_RE.match(texto(e))
    return m.group(1).upper() if m else None


def shape_fill(e: dict) -> list[float]:
    return (e.get("fill_color") or {}).get("components") or []


def cor_cabecalho_alerta(s: dict) -> bool:
    c = shape_fill(s)
    if len(c) < 3:
        return False
    r, g, b = c[:3]
    # Faixa cromática saturada; exclui cinzas e fundos neutros.
    return max(r, g, b)-min(r, g, b) > 55 and max(r, g, b) > 130


def detectar_alertas_pagina(p: dict) -> list[dict]:
    """
    Estratégia primária: rótulo textual + faixa de cabeçalho colorida + corpo
    alinhado geometricamente. Fallback: rótulo textual com agrupamento espacial
    limitado até o próximo título/alerta ou mudança grande de posição.
    """
    els = p.get("elements", [])
    textos = [e for e in els if e.get("type") == "text"]
    icones = [e for e in els if e.get("type") == "icon"]
    shapes = [e for e in els if e.get("type") == "shape"]
    resultados = []

    for head in textos:
        level = label_alerta(head)
        if not level:
            continue
        hb = box(head)
        hx0, hy0, hx1, hy1 = hb

        # Faixa colorida na qual o rótulo está contido ou sobreposto.
        bandas = [s for s in shapes if s.get("shape_type") == "rectangle"
                  and cor_cabecalho_alerta(s)
                  and contem(box(s), hb, 5)]
        bandas.sort(key=lambda s: area_intersecao(box(s), hb), reverse=True)
        banda = bandas[0] if bandas else None

        quadro = None
        if banda:
            bb = box(banda)
            # Corpo: retângulo neutro ou forma com mesmo eixo x, começando após o cabeçalho.
            corpos = [s for s in shapes if s is not banda
                      and s.get("shape_type") == "rectangle"
                      and abs(box(s)[0]-bb[0]) <= 4
                      and abs(box(s)[2]-bb[2]) <= 4
                      and box(s)[1] <= bb[3]+5
                      and box(s)[3] > bb[3]+5
                      and (not cor_cabecalho_alerta(s))]
            if corpos:
                corpo = max(corpos, key=lambda s: box(s)[3])
                cb = box(corpo)
                quadro = (min(bb[0], cb[0]), min(bb[1], cb[1]),
                          max(bb[2], cb[2]), max(bb[3], cb[3]))
            else:
                # Muitos alertas têm apenas a faixa colorida e uma linha horizontal
                # inferior, sem retângulo de fundo. Usa essa linha como limite.
                linhas_fechamento = []
                for sh in shapes:
                    if sh.get("shape_type") != "line":
                        continue
                    sb = box(sh)
                    if (abs(sb[3]-sb[1]) <= 1.5 and sb[1] > bb[3]
                            and sb[1]-bb[3] <= 85
                            and sb[0] <= bb[0]+4 and sb[2] >= bb[2]-4):
                        linhas_fechamento.append(sb[1])
                limite = min(linhas_fechamento) if linhas_fechamento else min(float(p["height"]), bb[3]+55)
                quadro = (bb[0], bb[1], bb[2], limite)

        if quadro is None:
            # Fallback conservador: janela vertical da caixa textual ao redor do rótulo.
            # Limita largura à coluna e altura a 105 pt para não absorver texto distante.
            coluna = 0 if (hx0+hx1)/2 < COLUNA_X else 1
            x0 = max(0, hx0-22); x1 = min(float(p["width"]), hx1+150)
            if coluna == 0:
                x1 = min(x1, COLUNA_X-4)
            else:
                x0 = max(x0, COLUNA_X+4)
            quadro = (x0, hy0-5, x1, min(float(p["height"]), hy1+105))

        # Textos pertencentes ao alerta: dentro do quadro e abaixo/próximos ao cabeçalho.
        membros = []
        for e in textos:
            if e["id"] == head["id"]:
                continue
            eb = box(e)
            if contem(quadro, eb, 12) and eb[1] >= hy0-2:
                # O conteúdo do cabeçalho pode ser um rótulo independente; próximos títulos não entram.
                if label_alerta(e):
                    continue
                if nivel_titulo(e):
                    continue
                membros.append(e)
        membros.sort(key=lambda e: (box(e)[1], box(e)[0]))

        # Ícone mais próximo do rótulo, desde que esteja dentro do quadro.
        icons_inside = [i for i in icones if contem(quadro, box(i), 3)]
        icons_inside.sort(key=lambda i: math.dist(centro(i), centro(head)))
        icon_id = icons_inside[0].get("icon_id") if icons_inside else None

        source = source_ids(head)
        source.extend(source_ids(banda) if banda else [])
        for e in membros:
            source.extend(source_ids(e))
        for i in icons_inside:
            source.extend(source_ids(i))

        resultados.append({
            "page": int(p["page"]), "label": level, "header": head,
            "members": membros, "icons": icons_inside, "icon_id": icon_id,
            "bbox": quadro, "shape_ids": [s["id"] for s in shapes if contem(quadro, box(s), 1)],
            "source_ids": dedup(source),
            "confidence": "geometric" if banda else "fallback",
        })
    return resultados


def classificar_escopo(alerta: dict, section_stack: list[dict]) -> tuple[str, list[str]]:
    """
    Alerta logo após título de nível 1/2 e antes de conteúdo substantivo:
    chapter_intro. Nos demais casos: local_topic.
    """
    path = [h["content"] for h in section_stack]
    parent = section_stack[-1]["id"] if section_stack else None
    y = box(alerta["header"])[1]
    ultimo = section_stack[-1] if section_stack else None
    if ultimo and ultimo["level"] <= 2 and y < 150:
        return "chapter_intro", [ultimo["id"]]
    return "local_topic", [parent] if parent else []


# ---------------------------------------------------------------------------
# Construção de unidades semânticas
# ---------------------------------------------------------------------------

def construir_registros(paginas: list[dict], descr_images: dict,
                        descr_icones: dict) -> tuple[list[dict], dict]:
    registros = []
    pilha: list[dict] = []
    contador = 0
    page_orders: dict[int, int] = {}
    alert_map: dict[int, list[dict]] = {}

    for p in paginas:
        num = int(p["page"])
        ordem = ordenar_elementos_pagina(p)
        alertas = detectar_alertas_pagina(p)
        alert_map[num] = alertas
        alert_by_head = {a["header"]["id"]: a for a in alertas}
        alert_member_ids = {e["id"] for a in alertas for e in a["members"]}
        alert_icon_ids = {i["id"] for a in alertas for i in a["icons"]}
        icones = [e for e in p.get("elements", []) if e.get("type") == "icon"]
        tabelas = detectar_tabelas_pagina(p)
        table_ids = set()
        table_records = []

        # Cria registros de tabelas antes do fluxo textual; a posição determina a ordem.
        for t in tabelas:
            md, inline, src = montar_markdown_tabela(t, p.get("elements", []))
            if not md:
                continue
            x0, y0, x1, y1 = t["bbox"]
            table_ids.update(src)
            table_records.append({
                "_bbox": t["bbox"], "type": "table", "markdown": md,
                "inline_icons": inline, "source_element_ids": src,
                "page_start": num, "page_end": num,
            })

        itens = []
        for e in ordem:
            if e["id"] in alert_member_ids or e["id"] in alert_icon_ids:
                continue
            if any(contem(t["_bbox"], box(e), 2) for t in table_records):
                continue
            if e["type"] == "text":
                content = texto(e)
                if not content:
                    continue
                if e["id"] in alert_by_head:
                    a = alert_by_head[e["id"]]
                    scope, refs = classificar_escopo(a, pilha)
                    items = []
                    lead_parts = []
                    for m in a["members"]:
                        mt = texto(m)
                        if not mt:
                            continue
                        match = ITEM_RE.match(mt)
                        if match:
                            items.append(match.group(1).strip())
                        else:
                            lead_parts.append(mt)
                    lead = " ".join(lead_parts).strip()
                    if not lead:
                        lead = content
                    record = {
                        "type": "alert_box", "alert_level": a["label"],
                        "alert_scope": scope, "icon_id": a["icon_id"],
                        "refers_to_ids": refs, "lead_text": lead,
                        "items": items, "inline_icons": [],
                        "page_start": num, "page_end": num,
                        "source_element_ids": a["source_ids"],
                        "_bbox": a["bbox"], "_confidence": a["confidence"],
                    }
                    # Ícones secundários do quadro continuam rastreáveis.
                    record["inline_icons"] = dedup(
                        [i.get("icon_id") for i in a["icons"] if i.get("icon_id") and i.get("icon_id") != a["icon_id"]]
                    )
                    itens.append(record)
                    continue

                lvl = nivel_titulo(e)
                if lvl:
                    while pilha and pilha[-1]["level"] >= lvl:
                        pilha.pop()
                    parent = pilha[-1]["id"] if pilha else None
                    contador += 1
                    rec = {
                        "id": f"blk_{contador:06d}", "reading_order": contador,
                        "read_order": len(itens)+1, "type": "heading", "level": lvl,
                        "page_start": num, "page_end": num,
                        "section_path": [h["content"] for h in pilha],
                        "parent_section_id": parent, "content": content,
                        "inline_icons": [], "referenced_alerts": [],
                        "source_element_ids": source_ids(e), "_bbox": box(e),
                    }
                    itens.append(rec)
                    pilha.append({"id": rec["id"], "content": content, "level": lvl})
                else:
                    inline, icons = marcar_icones_inline(content, e, icones)
                    itens.append({
                        "type": "paragraph", "content": inline,
                        "inline_icons": icons, "source_element_ids": source_ids(e),
                        "page_start": num, "page_end": num, "_bbox": box(e),
                    })

            elif e["type"] == "image":
                asset = e.get("asset_id")
                m = re.search(r"(\d+)$", asset or "")
                image_num = int(m.group(1)) if m else None
                itens.append({
                    "type": "image", "id_image": image_num,
                    "asset_id": asset, "image_path": e.get("path"),
                    "figure_label": None, "caption": None,
                    "description": descr_images.get(str(image_num), "") if image_num else "",
                    "legend_items": [], "inline_icons": [],
                    "source_element_ids": source_ids(e),
                    "page_start": num, "page_end": num, "_bbox": box(e),
                })

            elif e["type"] == "icon" and e.get("classification") == "standalone":
                itens.append({
                    "type": "icon", "icon_id": e.get("icon_id"),
                    "icon_order_id": e.get("icon_order_id"),
                    "image_path": f"assets/icons/{e.get('icon_id')}.png" if e.get("icon_id") else None,
                    "description": descr_icones.get(str(int(re.search(r"(\d+)$", str(e.get("icon_id", ""))).group(1)))
                                          if re.search(r"(\d+)$", str(e.get("icon_id", ""))) else "", ""),
                    "inline_icons": [], "source_element_ids": source_ids(e),
                    "page_start": num, "page_end": num, "_bbox": box(e),
                })

        itens.extend(table_records)
        itens.sort(key=lambda r: (r.get("_bbox", (0,0,0,0))[1], r.get("_bbox", (0,0,0,0))[0]))
        # Atualiza contexto em blocos semânticos não heading.
        for local_order, r in enumerate(itens, 1):
            if r["type"] == "heading":
                # O heading já recebeu o caminho anterior, conforme convenção do modelo.
                pass
            else:
                r.setdefault("section_path", [h["content"] for h in pilha])
                r.setdefault("parent_section_id", pilha[-1]["id"] if pilha else None)
            r["read_order"] = local_order
            registros.append(r)

    # Reatribui IDs e ordem após agrupamento de tabelas/alertas.
    for i, r in enumerate(registros, 1):
        r["id"] = f"blk_{i:06d}"
        r["reading_order"] = i

    # Recalcula parent_section_id / section_path com IDs finais.
    stack = []
    for r in registros:
        if r["type"] == "heading":
            lvl = r["level"]
            while stack and stack[-1]["level"] >= lvl:
                stack.pop()
            r["section_path"] = [x["content"] for x in stack]
            r["parent_section_id"] = stack[-1]["id"] if stack else None
            stack.append({"id": r["id"], "content": r["content"], "level": lvl})
        else:
            r["section_path"] = [x["content"] for x in stack]
            r["parent_section_id"] = stack[-1]["id"] if stack else None

    return registros, alert_map


# ---------------------------------------------------------------------------
# Costura de parágrafos, listas e imagens
# ---------------------------------------------------------------------------

def costurar_paragrafos(registros: list[dict]) -> list[dict]:
    saida = []
    for r in registros:
        if saida:
            prev = saida[-1]
            if (prev["type"] == r["type"] == "paragraph"
                    and prev.get("section_path") == r.get("section_path")
                    and prev["page_end"] == r["page_start"]
                    and prev.get("page_start") == r.get("page_start")
                    and prev.get("_column") == r.get("_column")):
                pass
            # Costura somente quebra explícita por hífen ou fragmento sem pontuação
            # em páginas adjacentes; não une parágrafos comuns da mesma página.
            if (prev["type"] == r["type"] == "paragraph"
                    and prev.get("section_path") == r.get("section_path")
                    and r["page_start"] == prev["page_end"] + 1):
                a, b = prev["content"].rstrip(), r["content"].lstrip()
                if a.endswith("-") or (a and not a.endswith((".", "!", "?", ":"))):
                    prev["content"] = a[:-1] + b if a.endswith("-") else a + " " + b
                    prev["page_end"] = r["page_end"]
                    prev["source_element_ids"] = dedup(prev["source_element_ids"] + r["source_element_ids"])
                    prev["inline_icons"] = dedup(prev["inline_icons"] + r["inline_icons"])
                    continue
        saida.append(r)
    return saida


def unificar_tabelas_continuadas(registros: list[dict]) -> list[dict]:
    """Une tabelas de páginas consecutivas quando cabeçalho e coluna coincidem."""
    removidos = set()
    for i, a in enumerate(registros):
        if a.get("type") != "table" or i in removidos:
            continue
        box_a = a.get("_bbox")
        if not box_a:
            continue
        header_a = (a.get("markdown", "").splitlines() or [""])[0]
        for j in range(i+1, len(registros)):
            b = registros[j]
            if b.get("type") != "table" or j in removidos:
                continue
            if b.get("page_start") != a.get("page_end", a.get("page_start")) + 1:
                continue
            box_b = b.get("_bbox")
            header_b = (b.get("markdown", "").splitlines() or [""])[0]
            mesma_coluna = box_b and abs(box_a[0]-box_b[0]) <= 12
            if mesma_coluna and header_a == header_b:
                lines_a = a["markdown"].splitlines()
                lines_b = b["markdown"].splitlines()
                a["markdown"] = "\n".join(lines_a + lines_b[2:])
                a["page_end"] = b["page_end"]
                a["source_element_ids"] = dedup(a["source_element_ids"] + b["source_element_ids"])
                a["inline_icons"] = dedup(a["inline_icons"] + b["inline_icons"])
                a["_bbox"] = (min(box_a[0], box_b[0]), min(box_a[1], box_b[1]),
                              max(box_a[2], box_b[2]), max(box_a[3], box_b[3]))
                removidos.add(j)
                break
    return [r for i, r in enumerate(registros) if i not in removidos]


def converter_listas(registros: list[dict]) -> list[dict]:
    """Agrupa marcadores explícitos consecutivos; não converte linhas sem marcador."""
    saida, i = [], 0
    while i < len(registros):
        r = registros[i]
        if r["type"] != "paragraph" or not ITEM_RE.match(r.get("content", "")):
            saida.append(r); i += 1; continue
        grupo, fontes, inline = [], [], []
        base = r
        while i < len(registros):
            atual = registros[i]
            if atual["type"] != "paragraph" or not ITEM_RE.match(atual.get("content", "")):
                break
            m = ITEM_RE.match(atual["content"])
            grupo.append(m.group(1).strip())
            fontes.extend(atual["source_element_ids"])
            inline.extend(atual["inline_icons"])
            i += 1
        novo = dict(base)
        novo["type"] = "list"
        novo["list_style"] = "ordered" if re.match(r"^\s*\d", base["content"]) else "unordered"
        novo["items"] = grupo
        novo["source_element_ids"] = dedup(fontes)
        novo["inline_icons"] = dedup(inline)
        novo.pop("content", None)
        saida.append(novo)
    return saida


def associar_legendas(registros: list[dict]) -> None:
    for idx, r in enumerate(registros):
        if r["type"] != "image":
            continue
        image_id = r.get("id_image")
        janela = registros[max(0, idx-4):min(len(registros), idx+5)]
        for c in janela:
            if c["type"] != "paragraph":
                continue
            m = FIG_RE.search(c.get("content", ""))
            if m and (image_id is None or int(m.group(1)) == image_id):
                r["figure_label"] = f"Fig. {m.group(1)}"
                r["caption"] = c["content"]
                c["_consumed_caption"] = True
                break
    registros[:] = [r for r in registros if not r.pop("_consumed_caption", False)]


# ---------------------------------------------------------------------------
# Referências cruzadas a alertas
# ---------------------------------------------------------------------------

def vincular_alertas_referenciados(registros: list[dict]) -> None:
    alertas_por_pagina = defaultdict(list)
    for r in registros:
        if r["type"] == "alert_box":
            for p in range(r["page_start"], r["page_end"]+1):
                alertas_por_pagina[p].append(r)

    for r in registros:
        texto_busca = r.get("content") or r.get("lead_text") or ""
        m = PAGINA_RE.search(texto_busca)
        if not m or not re.search(r"(?:observe|ver|consulte|veja).{0,100}(?:símbolo|alerta|aviso|referência)", texto_busca, re.I):
            continue
        destino = int(m.group(1))
        candidatos = alertas_por_pagina.get(destino, [])
        # Se a frase cita um tipo de alerta, prioriza o nível explicitamente mencionado.
        nivel = next((x for x in ALERTA_LABELS if re.search(rf"\b{x}\b", texto_busca, re.I)), None)
        if nivel:
            filtrados = [a for a in candidatos if a.get("alert_level") == nivel]
            if filtrados:
                candidatos = filtrados
        r["referenced_alerts"] = [a["id"] for a in candidatos]


# ---------------------------------------------------------------------------
# Debug visual e validação
# ---------------------------------------------------------------------------

def gerar_debug(pdf_original: Path, destino: Path, paginas: list[dict],
                registros: list[dict]) -> None:
    """Gera PDF de inspeção sem varrer todos os registros em cada página."""
    if fitz is None:
        print("AVISO: instale PyMuPDF (pip install pymupdf) para gerar o PDF de debug.")
        return
    if not pdf_original or not pdf_original.exists():
        print("AVISO: PDF original não encontrado; debug visual ignorado.")
        return

    # Índice de origem: elemento -> (página, caixa).
    origem = {}
    for p in paginas:
        page_num = int(p["page"])
        for e in p.get("elements", []):
            origem[e["id"]] = (page_num, box(e))

    # Agrega as caixas por registro e página antes de abrir/desenhar o PDF.
    por_pagina = defaultdict(list)
    for r in registros:
        caixas = defaultdict(list)
        for sid in r.get("source_element_ids", []):
            item = origem.get(sid)
            if item:
                caixas[item[0]].append(item[1])
        for page_num, bs in caixas.items():
            rect = (min(x[0] for x in bs), min(x[1] for x in bs),
                    max(x[2] for x in bs), max(x[3] for x in bs))
            por_pagina[page_num].append((r["reading_order"], r["type"], rect))

    doc = fitz.open(pdf_original)
    for page_num, itens in por_pagina.items():
        if not 1 <= page_num <= len(doc):
            continue
        page = doc[page_num-1]
        for order, tipo, coords in itens:
            x0, y0, x1, y1 = coords
            color = CORES_DEBUG.get(tipo, (0, 0, 0))
            page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=color,
                           width=0.65, overlay=True)
            page.insert_text((x0, max(7, y0-2)), f"#{order}", fontsize=5,
                             color=color, overlay=True)

    destino.parent.mkdir(parents=True, exist_ok=True)
    doc.save(destino, garbage=4, deflate=True)
    doc.close()


def limpar_campos_internos(r: dict) -> dict:
    r = dict(r)
    for k in list(r):
        if k.startswith("_"):
            r.pop(k, None)
    # Campos comuns e integridade dos tipos.
    r.setdefault("inline_icons", [])
    r.setdefault("referenced_alerts", [])
    r.setdefault("source_element_ids", [])
    r.setdefault("page_end", r["page_start"])
    r.setdefault("section_path", [])
    r.setdefault("parent_section_id", None)
    return r


def validar(registros: list[dict], catalogo: dict | None = None) -> list[str]:
    erros = []
    ids = {r["id"] for r in registros}
    for i, r in enumerate(registros, 1):
        if r["reading_order"] != i:
            erros.append(f"reading_order inválido no registro {r['id']}")
        if r["type"] not in TIPOS_VALIDOS:
            erros.append(f"tipo desconhecido: {r['type']}")
        if not r.get("source_element_ids"):
            erros.append(f"sem rastreabilidade: {r['id']}")
        if r["type"] == "alert_box" and r.get("alert_level") not in ALERTA_LABELS:
            erros.append(f"nível de alerta inválido: {r['id']}")
        for ref in r.get("referenced_alerts", []):
            if ref not in ids:
                erros.append(f"referência de alerta inexistente: {r['id']} -> {ref}")
    return erros


# ---------------------------------------------------------------------------
# Pipeline/CLI
# ---------------------------------------------------------------------------

def executar(entrada: Path, saida: Path, pasta_dados: Path,
             pdf_original: Path | None = None, debug: bool = False) -> list[dict]:
    paginas = ler_jsonl(entrada)
    descr_images = ler_json(pasta_dados / "descricao_images.json")
    descr_icones = ler_json(pasta_dados / "descricao_icones.json")
    catalogo = ler_json(pasta_dados / "icons_catalog.json", {"icons": []})

    registros, alert_map = construir_registros(paginas, descr_images, descr_icones)
    registros = unificar_tabelas_continuadas(registros)
    registros = costurar_paragrafos(registros)
    registros = converter_listas(registros)
    associar_legendas(registros)

    # Reindexação final após costura, listas e remoção de legendas duplicadas.
    for i, r in enumerate(registros, 1):
        r["id"] = f"blk_{i:06d}"
        r["reading_order"] = i
    vincular_alertas_referenciados(registros)
    registros = [limpar_campos_internos(r) for r in registros]

    gravar_jsonl(saida, registros)
    erros = validar(registros, catalogo)
    print(f"Fase 3: {len(registros)} registros gravados em {saida}")
    print("Distribuição:", dict(Counter(r["type"] for r in registros)))
    print(f"Alertas detectados: {sum(1 for r in registros if r['type']=='alert_box')}")
    print(f"Tabelas detectadas: {sum(1 for r in registros if r['type']=='table')}")
    if erros:
        print(f"AVISO: {len(erros)} problemas de validação (primeiros 20):")
        for erro in erros[:20]:
            print(" -", erro)
    else:
        print("Validação estrutural: OK")

    if debug and pdf_original:
        gerar_debug(pdf_original, pasta_dados / "debug_visual" / "debug_fase3_ordem_leitura.pdf",
                    paginas, registros)
    return registros


def main():
    parser = argparse.ArgumentParser(description="Reconstrução semântica da Fase 3.")
    parser.add_argument("--entrada", type=Path, default=Path("dados_preparados/document_fase2.jsonl"))
    parser.add_argument("--saida", type=Path, default=Path("dados_preparados/document_preparado.jsonl"))
    parser.add_argument("--dados", type=Path, default=Path("dados_preparados"))
    parser.add_argument("--pdf", type=Path, default=None)
    parser.add_argument("--debug", action="store_true", help="Gera o PDF visual de depuração (operação opcional e mais lenta).")
    args = parser.parse_args()
    executar(args.entrada, args.saida, args.dados, args.pdf, args.debug)


if __name__ == "__main__":
    main()
