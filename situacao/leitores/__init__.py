# -*- coding: utf-8 -*-
"""leitores — quem abre o PDF e diz o que está escrito nele.

O CONTRATO, E POR QUE ELE É TÃO ESTREITO
    Um leitor recebe bytes e devolve um dicionário. Só isso.

        ler(bruto: bytes) -> dict

    Ele NÃO grava, NÃO indexa, NÃO decide empresa e NÃO conhece o portão. E,
    acima de tudo:

    **UM LEITOR NUNCA LEVANTA.** Layout de órgão muda sem aviso — a Receita
    reformata o relatório, a prefeitura troca o gerador de PDF — e no dia em
    que isso acontece o que não pode acontecer é a tela quebrar ou o índice
    ficar pela metade. Leitor que não entende devolve `{}`, o índice fica sem
    validade, e o estado derivado vira `SEM_VALIDADE`: "o documento está aqui,
    mas eu não sei o que ele diz".

    Isso é honesto e é seguro. O oposto — devolver um palpite — produziria uma
    certidão marcada como válida até uma data que ninguém leu.

CAMPO QUE NÃO FOI LIDO NÃO APARECE
    Um leitor devolve só o que conseguiu ler. Nada de `validade: ""` para
    "campo existe mas está vazio": ausência é a única forma de dizer "não sei",
    e ela precisa ser distinguível de um valor.

A VERSÃO DO LEITOR ENTRA NO ÍNDICE
    `leitura_versao` sobe quando o leitor muda. É o que permite reprocessar
    depois — o original está no acervo, e reler é sempre possível.
"""
from __future__ import annotations

VERSAO_DESCONHECIDA = 0


def registro() -> dict:
    """`{(esfera, tipo): leitor}` — quem sabe ler o quê, hoje.

    Importado aqui dentro para que o pacote não exija `pypdf` só para ser
    importado: quem não vai ler PDF não precisa da biblioteca.
    """
    from .. import modelo
    from . import sitfis
    return {(modelo.FEDERAL, modelo.EXTRATO): sitfis.ler}


def ler(esfera: str, tipo: str, bruto: bytes) -> dict:
    """Lê com o leitor da esfera/tipo, ou devolve `{}` se não houver leitor.

    Não conhecer o formato é um resultado legítimo, e não um erro: o municipal
    e o estadual ainda não têm leitor, e os documentos deles continuam sendo
    guardados e listados — apenas sem validade lida.
    """
    try:
        leitor = registro().get((esfera, tipo))
    except Exception:
        return {}
    if leitor is None:
        return {}
    try:
        return leitor(bruto) or {}
    except Exception:
        # A MESMA REGRA, MAIS UMA VEZ: nem o leitor mais quebrado derruba quem
        # o chamou. O documento já está guardado; perder a tela por causa da
        # leitura seria trocar o essencial pelo acessório.
        return {}
