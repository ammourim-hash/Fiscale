#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O Bearer dinâmico do Integra Contador, e a falha segura do `--ver`.

    python teste_situacao_auth.py

O QUE SE PROTEGE
    • que o segredo não vaze — nem em `repr`, nem em mensagem de erro;
    • que o token seja reaproveitado, e renovado ANTES de vencer;
    • que a sessão mTLS feche mesmo quando a chamada estoura;
    • que o `--ver` NUNCA diga "PRONTO" sobre credencial que não autentica.

NÃO VAI À REDE E NÃO USA CERTIFICADO. A sessão é injetada.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio as apoio                         # noqa: E402

from situacao import autenticacao as auth           # noqa: E402
from situacao import ciclo                          # noqa: E402
from situacao import configurar                     # noqa: E402
from situacao import credencial as cred             # noqa: E402
from situacao import indice as idx                  # noqa: E402
from situacao import modelo as mod                  # noqa: E402

_ok = _falhas = 0
_erros: list = []

CHAVE = "chave-de-mentira"
SEGREDO = "segredo-de-mentira"


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
        ok(False, "%s  (obtive %.110r, esperava %.110r)" % (desc, a, b))


def levanta(fn, exc, desc):
    try:
        fn()
    except exc:
        ok(True, desc)
    except Exception as e:
        ok(False, "%s  (levantou %s)" % (desc, e.__class__.__name__))
    else:
        ok(False, "%s  (não levantou nada)" % desc)


class Resposta:
    def __init__(self, status=200, corpo=None, texto=""):
        self.status_code = status
        self._corpo = corpo
        self.text = texto or (json.dumps(corpo) if corpo is not None else "")

    def json(self):
        if self._corpo is None:
            raise ValueError("não é JSON")
        return self._corpo


class SessaoFalsa:
    """Um dublê de `requests.Session` que anota o que recebeu."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.fechada = 0

    def post(self, url, headers=None, data=None, timeout=None):
        self.chamadas.append({"url": url, "headers": dict(headers or {}),
                              "data": dict(data or {}), "timeout": timeout})
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        self.fechada += 1


def corpo_ok(acesso="ACESSO-123", jwt="JWT-456", dura=600):
    return {"access_token": acesso, "jwt_token": jwt,
            "token_type": "Bearer", "expires_in": dura}


def main():
    base = apoio.pasta_temp("fiscale_auth_")
    try:
        # ═══════════════════════════════════════════════════════════════
        secao("Sem as chaves, ele nem se monta")
        levanta(lambda: auth.Autenticador("", SEGREDO, abrir_sessao=lambda: None),
                auth.SemChaves, "sem consumerKey → SemChaves")
        levanta(lambda: auth.Autenticador(CHAVE, "", abrir_sessao=lambda: None),
                auth.SemChaves, "sem consumerSecret → SemChaves")
        levanta(lambda: auth.Autenticador(CHAVE, SEGREDO),
                auth.SemChaves, "sem a sessão do certificado → SemChaves")
        try:
            auth.Autenticador("", "", abrir_sessao=lambda: None)
        except auth.SemChaves as e:
            ok("consumer_key" in str(e) and "consumer_secret" in str(e),
               "e a mensagem diz COMO gravá-los")

        # ═══════════════════════════════════════════════════════════════
        secao("A troca: Basic na ida, par de fichas na volta")
        s = SessaoFalsa(Resposta(200, corpo_ok()))
        relogio = {"t": 1000.0}
        a = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=lambda: s,
                              agora=lambda: relogio["t"])
        f = a.ficha()
        igual(f.access_token, "ACESSO-123", "o access_token volta")
        igual(f.jwt_token, "JWT-456",
              "e o jwt vem NA MESMA resposta — os dois nascem juntos")
        igual(f.expira_em, 1600.0, "a expiração é agora + expires_in")

        c = s.chamadas[0]
        igual(c["url"], auth.URL, "bateu no /authenticate do SERPRO")
        igual(c["data"], {"grant_type": "client_credentials"},
              "com grant_type de cliente")
        import base64 as _b64
        esperado = "Basic " + _b64.b64encode(
            ("%s:%s" % (CHAVE, SEGREDO)).encode()).decode()
        igual(c["headers"]["Authorization"], esperado,
              "e o Basic é base64(chave:segredo)")
        igual(s.fechada, 1, "a sessão mTLS foi fechada depois de usar")

        # ═══════════════════════════════════════════════════════════════
        secao("O segredo não escapa por lugar nenhum")
        ok(SEGREDO not in repr(f) and "ACESSO-123" not in repr(f),
           "o `repr` da ficha não traz valor nenhum: %s" % repr(f))
        ok(SEGREDO not in str(f), "nem o `str`")
        ok(not hasattr(a, "consumer_secret"),
           "o autenticador não guarda o segredo em atributo legível")
        fonte = (RAIZ / "situacao" / "autenticacao.py").read_text("utf-8")
        ok("print(" not in fonte, "e o módulo não imprime nada, nunca")

        # ═══════════════════════════════════════════════════════════════
        secao("Reaproveita, e renova ANTES de vencer")
        s2 = SessaoFalsa(Resposta(200, corpo_ok("A1", "J1", 600)),
                         Resposta(200, corpo_ok("A2", "J2", 600)))
        relogio["t"] = 1000.0
        a = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=lambda: s2,
                              agora=lambda: relogio["t"])
        igual(a.ficha().access_token, "A1", "primeira vez, minta")
        igual(a.ficha().access_token, "A1", "segunda vez, reaproveita")
        igual(len(s2.chamadas), 1, "e não houve segunda chamada")
        igual(a.renovacoes, 1, "uma renovação só")

        # Falta MENOS que a margem: renova, mesmo sem ter vencido.
        relogio["t"] = 1000.0 + 600 - (auth.MARGEM_S - 1)
        igual(a.ficha().access_token, "A2",
              "dentro da margem ele renova — token que vence no voo vira 401 "
              "no meio do ciclo")
        igual(len(s2.chamadas), 2, "houve a segunda chamada")

        s3 = SessaoFalsa(Resposta(200, corpo_ok("B1")),
                         Resposta(200, corpo_ok("B2")))
        relogio["t"] = 1000.0
        a = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=lambda: s3,
                              agora=lambda: relogio["t"])
        a.ficha()
        a.esquecer()
        igual(a.ficha().access_token, "B2",
              "`esquecer()` força novo par — serve para o 401 inesperado")

        # ═══════════════════════════════════════════════════════════════
        secao("Quando dá errado, dá errado com nome")
        s4 = SessaoFalsa(Resposta(401, None, "Invalid Credentials"))
        a = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=lambda: s4)
        try:
            a.ficha()
            ok(False, "deveria ter levantado")
        except auth.ErroAutenticacao as e:
            ok("401" in str(e), "o HTTP aparece na mensagem")
            ok("Invalid Credentials" in str(e), "e o texto do serviço também")
            ok(SEGREDO not in str(e) and CHAVE not in str(e),
               "mas a credencial NÃO aparece")
        igual(s4.fechada, 1, "e a sessão fechou mesmo com erro")

        s5 = SessaoFalsa(Resposta(200, {"token_type": "Bearer"}))
        a = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=lambda: s5)
        levanta(a.ficha, auth.ErroAutenticacao,
                "200 sem access_token também é erro — não se inventa token")

        s6 = SessaoFalsa(RuntimeError("cabo arrancado"))
        a = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=lambda: s6)
        levanta(a.ficha, auth.ErroAutenticacao, "rede caída vira erro nomeado")
        igual(s6.fechada, 1, "e a sessão fecha até quando a chamada estoura")

        def sessao_ruim():
            raise OSError("certificado ilegível")
        a = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=sessao_ruim)
        levanta(a.ficha, auth.SemChaves,
                "certificado que não abre é falta de credencial, não erro de rede")

        # ═══════════════════════════════════════════════════════════════
        secao("Sem `expires_in`, assume pouco")
        s7 = SessaoFalsa(Resposta(200, {"access_token": "C1"}))
        relogio["t"] = 500.0
        a = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=lambda: s7,
                              agora=lambda: relogio["t"])
        igual(a.ficha().expira_em, 500.0 + auth.DURACAO_PADRAO_S,
              "errar para MENOS custa uma renovação; para mais custa um 401")

        # ═══════════════════════════════════════════════════════════════
        secao("O ciclo minta o bearer quando não há token guardado")
        conf = base / cred.ARQUIVO
        conf.write_text(json.dumps({
            "contratante": "00000000000000", "ambiente": "producao",
            "padrao": "p1",
            "procuradores": [{"apelido": "p1", "documento": "99999999999",
                              "ativo": True}]}), encoding="utf-8")
        pedidos = []

        def dublê_sitfis(url, corpo, cab):
            pedidos.append({"url": url, "cab": dict(cab)})
            body = {"status": 400, "mensagens": [], "dados": ""}
            return 200, body, b"{}"

        s8 = SessaoFalsa(Resposta(200, corpo_ok("BEARER-REAL", "JWT-REAL")))
        autenticador = auth.Autenticador(CHAVE, SEGREDO, abrir_sessao=lambda: s8)
        ix = idx.abrir(base)
        ciclo.uma_empresa(base, "11222333000181", transporte=dublê_sitfis,
                          indice=ix, autenticador=autenticador)
        ok(pedidos, "a chamada ao SITFIS aconteceu")
        igual(pedidos[0]["cab"]["Authorization"], "Bearer BEARER-REAL",
              "com o bearer MINTADO, e não com o do trial")
        igual(pedidos[0]["cab"].get("jwt_token"), "JWT-REAL",
              "e com o jwt da MESMA resposta — o par nasce junto")
        from situacao.fontes import sitfis as sf
        ok(sf.TOKEN_TRIAL not in json.dumps(pedidos),
           "o token público do trial não apareceu em lugar nenhum")
        ix.fechar()

        # ═══════════════════════════════════════════════════════════════
        secao("O `--ver` NÃO diz PRONTO sobre credencial que não autentica")
        # ISTO É O QUE FALHOU EM 08/09/2026: ele olhava só contratante,
        # procurador e ambiente, disse PRONTO, e a chamada real levou 403.
        saida = []
        import builtins
        real_print = builtins.print
        builtins.print = lambda *a, **k: saida.append(" ".join(str(x) for x in a))
        try:
            configurar.main(["--ver", "--dados", str(base)])
        finally:
            builtins.print = real_print
        texto = "\n".join(saida)
        ok("PRONTO" not in texto,
           "produção sem consumer_key/secret NÃO é PRONTO")
        ok("consumer_key" in texto and "consumer_secret" in texto,
           "e ele NOMEIA o que falta")
        ok("credencial de ACESSO" in texto,
           "dizendo que a identidade está certa e o que falta é o acesso")

        # No trial, as mesmas chaves não são exigidas — e é correto.
        d = json.loads(conf.read_text(encoding="utf-8"))
        d["ambiente"] = "trial"
        conf.write_text(json.dumps(d), encoding="utf-8")
        saida.clear()
        builtins.print = lambda *a, **k: saida.append(" ".join(str(x) for x in a))
        try:
            configurar.main(["--ver", "--dados", str(base)])
        finally:
            builtins.print = real_print
        ok("PRONTO" in "\n".join(saida),
           "no trial ele diz PRONTO: lá o token público é legítimo")

        # ═══════════════════════════════════════════════════════════════
        secao("Nada disto tocou a rede nem abriu certificado")
        ok("import requests" not in fonte,
           "o autenticador não importa `requests` — a sessão vem de fora")
        ok("criar_sessao" in fonte,
           "e em produção ele usa a fábrica mTLS única do projeto")

    finally:
        pass

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
