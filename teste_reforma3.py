#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REFORMA 3 — a Reforma chega ao contrato comum (`ItemOperacao`).

    python teste_reforma3.py

O QUE ESTA SUÍTE PROVA

    1. o grupo IBS/CBS atravessa do documento até o `ItemOperacao`, inteiro;
    2. CST e `cClassTrib` são preservados — é o par que distingue imunidade
       de alíquota zero, e perder um deles apaga a distinção;
    3. os valores chegam como `Decimal`, sem arredondar;
    4. ausente continua `None`, e item de CST 410 não vira zero;
    5. documento sem Reforma continua normalizando como sempre;
    6. o item pode vir DO ÍNDICE, sem abrir XML nenhum — inclusive de índice
       antigo, que entrega o grupo vazio em vez de quebrar;
    7. `id_documento`, chave, hash e origem atravessam intactos;
    8. vários itens mantêm correspondência um a um.

O QUE ELA NÃO PROVA, PORQUE NÃO FOI FEITO
    Nenhuma alíquota, nenhum cálculo de IBS/CBS, nenhuma regra fiscal nova.
    `TotaisNFe` não foi tocado (fechou na REFORMA 2) e o acervo real não foi
    reindexado.

NADA AQUI TOCA A PASTA REAL NEM A REDE.
"""
from __future__ import annotations

import builtins
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402
from ingestao import consulta as cq                  # noqa: E402
from ingestao import documento as dm                 # noqa: E402
from ingestao import identificacao as idf            # noqa: E402
from ingestao import indice as idx                   # noqa: E402
from ingestao import normalizacao as nz              # noqa: E402
from ingestao import parsers as prs                  # noqa: E402

from teste_fixturas_nfe import (                     # noqa: E402
    chave_ficticia, xml_nfe, EMPRESA_A, FORNECEDOR,
)

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                             # noqa: E402

_connect_original = _socket.socket.connect


def _connect_proibido(self, endereco, *a, **kw):
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


# ══════════════════════════════════════════════════════════════════════════
# Fixtures — mesmas formas das REFORMAS 1 e 2, locais pelo mesmo motivo:
# importar aquelas suítes as EXECUTARIA, porque são scripts.
# ══════════════════════════════════════════════════════════════════════════
def item_tributado(ibs="1.50", cbs="9.00", base="1000.00") -> str:
    return (f"<IBSCBS><CST>000</CST><cClassTrib>000001</cClassTrib>"
            f"<gIBSCBS><vBC>{base}</vBC>"
            f"<gIBSUF><pIBSUF>0.10</pIBSUF><vIBSUF>1.00</vIBSUF></gIBSUF>"
            f"<gIBSMun><pIBSMun>0.05</pIBSMun><vIBSMun>0.50</vIBSMun></gIBSMun>"
            f"<vIBS>{ibs}</vIBS>"
            f"<gCBS><pCBS>0.90</pCBS><vCBS>{cbs}</vCBS></gCBS>"
            f"</gIBSCBS></IBSCBS>")


def item_imune() -> str:
    return "<IBSCBS><CST>410</CST><cClassTrib>410002</cClassTrib></IBSCBS>"


def total_reforma(base="1000.00", ibs="1.50", cbs="9.00") -> str:
    return (f"<IBSCBSTot><vBCIBSCBS>{base}</vBCIBSCBS>"
            f"<gIBS><vIBS>{ibs}</vIBS></gIBS>"
            f"<gCBS><vCBS>{cbs}</vCBS></gCBS></IBSCBSTot>")


def montar(chave, *, itens_reforma=(), total="", itens=1) -> bytes:
    x = xml_nfe(chave, itens=itens).decode("utf-8")
    partes = x.split("</imposto>")
    for i, bloco in enumerate(itens_reforma):
        if i < len(partes) - 1:
            partes[i] += bloco
    x = "</imposto>".join(partes)
    if total:
        x = x.replace("</ICMSTot></total>", "</ICMSTot>" + total + "</total>")
    return x.encode("utf-8")


N = 0


def nova_chave():
    global N
    N += 1
    return chave_ficticia(FORNECEDOR, N)


def normalizar_do_xml(conteudo: bytes, *, caminho="x.xml", hash_c="sha256:abc"):
    """O caminho de hoje: parser → `Operacao`."""
    ident = idf.identificar(conteudo)
    doc = prs.para(ident).interpretar(conteudo, ident, EMPRESA_A)
    return doc, nz.de_documento_nfe(doc, caminho=caminho, hash_conteudo=hash_c)


def indexar(raiz, conteudo: bytes) -> str:
    ident = idf.identificar(conteudo)
    doc = prs.para(ident).interpretar(conteudo, ident, EMPRESA_A)
    ind = idx.Indice(raiz, EMPRESA_A)
    with ind.conectar() as con:
        ind.registrar(con, doc, hash_conteudo="sha256:teste")
    return doc.id_documento


# ══════════════════════════════════════════════════════════════════════════
secao("(a) item com IBS/CBS chega ao ItemOperacao")
doc, op = normalizar_do_xml(montar(nova_chave(),
                                   itens_reforma=[item_tributado()],
                                   total=total_reforma()))
it = op.itens[0]
ok(hasattr(it, "reforma"), "`ItemOperacao` tem o campo `reforma`")
ok(it.tem_reforma, "e ele está presente")
igual(it.reforma.valor_ibs, Decimal("1.50"), "IBS chegou")
igual(it.reforma.valor_cbs, Decimal("9.00"), "CBS chegou")

secao("(b) CST e cClassTrib preservados")
igual(it.reforma.cst, "000", "CST")
igual(it.reforma.classificacao_tributaria, "000001", "`cClassTrib`")
ok(it.cst_icms and it.cst_pis is not None,
   "e os CST antigos (ICMS/PIS/COFINS) continuam onde estavam")

secao("(c) base/IBS/CBS como Decimal, sem arredondar")
_, op2 = normalizar_do_xml(montar(nova_chave(),
                                  itens_reforma=[item_tributado(
                                      ibs="1.50", cbs="0.10",
                                      base="1234.5678901234")],
                                  total=total_reforma()))
r = op2.itens[0].reforma
ok(all(isinstance(v, Decimal) for v in (r.base, r.valor_ibs, r.valor_cbs)),
   "os três são Decimal")
igual(str(r.base), "1234.5678901234", "14 dígitos atravessam inteiros")
igual(str(r.valor_ibs), "1.50", "1.50 não encolhe para 1.5")
igual(str(r.valor_cbs), "0.10", "0.10 não encolhe para 0.1")
igual(r.aliquota_ibs_uf, Decimal("0.10"), "a alíquota lida também atravessa")
igual(r.valor_ibs_uf, Decimal("1.00"), "IBS estadual")
igual(r.valor_ibs_mun, Decimal("0.50"), "IBS municipal")

secao("(d) ausência permanece None")
_, op3 = normalizar_do_xml(xml_nfe(nova_chave()))
r3 = op3.itens[0].reforma
ok(not op3.itens[0].tem_reforma, "documento sem Reforma: grupo ausente")
igual(r3.cst, "", "CST vazio, não '000'")
igual(r3.valor_ibs, None, "IBS None, não zero")
igual(r3.valor_cbs, None, "CBS None, não zero")
igual(r3.base, None, "base None, não zero")

secao("(e) CST 410 sem valores não vira zero")
_, op4 = normalizar_do_xml(montar(nova_chave(), itens_reforma=[item_imune()]))
r4 = op4.itens[0].reforma
ok(r4.presente, "o grupo está presente (CST + cClassTrib)")
igual(r4.cst, "410", "CST 410 preservado")
igual(r4.classificacao_tributaria, "410002", "`cClassTrib` preservado")
igual(r4.valor_ibs, None, "IBS continua None")
igual(r4.valor_cbs, None, "CBS continua None")
igual(r4.base, None, "base continua None")

secao("(f) documento sem Reforma continua normalizado como sempre")
igual(op3.especie, dm.NFE55, "a espécie continua NFE55")
ok(op3.valor_bruto is not None, "o valor bruto continua vindo")
ok(op3.sentido, "o sentido continua sendo determinado")
igual(len(op3.itens), 1, "e o item continua lá")
igual(op3.itens[0].cfop, "5102", "com o CFOP intacto")

secao("(g/j) o item vem DO ÍNDICE, sem abrir XML — inclusive índice antigo")
with apoio.raiz_temporaria("reforma3_") as raiz:
    id_doc = indexar(raiz, montar(nova_chave(), itens=2,
                                  itens_reforma=[item_tributado(ibs="1.50"),
                                                 item_imune()],
                                  total=total_reforma()))
    linhas = cq.itens_do_documento(raiz, EMPRESA_A, id_doc)
    igual(len(linhas), 2, "o índice devolveu os dois itens")

    # A trava: qualquer abertura de arquivo aqui é falha do teste.
    _open_original = builtins.open
    _abriu: list[str] = []

    def _open_proibido(arquivo, *a, **kw):
        _abriu.append(str(arquivo))
        raise AssertionError(f"normalização abriu arquivo: {arquivo}")

    builtins.open = _open_proibido
    try:
        itens = nz.itens_do_indice(linhas)
    finally:
        builtins.open = _open_original
    ok(not _abriu, "nenhum arquivo foi aberto ao normalizar do índice")
    igual(len(itens), 2, "dois `ItemOperacao` montados")
    igual(itens[0].reforma.valor_ibs, Decimal("1.50"),
          "o item tributado trouxe o IBS do índice")
    igual(itens[0].reforma.valor_cbs, Decimal("9.00"),
          "e a CBS do índice — o outro lado do par, que some sozinho")
    igual(itens[0].reforma.base, Decimal("1000.00"), "com a base")
    igual(itens[0].reforma.aliquota_cbs, Decimal("0.90"),
          "e a alíquota que o documento declarou (lida, não calculada)")
    igual(itens[0].reforma.cst, "000", "com o CST")
    igual(itens[1].reforma.cst, "410", "e o item imune trouxe o CST 410")
    igual(itens[1].reforma.valor_ibs, None, "sem inventar valor para ele")
    igual(itens[1].reforma.classificacao_tributaria, "410002",
          "com o `cClassTrib` preservado")
    ok(isinstance(itens[0].reforma.valor_cbs, Decimal),
       "e o valor voltou como Decimal, não texto")

secao("(g') índice ANTIGO, sem as colunas da Reforma")
# O bloco chega vazio (ou sem as chaves) e nada pode quebrar nem virar zero.
vazio = nz.de_item_indice({"numero": 1, "codigo": "X", "cfop": "5102",
                           "reforma": {"presente": False}})
igual(vazio.reforma.cst, "", "CST vazio")
igual(vazio.reforma.valor_ibs, None, "IBS None")
ok(not vazio.tem_reforma, "e o item declara que não tem Reforma")
sem_bloco = nz.de_item_indice({"numero": 2, "cfop": "5102"})
igual(sem_bloco.reforma.valor_cbs, None, "sem o bloco `reforma`: CBS None")
igual(sem_bloco.numero, 2, "e o resto do item continua sendo lido")
igual(nz.de_item_indice({}).numero, 0, "linha vazia não quebra")

secao("(h) rastreabilidade: id_documento, chave, hash e origem intactos")
doc5, op5 = normalizar_do_xml(montar(nova_chave(),
                                     itens_reforma=[item_tributado()],
                                     total=total_reforma()),
                              caminho="empresa/acervo/NFE55/ab/xyz/original.xml",
                              hash_c="sha256:cafe")
igual(op5.id_documento, doc5.id_documento, "`id_documento` preservado")
igual(op5.origem.chave, doc5.chave, "chave na origem")
igual(op5.origem.hash_conteudo, "sha256:cafe", "hash preservado")
igual(op5.origem.caminho, "empresa/acervo/NFE55/ab/xyz/original.xml",
      "caminho preservado")
igual(op5.origem.fonte_leitor, "acervo", "fonte do leitor declarada")
igual(op5.identidade_empresa, doc5.identidade_empresa, "empresa preservada")
j = op5.para_json()
igual(j["id_documento"], doc5.id_documento, "e o JSON mantém o vínculo")
igual(j["origem"]["hash"], "sha256:cafe", "com o hash junto")

secao("(h') trocar os itens pelo índice NÃO perde a rastreabilidade")
with apoio.raiz_temporaria("reforma3_") as raiz:
    conteudo = montar(nova_chave(), itens_reforma=[item_tributado()],
                      total=total_reforma())
    id_doc = indexar(raiz, conteudo)
    _, op6 = normalizar_do_xml(conteudo, caminho="a/b.xml", hash_c="sha256:d0c")
    op7 = nz.com_itens_do_indice(
        op6, cq.itens_do_documento(raiz, EMPRESA_A, id_doc))
    igual(op7.id_documento, op6.id_documento, "`id_documento` igual")
    igual(op7.origem, op6.origem, "origem idêntica (caminho, chave e hash)")
    igual(op7.sentido, op6.sentido, "sentido preservado")
    igual(op7.valor_bruto, op6.valor_bruto, "valor bruto preservado")
    igual(op7.itens[0].reforma.valor_ibs, Decimal("1.50"),
          "e os itens agora vêm do índice, com a Reforma")

secao("(i) vários itens mantêm correspondência um a um")
_, op8 = normalizar_do_xml(montar(nova_chave(), itens=3,
                                  itens_reforma=[item_tributado(ibs="1.11"),
                                                 item_imune(),
                                                 item_tributado(ibs="3.33")],
                                  total=total_reforma()))
igual(len(op8.itens), 3, "três itens")
igual([i.numero for i in op8.itens], [1, 2, 3], "numerados em ordem")
igual(op8.itens[0].reforma.valor_ibs, Decimal("1.11"), "item 1: IBS próprio")
igual(op8.itens[1].reforma.cst, "410", "item 2: imune")
igual(op8.itens[1].reforma.valor_ibs, None, "item 2 sem valor")
igual(op8.itens[2].reforma.valor_ibs, Decimal("3.33"), "item 3: IBS próprio")
ok(op8.itens[0].reforma is not op8.itens[2].reforma,
   "e cada item tem o seu grupo, não uma referência compartilhada")

with apoio.raiz_temporaria("reforma3_") as raiz:
    id_doc = indexar(raiz, montar(nova_chave(), itens=3,
                                  itens_reforma=[item_tributado(ibs="1.11"),
                                                 item_imune(),
                                                 item_tributado(ibs="3.33")],
                                  total=total_reforma()))
    pelo_indice = nz.itens_do_indice(
        cq.itens_do_documento(raiz, EMPRESA_A, id_doc))
    igual([i.numero for i in pelo_indice], [1, 2, 3],
          "pelo índice, a ordem também é a dos números")
    igual([str(i.reforma.valor_ibs) for i in pelo_indice],
          ["1.11", "None", "3.33"], "e cada valor ficou no seu item")

secao("NENHUM CÁLCULO FISCAL FOI INTRODUZIDO")
import importlib                                      # noqa: E402
import inspect                                        # noqa: E402
import re                                             # noqa: E402

# A normalização AGORA fala de Reforma — e é o objetivo desta fase. O que ela
# não pode ter é ARITMÉTICA sobre esses valores.
fonte_nz = inspect.getsource(nz)
# Substring, sem `\b`: `\bibs\b` NÃO casa dentro de `valor_ibs` (o `_` é
# caractere de palavra), e uma mutação que multiplicava o IBS atravessou a
# primeira versão desta trava. Sem `/` nos operadores: ele aparece no texto
# "IBS/CBS" dos comentários.
CONTA = re.compile(r"[*%]|\bsum\(|\bround\(|[0-9]\s*\+\s*[0-9]")
novas = [l for l in fonte_nz.splitlines()
         if re.search(r"(ibs|cbs)", l, re.I) and CONTA.search(l)]
ok(not novas, "nenhuma linha da normalização faz conta com IBS/CBS"
              + (f" (achei {novas[:2]})" if novas else ""))
ok("aliquota=" not in fonte_nz.replace(" ", ""),
   "e nenhuma alíquota é atribuída pela normalização")

for mod in ("classificador", "apuracao_federal", "iss", "retencao",
            "ingestao.vendas"):
    fonte = inspect.getsource(importlib.import_module(mod))
    achados = sorted(set(m.group(0).lower() for m in
                         re.finditer(r"\b(ibs|cbs|cclasstrib|reforma)\b",
                                     fonte, re.I)))
    ok(not achados, f"`{mod}` segue sem IBS/CBS"
                    + (f" (achei {achados})" if achados else ""))

ok(not hasattr(nz.Retencoes(), "ibs") and not hasattr(nz.Retencoes(), "cbs"),
   "`Retencoes` continua sem IBS/CBS — split payment não entrou")
ok(not hasattr(nz.Operacao("x", "y", dm.NFE55, nz.OrigemDocumento()), "ibs"),
   "`Operacao` não ganhou total de Reforma (isso é da REFORMA 2, em TotaisNFe)")

_socket.socket.connect = _connect_original

print("\n" + "=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  - " + e)
    sys.exit(1)
print("REFORMA 3: IBS/CBS no contrato comum — transportado, não calculado.")
