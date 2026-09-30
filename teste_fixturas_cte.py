#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fixturas de CT-e — inventadas aqui, do zero.

    NENHUM XML real, nenhum CNPJ real, nenhum certificado. Os CNPJ são
    fictícios com DV correto (senão a normalização os recusa) e não pertencem a
    empresa nenhuma do cadastro.

POR QUE FIXTURA E NÃO AMOSTRA REAL
    Um CT-e real traz nome, endereço e valor de frete de um cliente do
    escritório. Ele não entra no repositório nem nos testes: o repositório é
    versionado, e o que entra nele sai da máquina junto.

    O custo é ter de construir a chave de acesso com o dígito verificador
    certo — que é o que `chave_cte()` faz.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

# Lido pelo runner por AST, sem importar: este arquivo é APOIO, não suíte.
TESTE_APOIO = True

NS_CTE = "http://www.portalfiscal.inf.br/cte"

# ── identidades fictícias, com DV válido ────────────────────────────────────
TRANSPORTADORA = "11222333000181"     # emitente do CT-e nos testes
EMBARCADOR = "44555666000181"         # remetente / tomador
DESTINO = "77888999000181"            # destinatário
TERCEIRO = "22333444000181"           # expedidor / recebedor
ESCRITORIO = "99000111000165"         # autXML


def _dv(base43: str) -> str:
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = sum(int(c) * pesos[i % 8] for i, c in enumerate(reversed(base43)))
    r = soma % 11
    return "0" if r in (0, 1) else str(11 - r)


def chave_cte(emitente: str = TRANSPORTADORA, numero: int = 1,
              modelo: str = "57", uf: str = "26", aamm: str = "2606") -> str:
    """Chave de 44 dígitos com DV correto. Sem o DV, a identificação recusa."""
    base = (uf + aamm + emitente + modelo + "001" +
            str(numero).zfill(9) + "1" + str(numero).zfill(8))
    return base + _dv(base)


def cte(chave: str, *, emitente=TRANSPORTADORA, remetente=EMBARCADOR,
        destinatario=DESTINO, expedidor="", recebedor="",
        toma="0", toma4="", autxml=(), valor="1500.00",
        com_protocolo=True, modelo="57") -> bytes:
    """Um `cteProc` mínimo porém realista.

    `toma` é o código 0..3 que APONTA para outro participante — o caminho que
    todos os 59 CT-e reais do acervo usam. `toma4` traz o bloco com CNPJ
    próprio, que é o caso raro.
    """
    def bloco(tag, doc, nome, ender_tag):
        if not doc:
            return ""
        return (f"<{tag}><CNPJ>{doc}</CNPJ><IE>1234567</IE>"
                f"<xNome>{nome}</xNome>"
                f"<{ender_tag}><xMun>RECIFE</xMun><UF>PE</UF></{ender_tag}>"
                f"</{tag}>")

    bloco_toma = ""
    if toma4:
        bloco_toma = (f"<toma4><toma>4</toma><CNPJ>{toma4}</CNPJ>"
                      f"<IE>7654321</IE><xNome>TOMADOR PROPRIO LTDA</xNome>"
                      f"</toma4>")
    else:
        bloco_toma = f"<toma3><toma>{toma}</toma></toma3>"

    aut = "".join(f"<autXML><CNPJ>{a}</CNPJ></autXML>" for a in autxml)
    prot = (f"<protCTe versao=\"4.00\"><infProt><chCTe>{chave}</chCTe>"
            f"<nProt>126260000000001</nProt>"
            f"<dhRecbto>2026-06-15T10:05:00-03:00</dhRecbto>"
            f"<cStat>100</cStat><xMotivo>Autorizado o uso do CT-e</xMotivo>"
            f"</infProt></protCTe>") if com_protocolo else ""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cteProc xmlns="{NS_CTE}" versao="4.00">
  <CTe><infCte versao="4.00" Id="CTe{chave}">
    <ide><cUF>26</cUF><mod>{modelo}</mod><serie>1</serie><nCT>1</nCT>
      <dhEmi>2026-06-15T09:00:00-03:00</dhEmi>
      <CFOP>5353</CFOP><natOp>PRESTACAO DE SERVICO DE TRANSPORTE</natOp>
      <tpCTe>0</tpCTe><tpServ>0</tpServ><modal>01</modal>
      <UFIni>PE</UFIni><UFFim>PB</UFFim>
      <xMunIni>RECIFE</xMunIni><xMunFim>JOAO PESSOA</xMunFim>
      {bloco_toma}
    </ide>
    {bloco("emit", emitente, "TRANSPORTADORA FICTICIA LTDA", "enderEmit")}
    {bloco("rem", remetente, "EMBARCADOR FICTICIO LTDA", "enderReme")}
    {bloco("dest", destinatario, "DESTINO FICTICIO LTDA", "enderDest")}
    {bloco("exped", expedidor, "EXPEDIDOR FICTICIO LTDA", "enderExped")}
    {bloco("receb", recebedor, "RECEBEDOR FICTICIO LTDA", "enderReceb")}
    <vPrest><vTPrest>{valor}</vTPrest><vRec>{valor}</vRec></vPrest>
    <imp><ICMS><ICMS00><CST>00</CST><vBC>{valor}</vBC>
      <pICMS>12.00</pICMS><vICMS>180.00</vICMS></ICMS00></ICMS></imp>
    {aut}
  </infCte></CTe>
  {prot}
</cteProc>""".encode("utf-8")


def cte_resumo(chave: str, *, emitente=TRANSPORTADORA, valor="1500.00",
               situacao="1") -> bytes:
    """`resCTe` — o resumo da Distribuição DF-e: emitente, valor, situação."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<resCTe xmlns="{NS_CTE}" versao="1.00">
  <chCTe>{chave}</chCTe><CNPJ>{emitente}</CNPJ>
  <xNome>TRANSPORTADORA FICTICIA LTDA</xNome><IE>1234567</IE>
  <dhEmi>2026-06-15T09:00:00-03:00</dhEmi><tpCTe>0</tpCTe>
  <vTPrest>{valor}</vTPrest><cSitCTe>{situacao}</cSitCTe>
  <nProt>126260000000001</nProt>
</resCTe>""".encode("utf-8")


def evento_cte(chave: str, *, tp="110111", seq="1", orgao="26",
               autor=TRANSPORTADORA, justificativa="LANCAMENTO INCORRETO",
               resumo=False) -> bytes:
    """Evento de CT-e — completo (`procEventoCTe`) ou resumo (`resEvento`).

    O `resEvento` usa o namespace do CT-e de propósito: é ele que separa este
    documento de um `resEvento` de NF-e, que tem exatamente a mesma tag raiz.
    """
    if resumo:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<resEvento xmlns="{NS_CTE}" versao="1.00">
  <chCTe>{chave}</chCTe><CNPJ>{autor}</CNPJ><cOrgao>{orgao}</cOrgao>
  <tpEvento>{tp}</tpEvento><nSeqEvento>{seq}</nSeqEvento>
  <dhEvento>2026-06-20T14:00:00-03:00</dhEvento>
  <xEvento>Cancelamento</xEvento><nProt>126260000000009</nProt>
</resEvento>""".encode("utf-8")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<procEventoCTe xmlns="{NS_CTE}" versao="4.00">
  <eventoCTe><infEvento Id="ID{tp}{chave}{seq.zfill(2)}">
    <cOrgao>{orgao}</cOrgao><tpAmb>1</tpAmb><CNPJ>{autor}</CNPJ>
    <chCTe>{chave}</chCTe>
    <dhEvento>2026-06-20T14:00:00-03:00</dhEvento>
    <tpEvento>{tp}</tpEvento><nSeqEvento>{seq}</nSeqEvento>
    <detEvento versaoEvento="4.00"><evCancCTe>
      <descEvento>Cancelamento</descEvento>
      <nProt>126260000000001</nProt><xJust>{justificativa}</xJust>
    </evCancCTe></detEvento>
  </infEvento></eventoCTe>
  <retEventoCTe><infEvento><cStat>135</cStat>
    <xEvento>Cancelamento</xEvento><nProt>126260000000009</nProt>
    <dhRegEvento>2026-06-20T14:01:00-03:00</dhRegEvento>
  </infEvento></retEventoCTe>
</procEventoCTe>""".encode("utf-8")
