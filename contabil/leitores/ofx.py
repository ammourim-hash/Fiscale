# -*- coding: utf-8 -*-
"""ofx.py — extrato OFX 1.x (SGML) e 2.x (XML), com o mesmo código.

POR QUE NÃO UM PARSER XML
    O OFX 1.x, que é o que a maioria dos bancos brasileiros ainda entrega, é
    SGML: o elemento-folha não fecha (`<TRNAMT>-49.90` e acabou a linha). Um
    parser XML recusa o arquivo inteiro. Aqui a leitura é por marcador:
    `<NOME>valor` até o próximo `<` ou fim de linha — o que serve às duas
    versões, porque no 2.x o próximo `<` é justamente o `</NOME>`.

A CONTAGEM QUE NÃO DEPENDE DE ENTENDER
    Antes de ler qualquer campo, conta-se quantas vezes `<STMTTRN>` aparece no
    texto. Esse número vai para `blocos_brutos`, e a importação recusa se o
    leitor devolver um movimento a menos. Transação com valor ilegível não
    some: vira movimento com `problema`, e a contagem fecha.

UM ARQUIVO, UMA CONTA
    OFX pode trazer mais de uma conta. Nesta fase isso é recusado com o nome
    do motivo — misturar duas contas num extrato só é o tipo de erro que
    aparece meses depois, na conciliação.
"""
from __future__ import annotations

import html
import re

from .. import modelo
from . import Leitura, MovLido, SemLeitor, decodificar

_BLOCO = re.compile(r"<STMTTRN>", re.I)
_FIM_BLOCO = re.compile(r"</STMTTRN>|<STMTTRN>|</BANKTRANLIST>", re.I)
_CHARSET = re.compile(r"CHARSET:\s*([\w-]+)|encoding=[\"']([\w-]+)", re.I)


def _campo(bloco: str, nome: str) -> str:
    m = re.search(r"<%s>([^<\r\n]*)" % nome, bloco, re.I)
    return html.unescape(m.group(1)).strip() if m else ""


def _data(v: str) -> str:
    """`20260115120000[-3:BRT]` → `2026-01-15`. Vazio se não houver."""
    d = re.match(r"\s*(\d{8})", v or "")
    if not d:
        return ""
    return modelo.data_iso(d.group(1))


def _valor(v: str) -> int:
    s = (v or "").strip()
    # No OFX o ponto é o decimal. Banco que escreve vírgula existe, e aí ela
    # é o decimal — nunca há separador de milhar num TRNAMT.
    return modelo.centavos(s, decimal_ponto="," not in s)


def _contas(texto: str) -> list:
    achadas = []
    for m in re.finditer(r"<(BANKACCTFROM|CCACCTFROM)>(.*?)(</\1>|<BANKTRANLIST>|"
                         r"<STMTTRN>)", texto, re.I | re.S):
        corpo = m.group(2)
        conta = (_campo(corpo, "BANKID"), _campo(corpo, "BRANCHID"),
                 _campo(corpo, "ACCTID"))
        if conta not in achadas:
            achadas.append(conta)
    return achadas


def ler(bruto: bytes) -> Leitura:
    cab = bruto[:600].decode("ascii", errors="replace")
    m = _CHARSET.search(cab)
    texto = decodificar(bruto, (m.group(1) or m.group(2)) if m else "")
    linhas_total = len(texto.splitlines())

    contas = _contas(texto)
    if len(contas) > 1:
        raise SemLeitor(
            "este OFX traz %d contas diferentes; nesta fase cada arquivo "
            "precisa ser de uma conta só" % len(contas))
    banco, agencia, conta = contas[0] if contas else ("", "", "")

    inicios = [x.start() for x in _BLOCO.finditer(texto)]
    lei = Leitura(formato="OFX", linhas_total=linhas_total,
                  blocos_brutos=len(inicios), banco=banco, agencia=agencia,
                  conta=conta, moeda=_campo(texto, "CURDEF"))

    for ordem, ini in enumerate(inicios, start=1):
        fim = _FIM_BLOCO.search(texto, ini + len("<STMTTRN>"))
        bloco = texto[ini:fim.start() if fim else len(texto)]
        linha = texto.count("\n", 0, ini) + 1
        mov = MovLido(ordem=ordem, linha=linha)
        problemas = []
        mov.tipo_operacao = _campo(bloco, "TRNTYPE").upper()
        mov.id_transacao = _campo(bloco, "FITID")
        try:
            postado = _data(_campo(bloco, "DTPOSTED"))
            usuario = _data(_campo(bloco, "DTUSER"))
        except modelo.Invalido:
            postado = usuario = ""
            problemas.append("data ilegível")
        mov.data = usuario or postado
        mov.data_lancamento = postado or usuario
        if not mov.data and "data ilegível" not in problemas:
            problemas.append("sem data")
        bruto_valor = _campo(bloco, "TRNAMT")
        try:
            mov.valor = _valor(bruto_valor) if bruto_valor else None
            if mov.valor is None:
                problemas.append("sem valor")
        except modelo.Invalido:
            mov.valor = None
            problemas.append("valor ilegível: %r" % bruto_valor[:30])
        nome = _campo(bloco, "NAME")
        memo = _campo(bloco, "MEMO")
        partes = [p for p in (memo, nome) if p]
        if len(partes) == 2 and (nome.upper() in memo.upper()):
            partes = [memo]
        mov.descricao = " | ".join(partes)
        mov.documento = _campo(bloco, "CHECKNUM") or _campo(bloco, "REFNUM")
        mov.problema = "; ".join(problemas)
        lei.movimentos.append(mov)

    bal = re.search(r"<LEDGERBAL>(.*?)(</LEDGERBAL>|<AVAILBAL>|</STMTRS>)",
                    texto, re.I | re.S)
    if bal:
        try:
            lei.saldo_final = _valor(_campo(bal.group(1), "BALAMT"))
            lei.saldo_final_data = _data(_campo(bal.group(1), "DTASOF"))
        except modelo.Invalido:
            lei.avisos.append("saldo final ilegível")
    if not inicios and "<OFX>" not in texto.upper():
        raise SemLeitor("o arquivo parece OFX pelo nome, mas não tem a "
                        "estrutura de um OFX")
    return lei
