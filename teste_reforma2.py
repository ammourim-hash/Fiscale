#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REFORMA 2 — o total declarado (`IBSCBSTot`) persistido no índice.

    python teste_reforma2.py

O QUE ESTA SUÍTE PROVA

    1. os três valores do `IBSCBSTot` são GRAVADOS no índice e voltam de lá
       sem reabrir o XML;
    2. `Decimal` atravessa a ida e a volta sem perder um dígito — inclusive
       o zero à direita, que é precisão declarada pelo emitente;
    3. ausente continua ausente: documento sem Reforma grava `None`, e item
       de CST 410 (imunidade) NÃO vira zero;
    4. índice criado ANTES desta fase continua abrindo, ganha as colunas
       sozinho e não perde nenhuma linha;
    5. regravar o mesmo documento não duplica registro;
    6. a rastreabilidade até `id_documento` continua inteira.

O QUE ELA NÃO PROVA, PORQUE NÃO FOI FEITO
    Nenhuma alíquota, nenhum cálculo de IBS/CBS, nada em `Operacao`/
    `ItemOperacao` e nenhuma camada de apuração tocada.

NADA AQUI TOCA A PASTA REAL NEM A REDE.
"""
from __future__ import annotations

import sqlite3
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
# Fixtures
#
# São as mesmas formas da REFORMA 1, repetidas aqui de propósito: importar
# `teste_reforma1` EXECUTARIA aquela suíte inteira, porque ela é um script.
# São dez linhas; o acoplamento entre suítes custaria mais.
# ══════════════════════════════════════════════════════════════════════════
def item_tributado(ibs="1.50", cbs="9.00") -> str:
    return (f"<IBSCBS><CST>000</CST><cClassTrib>000001</cClassTrib>"
            f"<gIBSCBS><vBC>1000.00</vBC>"
            f"<gIBSUF><pIBSUF>0.10</pIBSUF><vIBSUF>1.00</vIBSUF></gIBSUF>"
            f"<gIBSMun><pIBSMun>0.05</pIBSMun><vIBSMun>0.50</vIBSMun></gIBSMun>"
            f"<vIBS>{ibs}</vIBS>"
            f"<gCBS><pCBS>0.90</pCBS><vCBS>{cbs}</vCBS></gCBS>"
            f"</gIBSCBS></IBSCBS>")


def item_imune() -> str:
    """CST 410 — imunidade. Sem valores, como o emitente manda."""
    return "<IBSCBS><CST>410</CST><cClassTrib>410002</cClassTrib></IBSCBS>"


def total_reforma(base="1000.00", ibs="1.50", cbs="9.00") -> str:
    return (f"<IBSCBSTot><vBCIBSCBS>{base}</vBCIBSCBS>"
            f"<gIBS><vDif>0.00</vDif><vIBS>{ibs}</vIBS></gIBS>"
            f"<gCBS><vDif>0.00</vDif><vCBS>{cbs}</vCBS></gCBS>"
            f"</IBSCBSTot>")


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


def indexar(raiz, conteudo: bytes):
    """Parser → índice, o caminho real. Devolve o `id_documento` gravado."""
    ident = idf.identificar(conteudo)
    doc = prs.para(ident).interpretar(conteudo, ident, EMPRESA_A)
    ind = idx.Indice(raiz, EMPRESA_A)
    with ind.conectar() as con:
        ind.registrar(con, doc, hash_conteudo="sha256:teste")
    return doc.id_documento


def do_indice(raiz, id_documento: str):
    """Relê do ÍNDICE — sem tocar no XML."""
    pg = cq.consultar(raiz, EMPRESA_A, cq.Filtro())
    for d in pg.itens:
        if d.id_documento == id_documento:
            return d
    return None


# ══════════════════════════════════════════════════════════════════════════
secao("(a) documento com IBSCBSTot → os três valores persistidos")
with apoio.raiz_temporaria("reforma2_") as raiz:
    ident = indexar(raiz, montar(nova_chave(),
                                 itens_reforma=[item_tributado()],
                                 total=total_reforma()))
    with sqlite3.connect(str(idx.Indice(raiz, EMPRESA_A).caminho())) as con:
        con.row_factory = sqlite3.Row
        linha = con.execute("SELECT * FROM documentos WHERE id_documento = ?",
                            (ident,)).fetchone()
    igual(linha["total_base_ibs_cbs"], "1000.00", "`vBCIBSCBS` gravado como TEXTO")
    igual(linha["total_ibs"], "1.50", "`vIBS` gravado como TEXTO")
    igual(linha["total_cbs"], "9.00", "`vCBS` gravado como TEXTO")
    ok(not isinstance(linha["total_ibs"], float),
       "e nenhum deles vira float no caminho")

secao("(b) documento sem Reforma → os três campos ficam None")
with apoio.raiz_temporaria("reforma2_") as raiz:
    ident = indexar(raiz, xml_nfe(nova_chave()))
    with sqlite3.connect(str(idx.Indice(raiz, EMPRESA_A).caminho())) as con:
        con.row_factory = sqlite3.Row
        linha = con.execute("SELECT * FROM documentos WHERE id_documento = ?",
                            (ident,)).fetchone()
    igual(linha["total_base_ibs_cbs"], None, "base é NULL, não '0'")
    igual(linha["total_ibs"], None, "IBS é NULL, não '0'")
    igual(linha["total_cbs"], None, "CBS é NULL, não '0'")
    d = do_indice(raiz, ident)
    igual(d.total_ibs, None, "e a consulta devolve None")
    ok(not d.tem_total_reforma, "`tem_total_reforma` é falso")

secao("(c) leitura posterior recupera os mesmos valores, SEM reabrir o XML")
with apoio.raiz_temporaria("reforma2_") as raiz:
    ident = indexar(raiz, montar(nova_chave(),
                                 itens_reforma=[item_tributado()],
                                 total=total_reforma(base="546.42", ibs="0.55",
                                                     cbs="4.92")))
    d = do_indice(raiz, ident)
    igual(d.total_base_ibs_cbs, Decimal("546.42"), "base volta como Decimal")
    igual(d.total_ibs, Decimal("0.55"), "IBS volta como Decimal")
    igual(d.total_cbs, Decimal("4.92"), "CBS volta como Decimal")
    ok(d.tem_total_reforma, "`tem_total_reforma` é verdadeiro")
    ok(all(isinstance(v, Decimal) for v in
           (d.total_base_ibs_cbs, d.total_ibs, d.total_cbs)),
       "os três são Decimal, nunca float nem str")
    j = d.para_json()
    igual(j["total_ibs"], "0.55", "no JSON sai como texto, preservado")
    igual(j["total_cbs"], "4.92", "idem a CBS")

secao("(d) Decimal preservado — inclusive o zero à direita")
with apoio.raiz_temporaria("reforma2_") as raiz:
    # `1.50` não pode voltar `1.5`: a casa decimal é precisão DECLARADA pelo
    # emitente, e encolhê-la é alterar o documento em silêncio.
    ident = indexar(raiz, montar(nova_chave(),
                                 itens_reforma=[item_tributado()],
                                 total=total_reforma(base="1234.5678901234",
                                                     ibs="1.50", cbs="0.10")))
    d = do_indice(raiz, ident)
    igual(str(d.total_ibs), "1.50", "1.50 não encolhe para 1.5")
    igual(str(d.total_cbs), "0.10", "0.10 não encolhe para 0.1")
    igual(str(d.total_base_ibs_cbs), "1234.5678901234",
          "14 dígitos atravessam sem arredondar")
    ok(d.total_base_ibs_cbs == Decimal("1234.5678901234"),
       "e continua igual ao valor original")

secao("(e) CST 410 sem valores → não grava zero")
with apoio.raiz_temporaria("reforma2_") as raiz:
    # O item é imune e o documento nem declara total: nada pode virar 0.
    ident = indexar(raiz, montar(nova_chave(), itens_reforma=[item_imune()]))
    d = do_indice(raiz, ident)
    igual(d.total_ibs, None, "total de IBS continua None")
    igual(d.total_cbs, None, "total de CBS continua None")
    with sqlite3.connect(str(idx.Indice(raiz, EMPRESA_A).caminho())) as con:
        con.row_factory = sqlite3.Row
        item = con.execute("SELECT * FROM itens WHERE id_documento = ?",
                           (ident,)).fetchone()
    igual(item["reforma_cst"], "410", "o CST do item foi preservado")
    igual(item["ibs_valor"], None, "e o valor do item segue NULL, não '0'")
    igual(item["cbs_valor"], None, "idem a CBS do item")

secao("(f) índice ANTERIOR à Reforma 2 continua compatível")
with apoio.raiz_temporaria("reforma2_") as raiz:
    # Um índice de verdade, criado com o esquema SEM as três colunas. É a
    # situação de quem já usa o sistema: `CREATE TABLE IF NOT EXISTS` não
    # altera tabela existente, e sem `_ACRESCIMOS` o INSERT quebraria lá,
    # não aqui.
    alvo = idx.Indice(raiz, EMPRESA_A)
    caminho = alvo.caminho(criar=True)
    antigo = idx._ESQUEMA
    for col in ("    total_base_ibs_cbs TEXT,          -- `vBCIBSCBS`\n",
                "    total_ibs         TEXT,           -- `gIBS/vIBS`\n",
                "    total_cbs         TEXT,           -- `gCBS/vCBS`\n"):
        antigo = antigo.replace(col, "")
    with sqlite3.connect(str(caminho)) as con:
        con.executescript(antigo)
        con.execute(
            "INSERT INTO documentos (id_documento, especie, chave, "
            "identidade_empresa, estado, valor_total) VALUES (?,?,?,?,?,?)",
            ("legado-1", dm.NFE55, "9" * 44, EMPRESA_A, idx.INDEXADO, "10.00"))
    with sqlite3.connect(str(caminho)) as con:
        colunas = {r[1] for r in con.execute("PRAGMA table_info(documentos)")}
    ok("total_ibs" not in colunas, "o índice nasceu SEM as colunas novas")

    # A consulta abre em somente-leitura e precisa sobreviver a isso.
    antes = cq.consultar(raiz, EMPRESA_A, cq.Filtro())
    igual(antes.total, 1, "a consulta lê o índice antigo sem quebrar")
    igual(antes.itens[0].total_ibs, None,
          "e devolve None para a coluna que não existe")

    # Agora o caminho normal de escrita: a migração acontece ao conectar.
    ident = indexar(raiz, montar(nova_chave(),
                                 itens_reforma=[item_tributado()],
                                 total=total_reforma()))
    with sqlite3.connect(str(caminho)) as con:
        colunas = {r[1] for r in con.execute("PRAGMA table_info(documentos)")}
    ok({"total_base_ibs_cbs", "total_ibs", "total_cbs"} <= colunas,
       "conectar acrescentou as três colunas sozinho")
    depois = cq.consultar(raiz, EMPRESA_A, cq.Filtro())
    igual(depois.total, 2, "o documento legado continua lá — nada foi perdido")
    legado = [x for x in depois.itens if x.id_documento == "legado-1"][0]
    igual(legado.valor_total, Decimal("10.00"), "com o valor original intacto")
    igual(legado.total_ibs, None, "e sem inventar Reforma para ele")
    igual(do_indice(raiz, ident).total_ibs, Decimal("1.50"),
          "enquanto o documento novo já traz o total declarado")

secao("(g) regravar não duplica registro")
with apoio.raiz_temporaria("reforma2_") as raiz:
    conteudo = montar(nova_chave(), itens_reforma=[item_tributado()],
                      total=total_reforma())
    ident = indexar(raiz, conteudo)
    ident2 = indexar(raiz, conteudo)
    ident3 = indexar(raiz, conteudo)
    igual(ident2, ident, "o id do documento é o mesmo nas três vezes")
    igual(ident3, ident, "inclusive na terceira")
    with sqlite3.connect(str(idx.Indice(raiz, EMPRESA_A).caminho())) as con:
        n_doc = con.execute("SELECT COUNT(*) FROM documentos").fetchone()[0]
        n_item = con.execute("SELECT COUNT(*) FROM itens WHERE id_documento = ?",
                             (ident,)).fetchone()[0]
    igual(n_doc, 1, "uma linha de documento, não três")
    igual(n_item, 1, "um item, não três")
    igual(do_indice(raiz, ident).total_ibs, Decimal("1.50"),
          "e o total continua correto depois das regravações")

secao("(g') regravar com total DIFERENTE substitui, não acumula")
with apoio.raiz_temporaria("reforma2_") as raiz:
    chave = nova_chave()
    indexar(raiz, montar(chave, itens_reforma=[item_tributado()],
                         total=total_reforma(ibs="1.50")))
    ident = indexar(raiz, montar(chave, itens_reforma=[item_tributado()],
                                 total=total_reforma(ibs="7.77")))
    igual(do_indice(raiz, ident).total_ibs, Decimal("7.77"),
          "o índice reflete a última leitura, sem resto da anterior")

secao("(h) rastreabilidade até o id_documento")
with apoio.raiz_temporaria("reforma2_") as raiz:
    conteudo = montar(nova_chave(), itens_reforma=[item_tributado()],
                      total=total_reforma())
    ident = idf.identificar(conteudo)
    doc = prs.para(ident).interpretar(conteudo, ident, EMPRESA_A)
    id_doc = indexar(raiz, conteudo)
    igual(id_doc, doc.id_documento, "o id gravado é o do documento lido")
    d = do_indice(raiz, id_doc)
    igual(d.id_documento, doc.id_documento, "e volta do índice inalterado")
    igual(d.chave, doc.chave, "a chave de acesso continua ligada a ele")
    det = cq.detalhe(raiz, EMPRESA_A, id_documento=id_doc)
    ok(det is not None, "o detalhe encontra o documento pelo id")
    igual(det["documento"]["total_ibs"], "1.50",
          "e entrega o total da Reforma junto, sem reabrir o XML")
    igual(det["documento"]["id_documento"], doc.id_documento,
          "com o vínculo ao id preservado no JSON")

secao("COMPATIBILIDADE — os totais antigos não mudaram")
with apoio.raiz_temporaria("reforma2_") as raiz:
    chave = nova_chave()
    sem = indexar(raiz, xml_nfe(chave))
    d_sem = do_indice(raiz, sem)
with apoio.raiz_temporaria("reforma2_") as raiz:
    com = indexar(raiz, montar(chave, itens_reforma=[item_tributado()],
                               total=total_reforma()))
    d_com = do_indice(raiz, com)
igual(d_com.valor_total, d_sem.valor_total, "`valor_total` idêntico")
igual(d_com.chave, d_sem.chave, "chave idêntica")
igual(d_com.emitente, d_sem.emitente, "emitente idêntico")
igual(d_com.competencia, d_sem.competencia, "competência idêntica")
igual(d_com.situacao, d_sem.situacao, "situação idêntica")

secao("NENHUMA CAMADA DE APURAÇÃO FOI TOCADA")
import importlib                                      # noqa: E402
import inspect                                        # noqa: E402
import re                                             # noqa: E402

PALAVRA = re.compile(r"\b(ibs|cbs|cclasstrib|reforma)\b", re.I)
for mod in ("ingestao.vendas", "classificador",
            "apuracao_federal", "iss", "retencao"):
    fonte = inspect.getsource(importlib.import_module(mod))
    achados = sorted(set(m.group(0).lower() for m in PALAVRA.finditer(fonte)))
    ok(not achados, f"`{mod}` segue sem IBS/CBS"
                    + (f" (achei {achados})" if achados else ""))

from ingestao import normalizacao as nz               # noqa: E402

# `ingestao.normalizacao` saiu da lista acima na REFORMA 3, quando o grupo
# passou a ser transportado até o `ItemOperacao`. A pergunta mudou de "fala de
# IBS/CBS?" para "faz CONTA com IBS/CBS?" — ver o mesmo bloco em
# `teste_reforma1.py`.
# Substring, sem `\b`, e sem `/` entre os operadores: ver a explicação no
# mesmo bloco de `teste_reforma1.py`.
CONTA = re.compile(r"[*%]|\bsum\(|\bround\(|[0-9]\s*\+\s*[0-9]")
contas = [l for l in inspect.getsource(nz).splitlines()
          if re.search(r"(ibs|cbs)", l, re.I) and CONTA.search(l)]
ok(not contas, "a normalização transporta IBS/CBS, mas não faz conta com eles"
               + (f" (achei {contas[:2]})" if contas else ""))
ok(isinstance(nz.ItemOperacao().reforma, dm.TributoReforma),
   "`ItemOperacao` carrega o grupo pelo tipo do projeto (REFORMA 3)")
ok(not hasattr(nz.Operacao("x", "y", dm.NFE55, nz.OrigemDocumento()), "ibs"),
   "`Operacao` continua sem IBS (o total é da REFORMA 2, em `TotaisNFe`)")

_socket.socket.connect = _connect_original

print("\n" + "=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  - " + e)
    sys.exit(1)
print("REFORMA 2: IBSCBSTot persistido no índice — sem apurar nada.")
