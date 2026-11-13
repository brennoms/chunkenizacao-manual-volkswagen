from utils.caminhos import raiz
from utils.constantes import URL_MANUAL_TECNICO_VOLKSWAGEN
from utils.funcoes import calcular_hash_sha256, verificar_hash_sha256, salvar_em_arquivo, baixar_pdf


#=========================================================================================================
# Baixa o PDF do manual técnico da Volkswagen, caso ele não exista ou o hash não corresponda ao esperado.
#=========================================================================================================

pdf_ok = False
pdf_path = raiz.dados_extraidos.pdf_original
pdf_hash_path = raiz.dados_extraidos.pdf_original_hash

if (pdf_path.exists() and pdf_hash_path.exists()):
    pdf_ok = verificar_hash_sha256(pdf_path, pdf_hash_path)

if not pdf_ok:
    print("O arquivo PDF não está presente ou o hash não corresponde ao esperado.")
    baixar_pdf(URL_MANUAL_TECNICO_VOLKSWAGEN, pdf_path)
    salvar_em_arquivo(pdf_hash_path, calcular_hash_sha256(pdf_path))