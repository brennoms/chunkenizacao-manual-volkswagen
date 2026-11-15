import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Optional, Set

import pymupdf as fitz  # PyMuPDF


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

            for el in elementos:
                el_id = el.get("id")
                el_type = el.get("type")
                contagem_tipos[el_type] += 1

                # Regra 2: ID único global
                if el_id in ids_vistos:
                    erros.append(f"ID duplicado encontrado: {el_id} (página {p_num}).")
                ids_vistos.add(el_id)

                ordens_extracao.append(el.get("extraction_order"))

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

            # Regra 3: extraction_order de 1..N sem repetição
            esperado_seq = list(range(1, qtd_el + 1))
            if sorted(ordens_extracao) != esperado_seq:
                erros.append(f"Página {p_num}: extraction_order inválido.")

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


def _perguntar_tipos_elementos() -> Set[str]:
    tipos_validos = {"text", "image", "shape", "vector", "link"}
    while True:
        resposta = input(
            "Quais tipos deseja observar? "
            "(text, image, shape, vector, link; separados por vírgula; "
            "Enter para todos): "
        ).strip().lower()
        if not resposta:
            return tipos_validos

        tipos_selecionados = {tipo.strip() for tipo in resposta.split(",") if tipo.strip()}
        tipos_invalidos = tipos_selecionados - tipos_validos
        if not tipos_invalidos and tipos_selecionados:
            return tipos_selecionados

        print(f"❌ Tipos inválidos: {', '.join(sorted(tipos_invalidos))}.")


def gerar_debug_visual_pdf(
    pdf_path: Path,
    output_dir: Path,
    tipos_observados: Set[str],
    pdf_saida_path: Optional[Path] = None,
) -> Path:
    """
    Desenha os bounding boxes extraídos diretamente em todas as páginas do PDF
    e salva um novo arquivo PDF completo com as marcações vetoriais.
    """
    debug_dir = output_dir / "debug_visual"
    debug_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "document.jsonl"

    if pdf_saida_path is None:
        sufixo_tipos = "_".join(sorted(tipos_observados)) if len(tipos_observados) < 5 else "completo"
        pdf_saida_path = debug_dir / f"debug_documento_{sufixo_tipos}.pdf"

    # Cores RGB (0..1) por tipo de elemento
    cores_tipo = {
        "text": (0.0, 0.4, 1.0),    # Azul
        "image": (0.0, 0.7, 0.2),   # Verde
        "shape": (1.0, 0.5, 0.0),   # Laranja
        "vector": (0.6, 0.0, 0.8),  # Roxo
        "link": (0.9, 0.1, 0.1),    # Vermelho
    }

    doc = fitz.open(pdf_path)
    total_caixas_desenhadas = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for linha in f:
            dados_pag = json.loads(linha)
            p_num = dados_pag["page"]
            page = doc[p_num - 1]
            shape_canvas = page.new_shape()
            desenhou_na_pagina = False

            for el in dados_pag["elements"]:
                el_type = el["type"]
                if el_type not in tipos_observados:
                    continue

                b = el["bbox"]
                rect = fitz.Rect(b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"])
                cor = cores_tipo.get(el_type, (0, 0, 0))

                shape_canvas.draw_rect(rect)
                shape_canvas.finish(color=cor, width=0.6, stroke_opacity=0.85)

                ordem = str(el["extraction_order"])
                shape_canvas.insert_text(
                    fitz.Point(rect.x0, max(6, rect.y0 - 1)),
                    f"#{ordem}",
                    fontsize=5,
                    color=cor,
                )
                desenhou_na_pagina = True
                total_caixas_desenhadas += 1

            if desenhou_na_pagina:
                shape_canvas.commit()

    # Salva o PDF anotado com compressão de fluxos (deflate) e limpeza de objetos (garbage=3)
    doc.save(str(pdf_saida_path), garbage=3, deflate=True)
    doc.close()

    print(
        f"📄 PDF de debug visual gerado com sucesso!\n"
        f" - Elementos demarcados: {total_caixas_desenhadas}\n"
        f" - Arquivo salvo em    : {pdf_saida_path}"
    )
    return pdf_saida_path


def validar_extracao(
        pdf_path: Path,
        output_dir: Path,
):
    """
    Função principal para validar a extração do PDF.
    """
    from utils.caminhos import raiz

    ok = validar_regras_extracao(output_dir)
    if ok:
        tipos_observados = _perguntar_tipos_elementos()
        gerar_debug_visual_pdf(
            pdf_path=pdf_path,
            output_dir=output_dir,
            tipos_observados=tipos_observados,
        )

if __name__ == "__main__":
    from utils.caminhos import raiz

    validar_extracao(
        pdf_path = raiz["dados_extraidos"]["pdf_original"], 
        output_dir = raiz["dados_extraidos"]["conteudo_extraido"]["path"]
    )
    