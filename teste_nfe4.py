#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da NF-e 4 — verdade operacional (4A) e enriquecimento fiscal (4B).

    python teste_nfe4.py

O QUE ESTA SUÍTE PROVA

    4A · a situação de HOJE sai de documento + eventos, sem adulterar nenhum
         dos dois; os cinco papéis vêm da estrutura oficial do XML; e uma
         empresa pode ter mais de um papel no mesmo documento.

    4B · CST e CSOSN param de ser o mesmo campo; CEST, origem, tributos por
         item e os grupos da Reforma são lidos e preservados; e a reindexação
         **não muda** valor, chave, emitente, destinatário nem data.

A INVARIANTE QUE DÁ NOME À FASE
    Enriquecer é acrescentar. Se um campo que já existia mudar de valor ao
    reprocessar, não foi enriquecimento — foi regressão. `reindexacao.comparar`
    confere isso documento a documento, e há teste que o força a acusar.

NADA AQUI TOCA A PASTA REAL NEM A REDE.
"""
from __future__ import annotations

import hashlib
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402
import ingestao as ing                               # noqa: E402
from ingestao import acervo as acv                   # noqa: E402
from ingestao import consulta as cq                  # noqa: E402
from ingestao import documento as dm                 # noqa: E402
from ingestao import importacao as imp               # noqa: E402
from ingestao import indice as idx                   # noqa: E402
from ingestao import parsers as prs                  # noqa: E402
from ingestao import pipeline as pipe                # noqa: E402
from ingestao import reindexacao as rx               # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                             # noqa: E402

_TENTATIVAS_DE_REDE: list[str] = []
_connect_original = _socket.socket.connect


def _connect_proibido(self, endereco, *a, **kw):
    _TENTATIVAS_DE_REDE.append(str(endereco))
    raise AssertionError(f"teste tentou abrir conexão de rede para {endereco}")


_socket.socket.connect = _connect_proibido


def secao(t):
    print(f"\n{t}")


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print(f"  ok   {desc}")
    else:
        _falhas += 1
        _erros.append(desc)
        print(f"  FALHOU  {desc}")


def igual(a, b, desc):
    ok(a == b, desc + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


from teste_fixturas_nfe import (                     # noqa: E402
    cnpj_ficticio, chave_ficticia, xml_nfe, xml_resumo, xml_evento, bruto,
    EMPRESA_A, FORNECEDOR,
)

TRANSPORTADORA = cnpj_ficticio("777777770001")
OUTRA = cnpj_ficticio("888888880001")


# ══════════════════════════════════════════════════════════════════════════
# Fixtures desta fase: XML com os blocos que a NF-e 4 passou a ler.
# ══════════════════════════════════════════════════════════════════════════
def xml_com_papeis(chave, *, emitente=FORNECEDOR, destinatario=EMPRESA_A,
                   transportador="", autorizados=(), valor="1000.00"):
    """NF-e com `transp/transporta` e `autXML` — blocos oficiais, e é deles que
    o papel sai. Nunca da pasta, do valor ou de o XML ser completo."""
    base = xml_nfe(chave, valor=valor, emitente=emitente,
                   destinatario=destinatario).decode("utf-8")
    extra = ""
    if transportador:
        extra += f"<transp><modFrete>0</modFrete><transporta>" \
                 f"<CNPJ>{transportador}</CNPJ><xNome>TRANSP</xNome>" \
                 f"</transporta></transp>"
    for a in autorizados:
        extra += f"<autXML><CNPJ>{a}</CNPJ></autXML>"
    if extra:
        base = base.replace("</infNFe>", extra + "</infNFe>")
    return base.encode("utf-8")


IMPOSTO_RICO = """<imposto>
  <ICMS><ICMS20>
    <orig>1</orig><CST>20</CST><modBC>3</modBC><pRedBC>30.00</pRedBC>
    <vBC>700.00</vBC><pICMS>18.00</pICMS><vICMS>126.00</vICMS>
    <vBCFCP>700.00</vBCFCP><pFCP>2.00</pFCP><vFCP>14.00</vFCP>
    <vBCST>900.00</vBCST><pICMSST>18.00</pICMSST><vICMSST>36.00</vICMSST>
    <vICMSDeson>10.00</vICMSDeson><motDesICMS>9</motDesICMS>
  </ICMS20></ICMS>
  <IPI><cEnq>999</cEnq><IPITrib>
    <CST>50</CST><vBC>1000.00</vBC><pIPI>5.00</pIPI><vIPI>50.00</vIPI>
  </IPITrib></IPI>
  <PIS><PISQtde>
    <CST>03</CST><qBCProd>10.0000</qBCProd><vAliqProd>0.5000</vAliqProd>
    <vPIS>5.00</vPIS>
  </PISQtde></PIS>
  <COFINS><COFINSAliq>
    <CST>01</CST><vBC>1000.00</vBC><pCOFINS>7.60</pCOFINS><vCOFINS>76.00</vCOFINS>
  </COFINSAliq></COFINS>
  <IBSCBS><CST>000</CST><cClassTrib>000001</cClassTrib>
    <gIBSCBS><vBC>1000.00</vBC>
      <gIBSUF><pIBSUF>0.10</pIBSUF><vIBSUF>1.00</vIBSUF></gIBSUF>
      <gIBSMun><pIBSMun>0.05</pIBSMun><vIBSMun>0.50</vIBSMun></gIBSMun>
      <vIBS>1.50</vIBS>
      <gCBS><pCBS>0.90</pCBS><vCBS>9.00</vCBS></gCBS>
    </gIBSCBS></IBSCBS>
  <IS><CSTIS>001</CSTIS><vBCIS>1000.00</vBCIS><pIS>2.00</pIS><vIS>20.00</vIS></IS>
</imposto>"""

IMPOSTO_SIMPLES = """<imposto>
  <ICMS><ICMSSN101><orig>0</orig><CSOSN>101</CSOSN>
    <pCredSN>2.50</pCredSN><vCredICMSSN>25.00</vCredICMSSN>
  </ICMSSN101></ICMS>
</imposto>"""


def xml_rico(chave, imposto=IMPOSTO_RICO, *, cest="2100100", desconto="15.00"):
    """Uma NF-e cujo item traz tudo o que a 4B passou a ler."""
    base = xml_nfe(chave, valor="1000.00").decode("utf-8")
    ini = base.index("<imposto>")
    fim = base.index("</imposto>") + len("</imposto>")
    base = base[:ini] + imposto + base[fim:]
    base = base.replace("<CFOP>5102</CFOP>",
                        f"<CEST>{cest}</CEST><CFOP>5102</CFOP>")
    base = base.replace("<vProd>1000.00</vProd>",
                        f"<vProd>1000.00</vProd><vDesc>{desconto}</vDesc>")
    return base.encode("utf-8")


def guardar(raiz, empresa, docs, servico="nfe_distribuicao"):
    ac = acv.abrir(raiz, empresa, servico=servico, ambiente=ing.PRODUCAO)
    for d in docs:
        ac.preservar(d)
    pipe.indexar_pendentes(raiz, empresa)


def um(raiz, empresa, chave):
    itens = cq.consultar(raiz, empresa, cq.Filtro(chave=chave)).itens
    return itens[0] if itens else None


# ══════════════════════════════════════════════════════════════════════════
# ██  NF-e 4A — VERDADE OPERACIONAL
# ══════════════════════════════════════════════════════════════════════════
secao("4A · Um critério de cancelamento só")

igual(imp.e_cancelamento("110111", "126260087417242"), True,
      "110111 com protocolo cancela")
igual(imp.e_cancelamento("110112", "999"), True, "110112 também (substituição)")
igual(imp.e_cancelamento("110111", ""), False,
      "sem protocolo NÃO cancela — pedido não é homologação")
igual(imp.e_cancelamento("110111", "   "), False, "nem protocolo em branco")
igual(imp.e_cancelamento("110111", "", exigir_protocolo=False), True,
      "salvo no caminho do acervo, onde o protocolo ainda não foi lido")
igual(imp.e_cancelamento("110110", "999"), False, "carta de correção não cancela")
igual(imp.e_cancelamento("510630", "999"), False,
      "registro de passagem não cancela")
igual(imp.e_cancelamento("", "999"), False, "evento sem tipo não cancela")


secao("4A · Autorizada SEM cancelamento continua autorizada")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c1 = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [bruto(xml_nfe(c1), nsu="1")])
    d = um(raiz, EMPRESA_A, c1)
    igual(d.situacao, dm.AUTORIZADO, "o documento diz AUTORIZADO")
    igual(d.situacao_atual, dm.AUTORIZADO, "e a situação de hoje também")
    ok(not d.cancelada, "não está cancelada")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(cancelada=True)), 0,
          "e o filtro de canceladas não a devolve")


secao("4A · Autorizada COM cancelamento válido: as duas verdades")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c1 = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [
        bruto(xml_nfe(c1), nsu="1"),
        bruto(xml_evento(c1, tp="110111"), nsu="2",
              schema="procEventoNFe_v1.00.xsd")])
    d = um(raiz, EMPRESA_A, c1)
    igual(d.situacao, dm.AUTORIZADO,
          "o DOCUMENTO continua dizendo AUTORIZADO — evidência não se adultera")
    igual(d.situacao_atual, dm.CANCELADO,
          "e a SITUAÇÃO DE HOJE é CANCELADO")
    ok(d.cancelada, "o atalho `cancelada` acompanha")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(cancelada=True)), 1,
          "o filtro acha")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(cancelada=False)), 0,
          "e o inverso não")
    igual(cq.contar(raiz, EMPRESA_A,
                    cq.Filtro(situacoes_atuais=(dm.CANCELADO,))), 1,
          "o filtro por situação atual também")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(situacoes=(dm.AUTORIZADO,))), 1,
          "e o filtro pela situação DO DOCUMENTO continua achando AUTORIZADO")

    secao("4A · O evento fica intacto e é alcançável")
    evs = cq.eventos_da_chave(raiz, EMPRESA_A, c1)
    igual(len(evs), 1, "o evento existe")
    igual(evs[0]["tp_evento"], "110111", "com o tipo")
    ok(evs[0]["protocolo"], "e o protocolo")
    ok(evs[0]["dh_evento"], "e a data")
    ok("orgao" in evs[0].keys(), "e o órgão")


secao("4A · Não é qualquer evento que cancela")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c1 = chave_ficticia(FORNECEDOR, 1)
    c2 = chave_ficticia(FORNECEDOR, 2)
    guardar(raiz, EMPRESA_A, [
        bruto(xml_nfe(c1), nsu="1"), bruto(xml_nfe(c2), nsu="2"),
        # carta de correção e registro de passagem NÃO cancelam
        bruto(xml_evento(c1, tp="110110"), nsu="3",
              schema="procEventoNFe_v1.00.xsd"),
        bruto(xml_evento(c2, tp="510630"), nsu="4",
              schema="procEventoNFe_v1.00.xsd")])
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(cancelada=True)), 0,
          "nenhuma nota virou cancelada")
    igual(um(raiz, EMPRESA_A, c1).situacao_atual, dm.AUTORIZADO,
          "a da carta de correção continua autorizada")
    igual(um(raiz, EMPRESA_A, c2).situacao_atual, dm.AUTORIZADO,
          "a do registro de passagem também")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(com_evento=True)), 2,
          "mas as duas TÊM evento — ter evento não é estar cancelada")


secao("4A · Evento sem documento não cria documento")
with apoio.raiz_temporaria("nfe4_") as raiz:
    orfa = chave_ficticia(FORNECEDOR, 9)
    guardar(raiz, EMPRESA_A, [
        bruto(xml_evento(orfa, tp="110111"), nsu="1",
              schema="procEventoNFe_v1.00.xsd")])
    igual(cq.contar(raiz, EMPRESA_A), 0,
          "o evento órfão não vira NF-e na listagem")
    igual(len(cq.eventos_da_chave(raiz, EMPRESA_A, orfa)), 1,
          "mas está guardado e é alcançável pela chave")


secao("4A · Múltiplos eventos na mesma nota")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c1 = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [
        bruto(xml_nfe(c1), nsu="1"),
        bruto(xml_evento(c1, tp="110110", seq="1"), nsu="2",
              schema="procEventoNFe_v1.00.xsd"),
        bruto(xml_evento(c1, tp="610600", seq="1"), nsu="3",
              schema="procEventoNFe_v1.00.xsd"),
        bruto(xml_evento(c1, tp="110111", seq="1"), nsu="4",
              schema="procEventoNFe_v1.00.xsd")])
    d = um(raiz, EMPRESA_A, c1)
    igual(d.eventos, 3, "os três eventos aparecem na contagem")
    igual(d.situacao_atual, dm.CANCELADO,
          "e o cancelamento prevalece entre eles")


# ── papéis ─────────────────────────────────────────────────────────────────
secao("4A · Os cinco papéis vêm da estrutura oficial")
with apoio.raiz_temporaria("nfe4_") as raiz:
    ch = {}
    docs = []
    def nota(rot, n, **kw):
        c = chave_ficticia(kw.get("emitente", FORNECEDOR), n)
        ch[rot] = c
        docs.append(bruto(xml_com_papeis(c, **kw), nsu=str(n)))
        return c

    nota("destinatario", 1)
    nota("emitente", 2, emitente=EMPRESA_A, destinatario=FORNECEDOR)
    nota("transportador", 3, destinatario=OUTRA, transportador=EMPRESA_A)
    nota("autxml", 4, destinatario=OUTRA, autorizados=(EMPRESA_A,))
    nota("dois_papeis", 5, transportador=EMPRESA_A)   # dest E transportadora
    nota("alheia", 6, destinatario=OUTRA)             # não participa
    guardar(raiz, EMPRESA_A, docs)

    igual(um(raiz, EMPRESA_A, ch["destinatario"]).papel, cq.DESTINATARIO,
          "destinatária, pelo bloco `dest`")
    igual(um(raiz, EMPRESA_A, ch["emitente"]).papel, cq.EMITENTE,
          "emitente, pelo bloco `emit`")
    igual(um(raiz, EMPRESA_A, ch["transportador"]).papel, cq.TRANSPORTADOR,
          "transportadora, pelo bloco `transporta`")
    igual(um(raiz, EMPRESA_A, ch["autxml"]).papel, cq.AUTXML,
          "autorizada, pelo bloco `autXML`")
    igual(um(raiz, EMPRESA_A, ch["alheia"]).papel, cq.OUTRO,
          "e OUTRO quando não aparece em bloco nenhum")

    secao("4A · Múltiplos papéis não são espremidos num só")
    d = um(raiz, EMPRESA_A, ch["dois_papeis"])
    igual(set(d.papeis), {cq.DESTINATARIO, cq.TRANSPORTADOR},
          "os dois papéis são preservados")
    igual(d.papel, cq.DESTINATARIO,
          "e o principal segue a precedência (receber é mais forte que transportar)")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(papel=cq.TRANSPORTADOR)), 2,
          "filtrar por TRANSPORTADOR acha as duas, inclusive a de papel duplo")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(papel=cq.DESTINATARIO)), 2,
          "e por DESTINATARIO também")

    secao("4A · A precedência é declarada, não acidental")
    igual(dm.papel_principal([dm.AUTXML, dm.EMITENTE]), dm.EMITENTE,
          "emitir vence estar autorizado")
    igual(dm.papel_principal([dm.TRANSPORTADOR, dm.DESTINATARIO]),
          dm.DESTINATARIO, "receber vence transportar")
    igual(dm.papel_principal([]), dm.OUTRO, "sem papel nenhum, OUTRO")

    secao("4A · Nenhum papel é mais recusado")
    igual(cq.PAPEIS_PENDENTES, (), "não há papel pendente")
    for p in cq.PAPEIS:
        cq.contar(raiz, EMPRESA_A, cq.Filtro(papel=p))
    ok(True, f"os {len(cq.PAPEIS)} papéis do contrato respondem")


secao("4A · A inferência proibida continua proibida")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [bruto(
        xml_com_papeis(c, destinatario=OUTRA, transportador=EMPRESA_A),
        nsu="1")])
    d = um(raiz, EMPRESA_A, c)
    igual(d.conteudo, cq.COMPLETO, "o documento é COMPLETO")
    igual(d.papel, cq.TRANSPORTADOR, "e mesmo assim NÃO é compra dela")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(papel=cq.DESTINATARIO)), 0,
          "XML completo não é sinônimo de destinatária")


secao("4A · Resumo: papel indeterminado, sem inventar")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [bruto(xml_resumo(c), nsu="1",
                                    schema="resNFe_v1.01.xsd")])
    d = um(raiz, EMPRESA_A, c)
    igual(d.conteudo, cq.RESUMO, "é resumo")
    igual(d.papel, cq.OUTRO,
          "o papel é OUTRO — `resNFe` não nomeia destinatário nem transportador")
    igual(d.papeis, (cq.OUTRO,), "e não se inventa uma lista de papéis")

    secao("4A · E enriquece quando o completo chega")
    guardar(raiz, EMPRESA_A, [bruto(xml_com_papeis(c), nsu="2")])
    d2 = um(raiz, EMPRESA_A, c)
    igual(d2.conteudo, cq.COMPLETO, "o canônico foi promovido")
    igual(d2.papel, cq.DESTINATARIO, "e o papel agora é conhecido")
    igual(cq.contar(raiz, EMPRESA_A), 1, "continua sendo UM documento")


# ══════════════════════════════════════════════════════════════════════════
# ██  NF-e 4B — ENRIQUECIMENTO FISCAL
# ══════════════════════════════════════════════════════════════════════════
secao("4B · CST e CSOSN param de ser o mesmo campo")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c_cst = chave_ficticia(FORNECEDOR, 1)
    c_sn = chave_ficticia(FORNECEDOR, 2)
    guardar(raiz, EMPRESA_A, [
        bruto(xml_rico(c_cst), nsu="1"),
        bruto(xml_rico(c_sn, IMPOSTO_SIMPLES), nsu="2")])

    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(icms_cst="20")), 1,
          "filtrar por CST ICMS acha a do regime normal")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(icms_csosn="101")), 1,
          "filtrar por CSOSN acha a do Simples")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(icms_cst="101")), 0,
          "e CST 101 NÃO devolve quem tem CSOSN 101 — são campos diferentes")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(icms_csosn="20")), 0,
          "nem o contrário")

    import sqlite3
    con = sqlite3.connect(str(cq.caminho_do_indice(raiz, EMPRESA_A)))
    colunas = {r[1] for r in con.execute("PRAGMA table_info(itens)")}
    con.close()
    for c in ("icms_cst", "icms_csosn"):
        ok(c in colunas, f"o índice tem a coluna `{c}`")


secao("4B · CEST, origem e desconto")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [bruto(xml_rico(c, cest="2100100"), nsu="1")])
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(cest="2100100")), 1,
          "CEST é filtrável")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(ncm="84713012")), 1,
          "e o NCM continua, separado dele")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(cest="9999999")), 0,
          "CEST que não existe devolve vazio")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(origem="1")), 1,
          "a origem da mercadoria é filtrável")

    i = prs.para(__import__("ingestao.identificacao", fromlist=["x"]).identificar(
        xml_rico(c))).interpretar(
        xml_rico(c),
        __import__("ingestao.identificacao", fromlist=["x"]).identificar(xml_rico(c)),
        EMPRESA_A).extensao.itens[0]
    igual(i.cest, "2100100", "o CEST é lido do documento, não inferido do NCM")
    igual(i.desconto, Decimal("15.00"), "e o desconto do item")


secao("4B · ICMS inteiro: ST, FCP, redução, desoneração")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c = chave_ficticia(FORNECEDOR, 1)
    conteudo = xml_rico(c)
    from ingestao import identificacao as idf
    ident = idf.identificar(conteudo)
    item = prs.para(ident).interpretar(conteudo, ident, EMPRESA_A).extensao.itens[0]
    i = item.icms
    igual(i.cst, "20", "CST")
    igual(i.csosn, "", "sem CSOSN (é regime normal)")
    igual(i.origem, "1", "origem")
    igual(i.modalidade_base, "3", "modalidade da base")
    igual(i.base, Decimal("700.00"), "base")
    igual(i.aliquota, Decimal("18.00"), "alíquota")
    igual(i.valor, Decimal("126.00"), "valor")
    igual(i.reducao_base, Decimal("30.00"), "redução de base")
    igual(i.base_st, Decimal("900.00"), "base ST")
    igual(i.valor_st, Decimal("36.00"), "valor ST")
    igual(i.valor_fcp, Decimal("14.00"), "FCP")
    igual(i.valor_desonerado, Decimal("10.00"), "valor desonerado")
    igual(i.motivo_desoneracao, "9", "motivo da desoneração")

    secao("4B · CSOSN preenche o campo certo")
    cs = xml_rico(chave_ficticia(FORNECEDOR, 2), IMPOSTO_SIMPLES)
    ident2 = idf.identificar(cs)
    icms2 = prs.para(ident2).interpretar(cs, ident2, EMPRESA_A).extensao.itens[0].icms
    igual(icms2.csosn, "101", "o CSOSN vai para `csosn`")
    igual(icms2.cst, "", "e o `cst` fica VAZIO — não é CST nenhum")
    igual(icms2.classificacao, "101",
          "`classificacao` mostra o que o documento informou, para exibição")


secao("4B · PIS, COFINS e IPI")
with apoio.raiz_temporaria("nfe4_") as raiz:
    from ingestao import identificacao as idf
    conteudo = xml_rico(chave_ficticia(FORNECEDOR, 1))
    ident = idf.identificar(conteudo)
    item = prs.para(ident).interpretar(conteudo, ident, EMPRESA_A).extensao.itens[0]

    igual(item.pis.cst, "03", "CST do PIS")
    igual(item.pis.quantidade_base, Decimal("10.0000"),
          "PIS por QUANTIDADE tem base própria")
    igual(item.pis.aliquota_por_unidade, Decimal("0.5000"),
          "e alíquota em reais por unidade")
    igual(item.pis.aliquota, None,
          "a alíquota percentual fica ausente — não se inventa 0 para ela")
    igual(item.pis.valor, Decimal("5.00"), "e o valor")

    igual(item.cofins.cst, "01", "CST da COFINS")
    igual(item.cofins.base, Decimal("1000.00"), "base")
    igual(item.cofins.aliquota, Decimal("7.60"), "alíquota percentual")
    igual(item.cofins.valor, Decimal("76.00"), "valor")

    igual(item.ipi.cst, "50", "CST do IPI, lido de dentro do `IPITrib`")
    igual(item.ipi.enquadramento, "999",
          "e o enquadramento, que fica no grupo IPI e não no subgrupo")
    igual(item.ipi.valor, Decimal("50.00"), "valor do IPI")

    guardar(raiz, EMPRESA_A, [bruto(conteudo, nsu="1")])
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(pis_cst="03")), 1,
          "CST do PIS é filtrável")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(cofins_cst="01")), 1,
          "CST da COFINS também")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(ipi_cst="50")), 1,
          "e do IPI")


secao("4B · IBS, CBS e Imposto Seletivo")
with apoio.raiz_temporaria("nfe4_") as raiz:
    from ingestao import identificacao as idf
    conteudo = xml_rico(chave_ficticia(FORNECEDOR, 1))
    ident = idf.identificar(conteudo)
    r = prs.para(ident).interpretar(conteudo, ident, EMPRESA_A).extensao.itens[0].reforma

    ok(r.presente, "o grupo da Reforma é reconhecido")
    igual(r.cst, "000", "CST do IBS/CBS")
    igual(r.classificacao_tributaria, "000001", "`cClassTrib`")
    igual(r.base, Decimal("1000.00"), "base")
    igual(r.aliquota_ibs_uf, Decimal("0.10"), "alíquota do IBS estadual")
    igual(r.valor_ibs_uf, Decimal("1.00"), "valor do IBS estadual")
    igual(r.aliquota_ibs_mun, Decimal("0.05"), "alíquota do IBS municipal")
    igual(r.valor_ibs_mun, Decimal("0.50"), "valor do IBS municipal")
    igual(r.valor_ibs, Decimal("1.50"), "IBS total")
    igual(r.aliquota_cbs, Decimal("0.90"), "alíquota da CBS")
    igual(r.valor_cbs, Decimal("9.00"), "valor da CBS")
    igual(r.cst_is, "001", "CST do Imposto Seletivo")
    igual(r.valor_is, Decimal("20.00"), "valor do IS")

    secao("4B · Documento SEM Reforma não ganha zeros")
    simples = xml_rico(chave_ficticia(FORNECEDOR, 2), IMPOSTO_SIMPLES)
    ident2 = idf.identificar(simples)
    r2 = prs.para(ident2).interpretar(
        simples, ident2, EMPRESA_A).extensao.itens[0].reforma
    ok(not r2.presente, "o grupo simplesmente não está presente")
    igual(r2.valor_ibs, None, "IBS ausente é None, não 0")
    igual(r2.valor_cbs, None, "CBS ausente é None, não 0")
    igual(r2.base, None, "base ausente é None")

    guardar(raiz, EMPRESA_A, [bruto(conteudo, nsu="1"), bruto(simples, nsu="2")])
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(com_reforma=True)), 1,
          "dá para achar quem já traz a Reforma")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro(com_reforma=False)), 1,
          "e quem não traz")


# ── reindexação ────────────────────────────────────────────────────────────
secao("4B · Reindexação: o que muda e o que não pode mudar")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c1 = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [bruto(xml_rico(c1), nsu="1"),
                              bruto(xml_evento(c1, tp="110111"), nsu="2",
                                    schema="procEventoNFe_v1.00.xsd")])
    antes = rx.instantaneo(raiz, EMPRESA_A)
    ok(antes.total > 0, "há o que comparar")

    r = rx.reindexar_empresa(raiz, EMPRESA_A, tudo=True)
    ok(r["integro"], "a reindexação é íntegra")
    igual(r["invariantes_alteradas"], 0, "nenhuma invariante mudou")
    igual(r["documentos_sumidos"], 0, "nenhum documento sumiu")
    igual(r["valor_antes"], r["valor_depois"], "o valor total não mudou")
    igual(Decimal(r["diferenca_de_valor"]), Decimal("0"), "diferença de zero")

    secao("4B · Idempotência da reindexação")
    depois1 = rx.instantaneo(raiz, EMPRESA_A)
    rx.reindexar_empresa(raiz, EMPRESA_A, tudo=True)
    depois2 = rx.instantaneo(raiz, EMPRESA_A)
    igual(depois2.documentos, depois1.documentos,
          "reindexar de novo dá exatamente o mesmo índice")
    igual(depois2.itens, depois1.itens, "com os mesmos itens")

    secao("4B · O acervo não é tocado")
    originais = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml")}
    rx.reindexar_empresa(raiz, EMPRESA_A, tudo=True)
    igual({p: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml")},
          originais, "cada `original.xml` continua byte a byte igual")

    secao("4B · O ensaio não reprocessa")
    e = rx.reindexar_empresa(raiz, EMPRESA_A, ensaio=True)
    ok(e["ensaio"], "o ensaio se declara")
    ok("desatualizados" in e, "e diz quantos estão desatualizados")
    ok("reprocessados" not in e, "sem reprocessar nada")


secao("4B · A comparação ACUSA quando uma invariante muda")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c1 = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [bruto(xml_nfe(c1, valor="100.00"), nsu="1")])
    antes = rx.instantaneo(raiz, EMPRESA_A)
    # adultera o índice por fora, como faria um parser com defeito
    import sqlite3
    con = sqlite3.connect(str(cq.caminho_do_indice(raiz, EMPRESA_A)))
    con.execute("UPDATE documentos SET valor_total = '999.99'")
    con.commit(); con.close()
    depois = rx.instantaneo(raiz, EMPRESA_A)
    c = rx.comparar(antes, depois)
    ok(not c.integro, "a comparação NÃO passa")
    igual(len(c.alterados), 1, "e aponta exatamente uma alteração")
    igual(c.alterados[0][1], "valor_total", "dizendo qual campo mudou")

    secao("4B · E acusa documento que some")
    con = sqlite3.connect(str(cq.caminho_do_indice(raiz, EMPRESA_A)))
    con.execute("DELETE FROM documentos")
    con.commit(); con.close()
    c2 = rx.comparar(antes, rx.instantaneo(raiz, EMPRESA_A))
    ok(not c2.integro, "sumiço também reprova")
    igual(len(c2.sumidos), 1, "e é contado")


secao("4B · Índice ausente não inventa instantâneo")
with apoio.raiz_temporaria("nfe4_") as raiz:
    inst = rx.instantaneo(raiz, cnpj_ficticio("123456780001"))
    igual(inst.total, 0, "empresa sem índice devolve instantâneo vazio")
    ok(not inst.erro, "sem erro — não ter índice não é falha")
    ok(rx.instantaneo(raiz, "não é cnpj").erro,
       "mas identidade inválida é erro declarado")


# ── invariantes de sempre ──────────────────────────────────────────────────
secao("Checkpoint intocado pela reindexação")
with apoio.raiz_temporaria("nfe4_") as raiz:
    c1 = chave_ficticia(FORNECEDOR, 1)
    guardar(raiz, EMPRESA_A, [bruto(xml_rico(c1), nsu="1")])
    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO)
    cp.ult_nsu = "000000000000555"
    repo.salvar(cp)
    pasta = Path(raiz) / EMPRESA_A / "ingestao"
    antes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(pasta.glob("*.json"))}

    rx.reindexar_empresa(raiz, EMPRESA_A, tudo=True)
    cq.consultar(raiz, EMPRESA_A, cq.Filtro(cancelada=True))

    igual({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(pasta.glob("*.json"))}, antes,
          "nenhum arquivo de checkpoint foi tocado")
    igual(repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO).ult_nsu,
          "000000000000555", "e o ultNSU continua onde estava")


secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS_DE_REDE, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede NÃO estava ativa")
except AssertionError:
    ok(True, "e a trava estava mesmo ativa (conferido)")
_socket.socket.connect = _connect_original


print("\n" + "=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  - " + e)
    sys.exit(1)
print("NF-e 4: verdade operacional e enriquecimento fiscal verdes.")
