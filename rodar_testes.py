#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rodar_testes.py — roda a suíte inteira e diz um número em que dá para confiar.

    python rodar_testes.py                 # tudo
    python rodar_testes.py teste_nfe6.py   # só estas
    python rodar_testes.py --json saida.json

POR QUE ESTE ARQUIVO EXISTE
    A contagem de asserções do projeto passou meses sendo feita por laço de
    shell com `grep`, e cada laço contava diferente. Em 29/08/2026 dois
    números conflitavam para a mesma árvore — 6.053 e 5.830 — e nenhum dos
    dois estava certo: o real era 5.922. As três causas foram:

      1. módulos de APOIO contados como suítes verdes. Rodá-los devolve 0 e
         zero asserções; três arquivos entravam no total sem testar nada;
      2. metade das suítes imprime a linha-resumo INDENTADA, e uma expressão
         ancorada em `^` as somava como zero — foi o que produziu 5.830, a
         menos exatamente das 92 asserções de duas suítes;
      3. duas suítes importavam uma terceira que tem asserção no nível do
         módulo, e a saída ficava com DUAS linhas-resumo. O total dos mesmos
         três arquivos era 627, 936 ou 1.251 conforme qual delas se lesse.

    Este runner resolve as três, e — mais importante — **recusa-se a adivinhar**
    quando algo não bate. Um número que engole ambiguidade é pior que um erro.

O QUE ELE DISTINGUE, E COMO
    APOIO       o arquivo declara `TESTE_APOIO = True`. Lido por AST, sem
                importar: importar um módulo para decidir se ele é teste é
                exatamente o que causa efeito colateral.
    SUITE       o resto. Executado como processo próprio.
    FALHA       código de saída ≠ 0, ou a linha-resumo acusa falha.
    TIMEOUT     estourou `--tempo` (padrão 900 s por suíte).
    SEM_RESULTADO  rodou, saiu 0, e não imprimiu resumo nem linha `ok`.
                   Não é sucesso: é uma suíte que não disse nada.
    ANINHADA    imprimiu MAIS DE UMA linha-resumo — sinal de que importou
                outra suíte. Tratado como erro, não somado às cegas.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent

# A linha que as suítes imprimem no fim. SEM âncora em `^`: várias a imprimem
# indentada, e exigir a coluna zero foi metade do erro de contagem.
RESUMO = re.compile(r"(\d+)\s+ok\s+·\s+(\d+)\s+falha")
LINHA_OK = re.compile(r"^\s+ok\s{2,}", re.M)
LINHA_FALHOU = re.compile(r"^\s+FALHOU\s", re.M)

SUITE, APOIO = "SUITE", "APOIO"
OK, FALHA, TIMEOUT, SEM_RESULTADO, ANINHADA = (
    "ok", "FALHA", "TIMEOUT", "SEM_RESULTADO", "ANINHADA")

TEMPO_PADRAO = 900


def python_dos_testes() -> str:
    """A venv do módulo NFS-e quando ela existe — é onde estão as dependências."""
    for cand in (RAIZ / "nfse" / ".venv" / "Scripts" / "python.exe",
                 RAIZ / "nfse" / ".venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def e_apoio(caminho: Path) -> bool:
    """`TESTE_APOIO = True` no nível do módulo, lido sem executar nada.

    A declaração é explícita de propósito. A versão anterior adivinhava pelo
    conteúdo ("tem `__main__`?"), e adivinhação em contagem de teste é como
    um arquivo de apoio vira suíte verde sem ninguém notar.
    """
    try:
        arvore = ast.parse(caminho.read_text("utf-8", errors="replace"))
    except SyntaxError:
        return False
    for no in arvore.body:
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if (isinstance(alvo, ast.Name) and alvo.id == "TESTE_APOIO"
                        and isinstance(no.value, ast.Constant)
                        and no.value.value is True):
                    return True
    return False


def executar(caminho: Path, tempo: int) -> dict:
    t0 = time.perf_counter()
    try:
        p = subprocess.run([python_dos_testes(), "-X", "utf8", caminho.name],
                           cwd=str(RAIZ), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=tempo)
        saida, codigo, estourou = (p.stdout or "") + (p.stderr or ""), p.returncode, False
    except subprocess.TimeoutExpired as e:
        bruto = e.stdout or b""
        saida = bruto if isinstance(bruto, str) else bruto.decode("utf-8", "replace")
        codigo, estourou = None, True

    segundos = round(time.perf_counter() - t0, 1)
    resumos = RESUMO.findall(saida)

    if estourou:
        estado, assercoes, falhas = TIMEOUT, 0, 0
    elif len(resumos) > 1:
        # Não somar. Duas linhas-resumo querem dizer que esta suíte executou
        # outra por dentro, e qualquer total daqui seria arbitrário.
        estado, assercoes, falhas = ANINHADA, 0, 0
    elif resumos:
        assercoes, falhas = int(resumos[0][0]), int(resumos[0][1])
        estado = OK if (codigo == 0 and falhas == 0) else FALHA
    else:
        assercoes = len(LINHA_OK.findall(saida))
        falhas = len(LINHA_FALHOU.findall(saida))
        if assercoes == 0 and falhas == 0:
            estado, assercoes = SEM_RESULTADO, 0
        else:
            estado = OK if (codigo == 0 and falhas == 0) else FALHA

    return {"arquivo": caminho.name, "tipo": SUITE, "estado": estado,
            "assercoes": assercoes, "falhas": falhas, "codigo_saida": codigo,
            "segundos": segundos, "linhas_resumo": len(resumos),
            "cauda": saida.strip().splitlines()[-4:]}


def main(argv: list[str]) -> int:
    tempo = TEMPO_PADRAO
    destino_json = None
    alvos: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            i += 1
            destino_json = argv[i]
        elif a == "--tempo":
            i += 1
            tempo = int(argv[i])
        else:
            alvos.append(a)
        i += 1

    arquivos = ([RAIZ / a for a in alvos] if alvos
                else sorted(RAIZ.glob("teste_*.py")))
    linhas: list[dict] = []

    print(f"{'arquivo':<34} {'estado':<14} {'asserções':>9} {'falhas':>7} "
          f"{'saída':>6} {'tempo':>8}")
    print("─" * 84)

    for arq in arquivos:
        if e_apoio(arq):
            r = {"arquivo": arq.name, "tipo": APOIO, "estado": "—",
                 "assercoes": 0, "falhas": 0, "codigo_saida": None,
                 "segundos": 0.0, "linhas_resumo": 0, "cauda": []}
            linhas.append(r)
            print(f"{arq.name:<34} {'módulo de apoio':<14} "
                  f"{'—':>9} {'—':>7} {'—':>6} {'—':>8}")
            continue
        r = executar(arq, tempo)
        linhas.append(r)
        # UMA linha por suíte, sempre com as mesmas colunas.
        print(f"{r['arquivo']:<34} {r['estado']:<14} {r['assercoes']:>9} "
              f"{r['falhas']:>7} {str(r['codigo_saida']):>6} "
              f"{r['segundos']:>7.1f}s")

    suites = [r for r in linhas if r["tipo"] == SUITE]
    apoios = [r for r in linhas if r["tipo"] == APOIO]
    problemas = [r for r in suites if r["estado"] != OK]

    resumo = {
        "arquivos_teste": len(linhas),
        "suites_executaveis": len(suites),
        "modulos_de_apoio": len(apoios),
        "assercoes_unicas": sum(r["assercoes"] for r in suites),
        "falhas": sum(r["falhas"] for r in suites),
        "verde": not problemas,
        "problemas": {r["arquivo"]: r["estado"] for r in problemas},
        "segundos": round(sum(r["segundos"] for r in linhas), 1),
    }

    print("─" * 84)
    print(f"{resumo['suites_executaveis']} suítes executáveis · "
          f"{resumo['assercoes_unicas']} asserções únicas · "
          f"{resumo['falhas']} falha(s) · "
          f"{resumo['modulos_de_apoio']} módulos de apoio · "
          f"{resumo['segundos']:.0f}s")
    if problemas:
        print("\nPROBLEMAS:")
        for r in problemas:
            print(f"  {r['estado']:<14} {r['arquivo']}")
            for l in r["cauda"]:
                print(f"       {l[:96]}")

    if destino_json:
        Path(destino_json).write_text(
            json.dumps({"resumo": resumo, "arquivos": linhas},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\ndetalhe por arquivo em {destino_json}")

    return 0 if resumo["verde"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
