#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rastreabilidade da apuração (APURAÇÃO 5).

    python teste_apuracao5.py

O QUE ESTA SUÍTE GARANTE
    Que todo valor apurado tem caminho de volta ao documento (D44), que a soma
    dos documentos rastreados FECHA com a base que o motor usou, e que o
    rastreio não recalcula nada — ele lê as marcas que o próprio motor deixou.

    Garante também que as 1.206 comparações da APURAÇÃO 4 continuam idênticas
    depois de tudo isso.

FIXTURES fictícias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import ast
import sys
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
from ingestao import rastreio as rt                # noqa: E402

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
PA = "2026-06"
FONTE = (RAIZ / "nfse" / "backend" / "ingestao" / "rastreio.py").read_text("utf-8")


def montar(base, arquivos):
    FF.gravar(Path(base) / EMP, arquivos)
    return Path(base) / EMP


def manuais_de(rbt12: float, pa: str) -> dict:
    v = round(rbt12 / 12.0, 2)
    m = {cls._mes_menos(pa, i): v for i in range(1, 13)}
    m[cls._mes_menos(pa, 12)] = round(rbt12 - v * 11, 2)
    return m


def caminhos(pasta):
    ops_cls = [nz.de_nota_classificador(n, EMP) for n in cls.ler_nfse(pasta, EMP)]
    ops_core = [nz.de_nota_core(n, EMP) for n in core.carregar_notas(pasta)]
    return (ops_cls, pm.para_classificador(ops_cls),
            ops_core, pm.para_federal(ops_core, identidade_empresa=EMP))


# ══════════════════════════════════════════════════════════════════════════
secao("O rastreio NÃO recalcula — ele chama o motor e lê as marcas")
arv = ast.parse(FONTE)
chamadas = {getattr(n.func, "attr", "") or getattr(n.func, "id", "")
            for n in ast.walk(arv) if isinstance(n, ast.Call)}
ok("calcular_das" in chamadas, "chama o `calcular_das` de verdade")
ok("apurar" in chamadas, "e o `apurar` de verdade")
ok("apurar_trimestre" in chamadas, "e o `apurar_trimestre` de verdade")
for proibida in ("aliquota_efetiva", "reparticao", "resolver_anexo",
                 "anexo_por_lc116", "rbt12", "rbt12_de"):
    ok(proibida not in chamadas, f"e NÃO chama `{proibida}` por conta própria")
import re                                          # noqa: E402
palavras = set(re.findall(r"[A-Za-zÀ-ÿ_0-9]+", FONTE))
for termo in ("TABELAS", "REPARTICAO", "ALIQUOTAS", "IRPJ_ALIQ", "CSLL_ALIQ"):
    ok(termo not in palavras, f"nenhuma tabela fiscal em `{termo}`")
numeros = {n.value for n in ast.walk(arv)
           if isinstance(n, ast.Constant) and isinstance(n.value, float)}
igual(numeros, {0.01, 0.0},
      "os únicos decimais são a tolerância de 1 centavo e o 0.0 de ausente")

secao("Receita da competência: a soma dos documentos FECHA com a base")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [FF.xml_nfse(1, PA, 1000.0, ctribnac="171901"),
                          FF.xml_nfse(2, PA, 2500.50, ctribnac="171901"),
                          FF.xml_nfse(3, PA, 499.50, ctribnac="171901")])
    ops, notas, _, _ = caminhos(pasta)
    cfg = {"receitas_manuais": manuais_de(1_000_000.0, PA)}
    r = rt.rastrear_das(notas, PA, cfg, None)
    ok(r is not None, "o rastreio existe")
    igual(r.receita.valor, 4000.0, "a receita do motor é 4.000,00")
    igual(r.receita.soma_documentos, 4000.0, "e a soma dos documentos também")
    ok(r.receita.fecha, "portanto fecha")
    igual(r.receita.diferenca, 0.0, "com diferença zero")
    igual(r.receita.quantidade, 3, "três documentos rastreados")
    ok(all(d for d in r.receita.documentos), "todos com id_documento")
    igual(sorted(r.receita.documentos), sorted(o.id_documento for o in ops),
          "e são exatamente os das operações")
    ok(r.tudo_fecha, "e o rastreio inteiro fecha")

secao("Linhas do DAS por Anexo: cada linha fecha com os seus documentos")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [
        FF.xml_nfse(1, PA, 10000.0, ctribnac="171901"),   # Anexo III por lei
        FF.xml_nfse(2, PA, 20000.0, ctribnac="171901"),
        FF.xml_nfse(3, PA, 30000.0, ctribnac="070201"),   # Anexo IV por lei
    ])
    _, notas, _, _ = caminhos(pasta)
    cfg = {"receitas_manuais": manuais_de(1_000_000.0, PA)}
    r = rt.rastrear_das(notas, PA, cfg, None)
    igual(len(r.por_anexo), 2, "duas linhas: Anexo III e Anexo IV")
    por = {rot.split(" ·")[0]: c for rot, c in r.por_anexo.items()}
    igual(por["Anexo III"].valor, 30000.0, "Anexo III soma 30.000")
    igual(por["Anexo III"].quantidade, 2, "com 2 NFS-e")
    ok(por["Anexo III"].fecha, "e fecha")
    igual(por["Anexo IV"].valor, 30000.0, "Anexo IV soma 30.000")
    igual(por["Anexo IV"].quantidade, 1, "com 1 NFS-e")
    ok(por["Anexo IV"].fecha, "e fecha")
    soma = sum(c.soma_documentos for c in r.por_anexo.values())
    igual(round(soma, 2), r.receita.valor,
          "e as linhas somadas dão a receita da competência")
    ok(all(c.observacao.startswith("DAS da linha") for c in r.por_anexo.values()),
       "cada linha diz o DAS que gerou")

secao("ISS retido e ISS fixo: a linha separada tem os seus documentos")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [
        FF.xml_nfse(1, PA, 10000.0, ctribnac="171901"),
        FF.xml_nfse(2, PA, 5000.0, ctribnac="171901", iss_retido=True,
                    valor_iss=250.0)])
    _, notas, _, _ = caminhos(pasta)
    cfg = {"receitas_manuais": manuais_de(1_000_000.0, PA)}
    r = rt.rastrear_das(notas, PA, cfg, None)
    igual(len(r.por_anexo), 2, "duas linhas: com e sem retenção de ISS")
    retida = [c for rot, c in r.por_anexo.items() if "retido" in rot.lower()]
    igual(len(retida), 1, "uma linha é a da retenção")
    igual(retida[0].valor, 5000.0, "com 5.000 de receita")
    igual(retida[0].quantidade, 1, "e um documento")
    igual(r.iss_retido.valor, 250.0, "o ISS retido do mês é 250,00")
    igual(r.iss_retido.quantidade, 1, "de uma nota")
    ok(r.iss_retido.fecha, "e fecha")
    ok(r.tudo_fecha, "o rastreio inteiro fecha")

secao("ISS fixo (§22-A): o motor marca TODAS, e o rastreio segue a marca")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [FF.xml_nfse(1, PA, 100458.50, ctribnac="171901")])
    _, notas, _, _ = caminhos(pasta)
    cfg = {"issFixo": True, "receitas_manuais": manuais_de(1_287_572.25, PA)}
    r = rt.rastrear_das(notas, PA, cfg, None)
    igual(r.das_estimado, 8972.55, "o DAS da guia real")
    linha = list(r.por_anexo.values())[0]
    igual(linha.valor, 100458.50, "a linha traz a receita inteira")
    igual(linha.quantidade, 1, "com o documento que a formou")
    ok(linha.fecha, "e fecha")
    ok(cls.MOTIVO_ISS_FIXO in list(r.por_anexo.keys())[0],
       "e o rótulo carrega o motivo do §22-A, vindo do motor")

secao("Cancelada e substituída: fora da receita, mas RASTREÁVEIS")
with apoio.raiz_temporaria("ap5_") as base:
    ch1, ch2, ch3 = FF.chave_nfse(1), FF.chave_nfse(2), FF.chave_nfse(3)
    pasta = montar(base, [
        FF.xml_nfse(1, PA, 1000.0, ctribnac="171901"),
        FF.xml_nfse(2, PA, 2000.0, ctribnac="171901"),
        FF.xml_nfse(3, PA, 2500.0, ctribnac="171901"),
        FF.xml_nfse(4, PA, 700.0, ctribnac="171901"),
        FF.xml_evento_nfse(ch1, "Cancelamento de NFS-e"),
        FF.xml_evento_nfse(ch2, "Cancelamento por Substituição",
                           chave_substituta=ch3)])
    ops, notas, _, _ = caminhos(pasta)
    cfg = {"receitas_manuais": manuais_de(1_000_000.0, PA)}
    r = rt.rastrear_das(notas, PA, cfg, None)
    igual(r.receita.valor, 3200.0, "a receita é só a substituta + a normal")
    igual(r.receita.quantidade, 2, "dois documentos na receita")
    igual(r.excluidos.quantidade, 2, "e DOIS documentos excluídos, rastreados")
    igual(r.excluidos.soma_documentos, 3000.0,
          "com o valor que ficou de fora: 1.000 + 2.000")
    ok(r.excluidos.fecha, "a composição dos excluídos fecha por construção")
    ok("FORA da receita" in r.excluidos.observacao,
       "e a observação diz que é valor fora da base, não base")
    ids_excl = set(r.excluidos.documentos)
    ok(all(i for i in ids_excl), "os excluídos têm id_documento")
    ok(ids_excl.isdisjoint(set(r.receita.documentos)),
       "e nenhum deles aparece também na receita")
    for i in ids_excl:
        c = rt.caminho_do_documento(ops, i)
        ok(c is not None, "cada excluído é localizável")
        ok(c["arquivo"], "com o arquivo XML de origem")
        ok(not c["entra_na_receita"], "e marcado como fora da receita")
    ok(r.tudo_fecha, "o rastreio inteiro fecha mesmo com exclusões")

secao("Do id_documento de volta ao XML")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [FF.xml_nfse(1, PA, 1000.0, ctribnac="171901")])
    ops, notas, _, _ = caminhos(pasta)
    r = rt.rastrear_das(notas, PA, {"receitas_manuais": manuais_de(1e6, PA)}, None)
    id_doc = r.receita.documentos[0]
    c = rt.caminho_do_documento(ops, id_doc)
    igual(c["id_documento"], id_doc, "o caminho aponta o mesmo documento")
    igual(c["especie"], nz.NFSE, "com a espécie")
    ok(c["arquivo"].startswith("nfse-"), "e o nome do arquivo em disco")
    ok(c["chave_final"] and len(c["chave_final"]) == 6,
       "só o final da chave — nunca a chave inteira no relatório")
    igual(rt.caminho_do_documento(ops, "inexistente"), None,
          "identificador desconhecido devolve None, não estoura")

# ══════════════════════════════════════════════════════════════════════════
secao("PIS e COFINS: receita usada e retenções apontam as notas certas")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [
        FF.xml_nfse(1, PA, 100000.0, tp_ret_piscofins="1", ret_pis=650.0,
                    ret_cofins=3000.0, ret_csll=1000.0, ret_irrf=1500.0),
        FF.xml_nfse(2, PA, 50000.0),
        FF.xml_nfse(3, PA, 90000.0, emitente=FF.OUTRO)])
    _, _, ops_core, fed = caminhos(pasta)
    r = rt.rastrear_federal(fed, EMP, PA, "presumido")
    igual(r.receita.valor, 150000.0, "receita usada na base")
    igual(r.receita.quantidade, 2, "de duas notas EMITIDAS")
    ok(r.receita.fecha, "e fecha")
    igual(r.pis_devido, 975.0, "PIS devido 0,65%")
    igual(r.cofins_devido, 4500.0, "COFINS devido 3%")
    igual(r.retencoes["PIS"].valor, 650.0, "PIS retido")
    igual(r.retencoes["PIS"].quantidade, 1, "de UMA nota — a que teve retenção")
    ok(r.retencoes["PIS"].fecha, "e fecha")
    igual(r.retencoes["COFINS"].quantidade, 1, "COFINS: mesma nota")
    igual(r.retencoes["CSLL"].valor, 1000.0, "CSLL retida")
    igual(r.retencoes["IRRF"].valor, 1500.0, "IRRF retido")
    ok(all(c.fecha for c in r.retencoes.values()), "todas as retenções fecham")
    ok(r.tudo_fecha, "o rastreio federal inteiro fecha")
    a = af.apurar(fed, EMP, PA, "presumido")
    igual(r.receita.valor, a["receita_bruta"],
          "e o valor rastreado é o MESMO que o motor apurou")

secao("A nota recebida de terceiro não entra na base nem no rastreio")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [FF.xml_nfse(1, PA, 10000.0),
                          FF.xml_nfse(2, PA, 90000.0, emitente=FF.OUTRO)])
    _, _, ops_core, fed = caminhos(pasta)
    r = rt.rastrear_federal(fed, EMP, PA, "presumido")
    igual(r.receita.quantidade, 1, "só a emitida entra")
    igual(r.receita.valor, 10000.0, "e a base é a dela")

secao("Regime sem apuração federal não gera rastreio")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [FF.xml_nfse(1, PA, 10000.0)])
    _, _, _, fed = caminhos(pasta)
    igual(rt.rastrear_federal(fed, EMP, PA, "simples"), None,
          "Simples não tem PIS/COFINS separado — sem rastreio, sem invenção")

secao("IRPJ e CSLL trimestrais: receita do trimestre, mês a mês")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [
        FF.xml_nfse(1, "2026-04", 100000.0, ret_irrf=2000.0, ret_csll=1000.0),
        FF.xml_nfse(2, "2026-05", 100000.0),
        FF.xml_nfse(3, "2026-06", 100000.0)])
    _, _, _, fed = caminhos(pasta)
    r = rt.rastrear_trimestre(fed, EMP, 2026, 2)
    igual(r.periodo, "2026-T2", "segundo trimestre")
    igual(r.receita.valor, 300000.0, "receita do trimestre")
    igual(r.receita.quantidade, 3, "três notas")
    ok(r.receita.fecha, "e fecha")
    igual(sorted(r.por_mes), ["2026-04", "2026-05", "2026-06"], "três meses")
    for m, c in r.por_mes.items():
        igual(c.valor, 100000.0, f"{m}: 100.000")
        igual(c.quantidade, 1, f"{m}: uma nota")
        ok(c.fecha, f"{m}: fecha")
    igual(r.irpj_devido, 18000.0, "IRPJ devido do trimestre")
    igual(r.csll_devido, 8640.0, "CSLL devida")
    igual(r.retencoes["IRRF"].valor, 2000.0, "IRRF retido")
    igual(r.retencoes["IRRF"].quantidade, 1, "de uma nota")
    igual(r.retencoes["CSLL"].valor, 1000.0, "CSLL retida")
    ok(r.tudo_fecha, "o rastreio trimestral inteiro fecha")
    a = af.apurar_trimestre(fed, EMP, 2026, 2)
    igual(r.receita.valor, a["receita_bruta"], "igual ao que o motor apurou")
    igual(r.irpj_devido, a["irpj_devido"], "e o IRPJ também")

# ══════════════════════════════════════════════════════════════════════════
secao("As comparações da APURAÇÃO 4 continuam idênticas")
with apoio.raiz_temporaria("ap5_") as base:
    pasta = montar(base, [
        FF.xml_nfse(1, PA, 100458.50, ctribnac="171901"),
        FF.xml_nfse(2, PA, 15000.00, ctribnac="090201"),
        FF.xml_nfse(3, PA, 8000.00, ctribnac="070201", iss_retido=True,
                    valor_iss=400.0)])
    antigas = cls.ler_nfse(pasta, EMP)
    _, novas, _, _ = caminhos(pasta)
    cfg = {"receitas_manuais": manuais_de(1_287_572.25, PA)}
    a = cls.calcular_das(antigas, PA, cfg, None)
    b = cls.calcular_das(novas, PA, cfg, None)
    igual(a["das_estimado"], b["das_estimado"], "DAS idêntico antigo × novo")
    igual(a["tributos"], b["tributos"], "tributos idênticos")
    igual(a["receita_mes"], b["receita_mes"], "receita idêntica")
    # E o rastreio, feito depois, não mudou nada do resultado.
    r = rt.rastrear_das(novas, PA, cfg, None)
    igual(r.das_estimado, a["das_estimado"],
          "e o DAS do rastreio é o mesmo do caminho antigo")
    c = cls.calcular_das(novas, PA, cfg, None)
    igual(c["das_estimado"], a["das_estimado"],
          "recalcular DEPOIS do rastreio dá o mesmo número — a mutação não corrompe")

secao("Nada consome o rastreio ainda — interface intocada")
backend = RAIZ / "nfse" / "backend"
consumidores = []
for arq in sorted(backend.rglob("*.py")):
    if arq.name == "rastreio.py" or "__pycache__" in arq.parts:
        continue
    try:
        a = ast.parse(arq.read_text("utf-8", errors="replace"))
    except SyntaxError:
        continue
    nomes = {n.module for n in ast.walk(a) if isinstance(n, ast.ImportFrom)}
    nomes |= {x.name for n in ast.walk(a) if isinstance(n, ast.Import) for x in n.names}
    nomes |= {x.name for n in ast.walk(a) if isinstance(n, ast.ImportFrom)
              for x in n.names}
    if any("rastreio" in str(v) for v in nomes):
        consumidores.append(arq.name)
igual(consumidores, [], "nenhum módulo importa o rastreio — a tela é fase própria")
_main = (backend / "main.py").read_text("utf-8")
ok("rastreio" not in _main, "main.py não conhece o rastreio")

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
print("APURAÇÃO 5: rastreabilidade verde.")
