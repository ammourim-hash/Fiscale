#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REFORMA 1 — o total declarado da Reforma (`IBSCBSTot`) e a conferência.

    python teste_reforma1.py

O QUE ESTA SUÍTE PROVA

    1. o parser lê `<total><IBSCBSTot>` — `vBCIBSCBS`, `gIBS/vIBS` e
       `gCBS/vCBS` — e preserva os três em `TotaisNFe`;
    2. os QUATRO estados de um documento ficam distinguíveis: sem Reforma
       nenhuma, com grupo no item e sem valores, com valores, e com o total
       declarado;
    3. a conferência compara soma dos itens × total declarado, e só quando há
       base para comparar;
    4. item sem valor NÃO vira divergência — `confere` devolve `None`, nunca
       `False` por omissão;
    5. nada do que já existia mudou: ICMS, IPI, PIS, COFINS e os totais
       antigos saem idênticos, e XML sem Reforma continua válido.

O QUE ELA NÃO PROVA, PORQUE NÃO FOI FEITO
    Nenhuma alíquota é calculada, nenhum IBS/CBS é apurado, nada chega a
    `Operacao`/`ItemOperacao` e nenhuma camada de apuração foi tocada.

NADA AQUI TOCA A PASTA REAL NEM A REDE.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

from ingestao import documento as dm                 # noqa: E402
from ingestao import identificacao as idf            # noqa: E402
from ingestao import parsers as prs                  # noqa: E402

from teste_fixturas_nfe import (                     # noqa: E402
    chave_ficticia, xml_nfe, EMPRESA_A, FORNECEDOR,
)

_ok = _falhas = 0
_erros: list[str] = []

# ── nenhuma conexão de rede, nem por engano ────────────────────────────────
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
# São montadas sobre `xml_nfe`, a mesma fábrica das outras suítes, para que
# ninguém precise manter um segundo modelo de NF-e. O que muda é só o que
# esta fase acrescenta: o grupo do item e o bloco de total.
#
# Os blocos são cópias fiéis da ESTRUTURA observada no acervo real em
# 22/09/2026 — inclusive o item de CST 410, que vem com `<CST>` e
# `<cClassTrib>` e NENHUM valor, sem sequer o `gIBSCBS`. Os números são
# fictícios; a forma não é.
# ══════════════════════════════════════════════════════════════════════════
def item_tributado(base="1000.00", ibs_uf="1.00", ibs_mun="0.50",
                   ibs="1.50", cbs="9.00") -> str:
    """Item com CST 000 — tributação integral, com valores."""
    return (f"<IBSCBS><CST>000</CST><cClassTrib>000001</cClassTrib>"
            f"<gIBSCBS><vBC>{base}</vBC>"
            f"<gIBSUF><pIBSUF>0.10</pIBSUF><vIBSUF>{ibs_uf}</vIBSUF></gIBSUF>"
            f"<gIBSMun><pIBSMun>0.05</pIBSMun><vIBSMun>{ibs_mun}</vIBSMun></gIBSMun>"
            f"<vIBS>{ibs}</vIBS>"
            f"<gCBS><pCBS>0.90</pCBS><vCBS>{cbs}</vCBS></gCBS>"
            f"</gIBSCBS></IBSCBS>")


def item_imune() -> str:
    """Item com CST 410 — imunidade/não incidência. Sem valores, de propósito:
    é 62,6% dos itens do acervo e é assim que o emitente o manda."""
    return "<IBSCBS><CST>410</CST><cClassTrib>410002</cClassTrib></IBSCBS>"


def total_reforma(base="1000.00", ibs="1.50", cbs="9.00") -> str:
    return (f"<IBSCBSTot><vBCIBSCBS>{base}</vBCIBSCBS>"
            f"<gIBS><vDif>0.00</vDif><vIBS>{ibs}</vIBS></gIBS>"
            f"<gCBS><vDif>0.00</vDif><vCBS>{cbs}</vCBS></gCBS>"
            f"</IBSCBSTot>")


def montar(chave, *, itens_reforma=(), total="", itens=1,
           omitir_icmstot=False) -> bytes:
    """NF-e com os blocos da Reforma enxertados nos lugares oficiais."""
    x = xml_nfe(chave, itens=itens).decode("utf-8")
    # Um bloco por item, na ordem. `replace(..., 1)` não serve: ele acerta
    # sempre a PRIMEIRA ocorrência, e empilharia todos os grupos no item 1.
    partes = x.split("</imposto>")
    for i, bloco in enumerate(itens_reforma):
        if i < len(partes) - 1:
            partes[i] += bloco
    x = "</imposto>".join(partes)
    if total:
        x = x.replace("</ICMSTot></total>", "</ICMSTot>" + total + "</total>")
    if omitir_icmstot:
        ini = x.index("<ICMSTot>")
        fim = x.index("</ICMSTot>") + len("</ICMSTot>")
        x = x[:ini] + x[fim:]
    return x.encode("utf-8")


def ler(conteudo: bytes):
    ident = idf.identificar(conteudo)
    return prs.para(ident).interpretar(conteudo, ident, EMPRESA_A).extensao


N = 0


def nova_chave():
    global N
    N += 1
    return chave_ficticia(FORNECEDOR, N)


# ══════════════════════════════════════════════════════════════════════════
secao("(a) XML com IBSCBSTot: os três campos chegam ao TotaisNFe")
ext = ler(montar(nova_chave(), itens_reforma=[item_tributado()],
                 total=total_reforma()))
t = ext.totais
igual(t.base_ibs_cbs, Decimal("1000.00"), "`vBCIBSCBS` vira `base_ibs_cbs`")
igual(t.ibs, Decimal("1.50"), "`gIBS/vIBS` vira `ibs`")
igual(t.cbs, Decimal("9.00"), "`gCBS/vCBS` vira `cbs`")
ok(t.reforma_presente, "`reforma_presente` reconhece o total declarado")

secao("(a') o total da Reforma NÃO é lido de dentro do ICMSTot")
# O `vBC` do ICMSTot é 1000.00 e o `vBCIBSCBS` também: se o parser lesse o
# lugar errado o teste acima passaria por acidente. Aqui os dois divergem.
ext = ler(montar(nova_chave(), itens_reforma=[item_tributado()],
                 total=total_reforma(base="777.77")))
igual(ext.totais.base_ibs_cbs, Decimal("777.77"),
      "base da Reforma sai do IBSCBSTot, não do ICMSTot")
igual(ext.totais.base_icms, Decimal("1000.00"), "e a base do ICMS fica intacta")

secao("(b) XML sem IBSCBSTot")
ext = ler(montar(nova_chave(), itens_reforma=[item_tributado()]))
igual(ext.totais.base_ibs_cbs, None, "`base_ibs_cbs` ausente é None, não zero")
igual(ext.totais.ibs, None, "`ibs` ausente é None")
igual(ext.totais.cbs, None, "`cbs` ausente é None")
ok(not ext.totais.reforma_presente, "`reforma_presente` é falso sem o bloco")
igual(ext.conferir_reforma().estado, dm.REFORMA_SEM_TOTAL,
      "item com grupo e documento sem total: SEM_TOTAL")

secao("(b') XML antigo, sem Reforma nenhuma — continua válido")
ext = ler(xml_nfe(nova_chave()))
igual(ext.totais.base_ibs_cbs, None, "sem `base_ibs_cbs`")
igual(ext.totais.ibs, None, "sem `ibs`")
igual(ext.totais.cbs, None, "sem `cbs`")
ok(not ext.itens[0].reforma.presente, "e o item não tem grupo da Reforma")
igual(ext.conferir_reforma().estado, dm.REFORMA_AUSENTE, "estado AUSENTE")
igual(ext.conferir_reforma().confere, None,
      "`confere` é None — ausência nunca vira reprovação")

secao("(c) item com CST 410, sem valores")
ext = ler(montar(nova_chave(), itens_reforma=[item_imune()],
                 total=total_reforma(ibs="0.00", cbs="0.00")))
r = ext.itens[0].reforma
ok(r.presente, "o grupo é reconhecido pelo CST/cClassTrib")
igual(r.cst, "410", "CST 410 preservado")
igual(r.classificacao_tributaria, "410002", "`cClassTrib` preservado")
igual(r.valor_ibs, None, "sem valor de IBS — None, não zero")
igual(r.valor_cbs, None, "sem valor de CBS — None, não zero")
igual(r.base, None, "sem base")
c = ext.conferir_reforma()
igual(c.estado, dm.REFORMA_SEM_VALORES, "estado SEM_VALORES")
ok(not c.comparavel, "não é comparável")
igual(c.confere, None, "e `confere` é None, NÃO False")
igual(c.itens_com_grupo, 1, "conta o item com grupo")
igual(c.itens_com_valor, 0, "e reconhece que nenhum trouxe valor")

secao("(d) XML com valores de IBS/CBS no item")
ext = ler(montar(nova_chave(), itens_reforma=[item_tributado()],
                 total=total_reforma()))
r = ext.itens[0].reforma
igual(r.valor_ibs, Decimal("1.50"), "IBS do item")
igual(r.valor_cbs, Decimal("9.00"), "CBS do item")
igual(r.base, Decimal("1000.00"), "base do item")
igual(r.cst, "000", "CST 000")

secao("(e) soma dos itens igual ao total declarado")
ext = ler(montar(nova_chave(), itens=2,
                 itens_reforma=[item_tributado(ibs="1.50", cbs="9.00"),
                                item_tributado(ibs="2.50", cbs="1.00")],
                 total=total_reforma(ibs="4.00", cbs="10.00")))
c = ext.conferir_reforma()
igual(c.estado, dm.REFORMA_CONFERE, "estado CONFERE")
ok(c.comparavel, "é comparável")
igual(c.confere, True, "`confere` é True")
igual(c.soma_ibs, Decimal("4.00"), "soma do IBS dos dois itens")
igual(c.soma_cbs, Decimal("10.00"), "soma da CBS dos dois itens")
igual(c.diferenca_ibs, Decimal("0.00"), "diferença de IBS é zero")
igual(c.diferenca_cbs, Decimal("0.00"), "diferença de CBS é zero")
igual(c.itens_com_valor, 2, "os dois itens entraram na conta")

secao("(e') e quando NÃO bate, a conferência acusa")
ext = ler(montar(nova_chave(), itens=2,
                 itens_reforma=[item_tributado(ibs="1.50", cbs="9.00"),
                                item_tributado(ibs="2.50", cbs="1.00")],
                 total=total_reforma(ibs="4.00", cbs="99.00")))
c = ext.conferir_reforma()
igual(c.estado, dm.REFORMA_DIVERGE, "estado DIVERGE")
igual(c.confere, False, "`confere` é False")
igual(c.diferenca_cbs, Decimal("-89.00"), "a diferença é o tamanho do erro")
igual(c.diferenca_ibs, Decimal("0.00"), "e o lado que bate continua zerado")

secao("(e'') um centavo de diferença JÁ é divergência — sem tolerância")
ext = ler(montar(nova_chave(), itens_reforma=[item_tributado(ibs="1.50")],
                 total=total_reforma(ibs="1.51")))
igual(ext.conferir_reforma().estado, dm.REFORMA_DIVERGE,
      "1,50 contra 1,51 não passa")

secao("(f) valor insuficiente para comparar: mistura de tributado e imune")
ext = ler(montar(nova_chave(), itens=2,
                 itens_reforma=[item_tributado(), item_imune()],
                 total=total_reforma()))
c = ext.conferir_reforma()
igual(c.estado, dm.REFORMA_SEM_VALORES,
      "um item sem valor basta para não haver base de comparação")
igual(c.itens_com_grupo, 2, "os dois itens têm grupo")
igual(c.itens_com_valor, 1, "mas só um trouxe valor")
igual(c.confere, None, "e isso NÃO é divergência")
ok(c.soma_ibs is None, "nem se inventa soma parcial")

secao("(f') total declarado sem item que o sustente")
ext = ler(montar(nova_chave(), total=total_reforma()))
c = ext.conferir_reforma()
igual(c.estado, dm.REFORMA_TOTAL_SEM_ITEM, "estado TOTAL_SEM_ITEM")
igual(c.confere, None, "não comparável")
igual(c.total_ibs, Decimal("1.50"), "mas o total declarado fica preservado")

secao("(f'') IBSCBSTot sem ICMSTot não derruba o parser")
# O `ICMSTot` deixou de ser a condição para existir `TotaisNFe`. Sem a
# guarda, ler o total da Reforma num XML sem ICMSTot levantaria TypeError.
ext = ler(montar(nova_chave(), itens_reforma=[item_tributado()],
                 total=total_reforma(), omitir_icmstot=True))
igual(ext.totais.ibs, Decimal("1.50"), "o total da Reforma é lido assim mesmo")
igual(ext.totais.base_icms, None, "e o total do ICMS fica ausente, não zero")

secao("COMPATIBILIDADE — nada do que já existia mudou")
chave = nova_chave()
antigo = ler(xml_nfe(chave))
com_reforma = ler(montar(chave, itens_reforma=[item_tributado()],
                         total=total_reforma()))
for campo in ("base_icms", "icms", "base_icms_st", "icms_st", "fcp",
              "produtos", "frete", "seguro", "desconto", "ipi", "pis",
              "cofins", "outros", "total"):
    igual(getattr(com_reforma.totais, campo), getattr(antigo.totais, campo),
          f"total `{campo}` idêntico com e sem a Reforma")
a, b = antigo.itens[0], com_reforma.itens[0]
igual(b.icms.cst, a.icms.cst, "CST do ICMS do item intacto")
igual(b.icms.valor, a.icms.valor, "valor do ICMS do item intacto")
igual(b.pis.valor, a.pis.valor, "PIS do item intacto")
igual(b.cofins.valor, a.cofins.valor, "COFINS do item intacto")
igual(b.valor_produto, a.valor_produto, "valor do produto intacto")

secao("A CONFERÊNCIA NÃO ALTERA O DOCUMENTO")
ext = ler(montar(nova_chave(), itens_reforma=[item_tributado()],
                 total=total_reforma()))
antes = (ext.totais, ext.itens)
ext.conferir_reforma()
ext.conferir_reforma()
ok(ext.totais is antes[0] and ext.itens is antes[1],
   "chamar duas vezes não troca nada no documento")
ok(isinstance(ext.conferir_reforma(), dm.ConferenciaReforma),
   "e ela devolve sempre um retrato, nunca levanta")

secao("NENHUMA CAMADA DE APURAÇÃO FOI TOCADA")
import importlib                                      # noqa: E402
import inspect                                        # noqa: E402
import re                                             # noqa: E402

from ingestao import normalizacao as nz               # noqa: E402

# A trava é sobre o CÓDIGO dos módulos de apuração: nenhum deles pode passar a
# falar de IBS/CBS por efeito desta etapa. Se um dia falar, é porque alguém
# implementou a fase seguinte — e aí este teste é o lugar de decidir isso.
PALAVRA = re.compile(r"\b(ibs|cbs|cclasstrib|reforma)\b", re.I)
for mod in ("ingestao.vendas", "classificador",
            "apuracao_federal", "iss", "retencao"):
    try:
        fonte = inspect.getsource(importlib.import_module(mod))
    except Exception as e:                            # pragma: no cover
        ok(False, f"não consegui ler o fonte de {mod}: {e}")
        continue
    achados = sorted(set(m.group(0).lower() for m in PALAVRA.finditer(fonte)))
    ok(not achados, f"`{mod}` segue sem IBS/CBS"
                    + (f" (achei {achados})" if achados else ""))

# `ingestao.normalizacao` SAIU desta lista na REFORMA 3, que é quando o grupo
# passou a ser transportado até o `ItemOperacao`. A trava não foi afrouxada:
# mudou de pergunta. Não é mais "a normalização fala de IBS/CBS?", e sim "ela
# faz CONTA com IBS/CBS?" — porque transportar é o trabalho dela, e calcular
# continua sendo o que ninguém pode fazer aqui.
fonte_nz = inspect.getsource(nz)
# SEM `\b`: o limite de palavra não casa dentro de `valor_ibs`, porque `_` é
# caractere de palavra — e foi assim que uma mutação que MULTIPLICAVA o IBS
# atravessou a primeira versão desta trava sem ser vista. Substring, portanto.
# E sem `/` na lista de operadores: ele aparece no texto "IBS/CBS" dos
# comentários e acusaria linha que não faz conta nenhuma.
CONTA = re.compile(r"[*%]|\bsum\(|\bround\(|[0-9]\s*\+\s*[0-9]")
contas = [l for l in fonte_nz.splitlines()
          if re.search(r"(ibs|cbs)", l, re.I) and CONTA.search(l)]
ok(not contas, "a normalização transporta IBS/CBS, mas não faz conta com eles"
               + (f" (achei {contas[:2]})" if contas else ""))
ok(isinstance(nz.ItemOperacao().reforma, dm.TributoReforma),
   "`ItemOperacao` carrega o grupo pelo tipo do projeto (REFORMA 3)")
ok(not nz.ItemOperacao().reforma.presente,
   "e ele nasce vazio: item sem Reforma não ganha valor nenhum")
ok(not hasattr(nz.Retencoes(), "ibs") and not hasattr(nz.Retencoes(), "cbs"),
   "`Retencoes` continua sem IBS/CBS")
ok(hasattr(dm.TotaisNFe(), "base_ibs_cbs"),
   "o campo novo existe onde devia: em `TotaisNFe`, e só lá")

_socket.socket.connect = _connect_original

print("\n" + "=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  - " + e)
    sys.exit(1)
print("REFORMA 1: IBSCBSTot lido, preservado e conferido — sem apurar nada.")
