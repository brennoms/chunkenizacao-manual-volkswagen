from utils.caminhos import raiz
from utils.constantes import URL_MANUAL_TECNICO_VOLKSWAGEN
from utils.funcoes import calcular_hash_sha256, verificar_hash_sha256, salvar_em_arquivo, baixar_pdf


pdf_ok = False

if (raiz.dados_extraidos.pdf_original.exists() and raiz.dados_extraidos.pdf_original_hash.exists()):
    pdf_ok = verificar_hash_sha256(
        raiz.dados_extraidos.pdf_original, 
        raiz.dados_extraidos.pdf_original_hash
    )

if not pdf_ok:
    print("O arquivo PDF não está presente ou o hash não corresponde ao esperado.")
    baixar_pdf(URL_MANUAL_TECNICO_VOLKSWAGEN, raiz.dados_extraidos.pdf_original)
    salvar_em_arquivo(
        raiz.dados_extraidos.pdf_original_hash, 
        calcular_hash_sha256(raiz.dados_extraidos.pdf_original)
    )
