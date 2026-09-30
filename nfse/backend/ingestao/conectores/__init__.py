"""
conectores/ — as peças que conhecem UM serviço oficial cada.

REGRA DA PASTA
    Aqui dentro mora tudo que é específico de um serviço: SOAP, REST, namespace,
    `cStat`, `docZip`, gzip, base64. **Nada disso pode vazar para o núcleo.**

    O núcleo (`distribuicao.py`, `acervo.py`, `indice.py`) fala apenas
    `Fonte`, `Lote` e `DocumentoBruto`. Se um dia o serviço mudar de SOAP para
    REST, só um arquivo desta pasta muda.

O QUE UM CONECTOR NÃO FAZ
    Não interpreta regra tributária, não classifica, não escreve no índice, não
    grava no acervo e não decide quando o checkpoint avança. Ele adquire e
    entrega `DocumentoBruto`. Ponto.

IMPORTAÇÃO PREGUIÇOSA
    Este `__init__` **não** importa os conectores. Um conector pode depender de
    biblioteca que nem sempre está instalada (`requests_pkcs12`, e no futuro
    algum cliente municipal); importar aqui faria o pacote `ingestao` inteiro
    falhar por causa de um serviço que ninguém ia usar naquela execução.

        from ingestao.conectores import nfe_dfe        # explícito, quando precisa
"""
