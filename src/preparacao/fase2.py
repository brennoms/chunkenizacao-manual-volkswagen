"""Consolidação e deduplicação global de ícones extraídos do manual."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import imagehash
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont


TOLERANCIA_BBOX = 1.5
LIMITE_TEXTO_ICONE = 5
LIMITE_SHAPE_ICONE = 40.0
LIMITE_HAMMING_PHASH = 7
LIMITE_COR_ICONE = 15
LIMITE_VARIACAO_ASPECTO = 0.1
ESCALA_RENDERIZACAO_ICONE = 30.0
BUSCAR_ICONES = False  # Defina como True para salvar as capturas dos ícones

COLUNAS_PARIDADE = 8
TAMANHO_CELULA_PARIDADE = 96
TAMANHO_ICONE_PARIDADE = 64
TAMANHO_FONTE_NUMERO_PARIDADE = 24
MARGEM_PARIDADE = 8
ESPACO_PARIDADE = 4
COLUNAS_MATRIZES_PARIDADE = 4

# Informe aqui as ocorrências que devem compartilhar o mesmo `icon_id`.
ICONES_SEMELHANTES_MANUAIS: Dict[int, List[int]] = {
}


def _bbox(elemento: Dict[str, Any]) -> Tuple[float, float, float, float]:
    bbox = elemento["bbox"]
    x0 = float(bbox["x"])
    y0 = float(bbox["y"])
    return x0, y0, x0 + float(bbox["width"]), y0 + float(bbox["height"])


def _celulas_bbox(caixa: Tuple[float, float, float, float]) -> Iterable[Tuple[int, int]]:
    x0, y0, x1, y1 = caixa
    cx0 = int((x0 - TOLERANCIA_BBOX) // LIMITE_SHAPE_ICONE)
    cy0 = int((y0 - TOLERANCIA_BBOX) // LIMITE_SHAPE_ICONE)
    cx1 = int((x1 + TOLERANCIA_BBOX) // LIMITE_SHAPE_ICONE)
    cy1 = int((y1 + TOLERANCIA_BBOX) // LIMITE_SHAPE_ICONE)
    return (
        (cx, cy)
        for cx in range(cx0, cx1 + 1)
        for cy in range(cy0, cy1 + 1)
    )


def _caixas_intersectam(
    primeira: Tuple[float, float, float, float],
    segunda: Tuple[float, float, float, float],
) -> bool:
    ax0, ay0, ax1, ay1 = primeira
    bx0, by0, bx1, by1 = segunda
    return (
        ax0 - TOLERANCIA_BBOX <= bx1
        and bx0 - TOLERANCIA_BBOX <= ax1
        and ay0 - TOLERANCIA_BBOX <= by1
        and by0 - TOLERANCIA_BBOX <= ay1
    )


def _intersecta(primeiro: Dict[str, Any], segundo: Dict[str, Any]) -> bool:
    return _caixas_intersectam(_bbox(primeiro), _bbox(segundo))


def _eh_shape_candidato(elemento: Dict[str, Any]) -> bool:
    if elemento.get("type") != "shape" or elemento.get("shape_type") == "line":
        return False
    bbox = elemento["bbox"]
    return (
        float(bbox["width"]) <= LIMITE_SHAPE_ICONE
        and float(bbox["height"]) <= LIMITE_SHAPE_ICONE
    )


def _eh_texto_curto(elemento: Dict[str, Any]) -> bool:
    return (
        elemento.get("type") == "text"
        and 0 < len(str(elemento.get("content") or "").strip()) <= LIMITE_TEXTO_ICONE
    )


class _UnionFind:
    def __init__(self, tamanho: int) -> None:
        self._pai = list(range(tamanho))
        self._rank = [0] * tamanho

    def find(self, item: int) -> int:
        while self._pai[item] != item:
            self._pai[item] = self._pai[self._pai[item]]
            item = self._pai[item]
        return item

    def union(self, primeiro: int, segundo: int) -> None:
        raiz_a = self.find(primeiro)
        raiz_b = self.find(segundo)
        if raiz_a == raiz_b:
            return
        if self._rank[raiz_a] < self._rank[raiz_b]:
            raiz_a, raiz_b = raiz_b, raiz_a
        self._pai[raiz_b] = raiz_a
        if self._rank[raiz_a] == self._rank[raiz_b]:
            self._rank[raiz_a] += 1


def _indices_sobrepostos(
    elementos: Sequence[Dict[str, Any]], indices_graficos: Sequence[int]
) -> List[int]:
    if not indices_graficos:
        return []
    caixas = {indice: _bbox(elementos[indice]) for indice in indices_graficos}
    grade: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for indice in indices_graficos:
        for celula in _celulas_bbox(caixas[indice]):
            grade[celula].append(indice)

    sobrepostos = []
    for indice, elemento in enumerate(elementos):
        if not _eh_texto_curto(elemento):
            continue
        caixa = _bbox(elemento)
        candidatos = {
            grafico
            for celula in _celulas_bbox(caixa)
            for grafico in grade[celula]
        }
        if any(_caixas_intersectam(caixa, caixas[grafico]) for grafico in candidatos):
            sobrepostos.append(indice)
    return sobrepostos


def _unir_candidatos(
    elementos: Sequence[Dict[str, Any]], candidatos: Sequence[int]
) -> List[List[int]]:
    """Une candidatos com uma grade espacial, evitando comparação quadrática."""
    if not candidatos:
        return []
    uf = _UnionFind(len(candidatos))
    indice_local = {indice: posicao for posicao, indice in enumerate(candidatos)}
    caixas = {indice: _bbox(elementos[indice]) for indice in candidatos}
    grade: Dict[Tuple[int, int], List[int]] = defaultdict(list)

    for posicao, indice in enumerate(candidatos):
        x0, y0, x1, y1 = caixas[indice]
        vizinhos = {
            vizinho
            for celula in _celulas_bbox(caixas[indice])
            for vizinho in grade[celula]
        }
        for outra_posicao in vizinhos:
            outro_indice = candidatos[outra_posicao]
            if _caixas_intersectam(caixas[indice], caixas[outro_indice]):
                uf.union(posicao, outra_posicao)
        for celula in _celulas_bbox(caixas[indice]):
            grade[celula].append(posicao)

    grupos: Dict[int, List[int]] = defaultdict(list)
    for indice in candidatos:
        grupos[uf.find(indice_local[indice])].append(indice)
    return sorted(grupos.values(), key=lambda grupo: min(grupo))


def _bbox_envolvente(
    elementos: Sequence[Dict[str, Any]], indices: Iterable[int]
) -> Dict[str, float]:
    caixas = [_bbox(elementos[indice]) for indice in indices]
    x0 = min(caixa[0] for caixa in caixas)
    y0 = min(caixa[1] for caixa in caixas)
    x1 = max(caixa[2] for caixa in caixas)
    y1 = max(caixa[3] for caixa in caixas)
    return {
        "x": round(x0, 2),
        "y": round(y0, 2),
        "width": round(x1 - x0, 2),
        "height": round(y1 - y0, 2),
    }


def _assinatura_geometrica(
    elementos: Sequence[Dict[str, Any]], indices: Sequence[int]
) -> str:
    """Fallback determinístico quando a página original não foi fornecida."""
    bbox = _bbox_envolvente(elementos, indices)
    x0, y0 = bbox["x"], bbox["y"]
    escala_x = bbox["width"] or 1.0
    escala_y = bbox["height"] or 1.0
    partes = []
    for indice in indices:
        elemento = elementos[indice]
        caixa = _bbox(elemento)
        partes.append(
            {
                "type": elemento.get("type"),
                "shape_type": elemento.get("shape_type"),
                "fill": elemento.get("fill_color"),
                "stroke": elemento.get("stroke_color"),
                "content": elemento.get("content")
                if elemento.get("type") == "text"
                else None,
                "geometry": elemento.get("geometry", {}),
                "relative_bbox": [
                    round((caixa[0] - x0) / escala_x, 5),
                    round((caixa[1] - y0) / escala_y, 5),
                    round((caixa[2] - caixa[0]) / escala_x, 5),
                    round((caixa[3] - caixa[1]) / escala_y, 5),
                ],
            }
        )
    payload = json.dumps(partes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _phash_e_cor(
    pagina: fitz.Page, bbox: Dict[str, float]
) -> Tuple[str, List[int], fitz.Pixmap]:
    """Renderiza o recorte e calcula pHash de 64 bits mais cor média quantizada."""
    retangulo = fitz.Rect(
        float(bbox["x"]),
        float(bbox["y"]),
        float(bbox["x"]) + max(float(bbox["width"]), 1.0),
        float(bbox["y"]) + max(float(bbox["height"]), 1.0),
    ) & pagina.rect
    pixmap = pagina.get_pixmap(
        matrix=fitz.Matrix(ESCALA_RENDERIZACAO_ICONE, ESCALA_RENDERIZACAO_ICONE),
        clip=retangulo,
        alpha=True,
    )
    phash, cor = _assinatura_da_imagem(_pixmap_para_imagem(pixmap))
    return phash, cor, pixmap


def _assinatura_da_imagem(imagem: Image.Image) -> Tuple[str, List[int]]:
    if imagem.mode == "RGBA":
        imagem_assinatura = Image.new("RGBA", imagem.size, "white")
        imagem_assinatura.alpha_composite(imagem)
        imagem_assinatura = imagem_assinatura.convert("RGB")
    else:
        imagem_assinatura = imagem
    phash = str(imagehash.phash(imagem_assinatura))
    dados = imagem_assinatura.tobytes()
    medias = [
        sum(dados[channel::3]) // max(len(dados[channel::3]), 1)
        for channel in range(3)
    ]
    cor = [int(valor // 16 * 16) for valor in medias]
    return phash, cor


def _adicionar_asset(pixmap: fitz.Pixmap, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    _pixmap_para_imagem(pixmap).save(str(caminho), format="PNG")


def _pixmap_para_imagem(pixmap: fitz.Pixmap) -> Image.Image:
    modo = "RGBA" if pixmap.alpha else "RGB"
    return Image.frombytes(modo, (pixmap.width, pixmap.height), pixmap.samples)


def _carregar_icones_existentes(
    ocorrencias: Sequence[Dict[str, Any]], diretorio: Path
) -> None:
    for ocorrencia in ocorrencias:
        icon_order_id = ocorrencia["element"]["icon_order_id"]
        caminho = diretorio / f"icon_{icon_order_id:04d}.png"
        if not caminho.exists():
            raise FileNotFoundError(
                f"Ícone existente para icon_order_id={icon_order_id} não encontrado: "
                f"{caminho}"
            )
        pixmap = fitz.Pixmap(str(caminho))
        ocorrencia["pixmap"] = pixmap
        with Image.open(caminho) as imagem:
            ocorrencia["signature"] = _assinatura_da_imagem(imagem.copy())


def _grupos_manuais_por_ordem() -> Dict[int, int]:
    grupos: List[set[int]] = []
    for icon_order_id, semelhantes in ICONES_SEMELHANTES_MANUAIS.items():
        grupo = {int(icon_order_id), *(int(item) for item in semelhantes)}
        grupos_com_intersecao = [existente for existente in grupos if grupo & existente]
        if grupos_com_intersecao:
            grupo.update(*(existente for existente in grupos_com_intersecao))
            grupos[:] = [
                existente
                for existente in grupos
                if existente not in grupos_com_intersecao
            ]
        grupos.append(grupo)
    return {
        icon_order_id: min(grupo)
        for grupo in grupos
        for icon_order_id in grupo
    }


def _grupos_manuais_por_ocorrencia(
    ocorrencias: Sequence[Dict[str, Any]],
) -> Dict[int, int]:
    """Inclui no grupo manual ocorrências semelhantes a qualquer item listado."""
    grupos_por_ordem = _grupos_manuais_por_ordem()
    grupos_por_ocorrencia = dict(grupos_por_ordem)
    ocorrencias_por_ordem = {
        ordem: ocorrencia
        for ordem, ocorrencia in enumerate(ocorrencias, start=1)
    }

    for grupo_manual in sorted(set(grupos_por_ordem.values())):
        ordens_listadas = [
            ordem
            for ordem, grupo in grupos_por_ordem.items()
            if grupo == grupo_manual and ordem in ocorrencias_por_ordem
        ]
        for ordem, ocorrencia in ocorrencias_por_ordem.items():
            if ordem in grupos_por_ocorrencia:
                continue
            if any(
                _assinaturas_equivalentes(
                    ocorrencia["signature"],
                    ocorrencias_por_ordem[ordem_listada]["signature"],
                    ocorrencia["bbox"],
                    ocorrencias_por_ordem[ordem_listada]["bbox"],
                )
                for ordem_listada in ordens_listadas
            ):
                grupos_por_ocorrencia[ordem] = grupo_manual

    return grupos_por_ocorrencia


def _criar_matriz_paridade(
    icon_id: str, ocorrencias: Sequence[Tuple[int, fitz.Pixmap]]
) -> Image.Image:
    """Cria um painel com todas as ocorrências do mesmo ícone."""
    if not ocorrencias:
        return Image.new("RGB", (1, 1), "white")
    imagens = [
        (icon_order_id, _pixmap_para_imagem(pixmap))
        for icon_order_id, pixmap in ocorrencias
    ]
    linhas = (len(imagens) + COLUNAS_PARIDADE - 1) // COLUNAS_PARIDADE
    largura = (
        MARGEM_PARIDADE * 2
        + COLUNAS_PARIDADE * TAMANHO_CELULA_PARIDADE
        + (COLUNAS_PARIDADE - 1) * ESPACO_PARIDADE
    )
    altura = (
        MARGEM_PARIDADE * 2
        + linhas * TAMANHO_CELULA_PARIDADE
        + (linhas - 1) * ESPACO_PARIDADE
    )
    altura_titulo = 20
    painel = Image.new("RGB", (largura, altura_titulo + altura), "white")
    desenho = ImageDraw.Draw(painel)
    fonte_numero = ImageFont.load_default(size=TAMANHO_FONTE_NUMERO_PARIDADE)
    desenho.text(
        (MARGEM_PARIDADE, 2),
        f"{icon_id} ({len(imagens)})",
        fill="black",
        font=ImageFont.load_default(),
    )
    for indice, (icon_order_id, imagem) in enumerate(imagens):
        coluna = indice % COLUNAS_PARIDADE
        linha = indice // COLUNAS_PARIDADE
        origem_x = (
            MARGEM_PARIDADE
            + coluna * (TAMANHO_CELULA_PARIDADE + ESPACO_PARIDADE)
        )
        origem_y = (
            altura_titulo
            + MARGEM_PARIDADE
            + linha * (TAMANHO_CELULA_PARIDADE + ESPACO_PARIDADE)
        )
        desenho.text(
            (origem_x + 2, origem_y + 2),
            str(icon_order_id),
            fill="black",
            font=fonte_numero,
        )
        fator = min(
            TAMANHO_ICONE_PARIDADE / max(imagem.width, 1),
            TAMANHO_ICONE_PARIDADE / max(imagem.height, 1),
        )
        tamanho = (
            max(1, round(imagem.width * fator)),
            max(1, round(imagem.height * fator)),
        )
        imagem = imagem.resize(tamanho, Image.Resampling.NEAREST)
        x = origem_x + (TAMANHO_CELULA_PARIDADE - imagem.width) // 2
        y = origem_y + 28 + (TAMANHO_CELULA_PARIDADE - imagem.height - 28) // 2
        mascara = imagem if imagem.mode == "RGBA" else None
        painel.paste(imagem, (x, y), mascara)
    return painel


def _gerar_matriz_paridade_total(
    pixmaps_por_icon: Dict[str, List[Tuple[int, fitz.Pixmap]]], caminho: Path
) -> None:
    """Cria uma única imagem com os painéis de todos os ícones."""
    paineis = [
        _criar_matriz_paridade(icon_id, ocorrencias)
        for icon_id, ocorrencias in pixmaps_por_icon.items()
        if ocorrencias
    ]
    if not paineis:
        return
    largura_painel = max(painel.width for painel in paineis)
    espaco_painel = 16
    alturas_linhas = [
        max(
            painel.height
            for painel in paineis[inicio : inicio + COLUNAS_MATRIZES_PARIDADE]
        )
        for inicio in range(0, len(paineis), COLUNAS_MATRIZES_PARIDADE)
    ]
    largura = (
        COLUNAS_MATRIZES_PARIDADE * largura_painel
        + (COLUNAS_MATRIZES_PARIDADE - 1) * espaco_painel
    )
    altura = sum(alturas_linhas) + (len(alturas_linhas) - 1) * espaco_painel
    matriz_total = Image.new("RGB", (largura, altura), "#d9d9d9")
    indice = 0
    y = 0
    for altura_linha in alturas_linhas:
        for coluna in range(COLUNAS_MATRIZES_PARIDADE):
            if indice >= len(paineis):
                break
            matriz_total.paste(
                paineis[indice],
                (coluna * (largura_painel + espaco_painel), y),
            )
            indice += 1
        y += altura_linha + espaco_painel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    matriz_total.save(caminho)


def _assinaturas_equivalentes(
    primeira: Tuple[str, List[int]],
    segunda: Tuple[str, List[int]],
    primeiro_bbox: Dict[str, float],
    segundo_bbox: Dict[str, float],
) -> bool:
    """Aceita pequenas variações de rasterização do mesmo ícone."""
    primeira_hash, primeira_cor = primeira
    segunda_hash, segunda_cor = segunda
    if len(primeira_hash) != 16 or len(segunda_hash) != 16:
        return primeira == segunda
    if (
        (int(primeira_hash, 16) ^ int(segunda_hash, 16)).bit_count()
        > LIMITE_HAMMING_PHASH
        or any(
            abs(primeira_valor - segunda_valor) > LIMITE_COR_ICONE
            for primeira_valor, segunda_valor in zip(primeira_cor, segunda_cor)
        )
    ):
        return False
    primeiro_aspecto = float(primeiro_bbox["width"]) / max(
        float(primeiro_bbox["height"]), 1.0
    )
    segundo_aspecto = float(segundo_bbox["width"]) / max(
        float(segundo_bbox["height"]), 1.0
    )
    return abs(primeiro_aspecto - segundo_aspecto) <= LIMITE_VARIACAO_ASPECTO


def _preparar_imagens_das_ocorrencias(
    ocorrencias: Sequence[Dict[str, Any]], diretorio_all_icones: Path
) -> None:
    if BUSCAR_ICONES:
        diretorio_all_icones.mkdir(parents=True, exist_ok=True)
        for caminho in diretorio_all_icones.glob("*.png"):
            caminho.unlink()
        for ocorrencia in ocorrencias:
            ordem = ocorrencia["element"]["icon_order_id"]
            caminho = diretorio_all_icones / f"icon_{ordem:04d}.png"
            _adicionar_asset(ocorrencia["pixmap"], caminho)
            with Image.open(caminho) as imagem:
                ocorrencia["signature"] = _assinatura_da_imagem(imagem.copy())
        return
    _carregar_icones_existentes(ocorrencias, diretorio_all_icones)


def _catalogar_ocorrencias(
    ocorrencias: Sequence[Dict[str, Any]], diretorio_icons: Path
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Tuple[int, fitz.Pixmap]]]]:
    catalogo: Dict[str, Dict[str, Any]] = {}
    representantes: List[
        Tuple[Tuple[str, List[int]], Dict[str, float], Dict[str, Any]]
    ] = []
    pixmaps_por_icon: Dict[str, List[Tuple[int, fitz.Pixmap]]] = defaultdict(list)
    grupos_manuais = _grupos_manuais_por_ocorrencia(ocorrencias)
    registros_por_grupo_manual: Dict[int, Dict[str, Any]] = {}

    for ocorrencia in ocorrencias:
        icon_order_id = ocorrencia["element"]["icon_order_id"]
        assinatura, cor = ocorrencia["signature"]
        grupo_manual = grupos_manuais.get(icon_order_id)
        registro = (
            registros_por_grupo_manual.get(grupo_manual)
            if grupo_manual is not None
            else catalogo.get(f"{assinatura}:{','.join(map(str, cor))}")
        )
        if registro is None:
            assinatura_atual = (assinatura, cor)
            registro = next(
                (
                    candidato
                    for assinatura_representante, bbox_representante, candidato
                    in representantes
                    if _assinaturas_equivalentes(
                        assinatura_atual,
                        assinatura_representante,
                        ocorrencia["bbox"],
                        bbox_representante,
                    )
                ),
                None,
            )
        if registro is None:
            icon_id = f"icon_{len(catalogo) + 1:04d}"
            registro = {
                "icon_id": icon_id,
                "image_path": f"assets/icons/{icon_id}.png",
                "phash": assinatura,
                "dominant_color": cor,
                "occurrences": [],
            }
            catalogo[f"{assinatura}:{','.join(map(str, cor))}"] = registro
            representantes.append(((assinatura, cor), ocorrencia["bbox"], registro))
            if BUSCAR_ICONES:
                _adicionar_asset(
                    ocorrencia["pixmap"], diretorio_icons / f"{icon_id}.png"
                )

        if grupo_manual is not None:
            registros_por_grupo_manual[grupo_manual] = registro
        ocorrencia["element"]["icon_id"] = registro["icon_id"]
        pixmaps_por_icon[registro["icon_id"]].append(
            (icon_order_id, ocorrencia["pixmap"])
        )
        registro["occurrences"].append(
            {
                "page": ocorrencia["page"],
                "icon_order_id": icon_order_id,
                "source_element_ids": ocorrencia["source_element_ids"],
            }
        )
    return catalogo, pixmaps_por_icon


def consolidar_pagina(
    pagina: Dict[str, Any], pagina_pdf: Optional[fitz.Page] = None
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Consolida os ícones de uma página, mantendo a ordem dos demais elementos."""
    elementos = pagina.get("elements", [])
    graficos = [
        indice
        for indice, elemento in enumerate(elementos)
        if elemento.get("type") == "vector" or _eh_shape_candidato(elemento)
    ]
    candidatos = graficos + _indices_sobrepostos(elementos, graficos)
    grupos = _unir_candidatos(elementos, candidatos)
    por_indice = {indice: grupo for grupo in grupos for indice in grupo}
    consolidados: List[Dict[str, Any]] = []
    elementos_saida: List[Dict[str, Any]] = []

    for indice, elemento in enumerate(elementos):
        grupo = por_indice.get(indice)
        if grupo is None:
            elementos_saida.append(elemento)
            continue
        if indice != min(grupo):
            continue
        bbox = _bbox_envolvente(elementos, grupo)
        fontes = [
            elementos[posicao].get("id")
            for posicao in sorted(grupo)
            if elementos[posicao].get("id") is not None
        ]
        tem_texto = any(elementos[posicao].get("type") == "text" for posicao in grupo)
        icon = {
            "id": f"icon_source_{pagina['page']}_{len(consolidados) + 1:04d}",
            "type": "icon",
            "bbox": bbox,
            "normalized_bbox": {
                "x": round(bbox["x"] / float(pagina["width"]), 6),
                "y": round(bbox["y"] / float(pagina["height"]), 6),
                "width": round(bbox["width"] / float(pagina["width"]), 6),
                "height": round(bbox["height"] / float(pagina["height"]), 6),
            },
            "source_element_ids": fontes,
            "classification": "inline" if tem_texto else "standalone",
        }
        elementos_saida.append(icon)
        assinatura = _phash_e_cor(pagina_pdf, bbox) if pagina_pdf is not None else None
        consolidados.append(
            {
                "page": pagina["page"],
                "bbox": bbox,
                "source_element_ids": fontes,
                "element": icon,
                "pixmap": assinatura[2] if assinatura else None,
                "signature": assinatura[:2]
                if assinatura
                else (_assinatura_geometrica(elementos, sorted(grupo)), []),
            }
        )

    pagina_saida = dict(pagina)
    pagina_saida["elements"] = elementos_saida
    return pagina_saida, consolidados


def executar_fase2_consolidacao(
    jsonl_entrada: Path,
    jsonl_saida: Path,
    pdf_original: Optional[Path] = None,
    diretorio_icons: Optional[Path] = None,
    arquivo_catalogo: Optional[Path] = None,
    diretorio_paridade: Optional[Path] = None,
    diretorio_all_icones: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], Counter]:
    """Executa a Fase 2 e grava JSONL, catálogo e imagens de referência."""
    jsonl_saida.parent.mkdir(parents=True, exist_ok=True)
    diretorio_icons = diretorio_icons or jsonl_saida.parent / "assets" / "icons"
    arquivo_catalogo = arquivo_catalogo or jsonl_saida.parent / "icons_catalog.json"
    diretorio_paridade = diretorio_paridade or jsonl_saida.parent / "assets" / "paridade"
    diretorio_all_icones = (
        diretorio_all_icones
        or jsonl_saida.parent / "assets" / "all_icons"
    )
    if BUSCAR_ICONES and pdf_original is None:
        raise ValueError("BUSCAR_ICONES=True exige o caminho do PDF original.")
    pdf = (
        fitz.open(str(pdf_original))
        if BUSCAR_ICONES and pdf_original is not None
        else None
    )
    paginas: List[Dict[str, Any]] = []
    ocorrencias: List[Dict[str, Any]] = []
    estatisticas = Counter()

    try:
        with jsonl_entrada.open("r", encoding="utf-8") as entrada:
            for linha in entrada:
                pagina = json.loads(linha)
                pagina_pdf = pdf[pagina["page"] - 1] if pdf is not None else None
                pagina_saida, encontrados = consolidar_pagina(pagina, pagina_pdf)
                paginas.append(pagina_saida)
                ocorrencias.extend(encontrados)
                estatisticas["paginas"] += 1
                estatisticas["icones_consolidados"] += len(encontrados)
                estatisticas["elementos_fundidos"] += sum(
                    max(0, len(item["source_element_ids"]) - 1) for item in encontrados
                )
    finally:
        if pdf is not None:
            pdf.close()

    for icon_order_id, ocorrencia in enumerate(ocorrencias, start=1):
        ocorrencia["element"]["icon_order_id"] = icon_order_id

    _preparar_imagens_das_ocorrencias(ocorrencias, diretorio_all_icones)
    catalogo, pixmaps_por_icon = _catalogar_ocorrencias(
        ocorrencias, diretorio_icons
    )

    caminho_paridade = diretorio_paridade / "matriz_paridade.png"
    _gerar_matriz_paridade_total(pixmaps_por_icon, caminho_paridade)

    estatisticas["icones_encontrados"] = len(ocorrencias)
    estatisticas["icones_diferentes"] = len(catalogo)
    with jsonl_saida.open("w", encoding="utf-8") as saida:
        for pagina in paginas:
            saida.write(json.dumps(pagina, ensure_ascii=False) + "\n")

    arquivo_catalogo.parent.mkdir(parents=True, exist_ok=True)
    arquivo_catalogo.write_text(
        json.dumps(
            {
                "summary": {
                    "icones_encontrados": len(ocorrencias),
                    "icones_diferentes": len(catalogo),
                    "paridade_path": "assets/paridade/matriz_paridade.png",
                },
                "icons": list(catalogo.values()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "Fase 2: "
        f"{len(ocorrencias)} ícones encontrados, "
        f"{len(catalogo)} ícones diferentes."
    )
    return paginas, estatisticas


def gerar_debug_fase2_pdf(
    pdf_original: Path, paginas: Sequence[Dict[str, Any]], pdf_saida: Path
) -> Path:
    """Marca cada ícone consolidado com uma caixa única e seu icon_id."""
    pdf_saida.parent.mkdir(parents=True, exist_ok=True)
    documento = fitz.open(str(pdf_original))
    total = 0
    for pagina in paginas:
        canvas = documento[pagina["page"] - 1].new_shape()
        desenhou = False
        for elemento in pagina.get("elements", []):
            if elemento.get("type") != "icon":
                continue
            bbox = elemento["bbox"]
            retangulo = fitz.Rect(
                bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]
            )
            canvas.draw_rect(retangulo)
            canvas.finish(color=(0.05, 0.35, 0.95), width=0.8, fill_opacity=0)
            canvas.insert_text(
                fitz.Point(retangulo.x0, max(6, retangulo.y0 - 1)),
                elemento["icon_id"],
                fontsize=5,
                color=(0.05, 0.35, 0.95),
            )
            desenhou = True
            total += 1
        if desenhou:
            canvas.commit()
    documento.save(str(pdf_saida), garbage=3, deflate=True)
    documento.close()
    print(f"PDF de debug da Fase 2 gerado: {pdf_saida} ({total} ícones)")
    return pdf_saida


if __name__ == "__main__":
    from utils.caminhos import raiz

    #limpa o diretório de saída da Fase 2 antes de executar novamente
    if BUSCAR_ICONES:
        if raiz["dados_preparados"]["document_fase2"].exists():
            raiz["dados_preparados"]["document_fase2"].unlink()
        if raiz["dados_preparados"]["icons_catalog"].exists():
            raiz["dados_preparados"]["icons_catalog"].unlink()
        if raiz["dados_preparados"]["debug_visual"]["fase2_icones"].exists():
            raiz["dados_preparados"]["debug_visual"]["fase2_icones"].unlink()
        if raiz["dados_preparados"]["assets"]["paridade"].exists():
            for file in raiz["dados_preparados"]["assets"]["paridade"].glob("*"):
                file.unlink()
        if raiz["dados_preparados"]["assets"]["icons"].exists():
            for file in raiz["dados_preparados"]["assets"]["icons"].glob("*"):
                file.unlink()
        diretorio_all_icones = raiz["dados_preparados"]["path"] / "assets" / "all_icons"
        if diretorio_all_icones.exists():
            for file in diretorio_all_icones.glob("*"):
                file.unlink()
            

    paginas_fase2, _estatisticas = executar_fase2_consolidacao(
        jsonl_entrada=raiz["dados_preparados"]["document_fase1"],
        jsonl_saida=raiz["dados_preparados"]["document_fase2"],
        pdf_original=raiz["dados_extraidos"]["pdf_original"],
        diretorio_icons=raiz["dados_preparados"]["assets"]["icons"],
        arquivo_catalogo=raiz["dados_preparados"]["icons_catalog"],
    )
    gerar_debug_fase2_pdf(
        pdf_original=raiz["dados_extraidos"]["pdf_original"],
        paginas=paginas_fase2,
        pdf_saida=raiz["dados_preparados"]["debug_visual"]["fase2_icones"],
    )
