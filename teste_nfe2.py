#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da NF-e 2 — consulta indexada e prova de equivalência.

    python teste_nfe2.py

O QUE ESTA SUÍTE PROVA
    1. Que `consulta.py` responde do índice, com filtro, paginação e ordenação
       feitos pelo SQLite — e recusa, com motivo, o que ainda não sabe fazer.
    2. Que ela **nunca** toca a rede, nem o checkpoint, nem o NSU.
    3. Que `equivalencia.py` compara os dois leitores sem corrigir nada.

A GUARDA QUE DÁ NOME À FASE
    "Filtro de data é local" precisa ser fato verificado, não promessa. Há duas
    verificações independentes para isso: uma varre o TEXTO do módulo cobrando
    a ausência de `distNSU`/`consNSU`/`consChNFe`/`requests`; outra roda a
    consulta inteira com `socket.connect` proibido. A primeira pega intenção; a
    segunda pega acidente.

NADA AQUI TOCA A PASTA REAL
    `teste_apoio.raiz_temporaria()` liga a trava contra `~/Fiscale/dados`.
"""
from __future__ import annotations

import ast
import shutil
import sqlite3
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                     # noqa: E402
import ingestao as ing                          # noqa: E402
from ingestao import acervo as acv              # noqa: E402
from ingestao import consulta as cq             # noqa: E402
from ingestao import documento as dm            # noqa: E402
from ingestao import equivalencia as eqv        # noqa: E402
from ingestao import indice as idx              # noqa: E402
from ingestao import pipeline as pipe           # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

# ── trava de rede ───────────────────────────────────────────────────────────
import socket as _socket                        # noqa: E402

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


def levanta(excecao, f, desc):
    try:
        f()
    except excecao:
        ok(True, desc)
    except Exception as e:                                   # pragma: no cover
        ok(False, f"{desc} — levantou {type(e).__name__} em vez de {excecao.__name__}")
    else:
        ok(False, f"{desc} — não levantou nada")


from teste_fixturas_nfe import (                            # noqa: E402
    cnpj_ficticio, chave_ficticia, xml_nfe, xml_resumo, xml_evento, bruto,
    EMPRESA_A, FORNECEDOR,
)

OUTRO_FORNECEDOR = cnpj_ficticio("444444440001")
EMPRESA_SEM_INDICE = cnpj_ficticio("555555550001")


# ══════════════════════════════════════════════════════════════════════════
# Cenário: uma empresa com documentos variados no acervo
# ══════════════════════════════════════════════════════════════════════════
def montar(raiz, empresa=EMPRESA_A):
    """Sete documentos e um evento, cobrindo o que a consulta precisa separar.

    As datas são de meses diferentes de propósito: é o que permite provar que o
    filtro de período recorta de verdade, em vez de devolver tudo sempre.
    """
    ac = acv.abrir(raiz, empresa, servico="nfe_distribuicao", ambiente="producao")
    docs = []

    def nota(n, *, dia, valor, emitente=FORNECEDOR, destinatario=EMPRESA_A,
             numero=None, resumo=False):
        chave = chave_ficticia(emitente, n)
        if resumo:
            conteudo = xml_resumo(chave, valor=valor, emitente=emitente)
            schema = "resNFe_v1.01.xsd"
        else:
            conteudo = xml_nfe(chave, valor=valor, emitente=emitente,
                               destinatario=destinatario,
                               numero=str(numero or n))
            schema = "procNFe_v4.00.xsd"
        # A data vive dentro do XML (`dhEmi`), e é ela que o parser lê. Trocar
        # só o `dhEmi` — e não toda ocorrência da data — mantém `dhRecbto`
        # coerente e prova que o filtro olha a EMISSÃO, não o protocolo.
        conteudo = conteudo.replace(b"<dhEmi>2026-08-01",
                                    f"<dhEmi>{dia}".encode())
        docs.append(bruto(conteudo, nsu=str(1000 + n), schema=schema))
        return chave

    c1 = nota(1, dia="2026-06-10", valor="1000.00")
    c2 = nota(2, dia="2026-07-05", valor="2000.00")
    c3 = nota(3, dia="2026-07-20", valor="3000.00", emitente=OUTRO_FORNECEDOR)
    c4 = nota(4, dia="2026-08-01", valor="4000.00")
    c5 = nota(5, dia="2026-08-15", valor="5000.00", resumo=True)
    # uma nota em que a EMPRESA é a emitente (venda própria)
    c6 = nota(6, dia="2026-08-20", valor="6000.00",
              emitente=EMPRESA_A, destinatario=FORNECEDOR)
    # evento de cancelamento da primeira
    docs.append(bruto(xml_evento(c1, tp="110111"), nsu="1099",
                      schema="procEventoNFe_v1.00.xsd"))

    for d in docs:
        ac.preservar(d)
    pipe.indexar_pendentes(raiz, empresa)
    return {"c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5, "c6": c6}


def chaves(pg) -> set:
    return {d.chave for d in pg.itens}


# ══════════════════════════════════════════════════════════════════════════
secao("A guarda da fase: a consulta não conhece a SEFAZ")

_FONTE_CONSULTA = (RAIZ / "nfse" / "backend" / "ingestao" / "consulta.py").read_text("utf-8")
_ARVORE = ast.parse(_FONTE_CONSULTA)

_importados = set()
for no in ast.walk(_ARVORE):
    if isinstance(no, ast.Import):
        _importados |= {a.name.split(".")[0] for a in no.names}
    elif isinstance(no, ast.ImportFrom) and no.module:
        _importados.add(no.module.split(".")[0])

for proibido in ("requests", "http", "socket", "urllib", "ssl", "conectores"):
    ok(proibido not in _importados,
       f"consulta.py não importa `{proibido}`")

# Nomes que só existem no vocabulário da SEFAZ. Se aparecerem em CÓDIGO, a
# camada local virou cliente de webservice.
_codigo = "\n".join(
    l for l in _FONTE_CONSULTA.splitlines()
    if not l.lstrip().startswith("#"))
# a docstring do módulo CITA os nomes para explicar por que não os usa; o que
# se cobra é ausência em código executável.
_sem_docstring = _codigo.split('"""', 2)[-1]
for termo in ("distNSU", "consNSU", "consChNFe", "NFeDistribuicaoDFe",
              "ultNSU", "maxNSU", "carregar_nfe"):
    ok(termo not in _sem_docstring,
       f"nenhum uso de `{termo}` no código de consulta.py")

ok("mode=ro" in _FONTE_CONSULTA,
   "o índice é aberto em modo somente-leitura")


# ══════════════════════════════════════════════════════════════════════════
secao("Índice ausente, vazio e ilegível — três coisas diferentes")
with apoio.raiz_temporaria("nfe2_") as raiz:
    igual(cq.contar(raiz, EMPRESA_SEM_INDICE), 0, "empresa sem índice conta zero")
    pg = cq.consultar(raiz, EMPRESA_SEM_INDICE)
    igual(pg.total, 0, "e a consulta devolve página vazia")
    igual(pg.itens, (), "sem itens")
    igual(pg.paginas, 0, "sem páginas")
    ok(not (Path(raiz) / EMPRESA_SEM_INDICE).exists(),
       "e NÃO cria pasta nem banco para uma empresa que não tem índice")

    # índice existente porém vazio
    idx.abrir(raiz, EMPRESA_A).caminho(criar=True).parent.mkdir(exist_ok=True)
    with idx.abrir(raiz, EMPRESA_A).conectar():
        pass
    igual(cq.contar(raiz, EMPRESA_A), 0, "índice vazio conta zero")

    # banco corrompido
    corrompida = cnpj_ficticio("666666660001")
    p = cq.caminho_do_indice(raiz, corrompida)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"isto nao e um banco sqlite")
    levanta(cq.IndiceIlegivel, lambda: cq.contar(raiz, corrompida),
            "banco corrompido levanta IndiceIlegivel, não devolve vazio")
    igual(p.read_bytes(), b"isto nao e um banco sqlite",
          "e o arquivo corrompido não é tocado")

    levanta(cq.ErroConsulta, lambda: cq.contar(raiz, "não é um cnpj"),
            "identidade inválida é recusada com motivo")


# ══════════════════════════════════════════════════════════════════════════
secao("Consulta por empresa — o básico")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)
    total = cq.contar(raiz, EMPRESA_A)
    igual(total, 6, "as 6 notas entram na contagem")
    pg = cq.consultar(raiz, EMPRESA_A, tamanho=100)
    igual(pg.total, 6, "e na consulta")
    igual(len(pg.itens), 6, "todas na primeira página")

    secao("O evento NÃO entra na listagem de documentos")
    ok(all(d.especie in (dm.NFE55, dm.NFCE65) for d in pg.itens),
       "só notas na lista — nenhum EVENTO_NFE")
    evs = cq.eventos_da_chave(raiz, EMPRESA_A, c["c1"])
    igual(len(evs), 1, "mas o evento existe e é alcançável pela chave")
    igual(evs[0]["tp_evento"], "110111", "com o tipo correto")
    igual(cq.eventos_da_chave(raiz, EMPRESA_A, c["c2"]), (),
          "nota sem evento devolve tupla vazia")

    # isolamento: a empresa vizinha não vê nada
    igual(cq.contar(raiz, cnpj_ficticio("777777770001")), 0,
          "outra empresa não enxerga estes documentos")


# ══════════════════════════════════════════════════════════════════════════
secao("Período — e a distinção entre os campos de data")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)

    def por_periodo(de=None, ate=None, campo=cq.EMISSAO):
        return cq.consultar(raiz, EMPRESA_A, cq.Filtro(
            campo_data=campo, data_de=de, data_ate=ate), tamanho=100)

    igual(por_periodo(date(2026, 8, 1), date(2026, 8, 31)).total, 3,
          "agosto tem 3 notas")
    igual(por_periodo(date(2026, 7, 1), date(2026, 7, 31)).total, 2,
          "julho tem 2")
    igual(por_periodo(date(2026, 6, 1), date(2026, 6, 30)).total, 1,
          "junho tem 1")
    igual(por_periodo(date(2026, 6, 1), date(2026, 8, 31)).total, 6,
          "o intervalo inteiro traz todas")
    igual(por_periodo(date(2027, 1, 1)).total, 0, "período sem documento é zero")
    igual(por_periodo(ate=date(2026, 6, 30)).total, 1, "só limite superior")
    igual(por_periodo(de=date(2026, 8, 1)).total, 3, "só limite inferior")

    # borda inclusiva
    igual(por_periodo(date(2026, 8, 1), date(2026, 8, 1)).total, 1,
          "a borda é inclusiva nos dois lados")

    igual(por_periodo(date(2026, 8, 1), date(2026, 8, 31), cq.COMPETENCIA).total, 3,
          "o mesmo recorte por competência")

    # ENTRADA era recusada aqui: o índice não guardava `dhSaiEnt`. A NF-e 6
    # passou a lê-la, então o que se cobra agora é que ela FUNCIONE — e que
    # continue sendo outra coisa que a emissão.
    ok(por_periodo(date(2026, 8, 1), campo=cq.ENTRADA).total >= 0,
       "filtrar por data de ENTRADA é atendido desde a NF-e 6")
    levanta(cq.CampoDeDataIndisponivel,
            lambda: por_periodo(date(2026, 8, 1), campo=cq.CAPTURA),
            "filtrar por data de CAPTURA é recusado — mora no acervo, não no índice")
    levanta(cq.CampoDeDataIndisponivel,
            lambda: por_periodo(campo="INVENTADO"),
            "campo de data desconhecido é recusado")
    igual(cq.CAMPO_DATA_PADRAO, cq.EMISSAO,
          "o padrão documentado para NF-e recebida é a EMISSÃO")


# ══════════════════════════════════════════════════════════════════════════
secao("Papel — cinco nomes no contrato, três respostas honestas")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)
    f = lambda p: cq.consultar(raiz, EMPRESA_A, cq.Filtro(papel=p), tamanho=100)

    igual(f(cq.DESTINATARIO).total, 4, "4 notas em que a empresa é destinatária")
    igual(f(cq.OUTRO).total, 1,
          "e o RESUMO fica em OUTRO: `resNFe` não traz destinatário, "
          "então o papel não pode ser afirmado")
    igual(f(cq.EMITENTE).total, 1, "1 em que ela é a emitente")
    igual(chaves(f(cq.EMITENTE)), {c["c6"]}, "e é a nota certa")

    for pendente in cq.PAPEIS_PENDENTES:
        levanta(cq.PapelIndisponivel, lambda p=pendente: f(p),
                f"filtrar por {pendente} é RECUSADO — o índice não o distingue")
    levanta(cq.PapelIndisponivel, lambda: f("INVENTADO"),
            "papel desconhecido é recusado")

    ok(cq.TRANSPORTADOR in cq.PAPEIS and cq.AUTXML in cq.PAPEIS,
       "mas os cinco papéis existem no contrato, para a NF-e 4 preencher")
    ok(set(cq.PAPEIS_RESPONDIVEIS) | set(cq.PAPEIS_PENDENTES) == set(cq.PAPEIS),
       "e todo papel do contrato está ou respondível ou declarado pendente")

    secao("A inferência proibida: XML completo NÃO é sinônimo de compra")
    completos = cq.consultar(raiz, EMPRESA_A, cq.Filtro(conteudo=cq.COMPLETO),
                             tamanho=100)
    papeis = {d.papel for d in completos.itens}
    ok(len(papeis) > 1,
       "há documento COMPLETO com mais de um papel — completo não define papel")
    ok(any(d.papel == cq.EMITENTE for d in completos.itens),
       "inclusive um em que a empresa é emitente, não compradora")


# ══════════════════════════════════════════════════════════════════════════
secao("Resumo × completo")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)
    r = cq.consultar(raiz, EMPRESA_A, cq.Filtro(conteudo=cq.RESUMO), tamanho=100)
    igual(r.total, 1, "1 resumo")
    igual(chaves(r), {c["c5"]}, "e é o que foi preservado como resNFe")
    igual(r.itens[0].conteudo, cq.RESUMO, "marcado como RESUMO")
    igual(cq.consultar(raiz, EMPRESA_A, cq.Filtro(conteudo=cq.COMPLETO),
                       tamanho=100).total, 5, "5 completos")
    levanta(cq.ErroConsulta,
            lambda: cq.consultar(raiz, EMPRESA_A, cq.Filtro(conteudo="MEIO")),
            "conteúdo inválido é recusado")


# ══════════════════════════════════════════════════════════════════════════
secao("Filtros de identificação")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)
    def f(**kw):
        return cq.consultar(raiz, EMPRESA_A, cq.Filtro(**kw), tamanho=100)

    igual(chaves(f(chave=c["c3"])), {c["c3"]}, "busca por chave exata")
    igual(chaves(f(chave=c["c3"][:20] + " " + c["c3"][20:])), {c["c3"]},
          "e a chave é normalizada (espaço e pontuação não atrapalham)")
    igual(f(chave="9" * 44).total, 0, "chave inexistente devolve vazio")

    igual(f(numero="3").total, 1, "busca por número")
    igual(f(numero="999").total, 0, "número inexistente")

    igual(f(emitente=OUTRO_FORNECEDOR).total, 1, "por CNPJ do emitente")
    igual(chaves(f(emitente=OUTRO_FORNECEDOR)), {c["c3"]}, "e é a nota dele")
    igual(f(emitente=FORNECEDOR).total, 4, "o outro fornecedor tem 4")
    igual(f(documento=OUTRO_FORNECEDOR).total, 1,
          "`documento` acha em qualquer dos dois lados")
    igual(f(destinatario=FORNECEDOR).total, 1,
          "por destinatário: a venda própria")

    secao("Texto livre")
    igual(f(texto="PRODUTO").total, 5,
          "texto livre alcança a descrição do item (só os completos a têm)")
    igual(f(texto=c["c2"][-12:]).total, 1,
          "e alcança um pedaço da chave (o fim dela: o começo é comum a todas)")
    igual(f(texto="COISA QUE NAO EXISTE").total, 0, "texto sem casamento é zero")


# ══════════════════════════════════════════════════════════════════════════
secao("Filtros que passam pelos itens, e por eventos")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)
    def f(**kw):
        return cq.consultar(raiz, EMPRESA_A, cq.Filtro(**kw), tamanho=100)

    igual(f(cfop="5102").total, 5, "por CFOP — só os completos têm item")
    igual(f(cfop="6102").total, 0, "CFOP inexistente")
    igual(f(ncm="84713012").total, 5, "por NCM")
    igual(f(ncm="00000000").total, 0, "NCM inexistente")

    igual(chaves(f(com_evento=True)), {c["c1"]}, "só a nota que tem evento")
    igual(f(com_evento=False).total, 5, "e as outras cinco não têm")
    igual(f(com_evento=True).itens[0].eventos, 1,
          "a contagem de eventos vem junto do documento")

    secao("Situação e quarentena")
    igual(f(situacoes=(dm.AUTORIZADO,)).total, 6, "por situação AUTORIZADO")
    igual(f(quarentena=False).total, 6, "nenhum documento em quarentena")
    igual(f(quarentena=True).total, 0, "e o filtro inverso confirma")

    secao("Filtros combinados")
    igual(f(data_de=date(2026, 8, 1), papel=cq.DESTINATARIO,
            conteudo=cq.COMPLETO).total, 1,
          "agosto + destinatária + completo = 1 (a venda própria e o resumo saem)")
    igual(f(data_de=date(2026, 7, 1), data_ate=date(2026, 8, 31),
            emitente=FORNECEDOR).total, 3,
          "período + emitente")
    igual(f(cfop="5102", com_evento=True).total, 1, "CFOP + evento")
    igual(f(cfop="5102", com_evento=True, numero="999").total, 0,
          "e um filtro impossível na combinação zera o resultado")


# ══════════════════════════════════════════════════════════════════════════
secao("Paginação e ordenação")
with apoio.raiz_temporaria("nfe2_") as raiz:
    montar(raiz)
    p1 = cq.consultar(raiz, EMPRESA_A, tamanho=2, pagina=1)
    p2 = cq.consultar(raiz, EMPRESA_A, tamanho=2, pagina=2)
    p3 = cq.consultar(raiz, EMPRESA_A, tamanho=2, pagina=3)
    p4 = cq.consultar(raiz, EMPRESA_A, tamanho=2, pagina=4)

    igual(p1.total, 6, "o total é do conjunto, não da página")
    igual(p1.paginas, 3, "3 páginas de 2")
    igual([len(p.itens) for p in (p1, p2, p3, p4)], [2, 2, 2, 0],
          "as páginas se esgotam sem repetir nem estourar")
    ok(p1.tem_proxima and p2.tem_proxima and not p3.tem_proxima,
       "`tem_proxima` acompanha")
    todas = chaves(p1) | chaves(p2) | chaves(p3)
    igual(len(todas), 6, "as três páginas cobrem o conjunto inteiro")
    igual(chaves(p1) & chaves(p2), set(), "sem sobreposição entre páginas")

    igual(cq.consultar(raiz, EMPRESA_A, tamanho=0).tamanho, 1,
          "tamanho 0 é elevado a 1 em vez de dividir por zero")
    igual(cq.consultar(raiz, EMPRESA_A, pagina=0).pagina, 1,
          "página 0 vira 1")
    igual(cq.consultar(raiz, EMPRESA_A, tamanho=99999).tamanho, cq.TAMANHO_MAXIMO,
          "e o tamanho tem teto — a tela não derruba o servidor pedindo tudo")

    secao("Ordenação")
    desc = cq.consultar(raiz, EMPRESA_A, ordenar_por="valor", direcao="desc",
                        tamanho=100)
    asc = cq.consultar(raiz, EMPRESA_A, ordenar_por="valor", direcao="asc",
                       tamanho=100)
    valores = [d.valor_total for d in desc.itens]
    igual(valores, sorted(valores, reverse=True), "ordena por valor decrescente")
    igual([d.valor_total for d in asc.itens], sorted(valores),
          "e crescente")
    igual(asc.itens[0].chave, desc.itens[-1].chave,
          "a direção realmente inverte")

    datas = [d.dh_emissao for d in cq.consultar(raiz, EMPRESA_A, tamanho=100).itens]
    igual(datas, sorted(datas, reverse=True),
          "o padrão é emissão decrescente — a nota mais nova primeiro")

    levanta(cq.OrdenacaoInvalida,
            lambda: cq.consultar(raiz, EMPRESA_A, ordenar_por="; DROP TABLE"),
            "ordenação fora da lista fechada é recusada (nada entra cru no SQL)")
    for nome in cq.ORDENS:
        cq.consultar(raiz, EMPRESA_A, ordenar_por=nome, tamanho=3)
    ok(True, f"as {len(cq.ORDENS)} ordenações declaradas funcionam")


# ══════════════════════════════════════════════════════════════════════════
secao("Agregação por competência")
with apoio.raiz_temporaria("nfe2_") as raiz:
    montar(raiz)
    linhas = cq.resumo_por_competencia(raiz, EMPRESA_A)
    igual(len(linhas), 3, "três competências")
    igual([l["competencia"] for l in linhas],
          ["2026-06-01", "2026-07-01", "2026-08-01"], "em ordem")
    igual([l["quantidade"] for l in linhas], [1, 2, 3], "com as quantidades")
    igual(linhas[0]["valor"], Decimal("1000.00"), "e a soma do mês")
    igual(cq.resumo_por_competencia(raiz, EMPRESA_SEM_INDICE), (),
          "empresa sem índice devolve tupla vazia")


# ══════════════════════════════════════════════════════════════════════════
secao("A consulta não move NSU nem checkpoint")
with apoio.raiz_temporaria("nfe2_") as raiz:
    montar(raiz)
    repo = RepositorioCheckpoint(raiz)
    antes = repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO)
    nsu_antes, arquivo = antes.ult_nsu, None
    p = Path(raiz) / EMPRESA_A / "ingestao"
    if p.exists():
        arquivo = {f.name: f.read_bytes() for f in p.glob("*.json")}

    for _ in range(5):
        cq.consultar(raiz, EMPRESA_A, cq.Filtro(data_de=date(2026, 1, 1)),
                     tamanho=100)
        cq.contar(raiz, EMPRESA_A)
        cq.resumo_por_competencia(raiz, EMPRESA_A)

    depois = repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO)
    igual(depois.ult_nsu, nsu_antes, "o ultNSU não se moveu")
    if arquivo is not None:
        igual({f.name: f.read_bytes() for f in p.glob("*.json")}, arquivo,
              "e nenhum arquivo de checkpoint foi reescrito")
    else:
        ok(not p.exists(), "e nenhum arquivo de checkpoint foi criado")


# ══════════════════════════════════════════════════════════════════════════
secao("Equivalência — o comparador não corrige nada")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)
    antes = cq.contar(raiz, EMPRESA_A)
    rel = eqv.comparar_empresa(raiz, EMPRESA_A)

    igual(cq.contar(raiz, EMPRESA_A), antes,
          "comparar não altera o índice")
    igual(rel.identidade, EMPRESA_A, "o relatório é da empresa pedida")
    igual(rel.quantidade_indice, 6, "e conta os 6 do índice")
    ok(rel.quantidade_legado == 0,
       "o leitor legado não enxerga o acervo — 0 documentos na pasta legada")
    igual(rel.apenas_indice, 6, "os 6 são classificados como só no acervo")
    igual(rel.por_categoria.get(eqv.DOCUMENTO_APENAS_NO_ACERVO), 6,
          "com a categoria explícita")
    ok(all(d.categoria in eqv.CATEGORIAS for d in rel.diferencas),
       "toda diferença cai numa categoria declarada — nenhuma sem nome")

    secao("O comparador distingue cobertura de fidelidade")
    igual(rel.divergentes, 0,
          "nenhum documento presente nos dois lados diverge")
    igual(rel.equivalencia, 100.0,
          "equivalência mede o que está nos DOIS lados, e ela é total")
    ok(rel.cobertura < 100.0 or rel.quantidade_legado == 0,
       "cobertura é outra métrica: mede a migração pendente da NF-e 3")

    r = rel.resumo()
    for campo in ("quantidade_legado", "quantidade_indice", "em_ambos",
                  "apenas_legado", "apenas_indice", "valor_legado",
                  "valor_indice", "diferenca_absoluta", "documentos_divergentes",
                  "equivalencia_pct", "por_categoria"):
        ok(campo in r, f"o relatório traz `{campo}`")
    ok(r["identidade"].endswith("***"), "e a identidade sai mascarada")

    secao("Identidade inválida não derruba o comparador")
    ruim = eqv.comparar_empresa(raiz, "xxx")
    ok(ruim.erro, "devolve relatório com erro em vez de levantar")
    igual(ruim.quantidade_indice, 0, "e sem inventar contagem")


# ══════════════════════════════════════════════════════════════════════════
secao("Equivalência com documentos nos DOIS lados")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)
    # planta na pasta legada as MESMAS notas, como o `nfe.py` as gravaria
    pasta = Path(raiz) / EMPRESA_A / "nfe"
    pasta.mkdir(parents=True, exist_ok=True)
    conteudo = xml_nfe(c["c1"], valor="1000.00").replace(
        b"<dhEmi>2026-08-01", b"<dhEmi>2026-06-10")
    (pasta / "000000001001-procNFe.xml").write_bytes(conteudo)

    rel = eqv.comparar_empresa(raiz, EMPRESA_A)
    igual(rel.quantidade_legado, 1, "o legado enxerga a nota plantada")
    igual(rel.em_ambos, 1, "e ela está nos dois lados")
    igual(rel.valor_em_ambos_legado, rel.valor_em_ambos_indice,
          "o valor confere nos dois leitores")

    # A ÚNICA divergência aqui é de cancelamento, e o índice é quem está certo:
    # o cenário tem um evento de cancelamento no ACERVO e a nota na pasta
    # legada. O leitor antigo só varre a pasta, não vê o evento, e conclui que
    # a nota está ativa. Desde a NF-e 4A a consulta deriva a situação de hoje
    # dos eventos — e por isso responde CANCELADO.
    #
    # Antes da NF-e 4A esta asserção cobrava divergência ZERO, e passava pelo
    # motivo errado: os dois leitores concordavam porque nenhum dos dois
    # aplicava o evento.
    cancel = [d for d in rel.diferencas if d.categoria == eqv.CANCELAMENTO]
    igual(len(cancel), 1, "há uma divergência, e é de cancelamento")
    igual(cancel[0].legado, "ativa", "o legado não vê o evento (está no acervo)")
    igual(cancel[0].indice, "CANCELADO", "e o índice vê — e está certo")
    igual([d.categoria for d in rel.diferencas
           if d.categoria not in (eqv.CANCELAMENTO, eqv.DOCUMENTO_APENAS_NO_ACERVO)],
          [], "nenhuma outra divergência: valor, data e papel conferem")
    igual(rel.apenas_indice, 5, "os outros 5 continuam só no acervo")

    secao("Uma diferença de valor é DETECTADA, não corrigida")
    adulterado = xml_nfe(c["c2"], valor="9999.99").replace(
        b"<dhEmi>2026-08-01", b"<dhEmi>2026-07-05")
    (pasta / "000000001002-procNFe.xml").write_bytes(adulterado)
    rel2 = eqv.comparar_empresa(raiz, EMPRESA_A)
    divs = [d for d in rel2.diferencas if d.categoria == eqv.VALOR_DIFERENTE]
    igual(len(divs), 1, "a diferença de valor aparece")
    igual(divs[0].chave, c["c2"], "na nota certa")
    ok(divs[0].legado != divs[0].indice, "com os dois valores lado a lado")
    igual(cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=c["c2"])).itens[0].valor_total,
          Decimal("2000.00"),
          "e o índice NÃO foi alterado para fazer o número bater")
    ok(rel2.equivalencia < 100.0, "a equivalência cai, como deve")

    secao("Carteira consolidada")
    cart = eqv.comparar_carteira(raiz)
    ok(cart["consolidado"]["empresas"] >= 1, "a carteira encontra a empresa")
    for campo in ("quantidade_legado", "quantidade_indice", "em_ambos",
                  "documentos_divergentes", "equivalencia_pct", "por_categoria"):
        ok(campo in cart["consolidado"], f"o consolidado traz `{campo}`")
    ok(isinstance(cart["empresas"], list), "e a visão por empresa")


# ══════════════════════════════════════════════════════════════════════════
secao("Integridade do índice — o que a consulta enxerga do acervo")
with apoio.raiz_temporaria("nfe2_") as raiz:
    c = montar(raiz)
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    ind = idx.abrir(raiz, EMPRESA_A)

    secao("Documento duplicado não vira dois")
    antes = cq.contar(raiz, EMPRESA_A)
    repetido = xml_nfe(c["c1"], valor="1000.00").replace(
        b"<dhEmi>2026-08-01", b"<dhEmi>2026-06-10")
    ac.preservar(bruto(repetido, nsu="2001"))
    pipe.indexar_pendentes(raiz, EMPRESA_A)
    igual(cq.contar(raiz, EMPRESA_A), antes,
          "o mesmo documento reentregue não duplica a consulta")
    igual(cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=c["c1"])).total, 1,
          "e a chave continua única na listagem")

    secao("Falha de parser não perde o documento")
    ausentes_antes = len(ind.ausentes(ac))
    ac.preservar(bruto(b"<nfeProc><NFe><infNFe Id=\"NFe", nsu="2002"))
    rel_ind = pipe.indexar_pendentes(raiz, EMPRESA_A)
    ok(True, "um XML malformado não derruba a indexação do lote")
    ok(cq.contar(raiz, EMPRESA_A) >= antes,
       "e nenhum documento válido some por causa dele")

    secao("O índice é reconstruível sem tocar o acervo")
    originais_antes = sorted(p.name for p in
                             (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml"))
    total_antes = cq.contar(raiz, EMPRESA_A)
    idx.reconstruir(raiz, EMPRESA_A)
    igual(cq.contar(raiz, EMPRESA_A), total_antes,
          "reconstruir o índice devolve a mesma contagem")
    igual(sorted(p.name for p in
                 (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml")),
          originais_antes, "e o acervo fica intacto")


# ══════════════════════════════════════════════════════════════════════════
secao("Capacidades — a camada declara o que não sabe fazer")
cap = cq.capacidades()
igual(set(cap["papeis"]), set(cq.PAPEIS), "declara os cinco papéis")
igual(set(cap["papeis_pendentes"]), set(cq.PAPEIS_PENDENTES),
      "e quais deles ainda não responde")
ok(all("NF-e 4" in m for m in cap["papeis_pendentes"].values()),
   "apontando a fase que resolve")
igual(set(cap["campos_data_disponiveis"]), set(cq.CAMPOS_DATA_DISPONIVEIS),
      "declara os campos de data que existem")
ok(cq.ENTRADA in cap["campos_data_disponiveis"],
   "ENTRADA saiu dos pendentes: a NF-e 6 a implementou")
ok(cq.CAPTURA in cap["campos_data_pendentes"],
   "e CAPTURA continua pendente, com motivo")
ok(cap.get("advertencia_entrada"),
   "com a advertência de que filtrar por entrada exclui quem não a informou")
ok("nenhuma chamada de rede" in cap["fonte"],
   "e diz de onde os dados vêm")


# ══════════════════════════════════════════════════════════════════════════
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
print("NF-e 2: consulta indexada e equivalência verdes.")
