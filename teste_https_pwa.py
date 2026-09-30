#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portal — Fase 3: HTTPS atrás de proxy + FISCALE como aplicativo (PWA).

    python teste_https_pwa.py

O QUE ESTE TESTE TRAVA
    1. `fiscale_proxy`: cabeçalho de encaminhamento só vale vindo do loopback,
       e loopback COM cabeçalho nunca é "no próprio PC".
    2. Ponta a ponta, com um proxy TLS de teste na frente de um FISCALE
       isolado (é o papel que o Caddy fará):
         • por HTTPS o cookie sai Secure e vem HSTS; direto na porta, não;
         • pelo proxy NÃO dá para criar o primeiro admin nem se passar por
           "local" — a falha que tornaria o proxy um buraco;
         • login, Central, FISCALE, ELO (sem segunda senha), operador/admin,
           logout e sessão vencida voltando ao login;
         • a auditoria registra o IP de quem está atrás do proxy;
         • manifest, ícones, sw.js e a tela "sem conexão" abrem sem sessão.
    3. PWA: manifest válido (nome, ícones reais nos tamanhos declarados),
       service worker que não guarda página nem /api, botão de instalar que
       nasce escondido, e nenhuma credencial no navegador.

O QUE ESTE TESTE **NÃO** PROVA — e está dito
    • O Caddy de verdade (não está instalado): o redirecionamento HTTP→HTTPS
      e o `bind` na rede privada são conferidos só no TEXTO do modelo.
    • O certificado de produção. O deste teste é autoassinado, nasce numa
      pasta temporária e morre com ela.
    • A instalação em si: o balão "Instalar" é do navegador e só existe numa
      janela real (roteiro em FISCALE_HTTPS.md).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import http.server
import ipaddress
import json
import re
import shutil
import socket
import socketserver
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_proxy as fp                           # noqa: E402
from teste_clientes_modelo import Instancia          # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

SENHA_ADMIN = "Teste!Https#Admin2026"
SENHA_ALINE = "Teste!Https#Aline2026"
IP_DE_FORA = "203.0.113.50"          # faixa de documentação (RFC 5737)


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


def fonte(rel):
    return (RAIZ / rel).read_text("utf-8")


# ═══════════════════════════════════════════════════════════════════════════
def unidade_proxy():
    secao("1 · fiscale_proxy — quem é local, de onde veio, se é HTTPS")
    sem = {}
    igual(fp.eh_local("127.0.0.1", sem), True, "loopback sem cabeçalho = no próprio PC")
    igual(fp.eh_local("::1", sem), True, "loopback IPv6 também")
    igual(fp.origem("127.0.0.1", sem), "127.0.0.1", "origem = soquete")

    via = {"X-Forwarded-For": IP_DE_FORA, "X-Forwarded-Proto": "https"}
    igual(fp.eh_local("127.0.0.1", via), False, "loopback COM encaminhamento NÃO é local")
    igual(fp.origem("127.0.0.1", via), IP_DE_FORA, "origem = IP que o proxy escreveu")
    igual(fp.via_https("127.0.0.1", via), True, "e está em HTTPS")

    igual(fp.origem("127.0.0.1", {"X-Forwarded-For": f"127.0.0.1, {IP_DE_FORA}"}),
          IP_DE_FORA, "de uma cadeia, só o último (o do proxy local)")
    igual(fp.origem("127.0.0.1", {"X-Forwarded-For": "lixo"}), "proxy-sem-ip",
          "valor inválido não vira IP inventado nem 127.0.0.1")
    igual(fp.eh_local("127.0.0.1", {"X-Forwarded-For": "lixo"}), False,
          "e continua não sendo local")
    igual(fp.eh_local("127.0.0.1", {"Forwarded": "for=1.2.3.4"}), False,
          "`Forwarded` (RFC 7239) basta para negar o local")

    rede = "192.168.0.20"
    falso = {"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"}
    igual(fp.origem(rede, falso), rede, "de outra máquina, o cabeçalho é ignorado")
    igual(fp.via_https(rede, falso), False, "e ela não se declara HTTPS por cabeçalho")
    igual(fp.eh_local(rede, sem), False, "outra máquina nunca é local")
    igual(fp.via_https("127.0.0.1", {"X-Forwarded-For": IP_DE_FORA}), False,
          "sem X-Forwarded-Proto=https não é HTTPS")
    ok(fp.HSTS.startswith("max-age=") and int(fp.HSTS.split("=")[1]) <= 86400,
       "HSTS começa curto (≤ 1 dia) — erro de configuração não fica preso um ano")


# ═══════════════════════════════════════════════════════════════════════════
def pwa_estatico():
    secao("2 · Manifest")
    m = json.loads(fonte("web/manifest.webmanifest"))
    igual(m.get("name"), "FISCALE", "name = FISCALE")
    igual(m.get("short_name"), "FISCALE", "short_name = FISCALE")
    igual(m.get("description"), "Plataforma Fiscal do Escritório", "descrição")
    igual(m.get("display"), "standalone", "abre em janela própria (standalone)")
    ok(m.get("start_url", "").startswith(m.get("scope", "/")), "start_url dentro do scope")
    ok((RAIZ / "web" / m["start_url"].lstrip("/").split("?")[0]).exists(),
       f"start_url existe ({m['start_url']})")
    ok(m.get("id"), "tem id estável (a instalação não se duplica se o start_url mudar)")
    ok(re.fullmatch(r"#[0-9A-Fa-f]{6}", m.get("theme_color", "")), "theme_color válido")
    ok("Plano de Saúde" not in json.dumps(m, ensure_ascii=False), "descrição antiga saiu")

    from PIL import Image
    tamanhos = {}
    for ic in m.get("icons", []):
        arq = RAIZ / "web" / ic["src"].lstrip("/")
        ok(arq.exists(), f"ícone existe: {ic['src']}")
        if arq.exists():
            with Image.open(arq) as im:
                igual(f"{im.width}x{im.height}", ic["sizes"],
                      f"{ic['src']}: tamanho real = declarado")
                igual(im.format, "PNG", f"{ic['src']}: é PNG de verdade")
        tamanhos.setdefault(ic.get("purpose", "any"), set()).add(ic["sizes"])
    ok({"192x192", "512x512"} <= tamanhos.get("any", set()), "tem 192 e 512 (exigência do Chrome/Edge)")
    ok("512x512" in tamanhos.get("maskable", set()), "tem ícone maskable")
    for atalho in m.get("shortcuts", []):
        ok((RAIZ / "web" / atalho["url"].lstrip("/")).exists(), f"atalho aponta para tela real: {atalho['url']}")
    texto = json.dumps(m).lower()
    for proibido in ("senha", "token", "password", "secret", ".pfx", "cookie"):
        ok(proibido not in texto, f"manifest não fala em “{proibido}”")

    secao("3 · Service worker")
    sw = fonte("web/sw.js")
    guardar = re.search(r"const GUARDAR = \[(.*?)\];", sw, re.S)
    ok(guardar, "a lista do que vai para o aparelho é explícita")
    itens = re.findall(r"'([^']+)'|(OFFLINE)", guardar.group(1)) if guardar else []
    nomes = sorted(a or "/offline.html" for a, b in itens)
    igual(nomes, ["/offline.html"], "só a tela 'sem conexão' fica guardada no aparelho")
    ok("/api" not in sw.replace("/api vai para cache", ""), "nenhuma rota /api é tocada pelo SW")
    ok(sw.count("caches.open") == 1 and sw.count(".put(") == 0,
       "nada é gravado em cache além da instalação (sem .put em resposta de rede)")
    ok("r.mode !== 'navigate'" in sw and "r.method !== 'GET'" in sw,
       "só navegação GET passa pelo SW (login/POST/downloads seguem direto)")
    ok("origin !== self.location.origin" in sw, "e só da própria origem (o bilhete do ELO não passa)")
    ok("caches.delete" in sw, "limpa os caches do SW antigo")
    ok("cookie" not in sw.lower().replace("cookie de sessão", "").replace("o cookie é", ""),
       "o SW não lê cookie")

    off = fonte("web/offline.html")
    ok(not re.search(r"(src|href)=[\"']https?://", off), "tela 'sem conexão' não carrega nada de fora")
    ok(not re.search(r"<(img|link|script)[^>]+(src|href)=", off),
       "nem do próprio servidor: sem servidor, só o que está embutido aparece (visto no Edge)")
    ok("fetch(" not in off and "/api/" not in off, "e não consulta servidor nem mostra dado")

    secao("4 · Instalar FISCALE")
    pwa = fonte("web/fiscale-pwa.js")
    ok("beforeinstallprompt" in pwa and "prompt()" in pwa, "usa o pedido de instalação do navegador")
    ok("window.isSecureContext" in pwa, "só registra o SW em contexto seguro (HTTP na rede segue igual)")
    ok("(display-mode: standalone)" in pwa, "reconhece quando já está aberto como aplicativo")
    for rel in ("web/central.html", "web/home_portal.html"):
        t = fonte(rel)
        ok('<link rel="manifest" href="/manifest.webmanifest">' in t, f"{rel}: declara o manifest")
        ok('<script src="fiscale-pwa.js"></script>' in t, f"{rel}: carrega fiscale-pwa.js")
        b = re.search(r"<button[^>]*data-instalar-fiscale[^>]*>", t)
        ok(b and "hidden" in b.group(0), f"{rel}: o botão “Instalar FISCALE” nasce escondido")
        ok("Instalar FISCALE" in t, f"{rel}: com o texto “Instalar FISCALE”")
    ok(".faixa button[hidden]{display:none}" in fonte("web/home_portal.html"),
       "na Home, `hidden` vence o display:inline-flex dos botões")
    ok("Domínio" not in fonte("web/central.html"), "a Central continua sem o cartão Domínio")

    secao("5 · Nenhuma credencial no navegador")
    for rel in ("web/sw.js", "web/fiscale-pwa.js", "web/offline.html", "web/central.html",
                "web/login.html", "web/manifest.webmanifest"):
        t = fonte(rel)
        ok("localStorage" not in t and "sessionStorage" not in t and "indexedDB" not in t,
           f"{rel}: sem localStorage/sessionStorage/IndexedDB")
        ok("document.cookie" not in t, f"{rel}: não lê cookie")

    secao("6 · Rotas livres: só o necessário para instalar")
    srv = fonte("fiscale_server.py")
    import ast
    livres = ()
    for no in ast.parse(srv).body:
        if isinstance(no, ast.Assign) and any(getattr(a, "id", "") == "ROTAS_LIVRES" for a in no.targets):
            livres = ast.literal_eval(no.value)
    igual(sorted(livres), sorted([
        "/login.html", "/api/login", "/icone.webp", "/manifest.webmanifest", "/sw.js",
        "/api/primeiro-acesso", "/api/politica-senha", "/offline.html",
        "/icones/fiscale-192.png", "/icones/fiscale-512.png",
        "/icones/fiscale-maskable-512.png", "/icones/fiscale-180.png"]),
        "a lista de rotas sem login é exatamente a esperada (nada a mais)")
    for rel in [r for r in livres if not r.startswith("/api/")]:
        ok((RAIZ / "web" / rel.lstrip("/")).exists(), f"rota livre aponta para arquivo real: {rel}")

    secao("7 · Modelo do proxy (texto — o Caddy não está instalado)")
    cad = fonte("proxy/Caddyfile.exemplo")
    ok("reverse_proxy 127.0.0.1:8777" in cad, "o proxy fala com a 8777 só pelo loopback")
    ok(cad.count("bind {$FISCALE_BIND}") >= 2, "HTTPS e HTTP escutam só na interface da rede privada")
    ok("redir https://{$FISCALE_HOST}{uri} permanent" in cad, "HTTP redireciona para HTTPS")
    ok("header_up X-Forwarded-For {remote_host}" in cad, "X-Forwarded-For é sobrescrito, não acrescentado")
    ok("header_up X-Forwarded-Proto https" in cad, "e o FISCALE é avisado de que é HTTPS")
    ok(not re.search(r"(?m)^\s*tls\s+internal", cad), "sem certificado improvisado (tls internal)")
    ok(not re.search(r"CF_API_TOKEN\s*=|token\s+[A-Za-z0-9_-]{20,}", cad),
       "nenhum token escrito no arquivo (só variável de ambiente)")

    secao("8 · JavaScript compila")
    node = shutil.which("node")
    if not node:
        ok(True, "Node indisponível — conferência de sintaxe pulada")
    else:
        for rel in ("web/sw.js", "web/fiscale-pwa.js"):
            r = subprocess.run([node, "--check", str(RAIZ / rel)], capture_output=True, text=True)
            ok(r.returncode == 0, f"{rel} compila {r.stderr.strip()[:80]}")


# ═══════════════════════════════════════════════════════════════════════════
#  Proxy TLS de TESTE — faz o papel do Caddy. Certificado autoassinado numa
#  pasta temporária, só para este processo.
# ═══════════════════════════════════════════════════════════════════════════
def certificado_de_teste(pasta: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    chave = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fiscale-teste.localhost")])
    agora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
            .public_key(chave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(agora - dt.timedelta(minutes=5))
            .not_valid_after(agora + dt.timedelta(hours=2))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False)
            .sign(chave, hashes.SHA256()))
    c, k = pasta / "teste.crt", pasta / "teste.key"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(chave.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    return c, k


class ProxyDeTeste:
    """HTTPS na frente do FISCALE, com os mesmos cabeçalhos do Caddyfile."""

    def __init__(self, porta_fiscale: int, pasta: Path):
        self.alvo = porta_fiscale
        self.cert, self.chave = certificado_de_teste(pasta)
        alvo = self.alvo

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _repassar(self):
                n = int(self.headers.get("Content-Length") or 0)
                corpo = self.rfile.read(n) if n else None
                cab = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "x-forwarded-for", "x-forwarded-proto",
                                            "forwarded", "x-real-ip", "connection")}
                # como no Caddyfile: SOBRESCREVE com o "IP de fora" simulado
                cab["X-Forwarded-For"] = IP_DE_FORA
                cab["X-Forwarded-Proto"] = "https"
                c = http.client.HTTPConnection("127.0.0.1", alvo, timeout=120)
                c.request(self.command, self.path, body=corpo, headers=cab)
                r = c.getresponse()
                dados = r.read()
                self.send_response(r.status)
                for k, v in r.getheaders():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)
                c.close()

            do_GET = do_POST = _repassar

        class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.srv = S(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(self.cert), str(self.chave))
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.porta = self.srv.server_address[1]
        self.cliente_ctx = ssl.create_default_context(cafile=str(self.cert))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def fechar(self):
        self.srv.shutdown()
        self.srv.server_close()


def _pedir(conexao, metodo, caminho, corpo=None, cookie=""):
    cab = {}
    if cookie:
        cab["Cookie"] = cookie
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
        cab["Content-Type"] = "application/json"
        cab["Content-Length"] = str(len(dados))
    conexao.request(metodo, caminho, body=dados, headers=cab)
    r = conexao.getresponse()
    bruto = r.read()
    h = {}
    for k, v in r.getheaders():
        h.setdefault(k.lower(), v)
    conexao.close()
    return r.status, h, bruto


def js(b):
    try:
        return json.loads(b or b"{}")
    except Exception:
        return {}


def cookie_de(h):
    return (h.get("set-cookie") or "").split(";")[0]


def ponta_a_ponta():
    inst = Instancia()
    proxy = None
    tmp = Path(tempfile.mkdtemp(prefix="fiscale_https_"))
    try:
        secao("9 · FISCALE isolado + proxy HTTPS de teste")
        subiu = inst.subir()
        ok(subiu, f"FISCALE de teste na porta {inst.porta}")
        if not subiu:
            return
        proxy = ProxyDeTeste(inst.porta, tmp)
        ok(True, f"proxy HTTPS de teste na porta {proxy.porta} (certificado temporário)")

        def https(m, c, corpo=None, cookie=""):
            return _pedir(http.client.HTTPSConnection("127.0.0.1", proxy.porta,
                                                      context=proxy.cliente_ctx, timeout=120),
                          m, c, corpo, cookie)

        def direto(m, c, corpo=None, cookie=""):
            return _pedir(http.client.HTTPConnection("127.0.0.1", inst.porta, timeout=120),
                          m, c, corpo, cookie)

        secao("10 · Acesso HTTPS")
        st, h, corpo = https("GET", "/login.html")
        ok(st == 200 and b"Bem-vindo(a)!" in corpo, "o login abre por HTTPS")
        igual(h.get("strict-transport-security"), fp.HSTS, "com HSTS")
        st, h, _ = direto("GET", "/login.html")
        ok(st == 200 and "strict-transport-security" not in h,
           "direto na porta (HTTP da rede do escritório): sem HSTS, funcionando como antes")
        ok(b"location.protocol === 'https:'" in corpo,
           "o indicador “Conexão segura • SSL” depende de a página estar em HTTPS")

        secao("11 · Instalação: o que o navegador busca sem sessão")
        for rota, tipo in (("/manifest.webmanifest", ""), ("/sw.js", "javascript"),
                           ("/offline.html", "html"), ("/icones/fiscale-192.png", "image/png"),
                           ("/icones/fiscale-512.png", "image/png"),
                           ("/icones/fiscale-maskable-512.png", "image/png"),
                           ("/icones/fiscale-180.png", "image/png")):
            st, h, corpo = https("GET", rota)
            ok(st == 200 and tipo in (h.get("content-type") or ""),
               f"{rota} → {st} {h.get('content-type', '')}")
        st, h, corpo = https("GET", "/manifest.webmanifest")
        igual(js(corpo).get("name"), "FISCALE", "o manifest servido é o do FISCALE")

        secao("12 · O proxy não vira “local”")
        st, _, corpo = https("GET", "/api/primeiro-acesso")
        igual(js(corpo).get("local"), False, "por HTTPS, primeiro-acesso diz local=false")
        st, _, corpo = https("POST", "/api/primeiro-acesso", {"senha": SENHA_ADMIN})
        ok(st != 200 and not js(corpo).get("ok"),
           f"e RECUSA criar o admin pelo proxy ({st})")
        st, h, corpo = direto("POST", "/api/primeiro-acesso", {"senha": SENHA_ADMIN})
        ok(st == 200 and js(corpo).get("ok"), "no próprio PC (direto), cria normalmente")
        c_admin_local = cookie_de(h)

        secao("13 · Login por HTTPS")
        st, h, corpo = https("POST", "/api/login", {"usuario": "admin", "senha": SENHA_ADMIN})
        ok(st == 200 and js(corpo).get("ok"), "admin entra por HTTPS")
        sc = h.get("set-cookie") or ""
        ok("Secure" in sc and "HttpOnly" in sc and "SameSite=Lax" in sc,
           "cookie Secure + HttpOnly + SameSite=Lax")
        ok("fiscale_sessao=" not in corpo.decode("utf-8"), "token só no cookie, nunca no corpo")
        c_admin = cookie_de(h)
        st, h, _ = direto("POST", "/api/login", {"usuario": "admin", "senha": SENHA_ADMIN})
        ok("Secure" not in (h.get("set-cookie") or ""),
           "direto em HTTP o cookie continua sem Secure (senão a rede do escritório não entraria)")
        st, _, corpo = https("GET", "/api/quem", cookie=c_admin)
        q = js(corpo)
        ok(st == 200 and q.get("admin") is True and q.get("local") is False,
           "por HTTPS: admin reconhecido, e NÃO local (sem “Encerrar”)")
        st, _, corpo = direto("GET", "/api/quem", cookie=c_admin_local)
        igual(js(corpo).get("local"), True, "direto no PC continua local")

        st, _, corpo = direto("POST", "/api/usuarios",
                              {"usuario": "aline", "nome": "Aline Exemplo",
                               "email": "aline@escritorio.com.br", "senha": SENHA_ALINE,
                               "admin": False}, cookie=c_admin_local)
        ok(st == 200 and "erro" not in js(corpo), "operadora cadastrada")

        secao("14 · Auditoria enxerga quem está atrás do proxy")
        https("POST", "/api/login", {"usuario": "aline", "senha": "errada-errada-errada"})
        st, _, corpo = direto("GET", "/api/auditoria?evento=LOGIN_RECUSADO", cookie=c_admin_local)
        ips = [i.get("ip") for i in js(corpo).get("itens", [])]
        ok(IP_DE_FORA in ips, f"o login recusado foi registrado com o IP de fora ({ips[:3]})")
        ok("127.0.0.1" not in ips, "e não com 127.0.0.1")

        secao("15 · Operador: login → Central → FISCALE → ELO, sem segunda senha")
        st, h, corpo = https("POST", "/api/login",
                             {"usuario": "aline@escritorio.com.br", "senha": SENHA_ALINE, "manter": True})
        ok(st == 200 and js(corpo).get("destino") == "/central.html", "entra e vai para a Central")
        sc = h.get("set-cookie") or ""
        ok("Max-Age=604800" in sc and "Secure" in sc, "“Manter conectado”: 7 dias, Secure (política intacta)")
        c_aline = cookie_de(h)
        st, _, corpo = https("GET", "/central.html", cookie=c_aline)
        ok(st == 200 and b"Aplica" in corpo, "Central abre")
        ok(c_aline.split("=", 1)[1].encode() not in corpo, "sem token no HTML")
        st, _, corpo = https("GET", "/home_portal.html", cookie=c_aline)
        ok(st == 200 and b"fiscale-pwa.js" in corpo, "o FISCALE (start_url do app) abre com a sessão")
        st, _, corpo = https("POST", "/api/elo/abrir", cookie=c_aline)
        d = js(corpo)
        ok(st in (200, 503) and st not in (401, 403),
           f"ELO pela Central não pede senha nem é barrado ({st})")
        if st == 200:
            ok(d.get("ok") and d.get("token") and d.get("destino", "").endswith("/api/auth/exchange"),
               "o bilhete do ELO sai no corpo, para seguir por POST")
        st, h, _ = https("GET", "/usuarios.html", cookie=c_aline)
        ok(st == 302 and h.get("location") == "/home_portal.html", "operadora não abre página de admin")
        st, _, _ = https("GET", "/usuarios.html", cookie=c_admin)
        igual(st, 200, "admin abre")

        secao("16 · Abrir o app sem sessão / com sessão vencida")
        st, h, _ = https("GET", "/home_portal.html")
        ok(st == 302 and h.get("location") == "/login.html", "sem sessão, o ícone leva ao login")
        tok = c_aline.split("=", 1)[1]
        con = sqlite3.connect(str(inst.dados / "sessoes.db"))
        con.execute("UPDATE sessao SET expira_em = ? WHERE token_hash = ?",
                    (int(time.time()) - 1, hashlib.sha256(tok.encode()).hexdigest()))
        con.commit()
        con.close()
        st, h, _ = https("GET", "/home_portal.html", cookie=c_aline)
        ok(st == 302 and h.get("location") == "/login.html", "sessão vencida: volta ao login")
        st, _, _ = https("GET", "/api/quem", cookie=c_aline)
        igual(st, 401, "e a API recusa o cookie vencido")

        secao("17 · Sair")
        st, h, _ = https("POST", "/api/logout", cookie=c_admin)
        igual(st, 200, "logout por HTTPS")
        st, _, _ = https("GET", "/api/quem", cookie=c_admin)
        igual(st, 401, "o cookie deixa de valer no servidor")
    finally:
        if proxy:
            proxy.fechar()
        inst.derrubar()
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    unidade_proxy()
    pwa_estatico()
    ponta_a_ponta()
    print()
    print("=" * 62)
    print(f"{_ok} ok · {_falhas} falha(s)")
    if _erros:
        print("\nFalhas:")
        for e in _erros:
            print("  -", e)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
