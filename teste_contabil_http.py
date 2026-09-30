#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O Contábil PELA REDE — o caminho que o navegador faz.

    python teste_contabil_http.py

POR QUE ESTE TESTE SOBE UM SERVIDOR
    Rotas de importação já foram escritas, testadas por função e mortas no
    caminho até o handler (prefixo repassado ao proxy do NFS-e, `if` errado).
    Aqui o pedido percorre o `fiscale_server.py` de verdade: sessão, papel,
    CSRF, multipart, e a resposta binária do extrato original.

O QUE ELE PROTEGE
    • sem sessão, nada (401) — nem leitura;
    • escrita sem o token CSRF da sessão é recusada (403) e não cria nada;
    • o extrato sobe por multipart e cada movimento aparece na busca;
    • o original baixado pela rede é o arquivo enviado, byte a byte;
    • a empresa B não vê nada da empresa A, nem pedindo o id direto;
    • empresa fora do cadastro de Clientes é 404;
    • a tela do Contábil é servida e está na barra de módulos;
    • as rotas são do operador (não exigem admin) — e continuam atrás do login.

NÃO TOCA O FISCALE DE PRODUÇÃO. Instância própria, porta própria, pasta
própria; derruba a ÁRVORE de processos pelo PID no fim.
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
import teste_fixturas_fiscal as fx                   # noqa: E402
import fiscale_papeis                                 # noqa: E402

_ok = _falhas = 0
_erros: list = []

SENHA = "livro razao de cabeceira"          # inventada aqui, para este teste
SENHA_BIA = "conciliacao de quinta a tarde"  # idem, para a operadora do teste
A = fx.PRESTADOR
B = fx.OUTRO

OFX = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nCHARSET:1252\n\n<OFX>\n"
       "<BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL\n"
       "<BANKACCTFROM><BANKID>341<BRANCHID>1234<ACCTID>11111-1</BANKACCTFROM>\n"
       "<BANKTRANLIST>\n"
       "<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260105<TRNAMT>5000.00<FITID>H1"
       "<MEMO>PIX RECEBIDO ABC LTDA</STMTTRN>\n"
       "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260106<TRNAMT>-49.90<FITID>H2"
       "<MEMO>TARIFA MANUTENÇÃO CONTA</STMTTRN>\n"
       "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>\r\n"
       ).encode("cp1252")


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


def multipart(campos, arquivo):
    limite = "----fiscale" + uuid.uuid4().hex
    partes = []
    for nome, valor in campos.items():
        partes.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\""
                       "\r\n\r\n%s\r\n" % (limite, nome, valor)).encode("utf-8"))
    campo, nome_arq, dados = arquivo
    partes.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
                   "filename=\"%s\"\r\nContent-Type: application/octet-stream"
                   "\r\n\r\n" % (limite, campo, nome_arq)).encode("utf-8"))
    partes += [dados, b"\r\n", ("--%s--\r\n" % limite).encode("utf-8")]
    return b"".join(partes), "multipart/form-data; boundary=%s" % limite


class Instancia:
    def __init__(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="fiscale_http_contabil_"))
        self.dados = self.raiz / "dados"
        self.dados.mkdir(parents=True)
        self.porta = porta_livre()
        self.proc = None
        self.cookie = ""
        self.csrf = ""

    def subir(self):
        # O cadastro de Clientes é o universo de empresas. Duas inventadas.
        (self.dados / "state_clientes.json").write_text(json.dumps({
            "clientes": [{"cnpj": A, "nome": "EMPRESA A LTDA"},
                         {"cnpj": B, "nome": "EMPRESA B LTDA"}]}),
            encoding="utf-8")
        env = dict(os.environ)
        env.update({"FISCALE_DADOS": str(self.dados),
                    "FISCALE_PORT": str(self.porta),
                    "FISCALE_SEM_BANDEJA": "1", "FISCALE_SEM_NAVEGADOR": "1",
                    "FISCALE_NO_BROWSER": "1", "PYTHONIOENCODING": "utf-8"})
        self.log = open(self.raiz / "saida.log", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(RAIZ / "fiscale_server.py")], cwd=str(RAIZ),
            env=env, stdin=subprocess.DEVNULL, stdout=self.log,
            stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                self.pedir("GET", "/login.html")
                return True
            except OSError:
                time.sleep(0.5)
        return False

    def derrubar(self):
        try:
            apoio.encerrar_arvore(self.proc)
        finally:
            self.proc = None
            try:
                self.log.close()
            except Exception:
                pass
            shutil.rmtree(self.raiz, ignore_errors=True)

    def pedir(self, metodo, caminho, corpo=None, tipo=None, csrf=True):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=120)
        cab = {}
        if self.cookie:
            cab["Cookie"] = self.cookie
        if tipo:
            cab["Content-Type"] = tipo
        if csrf and self.csrf and metodo == "POST":
            cab["X-Fiscale-Csrf"] = self.csrf
        if corpo is not None:
            cab["Content-Length"] = str(len(corpo))
        c.request(metodo, caminho, body=corpo, headers=cab)
        r = c.getresponse()
        dados = r.read()
        h = {k.lower(): v for k, v in r.getheaders()}
        if "set-cookie" in h:
            self.cookie = h["set-cookie"].split(";")[0]
        c.close()
        return r.status, h, dados

    def json(self, metodo, caminho, obj=None, csrf=True):
        corpo = json.dumps(obj or {}).encode("utf-8") if metodo == "POST" else None
        s, h, d = self.pedir(metodo, caminho, corpo,
                             "application/json" if corpo is not None else None,
                             csrf=csrf)
        try:
            return s, json.loads(d or b"{}")
        except ValueError:
            return s, {"_bruto": d[:200].decode("utf-8", "replace")}


def main():
    inst = Instancia()
    try:
        secao("Uma instância isolada, numa porta própria")
        ok(inst.porta != 8777, "a porta %d não é a do escritório" % inst.porta)
        ok(inst.subir(), "o Fiscale subiu na porta %d" % inst.porta)

        secao("1 · Sem sessão, nada")
        s, _ = inst.json("GET", "/api/contabil/visao?empresa=" + A)
        igual(s, 401, "GET /api/contabil/visao sem sessão → 401")
        s, _ = inst.json("POST", "/api/contabil/lancamentos", {"empresa": A})
        igual(s, 401, "POST /api/contabil/lancamentos sem sessão → 401")

        s, _ = inst.json("POST", "/api/primeiro-acesso", {"senha": SENHA})
        igual(s, 200, "o admin de teste foi criado")
        inst.cookie = ""
        s, _ = inst.json("POST", "/api/login", {"usuario": "admin", "senha": SENHA})
        igual(s, 200, "login")

        secao("2 · O Contábil é uma APLICAÇÃO, não um módulo do FISCALE")
        s, h, d = inst.pedir("GET", "/contabil.html")
        igual(s, 200, "/contabil.html é servida")
        pagina = d.decode("utf-8")
        ok("fiscale-nav.js" not in pagina,
           "a tela NÃO carrega a barra de módulos do FISCALE")
        ok("Em desenvolvimento" in pagina, "e traz o aviso de 'Em desenvolvimento'")
        ok("Abrir empresa" in pagina and 'id="telaEmpresa"' in pagina,
           "a entrada da aplicação é a escolha da empresa")
        ok("Trocar empresa" in pagina, "com o caminho para trocar de empresa")
        ok('href="central.html#aplicacoes"' in pagina,
           "e a volta para Aplicações")
        s, h, d = inst.pedir("GET", "/fiscale-nav.js")
        ok(b"contabil.html" not in d,
           "o Contábil saiu da barra de módulos do FISCALE")
        s, h, d = inst.pedir("GET", "/central.html")
        central = d.decode("utf-8")
        ok('href="contabil.html"' in central and "Escrituração e conciliação" in central,
           "a Central de Aplicações oferece o cartão Contábil")
        ok('href="#administracao"' in central and 'id="vistaAdmin"' in central,
           "e o cartão Administração leva à vista de administração da plataforma")

        secao("2.1 · Nada de administração dentro do Contábil")
        for alvo in ("usuarios.html", "backup.html", "seguranca_acessos.html",
                     "/api/usuarios", "/api/auditoria", "/api/backup"):
            ok(alvo not in pagina, "a tela do Contábil não cita %s" % alvo)
        # As funções de administração não estão nem no documento de quem não é
        # admin: a lista da Central é montada pelo script, sob `if(q.admin)`.
        ok('href="usuarios.html"' not in central and 'href="backup.html"' not in central,
           "a Central não traz link estático para página de administração")
        ok("if(q.admin)" in central and "montarAdministracao()" in central,
           "a lista de administração só é montada quando o servidor diz que é admin")
        s, voc = inst.json("GET", "/api/contabil/vocabulario")
        igual((s, len(voc.get("modulos", []))), (200, 13),
              "vocabulário: 13 áreas de navegação")
        igual(voc.get("formatos_extrato"), ["OFX", "CSV"],
              "formatos lidos: OFX e CSV")

        secao("3 · Escrita sem o token CSRF é recusada e não cria nada")
        s, r = inst.json("POST", "/api/contabil/plano/conta",
                         {"empresa": A, "codigo": "1", "descricao": "ATIVO",
                          "tipo": "SINTETICA", "natureza": "DEVEDORA",
                          "grupo": "ATIVO"}, csrf=False)
        igual(s, 403, "POST sem X-Fiscale-Csrf → 403")
        ok(not (inst.dados / A / "contabil").exists(),
           "e nenhuma pasta contábil foi criada")
        s, c = inst.json("GET", "/api/csrf")
        inst.csrf = c.get("csrf", "")
        ok(bool(inst.csrf), "o token CSRF da sessão foi obtido")

        secao("4 · Plano, extrato e movimentos, pela rede")
        for conta in (("1", "ATIVO", "SINTETICA", "DEVEDORA", "ATIVO"),
                      ("1.1", "Bancos", "ANALITICA", "DEVEDORA", "")):
            s, r = inst.json("POST", "/api/contabil/plano/conta",
                             {"empresa": A, "codigo": conta[0],
                              "descricao": conta[1], "tipo": conta[2],
                              "natureza": conta[3], "grupo": conta[4]})
            igual(s, 200, "conta %s criada" % conta[0])
        corpo, tipo = multipart({"empresa": A}, ("arquivo", "jan.ofx", OFX))
        s, h, d = inst.pedir("POST", "/api/contabil/bancos/importar", corpo, tipo)
        r = json.loads(d)
        igual((s, r.get("desfecho")), (200, "IMPORTADO"), "OFX importado por multipart")
        imp = r.get("importacao", {})
        igual(imp.get("movimentos_novos"), 2, "2 movimentos novos")
        s, h, d = inst.pedir("POST", "/api/contabil/bancos/importar", corpo, tipo)
        igual(json.loads(d).get("desfecho"), "DUPLICATA",
              "reenviar o mesmo arquivo → DUPLICATA")
        s, r = inst.json("GET", "/api/contabil/movimentos?empresa=" + A)
        movs = r.get("movimentos", [])
        igual(len(movs), 2, "os 2 movimentos aparecem na busca")
        igual(sorted(m["categoria"] for m in movs), ["CLIENTE", "TARIFA_BANCARIA"],
              "cada um com a sua sugestão")
        s, r = inst.json("GET", "/api/contabil/lancamentos?empresa=" + A)
        igual(r.get("lancamentos"), [], "e nenhum lançamento foi criado")
        s, r = inst.json("GET", "/api/contabil/movimentos?empresa=%s&valor=49,90" % A)
        igual(len(r.get("movimentos", [])), 1, "busca por valor pela rede")

        secao("5 · O original, pela rede, byte a byte")
        s, h, d = inst.pedir("GET", "/api/contabil/extrato/original?empresa=%s"
                                    "&importacao=%s" % (A, imp.get("id")))
        igual(s, 200, "download do original → 200")
        ok(d == OFX, "os bytes baixados são os enviados (%d bytes)" % len(OFX))
        ok("attachment" in h.get("content-disposition", ""),
           "como anexo: %s" % h.get("content-disposition"))

        secao("6 · A empresa B não vê nada da A")
        s, r = inst.json("GET", "/api/contabil/movimentos?empresa=" + B)
        igual((s, r.get("movimentos")), (200, []), "B: lista vazia")
        s, r = inst.json("GET", "/api/contabil/movimento?empresa=%s&id=%s"
                         % (B, movs[0]["id"]))
        igual(s, 404, "B pedindo o id de um movimento de A → 404")
        s, h, d = inst.pedir("GET", "/api/contabil/extrato/original?empresa=%s"
                                    "&importacao=%s" % (B, imp.get("id")))
        ok(s in (400, 404) and d != OFX, "B não baixa o extrato de A (%d)" % s)
        s, r = inst.json("POST", "/api/contabil/conciliar",
                         {"empresa": B, "movimento_id": movs[0]["id"],
                          "alvo_tipo": "LANCAMENTO", "alvo_id": "x"})
        igual(s, 400, "B conciliando um movimento de A → recusado")
        s, r = inst.json("GET", "/api/contabil/movimento?empresa=%s&id=%s"
                         % (A, movs[0]["id"]))
        igual((s, r.get("ligacoes")), (200, []),
              "e o movimento de A continua sem ligação nenhuma")
        s, r = inst.json("GET", "/api/contabil/visao?empresa="
                         + fx.cnpj_ficticio("999999990001"))
        igual(s, 404, "empresa fora do cadastro → 404")

        secao("7 · As rotas são do operador — e só com login")
        for metodo, rota in (("GET", "/api/contabil/visao"),
                             ("POST", "/api/contabil/lancamentos"),
                             ("POST", "/api/contabil/bancos/importar"),
                             ("POST", "/api/contabil/conciliar")):
            ok(fiscale_papeis.pode("operador", metodo, rota),
               "operador alcança %s %s" % (metodo, rota))

        secao("7.1 · Fiscal → Contábil pela rede (Contábil 2)")
        s, r = inst.json("GET", "/api/contabil/fiscal?empresa=%s&competencia=2026-01" % A)
        igual(s, 200, "GET fiscal de uma competência responde")
        igual(r["panorama"]["competencia"], "2026-01", "e diz de qual competência é")
        s, r = inst.json("GET", "/api/contabil/fiscal?empresa=" + A)
        igual(s, 400, "sem competência → 400 (não existe 'todos os meses')")
        s, r = inst.json("POST", "/api/contabil/fiscal/sincronizar",
                         {"empresa": A, "competencia": "2026-01"}, csrf=False)
        igual(s, 403, "sincronizar sem CSRF → 403")
        s, r = inst.json("POST", "/api/contabil/fiscal/sincronizar",
                         {"empresa": A, "competencia": "2026-01"})
        ok(s == 200 and "panorama" in r, "com CSRF, sincroniza e devolve o panorama")
        s, r = inst.json("POST", "/api/contabil/competencia/estado",
                         {"empresa": A, "competencia": "2026-01", "estado": "FECHADA"})
        igual(r["competencia"]["estado"], "FECHADA", "fechar a competência pela rede")
        s, r = inst.json("POST", "/api/contabil/fiscal/lancar",
                         {"empresa": A, "competencia": "2026-01"})
        ok(s == 400 and "FECHADA" in r.get("erro", ""),
           "e o mês fechado barra a geração de lançamento: %s" % r.get("erro", "")[:60])
        s, r = inst.json("POST", "/api/contabil/competencia/estado",
                         {"empresa": A, "competencia": "2026-01", "estado": "ABERTA",
                          "motivo": "seguir o teste"})
        igual(r["competencia"]["estado"], "ABERTA", "reabrir com motivo")
        ok("Fiscal → Contábil" in pagina and "Sincronizar competência" in pagina,
           "a tela tem o painel Fiscal → Contábil")
        ok("Contas padrão" in pagina, "e a ligação papel → conta no Plano de Contas")
        s, h, d = inst.pedir("GET", "/nfe_documentos.html")
        ok(b"Ver impacto cont" in d,
           "a tela fiscal oferece 'Ver impacto contábil' no documento")

        secao("8 · Um operador de verdade: usa o Contábil, não vê administração")
        cookie_admin = inst.cookie
        s, r = inst.json("POST", "/api/usuarios",
                         {"usuario": "bia", "nome": "Bia Operadora",
                          "email": "bia@escritorio.com.br", "senha": SENHA_BIA,
                          "admin": False})
        ok(s == 200 and "erro" not in r, "operadora cadastrada pelo admin")
        # Páginas de administração, com o ADMIN: abrem.
        for pagina_adm in ("/usuarios.html", "/backup.html", "/seguranca_acessos.html"):
            s, _, _ = inst.pedir("GET", pagina_adm)
            igual(s, 200, "admin abre %s" % pagina_adm)
        inst.cookie = ""
        inst.csrf = ""
        s, r = inst.json("POST", "/api/login", {"usuario": "bia", "senha": SENHA_BIA})
        igual(s, 200, "a operadora entra")
        s, r = inst.json("GET", "/api/quem")
        igual((r.get("papel"), r.get("admin")), ("operador", False),
              "e o servidor a reconhece como operadora")
        s, _, _ = inst.pedir("GET", "/contabil.html")
        igual(s, 200, "a operadora abre a aplicação Contábil")
        s, r = inst.json("GET", "/api/contabil/empresas")
        igual((s, len(r.get("empresas", []))), (200, 2),
              "escolhe entre as empresas do cadastro")
        s, r = inst.json("GET", "/api/contabil/movimentos?empresa=" + A)
        igual((s, len(r.get("movimentos", []))), (200, 2),
              "e trabalha nos movimentos da empresa aberta")
        s, c = inst.json("GET", "/api/csrf")
        inst.csrf = c.get("csrf", "")
        s, r = inst.json("POST", "/api/contabil/plano/conta",
                         {"empresa": A, "codigo": "2", "descricao": "PASSIVO",
                          "tipo": "SINTETICA", "natureza": "CREDORA",
                          "grupo": "PASSIVO"})
        igual(s, 200, "a operadora escreve no Contábil (conta criada)")
        for pagina_adm in ("/usuarios.html", "/backup.html", "/seguranca_acessos.html"):
            s, h, _ = inst.pedir("GET", pagina_adm)
            ok(s == 302 and h.get("location") == "/home_portal.html",
               "operadora não abre %s (vai para a Home)" % pagina_adm)
        for rota_adm in ("/api/usuarios", "/api/auditoria", "/api/backup/situacao"):
            s, _ = inst.json("GET", rota_adm)
            igual(s, 403, "operadora recusada em %s" % rota_adm)
        s, _, d = inst.pedir("GET", "/central.html")
        ok(b'app-card hid" id="cartaoAdmin"' in d,
           "a Central chega a ela com o cartão Administração escondido")

        inst.cookie = cookie_admin
        s, r = inst.json("GET", "/api/quem")
        igual(r.get("admin"), True, "o admin continua admin na mesma instância")
        inst.cookie = ""
        s, _ = inst.json("GET", "/api/contabil/movimentos?empresa=" + A)
        igual(s, 401, "depois de perder a sessão, 401 de novo")
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
