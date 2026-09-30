#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CENTRO DE OPERAÇÕES — a fila de trabalho, e o que ela não inventa.

    python teste_centro_operacoes.py

O QUE ESTA SUÍTE PROVA

    1. o agregador só LÊ: não escreve arquivo, não vai à rede, não calcula;
    2. bloco que não pôde ser medido devolve `indisponivel` com motivo —
       nunca zero, e nunca entra no total;
    3. certificado vencido, vencendo, sem validade lida e ausente são QUATRO
       respostas diferentes;
    4. nenhum CNPJ cru sai na resposta — tudo mascarado;
    5. cada bloco declara a `fonte` de onde o número saiu;
    6. um bloco que estoura não derruba os outros;
    7. a rota `/api/centro` é do operador e responde pela rede;
    8. a tela consome a rota real, tem os atalhos pedidos e não traz número
       escrito no HTML.

O QUE ELA NÃO FAZ
    Não vai à rede externa, não usa dado real, não toca na pasta de produção.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_centro as fc                            # noqa: E402
import teste_apoio as apoio                            # noqa: E402

_ok = _falhas = 0

ALFA = "11222333000181"
BETA = "66789006000106"
GAMA = "45997418000153"
HOJE = date(2026, 9, 27)


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        print("  FALHOU   " + desc)


def clientes():
    """Quatro situações de certificado — e uma empresa sem nenhum."""
    return [
        {"nome": "ALFA LTDA", "cnpj": ALFA, "cert": {"arquivo": "a.pfx"},
         "certValidade": "2020-01-01"},                        # vencido
        {"nome": "BETA LTDA", "cnpj": BETA, "cert": {"arquivo": "b.pfx"},
         "certValidade": (HOJE + timedelta(days=10)).isoformat()},   # vencendo
        {"nome": "GAMA LTDA", "cnpj": GAMA, "cert": {"arquivo": "g.pfx"},
         "certValidade": ""},                                  # sem validade
        {"nome": "DELTA LTDA", "cnpj": "45283163000167"},      # sem certificado
    ]


# ══════════════════════════════════════════════════════════════════════════
secao("1 · O agregador só lê")

FONTE = (RAIZ / "fiscale_centro.py").read_text("utf-8")
_corpo = "\n".join(l for l in FONTE.splitlines()
                   if not l.lstrip().startswith("#"))
for proibido in ("requests", "urlopen", "http.client", "socket",
                 "write_text", "open(", "commit(", "INSERT", "UPDATE",
                 "DELETE", "subprocess"):
    ok(proibido not in _corpo, "não faz `%s`" % proibido)
ok("read_text" in _corpo, "lê arquivos de estado")
ok("cq.contar" in _corpo, "e conta no índice, em vez de abrir o acervo")
for aliquota in ("0.0065", "0.06", "* 0.", "aliquota"):
    ok(aliquota not in _corpo, "e não calcula tributo (`%s`)" % aliquota)


# ══════════════════════════════════════════════════════════════════════════
secao("2 · Quatro situações de certificado, quatro respostas")
b = fc.empresas_atencao(clientes(), hoje=HOJE)
ok(b["valor"] == 4, "as quatro empresas aparecem (%s)" % b["valor"])
motivos = " | ".join(i["motivo"] for i in b["itens"])
ok("VENCIDO" in motivos, "o vencido é chamado de vencido")
ok("vence em" in motivos, "o que está para vencer diz a data")
ok("sem validade lida" in motivos,
   "sem validade é 'não sei', não 'vencido'")
ok("sem certificado" in motivos, "e a empresa sem certificado aparece")
ok(all(i["onde"] == "clientes.html" for i in b["itens"]),
   "todas apontam para onde resolver")
ok(b["fonte"].startswith("cadastro"), "declarando a fonte: %r" % b["fonte"])
# Um certificado válido e longe do vencimento NÃO é pendência.
limpo = fc.empresas_atencao(
    [{"nome": "OK LTDA", "cnpj": ALFA, "cert": {"arquivo": "x.pfx"},
      "certValidade": (HOJE + timedelta(days=200)).isoformat()}], hoje=HOJE)
ok(limpo["valor"] == 0 and limpo["indisponivel"] == "",
   "certificado em dia não vira pendência, e o bloco não fica indisponível")


# ══════════════════════════════════════════════════════════════════════════
secao("3 · Ausência de dado não é zero")
with apoio.raiz_temporaria("centro_") as raiz:
    f = fc.fila(raiz, clientes(), hoje=HOJE)

    ok(f["blocos"]["documentos_pendentes"]["valor"] is None,
       "sem pasta vigiada configurada, o bloco é indisponível")
    ok("não foi configurada" in f["blocos"]["documentos_pendentes"]["indisponivel"],
       "dizendo por quê")
    ok(f["blocos"]["apuracoes_pendentes"]["valor"] is None,
       "sem estado de apuração, o bloco é indisponível")
    ok("nenhuma apuração" in f["blocos"]["apuracoes_pendentes"]["indisponivel"],
       "também com motivo")
    ok("documentos_pendentes" in f["blocos_indisponiveis"]
       and "apuracoes_pendentes" in f["blocos_indisponiveis"],
       "e os dois são listados como sem resposta")

    # O total NÃO soma os indisponíveis como zero.
    soma = sum(b["valor"] for b in f["blocos"].values()
               if b["valor"] is not None)
    ok(f["total_pendencias"] == soma,
       "o total conta só o que foi medido (%s)" % f["total_pendencias"])

    secao("4 · Nenhum CNPJ cru sai na resposta")
    bruto = json.dumps(f, ensure_ascii=False)
    for doc in (ALFA, BETA, GAMA, "45283163000167"):
        ok(doc not in bruto, "o CNPJ %s… não aparece" % doc[:6])
    ok("11.***.***/0001-81" in bruto, "e a forma mascarada aparece")

    secao("5 · Todo bloco declara a fonte")
    for nome in fc.BLOCOS:
        ok(bool(f["blocos"][nome]["fonte"]),
           "%s declara fonte (%s)" % (nome, f["blocos"][nome]["fonte"][:40]))
    ok(f["ordem"] == list(fc.BLOCOS), "a ordem dos blocos é a declarada")
    ok("indisponivel" in f["nada_inventado"],
       "e a resposta explica a própria regra")

    secao("6 · Bloco que estoura não derruba a fila")
    original = fc.inconsistencias
    try:
        def explode(*a, **k):
            raise RuntimeError("proposital")
        fc.inconsistencias = explode
        g = fc.fila(raiz, clientes(), hoje=HOJE)
        ok(g["blocos"]["inconsistencias"]["valor"] is None,
           "o bloco que estourou fica indisponível")
        ok("RuntimeError" in g["blocos"]["inconsistencias"]["indisponivel"],
           "com o nome da exceção: %r"
           % g["blocos"]["inconsistencias"]["indisponivel"])
        ok(g["blocos"]["empresas_atencao"]["valor"] == 4,
           "e os outros blocos continuam respondendo")
    finally:
        fc.inconsistencias = original

    secao("7 · Empresa sem índice nem estado não gera pendência falsa")
    ok(fc.inconsistencias(raiz, clientes())["valor"] == 0,
       "sem índice, zero documentos em quarentena — e o bloco responde")
    ok(fc.falhas_captura(raiz)["valor"] == 0,
       "sem checkpoint, zero falhas de captura")


# ══════════════════════════════════════════════════════════════════════════
secao("8 · A rota é do operador, e a tela consome a real")

import fiscale_papeis as pp                            # noqa: E402
ok(pp.pode(pp.OPERADOR, "GET", "/api/centro"),
   "o operador alcança GET /api/centro")
ok(pp.pode(pp.ADMIN, "GET", "/api/centro"), "e o administrador também")

SERV = (RAIZ / "fiscale_server.py").read_text("utf-8")
ok('rota == "/api/centro"' in SERV, "a rota existe no servidor")
_i = SERV.index('rota == "/api/centro"')
BLOCO = SERV[_i:SERV.index('if rota == "/api/auditoria/saude"', _i)]
ok("fiscale_centro.fila" in BLOCO, "e chama a fila")
ok("ler_estado(\"clientes\")" in BLOCO, "com o cadastro que já existe")
# `sys.stderr.write` é log de falha, não gravação de dado — por isso a
# verificação é sobre quem ESCREVE ESTADO, não sobre a palavra "write".
for proibido in ("salvar_estado", "escrever_estado", "_salvar", "POST",
                 "open(", "write_text"):
    ok(proibido not in BLOCO, "a rota não escreve estado (`%s`)" % proibido)

TELA = (RAIZ / "web" / "home_portal.html").read_text("utf-8")
ok("/api/centro" in TELA, "a tela pede /api/centro")
ok("carregarFila" in TELA and 'id="fila"' in TELA,
   "e tem a área da fila")
ok("indisponivel" in TELA, "a tela trata bloco indisponível")
ok("b.valor === null" in TELA, "distinguindo null de zero")
for atalho in ("nfe_documentos.html", "nfse.html", "cte.html",
               "clientes.html"):
    ok(atalho in TELA, "atalho para %s" % atalho)
ok("somente_admin" in TELA,
   "e não oferece ao operador link de página de administrador")
_ini = TELA.index("async function carregarFila")
_fim = TELA.index("identificar();", _ini)
CORPO = TELA[_ini:_fim]
numeros = re.findall(r">\s*(\d{2,})\s*<", CORPO)
ok(not numeros, "nenhum número fixo no HTML da fila (achei %s)" % numeros[:5])
for demo in ("exemplo", "demonstra", "mock", "lorem", "fict"):
    ok(demo not in CORPO.lower(), "e nenhuma marca de dado falso (`%s`)" % demo)


print("\n%d ok · %d falha(s)" % (_ok, _falhas))
sys.exit(1 if _falhas else 0)
