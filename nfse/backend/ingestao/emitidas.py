# -*- coding: utf-8 -*-
"""emitidas.py — o que a EMPRESA emitiu, lido do acervo que já existe.

O QUE ESTA CAMADA É
    Uma PERGUNTA respondida sobre o acervo indexado: "destes documentos, quais
    a empresa emitiu, o que o acervo fez com eles, e o que ficou de fora?".

    Ela não captura, não importa, não grava, não deduplica e não consulta a
    SEFAZ. Tudo isso já existe e continua onde está: a captura nas fontes
    (`fontes_emissao`), a gravação no portão (`importacao`), a deduplicação no
    acervo (`id_documento` é chave primária) e a filtragem no `consulta`.

O QUE ELA DELIBERADAMENTE NÃO É
    **Não é uma segunda fonte de NF-e.** A emissão própria chega por onde já
    chegava — a pasta vigiada e, no futuro, uma API de fornecedor. Se este
    módulo passar a buscar documento em algum lugar, virou fonte paralela, e é
    exatamente o que a APURAÇÃO 6B gastou uma fase para não ter: mais de um
    caminho gravando documento fiscal.

    **Não é apuração.** Nenhum número daqui alimenta DAS, PIS, COFINS, IRPJ ou
    CSLL. Quem calcula receita de venda é `vendas.py`, a partir das operações
    normalizadas — outra camada, outra pergunta.

POR QUE `papel` E NÃO `tpNF`
    `tpNF` é do EMITENTE: ler `tpNF` para decidir o sentido da operação já fez
    301 compras virarem "saídas". Quem diz se a empresa emitiu é o `papel` dela
    no documento, que a normalização gravou no índice comparando o CNPJ do
    emitente com o da empresa. Aqui só se lê esse campo.

QUAL IDENTIFICADOR DEDUPLICA
    A **chave de acesso** (44 dígitos). Ela é o identificador fiscal do
    documento, e é por ela que duas entregas do mesmo XML são a mesma NF-e.
    Este módulo **não deduplica** — o portão já fez isso. Ele só RELATA: se
    `linhas_com_chave` e `chaves_distintas` divergirem, há duplicidade no
    índice e isso é defeito a investigar, não número a esconder.

TRÊS ESTADOS, E O TERCEIRO NÃO É SILÊNCIO
    encontrados   linhas no índice com a empresa como emitente
    processados   as que o índice leu inteiras (fora da quarentena)
    com_erro      as que entraram em quarentena — schema desconhecido, falha de
                  parser, conteúdo recusado. Existem, têm motivo gravado, e
                  aparecem. Somar erro com sucesso, ou omiti-lo, produz uma
                  tela em que nada bate.

    `encontrados == processados + com_erro`, sempre. É asserção de teste.
"""
from __future__ import annotations

from . import consulta as cq
from . import documento as dm
from .identidade import normalizar

# O papel da empresa que define "emitida". Um só, e vem do vocabulário do
# domínio — não é string escrita aqui.
PAPEL = dm.EMITENTE

# NF-e e NFC-e são o mesmo acervo; o que as separa é o modelo da chave.
ESPECIES = (dm.NFE55, dm.NFCE65)

# Ordem de apresentação das situações. Os VALORES são os do domínio; o que
# mora aqui é só a ordem em que a tela os lê.
SITUACOES = (dm.AUTORIZADO, dm.CANCELADO, dm.DENEGADO,
             dm.SEM_PROTOCOLO, dm.INDEFINIDA)

# O identificador fiscal que deduplica. Declarado para que a tela possa dizer
# ao usuário por qual campo dois arquivos são o mesmo documento.
IDENTIFICADOR = "chave"


def filtro(base: cq.Filtro | None = None, *, especies=None) -> cq.Filtro:
    """O filtro desta área: o que vier, com `papel` forçado em EMITENTE.

    Forçar é o ponto. Se a origem do filtro é a querystring da tela, alguém
    pode mandar `papel=DESTINATARIO` para uma rota chamada "emitidas" — e a
    rota responderia compras com nome de venda.
    """
    f = base or cq.Filtro()
    if especies:
        f = f.com(especies=tuple(especies))
    return f.com(papel=PAPEL)


def listar(dados_dir, identidade, base: cq.Filtro | None = None, **kw) -> cq.Pagina:
    """Uma página de documentos emitidos. Delegação pura ao `consulta`.

    Nenhuma consulta própria: paginação, ordenação e recorte continuam sendo
    do SQLite, na camada que já sabe fazer isso.
    """
    return cq.consultar(dados_dir, identidade, filtro(base), **kw)


def resumo(dados_dir, identidade, base: cq.Filtro | None = None, *,
           especies=None) -> dict:
    """Os números da área, todos contados pelo índice.

    `base` é o filtro da tela (período, situação, texto…). Ele é respeitado —
    menos o `papel`, que esta área define.
    """
    f = filtro(base, especies=especies)

    encontrados = cq.contar(dados_dir, identidade, f)
    com_erro = cq.contar(dados_dir, identidade, f.com(quarentena=True))
    processados = cq.contar(dados_dir, identidade, f.com(quarentena=False))
    com_chave, distintas = cq.contar_chaves(dados_dir, identidade, f)

    por_especie = {}
    for esp in (especies or f.especies or ESPECIES):
        n = cq.contar(dados_dir, identidade, f.com(especies=(esp,)))
        if n:
            por_especie[esp] = n

    por_situacao = {}
    for sit in SITUACOES:
        n = cq.contar(dados_dir, identidade, f.com(situacoes_atuais=(sit,)))
        if n:
            por_situacao[sit] = n

    # "Não há emitida" e "não há acervo" são respostas DIFERENTES, e a tela
    # precisa das duas para não dizer "nada encontrado" a quem nunca capturou.
    # A contagem é sem `papel` e sem o período da tela: é sobre existir acervo.
    total_no_acervo = cq.contar(dados_dir, identidade,
                                cq.Filtro(especies=f.especies))

    ident = normalizar(identidade)
    return {
        "empresa": ident.mascarado(),
        "papel": PAPEL,
        "identificador_de_deduplicacao": IDENTIFICADOR,
        # ── os três estados ─────────────────────────────────────────────
        "encontrados": encontrados,
        "processados": processados,
        "com_erro": com_erro,
        # ── deduplicação: relatada, nunca reaplicada ────────────────────
        "chaves_distintas": distintas,
        "linhas_com_chave": com_chave,
        "sem_chave": max(0, encontrados - com_chave),
        "duplicidade_no_indice": max(0, com_chave - distintas),
        # ── recortes ────────────────────────────────────────────────────
        "por_especie": por_especie,
        "por_situacao": por_situacao,
        # ── contexto para o estado vazio ────────────────────────────────
        "total_no_acervo": total_no_acervo,
        "tem_acervo": total_no_acervo > 0,
        "apenas_conferencia": (
            "leitura do acervo — não alimenta DAS, PIS, COFINS, IRPJ nem CSLL"),
    }
