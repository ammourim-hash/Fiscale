#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nota de ENTRADA não tem apuração: só retenções, ou nada.

    python teste_notas_entrada_impostos.py

POR QUE ESTE TESTE EXISTE
    A tela de notas do módulo NFS-e desenhava o quadro de tributos federais
    (DAS no Simples; PIS/COFINS e IRPJ/CSLL no Presumido/Real) também na aba de
    notas RECEBIDAS. A rota `/api/federal-empresa` ignorava o papel e apurava a
    RECEITA da empresa — e a tela punha isso em cima dos serviços TOMADOS, que
    são despesa. Parecia imposto a pagar sobre o que a empresa comprou.

    Três cenários, travados aqui:
      1. nota de SAÍDA: a apuração continua aparecendo, como sempre;
      2. nota de ENTRADA com retenção: aparecem SÓ as retenções na fonte;
      3. nota de ENTRADA sem retenção: nenhum bloco tributário.

    O DETALHE da nota é outra coisa: espelha o XML/DANFSe linha a linha, com
    "—" quando o campo está vazio, em saída e em entrada (teste_iss_recolher §10).

O QUE ESTE TESTE **NÃO** FAZ
    Não usa dado real, não vai à rede. A rota é chamada DIRETO (o projeto não
    tem `httpx` para o TestClient) sobre uma pasta de dados temporária.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                                 # noqa: E402
import teste_fixturas_fiscal as fx                          # noqa: E402

_ok = _falhas = 0
_erros: list = []

CONTA = "conta-entrada-1"
EMPRESA = fx.PRESTADOR          # a empresa do escritório
FORNECEDOR = fx.OUTRO           # quem presta serviço PARA ela
CLIENTE = fx.TOMADOR            # para quem ela presta

APURACAO = ("meses", "trimestres", "totais", "competencias")


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        _erros.append(desc)
        print("  FALHOU  " + desc)


def igual(a, b, desc):
    ok(a == b, desc if a == b else "%s  (obtive %r, esperava %r)" % (desc, a, b))


def gravar(raiz, numero, comp, valor, emitente, tomador, **kw):
    nome, bruto = fx.xml_nfse(numero, comp, valor, emitente=emitente,
                              tomador=tomador, **kw)
    d = Path(raiz) / EMPRESA / "xmls"
    d.mkdir(parents=True, exist_ok=True)
    (d / nome).write_bytes(bruto)


def carregar_main(raiz):
    import os
    os.environ["FISCALE_DADOS"] = str(raiz)
    import fiscale_dados as fd
    fd._raiz_cache = None
    fd.raiz(raiz)
    sys.modules.pop("main", None)
    import main
    return main


def main_():
    import apuracao_federal as af

    # ═══════════════════════════════════════════════════════════════════
    secao("A · A função pura: entrada nunca carrega apuração")
    com = af.retencoes_de_entrada([
        {"valor_retido": 46.50, "valor_irrf": 15.00, "valor_inss": 110.00,
         "inss_retido": True, "valor_iss_retido": 50.00, "iss_retido": True},
        {"valor_retido": 0.0, "valor_irrf": 0.0},
    ])
    igual(com["papel"], "recebidas", "papel recebidas")
    igual(com["apuracao"], False, "apuracao False")
    ok(com["tem_retencao"], "tem retenção")
    igual(com["retencoes"], {"csrf": 46.50, "irrf": 15.00, "inss": 110.00, "iss": 50.00},
          "as quatro retenções")
    igual(com["total_retido"], 221.50, "total retido")
    igual(com["qtd_notas_com_retencao"], 1, "uma nota com retenção")
    for k in APURACAO + ("pis_a_pagar", "das", "irpj_a_pagar"):
        ok(k not in com, "sem chave de apuração %r" % k)

    sem = af.retencoes_de_entrada([{"valor_retido": 0.0, "valor_irrf": 0.0,
                                    "valor_inss": 80.0, "inss_retido": False}])
    ok(not sem["tem_retencao"], "sem retenção -> tem_retencao False")
    igual(sem["total_retido"], 0.0, "total zero")
    igual(sem["retencoes"]["inss"], 0.0,
          "INSS só conta com a marca de retido (valor sem marca não é retenção)")
    ok(not af.retencoes_de_entrada([])["tem_retencao"], "lista vazia -> nada")

    # ═══════════════════════════════════════════════════════════════════
    with apoio.raiz_temporaria("entrada_impostos_") as raiz:
        (raiz / "certs").mkdir()
        (raiz / "certs" / "E.pfx").write_bytes(b"PFX-FALSO")
        (raiz / "certificados.json").write_text(json.dumps([{
            "id": CONTA, "cnpj": EMPRESA, "nome": "EMPRESA TESTE LTDA",
            "caminho": "certs/E.pfx"}]), encoding="utf-8")

        # AGOSTO: uma SAÍDA (com retenção sofrida) e uma ENTRADA com retenção.
        gravar(raiz, 1, "2026-08", 10000.00, EMPRESA, CLIENTE,
               ret_csll=465.00, ret_irrf=150.00)
        gravar(raiz, 2, "2026-08", 1000.00, FORNECEDOR, EMPRESA,
               ret_csll=46.50, ret_irrf=15.00)
        # SETEMBRO: só uma ENTRADA, sem retenção nenhuma.
        gravar(raiz, 3, "2026-09", 2000.00, FORNECEDOR, EMPRESA)

        m = carregar_main(raiz)
        m.regime_da_empresa = lambda *a, **k: "presumido"

        secao("1 · SAÍDA: a apuração do Presumido continua")
        s = m.api_federal_empresa(CONTA, "2026-08-01", "2026-08-31", papel="emitidas")
        igual(s.get("papel"), "emitidas", "papel emitidas")
        ok(s.get("apuracao") is True, "apuracao True")
        ok(s.get("meses"), "há meses apurados")
        ok("pis_devido" in (s.get("meses") or [{}])[0], "com PIS devido")
        ok(s.get("trimestres"), "e IRPJ/CSLL do trimestre (Presumido)")
        igual((s.get("meses") or [{}])[0].get("receita"), 10000.00,
              "a receita é SÓ a nota emitida (a tomada não entra)")
        s2 = m.api_federal_empresa(CONTA, "2026-08-01", "2026-08-31")
        igual(s2.get("papel"), "emitidas", "sem papel informado, continua sendo saída")

        secao("2 · ENTRADA COM RETENÇÃO: só as retenções")
        e = m.api_federal_empresa(CONTA, "2026-08-01", "2026-08-31", papel="recebidas")
        igual(e.get("papel"), "recebidas", "papel recebidas")
        igual(e.get("apuracao"), False, "apuracao False")
        for k in APURACAO:
            ok(k not in e, "NENHUMA chave de apuração: %r ausente" % k)
        ok(e.get("tem_retencao"), "tem retenção")
        igual(e["retencoes"]["csrf"], 46.50, "CSRF da nota TOMADA (46,50), não da emitida (465,00)")
        igual(e["retencoes"]["irrf"], 15.00, "IRRF da tomada (15,00), não da emitida (150,00)")
        igual(e["total_retido"], 61.50, "total 61,50")

        secao("3 · ENTRADA SEM RETENÇÃO: nenhum bloco")
        v = m.api_federal_empresa(CONTA, "2026-09-01", "2026-09-30", papel="recebidas")
        igual(v.get("apuracao"), False, "apuracao False")
        ok(not v.get("tem_retencao"), "tem_retencao False")
        igual(v.get("total_retido"), 0.0, "total zero")
        for k in APURACAO:
            ok(k not in v, "nenhuma chave de apuração: %r ausente" % k)

    # ═══════════════════════════════════════════════════════════════════
    secao("4 · A tela: pede pelo papel e desenha só o que a rota decidiu")
    tela = (RAIZ / "nfse" / "frontend" / "index.html").read_text("utf-8")
    ini = tela.index("async function carregarFederal(){")
    corpo = tela[ini:tela.index("\n}\n", ini)]
    ok("&papel=${papel}" in corpo, "carregarFederal manda o papel para a rota")
    i_rec = corpo.find("if(papel==='recebidas')")
    i_meses = corpo.find("if(!d.meses")
    ok(0 < i_rec < i_meses,
       "o ramo da ENTRADA sai ANTES de qualquer desenho de apuração")
    ok("if(papel!==modo) return;" in corpo,
       "resposta atrasada não cai na aba errada")
    ok("if(modo!=='emitidas' || !certSel) return;" in tela,
       "o quadro do DAS continua restrito às emitidas")
    # O DETALHE DA NOTA mudou em 13/09/2026: ele espelha o XML/DANFSe e mostra
    # TODA linha, com "—" quando vazia, também na entrada. O quadro de entrada
    # (acima e na seção 5) continua mostrando só as retenções.
    ok("(entrada && !(somaRet>0)) ? ''" not in tela and "(entrada && zero) ? ''" not in tela,
       "detalhe da nota: nenhuma linha some por ser entrada (espelho do XML)")

    secao("5 · A tela, executada: as três saídas do quadro de entrada")
    node = shutil.which("node")
    if not node:
        print("  (node indisponível nesta máquina — execução JS não verificada)")
    else:
        ini = tela.index("function quadroRetencoesEntrada(d){")
        fim = tela.index("\n}\n", ini) + 3
        js = ("const BRL=v=>'R$ '+Number(v||0).toFixed(2);\n" + tela[ini:fim] +
              "\nconst sem=quadroRetencoesEntrada({papel:'recebidas',tem_retencao:false,"
              "retencoes:{csrf:0,irrf:0,inss:0,iss:0},total_retido:0});"
              "\nconst com=quadroRetencoesEntrada({papel:'recebidas',tem_retencao:true,"
              "retencoes:{csrf:46.5,irrf:15,inss:0,iss:0},total_retido:61.5,qtd_notas_com_retencao:1});"
              "\nconsole.log(JSON.stringify({sem:sem,com:com}));")
        saida = subprocess.run([node, "-e", js], capture_output=True, text=True,
                               encoding="utf-8", timeout=30)
        ok(saida.returncode == 0, "o JS roda (%s)" % (saida.stderr.strip()[:80] or "ok"))
        if saida.returncode == 0:
            r = json.loads(saida.stdout)
            igual(r["sem"], "", "entrada SEM retenção -> string vazia (nada na tela)")
            ok("Retenções na fonte" in r["com"], "entrada COM retenção -> quadro de retenções")
            ok("Contrib. sociais (CSRF)" in r["com"] and "IRRF" in r["com"],
               "com as retenções que existem")
            ok("INSS retido" not in r["com"] and "ISS retido" not in r["com"],
               "e sem as que são zero")
            for proibido in ("DAS", "PIS a pagar", "COFINS a pagar", "IRPJ", "Presumido",
                             "Lucro Real", "devido"):
                ok(proibido not in r["com"], "nenhum termo de apuração: %r" % proibido)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main_())
