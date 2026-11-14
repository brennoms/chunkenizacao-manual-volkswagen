import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymupdf as fitz  # PyMuPDF

from ..utils.funcoes import calcular_hash_sha256

EXTRACTOR_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"


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
        # Converte 0.0-1.0 para 0-255 para manter consistência em RGB
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
# Extratores por Tipo de Elemento
# =====================================================================

def _extrair_textos_da_pagina(
    raw_dict: Dict[str, Any],
    page_w: float,
    page_h: float,
    content_xref: Optional[int],
) -> List[Dict[str, Any]]:
    """Extrai blocos de texto agrupados por parágrafo/frase contígua."""
    textos_extraidos = []

    for block in raw_dict.get("blocks", []):
        if block.get("type") != 0:  # 0 = texto
            continue

        linhas_texto = []
        contador_fontes: Counter = Counter()
        contador_tamanhos: Counter = Counter()
        contador_cores: Counter = Counter()
        estilos_encontrados = set()
        direcoes = []

        for line in block.get("lines", []):
            partes_linha = []
            dir_vec = line.get("dir", (1.0, 0.0))
            direcoes.append(dir_vec)

            for span in line.get("spans", []):
                txt = span.get("text", "")
                if not txt:
                    continue
                partes_linha.append(txt)

                peso = len(txt.strip()) or 1
                font_name = span.get("font") or "unknown"
                font_size = round(float(span.get("size", 0.0)), 2)
                color_int = span.get("color", 0)
                flags = span.get("flags", 0)

                contador_fontes[font_name] += peso
                contador_tamanhos[font_size] += peso
                contador_cores[color_int] += peso
                estilos_encontrados.update(_extrair_estilos_span(flags, font_name))

            if partes_linha:
                linhas_texto.append("".join(partes_linha))

        conteudo_final = "\n".join(linhas_texto).strip()
        if not conteudo_final:
            continue

        fonte_predominante = contador_fontes.most_common(1)[0][0] if contador_fontes else None
        tamanho_predominante = contador_tamanhos.most_common(1)[0][0] if contador_tamanhos else None
        cor_predominante = contador_cores.most_common(1)[0][0] if contador_cores else None

        # Calcula rotação do bloco a partir do vetor diretor (cos, sin)
        rotacao = 0.0
        if direcoes:
            dx, dy = direcoes[0]
            rotacao = round(math.degrees(math.atan2(dy, dx)) % 360, 2)

        rect = fitz.Rect(block["bbox"])
        bbox, norm_bbox = _calcular_bboxes(rect, page_w, page_h)

        textos_extraidos.append({
            "type": "text",
            "bbox": bbox,
            "normalized_bbox": norm_bbox,
            "source_id": None,
            "source": _criar_source(content_stream_xref=content_xref),
            "extraction_method": "native",
            "rotation": rotacao,
            "transform": None,
            "parent_id": None,
            "content": conteudo_final,
            "font": fonte_predominante,
            "font_size": tamanho_predominante,
            "color": _cor_int_para_objeto(cor_predominante, alpha=1.0),
            "style": sorted(estilos_encontrados),
        })

    return textos_extraidos


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

    # Se possuir SMask válido, recompõe a imagem com canal alfa via Pixmap
    if smask_xref and smask_xref > 0:
        try:
            pix_base = fitz.Pixmap(doc, xref)
            pix_mask = fitz.Pixmap(doc, smask_xref)
            # Se a imagem base estiver em CMYK ou outro espaço sem suporte direto a alfa, converte para RGB
            if pix_base.colorspace and pix_base.colorspace.n > 3:
                pix_base = fitz.Pixmap(fitz.csRGB, pix_base)
            pix_composto = fitz.Pixmap(pix_base, pix_mask)
            img_bytes = pix_composto.tobytes("png")
            return img_bytes, "png", pix_composto.width, pix_composto.height
        except Exception:
            # Fallback para os bytes originais caso a máscara seja incompatível
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

    # Mapeia xref -> (smask_xref, resource_name) a partir da lista de recursos da página
    mapa_recursos: Dict[int, Tuple[Optional[int], Optional[str]]] = {}
    for item in page.get_images(full=True):
        # item = (xref, smask, width, height, bpc, colorspace, alt_cs, name, filter, referencer)
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

        # Desduplicação: salva no disco apenas se o hash ainda não existir
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
    if op == "l":  # Linha entre p1 e p2
        p1, p2 = item[1], item[2]
        return {"op": "l", "p1": [round(p1.x, 2), round(p1.y, 2)], "p2": [round(p2.x, 2), round(p2.y, 2)]}
    elif op == "re":  # Retângulo
        r = item[1]
        return {
            "op": "re",
            "rect": [round(r.x0, 2), round(r.y0, 2), round(r.width, 2), round(r.height, 2)],
        }
    elif op == "qu":  # Quad
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
    elif op == "c":  # Curva de Bézier cúbica (p1, p2, p3, p4)
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

        # Verifica se é uma primitiva pura (1 único comando 're' ou 'l')
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
    # Busca versão no catálogo ou no cabeçalho do arquivo
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

            # Extrai cada categoria de elemento
            elementos_pagina: List[Dict[str, Any]] = []
            elementos_pagina.extend(_extrair_textos_da_pagina(raw_dict, page_w, page_h, content_xref))
            elementos_pagina.extend(
                _extrair_imagens_da_pagina(
                    doc, page, page_w, page_h, content_xref, assets_dir, registro_hashes_imagens
                )
            )
            elementos_pagina.extend(_extrair_vetores_e_formas_da_pagina(page, page_w, page_h, content_xref))
            elementos_pagina.extend(_extrair_links_da_pagina(doc, page, page_w, page_h))

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

from utils.caminhos import raiz

pdf_path = raiz["dados_extraidos"]["pdf_original"]
pdf_hash_path = raiz["dados_extraidos"]["pdf_original_hash"]
diretorio_saida = raiz["dados_extraidos"]["conteudo_extraido"]["path"]
hash_atual = pdf_hash_path.read_text(encoding="utf-8").strip()

print(f"Iniciando extração bruta de '{pdf_path.name}'...")
extrair_pdf_bruto(
    pdf_path=pdf_path,
    output_dir=diretorio_saida,
    file_sha256=hash_atual,
)
