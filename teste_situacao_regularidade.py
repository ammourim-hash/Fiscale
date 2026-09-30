#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A regra de regularidade — e a promessa de que falha nunca vira verde.

    python teste_situacao_regularidade.py

POR QUE ESTE TESTE EXISTE
    A central de regularidade responde a pergunta mais perigosa do módulo:
    "esta empresa está regular?". Um verde errado vai para um cliente, para
    uma licitação, para um banco. Este teste trava as regras que impedem isso:

    1. FALHA NUNCA É REGULAR: recusa, portal fora do ar, credencial ausente —
       tudo vira "precisa consultar", nunca verde;
    2. SEM DOCUMENTO NÃO É PENDÊNCIA: é "não consultado";
    3. o DOCUMENTO MAIS RECENTE responde, não o mais favorável;
    4. o leitor só afirma o que viu em amostra real: extrato sem a frase de
       ausência de pendências não vira "pendência" por dedução;
    5. a conclusão humana vence a automática — para AQUELE documento — e é
       gravada à parte, com autor, sem tocar no original;
    6. o consolidado é SÓ o federal (o municipal saiu do escopo em 12/09),
       e um documento municipal antigo no envelope não pesa na carteira;
    7. o índice v1 migra sem perder nada, e o 202/204 do SITFIS é tratado com
       repetição curta e limitada.

O QUE ESTE TESTE **NÃO** FAZ
    Não vai à rede, não usa certificado, não lê a pasta de dados real.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio as apoio                                  # noqa: E402
from situacao import (armazenamento, carteira, credencial,   # noqa: E402
                      importacao, indice, interpretacao, modelo,
                      regularidade as R)
from situacao.fontes import sitfis as sf                     # noqa: E402

_ok = _falhas = 0
_erros: list = []

ALFA = "11222333000181"
BETA = "66789006000106"
FIX = RAIZ / "situacao" / "fixturas" / "sitfis"
HOJE = dt.date(2026, 9, 12)


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
    ok(a == b, desc if a == b else "%s  (obtive %.160r, esperava %.160r)"
       % (desc, a, b))


def pdf_de(texto: str) -> bytes:
    from fpdf import FPDF
    p = FPDF()
    p.add_page()
    p.set_font("Helvetica", size=12)
    p.cell(0, 10, texto)
    return bytes(p.output())


def sitfis_limpo() -> bytes:
    """O PDF REAL do trial (mascarado pelo SERPRO), com a frase de ausência."""
    corpo = json.loads((FIX / "emitir-sucesso.json").read_text("utf-8"))
    return base64.b64decode(sf.ler_dados(corpo)["pdf"])


def doc(tipo, quando, **kw):
    d = {"id": hashlib.sha256((tipo + quando).encode()).hexdigest()[:32],
         "tipo": tipo, "capturado_utc": quando}
    d.update(kw)
    return d


def main():
    F, M = modelo.FEDERAL, modelo.MUNICIPAL

    # ═══════════════════════════════════════════════════════════════════
    secao("1 · Sem documento não é pendência, e sem consulta não é regular")
    r = R.avaliar_esfera(F, hoje=HOJE)
    igual(r["status"], R.NAO_CONSULTADO, "nada registrado -> NÃO CONSULTADO")
    igual(R.consolidar(r)["status"], R.G_NAO_CONSULTADO, "e o consolidado também")

    secao("2 · FALHA NUNCA VIRA REGULAR")
    for desfecho in (modelo.RECUSADA, modelo.INDISPONIVEL, modelo.ERRO,
                     modelo.ILEGIVEL):
        r = R.avaliar_esfera(F, tentativas=[{"quando_utc": "2026-09-12T10:00:00Z",
                                             "desfecho": desfecho,
                                             "detalhe": "x"}], hoje=HOJE)
        ok(r["status"] not in (R.REGULAR, R.CPD_EN),
           "%s não é regular (é %s)" % (desfecho, r["status"]))
        ok(R.grupo(r["status"]) == "necessaria",
           "e cai em 'consulta necessária'")
    r = R.avaliar_esfera(F, tentativas=[{"quando_utc": "2026-09-12T10:00:00Z",
                                         "desfecho": modelo.RECUSADA}],
                         hoje=HOJE, canal={"representacao": False,
                                           "motivo": "sem procurador"})
    igual(r["status"], R.REQUER_REPRESENTACAO,
          "recusa sem procurador configurado -> REQUER REPRESENTAÇÃO")
    r = R.avaliar_esfera(F, tentativas=[{"quando_utc": "2026-09-12T10:00:00Z",
                                         "desfecho": modelo.NAO_EMITIDA}],
                         hoje=HOJE)
    igual(r["status"], R.PENDENCIA,
          "NÃO EMITIDA é o órgão dizendo que há pendência -> PENDÊNCIA")

    # Regular com documento; com erro, nunca regular.
    fed_ok = R.avaliar_esfera(F, [doc("CERTIDAO", "2026-09-10T10:00:00Z",
                                      natureza=modelo.NEGATIVA,
                                      validade="2027-03-01")], hoje=HOJE)
    fed_erro = R.avaliar_esfera(F, tentativas=[{"quando_utc": "2026-09-12T10:00:00Z",
                                                "desfecho": modelo.ERRO}], hoje=HOJE)
    igual(fed_ok["status"], R.REGULAR, "certidão negativa válida -> REGULAR")
    igual(R.consolidar(fed_ok)["status"], R.G_REGULAR, "e o consolidado é REGULAR")
    igual(R.consolidar(fed_erro)["status"], R.G_CONSULTA_NECESSARIA,
          "federal com erro -> CONSULTA NECESSÁRIA, nunca regular")

    secao("3 · O documento MAIS RECENTE responde, não o mais favorável")
    velha_negativa = doc("CERTIDAO", "2026-08-01T10:00:00Z",
                         natureza=modelo.NEGATIVA, validade="2027-01-01")
    novo_positivo = doc("CERTIDAO", "2026-09-11T10:00:00Z",
                        natureza=modelo.POSITIVA, validade="2026-12-01")
    r = R.avaliar_esfera(F, [velha_negativa, novo_positivo], hoje=HOJE)
    igual(r["status"], R.POSITIVA, "a certidão positiva de ontem vence a negativa de agosto")
    r = R.avaliar_esfera(F, [novo_positivo, velha_negativa], hoje=HOJE)
    igual(r["status"], R.POSITIVA, "e a ordem da lista não muda a resposta")

    secao("4 · Validade e frescor: vencido não é regular")
    r = R.avaliar_esfera(F, [doc("CERTIDAO", "2026-01-01T10:00:00Z",
                                 natureza=modelo.NEGATIVA, validade="2026-09-11")],
                         hoje=HOJE)
    igual(r["status"], R.CONSULTA_EXPIRADA, "negativa vencida ontem -> CONSULTA EXPIRADA")
    igual(r.get("status_anterior"), R.REGULAR, "com o status antigo como contexto")
    r = R.avaliar_esfera(F, [doc("CERTIDAO", "2026-01-01T10:00:00Z",
                                 natureza=modelo.NEGATIVA, validade="2026-09-12")],
                         hoje=HOJE)
    igual(r["status"], R.REGULAR, "o dia da validade ainda vale")
    r = R.avaliar_esfera(F, [doc("EXTRATO", "2026-07-01T10:00:00Z",
                                 sem_pendencias=1, apurado_em="2026-07-01")], hoje=HOJE)
    igual(r["status"], R.CONSULTA_EXPIRADA, "extrato limpo de julho -> expirado")
    r = R.avaliar_esfera(F, [doc("CERTIDAO", "2026-07-01T10:00:00Z",
                                 natureza=modelo.NEGATIVA)], hoje=HOJE)
    igual(r["status"], R.CONSULTA_EXPIRADA,
          "certidão SEM validade lida envelhece como extrato (nunca emissão+180)")

    secao("5 · O leitor só afirma o que viu em amostra real")
    r = R.avaliar_esfera(F, [doc("EXTRATO", "2026-09-12T08:00:00Z",
                                 sem_pendencias=1)], hoje=HOJE)
    igual(r["status"], R.REGULAR, "extrato com a frase de ausência -> REGULAR")
    igual(r["fonte_status"], "LEITOR", "e diz que foi o leitor")
    r = R.avaliar_esfera(F, [doc("EXTRATO", "2026-09-12T08:00:00Z")], hoje=HOJE)
    igual(r["status"], R.REQUER_INTERACAO,
          "extrato SEM a frase -> REQUER INTERAÇÃO, e NÃO pendência deduzida")
    r = R.avaliar_esfera(F, [doc("CERTIDAO", "2026-09-12T08:00:00Z")], hoje=HOJE)
    igual(r["status"], R.REQUER_INTERACAO,
          "certidão cuja natureza não foi lida -> REQUER INTERAÇÃO")

    secao("6 · A conclusão humana vence a automática — para AQUELE documento")
    d_novo = doc("EXTRATO", "2026-09-12T08:00:00Z", sem_pendencias=1)
    d_velho = doc("EXTRATO", "2026-09-01T08:00:00Z")
    humana = {"documento_id": d_novo["id"], "status": R.PENDENCIA,
              "quantidade_pendencias": 3, "valor_total": 1240.5,
              "usuario": "aline", "quando_utc": "2026-09-12T09:00:00Z"}
    r = R.avaliar_esfera(F, [d_novo, d_velho], interpretacoes=[humana], hoje=HOJE)
    igual(r["status"], R.PENDENCIA, "a pessoa leu pendência onde o leitor viu a frase")
    igual(r["fonte_status"], "HUMANA", "e a fonte diz que foi uma pessoa")
    igual(r["quantidade_pendencias"], 3, "com a quantidade")
    outra = dict(humana, documento_id=d_velho["id"], status=R.REGULAR)
    r = R.avaliar_esfera(F, [d_novo, d_velho], interpretacoes=[outra], hoje=HOJE)
    igual(r["status"], R.REGULAR,
          "classificação de OUTRO documento não contamina o atual (vale o leitor)")
    igual(r["fonte_status"], "LEITOR", "confirmado pela fonte")

    secao("7 · Consolidado: é o federal, e só ele")
    for st, esperado, desc in (
            (R.REGULAR, R.G_REGULAR, "regular -> REGULAR"),
            (R.CPD_EN, R.G_REGULAR, "CPD-EN vale como regular no consolidado"),
            (R.PENDENCIA, R.G_COM_PENDENCIA, "pendência -> COM PENDÊNCIA"),
            (R.POSITIVA, R.G_COM_PENDENCIA, "certidão positiva -> COM PENDÊNCIA"),
            (R.NAO_CONSULTADO, R.G_NAO_CONSULTADO, "nada -> NÃO CONSULTADO"),
            (R.CONSULTA_EXPIRADA, R.G_CONSULTA_NECESSARIA, "expirada nunca é regular"),
            (R.ERRO_ACESSO, R.G_CONSULTA_NECESSARIA, "erro de acesso nunca é regular"),
            (R.REQUER_REPRESENTACAO, R.G_CONSULTA_NECESSARIA, "sem representação nunca é regular"),
            (R.REQUER_INTERACAO, R.G_CONSULTA_NECESSARIA, "requer interação nunca é regular")):
        igual(R.consolidar({"status": st})["status"], esperado, desc)
    ok(not hasattr(R, "G_PENDENCIA_MUNICIPAL") and not hasattr(R, "G_CRITICA"),
       "não existe mais status consolidado que cruze o município")
    ok(not hasattr(R, "NAO_APLICAVEL"), "nem esfera 'não se aplica'")

    # ═══════════════════════════════════════════════════════════════════
    with apoio.raiz_temporaria("sit_regularidade_") as raiz:
        ix = indice.abrir(raiz)
        try:
            secao("8 · O portão grava QUEM, COMO e o HASH na tentativa")
            r = importacao.importar(raiz, ix, ALFA, M, modelo.EXTRATO,
                                    pdf_de("EXTRATO DE DEBITOS RECIFE"),
                                    origem=modelo.ASSISTIDA_RECIFE,
                                    usuario="aline", metodo=modelo.METODO_UPLOAD)
            t = ix.tentativas(ALFA)[0]
            igual(t["usuario"], "aline", "usuário na tentativa")
            igual(t["metodo"], "UPLOAD", "método na tentativa")
            igual(t["sha256"], r["sha256"], "e o hash do documento guardado")

            secao("9 · Interpretação: arquivo à parte, com autor, sem tocar no PDF")
            original = Path(r["caminho"]).read_bytes()
            try:
                interpretacao.registrar(raiz, ix, ALFA, M, modelo.EXTRATO, r["id"],
                                        R.PENDENCIA, usuario="")
                ok(False, "classificação sem usuário deveria ser recusada")
            except interpretacao.Recusada:
                ok(True, "classificação sem usuário é recusada")
            try:
                interpretacao.registrar(raiz, ix, ALFA, M, modelo.EXTRATO, r["id"],
                                        R.REGULAR, usuario="aline",
                                        quantidade_pendencias=2)
                ok(False, "regular com 2 pendências deveria ser recusado")
            except interpretacao.Recusada:
                ok(True, "regular com pendências é contraditório e é recusado")
            try:
                interpretacao.registrar(raiz, ix, ALFA, M, modelo.EXTRATO, r["id"],
                                        R.ERRO_ACESSO, usuario="aline")
                ok(False, "erro não é classificação")
            except interpretacao.Recusada:
                ok(True, "'erro de acesso' não se classifica — é fato da tentativa")

            reg1 = interpretacao.registrar(
                raiz, ix, ALFA, M, modelo.EXTRATO, r["id"], R.PENDENCIA, "aline",
                valor_total="1.240,50",
                pendencias=[{"tributo": "ISS", "competencia": "07/2026",
                             "vencimento": "10/08/2026", "principal": "1.000,00",
                             "encargos": "240,50", "total": "1.240,50",
                             "situacao": "em aberto"}])
            igual(reg1["valor_total"], 1240.5, "valor digitado no formato brasileiro")
            igual(reg1["quantidade_pendencias"], 1, "quantidade vem da lista")
            igual(reg1["pendencias"][0]["encargos"], 240.5, "encargos numéricos")
            ok(Path(r["caminho"]).read_bytes() == original,
               "o original.pdf continua byte a byte igual")
            pasta_i = Path(r["caminho"]).parent / interpretacao.PASTA
            igual(len(list(pasta_i.glob("*.json"))), 1, "um arquivo de interpretação")

            reg2 = interpretacao.registrar(raiz, ix, ALFA, M, modelo.EXTRATO,
                                           r["id"], R.REGULAR, "pedro",
                                           observacao="débito pago em 11/09")
            igual(len(list(pasta_i.glob("*.json"))), 2,
                  "reclassificar SUCEDE: o arquivo anterior fica")
            ints = ix.interpretacoes(ALFA, M)
            igual(ints[0]["usuario"], "pedro", "a mais recente primeiro")
            igual(ints[1]["usuario"], "aline", "a anterior continua no histórico")

            secao("10 · A interpretação sobrevive à reconstrução do índice")
            ix.con.execute("DELETE FROM interpretacao")
            ix.con.commit()
            igual(ix.interpretacoes(ALFA), [], "índice zerado")
            n = interpretacao.reconstruir(raiz, ix, [ALFA])
            igual(n, 2, "as duas voltam do disco")
            igual(ix.interpretacoes(ALFA)[0]["id"], reg2["id"], "na mesma ordem")

            secao("11 · Documento adulterado não é classificado")
            Path(r["caminho"]).write_bytes(original + b"%adulterado")
            try:
                interpretacao.registrar(raiz, ix, ALFA, M, modelo.EXTRATO, r["id"],
                                        R.REGULAR, "aline")
                ok(False, "deveria recusar documento que não confere")
            except interpretacao.Recusada as e:
                ok("hash" in str(e), "recusado citando o hash: %s" % e)
            Path(r["caminho"]).write_bytes(original)

            secao("12 · A carteira: só a federal, canal e contadores")
            ok(not hasattr(carteira, "eh_recife"), "a carteira não conhece mais o Recife")

            # o SITFIS real, lido pelo leitor calibrado
            importacao.importar(raiz, ix, BETA, F, modelo.EXTRATO, sitfis_limpo(),
                                origem=modelo.SITFIS, usuario="",
                                metodo=modelo.METODO_INTEGRACAO)
            empresas = [{"identidade": ALFA, "nome": "ALFA"},
                        {"identidade": BETA, "nome": "BETA"}]
            cf = {"automatico": False, "representacao": True, "motivo": "x"}
            c = carteira.montar(ix, empresas, cf)
            linhas = {l["identidade"]: l for l in c["empresas"]}
            igual(linhas[BETA]["federal"]["status"], R.REGULAR,
                  "o SITFIS real (frase de ausência) chega REGULAR pela carteira")
            igual(linhas[BETA]["geral"]["status"], R.G_REGULAR,
                  "e o consolidado é REGULAR")
            ok("municipal" not in linhas[ALFA] and "im" not in linhas[ALFA],
               "a linha não tem mais esfera municipal nem inscrição")
            igual(linhas[ALFA]["federal"]["status"], R.NAO_CONSULTADO,
                  "ALFA federal: nada consultado")
            igual(linhas[ALFA]["geral"]["status"], R.G_NAO_CONSULTADO,
                  "o extrato MUNICIPAL classificado regular (pedro) NÃO pesa: não consultado")
            igual(c["contadores"], {"empresas": 2, "regulares": 1, "com_pendencia": 0,
                                    "nao_consultadas": 1, "consulta_necessaria": 0,
                                    "erro": 0}, "contadores do topo")
            igual(carteira.historico(ix, ALFA), [],
                  "o histórico da central é só federal: o municipal antigo não aparece")

            # uma classificação FEDERAL entra no histórico, com autor
            fed_doc = ix.documentos(BETA, F)[0]
            interpretacao.registrar(raiz, ix, BETA, F, modelo.EXTRATO, fed_doc["id"],
                                    R.PENDENCIA, "aline", quantidade_pendencias=2)
            c = carteira.montar(ix, empresas, cf)
            linhas = {l["identidade"]: l for l in c["empresas"]}
            igual(linhas[BETA]["geral"]["status"], R.G_COM_PENDENCIA,
                  "a leitura humana de pendência no SITFIS vira COM PENDÊNCIA")
            hist = carteira.historico(ix, BETA)
            ok({h["evento"] for h in hist} == {"CONSULTA", "CLASSIFICACAO"},
               "o histórico junta consultas e classificações federais")
            ok(all(h.get("esfera") == F for h in hist), "todas federais")

            secao("13 · O canal federal diz o que falta — sem abrir segredo")
            cofre = credencial.Cofre(raiz / "nao-existe.json")
            igual(carteira.canal_federal(cofre)["representacao"], False,
                  "sem procurador -> representação ausente")
            cofre.dados = {"contratante": "04103256000185", "ambiente": "producao",
                           "procuradores": [{"apelido": "k", "documento": "12345678909"}],
                           "segredos": {"jwt_token": {"protegido": "xx"}}}
            cfp = carteira.canal_federal(cofre)
            ok(not cfp["automatico"], "produção sem consumer_key/secret não é automático")
            ok("consumer_key" in cfp["motivo"], "e o motivo diz o que falta: %s" % cfp["motivo"])
            cofre.dados["segredos"].update({"consumer_key": {"protegido": "a"},
                                            "consumer_secret": {"protegido": "b"}})
            ok(carteira.canal_federal(cofre)["automatico"],
               "com as duas chaves presentes (sem decifrar) -> automático")
        finally:
            ix.fechar()

        secao("14 · O índice v1 migra sem perder linha")
        v1 = raiz / "v1"
        v1.mkdir()
        con = sqlite3.connect(v1 / indice.ARQUIVO)
        con.executescript("""
            CREATE TABLE documento (id TEXT NOT NULL, identidade TEXT NOT NULL,
              esfera TEXT NOT NULL, tipo TEXT NOT NULL, jurisdicao TEXT NOT NULL DEFAULT '',
              sha256 TEXT NOT NULL, bytes INTEGER NOT NULL DEFAULT 0,
              caminho TEXT NOT NULL DEFAULT '', origem TEXT NOT NULL DEFAULT '',
              capturado_utc TEXT NOT NULL DEFAULT '', natureza TEXT NOT NULL DEFAULT '',
              emissao TEXT NOT NULL DEFAULT '', validade TEXT NOT NULL DEFAULT '',
              apurado_em TEXT NOT NULL DEFAULT '', codigo TEXT NOT NULL DEFAULT '',
              valor_total REAL, leitura_versao INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (identidade, esfera, tipo, id));
            CREATE TABLE tentativa (id INTEGER PRIMARY KEY AUTOINCREMENT,
              identidade TEXT NOT NULL, esfera TEXT NOT NULL, tipo TEXT NOT NULL,
              quando_utc TEXT NOT NULL, desfecho TEXT NOT NULL,
              detalhe TEXT NOT NULL DEFAULT '', origem TEXT NOT NULL DEFAULT '',
              documento_id TEXT NOT NULL DEFAULT '');
            INSERT INTO tentativa (identidade, esfera, tipo, quando_utc, desfecho, detalhe, origem)
              VALUES ('15862792000180','FEDERAL','EXTRATO','2026-09-09T02:30:54.311Z',
                      'RECUSADA','Falta o token','SITFIS');
        """)
        con.commit()
        con.close()
        with indice.abrir(v1) as ix1:
            ts = ix1.tentativas()
            igual(len(ts), 1, "a tentativa de 09/09 continua lá")
            igual(ts[0]["usuario"], "", "com a coluna nova vazia")
            ix1.registrar_tentativa(ALFA, F, modelo.EXTRATO, modelo.OBTIDA,
                                    usuario="x", metodo="UPLOAD")
            igual(len(ix1.tentativas()), 2, "e o índice migrado aceita escrita nova")
            igual(ix1.interpretacoes(), [], "a tabela de interpretação nasceu vazia")
        with indice.abrir(v1) as ix1:
            igual(len(ix1.tentativas()), 2, "reabrir não migra duas vezes")

    # ═══════════════════════════════════════════════════════════════════
    secao("15 · SITFIS: relatório em processamento (202/204), repetição limitada")
    _, apoiar, bruto_a = 200, json.loads((FIX / "apoiar-sucesso.json").read_text("utf-8")), b""
    emitir = json.loads((FIX / "emitir-sucesso.json").read_text("utf-8"))

    class Duble:
        def __init__(self, *resp):
            self.resp = list(resp)
            self.n = 0

        def __call__(self, url, corpo, cab):
            self.n += 1
            return self.resp.pop(0)

    dormiu = []
    processando = {"status": 202, "dados": json.dumps({"tempoEspera": 2500}),
                   "mensagens": [{"texto": "A emissão relatório de situação fiscal "
                                           "está em processamento"}]}
    d = Duble((200, apoiar, b""), (200, processando, b""), (204, None, b""),
              (200, emitir, b""))
    col = sf.ConectorSitfis(d, "00000000000000", dormir=dormiu.append).obter(ALFA)
    igual(col.desfecho, modelo.OBTIDA, "202 no corpo e depois 204: espera e obtém")
    igual(d.n, 4, "com exatamente 2 repetições do /Emitir")
    ok(2.5 in dormiu, "respeitando o tempoEspera NOVO que o serviço mandou")

    dormiu = []
    d = Duble((200, apoiar, b""), *[(204, None, b"")] * (sf.REPETICOES_EMITIR + 1))
    col = sf.ConectorSitfis(d, "00000000000000", dormir=dormiu.append).obter(ALFA)
    igual(col.desfecho, modelo.INDISPONIVEL, "204 que não acaba -> INDISPONÍVEL")
    igual(d.n, 2 + sf.REPETICOES_EMITIR, "sem laço aberto: %d chamadas" % d.n)
    ok(not col.tem_documento, "e nada foi tratado como documento")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
