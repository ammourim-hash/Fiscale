#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FASE CONTÁBIL 1 — plano, lançamentos, extratos, classificação, conciliação.

    python teste_contabil1.py

AS PROMESSAS QUE ESTA SUÍTE SEGURA
    • partida dobrada: o que não fecha é recusado ANTES de gravar;
    • nenhum lançamento nasce definitivo — nem o manual, nem o do banco;
    • nenhuma linha do extrato some: movimentos + linhas ignoradas = total;
    • cada movimento é um registro próprio, com a linha de onde saiu;
    • reimportar não duplica (nem o mesmo arquivo, nem o extrato sobreposto);
    • o original é guardado byte a byte e conferido pelo hash;
    • classificação é sugestão: importar e classificar não criam lançamento;
    • transferência entre contas não vira receita nem despesa;
    • conciliação: SUGESTÃO → CONCILIADO só com confirmação; DIVERGENTE exige
      motivo; desfazer não apaga;
    • rastreio: banco → movimento → documento → operação → lançamento → arquivo;
    • a empresa A não enxerga nada da empresa B;
    • o acervo fiscal é LIDO e nunca alterado.

NÃO TOCA A PASTA REAL NEM A REDE. Tudo em diretório temporário próprio.
Os CNPJ e valores daqui são inventados para o teste e não vão para base
nenhuma de produção.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio as apoio                      # noqa: E402
import teste_fixturas_fiscal as fx               # noqa: E402

from contabil import (armazenamento, bancos, base, classificacao,  # noqa: E402
                      conciliacao, extratos, fiscal, lancamentos, modelo,
                      plano, rotas, visao)
from contabil.leitores import (Leitura, MovLido, SemLeitor,  # noqa: E402
                               conferir_completude)
from contabil.leitores import csv_extrato, ofx  # noqa: E402

_ok = _falhas = 0
_erros: list = []

A = fx.PRESTADOR                 # empresa A (é quem emite as NFS-e da fixtura)
B = fx.OUTRO                     # empresa B
CLIENTE = fx.TOMADOR             # tomador das NFS-e de A


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
        ok(False, "%s  (obtive %.140r, esperava %.140r)" % (desc, a, b))


def recusa(fn, desc, trecho=""):
    try:
        fn()
    except (modelo.Invalido, extratos.Recusado, SemLeitor) as e:
        ok(trecho.lower() in str(e).lower(),
           "%s — recusado: %s" % (desc, str(e)[:90]))
        return
    ok(False, desc + " — deveria ter sido recusado")


# ── Arquivos de extrato inventados para o teste ────────────────────────────
def ofx_de(transacoes, conta="56789-0", saldo="1750.10", uma_linha=False):
    corpo = "".join(
        "<STMTTRN><TRNTYPE>%s<DTPOSTED>%s120000[-3:BRT]<TRNAMT>%s<FITID>%s"
        "%s<MEMO>%s</STMTTRN>\n" % (t[0], t[1], t[2], t[3],
                                    ("<CHECKNUM>%s" % t[5]) if len(t) > 5 else "",
                                    t[4])
        for t in transacoes)
    txt = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nCHARSET:1252\n\n<OFX>\n"
           "<BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL\n"
           "<BANKACCTFROM><BANKID>341<BRANCHID>1234<ACCTID>%s</BANKACCTFROM>\n"
           "<BANKTRANLIST><DTSTART>20260101<DTEND>20260131\n%s"
           "</BANKTRANLIST><LEDGERBAL><BALAMT>%s<DTASOF>20260131</LEDGERBAL>\n"
           "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>\n" % (conta, corpo, saldo))
    if uma_linha:
        txt = txt.replace("\n", "")
    return txt.encode("cp1252")


OFX_JAN = ofx_de([
    ("CREDIT", "20260105", "5000.00", "F1", "PIX RECEBIDO ABC LTDA"),
    ("DEBIT", "20260106", "-49.90", "F2", "TARIFA MANUTENÇÃO CONTA"),
    ("DEBIT", "20260107", "-3200.00", "F3", "PIX PARA FORNECEDOR XYZ", "000123"),
    ("DEBIT", "20260108", "-1000.00", "F4", "TRANSF ENTRE CONTAS MESMA TITULARIDADE"),
    ("DEBIT", "20260109", "-15.00", "F5", "TARIFA PIX"),
    ("DEBIT", "20260109", "-15.00", "F6", "TARIFA PIX"),
    ("CREDIT", "20260110", "1.234,56", "F7", "CREDITO DIVERSO"),
    ("CREDIT", "20260111", "XX", "F8", "VALOR QUEBRADO"),
])

# CSV com tudo o que um extrato de verdade traz além dos movimentos.
CSV_BRUTO = (
    "Extrato de conta corrente\r\n"
    "Agência: 0001  Conta: 99887-6\r\n"
    "\r\n"
    "Data;Histórico;Documento;Valor (R$);Saldo (R$)\r\n"
    "01/01/2026;SALDO ANTERIOR;;;10.000,00\r\n"
    "02/01/2026;PIX RECEBIDO CLIENTE BOM SA;111;2.500,00;12.500,00\r\n"
    "02/01/2026;complemento do histórico sem valor;;;\r\n"
    "03/01/2026;TARIFA PACOTE SERVIÇOS;;-39,90;12.460,10\r\n"
    "03/01/2026;TARIFA PACOTE SERVIÇOS;;-39,90;12.420,20\r\n"
    "32/13/2026;LINHA COM DATA RUIM;9;-10,00;12.410,20\r\n"
    "04/01/2026;DARF IRPJ;;-800,00;11.610,20\r\n"
    "\r\n"
    "04/01/2026;SALDO DO DIA;;;11.610,20\r\n"
    "Total;;;;").encode("cp1252")


def plano_basico(b):
    plano.criar(b, "1", "ATIVO", "SINTETICA", "DEVEDORA", "ATIVO")
    plano.criar(b, "1.1", "Disponível", "SINTETICA", "DEVEDORA")
    c1 = plano.criar(b, "1.1.01", "Banco Itaú", "ANALITICA", "DEVEDORA",
                     ref_ecd="1.01.01.02.00")
    c2 = plano.criar(b, "1.1.02", "Banco do Brasil", "ANALITICA", "DEVEDORA")
    c3 = plano.criar(b, "1.1.03", "Clientes", "ANALITICA", "DEVEDORA")
    plano.criar(b, "2", "PASSIVO", "SINTETICA", "CREDORA", "PASSIVO")
    c4 = plano.criar(b, "2.1", "Fornecedores", "ANALITICA", "CREDORA")
    plano.criar(b, "3", "RECEITAS", "SINTETICA", "CREDORA", "RECEITA")
    c5 = plano.criar(b, "3.1", "Receita de serviços", "ANALITICA", "CREDORA")
    plano.criar(b, "4", "DESPESAS", "SINTETICA", "DEVEDORA", "DESPESA")
    c6 = plano.criar(b, "4.1", "Despesas bancárias", "ANALITICA", "DEVEDORA")
    return {"itau": c1, "bb": c2, "clientes": c3, "fornec": c4,
            "receita": c5, "desp_banc": c6}


def contar(b, tabela):
    return b.con.execute("SELECT COUNT(*) FROM %s" % tabela).fetchone()[0]


def main():
    raiz = apoio.pasta_temp("fiscale_contabil1_")
    dados = raiz / "dados"
    dados.mkdir()
    ok(not apoio.e_raiz_real(dados), "a pasta de dados é temporária, não a real")

    # ═══ 0 · dinheiro e datas ═══════════════════════════════════════════
    secao("0 · Dinheiro em centavos inteiros, nunca float")
    igual(modelo.centavos("1.234,56"), 123456, "1.234,56 → 123456")
    igual(modelo.centavos("-49.90", decimal_ponto=True), -4990, "-49.90 (OFX) → -4990")
    igual(modelo.centavos("R$ 5.000"), 500000, "R$ 5.000 → 500000 (ponto é milhar)")
    igual(modelo.centavos("(39,90)"), -3990, "(39,90) → negativo")
    igual(modelo.centavos("39,90 D"), -3990, "39,90 D → débito")
    igual(modelo.centavos("39,90-"), -3990, "39,90- → negativo")
    recusa(lambda: modelo.centavos("12abc"), "texto que não é valor", "ilegível")
    igual(modelo.data_iso("05/01/2026"), "2026-01-05", "data brasileira")
    igual(modelo.competencia("01/2026"), "2026-01", "competência MM/AAAA")

    # ═══ 1 · plano de contas ════════════════════════════════════════════
    secao("1 · Plano de contas por empresa")
    with base.abrir(dados, A) as b:
        pc = plano_basico(b)
        igual(len(plano.listar(b)), 11, "11 contas criadas")
        igual(pc["itau"]["grupo"], "ATIVO", "o grupo é herdado da conta-pai")
        igual(pc["itau"]["codigo_pai"], "1.1", "a pai é derivada do código")
        igual(pc["itau"]["ref_ecd"], "1.01.01.02.00", "o código referencial ECD fica guardado")
        recusa(lambda: plano.criar(b, "9.1", "Sem pai", "ANALITICA", "DEVEDORA"),
               "conta sem a sintética pai", "precisa da conta sintética")
        recusa(lambda: plano.criar(b, "1.1.01.1", "Filha de analítica",
                                   "ANALITICA", "DEVEDORA"),
               "subconta de analítica", "analítica")
        recusa(lambda: plano.criar(b, "1.1.09", "Grupo errado", "ANALITICA",
                                   "DEVEDORA", "RECEITA"),
               "grupo diferente do da pai", "diverge")
        recusa(lambda: plano.criar(b, "1.1.01", "Duplicada", "ANALITICA",
                                   "DEVEDORA"), "código repetido", "já existe")
        recusa(lambda: plano.criar(b, "1.x", "Código torto", "ANALITICA",
                                   "DEVEDORA"), "código fora do formato", "formato")
        recusa(lambda: plano.alterar(b, plano.por_codigo(b, "1.1")["id"],
                                     ativa=False),
               "desativar sintética com filhas ativas", "desative as subcontas")
        recusa(lambda: plano.lancavel(b, plano.por_codigo(b, "1.1")["id"]),
               "sintética não recebe lançamento", "sintética")
        r = plano.importar_csv(b, "codigo;descricao;tipo;natureza\n"
                                  "5;COMPENSAÇÃO;S;D\n5.1;ok;A;D\n5.2;;A;D\n")
        ok(not r["ok"] and r["erros"], "planilha com uma linha ruim é recusada")
        igual(plano.por_codigo(b, "5.1"), None,
              "e NENHUMA conta dela entrou (tudo ou nada)")

    # ═══ 2 · lançamentos ═════════════════════════════════════════════════
    secao("2 · Partida dobrada equilibrada")
    with base.abrir(dados, A) as b:
        lc = lancamentos.criar(b, "2026-01-15", "2026-01", "Serviço prestado",
                               [{"conta_id": pc["clientes"]["id"], "tipo": "D",
                                 "valor": "1.500,00"},
                                {"conta_id": pc["receita"]["id"], "tipo": "C",
                                 "valor": "1.000,00"},
                                {"codigo": "2.1", "tipo": "C", "valor": "500,00"}],
                               "MANUAL", documento="NF 10", lote="L1",
                               usuario="ana")
        igual(lc["status"], "PENDENTE", "nasce PENDENTE, mesmo sendo manual")
        igual(lc["total"], 150000, "débitos = créditos = 1.500,00")
        igual(len(lc["partidas"]), 3, "três partidas (uma débito, duas crédito)")
        igual((lc["documento"], lc["lote"], lc["competencia"]),
              ("NF 10", "L1", "2026-01"), "documento, lote e competência guardados")
        lc_id = lc["id"]

    secao("3 · Lançamento desequilibrado é rejeitado (e nada é gravado)")
    with base.abrir(dados, A) as b:
        antes = (contar(b, "lancamento"), contar(b, "partida"))
        recusa(lambda: lancamentos.criar(
            b, "2026-01-15", "", "desequilibrado",
            [{"conta_id": pc["clientes"]["id"], "tipo": "D", "valor": "100,00"},
             {"conta_id": pc["receita"]["id"], "tipo": "C", "valor": "99,99"}],
            "MANUAL"), "débito 100,00 × crédito 99,99", "desequilibrado")
        recusa(lambda: lancamentos.criar(
            b, "2026-01-15", "", "só débito",
            [{"conta_id": pc["clientes"]["id"], "tipo": "D", "valor": "10"},
             {"conta_id": pc["itau"]["id"], "tipo": "D", "valor": "10"}],
            "MANUAL"), "só débitos", "débito e crédito")
        recusa(lambda: lancamentos.criar(
            b, "2026-01-15", "", "valor zero",
            [{"conta_id": pc["clientes"]["id"], "tipo": "D", "valor": "0"},
             {"conta_id": pc["receita"]["id"], "tipo": "C", "valor": "0"}],
            "MANUAL"), "valor zero", "positivo")
        recusa(lambda: lancamentos.criar(
            b, "2026-01-15", "", "sintética",
            [{"codigo": "1.1", "tipo": "D", "valor": "10"},
             {"conta_id": pc["receita"]["id"], "tipo": "C", "valor": "10"}],
            "MANUAL"), "conta sintética", "sintética")
        recusa(lambda: lancamentos.criar(
            b, "2026-01-15", "", "origem inventada",
            [{"conta_id": pc["clientes"]["id"], "tipo": "D", "valor": "10"},
             {"conta_id": pc["receita"]["id"], "tipo": "C", "valor": "10"}],
            "ROBO"), "origem fora da lista", "origem")
        igual((contar(b, "lancamento"), contar(b, "partida")), antes,
              "nenhuma linha entrou no banco pelas recusas")

    secao("4 · Ciclo de vida: confirmar exige gente; confirmado só por ajuste")
    with base.abrir(dados, A) as b:
        recusa(lambda: lancamentos.confirmar(b, lc_id, ""),
               "confirmar sem usuário", "quem confirma")
        c = lancamentos.confirmar(b, lc_id, "ana")
        igual((c["status"], c["confirmado_por"]), ("CONFIRMADO", "ana"),
              "confirmado, com o nome de quem confirmou")
        recusa(lambda: lancamentos.cancelar(b, lc_id, "ana", "errei"),
               "cancelar confirmado", "ajuste")
        aj = lancamentos.criar(b, "2026-01-20", "2026-01", "ajuste",
                               [{"conta_id": pc["receita"]["id"], "tipo": "D",
                                 "valor": "100"},
                                {"conta_id": pc["clientes"]["id"], "tipo": "C",
                                 "valor": "100"}], "MANUAL", usuario="ana",
                               ajusta_id=lc_id)
        igual((aj["origem"], aj["status"]), ("AJUSTE", "PENDENTE"),
              "o ajuste nasce PENDENTE com origem AJUSTE")
        lancamentos.confirmar(b, aj["id"], "bia")
        igual(lancamentos.obter(b, lc_id)["status"], "AJUSTADO",
              "confirmado o ajuste, o original vira AJUSTADO (e continua no razão)")
        pend = lancamentos.criar(b, "2026-01-21", "", "a cancelar",
                                 [{"conta_id": pc["receita"]["id"], "tipo": "D",
                                   "valor": "1"},
                                  {"conta_id": pc["clientes"]["id"], "tipo": "C",
                                   "valor": "1"}], "FISCAL")
        recusa(lambda: lancamentos.cancelar(b, pend["id"], "ana", ""),
               "cancelar sem motivo", "motivo")
        igual(lancamentos.cancelar(b, pend["id"], "ana", "duplicado")["status"],
              "CANCELADO", "pendente cancelado com motivo")
        acoes = [e["acao"] for e in b.eventos("LANCAMENTO", lc_id)]
        igual(acoes, ["CRIADO", "CONFIRMADO", "AJUSTADO"],
              "a trilha registra criação, confirmação e ajuste")
        ok("status" not in lancamentos.criar.__code__.co_varnames,
           "`criar()` não tem parâmetro de status: não há como nascer definitivo")

    # ═══ 5 · fontes fiscais da empresa A (NFS-e real da fixtura + índice) ═
    secao("5 · Acervo fiscal: lido pelas funções que já existem, sem escrever")
    pasta_a = dados / A
    fx.gravar(pasta_a, [fx.xml_nfse(1, "2026-01", 5000.00, emitente=A,
                                    tomador=CLIENTE),
                        fx.xml_nfse(2, "2026-01", 777.00, emitente=A,
                                    tomador=CLIENTE)])
    # O índice da ingestão, criado pelo próprio `ingestao.indice`.
    ind = fiscal._modulo_backend("ingestao.indice")
    with ind.abrir(dados, A).conectar() as con:
        con.execute(
            "INSERT INTO documentos (id_documento, especie, chave, numero, "
            "identidade_empresa, papel, emitente, emitente_nome, contraparte, "
            "contraparte_nome, dh_emissao, competencia, valor_total, situacao,"
            " estado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("NFE55-TESTE-1", "NFE55", "2" * 44, "555", A, "DESTINATARIO",
             fx.cnpj_ficticio("444444440001"), "FORNECEDOR XYZ LTDA",
             fx.cnpj_ficticio("444444440001"), "FORNECEDOR XYZ LTDA",
             "2026-01-03T10:00:00-03:00", "2026-01-01", "3200.00",
             "AUTORIZADO", "INDEXADO"))
    arq_ind = fiscal.caminho_indice(dados, A)
    antes_ind = hashlib.sha256(arq_ind.read_bytes()).hexdigest()
    docs_a = fiscal.documentos(dados, A)
    igual(sorted(d["especie"] for d in docs_a), ["NFE55", "NFSE", "NFSE"],
          "duas NFS-e (via core.carregar_notas) e uma NF-e (via índice)")
    nfse1 = next(d for d in docs_a if d["numero"] == "1")
    igual((nfse1["sentido"], nfse1["contraparte_doc"], nfse1["valor"]),
          ("ENTRADA", CLIENTE, 500000), "NFS-e emitida: recebimento do tomador")
    igual(hashlib.sha256(arq_ind.read_bytes()).hexdigest(), antes_ind,
          "o índice da ingestão não mudou um byte com a leitura")
    igual(fiscal.documentos(dados, B), [],
          "a empresa B não tem documento fiscal nenhum")
    ok(not (dados / B).exists(),
       "e ler o acervo de B não criou pasta nenhuma para ela")
    ctx = fiscal.contexto_classificacao(docs_a, A)
    ok(CLIENTE in ctx["clientes"], "o tomador das NFS-e é cliente no contexto")

    # ═══ 6 · importação OFX ═════════════════════════════════════════════
    secao("6 · Importação OFX sem perda de linhas, cada movimento individual")
    lei = ofx.ler(OFX_JAN)
    igual((lei.blocos_brutos, len(lei.movimentos)), (8, 8),
          "8 <STMTTRN> no texto cru, 8 movimentos lidos")
    quebrado = lei.movimentos[7]
    ok(quebrado.valor is None and "valor ilegível" in quebrado.problema,
       "transação com valor ilegível NÃO some: vira movimento com problema")
    igual(lei.movimentos[6].valor, 123456, "TRNAMT com vírgula decimal é lido")
    igual((lei.banco, lei.agencia, lei.conta), ("341", "1234", "56789-0"),
          "banco, agência e conta lidos do BANKACCTFROM")
    igual(lei.saldo_final, 175010, "saldo final lido do LEDGERBAL")
    igual(lei.movimentos[2].documento, "000123", "CHECKNUM vira documento")
    igual(lei.movimentos[1].descricao, "TARIFA MANUTENÇÃO CONTA",
          "descrição com acento (charset 1252) preservada")
    lei1 = ofx.ler(ofx_de([("CREDIT", "20260105", "10.00", "Z1", "A"),
                           ("CREDIT", "20260105", "20.00", "Z2", "B")],
                          uma_linha=True))
    igual([m.ordem for m in lei1.movimentos], [1, 2],
          "OFX numa linha só: a ORDEM distingue as transações")
    recusa(lambda: conferir_completude(Leitura(
        formato="OFX", linhas_total=1, blocos_brutos=3,
        movimentos=[MovLido(ordem=1, linha=1)])),
        "leitura que perdeu transação", "nada foi importado")

    with base.abrir(dados, A) as b:
        r = extratos.importar(b, OFX_JAN, "janeiro.ofx", "ana")
        igual(r["desfecho"], "IMPORTADO", "extrato importado")
        igual(r["importacao"]["movimentos_novos"], 8, "8 movimentos novos")
        ms = bancos.buscar(b, importacao_id=r["importacao"]["id"])
        igual(len(ms), 8, "8 registros individuais de movimento")
        igual(len({m["id"] for m in ms}), 8, "cada um com identificador próprio")
        tar = [m for m in ms if m["descricao"] == "TARIFA PIX"]
        igual(len(tar), 2, "duas tarifas idênticas no mesmo dia = dois movimentos")
        m0 = next(m for m in ms if m["id_transacao"] == "F1")
        igual((m0["data"], m0["data_lancamento"], m0["valor"], m0["sentido"],
               m0["banco"], m0["agencia"], m0["conta"], m0["tipo_operacao"]),
              ("2026-01-05", "2026-01-05", 500000, "ENTRADA", "341", "1234",
               "56789-0", "CREDIT"),
              "data, lançamento, valor, sentido, banco, agência, conta e tipo")
        ok(m0["linha"] > 0, "e a linha de origem no arquivo (%d)" % m0["linha"])
        imp_ofx = r["importacao"]
        igual(contar(b, "lancamento"), 3,
              "importar NÃO criou lançamento (os 3 são da seção 2 e 4)")

    # ═══ 7 · classificação ═══════════════════════════════════════════════
    secao("7 · Classificação é sugestão, nunca lançamento")
    with base.abrir(dados, A) as b:
        pm = {m["id_transacao"]: m for m in bancos.buscar(b)}
        igual((pm["F1"]["categoria"], pm["F1"]["sugestao"]),
              ("CLIENTE", "CLIENTE / RECEBIMENTO"),
              "PIX RECEBIDO ABC LTDA → CLIENTE / RECEBIMENTO")
        igual((pm["F2"]["categoria"], pm["F2"]["sugestao"]),
              ("TARIFA_BANCARIA", "DESPESA BANCÁRIA"),
              "TARIFA MANUTENÇÃO CONTA → DESPESA BANCÁRIA")
        igual((pm["F3"]["categoria"], pm["F3"]["sugestao"]),
              ("FORNECEDOR", "FORNECEDOR / PAGAMENTO"),
              "PIX PARA FORNECEDOR XYZ → FORNECEDOR / PAGAMENTO")
        igual(pm["F4"]["categoria"], "TRANSFERENCIA_ENTRE_CONTAS",
              "TRANSF ENTRE CONTAS → transferência")
        igual(pm["F7"]["categoria"], "NAO_IDENTIFICADO",
              "CREDITO DIVERSO → não identificado (sem palpite)")
        ok(all(m["categoria_confirmada"] == "" for m in pm.values()),
           "nenhuma categoria nasce confirmada")
        antes = contar(b, "lancamento")
        mc = bancos.confirmar_categoria(b, pm["F7"]["id"], "OUTROS", "ana")
        igual((mc["categoria"], mc["categoria_confirmada"], mc["confirmada_por"]),
              ("NAO_IDENTIFICADO", "OUTROS", "ana"),
              "a confirmação fica AO LADO da sugestão, que não é sobrescrita")
        igual(contar(b, "lancamento"), antes, "confirmar categoria não lança nada")
        recusa(lambda: bancos.confirmar_categoria(b, pm["F7"]["id"], "OUTROS", ""),
               "confirmar categoria sem usuário", "quem confirma")
    fonte = (RAIZ / "contabil" / "classificacao.py").read_text("utf-8")
    ok(all(x not in fonte for x in ("INSERT", "UPDATE", "import lancamentos",
                                    "from . import lancamentos", ".con.")),
       "classificacao.py não escreve em banco nem conhece lançamento")
    s = classificacao.classificar("PIX RECEBIDO " + "11.222.333/0001-81", 100,
                                  contexto={"empresa": "11222333000181"})
    igual(s["categoria"], "TRANSFERENCIA_ENTRE_CONTAS",
          "PIX do próprio CNPJ → transferência (não é receita)")
    s = classificacao.classificar("PIX RECEBIDO LOJA DAS FLORES", 100)
    ok(s["categoria"] != "TRIBUTOS", "'DAS' como preposição não vira tributo")
    s = classificacao.classificar("PIX RECEBIDO " + CLIENTE, 500000,
                                  contexto=ctx)
    igual((s["categoria"], s["regra"]), ("CLIENTE", "DOC_DE_CLIENTE_NO_ACERVO"),
          "CNPJ do tomador das NFS-e → CLIENTE pelo acervo")
    igual(classificacao.documentos_na_descricao("PIX ***.456.789-** JOAO"), [],
          "CPF mascarado não é documento")

    # ═══ 8 · idempotência ════════════════════════════════════════════════
    secao("8 · Reimportar não duplica")
    with base.abrir(dados, A) as b:
        n_mov, n_imp = contar(b, "movimento"), contar(b, "importacao")
        r2 = extratos.importar(b, OFX_JAN, "janeiro (cópia).ofx", "ana")
        igual(r2["desfecho"], "DUPLICATA", "o mesmo arquivo → DUPLICATA")
        igual((contar(b, "movimento"), contar(b, "importacao")), (n_mov, n_imp),
              "nenhum movimento nem importação a mais")
        # Extrato sobreposto: repete F3..F8 (FITID diferentes!) e traz 1 novo.
        sobreposto = ofx_de([
            ("DEBIT", "20260107", "-3200.00", "OUTRO3", "PIX PARA FORNECEDOR XYZ",
             "000123"),
            ("DEBIT", "20260108", "-1000.00", "OUTRO4",
             "TRANSF ENTRE CONTAS MESMA TITULARIDADE"),
            ("DEBIT", "20260109", "-15.00", "OUTRO5", "TARIFA PIX"),
            ("DEBIT", "20260109", "-15.00", "OUTRO6", "TARIFA PIX"),
            ("CREDIT", "20260110", "1.234,56", "OUTRO7", "CREDITO DIVERSO"),
            ("CREDIT", "20260111", "XX", "OUTRO8", "VALOR QUEBRADO"),
            ("CREDIT", "20260201", "300.00", "NOVO", "PIX RECEBIDO NOVO"),
        ], saldo="2050.10")
        r3 = extratos.importar(b, sobreposto, "sobreposto.ofx", "ana")
        igual((r3["importacao"]["movimentos_novos"],
               r3["importacao"]["movimentos_existentes"]), (1, 6),
              "extrato sobreposto: 1 novo, 6 reconhecidos (mesmo com FITID novo)")
        igual(contar(b, "movimento"), n_mov + 1, "só o movimento novo entrou")
        tarifa = bancos.buscar(b, texto="TARIFA PIX")
        igual(len(tarifa), 2, "as duas tarifas iguais continuam sendo duas")
        orig = b.con.execute("SELECT COUNT(*) FROM movimento_origem WHERE "
                             "movimento_id=?", (tarifa[0]["id"],)).fetchone()[0]
        igual(orig, 2, "e cada uma aponta para as DUAS linhas de origem")

    # ═══ 9 · CSV ════════════════════════════════════════════════════════
    secao("9 · CSV: cada linha presta contas")
    lc_ = csv_extrato.ler(CSV_BRUTO)
    total = len(CSV_BRUTO.decode("cp1252").splitlines())
    igual(lc_.linhas_total, total, "total de linhas físicas = %d" % total)
    igual(len(lc_.movimentos) + len(lc_.ignoradas), total,
          "movimentos + ignoradas = total de linhas")
    igual(len(lc_.movimentos), 5, "5 movimentos (inclusive a data ruim)")
    motivos = sorted(m for _, _, m in lc_.ignoradas)
    igual(motivos, sorted(["PREAMBULO", "PREAMBULO", "EM_BRANCO", "CABECALHO",
                           "SALDO_INFORMATIVO", "SEM_VALOR", "EM_BRANCO",
                           "SALDO_INFORMATIVO", "SEM_VALOR"]),
          "cada linha não-movimento tem o seu motivo")
    ruim = next(m for m in lc_.movimentos if "DATA RUIM" in m.descricao)
    ok(ruim.problema.startswith("data ilegível") and ruim.valor == -1000,
       "data ilegível: o movimento fica, com o problema dito")
    igual((lc_.agencia, lc_.conta), ("0001", "99887-6"),
          "agência e conta lidas do preâmbulo")
    igual(lc_.movimentos[0].saldo, 1250000, "saldo por linha lido")
    recusa(lambda: csv_extrato.ler(b"a;b;c\n1;2;3\n"),
           "CSV sem cabeçalho reconhecível", "cabeçalho")
    with base.abrir(dados, A) as b:
        recusa(lambda: extratos.importar(b, CSV_BRUTO, "x.csv", "ana"),
               "CSV sem conta escolhida", "escolha a conta")
        cb_bb = bancos.criar_conta(b, "001", "0001", "99887-6", "BB movimento",
                                   pc["bb"]["id"])
        r = extratos.importar(b, CSV_BRUTO, "bb.csv", "ana", cb_bb["id"])
        igual((r["importacao"]["movimentos_lidos"],
               r["importacao"]["linhas_ignoradas"]), (5, 9),
              "5 movimentos e 9 linhas ignoradas registradas")
        ign = extratos.linhas_ignoradas(b, r["importacao"]["id"])
        igual(len(ign), 9, "as 9 linhas ignoradas estão no banco, com conteúdo")
        ok(any("complemento" in l["conteudo"] for l in ign),
           "inclusive o complemento de histórico sem valor")
        imp_csv = r["importacao"]

    # ═══ 10 · byte a byte ═══════════════════════════════════════════════
    secao("10 · O original é preservado byte a byte")
    with base.abrir(dados, A) as b:
        for imp, bruto in ((imp_ofx, OFX_JAN), (imp_csv, CSV_BRUTO)):
            devolvido, _nome, _fmt = extratos.original(b, imp["id"])
            ok(devolvido == bruto, "%s devolvido idêntico (%d bytes, CRLF e "
               "1252 intactos)" % (imp["formato"], len(bruto)))
            igual(imp["sha256"], hashlib.sha256(bruto).hexdigest(),
                  "e o hash registrado é o dos bytes recebidos")
        caminho = armazenamento.localizar(dados, A, imp_csv["arquivo_id"])
        caminho.write_bytes(CSV_BRUTO + b"x")
        try:
            extratos.original(b, imp_csv["id"])
            ok(False, "arquivo adulterado deveria ser recusado")
        except armazenamento.ErroArmazenamento:
            ok(True, "arquivo adulterado no disco é recusado na entrega")
        caminho.write_bytes(CSV_BRUTO)
    with base.abrir(dados, A) as b:
        n = contar(b, "importacao")
        pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
        recusa(lambda: extratos.importar(b, pdf, "extrato.pdf", "ana", cb_bb["id"]),
               "PDF sem leitor de layout", "pdf")
        igual(contar(b, "importacao"), n, "PDF recusado não deixa importação")

    # ═══ 11 · transferência ════════════════════════════════════════════
    secao("11 · Transferência entre contas não vira receita nem despesa")
    with base.abrir(dados, A) as b:
        cb_itau = bancos.achar_conta(b, "341", "1234", "56789-0")
        bancos.alterar_conta(b, cb_itau["id"], conta_contabil_id=pc["itau"]["id"])
        # A outra ponta dos R$ 1.000 chega no extrato do BB.
        entrada = ("Data;Histórico;Valor\r\n"
                   "08/01/2026;TED RECEBIDA;1.000,00\r\n").encode("utf-8")
        extratos.importar(b, entrada, "bb-ted.csv", "ana", cb_bb["id"])
        saida = bancos.buscar(b, texto="TRANSF ENTRE CONTAS")[0]
        chegada = bancos.buscar(b, texto="TED RECEBIDA")[0]
        igual((saida["par_id"], chegada["par_id"]), (chegada["id"], saida["id"]),
              "as duas pontas foram pareadas")
        igual(chegada["categoria"], "TRANSFERENCIA_ENTRE_CONTAS",
              "a ponta 'TED RECEBIDA' passou a transferência pelo par")
        recusa(lambda: bancos.gerar_lancamento(b, saida["id"],
                                               pc["receita"]["id"], "", "ana"),
               "transferência com contrapartida de RECEITA", "não é receita")
        recusa(lambda: bancos.gerar_lancamento(b, saida["id"],
                                               pc["desp_banc"]["id"], "", "ana"),
               "transferência com contrapartida de DESPESA", "não é receita")
        lt = bancos.gerar_lancamento(b, saida["id"], pc["bb"]["id"], "", "ana")
        grupos = {p["grupo"] for p in lt["partidas"]}
        igual(grupos, {"ATIVO"}, "o lançamento só toca contas do ATIVO")
        igual(lt["status"], "PENDENTE", "e nasce PENDENTE")
        ligados = {r_[0] for r_ in b.con.execute(
            "SELECT movimento_id FROM conciliacao WHERE alvo_id=? AND "
            "desfeita_utc=''", (lt["id"],))}
        igual(ligados, {saida["id"], chegada["id"]},
              "as duas pontas ficam ligadas ao MESMO lançamento")
        lancamentos.confirmar(b, lt["id"], "ana")
        igual({bancos.obter_movimento(b, x)["estado_conciliacao"] for x in ligados},
              {"CONCILIADO"}, "confirmado o lançamento, as duas pontas conciliam")

    # ═══ 12 · conciliação ═══════════════════════════════════════════════
    secao("12 · Conciliação: sugestão, conciliado, divergente, desfeito")
    with base.abrir(dados, A) as b:
        pm = {m["id_transacao"]: m for m in bancos.buscar(b) if m["id_transacao"]}
        m1 = pm["F1"]
        cands = conciliacao.candidatos(b, m1["id"])
        ok(any(c["alvo_id"].startswith("NFSE:") and c["alvo_resumo"]["numero"] == "1"
               for c in cands), "o PIX de 5.000 tem a NFS-e nº 1 como candidata")
        igual(m1["estado_conciliacao"], "SUGESTAO", "e por isso está em SUGESTÃO")
        m3 = pm["F3"]
        ok(any(c["alvo_id"] == "ACERVO:NFE55-TESTE-1"
               for c in conciliacao.candidatos(b, m3["id"])),
           "o PIX de 3.200 ao fornecedor acha a NF-e de compra do índice")
        # Um segundo recebimento de 5.000 disputa a mesma nota.
        rival = ("Data;Histórico;Valor\r\n"
                 "06/01/2026;PIX RECEBIDO OUTRO PAGADOR;5.000,00\r\n").encode("utf-8")
        extratos.importar(b, rival, "bb-rival.csv", "ana", cb_bb["id"])
        mr = bancos.buscar(b, texto="OUTRO PAGADOR")[0]
        ok(any(c["alvo_id"] == "NFSE:" + fx.chave_nfse(1)
               for c in conciliacao.candidatos(b, mr["id"])),
           "o segundo PIX de 5.000 também tem a NFS-e nº 1 como candidata")
        recusa(lambda: conciliacao.conciliar(b, m1["id"], "DOCUMENTO_FISCAL",
                                             "NFSE:" + fx.chave_nfse(1), ""),
               "conciliar sem usuário", "quem concilia")
        c1 = conciliacao.conciliar(b, m1["id"], "DOCUMENTO_FISCAL",
                                   "NFSE:" + fx.chave_nfse(1), "ana")
        igual((c1["estado"], c1["diferenca"]), ("CONCILIADO", 0),
              "PIX de 5.000 × NFS-e de 5.000 → CONCILIADO, diferença zero")
        igual(conciliacao.candidatos(b, m1["id"]), [],
              "conciliado, a nota sai da lista de candidatos dele")
        mr = bancos.obter_movimento(b, mr["id"])
        igual((conciliacao.candidatos(b, mr["id"]), mr["estado_conciliacao"]),
              ([], "PENDENTE"),
              "e do rival também: sem candidato, ele volta a PENDENTE")
        recusa(lambda: conciliacao.conciliar(b, pm["F7"]["id"], "DOCUMENTO_FISCAL",
                                             "NFSE:" + fx.chave_nfse(1), "ana"),
               "a mesma nota em outro movimento", "outro movimento")
        recusa(lambda: conciliacao.conciliar(b, pm["F7"]["id"], "DOCUMENTO_FISCAL",
                                             "NFSE:" + fx.chave_nfse(2), "ana"),
               "valor diferente sem motivo", "motivo")
        cd = conciliacao.conciliar(b, pm["F7"]["id"], "DOCUMENTO_FISCAL",
                                   "NFSE:" + fx.chave_nfse(2), "ana",
                                   motivo="recebimento parcial")
        igual((cd["estado"], cd["diferenca"]), ("DIVERGENTE", 123456 - 77700),
              "com motivo → DIVERGENTE, com a diferença registrada")
        des = conciliacao.desfazer(b, cd["id"], "ana", "liguei errado")
        ok(des["desfeita_utc"] and des["desfeita_por"] == "ana",
           "desfazer marca quem e quando")
        igual(bancos.obter_movimento(b, pm["F7"]["id"])["estado_conciliacao"],
              "PENDENTE", "o movimento volta a PENDENTE")
        ok(conciliacao.obter(b, cd["id"]) is not None,
           "e a ligação desfeita continua guardada")
        # Lançamento gerado do movimento: SUGESTÃO até confirmar.
        mt = pm["F2"]
        lt = bancos.gerar_lancamento(b, mt["id"], pc["desp_banc"]["id"], "", "ana")
        igual(bancos.obter_movimento(b, mt["id"])["estado_conciliacao"], "SUGESTAO",
              "lançamento pendente deixa o movimento em SUGESTÃO")
        igual(lt["origem"], "BANCO", "origem BANCO")
        recusa(lambda: bancos.gerar_lancamento(b, mt["id"], pc["desp_banc"]["id"],
                                               "", "ana"),
               "segundo lançamento para o mesmo movimento", "já está ligado")
        lancamentos.cancelar(b, lt["id"], "ana", "conta errada")
        igual(bancos.obter_movimento(b, mt["id"])["estado_conciliacao"], "PENDENTE",
              "cancelado o lançamento, a ligação cai e o movimento volta a PENDENTE")
        lt2 = bancos.gerar_lancamento(b, mt["id"], pc["desp_banc"]["id"], "", "ana")
        lancamentos.confirmar(b, lt2["id"], "bia")
        igual(bancos.obter_movimento(b, mt["id"])["estado_conciliacao"],
              "CONCILIADO", "confirmado o novo lançamento → CONCILIADO")
        # Candidato de lançamento lançado à mão.
        m_novo = bancos.buscar(b, texto="PIX RECEBIDO NOVO")[0]
        manual = lancamentos.criar(b, "2026-02-01", "", "recebimento",
                                   [{"conta_id": pc["itau"]["id"], "tipo": "D",
                                     "valor": "300"},
                                    {"conta_id": pc["clientes"]["id"], "tipo": "C",
                                     "valor": "300"}], "MANUAL", usuario="ana")
        cs = conciliacao.procurar(b, m_novo["id"], docs=[])
        ok(any(c["alvo_id"] == manual["id"] for c in cs),
           "lançamento manual com o mesmo valor na conta do banco vira candidato")
        pix_cli = ("Data;Histórico;Valor\r\n"
                   "12/01/2026;PIX RECEBIDO %s;10,00\r\n" % CLIENTE).encode("utf-8")
        extratos.importar(b, pix_cli, "bb-pix.csv", "ana", cb_bb["id"])
        mcli = bancos.buscar(b, contraparte_doc=CLIENTE)
        igual((len(mcli), mcli[0]["categoria"] if mcli else ""),
              (1, "CLIENTE"), "PIX com o CNPJ do tomador: achado pelo CNPJ, "
              "e sugerido como CLIENTE pelo acervo")
        buscas = {
            "valor": len(bancos.buscar(b, valor="3.200,00")),
            "data": len(bancos.buscar(b, de="2026-01-09", ate="2026-01-09")),
            "descrição": len(bancos.buscar(b, texto="FORNECEDOR")),
            "documento": len(bancos.buscar(b, documento="000123")),
            "cnpj": len(bancos.buscar(b, contraparte_doc=CLIENTE)),
            "fornecedor": len(bancos.buscar(b, contraparte="XYZ")),
            "conta contábil": len(bancos.buscar(b, conta_contabil_id=pc["desp_banc"]["id"])),
            "estado": len(bancos.buscar(b, estado="CONCILIADO")),
        }
        ok(all(v >= 1 for v in buscas.values()),
           "busca por valor, data, descrição, documento, CNPJ, fornecedor, conta "
           "contábil e estado: %s" % buscas)

    # ═══ 13 · rastreio ═════════════════════════════════════════════════
    secao("13 · Rastreabilidade até a origem")
    with base.abrir(dados, A) as b:
        pm = {m["id_transacao"]: m for m in bancos.buscar(b) if m["id_transacao"]}
        lcx = bancos.gerar_lancamento(b, pm["F1"]["id"], pc["clientes"]["id"],
                                      "Recebimento NFS-e 1", "ana")
        rs = conciliacao.rastreio(b, pm["F1"]["id"])
        igual(rs["banco"]["numero"], "56789-0", "Banco")
        igual(rs["movimento"]["valor"], 500000, "→ Movimento")
        igual(rs["documentos_fiscais"][0]["numero"], "1", "→ Documento fiscal")
        ok("SERVIÇO PRESTADO" in rs["documentos_fiscais"][0]["operacao"],
           "→ Operação (%s)" % rs["documentos_fiscais"][0]["operacao"])
        igual(rs["lancamentos"][0]["id"], lcx["id"], "→ Lançamento")
        igual((rs["arquivos"][0]["nome_arquivo"], rs["arquivos"][0]["sha256"]),
              ("janeiro.ofx", imp_ofx["sha256"]),
              "e de volta ao arquivo original, pelo hash")
        ok(rs["arquivos"][0]["linha"] > 0, "com a linha de origem")

    # ═══ 14 · visão geral ══════════════════════════════════════════════
    secao("14 · Visão Geral: percentual só sobre o importado")
    with base.abrir(dados, A) as b:
        v = visao.resumo(b, "2026-01")
        tot = v["movimentos_importados"]
        igual(v["percentual_conciliado"],
              round(100 * v["por_estado"]["CONCILIADO"] / tot, 1),
              "percentual = conciliados / importados")
        igual(sum(v["por_estado"].values()), tot, "os estados somam o importado")
        ok(v["lancamentos_pendentes"] >= 1 and v["situacao"] == "EM_ABERTO",
           "há pendência → competência EM_ABERTO")
        igual(v["base_do_percentual"], "movimentos efetivamente importados",
              "a base do percentual é dita")
    with base.abrir(dados, B) as b:
        vb = visao.resumo(b)
        igual((vb["percentual_conciliado"], vb["situacao"]),
              (None, "SEM_MOVIMENTO"), "sem movimento: percentual None, não 0%")

    # ═══ 15 · isolamento ═══════════════════════════════════════════════
    secao("15 · A empresa A não enxerga a empresa B")
    with base.abrir(dados, A) as b:
        id_de_a = bancos.buscar(b)[0]["id"]
        imp_de_a = imp_ofx["id"]
    with base.abrir(dados, B) as b:
        igual(bancos.buscar(b), [], "B não vê movimento nenhum")
        igual(bancos.obter_movimento(b, id_de_a), None,
              "o id de um movimento de A não existe no banco de B")
        recusa(lambda: conciliacao.conciliar(b, id_de_a, "LANCAMENTO", "x", "ana"),
               "conciliar em B um movimento de A", "não encontrado")
        recusa(lambda: extratos.original(b, imp_de_a),
               "baixar em B o extrato de A", "não encontrada")
        igual(plano.listar(b), [], "B não herdou o plano de A")
        recusa(lambda: conciliacao.conciliar(
            b, id_de_a, "DOCUMENTO_FISCAL", "NFSE:" + fx.chave_nfse(1), "ana"),
            "ligar em B a NFS-e de A", "não encontrado")
    # Um banco copiado para a pasta da outra empresa é recusado.
    intruso = apoio.pasta_temp("fiscale_contabil1_intruso_")
    (intruso / B / "contabil").mkdir(parents=True)
    shutil.copy(base.caminho(dados, A), intruso / B / "contabil" / "contabil.db")
    try:
        with base.abrir(intruso, B):
            ok(False, "banco de A na pasta de B deveria ser recusado")
    except modelo.Invalido:
        ok(True, "banco contábil de A copiado para a pasta de B é recusado")

    # ═══ 16 · rotas ════════════════════════════════════════════════════
    secao("16 · Rotas: cadastro manda, leitura não cria nada")
    cadastro = [{"identidade": A, "nome": "EMPRESA A"},
                {"identidade": B, "nome": "EMPRESA B"}]
    st, r = rotas.atender("GET", "/api/contabil/movimentos", {"empresa": [A]},
                          {}, None, "ana", dados, cadastro)
    ok(st == 200 and len(r["movimentos"]) > 0, "A lista os próprios movimentos")
    st, r = rotas.atender("GET", "/api/contabil/movimento",
                          {"empresa": [B], "id": [id_de_a]}, {}, None, "ana",
                          dados, cadastro)
    igual(st, 404, "movimento de A pedido com empresa=B → 404")
    st, r = rotas.atender("GET", "/api/contabil/visao",
                          {"empresa": [fx.cnpj_ficticio("999999990001")]}, {},
                          None, "ana", dados, cadastro)
    igual(st, 404, "empresa fora do cadastro → 404")
    novo = fx.cnpj_ficticio("888888880001")
    st, r = rotas.atender("GET", "/api/contabil/visao", {"empresa": [novo]}, {},
                          None, "ana", dados,
                          cadastro + [{"identidade": novo, "nome": "N"}])
    ok(st == 200 and r["visao"]["situacao"] == "SEM_MOVIMENTO",
       "empresa sem Contábil: visão vazia")
    ok(not (dados / novo).exists(), "e a leitura não criou pasta para ela")
    st, r = rotas.atender("POST", "/api/contabil/lancamentos", {},
                          {"empresa": A}, None, "", dados, cadastro)
    igual(st, 401, "escrita sem usuário → 401")
    st, r = rotas.atender("GET", "/api/contabil/vocabulario", {}, {}, None, "ana",
                          dados, cadastro)
    igual([m["id"] for m in r["modulos"] if not m["pronto"]],
          ["clientes", "fornecedores", "estoque", "imobilizado", "emprestimos",
           "socios", "fechamento", "demonstracoes", "sped"],
          "as nove áreas sem tela são declaradas 'em desenvolvimento'")
    igual(len(r["modulos"]), 13, "e a área tem os 13 itens de navegação")

    # ═══ 17 · fronteiras ═══════════════════════════════════════════════
    secao("17 · O Contábil não toca regra tributária nem rede")
    fontes = {p.name: p.read_text("utf-8") for p in
              (RAIZ / "contabil").rglob("*.py")}
    proibidos = ("apuracao", "import iss", "retencao", "vencimentos",
                 "servico_distribuicao", "checkpoint", "requests", "socket",
                 "urllib", "fiscale_backup")
    achados = sorted({(n, x) for n, t in fontes.items() for x in proibidos
                      if ("import " + x) in t or ("from " + x) in t
                      or ("import %s" % x) in t})
    igual(achados, [], "nenhum módulo do Contábil importa apuração, retenção, "
          "checkpoint, backup ou rede")
    escreve_acervo = [n for n, t in fontes.items() if "documentos.db" in t
                      and "mode=ro" not in t]
    igual(escreve_acervo, [], "o índice fiscal só é aberto em somente-leitura")
    import ast
    chamadas = {n.func.attr for n in ast.walk(ast.parse(fontes["fiscal.py"]))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    ok("conectar" not in chamadas,
       "fiscal.py não chama o conectar() da ingestão (que grava o meta)")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
