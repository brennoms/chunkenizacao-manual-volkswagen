import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import List

import fitz  # PyMuPDF


def validar_regras_extracao(output_dir: Path) -> bool:
    """Verifica todas as regras de integridade do modelo de dados."""
    erros = []
    avisos = []

    meta_path = output_dir / "metadata.json"
    jsonl_path = output_dir / "document.jsonl"
    assets_dir = output_dir / "assets"

    if not meta_path.exists() or not jsonl_path.exists():
        print("❌ Erro fatal: metadata.json ou document.jsonl não encontrados.")
        return False

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    page_count_esperado = metadata.get("page_count", 0)

    ids_vistos = set()
    contagem_tipos = Counter()
    hashes_verificados = {}
    paginas_lidas = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for num_linha, linha in enumerate(f, start=1):
            paginas_lidas += 1
            pagina = json.loads(linha)
            p_num = pagina.get("page")

            if p_num != num_linha:
                erros.append(f"Página {p_num} fora de sequência na linha {num_linha}.")

            elementos = pagina.get("elements", [])
            qtd_el = len(elementos)

            ordens_extracao = []
            ordens_espaciais = []

            for el in elementos:
                el_id = el.get("id")
                el_type = el.get("type")
                contagem_tipos[el_type] += 1

                # Regra 2: ID único global
                if el_id in ids_vistos:
                    erros.append(f"ID duplicado encontrado: {el_id} (página {p_num}).")
                ids_vistos.add(el_id)

                ordens_extracao.append(el.get("extraction_order"))
                ordens_espaciais.append(el.get("spatial_order"))

                # Verifica bbox normalizado
                nb = el.get("normalized_bbox", {})
                if nb.get("width", 0) < 0 or nb.get("height", 0) < 0:
                    erros.append(f"Elemento {el_id} (pág {p_num}) possui dimensão negativa.")
                if not (-0.5 <= nb.get("x", 0) <= 1.5 and -0.5 <= nb.get("y", 0) <= 1.5):
                    avisos.append(f"Elemento {el_id} (pág {p_num}) muito fora dos limites da página: {nb}")

                # Regra 5 e 6: Integridade dos assets e hashes de imagem
                if el_type == "image":
                    rel_path = el.get("path")
                    c_hash = el.get("content_hash")
                    asset_file = output_dir / rel_path if rel_path else None

                    if not asset_file or not asset_file.exists():
                        erros.append(f"Asset ausente no disco para {el_id}: {rel_path}")
                    elif c_hash not in hashes_verificados:
                        bytes_disco = asset_file.read_bytes()
                        if len(bytes_disco) == 0:
                            erros.append(f"Asset vazio (0 bytes): {rel_path}")
                        hash_real = hashlib.sha256(bytes_disco).hexdigest()
                        if hash_real != c_hash:
                            erros.append(f"Hash divergente em {rel_path}: esperado {c_hash}, calculado {hash_real}")
                        hashes_verificados[c_hash] = rel_path

            # Regra 3: extraction_order e spatial_order de 1..N sem repetição
            esperado_seq = list(range(1, qtd_el + 1))
            if sorted(ordens_extracao) != esperado_seq:
                erros.append(f"Página {p_num}: extraction_order inválido.")
            if sorted(ordens_espaciais) != esperado_seq:
                erros.append(f"Página {p_num}: spatial_order inválido.")

    # Regra 1: 1 registro JSON por página
    if paginas_lidas != page_count_esperado:
        erros.append(f"Total de linhas no JSONL ({paginas_lidas}) difere do metadata ({page_count_esperado}).")

    # Verifica se há arquivos órfãos na pasta assets/
    arquivos_assets = list(assets_dir.glob("*")) if assets_dir.exists() else []
    if len(arquivos_assets) != len(hashes_verificados):
        avisos.append(
            f"Pasta assets contém {len(arquivos_assets)} arquivos, "
            f"mas o JSONL referencia {len(hashes_verificados)} hashes únicos."
        )

    # Relatório Final
    print("=" * 60)
    print("RELATÓRIO DE VALIDAÇÃO DA EXTRAÇÃO BRUTA")
    print("=" * 60)
    print(f"Páginas validadas     : {paginas_lidas}/{page_count_esperado}")
    print(f"Total de elementos    : {len(ids_vistos)}")
    for t, qtd in contagem_tipos.most_common():
        print(f"  - {t:<10}        : {qtd}")
    print(f"Assets únicos validados: {len(hashes_verificados)}")
    print("-" * 60)

    if avisos:
        print(f"⚠️  Avisos ({len(avisos)}):")
        for av in avisos[:5]:
            print(f"   {av}")
        if len(avisos) > 5:
            print(f"   ... e mais {len(avisos) - 5} avisos.")

    if erros:
        print(f"❌ Falha na validação! {len(erros)} erro(s) encontrado(s):")
        for err in erros[:10]:
            print(f"   {err}")
        return False

    print("✅ Sucesso! Todas as regras do modelo de dados foram cumpridas.")
    return True


def gerar_debug_visual(pdf_path: Path, output_dir: Path, paginas_amostra: List[int]) -> None:
    """
    Desenha os bounding boxes extraídos sobre as páginas originais do PDF
    e salva como PNG em output_dir/debug_visual/ para inspeção humana.
    """
    debug_dir = output_dir / "debug_visual"
    debug_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "document.jsonl"

    # Cores RGB (0..1) por tipo de elemento
    cores_tipo = {
        "text": (0.0, 0.4, 1.0),    # Azul
        "image": (0.0, 0.7, 0.2),   # Verde
        "shape": (1.0, 0.5, 0.0),   # Laranja
        "vector": (0.9, 0.1, 0.1),  # Vermelho
        "link": (0.6, 0.0, 0.8),    # Roxo
    }

    conjunto_amostra = set(paginas_amostra)
    doc = fitz.open(pdf_path)

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for linha in f:
            dados_pag = json.loads(linha)
            p_num = dados_pag["page"]
            if p_num not in conjunto_amostra:
                continue

            page = doc[p_num - 1]
            shape_canvas = page.new_shape()

            for el in dados_pag["elements"]:
                el_type = el["type"]
                b = el["bbox"]
                rect = fitz.Rect(b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"])
                cor = cores_tipo.get(el_type, (0, 0, 0))

                shape_canvas.draw_rect(rect)
                shape_canvas.finish(color=cor, width=0.8, stroke_opacity=0.85)

                # Anota o spatial_order nos blocos de texto para validar a ordenação
                if el_type == "text" or el_type == "image" or el_type == "vector":
                    ordem = str(el["spatial_order"])
                    shape_canvas.insert_text(
                        fitz.Point(rect.x0, max(8, rect.y0 - 1)),
                        f"#{ordem}",
                        fontsize=6,
                        color=cor,
                    )

            shape_canvas.commit()
            pix = page.get_pixmap(dpi=150)
            caminho_img = debug_dir / f"debug_pagina_{p_num:03d}.png"
            pix.save(str(caminho_img))
            print(f"🖼️  Imagem de debug gerada: {caminho_img}")

    doc.close()


if __name__ == "__main__":
    from utils.caminhos import raiz

    pdf_path = raiz["dados_extraidos"]["pdf_original"]
    diretorio_saida = raiz["dados_extraidos"]["conteudo_extraido"]["path"]

    ok = validar_regras_extracao(Path(diretorio_saida))
    if ok:
        
        debug_dir = raiz["dados_extraidos"]["conteudo_extraido"]["debug_visual"]["path"]
        if debug_dir.exists():
            for f in debug_dir.glob("debug_pagina_*.png"):
                f.unlink()

        # Escolha páginas variadas: capa (1), índice (2-4), páginas de conteúdo e tabelas (ex: 20, 50, 120)
        paginas_amostra = [1, 2, 6, 9, 12, 15, 18, 22, 32, 33, 35]
        gerar_debug_visual(pdf_path, Path(diretorio_saida), paginas_amostra=paginas_amostra)
