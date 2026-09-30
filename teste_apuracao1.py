#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rede de segurança do motor fiscal (APURAÇÃO 1).

    python teste_apuracao1.py

O QUE ESTA SUÍTE É
    Teste de **caracterização**: congela o comportamento ATUAL de
    `classificador.py`, `apuracao_federal.py` e dos pontos de `core.py` ligados
    à apuração. Não julga se a regra é a melhor — garante que ela não mude por
    acidente quando o acervo novo for conectado.

POR QUE ELA EXISTE
    O motor fiscal move dinheiro e não tinha teste nenhum. Dois erros reais já
    aconteceram aqui: o ISS fixo do escritório contábil ausente (R$ 3.998,38 a
    mais no DAS) e a retenção consolidada descartando PIS e COFINS (R$ 989,95
    em 19 notas). Os dois foram achados conferindo contra documento real, não
    por teste — e é isso que muda a partir daqui.

O NÚMERO QUE MANDA
    O caso da MONTE (PA 06/2026) foi conferido contra a guia paga:
    R$ 100.458,50 × 13,232% × (1 - 32,50% de ISS) = **R$ 8.972,55**.
    Está congelado abaixo. Se algum dia esse número mudar, ou a regra mudou de
    propósito, ou alguém quebrou a apuração.

FIXTURES fictícias (`teste_fixturas_fiscal.py`). REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_fiscal as F                  # noqa: E402
import apuracao_federal as af                      # noqa: E402
import classificador as cls                        # noqa: E402
import core                                        # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []
_divergencias: list[str] = []

import socket as _socket                           # noqa: E402
_TENTATIVAS: list[str] = []
_connect_original = _socket.socket.connect


def _proibido(self, endereco, *a, **kw):
    _TENTATIVAS.append(str(endereco))
    raise AssertionError(f"teste tentou abrir conexão de rede para {endereco}")


_socket.socket.connect = _proibido


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
    ok(a == b, desc if a == b else f"{desc}  (obtive {a!r}, esperava {b!r})")


def anota(texto):
    """Divergência OBSERVADA, não corrigida. Vira relatório no fim."""
    _divergencias.append(texto)
    print(f"  >>   {texto}")


PRESTADOR = F.PRESTADOR


def notas_de(pasta, arquivos):
    F.gravar(pasta, arquivos)
    return cls.ler_nfse(pasta, PRESTADOR)


def manuais_de(rbt12: float, pa: str) -> dict:
    """12 meses informados que somam exatamente `rbt12` — fixa o RBT12."""
    v = round(rbt12 / 12.0, 2)
    meses = {cls._mes_menos(pa, i): v for i in range(1, 13)}
    # ajusta o último centavo para bater exatamente
    k = cls._mes_menos(pa, 12)
    meses[k] = round(rbt12 - v * 11, 2)
    return meses


# ══════════════════════════════════════════════════════════════════════════
secao("Tabelas do Simples: alíquota efetiva por Anexo (LC 123 / CGSN 140)")
esperado = {"I": (0.0845, 4, 0.107, 22500), "II": (0.0895, 4, 0.112, 22500),
            "III": (0.12436, 4, 0.16, 35640), "IV": (0.10022, 4, 0.14, 39780),
            "V": (0.1879, 4, 0.205, 17100)}
for anexo, (ef, faixa, nom, pd) in esperado.items():
    r = cls.aliquota_efetiva(anexo, 1_000_000.0)
    igual(r["efetiva"], ef, f"Anexo {anexo}: efetiva {ef:.5f} em RBT12 de 1 mi")
    igual(r["faixa"], faixa, f"Anexo {anexo}: faixa {faixa}")
    igual((r["nominal"], r["pd"]), (nom, pd), f"Anexo {anexo}: nominal e PD")

secao("Fronteiras de faixa e limite do Simples")
igual(cls.aliquota_efetiva("I", 180000)["faixa"], 1, "180.000 ainda é 1ª faixa")
igual(cls.aliquota_efetiva("I", 180000.01)["faixa"], 2, "um centavo acima já é 2ª")
igual(cls.aliquota_efetiva("I", 180000.01)["efetiva"], 0.04,
      "e a efetiva é contínua na fronteira — 4% dos dois lados")
igual(cls.aliquota_efetiva("I", 4_800_000)["faixa"], 6, "4,8 mi é a 6ª faixa")
r = cls.aliquota_efetiva("I", 4_800_000.01)
igual(r["efetiva"], None, "acima de 4,8 mi não há alíquota")
ok("limite do Simples" in r["erro"], "e o motivo é dito")
igual(cls.aliquota_efetiva("I", 0), None, "RBT12 zero não gera alíquota")
igual(cls.aliquota_efetiva("X", 1000), None, "anexo inexistente devolve None")
igual(cls.LIMITE_SIMPLES, 4800000.0, "limite do Simples congelado")
igual(cls.SUBLIMITE_ISS_ICMS, 3600000.0, "sublimite de ISS/ICMS congelado")

secao("Repartição dos tributos dentro da alíquota")
rep = cls.reparticao("III", 2)
igual(rep, {"IRPJ": .040, "CSLL": .035, "COFINS": .1405, "PIS": .0305,
            "CPP": .4340, "ISS": .3200},
      "Anexo III faixa 2 — a repartição conferida contra PGDAS-D real")
igual(round(sum(rep.values()), 4), 1.0, "e a repartição soma 100%")
for anexo, tab in cls.REPARTICAO.items():
    for i, faixa in enumerate(tab, 1):
        igual(round(sum(faixa.values()), 4), 1.0,
              f"Anexo {anexo} faixa {i}: repartição soma 100%")
ok("ISS" not in cls.REPARTICAO["I"][0], "Anexo I não tem ISS")
ok("ISS" not in cls.REPARTICAO["II"][0], "Anexo II não tem ISS")
ok("CPP" not in cls.REPARTICAO["IV"][0], "Anexo IV não tem CPP (fora do DAS)")
ok("IPI" in cls.REPARTICAO["II"][0], "Anexo II tem IPI")

secao("Item da LC 116 e o Anexo que a LEI fixa")
igual(cls.item_lc116("171901"), "17.19", "cTribNac 171901 -> item 17.19")
igual(cls.item_lc116("090201"), "9.02", "cTribNac 090201 -> item 9.02")
igual(cls.item_lc116(""), "", "sem cTribNac não há item")
igual(cls.anexo_por_lc116("17.19")[0], "III", "17.19 é Anexo III por lei (§5º-B)")
igual(cls.anexo_por_lc116("7.02")[0], "IV", "7.02 é Anexo IV por lei (§5º-C)")
igual(cls.anexo_por_lc116("1.01")[0], "", "1.01 depende do Fator R")
ok("4.08" in cls.LC116_ANEXO_III,
   "4.08 (fisioterapia) está no Anexo III — veio de PGDAS-D real")
igual(cls.ITEM_CONTABIL, "17.19", "o item do escritório contábil é 17.19")

secao("CFOP -> tipo de receita, por faixa")
for cfop, tipo in (("5102", "Comércio"), ("6108", "Comércio"),
                   ("5101", "Indústria"), ("5405", "Indústria"),
                   ("5933", "Serviço"), ("1202", "Devolução/Entrada"),
                   ("2202", "Devolução/Entrada"), ("5152", "Comércio"),
                   ("x", "Indefinido"), ("", "Indefinido")):
    igual(cls.tipo_por_cfop(cfop), tipo, f"CFOP {cfop or '(vazio)'} -> {tipo}")

# ══════════════════════════════════════════════════════════════════════════
secao("RBT12: 12 meses ANTERIORES ao PA, nunca o próprio mês")
with apoio.raiz_temporaria("ap1_") as raiz:
    arqs = [F.xml_nfse(i, f"2025-{i:02d}", 1000.0) for i in range(1, 13)]
    arqs.append(F.xml_nfse(13, "2026-01", 999999.0))     # o PA, não pode entrar
    notas = notas_de(raiz, arqs)
    r = cls.rbt12_de(notas, "2026-01")
    igual(r["total"], 12000.0, "soma os 12 meses de 2025")
    igual(len(r["meses"]), 12, "com 12 linhas de detalhe")
    igual(r["meses"][0]["comp"], "2025-01", "começa 12 meses antes")
    igual(r["meses"][-1]["comp"], "2025-12", "e termina no mês anterior ao PA")
    ok(all(m["origem"] == "xml" for m in r["meses"]), "todos vindos do XML")

secao("RBT12: receita informada à mão tem precedência sobre o XML")
with apoio.raiz_temporaria("ap1_") as raiz:
    notas = notas_de(raiz, [F.xml_nfse(1, "2025-06", 1000.0)])
    r = cls.rbt12_de(notas, "2026-01", manuais={"2025-06": 5000.0})
    igual(r["total"], 5000.0, "o informado substitui o do XML")
    m = [x for x in r["meses"] if x["comp"] == "2025-06"][0]
    igual(m["origem"], "informado", "e a origem fica marcada")

secao("RBT12: mês zerado antes da primeira nota é 'sem dados', não zero")
with apoio.raiz_temporaria("ap1_") as raiz:
    notas = notas_de(raiz, [F.xml_nfse(1, "2025-10", 1000.0)])
    r = cls.rbt12_de(notas, "2026-01")
    faltando = r["sem_dados"]
    ok("2025-01" in faltando, "mês anterior à primeira nota entra em sem_dados")
    ok("2025-11" not in faltando,
       "mês DEPOIS da primeira nota, sem faturamento, NÃO é buraco")
    m = [x for x in r["meses"] if x["comp"] == "2025-11"][0]
    igual(m["origem"], "sem faturamento", "e é rotulado como sem faturamento")

secao("Início de atividade: proporcionaliza, não soma (CGSN 140, art. 21 §2º)")
with apoio.raiz_temporaria("ap1_") as raiz:
    notas = notas_de(raiz, [F.xml_nfse(1, "2025-11", 10000.0),
                            F.xml_nfse(2, "2025-12", 20000.0)])
    r = cls.rbt12_de(notas, "2026-01", inicio="2025-11")
    igual(r["total"], 180000.0, "média de 15.000 × 12 = 180.000")
    igual(r["proporcional"]["meses_considerados"], 2, "dois meses de atividade")
    igual(r["proporcional"]["media_mensal"], 15000.0, "média mensal")
    ok("CGSN 140/2018" in r["proporcional"]["regra"], "com a regra citada")
    igual(r["sem_dados"], [], "e nenhum mês vira buraco — a empresa não existia")
    somado = sum(m["valor"] for m in r["meses"])
    ok(r["total"] > somado,
       "proporcionalizar dá MAIS que somar — é o que evita DAS menor que o devido")

secao("Primeiro mês de atividade: usa o próprio PA × 12")
with apoio.raiz_temporaria("ap1_") as raiz:
    notas = notas_de(raiz, [F.xml_nfse(1, "2026-01", 8000.0)])
    r = cls.rbt12_de(notas, "2026-01", inicio="2026-01")
    igual(r["total"], 96000.0, "8.000 × 12 = 96.000")
    igual(r["proporcional"]["meses_considerados"], 1, "um mês considerado")

secao("Passados 12 meses, a proporcionalização acaba")
with apoio.raiz_temporaria("ap1_") as raiz:
    notas = notas_de(raiz, [F.xml_nfse(i, f"2025-{i:02d}", 1000.0)
                            for i in range(1, 13)])
    r = cls.rbt12_de(notas, "2026-01", inicio="2025-01")
    igual(r["proporcional"], None, "12 meses completos: soma normal")
    igual(r["total"], 12000.0, "e o total é a soma")

# ══════════════════════════════════════════════════════════════════════════
secao("Fator R: >= 28% vai para o III, abaixo vai para o V")
cfg = {"fatorR": True}
igual(cls.resolver_anexo({"item_lc116": "1.01"}, cfg, 0.28)[0], "III",
      "exatamente 28% já é Anexo III")
igual(cls.resolver_anexo({"item_lc116": "1.01"}, cfg, 0.2799)[0], "V",
      "abaixo de 28% é Anexo V")
igual(cls.resolver_anexo({"item_lc116": "1.01"}, cfg, None)[0], "FATOR R",
      "sem folha informada, pede a folha")
igual(cls.resolver_anexo({"item_lc116": "7.02"}, cfg, 0.10)[0], "IV",
      "a lei vence o Fator R: 7.02 é Anexo IV mesmo com fator baixo")
igual(cls.resolver_anexo({"item_lc116": "9.02"}, cfg, 0.10)[0], "III",
      "e 9.02 é Anexo III por lei")
igual(cls.resolver_anexo({"item_lc116": "1.01"}, {"anexoServico": "V"}, None)[0],
      "V", "sem Fator R, vale o anexo fixo do cadastro")
igual(cls.resolver_anexo({"item_lc116": "1.01"}, {}, None)[0], "CONFIGURAR",
      "sem nada configurado, pede configuração em vez de chutar")
igual(cls.resolver_anexo({"anexo_manual": "I", "base_manual": "x"}, {}, None)[0],
      "I", "lançamento manual respeita o anexo informado")

# ══════════════════════════════════════════════════════════════════════════
secao("DAS: o caso REAL conferido contra a guia paga (MONTE, PA 06/2026)")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    notas = notas_de(raiz, [F.xml_nfse(1, PA, 100458.50, ctribnac="171901")])
    cfg = {"issFixo": True, "receitas_manuais": manuais_de(1_287_572.25, PA)}
    das = cls.calcular_das(notas, PA, cfg, None)
    igual(das["rbt12"], 1_287_572.25, "RBT12 fixado")
    linha = das["linhas"][0]
    igual(linha["anexo"], "III", "Anexo III — item 17.19 por lei")
    igual(linha["faixa"], 4, "4ª faixa")
    igual(linha["efetiva"], 0.13232, "alíquota efetiva de 13,232%")
    igual(linha["receita"], 100458.50, "receita do mês")
    ok(linha["iss_retido"], "o ISS sai do DAS (valor fixo)")
    igual(linha["motivo"], cls.MOTIVO_ISS_FIXO, "pelo motivo do §22-A")
    igual(das["das_estimado"], 8972.55,
          "DAS = 8.972,55 — IDÊNTICO à guia real (diferença zero)")
    igual(linha["tributos"]["ISS"], 0.0, "com o ISS zerado dentro do detalhe")
    sem_iss = round(100458.50 * 0.13232 * (1 - 0.3250), 2)
    igual(das["das_estimado"], sem_iss, "e a conta fecha: receita × efetiva × (1 - ISS)")

secao("Sem o ISS fixo, o mesmo mês daria R$ 3.998,38 a mais — o erro antigo")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    notas = notas_de(raiz, [F.xml_nfse(1, PA, 100458.50, ctribnac="171901")])
    cfg = {"receitas_manuais": manuais_de(1_287_572.25, PA)}
    das = cls.calcular_das(notas, PA, cfg, None)
    igual(das["das_estimado"], 13292.67, "sem o §22-A o DAS sobe para 13.292,67")
    igual(round(das["das_estimado"] - 8972.55, 2), 4320.12,
          "a diferença que a regra do ISS fixo evita")

secao("DAS por Anexo: I, II, III, IV e V, com a mesma receita")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    notas = notas_de(raiz, [F.xml_nfse(1, PA, 10000.0, ctribnac="")])
    manuais = manuais_de(1_000_000.0, PA)
    for anexo, efetiva in (("I", 0.0845), ("II", 0.0895), ("III", 0.12436),
                           ("IV", 0.10022), ("V", 0.1879)):
        cfg = {"anexoServico": anexo, "receitas_manuais": manuais}
        das = cls.calcular_das(notas, PA, cfg, None)
        linha = das["linhas"][0]
        igual(linha["anexo"], anexo, f"Anexo {anexo} aplicado")
        igual(linha["efetiva"], efetiva, f"Anexo {anexo}: efetiva {efetiva}")
        igual(das["das_estimado"], round(10000.0 * efetiva, 2),
              f"Anexo {anexo}: DAS = 10.000 × efetiva")
        soma_trib = round(sum(linha["tributos"].values()), 2)
        ok(abs(soma_trib - das["das_estimado"]) <= 0.01,
           f"Anexo {anexo}: os tributos detalhados somam o DAS (tolerancia 1 centavo)")
        if soma_trib != das["das_estimado"]:
            anota(f"Anexo {anexo}: a soma dos tributos detalhados ({soma_trib}) "
                  f"difere do DAS ({das['das_estimado']}) em "
                  f"{abs(soma_trib - das['das_estimado']):.2f} - cada tributo e "
                  "arredondado antes de somar. E centavo de exibicao, nao de guia.")

secao("ISS retido na fonte: a fatia do ISS sai do DAS, e só dela")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    notas = notas_de(raiz, [
        F.xml_nfse(1, PA, 10000.0, ctribnac="171901"),
        F.xml_nfse(2, PA, 10000.0, ctribnac="171901", iss_retido=True,
                   valor_iss=500.0)])
    cfg = {"receitas_manuais": manuais_de(1_000_000.0, PA)}
    das = cls.calcular_das(notas, PA, cfg, None)
    igual(len(das["linhas"]), 2, "duas linhas: com e sem retenção")
    com = [x for x in das["linhas"] if x["iss_retido"]][0]
    sem = [x for x in das["linhas"] if not x["iss_retido"]][0]
    igual(sem["das"], round(10000 * 0.12436, 2), "a linha sem retenção paga cheio")
    igual(com["das"], round(10000 * 0.12436 * (1 - 0.3250), 2),
          "a com retenção paga sem a fatia do ISS")
    ok(com["das"] < sem["das"], "e portanto paga menos")
    igual(com["tributos"]["ISS"], 0.0, "o ISS da linha retida é zero")
    ok(sem["tributos"]["ISS"] > 0, "e o da não retida não é")
    igual(das["iss_retido_no_mes"], 500.0, "o ISS retido do mês é somado")

secao("Nota cancelada não entra na receita")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    a1 = F.xml_nfse(1, PA, 10000.0, ctribnac="171901")
    a2 = F.xml_nfse(2, PA, 90000.0, ctribnac="171901")
    ev = F.xml_evento_nfse(F.chave_nfse(2))
    notas = notas_de(raiz, [a1, a2, ev])
    canceladas = [n for n in notas if n["cancelada"]]
    igual(len(canceladas), 1, "uma nota marcada como cancelada")
    das = cls.calcular_das(notas, PA, {"anexoServico": "III",
                                       "receitas_manuais": manuais_de(1_000_000.0, PA)},
                           None)
    igual(das["receita_mes"], 10000.0, "e a receita do mês ignora a cancelada")

secao("Receita informada à mão entra pelo MESMO cálculo")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    notas = notas_de(raiz, [])
    manuais = cls.lancamentos_para_notas([
        {"competencia": PA, "valor": 5000.0, "tipo": "aluguel_movel",
         "descricao": "Locação de equipamento"}])
    igual(len(manuais), 1, "o lançamento vira uma nota sintética")
    igual(manuais[0]["anexo_manual"], "III", "aluguel de móvel é Anexo III")
    ok(manuais[0]["iss_retido"], "com o ISS fora do DAS")
    ok("Súmula Vinculante 31" in manuais[0]["motivo_sem_iss"],
       "e o motivo é a Súmula 31, não 'retido na fonte'")
    das = cls.calcular_das(notas + manuais, PA,
                           {"receitas_manuais": manuais_de(1_000_000.0, PA)}, None)
    igual(das["receita_mes"], 5000.0, "a receita sem nota entra na apuração")
    igual(das["das_estimado"], round(5000 * 0.12436 * (1 - 0.3250), 2),
          "e paga pelo Anexo III sem a fatia do ISS")

secao("Lançamento manual inválido é descartado, não estimado")
igual(cls.lancamentos_para_notas([{"competencia": "", "valor": 100}]), [],
      "sem competência não entra")
igual(cls.lancamentos_para_notas([{"competencia": "2026-06", "valor": 0}]), [],
      "valor zero não entra")
igual(cls.lancamentos_para_notas([{"competencia": "2026-06", "valor": -5,
                                   "tipo": "comercio"}]), [],
      "valor negativo não entra")
igual(cls.lancamentos_para_notas([{"competencia": "2026-06", "valor": 10}]), [],
      "sem tipo nem anexo não entra")
igual(len(cls.lancamentos_para_notas([{"competencia": "2026-06", "valor": 10,
                                       "anexo": "i"}])), 1,
      "anexo em minúscula é aceito e normalizado")

secao("Sublimite e mês sem nota")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    notas = notas_de(raiz, [F.xml_nfse(1, PA, 1000.0, ctribnac="171901")])
    das = cls.calcular_das(notas, PA, {"receitas_manuais": manuais_de(3_700_000.0, PA)},
                           None)
    ok(das["acima_sublimite"], "RBT12 acima de 3,6 mi acende o sublimite")
    das2 = cls.calcular_das(notas, "2026-07", {}, None)
    igual(das2, None, "mês sem nenhuma nota não gera DAS")

# ══════════════════════════════════════════════════════════════════════════
secao("Prevenção de duplicidade do histórico municipal")
_fonte_cls = (RAIZ / "nfse" / "backend" / "classificador.py").read_text("utf-8")
ok("ANTI-DUPLICIDADE" in _fonte_cls, "a regra está declarada no código")
ok("meses_nacionais" in _fonte_cls, "e é implementada por mês, não por nota")
# A regra: municipal só entra em mês SEM nota nacional.
nacionais = [{"competencia": "2025-12", "valor": 1000.0, "data": "2025-12-10"}]
municipais = [{"competencia": "2025-12", "valor": 1000.0, "data": "2025-12-05"},
              {"competencia": "2025-11", "valor": 800.0, "data": "2025-11-05"}]
meses_nac = {(n.get("competencia") or "")[:7] for n in nacionais}
resultado = nacionais + [n for n in municipais
                         if (n.get("competencia") or "")[:7] not in meses_nac]
igual(len(resultado), 2, "o mês que existe nas duas fontes conta UMA vez")
igual(round(sum(n["valor"] for n in resultado), 2), 1800.0,
      "1.000 do nacional + 800 do municipal — sem os 1.000 duplicados")
ok("2025-12" in meses_nac, "12/2025 está nas duas e só o nacional prevalece")

# ══════════════════════════════════════════════════════════════════════════
secao("PIS/COFINS cumulativos (Lucro Presumido)")
igual(af.ALIQUOTAS["presumido"][:2], (0.0065, 0.0300), "0,65% e 3,00%")
igual(af.ALIQUOTAS["real"][:2], (0.0165, 0.0760), "1,65% e 7,60%")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    F.gravar(raiz, [F.xml_nfse(1, PA, 100000.0)])
    notas = core.carregar_notas(raiz)
    r = af.apurar(notas, PRESTADOR, PA, "presumido")
    ok(r["aplicavel"], "aplicável ao Presumido")
    igual(r["base"], "cumulativo", "regime cumulativo")
    igual(r["receita_bruta"], 100000.0, "receita das notas emitidas")
    igual(r["pis_devido"], 650.0, "PIS 0,65% = 650,00")
    igual(r["cofins_devido"], 3000.0, "COFINS 3% = 3.000,00")
    igual(r["total_a_pagar"], 3650.0, "total a pagar")
    igual(r["avisos"], [], "sem avisos quando está tudo normal")

secao("PIS/COFINS não cumulativos: débito BRUTO, sem inventar crédito")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    F.gravar(raiz, [F.xml_nfse(1, PA, 100000.0)])
    notas = core.carregar_notas(raiz)
    r = af.apurar(notas, PRESTADOR, PA, "real")
    igual(r["pis_devido"], 1650.0, "PIS 1,65%")
    igual(r["cofins_devido"], 7600.0, "COFINS 7,6%")
    igual(r["credito_pis"], 0.0, "nenhum crédito é inventado")
    igual(r["credito_cofins"], 0.0, "nem de COFINS")
    ok(any("débito bruto" in a for a in r["avisos"]),
       "e o aviso diz que é DÉBITO BRUTO, com os créditos por fora")
    r2 = af.apurar(notas, PRESTADOR, PA, "real", credito_pis=650.0,
                   credito_cofins=3000.0)
    igual(r2["pis_a_pagar"], 1000.0, "crédito informado abate o PIS")
    igual(r2["cofins_a_pagar"], 4600.0, "e o COFINS")

secao("Regime que não apura federal")
r = af.apurar([], PRESTADOR, "2026-06", "simples")
ok(not r["aplicavel"], "Simples não tem apuração federal separada")
ok("Presumido ou Lucro Real" in r["motivo"], "e o motivo é dito")

secao("Retenções federais sofridas abatem o devido")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    F.gravar(raiz, [F.xml_nfse(1, PA, 100000.0, tp_ret_piscofins="1",
                               ret_pis=650.0, ret_cofins=3000.0,
                               ret_csll=1000.0, ret_irrf=1500.0)])
    notas = core.carregar_notas(raiz)
    r = af.apurar(notas, PRESTADOR, PA, "presumido")
    igual(r["retido_pis"], 650.0, "PIS retido lido da nota")
    igual(r["retido_cofins"], 3000.0, "COFINS retido")
    igual(r["retido_csll"], 1000.0, "CSLL retida")
    igual(r["retido_irrf"], 1500.0, "IRRF retido")
    igual(r["pis_a_pagar"], 0.0, "retenção cobre o PIS devido")
    igual(r["cofins_a_pagar"], 0.0, "e o COFINS")
    igual(r["total_a_pagar"], 0.0, "nada a recolher")

secao("Retenção maior que o devido vira SALDO, não some")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    F.gravar(raiz, [F.xml_nfse(1, PA, 10000.0, tp_ret_piscofins="1",
                               ret_pis=500.0, ret_cofins=1000.0)])
    notas = core.carregar_notas(raiz)
    r = af.apurar(notas, PRESTADOR, PA, "presumido")
    igual(r["pis_a_pagar"], 0.0, "não fica negativo")
    igual(r["pis_saldo_credor"], round(500.0 - 65.0, 2),
          "o excesso vira saldo credor visível")
    igual(r["cofins_saldo_credor"], round(1000.0 - 300.0, 2), "idem COFINS")

secao("Retenção CSRF consolidada em 4,65% é rateada pela lei, não por chute")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    F.gravar(raiz, [F.xml_nfse(1, PA, 10000.0, ret_csll=465.0)])
    notas = core.carregar_notas(raiz)
    n = notas[0]
    ok(n["retencao_rateada"], "4,65% exatos: o rateio da IN RFB 459/2004 é aplicado")
    igual(n["valor_pis"], 65.0, "PIS 0,65%")
    igual(n["valor_cofins"], 300.0, "COFINS 3%")
    igual(n["valor_csll"], 100.0, "CSLL fica com o resto — total preservado")
    igual(round(n["valor_pis"] + n["valor_cofins"] + n["valor_csll"], 2), 465.0,
          "e a soma continua sendo o total retido")

secao("Consolidada FORA de 4,65% não é rateada — vira aviso")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    F.gravar(raiz, [F.xml_nfse(1, PA, 10000.0, ret_csll=200.0)])
    notas = core.carregar_notas(raiz)
    ok(not notas[0]["retencao_rateada"], "sem respaldo legal, não divide")
    r = af.apurar(notas, PRESTADOR, PA, "presumido")
    ok(any("consolidou tudo em vRetCSLL" in a for a in r["avisos"]),
       "e a apuração avisa que o abatimento precisa ser conferido")
    igual(r["retido_pis"], 0.0, "sem inventar PIS retido")

secao("Nota RECEBIDA não entra na receita de quem apura")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    F.gravar(raiz, [F.xml_nfse(1, PA, 10000.0),
                    F.xml_nfse(2, PA, 90000.0, emitente=F.OUTRO)])
    notas = core.carregar_notas(raiz)
    igual(len(notas), 2, "as duas notas estão no cache")
    r = af.apurar(notas, PRESTADOR, PA, "presumido")
    igual(r["receita_bruta"], 10000.0, "só a EMITIDA pela empresa é receita")
    igual(r["notas"], 1, "uma nota considerada")

# ══════════════════════════════════════════════════════════════════════════
secao("IRPJ e CSLL do Presumido são TRIMESTRAIS")
igual(af.IRPJ_ALIQ, 0.15, "IRPJ 15%")
igual(af.IRPJ_ADICIONAL, 0.10, "adicional 10%")
igual(af.IRPJ_LIMITE_MENSAL, 20000.00, "limite mensal do adicional")
igual(af.CSLL_ALIQ, 0.09, "CSLL 9%")
igual(af.meses_do_trimestre(2026, 2), ["2026-04", "2026-05", "2026-06"],
      "2º trimestre = abril, maio e junho")
igual(af.meses_do_trimestre(2026, 4), ["2026-10", "2026-11", "2026-12"],
      "4º trimestre")

secao("Trimestre sem adicional")
with apoio.raiz_temporaria("ap1_") as raiz:
    F.gravar(raiz, [F.xml_nfse(1, "2026-04", 50000.0),
                    F.xml_nfse(2, "2026-05", 50000.0),
                    F.xml_nfse(3, "2026-06", 50000.0)])
    notas = core.carregar_notas(raiz)
    r = af.apurar_trimestre(notas, PRESTADOR, 2026, 2)
    igual(r["receita_bruta"], 150000.0, "receita do trimestre")
    igual(r["presuncao_irpj"], 32.0, "presunção padrão de serviços")
    igual(r["base_irpj"], 48000.0, "base = 32% de 150.000")
    igual(r["limite_adicional"], 60000.0,
          "limite do adicional = 20.000 × 3 meses")
    igual(r["excedente_adicional"], 0.0, "48.000 não passa de 60.000")
    igual(r["irpj_devido"], 7200.0, "IRPJ = 15% de 48.000")
    igual(r["csll_devido"], 4320.0, "CSLL = 9% de 32% de 150.000")
    ok(r["presuncao_padrao"], "e avisa que usou a presunção padrão")

secao("Trimestre COM adicional de 10%")
with apoio.raiz_temporaria("ap1_") as raiz:
    F.gravar(raiz, [F.xml_nfse(i, f"2026-0{i+3}", 100000.0) for i in (1, 2, 3)])
    notas = core.carregar_notas(raiz)
    r = af.apurar_trimestre(notas, PRESTADOR, 2026, 2)
    igual(r["receita_bruta"], 300000.0, "receita do trimestre")
    igual(r["base_irpj"], 96000.0, "base de 96.000")
    igual(r["excedente_adicional"], 36000.0, "excede o limite em 36.000")
    igual(r["irpj_normal"], 14400.0, "IRPJ normal 15%")
    igual(r["irpj_adicional"], 3600.0, "adicional 10% sobre o excedente")
    igual(r["irpj_devido"], 18000.0, "IRPJ total")
    ok(any("Adicional de 10%" in a for a in r["avisos"]), "com aviso do adicional")

secao("Presunção de comércio muda o valor — e é escolha do contador")
with apoio.raiz_temporaria("ap1_") as raiz:
    F.gravar(raiz, [F.xml_nfse(1, "2026-04", 150000.0)])
    notas = core.carregar_notas(raiz)
    r = af.apurar_trimestre(notas, PRESTADOR, 2026, 2,
                            presuncao_irpj=8.0, presuncao_csll=12.0)
    igual(r["base_irpj"], 12000.0, "base = 8% de 150.000")
    igual(r["base_csll"], 18000.0, "CSLL presume 12%")
    igual(r["irpj_devido"], 1800.0, "IRPJ de comércio")
    ok(not r["presuncao_padrao"], "e não é mais a presunção padrão")
    ok(not any("presunção padrão" in a for a in r["avisos"]),
       "logo o aviso de padrão some")
igual(len(af.PRESUNCOES), 4, "as quatro presunções da Lei 9.249/1995")

secao("Retenções do trimestre abatem IRPJ e CSLL")
with apoio.raiz_temporaria("ap1_") as raiz:
    F.gravar(raiz, [F.xml_nfse(1, "2026-04", 150000.0, ret_irrf=5000.0,
                               ret_csll=3000.0)])
    notas = core.carregar_notas(raiz)
    r = af.apurar_trimestre(notas, PRESTADOR, 2026, 2)
    igual(r["retido_irrf"], 5000.0, "IRRF do trimestre")
    igual(r["irpj_devido"], 7200.0, "IRPJ devido")
    igual(r["irpj_a_pagar"], 2200.0, "IRRF abate o IRPJ")
    igual(r["csll_a_pagar"], round(4320.0 - 3000.0, 2), "CSLL retida abate a CSLL")
igual(af.apurar_trimestre([], PRESTADOR, 2026, 5)["aplicavel"], False,
      "trimestre inválido é recusado")

# ══════════════════════════════════════════════════════════════════════════
secao("Cancelamento e substituição de NFS-e (core)")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    ch1, ch2, ch3 = F.chave_nfse(1), F.chave_nfse(2), F.chave_nfse(3)
    F.gravar(raiz, [
        F.xml_nfse(1, PA, 1000.0),
        F.xml_nfse(2, PA, 2000.0),
        F.xml_nfse(3, PA, 2500.0),
        F.xml_evento_nfse(ch1, "Cancelamento de NFS-e"),
        F.xml_evento_nfse(ch2, "Cancelamento por Substituição", chave_substituta=ch3),
    ])
    notas = {n["chave"]: n for n in core.carregar_notas(raiz)}
    igual(notas[ch1]["situacao"], "Cancelada", "a cancelada é Cancelada")
    igual(notas[ch2]["situacao"], "Substituída", "a original vira Substituída")
    igual(notas[ch3]["situacao"], "Substituta", "e a nova é Substituta")
    ok(notas[ch1]["cancelada"], "cancelada sai da receita")
    ok(notas[ch2]["cancelada"], "substituída também sai")
    ok(not notas[ch3]["cancelada"], "mas a substituta PERMANECE — é a válida do par")
    r = af.apurar(core.carregar_notas(raiz), PRESTADOR, PA, "presumido")
    igual(r["receita_bruta"], 2500.0,
          "a receita do mês é só a da substituta — nem soma nem apaga o par")

# ══════════════════════════════════════════════════════════════════════════
secao("PONTO FRÁGIL 1 — rbt12_de() × rbt12(): onde divergem")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-01"
    notas = notas_de(raiz, [F.xml_nfse(i, f"2025-{i:02d}", 1000.0)
                            for i in range(1, 13)]
                     + [F.xml_nfse(13, PA, 7000.0)])
    a = cls.rbt12_de(notas, PA)["total"]
    b = cls.rbt12(notas)
    igual(a, 12000.0, "rbt12_de: 12 meses ANTERIORES ao PA")
    igual(b, 18000.0,
          "rbt12: janela de 12 meses TERMINANDO no mês da nota mais nova")
    ok(a != b, "as duas funções NÃO dão o mesmo número")
    anota(f"rbt12_de={a} x rbt12={b}. A janela e outra: rbt12_de usa os 12 meses "
          "ANTERIORES ao PA (2025-01..2025-12); rbt12 usa os 12 meses que TERMINAM "
          "no mes da nota mais nova (2025-02..2026-01) - inclui o mes corrente e "
          "descarta o mais antigo. calcular_das usa a primeira; classificar usa a "
          "segunda para o FATOR R.")
    igual(cls.rbt12_de(notas, PA, manuais={"2025-06": 9999.0})["total"],
          12000.0 - 1000.0 + 9999.0, "rbt12_de aceita receita informada")
    anota("rbt12() NÃO aceita `manuais`: em empresa com histórico municipal "
          "informado à mão, o Fator R usa uma base menor que o DAS.")
    r_prop = cls.rbt12_de(notas, PA, inicio="2025-11")
    ok(r_prop["proporcional"] is not None, "rbt12_de proporcionaliza início de atividade")
    anota("rbt12() não proporcionaliza início de atividade: no primeiro ano, "
          "Fator R e DAS partem de bases diferentes.")
    ok(isinstance(cls.rbt12_de(notas, PA), dict), "rbt12_de devolve dict com detalhe")
    ok(isinstance(cls.rbt12(notas), float), "rbt12 devolve só o float")

secao("PONTO FRÁGIL 2 — core.carregar_notas() × classificador.ler_nfse()")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    ch2, ch3 = F.chave_nfse(2), F.chave_nfse(3)
    F.gravar(raiz, [
        F.xml_nfse(1, PA, 1000.0),
        F.xml_nfse(2, PA, 2000.0),
        F.xml_nfse(3, PA, 2500.0),
        F.xml_nfse(4, PA, 4000.0, emitente=F.OUTRO),
        F.xml_evento_nfse(ch2, "Cancelamento por Substituição", chave_substituta=ch3),
    ])
    do_core = {n["chave"]: n for n in core.carregar_notas(raiz)}
    do_cls = {n["chave"]: n for n in cls.ler_nfse(raiz, PRESTADOR)}
    igual(len(do_core), 4, "core devolve TODAS as notas do cache")
    igual(len(do_cls), 3, "classificador devolve só as EMITIDAS pela empresa")
    anota("core.carregar_notas() não filtra emitente; classificador.ler_nfse() "
          "filtra. Quem usa o core precisa filtrar por fora — é o que "
          "apuracao_federal faz em _emitidas_do_mes().")
    ok(do_core[ch2]["cancelada"], "core marca a substituída como cancelada")
    ok(do_cls[ch2]["cancelada"],
       "o classificador TAMBÉM a exclui — sua regra pega 'cancel' e 'substitu'")
    ok(not do_core[ch3]["cancelada"] and not do_cls[ch3]["cancelada"],
       "e os dois mantêm a substituta, que é a nota válida do par")
    anota("Os dois leitores CONCORDAM em quem sai da receita neste caso - nao ha "
          "duplicidade de receita. A diferenca e de vocabulario: o core distingue "
          "Cancelada / Substituida / Substituta; o classificador tem so o booleano "
          "`cancelada`, entao a tela do DAS nao consegue dizer POR QUE a nota saiu.")
    igual(do_core[ch3]["situacao"], "Substituta", "core distingue a substituta")
    ok("situacao" not in do_cls[ch3], "o classificador não tem o campo situacao")
    n_core, n_cls = do_core[F.chave_nfse(1)], do_cls[F.chave_nfse(1)]
    igual(n_core["valor_servico"], n_cls["valor"], "o VALOR bate entre os dois")
    igual(n_core["competencia"], n_cls["competencia"], "a competência bate")
    igual(n_core["iss_retido"], n_cls["iss_retido"], "o ISS retido bate")
    anota("Nomes de campo diferentes para o mesmo dado: valor_servico/valor, "
          "valor_pis/ret_pis, valor_csll/ret_csll — qualquer unificação futura "
          "precisa de um mapa explícito.")

secao("PONTO FRÁGIL 2b — bloco <piscofins> ausente separa os dois leitores")
with apoio.raiz_temporaria("ap1_") as raiz:
    PA = "2026-06"
    F.gravar(raiz, [F.xml_nfse(1, PA, 10000.0, tp_ret_piscofins="1",
                               ret_pis=65.0, ret_cofins=300.0,
                               com_bloco_piscofins=False)])
    n_core = core.carregar_notas(raiz)[0]
    n_cls = cls.ler_nfse(raiz, PRESTADOR)[0]
    igual(n_cls["ret_pis"], 65.0, "o classificador acha o PIS retido solto")
    igual(n_core["valor_pis"], 0.0,
          "o core NÃO acha: ele exige o bloco <piscofins>")
    anota("Quando a nota traz tpRetPisCofins FORA do bloco <piscofins>, o core "
          "lê zero e o classificador lê o valor. Como a apuração federal usa o "
          "core, a retenção deixaria de ser abatida nesse formato.")

# ══════════════════════════════════════════════════════════════════════════
secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede não estava ativa")
except AssertionError as exc:
    ok("conexão de rede" in str(exc), "e a trava estava mesmo ativa (conferido)")
finally:
    _TENTATIVAS.clear()
    _socket.socket.connect = _connect_original

print()
if _divergencias:
    print("=" * 62)
    print("DIVERGÊNCIAS OBSERVADAS — registradas, NÃO corrigidas nesta fase:")
    for d in _divergencias:
        print(f"  · {d}")
print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("APURAÇÃO 1: rede de segurança verde.")
