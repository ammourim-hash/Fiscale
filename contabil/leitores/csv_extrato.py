# -*- coding: utf-8 -*-
"""csv_extrato.py — extrato em planilha CSV, de banco que for.

NÃO HÁ "O" CSV DE EXTRATO
    Cada banco exporta colunas diferentes, em ordem diferente, com separador
    diferente, e com linhas de enfeite antes do cabeçalho (nome do cliente,
    agência, período). O leitor não conhece banco: ele ACHA o cabeçalho pelos
    nomes das colunas e mapeia por significado:

        data         DATA, DT, DATA MOVIMENTO, DATA LANÇAMENTO...
        descrição    HISTÓRICO, DESCRIÇÃO, LANÇAMENTO, MEMO, DETALHE...
        documento    DOCUMENTO, DOC, Nº DOC...
        valor        VALOR — ou CRÉDITO e DÉBITO em colunas separadas
        sentido      D/C, C/D, TIPO, NATUREZA (quando o valor vem sem sinal)
        saldo        SALDO

CADA LINHA PRESTA CONTAS
    O arquivo inteiro é lido linha a linha, e cada linha física termina em
    exatamente um lugar:

        movimento                  tem valor (mesmo que a data falhe: aí vai
                                   com `problema`, e aparece na tela)
        PREÂMBULO                  antes do cabeçalho
        CABEÇALHO                  a própria linha de títulos
        EM BRANCO                  vazia
        SALDO INFORMATIVO          "SALDO ANTERIOR", "SALDO DO DIA"...
        SEM VALOR                  linha sem valor nenhum (continuação,
                                   rodapé, total) — o texto fica guardado

    A importação confere que movimentos + ignoradas = total de linhas.

O QUE ESTE LEITOR NÃO FAZ
    Não junta linha de continuação com a anterior (alguns bancos quebram o
    histórico em duas linhas). Juntar exigiria saber o banco, e a regra aqui
    é não adivinhar: a segunda linha fica visível, como SEM VALOR, com o
    texto dela.
"""
from __future__ import annotations

import csv
import re

from .. import modelo
from . import Leitura, MovLido, SemLeitor, decodificar

PREAMBULO = "PREAMBULO"
CABECALHO = "CABECALHO"
EM_BRANCO = "EM_BRANCO"
SALDO_INFO = "SALDO_INFORMATIVO"
SEM_VALOR = "SEM_VALOR"


def _n(s: str) -> str:
    return modelo.normalizar_texto(s).replace(".", " ").replace("_", " ") \
        .replace("  ", " ").strip()


def _papel_coluna(nome: str) -> str:
    n = _n(nome)
    n = re.sub(r"\(.*?\)", "", n).strip()
    if not n:
        return ""
    if n.startswith("SALDO"):
        return "saldo"
    if n in ("D/C", "C/D", "DC", "CD", "D C", "C D", "NATUREZA", "SINAL",
             "TIPO", "DEBITO/CREDITO", "CREDITO/DEBITO", "TIPO LANCAMENTO"):
        return "sentido"
    if n.startswith("DATA") or n.startswith("DT ") or n == "DT":
        if "LANC" in n or "CONTAB" in n or "POST" in n:
            return "data_lancamento"
        return "data"
    if n in ("CREDITO", "CREDITOS", "ENTRADA", "ENTRADAS", "VALOR CREDITO",
             "CREDITO R$", "RECEBIMENTOS"):
        return "credito"
    if n in ("DEBITO", "DEBITOS", "SAIDA", "SAIDAS", "VALOR DEBITO",
             "DEBITO R$", "PAGAMENTOS"):
        return "debito"
    if n.startswith("VALOR") or n in ("MONTANTE", "QUANTIA", "VLR", "VL"):
        return "valor"
    if n in ("DOCUMENTO", "DOC", "N DOC", "NR DOC", "NUMERO DOCUMENTO",
             "NUMERO DO DOCUMENTO", "N DOCUMENTO", "Nº DOC", "NO DOC",
             "N° DOC", "N DO DOCUMENTO", "DOCTO", "NUM DOC", "COMPROVANTE"):
        return "documento"
    if n.startswith(("N DOC", "NUMERO DOC", "Nº DOC", "NO DOC", "N° DOC")):
        return "documento"
    if n in ("ID", "ID TRANSACAO", "IDENTIFICADOR", "CODIGO TRANSACAO",
             "AUTENTICACAO"):
        return "id_transacao"
    if n in ("TIPO OPERACAO", "TIPO DE OPERACAO", "TIPO TRANSACAO",
             "CATEGORIA", "OPERACAO"):
        return "tipo_operacao"
    if n in ("HISTORICO", "DESCRICAO", "LANCAMENTO", "LANCAMENTOS", "MEMO",
             "DETALHE", "DETALHES", "COMPLEMENTO", "TITULO", "DESCRICAO DO "
             "LANCAMENTO", "HISTORICO DO LANCAMENTO", "INFORMACOES",
             "FAVORECIDO", "NOME", "ESTABELECIMENTO", "OBSERVACAO"):
        return "descricao"
    if n.startswith(("HISTORICO", "DESCRICAO")):
        return "descricao"
    return ""


def _delimitador(linhas: list) -> str:
    amostra = [l for l in linhas[:40] if l.strip()]
    melhor, pontos = ",", -1
    for d in (";", "\t", ",", "|"):
        contagens = [l.count(d) for l in amostra]
        if not contagens:
            continue
        # O separador certo aparece MUITAS vezes e com a mesma frequência
        # nas linhas de dados.
        frequente = max(set(contagens), key=contagens.count)
        p = frequente * contagens.count(frequente)
        if frequente and p > pontos:
            melhor, pontos = d, p
    return melhor


def _celulas(linha: str, delim: str) -> list:
    return next(csv.reader([linha], delimiter=delim)) if linha else []


def _mapa(celulas: list) -> dict:
    mapa = {}
    for i, c in enumerate(celulas):
        p = _papel_coluna(c)
        if not p:
            continue
        if p == "descricao":
            mapa.setdefault("descricao", []).append(i)
        elif p not in mapa:
            mapa[p] = i
        elif p == "data" and "data_lancamento" not in mapa:
            mapa["data_lancamento"] = i
    return mapa


def _eh_cabecalho(mapa: dict) -> bool:
    return "data" in mapa and ("valor" in mapa or "credito" in mapa
                               or "debito" in mapa)


def _conta_do_preambulo(texto: str, lei: Leitura) -> None:
    n = modelo.normalizar_texto(texto)
    m = re.search(r"AGENCIA\W*([\d-]{2,8})", n)
    if m and not lei.agencia:
        lei.agencia = m.group(1)
    m = re.search(r"CONTA(?: CORRENTE)?\W*([\dX-]{3,20})", n)
    if m and not lei.conta:
        lei.conta = m.group(1)
    m = re.search(r"BANCO\W*(\d{3})\b", n)
    if m and not lei.banco:
        lei.banco = m.group(1)


def ler(bruto: bytes) -> Leitura:
    texto = decodificar(bruto)
    linhas = texto.splitlines()
    lei = Leitura(formato="CSV", linhas_total=len(linhas))
    if not linhas:
        raise SemLeitor("arquivo vazio")
    delim = _delimitador(linhas)

    cab_idx, mapa = None, {}
    for i, l in enumerate(linhas[:60]):
        try:
            m = _mapa(_celulas(l, delim))
        except csv.Error:
            continue
        if _eh_cabecalho(m):
            cab_idx, mapa = i, m
            break
    if cab_idx is None:
        raise SemLeitor(
            "não achei o cabeçalho do extrato: o CSV precisa de uma linha com "
            "o nome das colunas, com ao menos DATA e VALOR (ou CRÉDITO/DÉBITO)")

    for i in range(cab_idx):
        conteudo = linhas[i]
        if conteudo.strip():
            _conta_do_preambulo(conteudo, lei)
        lei.ignoradas.append((i + 1, conteudo,
                              PREAMBULO if conteudo.strip() else EM_BRANCO))
    lei.ignoradas.append((cab_idx + 1, linhas[cab_idx], CABECALHO))

    ordem = 0
    for i in range(cab_idx + 1, len(linhas)):
        bruta = linhas[i]
        nlinha = i + 1
        if not bruta.strip() or not bruta.replace(delim, "").strip():
            lei.ignoradas.append((nlinha, bruta, EM_BRANCO))
            continue
        try:
            cel = _celulas(bruta, delim)
        except csv.Error:
            cel = bruta.split(delim)

        def c(nome):
            j = mapa.get(nome)
            return cel[j].strip() if j is not None and j < len(cel) else ""

        descricao = " ".join(cel[j].strip() for j in mapa.get("descricao", [])
                             if j < len(cel) and cel[j].strip())
        problemas = []
        valor = None
        try:
            if "valor" in mapa and c("valor"):
                valor = modelo.centavos(c("valor"))
            else:
                cr, db = c("credito"), c("debito")
                if cr and modelo.centavos(cr) != 0:
                    valor = abs(modelo.centavos(cr))
                elif db and modelo.centavos(db) != 0:
                    valor = -abs(modelo.centavos(db))
                elif cr or db:
                    valor = 0
        except modelo.Invalido:
            problemas.append("valor ilegível")
        sentido = modelo.normalizar_texto(c("sentido"))
        if valor is not None and sentido:
            if sentido in ("D", "DEBITO", "-", "SAIDA"):
                valor = -abs(valor)
            elif sentido in ("C", "CREDITO", "+", "ENTRADA"):
                valor = abs(valor)

        eh_saldo = modelo.normalizar_texto(descricao).startswith(
            ("SALDO", "S A L D O")) or (not descricao and c("saldo")
                                        and valor is None)
        if eh_saldo:
            lei.ignoradas.append((nlinha, bruta, SALDO_INFO))
            try:
                s = c("saldo") or (c("valor") if "valor" in mapa else "")
                if s:
                    lei.saldo_final = modelo.centavos(s)
                    lei.saldo_final_data = modelo.data_iso(c("data")) \
                        if c("data") else lei.saldo_final_data
            except modelo.Invalido:
                pass
            continue
        if valor is None and not problemas:
            lei.ignoradas.append((nlinha, bruta, SEM_VALOR))
            continue

        ordem += 1
        # Coluna "TIPO" que não traz D/C traz o tipo da operação ("PIX",
        # "TED"): é informação, e fica no campo dela.
        tipo_op = c("tipo_operacao")
        if not tipo_op and sentido and sentido not in (
                "D", "C", "DEBITO", "CREDITO", "+", "-", "SAIDA", "ENTRADA"):
            tipo_op = c("sentido")
        mov = MovLido(ordem=ordem, linha=nlinha, descricao=descricao,
                      documento=c("documento"), valor=valor,
                      tipo_operacao=tipo_op,
                      id_transacao=c("id_transacao"))
        try:
            mov.data = modelo.data_iso(c("data"))
        except modelo.Invalido:
            problemas.append("data ilegível: %r" % c("data")[:20])
        if "data_lancamento" in mapa and c("data_lancamento"):
            try:
                mov.data_lancamento = modelo.data_iso(c("data_lancamento"))
            except modelo.Invalido:
                problemas.append("data de lançamento ilegível")
        mov.data_lancamento = mov.data_lancamento or mov.data
        if c("saldo"):
            try:
                mov.saldo = modelo.centavos(c("saldo"))
            except modelo.Invalido:
                pass
        mov.problema = "; ".join(problemas)
        lei.movimentos.append(mov)

    if not lei.movimentos:
        raise SemLeitor("o CSV tem cabeçalho mas nenhuma linha de movimento")
    return lei
