from pathlib import Path

TEST = False

_raiz = Path(__file__).parent.parent.parent

raiz = {

    "path": _raiz,

    "descricoes_manuais": {
        "descricao_images": _raiz / "descricoes_manuais" / "descricao_images.json",
        "descricao_icones": _raiz / "descricoes_manuais" / "descricao_icones.json",
    },

    "dados_extraidos": {
        "path": _raiz / "OUTPUT" / "dados_extraidos",
        "pdf_original": _raiz / "OUTPUT" / "dados_extraidos" / "manual_tecnico_volkswagen.pdf",
        "pdf_original_hash": _raiz / "OUTPUT" / "dados_extraidos" / "manual_tecnico_volkswagen.pdf.sha256",
        "conteudo_extraido": {
            "path": _raiz / "OUTPUT" / "dados_extraidos" / "conteudo_extraido",
            "metadata": _raiz / "OUTPUT" / "dados_extraidos" / "conteudo_extraido" / "metadata.jsonl",
            "document": _raiz / "OUTPUT" / "dados_extraidos" / "conteudo_extraido" / "document.jsonl",
            "assets": {
                "path": _raiz / "OUTPUT" / "dados_extraidos" / "conteudo_extraido" / "assets"
            },
            "debug_visual": {
                "path": _raiz / "OUTPUT" / "dados_extraidos" / "conteudo_extraido" / "debug_visual"
            }
        },
    },

    "dados_preparados": {
        "path": _raiz / "OUTPUT" / "dados_preparados",
        "document_fase1": _raiz / "OUTPUT" / "dados_preparados" / "document_fase1.jsonl",
        "document_fase2": _raiz / "OUTPUT" / "dados_preparados" / "document_fase2.jsonl",
        "icons_catalog": _raiz / "OUTPUT" / "dados_preparados" / "icons_catalog.json",
        "document_preparado": _raiz / "OUTPUT" / "dados_preparados" / "document_preparado.jsonl",
        "assets": {
            "path": _raiz / "OUTPUT" / "dados_preparados" / "assets",
            "icons": _raiz / "OUTPUT" / "dados_preparados" / "assets" / "icons",
            "paridade": _raiz / "OUTPUT" / "dados_preparados" / "assets" / "paridade",
            "all_icons": _raiz / "OUTPUT" / "dados_preparados" / "assets" / "all_icons",
        },
        "debug_visual": {
            "path": _raiz / "OUTPUT" / "dados_preparados" / "debug_visual",
            "fase1_removidos": _raiz / "OUTPUT" / "dados_preparados" / "debug_visual" / "debug_fase1_removidos.pdf",
            "fase2_icones": _raiz / "OUTPUT" / "dados_preparados" / "debug_visual" / "debug_fase2_icones.pdf",
            "fase3_ordem_leitura": _raiz / "OUTPUT" / "dados_preparados" / "debug_visual" / "debug_fase3_ordem_leitura.pdf"
        }
    },

    "chunks": {
        "path": _raiz / "OUTPUT" / "chunks",
        "01_raw": {
            "path": _raiz / "OUTPUT" / "chunks" / "01_raw",
            "pdf_original": _raiz / "OUTPUT" / "chunks" / "01_raw" / "manual_tecnico_volkswagen.pdf"
        },
        "02_extracted": {
            "path": _raiz / "OUTPUT" / "chunks" / "02_extracted",
            "document_preparado": _raiz / "OUTPUT" / "chunks" / "02_extracted" / "document_preparado.jsonl",
            "ocr": _raiz / "OUTPUT" / "chunks" / "02_extracted" / "manual_tecnico_volkswagen_ocr.jsonl",
            "texto_bruto": _raiz / "OUTPUT" / "chunks" / "02_extracted" / "manual_tecnico_volkswagen_texto_bruto.txt",
        },
        "03_chunks": {
            "path": _raiz / "OUTPUT" / "chunks" / "03_chunks",
            "chunks": _raiz / "OUTPUT" / "chunks" / "03_chunks" / "chunks.jsonl",
            "manifest": _raiz / "OUTPUT" / "chunks" / "03_chunks" / "manifest.json"
        }
    }

}

if TEST:
    def incluir_test(diretorio: dict):
        for key, c in diretorio.items():
            if isinstance(c, dict):
                incluir_test(c)
            elif isinstance(c, Path):
                diretorio[key] = _raiz / "test" / c.relative_to(_raiz)
    incluir_test(raiz)

def garantir_diretorios(diretorio: dict):
    for c in diretorio.values():
        if isinstance(c, dict):
            garantir_diretorios(c)
        elif isinstance(c, Path) and c.suffix == "" and not c.exists():
            c.mkdir(parents=True, exist_ok=True)
            if '__name__' == '__main__':
                print(f"Diretório {c} criado com sucesso.")

garantir_diretorios(raiz)
