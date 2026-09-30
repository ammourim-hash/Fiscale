#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portal — Fase 1 (login) e Fase 2 (Central de Aplicações).

    python teste_portal.py

O QUE ESTE TESTE TRAVA
    A tela nova de login é só CARA nova: o que protege continua no servidor.
    Por isso quase tudo aqui é conferido pela porta HTTP de uma instância
    isolada, e não lendo o HTML:

      • sem sessão, nada além do login responde;
      • "Manter conectado" muda a validade (12 h → 7 dias) e SÓ com `true`
        de verdade; sem marcar, é exatamente o comportamento de antes;
      • o destino depois do login é a Central, e `/` também leva a ela;
      • a Central recebe nome e último acesso, e a resposta comum de
        `/api/quem` (chamada em toda tela) continua enxuta;
      • operador vê a Central e não alcança página de administrador;
      • sair revoga no servidor — o cookie copiado deixa de valer;
      • nenhuma tela guarda token/senha no navegador, e o Domínio não
        aparece em lugar nenhum (decisão de 13/09/2026).

O QUE ESTE TESTE **NÃO** FAZ
    Não toca na 8777 nem na pasta real: sobe um FISCALE próprio, em porta e
    pasta temporárias, e o derruba pela árvore de processos (nunca por nome).
"""
from __future__ import annotations

import base64
import http.client
import json
import re
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_papeis as pp                          # noqa: E402
import fiscale_sessoes                               # noqa: E402
from teste_clientes_modelo import Instancia          # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

SENHA_ADMIN = "Teste!Portal#Admin2026"
SENHA_ALINE = "Teste!Portal#Aline2026"


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


# ── cliente HTTP com cookie explícito (duas pessoas ao mesmo tempo) ─────────
def pedir(porta, metodo, caminho, corpo=None, cookie=""):
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=120)
    cab = {}
    if cookie:
        cab["Cookie"] = cookie
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
        cab["Content-Type"] = "application/json"
        cab["Content-Length"] = str(len(dados))
    c.request(metodo, caminho, body=dados, headers=cab)
    r = c.getresponse()
    bruto = r.read()
    h = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, h, bruto


def js(bruto):
    try:
        return json.loads(bruto or b"{}")
    except Exception:
        return {}


def max_age(set_cookie):
    m = re.search(r"Max-Age=(\d+)", set_cookie or "")
    return int(m.group(1)) if m else None


def cookie_de(h):
    return (h.get("set-cookie") or "").split(";")[0]


def entrar(porta, usuario, senha, **extra):
    corpo = {"usuario": usuario, "senha": senha}
    corpo.update(extra)
    return pedir(porta, "POST", "/api/login", corpo)


def estaticos():
    login = fonte("web/login.html")
    central = fonte("web/central.html")
    elo = fonte("web/fiscale-elo.js")
    home = fonte("web/home_portal.html")

    secao("1 · A tela de login é a da referência")
    for trecho in ("Bem-vindo(a)!", "Usuário ou e-mail", "Senha", "ENTRAR",
                   "Manter conectado", "Esqueci minha senha", "Plataforma Fiscal do Escritório",
                   "NF-e / NFC-e", "NFS-e", "CT-e", "Apuração Fiscal", "Acervo e Conferência",
                   "Ambiente seguro"):
        ok(trecho in login, f"login traz “{trecho}”")
    ok("manter: $('#manter').checked" in login, "e envia o “Manter conectado” ao servidor")
    ok("location.protocol === 'https:'" in login,
       "só afirma “SSL” quando a página veio mesmo por HTTPS")
    ok(not re.search(r"<(script|link)[^>]+(src|href)=[\"']https?://", login),
       "nenhum recurso de fora (abre igual sem internet)")

    secao("2 · A Central é a da referência, e sem o Domínio")
    for trecho in ("Início", "Aplicações", "Suporte", "Configurações", "Aplicações disponíveis",
                   "Plataforma Fiscal", "Comunicações", "Administração",
                   "Gestão do Sistema", "Usuário logado", "Último acesso"):
        ok(trecho in central, f"central traz “{trecho}”")
    ok('id="cartaoAdmin"' in central and 'app-card hid" id="cartaoAdmin"' in central,
       "o cartão de Administração nasce escondido")
    ok("if(q.admin)" in central, "e só aparece quando o servidor diz que é admin")
    for nome in ("Domínio", "Dominio", "DOMINIO"):
        ok(nome not in central and nome not in login, f"“{nome}” não aparece no portal")
    ok("textContent = 'Olá, '" in central,
       "o nome do cadastro entra por textContent, nunca como HTML")

    secao("3 · Nada de credencial no navegador")
    for rel, txt in (("login.html", login), ("central.html", central), ("fiscale-elo.js", elo)):
        ok("localStorage" not in txt and "sessionStorage" not in txt,
           f"{rel} não usa localStorage/sessionStorage")
        ok("document.cookie" not in txt, f"{rel} não lê cookie (é HttpOnly)")
    ok("i.name = 'token'" in elo and "f.method = 'POST'" in elo,
       "o bilhete do ELO segue por POST, nunca na URL")
    ok("?token=" not in elo and "'token='" not in elo, "e não há token montado em URL")

    secao("4 · Um só caminho para abrir o ELO")
    ok('<script src="fiscale-elo.js"></script>' in home
       and '<script src="fiscale-elo.js"></script>' in central,
       "Home e Central carregam o mesmo fiscale-elo.js")
    ok("async function abrirElo" not in home and "async function abrirElo" not in central,
       "nenhuma das duas guarda uma cópia própria")
    ok("central.html" in home, "a Home tem o caminho de volta para a Central")

    secao("4B · Comunicações embutido na Central")
    ok('id="vistaComunicacoes"' in central and 'id="eloFrame"' in central,
       "a Central tem a área Comunicações com o iframe do ELO")
    ok('allow="microphone"' in central,
       "e permite microfone no iframe (senão a gravação de voz falharia por política)")
    ok("embutido=1" in elo, "o iframe pede o ELO em modo embutido")
    # O endereço do ELO vem do servidor (elo_config.json), nunca escrito na tela.
    for nome, txt in (("central.html", central), ("fiscale-elo.js", elo)):
        ok(not re.search(r"(127\.0\.0\.1|localhost):\d{4}", txt),
           f"{nome} não traz o endereço do ELO escrito no código")
    ok("d.base" in elo, "a origem do ELO vem da resposta de /api/elo/abrir")
    ok(elo.count("'/api/elo/abrir'") == 1,
       "um pedido de bilhete só, para os dois modos de abrir")
    ok("iframe.src = d.base + '/api/auth/destino" in elo
       and "token" not in elo.split("iframe.src =")[1].split(";")[0],
       "o bilhete não viaja na URL do iframe")
    ok("window.abrirEloEmbutido" in elo,
       "`abrirEloEmbutido` é exportada — sem isto a Central chamaria undefined")
    ok('referrerpolicy="no-referrer"' in central,
       "o iframe continua sem mandar Referer para o ELO")

    secao("4C · O embutido não pode ficar esperando para sempre")
    # O pior modo de falha deste fluxo é silêncio: sem `aoPronto`, a tela ficava
    # em "Abrindo o ELO…" sem mensagem e sem como tentar de novo.
    ok("ESPERA_MAXIMA_MS" in elo, "há um limite de espera declarado")
    ok(re.search(r"ESPERA_MAXIMA_MS\s*=\s*\d{4,}", elo),
       "com valor em milissegundos, em constante nomeada")
    ok("setTimeout(function(){" in elo and "falhou(" in elo,
       "que termina no mesmo caminho de erro (`falhou`)")
    # O relógio é armado ANTES da navegação: uma falha imediata de rede
    # acontece antes da próxima volta do laço.
    pos_relogio = elo.find("relogio = setTimeout")
    pos_src = elo.find("iframe.src = d.base")
    ok(0 < pos_relogio < pos_src,
       "e é armado ANTES da primeira navegação do iframe")
    ok("clearTimeout(relogio)" in elo, "o relógio é cancelado quando dá certo")
    ok(elo.count("clearTimeout(relogio)") >= 2,
       "tanto no sucesso quanto na falha — nenhum relógio sobra correndo")
    ok("if(encerrado) return" in elo,
       "uma carga atrasada não ressuscita uma tentativa já perdida")
    ok("if(!d.base){" in elo and "falhou(" in elo.split("if(!d.base){")[1][:200],
       "sem `base`, a falha é a mesma das outras — não uma guarda morta")
    # A Central substitui o estado e devolve o caminho de volta.
    ok("eloRotulo" in central,
       "o rótulo do estado é um elemento próprio, para poder ser substituído")
    ok("Não foi possível abrir o ELO." in central,
       "e no erro ele passa a dizer que não abriu")
    ok("eloTentarDeNovo" in central and "tentarComunicacoesDeNovo" in central,
       "com botão de nova tentativa")
    ok("eloAberto = false" in central,
       "e `eloAberto` é rearmado, senão a retentativa não aconteceria")

    secao("5 · Papéis das telas novas")
    for rota in ("/central.html", "/fiscale-elo.js", "/login.html"):
        ok(pp.pode(pp.OPERADOR, "GET", rota), f"operador alcança GET {rota}")
    ok(not pp.pode(pp.OPERADOR, "GET", "/usuarios.html"),
       "e continua sem alcançar /usuarios.html")


def sessoes_unidade():
    import tempfile
    secao("6 · Validade da sessão")
    igual(fiscale_sessoes.VALIDADE_PADRAO_S, 12 * 3600, "o padrão continua 12 horas")
    igual(fiscale_sessoes.VALIDADE_MANTER_S, 7 * 24 * 3600, "“Manter conectado” vale 7 dias")
    with tempfile.TemporaryDirectory() as tmp:
        s = fiscale_sessoes.Sessoes(str(Path(tmp) / "s.db"))
        t_padrao = s.criar("aline")
        t_longa = s.criar("aline", validade_s=fiscale_sessoes.VALIDADE_MANTER_S)
        t_curta = s.criar("aline", validade_s=1)
        lin = dict(s._con.execute(
            "SELECT token_hash, expira_em - criada_em FROM sessao").fetchall())
        igual(lin[fiscale_sessoes._hash(t_padrao)], 12 * 3600, "sem validade: 12 h")
        igual(lin[fiscale_sessoes._hash(t_longa)], 7 * 24 * 3600, "com manter: 7 dias")
        time.sleep(1.2)
        igual(s.get(t_curta), None, "validade própria também expira")
        igual(s.get(t_padrao), "aline", "sem derrubar as outras sessões da pessoa")
        s._con.close()


def http_ponta_a_ponta():
    inst = Instancia()
    try:
        secao("7 · Instância isolada")
        subiu = inst.subir()
        ok(subiu, f"FISCALE de teste respondeu na porta {inst.porta}")
        if not subiu:
            return
        P = inst.porta

        secao("8 · Sem sessão, só o login responde")
        st, h, _ = pedir(P, "GET", "/central.html")
        ok(st == 302 and h.get("location") == "/login.html", "central.html → login")
        st, h, _ = pedir(P, "GET", "/")
        ok(st == 302 and h.get("location") == "/login.html", "/ → login")
        st, _, _ = pedir(P, "GET", "/api/quem?completo=1")
        igual(st, 401, "/api/quem?completo=1 sem sessão → 401")
        st, _, corpo = pedir(P, "GET", "/login.html")
        ok(st == 200 and b"Bem-vindo(a)!" in corpo, "o login novo é servido")

        secao("9 · Primeiro acesso e cadastro")
        st, h, corpo = pedir(P, "POST", "/api/primeiro-acesso", {"senha": SENHA_ADMIN})
        ok(st == 200 and js(corpo).get("ok"), "admin criado no próprio computador")
        c_admin = cookie_de(h)
        st, _, corpo = pedir(P, "POST", "/api/usuarios",
                             {"usuario": "aline", "nome": "Aline Exemplo",
                              "email": "aline@escritorio.com.br", "senha": SENHA_ALINE,
                              "admin": False}, cookie=c_admin)
        ok(st == 200 and js(corpo).get("ok", True) and "erro" not in js(corpo),
           f"operadora Aline cadastrada ({st} {js(corpo).get('erro', '')})")

        secao("10 · Login sem “Manter conectado” = comportamento de antes")
        st, h, corpo = entrar(P, "aline@escritorio.com.br", SENHA_ALINE)
        d = js(corpo)
        ok(st == 200 and d.get("ok"), "entra pelo e-mail")
        igual(d.get("destino"), "/central.html", "o destino é a Central")
        igual(max_age(h.get("set-cookie")), 12 * 3600, "cookie de 12 h")
        sc = h.get("set-cookie") or ""
        ok("HttpOnly" in sc and "SameSite=Lax" in sc, "HttpOnly e SameSite=Lax mantidos")
        c1 = cookie_de(h)
        ok("fiscale_sessao=" in c1 and "fiscale_sessao=" not in corpo.decode("utf-8"),
           "o token vai só no cookie, nunca no corpo")

        secao("11 · “Manter conectado”")
        st, h, _ = entrar(P, "aline", SENHA_ALINE, manter=True)
        igual(max_age(h.get("set-cookie")), 7 * 24 * 3600, "manter=true → cookie de 7 dias")
        c_longa = cookie_de(h)
        for falso in ("true", 1, "sim"):
            st, h, _ = entrar(P, "aline", SENHA_ALINE, manter=falso)
            igual(max_age(h.get("set-cookie")), 12 * 3600,
                  f"manter={falso!r} não é marcação → 12 h")

        secao("12 · Senha errada continua igual")
        st, h, corpo = entrar(P, "aline", "errada-errada-123", manter=True)
        igual(st, 401, "senha errada → 401")
        ok("set-cookie" not in h, "e nenhum cookie é emitido")

        secao("13 · A Central com sessão")
        st, h, _ = pedir(P, "GET", "/", cookie=c1)
        ok(st == 302 and h.get("location") == "/central.html", "/ → Central")
        st, _, corpo = pedir(P, "GET", "/central.html", cookie=c1)
        ok(st == 200 and b"Aplica" in corpo, "a Central abre para a operadora")
        ok(c1.split("=", 1)[1].encode() not in corpo, "e não carrega o token no HTML")
        st, _, corpo = pedir(P, "GET", "/api/quem?completo=1", cookie=c1)
        q = js(corpo)
        igual(q.get("nome"), "Aline Exemplo", "nome para a saudação")
        igual(q.get("papel"), "operador", "papel operador")
        ok(q.get("admin") is False, "não é admin")
        ok(bool(q.get("ultimo_acesso")), "último acesso = um login ANTERIOR ao atual")
        st, _, corpo = pedir(P, "GET", "/api/quem", cookie=c1)
        ok("nome" not in js(corpo) and "ultimo_acesso" not in js(corpo),
           "a resposta comum de /api/quem continua enxuta")
        # Página de admin devolve o operador à Home (comportamento que já
        # existia em `_barrar_por_papel`); o que importa é não servir a tela.
        st, h, corpo = pedir(P, "GET", "/usuarios.html", cookie=c1)
        ok(st == 302 and h.get("location") == "/home_portal.html" and not corpo,
           f"operadora não abre /usuarios.html (volta à Home: {st})")
        st, _, corpo = pedir(P, "GET", "/api/quem?completo=1", cookie=c_admin)
        ok(js(corpo).get("admin") is True, "o admin é reconhecido (vê o cartão Administração)")

        secao("13B · Contrato de /api/elo/abrir — o que o embutido consome")
        # Sem `elo_config.json` o tenant é vazio, e `emitir_token` recusa. Isso
        # tem de ser 503 com motivo — não 200 com bilhete inválido.
        st, _, corpo = pedir(P, "POST", "/api/elo/abrir", {}, cookie=c1)
        d = js(corpo)
        igual(st, 503, "sem tenant configurado, a rota responde 503")
        ok(d.get("ok") is False and "tenant" in (d.get("erro") or "").lower(),
           "dizendo que falta o tenant: %r" % (d.get("erro") or "")[:60])

        # Agora com configuração — e com barra no fim de propósito, porque é o
        # jeito mais natural de alguém digitar a URL num JSON à mão.
        (inst.dados / "elo_config.json").write_text(
            json.dumps({"url": "http://127.0.0.1:3999/",
                        "tenant_id": "tenant-de-teste"}), encoding="utf-8")
        st, _, corpo = pedir(P, "POST", "/api/elo/abrir", {}, cookie=c1)
        d = js(corpo)
        igual(st, 200, "com tenant configurado, a rota abre")
        ok(d.get("ok") is True, "e devolve ok")
        for campo in ("token", "base", "destino"):
            ok(bool(d.get(campo)), "a resposta traz `%s`" % campo)
        igual(d.get("base"), "http://127.0.0.1:3999",
              "`base` é o endereço do elo_config.json")
        ok(not str(d.get("base") or "").endswith("/"),
           "e não termina com barra — o cliente concatena o caminho")
        igual(d.get("destino"), d.get("base") + "/api/auth/exchange",
              "`destino` é `base` + a rota de troca do ELO")
        # O bilhete continua sendo o de sempre: JWT EdDSA em três partes.
        partes = str(d.get("token") or "").split(".")
        igual(len(partes), 3, "o bilhete continua sendo um JWT de três partes")
        cab = json.loads(base64.urlsafe_b64decode(
            partes[0] + "=" * (-len(partes[0]) % 4)))
        igual(cab.get("alg"), "EdDSA", "assinado com EdDSA (Ed25519)")
        ok(bool(cab.get("kid")), "e com o `kid` da chave do escritório")
        ok(d.get("token") not in str(d.get("base")),
           "o bilhete não aparece dentro do endereço")

        secao("14 · Sair revoga no servidor")
        st, h, _ = pedir(P, "POST", "/api/logout", cookie=c_longa)
        igual(st, 200, "logout responde")
        igual(max_age(h.get("set-cookie")), 0, "e limpa o cookie")
        st, _, _ = pedir(P, "GET", "/api/quem", cookie=c_longa)
        igual(st, 401, "o cookie de 7 dias copiado antes deixa de valer")
        st, _, _ = pedir(P, "GET", "/api/quem", cookie=c1)
        igual(st, 200, "a outra sessão da mesma pessoa continua de pé")
    finally:
        inst.derrubar()


def main() -> int:
    estaticos()
    sessoes_unidade()
    http_ponta_a_ponta()
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
