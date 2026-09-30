#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Importação de XML municipal (NFS-e) e o `/openapi.json` do módulo NFS-e.

    python teste_nfse_importar_xml.py

POR QUE ESTE TESTE EXISTE
    Em 07/09/2026 (commit 4bb89f7) a remoção de `/api/painel/baixar-historico`
    levou junto a classe `ImportarXmlMunicipal`, que ficava logo abaixo. Como o
    `main.py` usa `from __future__ import annotations`, o módulo continuou
    importando sem erro — e dois defeitos passaram nove dias sem ninguém ver:

      1. `POST /api/prefeituras/importar-xml` (Olinda/Tinus) deixou de
         funcionar;
      2. `GET /openapi.json` passou a devolver 500, na produção inclusive —
         e é por ele que se diagnostica "mexeu no backend, reinicie".

    Nenhum teste chamava a rota nem o esquema. Este chama os dois, de três
    jeitos: no próprio processo, no `runner.py` sozinho e através do FISCALE
    inteiro, com login, do jeito que o navegador faz.

O QUE ESTE TESTE **NÃO** FAZ
    Não usa XML fiscal real, certificado real, senha real nem a pasta de dados
    real. Os XML são montados aqui, com CNPJ de exemplo (11.222.333/0001-81).
    Nada vai à prefeitura: a rota só lê o que recebe e grava na pasta da
    própria instância de teste, que é apagada no fim.
"""
from __future__ import annotations

import base64
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio as apoio                          # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

SENHA = "tapioca de goma no varal da serra"      # inventada para este teste
CNPJ = "11222333000181"                            # CNPJ de exemplo
OUTRO = "99888777000170"                           # outra empresa, também inventada
ID_CONTA = "prova-importar-xml"
REAL = Path(os.environ.get("USERPROFILE") or Path.home()) / "Fiscale" / "dados"


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


def nfse(numero: str, emitente: str, competencia: str = "2026-08-15") -> str:
    """NFS-e no layout nacional, mínima e inventada."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00">'
        '<infNFSe Id="NFS2611606%s">' % numero.zfill(40)
        + "<nNFSe>%s</nNFSe><cStat>100</cStat>" % numero
        + "<dhProc>2026-08-16T10:00:00-03:00</dhProc>"
        + "<emit><CNPJ>%s</CNPJ><xNome>EMPRESA DE PROVA LTDA</xNome></emit>" % emitente
        + "<valores><vLiq>100.00</vLiq></valores>"
        + "<DPS><infDPS><dCompet>%s</dCompet>" % competencia
        + "<toma><CNPJ>%s</CNPJ><xNome>TOMADOR DE PROVA</xNome></toma>" % OUTRO
        + "<valores><vServPrest><vServ>100.00</vServ></vServPrest></valores>"
        + "</infDPS></DPS></infNFSe></NFSe>")


def zip_b64(arquivos: dict) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, texto in arquivos.items():
            z.writestr(nome, texto)
    return "data:application/zip;base64," + base64.b64encode(buf.getvalue()).decode()


def foto_real():
    if not REAL.exists():
        return []
    return sorted((p.name, p.stat().st_mtime_ns if p.is_file() else -1)
                  for p in REAL.iterdir()
                  if not p.name.endswith((".log", ".db", ".db-wal", ".db-shm")))


class Http:
    """Cliente HTTP cru com cookie, para uma porta local."""

    def __init__(self, porta):
        self.porta = porta
        self.cookie = ""

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
        dados = r.read()
        h = {k.lower(): v for k, v in r.getheaders()}
        if "set-cookie" in h:
            self.cookie = h["set-cookie"].split(";")[0]
        c.close()
        return r.status, dados

    def json(self, metodo, caminho, obj=None, bruto=None):
        corpo = bruto if bruto is not None else json.dumps(obj or {}).encode("utf-8")
        s, d = self.pedir(metodo, caminho, corpo, "application/json")
        try:
            return s, json.loads(d or b"{}")
        except ValueError:
            return s, {"_bruto": d[:200].decode("utf-8", "replace")}

    def espera(self, caminho, tentativas=240):
        for _ in range(tentativas):
            try:
                self.pedir("GET", caminho)
                return True
            except OSError:
                time.sleep(0.5)
        return False


def ambiente(dados: Path, porta: int) -> dict:
    env = dict(os.environ)
    env.update({"FISCALE_DADOS": str(dados), "FISCALE_PORT": str(porta),
                "FISCALE_SEM_BANDEJA": "1", "FISCALE_NO_BROWSER": "1",
                "FISCALE_SEM_ATALHO": "1", "FISCALE_TESTE_PROIBIR_RAIZ_REAL": "1",
                "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def main() -> int:
    foto_antes = foto_real()

    # ── 1 · o código ─────────────────────────────────────────────────────
    secao("1 · A classe existe onde a rota a usa")
    fonte = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
    pos_classe = fonte.find("\nclass ImportarXmlMunicipal(BaseModel):")
    pos_rota = fonte.find('@app.post("/api/prefeituras/importar-xml")')
    ok(pos_classe > 0, "main.py define `class ImportarXmlMunicipal(BaseModel)`")
    ok(0 < pos_classe < pos_rota, "e define ANTES da rota que a usa")
    ok("def prefeituras_importar_xml(req: ImportarXmlMunicipal)" in fonte,
       "a rota recebe exatamente esse tipo")
    ok("main.py.bak" not in fonte and "import main_bak" not in fonte,
       "nada importa o main.py.bak")

    # ── 2 · no próprio processo ──────────────────────────────────────────
    secao("2 · O esquema OpenAPI monta, no próprio processo")
    with apoio.raiz_temporaria("nfse_openapi_") as raiz:
        sys.path.insert(0, str(RAIZ / "nfse" / "backend"))
        import main as nfse_main                    # noqa: E402
        igual(Path(nfse_main.DADOS_DIR).resolve(), raiz.resolve(),
              "o módulo NFS-e importado usa a pasta temporária")
        esquema = None
        try:
            esquema = nfse_main.app.openapi()
        except Exception as e:                      # o defeito de 07/09
            ok(False, "app.openapi() estourou: %s" % type(e).__name__)
        ok(esquema is not None, "app.openapi() não estoura")
        if esquema:
            op = esquema["paths"].get("/api/prefeituras/importar-xml", {}).get("post", {})
            ref = (op.get("requestBody", {}).get("content", {})
                   .get("application/json", {}).get("schema", {}).get("$ref", ""))
            igual(ref, "#/components/schemas/ImportarXmlMunicipal",
                  "a rota aparece no esquema com o corpo certo")
            modelo = esquema["components"]["schemas"].get("ImportarXmlMunicipal", {})
            igual(modelo.get("required"), ["id"], "`id` é obrigatório")
            igual(sorted(modelo.get("properties", {})), ["arquivos", "id"],
                  "e os campos são `id` e `arquivos`")
            anotacoes = [(r.path, r.endpoint.__annotations__) for r in nfse_main.app.routes
                         if hasattr(r, "endpoint")]
            soltas = [(p, a) for p, a in anotacoes for v in a.values()
                      if isinstance(v, str) and v.isidentifier() and v[0].isupper()
                      and not hasattr(nfse_main, v)]
            igual(soltas, [], "nenhuma rota anota um tipo que não existe no módulo")

    # ── 3 · o runner sozinho (como a produção sobe o módulo) ─────────────
    secao("3 · /openapi.json pelo runner.py, em porta própria")
    caixa = Path(tempfile.mkdtemp(prefix="nfse_runner_"))
    porta_runner = porta_livre()
    log_runner = open(caixa / "runner.log", "wb")
    runner = subprocess.Popen(
        [sys.executable, str(RAIZ / "nfse" / "runner.py"), str(porta_runner)],
        cwd=str(RAIZ / "nfse"), env=ambiente(caixa / "dados", porta_runner),
        stdin=subprocess.DEVNULL, stdout=log_runner, stderr=subprocess.STDOUT)
    try:
        cli = Http(porta_runner)
        ok(cli.espera("/api/certificados"), "o runner subiu na porta %d" % porta_runner)
        s, d = cli.pedir("GET", "/openapi.json")
        igual(s, 200, "GET /openapi.json -> 200 (era 500)")
        try:
            paths = json.loads(d).get("paths", {})
        except ValueError:
            paths = {}
        ok("/api/prefeituras/importar-xml" in paths, "e o esquema lista a rota de importação")
        s, _ = cli.pedir("GET", "/docs")
        igual(s, 200, "GET /docs -> 200")
    finally:
        apoio.encerrar_arvore(runner)
        log_runner.close()
        shutil.rmtree(caixa, ignore_errors=True)

    # ── 4 · o FISCALE inteiro, com login ─────────────────────────────────
    secao("4 · A rota pelo FISCALE, como o navegador chama")
    inst = Path(tempfile.mkdtemp(prefix="nfse_importar_"))
    dados = inst / "dados"
    dados.mkdir()
    (dados / "certificados.json").write_text(json.dumps([
        {"id": ID_CONTA, "cnpj": CNPJ, "nome": "EMPRESA DE PROVA LTDA",
         "caminho": "certs/nao-existe.pfx"}], ensure_ascii=False), encoding="utf-8")
    porta = porta_livre()
    log = open(inst / "saida.log", "wb")
    proc = subprocess.Popen([sys.executable, str(RAIZ / "fiscale_server.py")],
                            cwd=str(RAIZ), env=ambiente(dados, porta),
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    rota = "/api/prefeituras/importar-xml"
    try:
        cli = Http(porta)
        ok(cli.espera("/login.html"), "o FISCALE de teste subiu na porta %d" % porta)

        s, _ = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": []})
        igual(s, 401, "sem sessão: 401")
        s, _ = cli.json("POST", "/api/primeiro-acesso", {"senha": SENHA})
        igual(s, 200, "admin de teste criado (senha inventada)")
        cli.cookie = ""
        s, _ = cli.json("POST", "/api/login", {"usuario": "admin", "senha": SENHA})
        igual(s, 200, "login de teste")

        secao("4a · XML válido")
        s, r = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": [
            {"nome": "nota-1.xml", "xml": nfse("1", CNPJ)}]})
        igual(s, 200, "importar um XML da empresa -> 200")
        igual((r.get("lidos"), r.get("importadas"), r.get("invalidos")), (1, 1, 0),
              "lidos 1, importadas 1, inválidos 0")
        igual(r.get("competencias"), {"2026-08": 1}, "competência 2026-08 contada")
        gravados = sorted(p.name for p in (dados / CNPJ / "xmls").glob("*.xml"))
        igual(gravados, ["nfse-nota-1.xml"], "a nota foi para <dados>/<cnpj>/xmls")

        s, r = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": [
            {"nome": "nota-1.xml", "xml": nfse("1", CNPJ)}]})
        igual((s, r.get("importadas"), r.get("repetidas")), (200, 0, 1),
              "o mesmo arquivo de novo: repetida, não duplica")

        s, r = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": [
            {"nome": "lote.zip", "zip": zip_b64({"a/nota-2.xml": nfse("2", CNPJ),
                                                 "leia-me.txt": "ignorar"})}]})
        igual((s, r.get("importadas")), (200, 1), "um .zip com XML dentro também importa")

        secao("4b · Erros de XML")
        s, r = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": [
            {"nome": "outra.xml", "xml": nfse("3", OUTRO)}]})
        igual((s, r.get("importadas"), r.get("de_outra_empresa")), (200, 0, 1),
              "XML de outra empresa: recusado e contado à parte")
        s, r = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": [
            {"nome": "quebrado.xml", "xml": "<NFSe><infNFSe>"}]})
        igual((s, r.get("importadas"), r.get("invalidos")), (200, 0, 1),
              "XML malformado: inválido, sem estourar")
        s, r = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": [
            {"nome": "boleto.xml", "xml": "<boleto><valor>1</valor></boleto>"}]})
        igual((s, r.get("invalidos")), (200, 1), "XML que não é NFS-e: inválido")
        s, r = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": [
            {"nome": "falso.zip", "zip": base64.b64encode(b"isto nao e zip").decode()}]})
        igual((s, r.get("invalidos"), r.get("importadas")), (200, 1, 0),
              ".zip corrompido: inválido, sem estourar")

        secao("4c · Arquivo ausente e pedido inválido")
        s, r = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": []})
        igual(s, 400, "nenhum arquivo: 400")
        ok("Nenhum arquivo" in json.dumps(r, ensure_ascii=False), "com a mensagem da tela")
        s, _ = cli.json("POST", rota, {"id": ID_CONTA, "arquivos": [{"nome": "vazio.xml"}]})
        igual(s, 400, "item sem conteúdo (arquivo ausente): 400")
        s, _ = cli.json("POST", rota, {"id": ID_CONTA})
        igual(s, 400, "sem a lista de arquivos: 400")
        s, _ = cli.json("POST", rota, {"arquivos": [{"nome": "x.xml", "xml": nfse("9", CNPJ)}]})
        igual(s, 422, "sem `id`: 422 (validação do modelo)")
        s, _ = cli.json("POST", rota, {"id": "nao-existe", "arquivos": [
            {"nome": "x.xml", "xml": nfse("9", CNPJ)}]})
        igual(s, 404, "empresa inexistente: 404")
        s, _ = cli.json("POST", rota, bruto=b"isto nao e json")
        ok(s in (400, 422), "corpo que não é JSON: recusado (%s)" % s)

        gravados = sorted(p.name for p in (dados / CNPJ / "xmls").glob("*.xml"))
        igual(gravados, ["nfse-nota-1.xml", "nfse-nota-2.xml"],
              "no fim, só as duas notas válidas da empresa foram gravadas")
    finally:
        apoio.encerrar_arvore(proc)
        log.close()
        shutil.rmtree(inst, ignore_errors=True)

    # ── 5 · a produção ───────────────────────────────────────────────────
    secao("5 · A produção não foi tocada")
    igual(foto_real(), foto_antes,
          "a pasta de dados real está igual (fora log/db, que a produção escreve)")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
