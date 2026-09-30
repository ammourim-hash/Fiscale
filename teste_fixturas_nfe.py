#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teste_fixturas_nfe.py — documentos fiscais FICTÍCIOS para as suítes.

POR QUE ISTO É UM MÓDULO SEPARADO
    As suítes do projeto são scripts: rodam ao serem importadas. Quando a ING 3B
    fez `import teste_ingestao3` só para reaproveitar as fixtures, ela executou
    a suíte inteira da ING 3A no meio de si mesma — e, pior, o `finally` da ING
    3A restaurou `socket.connect`, desligando a trava de rede da ING 3B sem que
    nada avisasse.

    Fixture é dado; suíte é programa. Separar os dois evita esse acoplamento.

REGRA DESTE ARQUIVO: NADA AQUI É REAL
    CNPJ e chaves de acesso são inventados, com dígito verificador calculado
    para serem aceitos pelas validações. Nenhum dado de cliente, nenhum
    certificado, nenhuma senha — nem fictícia dentro de campo de senha, porque
    rótulo de fixture com cara de segredo já derrotou varredura neste projeto.

POR QUE O DV É CALCULADO E NÃO CHUMBADO
    Um CNPJ ou uma chave com DV errado seria rejeitado por `identidade.py` e
    `identificacao.py`, e o teste passaria a exercitar o caminho de erro sem que
    ninguém percebesse. Calcular o DV mantém as fixtures no caminho feliz.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ / "nfse" / "backend") not in sys.path:
    sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

from ingestao import identificacao as idf              # noqa: E402
from ingestao.distribuicao import DocumentoBruto       # noqa: E402

# Lido pelo runner por AST, sem importar: declara que este arquivo é
# APOIO, não suíte. Rodá-lo não executa asserção nenhuma, e contá-lo
# como suíte verde seria contar um arquivo que nunca testou nada.
TESTE_APOIO = True


# ── identificadores fictícios ───────────────────────────────────────────────
def cnpj_ficticio(base12: str) -> str:
    """Completa os dois dígitos verificadores de um CNPJ inventado."""
    c = base12[:12]
    for _ in range(2):
        pesos = list(range(len(c) - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(c, pesos)) % 11
        c += str(0 if resto < 2 else 11 - resto)
    return c


EMPRESA_A = cnpj_ficticio("111111110001")
EMPRESA_B = cnpj_ficticio("222222220001")
FORNECEDOR = cnpj_ficticio("333333330001")
CPF_AUTONOMO = "12345678909"        # CPF fictício com DV válido


def chave_ficticia(cnpj: str, numero: int = 1, serie: int = 1,
                   aamm: str = "2608", cuf: str = "26") -> str:
    """Chave de acesso de 44 dígitos com DV correto.

    2 cUF + 4 AAMM + 14 CNPJ + 2 modelo + 3 série + 9 número + 1 tpEmis
    + 8 código numérico + 1 DV.
    """
    corpo = f"{cuf}{aamm}{cnpj}55{serie:03d}{numero:09d}1{numero:08d}"
    if len(corpo) != 43:
        raise ValueError(f"corpo da chave com {len(corpo)} dígitos, esperava 43")
    return corpo + str(idf.dv_chave(corpo + "0"))


CHAVE_1 = chave_ficticia(FORNECEDOR, 1)
CHAVE_2 = chave_ficticia(FORNECEDOR, 2)


# ── documentos ──────────────────────────────────────────────────────────────
def xml_nfe(chave: str, *, valor="1234.56", emitente=FORNECEDOR,
            destinatario=EMPRESA_A, numero="1", serie="1",
            com_protocolo=True, com_pis=True, itens=1,
            natureza="VENDA DE MERCADORIA", saida_entrada="",
            emissao="2026-08-01T09:00:00-03:00", cfop="5102") -> bytes:
    """`nfeProc` mínima porém realista.

    `com_pis=False` omite o grupo PIS inteiro — é assim que se testa
    "ausente != zero" sem simular o valor zero.

    `saida_entrada=""` OMITE `dhSaiEnt`, que é o padrão de propósito: o campo
    é opcional na NF-e, e a fixtura sem ele é a que prova que ausência sai
    como ausência. Quem quer testar a presença passa a data.

    `emissao` e `cfop` existem desde a CONTÁBIL 2: a COMPETÊNCIA sai da data
    de emissão e a NATUREZA sai do CFOP, e sem poder mudá-los não dá para
    montar dois meses nem uma operação que não seja venda. Os padrões são os
    de sempre — nenhuma suíte antiga muda de comportamento."""
    det = ""
    for n in range(1, itens + 1):
        pis = ("""<PIS><PISAliq><CST>01</CST><vBC>1000.00</vBC>
                  <pPIS>1.65</pPIS><vPIS>16.50</vPIS></PISAliq></PIS>"""
               if com_pis else "")
        det += f"""
      <det nItem="{n}">
        <prod>
          <cProd>COD{n}</cProd><xProd>PRODUTO {n}</xProd>
          <NCM>84713012</NCM><CFOP>{cfop}</CFOP><uCom>UN</uCom>
          <qCom>2.0000</qCom><vUnCom>617.2800000000</vUnCom>
          <vProd>{valor}</vProd>
        </prod>
        <imposto>
          <ICMS><ICMS00><CST>00</CST><vBC>1000.00</vBC>
            <pICMS>18.00</pICMS><vICMS>180.00</vICMS></ICMS00></ICMS>
          {pis}
        </imposto>
      </det>"""
    prot = f"""
  <protNFe versao="4.00"><infProt>
    <chNFe>{chave}</chNFe><nProt>126000000000001</nProt>
    <dhRecbto>2026-08-01T10:05:00-03:00</dhRecbto>
    <cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo>
  </infProt></protNFe>""" if com_protocolo else ""
    sai = f"<dhSaiEnt>{saida_entrada}</dhSaiEnt>" if saida_entrada else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe><infNFe versao="4.00" Id="NFe{chave}">
    <ide><nNF>{numero}</nNF><serie>{serie}</serie><natOp>{natureza}</natOp>
      <tpNF>1</tpNF><finNFe>1</finNFe>
      <dhEmi>{emissao}</dhEmi>{sai}</ide>
    <emit><CNPJ>{emitente}</CNPJ><xNome>FORNECEDOR FICTICIO LTDA</xNome>
      <IE>1234567</IE>
      <enderEmit><xMun>RECIFE</xMun><UF>PE</UF><cMun>2611606</cMun></enderEmit></emit>
    <dest><CNPJ>{destinatario}</CNPJ><xNome>EMPRESA TESTE LTDA</xNome>
      <enderDest><xMun>OLINDA</xMun><UF>PE</UF><cMun>2609600</cMun></enderDest></dest>
    {det}
    <total><ICMSTot>
      <vBC>1000.00</vBC><vICMS>180.00</vICMS><vProd>{valor}</vProd>
      <vNF>{valor}</vNF>
    </ICMSTot></total>
  </infNFe></NFe>{prot}
</nfeProc>""".encode("utf-8")


def xml_resumo(chave: str, *, valor="1234.56", emitente=FORNECEDOR) -> bytes:
    """`resNFe` — o resumo que a Distribuição DF-e entrega ANTES do completo."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<resNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
  <chNFe>{chave}</chNFe><CNPJ>{emitente}</CNPJ>
  <xNome>FORNECEDOR FICTICIO LTDA</xNome><IE>1234567</IE>
  <dhEmi>2026-08-01T09:00:00-03:00</dhEmi><tpNF>1</tpNF>
  <vNF>{valor}</vNF><digVal>abc</digVal><dhRecbto>2026-08-02T08:00:00-03:00</dhRecbto>
  <nProt>126000000000001</nProt><cSitNFe>1</cSitNFe>
</resNFe>""".encode("utf-8")


def xml_evento(chave: str, tp="110111", seq="1", just="Erro de digitacao",
               orgao="26", orgao_resposta=None, omitir_orgao=False) -> bytes:
    """`procEventoNFe`. O padrão é o CANCELAMENTO (tpEvento 110111).

    `orgao_resposta` permite divergir o `cOrgao` do `<retEvento>` do que está
    no `<evento>` — não porque tenha sido observado assim, mas para provar que
    a identidade usa o bloco declarado, e não "o primeiro que aparecer"."""
    org_evento = "" if omitir_orgao else f"<cOrgao>{orgao}</cOrgao>"
    org_ret = (f"<cOrgao>{orgao_resposta}</cOrgao>"
               if orgao_resposta is not None else "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<procEventoNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">
  <evento versao="1.00"><infEvento Id="ID{tp}{chave}{seq.zfill(2)}">
    {org_evento}<CNPJ>{FORNECEDOR}</CNPJ><chNFe>{chave}</chNFe>
    <dhEvento>2026-08-03T11:00:00-03:00</dhEvento>
    <tpEvento>{tp}</tpEvento><nSeqEvento>{seq}</nSeqEvento>
    <detEvento versao="1.00"><descEvento>Cancelamento</descEvento>
      <nProt>126000000000001</nProt><xJust>{just}</xJust></detEvento>
  </infEvento></evento>
  <retEvento versao="1.00"><infEvento>{org_ret}<cStat>135</cStat>
    <nProt>126000000000009</nProt></infEvento></retEvento>
</procEventoNFe>""".encode("utf-8")


def xml_res_evento(chave: str, tp="110111", seq="1",
                   descricao="Cancelamento de NF-e", orgao="26",
                   protocolo="126000000000009", autor=None,
                   omitir=()) -> bytes:
    """`resEvento` — o RESUMO de evento que a Distribuição DF-e entrega.

    ESTRUTURA TOTALMENTE FICTÍCIA, escrita a partir da forma observada em
    15/08/2026: os campos ficam **direto sob a raiz**, sem `infEvento` e sem
    `detEvento`. Nenhum CNPJ, chave, protocolo ou razão social real foi copiado
    para cá — só o formato.

    `omitir` permite tirar campos para testar ausência, porque no resumo
    justificativa e correção simplesmente não existem, e outros campos podem
    faltar dependendo do tipo de evento.
    """
    autor = autor or FORNECEDOR
    campos = [
        ("cOrgao", orgao),
        ("CNPJ", autor),
        ("chNFe", chave),
        ("dhEvento", "2026-08-03T11:00:00-03:00"),
        ("tpEvento", tp),
        ("nSeqEvento", seq),
        ("xEvento", descricao),
        ("dhRecbto", "2026-08-03T11:05:00-03:00"),
        ("nProt", protocolo),
    ]
    corpo = "".join(f"<{k}>{v}</{k}>" for k, v in campos if k not in omitir)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<resEvento xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
{corpo}
</resEvento>""".encode("utf-8")


# Documento de um serviço que a ING 3 ainda não sabe ler. Existe para provar que
# schema desconhecido é PRESERVADO, não descartado.
XML_DESCONHECIDO = b"""<?xml version="1.0"?>
<procCTeOS xmlns="http://www.portalfiscal.inf.br/cte" versao="4.00">
  <CTeOS><infCte Id="CTe000"><ide><nCT>1</nCT></ide></infCte></CTeOS>
</procCTeOS>"""

# XML cortado ao meio, como chega quando a transferência falha.
XML_MALFORMADO = b"""<?xml version="1.0"?><nfeProc><NFe><infNFe Id="NFe123">"""


def bruto(conteudo: bytes, nsu: str, schema="procNFe_v4.00.xsd",
          chave="") -> DocumentoBruto:
    return DocumentoBruto(nsu=nsu, conteudo=conteudo, schema=schema, chave=chave)
