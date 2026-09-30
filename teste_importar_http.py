#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Importar cadastro PELA REDE — o mesmo caminho do navegador.

    python teste_importar_http.py

POR QUE ESTE TESTE EXISTE
    As rotas de importação foram escritas, classificadas em `fiscale_papeis`,
    cobertas por asserções — e nunca funcionaram. Duas vezes.

    Primeiro o proxy do NFS-e as engolia (`/api/clientes` é prefixo repassado)
    e devolvia 404. Depois, ao mover o bloco, ele foi parar DENTRO de
    `if rota.startswith("/api/backup/")`: nenhum caminho de cadastro começa
    assim, e a requisição terminava no "rota desconhecida", 404.

    Os dois defeitos passariam por qualquer teste que chame o módulo, o
    handler ou `_rota_nfse` direto. Só um cliente HTTP de verdade, contra um
    servidor de verdade, os pega — porque só ele percorre o `do_POST` inteiro,
    na ordem em que o navegador o percorre.

O QUE ESTE TESTE **NÃO** FAZ
    Não toca no Fiscale de produção, não usa senha real e não lê o cadastro
    real. Sobe uma instância própria, numa porta própria, com pasta de dados
    própria, e a derruba no fim.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio as apoio                          # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

SENHA = "planilha jabuticaba no telhado"      # inventada para este teste


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
    if a == b:
        ok(True, desc)
    else:
        ok(False, "%s  (obtive %.140r, esperava %.140r)" % (desc, a, b))


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Instancia:
    """Um Fiscale de verdade, só deste teste."""

    def __init__(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="fiscale_http_"))
        self.dados = self.raiz / "dados"
        self.dados.mkdir(parents=True)
        self.porta = porta_livre()
        self.proc = None
        self.cookie = ""

    def subir(self):
        env = dict(os.environ)
        env["FISCALE_DADOS"] = str(self.dados)
        env["FISCALE_PORT"] = str(self.porta)
        env["FISCALE_SEM_BANDEJA"] = "1"
        env["FISCALE_SEM_NAVEGADOR"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        self.log = open(self.raiz / "saida.log", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(RAIZ / "fiscale_server.py")],
            cwd=str(RAIZ), env=env, stdin=subprocess.DEVNULL,
            stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(120):
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
        """Uma requisição HTTP crua. Devolve `(status, cabeçalhos, corpo)`."""
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
        dados = r.read()
        cabecalhos = {k.lower(): v for k, v in r.getheaders()}
        if "set-cookie" in cabecalhos:
            self.cookie = cabecalhos["set-cookie"].split(";")[0]
        c.close()
        return r.status, cabecalhos, dados

    def json(self, metodo, caminho, obj=None):
        corpo = json.dumps(obj or {}).encode("utf-8")
        s, h, d = self.pedir(metodo, caminho, corpo, "application/json")
        try:
            return s, json.loads(d or b"{}")
        except ValueError:
            return s, {"_bruto": d[:200].decode("utf-8", "replace")}


def multipart(campos, arquivo=None):
    """`(corpo, content-type)` — exatamente o que um `FormData` produz."""
    limite = "----fiscale" + uuid.uuid4().hex
    partes = []
    for nome, valor in (campos or {}).items():
        partes.append(
            ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
             % (limite, nome, valor)).encode("utf-8"))
    if arquivo:
        campo, nome_arq, dados = arquivo
        partes.append(
            ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
             "filename=\"%s\"\r\nContent-Type: text/csv\r\n\r\n"
             % (limite, campo, nome_arq)).encode("utf-8"))
        partes.append(dados)
        partes.append(b"\r\n")
    partes.append(("--%s--\r\n" % limite).encode("utf-8"))
    return b"".join(partes), "multipart/form-data; boundary=%s" % limite


def main():
    inst = Instancia()
    try:
        secao("Uma instância isolada, numa porta própria")
        ok(inst.subir(), "o Fiscale subiu na porta %d" % inst.porta)
        ok(not any(inst.dados.glob("state_clientes*")),
           "e a pasta de dados começa sem cadastro nenhum")

        # ── 1 · autenticação com usuário de teste ────────────────────────
        secao("1 · Autenticação com usuário de teste (senha inventada aqui)")
        # O 401 é conferido ANTES de criar o admin: `/api/primeiro-acesso`
        # devolve cookie de sessão, e conferir depois seria conferir já logado.
        s, _, _ = inst.pedir("GET", "/api/clientes/modelo")
        igual(s, 401, "sem sessão, o modelo responde 401")
        s, _, _ = inst.pedir("POST", "/api/clientes/importar/previa", b"{}",
                             "application/json")
        igual(s, 401, "e a prévia também")
        s, r = inst.json("POST", "/api/primeiro-acesso", {"senha": SENHA})
        igual(s, 200, "o admin de teste foi criado")
        inst.cookie = ""            # esquece a sessão que o primeiro acesso deu
        s, r = inst.json("POST", "/api/login",
                         {"usuario": "admin", "senha": SENHA})
        igual(s, 200, "e o login devolve 200")
        ok(inst.cookie.startswith("fiscale_sessao="), "com cookie de sessão")

        # ── 2 · download real do modelo ──────────────────────────────────
        secao("2 · O modelo, baixado por HTTP")
        s, h, modelo = inst.pedir("GET", "/api/clientes/modelo")
        igual(s, 200, "GET /api/clientes/modelo -> 200")
        ok("text/csv" in h.get("content-type", ""),
           "Content-Type: %s" % h.get("content-type"))
        ok("attachment" in h.get("content-disposition", ""),
           "Content-Disposition manda baixar")
        ok("modelo-empresas-fiscale.csv" in h.get("content-disposition", ""),
           "com o nome do arquivo")
        igual(int(h.get("content-length", 0)), len(modelo),
              "e o Content-Length bate com o corpo")
        ok(modelo.startswith(bytes((0xEF, 0xBB, 0xBF))), "o CSV vem com BOM")
        primeira = modelo.decode("utf-8-sig").splitlines()[0]
        ok(";" in primeira, "separado por ponto e vírgula")
        ok(primeira.startswith("CPF/CNPJ"), "e a primeira coluna é CPF/CNPJ")

        # ── 3, 4, 5, 6 · upload multipart do MESMO arquivo ───────────────
        secao("3-6 · O mesmo arquivo, enviado por multipart/form-data")
        corpo, tipo = multipart({}, ("arquivo", "modelo-empresas-fiscale.csv",
                                     modelo))
        s, h, d = inst.pedir("POST", "/api/clientes/importar/previa",
                             corpo, tipo)
        igual(s, 200, "POST /api/clientes/importar/previa -> 200")
        ok("application/json" in h.get("content-type", ""),
           "resposta em JSON")
        previa = json.loads(d)
        ok("erro" not in previa, "sem erro: %s" % previa.get("erro", "(nenhum)"))
        resumo = previa.get("resumo") or {}
        igual(resumo.get("novos"), 2, "dois cadastros novos")
        igual(resumo.get("erros"), 0, "nenhum erro")
        igual(resumo.get("conflitos"), 0, "nenhum conflito")
        nomes = sorted(n["razao_social"] for n in previa["novos"])
        igual(nomes, ["EMPRESA EXEMPLO LTDA", "FULANO DE TAL"],
              "uma pessoa jurídica e uma pessoa física")
        import fiscale_cadastro as cad
        tipos = sorted(cad.tipo_documento(n["cnpj"]) for n in previa["novos"])
        igual(tipos, ["CNPJ", "CPF"], "e os dois tipos de documento")
        ok(previa.get("token"), "a prévia devolve o bilhete")

        # ── 7 · nada gravado antes da confirmação ────────────────────────
        secao("7 · A prévia não gravou nada")
        estado = inst.dados / "state_clientes.json"
        ok(not estado.exists() or not (json.loads(
            estado.read_text("utf-8-sig")).get("clientes") or []),
           "o cadastro continua vazio depois da prévia")
        igual(list(inst.dados.glob("*antes-da-importacao*")), [],
              "e nenhuma cópia de segurança foi criada")

        # ── /aplicar exige prévia válida e vinculada ─────────────────────
        secao("/aplicar sem prévia, com bilhete errado, ou com outro arquivo")
        corpo, tipo = multipart({}, ("arquivo", "m.csv", modelo))
        s, _, d = inst.pedir("POST", "/api/clientes/importar/aplicar",
                             corpo, tipo)
        igual(s, 409, "sem bilhete -> 409")
        ok("prévia é obrigatória" in json.loads(d).get("erro", ""),
           "dizendo que a prévia é obrigatória")

        corpo, tipo = multipart({"token": "bilhete-inventado"},
                                ("arquivo", "m.csv", modelo))
        s, _, d = inst.pedir("POST", "/api/clientes/importar/aplicar",
                             corpo, tipo)
        igual(s, 409, "bilhete inventado -> 409")

        outro = modelo + b"11.222.333/0001-81;OUTRA LTDA;;6920601;;;;Recife;PE;;;;;\r\n"
        corpo, tipo = multipart({"token": previa["token"]},
                                ("arquivo", "m.csv", outro))
        s, _, d = inst.pedir("POST", "/api/clientes/importar/aplicar",
                             corpo, tipo)
        igual(s, 409, "arquivo DIFERENTE do conferido -> 409")
        ok("mudou depois da prévia" in json.loads(d).get("erro", ""),
           "dizendo que o arquivo mudou")
        ok(not estado.exists() or not (json.loads(
            estado.read_text("utf-8-sig")).get("clientes") or []),
           "e nenhuma das três tentativas gravou nada")

        # ── 8 · cancelamento sem resíduos ────────────────────────────────
        secao("8 · Cancelar não deixa resíduo")
        antes = sorted(p.name for p in inst.dados.iterdir())
        corpo, tipo = multipart({}, ("arquivo", "m.csv", modelo))
        inst.pedir("POST", "/api/clientes/importar/previa", corpo, tipo)
        depois = sorted(p.name for p in inst.dados.iterdir())
        igual(depois, antes,
              "conferir e desistir não cria arquivo nenhum na pasta de dados")
        ok(not list(inst.dados.rglob("*.csv"))
           and not list(inst.dados.rglob("*.xlsx")),
           "e a planilha não fica guardada em lugar nenhum")

        # ── aplicar de verdade, com o bilhete certo ──────────────────────
        secao("Com o bilhete certo, grava — e o bilhete serve UMA vez")
        corpo, tipo = multipart({}, ("arquivo", "m.csv", modelo))
        s, _, d = inst.pedir("POST", "/api/clientes/importar/previa",
                             corpo, tipo)
        bilhete = json.loads(d)["token"]
        corpo, tipo = multipart({"token": bilhete}, ("arquivo", "m.csv", modelo))
        s, _, d = inst.pedir("POST", "/api/clientes/importar/aplicar",
                             corpo, tipo)
        igual(s, 200, "com bilhete válido -> 200")
        r = json.loads(d)
        igual(r["aplicado"]["criados"], 2, "criou as duas empresas")
        igual(r["total"], 2, "o cadastro tem duas")
        gravado = json.loads(estado.read_text("utf-8-sig"))["clientes"]
        igual(len(gravado), 2, "e estão no arquivo, em disco")
        ok(any(cad.eh_pessoa_fisica(cad.documento_de(c)) for c in gravado),
           "inclusive a pessoa física")

        corpo, tipo = multipart({"token": bilhete}, ("arquivo", "m.csv", modelo))
        s, _, d = inst.pedir("POST", "/api/clientes/importar/aplicar",
                             corpo, tipo)
        igual(s, 409, "o MESMO bilhete de novo -> 409")
        igual(len(json.loads(estado.read_text("utf-8-sig"))["clientes"]), 2,
              "e o cadastro não duplicou")

        # ── o envio JSON+base64 continua funcionando ─────────────────────
        secao("O formato antigo (JSON + base64) continua aceito")
        import base64
        s, r = inst.json("POST", "/api/clientes/importar/previa",
                         {"nome": "m.csv",
                          "b64": base64.b64encode(modelo).decode()})
        igual(s, 200, "JSON com base64 -> 200")
        igual(r["resumo"]["inalterados"], 2,
              "e reconhece as duas que já estão cadastradas")

        # ── o proxy do NFS-e não engole estas rotas ──────────────────────
        secao("O proxy do NFS-e não engole estas rotas")
        for caminho in ("/api/clientes/importar/previa",
                        "/api/clientes/importar/aplicar"):
            corpo, tipo = multipart({}, ("arquivo", "m.csv", modelo))
            s, _, d = inst.pedir("POST", caminho, corpo, tipo)
            ok(s != 404, "%s não devolve 404 (devolveu %d)" % (caminho, s))
            ok(b"rota desconhecida" not in d,
               "%s não cai no 'rota desconhecida'" % caminho)

        secao("Erros de planilha chegam como erro, não como 404")
        corpo, tipo = multipart({}, ("arquivo", "coisa.exe", b"nao e planilha"))
        s, _, d = inst.pedir("POST", "/api/clientes/importar/previa",
                             corpo, tipo)
        igual(s, 400, "extensão não aceita -> 400")
        ok("xlsx" in json.loads(d).get("erro", ""), "com o motivo dito")
        corpo, tipo = multipart({})
        s, _, d = inst.pedir("POST", "/api/clientes/importar/previa",
                             corpo, tipo)
        igual(s, 400, "multipart sem arquivo -> 400")
        ok("Nenhum arquivo" in json.loads(d).get("erro", ""),
           "dizendo que não veio arquivo")

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
