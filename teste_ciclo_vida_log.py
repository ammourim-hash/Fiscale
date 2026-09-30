#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ciclo de vida no `fiscale.log` — quando subiu, quando caiu e por quê.

    python teste_ciclo_vida_log.py

O QUE SE PROVA
    Que o log passou a responder sozinho a pergunta que, até ontem, só o
    Visualizador de Eventos do Windows respondia: a que horas este servidor
    subiu, em que porta, com que PID, se o módulo NFS-e entrou no Job Object,
    e por que a execução anterior terminou.

    Que o motivo do encerramento **não é inventado**. "Encerrar" pela tela
    diz `ENCERRAR_TELA`; morte externa não deixa registro na hora e é
    reconhecida na subida seguinte como `FIM_DA_SESSAO_OU_TERMINACAO_EXTERNA`;
    e onde não dá para afirmar, o registro diz `MOTIVO_NAO_DETERMINADO`.

    Que nada de segredo entra no log: a senha usada para criar o admin desta
    instância de teste é procurada no arquivo inteiro e não pode estar lá.

    Tudo em raiz temporária e porta própria. A produção não é tocada, e a
    queda provocada aqui é a da instância DESTE teste, pelo PID dela.
"""
from __future__ import annotations

import io as _io
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

import teste_apoio as apoio                              # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

# O mesmo interpretador, na variante sem console.
_PYTHONW = str(Path(sys.executable).with_name("pythonw.exe"))
if not os.path.exists(_PYTHONW):
    _PYTHONW = sys.executable

SENHA = "Ciclo!DeVida#2026"
# [ciclo] dd/mm/aaaa hh:mm:ss EVENTO campo=valor ...
LINHA = re.compile(r"^\[ciclo\] (\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}) ([A-Z_]+)(.*)$")


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
        print("  FALHOU   " + desc)


def igual(a, b, desc):
    ok(a == b, "%s (%r == %r)" % (desc, a, b))


def porta_livre() -> int:
    """Porta que aceita bind em 0.0.0.0 — que é como o servidor sobe.

    Pedir porta efêmera ao sistema (bind na 0) devolve algo na faixa 49xxx, e
    boa parte dela está RESERVADA no Windows: o servidor levava
    `WinError 10013` e nem chegava a registrar o início. Procurar numa faixa
    alta e livre evita medir o erro errado.
    """
    for porta in range(8810, 8890):
        try:
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("0.0.0.0", porta))
            s.close()
            return porta
        except OSError:
            continue
    raise RuntimeError("nenhuma porta livre entre 8810 e 8890")


class Instancia:
    """Um FISCALE só deste teste: pasta temporária, porta própria, sem bandeja."""

    def __init__(self, raiz: Path, porta: int):
        self.dados = raiz
        self.porta = porta
        self.proc = None
        self.cookie = ""          # `/api/encerrar` fica DEPOIS do portão de
        # login: sem guardar o cookie, o teste levava 401 e o servidor
        # continuava no ar — e o teste culpava o registro.

    def subir(self, espera=180) -> bool:
        env = dict(os.environ)
        env["FISCALE_DADOS"] = str(self.dados)
        env["FISCALE_PORT"] = str(self.porta)
        env["FISCALE_SEM_BANDEJA"] = "1"
        env["FISCALE_NO_BROWSER"] = "1"
        env["FISCALE_SEM_ATALHO"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # `pythonw` E SEM CONSOLE, como na produção. Os dois detalhes
        # importam: só quando `sys.stdout` é None o servidor redireciona tudo
        # para o `fiscale.log`, que é o arquivo sob teste. Redirecionar para
        # DEVNULL daria um stdout VÁLIDO, os prints iriam para o nada e este
        # teste mediria o lugar errado — foi o que aconteceu na primeira
        # tentativa.
        self.proc = subprocess.Popen(
            [_PYTHONW, str(RAIZ / "fiscale_server.py")],
            cwd=str(RAIZ), env=env, close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        for _ in range(espera):
            if self.responde():
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.5)
        return False

    def responde(self) -> bool:
        with socket.socket() as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", self.porta)) == 0

    def pedir(self, metodo, caminho, corpo=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=60)
        dados = json.dumps(corpo).encode() if corpo is not None else None
        cab = {}
        if self.cookie:
            cab["Cookie"] = self.cookie
        if dados is not None:
            cab["Content-Type"] = "application/json"
            cab["Content-Length"] = str(len(dados))
        c.request(metodo, caminho, body=dados, headers=cab)
        r = c.getresponse()
        corpo_r = r.read()
        h = {k.lower(): v for k, v in r.getheaders()}
        if "set-cookie" in h:
            self.cookie = h["set-cookie"].split(";")[0]
        c.close()
        return r.status, h, corpo_r

    def matar_abrupto(self):
        """Mata a ÁRVORE desta instância, pelo PID dela — nunca por nome."""
        apoio.encerrar_arvore(self.proc)
        self.proc = None

    def log(self) -> str:
        p = self.dados / "fiscale.log"
        return _io.open(p, encoding="utf-8", errors="replace").read() if p.exists() else ""

    def ciclos(self) -> list[tuple]:
        saida = []
        for linha in self.log().splitlines():
            m = LINHA.match(linha.strip())
            if m:
                campos = dict(re.findall(r"(\w+)=([^\s]+)", m.group(3)))
                saida.append((m.group(1), m.group(2), campos, linha.strip()))
        return saida

    def esperar_evento(self, evento, tentativas=40):
        for _ in range(tentativas):
            if any(e == evento for _, e, _, _ in self.ciclos()):
                return True
            time.sleep(0.25)
        return False


def main() -> int:
    raiz = Path(tempfile.mkdtemp(prefix="fiscale_ciclo_"))
    dados = raiz / "dados"
    dados.mkdir(parents=True)
    apoio.exigir_raiz_temporaria(dados)
    porta = porta_livre()
    inst = Instancia(dados, porta)

    try:
        # ══════════════════════════════════════════════════════════════
        secao("1. Início: data, hora, porta, PID, pasta de dados")
        # ══════════════════════════════════════════════════════════════
        ok(inst.subir(), "instância de teste no ar na porta %d" % porta)
        ok(inst.esperar_evento("INICIO"), "o log registrou o INICIO")
        ciclos = inst.ciclos()
        ok(ciclos, "há linhas de ciclo de vida (%d)" % len(ciclos))
        for quando, _evento, _campos, _linha in ciclos:
            pass
        ok(all(re.match(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}$", c[0]) for c in ciclos),
           "toda linha de ciclo tem data e hora local")

        inicio = [c for c in ciclos if c[1] == "INICIO"][0]
        campos = inicio[2]
        igual(campos.get("porta"), str(porta), "o INICIO diz a porta")
        ok(campos.get("pid", "").isdigit(), "o INICIO diz o PID (%s)" % campos.get("pid"))
        ok(str(dados) in inicio[3], "o INICIO diz o caminho de dados")
        ok(campos.get("bandeja") == "nao", "e diz que esta instância subiu sem bandeja")

        secao("2. Módulo NFS-e e Job Object")
        eventos = [c[1] for c in inst.ciclos()]
        ok("NFSE_ATIVO" in eventos or "NFSE_INDISPONIVEL" in eventos,
           "o resultado da subida do NFS-e foi registrado")
        if "NFSE_ATIVO" in eventos:
            ativo = [c for c in inst.ciclos() if c[1] == "NFSE_ATIVO"][0]
            ok(ativo[2].get("pid_lancador", "").isdigit(),
               "com o PID do lançador (%s)" % ativo[2].get("pid_lancador"))
            ok(ativo[2].get("porta", "").isdigit(),
               "e a porta interna (%s)" % ativo[2].get("porta"))
        job = [c for c in inst.ciclos() if c[1] == "NFSE_JOB"]
        ok(job, "o estado do Job Object foi registrado (%d linha[s])" % len(job))
        if job:
            ok(job[0][2].get("resultado") in ("VINCULADO", "NAO_VINCULADO"),
               "só o estado do vínculo: %s" % job[0][2].get("resultado"))
            ok("handle" not in job[0][3].lower() and "0x" not in job[0][3],
               "sem handle nem endereço no registro")

        secao("3. Marcador da execução em curso")
        marcador = dados / ".execucao-atual.json"
        ok(marcador.exists(), "o marcador existe enquanto o servidor roda")
        m = json.loads(_io.open(marcador, encoding="utf-8").read())
        ok(set(m) == {"pid", "porta", "inicio"},
           "e guarda só PID, porta e horário (%s)" % sorted(m))

        secao("4. Encerrar pela TELA registra ENCERRAR_TELA")
        st, _, _ = inst.pedir("POST", "/api/primeiro-acesso", {"senha": SENHA})
        igual(st, 200, "admin criado nesta instância de teste")
        try:
            st, _, _ = inst.pedir("POST", "/api/encerrar", {})
            igual(st, 200, "a tela pediu o encerramento e foi aceita")
        except Exception as e:
            ok(False, "o pedido de encerramento falhou: %s" % e.__class__.__name__)
        for _ in range(40):
            if not inst.responde():
                break
            time.sleep(0.25)
        ok(not inst.responde(), "o servidor encerrou")
        fim = [c for c in inst.ciclos() if c[1] == "ENCERRAMENTO"]
        ok(fim, "o encerramento foi registrado")
        if fim:
            igual(fim[-1][2].get("motivo"), "ENCERRAR_TELA", "com o motivo certo")
            ok(fim[-1][2].get("pid", "").isdigit(), "com o PID")
        ok(not marcador.exists(), "e o marcador foi apagado no encerramento limpo")
        parados = [c for c in inst.ciclos() if c[1] == "NFSE_PARADO"]
        ok(parados, "a parada do módulo NFS-e também foi registrada")
        if parados:
            ok(parados[-1][2].get("resultado") in
               ("ENCERRADO", "ENCERRADO_A_FORCA", "JA_PARADO"),
               "com o resultado (%s)" % parados[-1][2].get("resultado"))

        secao("5. Morte externa: reconhecida na subida seguinte, sem inventar causa")
        inst2 = Instancia(dados, porta)
        ok(inst2.subir(), "segunda execução no ar")
        ok(inst2.esperar_evento("INICIO"), "com novo INICIO registrado")
        antes = len([c for c in inst2.ciclos() if c[1] == "ENCERRAMENTO_ANTERIOR"])
        inst2.matar_abrupto()          # queda da instância DESTE teste, por PID
        time.sleep(1.5)
        ok((dados / ".execucao-atual.json").exists(),
           "morte externa deixa o marcador para trás (é a evidência)")

        inst3 = Instancia(dados, porta)
        ok(inst3.subir(), "terceira execução no ar")
        ok(inst3.esperar_evento("ENCERRAMENTO_ANTERIOR"),
           "a subida seguinte relata a execução anterior")
        anteriores = [c for c in inst3.ciclos() if c[1] == "ENCERRAMENTO_ANTERIOR"]
        ok(len(anteriores) > antes, "um relato novo foi acrescentado")
        ultimo = anteriores[-1]
        igual(ultimo[2].get("motivo"), "FIM_DA_SESSAO_OU_TERMINACAO_EXTERNA",
              "com o motivo que descreve as duas possibilidades, sem escolher uma")
        ok(ultimo[2].get("pid", "").isdigit(), "e com o PID da execução que morreu")
        ok("provavelmente" not in ultimo[3].lower() and "crash" not in ultimo[3].lower(),
           "sem palpite sobre a causa")

        secao("6. Nenhum segredo no log")
        texto = inst3.log()
        ok(SENHA not in texto, "a senha do admin de teste não aparece no log")
        for proibido in ("senha=", "frase", "token=", "pfx", "hash=", "sal="):
            ok(proibido not in texto.lower(),
               "nada de %r nas linhas do log" % proibido)
        ok(str(dados) in texto, "o caminho de dados aparece (é informação, não segredo)")

        secao("7. Encerramento sem motivo declarado vira MOTIVO_NAO_DETERMINADO")
        fonte = _io.open(RAIZ / "fiscale_server.py", encoding="utf-8").read()
        ok('atexit.register(lambda: registrar_encerramento(MOTIVO_DESCONHECIDO' in fonte,
           "a saída sem motivo declarado cai em MOTIVO_NAO_DETERMINADO")
        ok('registrar_encerramento(MOTIVO_BANDEJA' in fonte,
           "a bandeja declara ENCERRAR_BANDEJA (não há como clicar nela num teste)")
        ok('registrar_encerramento(MOTIVO_TELA' in fonte,
           "e a tela declara ENCERRAR_TELA")
        ok('MOTIVO_DESCONHECIDO = "MOTIVO_NAO_DETERMINADO"' in fonte,
           "o vocabulário de motivos está fechado no código")
        ok('".execucao-atual.json"' in
           _io.open(RAIZ / "fiscale_backup.py", encoding="utf-8").read(),
           "e o marcador de execução fica FORA do backup")

        inst3.matar_abrupto()
    finally:
        for i in (inst,):
            try:
                if i.proc and i.proc.poll() is None:
                    i.matar_abrupto()
            except Exception:
                pass
        shutil.rmtree(raiz, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
