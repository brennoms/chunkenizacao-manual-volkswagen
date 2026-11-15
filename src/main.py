from utils.caminhos import raiz


# Extração
from extracao.download_pdf import baixar_pdf_manual_tecnico
from extracao.extracao_pdf import extrair_pdf_bruto
from extracao.validar_extracao import validar_extracao

print("\n\n===========================================================================")
print("Iniciando o processo de extração do PDF do manual técnico da Volkswagen...")
print("===========================================================================")

print("\nBaixando o PDF do manual técnico da Volkswagen:")
baixar_pdf_manual_tecnico()

print("\nExtraindo o conteúdo do PDF do manual técnico da Volkswagen:")
extrair_pdf_bruto(
    pdf_path = raiz["dados_extraidos"]["pdf_original"],
    output_dir = raiz["dados_extraidos"]["conteudo_extraido"]["path"],
    file_sha256 = raiz["dados_extraidos"]["pdf_original_hash"].read_text(encoding="utf-8").strip(),
)

print("\nValidando a extração do conteúdo do PDF do manual técnico da Volkswagen:")
validar_extracao(
    pdf_path = raiz["dados_extraidos"]["pdf_original"], 
    output_dir = raiz["dados_extraidos"]["conteudo_extraido"]["path"]
)



# Preparação
from preparacao.fase1 import executar_fase1_completo
from preparacao.fase2 import executar_fase2
from preparacao.fase3 import executar_fase3

print("\n\n===========================================================================")
print("Iniciando o processo de preparação do PDF do manual técnico da Volkswagen...")
print("===========================================================================")

print("\nFase 1: Remoção de conteúdo irrelevante:")
executar_fase1_completo(
    jsonl_entrada = raiz["dados_extraidos"]["conteudo_extraido"]["document"],
    jsonl_saida = raiz["dados_preparados"]["document_fase1"],
    pdf_original = raiz["dados_extraidos"]["pdf_original"],
    pdf_debug_saida = raiz["dados_preparados"]["debug_visual"]["fase1_removidos"],
)

print("\nFase 2: Processamento de ícones:")
executar_fase2(
    jsonl_entrada=raiz["dados_preparados"]["document_fase1"],
    jsonl_saida=raiz["dados_preparados"]["document_fase2"],
    pdf_original=raiz["dados_extraidos"]["pdf_original"],
    diretorio_asssets = raiz["dados_preparados"]["assets"]["path"],
    diretorio_icons=raiz["dados_preparados"]["assets"]["icons"],
    arquivo_catalogo=raiz["dados_preparados"]["icons_catalog"],
    pdf_saida_debug=raiz["dados_preparados"]["debug_visual"]["fase2_icones"]
)

print("\nFase 3: Ordenação de leitura:")
executar_fase3(
    jsonl_entrada = raiz["dados_preparados"]["document_fase2"],
    jsonl_saida = raiz["dados_preparados"]["document_preparado"],
    arquivo_descr_images = raiz["descricoes_manuais"]["descricao_images"],
    arquivo_descr_icons = raiz["descricoes_manuais"]["descricao_icones"],
    arquivo_catalogo_icons = raiz["dados_preparados"]["icons_catalog"],
    caminho_debug_pdf = raiz["dados_preparados"]["debug_visual"]["fase3_ordem_leitura"],
    pdf_original = raiz["dados_extraidos"]["pdf_original"],
)


# Chunkenização
from chunkenizacao.gerar_ocr_e_texto_bruto import gerar_documentos_extraidos
from chunkenizacao.gerar_chunks import gerar_chunks

print("\n\n===========================================================================")
print("Iniciando o processo de chunkenização do PDF do manual técnico da Volkswagen...")
print("===========================================================================")

print("\nGerando documentos extraídos do PDF do manual técnico da Volkswagen:")
gerar_documentos_extraidos(
    document_entrada = raiz["dados_preparados"]["document_preparado"],
    pdf_original_path = raiz["dados_extraidos"]["pdf_original"],
    saida_pdf_original = raiz["chunks"]["01_raw"]["pdf_original"],
    saida_ocr = raiz["chunks"]["02_extracted"]["ocr"],
    saida_texto_bruto = raiz["chunks"]["02_extracted"]["texto_bruto"],
)

print("\nGerando chunks do PDF do manual técnico da Volkswagen:")
gerar_chunks(
    diretorio_relativo = raiz["chunks"]["path"],
    caminho_ocr_jsonl = raiz["chunks"]["02_extracted"]["ocr"],
    saida_chunks = raiz["chunks"]["03_chunks"]["chunks"],
    saida_manifest = raiz["chunks"]["03_chunks"]["manifest"]
)
