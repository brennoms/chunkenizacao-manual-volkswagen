from utils.caminhos import raiz


# Extração
from extracao.download_pdf import baixar_pdf_manual_tecnico
from extracao.extracao_pdf import extrair_pdf_bruto
from extracao.validar_extracao import validar_extracao


print("\nIniciando o processo de extração do PDF do manual técnico da Volkswagen...\n")
baixar_pdf_manual_tecnico()
extrair_pdf_bruto(
    pdf_path = raiz["dados_extraidos"]["pdf_original"],
    output_dir = raiz["dados_extraidos"]["conteudo_extraido"]["path"],
    file_sha256 = raiz["dados_extraidos"]["pdf_original_hash"].read_text(encoding="utf-8").strip(),
)
# Ative se quiser validar a extração e gerar o PDF de debug visual
#validar_extracao(
#    pdf_path = raiz["dados_extraidos"]["pdf_original"], 
#    output_dir = raiz["dados_extraidos"]["conteudo_extraido"]["path"]
#)


# Preparação
from preparacao.fase1 import executar_fase1_completo

