#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cálculo paralelo (APURAÇÃO 4).

    python teste_apuracao4.py

O QUE ESTA SUÍTE GARANTE
    Que o caminho novo — leitor → normalização → ponte — chega ao MESMO motor
    fiscal e produz os MESMOS números, centavo a centavo. E, sobretudo, que a
    ponte é só tradução de vocabulário: **nenhuma fórmula, alíquota, tabela ou
    decisão de anexo mora nela**.

    Se um cálculo aparecer em `ponte_motor.py`, existirão duas apurações — e a
    segunda vai divergir da primeira em silêncio. É esse o risco que a
    APURAÇÃO 1 existe para travar, e que esta suíte guarda aqui.

FIXTURES fictícias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import ast
import re
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_fiscal as FF                 # noqa: E402
import apuracao_federal as af                      # noqa: E402
import classificador as cls                        # noqa: E402
import core                                        # noqa: E402
from ingestao import normalizacao as nz            # noqa: E402
from ingestao import ponte_motor as pm             # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

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


EMP = FF.PRESTADOR
FONTE_PONTE = (RAIZ / "nfse" / "backend" / "ingestao" / "ponte_motor.py").read_text("utf-8")


def montar(base, arquivos):
    FF.gravar(Path(base) / EMP, arquivos)
    return Path(base) / EMP


def manuais_de(rbt12: float, pa: str) -> dict:
    v = round(rbt12 / 12.0, 2)
    meses = {cls._mes_menos(pa, i): v for i in range(1, 13)}
    meses[cls._mes_menos(pa, 12)] = round(rbt12 - v * 11, 2)
    return meses


def dois_caminhos(pasta):
    """`(notas_antigas, notas_novas)` para o classificador e para o federal."""
    n_cls = cls.ler_nfse(pasta, EMP)
    n_core = core.carregar_notas(pasta)
    novas_cls = pm.para_classificador(
        [nz.de_nota_classificador(n, EMP) for n in n_cls])
    novas_fed = pm.para_federal(
        [nz.de_nota_core(n, EMP) for n in n_core], identidade_empresa=EMP)
    return n_cls, novas_cls, n_core, novas_fed


# ══════════════════════════════════════════════════════════════════════════
secao("A ponte NÃO contém fórmula — é tradução de vocabulário")
arv = ast.parse(FONTE_PONTE)
chamadas = {getattr(n.func, "attr", "") or getattr(n.func, "id", "")
            for n in ast.walk(arv) if isinstance(n, ast.Call)}
for proibida in ("calcular_das", "aliquota_efetiva", "reparticao",
                 "resolver_anexo", "anexo_por_lc116", "apurar",
                 "apurar_trimestre", "rbt12", "rbt12_de"):
    ok(proibida not in chamadas, f"a ponte não chama `{proibida}`")
importados = {n.module for n in ast.walk(arv) if isinstance(n, ast.ImportFrom)}
importados |= {a.name for n in ast.walk(arv) if isinstance(n, ast.Import)
               for a in n.names}
for modulo in ("classificador", "apuracao_federal", "core"):
    ok(modulo not in importados, f"nem importa `{modulo}`")
palavras = set(re.findall(r"[A-Za-zÀ-ÿ_0-9]+", FONTE_PONTE))
for termo in ("TABELAS", "REPARTICAO", "aliquota", "ALIQUOTAS", "IRPJ_ALIQ",
              "CSLL_ALIQ", "LIMITE_SIMPLES", "monofasico"):
    ok(termo not in palavras, f"e não há `{termo}` no arquivo")
numeros = [n.value for n in ast.walk(arv)
           if isinstance(n, ast.Constant) and isinstance(n.value, float)]
igual(numeros, [0.0],
      "o único literal decimal da ponte é o 0.0 de 'ausente vira zero'")

secao("A ponte preserva o id_documento — a semente da rastreabilidade")
with apoio.raiz_temporaria("ap4_") as base:
    pasta = montar(base, [FF.xml_nfse(1, "2026-06", 1000.0, ctribnac="171901"),
                          FF.xml_nfse(2, "2026-06", 2000.0, ctribnac="171901")])
    ops = [nz.de_nota_classificador(n, EMP) for n in cls.ler_nfse(pasta, EMP)]
    novas = pm.para_classificador(ops)
    igual(len(novas), 2, "duas notas traduzidas")
    ok(all(n["id_documento"] for n in novas), "todas com id_documento")
    igual(sorted(n["id_documento"] for n in novas),
          sorted(o.id_documento for o in ops), "e são exatamente os das operações")
    ok(all(n["chave"] for n in novas), "com a chave do documento junto")
    ok(all(n["origem"] for n in novas), "e a origem de qual leitor veio")
    ids = pm.documentos_da_competencia(ops, "2026-06")
    igual(len(ids), 2, "documentos_da_competencia devolve os dois")
    igual(ids, sorted(o.id_documento for o in ops), "com os mesmos identificadores")

secao("NF-e não entra na apuração pela ponte")
op_nfe = nz.Operacao(id_documento="x", identidade_empresa=EMP, especie=nz.NFE,
                     origem=nz.OrigemDocumento())
igual(pm.para_classificador([op_nfe]), [],
      "operação de NF-e é descartada — mercadoria não vai para base de serviço")
igual(pm.para_federal([op_nfe]), [], "nem para a apuração federal")

# ══════════════════════════════════════════════════════════════════════════
secao("DAS: caminho antigo × novo, centavo a centavo")
with apoio.raiz_temporaria("ap4_") as base:
    PA = "2026-06"
    pasta = montar(base, [
        FF.xml_nfse(1, PA, 100458.50, ctribnac="171901"),
        FF.xml_nfse(2, PA, 15000.00, ctribnac="090201"),
        FF.xml_nfse(3, PA, 8000.00, ctribnac="070201", iss_retido=True,
                    valor_iss=400.0)])
    cfg = {"issFixo": False, "receitas_manuais": manuais_de(1_287_572.25, PA)}
    antigas, novas, _, _ = dois_caminhos(pasta)
    a = cls.calcular_das(antigas, PA, cfg, None)
    b = cls.calcular_das(novas, PA, cfg, None)
    igual(a["rbt12"], b["rbt12"], "RBT12 idêntico")
    igual(a["receita_mes"], b["receita_mes"], "receita do mês idêntica")
    igual(a["das_estimado"], b["das_estimado"], "DAS idêntico ao centavo")
    igual(a["iss_retido_no_mes"], b["iss_retido_no_mes"], "ISS retido idêntico")
    igual(a["tributos"], b["tributos"], "e todos os tributos do detalhe")
    igual(sorted(l["anexo"] for l in a["linhas"]),
          sorted(l["anexo"] for l in b["linhas"]), "os mesmos anexos")
    igual(len(a["linhas"]), len(b["linhas"]), "e o mesmo número de linhas")
    for la, lb in zip(sorted(a["linhas"], key=lambda x: (x["anexo"], x["receita"])),
                      sorted(b["linhas"], key=lambda x: (x["anexo"], x["receita"]))):
        igual((la["receita"], la["efetiva"], la["das"]),
              (lb["receita"], lb["efetiva"], lb["das"]),
              f"linha do Anexo {la['anexo']}: receita, efetiva e DAS iguais")

secao("DAS com ISS fixo: o caso da guia real sobrevive à travessia")
with apoio.raiz_temporaria("ap4_") as base:
    PA = "2026-06"
    pasta = montar(base, [FF.xml_nfse(1, PA, 100458.50, ctribnac="171901")])
    cfg = {"issFixo": True, "receitas_manuais": manuais_de(1_287_572.25, PA)}
    antigas, novas, _, _ = dois_caminhos(pasta)
    a = cls.calcular_das(antigas, PA, cfg, None)
    b = cls.calcular_das(novas, PA, cfg, None)
    igual(a["das_estimado"], 8972.55, "o caminho antigo dá 8.972,55")
    igual(b["das_estimado"], 8972.55, "o caminho novo dá 8.972,55")
    igual(a["das_estimado"], b["das_estimado"], "idênticos")

secao("Fator R: os dois caminhos escolhem o mesmo anexo")
with apoio.raiz_temporaria("ap4_") as base:
    PA = "2026-06"
    arquivos = [FF.xml_nfse(i, f"2025-{i:02d}", 10000.0, ctribnac="010101")
                for i in range(1, 13)]
    arquivos.append(FF.xml_nfse(13, PA, 10000.0, ctribnac="010101"))
    pasta = montar(base, arquivos)
    antigas, novas, _, _ = dois_caminhos(pasta)
    r12_a, r12_b = cls.rbt12(antigas), cls.rbt12(novas)
    igual(r12_a, r12_b, "o RBT12 do Fator R é o mesmo nos dois caminhos")
    # O limiar do Fator R é 28% do RBT12 — calculado, não chutado: a janela
    # do `rbt12()` não é a mesma do `rbt12_de()` (ver APURAÇÃO 1), e supor o
    # valor daria um teste que passa por acaso.
    limiar = r12_a * 0.28
    for folha, esperado in ((limiar * 0.8, "V"), (limiar * 1.2, "III")):
        f_a = round(folha / r12_a, 4)
        f_b = round(folha / r12_b, 4)
        igual(f_a, f_b, f"fator idêntico com folha {folha:.0f}")
        ok((f_a >= 0.28) == (esperado == "III"),
           f"folha {folha:.0f} sobre RBT12 {r12_a:.0f} dá fator {f_a} -> {esperado}")
        cfg = {"fatorR": True, "folha12m": folha,
               "receitas_manuais": manuais_de(1_000_000.0, PA)}
        a = cls.calcular_das(antigas, PA, cfg, f_a)
        b = cls.calcular_das(novas, PA, cfg, f_b)
        igual(a["linhas"][0]["anexo"], esperado, f"anexo {esperado} no antigo")
        igual(b["linhas"][0]["anexo"], esperado, f"anexo {esperado} no novo")
        igual(a["das_estimado"], b["das_estimado"], "e o DAS idêntico")

secao("Cancelada e substituída saem da base nos DOIS caminhos")
with apoio.raiz_temporaria("ap4_") as base:
    PA = "2026-06"
    ch1, ch2, ch3 = FF.chave_nfse(1), FF.chave_nfse(2), FF.chave_nfse(3)
    pasta = montar(base, [
        FF.xml_nfse(1, PA, 1000.0, ctribnac="171901"),
        FF.xml_nfse(2, PA, 2000.0, ctribnac="171901"),
        FF.xml_nfse(3, PA, 2500.0, ctribnac="171901"),
        FF.xml_nfse(4, PA, 700.0, ctribnac="171901"),
        FF.xml_evento_nfse(ch1, "Cancelamento de NFS-e"),
        FF.xml_evento_nfse(ch2, "Cancelamento por Substituição",
                           chave_substituta=ch3)])
    cfg = {"anexoServico": "III", "receitas_manuais": manuais_de(1_000_000.0, PA)}
    antigas, novas, _, _ = dois_caminhos(pasta)
    a = cls.calcular_das(antigas, PA, cfg, None)
    b = cls.calcular_das(novas, PA, cfg, None)
    igual(a["receita_mes"], b["receita_mes"], "mesma receita nos dois")
    igual(a["das_estimado"], b["das_estimado"], "e o mesmo DAS")
    igual(b["receita_mes"], 3200.0,
          "só a substituta (2.500) e a normal (700) entram")

# ══════════════════════════════════════════════════════════════════════════
secao("PIS/COFINS: caminho antigo × novo")
with apoio.raiz_temporaria("ap4_") as base:
    PA = "2026-06"
    pasta = montar(base, [
        FF.xml_nfse(1, PA, 100000.0, tp_ret_piscofins="1", ret_pis=650.0,
                    ret_cofins=3000.0, ret_csll=1000.0, ret_irrf=1500.0),
        FF.xml_nfse(2, PA, 50000.0),
        FF.xml_nfse(3, PA, 90000.0, emitente=FF.OUTRO)])
    _, _, antigas, novas = dois_caminhos(pasta)
    for regime in ("presumido", "real"):
        a = af.apurar(antigas, EMP, PA, regime)
        b = af.apurar(novas, EMP, PA, regime)
        for campo in ("receita_bruta", "pis_devido", "cofins_devido",
                      "retido_pis", "retido_cofins", "retido_csll",
                      "retido_irrf", "pis_a_pagar", "cofins_a_pagar",
                      "pis_saldo_credor", "cofins_saldo_credor",
                      "total_a_pagar", "notas"):
            igual(a[campo], b[campo], f"{regime}: `{campo}` idêntico")
        igual(a["avisos"], b["avisos"], f"{regime}: os mesmos avisos")
    a = af.apurar(antigas, EMP, PA, "presumido")
    igual(a["receita_bruta"], 150000.0,
          "e a nota recebida de terceiro ficou de fora nos dois")

secao("Retenção consolidada rateada atravessa a ponte")
with apoio.raiz_temporaria("ap4_") as base:
    PA = "2026-06"
    pasta = montar(base, [FF.xml_nfse(1, PA, 10000.0, ret_csll=465.0)])
    _, _, antigas, novas = dois_caminhos(pasta)
    a = af.apurar(antigas, EMP, PA, "presumido")
    b = af.apurar(novas, EMP, PA, "presumido")
    igual(a["retido_pis"], b["retido_pis"], "PIS rateado idêntico")
    igual(a["retido_cofins"], b["retido_cofins"], "COFINS rateado idêntico")
    igual(a["retido_csll"], b["retido_csll"], "CSLL idêntica")
    igual(b["retido_pis"], 65.0, "e o rateio da IN 459/2004 sobreviveu")

secao("IRPJ/CSLL trimestral: caminho antigo × novo")
with apoio.raiz_temporaria("ap4_") as base:
    pasta = montar(base, [
        FF.xml_nfse(1, "2026-04", 100000.0, ret_irrf=2000.0, ret_csll=1000.0),
        FF.xml_nfse(2, "2026-05", 100000.0),
        FF.xml_nfse(3, "2026-06", 100000.0)])
    _, _, antigas, novas = dois_caminhos(pasta)
    a = af.apurar_trimestre(antigas, EMP, 2026, 2)
    b = af.apurar_trimestre(novas, EMP, 2026, 2)
    for campo in ("receita_bruta", "base_irpj", "base_csll", "irpj_normal",
                  "irpj_adicional", "irpj_devido", "csll_devido",
                  "excedente_adicional", "limite_adicional", "retido_irrf",
                  "retido_csll", "irpj_a_pagar", "csll_a_pagar",
                  "total_a_pagar"):
        igual(a[campo], b[campo], f"trimestral: `{campo}` idêntico")
    igual(a["receita_por_mes"], b["receita_por_mes"],
          "e a receita mês a mês do trimestre")

secao("Receita informada à mão continua entrando nos dois caminhos")
with apoio.raiz_temporaria("ap4_") as base:
    PA = "2026-06"
    pasta = montar(base, [FF.xml_nfse(1, PA, 1000.0, ctribnac="171901")])
    manuais = cls.lancamentos_para_notas(
        [{"competencia": PA, "valor": 5000.0, "tipo": "aluguel_movel"}])
    cfg = {"receitas_manuais": manuais_de(1_000_000.0, PA)}
    antigas, novas, _, _ = dois_caminhos(pasta)
    a = cls.calcular_das(antigas + manuais, PA, cfg, None)
    b = cls.calcular_das(novas + manuais, PA, cfg, None)
    igual(a["receita_mes"], b["receita_mes"], "mesma receita com o lançamento")
    igual(a["das_estimado"], b["das_estimado"], "e o mesmo DAS")
    igual(b["receita_mes"], 6000.0, "1.000 da nota + 5.000 do lançamento")

secao("Mês sem nota: os dois caminhos concordam em não apurar")
with apoio.raiz_temporaria("ap4_") as base:
    pasta = montar(base, [FF.xml_nfse(1, "2026-06", 1000.0)])
    antigas, novas, _, _ = dois_caminhos(pasta)
    igual(cls.calcular_das(antigas, "2026-07", {}, None), None, "antigo: None")
    igual(cls.calcular_das(novas, "2026-07", {}, None), None, "novo: None")

secao("A fonte oficial da apuração NÃO foi trocada")
backend = RAIZ / "nfse" / "backend"
consumidores = []
for arq in sorted(backend.rglob("*.py")):
    if arq.name in ("ponte_motor.py",) or "__pycache__" in arq.parts:
        continue
    try:
        a = ast.parse(arq.read_text("utf-8", errors="replace"))
    except SyntaxError:
        continue
    nomes = {n.module for n in ast.walk(a) if isinstance(n, ast.ImportFrom)}
    nomes |= {x.name for n in ast.walk(a) if isinstance(n, ast.Import)
              for x in n.names}
    nomes |= {x.name for n in ast.walk(a) if isinstance(n, ast.ImportFrom)
              for x in n.names}
    if any("ponte_motor" in str(v) for v in nomes):
        consumidores.append(arq.name)
igual(consumidores, [],
      "nenhum módulo do sistema importa a ponte — ela só existe para conferência")
_fonte_cls = (backend / "classificador.py").read_text("utf-8")
_fonte_af = (backend / "apuracao_federal.py").read_text("utf-8")
ok("normalizacao" not in _fonte_cls, "classificador não conhece a normalização")
ok("ponte_motor" not in _fonte_cls, "nem a ponte")
ok("normalizacao" not in _fonte_af, "apuracao_federal não conhece a normalização")
ok("ponte_motor" not in _fonte_af, "nem a ponte")

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
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("APURAÇÃO 4: cálculo paralelo verde.")
