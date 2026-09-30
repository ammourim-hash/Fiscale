#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O índice, o portão e a credencial — o envelope selado.

    python teste_situacao_indice.py

O QUE SE PROTEGE AQUI
    • que certidão e extrato NÃO compartilhem a mesma conta de estado;
    • que o índice possa ser jogado fora e refeito a partir do disco;
    • que o portão seja a única porta, e que ele grave ANTES de indexar;
    • que a tentativa nasça inclusive quando não há documento;
    • que o procurador venha da configuração, e nunca do código.

NÃO TOCA REDE NEM A PASTA REAL.
"""
from __future__ import annotations

import datetime as dt
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

from situacao import armazenamento as arm       # noqa: E402
from situacao import credencial as cred         # noqa: E402
from situacao import importacao as portao       # noqa: E402
from situacao import indice as idx              # noqa: E402
from situacao import modelo as mod              # noqa: E402
from situacao.fontes import Colheita            # noqa: E402
from situacao.fontes import sitfis as sf        # noqa: E402

_ok = _falhas = 0
_erros: list = []

PDF_A = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
PDF_B = b"%PDF-1.4\n1 0 obj<</Type/Catalog/X 2>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
HTML = b"<html><body>Sessao expirada</body></html>"

MONTE = "05678005000191"
TRX = "64567004000139"
HOJE = dt.date(2026, 9, 7)


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


def main():
    base = apoio.pasta_temp("fiscale_indice_")
    ix = idx.abrir(base)
    try:
        # ═══════════════════════════════════════════════════════════════
        secao("O portão é a única porta, e grava antes de indexar")
        r = portao.importar(base, ix, MONTE, mod.FEDERAL, mod.CERTIDAO, PDF_A,
                            origem="ASSISTIDA",
                            leitura={"validade": "2027-03-01",
                                     "emissao": "2026-09-01",
                                     "natureza": mod.NEGATIVA,
                                     "codigo": "ABC123", "leitura_versao": 1})
        igual(r["desfecho"], arm.NOVO, "primeira entrada → NOVO")
        ok(Path(r["caminho"]).exists(), "o arquivo está no disco")
        igual(len(ix.documentos(MONTE)), 1, "e o índice tem uma linha")
        # O ÍNDICE NUNCA APONTA PARA ARQUIVO QUE NÃO EXISTE.
        ok(all(Path(d["caminho"]).exists() for d in ix.documentos()),
           "todo caminho indexado aponta para arquivo existente")

        # ═══════════════════════════════════════════════════════════════
        secao("O que não é documento não entra, e a recusa fica registrada")
        levanta(lambda: portao.importar(base, ix, MONTE, mod.FEDERAL,
                                        mod.CERTIDAO, HTML),
                portao.Recusado, "página de erro do portal é RECUSADA")
        levanta(lambda: portao.importar(base, ix, MONTE, mod.FEDERAL,
                                        mod.CERTIDAO, b""),
                portao.Recusado, "documento vazio é recusado")
        levanta(lambda: portao.importar(base, ix, "123", mod.FEDERAL,
                                        mod.CERTIDAO, PDF_A),
                portao.Recusado, "identidade fora de formato é recusada")
        levanta(lambda: portao.importar(base, ix, MONTE, "FEDARAL",
                                        mod.CERTIDAO, PDF_A),
                portao.Recusado, "esfera com typo é recusada")
        igual(len(ix.documentos(MONTE)), 1, "e nada disso virou documento")
        recusas = [t for t in ix.tentativas(MONTE)
                   if t["desfecho"] == mod.ILEGIVEL]
        igual(len(recusas), 2, "as duas recusas de conteúdo viraram tentativa")

        # ═══════════════════════════════════════════════════════════════
        secao("CERTIDÃO responde pela VALIDADE")
        st = ix.estado(MONTE, mod.FEDERAL, mod.CERTIDAO, HOJE)
        igual(st["estado"], mod.VALIDA, "validade em março → VÁLIDA")
        igual(st["regular"], True, "e negativa é regular")
        igual(ix.estado(MONTE, mod.FEDERAL, mod.CERTIDAO,
                        dt.date(2027, 2, 20))["estado"], mod.VENCE_EM_BREVE,
              "perto do fim → VENCE EM BREVE")
        igual(ix.estado(MONTE, mod.FEDERAL, mod.CERTIDAO,
                        dt.date(2027, 4, 1))["estado"], mod.VENCIDA,
              "depois → VENCIDA")

        # ═══════════════════════════════════════════════════════════════
        secao("EXTRATO responde pela IDADE — e nunca 'vence'")
        portao.importar(base, ix, MONTE, mod.FEDERAL, mod.EXTRATO, PDF_B,
                        origem="SITFIS",
                        leitura={"apurado_em": "2026-09-05",
                                 "leitura_versao": 1})
        igual(ix.estado(MONTE, mod.FEDERAL, mod.EXTRATO, HOJE)["estado"],
              mod.APURADO, "apurado há 2 dias → APURADO")
        velho = ix.estado(MONTE, mod.FEDERAL, mod.EXTRATO,
                          dt.date(2026, 12, 1))["estado"]
        igual(velho, mod.DESATUALIZADO, "3 meses depois → DESATUALIZADO")
        ok(velho != mod.VENCIDA,
           "e NUNCA 'vencido' — extrato não tem carimbo de prazo")
        # A prova de que são contas diferentes: o mesmo dia, respostas de
        # naturezas distintas para os dois tipos da MESMA empresa e esfera.
        dia = dt.date(2026, 12, 1)
        c = ix.estado(MONTE, mod.FEDERAL, mod.CERTIDAO, dia)["estado"]
        e = ix.estado(MONTE, mod.FEDERAL, mod.EXTRATO, dia)["estado"]
        igual((c, e), (mod.VALIDA, mod.DESATUALIZADO),
              "no MESMO dia: certidão válida e extrato desatualizado")

        # ═══════════════════════════════════════════════════════════════
        secao("Sem leitura, o estado é 'não sei' — e não 'tudo certo'")
        portao.importar(base, ix, TRX, mod.MUNICIPAL, mod.CERTIDAO, PDF_A,
                        origem="ASSISTIDA")
        st = ix.estado(TRX, mod.MUNICIPAL, mod.CERTIDAO, HOJE)
        igual(st["estado"], mod.SEM_VALIDADE,
              "documento guardado sem validade lida → SEM_VALIDADE")
        igual(st["regular"], None, "e a regularidade é None, não False")
        igual(ix.estado(TRX, mod.ESTADUAL, mod.CERTIDAO, HOJE)["estado"],
              mod.SEM_DOCUMENTO, "onde não há nada → SEM_DOCUMENTO")

        # ═══════════════════════════════════════════════════════════════
        secao("A bilhetagem tem freio")
        precisa, motivo = ix.precisa_consultar(MONTE, mod.FEDERAL,
                                               mod.CERTIDAO, HOJE)
        ok(not precisa, "com certidão válida NÃO se gasta chamada (%s)" % motivo)
        precisa, motivo = ix.precisa_consultar(TRX, mod.ESTADUAL,
                                               mod.CERTIDAO, HOJE)
        ok(precisa, "sem documento, precisa consultar (%s)" % motivo)
        precisa, _ = ix.precisa_consultar(MONTE, mod.FEDERAL, mod.CERTIDAO,
                                          dt.date(2027, 4, 1))
        ok(precisa, "vencida, precisa de novo")
        precisa, _ = ix.precisa_consultar(TRX, mod.MUNICIPAL, mod.CERTIDAO,
                                          HOJE)
        ok(precisa, "e 'não sei até quando vale' também manda consultar")

        # ═══════════════════════════════════════════════════════════════
        secao("Quem vence nos próximos 30 dias — só certidão")
        vencendo = ix.a_vencer(30, dt.date(2027, 2, 20))
        igual(len(vencendo), 1, "a certidão da MONTE aparece")
        igual(vencendo[0]["tipo"], mod.CERTIDAO, "e é certidão")
        ok(all(v["tipo"] == mod.CERTIDAO for v in ix.a_vencer(3650, HOJE)),
           "nenhum extrato entra nessa lista, por mais velho que seja")

        # ═══════════════════════════════════════════════════════════════
        secao("O panorama do escritório")
        pan = ix.panorama([MONTE, TRX], HOJE)
        igual(len(pan), 2 * len(mod.ESFERAS) * len(mod.TIPOS),
              "uma linha por empresa × esfera × tipo")
        sem = [p for p in pan if p["estado"] == mod.SEM_DOCUMENTO]
        # 2 empresas x 3 esferas x 2 tipos = 12; ha 3 documentos, entao 9
        # lacunas. (A MUNICIPAL da TRX tem documento sem validade lida: ela
        # conta como preenchida, e aparece como SEM_VALIDADE, nao aqui.)
        igual(len(sem), 9, "e as lacunas aparecem como SEM_DOCUMENTO")

        # ═══════════════════════════════════════════════════════════════
        secao("O índice é descartável: some e volta do disco")
        antes = {(d["identidade"], d["esfera"], d["tipo"], d["id"])
                 for d in ix.documentos()}
        igual(len(antes), 3, "há 3 documentos indexados")
        n_tent = len(ix.tentativas(limite=999))
        armazens = [arm.abrir(base, MONTE), arm.abrir(base, TRX)]
        igual(ix.reconstruir(armazens), 3, "a reconstrução varre os 3")
        depois = {(d["identidade"], d["esfera"], d["tipo"], d["id"])
                  for d in ix.documentos()}
        igual(depois, antes, "e o conjunto volta idêntico")
        igual(len(ix.tentativas(limite=999)), n_tent,
              "as TENTATIVAS não são tocadas — histórico não se reconstrói")

        # A leitura que veio da FONTE sobrevive; a que veio de parser, não.
        # Isso é correto e precisa ser visível.
        st = ix.estado(MONTE, mod.FEDERAL, mod.CERTIDAO, HOJE)
        igual(st["estado"], mod.SEM_VALIDADE,
              "sem leitor, a validade LIDA se perde na reconstrução")

        # ═══════════════════════════════════════════════════════════════
        secao("O que a FONTE entregou sobrevive à reconstrução")
        # `extra` é proveniência: a API devolveu validade e natureza em JSON.
        portao.importar(base, ix, TRX, mod.FEDERAL, mod.CERTIDAO, PDF_B,
                        origem="CONSULTA_CND",
                        extra={"validade": "2027-05-10",
                               "natureza": mod.CPD_EN})
        ix.reconstruir([arm.abrir(base, MONTE), arm.abrir(base, TRX)])
        st = ix.estado(TRX, mod.FEDERAL, mod.CERTIDAO, HOJE)
        igual(st["estado"], mod.VALIDA,
              "a validade que a FONTE deu volta sozinha")
        igual(st["regular"], True, "e a CPD-EN continua contando como regular")
        igual(st["natureza"], mod.CPD_EN, "com a natureza preservada")

        # ═══════════════════════════════════════════════════════════════
        secao("Com um leitor, a reconstrução recupera tudo")
        def leitor(bruto):
            return {"validade": "2027-03-01", "natureza": mod.NEGATIVA,
                    "leitura_versao": 2} if bruto == PDF_A else {}
        ix.reconstruir([arm.abrir(base, MONTE), arm.abrir(base, TRX)],
                       leitor=leitor)
        igual(ix.estado(MONTE, mod.FEDERAL, mod.CERTIDAO, HOJE)["estado"],
              mod.VALIDA, "o leitor devolve a validade ao índice")

        def leitor_quebrado(bruto):
            raise RuntimeError("parser em pane")
        n = ix.reconstruir([arm.abrir(base, MONTE)], leitor=leitor_quebrado)
        igual(n, 2, "leitor que explode NÃO faz perder o documento")

        # ═══════════════════════════════════════════════════════════════
        secao("Colheita sem documento vira tentativa, e nada mais")
        ix.reconstruir([arm.abrir(base, MONTE), arm.abrir(base, TRX)])
        quantos = len(ix.documentos())
        r = portao.de_colheita(base, ix, TRX, mod.ESTADUAL, mod.CERTIDAO,
                               Colheita(mod.NAO_EMITIDA,
                                        detalhe="há pendência de ICMS",
                                        origem="ASSISTIDA"))
        igual(r["guardado"], False, "não guardou nada")
        igual(len(ix.documentos()), quantos, "o índice não cresceu")
        t = ix.tentativas(TRX)[0]
        igual(t["desfecho"], mod.NAO_EMITIDA, "mas a tentativa foi registrada")
        ok("ICMS" in t["detalhe"], "com o motivo que o órgão deu")
        igual(ix.estado(TRX, mod.ESTADUAL, mod.CERTIDAO, HOJE)["estado"],
              mod.SEM_DOCUMENTO,
              "e o estado continua SEM_DOCUMENTO — recusa não é certidão")

        # ═══════════════════════════════════════════════════════════════
        secao("Colheita COM documento passa pelo portão inteiro")
        r = portao.de_colheita(base, ix, TRX, mod.FEDERAL, mod.EXTRATO,
                               Colheita(mod.OBTIDA, documento=PDF_A,
                                        origem="SITFIS"),
                               leitura={"apurado_em": "2026-09-06"})
        igual(r["guardado"], True, "guardou")
        igual(ix.estado(TRX, mod.FEDERAL, mod.EXTRATO, HOJE)["estado"],
              mod.APURADO, "e o extrato entrou como APURADO")

        # ═══════════════════════════════════════════════════════════════
        secao("A credencial: o procurador vem do arquivo, nunca do código")
        conf = base / cred.ARQUIVO
        conf.write_text(json.dumps({
            "contratante": "00000000000000",
            "ambiente": "trial",
            "padrao": "procurador-um",
            "procuradores": [
                {"apelido": "procurador-um", "documento": "99999999999",
                 "certificado_id": "cert-A", "ativo": True},
                {"apelido": "procurador-dois", "documento": "88888888888",
                 "certificado_id": "cert-B", "ativo": True},
            ]}), encoding="utf-8")
        cofre = cred.abrir(base)
        c = cofre.escolher()
        igual(c.procurador.apelido, "procurador-um", "o padrão é respeitado")
        igual(c.contratante, "00000000000000", "o contratante é o do contrato")
        igual(c.autor, "99999999999",
              "e o AUTOR é o procurador — não o contratante")
        ok(c.autor != c.contratante,
           "os dois são documentos DIFERENTES, e é isso que produção exige")
        igual(cofre.escolher("procurador-dois").certificado_id, "cert-B",
              "dá para escolher outro procurador pelo apelido")
        levanta(lambda: cofre.escolher("ninguem"), cred.SemCredencial,
                "apelido inexistente é recusado")

        # Nenhuma identidade real no código.
        fonte_cred = (RAIZ / "situacao" / "credencial.py").read_text("utf-8")
        fonte_sitfis = (RAIZ / "situacao" / "fontes" / "sitfis.py").read_text("utf-8")
        import re as _re
        for nome, txt in (("credencial.py", fonte_cred),
                          ("sitfis.py", fonte_sitfis)):
            achados = [d for d in _re.findall(r"\b\d{11,14}\b", txt)
                       if d not in ("00000000000000", "99999999999")]
            igual(achados, [], "nenhum documento real embutido em %s" % nome)

        # ═══════════════════════════════════════════════════════════════
        secao("Sem padrão e com dois candidatos, ele NÃO escolhe sozinho")
        d = json.loads(conf.read_text(encoding="utf-8"))
        del d["padrao"]
        conf.write_text(json.dumps(d), encoding="utf-8")
        levanta(lambda: cred.abrir(base).escolher(), cred.SemCredencial,
                "ambiguidade vira erro, e não 'o primeiro da lista'")
        d["procuradores"][1]["ativo"] = False
        conf.write_text(json.dumps(d), encoding="utf-8")
        igual(cred.abrir(base).escolher().procurador.apelido, "procurador-um",
              "com um só ativo, não há ambiguidade")

        # ═══════════════════════════════════════════════════════════════
        secao("Segredo não é gravado em claro")
        cofre = cred.abrir(base)
        levanta(lambda: cofre.guardar_segredo("consumer_key", "abc"),
                cred.SegredoEmClaro, "sem cofre, gravar segredo é RECUSADO")
        # Um cofre de mentira, mas que ESCONDE: prefixar o segredo o deixaria
        # legível no arquivo, e o teste de vazamento passaria a acusar o dublê
        # em vez do código.
        import base64 as _b64
        _cifrar = lambda s: _b64.b64encode(s.encode("utf-8")[::-1]).decode()
        _decifrar = lambda b: _b64.b64decode(b)[::-1].decode("utf-8")
        cofre2 = cred.abrir(base, proteger=_cifrar, desproteger=_decifrar)
        cofre2.guardar_segredo("consumer_key", "abc")
        cofre2.salvar()
        bruto = conf.read_text(encoding="utf-8")
        ok("abc" not in bruto, "o segredo não aparece em claro no arquivo")
        igual(cred.abrir(base, desproteger=_decifrar)
              .segredo("consumer_key"), "abc", "e volta com o cofre certo")
        igual(cred.abrir(base).segredo("consumer_key"), "",
              "sem cofre, o segredo simplesmente não é devolvido")

        # ═══════════════════════════════════════════════════════════════
        secao("O conector nasce da credencial, com o autor certo")
        d["padrao"] = "procurador-um"
        conf.write_text(json.dumps(d), encoding="utf-8")
        c = cred.abrir(base).escolher()
        chamadas = []

        def dublê(url, corpo, cab):
            chamadas.append(corpo)
            return 200, {"status": 400, "mensagens": [], "dados": ""}, b"{}"

        con = sf.de_credencial(c, transporte=dublê, dormir=lambda s: None)
        con.obter("99999999999999")
        igual(chamadas[0]["contratante"]["numero"], "00000000000000",
              "o contratante vai no lugar dele")
        igual(chamadas[0]["autorPedidoDados"]["numero"], "99999999999",
              "e o PROCURADOR no `autorPedidoDados`")
        igual(chamadas[0]["autorPedidoDados"]["tipo"], sf.PF,
              "reconhecido como pessoa física, que é o caso real")
        ok(con.base.endswith("integra-contador-trial/v1"),
           "ambiente trial → base do trial")
        d["ambiente"] = "producao"
        conf.write_text(json.dumps(d), encoding="utf-8")
        con2 = sf.de_credencial(cred.abrir(base).escolher(), transporte=dublê)
        ok(con2.base.endswith("integra-contador/v1") and "trial" not in con2.base,
           "ambiente produção → base de produção")

    finally:
        ix.fechar()
        shutil.rmtree(base, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
