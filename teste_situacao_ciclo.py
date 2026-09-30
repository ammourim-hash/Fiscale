#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O leitor de PDF, a tradução das mensagens e o ciclo até a chamada real.

    python teste_situacao_ciclo.py

O QUE SE PROTEGE
    • que o leitor NUNCA levante, e que layout desconhecido vire SEM_VALIDADE;
    • que ele não invente data quando o campo não é uma data;
    • que a tela receba uma frase que diz de quem é a próxima ação;
    • que o freio de bilhetagem venha ANTES da rede;
    • que meia credencial não vire chamada.

NÃO VAI À REDE. O transporte é injetado.
"""
from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

# A PASTA TEMPORARIA VEM DO APOIO, e nao do `tempfile` cru.
#     `pasta_temp()` registra a pasta para ser apagada no fim do
#     processo, e o `atexit` dele solta as conexoes SQLite antes de
#     tentar: no Windows arquivo aberto simplesmente nao e apagado, e
#     o `rmtree` falha em silencio deixando o esqueleto para tras.
#     Foi assim que %TEMP% juntou centenas de pastas vazias.
import teste_apoio as apoio                    # noqa: E402

from situacao import ciclo                          # noqa: E402
from situacao import credencial as cred             # noqa: E402
from situacao import indice as idx                  # noqa: E402
from situacao import mensagens, modelo as mod       # noqa: E402
from situacao.leitores import sitfis as leitor      # noqa: E402
from situacao import leitores                       # noqa: E402

_ok = _falhas = 0
_erros: list = []
FIX = RAIZ / "situacao" / "fixturas" / "sitfis"
EMPRESA = "11222333000181"


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


def pdf_do_sitfis() -> bytes:
    d = json.loads((FIX / "emitir-sucesso.json").read_text("utf-8"))
    return base64.b64decode(json.loads(d["dados"])["pdf"])


def resposta(status=200, dados=None, mensagem="ok"):
    corpo = {"status": status, "mensagens": [{"codigo": "X", "texto": mensagem}],
             "dados": json.dumps(dados) if dados is not None else ""}
    return 200, corpo, json.dumps(corpo).encode("utf-8")


def main():
    base = apoio.pasta_temp("fiscale_ciclo_")
    try:
        # ═══════════════════════════════════════════════════════════════
        secao("O leitor, contra o PDF REAL capturado")
        pdf = pdf_do_sitfis()
        lido = leitor.ler(pdf)
        igual(lido.get("natureza"), mod.NEGATIVA,
              "lê a natureza do rótulo 'Certidão Negativa:'")
        igual(lido.get("sem_pendencias"), True,
              "e reconhece a frase de ausência de pendências")
        igual(lido.get("leitura_versao"), leitor.VERSAO,
              "carimba a versão do leitor, para dar para reprocessar depois")

        # A AMOSTRA TEM AS DATAS MASCARADAS (99/99/9999) PELO PRÓPRIO SERPRO.
        #     O leitor as RECUSA em vez de forçar. É a política inteira num
        #     caso real: campo que não dá para ler não aparece.
        ok("apurado_em" not in lido,
           "e RECUSA a data mascarada 99/99/9999 do cabeçalho")
        ok("certidao_validade" not in lido,
           "e a validade mascarada também")

        # ═══════════════════════════════════════════════════════════════
        secao("O leitor nunca levanta, e nunca inventa")
        for nome, bruto in (("HTML", b"<html>erro</html>"),
                            ("vazio", b""),
                            ("PDF truncado", b"%PDF-1.4\n1 0 obj"),
                            ("PDF de outra coisa",
                             b"%PDF-1.4\ntrailer<<>>\n%%EOF")):
            igual(leitor.ler(bruto), {}, "%s -> {} (sem exceção)" % nome)
        igual(leitores.ler(mod.MUNICIPAL, mod.CERTIDAO, pdf), {},
              "esfera sem leitor devolve {} — não conhecer é resultado válido")
        igual(leitores.ler(mod.FEDERAL, mod.EXTRATO, pdf).get("natureza"),
              mod.NEGATIVA, "e o registro encaminha ao leitor certo")

        secao("A conversão de data recusa o que não é data")
        for texto, esperado in (("07/09/2026", "2026-09-07"),
                                ("99/99/9999", ""), ("31/02/2026", ""),
                                ("05/05/1899", ""), ("", ""), ("1/1/26", "")):
            igual(leitor._data_br(texto), esperado, "%r -> %r" % (texto, esperado))

        secao("Layout desconhecido faz o estado RETROCEDER, não quebrar")
        # Um PDF com a assinatura do documento mas sem nenhum campo conhecido:
        # é o que acontece quando o órgão reformata o relatório.
        ix = idx.abrir(base)
        from situacao import importacao as portao
        # UM PDF QUE NAO E O SITFIS.
        #     Trocar bytes no PDF original nao funciona: o conteudo e
        #     comprimido, e a palavra nao aparece em claro no arquivo -- a
        #     primeira versao deste teste "mudou" o layout e o leitor
        #     continuou lendo tudo, porque nada tinha mudado.
        outro = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n"
                 b"trailer<</Root 1 0 R>>\n%%EOF\n")
        r = portao.importar(base, ix, EMPRESA, mod.FEDERAL, mod.EXTRATO, outro)
        st = ix.estado(EMPRESA, mod.FEDERAL, mod.EXTRATO)
        igual(st["estado"], mod.SEM_VALIDADE,
              "o documento continua guardado, e o estado vira SEM_VALIDADE")
        igual(st["regular"], None, "e a regularidade vira 'não sei'")
        ok(Path(r["caminho"]).exists(),
           "o PDF NÃO foi perdido porque o leitor não o entendeu")
        ix.fechar()

        # ═══════════════════════════════════════════════════════════════
        secao("A tradução: moldura nossa, texto do órgão verbatim")
        h = mensagens.humanizar(mod.NAO_EMITIDA,
                                "Existem pendencias de ICMS impeditivas.")
        ok("pendência" in h["titulo"].lower(),
           "NAO_EMITIDA vira 'o órgão recusou emitir — há pendência'")
        ok("contabilidade" in h["orientacao"],
           "e a orientação diz de quem é a próxima ação")
        igual(h["do_orgao"], "Existem pendencias de ICMS impeditivas.",
              "o texto do órgão vai VERBATIM, em campo separado")
        ok("ICMS" in mensagens.frase(mod.NAO_EMITIDA,
                                     "Existem pendencias de ICMS impeditivas."),
           "e a frase de uma linha carrega os dois")

        h = mensagens.humanizar(mod.RECUSADA, "Dados inválidos. Utilize o json")
        ok("procuração" in h["orientacao"],
           "RECUSADA orienta a conferir procuração e certificado")
        ok("procuração" in h["orientacao"] and "SITFIS" in h["orientacao"],
           "e a frase OBSERVADA 'dados inválidos' acrescenta o que se sabe dela")

        h = mensagens.humanizar(mod.INDISPONIVEL, "gateway fora")
        ok("passar sozinho" in h["orientacao"],
           "INDISPONIVEL diz que não há o que fazer além de esperar")
        for d in mod.DESFECHOS:
            ok(bool(mensagens.humanizar(d)["titulo"]),
               "todo desfecho tem moldura: %s" % d)

        # NÃO SE INVENTA CÓDIGO.
        ok(len(mensagens.CONHECIDAS) <= 2,
           "a tabela de frases conhecidas é curta — só o que foi observado")
        ok(all(len(x) == 3 and x[2] for x in mensagens.CONHECIDAS),
           "e cada entrada registra QUANDO foi observada")

        # ═══════════════════════════════════════════════════════════════
        secao("O ciclo: o freio vem antes da rede")
        conf = base / cred.ARQUIVO
        conf.write_text(json.dumps({
            "contratante": "00000000000000", "ambiente": "trial",
            "padrao": "p1",
            "procuradores": [{"apelido": "p1", "documento": "99999999999",
                              "certificado_id": "cert-A", "ativo": True}]}),
            encoding="utf-8")

        chamou = []

        def dublê_ok(url, corpo, cab):
            chamou.append(url)
            if url.endswith("/Apoiar"):
                return resposta(200, {"protocoloRelatorio": "PROTO",
                                      "tempoEspera": 4000})
            return resposta(200, {"pdf": base64.b64encode(pdf).decode()})

        ix = idx.abrir(base)
        r = ciclo.uma_empresa(base, EMPRESA, transporte=dublê_ok, indice=ix)
        igual(r["desfecho"], mod.OBTIDA, "sem extrato em dia, ele consulta")
        ok(r["guardado"], "e o documento passa pelo portão")
        igual(len(chamou), 2, "duas chamadas: protocolo e relatório")

        chamou.clear()
        r = ciclo.uma_empresa(base, EMPRESA, transporte=dublê_ok, indice=ix)
        igual(r["desfecho"], "PULADO",
              "com extrato recém-apurado, NÃO consulta de novo")
        igual(chamou, [], "e nenhuma chamada foi feita — a rede nem foi tocada")
        ok("extrato apurado" in r["orientacao"], "dizendo por quê")

        chamou.clear()
        r = ciclo.uma_empresa(base, EMPRESA, transporte=dublê_ok, indice=ix,
                              forcar=True)
        igual(len(chamou), 2, "`forcar` existe, e é explícito")

        # ═══════════════════════════════════════════════════════════════
        secao("Meia credencial não vira chamada")
        d = json.loads(conf.read_text(encoding="utf-8"))
        d["ambiente"] = "producao"
        conf.write_text(json.dumps(d), encoding="utf-8")
        # SÃO DUAS PORTAS, E A ORDEM IMPORTA.
        #     Sem o token de acesso não há nem autenticação, então ele é
        #     conferido primeiro. Foi o que faltou na primeira chamada real
        #     (08/09/2026): sem `token` no cofre, o código caía no bearer
        #     PÚBLICO do trial e o gateway de produção devolveu 403/900908 --
        #     uma recusa correta apontando para o lugar errado.
        chamou.clear()
        r = ciclo.uma_empresa(base, "11444777000161", transporte=dublê_ok,
                              indice=ix)
        igual(r["desfecho"], mod.RECUSADA,
              "produção sem token de acesso → RECUSADA")
        igual(chamou, [], "e a rede não foi tocada")
        # A MENSAGEM FICOU MAIS PRECISA DE PROPOSITO.
        #     Antes ela dizia "falta o token de acesso" -- verdade, mas
        #     generica. Agora que o ciclo TENTA mintar o bearer, ele descobre
        #     a falta na origem e nomeia os dois segredos que faltam, com o
        #     comando que os grava. Recusa que ensina o proximo passo vale
        #     mais que recusa que so constata.
        ok("consumer_key" in r["do_orgao"] and "consumer_secret" in r["do_orgao"],
           "nomeando os DOIS segredos que faltam")
        ok("configurar" in r["do_orgao"],
           "e o comando que os grava")
        ok("procuração" not in r["do_orgao"] and "certificado" not in r["do_orgao"],
           "sem mandar procurar procuração nem certificado, que estão certos")

        # Com o token na mão, a próxima porta é o jwt.
        chamou.clear()
        r = ciclo.uma_empresa(base, "66789006000106", transporte=dublê_ok,
                              indice=ix, token="um-token-de-acesso")
        igual(r["desfecho"], mod.RECUSADA,
              "produção COM token e sem jwt_token → RECUSADA")
        igual(chamou, [], "e a rede continua intocada")
        ok("jwt_token" in r["do_orgao"],
           "agora sim o motivo é o jwt")

        # O TRIAL NÃO É RESERVA DE PRODUÇÃO — nunca.
        fonte_ciclo = (RAIZ / "situacao" / "ciclo.py").read_text("utf-8")
        i = fonte_ciclo.index("chave = token or")
        j = fonte_ciclo.index("conector = _sitfis.de_credencial")
        trecho = fonte_ciclo[i:j]
        # A BUSCA E PELA ATRIBUICAO, NAO PELA MENCAO. O comentario logo acima
        # da guarda cita `_sitfis.TOKEN_TRIAL` justamente para explicar o
        # defeito -- procurar o nome solto acharia a explicacao, nao o codigo.
        k = trecho.index("chave = _sitfis.TOKEN_TRIAL")
        ok("eh_producao" in trecho[:k],
           "a queda para o token do trial só acontece depois de descartar "
           "produção")

        conf.write_text(json.dumps({"contratante": "", "procuradores": []}),
                        encoding="utf-8")
        chamou.clear()
        r = ciclo.uma_empresa(base, "66789006000106", transporte=dublê_ok,
                              indice=ix)
        igual(r["desfecho"], mod.RECUSADA, "sem contratante configurado → RECUSADA")
        igual(chamou, [], "sem tocar a rede")
        tent = ix.tentativas("66789006000106")
        ok(tent and tent[0]["desfecho"] == mod.RECUSADA,
           "e a recusa fica registrada como tentativa")

        # ═══════════════════════════════════════════════════════════════
        secao("Uma empresa que falha não derruba a carteira")
        conf.write_text(json.dumps(d), encoding="utf-8")   # produção sem jwt

        def dublê_explode(url, corpo, cab):
            raise RuntimeError("cabo arrancado")

        saida = ciclo.todas(base, ["11222333000181", "11444777000161",
                                   "66789006000106"],
                            transporte=dublê_explode)
        igual(len(saida), 3, "as três empresas aparecem no resultado")
        ok(all(s.get("frase") for s in saida),
           "cada uma com sua frase legível")

        # ═══════════════════════════════════════════════════════════════
        secao("Nada disto tocou a rede")
        fonte = (RAIZ / "situacao" / "ciclo.py").read_text("utf-8")
        ok("transporte or _sitfis.transporte_requests()" in fonte,
           "o transporte real só é montado quando ninguém injeta outro")
        ix.fechar()

    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
