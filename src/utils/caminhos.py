from pathlib import Path

_raiz = Path(__file__).parent.parent.parent

raiz = {

    "path": _raiz,

    "dados_extraidos": {
        "path": _raiz / "dados_extraidos",
        "pdf_original": _raiz / "dados_extraidos" / "manual_tecnico_volkswagen.pdf",
        "pdf_original_hash": _raiz / "dados_extraidos" / "manual_tecnico_volkswagen.pdf.sha256",
        "conteudo_extraido": {
            "path": _raiz / "dados_extraidos" / "conteudo_extraido",
            "metadata": _raiz / "dados_extraidos" / "conteudo_extraido" / "metadata.jsonl",
            "document": _raiz / "dados_extraidos" / "conteudo_extraido" / "document.jsonl",
            "assets": {
                "path": _raiz / "dados_extraidos" / "conteudo_extraido" / "assets"
            },
            "debug_visual": {
                "path": _raiz / "dados_extraidos" / "conteudo_extraido" / "debug_visual"
            }
        },
    },
    "dados_preparados": {
        "path": _raiz / "dados_preparados",
        "document_fase1": _raiz / "dados_preparados" / "document_fase1.jsonl",
        "document_fase2": _raiz / "dados_preparados" / "document_fase2.jsonl",
        "icons_catalog": _raiz / "dados_preparados" / "icons_catalog.json",
        "document_preparado": _raiz / "dados_preparados" / "document_preparado.jsonl",
        "descricao_images": _raiz / "dados_preparados" / "descricao_images.json",
        "assets": {
            "path": _raiz / "dados_preparados" / "assets",
            "icons": _raiz / "dados_preparados" / "assets" / "icons",
        },
        "debug_visual": {
            "path": _raiz / "dados_preparados" / "debug_visual",
            "fase1_removidos": _raiz / "dados_preparados" / "debug_visual" / "debug_fase1_removidos.pdf",
            "fase2_icones": _raiz / "dados_preparados" / "debug_visual" / "debug_fase2_icones.pdf",
            "fase3_ordem_leitura": _raiz / "dados_preparados" / "debug_visual" / "debug_fase3_ordem_leitura.pdf"
        }
    }

}

def garantir_diretorios(diretorio: dict):
    for c in diretorio.values():
        if isinstance(c, dict):
            garantir_diretorios(c)
        elif isinstance(c, Path) and c.suffix == "" and not c.exists():
            c.mkdir(parents=True, exist_ok=True)
            if '__name__' == '__main__':
                print(f"Diretório {c} criado com sucesso.")

garantir_diretorios(raiz)
