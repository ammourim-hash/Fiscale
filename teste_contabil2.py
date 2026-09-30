#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CONTÁBIL 2 — Fiscal → Contábil por competência.

    python teste_contabil2.py

AS PROMESSAS QUE ESTA SUÍTE SEGURA
    • o documento entra no Contábil pela competência DELE, e mês nenhum
      empresta documento para o outro;
    • um fato por documento: sincronizar de novo não cria o segundo;
    • documento cancelado/denegado não vira fato com efeito, e se já havia
      lançamento isso aparece como DIVERGÊNCIA — não como conserto silencioso;
    • a soma dos fatos é a soma dos documentos que os compõem;
    • toda sugestão sabe voltar ao XML: id, caminho e hash;
    • nenhum lançamento nasce definitivo — nem o gerado do Fiscal;
    • competência FECHADA barra escrita e só reabre com motivo, no histórico;
    • empresa A e empresa B não se enxergam.

OS DOCUMENTOS SÃO OS DAS FIXTURAS DO PROJETO
    `teste_fixturas_nfe` e `teste_fixturas_fiscal` produzem XML com a mesma
    estrutura dos reais (e DV calculado), gravados num acervo temporário pelo
    caminho de produção — `acervo.preservar` + `indice.reconstruir`. A
    contraprova contra o acervo REAL do escritório está no relatório da fase:
    ela é leitura, roda fora da suíte e não escreve na produção.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                      # noqa: E402
import teste_fixturas_fiscal as fxs              # noqa: E402
import teste_fixturas_nfe as fxn                 # noqa: E402

from ingestao import acervo as acv               # noqa: E402
from ingestao import indice as ind               # noqa: E402

from contabil import (base, competencias, fatos, lancamentos,  # noqa: E402
                      modelo, plano, rotas)

_ok = _falhas = 0
_erros: list = []

A = fxn.EMPRESA_A
B = fxn.EMPRESA_B
FORNECEDOR = fxn.FORNECEDOR
TOMADOR = fxs.TOMADOR


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
        ok(False, "%s  (obtive %.160r, esperava %.160r)" % (desc, a, b))


def recusa(fn, desc, trecho=""):
    try:
        fn()
    except modelo.Invalido as e:
        ok(trecho.lower() in str(e).lower(),
           "%s — recusado: %s" % (desc, str(e)[:90]))
        return
    ok(False, desc + " — deveria ter sido recusado")


# ── O acervo fiscal do teste ───────────────────────────────────────────────
def guardar_nfe(dados, empresa, **kw):
    """Grava uma NF-e no acervo pelo caminho de produção e devolve o id."""
    chave = kw.pop("chave")
    xml = fxn.xml_nfe(chave, **kw)
    p = acv.abrir(dados, empresa).preservar(fxn.bruto(xml, kw.get("numero", "1"),
                                                      chave=chave))
    return p.id_documento


def montar_plano(b):
    plano.criar(b, "1", "ATIVO", "SINTETICA", "DEVEDORA", "ATIVO")
    cli = plano.criar(b, "1.1", "Clientes", "ANALITICA", "DEVEDORA")
    mer = plano.criar(b, "1.2", "Mercadorias", "ANALITICA", "DEVEDORA")
    imo = plano.criar(b, "1.3", "Imobilizado", "ANALITICA", "DEVEDORA")
    ret = plano.criar(b, "1.4", "Tributos retidos a recuperar", "ANALITICA", "DEVEDORA")
    plano.criar(b, "2", "PASSIVO", "SINTETICA", "CREDORA", "PASSIVO")
    forn = plano.criar(b, "2.1", "Fornecedores", "ANALITICA", "CREDORA")
    plano.criar(b, "3", "RECEITAS", "SINTETICA", "CREDORA", "RECEITA")
    rserv = plano.criar(b, "3.1", "Receita de serviços", "ANALITICA", "CREDORA")
    rvend = plano.criar(b, "3.2", "Receita de vendas", "ANALITICA", "CREDORA")
    dev = plano.criar(b, "3.3", "Devoluções de vendas", "ANALITICA", "CREDORA")
    plano.criar(b, "4", "DESPESAS", "SINTETICA", "DEVEDORA", "DESPESA")
    serv = plano.criar(b, "4.1", "Serviços tomados", "ANALITICA", "DEVEDORA")
    plano.criar(b, "4.2", "Fretes", "ANALITICA", "DEVEDORA")
    return {"CLIENTES": cli, "MERCADORIAS": mer, "IMOBILIZADO": imo,
            "FORNECEDORES": forn, "RECEITA_SERVICOS": rserv,
            "RECEITA_VENDAS": rvend, "DEVOLUCOES_VENDAS": dev,
            "SERVICOS_TOMADOS": serv, "RETENCOES_A_RECUPERAR": ret}


def main():
    raiz = apoio.pasta_temp("fiscale_contabil2_")
    dados = raiz / "dados"
    dados.mkdir()
    ok(not apoio.e_raiz_real(dados), "a pasta de dados é temporária, não a real")

    # ═══ 1 · o acervo fiscal do ensaio ══════════════════════════════════
    secao("1 · Documentos fiscais no acervo, pelo caminho de produção")
    # AGOSTO: uma compra (o fornecedor emitiu para A) e uma venda de A.
    id_compra = guardar_nfe(dados, A, chave=fxn.chave_ficticia(FORNECEDOR, 1),
                            valor="1000.00", emitente=FORNECEDOR,
                            destinatario=A, numero="1")
    id_venda = guardar_nfe(dados, A, chave=fxn.chave_ficticia(A, 2),
                           valor="2500.00", emitente=A,
                           destinatario=FORNECEDOR, numero="2")
    # SETEMBRO: outra compra. A competência sai da DATA DE EMISSÃO — é ela que
    # prova que um mês não empresta documento ao outro.
    id_set = guardar_nfe(dados, A, chave=fxn.chave_ficticia(FORNECEDOR, 3,
                                                            aamm="2609"),
                         valor="700.00", emitente=FORNECEDOR, destinatario=A,
                         numero="3", emissao="2026-09-05T09:00:00-03:00")
    # Uma devolução de venda entrando (o cliente devolveu): CFOP 1202 na nossa
    # entrada, emitido por ele como 5202.
    id_dev = guardar_nfe(dados, A, chave=fxn.chave_ficticia(FORNECEDOR, 5),
                         valor="150.00", emitente=FORNECEDOR, destinatario=A,
                         numero="5", cfop="5202")
    rel = ind.reconstruir(dados, A)
    igual(rel.resumo()["indexados"], 4, "4 NF-e no acervo e no índice de A")

    # NFS-e de agosto e de setembro, pelo leitor que a apuração usa.
    fxs.gravar(dados / A, [
        fxs.xml_nfse(10, "2026-08", 5000.00, emitente=A, tomador=TOMADOR,
                     ret_irrf=75.00, ret_csll=50.00),
        fxs.xml_nfse(11, "2026-09", 1234.56, emitente=A, tomador=TOMADOR),
    ])
    # Empresa B: acervo próprio, com uma nota só.
    guardar_nfe(dados, B, chave=fxn.chave_ficticia(FORNECEDOR, 9),
                valor="999.00", emitente=FORNECEDOR, destinatario=B, numero="9")
    ind.reconstruir(dados, B)

    # ═══ 2 · sincronização por competência ══════════════════════════════
    secao("2 · A competência é explícita, e não mistura meses")
    with base.abrir(dados, A) as b:
        contas = montar_plano(b)
        r = fatos.sincronizar(b, "2026-08", usuario="ana")
        igual(r["documentos"], 4, "agosto trouxe 4 documentos (3 NF-e + 1 NFS-e)")
        igual(r["novos"], 4, "todos novos")
        ags = fatos.listar(b, competencia="2026-08")
        igual(sorted(f["especie"] for f in ags), ["NFE55", "NFE55", "NFE55", "NFSE"],
              "as espécies de agosto")
        ok(all(f["competencia"] == "2026-08" for f in ags),
           "e todo fato de agosto tem a competência de agosto")
        ok(id_set not in [f["id_documento"] for f in ags],
           "a NF-e de setembro NÃO entrou em agosto")
        ok(fxs.chave_nfse(11) not in "".join(f["chave"] for f in ags),
           "e a NFS-e de setembro também não")
        r2 = fatos.sincronizar(b, "2026-09", usuario="ana")
        igual(r2["documentos"], 2, "setembro trouxe os seus 2 documentos")
        igual(len(fatos.listar(b, competencia="2026-08")), 4,
              "e agosto continua com 4 — sincronizar setembro não mexeu nele")
        setembro = fatos.listar(b, competencia="2026-09")
        ok(all(f["competencia"] == "2026-09" for f in setembro),
           "nenhum documento de agosto apareceu em setembro")

        secao("3 · Um fato por documento (sincronizar de novo não duplica)")
        antes = b.con.execute("SELECT COUNT(*) FROM fato").fetchone()[0]
        r3 = fatos.sincronizar(b, "2026-08", usuario="ana")
        igual(b.con.execute("SELECT COUNT(*) FROM fato").fetchone()[0], antes,
              "a segunda sincronização não criou fato nenhum")
        igual((r3["novos"], r3["inalterados"]), (0, 4),
              "ela diz o que fez: 0 novos, 4 sem mudança")
        ids = [f["id_documento"] for f in fatos.listar(b)]
        igual(len(ids), len(set(ids)), "nenhum `id_documento` repetido")

        secao("4 · O tipo contábil sai da operação fiscal, não de adivinhação")
        compra = fatos.obter(b, id_compra)
        venda = fatos.obter(b, id_venda)
        nfse = [f for f in ags if f["especie"] == "NFSE"][0]
        igual(compra["tipo"], "COMPRA_MERCADORIA",
              "NF-e recebida com CFOP de venda do emitente → compra")
        igual(compra["sentido"], "ENTRADA", "e o sentido é entrada")
        igual(venda["tipo"], "RECEITA_VENDA", "NF-e emitida por A → receita de venda")
        igual(nfse["tipo"], "RECEITA_SERVICO", "NFS-e emitida → receita de serviço")
        igual(compra["natureza_fiscal"], "VENDA",
              "a natureza fiscal lida do CFOP é registrada como veio")
        igual(compra["cfops"], "5102", "com o CFOP do documento à vista")
        igual(nfse["retencoes"], 12500, "as retenções da NFS-e vieram (IRRF+CSLL)")
        igual(fatos.obter(b, id_dev)["tipo"], "DEVOLUCAO_VENDA",
              "NF-e entrando com CFOP de devolução → devolução de venda")

        secao("5 · Soma dos fatos = soma dos documentos")
        p = fatos.panorama(b, "2026-08")
        igual(p["valor_fiscal"], 100000 + 250000 + 15000 + 500000,
              "1.000 + 2.500 + 150 + 5.000 = o valor fiscal da competência")
        igual(sum(abs(f["valor"]) for f in ags), p["valor_fiscal"],
              "e ele é exatamente a soma dos fatos listados")
        igual(p["documentos"], 4, "4 documentos no panorama")

        secao("6 · Rastreabilidade: do fato ao XML, e do XML ao lançamento")
        ras = fatos.rastreio(b, compra["id"])
        igual(ras["documento"]["id_documento"], id_compra, "o id do acervo")
        ok(ras["documento"]["caminho"].endswith(".xml"),
           "o caminho aponta para o XML: %s" % ras["documento"]["caminho"][-40:])
        ok((dados / ras["documento"]["caminho"]).exists(),
           "e o arquivo existe mesmo nesse caminho")
        ok(bool(ras["documento"]["hash_conteudo"]), "com o hash do conteúdo")
        igual(ras["fato"]["origem"], "FISCAL", "a origem do fato é FISCAL")

        secao("7 · Sem conta mapeada não há sugestão — e ela diz qual falta")
        s = fatos.sugestao(b, compra)
        ok(not s["tem"] and "MERCADORIAS" in s["motivo"],
           "sem mapa: %s" % s["motivo"][:80])
        recusa(lambda: fatos.gerar_lancamento(b, compra["id"], "ana"),
               "gerar lançamento sem o mapa", "falta ligar")
        recusa(lambda: plano.mapear(b, "CLIENTES", contas["RECEITA_VENDAS"]["id"],
                                    "ana"),
               "ligar CLIENTES a uma conta de receita", "espera conta de ATIVO")
        for papel, conta in contas.items():
            plano.mapear(b, papel, conta["id"], "ana")
        igual(len([m for m in plano.mapa(b) if m["conta_id"]]), len(contas),
              "os %d papéis usados por esta empresa ficaram ligados" % len(contas))

        secao("8 · A sugestão é a do pedido: D Mercadorias / C Fornecedores")
        s = fatos.sugestao(b, fatos.obter(b, id_compra))
        igual([(p_["tipo"], p_["papel"], p_["valor"]) for p_ in s["partidas"]],
              [("D", "MERCADORIAS", 100000), ("C", "FORNECEDORES", 100000)],
              "compra de mercadoria")
        s = fatos.sugestao(b, fatos.obter(b, nfse["id_documento"]))
        igual([(p_["papel"], p_["tipo"], p_["valor"]) for p_ in s["partidas"]],
              [("CLIENTES", "D", 487500), ("RETENCOES_A_RECUPERAR", "D", 12500),
               ("RECEITA_SERVICOS", "C", 500000)],
              "NFS-e emitida: D Clientes + D Retenções / C Receita de Serviços")
        s = fatos.sugestao(b, fatos.obter(b, id_venda))
        igual([(p_["papel"], p_["tipo"]) for p_ in s["partidas"]],
              [("CLIENTES", "D"), ("RECEITA_VENDAS", "C")], "NF-e emitida")

        secao("9 · Nenhum lançamento do Fiscal nasce definitivo")
        g = fatos.gerar_lancamentos_da_competencia(b, "2026-08", "ana")
        igual(len(g["gerados"]), 4, "os 4 fatos de agosto viraram lançamento")
        igual(g["recusados"], [], "nenhum recusado")
        lcs = lancamentos.listar(b, competencia="2026-08", origem="FISCAL")
        igual(sorted({l["status"] for l in lcs}), ["PENDENTE"],
              "e TODOS nasceram PENDENTE")
        ok(all(l["lote"] == "FISCAL 2026-08" for l in lcs),
           "com o lote da competência")
        ok(all(l["documento"] for l in lcs), "e o documento de origem no lançamento")
        igual(fatos.obter(b, id_compra)["estado"], "LANCADO",
              "o fato passa a LANCADO")
        recusa(lambda: fatos.gerar_lancamento(b, compra["id"], "ana"),
               "gerar o segundo lançamento do mesmo documento", "já tem o lançamento")

        secao("10 · Da nota ao lançamento, e do lançamento à nota")
        ras = fatos.rastreio(b, id_compra)
        num = ras["lancamento"]["numero"]
        ok(num and ras["lancamento"]["status"] == "PENDENTE",
           "a nota sabe qual lançamento nasceu dela (nº %s)" % num)
        de_volta = [f for f in fatos.listar(b)
                    if f["lancamento_id"] == ras["lancamento"]["id"]]
        igual([f["id_documento"] for f in de_volta], [id_compra],
              "e o lançamento sabe de qual documento veio")
        igual(len(fatos.listar(b, competencia="2026-08", estado="LANCADO")), 4,
              "quais documentos formaram os lançamentos desta competência: 4")

        secao("11 · Cobertura e valor fiscal × valor contabilizado")
        p = fatos.panorama(b, "2026-08")
        igual((p["exigem_lancamento"], p["com_lancamento"], p["cobertura"]),
              (4, 4, 100.0), "cobertura de 100% com os quatro lançados")
        igual(p["valor_contabilizado"], p["valor_fiscal"],
              "e o valor contabilizado bate com o fiscal")
        igual(p["sugeridos_pendentes"], 4, "os quatro estão pendentes de confirmação")
        lancamentos.confirmar(b, lcs[0]["id"], "bia")
        p = fatos.panorama(b, "2026-08")
        igual((p["sugeridos_pendentes"], p["confirmados"]), (3, 1),
              "confirmar um muda a conta de pendentes e confirmados")

        secao("12 · Documento cancelado não gera fato indevido")
        cancelada = guardar_nfe(dados, A,
                                chave=fxn.chave_ficticia(FORNECEDOR, 4),
                                valor="300.00", emitente=FORNECEDOR,
                                destinatario=A, numero="4",
                                com_protocolo=False)
        ind.reconstruir(dados, A)
        fatos.sincronizar(b, "2026-08", usuario="ana")
        f_sem = fatos.obter(b, cancelada)
        igual((f_sem["tipo"], f_sem["estado"]), ("SEM_EFEITO", "SEM_EFEITO"),
              "NF-e sem protocolo de autorização entra como SEM_EFEITO")
        igual(f_sem["situacao"], "NAO_AUTORIZADA", "com a situação que o Fiscal leu")
        ok(not fatos.sugestao(b, f_sem)["tem"], "e não tem sugestão de lançamento")
        p = fatos.panorama(b, "2026-08")
        igual(p["exigem_lancamento"], 4,
              "ela não entra na conta de quem precisa de lançamento")
        igual(p["valor_fiscal"], 865000, "nem no valor fiscal da competência")
        g2 = fatos.gerar_lancamentos_da_competencia(b, "2026-08", "ana")
        igual(g2["gerados"], [], "gerar de novo não cria lançamento para ela")

        secao("13 · Divergência Fiscal × Contábil")
        igual(fatos.divergencias(b, "2026-08"), [],
              "sem divergência enquanto Fiscal e Contábil concordam")
        # O documento some da receita DEPOIS de lançado: o caso que o
        # fechamento não pode esconder.
        b.con.execute("UPDATE fato SET tipo=?, situacao=?, entra_na_receita=0 "
                      "WHERE id_documento=?",
                      ("SEM_EFEITO", "CANCELADA", id_compra))
        div = fatos.divergencias(b, "2026-08")
        igual([d["tipo"] for d in div], ["DOCUMENTO_SEM_EFEITO_COM_LANCAMENTO"],
              "documento cancelado com lançamento vivo vira divergência")
        ok(id_compra in div[0]["id_documento"], "apontando o documento")
        b.con.execute("UPDATE fato SET tipo=?, situacao=?, entra_na_receita=1 "
                      "WHERE id_documento=?",
                      ("COMPRA_MERCADORIA", "NORMAL", id_compra))
        # Valor divergente: o lançamento deixa de bater com o documento.
        lc_id = fatos.obter(b, id_venda)["lancamento_id"]
        b.con.execute("UPDATE partida SET valor=valor+100 WHERE lancamento_id=?",
                      (lc_id,))
        div = fatos.divergencias(b, "2026-08")
        igual([d["tipo"] for d in div], ["VALOR_DIVERGENTE"],
              "lançamento com valor diferente do documento é divergência")
        b.con.execute("UPDATE partida SET valor=valor-100 WHERE lancamento_id=?",
                      (lc_id,))
        igual(fatos.divergencias(b, "2026-08"), [], "desfeito, a divergência some")

        secao("14 · Cancelar o lançamento devolve o fato à fila")
        alvo = fatos.obter(b, id_venda)
        lancamentos.cancelar(b, alvo["lancamento_id"], "ana", "conta errada")
        f = fatos.obter(b, id_venda)
        igual((f["estado"], f["lancamento_id"]), ("SEM_LANCAMENTO", ""),
              "o fato volta a SEM_LANCAMENTO")
        igual(fatos.panorama(b, "2026-08")["com_lancamento"], 3,
              "e a cobertura cai junto")
        fatos.gerar_lancamento(b, f["id"], "ana")
        igual(fatos.obter(b, id_venda)["estado"], "LANCADO", "e pode ser lançado de novo")

        secao("15 · Ignorar um documento exige motivo, e é reversível")
        f_ign = fatos.obter(b, cancelada)
        recusa(lambda: fatos.ignorar(b, f_ign["id"], "ana", ""),
               "ignorar sem motivo", "motivo")
        fatos.ignorar(b, f_ign["id"], "ana", "documento sem valor fiscal")
        igual(fatos.obter(b, cancelada)["estado"], "IGNORADO", "fica IGNORADO")
        fatos.sincronizar(b, "2026-08", usuario="ana")
        f2 = fatos.obter(b, cancelada)
        igual((f2["estado"], f2["motivo"]),
              ("IGNORADO", "documento sem valor fiscal"),
              "e ressincronizar não apaga nem o estado nem o motivo de quem ignorou")
        fatos.reconsiderar(b, f_ign["id"], "ana")
        igual(fatos.obter(b, cancelada)["estado"], "SEM_EFEITO",
              "reconsiderar devolve ao estado que o Fiscal justifica")

        secao("16 · Competência fechada: protegida, e reabrir deixa rastro")
        igual(competencias.estado(b, "2026-08"), "ABERTA", "agosto nasce ABERTA")
        competencias.definir(b, "2026-08", "EM_CONFERENCIA", "ana")
        ok(lancamentos.criar(b, "2026-08-20", "2026-08", "em conferência aceita",
                             [{"conta_id": contas["CLIENTES"]["id"], "tipo": "D",
                               "valor": "10"},
                              {"conta_id": contas["RECEITA_VENDAS"]["id"],
                               "tipo": "C", "valor": "10"}], "MANUAL",
                             usuario="ana")["id"],
           "EM CONFERÊNCIA não barra: é recado, não tranca")
        competencias.definir(b, "2026-08", "FECHADA", "ana")
        recusa(lambda: lancamentos.criar(
            b, "2026-08-21", "2026-08", "depois de fechada",
            [{"conta_id": contas["CLIENTES"]["id"], "tipo": "D", "valor": "10"},
             {"conta_id": contas["RECEITA_VENDAS"]["id"], "tipo": "C", "valor": "10"}],
            "MANUAL", usuario="ana"), "lançar em mês fechado", "FECHADA")
        pend = [l for l in lancamentos.listar(b, competencia="2026-08")
                if l["status"] == "PENDENTE"]
        recusa(lambda: lancamentos.confirmar(b, pend[0]["id"], "ana"),
               "confirmar em mês fechado", "FECHADA")
        recusa(lambda: lancamentos.cancelar(b, pend[0]["id"], "ana", "x"),
               "cancelar em mês fechado", "FECHADA")
        recusa(lambda: fatos.gerar_lancamentos_da_competencia(b, "2026-08", "ana"),
               "gerar lançamento do Fiscal em mês fechado", "FECHADA")
        r_sinc = fatos.sincronizar(b, "2026-08", usuario="ana")
        ok(r_sinc["documentos"] > 0,
           "mas SINCRONIZAR continua valendo: o Fiscal não para de andar")
        ok(lancamentos.criar(b, "2026-09-05", "2026-09", "setembro segue aberto",
                             [{"conta_id": contas["CLIENTES"]["id"], "tipo": "D",
                               "valor": "10"},
                              {"conta_id": contas["RECEITA_VENDAS"]["id"],
                               "tipo": "C", "valor": "10"}], "MANUAL",
                             usuario="ana")["id"],
           "e fechar agosto não fecha setembro")
        recusa(lambda: competencias.definir(b, "2026-08", "ABERTA", "ana"),
               "reabrir sem motivo", "exige motivo")
        reg = competencias.definir(b, "2026-08", "ABERTA", "bia",
                                   "erro no lançamento da nota 2")
        igual(reg["estado"], "ABERTA", "com motivo, reabre")
        acoes = [h["acao"] for h in reg["historico"]]
        igual(acoes, ["ABERTA→EM_CONFERENCIA", "EM_CONFERENCIA→FECHADA",
                      "FECHADA→ABERTA"],
              "e o histórico guarda os três passos")
        ok(any(h["usuario"] == "bia" and "erro no lançamento" in h["detalhe"]
               for h in reg["historico"]),
           "com quem reabriu e por quê")

    # ═══ 17 · isolamento ════════════════════════════════════════════════
    secao("17 · Empresa A e empresa B não se enxergam")
    with base.abrir(dados, B) as b:
        r = fatos.sincronizar(b, "2026-08", usuario="ana")
        igual(r["documentos"], 1, "B enxerga só o documento DELA")
        fb = fatos.listar(b, competencia="2026-08")
        igual([f["valor"] for f in fb], [99900], "com o valor dela")
        ok(id_compra not in [f["id_documento"] for f in fb],
           "e nenhum documento de A entrou em B")
        igual(fatos.obter(b, id_compra), None,
              "pedir em B um documento de A não devolve nada")
        recusa(lambda: fatos.gerar_lancamento(b, id_compra, "ana"),
               "lançar em B um documento de A", "não encontrado")
        igual(competencias.estado(b, "2026-08"), "ABERTA",
              "e o fechamento de A não fechou o mês de B")
    with base.abrir(dados, A) as b:
        igual(len(fatos.listar(b, competencia="2026-08")), 5,
              "A continua com os fatos dela (4 + a sem efeito)")

    # ═══ 18 · pelas rotas ═══════════════════════════════════════════════
    secao("18 · As rotas respeitam empresa e competência")
    cadastro = [{"identidade": A, "nome": "EMPRESA A"},
                {"identidade": B, "nome": "EMPRESA B"}]
    st, r = rotas.atender("GET", "/api/contabil/fiscal",
                          {"empresa": [A], "competencia": ["2026-08"]}, {}, None,
                          "ana", dados, cadastro)
    igual((st, r["panorama"]["documentos"]), (200, 5), "GET fiscal de agosto")
    st, r = rotas.atender("GET", "/api/contabil/fiscal",
                          {"empresa": [A], "competencia": ["2026-09"]}, {}, None,
                          "ana", dados, cadastro)
    igual(r["panorama"]["documentos"], 2, "GET fiscal de setembro traz os dele")
    st, r = rotas.atender("GET", "/api/contabil/fiscal", {"empresa": [A]}, {},
                          None, "ana", dados, cadastro)
    igual(st, 400, "sem competência a rota recusa — não existe 'todos os meses'")
    st, r = rotas.atender("GET", "/api/contabil/impacto",
                          {"empresa": [A], "id_documento": [id_compra]}, {}, None,
                          "ana", dados, cadastro)
    ok(st == 200 and r["rastreio"]["lancamento"],
       "GET impacto: o Fiscal pergunta pelo id e recebe o lançamento")
    st, r = rotas.atender("GET", "/api/contabil/impacto",
                          {"empresa": [B], "id_documento": [id_compra]}, {}, None,
                          "ana", dados, cadastro)
    ok(st == 200 and r["fato"] is None,
       "e perguntando pela empresa errada não vaza nada: %s" % r["mensagem"][:60])

    # ═══ 19 · fronteiras ════════════════════════════════════════════════
    secao("19 · O Contábil não reescreveu o Fiscal")
    fonte = (RAIZ / "contabil" / "fatos.py").read_text("utf-8")
    ok("classificar_cfop" in fonte and "5102" not in fonte,
       "a classificação de CFOP é chamada de `vendas.py`, não copiada")
    ok('"101"' not in fonte and '"201"' not in fonte,
       "e nenhuma tabela de CFOP foi duplicada aqui")
    ok("mode=ro" in fonte, "o índice fiscal é aberto em somente-leitura")
    for proibido in ("import classificador", "apuracao_federal", "calcular_das"):
        ok(proibido not in fonte,
           "não chama %s: apuração de tributo não é desta camada" % proibido)
    ok(all((dados / A / "acervo").exists() for _ in (1,)),
       "o acervo fiscal continua onde estava, intacto")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
