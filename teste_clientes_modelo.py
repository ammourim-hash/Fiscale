#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O botão «Baixar modelo» — o clique, não só a rota.

    python teste_clientes_modelo.py

POR QUE UM TESTE SÓ DE HTTP NÃO BASTAVA
    `teste_importar_http.py` já provava que `GET /api/clientes/modelo` devolve
    200 com o CSV certo. E o botão não funcionava assim mesmo.

    O defeito não estava na rota: uma string de aspas simples partida em duas
    linhas (`confirm('…?` + quebra real) deixava o `<script>` inteiro sem
    compilar. Nenhuma função da tela chegava a existir — nem `baixarModelo`,
    nem `iniciar`, nem nenhum outro `onclick`. A tela abria, os botões
    apareciam, e clicar não fazia nada e não dizia nada.

    É o tipo de falha que passa por baixo de todo teste de servidor: o servidor
    estava certo o tempo todo.

O QUE ESTA SUÍTE COBRE, E COMO
    1. **Sintaxe** de todo JavaScript embutido em `web/*.html`, pelo parser de
       verdade (`node --check`) quando o Node existe na máquina. É a guarda que
       teria pegado o defeito no minuto em que ele foi escrito.
    2. **Estrutura** do que o clique depende: o botão, o `onclick`, o elemento
       de status, e os estados exigidos.
    3. **Conteúdo** do modelo servido pela rota, numa instância isolada: BOM,
       `;`, cabeçalho e exemplos.

    A prova do clique no navegador está registrada em
    `FISCALE_BASELINE_TESTES.md`: ela precisa de um navegador, e o pacote
    portátil não pode passar a depender de um.
"""
from __future__ import annotations

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


# ── o parser de verdade, quando ele existe ──────────────────────────────────
def _node() -> str:
    return shutil.which("node") or ""


BLOCO_INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def blocos_de(html: str) -> list[str]:
    return [b for b in BLOCO_INLINE.findall(html) if b.strip()]


def erro_de_sintaxe(js: str) -> str:
    """`""` quando compila; a mensagem do parser quando não."""
    node = _node()
    if not node:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "bloco.js"
        f.write_text(js, "utf-8")
        r = subprocess.run([node, "--check", str(f)],
                           capture_output=True, text=True)
        return "" if r.returncode == 0 else (r.stderr or "erro").strip()


# ── instância isolada ───────────────────────────────────────────────────────
def porta_livre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Instancia:
    """Um Fiscale próprio, em porta e pasta próprias. Nada real é tocado."""

    SENHA = "Teste!Modelo#2026"

    def __init__(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="fiscale_modelo_"))
        self.dados = self.raiz / "dados"
        self.dados.mkdir(parents=True)
        self.porta = porta_livre()
        self.proc = None
        self.cookie = ""
        self.log = None

    def subir(self) -> bool:
        env = dict(os.environ)
        env["FISCALE_DADOS"] = str(self.dados)
        env["FISCALE_PORT"] = str(self.porta)
        env["FISCALE_SEM_BANDEJA"] = "1"
        env["FISCALE_SEM_NAVEGADOR"] = "1"
        env["FISCALE_NO_BROWSER"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        self.log = open(self.raiz / "saida.log", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(RAIZ / "fiscale_server.py")],
            cwd=str(RAIZ), env=env, stdin=subprocess.DEVNULL,
            stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(180):
            try:
                self.pedir("GET", "/login.html")
                return True
            except OSError:
                time.sleep(0.5)
        return False

    def derrubar(self):
        """Encerra a ÁRVORE, não só o processo lançado.

        `terminate()` sozinho matava o `fiscale_server`, e o `nfse/runner.py`
        que ele lança — um NETO — continuava vivo segurando uma porta interna.
        Cada execução desta suíte deixava dois processos para trás.

        `encerrar_arvore` mata exatamente os descendentes deste PID, nunca por
        nome nem por porta: o FISCALE do usuário roda o dia inteiro na 8777, e
        uma varredura por nome o derrubaria junto.
        """
        try:
            apoio.encerrar_arvore(self.proc)
        finally:
            self.proc = None
            try:
                self.log.close()
            except Exception:
                pass
            shutil.rmtree(self.raiz, ignore_errors=True)

    def pedir(self, metodo, caminho, corpo=None, tipo=None):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=120)
        cab = {}
        if self.cookie:
            cab["Cookie"] = self.cookie
        if tipo:
            cab["Content-Type"] = tipo
        if corpo is not None:
            cab["Content-Length"] = str(len(corpo))
        c.request(metodo, caminho, body=corpo, headers=cab)
        r = c.getresponse()
        d = r.read()
        h = {k.lower(): v for k, v in r.getheaders()}
        if "set-cookie" in h:
            self.cookie = h["set-cookie"].split(";")[0]
        c.close()
        return r.status, h, d


def main() -> int:
    # ══════════════════════════════════════════════════════════════════════
    secao("Todo JavaScript embutido do projeto compila")
    node = _node()
    if not node:
        ok(True, "Node não está nesta máquina — conferência de sintaxe pulada "
                 "(a estrutural abaixo continua valendo)")
    else:
        arquivos = sorted((RAIZ / "web").glob("*.html"))
        ok(len(arquivos) > 5, f"há {len(arquivos)} telas para conferir")
        quebrados = []
        for p in arquivos:
            for i, js in enumerate(blocos_de(p.read_text("utf-8"))):
                erro = erro_de_sintaxe(js)
                if erro:
                    quebrados.append(f"{p.name} (bloco {i}): "
                                     f"{erro.splitlines()[-1][:80]}")
        igual(quebrados, [], "nenhum bloco inline com erro de sintaxe")

        # A guarda provando que ela pega o defeito que existiu de verdade.
        real = ("function a() {\n"
                "  if (!confirm('Gravar as alterações?\n\n'\n"
                "      + 'Uma cópia é guardada antes.')) return;\n"
                "}\n")
        ok(bool(erro_de_sintaxe(real)),
           "e a conferência REPROVA o defeito real (string partida em duas "
           "linhas), em vez de só passar no que já está certo")

    # ══════════════════════════════════════════════════════════════════════
    secao("A tela de Clientes tem o que o clique precisa")
    html = (RAIZ / "web" / "clientes.html").read_text("utf-8")
    js = "\n".join(blocos_de(html))

    ok('id="btnModelo"' in html, "o botão tem id próprio")
    ok('onclick="baixarModelo(this)"' in html, "e chama `baixarModelo`")
    ok("function baixarModelo" in js,
       "`baixarModelo` é declaração de função — global, como o `onclick` exige")
    ok('id="statusModelo"' in html, "existe um lugar na tela para o recado")
    ok('aria-live' in html, "e ele é anunciado por leitor de tela")

    secao("Os estados que o botão precisa mostrar")
    for estado in ("Preparando modelo…", "Modelo baixado"):
        ok(estado in js, f"o botão diz «{estado}»")
    ok("modelo-empresas-fiscale.csv" in js, "o nome do arquivo está no código")
    ok("btn.disabled = false" in js, "o botão é reabilitado ao final")
    ok("finally" in js, "e o `finally` garante isso mesmo depois de erro")
    ok("sessão expirou" in js, "401 vira uma frase que diz o que fazer")
    ok("não respondeu" in js, "e a queda de rede também tem mensagem")
    ok("vazio" in js, "resposta vazia é tratada como falha, não como sucesso")
    ok("revokeObjectURL" in js, "a URL temporária é revogada")
    ok("setTimeout(() => URL.revokeObjectURL" in js,
       "e a revogação é ADIADA — revogar na mesma volta cancela o download")
    ok(js.index("a.click()") < js.index("URL.revokeObjectURL"),
       "o clique acontece ANTES da revogação")

    secao("Nenhum `alert` no caminho do modelo")
    trecho = js[js.index("function baixarModelo"):]
    trecho = trecho[:trecho.index("\n}")]
    ok("alert(" not in trecho,
       "a falha aparece na tela, não numa caixa que some ao clicar em OK")

    # ══════════════════════════════════════════════════════════════════════
    secao("O modelo servido pela rota, numa instância isolada")
    inst = Instancia()
    try:
        ok(inst.subir(), f"o Fiscale subiu na porta {inst.porta}")

        s, _, _ = inst.pedir("GET", "/api/clientes/modelo")
        igual(s, 401, "sem sessão, o modelo responde 401")

        s, _, _ = inst.pedir("POST", "/api/primeiro-acesso",
                             json.dumps({"senha": inst.SENHA}).encode(),
                             "application/json")
        igual(s, 200, "admin de teste criado nesta instância")

        s, h, corpo = inst.pedir("GET", "/api/clientes/modelo")
        igual(s, 200, "com sessão, o modelo responde 200")
        ok("text/csv" in h.get("content-type", ""), "e o tipo é CSV")
        ok("modelo-empresas-fiscale.csv" in h.get("content-disposition", ""),
           "com o nome do arquivo no cabeçalho")

        ok(corpo.startswith(b"\xef\xbb\xbf"),
           "o CSV começa com BOM — é o que faz o Excel pt-BR abrir certo")
        texto = corpo.decode("utf-8-sig")
        linhas = [l for l in texto.splitlines() if l.strip()]
        ok(";" in linhas[0], "o separador é `;`")
        ok(len(linhas[0].split(";")) > 5,
           f"o cabeçalho tem {len(linhas[0].split(';'))} colunas")
        igual(len(linhas), 3, "cabeçalho + dois exemplos")
        igual(len(linhas) - 1, 2, "e os exemplos são dois: um CNPJ e um CPF")
        ok("/" in linhas[1], "o primeiro exemplo é pessoa jurídica (CNPJ)")
        ok("-" in linhas[2] and "/" not in linhas[2].split(";")[0],
           "o segundo é pessoa física (CPF)")
        ok("\r\n" in texto, "as quebras são CRLF, como o Excel espera")
        ok("Razão Social" in texto, "os acentos sobreviveram ao BOM")

        secao("O modelo não é gerado do cadastro real")
        ok("EXEMPLO" in texto.upper(),
           "as linhas são exemplos declarados, não dados de cliente")
    finally:
        inst.derrubar()

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
