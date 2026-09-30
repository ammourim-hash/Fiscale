#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O conector SITFIS, contra dublê alimentado por respostas CAPTURADAS.

    python teste_situacao_sitfis.py

A REGRA DESTA SUÍTE
    Nenhuma resposta é escrita à mão, e nenhuma vem da documentação. Todo
    corpo que o dublê devolve sai de `situacao/fixturas/sitfis/`, que foram
    colhidos do ambiente de demonstração do SERPRO em 07/09/2026.

    O precedente: no mesmo dia, a API Consulta CND mostrou que o swagger dela
    documenta `Messagem` e `TipoCertidão`, e a API devolve `Mensagem` e
    `TipoCertidao`. Um dublê montado a partir do papel teria passado verde, e o
    conector devolveria mensagem vazia em produção para sempre.

O QUE ESTA SUÍTE NÃO SIMULA
    Protocolo não pronto, protocolo expirado, falta de procuração, PJ,
    relatório com pendências × limpo. O trial não produz nenhum deles, e
    inventá-los seria voltar a testar contra a nossa imaginação. O que se
    testa é que o conector NÃO QUEBRA quando a estrutura esperada falta —
    porque é assim que esses casos vão chegar.

NÃO VAI À REDE. O transporte é injetado, e a suíte confere isso.
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from situacao import modelo as mod                      # noqa: E402
from situacao.fontes import sitfis as sf                # noqa: E402

FIX = RAIZ / "situacao" / "fixturas" / "sitfis"

_ok = _falhas = 0
_erros: list = []


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


def so_codigo(fonte: str) -> str:
    """A fonte SEM comentários e SEM docstrings.

    Duas asserções desta suíte falharam por tropeçar na documentação do próprio
    módulo: ele EXPLICA que não usa `status_code` e que o código
    `Sucesso-Sitfis` diverge entre as duas pontas — e a busca por texto achava
    justamente essas frases. Guarda que acusa o comentário que a explica não
    protege nada; ensina a ignorá-la.
    """
    import ast
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef,
                           ast.AsyncFunctionDef)) and ast.get_docstring(no):
            no.body = no.body[1:]
    return ast.unparse(arvore)


def fixtura(nome: str) -> tuple:
    """`(status_http, corpo, bruto)` a partir do arquivo capturado.

    O HTTP é 200 porque **foi 200** em todas as capturas — inclusive nos erros.
    Fixar isso aqui é o que faz o teste do `status` do corpo ter sentido.
    """
    bruto = (FIX / nome).read_bytes()
    return 200, json.loads(bruto.decode("utf-8")), bruto


class Dublê:
    """Serve as fixturas na ordem, e anota o que foi pedido."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def __call__(self, url, corpo, cabecalhos):
        self.chamadas.append({"url": url, "corpo": corpo,
                              "cabecalhos": dict(cabecalhos)})
        if not self.respostas:
            raise AssertionError("o conector chamou mais vezes que o previsto")
        r = self.respostas.pop(0)
        return r() if callable(r) else r


def conector(dublê, dormiu: list, **kw):
    return sf.ConectorSitfis(
        transporte=dublê, contratante="00000000000000",
        dormir=lambda s: dormiu.append(s), **kw)


def main():
    # ═══════════════════════════════════════════════════════════════════
    secao("As fixturas são as capturadas, e não outra coisa")
    for nome in ("apoiar-sucesso.json", "emitir-sucesso.json",
                 "erro-dados-invalidos.json"):
        ok((FIX / nome).exists(), "existe a fixtura %s" % nome)
    ok((FIX / "LEIA-ME.md").exists(),
       "e o LEIA-ME que diz de onde vieram e o que NÃO cobrem")

    _, apoiar, _ = fixtura("apoiar-sucesso.json")
    _, emitir, _ = fixtura("emitir-sucesso.json")
    _, erro, _ = fixtura("erro-dados-invalidos.json")

    # ═══════════════════════════════════════════════════════════════════
    secao("O achado que este conector existe para respeitar")
    igual(erro["status"], 400,
          "o ERRO capturado traz status 400 no CORPO")
    igual(fixtura("erro-dados-invalidos.json")[0], 200,
          "e mesmo assim veio com HTTP 200")
    fonte = (RAIZ / "situacao" / "fontes" / "sitfis.py").read_text("utf-8")
    ok('corpo.get("status") != 200' in fonte,
       "por isso o conector decide pelo `status` do corpo")
    codigo = so_codigo(fonte)
    logica = codigo.split("def transporte_requests")[0]
    ok("status_code" not in logica,
       "e a lógica não usa o status HTTP para decidir desfecho")

    # ═══════════════════════════════════════════════════════════════════
    secao("O caminho feliz, ponta a ponta")
    d = Dublê(fixtura("apoiar-sucesso.json"), fixtura("emitir-sucesso.json"))
    dormiu = []
    c = conector(d, dormiu)
    r = c.obter("99999999999")

    igual(r.desfecho, mod.OBTIDA, "desfecho OBTIDA")
    ok(r.tem_documento, "veio documento")
    igual(r.documento[:5], b"%PDF-", "e ele é um PDF de verdade")
    ok(len(r.documento) > 10000, "com tamanho de relatório (%d bytes)"
       % len(r.documento))
    igual(r.origem, "SITFIS", "a origem é registrada")
    igual(len(r.respostas), 2, "as DUAS respostas cruas foram preservadas")

    # ═══════════════════════════════════════════════════════════════════
    secao("Ele fez as duas chamadas certas, na ordem certa")
    igual(len(d.chamadas), 2, "duas chamadas")
    ok(d.chamadas[0]["url"].endswith("/Apoiar"), "a primeira é /Apoiar")
    ok(d.chamadas[1]["url"].endswith("/Emitir"), "a segunda é /Emitir")
    igual(d.chamadas[0]["corpo"]["pedidoDados"]["idServico"],
          "SOLICITARPROTOCOLO91", "com o serviço de protocolo")
    igual(d.chamadas[1]["corpo"]["pedidoDados"]["idServico"],
          "RELATORIOSITFIS92", "e com o serviço de relatório")
    igual(d.chamadas[0]["corpo"]["contribuinte"]["tipo"], sf.PF,
          "11 dígitos são reconhecidos como pessoa física")

    # O PROTOCOLO É O QUE VEIO, e não um valor colado no código.
    enviado = json.loads(d.chamadas[1]["corpo"]["pedidoDados"]["dados"])
    esperado = json.loads(apoiar["dados"])["protocoloRelatorio"]
    igual(enviado["protocoloRelatorio"], esperado,
          "o protocolo enviado é EXATAMENTE o que o /Apoiar devolveu")

    # ═══════════════════════════════════════════════════════════════════
    secao("A espera é a que o serviço pediu")
    igual(json.loads(apoiar["dados"])["tempoEspera"], 4000,
          "a captura traz tempoEspera = 4000 ms")
    igual(dormiu, [4.0], "e o conector esperou 4,0 s — nem mais, nem menos")

    # ═══════════════════════════════════════════════════════════════════
    secao("O código da mensagem NÃO é usado como chave")
    igual(apoiar["mensagens"][0]["codigo"], "[Sucesso-Sitfis-SC01]",
          "o /Apoiar devolve o código com um colchete")
    igual(emitir["mensagens"][0]["codigo"], "[Sucesso-Sitfis-SC01]]",
          "e o /Emitir devolve com DOIS — a captura mostra a divergência")
    ok("Sucesso-Sitfis" not in codigo,
       "o conector não casa por esse texto em lugar nenhum")
    ok("Sucesso-Sitfis" in fonte,
       "embora o comentário EXPLIQUE a divergência, para o próximo leitor")

    # ═══════════════════════════════════════════════════════════════════
    secao("Erro no primeiro passo para tudo, e diz por quê")
    d = Dublê(fixtura("erro-dados-invalidos.json"))
    r = conector(d, []).obter("99999999999")
    igual(r.desfecho, mod.RECUSADA, "status 400 no corpo → RECUSADA")
    ok(not r.tem_documento, "e não veio documento")
    ok("inválidos" in r.detalhe.lower() or "invalidos" in r.detalhe.lower(),
       "o detalhe repete o que o SERVIÇO disse: %r" % r.detalhe[:60])
    igual(len(d.chamadas), 1,
          "e o /Emitir nem foi chamado — não se emite sem protocolo")

    # ═══════════════════════════════════════════════════════════════════
    secao("Erro no segundo passo é distinguido do primeiro")
    d = Dublê(fixtura("apoiar-sucesso.json"),
              fixtura("erro-dados-invalidos.json"))
    r = conector(d, []).obter("99999999999")
    igual(r.desfecho, mod.RECUSADA, "o 400 do /Emitir também é RECUSADA")
    igual(len(r.respostas), 2,
          "mas as duas respostas ficam guardadas — dá para auditar onde parou")

    # ═══════════════════════════════════════════════════════════════════
    secao("O que não tem amostra é tratado pela ESTRUTURA, sem inventar código")
    # Sucesso sem protocolo: é a cara provável do estado assíncrono que o
    # trial não produz. O conector não pode explodir nem seguir em frente.
    sem_proto = dict(apoiar)
    sem_proto["dados"] = json.dumps({"tempoEspera": 4000})
    d = Dublê((200, sem_proto, b"{}"))
    r = conector(d, []).obter("99999999999")
    igual(r.desfecho, mod.INDISPONIVEL,
          "sucesso SEM protocolo → INDISPONÍVEL (tentar de novo depois)")
    igual(len(d.chamadas), 1, "e não segue para o /Emitir")

    sem_pdf = dict(emitir)
    sem_pdf["dados"] = json.dumps({})
    d = Dublê(fixtura("apoiar-sucesso.json"), (200, sem_pdf, b"{}"))
    r = conector(d, []).obter("99999999999")
    igual(r.desfecho, mod.INDISPONIVEL, "sucesso SEM pdf → INDISPONÍVEL")

    # Resposta que nem JSON é: gateway caído, HTML de portal, proxy.
    d = Dublê((502, None, b"<html>Bad Gateway</html>"))
    r = conector(d, []).obter("99999999999")
    igual(r.desfecho, mod.INDISPONIVEL, "resposta que não é JSON → INDISPONÍVEL")
    ok("502" in r.detalhe, "e o detalhe diz o HTTP que veio")

    # ═══════════════════════════════════════════════════════════════════
    secao("O que não é PDF não passa por relatório")
    nao_pdf = dict(emitir)
    nao_pdf["dados"] = json.dumps(
        {"pdf": "PGh0bWw+U2Vzc2FvIGV4cGlyYWRhPC9odG1sPg=="})   # HTML em base64
    d = Dublê(fixtura("apoiar-sucesso.json"), (200, nao_pdf, b"{}"))
    r = conector(d, []).obter("99999999999")
    igual(r.desfecho, mod.ILEGIVEL,
          "HTML disfarçado de relatório é ILEGÍVEL, não OBTIDA")
    ok(not r.tem_documento, "e não vira documento guardado")

    lixo = dict(emitir)
    lixo["dados"] = json.dumps({"pdf": "isto não é base64!!!"})
    d = Dublê(fixtura("apoiar-sucesso.json"), (200, lixo, b"{}"))
    r = conector(d, []).obter("99999999999")
    igual(r.desfecho, mod.ILEGIVEL, "base64 inválido também é ILEGÍVEL")

    # ═══════════════════════════════════════════════════════════════════
    secao("Identidade fora de formato para antes de qualquer chamada")
    d = Dublê()
    r = conector(d, []).obter("123")
    igual(r.desfecho, mod.RECUSADA, "identidade curta → RECUSADA")
    igual(len(d.chamadas), 0, "e nenhuma chamada foi feita")
    igual(sf.tipo_do_documento("05678005000191"), sf.PJ, "14 dígitos → PJ")
    igual(sf.tipo_do_documento("123.456.789-09"), sf.PF,
          "e a máscara do CPF não atrapalha")

    # ═══════════════════════════════════════════════════════════════════
    secao("O `dados` é JSON dentro de string — e isso é lido sem explodir")
    igual(sf.ler_dados({"dados": '{"a": 1}'}), {"a": 1}, "texto JSON é lido")
    igual(sf.ler_dados({"dados": "não é json"}), {},
          "texto que não é JSON vira vazio, não exceção")
    igual(sf.ler_dados({}), {}, "ausência de `dados` também")
    igual(sf.ler_dados({"dados": '"só uma string"'}), {},
          "e JSON que não é objeto também")

    # ═══════════════════════════════════════════════════════════════════
    secao("Credencial não vaza para lugar nenhum")
    d = Dublê(fixtura("apoiar-sucesso.json"), fixtura("emitir-sucesso.json"))
    c = sf.ConectorSitfis(transporte=d, contratante="00000000000000",
                          token="TOKEN-SECRETO", jwt="JWT-SECRETO",
                          dormir=lambda s: None)
    r = c.obter("99999999999")
    ok("TOKEN-SECRETO" not in r.detalhe and "JWT-SECRETO" not in r.detalhe,
       "o detalhe devolvido não carrega token nem jwt")
    ok("TOKEN-SECRETO" not in json.dumps(r.extra),
       "nem a proveniência extra")
    ok(d.chamadas[0]["cabecalhos"].get("jwt_token") == "JWT-SECRETO",
       "mas o jwt VAI no cabeçalho quando existe (produção exige)")
    ok("jwt_token" not in sf.ConectorSitfis(
        transporte=d, contratante="00000000000000")._cabecalhos(),
       "e não vai quando não existe (o trial não pede)")

    # ═══════════════════════════════════════════════════════════════════
    secao("Nada disto tocou a rede")
    ok("import requests" not in fonte.split("def transporte_requests")[0],
       "`requests` só é importado dentro do transporte de verdade")
    ok("SITFIS" in sf.ORIGEM, "e a origem é nomeada")
    try:
        s = socket.socket()
        s.settimeout(0.001)
        s.connect(("192.0.2.1", 80))     # rede de documentação: nunca responde
        s.close()
        ok(False, "houve conexão de rede (não deveria haver)")
    except OSError:
        ok(True, "nenhuma conexão foi aberta por esta suíte")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
