#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SITUAÇÃO FISCAL · MUNICIPAL (Recife) — o canal assistido, e seus limites.

    python teste_situacao_recife.py

POR QUE ESTA SUÍTE EXISTE
    O escopo municipal foi retirado em 12/09/2026 e reaberto em 27/09/2026 —
    mas reaberto como **canal assistido**, que é a única forma honesta: o
    portal "Recife em Dia" não tem API, exige CAPTCHA resolvido por pessoa, e o
    PDF que ele entrega nesta máquina é IMAGEM (0 caracteres de texto).

    Reabrir escopo é o momento em que se inventa automação que não existe. Esta
    suíte existe para impedir isso.

O QUE ELA PROVA
    1. o PDF municipal entra pelo portão ÚNICO, com origem `ASSISTIDA_RECIFE`;
    2. ele é guardado, indexado e auditável — com hash e histórico;
    3. o motivo de a validade não ter sido lida é GRAVADO e legível, em vez de
       chegar à tela como um campo vazio;
    4. sem classificação humana, a esfera municipal NUNCA diz "regular";
    5. o status municipal usa o vocabulário da REGULARIDADE, não o do documento;
    6. o municipal **não entra** no consolidado federal;
    7. não existe consulta automática municipal — a rota recusa;
    8. reentregar o mesmo PDF não duplica documento.

O QUE ELA NÃO FAZ
    Não vai à rede (socket proibido), não usa dado real e não toca na pasta de
    produção: raiz temporária, CNPJ fictício, PDF construído aqui.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import situacao.carteira as cart                      # noqa: E402
import situacao.importacao as portao                   # noqa: E402
import situacao.indice as indice                       # noqa: E402
import situacao.modelo as modelo                       # noqa: E402
import situacao.regularidade as reg                    # noqa: E402
import teste_apoio as apoio                            # noqa: E402

_ok = _falhas = 0

import socket as _socket                               # noqa: E402


def _proibido(self, endereco, *a, **kw):
    raise AssertionError("teste tentou abrir conexão de rede para %s" % (endereco,))


_socket.socket.connect = _proibido

EMPRESA = "11222333000181"          # dígitos válidos, empresa inventada
OUTRA = "66789006000106"


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        print("  FALHOU   " + desc)


def pdf_imagem(rotulo: str) -> bytes:
    """Um PDF válido SEM camada de texto — o caso real do portal do Recife.

    Não é um PDF "quebrado": abre, tem página, e não tem texto nenhum. É
    exatamente o que um "print to PDF" produz, e o que nenhum leitor por
    extração de texto vai ler.
    """
    try:
        from pypdf import PdfWriter
    except Exception:                                   # pragma: no cover
        return b""
    from io import BytesIO
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    w.add_metadata({"/Producer": "Microsoft: Print To PDF", "/Title": rotulo})
    b = BytesIO()
    w.write(b)
    return b.getvalue()


# ══════════════════════════════════════════════════════════════════════════
secao("1 · O PDF municipal entra pelo portão único, com a origem do canal")
BRUTO = pdf_imagem("Recife em Dia")
if not BRUTO:
    print("  FALHOU   pypdf indisponível: sem ele esta suíte não mede nada")
    sys.exit(1)

with apoio.raiz_temporaria("sitrec_") as raiz:
    with indice.abrir(raiz) as ix:
        r = portao.importar(raiz, ix, EMPRESA, modelo.MUNICIPAL, modelo.EXTRATO,
                            BRUTO, origem="ASSISTIDA_RECIFE",
                            usuario="aline", metodo=modelo.METODO_UPLOAD)
        ok(r["desfecho"] == "NOVO", "documento novo guardado (%s)" % r["desfecho"])
        ok(r["origem"] == modelo.ASSISTIDA_RECIFE,
           "com a origem normalizada do canal (%s)" % r["origem"])
        ok(len(r["sha256"]) == 64, "e com hash do conteúdo")
        ok(r["esfera"] == modelo.MUNICIPAL and r["tipo"] == modelo.EXTRATO,
           "na esfera e tipo pedidos")

        # 8 · reentregar o mesmo arquivo não cria segundo documento
        r2 = portao.importar(raiz, ix, EMPRESA, modelo.MUNICIPAL,
                             modelo.EXTRATO, BRUTO, origem="ASSISTIDA_RECIFE",
                             usuario="aline", metodo=modelo.METODO_UPLOAD)
        ok(r2["id"] == r["id"] and r2["desfecho"] != "NOVO",
           "reentregar o mesmo PDF não duplica (%s)" % r2["desfecho"])
        ok(len(ix.documentos(EMPRESA, modelo.MUNICIPAL)) == 1,
           "um documento municipal no índice, não dois")

        secao("2 · Guardado, indexado e auditável")
        docs = ix.documentos(EMPRESA, modelo.MUNICIPAL)
        d = docs[0]
        ok(d.get("sha256") == r["sha256"], "o índice aponta para o mesmo hash")
        ok(Path(d.get("caminho") or "").is_file()
           or (Path(raiz) / (d.get("caminho") or "x")).exists(),
           "e o original está no disco")
        tents = ix.tentativas_da_esfera(EMPRESA, modelo.MUNICIPAL)
        ok(len(tents) == 2, "duas tentativas registradas (%d)" % len(tents))
        ok(all(t.get("usuario") == "aline" for t in tents),
           "com o usuário que trouxe")
        ok(all(t.get("origem") == modelo.ASSISTIDA_RECIFE for t in tents),
           "e com a fonte em cada uma")
        ok(all(t.get("quando_utc") for t in tents),
           "e com data/hora em cada uma")

        secao("3 · Por que a validade não foi lida — gravado, não em branco")
        m = cart.municipal(ix, EMPRESA)
        ok(bool(m["sem_leitura"]), "o motivo está preenchido")
        ok("camada de texto" in m["sem_leitura"],
           "e diz que o PDF não tem camada de texto: %r"
           % m["sem_leitura"][:80])
        ok("leitor" in m["sem_leitura"],
           "mencionando também a ausência de leitor para a esfera")
        ok(not d.get("validade") and not d.get("apurado_em"),
           "e nenhum campo de data foi inventado")

        secao("4 · Sem classificação humana, o municipal não diz 'regular'")
        ok(m["status"] == reg.NAO_CONSULTADO,
           "status NAO_CONSULTADO com documento guardado (%s)" % m["status"])
        ok(m["grupo"] != "regular", "e o grupo não é 'regular'")
        ok("não classificado" in m["motivo"] or "classificado" in m["motivo"],
           "o motivo explica que falta classificar: %r" % m["motivo"][:60])
        ok(m["fonte_status"] == "", "nenhuma conclusão automática declarada")
        ok(m["automatico"] is False, "e a esfera se declara não automática")
        ok("CAPTCHA" in m["motivo_sem_automatico"],
           "dizendo por quê (CAPTCHA)")

        secao("5 · O vocabulário do status é o da REGULARIDADE")
        for proibido in (modelo.SEM_DOCUMENTO, modelo.SEM_VALIDADE,
                         modelo.NAO_LIDA):
            ok(m["status"] != proibido,
               "status não usa `%s`, que é estado de DOCUMENTO" % proibido)
        ok(m["rotulo"] == reg.ROTULO.get(m["status"]),
           "e o rótulo vem da tabela da regularidade")

        secao("6 · O municipal não entra no consolidado federal")
        ok(m["entra_no_consolidado"] is False,
           "a resposta declara que não entra")
        emp = {"identidade": EMPRESA, "nome": "EMPRESA DE TESTE LTDA"}
        linha = cart.avaliar_empresa(ix, emp, cart.canal_federal(None))
        ok(linha["federal"]["status"] == reg.NAO_CONSULTADO,
           "a federal segue não consultada")
        ok(linha["geral"]["status"] == reg.NAO_CONSULTADO,
           "e o consolidado também — o PDF municipal não o move")
        ok("municipal" not in linha,
           "`avaliar_empresa` continua sem a esfera municipal")

        secao("7 · Classificação humana é o único caminho para concluir")
        import situacao.interpretacao as interp
        interp.registrar(raiz, ix, EMPRESA, modelo.MUNICIPAL, modelo.EXTRATO,
                         d["id"], reg.REGULAR, "aline",
                         observacao="Extrato sem débitos, conferido no PDF.")
        m2 = cart.municipal(ix, EMPRESA)
        ok(m2["status"] == reg.REGULAR,
           "depois da classificação, o municipal conclui (%s)" % m2["status"])
        ok(m2["fonte_status"] == "HUMANA", "declarando a fonte como humana")
        ok(m2["interpretacao"] and m2["interpretacao"]["usuario"] == "aline",
           "com o nome de quem classificou")
        ok("aline" in m2["motivo"], "e o motivo cita o autor")
        # E o consolidado federal continua intocado.
        linha2 = cart.avaliar_empresa(ix, emp, cart.canal_federal(None))
        ok(linha2["geral"]["status"] == reg.NAO_CONSULTADO,
           "mesmo regular no município, o consolidado federal não muda")

        secao("8 · Histórico e empresa sem documento")
        h = m2["historico"]
        ok(len(h) >= 3, "histórico com consultas e classificação (%d)" % len(h))
        ok(any(e["evento"] == "CLASSIFICACAO" for e in h),
           "a classificação aparece no histórico")
        ok(all(e.get("esfera") == modelo.MUNICIPAL for e in h),
           "e o histórico municipal só traz eventos municipais")
        ok(h == sorted(h, key=lambda e: e.get("quando_utc") or "", reverse=True),
           "do mais recente para o mais antigo")

        vazio = cart.municipal(ix, OUTRA)
        ok(vazio["status"] == reg.NAO_CONSULTADO
           and vazio["quantidade_documentos"] == 0,
           "empresa sem documento municipal: não consultada, não pendente")
        ok(vazio["sem_leitura"] == "",
           "e sem motivo de leitura, porque não há documento")


# ══════════════════════════════════════════════════════════════════════════
secao("9 · Não existe consulta automática municipal (leitura do código)")

SERV = (RAIZ / "fiscale_server.py").read_text("utf-8")
_i = SERV.index('# /api/situacao/consultar')
_f = SERV.index('if rota == "/api/situacao/importar"')
BLOCO = SERV[_i:_f]
ok('esfera != "FEDERAL"' in BLOCO,
   "a rota de consulta recusa qualquer esfera que não a federal")
ok("somente na esfera" in BLOCO, "com mensagem explicando")
ok("ASSISTIDA_RECIFE" in SERV,
   "e a origem do canal assistido é aceita na importação")

CART = (RAIZ / "situacao" / "carteira.py").read_text("utf-8")
for proibido in ("requests", "urlopen", "http", "captcha_resolver", "OCR"):
    ok(proibido not in CART, "`carteira.py` não menciona `%s`" % proibido)

TELA = (RAIZ / "web" / "situacao.html").read_text("utf-8")
ok("cartaoMunicipal" in TELA, "a tela tem o cartão municipal")
ok("consultarMunicipal" not in TELA,
   "e nenhuma função de consulta municipal")
ok("entra_no_consolidado" in TELA,
   "a tela mostra que o municipal não entra no consolidado")
ok("sem_leitura" in TELA, "e mostra por que a validade não foi lida")


print("\n%d ok · %d falha(s)" % (_ok, _falhas))
sys.exit(1 if _falhas else 0)
