# Métodos de extração

Este documento apresenta, de forma resumida, os métodos e procedimentos
utilizados para baixar os arquivos e preparar seu conteúdo. A implementação
desse processo está disponível em [src/extracao.py](../../src/extracao.py).

# Download do Manual Tecnico da Volkswagen

O documento foi obtido por meio do link:

- [https://www.vw.com.br/idhub/content/dam/onehub_pkw/importers/br/literatura-de-bordo/manual-nivus/21B.5B1.NIV.66%20-%20Manual%20de%20instru%C3%A7%C3%B5es%20-%20NIVUS.pdf](https://www.vw.com.br/idhub/content/dam/onehub_pkw/importers/br/literatura-de-bordo/manual-nivus/21B.5B1.NIV.66%20-%20Manual%20de%20instru%C3%A7%C3%B5es%20-%20NIVUS.pdf).

Após o download, o PDF foi salvo como
`dados_extraidos/manual_tecnico_volkswagen.pdf`. Para evitar o uso de um
arquivo incompleto ou alterado, foi calculado um hash SHA-256 e armazenado em
`dados_extraidos/manual_tecnico_volkswagen.pdf.sha256`. Nas execuções
seguintes, o hash do arquivo é comparado com esse registro antes de seu uso;
se o arquivo não existir ou não corresponder, ele é baixado novamente.

