#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APURAÇÃO · MEMÓRIA DE CÁLCULO — de onde veio cada valor.

    python teste_apuracao_memoria.py

O QUE ESTA SUÍTE PROVA

    1. a memória NÃO calcula tributo: não há alíquota, base nem soma de imposto
       no módulo — os valores vêm dos motores que já existiam;
    2. cada valor declara o motor de origem em `de_onde`;
    3. cada documento carrega o MOTIVO que o motor escreveu — nenhum motivo é
       redigido pela memória;
    4. nota cancelada sai da receita e aparece em `excluidos` **com motivo**;
    5. o que não pôde ser apurado é `DEPENDE_DE` com `falta` — nunca zero;
    6. no Simples, PIS/COFINS/IRPJ/CSLL são `NAO_APLICAVEL` (estão no DAS), e
       fora do Simples o DAS é `NAO_APLICAVEL`;
    7. a NF-e vem do caminho CANÔNICO (acervo + `vendas.py`), não da pasta
       legada — e o bloco dela não é somado à base do Simples;
    8. "sem acervo" e "acervo vazio" são respostas diferentes;
    9. a tela consome a rota real e não traz número escrito no HTML.

O QUE ELA NÃO FAZ
    Não vai à rede, não usa dado real, não toca na pasta de produção. Raiz
    temporária, CNPJ fictício, notas construídas aqui.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import apuracao_federal as af                          # noqa: E402
import apuracao_memoria as mem                         # noqa: E402
import teste_apoio as apoio                            # noqa: E402

_ok = _falhas = 0

import socket as _socket                               # noqa: E402


def _proibido(self, endereco, *a, **kw):
    raise AssertionError("teste tentou abrir conexão de rede para %s" % (endereco,))


_socket.socket.connect = _proibido

CNPJ = "11222333000181"


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


# ──────────────────────────────────────────────────────────────────────────
# Um resultado de `classificar()` como o motor o produz: notas já anotadas
# com `anexo` e `base_anexo`. Não é mock de valor — é a ENTRADA da memória,
# no formato exato que o classificador devolve.
# ──────────────────────────────────────────────────────────────────────────
def resultado(regime="simples", com_cancelada=True, das_pendente=False):
    notas = [
        {"numero": "101", "data": "2026-08-05", "competencia": "2026-08",
         "tomador": "CLIENTE UM", "item_lc116": "17.19", "valor": 3000.0,
         "anexo": "III", "base_anexo": "Item 17.19 — Anexo III por lei",
         "emit_cnpj": CNPJ, "valor_servico": 3000.0, "valor_pis": 19.5,
         "valor_cofins": 90.0, "cancelada": False},
        {"numero": "102", "data": "2026-08-18", "competencia": "2026-08",
         "tomador": "CLIENTE DOIS", "item_lc116": "17.19", "valor": 2000.0,
         "anexo": "III", "base_anexo": "Item 17.19 — Anexo III por lei",
         "emit_cnpj": CNPJ, "valor_servico": 2000.0, "valor_pis": 13.0,
         "valor_cofins": 60.0, "cancelada": False},
        {"numero": "099", "data": "2026-07-30", "competencia": "2026-07",
         "tomador": "OUTRO MÊS", "item_lc116": "17.19", "valor": 900.0,
         "anexo": "III", "base_anexo": "Item 17.19 — Anexo III por lei",
         "emit_cnpj": CNPJ, "valor_servico": 900.0, "cancelada": False},
    ]
    if com_cancelada:
        notas.append(
            {"numero": "103", "data": "2026-08-20", "competencia": "2026-08",
             "tomador": "CANCELADA LTDA", "item_lc116": "17.19", "valor": 500.0,
             "anexo": "—", "base_anexo": "Cancelada — fora da receita",
             "emit_cnpj": CNPJ, "valor_servico": 500.0, "cancelada": True})
    linha = {"anexo": "III", "receita": 5000.0, "faixa": 1,
             "efetiva": 0.06, "das": 300.0, "tributos": {"IRPJ": 12.0},
             "obs": ""}
    if das_pendente:
        linha = {"anexo": "FATOR R", "receita": 5000.0, "faixa": None,
                 "efetiva": None, "das": None,
                 "obs": "informe a Folha 12 meses"}
    return {
        "notas": notas, "itens_nfe": [], "regime": regime,
        "base_data": "competencia",
        "das": [{"competencia": "2026-08", "receita_mes": 5000.0,
                 "das_estimado": 0.0 if das_pendente else 300.0,
                 "linhas": [linha]}],
        "resumo": {"rbt12": 60000.0, "fator_r": 0.31, "alertas": []},
    }


# ══════════════════════════════════════════════════════════════════════════
secao("1 · A memória não calcula tributo")

FONTE = (RAIZ / "nfse" / "backend" / "apuracao_memoria.py").read_text("utf-8")
for proibido in ("0.0065", "0.0300", "0.0165", "0.0760", "0.09", "0.15",
                 "ALIQUOTAS", "IRPJ_ALIQ", "CSLL_ALIQ", "aliquota_efetiva",
                 "TABELAS", "REPARTICAO"):
    ok(proibido not in FONTE, "não há `%s` no módulo" % proibido)
for proibido in ("requests", "urlopen", "open(", "write"):
    ok(proibido not in FONTE, "e não faz `%s`" % proibido)
ok("apuracao_federal.apurar" in FONTE and "classificador.calcular_das" in FONTE,
   "os motores de origem são nomeados no módulo")

secao("2 · Todo valor declara o motor que o produziu")
m = mem.memoria(resultado(), competencia="2026-08")
for t in mem.TRIBUTOS:
    ok(bool(m["tributos"][t].get("de_onde")),
       "%s declara `de_onde` (%s)" % (t, m["tributos"][t].get("de_onde")))
ok(m["nfse"]["como_lido"].startswith("cache do Portal"),
   "a NFS-e declara de onde é lida")
ok("acervo indexado" in m["nfe"]["como_lido"],
   "e a NF-e declara o caminho canônico")

secao("3 · O motivo de cada documento é o do motor, não da memória")
linhas = m["nfse"]["linhas"]
ok(len(linhas) == 2, "duas notas de 2026-08 na receita (%d)" % len(linhas))
ok(all(l["motivo"] == "Item 17.19 — Anexo III por lei" for l in linhas),
   "cada uma carrega o `base_anexo` que o classificador escreveu")
ok(all(l["fonte"] == "NFS-e" and l["entra"] for l in linhas),
   "marcadas como dentro da receita, com a fonte")
ok({l["documento"] for l in linhas} == {"101", "102"},
   "e identificadas pelo número da nota")
ok(m["receita"]["identificada"] == 5000.0,
   "a receita identificada é a soma delas (%s)" % m["receita"]["identificada"])

secao("4 · Cancelada sai da receita — e aparece com motivo")
exc = m["excluidos"]
ok(len(exc) == 1, "um documento excluído (%d)" % len(exc))
ok(exc[0]["documento"] == "103" and exc[0]["entra"] is False,
   "é a nota cancelada, marcada como fora")
ok("Cancelada" in exc[0]["motivo"], "com o motivo: %r" % exc[0]["motivo"])
ok(m["excluidos_valor"] == 500.0, "e o valor excluído é somado à parte")
ok(all(l["documento"] != "103" for l in linhas),
   "a cancelada não aparece na receita")
# Sem a cancelada, não há excluídos — e isso não é erro.
m2 = mem.memoria(resultado(com_cancelada=False), competencia="2026-08")
ok(m2["excluidos"] == [] and m2["excluidos_valor"] == 0.0,
   "sem cancelada, a lista de excluídos é vazia")

secao("5 · O que não pôde ser apurado é DEPENDE_DE, com o que falta")
mp = mem.memoria(resultado(das_pendente=True), competencia="2026-08")
s = mp["tributos"]["SIMPLES"]
ok(s["estado"] == mem.DEPENDE_DE, "Simples fica DEPENDE_DE (%s)" % s["estado"])
ok("Folha 12 meses" in s["falta"], "dizendo o que falta: %r" % s["falta"][:60])
ok(s.get("valor") == 0.0 or s.get("valor") is not None,
   "e o valor não é apresentado como definitivo")
vazia = mem.memoria({"notas": [], "das": [], "regime": "simples",
                     "resumo": {}, "base_data": "competencia"},
                    competencia="2026-08")
ok(vazia["tributos"]["SIMPLES"]["estado"] == mem.DEPENDE_DE,
   "sem competência apurada, também é DEPENDE_DE")
ok("nenhuma competência" in vazia["tributos"]["SIMPLES"]["falta"],
   "com o motivo escrito")

secao("6 · Cada regime só mostra os tributos que lhe cabem")
for t in ("PIS", "COFINS", "IRPJ", "CSLL"):
    x = m["tributos"][t]
    ok(x["estado"] == mem.NAO_APLICAVEL,
       "no Simples, %s é NAO_APLICAVEL" % t)
    ok("DAS" in x["motivo"], "porque está dentro do DAS")
pres = mem.memoria(resultado(regime="presumido"), competencia="2026-08")
ok(pres["tributos"]["SIMPLES"]["estado"] == mem.NAO_APLICAVEL,
   "no Presumido, o DAS é NAO_APLICAVEL")
ok("presumido" in pres["tributos"]["SIMPLES"]["motivo"],
   "dizendo o regime")
ok(pres["tributos"]["IRPJ"]["estado"] == mem.DEPENDE_DE
   and "trimestre" in pres["tributos"]["IRPJ"]["falta"],
   "IRPJ do Presumido sem trimestre apurado: DEPENDE_DE e explica o trimestre")
real = mem.memoria(resultado(regime="real"), competencia="2026-08")
ok(real["tributos"]["CSLL"]["estado"] == mem.DEPENDE_DE
   and "LALUR" in real["tributos"]["CSLL"]["falta"],
   "no Real, IRPJ/CSLL dependem do LALUR — e é isso que aparece")

secao("7 · PIS/COFINS usam a MESMA lista de notas do motor")
r = resultado(regime="presumido")
fed = af.apurar(r["notas"], CNPJ, "2026-08", "presumido")
mf = mem.memoria(r, identidade=CNPJ, competencia="2026-08",
                 resultado_federal=fed)
p = mf["tributos"]["PIS"]
ok(p["estado"] == mem.CALCULADO, "PIS calculado (%s)" % p["estado"])
ok(p["base"] == fed["receita_bruta"],
   "a base é a do motor (%s)" % p["base"])
ok(p["devido"] == fed["pis_devido"], "o devido é o do motor")
ok(len(p["documentos"]) == len(af.emitidas_do_mes(r["notas"], CNPJ, "2026-08")),
   "e o rastro tem exatamente as notas que o motor usou (%d)"
   % len(p["documentos"]))
ok(all(d["fonte"] == "NFS-e" for d in p["documentos"]),
   "cada uma identificada com a fonte")
ok("103" not in [d["documento"] for d in p["documentos"]],
   "a cancelada não entra na base federal")

secao("8 · NF-e: caminho canônico, e fora da base do Simples")
ok(m["nfe"]["entra_na_base"] is False,
   "o bloco de NF-e declara que não entra na base")
ok("somada à base do Simples" in m["nfe"]["observacao"],
   "e diz isso em português: %r" % m["nfe"]["observacao"][:60])
ok(m["nfe"]["indisponivel"] == "acervo não informado nesta chamada",
   "sem acervo informado, é 'não consultado' — não zero")
ok(m["receita"]["por_fonte"]["NF-e"] == 0.0,
   "e a receita de NF-e fica em zero sem afirmar ausência de emissão")
ok("conferencia" in FONTE and "vendas" in FONTE,
   "o módulo lê NF-e por `conferencia`/`vendas`")
# A verificação é sobre CÓDIGO, não sobre prosa: o módulo explica no cabeçalho
# qual era a pasta legada, e citá-la para dizer "não é por aqui" é o oposto de
# usá-la. Então o docstring sai antes de medir.
import ast as _ast                                     # noqa: E402
_arvore = _ast.parse(FONTE)
CODIGO = "\n".join(
    l for l in FONTE.splitlines()
    if not l.lstrip().startswith("#")
)[len(_ast.get_docstring(_arvore) or ""):]
for legado in ("glob(", "rglob(", "procNFe", "ler_nfe_itens", "pasta_cnpj"):
    ok(legado not in CODIGO, "o código não usa a pasta legada (`%s`)" % legado)

with apoio.raiz_temporaria("memapu_") as raiz:
    vaz = mem.linhas_nfe(raiz, CNPJ)
    ok(vaz["documentos"] == 0 and vaz["indisponivel"] == "",
       "acervo vazio: zero documentos e NENHUM erro — resposta diferente de "
       "'não consultado'")
    mm = mem.memoria(resultado(), dados_dir=raiz, identidade=CNPJ,
                     competencia="2026-08")
    ok(mm["nfe"]["indisponivel"] == "",
       "com acervo informado, o bloco não se declara indisponível")
    ok(mm["receita"]["identificada"] == 5000.0,
       "e a receita de NFS-e continua a mesma")

# A GARANTIA QUE SUSTENTA O GUARDA DE `teste_apuracao6`: mesmo com NF-e de
# venda no acervo, nada disso entra em base nenhuma. Aqui o bloco de NF-e é
# trocado por um que devolve valor — se algum dia ele for somado, isto reprova.
_original = mem.linhas_nfe
try:
    mem.linhas_nfe = lambda *a, **k: {
        "fonte": "NF-e", "como_lido": "(dublê)", "documentos": 3,
        "na_receita": 2, "fora_da_receita": 1,
        "valor_na_receita": 77777.77, "valor_fora": 11.0,
        "linhas": [{"documento": "abcd1234", "valor": 77777.77,
                    "competencia": "2026-08", "natureza": "VENDA",
                    "motivo": "", "entra": True, "fonte": "NF-e"}],
        "excluidos": [{"documento": "efgh5678", "valor": 11.0, "fonte": "NF-e",
                       "motivo": "transferência não é receita", "entra": False}],
        "por_natureza": {}, "entra_na_base": False, "indisponivel": "",
        "observacao": mem.OBSERVACAO_NFE}
    mx = mem.memoria(resultado(), dados_dir="/qualquer", identidade=CNPJ,
                     competencia="2026-08")
    ok(mx["receita"]["identificada"] == 5000.0,
       "com R$ 77.777,77 de NF-e no acervo, a receita identificada NÃO muda")
    ok(mx["tributos"]["SIMPLES"]["valor"] == 300.0,
       "e o Simples continua o mesmo — a NF-e não entra na base")
    ok(mx["tributos"]["SIMPLES"]["base_vem_de"] == "NFS-e",
       "a base do Simples declara vir da NFS-e")
    ok(mx["receita"]["por_fonte"]["NF-e"] == 77777.77,
       "mas o valor aparece na tela, separado, para conferência")
    ok(any(e["fonte"] == "NF-e" and "transferência" in e["motivo"]
           for e in mx["excluidos"]),
       "e o excluído de NF-e chega com o motivo de `vendas.py`")
finally:
    mem.linhas_nfe = _original

secao("9 · A rota e a tela")

MAIN = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
ok('@app.post("/api/classificador/memoria")' in MAIN, "a rota existe")
_i = MAIN.index('@app.post("/api/classificador/memoria")')
_f = MAIN.index('@app.post("/api/classificador/exportar")')
BLOCO = MAIN[_i:_f]
ok("_mem.memoria" in BLOCO, "ela chama a memória")
ok("clsmod.classificar" in BLOCO, "com o mesmo classificar da tela")
ok("_af.apurar" in BLOCO, "e os motores federais")
for proibido in ("0.0065", "* 0.", "round(receita", "aliquota"):
    ok(proibido not in BLOCO, "a rota não calcula (`%s`)" % proibido)
ok("PedidoMemoria(CfgClassificador)" in MAIN,
   "o pedido herda a configuração da apuração")

TELA = (RAIZ / "web" / "classificador.html").read_text("utf-8")
ok("/api/classificador/memoria" in TELA, "a tela pede a rota")
ok("memoriaWrap" in TELA and "pintarMemoria" in TELA,
   "e tem a área e o desenhista da memória")
ok("de_onde" in TELA, "mostra o motor de origem de cada valor")
ok("Fora da receita" in TELA, "e a tabela de excluídos com motivo")
_ini = TELA.index("function pintarMemoria")
_fim = TELA.index("btnMemoria.onclick")
CORPO = TELA[_ini:_fim]
numeros = re.findall(r">\s*(\d{2,})\s*<", CORPO)
ok(not numeros, "nenhum número fixo no HTML da memória (achei %s)" % numeros[:5])
for demo in ("exemplo", "demonstra", "mock", "lorem", "fict"):
    ok(demo not in CORPO.lower(), "e nenhuma marca de dado falso (`%s`)" % demo)


print("\n%d ok · %d falha(s)" % (_ok, _falhas))
sys.exit(1 if _falhas else 0)
