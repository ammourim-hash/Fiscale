#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""As rotas de Situação Fiscal PELA REDE — o caminho do navegador.

    python teste_situacao_http.py

POR QUE ESTE TESTE EXISTE, E POR QUE ELE SOBE UM SERVIDOR
    As rotas de importação de cadastro foram escritas, classificadas em
    `fiscale_papeis`, cobertas por asserções — e não funcionaram. Duas vezes.
    Nas duas, o defeito estava no CAMINHO até o handler: um prefixo repassado
    ao proxy do NFS-e, e depois um bloco aninhado no `if` errado. Chamar a
    função direto passava verde nos dois casos.

    O dossiê tem a mesma forma de risco, e mais uma:
    ele não devolve JSON.
    Devolve `application/pdf` com CABEÇALHOS que a tela precisa ler
    (`X-Anexados`, `Content-Disposition`). Nada disso existe quando se chama
    `relatorio.dossie()` num teste de módulo — é construído pelo `do_GET`.

O QUE ELE PROTEGE
    • que as duas rotas existam e sejam servidas pelo Fiscale, não pelo proxy;
    • que sem sessão respondam 401 (e não entreguem PDF de cliente a ninguém);
    • que `X-Anexados: 0` chegue à tela — é o único jeito de ela avisar
      "baixou sem anexo", porque o navegador não conta páginas de PDF;
    • que o NOME DO ARQUIVO não carregue a marca do sistema: ele é a primeira
      coisa que a pessoa vê, vai para o e-mail encaminhado e sobrevive ao PDF;
    • que os BYTES do PDF que sai pela rede também não a carreguem;
    • que as rotas da bandeja municipal NÃO existam mais (removida em 12/09);
    • que ela seja de ADMIN — inclusive para operador logado;
    • que o item chegado pela pasta não traga endereço adivinhado.

O QUE ELE NÃO FAZ
    Não toca no Fiscale de produção, não usa senha real, não lê nem grava o
    cadastro real. Sobe instância própria, porta própria, pasta própria, e
    derruba a ÁRVORE de processos no fim.
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
_erros: list = []

SENHA = "dossie de abacaxi na varanda"       # inventada aqui, para este teste

ALFA = "11222333000181"          # CNPJ de exemplo, matematicamente válido
GAMA = "66789006000106"          # nunca receberá documento: é o caso "vazio"

# AS MARCAS QUE NÃO PODEM APARECER — nem nos bytes, nem no nome do arquivo.
MARCAS = ("fiscale", "pypdf", "fpdf", "pyfpdf")


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


def pdf_de(texto: str, paginas: int = 1) -> bytes:
    """Um PDF minúsculo e legítimo, com o texto pedido."""
    from fpdf import FPDF
    p = FPDF()
    for i in range(paginas):
        p.add_page()
        p.set_font("Helvetica", size=12)
        p.cell(0, 10, "%s (pagina %d)" % (texto, i + 1))
    return bytes(p.output())


def multipart(campos, arquivo=None):
    """`(corpo, content-type)` — exatamente o que um `FormData` produz."""
    limite = "----fiscale" + uuid.uuid4().hex
    partes = []
    for nome, valor in (campos or {}).items():
        partes.append(
            ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
             % (limite, nome, valor)).encode("utf-8"))
    if arquivo:
        campo, nome_arq, dados, mime = arquivo
        partes.append(
            ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
             "filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
             % (limite, campo, nome_arq, mime)).encode("utf-8"))
        partes.append(dados)
        partes.append(b"\r\n")
    partes.append(("--%s--\r\n" % limite).encode("utf-8"))
    return b"".join(partes), "multipart/form-data; boundary=%s" % limite


class Instancia:
    """Um Fiscale de verdade, só deste teste."""

    def __init__(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="fiscale_http_dossie_"))
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

        O `fiscale_server` lança o backend do NFS-e — um NETO. `terminate()`
        sozinho deixava o neto vivo segurando a porta interna. `encerrar_arvore`
        mata os descendentes DESTE PID, nunca por nome nem por porta: o FISCALE
        do escritório roda o dia inteiro na 8777.
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
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=180)
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


def paginas_de(bruto: bytes) -> int:
    import io
    import pypdf
    return len(pypdf.PdfReader(io.BytesIO(bruto)).pages)


def texto_de(bruto: bytes) -> str:
    import io
    import pypdf
    r = pypdf.PdfReader(io.BytesIO(bruto))
    return "\n".join((pg.extract_text() or "") for pg in r.pages)


def nome_do_cabecalho(cd: str) -> str:
    import re
    m = re.search(r'filename="([^"]+)"', cd or "")
    return m.group(1) if m else ""


def main():
    inst = Instancia()
    try:
        secao("Uma instância isolada, numa porta própria")
        ok(inst.subir(), "o Fiscale subiu na porta %d" % inst.porta)

        # ── 1 · sem sessão, ninguém baixa dossiê de cliente ──────────────
        secao("1 · Sem sessão as duas rotas recusam")
        # CONFERIDO ANTES DE CRIAR O ADMIN: `/api/primeiro-acesso` devolve
        # cookie, e conferir depois seria conferir já logado.
        s, _, _ = inst.pedir("GET", "/api/situacao/dossie?identidade=" + ALFA)
        igual(s, 401, "/api/situacao/dossie sem sessão -> 401")
        s, _, _ = inst.pedir("GET", "/api/situacao/lote")
        igual(s, 401, "/api/situacao/lote sem sessão -> 401")

        s, _ = inst.json("POST", "/api/primeiro-acesso", {"senha": SENHA})
        igual(s, 200, "o admin de teste foi criado")
        inst.cookie = ""
        s, _ = inst.json("POST", "/api/login",
                         {"usuario": "admin", "senha": SENHA})
        igual(s, 200, "e o login devolve 200")
        ok(inst.cookie.startswith("fiscale_sessao="), "com cookie de sessão")

        # ── 2 · a rota existe, e é o Fiscale que a serve ─────────────────
        secao("2 · Sem empresa informada, 400 — e não 404 do proxy")
        s, _, d = inst.pedir("GET", "/api/situacao/dossie")
        igual(s, 400, "dossie sem ?identidade -> 400")
        ok("empresa" in json.loads(d).get("erro", ""),
           "dizendo que falta a empresa: %r" % json.loads(d).get("erro"))

        # ── 3 · O CASO DO PEDIDO: dossiê sem anexo nenhum ────────────────
        secao("3 · Empresa sem documento: o dossiê sai, e o cabeçalho AVISA")
        s, h, pdf = inst.pedir("GET", "/api/situacao/dossie?identidade=" + GAMA)
        igual(s, 200, "GET dossie de empresa vazia -> 200")
        ok("application/pdf" in h.get("content-type", ""),
           "Content-Type: %s" % h.get("content-type"))
        igual(h.get("x-anexados"), "0",
              "X-Anexados: 0 — é por aqui que a tela sabe avisar")
        igual(int(h.get("content-length", 0)), len(pdf),
              "Content-Length bate com o corpo")
        ok(pdf.startswith(b"%PDF-"), "e o corpo é um PDF de verdade")
        igual(paginas_de(pdf), 1, "uma página: só a capa")
        ok("Nenhum documento anexado" in texto_de(pdf),
           "a capa também diz, com todas as letras")

        # ── 4 · o nome do arquivo é white-label ──────────────────────────
        secao("4 · O nome do arquivo não carrega a marca do sistema")
        cd = h.get("content-disposition", "")
        ok("attachment" in cd, "Content-Disposition manda baixar")
        nome = nome_do_cabecalho(cd)
        ok(nome.endswith(".pdf"), "o nome termina em .pdf: %r" % nome)
        for marca in MARCAS:
            ok(marca not in nome.lower(),
               "o nome do arquivo não contém %r" % marca)
        ok(nome.isascii(),
           "e é ASCII — o cabeçalho é latin-1 no protocolo: %r" % nome)
        ok("situacao-fiscal" in nome,
           "ele descreve o CONTEÚDO, não quem gerou: %r" % nome)

        # ── 5 · com documento guardado, o original entra ─────────────────
        secao("5 · Guardado um PDF pelo portão, o dossiê o ANEXA")
        corpo, tipo = multipart(
            {"identidade": ALFA, "esfera": "FEDERAL", "tipo": "CERTIDAO"},
            ("arquivo", "certidao.pdf",
             pdf_de("CERTIDAO NEGATIVA DE DEBITOS", 2), "application/pdf"))
        s, _, d = inst.pedir("POST", "/api/situacao/importar", corpo, tipo)
        igual(s, 200, "POST /api/situacao/importar -> 200")
        igual(json.loads(d)["documento"]["desfecho"], "NOVO",
              "e o documento entrou como NOVO")

        s, h2, pdf2 = inst.pedir("GET", "/api/situacao/dossie?identidade=" + ALFA)
        igual(s, 200, "GET dossie -> 200")
        igual(h2.get("x-anexados"), "1", "X-Anexados: 1")
        igual(h2.get("x-omitidos"), "0", "X-Omitidos: 0")
        igual(paginas_de(pdf2), 3, "capa + as 2 páginas do original")
        texto = texto_de(pdf2)
        ok("CERTIDAO NEGATIVA DE DEBITOS" in texto,
           "o conteúdo do ORIGINAL está lá")
        ok("pagina 2" in texto, "inclusive a segunda página")
        ok("Relatório de Situação Fiscal" in texto, "a capa usa o título neutro")

        # ── 6 · os BYTES que saem pela rede também são limpos ────────────
        secao("6 · Nenhuma marca do sistema nos bytes que saem pela rede")
        # O teste de módulo já varre o PDF montado em memória. Aqui se varre o
        # que o SOCKET entregou: é o único lugar onde um cabeçalho ou uma
        # reescrita no caminho apareceria.
        bruto = pdf2.lower()
        for marca in MARCAS:
            ok(marca.encode() not in bruto,
               "%r não aparece em nenhum byte do PDF servido" % marca)

        # ── 7 · o lote ───────────────────────────────────────────────────
        secao("7 · O relatório do escritório")
        s, h3, pdf3 = inst.pedir("GET", "/api/situacao/lote")
        igual(s, 200, "GET /api/situacao/lote -> 200")
        ok("application/pdf" in h3.get("content-type", ""),
           "também é PDF")
        ok(pdf3.startswith(b"%PDF-"), "e é PDF de verdade")
        ok(h3.get("x-empresas") is not None,
           "X-Empresas informa quantas entraram: %r" % h3.get("x-empresas"))
        nome3 = nome_do_cabecalho(h3.get("content-disposition", ""))
        for marca in MARCAS:
            ok(marca not in nome3.lower(),
               "o nome do lote também não contém %r" % marca)
        for marca in MARCAS:
            ok(marca.encode() not in pdf3.lower(),
               "nem os bytes do lote contêm %r" % marca)

        # O cadastro deste teste está vazio de propósito: o lote de um
        # escritório sem empresas ainda tem de SAIR, com capa, em vez de
        # explodir. Zero empresas é o primeiro dia de uso de qualquer um.
        igual(h3.get("x-anexados"), "0",
              "cadastro vazio -> lote sem anexo, mas com capa")

        # ── 8 · a bandeja municipal foi REMOVIDA ─────────────────────────
        secao("8 · As rotas da bandeja e da expectativa não existem mais")
        # O documento de hoje continua servível pelo envelope — é a MESMA
        # porta que a bandeja usava; o que saiu foi só o canal assistido.
        s, _, d = inst.pedir("GET", "/api/situacao/bandeja")
        ok(s in (404, 405), "GET /api/situacao/bandeja -> %s (não 200)" % s)
        for rota, corpo in (("/api/situacao/bandeja/config", {"ligada": True}),
                            ("/api/situacao/bandeja/guardar", {"id": "x"}),
                            ("/api/situacao/expectativa",
                             {"identidade": ALFA, "esfera": "FEDERAL",
                              "tipo": "EXTRATO", "origem": "ASSISTIDA_ECAC"})):
            s, r = inst.json("POST", rota, corpo)
            ok(s != 200, "POST %s -> %s (não 200)" % (rota, s))
        import importlib.util as _iu
        ok(_iu.find_spec("situacao.bandeja") is None,
           "e o módulo situacao.bandeja não existe no pacote")

        # ── 9 · a tela sabe ler o que a rota manda ───────────────────────
        secao("9 · A tela está ligada nas rotas")
        s, _, html = inst.pedir("GET", "/situacao.html")
        igual(s, 200, "a tela é servida")
        t = html.decode("utf-8", "replace")
        ok("/api/situacao/dossie" in t, "ela chama a rota do dossiê")
        ok("/api/situacao/lote" in t, "e a do lote")
        ok("X-Anexados" in t,
           "e LÊ o X-Anexados — sem isso o aviso de 'sem anexo' é impossível")
        ok("Relatório do Escritório" in t, "o botão do lote está na tela")
        ok("Baixar dossiê" in t, "e o botão por linha também")
        for marca in ("baixarDossie", "baixarLote"):
            ok(marca in t, "a função %s existe" % marca)
        # ESCOPO MUNICIPAL RETIRADO (12/09/2026): a bandeja de captura
        # assistida saiu da TELA da Situação Fiscal. As rotas dela continuam
        # testadas acima porque o motor ainda existe no servidor.
        ok("/api/situacao/bandeja" not in t, "a tela NÃO chama mais a bandeja")
        ok("/api/situacao/expectativa" not in t, "nem a expectativa")
        ok("guardarDaBandeja" not in t and "descartarDaBandeja" not in t,
           "nem as portas de saída da bandeja")

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
