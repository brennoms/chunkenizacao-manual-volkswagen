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
            "document": _raiz / "dados_extraidos" / "conteudo_extraido" / "document.jsonl"
        },
    },

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
