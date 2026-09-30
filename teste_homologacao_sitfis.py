#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O script de homologação do SITFIS — sem rede, sem credencial real.

    python teste_homologacao_sitfis.py

POR QUE ESTE TESTE EXISTE
    `homologacao_sitfis.py` é a primeira coisa que vai tocar a credencial de
    PRODUÇÃO do Integra Contador. Três coisas nele não podem falhar:

    1. NUNCA IMPRIMIR TOKEN, CHAVE OU SEGREDO — nem na tela, nem no rastro;
    2. NUNCA CAIR NO TOKEN DO TRIAL quando falta credencial de produção
       (custou uma chamada real em 08/09/2026);
    3. PARAR ANTES DA REDE quando falta chave, e dizer o que falta.

    E a consulta opcional faz UM pedido de protocolo — nunca o relatório,
    nunca a carteira.

O QUE ESTE TESTE **NÃO** FAZ
    Não vai à rede, não usa o cofre real, não usa certificado.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import homologacao_sitfis as hs                             # noqa: E402
import teste_apoio as apoio                                 # noqa: E402
from situacao import autenticacao, credencial               # noqa: E402
from situacao.fontes import sitfis as sf                    # noqa: E402

_ok = _falhas = 0
_erros: list = []

CHAVE = "chave-consumidor-FICTICIA-123"
SEGREDO = "segredo-consumidor-FICTICIO-456"
ACESSO = "token-de-acesso-FICTICIO-" + "a" * 40
JWT = "jwt-FICTICIO-" + "b" * 60
CONTRIBUINTE = "11222333000181"
FIX = RAIZ / "situacao" / "fixturas" / "sitfis"


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
    ok(a == b, desc if a == b else "%s  (obtive %.120r, esperava %.120r)" % (desc, a, b))


def proteger(v):             # "cofre" de teste: reversível, e NÃO é o texto
    return base64.b64encode(v[::-1].encode()).decode()


def desproteger(b):
    return base64.b64decode(b).decode()[::-1]


def cofre(raiz, ambiente="producao", chaves=True, procurador=True):
    c = credencial.Cofre(Path(raiz) / "cred.json", desproteger=desproteger,
                         proteger=proteger)
    c.dados = {"contratante": "04103256000185", "ambiente": ambiente,
               "procuradores": ([{"apelido": "k", "documento": "12345678909"}]
                                if procurador else []),
               "padrao": "k" if procurador else "", "segredos": {}}
    c.guardar_segredo("jwt_token", "jwt-antigo-guardado")
    if chaves:
        c.guardar_segredo("consumer_key", CHAVE)
        c.guardar_segredo("consumer_secret", SEGREDO)
    return c


class Autenticador:
    def __init__(self, ficha=None, erro=None):
        self._ficha, self._erro, self.chamadas = ficha, erro, 0

    def ficha(self):
        self.chamadas += 1
        if self._erro:
            raise self._erro
        return self._ficha


def fabrica(aut, registro):
    def f(raiz, cred, cofre_):
        registro.append((cred.contratante, cred.autor))
        return aut
    return f


class Transporte:
    def __init__(self, *respostas):
        self.respostas, self.chamadas = list(respostas), []

    def __call__(self, url, corpo, cab):
        self.chamadas.append((url, corpo, dict(cab)))
        return self.respostas.pop(0)


def rodar(raiz, **kw):
    linhas = []
    cod = hs.homologar(raiz, saida=linhas.append, agora=lambda: 1_000_000.0, **kw)
    return cod, "\n".join(linhas)


def sem_segredo(texto, desc):
    for s in (CHAVE, SEGREDO, ACESSO, JWT, "jwt-antigo-guardado", sf.TOKEN_TRIAL):
        ok(s not in texto, "%s: não contém %s…" % (desc, s[:12]))


def main():
    with apoio.raiz_temporaria("homolog_sitfis_") as raiz:
        log = raiz / hs.ARQ_LOG

        secao("1 · Sem procurador: para no cofre, sem rede")
        aut, reg = Autenticador(), []
        cod, out = rodar(raiz, cofre=cofre(raiz, procurador=False),
                         fabrica_autenticador=fabrica(aut, reg))
        igual(cod, hs.E_COFRE, "código de saída 2")
        igual(aut.chamadas, 0, "o autenticador nem foi chamado")

        secao("2 · Ambiente de demonstração: não é homologação de produção")
        cod, out = rodar(raiz, cofre=cofre(raiz, ambiente="trial"),
                         fabrica_autenticador=fabrica(aut, reg))
        igual(cod, hs.E_AMBIENTE, "código 3")
        igual(aut.chamadas, 0, "sem rede")

        secao("3 · O CASO DE HOJE: só jwt_token no cofre, sem consumerKey/secret")
        cod, out = rodar(raiz, cofre=cofre(raiz, chaves=False),
                         fabrica_autenticador=fabrica(aut, reg))
        igual(cod, hs.E_CHAVES, "código 4")
        igual(aut.chamadas, 0, "PARA ANTES DA REDE")
        ok("consumer_key e consumer_secret" in out, "dizendo o que falta")
        ok("configurar_serpro.bat" in out, "e como gravar")
        ok("Nada foi enviado ao SERPRO" in out, "e que nada saiu")
        ok("jwt_token" in out, "listando o que HÁ no cofre (só o nome)")
        sem_segredo(out, "a tela")

        secao("4 · Autenticação recusada: código 5, mensagem do módulo")
        aut = Autenticador(erro=autenticacao.ErroAutenticacao(
            "O autenticador do SERPRO recusou (HTTP 401)."))
        cod, out = rodar(raiz, cofre=cofre(raiz), fabrica_autenticador=fabrica(aut, reg))
        igual(cod, hs.E_AUTH, "código 5")
        ok("HTTP 401" in out, "com o motivo")

        secao("5 · Autenticou: prova sem revelar")
        ficha = autenticacao.Bearer(access_token=ACESSO, jwt_token=JWT,
                                    expira_em=1_000_000.0 + 1800)
        aut, reg = Autenticador(ficha=ficha), []
        cod, out = rodar(raiz, cofre=cofre(raiz), fabrica_autenticador=fabrica(aut, reg))
        igual(cod, hs.OK, "código 0")
        igual(aut.chamadas, 1, "uma autenticação")
        igual(reg, [("04103256000185", "12345678909")],
              "com o contratante e o PROCURADOR da credencial")
        ok("%d caracteres" % len(ACESSO) in out, "mostra o tamanho do access_token")
        ok(hs.impressao_digital(ACESSO) in out, "e a impressão digital")
        ok(hs.impressao_digital(JWT) in out, "e a do jwt_token")
        ok("(1800 s)" in out, "e quanto tempo falta")
        ok("Nenhuma consulta foi feita" in out, "e diz que não consultou")
        sem_segredo(out, "a tela")

        secao("6 · --consultar: UM /Apoiar, nunca o /Emitir")
        apoiar = json.loads((FIX / "apoiar-sucesso.json").read_text("utf-8"))
        tr = Transporte((200, apoiar, b""))
        aut = Autenticador(ficha=ficha)
        cod, out = rodar(raiz, cofre=cofre(raiz), fabrica_autenticador=fabrica(aut, []),
                         transporte=tr, consultar="11.222.333/0001-81")
        igual(cod, hs.OK, "código 0")
        igual(len(tr.chamadas), 1, "exatamente uma chamada")
        url, corpo, cab = tr.chamadas[0]
        ok(url.endswith("/Apoiar"), "ao /Apoiar")
        ok("/integra-contador/v1" in url and "trial" not in url, "no gateway de PRODUÇÃO")
        igual(cab.get("Authorization"), "Bearer " + ACESSO, "com o token GERADO")
        igual(cab.get("jwt_token"), JWT, "e o jwt GERADO, não o guardado")
        igual(corpo["pedidoDados"]["idServico"], sf.SERVICO_PROTOCOLO, "serviço de protocolo")
        igual(corpo["autorPedidoDados"]["numero"], "12345678909", "autor = procurador")
        igual(corpo["contribuinte"]["numero"], CONTRIBUINTE, "contribuinte pedido")
        ok("protocolo recebido" in out, "relata o protocolo")
        sem_segredo(out, "a tela")

        secao("7 · SITFIS recusa com HTTP 200: o CORPO decide")
        erro = json.loads((FIX / "erro-dados-invalidos.json").read_text("utf-8"))
        cod, out = rodar(raiz, cofre=cofre(raiz), fabrica_autenticador=fabrica(Autenticador(ficha=ficha), []),
                         transporte=Transporte((200, erro, b"")), consultar=CONTRIBUINTE)
        igual(cod, hs.E_SITFIS, "código 7 mesmo com HTTP 200")

        secao("8 · CNPJ inválido e ficha sem jwt: nada sai")
        tr = Transporte()
        cod, _ = rodar(raiz, cofre=cofre(raiz), fabrica_autenticador=fabrica(Autenticador(ficha=ficha), []),
                       transporte=tr, consultar="12345678901234")
        igual(cod, hs.E_CNPJ, "CNPJ com DV errado -> 6")
        sem_jwt = autenticacao.Bearer(access_token=ACESSO, jwt_token="", expira_em=1_000_900.0)
        cod, out = rodar(raiz, cofre=cofre(raiz), fabrica_autenticador=fabrica(Autenticador(ficha=sem_jwt), []),
                         transporte=tr, consultar=CONTRIBUINTE)
        igual(cod, hs.E_SITFIS, "sem jwt -> 7")
        igual(tr.chamadas, [], "e nenhuma chamada ao SITFIS nos dois casos")
        ok("NÃO VEIO" in out, "a tela avisa que o jwt não veio")

        secao("9 · O rastro: uma linha por execução, sem segredo")
        linhas = log.read_text("utf-8").splitlines()
        igual(len(linhas), 9, "nove execuções, nove linhas")
        registros = [json.loads(l) for l in linhas]
        igual([r["codigo"] for r in registros], [2, 3, 4, 5, 0, 0, 7, 6, 7],
              "cada uma com seu código")
        ok(all("quando" in r and "etapa" in r for r in registros), "com quando e etapa")
        ok(registros[4].get("access_token_sha16") == hs.impressao_digital(ACESSO),
           "a execução boa guarda só a impressão digital")
        sem_segredo(log.read_text("utf-8"), "o rastro")

    secao("10 · O próprio script não carrega credencial")
    fonte = (RAIZ / "homologacao_sitfis.py").read_text("utf-8")
    ok("TOKEN_TRIAL" not in fonte, "não referencia o token do trial")
    ok("print(ficha" not in fonte and "access_token)" not in fonte.replace(
        "impressao_digital(ficha.access_token)", "").replace("len(ficha.access_token)", "")
       .replace("token=ficha.access_token", ""),
       "não imprime o token em lugar nenhum")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
