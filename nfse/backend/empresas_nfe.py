#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quais empresas o módulo NF-e mostra — e por que as outras ficam de fora.

    from empresas_nfe import separar
    r = separar(certificados, clientes)
    r["operacionais"]   # o que vai para o seletor
    r["pendencias"]     # o que NÃO vai, com o motivo escrito

REGRA
    NF-e é documento de circulação de mercadoria: quem participa dela tem
    Inscrição Estadual. Empresa sem IE cadastrada não aparece no seletor
    operacional do módulo.

ESTE MÓDULO NÃO É UM CADASTRO
    Ele não guarda nada. É uma LEITURA sobre o cadastro que já existe
    (`state_clientes.json`, o mesmo do módulo Clientes) cruzada com os
    certificados. Criar uma segunda lista de empresas só para a NF-e seria
    criar uma segunda verdade — e no dia em que as duas divergissem, ninguém
    saberia qual vale.

    Por consequência, IE cadastrada depois faz a empresa aparecer sozinha, e
    IE apagada a faz sumir sozinha. Não há sincronização a rodar.

ESCONDER NÃO É APAGAR
    Empresa fora do seletor continua com acervo, índice, XML e checkpoint
    intactos. Ela sai da lista de trabalho, não do sistema. É por isso que
    `pendencias` existe: sumir em silêncio seria a mesma falha que
    `universo_do_ciclo` já custou uma vez.

O QUE NÃO SE FAZ AQUI
    Não se valida dígito verificador de IE. Seriam 27 algoritmos estaduais,
    ou uma consulta à SEFAZ — e consultar a SEFAZ para desenhar um `<select>`
    seria caro e frágil. A pergunta respondida é "há IE cadastrada?", não
    "esta IE é verdadeira perante o fisco".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import fiscale_cadastro as _cad          # noqa: E402

import re

# ── o que NÃO conta como Inscrição Estadual ─────────────────────────────────
# São valores que alguém digita para preencher o campo sem ter o dado. Vale
# notar `ISENTO`: ele não é lixo — é um estado real e legítimo. Mas quem é
# isento não tem inscrição, então para a pergunta deste módulo ("há IE?") a
# resposta é a mesma: não há.
_PLACEHOLDERS = {
    "", "-", "--", "---", ".", "0", "00", "N/A", "NA", "N.A.", "NULL", "NONE",
    "NAO", "NÃO", "NAO TEM", "NÃO TEM", "NAO POSSUI", "NÃO POSSUI",
    "SEM IE", "SEM INSCRICAO", "SEM INSCRIÇÃO", "ISENTO", "ISENTA",
    "NAO CONTRIBUINTE", "NÃO CONTRIBUINTE", "PENDENTE", "X", "XX", "XXX",
    "?", "??",
}

MOTIVO_SEM_IE = "SEM_INSCRICAO_ESTADUAL"
MOTIVO_SEM_CADASTRO = "SEM_CADASTRO_DE_CLIENTE"
MOTIVO_INATIVA = "EMPRESA_INATIVA"

# Como a situação aparece para o usuário. Fica aqui, e não na tela, porque
# tela e teste precisam falar a mesma língua — e porque o dia em que um rótulo
# mudar, ele muda num lugar só.
ROTULO_SITUACAO = {
    MOTIVO_SEM_CADASTRO: "SEM CADASTRO EM CLIENTES",
    MOTIVO_SEM_IE: "SEM INSCRIÇÃO ESTADUAL",
    MOTIVO_INATIVA: "EMPRESA INATIVA",
}

ROTULO_SUGESTAO = "IE SUGERIDA PELOS DOCUMENTOS"

EXPLICACAO = {
    MOTIVO_SEM_IE:
        "a empresa está cadastrada em Clientes, mas o campo Inscrição "
        "Estadual está vazio ou preenchido com um valor que não é uma "
        "inscrição.",
    MOTIVO_SEM_CADASTRO:
        "existe certificado para esta empresa, mas ela ainda não foi "
        "cadastrada em Clientes — então não há onde a Inscrição Estadual "
        "estar.",
    MOTIVO_INATIVA:
        "a empresa está marcada como inativa no cadastro.",
}


def so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def ie_valida(valor) -> bool:
    """Há uma Inscrição Estadual cadastrada aqui?

    Conservador de propósito: recusa o que é claramente ausência, e aceita o
    resto. Um filtro esperto demais tiraria da tela uma empresa que trabalha
    — e o usuário não teria como saber por quê.
    """
    bruto = str(valor or "").strip()
    if not bruto:
        return False
    if bruto.upper() in _PLACEHOLDERS:
        return False
    digitos = so_digitos(bruto)
    if len(digitos) < 2:          # "IE" sem número não é inscrição
        return False
    if set(digitos) == {"0"}:     # 0, 00, 000000000…
        return False
    return True


def esta_ativa(cliente: dict) -> bool:
    """Ausência de marca significa ATIVA.

    O FISCALE ainda não tem o conceito de empresa inativa — nenhum cliente
    grava esse campo hoje. O suporte fica pronto para quando existir, e o
    padrão é o que não muda o comportamento de ninguém agora.
    """
    if not isinstance(cliente, dict):
        return True
    for chave in ("ativo", "ativa"):
        if chave in cliente:
            return bool(cliente[chave])
    for chave in ("inativo", "inativa"):
        if chave in cliente:
            return not bool(cliente[chave])
    return True


def indexar_clientes(clientes) -> dict:
    """CNPJ só com dígitos → cliente. O cadastro grava formatado."""
    saida = {}
    for c in (clientes or []):
        if isinstance(c, dict):
            chave = so_digitos(c.get("cnpj"))
            if chave:
                saida[chave] = c
    return saida


def separar(certificados, clientes, *, docs_por_cnpj=None) -> dict:
    """Divide as empresas entre as que operam NF-e e as que estão pendentes.

    `docs_por_cnpj` é opcional e só enriquece a pendência: saber que a
    empresa fora do seletor tem 6.513 documentos no acervo é a diferença
    entre "cadastro incompleto" e "urgente".
    """
    por_cnpj = indexar_clientes(clientes)
    docs = {so_digitos(k): v for k, v in (docs_por_cnpj or {}).items()}

    operacionais, pendencias = [], []
    for cert in (certificados or []):
        if not isinstance(cert, dict):
            continue
        cnpj = so_digitos(cert.get("cnpj"))
        cli = por_cnpj.get(cnpj)
        base = {
            "id": cert.get("id"),
            "cnpj": cert.get("cnpj"),
            "nome": cert.get("nome") or cert.get("apelido") or cert.get("cnpj"),
            "apelido": cert.get("apelido") or "",
        }
        if cli is None:
            motivo = MOTIVO_SEM_CADASTRO
        elif not esta_ativa(cli):
            motivo = MOTIVO_INATIVA
        elif not ie_valida(cli.get("ie")):
            motivo = MOTIVO_SEM_IE
        else:
            operacionais.append({**base, "ie": str(cli.get("ie")).strip()})
            continue

        pendencias.append({
            **base,
            "motivo": motivo,
            "explicacao": EXPLICACAO[motivo],
            "ie_cadastrada": (cli or {}).get("ie") or "",
            "documentos_no_acervo": int(docs.get(cnpj, 0)),
        })

    # ORDEM CENTRAL para o seletor: Razão Social, Nome Fantasia, documento —
    # sem acento e sem caixa. É a mesma de Clientes, Certificados e NFS-e, e
    # vem de um lugar só, para que a mesma empresa não apareça em posições
    # diferentes conforme a tela.
    operacionais = _cad.ordenar(operacionais)

    # A lista de PENDÊNCIAS é outra coisa, e por isso tem outra ordem: aqui o
    # que interessa é quem tem documento parado, não o alfabeto. O desempate,
    # esse sim, usa a ordem central.
    pendencias.sort(key=lambda p: (-p["documentos_no_acervo"],
                                   _cad.chave_ordenacao(p)))
    com_acervo = [p for p in pendencias if p["documentos_no_acervo"] > 0]
    return {
        "operacionais": operacionais,
        "pendencias": pendencias,
        "total_certificados": len(certificados or []),
        "pendencias_com_acervo": len(com_acervo),
        "aviso": (
            "%d empresa%s tem documentos NF-e no acervo, mas não tem "
            "Inscrição Estadual cadastrada. Os documentos continuam "
            "guardados; a empresa só não aparece no seletor."
            % (len(com_acervo), "" if len(com_acervo) == 1 else "s")
        ) if com_acervo else "",
    }
