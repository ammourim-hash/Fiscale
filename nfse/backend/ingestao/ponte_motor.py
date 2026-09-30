"""
ponte_motor.py — da operação normalizada para o motor fiscal QUE JÁ EXISTE.

O QUE ESTA PONTE É
    Um tradutor de vocabulário. Ela pega uma `Operacao` e devolve o dicionário
    exatamente na forma que `classificador.calcular_das()` e
    `apuracao_federal.apurar()` já esperam receber hoje.

O QUE ELA NÃO É, E NÃO PODE VIRAR
    **Não há fórmula aqui.** Nenhuma alíquota, nenhuma tabela, nenhuma decisão
    de anexo, nenhuma regra de ISS. Se um dia aparecer um cálculo neste
    arquivo, o desenho falhou: passariam a existir duas apurações, e a segunda
    divergiria da primeira em silêncio — que é exatamente o risco que a
    APURAÇÃO 1 foi montada para evitar.

    A ponte existe para provar o contrário: que o caminho novo chega ao MESMO
    motor, com os MESMOS números.

RASTREABILIDADE ATRAVESSA
    Cada dicionário leva `id_documento` junto. O motor atual ignora esse campo
    — dicionário extra não incomoda ninguém —, mas ele fica lá para a
    APURAÇÃO 5 conseguir responder "quais notas formaram este valor" sem ter
    que reconstruir o caminho de trás para frente.

POR QUE `float` NA SAÍDA
    Porque é o que o motor atual consome. A operação normalizada guarda
    `Decimal`; a conversão acontece **aqui, na fronteira**, e em nenhum outro
    lugar. Quando o motor um dia falar `Decimal`, some esta função e nada mais
    muda.
"""
from __future__ import annotations

from decimal import Decimal

from . import normalizacao as nz


def _f(v: Decimal | None) -> float:
    """`Decimal` → `float`, só na fronteira com o motor atual."""
    return 0.0 if v is None else float(v)


def para_classificador(operacoes) -> list[dict]:
    """`Operacao` → a "nota" que `calcular_das()` recebe hoje.

    Os campos são os que o motor lê: `competencia`, `valor`, `cancelada`,
    `item_lc116`, `iss_retido`, `valor_iss` e, quando houver, `anexo_manual`.
    Nada é calculado; o que não existe fica vazio, e o motor decide o que
    fazer com isso — como já decide hoje.
    """
    saida = []
    for op in operacoes:
        if op.especie != nz.NFSE:
            # NF-e ainda não entra na apuração. Deixar passar caladamente
            # colocaria mercadoria na base de serviço.
            continue
        saida.append({
            "id_documento": op.id_documento,          # rastreabilidade (D44)
            "origem": op.origem.fonte_leitor,
            "arquivo": op.origem.arquivo,
            "chave": op.origem.chave,
            "competencia": op.competencia.strftime("%Y-%m") if op.competencia else "",
            "data": op.data_emissao.isoformat() if op.data_emissao else "",
            "valor": _f(op.valor_bruto),
            "cancelada": not op.entra_na_receita,
            "item_lc116": op.item_lc116,
            "ctribnac": op.codigo_servico,
            "iss_retido": op.retencoes.iss is not None and op.retencoes.iss > 0,
            "valor_iss": _f(op.retencoes.iss),
            "numero": op.numero,
            "municipio": op.municipio_incidencia,
            "tipo": "Serviço",
        })
    return saida


def para_federal(operacoes, *, identidade_empresa: str = "") -> list[dict]:
    """`Operacao` → a "nota" que `apuracao_federal.apurar()` recebe hoje.

    O módulo federal filtra as EMITIDAS por `emit_cnpj`; a operação já sabe o
    sentido, então o emitente é preenchido a partir dele — sem reimplementar
    a regra de "quem prestou o serviço sofre a retenção", que continua lá.
    """
    saida = []
    for op in operacoes:
        if op.especie != nz.NFSE:
            continue
        emit = op.emitente or (identidade_empresa if op.sentido == nz.SAIDA else "")
        saida.append({
            "id_documento": op.id_documento,
            "chave": op.origem.chave,
            "competencia": op.competencia.strftime("%Y-%m") if op.competencia else "",
            "cancelada": not op.entra_na_receita,
            "emit_cnpj": emit,
            "valor_servico": _f(op.valor_bruto),
            "valor_pis": _f(op.retencoes.pis),
            "valor_cofins": _f(op.retencoes.cofins),
            "valor_csll": _f(op.retencoes.csll),
            "valor_irrf": _f(op.retencoes.irrf),
            "valor_retido": _f(op.retencoes.pis) + _f(op.retencoes.cofins)
                            + _f(op.retencoes.csll),
            "valor_iss_retido": _f(op.retencoes.iss),
            "iss_retido": op.retencoes.iss is not None and op.retencoes.iss > 0,
        })
    return saida


def documentos_da_competencia(operacoes, competencia: str) -> list[str]:
    """Os `id_documento` que formam a base daquele mês.

    É a semente da rastreabilidade da D44: hoje devolve a lista; quando o
    motor souber devolver linha a linha, é este conjunto que vai casar com o
    valor apurado."""
    return sorted(op.id_documento for op in operacoes
                  if op.entra_na_receita and op.competencia
                  and op.competencia.strftime("%Y-%m") == competencia)
