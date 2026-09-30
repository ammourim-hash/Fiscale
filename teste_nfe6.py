#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da NF-e 6 — o lote, a data de saída/entrada e o DANFE.

    python teste_nfe6.py

O QUE ESTA SUÍTE PROVA
    1. Que o lote usa **a mesma consulta da tela** — não uma segunda pesquisa.
    2. Que o ZIP entrega o XML **original do acervo, byte a byte**.
    3. Que o que não entra no pacote **aparece explicado**, e que a soma fecha.
    4. Que `dhSaiEnt` é lido, guardado separado da emissão, e que a ausência
       dele sai como ausência.
    5. Que o DANFE nasce só de XML completo, e que resumo é recusado com
       motivo.
    6. Que nada disso encosta na rede, no checkpoint ou no acervo.

POR QUE AS ROTAS SÃO CHAMADAS DIRETO
    Mesma razão da NF-e 5: o `TestClient` do FastAPI exige `httpx`, que o
    projeto não tem e não deve ganhar por comodidade de teste. As rotas são
    funções, e o dublê de `Request` carrega o que elas leem.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402
import ingestao as ing                               # noqa: E402
from ingestao import acervo as acv                   # noqa: E402
from ingestao import consulta as cq                  # noqa: E402
from ingestao import documento as dm                 # noqa: E402
from ingestao import exportacao as exp               # noqa: E402
from ingestao import indice as idx                   # noqa: E402
from ingestao import parsers as prs                  # noqa: E402
from ingestao import pipeline as pipe                # noqa: E402
from ingestao import reindexacao as rex              # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402
import danfe                                          # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                             # noqa: E402

_TENTATIVAS_DE_REDE: list[str] = []
_connect_original = _socket.socket.connect


def _connect_proibido(self, endereco, *a, **kw):
    _TENTATIVAS_DE_REDE.append(str(endereco))
    raise AssertionError(f"teste tentou abrir conexão de rede para {endereco}")


_socket.socket.connect = _connect_proibido


def secao(t):
    print(f"\n{t}")


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print(f"  ok   {desc}")
    else:
        _falhas += 1
        _erros.append(desc)
        print(f"  FALHOU  {desc}")


def igual(a, b, desc):
    ok(a == b, desc + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


def levanta(f, desc, status=None):
    try:
        f()
    except Exception as e:
        certo = status is None or getattr(e, "status_code", None) == status
        ok(certo, desc if certo else
           f"{desc} — status {getattr(e, 'status_code', '?')}, esperava {status}")
    else:
        ok(False, f"{desc} — não levantou nada")


from teste_fixturas_nfe import (                     # noqa: E402
    cnpj_ficticio, chave_ficticia, xml_nfe, xml_resumo, xml_evento, bruto,
    EMPRESA_A, FORNECEDOR,
)

# Os utilitários que `teste_nfe6b` e `teste_nfe6b_ux` também usam moram em
# `teste_apoio_nfe6.py`. Eles ficavam AQUI, e importá-los daqui reexecutava
# as 312 asserções abaixo — três vezes por rodada completa.
from teste_apoio_nfe6 import (                        # noqa: E402
    OUTRA, CONTA, SAIDA, Req, montar, carregar_main, abrir_zip,
)


# ══════════════════════════════════════════════════════════════════════════
secao("O lote não tem pesquisa própria")

_FONTE_EXP = (RAIZ / "nfse" / "backend" / "ingestao" / "exportacao.py").read_text("utf-8")
_FONTE_MAIN = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
_ini = _FONTE_MAIN.index("#  NF-e 6 — o resultado da tela vira pacote")
# O fim do bloco é o CABEÇALHO DA SEÇÃO SEGUINTE, e não uma rota vizinha.
#
# Delimitar por vizinho é frágil, e já cobrou: quando o CT-e entrou entre a
# NF-e 6 e o `/api/abrir-pasta`, o código do CT-e passou a ser lido como se
# fosse da NF-e 6 — e este teste reprovou por um `checkpoint` que não é dele.
# O mesmo defeito derrubou `teste_cadastro1` quando um bloco mudou de lugar.
_MARCAS_DE_FIM = ("#  CT-e — leitura do acervo (CTE 1)",
                  '@app.post("/api/abrir-pasta")')
_fim = min(_FONTE_MAIN.index(m) for m in _MARCAS_DE_FIM if m in _FONTE_MAIN)
_BLOCO = _FONTE_MAIN[_ini:_fim]

ok("cq.consultar" in _FONTE_EXP, "a exportação chama a camada de consulta")
ok("_filtro_da_query" in _BLOCO,
   "e as rotas do lote usam o MESMO tradutor de filtros da listagem")
for proibido in ("carregar_nfe", "nfemod", "glob(", "rglob("):
    ok(proibido not in _FONTE_EXP,
       f"a exportação não usa `{proibido}` (nada de varrer pasta)")

secao("Nenhum clique de exportação fala com a SEFAZ")
def sem_comentario(fonte: str) -> str:
    """O código, sem comentário nem docstring.

    A guarda é sobre o que o módulo CHAMA, não sobre o que ele explica —
    `exportacao.py` diz na documentação que não move checkpoint, e uma busca
    textual ingênua acusaria justamente a frase que promete o contrário.
    """
    import io as _io
    import tokenize
    saida = []
    anterior = tokenize.INDENT
    for tok in tokenize.generate_tokens(_io.StringIO(fonte).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and anterior in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                tokenize.NL, tokenize.ENCODING):
            continue          # docstring
        saida.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            anterior = tok.type
    return " ".join(saida)


_CODIGO_EXP = sem_comentario(_FONTE_EXP)
for proibido in ("distNSU", "consNSU", "consChNFe", "NFeDistribuicaoDFe",
                 "requests", "socket", "servico_distribuicao", "checkpoint"):
    ok(proibido not in _CODIGO_EXP,
       f"o CÓDIGO de `exportacao.py` não menciona `{proibido}`")
_CODIGO_BLOCO = sem_comentario(chr(10).join(
    ["if 1:"] + [" " + l for l in _BLOCO.splitlines() if l.strip()]))
for proibido in ("distNSU", "consNSU", "consChNFe", "_svc_nfe",
                 "sincronizar", "checkpoint"):
    ok(proibido not in _CODIGO_BLOCO,
       f"o CÓDIGO das rotas da NF-e 6 não menciona `{proibido}`")


# ══════════════════════════════════════════════════════════════════════════
secao("dhSaiEnt — lido, guardado à parte, e ausente quando ausente")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)

    recebida = cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=ch["recebida"])).itens[0]
    emitida = cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=ch["emitida"])).itens[0]

    ok(recebida.dh_saida_entrada.startswith("2026-08-03"),
       "a nota que traz `dhSaiEnt` tem a data de saída/entrada indexada")
    ok(recebida.dh_emissao.startswith("2026-08-01"),
       "e a emissão continua sendo a emissão")
    ok(recebida.dh_saida_entrada != recebida.dh_emissao,
       "as duas datas são DIFERENTES — nenhuma foi copiada da outra")
    igual(emitida.dh_saida_entrada, "",
          "a nota SEM `dhSaiEnt` fica com o campo vazio")
    ok(emitida.dh_emissao != "",
       "e mesmo assim tem emissão — ausência de uma não apaga a outra")
    igual(emitida.competencia[:7], "2026-08",
          "a competência continua vindo da EMISSÃO, não da saída")

    secao("O período pode ser filtrado pela saída/entrada")
    por_saida = cq.contar(raiz, EMPRESA_A, cq.Filtro(
        campo_data=cq.ENTRADA, data_de="2026-08-03", data_ate="2026-08-03"))
    igual(por_saida, 2, "duas notas saíram em 03/08")
    por_emissao = cq.contar(raiz, EMPRESA_A, cq.Filtro(
        campo_data=cq.EMISSAO, data_de="2026-08-03", data_ate="2026-08-03"))
    igual(por_emissao, 0, "e nenhuma foi EMITIDA em 03/08 — os campos não se confundem")
    fora = cq.consultar(raiz, EMPRESA_A, cq.Filtro(
        campo_data=cq.ENTRADA, data_de="2026-01-01", data_ate="2026-12-31"))
    ok(all(d.dh_saida_entrada for d in fora.itens),
       "quem não informou `dhSaiEnt` fica de fora do filtro por saída/entrada")

    ok(cq.ENTRADA in cq.CAMPOS_DATA_DISPONIVEIS,
       "`ENTRADA` é campo de data disponível")
    ok(cq.CAPTURA not in cq.CAMPOS_DATA_DISPONIVEIS,
       "e `CAPTURA` continua indisponível, com motivo")
    levanta(lambda: cq.contar(raiz, EMPRESA_A, cq.Filtro(campo_data=cq.CAPTURA)),
            "pedir CAPTURA levanta, em vez de devolver a emissão em silêncio")
    ok("advertencia_entrada" in cq.capacidades(),
       "as capacidades avisam o que filtrar por entrada custa")

    secao("Índice de ANTES da NF-e 6: recusa com o motivo, não erro opaco")
    import shutil as _sh
    import sqlite3 as _sq
    velho_banco = Path(raiz) / EMPRESA_A / idx.PASTA / idx.ARQUIVO
    copia = Path(raiz) / "indice_velho.db"
    _sh.copyfile(velho_banco, copia)
    c = _sq.connect(copia)
    # O índice sobre a coluna nasceu junto com ela; um banco de ANTES da
    # NF-e 6 não tem nenhum dos dois.
    c.execute("DROP INDEX IF EXISTS ix_doc_saient")
    c.execute("ALTER TABLE documentos DROP COLUMN dh_saida_entrada")
    c.commit(); c.close()
    _sh.copyfile(copia, velho_banco)

    ok("dh_saida_entrada" not in {
        r[1] for r in _sq.connect(f"file:{velho_banco}?mode=ro", uri=True)
        .execute("PRAGMA table_info(documentos)")},
       "o índice simulado realmente não tem a coluna")
    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro()), 5,
          "consultar por emissão continua funcionando nele")
    levanta(lambda: cq.contar(raiz, EMPRESA_A, cq.Filtro(campo_data=cq.ENTRADA)),
            "mas filtrar por ENTRADA é recusado")
    try:
        cq.contar(raiz, EMPRESA_A, cq.Filtro(campo_data=cq.ENTRADA))
    except cq.CampoDeDataIndisponivel as e:
        ok("reindex" in str(e).lower(),
           "e a recusa diz o que fazer: reindexar")
    ok(cq.consultar(raiz, EMPRESA_A, cq.Filtro()).itens[0].dh_saida_entrada == "",
       "e a listagem sai sem a saída/entrada, em vez de quebrar")
    igual(cq.CAMPO_DATA_PADRAO, cq.EMISSAO,
          "e o padrão continua sendo a emissão")


# ══════════════════════════════════════════════════════════════════════════
secao("Reindexação: `dhSaiEnt` entra sem mexer em mais nada")
with apoio.raiz_temporaria("nfe6_") as raiz:
    montar(raiz)

    # Simula o índice de ANTES da NF-e 6: apaga a coluna nova.
    import sqlite3
    banco = Path(raiz) / EMPRESA_A / idx.PASTA / idx.ARQUIVO
    con = sqlite3.connect(banco)
    con.execute("UPDATE documentos SET dh_saida_entrada = NULL")
    con.execute("UPDATE documentos SET versao_parser = 3 "
                "WHERE parser = 'nfe55'")
    con.commit()
    con.close()

    antes = rex.instantaneo(raiz, EMPRESA_A)
    r = rex.reindexar_empresa(raiz, EMPRESA_A)

    ok(r["integro"], "a reindexação é íntegra")
    igual(r["invariantes_alteradas"], 0,
          "NENHUMA invariante mudou (chave, valor, emissão, situação, papel…)")
    igual(r["documentos_sumidos"], 0, "nenhum documento sumiu")
    igual(r["documentos_antes"], r["documentos_depois"], "a quantidade é a mesma")
    igual(r["valor_antes"], r["valor_depois"], "e o valor total é o mesmo")
    ok("dh_saida_entrada" not in rex.INVARIANTES,
       "o campo novo não é invariante — ele é justamente o que muda")

    depois = cq.consultar(raiz, EMPRESA_A, cq.Filtro(
        chave=chave_ficticia(FORNECEDOR, 1))).itens[0]
    ok(depois.dh_saida_entrada.startswith("2026-08-03"),
       "e depois de reindexar a saída/entrada está lá")

    secao("Reindexar de novo não faz nada — é idempotente")
    r2 = rex.reindexar_empresa(raiz, EMPRESA_A)
    igual(r2["invariantes_alteradas"], 0, "segunda passada: zero alterações")
    igual(r2["desatualizados_antes"], 0, "e nada estava desatualizado")
    igual(prs.VERSAO_NFE55, 4, "a versão do parser subiu para 4")


# ══════════════════════════════════════════════════════════════════════════
secao("O ZIP entrega o XML original, byte a byte")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    r = main.nfe_exportar_xmls(Req(), CONTA)
    z = abrir_zip(r)
    nomes = z.namelist()

    ok(all(n.startswith("XML/") or n == "RELATORIO.csv" for n in nomes),
       "o ZIP tem só a pasta XML e o relatório — nenhuma subpasta a mais")
    ok("RELATORIO.csv" in nomes, "o RELATORIO.csv está lá")
    xmls = [n for n in nomes if n.startswith("XML/")]
    igual(len(xmls), 4, "quatro XML completos (o resumo não entra)")

    ac = acv.abrir(raiz, EMPRESA_A)
    conferidos = 0
    for nome in xmls:
        chave = nome[4:-4]
        d = cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=chave)).itens[0]
        original = ac.caminho_canonico(d.especie, d.id_documento).read_bytes()
        if hashlib.sha256(z.read(nome)).hexdigest() == \
                hashlib.sha256(original).hexdigest():
            conferidos += 1
    igual(conferidos, len(xmls),
          "o SHA-256 de CADA XML do ZIP bate com o do acervo")

    ok(all(z.read(n).lstrip().startswith(b"<?xml") for n in xmls),
       "os arquivos são XML de verdade, não reconstruções")
    ok(all("/" not in n[4:] and ".." not in n for n in xmls),
       "nenhum nome dentro do ZIP tem barra ou `..`")
    z.close()
    Path(r.path).unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════
secao("A conta fecha: resultado = incluídos + explicados")
with apoio.raiz_temporaria("nfe6_") as raiz:
    montar(raiz)

    plano = exp.planejar(raiz, EMPRESA_A, cq.Filtro())
    rec = plano.reconciliacao()
    igual(rec["documentos_no_resultado"], 5, "cinco documentos no resultado")
    igual(rec["xmls_no_zip"], 4, "quatro vão para o ZIP")
    igual(rec["nao_incluidos"], 1, "um fica de fora")
    igual(rec["por_motivo"], {exp.XML_COMPLETO_INDISPONIVEL: 1},
          "e o motivo é XML_COMPLETO_INDISPONIVEL")
    ok(rec["confere"], "a soma fecha")
    ok(exp.XML_COMPLETO_INDISPONIVEL in rec["explicacoes"],
       "o motivo vem com a explicação em português")

    igual(cq.contar(raiz, EMPRESA_A, cq.Filtro()), rec["documentos_no_resultado"],
          "e o total do plano é EXATAMENTE o total da consulta da tela")

    secao("Somente resumo: nenhum XML é inventado")
    resumos = [x for x in plano.linhas if not x.incluido]
    igual(len(resumos), 1, "um documento é só resumo")
    igual(resumos[0].conteudo, cq.RESUMO, "e ele está marcado como RESUMO")
    ok(not resumos[0].incluido, "ele não entra no ZIP")


# ══════════════════════════════════════════════════════════════════════════
secao("Canceladas: a escolha é explícita, e nenhuma some em silêncio")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)

    todos = exp.planejar(raiz, EMPRESA_A, cq.Filtro(), politica=exp.TODOS)
    validas = exp.planejar(raiz, EMPRESA_A, cq.Filtro(),
                           politica=exp.SOMENTE_VALIDAS)
    canc = exp.planejar(raiz, EMPRESA_A, cq.Filtro(),
                        politica=exp.SOMENTE_CANCELADAS)

    igual(len(todos.incluidos), 4, "TODOS inclui as quatro completas")
    igual(len(validas.incluidos), 3, "SOMENTE_VALIDAS deixa a cancelada de fora")
    igual(len(canc.incluidos), 1, "SOMENTE_CANCELADAS traz só ela")

    fora = [x for x in validas.linhas if x.motivo == exp.EXCLUIDO_POR_SITUACAO]
    igual(len(fora), 1, "a excluída aparece no plano com motivo")
    igual(fora[0].chave, ch["cancelada"], "e é a nota certa")
    igual(fora[0].situacao, dm.AUTORIZADO,
          "a situação ORIGINAL dela continua AUTORIZADO")
    igual(fora[0].situacao_atual, dm.CANCELADO,
          "e a decisão usou a situação ATUAL, não a do documento")
    ok(validas.reconciliacao()["confere"], "a conta fecha também aqui")

    levanta(lambda: exp.planejar(raiz, EMPRESA_A, cq.Filtro(),
                                 politica="INVENTADA"),
            "política desconhecida é recusada com motivo")


# ══════════════════════════════════════════════════════════════════════════
secao("Papel: o ZIP respeita o filtro da tela")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    r = main.nfe_exportar_xmls(Req(papel="DESTINATARIO"), CONTA)
    z = abrir_zip(r)
    chaves = {n[4:-4] for n in z.namelist() if n.startswith("XML/")}
    ok(ch["recebida"] in chaves, "a recebida está no pacote")
    ok(ch["transporte"] not in chaves,
       "o frete de TERCEIRO não entra como recebida")
    ok(ch["emitida"] not in chaves, "nem a emitida")
    z.close()
    Path(r.path).unlink(missing_ok=True)

    r = main.nfe_exportar_xmls(Req(papel="TRANSPORTADOR"), CONTA)
    z = abrir_zip(r)
    chaves = {n[4:-4] for n in z.namelist() if n.startswith("XML/")}
    igual(chaves, {ch["transporte"]}, "e o pacote de TRANSPORTADOR só tem o frete")
    z.close()
    Path(r.path).unlink(missing_ok=True)

    secao("Sem filtro de papel, a composição é dita antes de exportar")
    p = exp.planejar(raiz, EMPRESA_A, cq.Filtro())
    aviso = p.aviso_de_papel()
    ok("Todos os papéis selecionados" in aviso, "a frase avisa que mistura")
    ok("TRANSPORTADOR" in aviso, "e diz que há transportador no meio")
    ok("não são compras" in aviso,
       "com a advertência de que frete de terceiro não é compra")
    ok(p.composicao.get("TRANSPORTADOR", 0) >= 1, "a composição conta por papel")

    com_papel = exp.planejar(raiz, EMPRESA_A, cq.Filtro(papel="DESTINATARIO"))
    ok("DESTINATARIO" in com_papel.aviso_de_papel(),
       "com papel filtrado, a frase diz qual é")
    ok("Todos os papéis" not in com_papel.aviso_de_papel(),
       "e não fala em mistura")


# ══════════════════════════════════════════════════════════════════════════
secao("Filtros combinados chegam inteiros ao lote")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    for q, esperado, desc in (
            (dict(data_ini="2026-08-01", data_fim="2026-08-31"), 4, "período"),
            (dict(data_ini="2020-01-01", data_fim="2020-12-31"), 0, "período vazio"),
            (dict(cfop="5102"), 4, "CFOP"),
            (dict(ncm="84713012"), 4, "NCM"),
            (dict(conteudo="COMPLETO"), 4, "conteúdo completo"),
            (dict(conteudo="RESUMO"), 0, "conteúdo resumo (nenhum XML completo)"),
            # DUAS notas são recebidas: a `recebida` e a `cancelada` — a
            # fixtura manda as duas para EMPRESA_A. Esperar uma só era erro
            # meu de leitura da fixtura, não do filtro.
            (dict(papel="compra"), 2, "vocabulário antigo `compra`"),
            (dict(papel="DESTINATARIO", cfop="5102"), 2, "papel + CFOP"),
            (dict(campo_data="ENTRADA", data_ini="2026-08-03",
                  data_fim="2026-08-03"), 2, "período por saída/entrada"),
            (dict(chave=ch["recebida"]), 1, "chave"),
            (dict(texto="PRODUTO"), 4, "texto livre"),
    ):
        p = main.nfe_exportar_previa(Req(**q), CONTA)
        igual(p["xmls_no_zip"], esperado, f"o lote respeita o filtro: {desc}")
        ok(p["confere"], f"e a conta fecha com {desc}")

    levanta(lambda: main.nfe_exportar_previa(Req(papel="INVENTADO"), CONTA),
            "filtro inválido é 400 com motivo, não pacote vazio", status=400)
    levanta(lambda: main.nfe_exportar_previa(Req(canceladas="TALVEZ"), CONTA),
            "política inválida é 400 com motivo", status=400)


# ══════════════════════════════════════════════════════════════════════════
secao("RELATORIO.csv — a reconciliação em planilha")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    plano = exp.planejar(raiz, EMPRESA_A, cq.Filtro())
    csv_bytes = exp.relatorio_csv(plano)

    ok(csv_bytes.startswith(b"\xef\xbb\xbf"),
       "o CSV começa com BOM — é o que faz o Excel em português abrir certo")
    texto = csv_bytes.decode("utf-8-sig")
    linhas = [l for l in texto.splitlines() if l.strip()]
    igual(len(linhas), 6, "cabeçalho + cinco documentos")
    cabecalho = linhas[0].split(";")

    for coluna in ("chave", "numero", "serie", "emissao", "saida_entrada",
                   "emitente_cnpj_cpf", "destinatario_cnpj_cpf",
                   "papel_da_empresa", "situacao_original", "situacao_atual",
                   "valor", "conteudo", "disponibilidade_xml", "exportado",
                   "motivo"):
        ok(coluna in cabecalho, f"o CSV traz a coluna `{coluna}`")

    igual(sum(1 for l in linhas[1:] if ";SIM;" in l), 4, "quatro marcados SIM")
    igual(sum(1 for l in linhas[1:] if ";NAO;" in l), 1, "e um NAO")
    ok(exp.XML_COMPLETO_INDISPONIVEL in texto,
       "com o motivo escrito na linha dele")
    ok("2026-08-03" in texto, "a saída/entrada aparece no relatório")
    ok(";" in linhas[0] and "," not in cabecalho[0],
       "o separador é `;`, que é o que o Excel pt-BR espera")

    secao("E o CSV sozinho também sai")
    main = carregar_main(raiz)
    r = main.nfe_exportar_relatorio(Req(), CONTA)
    ok(r.body.startswith(b"\xef\xbb\xbf"), "a rota devolve o CSV com BOM")
    ok(".csv" in r.headers["content-disposition"], "com nome de CSV")


# ══════════════════════════════════════════════════════════════════════════
secao("Nomes de arquivo são sanitizados")
for entrada, proibido in (
        ("../../etc/passwd", "/"),
        ("..\\..\\certificados.json", "\\"),
        ("EMPRESA / CIA & FILHOS LTDA.", "/"),
        ("nome\x00nulo", "\x00"),
        ("C:\\Windows\\win.ini", ":")):
    limpo = exp.sanitizar(entrada)
    ok(proibido not in limpo, f"`{entrada!r}` perde o {proibido!r}")
    ok(".." not in limpo, f"e não sobra `..` em {entrada!r}")
ok(exp.sanitizar("") == "SEM_NOME", "nome vazio vira `SEM_NOME`, não ``")
ok(exp.sanitizar("...") == "SEM_NOME", "só pontos também")

with apoio.raiz_temporaria("nfe6_") as raiz:
    montar(raiz)
    plano = exp.planejar(raiz, EMPRESA_A, cq.Filtro(
        papel="DESTINATARIO", data_de="2026-08-01", data_ate="2026-08-31"))
    nome = exp.nome_do_pacote(EMPRESA_A, plano)
    ok(nome.startswith("NFE_"), f"o nome começa com NFE_ ({nome})")
    ok("2026-08" in nome, "traz a competência")
    ok("DESTINATARIO" in nome, "e o papel")
    ok(nome.endswith(".zip"), "e termina em .zip")
    ok("/" not in nome and "\\" not in nome, "sem separador de caminho")
    # A razão social do cadastro tem `/`, `&` e `.` — e não entra no nome.
    ok("CIA" not in nome, "a razão social não entra no nome do pacote")


# ══════════════════════════════════════════════════════════════════════════
secao("Pacote do Domínio: XML na raiz, e nada mais dentro")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    r = main.nfe_exportar_xmls_planos(Req(papel="DESTINATARIO"), CONTA)
    z = abrir_zip(r)
    nomes = z.namelist()
    ok(all(n.endswith(".xml") for n in nomes),
       "só arquivos .xml dentro — nenhum CSV para o importador estranhar")
    ok(all("/" not in n for n in nomes), "e todos na RAIZ, sem subpasta")
    igual({n[:-4] for n in nomes}, {ch["recebida"], ch["cancelada"]},
          "as duas recebidas, nomeadas pela CHAVE")
    ok(ch["transporte"] + ".xml" not in nomes,
       "e o frete de terceiro não entrou como recebida")

    ac = acv.abrir(raiz, EMPRESA_A)
    d = cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=ch["recebida"])).itens[0]
    igual(z.read(ch["recebida"] + ".xml"),
          ac.caminho_canonico(d.especie, d.id_documento).read_bytes(),
          "e byte a byte igual ao acervo")
    z.close()
    Path(r.path).unlink(missing_ok=True)

    secao("O fluxo de pasta (o que o escritório já usa) também vem do acervo")
    # A pasta de saída fica FORA da raiz de dados: desde a NF-e 6B, exportar
    # para dentro da área do Fiscale é recusado — o acervo nunca recebe
    # exportação. `ORG_UNICA` porque este teste é do fluxo plano.
    destino = apoio.registrar_para_apagar(
        apoio.pasta_temp("nfe6_saida_")) / "xmls"
    plano = exp.planejar(raiz, EMPRESA_A, cq.Filtro(papel="DESTINATARIO"))
    # `com_relatorio=True` explícito: desde o ajuste de UX da 6B o relatório
    # nasce DESLIGADO, porque o objetivo do lote é pôr XML na pasta. Este
    # teste é sobre o relatório, então ele pede.
    rp = exp.escrever_em_pasta(destino, raiz, EMPRESA_A, plano,
                               organizacao=exp.ORG_UNICA, com_relatorio=True)
    igual(rp["xmls_escritos"], 2, "os dois XML escritos na pasta")
    ok((destino / exp.NOME_RELATORIO).exists(),
       "com o RELATORIO.csv ao lado, fora do meio dos XML")
    ok(rp["confere"], "e a conta fecha")


# ══════════════════════════════════════════════════════════════════════════
secao("Lote vazio: pacote válido, não erro")
with apoio.raiz_temporaria("nfe6_") as raiz:
    montar(raiz)
    main = carregar_main(raiz)

    p = main.nfe_exportar_previa(Req(data_ini="2019-01-01", data_fim="2019-12-31"),
                                 CONTA)
    igual(p["documentos_no_resultado"], 0, "nenhum documento no período")
    igual(p["xmls_no_zip"], 0, "e nenhum XML")
    ok(p["confere"], "a conta fecha com zero")

    r = main.nfe_exportar_xmls(Req(data_ini="2019-01-01", data_fim="2019-12-31"),
                               CONTA)
    z = abrir_zip(r)
    igual([n for n in z.namelist() if n.startswith("XML/")], [],
          "o ZIP vazio não tem XML nenhum")
    ok("RELATORIO.csv" in z.namelist(),
       "mas tem o relatório, dizendo que não havia o que levar")
    z.close()
    Path(r.path).unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════
secao("Lote grande: teto defensivo, e o ZIP montado no disco")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao",
                   ambiente=ing.PRODUCAO)
    import json
    (Path(raiz) / "certs").mkdir(parents=True, exist_ok=True)
    (Path(raiz) / "certs" / "E.pfx").write_bytes(b"x")
    (Path(raiz) / "certificados.json").write_text(json.dumps([{
        "id": CONTA, "cnpj": EMPRESA_A, "nome": "GRANDE LTDA",
        "caminho": "certs/E.pfx"}]), encoding="utf-8")
    for i in range(1, 121):
        ac.preservar(bruto(xml_nfe(chave_ficticia(FORNECEDOR, i),
                                   numero=str(i), itens=3), nsu=str(5000 + i)))
    pipe.indexar_pendentes(raiz, EMPRESA_A)

    plano = exp.planejar(raiz, EMPRESA_A, cq.Filtro())
    igual(plano.total, 120, "120 documentos no plano")
    ok(plano.total > exp.LOTE_DA_CONSULTA / 10,
       "e o plano foi montado paginando, não puxando tudo de uma vez")

    destino = Path(raiz) / "grande.zip"
    r = exp.escrever_zip(destino, raiz, EMPRESA_A, plano)
    igual(r["xmls_escritos"], 120, "os 120 foram para o ZIP")
    ok(destino.exists() and destino.stat().st_size > 0,
       "o ZIP existe no DISCO — não foi montado na memória")
    z = zipfile.ZipFile(destino)
    igual(len([n for n in z.namelist() if n.startswith("XML/")]), 120,
          "e tem os 120 dentro")
    ok(z.testzip() is None, "o ZIP não está corrompido")
    z.close()

    levanta(lambda: exp.planejar(raiz, EMPRESA_A, cq.Filtro(), limite=10),
            "acima do teto o lote é RECUSADO com motivo, não truncado em silêncio")
    try:
        exp.planejar(raiz, EMPRESA_A, cq.Filtro(), limite=10)
    except exp.LimiteExcedido as e:
        ok("120" in str(e) and "10" in str(e),
           "e a mensagem diz o tamanho e o teto")

    main = carregar_main(raiz)
    levanta(lambda: main.nfe_exportar_xmls(Req(), CONTA),
            "a rota devolve 413 quando passa do teto", status=413) \
        if exp.LIMITE_DOCUMENTOS < 120 else ok(
            True, "abaixo do teto a rota entrega normalmente")


# ══════════════════════════════════════════════════════════════════════════
secao("Arquivo ausente no acervo: explicado, não silenciado")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    d = cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=ch["recebida"])).itens[0]
    ac = acv.abrir(raiz, EMPRESA_A)
    alvo = ac.caminho_canonico(d.especie, d.id_documento)
    alvo.unlink()

    plano = exp.planejar(raiz, EMPRESA_A, cq.Filtro())
    destino = Path(raiz) / "com_falta.zip"
    r = exp.escrever_zip(destino, raiz, EMPRESA_A, plano)

    igual(r["xmls_escritos"], 3, "três XML foram (o que sumiu, não)")
    igual(r["por_motivo"].get(exp.ARQUIVO_AUSENTE_NO_ACERVO), 1,
          "e o ausente aparece com o motivo certo")
    ok(r["confere"], "a conta continua fechando")
    z = zipfile.ZipFile(destino)
    csv_texto = z.read("RELATORIO.csv").decode("utf-8-sig")
    ok(exp.ARQUIVO_AUSENTE_NO_ACERVO in csv_texto,
       "o CSV dentro do ZIP registra a ausência")
    ok(ch["recebida"] in csv_texto,
       "com a chave do documento que faltou")
    z.close()


# ══════════════════════════════════════════════════════════════════════════
secao("Segurança do lote")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    # Nenhum caminho vem do navegador — nem existe parâmetro para isso.
    import inspect
    for rota in (main.nfe_exportar_xmls, main.nfe_exportar_xmls_planos,
                 main.nfe_exportar_previa, main.nfe_exportar_relatorio,
                 main.nfe_exportar_danfes, main.nfe_danfe):
        p = set(inspect.signature(rota).parameters)
        ok(not (p & {"caminho", "arquivo", "pasta", "path", "file"}),
           f"`{rota.__name__}` não aceita caminho de arquivo")

    for veneno in ("../../etc/passwd", "..\\..\\certificados.json",
                   "C:\\Windows\\win.ini", "%2e%2e%2f", "'; DROP TABLE--"):
        p = main.nfe_exportar_previa(Req(chave=veneno), CONTA)
        igual(p["documentos_no_resultado"], 0,
              f"chave envenenada não acha nada: {veneno!r}")
        levanta(lambda v=veneno: main.nfe_danfe(CONTA, chave=v),
                f"e o DANFE recusa: {veneno!r}", status=404)

    # Documento de OUTRA empresa não sai pelo lote desta.
    ac_outra = acv.abrir(raiz, OUTRA, servico="nfe_distribuicao",
                         ambiente=ing.PRODUCAO)
    chave_outra = chave_ficticia(FORNECEDOR, 99)
    ac_outra.preservar(bruto(xml_nfe(chave_outra, numero="99",
                                     destinatario=OUTRA), nsu="9999"))
    pipe.indexar_pendentes(raiz, OUTRA)

    p = main.nfe_exportar_previa(Req(chave=chave_outra), CONTA)
    igual(p["documentos_no_resultado"], 0,
          "documento de outra empresa não aparece no lote desta")
    levanta(lambda: main.nfe_danfe(CONTA, chave=chave_outra),
            "nem o DANFE dele", status=404)

    d_outra = cq.consultar(raiz, OUTRA, cq.Filtro(chave=chave_outra)).itens[0]
    levanta(lambda: main.nfe_danfe(CONTA, id_documento=d_outra.id_documento),
            "e o id_documento de outra empresa também não passa", status=404)

    r = main.nfe_exportar_xmls(Req(), CONTA)
    z = abrir_zip(r)
    ok(chave_outra not in " ".join(z.namelist()),
       "o ZIP da empresa não contém documento de outra")
    # O CNPJ da emitente ESTÁ dentro da chave de acesso — é o documento que o
    # carrega, não o FISCALE que o expõe. O que não pode vazar é a estrutura
    # do disco.
    ok(not any(x in " ".join(z.namelist())
               for x in ("acervo", "original.xml", "\\", ":")),
       "nenhum pedaço do caminho em disco aparece nos nomes do ZIP")
    z.close()
    Path(r.path).unlink(missing_ok=True)

    secao("O caminho conferido contra a raiz do acervo")
    ac = acv.abrir(raiz, EMPRESA_A)
    fora = exp.Linha(id_documento="x" * 32, especie="NFE55", chave="1" * 44,
                     numero="", serie="", emissao="", saida_entrada="",
                     emitente="", emitente_nome="", contraparte="",
                     contraparte_nome="", papel="OUTRO", papeis=(),
                     situacao="", situacao_atual="", valor="", conteudo="")
    ok(exp._caminho_seguro(ac, ac.raiz().resolve(), fora) is None,
       "id inexistente não resolve para arquivo nenhum")


# ══════════════════════════════════════════════════════════════════════════
secao("DANFE: só de XML completo")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    r = main.nfe_danfe(CONTA, chave=ch["recebida"])
    ok(r.body.startswith(b"%PDF-"), "a rota devolve um PDF de verdade")
    ok(len(r.body) > 1000, "e não é um PDF vazio")
    igual(r.media_type, "application/pdf", "com o tipo certo")
    ok("inline" in r.headers["content-disposition"],
       "abre dentro do FISCALE por padrão")

    r2 = main.nfe_danfe(CONTA, chave=ch["recebida"], baixar=True)
    ok("attachment" in r2.headers["content-disposition"], "e baixa quando pedido")
    ok(ch["recebida"] in r2.headers["content-disposition"],
       "com o nome vindo da CHAVE")
    ok("acervo" not in r2.headers["content-disposition"].lower(),
       "e nenhum pedaço do caminho em disco no cabeçalho")

    levanta(lambda: main.nfe_danfe(CONTA, chave=ch["resumo"]),
            "documento só resumo é recusado", status=409)
    try:
        main.nfe_danfe(CONTA, chave=ch["resumo"])
    except Exception as e:
        ok("XML completo" in str(e.detail),
           "e a recusa diz 'XML completo necessário'")

    secao("O conteúdo do DANFE vem do XML")
    ac = acv.abrir(raiz, EMPRESA_A)
    d = cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=ch["recebida"])).itens[0]
    xml = ac.caminho_canonico(d.especie, d.id_documento).read_bytes()
    lido = danfe.ler(xml)

    igual(lido["chave"], ch["recebida"], "a chave")
    igual(lido["numero"], "1", "o número")
    igual(lido["serie"], "1", "a série")
    igual(lido["natureza"], "VENDA DE MERCADORIA", "a natureza da operação")
    ok(lido["emissao"].startswith("2026-08-01"), "a emissão")
    ok(lido["saida_entrada"].startswith("2026-08-03"), "a saída/entrada")
    igual(lido["protocolo"]["numero"], "126000000000001", "o protocolo")
    igual(len(lido["itens"]), 1, "o item")
    igual(lido["itens"][0]["ncm"], "84713012", "com NCM")
    igual(lido["itens"][0]["cfop"], "5102", "CFOP")
    igual(lido["itens"][0]["cst"], "00", "CST do ICMS")
    igual(lido["itens"][0]["origem"], "", "origem ausente sai ausente")
    igual(lido["totais"]["vNF"], "1000.00", "e o total da nota")

    secao("Ausente é ausente também no DANFE")
    igual(danfe._n(""), "", "valor ausente vira string vazia, não `0,00`")
    igual(danfe._n("0"), "0,00", "e zero informado vira `0,00`")
    igual(danfe._n("1234.5"), "1.234,50", "o número sai no formato brasileiro")
    igual(danfe._data(""), "", "data ausente não vira data nenhuma")
    igual(danfe._data("2026-08-03T14:30:00-03:00"), "03/08/2026 14:30",
          "e a presente sai em pt-BR")

    sem_saida = danfe.ler(ac.caminho_canonico(
        *[(x.especie, x.id_documento) for x in
          cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=ch["emitida"])).itens][0]
    ).read_bytes())
    igual(sem_saida["saida_entrada"], "",
          "documento sem `dhSaiEnt` não ganha data de saída no DANFE")

    secao("O DANFE se identifica como auxiliar")
    ok("Representacao auxiliar" in danfe.RODAPE,
       "o rodapé diz que é representação auxiliar")
    ok("nfe.fazenda.gov.br" in danfe.RODAPE, "e diz onde conferir a validade")
    ok("XML" in danfe.RODAPE, "lembrando que o documento é o XML")


# ══════════════════════════════════════════════════════════════════════════
secao("CODE-128C")
ok(len(danfe._PADROES) == 107, "a tabela tem os 107 símbolos")
ok(all(sum(int(c) for c in p) == 11 for p in danfe._PADROES[:106]),
   "todo padrão soma 11 módulos")
igual(sum(int(c) for c in danfe._PADROES[106]), 13, "e o STOP soma 13")
igual(len(set(danfe._PADROES)), 107, "nenhum padrão repetido")

_m = danfe.code128c("2626074571945400015555001000001071000010702")
igual(sum(w for w, _ in _m), 277,
      "uma chave de 44 dígitos vira 277 módulos (start+22+dv+stop)")
ok(_m[0] == (2, True), "começa com barra")
ok(danfe.code128c("12") != danfe.code128c("13"),
   "dígitos diferentes dão códigos diferentes")
igual(danfe.code128c("1234"), danfe.code128c("1234"),
      "e a codificação é determinística")
levanta(lambda: danfe.code128c("sem digitos"),
        "texto sem dígito é recusado")


# ══════════════════════════════════════════════════════════════════════════
secao("Reforma no DANFE: presente, rotulada, fora da grade oficial")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    ac = acv.abrir(raiz, EMPRESA_A)
    d = cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=ch["recebida"])).itens[0]
    xml = ac.caminho_canonico(d.especie, d.id_documento).read_bytes().decode("utf-8")
    com_reforma = xml.replace("</imposto>", """
      <IBSCBS><CST>000</CST><cClassTrib>000001</cClassTrib>
        <gIBSCBS><vBC>1000.00</vBC>
          <gIBSUF><pIBSUF>0.1000</pIBSUF><vIBSUF>1.00</vIBSUF></gIBSUF>
          <gIBSMun><pIBSMun>0.0500</pIBSMun><vIBSMun>0.50</vIBSMun></gIBSMun>
          <gCBS><pCBS>0.9000</pCBS><vCBS>9.00</vCBS></gCBS>
        </gIBSCBS></IBSCBS></imposto>""").encode("utf-8")

    lido = danfe.ler(com_reforma)
    r = lido["itens"][0]["reforma"]
    igual(r["cst"], "000", "o CST da Reforma é lido")
    igual(r["classificacao"], "000001", "e o cClassTrib")
    igual(r["ibs_uf_valor"], "1.00", "o IBS UF")
    igual(r["ibs_mun_valor"], "0.50", "o IBS Municipal")
    igual(r["cbs_valor"], "9.00", "e a CBS")

    pdf = danfe.gerar(com_reforma)
    ok(pdf.startswith(b"%PDF-"), "o PDF com Reforma é gerado")
    ok(len(pdf) > len(danfe.gerar(xml.encode("utf-8"))),
       "e é maior que o mesmo documento sem ela — o bloco existe")

    fonte = (RAIZ / "nfse" / "backend" / "danfe.py").read_text("utf-8")
    ok("nao calcula" in fonte or "não calcula" in fonte,
       "o código declara que o FISCALE não calcula IBS/CBS/IS")
    ok("INFORMADOS NO XML" in fonte,
       "e o bloco é rotulado como informado pelo documento")
    ok("fora da grade oficial" in fonte,
       "com a razão de estar fora da grade oficial escrita")


# ══════════════════════════════════════════════════════════════════════════
secao("Cache do DANFE: derivado, versionado, e nunca fonte")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    pasta = Path(raiz) / EMPRESA_A / "derivados" / "danfe"
    ok(not pasta.exists(), "antes do primeiro DANFE não há cache")

    p1 = main.nfe_danfe(CONTA, chave=ch["recebida"]).body
    arquivos = sorted(pasta.glob("*.pdf"))
    igual(len(arquivos), 1, "o primeiro pedido grava um PDF no cache")
    ok(f"__v{danfe.VERSAO}.pdf" in arquivos[0].name,
       "e o nome carrega a versão do gerador")

    p2 = main.nfe_danfe(CONTA, chave=ch["recebida"]).body
    igual(p1, p2, "o segundo pedido devolve o mesmo PDF")
    igual(len(sorted(pasta.glob("*.pdf"))), 1, "sem gerar arquivo novo")

    # Marca o arquivo do cache para provar que ele foi mesmo reusado.
    arquivos[0].write_bytes(b"%PDF-CACHE-MARCADO")
    igual(main.nfe_danfe(CONTA, chave=ch["recebida"]).body,
          b"%PDF-CACHE-MARCADO", "o cache é MESMO lido, não regerado à toa")

    secao("O cache é descartável")
    acervo_antes = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml")}
    apagados = acv.limpar_derivados(raiz, EMPRESA_A)
    ok(apagados >= 1, "`limpar_derivados` leva o cache junto com o índice")
    ok(not pasta.exists(), "a pasta do cache sumiu")
    igual({p: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml")},
          acervo_antes, "e o ACERVO ficou intacto")
    ok("derivados" in acv.DERIVADOS, "`derivados` está na lista do descartável")

    import fiscale_backup
    ok("derivados" in fiscale_backup.PASTAS_FORA,
       "e fica FORA do backup: PDF derivado não é evidência")


# ══════════════════════════════════════════════════════════════════════════
secao("DANFE em lote")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    r = main.nfe_exportar_danfes(Req(), CONTA)
    z = abrir_zip(r)
    pdfs = [n for n in z.namelist() if n.endswith(".pdf")]
    igual(len(pdfs), 4, "um PDF por XML completo")
    ok(all(n.startswith("DANFE/") for n in pdfs), "todos na pasta DANFE/")
    ok(all(z.read(n).startswith(b"%PDF-") for n in pdfs), "e todos são PDF")
    ok("NAO_GERADOS.csv" not in z.namelist(),
       "sem recusas, o CSV de não gerados não aparece")
    z.close()
    Path(r.path).unlink(missing_ok=True)

    secao("O DANFE nunca entra no pacote do Domínio")
    r = main.nfe_exportar_xmls_planos(Req(), CONTA)
    z = abrir_zip(r)
    ok(not any(n.endswith(".pdf") for n in z.namelist()),
       "nenhum PDF no pacote do Domínio")
    z.close()
    Path(r.path).unlink(missing_ok=True)

    r = main.nfe_exportar_xmls(Req(), CONTA)
    z = abrir_zip(r)
    ok(not any(n.endswith(".pdf") for n in z.namelist()),
       "nem no pacote de XML")
    z.close()
    Path(r.path).unlink(missing_ok=True)

    ok(main.LIMITE_DANFE_LOTE > 0, "existe teto para o DANFE em lote")
    ok(main.LIMITE_DANFE_LOTE < exp.LIMITE_DOCUMENTOS,
       "e ele é MENOR que o teto de XML — gerar PDF custa, copiar não")
    p = main.nfe_exportar_previa(Req(), CONTA)
    igual(p["limite_danfe_em_lote"], main.LIMITE_DANFE_LOTE,
          "a prévia informa o teto para a tela poder avisar antes")


# ══════════════════════════════════════════════════════════════════════════
secao("A rota antiga de exportação também passou a ler o acervo")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    _bloco_antigo = _FONTE_MAIN[_FONTE_MAIN.index('@app.post("/api/nfe/exportar")'):
                                _FONTE_MAIN.index('@app.post("/api/abrir-pasta")')]
    ok("carregar_nfe" not in _bloco_antigo,
       "ela não usa mais o leitor legado")
    ok("_exp.escrever_em_pasta" in _bloco_antigo, "e escreve a partir do acervo")

    r = main.nfe_exportar({"id": CONTA})
    igual(r["quantidade"], 4,
          "`quantidade` (o nome que a tela antiga consome) continua existindo")
    ok(r["confere"], "e agora vem com a reconciliação junto")
    ok("aviso_de_papel" in r, "e com o aviso de composição por papel")
    pasta = Path(r["pasta"])
    ok(pasta.exists(), "a pasta foi criada")
    ok("CIA" not in str(pasta) or "/" not in pasta.name,
       "a razão social do cadastro foi sanitizada no caminho")
    igual(len(list(pasta.glob("*.xml"))), 4, "com os quatro XML dentro")


# ══════════════════════════════════════════════════════════════════════════
secao("A tela")
_HTML = (RAIZ / "web" / "nfe_documentos.html").read_text("utf-8")

ok("Exportar" in _HTML, "a tela tem o botão Exportar")
ok('class="menu"' in _HTML, "num menu, não em quatro botões concorrentes")
for rota in ("/api/nfe/exportar/xmls", "/api/nfe/exportar/xmls-planos",
             "/api/nfe/exportar/relatorio", "/api/nfe/exportar/danfes",
             "/api/nfe/exportar/previa", "/api/nfe/danfe"):
    ok(rota in _HTML, f"e chama `{rota}`")
ok("queryExportacao" in _HTML and "query(" in _HTML,
   "a exportação reusa o montador de filtros da listagem")
ok("fCanceladasExp" in _HTML, "a escolha de canceladas está na tela")
for opcao in ("Todos os documentos", "Somente situação atual válida",
              "Somente as canceladas"):
    ok(opcao in _HTML, f"com a opção «{opcao}»")
ok("Saída / Entrada" in _HTML, "o detalhe mostra Saída/Entrada")
ok("o documento não informou" in _HTML,
   "e diz `—` com o motivo quando ela falta")
ok("Data de saída / entrada" in _HTML,
   "e dá para filtrar o período por ela")
ok("dhSaiEnt" in _HTML, "com o nome do campo oficial à vista")
ok("Visualizar DANFE" in _HTML, "o DANFE tem botão de visualizar")
ok("Baixar DANFE" in _HTML, "e de baixar")
ok("em preparação" not in _HTML,
   "o aviso «DANFE em preparação» saiu — ele existe agora")
ok("representação auxiliar" in _HTML,
   "e a tela diz que o DANFE é representação auxiliar")
ok("iframe" in _HTML, "abre dentro do FISCALE")
ok("max-width:900px" in _HTML, "com caminho próprio no celular")
ok("DANFE indisponível" in _HTML, "e resumo mostra a indisponibilidade")

for proibido in ("sqlite", "acervo/", "C:\\\\", "original.xml", "documentos.db"):
    ok(proibido not in _HTML, f"a tela não conhece `{proibido}`")


# ══════════════════════════════════════════════════════════════════════════
secao("Nada foi tocado: checkpoint, acervo, rede")
with apoio.raiz_temporaria("nfe6_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO)
    cp.ult_nsu = "000000000000321"
    repo.salvar(cp)
    pasta = Path(raiz) / EMPRESA_A / "ingestao"
    antes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(pasta.glob("*.json"))}
    acervo_antes = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("*")
                    if p.is_file()}

    for _ in range(2):
        main.nfe_exportar_previa(Req(), CONTA)
        for r in (main.nfe_exportar_xmls(Req(), CONTA),
                  main.nfe_exportar_xmls_planos(Req(), CONTA),
                  main.nfe_exportar_danfes(Req(), CONTA)):
            Path(r.path).unlink(missing_ok=True)
        main.nfe_exportar_relatorio(Req(), CONTA)
        main.nfe_danfe(CONTA, chave=ch["recebida"])

    igual({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(pasta.glob("*.json"))}, antes,
          "nenhum arquivo de checkpoint foi tocado")
    igual(repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO).ult_nsu,
          "000000000000321", "o ultNSU continua onde estava")
    igual({p: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("*") if p.is_file()},
          acervo_antes, "e o acervo está intacto, byte a byte")

    sobras = list(Path(raiz).glob("fiscale_nfe_*.zip"))
    igual(sobras, [], "nenhum temporário ficou para trás na pasta de dados")


secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS_DE_REDE, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede NÃO estava ativa")
except AssertionError:
    ok(True, "e a trava estava mesmo ativa (conferido)")
_socket.socket.connect = _connect_original


print("\n" + "=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  - " + e)
    sys.exit(1)
print("NF-e 6: lote, dhSaiEnt e DANFE, verde.")
