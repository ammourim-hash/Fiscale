#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retenção de contribuições sociais (CRF/CSRF): sem duplicar, sem descartar, e o alerta dos 4,65%.

    python teste_retencao_csrf.py

POR QUE ESTE TESTE EXISTE
    O campo "contribuições sociais - retidas" (vRetCSLL) tem DOIS significados
    no acervo real, medidos em 13/09/2026:

      • vRetCSLL = 4,65% com vPis/vCofins destacados → já é o TOTAL. O código
        somava PIS e COFINS por cima e DUPLICAVA: 9 notas, R$ 49,82 a mais
        (GEST WEB 37,68 em vez de 33,06; JBS 26,97 em vez de 15,11);
      • vRetCSLL = 1% com vPis/vCofins destacados → é SÓ a CSLL, e o total é a
        soma. Considerar estritamente o campo aqui repetiria o erro corrigido
        em 04/08/2026, que jogava fora R$ 989,95 de PIS/COFINS em 19 notas.

    Este teste trava as duas pontas ao mesmo tempo — corrigir uma quebrando a
    outra é exatamente o que já aconteceu uma vez em cada direção.

    E trava o ALERTA: retenção social > 0 que não corresponde a 4,65% do
    serviço (Lei 10.833/2003) acende pendência de conferência na nota e no
    resumo. Uma nota com 5% dispara; a de 4,65% com arredondamento de centavo,
    não.

O QUE ESTE TESTE **NÃO** FAZ
    Não usa XML real (os valores dos casos reais foram copiados, os documentos
    não), não vai à rede e não lê a pasta de dados.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_fixturas_fiscal as fx                          # noqa: E402

_ok = _falhas = 0
_erros: list = []


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


def nota(numero, valor, **kw):
    """`core.parse_nfse` e `classificador._retencoes_federais` sobre o MESMO XML."""
    import classificador
    import core
    _, bruto = fx.xml_nfse(numero, "2026-08", valor, **kw)
    xml = bruto.decode("utf-8")
    n = core.parse_nfse(xml)
    inf = ET.fromstring(xml).find("{%s}infNFSe" % fx.NS)
    return n, classificador._retencoes_federais(inf)


def main():
    import core
    import retencao

    # ═══════════════════════════════════════════════════════════════════
    secao("1 · A DUPLICIDADE ACABOU: vRetCSLL consolidado vale estritamente ele")
    # Valores do caso real da GEST WEB (nº 834): serviço 711,00.
    n, c = nota(1, 711.00, tp_ret_piscofins="3", ret_pis=4.62, ret_cofins=21.33,
                ret_csll=33.06)
    igual(n["valor_retido"], 33.06,
          "retido = o campo de contribuições sociais (33,06), e não 37,68 nem 59,01")
    ok(n["valor_retido"] != round(4.62 + 21.33 + 33.06, 2),
       "a soma dos três NÃO é mais o total")
    ok(n["valor_retido"] != round(4.62 + 33.06, 2),
       "nem PIS + campo (o 37,68 que o código anterior gravava)")
    ok(n["retencao_consolidada"], "marcada como consolidada no campo")
    igual(round(n["valor_pis"] + n["valor_cofins"] + n["valor_csll"], 2), 33.06,
          "a composição exibida fecha exatamente com o campo")
    igual(n["valor_csll"], 7.11, "a CSLL é o resto (1%): 33,06 − 4,62 − 21,33")
    igual(c["ret_total"], 33.06, "o classificador lê o MESMO total")

    n, c = nota(2, 1000.00, tp_ret_piscofins="1", ret_pis=6.50, ret_cofins=30.00,
                ret_csll=46.50)
    igual(n["valor_retido"], 46.50, "tpRetPisCofins=1 consolidado: 46,50, não 83,00")
    igual(c["ret_total"], 46.50, "e o classificador concorda")
    ok(not n["alerta_csrf"], "4,65% exatos: sem alerta")

    # Caso real da JBS (nº 4665): sem tpRetPisCofins, serviço 325,00.
    n, _ = nota(3, 325.00, tp_ret_piscofins="", ret_pis=2.11, ret_cofins=9.75,
                ret_csll=15.11)
    igual(n["valor_retido"], 15.11, "consolidado sem tpRetPisCofins: 15,11")

    # ═══════════════════════════════════════════════════════════════════
    secao("2 · E NÃO VOLTOU O ERRO DE 04/08: CSLL isolada continua somando")
    # Caso real da Clínica Ribas: serviço 2.709,00, CSLL = 1%.
    n, c = nota(4, 2709.00, tp_ret_piscofins="1", ret_pis=17.61, ret_cofins=81.27,
                ret_csll=27.09)
    igual(n["valor_retido"], 125.97,
          "vRetCSLL = 1% é SÓ a CSLL: total = 17,61 + 81,27 + 27,09")
    ok(not n["retencao_consolidada"], "e NÃO é tratado como consolidado")
    igual(n["valor_pis"], 17.61, "PIS preservado")
    igual(n["valor_cofins"], 81.27, "COFINS preservado")
    igual(c["ret_total"], 125.97, "o classificador concorda")
    ok(not n["alerta_csrf"], "4,65% pela soma: sem alerta")

    secao("3 · Os outros formatos continuam como eram")
    n, _ = nota(5, 1000.00, ret_csll=46.50)
    igual(n["valor_retido"], 46.50, "só o campo, em 4,65%: total 46,50")
    ok(n["retencao_rateada"], "e dividido pela lei (IN RFB 459/2004)")
    igual((n["valor_pis"], n["valor_cofins"], n["valor_csll"]), (6.50, 30.00, 10.00),
          "PIS 6,50 · COFINS 30,00 · CSLL 10,00")

    n, _ = nota(6, 1000.00, tp_ret_piscofins="3", ret_pis=6.50, ret_cofins=0.0,
                ret_csll=10.00)
    igual(n["valor_retido"], 16.50,
          "só PIS destacado + CSLL: continua somando (a comparação não separa este caso)")

    n, _ = nota(7, 1000.00)
    igual(n["valor_retido"], 0.0, "sem retenção nenhuma: zero")
    ok(not n["alerta_csrf"], "e sem alerta — nota sem retenção não é divergência")

    # ═══════════════════════════════════════════════════════════════════
    secao("4 · O ALERTA: uma nota com 5% dispara a regra dos 4,65%")
    n, c = nota(8, 1000.00, ret_csll=50.00)
    igual(n["valor_retido"], 50.00, "retenção de 5%: o valor lido fica como está")
    ok(n["alerta_csrf"], "ALERTA DISPARADO")
    igual(n["csrf_esperado"], 46.50, "com o esperado de 4,65%: 46,50")
    ok("4,65%" in n["alerta_csrf_msg"], "a mensagem cita os 4,65%")
    ok("5,00%" in n["alerta_csrf_msg"], "e a alíquota efetiva (5,00%)")
    ok("3,50" in n["alerta_csrf_msg"], "e a diferença (R$ 3,50)")
    ok(c["alerta_csrf"], "o classificador dispara o mesmo alerta")

    n5, _ = nota(9, 1000.00, tp_ret_piscofins="1", ret_pis=6.50, ret_cofins=30.00,
                 ret_csll=50.00)
    ok(n5["alerta_csrf"], "5% também quando vem consolidado com PIS/COFINS destacados")
    igual(n5["valor_retido"], 50.00, "e sem duplicar (50,00, não 86,50)")

    n_ok, _ = nota(10, 1000.00, ret_csll=46.50)
    r = core.resumo([n, n5, n_ok])
    igual(r["qtd_alerta_csrf"], 2, "o resumo da tabela conta as 2 pendências")

    secao("5 · Arredondamento de centavo não é divergência; 3 centavos é")
    # Caso real: serviço 429,73 → 2,79 + 12,89 + 4,29 = 19,97 contra 19,98.
    n, _ = nota(11, 429.73, tp_ret_piscofins="1", ret_pis=2.79, ret_cofins=12.89,
                ret_csll=4.29)
    ok(not n["alerta_csrf"], "1 centavo de arredondamento dos três tributos: sem alerta")
    a = retencao.alerta_csrf(19.95, 429.73)
    ok(a["alerta"], "3 centavos abaixo: alerta (19,95 contra 19,98)")
    ok(not retencao.alerta_csrf(0, 1000)["alerta"], "retenção zero nunca alerta")
    ok(not retencao.alerta_csrf(46.50, 0)["alerta"], "serviço zero não divide por zero")

    # ═══════════════════════════════════════════════════════════════════
    secao("6 · Uma regra só: tela, classificador e DANFSe usam retencao.compor")
    back = RAIZ / "nfse" / "backend"
    for arq in ("core.py", "classificador.py", "pdflocal.py"):
        fonte = (back / arq).read_text("utf-8")
        ok("compor(" in fonte, "%s chama retencao.compor" % arq)
    ok("(v_pis + v_cofins if tp_ret_pc" not in (back / "pdflocal.py").read_text("utf-8"),
       "a soma antiga do DANFSe saiu")
    ok("valor_retido = pis_ret + cofins_ret + v_ret_csll" not in
       (back / "core.py").read_text("utf-8"), "a soma antiga do core saiu")

    tela = (RAIZ / "nfse" / "frontend" / "index.html").read_text("utf-8")
    ok("n.alerta_csrf" in tela, "a tabela desenha o selo de alerta")
    ok("qtd_alerta_csrf" in tela, "e os totais mostram a contagem de pendências")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
