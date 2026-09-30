#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ciclo de vida do módulo NFS-e: nasce preso ao servidor e morre com ele.

    python teste_nfse_ciclo_vida.py

POR QUE ESTE TESTE EXISTE
    O módulo NFS-e é um processo-filho do `fiscale_server` (via o lançador da
    venv, que cria um neto com o Python base). O encerramento normal —
    Home > Encerrar, bandeja, `atexit` — já chamava `parar_modulo_nfse()`. Mas
    se o servidor morresse sem passar por ali (queda, Gerenciador de Tarefas,
    logoff), o módulo ficava órfão na porta interna, segurando a pasta de
    dados. Em 16/09/2026 isso ficou visível na produção, depois de um
    relançamento manual do módulo. A correção prende o filho num Job Object
    com KILL_ON_JOB_CLOSE, cujo handle é do servidor.

O QUE ESTA SUÍTE PROVA
    1. O servidor prende o filho ao job logo depois de lançá-lo, e o
       encerramento fecha o job.
    2. Nenhum código do projeto encerra processo por NOME.
    3. O NFS-e que o servidor lança fica NA ÁRVORE dele, em porta interna
       PRÓPRIA — nenhuma que já estivesse ocupada quando o teste começou,
       incluindo as da produção e da homologação quando elas estão no ar.
    4. Home > Encerrar (`/api/encerrar`) derruba o servidor E o módulo.
    5. Matar SÓ o processo do servidor (sem `atexit`, sem `parar_modulo_nfse`)
       também derruba o módulo — é o caso que o job resolve.
    6. Produção e homologação continuam com os mesmos processos e os mesmos
       dados.

NADA AQUI TOCA A PRODUÇÃO OU A HOMOLOGAÇÃO. Cada instância é própria, com
porta e pasta de dados temporárias; todo encerramento é por PID ou handle.
"""
from __future__ import annotations

import ctypes
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio as apoio                          # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

SENHA = "rede de dormir na varanda azul"         # inventada para este teste
PERFIL = Path(os.environ.get("USERPROFILE") or Path.home())
REAL = PERFIL / "Fiscale" / "dados"
HOMOLOGACAO = Path(r"C:\Fiscale\homologacao\dados")
PORTAS_VIGIADAS = (8777, 8790, 8898, 8791)


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
    ok(a == b, desc if a == b else desc + "  (obtive %r, esperava %r)" % (a, b))


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def vivo(pid) -> bool:
    if not pid:
        return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))      # QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        codigo = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(codigo))
        return codigo.value == 259                    # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def encerrar_so_este(pid) -> bool:
    """TerminateProcess num PID — só nele, sem árvore e sem `atexit`."""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x0001, False, int(pid))      # PROCESS_TERMINATE
    if not h:
        return False
    try:
        return bool(k32.TerminateProcess(h, 9))
    finally:
        k32.CloseHandle(h)


def escutas() -> dict[int, int]:
    """{porta: pid} das portas TCP em LISTENING (netstat, só leitura)."""
    saida = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                           text=True, errors="replace").stdout
    mapa = {}
    for linha in saida.splitlines():
        partes = linha.split()
        if len(partes) >= 5 and partes[3].upper().startswith(("LISTEN", "ESCUTA")):
            try:
                mapa[int(partes[1].rsplit(":", 1)[1])] = int(partes[4])
            except ValueError:
                pass
    return mapa


def foto(pasta: Path):
    if not pasta.exists():
        return []
    return sorted((p.name, p.stat().st_mtime_ns if p.is_file() else -1)
                  for p in pasta.iterdir()
                  if not p.name.endswith((".log", ".db", ".db-wal", ".db-shm")))


def espera(cond, segundos=30.0, passo=0.25):
    fim = time.time() + segundos
    while time.time() < fim:
        if cond():
            return True
        time.sleep(passo)
    return cond()


class Instancia:
    """Um FISCALE de verdade, só deste teste."""

    def __init__(self, rotulo):
        self.raiz = Path(tempfile.mkdtemp(prefix="nfse_ciclo_%s_" % rotulo))
        self.dados = self.raiz / "dados"
        self.dados.mkdir()
        self.porta = porta_livre()
        self.cookie = ""
        env = dict(os.environ)
        env.update({"FISCALE_DADOS": str(self.dados), "FISCALE_PORT": str(self.porta),
                    "FISCALE_SEM_BANDEJA": "1", "FISCALE_NO_BROWSER": "1",
                    "FISCALE_SEM_ATALHO": "1", "FISCALE_TESTE_PROIBIR_RAIZ_REAL": "1",
                    "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
        self.log = open(self.raiz / "saida.log", "wb")
        self.proc = subprocess.Popen([sys.executable, str(RAIZ / "fiscale_server.py")],
                                     cwd=str(RAIZ), env=env, stdin=subprocess.DEVNULL,
                                     stdout=self.log, stderr=subprocess.STDOUT)

    def pedir(self, metodo, caminho, obj=None):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=60)
        cab = {"Content-Type": "application/json"}
        if self.cookie:
            cab["Cookie"] = self.cookie
        corpo = None if obj is None else json.dumps(obj).encode("utf-8")
        c.request(metodo, caminho, body=corpo, headers=cab)
        r = c.getresponse()
        dados = r.read()
        h = {k.lower(): v for k, v in r.getheaders()}
        if "set-cookie" in h:
            self.cookie = h["set-cookie"].split(";")[0]
        c.close()
        return r.status, dados

    def de_pe(self):
        def tenta():
            try:
                return self.pedir("GET", "/login.html")[0] == 200
            except OSError:
                return False
        return espera(tenta, 90)

    def arvore(self):
        return apoio.descendentes(self.proc.pid)

    def modulo(self):
        """(porta, pid) do NFS-e desta instância: escuta interna DENTRO da árvore."""
        arvore = set(self.arvore())
        for porta, pid in escutas().items():
            if 8790 <= porta <= 8830 and pid in arvore:
                return porta, pid
        return None, None

    def servidor(self):
        """PID do processo que escuta a porta principal desta instância."""
        return escutas().get(self.porta)

    def limpar(self):
        try:
            apoio.encerrar_arvore(self.proc)
        finally:
            try:
                self.log.close()
            except Exception:
                pass
            shutil.rmtree(self.raiz, ignore_errors=True)


def main() -> int:
    if sys.platform != "win32":
        print("  ok   não se aplica fora do Windows")
        print("  1 ok · 0 falha(s)")
        return 0

    antes_portas = {p: escutas().get(p) for p in PORTAS_VIGIADAS}
    # TODAS as portas já ocupadas, e não só as quatro vigiadas. É contra este
    # conjunto que se prova o isolamento da instância de teste: exigir "não é
    # 8790 nem 8791" amarrava o sucesso ao fato de a homologação estar no ar —
    # com ela parada, a 8791 fica livre, o mecanismo de escolha a pega por ser
    # livre mesmo, e o teste reprovava um comportamento correto.
    ocupadas_antes = set(escutas())
    antes_real, antes_homolog = foto(REAL), foto(HOMOLOGACAO)

    # ── 1 · o código ─────────────────────────────────────────────────────
    secao("1 · O servidor prende o módulo a si e o solta no encerramento")
    fonte = (RAIZ / "fiscale_server.py").read_text("utf-8")
    ok("def _prender_ao_servidor(proc):" in fonte, "existe `_prender_ao_servidor`")
    ok("0x2000" in fonte and "KILL_ON_JOB_CLOSE" in fonte,
       "o job é criado com JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE")
    inicio = fonte[fonte.find("def iniciar_modulo_nfse():"):]
    inicio = inicio[:inicio.find("\ndef ", 10)]
    # O `(?:\w+\s*=\s*)?` aceita a atribuição do resultado, que passou a
    # existir quando o vínculo virou linha de registro (`preso = ...`). A
    # exigência não mudou: prender o filho IMEDIATAMENTE depois do `Popen`,
    # sem nada entre os dois.
    ok(re.search(r"stderr=subprocess\.STDOUT\)\s*\n\s*except Exception as e:.*?return\s*\n"
                 r"\s*(?:\w+\s*=\s*)?_prender_ao_servidor\(proc\)", inicio, re.S) is not None,
       "cada filho lançado é preso ao job logo depois do Popen")
    parar = fonte[fonte.find("def parar_modulo_nfse():"):]
    parar = parar[:parar.find("\ndef ", 10)]
    ok("NFSE_PROC.terminate()" in parar and "CloseHandle" in parar and "NFSE_JOB = None" in parar,
       "parar_modulo_nfse() encerra o filho e fecha o job")
    ok(fonte.count("parar_modulo_nfse()") >= 2,
       "Encerrar e a bandeja chamam parar_modulo_nfse() antes de os._exit")

    # ── 2 · nada por nome ────────────────────────────────────────────────
    secao("2 · Nenhum encerramento de processo por NOME")
    padroes = [
        (r"taskkill[^\n]*/IM\b", "taskkill /IM"),
        (r"Stop-Process\s+[^\n]*-Name\b", "Stop-Process -Name"),
        (r"Get-Process\s+[^\n]*-Name[^\n]*\|\s*Stop-Process", "Get-Process -Name | Stop-Process"),
        (r"\b(killall|pkill)\b", "killall/pkill"),
        (r"wmic[^\n]*name\s*=[^\n]*(delete|terminate)", "wmic ... name= ... terminate"),
        (r"\bpsutil\.process_iter\b", "psutil.process_iter"),
    ]
    fora = ("dist", "build", "node_modules", ".venv", "elo", "__pycache__", ".git")
    achados = []
    for arq in RAIZ.rglob("*"):
        if (not arq.is_file() or arq.suffix.lower() not in (".py", ".bat", ".cmd", ".ps1", ".vbs")
                or any(parte in fora or parte.startswith(("checkpoint_", "backup_pre_"))
                       for parte in arq.relative_to(RAIZ).parts)):
            continue
        texto = arq.read_text("utf-8", "ignore")
        if arq.name == Path(__file__).name:
            continue                                   # esta suíte cita os padrões
        for regex, nome in padroes:
            if re.search(regex, texto, re.I):
                achados.append("%s (%s)" % (arq.relative_to(RAIZ), nome))
    igual(achados, [], "nenhum arquivo do projeto encerra processo por nome")
    apoio_fonte = (RAIZ / "teste_apoio.py").read_text("utf-8")
    ok('"/PID", str(alvo)' in apoio_fonte,
       "o encerramento de árvore dos testes é por PID (taskkill /PID)")

    # ── 3 e 4 · árvore e Encerrar ────────────────────────────────────────
    secao("3 · O módulo nasce na árvore do servidor")
    a = Instancia("encerrar")
    try:
        ok(a.de_pe(), "instância de teste no ar (porta %d)" % a.porta)
        porta, pid_modulo = None, None
        espera(lambda: a.modulo()[0] is not None, 30)
        porta, pid_modulo = a.modulo()
        ok(pid_modulo is not None, "há um NFS-e escutando DENTRO da árvore do servidor")
        ok(porta not in ocupadas_antes,
           "em porta interna própria (%s): nenhuma outra instância a ocupava "
           "antes — inclusive produção e homologação, quando estão no ar" % porta)
        igual(escutas().get(porta), pid_modulo, "e é esse processo que escuta a porta")
        s, _ = a.pedir("POST", "/api/primeiro-acesso", {"senha": SENHA})
        igual(s, 200, "admin de teste criado (senha inventada)")
        s, _ = a.pedir("GET", "/favicon.ico")
        igual(s, 200, "o servidor fala com o próprio módulo (/favicon.ico repassado)")

        secao("4 · Home > Encerrar derruba servidor e módulo")
        pid_servidor = a.servidor()
        s, _ = a.pedir("POST", "/api/encerrar", {})
        igual(s, 200, "POST /api/encerrar -> 200")
        ok(espera(lambda: not vivo(pid_servidor), 30), "o servidor terminou")
        ok(espera(lambda: not vivo(pid_modulo), 30), "o NFS-e terminou junto")
        ok(espera(lambda: porta not in escutas(), 15), "e a porta interna %s ficou livre" % porta)
    finally:
        a.limpar()

    # ── 5 · morte súbita do servidor ─────────────────────────────────────
    secao("5 · Servidor morto sem aviso: o módulo morre com ele")
    b = Instancia("queda")
    try:
        ok(b.de_pe(), "instância de teste no ar (porta %d)" % b.porta)
        espera(lambda: b.modulo()[0] is not None, 30)
        porta, pid_modulo = b.modulo()
        pid_servidor = b.servidor()
        ok(pid_modulo is not None and pid_servidor is not None,
           "servidor (%s) e módulo (%s) identificados por PID" % (pid_servidor, pid_modulo))
        ok(pid_servidor != b.proc.pid and pid_servidor in b.arvore(),
           "o servidor é o Python base, filho do lançador da venv")
        ok(encerrar_so_este(pid_servidor),
           "TerminateProcess SÓ no servidor (sem atexit, sem parar_modulo_nfse)")
        ok(espera(lambda: not vivo(pid_servidor), 15), "o servidor morreu")
        ok(espera(lambda: not vivo(pid_modulo), 20),
           "o NFS-e morreu junto — o job fechou com o servidor")
        ok(espera(lambda: porta not in escutas(), 15), "e a porta interna %s ficou livre" % porta)
    finally:
        b.limpar()

    # ── 6 · produção e homologação ───────────────────────────────────────
    secao("6 · Produção e homologação intactas")
    depois_portas = {p: escutas().get(p) for p in PORTAS_VIGIADAS}
    igual(depois_portas, antes_portas,
          "8777, 8790, 8898 e 8791 com os mesmos PIDs de antes")
    igual(foto(REAL), antes_real, "dados da produção iguais")
    igual(foto(HOMOLOGACAO), antes_homolog, "dados da homologação iguais")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
