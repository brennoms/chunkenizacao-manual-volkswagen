from pathlib import Path

def calcular_hash_sha256(arquivo: Path):
    """
    Calcula o hash SHA-256 de um arquivo.

    Args:
        nome_arquivo (str): O nome do arquivo para calcular o hash.
    
    Returns:
        str: O hash SHA-256 do arquivo.
    """
    import hashlib

    sha256_hash = hashlib.sha256()
    with open(arquivo, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def verificar_hash_sha256(arquivo: Path, arquivo_hash_esperado: Path):
    """
    Verifica se o hash SHA-256 de um arquivo corresponde ao hash esperado.

    Args:
        arquivo (str): O nome do arquivo para verificar o hash.
        arquivo_hash_esperado (str): O nome do arquivo que contém o hash esperado.
    
    Returns:
        bool: True se os hashes corresponderem, False caso contrário.
    """
    if arquivo.exists() and arquivo_hash_esperado.exists():
        with open(arquivo_hash_esperado, 'r') as f:
            hash_esperado = f.read().strip()
        hash_calculado = calcular_hash_sha256(arquivo)
        return hash_calculado == hash_esperado
    return False


def salvar_em_arquivo(arquivo: Path, conteudo: str):
    """
    Salva o conteúdo em um arquivo.

    Args:
        arquivo (str): O nome do arquivo para salvar o conteúdo.
        conteudo (str): O conteúdo a ser salvo no arquivo.
    """
    with open(arquivo, 'w', encoding='utf-8') as f:
        f.write(conteudo)


def baixar_pdf(url: str, arquivo: Path):
    """
    Baixa um arquivo PDF de uma URL e salva com o nome especificado.

    Args:
        url (str): A URL do arquivo PDF a ser baixado.
        nome_arquivo (str): O nome do arquivo para salvar o PDF.
    """
    import requests

    response = requests.get(url)
    if response.status_code == 200:
        with open(arquivo, 'wb') as f:
            f.write(response.content)
        print(f"Arquivo {arquivo} baixado com sucesso.")
    else:
        print(f"Falha ao baixar o arquivo. Status code: {response.status_code}")

