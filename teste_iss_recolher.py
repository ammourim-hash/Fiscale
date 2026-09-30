#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ISS a recolher: próprio + retido de terceiros, com a matemática exata — e o layout largo da NFS-e.

    python teste_iss_recolher.py

POR QUE ESTE TESTE EXISTE
    O ISS que a empresa leva à guia municipal tem duas parcelas:
      • PRÓPRIO: saídas SEM retenção, de empresa NÃO optante do Simples;
      • RETIDO DE TERCEIROS: entradas em que a EMPRESA, como tomadora, reteve
        (tpRetISSQN = 2).

    A pegadinha que este teste trava foi medida no acervo real (13/09/2026):
    notas de optante do Simples trazem vISSQN preenchido — uma única empresa
    somava R$ 196.448,30 — e esse ISS está DENTRO do DAS. Somá-lo na guia
    municipal cobraria duas vezes. Ele aparece à parte e fora do total.

    Também confere que a tela tem o cartão "ISS a Recolher" (só na aba de
    emitidas: entrada não mostra apuração) e que o layout foi alargado.

O QUE ESTE TESTE **NÃO** FAZ
    Não usa dado real, não vai à rede. A rota é chamada DIRETO sobre uma pasta
    de dados temporária.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                                 # noqa: E402
import teste_fixturas_fiscal as fx                          # noqa: E402

_ok = _falhas = 0
_erros: list = []

CONTA = "conta-iss-1"
EMPRESA = fx.PRESTADOR
CLIENTE = fx.TOMADOR
FORNECEDOR = fx.OUTRO


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
    ok(a == b, desc if a == b else "%s  (obtive %r, esperava %r)" % (desc, a, b))


def nota(**kw):
    base = {"emit_cnpj": EMPRESA, "toma_doc": CLIENTE, "valor_iss": 0.0,
            "tp_ret_iss": "1", "op_simp_nac": "1", "municipio_incidencia": "2611606",
            "cancelada": False}
    base.update(kw)
    return base


def main():
    import iss

    # ═══════════════════════════════════════════════════════════════════
    secao("1 · A matemática, cenário a cenário")
    r = iss.iss_a_recolher([nota(valor_iss=50.00)], EMPRESA)
    igual((r["total"], r["proprio"], r["retido_terceiros"]), (50.00, 50.00, 0.0),
          "saída sem retenção, não optante: próprio 50,00 = total")

    r = iss.iss_a_recolher([nota(valor_iss=30.00, tp_ret_iss="2")], EMPRESA)
    igual(r["total"], 0.0, "saída RETIDA pelo tomador: quem recolhe é ele -> 0,00")
    r = iss.iss_a_recolher([nota(valor_iss=30.00, tp_ret_iss="3")], EMPRESA)
    igual(r["total"], 0.0, "saída retida por intermediário -> 0,00")

    r = iss.iss_a_recolher([nota(valor_iss=20.00, op_simp_nac="3")], EMPRESA)
    igual(r["total"], 0.0, "saída de optante do Simples (ME/EPP): ISS no DAS -> fora do total")
    igual(r["proprio_no_das"], 20.00, "mas informado como incluso no DAS (20,00)")
    ok(any("DAS" in a for a in r["avisos"]), "com aviso explicando")
    r = iss.iss_a_recolher([nota(valor_iss=5.00, op_simp_nac="2")], EMPRESA)
    igual((r["total"], r["proprio_no_das"]), (0.0, 5.00), "MEI idem")

    r = iss.iss_a_recolher([nota(emit_cnpj=FORNECEDOR, toma_doc=EMPRESA,
                                 valor_iss=25.00, tp_ret_iss="2")], EMPRESA)
    igual((r["total"], r["retido_terceiros"]), (25.00, 25.00),
          "entrada retida pela empresa (tomadora): retido 25,00 = total")
    r = iss.iss_a_recolher([nota(emit_cnpj=FORNECEDOR, toma_doc=EMPRESA,
                                 valor_iss=10.00, tp_ret_iss="1")], EMPRESA)
    igual(r["total"], 0.0, "entrada SEM retenção: o prestador recolhe -> 0,00")
    r = iss.iss_a_recolher([nota(emit_cnpj=FORNECEDOR, toma_doc=CLIENTE,
                                 valor_iss=10.00, tp_ret_iss="2")], EMPRESA)
    igual(r["total"], 0.0, "retenção de nota em que a empresa NÃO é tomadora -> 0,00")
    r = iss.iss_a_recolher([nota(valor_iss=99.00, cancelada=True)], EMPRESA)
    igual(r["total"], 0.0, "nota cancelada nunca entra")

    secao("2 · O mês inteiro, somado ao centavo")
    mes = [
        nota(valor_iss=50.00),                                          # próprio
        nota(valor_iss=12.37),                                          # próprio
        nota(valor_iss=30.00, tp_ret_iss="2"),                          # retida pelo cliente
        nota(valor_iss=20.00, op_simp_nac="3"),                         # no DAS
        nota(emit_cnpj=FORNECEDOR, toma_doc=EMPRESA, valor_iss=25.00, tp_ret_iss="2"),
        nota(emit_cnpj=FORNECEDOR, toma_doc=EMPRESA, valor_iss=7.63, tp_ret_iss="2",
             municipio_incidencia="2607901"),                           # outro município
        nota(emit_cnpj=FORNECEDOR, toma_doc=EMPRESA, valor_iss=10.00, tp_ret_iss="1"),
        nota(valor_iss=99.00, cancelada=True),
    ]
    r = iss.iss_a_recolher(mes, EMPRESA)
    igual(r["proprio"], 62.37, "próprio = 50,00 + 12,37")
    igual(r["retido_terceiros"], 32.63, "retido = 25,00 + 7,63")
    igual(r["total"], 95.00, "TOTAL A RECOLHER = 95,00")
    igual(r["proprio_no_das"], 20.00, "no DAS, fora do total = 20,00")
    igual((r["qtd_notas_proprio"], r["qtd_notas_retido"]), (2, 2), "2 notas próprias, 2 retidas")
    muns = {m["municipio"]: m for m in r["por_municipio"]}
    igual(muns["2611606"]["total"], 87.37, "guia do 2611606: 62,37 + 25,00")
    igual(muns["2607901"]["total"], 7.63, "guia do 2607901: 7,63")
    ok(any("2 municípios" in a for a in r["avisos"]), "avisa que são duas guias")
    igual(round(sum(m["total"] for m in r["por_municipio"]), 2), r["total"],
          "as guias por município fecham com o total")

    r = iss.iss_a_recolher([], EMPRESA)
    igual((r["total"], r["por_municipio"], r["avisos"]), (0.0, [], []), "período vazio: zero, sem guia")

    # ═══════════════════════════════════════════════════════════════════
    with apoio.raiz_temporaria("iss_recolher_") as raiz:
        secao("3 · Da nota ao total, pela rota")
        (raiz / "certs").mkdir()
        (raiz / "certs" / "E.pfx").write_bytes(b"PFX-FALSO")
        (raiz / "certificados.json").write_text(json.dumps([{
            "id": CONTA, "cnpj": EMPRESA, "nome": "EMPRESA ISS LTDA",
            "caminho": "certs/E.pfx"}]), encoding="utf-8")
        xd = raiz / EMPRESA / "xmls"
        xd.mkdir(parents=True)

        def grava(numero, valor, **kw):
            nome, bruto = fx.xml_nfse(numero, "2026-08", valor, **kw)
            (xd / nome).write_bytes(bruto)

        grava(1, 1000.00, emitente=EMPRESA, tomador=CLIENTE, valor_iss=50.00, op_simp_nac="1")
        grava(2, 600.00, emitente=EMPRESA, tomador=CLIENTE, valor_iss=30.00,
              iss_retido=True, op_simp_nac="1")
        grava(3, 400.00, emitente=EMPRESA, tomador=CLIENTE, valor_iss=20.00, op_simp_nac="3")
        grava(4, 500.00, emitente=FORNECEDOR, tomador=EMPRESA, valor_iss=25.00,
              iss_retido=True)
        grava(5, 200.00, emitente=FORNECEDOR, tomador=EMPRESA, valor_iss=10.00)

        import core
        n1 = core.parse_nfse((xd / fx.xml_nfse(1, "2026-08", 1000.00)[0]).read_text("utf-8"))
        igual(n1["valor_iss"], 50.00, "core lê vISSQN mesmo sem retenção")
        igual(n1["op_simp_nac"], "1", "e o opSimpNac")
        igual(n1["municipio_incidencia"], "2611606", "e o município de incidência")

        import os
        os.environ["FISCALE_DADOS"] = str(raiz)
        import fiscale_dados as fd
        fd._raiz_cache = None
        fd.raiz(raiz)
        sys.modules.pop("main", None)
        import main as m
        r = m.api_iss_recolher(CONTA, "2026-08-01", "2026-08-31")
        igual(r["proprio"], 50.00, "rota: próprio 50,00 (a retida e a do Simples não entram)")
        igual(r["retido_terceiros"], 25.00, "rota: retido de terceiros 25,00")
        igual(r["total"], 75.00, "rota: ISS a recolher 75,00")
        igual(r["proprio_no_das"], 20.00, "rota: 20,00 informados como no DAS")
        r = m.api_iss_recolher(CONTA, "2026-09-01", "2026-09-30")
        igual(r["total"], 0.0, "outro mês: zero")

    # ═══════════════════════════════════════════════════════════════════
    secao("4 · A tela: o cartão 'ISS a Recolher'")
    tela = (RAIZ / "nfse" / "frontend" / "index.html").read_text("utf-8")
    ok('<div id="issBox"></div>' in tela, "o espaço do cartão existe no painel de totalizadores")
    ok("ISS a Recolher" in tela, "com o título 'ISS a Recolher'")
    ok("/api/iss-recolher" in tela, "chamando a rota nova")
    ini = tela.index("async function carregarIss(){")
    corpo = tela[ini:tela.index("\n}\n", ini)]
    ok("if(modo!=='emitidas') return;" in corpo,
       "só na aba de emitidas (entrada não mostra apuração)")
    ok("carregarIss();" in tela, "e é carregado junto com a busca de notas")
    # O PROXY DO FISCALE SÓ REPASSA O QUE ESTÁ LISTADO. Sem a rota em
    # NFSE_PREFIXOS, a tela real (porta 8777) recebe 404 calado e o cartão
    # simplesmente não aparece — o teste da rota direto passaria verde.
    # A tupla é lida pelo AST: recortar o texto "até o primeiro `)`" parava no
    # primeiro comentário com parênteses e perdia metade da lista, verde.
    srv = (RAIZ / "fiscale_server.py").read_text("utf-8")
    prefixos = ()
    for no in ast.parse(srv).body:
        if isinstance(no, ast.Assign) and any(
                isinstance(a, ast.Name) and a.id == "NFSE_PREFIXOS" for a in no.targets):
            prefixos = tuple(ast.literal_eval(no.value))
    faltam = [p for p in ("/api/recife", "/api/clientes", "/api/prefeituras",
                          "/api/cte", "/favicon.ico") if p not in prefixos]
    igual(faltam, [], "a tupla NFSE_PREFIXOS foi lida inteira (%d prefixos)" % len(prefixos))
    ok("/api/iss-recolher" in prefixos, "o proxy do Fiscale repassa /api/iss-recolher ao módulo")
    pap = (RAIZ / "fiscale_papeis.py").read_text("utf-8")
    ok('("GET", "/api/iss-recolher")' in pap, "e a rota tem papel declarado")

    node = shutil.which("node")
    if not node:
        print("  (node indisponível — execução do cartão não verificada)")
    else:
        i = tela.index("function quadroIss(d){")
        f = tela.index("\n}\n", i) + 3
        js = ("const BRL=v=>'R$ '+Number(v||0).toFixed(2);"
              "const esc=s=>String(s==null?'':s);\n" + tela[i:f] +
              "\nconsole.log(quadroIss({total:95,proprio:62.37,retido_terceiros:32.63,"
              "proprio_no_das:20,qtd_notas_proprio:2,qtd_notas_retido:2,"
              "por_municipio:[{municipio:'2611606',proprio:62.37,retido_terceiros:25,total:87.37},"
              "{municipio:'2607901',proprio:0,retido_terceiros:7.63,total:7.63}],"
              "avisos:['R$ 20,00 de ISS ... DAS']}));")
        s = subprocess.run([node, "-e", js], capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        ok(s.returncode == 0, "o cartão renderiza (%s)" % (s.stderr.strip()[:60] or "ok"))
        h = s.stdout
        ok("ISS a Recolher" in h and "R$ 95.00" in h, "mostra o total consolidado")
        ok("ISS próprio" in h and "R$ 62.37" in h, "o próprio")
        ok("ISS retido de terceiros" in h and "R$ 32.63" in h, "e o retido de terceiros")
        ok("2607901" in h, "com a guia por município quando há mais de um")
        ok("DAS" in h, "e o aviso do Simples")

    secao("5 · Layout: a lateral de cartões deixou de ser espremida")
    ok(".wrap{max-width:2400px" in tela,
       "tela cheia: sem o teto de 1680px (só 2400px, contra ultrawide)")
    ok('id="dataIni" onchange="carregarNotas()" style="width:150px"' in tela
       and 'id="dataFim" onchange="carregarNotas()" style="width:150px"' in tela,
       "os campos de data têm largura para dd/mm/aaaa (eram 108px)")
    wc = re.search(r"td\.wcell,th\.wcell\{[^}]*\}", tela)
    ok(wc and "min-width:clamp(" in wc.group(0),
       "a coluna do prestador/tomador tem largura mínima que acompanha a tela")
    bloco1700 = tela[tela.index("@media(min-width:1700px){"):][:160]
    ok("#cardResultado td{padding:7px 10px}" in bloco1700,
       "o respiro maior da tabela só vale em tela larga (não força rolagem em notebook)")
    ok("max-width:min(1600px,96vw)" in tela, "a janela larga usa até 1600px")
    ok("width:min(1440px,94vw)" in tela, "o visualizador de nota usa até 1440px")

    secao("6 · Lateral cheia, seleção de certificado visível, nenhuma rolagem")
    ok('id="latBusca"' in tela and 'id="latLista"' in tela,
       "a busca e a lista de empresas moram na lateral (não só no modal)")
    ok("function renderCertsLateral(){" in tela, "desenhadas por renderCertsLateral")
    corpo_lat = tela[tela.index("function renderCertsLateral(){"):]
    corpo_lat = corpo_lat[:corpo_lat.index("\n}\n")]
    ok("alvo.scrollHeight>alvo.clientHeight" in corpo_lat,
       "a lista corta o que não cabe (mede a altura) em vez de rolar")
    ok("refine a busca" in corpo_lat, "e diz quantas empresas ficaram de fora")
    lista = re.search(r"\.lat-lista\{[^}]*\}", tela)
    ok(lista and "overflow:hidden" in lista.group(0) and "overflow:auto" not in lista.group(0),
       "a lista não tem barra de rolagem")
    ok("@media(min-width:1501px){\n    .lateral{height:calc(100vh - 28px)}" in tela,
       "em tela larga a lateral preenche a altura (sem faixa cinza vazia)")
    ok("@media(max-width:1500px){\n    .app{grid-template-columns:1fr}" in tela,
       "até 1500px os cartões sobem e a tabela ganha a largura inteira")
    ok(".lat-cert{border:2px solid var(--verde)" in tela, "o cartão do certificado tem destaque")
    ok("renderCertsLateral();" in tela[tela.index("function selecionar(id){"):][:3000],
       "selecionar uma empresa atualiza a lista da lateral")

    secao("7 · Certificado se cadastra só no módulo Clientes")
    # A tela do NFS-e só ESCOLHE e remove; cadastrar é no Clientes, onde o .pfx
    # vira cadastro da empresa. A rota POST /api/certificados continua no
    # backend porque é ela que o Clientes usa.
    for proibido in ("+ Novo certificado", "modalNovoCert", "abrirModalNovoCert",
                     "cadastrarLote", "procurarArquivo", "novoCaminho", "/api/certificados/lote"):
        ok(proibido not in tela, "a tela do NFS-e não tem mais %r" % proibido)
    ok('href="clientes.html"' in tela, "e aponta para o módulo Clientes")
    cli = (RAIZ / "web" / "clientes.html").read_text("utf-8")
    ok("/api/certificados" in cli, "o Clientes continua cadastrando pela rota de sempre")

    secao("8 · Classificação abre na empresa que está em tela no NFS-e")
    # Antes o quadro abria sem saber qual empresa estava aberta e retomava a
    # última consultada lá dentro: quem via as notas da EMPRESA J caía em
    # outra empresa e tinha de procurar de novo.
    ab = tela[tela.index("function abrirClassificacao(){"):]
    ab = ab[:ab.index("\n}\n")]
    ok("ps.set('id', certSel)" in ab, "a primeira abertura leva a empresa em tela (?id=)")
    ok("ps.set('ini', di)" in ab and "ps.set('fim', df)" in ab, "e o período pesquisado")
    ok("w.abrirEmpresa(certSel, di, df)" in ab,
       "com o quadro já carregado, troca a empresa lá dentro sem recarregar")
    cls = (RAIZ / "web" / "classificador.html").read_text("utf-8")
    ok("function abrirEmpresa(id, dIni, dFim){" in cls, "o Classificador sabe abrir uma empresa")
    ok("window.abrirEmpresa=abrirEmpresa;" in cls, "e expõe isso para o NFS-e")
    ok("if(!abrirEmpresa(q.get('id'), q.get('ini'), q.get('fim'))) restaurarUltimo();" in cls,
       "a empresa de quem abriu vence a última guardada; sem ela, retoma de onde parou")
    ok("if(!id || !certs.some(c=>c.id===id)) return false;" in cls,
       "empresa inexistente não muda nada na tela")

    secao("9 · Visualizador de nota: PDF inteiro e dados sem rolagem")
    # O leitor de PDF abria a folha na largura e mostrava só o topo do DANFSe;
    # a coluna de dados tinha overflow:auto. As duas rolagens saíram.
    ok("#toolbar=1&navpanes=0&view=Fit" in tela,
       "o PDF abre com a página inteira ajustada ao quadro (view=Fit)")
    lado = re.search(r"\.vis-lado\{[^}]*\}", tela)
    ok(lado and "overflow:hidden" in lado.group(0) and "overflow:auto" not in lado.group(0),
       "a coluna de dados da nota não tem barra de rolagem")

    secao("10 · Detalhe da nota espelha o XML/DANFSe: toda linha, mesmo vazia")
    # Pedido de 13/09/2026: o painel ao lado do PDF escondia linha zerada na
    # entrada, mostrava só o total composto e não tinha o PIS/COFINS de
    # apuração própria (tpRetPisCofins=2). Agora são as linhas do DANFSe.
    import core
    ROTULOS = ("IRRF", "Contribuição Previdenciária - Retida", "Contribuições Sociais - Retidas",
               "PIS - Débito Apuração Própria", "COFINS - Débito Apuração Própria",
               "ISSQN Retido", "Total das Retenções Federais", "PIS/COFINS - Débito Apur. Própria")
    for r in ROTULOS:
        ok("'%s'" % r in tela, "linha do DANFSe no detalhe: %s" % r)
    n_xml = core.parse_nfse(fx.xml_nfse(7, "2026-08", 5000.0, tp_ret_piscofins="1", ret_pis=32.5,
                                        ret_cofins=150.0, ret_csll=50.0, ret_irrf=75.0)[1].decode())
    igual(n_xml["xml_v_ret_csll"], 50.0, "core entrega o vRetCSLL como veio no XML")
    igual((n_xml["xml_v_pis"], n_xml["xml_v_cofins"]), (32.5, 150.0), "e o vPis/vCofins crus")
    n_proprio = core.parse_nfse(fx.xml_nfse(8, "2026-08", 5000.0, tp_ret_piscofins="2",
                                            ret_pis=32.5, ret_cofins=150.0)[1].decode())
    n_vazia = core.parse_nfse(fx.xml_nfse(9, "2026-08", 800.0)[1].decode())
    node = shutil.which("node")
    if not node:
        print("  (node indisponível nesta máquina — execução JS não verificada)")
    else:
        ini = tela.index("function esc(v){")
        fim = tela.index("\n}\n", tela.index("function _visHtml(n,i){")) + 3
        js = ("const BRL=v=>'R$ '+Number(v||0).toFixed(2).replace('.',',');"
              "const fmtComp=x=>x,fmtData=x=>x,certSel='c1',modo='recebidas',notasAtuais=[],visIdx=0;\n"
              + tela[ini:fim] +
              "\nconst linhas=h=>{const o={};for(const m of h.matchAll(/<tr[^>]*><td>([^<]+)<\\/td><td>([^<]*)<\\/td><\\/tr>/g))o[m[1]]=m[2];return o;};"
              "\nconsole.log(JSON.stringify(%s.map(n=>linhas(_visHtml(n,0)))));"
              % json.dumps([n_xml, n_proprio, n_vazia]))
        saida = subprocess.run([node, "-e", js], capture_output=True, text=True,
                               encoding="utf-8", timeout=30)
        ok(saida.returncode == 0, "o JS do detalhe roda (%s)" % (saida.stderr.strip()[:120] or "ok"))
        if saida.returncode == 0:
            ret, proprio, vazia = json.loads(saida.stdout)
            igual(sorted(vazia), sorted(ROTULOS), "nota SEM valor nenhum, em ENTRADA: as 8 linhas aparecem")
            ok(all(v == "—" for v in vazia.values()), "todas com '—'")
            igual(ret["Contribuições Sociais - Retidas"], "R$ 50,00",
                  "Contribuições Sociais - Retidas = vRetCSLL do XML (como o DANFSe)")
            igual(ret["Total das Retenções Federais"], "R$ 307,50",
                  "Total das Retenções Federais = IRRF + PIS + COFINS + CSLL (sem duplicar)")
            igual(ret["PIS/COFINS - Débito Apur. Própria"], "—", "tp=1: nada de apuração própria")
            igual(proprio["PIS - Débito Apuração Própria"], "R$ 32,50",
                  "tp=2: vPis é débito de apuração própria")
            igual(proprio["COFINS - Débito Apuração Própria"], "R$ 150,00", "e vCofins também")
            igual(proprio["PIS/COFINS - Débito Apur. Própria"], "R$ 182,50", "com a soma no bloco de totais")
            igual(proprio["Total das Retenções Federais"], "—", "e não viram retenção")
    m_app = re.search(r"\.app\{display:grid;grid-template-columns:([^;]+);", tela)
    ok(m_app and "252px" not in m_app.group(1), "a lateral não é mais fixa em 252px")
    ok(m_app and "clamp(320px" in m_app.group(1), "ela cresce com a tela, a partir de 320px")
    lin = re.search(r"\.lat-linha \.v\{[^}]*\}", tela)
    ok(lin and "white-space:nowrap" in lin.group(0),
       "a linha do cartão não quebra ('Válida até 10/02/2027' em uma linha)")
    ok('title="${inteiro}"' in tela, "e texto cortado mostra o inteiro no title")
    cab = re.search(r"\.lat-cab\{[^}]*\}", tela)
    ok(cab and "white-space:nowrap" in cab.group(0),
       "o título dos cartões não quebra linha ('Mapeamento de Atividades')")
    item = re.search(r"\.lat-item\{[^}]*\}", tela)
    ok(item and "white-space:nowrap" in item.group(0), "nem o texto dos itens")
    ok("@media(max-width:1000px){\n    .lateral{grid-template-columns:1fr}" in tela,
       "e em tela estreita continua empilhando em uma coluna")

    # OS VALORES DOS CARTÕES DE RESULTADO CABEM. Com 168px e fonte fixa de 19px,
    # "R$ 204.012,54" vazava para fora do cartão (13/09/2026).
    kpi = tela.split("Cards de KPI", 1)[1]
    tot = re.search(r"\.totais\{display:grid;grid-template-columns:([^;]+);", kpi)
    ok(tot and "minmax(200px" in tot.group(1), "cartões de resultado com no mínimo 200px")
    val = re.search(r"\.stat \.val\{[^}]*\}", kpi)
    ok(val and "clamp(" in val.group(0), "o valor encolhe com a tela em vez de vazar")
    ok(val and "white-space:nowrap" in val.group(0) and "text-overflow:ellipsis" in val.group(0),
       "não quebra linha e, no limite, termina em reticências")
    ok('<div class="val" title="${c.val}">' in tela, "com o valor inteiro no title")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
