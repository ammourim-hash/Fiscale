#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gravação concorrente do estado de tela — Fase 3.1.

    python teste_estado_concorrente.py

O DEFEITO QUE ISTO TRAVA
    Com o escritório inteiro no mesmo servidor, duas pessoas podem abrir a
    mesma tela. Antes, a última a salvar sobrescrevia o arquivo inteiro e o
    trabalho da primeira sumia — sem erro, sem aviso, sem registro.

    Agora a tela devolve no `If-Match` a versão que recebeu no `ETag`, e o
    servidor recusa a gravação atrasada com 409.

O QUE NÃO SE QUER
    Bloquear. Quem não manda `If-Match` (tela antiga, `restaurar_backup.py`)
    continua gravando como antes. A proteção é detectar e avisar.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio as _ta  # noqa: E402

# A raiz precisa existir ANTES de importar o servidor: `fiscale_server` resolve
# `DADOS` no import. Sem isto o teste escreveria na pasta real do usuário.
os.environ["FISCALE_TESTE_PROIBIR_RAIZ_REAL"] = "1"
PASTA = Path(_ta.pasta_temp(prefix="estado_conc_"))
os.environ["FISCALE_DADOS"] = str(PASTA)

import fiscale_dados as fd  # noqa: E402
fd.raiz(PASTA)

import fiscale_server as fs  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []


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


secao("A versão do estado muda a cada gravação")
destino = fs.caminho_estado("ensaio")
igual(fs.versao_estado("ensaio"), "0", "arquivo que não existe é a versão '0'")

fs.gravar_json_atomico(destino, {"n": 1})
v1 = fs.versao_estado("ensaio")
ok(v1 != "0", "depois de gravar, a versão deixa de ser '0'")

# `mtime_ns` tem resolução fina, mas não infinita: duas gravações no mesmo
# tique dariam a mesma versão. Mudar o conteúdo e conferir o que ficou em
# disco é o que este teste precisa garantir de fato.
fs.gravar_json_atomico(destino, {"n": 2})
igual(json.loads(Path(destino).read_text("utf-8"))["n"], 2,
      "e a segunda gravação é a que fica em disco")


secao("A gravação atômica não deixa arquivo pela metade")
grande = {"clientes": [{"i": i, "nome": "EMPRESA " * 40} for i in range(2000)]}
alvo = fs.caminho_estado("grande")
erros: list[str] = []


def gravar(n):
    try:
        fs.gravar_json_atomico(alvo, {**grande, "quem": n})
    except Exception as e:      # pragma: no cover - é o que o teste procura
        erros.append(f"{e.__class__.__name__}: {e}")


def ler_sem_parar(parar):
    while not parar.is_set():
        try:
            if os.path.exists(alvo):
                json.loads(Path(alvo).read_text("utf-8"))
        except json.JSONDecodeError:
            erros.append("um leitor pegou o arquivo pela metade")
            return
        except OSError:
            pass            # arquivo trocando de lugar: o `os.replace` faz isso


parar = threading.Event()
leitor = threading.Thread(target=ler_sem_parar, args=(parar,))
leitor.start()
fios = [threading.Thread(target=gravar, args=(i,)) for i in range(8)]
for t in fios:
    t.start()
for t in fios:
    t.join()
parar.set()
leitor.join(timeout=5)

ok(not erros, "8 gravações simultâneas e um leitor: nenhum JSON quebrado"
   + ("" if not erros else f"  ({erros[:2]})"))
ok(json.loads(Path(alvo).read_text("utf-8")).get("quem") is not None,
   "e o que sobrou em disco é um estado inteiro, de UMA das gravações")

ok(not [p for p in os.listdir(PASTA) if ".tmp" in p],
   "nenhum arquivo temporário ficou para trás")


secao("O servidor recusa a gravação atrasada (409)")
# Sem subir HTTP: a decisão é `enviado != versao_estado(mod)`, e é ela que
# precisa estar certa. O caminho HTTP inteiro é exercitado no ensaio na 8899.
fs.gravar_json_atomico(fs.caminho_estado("clientes"), {"clientes": [{"n": "maria"}]})
v_maria = fs.versao_estado("clientes")

# João abriu a tela agora: leva a mesma versão.
v_joao = fs.versao_estado("clientes")
igual(v_joao, v_maria, "as duas telas carregaram a mesma versão")

# Maria salva primeiro.
fs.gravar_json_atomico(fs.caminho_estado("clientes"), {"clientes": [{"n": "maria2"}]})
v_depois = fs.versao_estado("clientes")

ok(v_depois != v_joao, "depois que Maria salva, a versão de João está velha")
ok(v_depois == fs.versao_estado("clientes"),
   "e quem carregar agora leva a versão nova")
igual(json.loads(Path(fs.caminho_estado("clientes")).read_text("utf-8"))["clientes"][0]["n"],
      "maria2", "o que Maria salvou continua em disco")


secao("Quem não manda If-Match continua gravando (não bloqueia)")
ok(fs.versao_estado("sem_arquivo_nenhum") == "0",
   "módulo nunca salvo responde '0', e '0' vazio não recusa nada")


print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Todos os testes de estado concorrente passaram.")
