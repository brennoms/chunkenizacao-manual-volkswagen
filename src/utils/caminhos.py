from pathlib import Path
from types import SimpleNamespace


_raiz = Path(__file__).parent.parent.parent

_dict = {

    "path": _raiz,

    "dados_extraidos": {
        "path": _raiz / "dados_extraidos",
        "pdf_original": _raiz / "dados_extraidos" / "manual_tecnico_volkswagen.pdf",
        "pdf_original_hash": _raiz / "dados_extraidos" / "manual_tecnico_volkswagen.pdf.sha256",
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

def para_namespace(valor):
    if isinstance(valor, dict):
        return SimpleNamespace(
            **{
                chave: para_namespace(conteudo)
                for chave, conteudo in valor.items()
            }
        )

    return valor


garantir_diretorios(_dict)
raiz = para_namespace(_dict)
