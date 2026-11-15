import json
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional


def formatar_elemento_markdown(elemento: Dict[str, Any]) -> Optional[str]:
    """
    Converte um registro do document_preparado.jsonl em sua representação textual/Markdown,
    preservando integralmente o conteúdo original conforme o tipo de elemento.
    """
    el_type = elemento.get("type")

    if el_type == "heading":
        level = elemento.get("level", 1)
        content = (elemento.get("content") or "").strip()
        if not content:
            return None
        prefixo = "#" * max(1, min(level, 6))
        return f"{prefixo} {content}"

    elif el_type == "paragraph":
        content = (elemento.get("content") or "").strip()
        return content if content else None

    elif el_type == "table":
        markdown = (elemento.get("markdown") or "").strip()
        return markdown if markdown else None

    elif el_type == "list":
        lead_text = (elemento.get("lead_text") or "").strip()
        items = elemento.get("items") or []
        list_style = elemento.get("list_style", "unordered")

        linhas: List[str] = []
        if lead_text:
            linhas.append(lead_text)

        for idx, item in enumerate(items, start=1):
            item_limpo = str(item).strip()
            if not item_limpo:
                continue
            if list_style == "ordered":
                marcador = f"{idx}."
            elif list_style == "checklist":
                marcador = "- [x]"
            else:
                marcador = "-"
            linhas.append(f"{marcador} {item_limpo}")

        return "\n".join(linhas) if linhas else None

    elif el_type == "alert_box":
        alert_level = (elemento.get("alert_level") or "ALERTA").strip()
        lead_text = (elemento.get("lead_text") or "").strip()
        items = elemento.get("items") or []

        partes = [f"**[{alert_level}]**"]
        if lead_text:
            partes.append(lead_text)
        for item in items:
            item_limpo = str(item).strip()
            if item_limpo:
                partes.append(f"- {item_limpo}")

        return "\n".join(partes) if len(partes) > 1 else None

    elif el_type == "image":
        figure_label = (elemento.get("figure_label") or "").strip()
        caption = (elemento.get("caption") or "").strip()
        description = (elemento.get("description") or "").strip()
        legend_items = elemento.get("legend_items") or []

        partes: List[str] = []
        if caption:
            partes.append(f"**{caption}**")
        elif figure_label:
            partes.append(f"**{figure_label}**")

        if description:
            partes.append(f"*[Imagem: {description}]*")

        for item in legend_items:
            item_limpo = str(item).strip()
            if item_limpo:
                partes.append(f"- {item_limpo}")

        return "\n".join(partes) if partes else None

    elif el_type == "icon":
        icon_id = (elemento.get("icon_id") or "icon").strip()
        description = (elemento.get("description") or "").strip()
        if description:
            return f"*[Ícone {icon_id}: {description}]*"
        return f"*[Ícone {icon_id}]*"

    else:
        fallback = (elemento.get("content") or elemento.get("markdown") or "").strip()
        return fallback if fallback else None


def gerar_documentos_extraidos(
    document_entrada: Path,
    pdf_original_path: Path,
    saida_pdf_original: Path,
    saida_ocr: Path,
    saida_texto_bruto: Path,
) -> Dict[str, Path]:
    """
    Lê o document_preparado.jsonl (da Fase 3) e gera em chunks/02_extracted/:
    1. chunks/02_extracted/manual_tecnico_volkswagen_ocr.jsonl
    2. chunks/02_extracted/manual_tecnico_volkswagen_texto_bruto.txt
    """

    # 1. Garantir que manual_tecnico_volkswagen_ocr.jsonl seja a cópia exata da saída da Fase 3 e que uma cópia seja feita do arquivo PDF original
    if document_entrada.resolve() != saida_ocr.resolve():
        shutil.copy2(document_entrada, saida_ocr)
    if pdf_original_path.resolve() != saida_pdf_original.resolve():
        shutil.copy2(pdf_original_path, saida_pdf_original)

    # 2. Carregar registros a partir do manual_tecnico_volkswagen_ocr.jsonl para montar o texto_bruto.txt
    registros: List[Dict[str, Any]] = []
    with open(saida_ocr, "r", encoding="utf-8") as f:
        for num_linha, linha in enumerate(f, start=1):
            linha = linha.strip()
            if not linha:
                continue
            try:
                registros.append(json.loads(linha))
            except json.JSONDecodeError as e:
                raise ValueError(f"Erro ao decodificar JSON na linha {num_linha}: {e}")

    # 3. Ordenar estritamente pela ordem de leitura (reading_order)
    registros.sort(
        key=lambda x: (
            x.get("reading_order", float("inf")),
            x.get("page_start", float("inf")),
            x.get("read_order", float("inf")),
        )
    )

    blocos_texto_bruto: List[str] = []
    pagina_atual: Optional[int] = None
    total_renderizados = 0

    # 4. Processar cada elemento para o texto bruto
    for reg in registros:
        texto_renderizado = formatar_elemento_markdown(reg)
        if not texto_renderizado:
            continue

        page_start = reg.get("page_start")
        if page_start is not None and page_start != pagina_atual:
            pagina_atual = page_start
            blocos_texto_bruto.append(f"<!-- Página {pagina_atual} -->")

        blocos_texto_bruto.append(texto_renderizado)
        total_renderizados += 1

    # 5. Salvar manual_tecnico_volkswagen_texto_bruto.txt
    texto_final = "\n\n".join(blocos_texto_bruto) + "\n"
    with open(saida_texto_bruto, "w", encoding="utf-8") as f_txt:
        f_txt.write(texto_final)

    print("Processamento concluído com sucesso:")
    print(f" - Registros totais:    {len(registros)}")
    print(f" - Blocos renderizados: {total_renderizados}")
    print(f" - Arquivo OCR JSONL:   {saida_ocr}")
    print(f" - Arquivo Texto Bruto: {saida_texto_bruto}")

    return {
        "ocr_jsonl": saida_ocr,
        "texto_bruto": saida_texto_bruto,
    }


if __name__ == "__main__":
    from utils.caminhos import raiz

    gerar_documentos_extraidos(
        document_entrada = raiz["dados_preparados"]["document_preparado"],
        pdf_original_path = raiz["dados_extraidos"]["pdf_original"],
        saida_pdf_original = raiz["chunks"]["01_raw"]["pdf_original"],
        saida_ocr = raiz["chunks"]["02_extracted"]["ocr"],
        saida_texto_bruto = raiz["chunks"]["02_extracted"]["texto_bruto"],
    )