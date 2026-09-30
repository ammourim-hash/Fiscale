#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Leitura paralela e conferência (APURAÇÃO 3).

    python teste_apuracao3.py

O QUE ESTA SUÍTE GARANTE
    Que a conferência entre o caminho atual e o normalizado é HONESTA: separa
    divergência real de diferença de representação, não esconde nenhuma, não
    corrige nenhuma, e nunca troca a fonte da apuração.

    Garante também o limite: comparar conjuntos diferentes não prova nada. O
    `core` devolve as notas recebidas; o `classificador` não. Sem restringir às
    emitidas, a diferença seria de conjunto, não de leitura.

FIXTURES fictícias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_fiscal as FF                 # noqa: E402
import classificador as cls                        # noqa: E402
import core                                        # noqa: E402
from ingestao import conferencia as cf             # noqa: E402
from ingestao import normalizacao as nz            # noqa: E402

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


def montar(raiz, arquivos):
    """Grava os XMLs sob `<raiz>/<empresa>/xmls`, como em produção."""
    FF.gravar(Path(raiz) / EMP, arquivos)
    return Path(raiz)


# ══════════════════════════════════════════════════════════════════════════
secao("Classificação da diferença: real × representação")
igual(cf._classificar("retencoes.pis", None, Decimal("0")), cf.REPRESENTACAO,
      "None × 0 num campo monetário é representação")
igual(cf._classificar("retencoes.pis", Decimal("0.00"), None), cf.REPRESENTACAO,
      "e vale nos dois sentidos")
igual(cf._classificar("retencoes.pis", Decimal("10"), None), cf.REAL,
      "10 × None é divergência REAL — falta dinheiro de um lado")
igual(cf._classificar("retencoes.pis", Decimal("10"), Decimal("11")), cf.REAL,
      "valores diferentes são divergência real")
igual(cf._classificar("valor_bruto", None, Decimal("0")), cf.REPRESENTACAO,
      "vale para o valor bruto também")
igual(cf._classificar("situacao", "CANCELADA", "NORMAL"), cf.REAL,
      "campo NÃO monetário nunca vira representação")
igual(cf._classificar("competencia", None, None), cf.REAL,
      "e a regra do zero não se aplica fora do dinheiro")

secao("Conjuntos diferentes: o core traz recebidas, o classificador não")
with apoio.raiz_temporaria("ap3_") as base:
    raiz = montar(base, [FF.xml_nfse(1, "2026-06", 1000.0),
                         FF.xml_nfse(2, "2026-06", 500.0, emitente=FF.OUTRO)])
    r = cf.conferir_nfse(raiz, EMP)
    igual(r.documentos_core, 2, "o core devolve as duas")
    igual(r.documentos_classificador, 1, "o classificador só a emitida")
    igual(len(r.so_no_core), 1, "a recebida fica registrada como 'só no core'")
    igual(len(r.so_no_classificador), 0, "e nada aparece só no classificador")
    igual(r.comparados, 1, "só a interseção é comparada")

secao("`somente_saidas` é o que torna a comparação maçã-com-maçã")
with apoio.raiz_temporaria("ap3_") as base:
    raiz = montar(base, [FF.xml_nfse(1, "2026-06", 1000.0),
                         FF.xml_nfse(2, "2026-06", 500.0, emitente=FF.OUTRO)])
    r = cf.conferir_nfse(raiz, EMP, somente_saidas=True)
    igual(r.documentos_core, 1, "só as emitidas entram")
    igual(len(r.so_no_core), 0, "e os conjuntos passam a coincidir")
    igual(r.comparados, 1, "com tudo comparado")
    difs = cf.comparar_totais(r.totais_core, r.totais_cls)
    campos = {d["campo"] for d in difs}
    for campo in ("documentos", "na_receita", "valor_bruto", "valor_na_receita",
                  "pis", "cofins", "csll", "irrf", "iss"):
        ok(campo not in campos,
           f"total de `{campo}` IGUAL nos dois caminhos")

secao("Nota simples: nenhuma divergência real, só a de representação do ISS")
with apoio.raiz_temporaria("ap3_") as base:
    raiz = montar(base, [FF.xml_nfse(1, "2026-06", 1234.56)])
    r = cf.conferir_nfse(raiz, EMP, somente_saidas=True)
    igual(len(r.reais), 0, "zero divergências reais")
    igual([d.campo for d in r.representacao], ["retencoes.iss"],
          "e a única diferença é o ISS: 0 no core, None no classificador")
    d = r.representacao[0]
    igual(d.valor_a, Decimal("0.0"), "core informa zero")
    igual(d.valor_b, None, "classificador não informa")
    ok(d.id_documento, "a divergência carrega o id_documento")
    ok(d.para_json()["chave_final"], "e só o final da chave, nunca a chave inteira")

secao("Divergência REAL de situação aparece — e não é corrigida")
with apoio.raiz_temporaria("ap3_") as base:
    ch2, ch3 = FF.chave_nfse(2), FF.chave_nfse(3)
    raiz = montar(base, [
        FF.xml_nfse(2, "2026-06", 2000.0),
        FF.xml_nfse(3, "2026-06", 2500.0),
        FF.xml_evento_nfse(ch2, "Cancelamento por Substituição",
                           chave_substituta=ch3)])
    r = cf.conferir_nfse(raiz, EMP, somente_saidas=True)
    sit = [d for d in r.reais if d.campo == "situacao"]
    igual(len(sit), 2, "as duas notas do par divergem de situação")
    pares = sorted((d.valor_a, d.valor_b) for d in sit)
    igual(pares, [("SUBSTITUIDA", "CANCELADA"), ("SUBSTITUTA", "NORMAL")],
          "core distingue substituição; classificador rotula como cancelada/normal")
    # O que decide dinheiro é `entra_na_receita`, e nisso os dois concordam.
    difs = cf.comparar_totais(r.totais_core, r.totais_cls)
    campos = {d["campo"] for d in difs}
    ok("na_receita" not in campos, "e o número de notas NA RECEITA não muda")
    ok("valor_na_receita" not in campos, "nem o valor da receita")
    ok(campos <= {"canceladas", "substituidas", "substitutas"},
       "as diferenças de total são só de RÓTULO, não de base de cálculo")

secao("Divergência que MUDA dinheiro é classificada como real")
with apoio.raiz_temporaria("ap3_") as base:
    raiz = montar(base, [FF.xml_nfse(1, "2026-06", 10000.0,
                                     tp_ret_piscofins="1", ret_pis=65.0,
                                     ret_cofins=300.0,
                                     com_bloco_piscofins=False)])
    r = cf.conferir_nfse(raiz, EMP, somente_saidas=True)
    campos = {d.campo for d in r.reais}
    ok("retencoes.pis" in campos, "a retenção de PIS que só um leitor vê é REAL")
    ok("retencoes.cofins" in campos, "e a de COFINS também")
    d = [x for x in r.reais if x.campo == "retencoes.pis"][0]
    igual(d.valor_a, Decimal("0.0"), "core lê zero")
    igual(d.valor_b, Decimal("65.0"), "classificador lê 65,00")
    difs = cf.comparar_totais(r.totais_core, r.totais_cls)
    ok(any(x["campo"] == "pis" for x in difs),
       "e o total de PIS por competência também acusa")

secao("Totais por competência: separados por mês, sem misturar")
with apoio.raiz_temporaria("ap3_") as base:
    raiz = montar(base, [FF.xml_nfse(1, "2026-05", 1000.0),
                         FF.xml_nfse(2, "2026-06", 2000.0),
                         FF.xml_nfse(3, "2026-06", 3000.0)])
    r = cf.conferir_nfse(raiz, EMP, somente_saidas=True)
    t = r.totais_core
    igual(sorted(t), ["2026-05", "2026-06"], "duas competências")
    igual(t["2026-05"].documentos, 1, "uma nota em maio")
    igual(t["2026-06"].documentos, 2, "duas em junho")
    igual(t["2026-06"].valor_bruto, Decimal("5000.0"), "5.000 em junho")
    igual(t["2026-05"].valor_na_receita, Decimal("1000.0"), "1.000 em maio")
    ok(isinstance(t["2026-06"].valor_bruto, Decimal), "e os totais são Decimal")

secao("Cancelada sai da receita mas continua contada como documento")
with apoio.raiz_temporaria("ap3_") as base:
    ch1 = FF.chave_nfse(1)
    raiz = montar(base, [FF.xml_nfse(1, "2026-06", 1000.0),
                         FF.xml_nfse(2, "2026-06", 2000.0),
                         FF.xml_evento_nfse(ch1)])
    r = cf.conferir_nfse(raiz, EMP, somente_saidas=True)
    t = r.totais_core["2026-06"]
    igual(t.documentos, 2, "os dois documentos existem")
    igual(t.canceladas, 1, "um cancelado")
    igual(t.na_receita, 1, "e só um entra na receita")
    igual(t.valor_bruto, Decimal("3000.0"), "o bruto soma os dois")
    igual(t.valor_na_receita, Decimal("2000.0"), "a receita, só o válido")

secao("A conferência NÃO corrige nada")
import ast                                          # noqa: E402
_fonte = (RAIZ / "nfse" / "backend" / "ingestao" / "conferencia.py").read_text("utf-8")
arv = ast.parse(_fonte)
chamadas = {getattr(n.func, "attr", "") or getattr(n.func, "id", "")
            for n in ast.walk(arv) if isinstance(n, ast.Call)}
for proibida in ("calcular_das", "aliquota_efetiva", "apurar", "rbt12",
                 "rbt12_de", "com_situacao", "replace", "salvar", "write_text",
                 "write_bytes"):
    ok(proibida not in chamadas, f"a conferência não chama `{proibida}`")
ok("import classificador" in _fonte and "import core" in _fonte,
   "ela LÊ os leitores atuais — dentro da função, sem virar dependência de módulo")

secao("Quem consome a conferência — e só quem foi declarado")
backend = RAIZ / "nfse" / "backend"
# Buscar a palavra no texto acusaria qualquer módulo com um campo chamado
# `conferencia`, que nada tem a ver com isto. Import é coisa do AST.
consumidores = []
for arq in sorted(backend.rglob("*.py")):
    if arq.name == "conferencia.py" or "__pycache__" in arq.parts:
        continue
    try:
        arvore = ast.parse(arq.read_text("utf-8", errors="replace"))
    except SyntaxError:
        continue
    nomes = {n.module for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)}
    nomes |= {a.name for n in ast.walk(arvore) if isinstance(n, ast.Import)
              for a in n.names}
    nomes |= {a.name for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)
              for a in n.names}
    if any("conferencia" in str(x) for x in nomes):
        consumidores.append(arq.name)
# A CONFERÊNCIA PASSOU A TER UM SEGUNDO LEITOR, DECLARADO — 27/09/2026.
#     O título desta seção dizia "ainda", e era isso mesmo: o guarda existia
#     para que nenhum módulo fiscal consumisse a conferência SEM decisão. A
#     decisão veio: a memória de cálculo mostra a NF-e do acervo como bloco de
#     CONFERÊNCIA, com `entra_na_base = False`, e `teste_apuracao_memoria`
#     prova que ela não é somada a base nenhuma.
#
#     O guarda continua fechado: só estes dois, e qualquer terceiro reprova.
igual(consumidores, ["apuracao_memoria.py", "importacao.py"],
      "só o portão (escreve) e a memória de cálculo (lê) tocam a conferência")

secao("NF-e: leitura normalizada do acervo, sem tributo")
_fonte_nfe = _fonte
ok("NADA de tributo" in _fonte_nfe or "Não calcula tributo" in _fonte_nfe
   or "não calcula tributo" in _fonte_nfe.lower(),
   "está escrito que a leitura de NF-e não calcula tributo")
rel = cf.RelatorioNFe(identidade=EMP)
for campo in ("documentos", "entradas", "saidas", "mercadorias", "itens",
              "com_cfop", "com_ncm", "com_cst_icms", "sem_itens",
              "indeterminados"):
    ok(hasattr(rel, campo), f"o relatório de NF-e tem `{campo}`")
import re                                          # noqa: E402
palavras = set(re.findall(r"[A-Za-zÀ-ÿ_]+", _fonte_nfe))
for termo in ("anexo", "Anexo", "monofasico", "substituicao_tributaria",
              "devolucao", "DAS", "aliquota", "tributo_devido"):
    # Palavra inteira: "DAS" casa dentro de "TODAS" e acusaria um comentário.
    ok(termo not in palavras, f"nem tratamento de `{termo}`")

secao("Relatório de NF-e vazio quando não há acervo")
with apoio.raiz_temporaria("ap3_") as base:
    r = cf.conferir_nfe(base, EMP)
    igual(r.documentos, 0, "sem acervo, zero documentos")
    igual(r.itens, 0, "e zero itens — não estoura")

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
print("APURAÇÃO 3: conferência verde.")
