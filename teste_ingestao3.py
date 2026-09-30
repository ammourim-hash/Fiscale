#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da ING 3A — acervo imutável, identidade, índice, auditoria.

    python teste_ingestao3.py

O QUE ESTA SUÍTE PROVA
    Que a cadeia `NF-e adquirida → preservada → identificada → deduplicada →
    indexada → normalizada → auditável` funciona, e que **cada elo falha
    sozinho**: parser quebrado não perde NSU, schema desconhecido não é
    descartado, colisão não sobrescreve documento fiscal.

NADA AQUI TOCA A REDE
    As fontes são dublês. Não há socket, não há SEFAZ, não há ADN.

NADA AQUI TOCA A PASTA REAL
    `teste_apoio.raiz_temporaria()` aponta `FISCALE_DADOS` para um temporário e
    LIGA a trava que faz qualquer tentativa de resolver `~/Fiscale/dados`
    levantar na hora.

FIXTURES SÃO FICTÍCIAS
    CNPJ com DV calculado, chaves com DV calculado, valores inventados. Nenhum
    dado de cliente e nenhum segredo real — nem como substring.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio            # noqa: E402
import ingestao as ing                 # noqa: E402
from ingestao import acervo as acv     # noqa: E402
from ingestao import auditoria as aud  # noqa: E402
from ingestao import documento as dm   # noqa: E402
from ingestao import identificacao as idf   # noqa: E402
from ingestao import indice as idx     # noqa: E402
from ingestao import parsers as prs    # noqa: E402
from ingestao import pipeline as pipe  # noqa: E402
from ingestao.distribuicao import DocumentoBruto, Lote, DistribuicaoRunner  # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

# ── trava de rede ───────────────────────────────────────────────────────────
# "os testes não usam rede" precisa ser um FATO verificado, não uma intenção
# declarada no cabeçalho. Qualquer tentativa de abrir conexão a partir daqui
# levanta — e o contador no fim prova que nenhuma houve.
import socket as _socket                                     # noqa: E402

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


# ══════════════════════════════════════════════════════════════════════════
# Fixtures — vêm de `teste_fixturas_nfe`, que é DADO e não suíte.
#
# Elas moravam aqui até a ING 3B precisar delas: `import teste_ingestao3` rodava
# esta suíte inteira dentro da outra e, de quebra, restaurava `socket.connect`,
# desligando a trava de rede da suíte que importava. Fixture é dado; suíte é
# programa.
# ══════════════════════════════════════════════════════════════════════════
from teste_fixturas_nfe import (                        # noqa: E402
    cnpj_ficticio, chave_ficticia, xml_nfe, xml_resumo, xml_evento, bruto,
    EMPRESA_A, EMPRESA_B, FORNECEDOR, CHAVE_1, CHAVE_2,
    XML_DESCONHECIDO, XML_MALFORMADO,
)


class FonteFalsa:
    """Dublê. Entrega lotes de uma lista; nunca vai à rede."""

    def __init__(self, lotes, servico="nfe_distribuicao", ambiente="producao"):
        self.servico = servico
        self.ambiente = ing.resolver_ambiente(ambiente)
        self.capacidades = frozenset({"distNSU"})
        self._lotes = list(lotes)
        self.consultas = 0

    def consultar(self, desde_nsu: str) -> Lote:
        self.consultas += 1
        if self._lotes:
            return self._lotes.pop(0)
        return Lote(sem_documentos=True, ult_nsu=desde_nsu, max_nsu=desde_nsu,
                    motivo="nada novo")


def preservar_e_indexar(raiz, empresa, docs) -> idx.RelatorioIndexacao:
    ac = acv.abrir(raiz, empresa, servico="nfe_distribuicao", ambiente="producao")
    for d in docs:
        ac.preservar(d)
    return pipe.indexar_pendentes(raiz, empresa)


# ══════════════════════════════════════════════════════════════════════════
secao("Identificação — chave, DV, espécie")
with apoio.raiz_temporaria("ing3_") as raiz:
    ok(idf.chave_valida(CHAVE_1), "a chave de teste tem DV correto")
    ok(not idf.chave_valida(CHAVE_1[:-1] + ("0" if CHAVE_1[-1] != "0" else "1")),
       "DV trocado é recusado")
    ok(not idf.chave_valida("1" * 44), "chave com todos os dígitos iguais é recusada")
    ok(not idf.chave_valida(CHAVE_1[:43]), "43 dígitos é recusado")

    i_nfe = idf.identificar(xml_nfe(CHAVE_1))
    igual(i_nfe.especie, idf.NFE55, "nfeProc é identificado como NF-e")
    igual(i_nfe.chave, CHAVE_1, "a chave sai do atributo Id")
    igual(i_nfe.prioridade, idf.PRIO_AUTORIZADO, "com protNFe é 'autorizado'")
    ok(i_nfe.identidade_forte, "e a identidade é forte")

    i_sem_prot = idf.identificar(xml_nfe(CHAVE_1, com_protocolo=False))
    igual(i_sem_prot.prioridade, idf.PRIO_COMPLETO,
          "nfeProc SEM protNFe é rebaixado a 'completo'")
    igual(i_sem_prot.id_documento, i_nfe.id_documento,
          "mas é o MESMO documento (o id não depende da completude)")

    i_res = idf.identificar(xml_resumo(CHAVE_1))
    igual(i_res.prioridade, idf.PRIO_RESUMO, "resNFe é 'resumo'")
    igual(i_res.id_documento, i_nfe.id_documento,
          "e converge para o mesmo id do documento completo")

    i_ev = idf.identificar(xml_evento(CHAVE_1))
    igual(i_ev.especie, idf.EVENTO_NFE, "procEventoNFe é evento")
    ok(i_ev.id_documento != i_nfe.id_documento,
       "EVENTO E NOTA NÃO COMPARTILHAM ID — é o que impede o evento de apagar a nota")
    igual(i_ev.chave, CHAVE_1, "embora compartilhem a chave")

    ev2 = idf.identificar(xml_evento(CHAVE_1, seq="2"))
    ok(ev2.id_documento != i_ev.id_documento, "sequências diferentes, ids diferentes")
    cce = idf.identificar(xml_evento(CHAVE_1, tp="110110"))
    ok(cce.id_documento != i_ev.id_documento, "tipos de evento diferentes, ids diferentes")

# ══════════════════════════════════════════════════════════════════════════
secao("A mesma NF-e duas vezes — idempotente")
with apoio.raiz_temporaria("ing3_") as raiz:
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    d = bruto(xml_nfe(CHAVE_1), "000000000000001")

    p1 = ac.preservar(d)
    igual(p1.resultado, acv.NOVO, "a primeira vez é NOVO")
    conteudo_apos_1 = ac.ler_original(idf.NFE55, p1.id_documento)

    p2 = ac.preservar(d)
    igual(p2.resultado, acv.DUPLICATA, "a segunda vez é DUPLICATA")
    ok(not p2.gravou_bytes, "e nada foi escrito no disco")
    igual(ac.ler_original(idf.NFE55, p1.id_documento), conteudo_apos_1,
          "o original está byte a byte igual")

    # mesmo conteúdo chegando por NSU diferente (docZip repetido pelo serviço)
    p3 = ac.preservar(bruto(xml_nfe(CHAVE_1), "000000000000009"))
    igual(p3.resultado, acv.DUPLICATA, "docZip repetido em outro NSU é duplicata")
    igual(len(ac.listar()), 1, "e o acervo continua com UM documento")

    rel = pipe.indexar_pendentes(raiz, EMPRESA_A)
    igual(rel.indexados, 1, "o índice recebe uma linha só")

    trilha = aud.abrir(raiz, EMPRESA_A)
    contagem = trilha.contar()
    igual(contagem.get(aud.DEDUPLICACAO), 2, "a trilha registra as 2 deduplicações")
    igual(contagem.get(aud.PRESERVACAO), 1, "e uma única preservação")

# ══════════════════════════════════════════════════════════════════════════
secao("Mesma chave, conteúdo diferente — nunca sobrescreve")
with apoio.raiz_temporaria("ing3_") as raiz:
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")

    # 1) resumo primeiro, completo depois: é o caminho NORMAL da distribuição
    ac.preservar(bruto(xml_resumo(CHAVE_1), "000000000000001", schema="resNFe_v1.01.xsd"))
    id_doc = idf.id_de_nfe(CHAVE_1)
    original_resumo = ac.ler_original(idf.NFE55, id_doc)

    p = ac.preservar(bruto(xml_nfe(CHAVE_1), "000000000000002"))
    igual(p.resultado, acv.COPIA_PROMOVIDA, "o XML completo PROMOVE o canônico")
    ok(not p.quarentena, "e isso NÃO é quarentena — é o fluxo esperado")
    igual(ac.ler_original(idf.NFE55, id_doc), original_resumo,
          "o original.xml continua sendo o resumo, intocado")
    ok(b"protNFe" in ac.caminho_canonico(idf.NFE55, id_doc).read_bytes(),
       "mas o canônico agora é o completo")

    # 2) o resumo chegando DEPOIS do completo não rebaixa
    p = ac.preservar(bruto(xml_resumo(CHAVE_1, valor="1234.56"), "000000000000003"))
    igual(p.resultado, acv.DUPLICATA, "o mesmo resumo de novo é duplicata")
    p = ac.preservar(bruto(xml_resumo(CHAVE_1, valor="9999.99"), "000000000000004"))
    igual(p.resultado, acv.COPIA_MENOR, "resumo diferente é guardado sem promover")
    ok(b"protNFe" in ac.caminho_canonico(idf.NFE55, id_doc).read_bytes(),
       "o canônico continua sendo o completo")

    # 3) COLISÃO: duas cópias da MESMA prioridade com conteúdo diferente
    p = ac.preservar(bruto(xml_nfe(CHAVE_1, valor="7777.77"), "000000000000005"))
    igual(p.resultado, acv.COLISAO, "mesma prioridade + conteúdo diferente = COLISÃO")
    ok(p.quarentena, "e o documento vai para quarentena")
    igual(ac.ler_original(idf.NFE55, id_doc), original_resumo,
          "o original SEGUE intocado mesmo na colisão")

    copias = ac.ler_captura(idf.NFE55, id_doc)["copias"]
    igual(len(copias), 4, "as 4 cópias distintas estão todas guardadas")

    rel = pipe.indexar_pendentes(raiz, EMPRESA_A)
    igual(rel.quarentena, 1, "o índice marca o documento como quarentena")
    linha = idx.abrir(raiz, EMPRESA_A).obter(id_doc)
    igual(linha["estado"], idx.QUARENTENA, "e o estado é QUARENTENA")
    ok(linha["motivo"], "com o motivo registrado")

    contagem = aud.abrir(raiz, EMPRESA_A).contar()
    igual(contagem.get(aud.COLISAO), 1, "a trilha registra 1 colisão")
    igual(contagem.get(aud.COPIA_NOVA), 2, "e 2 cópias novas legítimas")

# ══════════════════════════════════════════════════════════════════════════
secao("Documento sem chave reconhecível — identidade fraca, sem dedup")
with apoio.raiz_temporaria("ing3_") as raiz:
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    sem_chave = b"""<?xml version="1.0"?>
<resNFe xmlns="http://www.portalfiscal.inf.br/nfe"><CNPJ>%s</CNPJ>
<xNome>SEM CHAVE</xNome><vNF>10.00</vNF></resNFe>""" % FORNECEDOR.encode()

    i = idf.identificar(sem_chave)
    igual(i.especie, idf.NFE55, "a espécie ainda é reconhecida")
    ok(not i.identidade_forte, "mas a identidade é FRACA")
    ok(i.motivo, "e o motivo fica registrado")

    p1 = ac.preservar(bruto(sem_chave, "000000000000001"))
    igual(p1.resultado, acv.NOVO, "é preservado assim mesmo")
    p2 = ac.preservar(bruto(sem_chave, "000000000000002"))
    igual(p2.resultado, acv.DUPLICATA, "bytes idênticos continuam idempotentes")

    outro = sem_chave.replace(b"10.00", b"20.00")
    p3 = ac.preservar(bruto(outro, "000000000000003"))
    ok(p3.id_documento != p1.id_documento,
       "conteúdo diferente sem chave gera OUTRO documento — nunca funde os dois")
    igual(len(ac.listar()), 2, "e o acervo tem os dois")

# ══════════════════════════════════════════════════════════════════════════
secao("Schema desconhecido — preservado, nunca descartado")
with apoio.raiz_temporaria("ing3_") as raiz:
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    p = ac.preservar(bruto(XML_DESCONHECIDO, "000000000000001", schema="procCTeOS_v4.00.xsd"))
    igual(p.resultado, acv.NOVO, "documento de schema desconhecido É preservado")
    igual(p.identificacao.especie, idf.DESCONHECIDO, "marcado como DESCONHECIDO")
    ok(p.identificacao.hash_conteudo.startswith("sha256:"), "com hash calculado")
    igual(ac.ler_original(idf.DESCONHECIDO, p.id_documento), XML_DESCONHECIDO,
          "e os bytes preservados são exatamente os que chegaram")

    cap = ac.ler_captura(idf.DESCONHECIDO, p.id_documento)
    igual(cap["nsu"], "000000000000001", "o NSU fica registrado")
    igual(cap["schema"], "procCTeOS_v4.00.xsd", "o schema informado pelo serviço também")
    igual(cap["raiz"], "procCTeOS", "e a tag raiz encontrada")
    ok(not cap["reconhecido"], "captura.json diz que não foi reconhecido")

    rel = pipe.indexar_pendentes(raiz, EMPRESA_A)
    igual(rel.schema_desconhecido, 1, "o índice o classifica como schema desconhecido")
    igual(rel.indexados, 0, "e não o conta como indexado")
    linha = idx.abrir(raiz, EMPRESA_A).obter(p.id_documento)
    igual(linha["estado"], idx.SCHEMA_DESCONHECIDO, "o estado no índice é explícito")

    contagem = aud.abrir(raiz, EMPRESA_A).contar()
    igual(contagem.get(aud.SCHEMA_DESCONHECIDO), 1, "a trilha registra o fato")

# ══════════════════════════════════════════════════════════════════════════
secao("XML malformado — também é preservado")
with apoio.raiz_temporaria("ing3_") as raiz:
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    p = ac.preservar(bruto(XML_MALFORMADO, "000000000000001"))
    igual(p.resultado, acv.NOVO, "XML malformado é preservado")
    igual(p.identificacao.especie, idf.DESCONHECIDO, "como DESCONHECIDO")
    ok("malformado" in p.identificacao.motivo, "com o motivo dizendo que é malformado")
    igual(ac.ler_original(idf.DESCONHECIDO, p.id_documento), XML_MALFORMADO,
          "os bytes quebrados são guardados como vieram")

    rel = pipe.indexar_pendentes(raiz, EMPRESA_A)
    igual(rel.schema_desconhecido, 1, "o índice não quebra com ele")
    ok(ac.preservar(bruto(XML_MALFORMADO, "000000000000002")).resultado == acv.DUPLICATA,
       "e ele deduplica normalmente pelo hash")

# ══════════════════════════════════════════════════════════════════════════
secao("Evento e cancelamento")
with apoio.raiz_temporaria("ing3_") as raiz:
    rel = preservar_e_indexar(raiz, EMPRESA_A, [
        bruto(xml_nfe(CHAVE_1), "000000000000001"),
        bruto(xml_evento(CHAVE_1, tp="110111"), "000000000000002",
              schema="procEventoNFe_v1.00.xsd"),
        bruto(xml_evento(CHAVE_1, tp="110110", seq="1"), "000000000000003",
              schema="procEventoNFe_v1.00.xsd"),
    ])
    igual(rel.indexados, 3, "nota + 2 eventos = 3 linhas")

    ind = idx.abrir(raiz, EMPRESA_A)
    igual(len(ind.listar(especie=dm.NFE55)), 1, "uma NF-e")
    igual(len(ind.listar(especie=dm.EVENTO_NFE)), 2, "dois eventos")

    eventos = ind.eventos_da_chave(CHAVE_1)
    igual(len(eventos), 2, "os dois eventos apontam para a chave da nota")
    tipos = sorted(e["tp_evento"] for e in eventos)
    igual(tipos, ["110110", "110111"], "cancelamento e carta de correção")

    canc = [e for e in eventos if e["tp_evento"] == "110111"][0]
    igual(canc["justificativa"], "Erro de digitacao", "a justificativa do cancelamento")
    ok(canc["protocolo"], "e o protocolo do evento")

    nota = ind.listar(especie=dm.NFE55)[0]
    ok(nota["id_documento"] != canc["id_documento"],
       "a nota continua existindo com id próprio depois do cancelamento")
    igual(nota["situacao"], dm.AUTORIZADO,
          "e o cancelamento NÃO reescreveu a situação da nota (isso é da apuração)")

# ══════════════════════════════════════════════════════════════════════════
secao("Parser — Decimal, precisão e ausente != zero")
with apoio.raiz_temporaria("ing3_") as raiz:
    conteudo = xml_nfe(CHAVE_1, valor="1234.56", com_pis=True)
    ident = idf.identificar(conteudo)
    doc = prs.ParserNFe55().interpretar(conteudo, ident, identidade_empresa=EMPRESA_A)

    ok(isinstance(doc.valor_total, Decimal), "o valor total é Decimal")
    igual(str(doc.valor_total), "1234.56", "e preserva a precisão do XML")
    item = doc.extensao.itens[0]
    igual(str(item.valor_unitario), "617.2800000000",
          "as casas decimais do XML são preservadas, sem arredondar")
    igual(str(item.quantidade), "2.0000", "inclusive na quantidade")

    igual(str(item.icms.valor), "180.00", "ICMS do item lido")
    igual(item.icms.cst, "00", "com o CST")
    igual(str(item.pis.valor), "16.50", "PIS lido quando informado")

    sem_pis = xml_nfe(CHAVE_2, com_pis=False)
    doc2 = prs.ParserNFe55().interpretar(sem_pis, idf.identificar(sem_pis),
                                         identidade_empresa=EMPRESA_A)
    item2 = doc2.extensao.itens[0]
    ok(item2.pis.valor is None, "PIS AUSENTE fica None, não 0.00")
    ok(item2.pis.base is None, "a base também")
    ok(not item2.pis.informado, "e o tributo se declara não informado")
    ok(item2.icms.valor is not None, "enquanto o ICMS presente continua preenchido")

    ok(doc.extensao.totais.ipi is None, "total de IPI ausente no XML fica None")
    ok(doc.extensao.totais.icms == Decimal("180.00"), "e o de ICMS, presente, é lido")

    igual(doc.papel, dm.DESTINATARIO, "o papel da empresa é derivado corretamente")
    igual(doc.emitente.identificador, FORNECEDOR, "emitente identificado")
    igual(doc.competencia, date(2026, 8, 1), "competência derivada da emissão")
    igual(doc.situacao, dm.AUTORIZADO, "situação lida do protocolo")

    ok(dm.soma(None, None) is None, "somar só ausentes devolve None, não zero")
    igual(dm.soma(None, Decimal("1.01"), Decimal("2.02")), Decimal("3.03"),
          "e somar ignorando ausentes preserva a precisão")

    try:
        dm.dinheiro(0.1)
        ok(False, "dinheiro() deveria recusar float")
    except dm.ValorInvalido:
        ok(True, "dinheiro() RECUSA float em vez de converter em silêncio")

# ══════════════════════════════════════════════════════════════════════════
secao("O índice guarda dinheiro como TEXTO, não como float")
with apoio.raiz_temporaria("ing3_") as raiz:
    preservar_e_indexar(raiz, EMPRESA_A, [bruto(xml_nfe(CHAVE_1, valor="0.10"), "1")])
    ind = idx.abrir(raiz, EMPRESA_A)
    with ind.conectar() as con:
        tipo = con.execute(
            "SELECT typeof(valor_total) t FROM documentos WHERE valor_total IS NOT NULL"
        ).fetchone()["t"]
    igual(tipo, "text", "a coluna guarda TEXT (REAL reintroduziria o float)")
    linha = ind.listar()[0]
    igual(linha["valor_total"], "0.10", "e o valor volta exatamente como entrou")
    igual(ind.total_por_competencia(date(2026, 8, 1)), Decimal("0.10"),
          "a totalização devolve Decimal")

# ══════════════════════════════════════════════════════════════════════════
secao("Falha DEPOIS da aquisição e ANTES da persistência — o NSU não anda")
with apoio.raiz_temporaria("ing3_") as raiz:
    class AcervoQueFalha(acv.AcervoArquivos):
        def preservar(self, doc):
            raise acv.ErroAcervo("disco cheio (simulado)")

    repo = RepositorioCheckpoint(raiz)
    ac = AcervoQueFalha(dados_dir=raiz, identidade=EMPRESA_A,
                        servico="nfe_distribuicao", ambiente="producao")
    fonte = FonteFalsa([Lote(documentos=(bruto(xml_nfe(CHAVE_1), "000000000000005"),),
                             ult_nsu="000000000000005", max_nsu="000000000000010")])
    res = DistribuicaoRunner(repo).executar(EMPRESA_A, fonte, ac, usar_trava=False)

    ok(not res.sucesso, "a execução reporta falha")
    igual(res.nsu_final, "000000000000000", "e o NSU final continua em zero")
    cp = repo.carregar(EMPRESA_A, "nfe_distribuicao", "producao")
    igual(cp.ult_nsu, "000000000000000", "o checkpoint gravado NÃO avançou")
    igual(len(acv.abrir(raiz, EMPRESA_A).listar()), 0, "e nada foi para o acervo")

secao("Falha na CONFIRMAÇÃO — mesma garantia")
with apoio.raiz_temporaria("ing3_") as raiz:
    class AcervoQueNaoConfirma(acv.AcervoArquivos):
        def confirmar(self, documentos):
            return False

    repo = RepositorioCheckpoint(raiz)
    ac = AcervoQueNaoConfirma(dados_dir=raiz, identidade=EMPRESA_A,
                              servico="nfe_distribuicao", ambiente="producao")
    fonte = FonteFalsa([Lote(documentos=(bruto(xml_nfe(CHAVE_1), "000000000000005"),),
                             ult_nsu="000000000000005", max_nsu="000000000000010")])
    res = DistribuicaoRunner(repo).executar(EMPRESA_A, fonte, ac, usar_trava=False)
    igual(repo.carregar(EMPRESA_A, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000000", "gravou mas não confirmou: o checkpoint não anda")
    igual(len(acv.abrir(raiz, EMPRESA_A).listar()), 1,
          "o documento está no disco e será reprocessado como duplicata")

# ══════════════════════════════════════════════════════════════════════════
secao("Falha DEPOIS da persistência e ANTES do parser — o NSU anda mesmo assim")
with apoio.raiz_temporaria("ing3_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    fonte = FonteFalsa([Lote(documentos=(bruto(xml_nfe(CHAVE_1), "000000000000005"),),
                             ult_nsu="000000000000005", max_nsu="000000000000005")])
    res = DistribuicaoRunner(repo).executar(EMPRESA_A, fonte, ac, usar_trava=False)
    ok(res.sucesso, "a aquisição terminou com sucesso")
    igual(repo.carregar(EMPRESA_A, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000005", "o checkpoint avançou porque o BYTE está salvo")

    # agora o parser falha — e isso não pode desfazer nada do que já foi feito
    original = prs.ParserNFe55.interpretar

    def quebrado(self, *a, **kw):
        raise prs.ErroParser("parser com defeito (simulado)")

    prs.ParserNFe55.interpretar = quebrado
    try:
        rel = pipe.indexar_pendentes(raiz, EMPRESA_A)
    finally:
        prs.ParserNFe55.interpretar = original

    igual(rel.falha_parser, 1, "a indexação registra a falha de parser")
    igual(repo.carregar(EMPRESA_A, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000005", "e o checkpoint NÃO recua por causa dela")
    igual(len(ac.listar()), 1, "o documento continua preservado")
    linha = idx.abrir(raiz, EMPRESA_A).obter(idf.id_de_nfe(CHAVE_1))
    igual(linha["estado"], idx.FALHA_PARSER, "o índice mostra o problema em vez de escondê-lo")

    # com o parser bom de volta, reprocessar do DISCO conserta
    rel2 = idx.reprocessar(raiz, EMPRESA_A, somente_desatualizados=False)
    igual(rel2.indexados, 1, "reprocessar do disco conserta sem baixar de novo")
    igual(fonte.consultas, 1, "e a fonte NÃO foi consultada de novo")

# ══════════════════════════════════════════════════════════════════════════
secao("Falha durante a normalização — um documento não derruba o lote")
with apoio.raiz_temporaria("ing3_") as raiz:
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    for i, c in enumerate([xml_nfe(CHAVE_1), xml_nfe(CHAVE_2)], start=1):
        ac.preservar(bruto(c, f"{i:015d}"))
    # uma NF-e com valor impossível: quebra na conversão para Decimal
    quebrada = xml_nfe(chave_ficticia(FORNECEDOR, 3), valor="R$ mil reais")
    ac.preservar(bruto(quebrada, "000000000000003"))

    rel = pipe.indexar_pendentes(raiz, EMPRESA_A)
    igual(rel.total, 3, "três documentos no lote")
    igual(rel.indexados, 2, "os dois íntegros são indexados")
    igual(rel.falha_parser, 1, "e só o defeituoso falha")
    ok(rel.erros, "o erro é reportado, não engolido")
    igual(len(ac.listar()), 3, "e os três continuam preservados no acervo")

# ══════════════════════════════════════════════════════════════════════════
secao("Reinício do processo — retoma de onde parou")
with apoio.raiz_temporaria("ing3_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    fonte = FonteFalsa([Lote(documentos=(bruto(xml_nfe(CHAVE_1), "000000000000005"),),
                             ult_nsu="000000000000005", max_nsu="000000000000010")])
    DistribuicaoRunner(repo).executar(EMPRESA_A, fonte, ac, usar_trava=False)
    pipe.indexar_pendentes(raiz, EMPRESA_A)

    # "reinício": objetos novos, lendo o mesmo disco
    repo2 = RepositorioCheckpoint(raiz)
    ac2 = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    igual(repo2.carregar(EMPRESA_A, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000005", "o checkpoint sobrevive ao reinício")
    igual(len(ac2.listar()), 1, "o acervo sobrevive")
    igual(len(idx.abrir(raiz, EMPRESA_A).listar()), 1, "o índice sobrevive")

    fonte2 = FonteFalsa([Lote(documentos=(bruto(xml_nfe(CHAVE_2), "000000000000010"),),
                              ult_nsu="000000000000010", max_nsu="000000000000010")])
    res = DistribuicaoRunner(repo2).executar(EMPRESA_A, fonte2, ac2, usar_trava=False)
    igual(res.nsu_inicial, "000000000000005", "a nova execução começa do NSU salvo")
    pipe.indexar_pendentes(raiz, EMPRESA_A)
    igual(len(ac2.listar()), 2, "e acumula o documento novo")

# ══════════════════════════════════════════════════════════════════════════
secao("Reprocessamento após versão nova do parser")
with apoio.raiz_temporaria("ing3_") as raiz:
    preservar_e_indexar(raiz, EMPRESA_A, [bruto(xml_nfe(CHAVE_1), "1"),
                                          bruto(xml_nfe(CHAVE_2), "2")])
    ind = idx.abrir(raiz, EMPRESA_A)
    igual(ind.desatualizados(), [], "com o parser atual, nada está desatualizado")

    versao_antes = prs.ParserNFe55.VERSAO
    prs.ParserNFe55.VERSAO = versao_antes + 1
    try:
        pendentes = ind.desatualizados()
        igual(len(pendentes), 2, "subir a versão marca os 2 documentos para releitura")
        rel = idx.reprocessar(raiz, EMPRESA_A)
        igual(rel.indexados, 2, "o reprocessamento relê os dois")
        igual(idx.abrir(raiz, EMPRESA_A).desatualizados(), [],
              "e depois nada continua desatualizado")
        linha = idx.abrir(raiz, EMPRESA_A).listar(especie=dm.NFE55)[0]
        igual(linha["versao_parser"], versao_antes + 1, "com a versão nova registrada")
    finally:
        prs.ParserNFe55.VERSAO = versao_antes

    contagem = aud.abrir(raiz, EMPRESA_A).contar()
    ok(contagem.get(aud.REPROCESSAMENTO), "a trilha registra o reprocessamento")

# ══════════════════════════════════════════════════════════════════════════
secao("Índice é descartável — reconstrói inteiro a partir do acervo")
with apoio.raiz_temporaria("ing3_") as raiz:
    preservar_e_indexar(raiz, EMPRESA_A, [
        bruto(xml_nfe(CHAVE_1), "1"),
        bruto(xml_nfe(CHAVE_2), "2"),
        bruto(xml_evento(CHAVE_1), "3"),
        bruto(XML_DESCONHECIDO, "4"),
    ])
    antes = idx.abrir(raiz, EMPRESA_A).listar()
    igual(len(antes), 4, "quatro linhas no índice")

    apagados = acv.limpar_derivados(raiz, EMPRESA_A)
    ok(apagados >= 1, "o índice foi apagado do disco")
    ok(not (raiz / EMPRESA_A / "indice" / "documentos.db").exists(), "o .db sumiu")
    igual(len(acv.abrir(raiz, EMPRESA_A).listar()), 4,
          "mas o ACERVO continua com os quatro documentos")

    rel = idx.reconstruir(raiz, EMPRESA_A)
    igual(rel.total, 4, "a reconstrução varre os quatro")
    igual(rel.indexados, 3, "três interpretáveis")
    igual(rel.schema_desconhecido, 1, "e um de schema desconhecido")

    depois = idx.abrir(raiz, EMPRESA_A).listar()
    igual([l["id_documento"] for l in depois], [l["id_documento"] for l in antes],
          "o índice reconstruído tem exatamente os mesmos documentos")
    igual([l["valor_total"] for l in depois], [l["valor_total"] for l in antes],
          "com os mesmos valores")

secao("Integridade — o acervo detecta corrupção silenciosa")
with apoio.raiz_temporaria("ing3_") as raiz:
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    p = ac.preservar(bruto(xml_nfe(CHAVE_1), "1"))
    igual(ac.verificar(), [], "acervo íntegro não reporta problema")
    (ac.pasta_do(idf.NFE55, p.id_documento) / acv.ORIGINAL).write_bytes(b"<corrompido/>")
    problemas = ac.verificar()
    igual(len(problemas), 1, "um byte trocado é detectado")
    ok("hash" in problemas[0]["problema"], "e o motivo é o hash que não confere")

# ══════════════════════════════════════════════════════════════════════════
secao("Isolamento entre duas empresas")
with apoio.raiz_temporaria("ing3_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    chave_a, chave_b = CHAVE_1, CHAVE_2
    itens = []
    for empresa, chave, nsu in ((EMPRESA_A, chave_a, "000000000000007"),
                                (EMPRESA_B, chave_b, "000000000000003")):
        ac = acv.abrir(raiz, empresa, servico="nfe_distribuicao", ambiente="producao")
        fonte = FonteFalsa([Lote(
            documentos=(bruto(xml_nfe(chave, destinatario=empresa), nsu),),
            ult_nsu=nsu, max_nsu=nsu)])
        itens.append((empresa, fonte, ac))

    from ingestao.distribuicao import executar_para_empresas
    resultados = executar_para_empresas(DistribuicaoRunner(repo), itens)
    ok(all(r.sucesso for r in resultados), "as duas varreduras terminam bem")

    for empresa in (EMPRESA_A, EMPRESA_B):
        pipe.indexar_pendentes(raiz, empresa)

    docs_a = idx.abrir(raiz, EMPRESA_A).listar()
    docs_b = idx.abrir(raiz, EMPRESA_B).listar()
    igual(len(docs_a), 1, "a empresa A tem um documento")
    igual(len(docs_b), 1, "a empresa B tem um documento")
    igual(docs_a[0]["chave"], chave_a, "e é o dela")
    igual(docs_b[0]["chave"], chave_b, "e é o dela")

    igual(repo.carregar(EMPRESA_A, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000007", "os checkpoints avançaram independentemente (A)")
    igual(repo.carregar(EMPRESA_B, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000003", "e (B)")

    # nenhum arquivo de uma dentro da pasta da outra
    for empresa, outra_chave in ((EMPRESA_A, chave_b), (EMPRESA_B, chave_a)):
        vazou = [p for p in (raiz / empresa).rglob("*.xml")
                 if outra_chave.encode() in p.read_bytes()]
        igual(vazou, [], f"nenhum XML da outra empresa dentro de {empresa[:4]}…")

    trilha_a = aud.abrir(raiz, EMPRESA_A).ler()
    mascaras = {r.get("empresa") for r in trilha_a}
    igual(len(mascaras), 1, "a trilha de A só menciona uma empresa")
    ok(EMPRESA_A not in json.dumps(trilha_a), "e a identidade vai MASCARADA na trilha")

# ══════════════════════════════════════════════════════════════════════════
secao("A auditoria responde às perguntas da fiscalização")
with apoio.raiz_temporaria("ing3_") as raiz:
    preservar_e_indexar(raiz, EMPRESA_A, [bruto(xml_nfe(CHAVE_1), "000000000000042")])
    trilha = aud.abrir(raiz, EMPRESA_A)
    hist = trilha.historico(idf.id_de_nfe(CHAVE_1))
    atos = [r["ato"] for r in hist]
    ok(aud.CAPTURA in atos, "de onde veio: há registro de CAPTURA")
    ok(aud.PRESERVACAO in atos, "quando foi preservado")
    ok(aud.PARSE in atos, "qual parser interpretou")

    captura = [r for r in hist if r["ato"] == aud.CAPTURA][0]
    igual(captura["nsu"], "000000000000042", "com o NSU")
    igual(captura["servico"], "nfe_distribuicao", "o serviço")
    igual(captura["ambiente"], "producao", "o ambiente")
    ok(captura["hash"].startswith("sha256:"), "e o hash do conteúdo")

    parse = [r for r in hist if r["ato"] == aud.PARSE][0]
    igual(parse["parser"], "nfe55", "o nome do parser")
    igual(parse["versao_parser"], prs.VERSAO_NFE55, "e a versão dele")
    ok(parse["sucesso"], "com o resultado")

secao("A trilha não vaza documento nem segredo")
with apoio.raiz_temporaria("ing3_") as raiz:
    trilha = aud.abrir(raiz, EMPRESA_A)
    trilha.registrar(aud.ERRO, id_documento="x",
                     detalhe={"senha": "NAO-DEVE-APARECER-1",
                              "conteudo": b"<nfeProc>bytes</nfeProc>",
                              "mensagem": "falhou com token=NAO-DEVE-APARECER-2"})
    texto = trilha.caminho().read_text("utf-8")
    ok("NAO-DEVE-APARECER-1" not in texto, "campo 'senha' não vai para a trilha")
    ok("NAO-DEVE-APARECER-2" not in texto, "token dentro de mensagem é higienizado")
    ok("<nfeProc>" not in texto, "e bytes de documento nunca são gravados")

    preservar_e_indexar(raiz, EMPRESA_A, [bruto(xml_nfe(CHAVE_1), "1")])
    tudo = trilha.caminho().read_text("utf-8")
    ok("PRODUTO 1" not in tudo, "o conteúdo do XML não aparece na trilha")
    ok(EMPRESA_A not in tudo, "nem a identidade da empresa sem máscara")

secao("Linha corrompida na trilha não impede ler as outras")
with apoio.raiz_temporaria("ing3_") as raiz:
    trilha = aud.abrir(raiz, EMPRESA_A)
    trilha.registrar(aud.CAPTURA, id_documento="a")
    with open(trilha.caminho(), "a", encoding="utf-8") as f:
        f.write('{"ato": "CAPTURA", "trunca')          # queda de energia
    trilha.registrar(aud.CAPTURA, id_documento="b")
    lidos = trilha.ler()
    ids = [r.get("id_documento") for r in lidos]
    ok("a" in ids and "b" in ids, "as linhas íntegras continuam legíveis")
    ok(any(r.get("detalhe", {}).get("linha_ilegivel") for r in lidos),
       "e a linha quebrada é reportada, não escondida")

# ══════════════════════════════════════════════════════════════════════════
secao("Fluxo completo pelo pipeline")
with apoio.raiz_temporaria("ing3_") as raiz:
    fonte = FonteFalsa([
        Lote(documentos=(bruto(xml_resumo(CHAVE_1), "000000000000001",
                               schema="resNFe_v1.01.xsd"),
                         bruto(xml_nfe(CHAVE_2), "000000000000002")),
             ult_nsu="000000000000002", max_nsu="000000000000004"),
        Lote(documentos=(bruto(xml_nfe(CHAVE_1), "000000000000003"),
                         bruto(xml_evento(CHAVE_2), "000000000000004",
                               schema="procEventoNFe_v1.00.xsd")),
             ult_nsu="000000000000004", max_nsu="000000000000004"),
    ])
    res = pipe.ingerir(raiz, EMPRESA_A, fonte, ambiente="producao", usar_trava=False)
    ok(res.sucesso, "a ingestão termina com sucesso")
    igual(res.execucao.documentos, 4, "quatro documentos trafegaram")
    igual(res.indexacao.indexados, 3, "e três documentos distintos foram indexados")

    ind = idx.abrir(raiz, EMPRESA_A)
    igual(len(ind.listar(especie=dm.NFE55)), 2, "duas NF-e")
    igual(len(ind.listar(especie=dm.EVENTO_NFE)), 1, "um evento")
    igual(ind.total_por_competencia(date(2026, 8, 1)), Decimal("2469.12"),
          "o total da competência soma em Decimal")
    ok(res.linha(), "e a execução tem uma linha de relatório legível")

# ══════════════════════════════════════════════════════════════════════════
secao("Backup: acervo e auditoria viajam; o índice fica")
with apoio.raiz_temporaria("ing3_") as origem:
    import fiscale_backup as bk

    # pelo pipeline completo, para que exista TAMBÉM um checkpoint a viajar
    fonte = FonteFalsa([Lote(
        documentos=(bruto(xml_nfe(CHAVE_1), "000000000000001"),
                    bruto(xml_evento(CHAVE_1), "000000000000002",
                          schema="procEventoNFe_v1.00.xsd"),
                    bruto(XML_DESCONHECIDO, "000000000000003")),
        ult_nsu="000000000000003", max_nsu="000000000000003")])
    pipe.ingerir(origem, EMPRESA_A, fonte, ambiente="producao", usar_trava=False)
    trilha_antes = aud.abrir(origem, EMPRESA_A).ler()
    indice_antes = idx.abrir(origem, EMPRESA_A).listar()
    ok((origem / EMPRESA_A / "indice" / "documentos.db").exists(), "o índice existe")

    destino_fbk = apoio.pasta_temp("ing3_fbk_") / "backup.fbk"
    bk.exportar(destino_fbk, frase="frase-ficticia-do-teste-ing3", raiz=origem)
    dentro = bk.inspecionar(destino_fbk)
    nomes = [a for a in dentro.get("arquivos", [])] if isinstance(
        dentro.get("arquivos"), list) else []

    import zipfile
    # O v2 não é um ZIP por fora: olha-se dentro pelo caminho que decifra.
    with bk.pacote_aberto(destino_fbk, "frase-ficticia-do-teste-ing3") as _d,             zipfile.ZipFile(_d) as z:
        no_pacote = z.namelist()
    ok(any("/acervo/" in n for n in no_pacote), "o ACERVO entra no pacote")
    ok(any("/auditoria/" in n for n in no_pacote), "a AUDITORIA entra no pacote")
    ok(not any("/indice/" in n for n in no_pacote),
       "o ÍNDICE fica de fora — é cache, e restaurá-lo velho seria pior")
    ok(any("/ingestao/" in n for n in no_pacote), "o checkpoint entra no pacote")

    with apoio.raiz_temporaria("ing3_novo_") as nova:
        r = bk.restaurar(destino_fbk, frase="frase-ficticia-do-teste-ing3",
                         raiz=nova, modo="substituir")
        ok(r.get("ok"), "a restauração conclui numa instalação nova")

        trilha_depois = aud.abrir(nova, EMPRESA_A).ler()
        igual(len(trilha_depois), len(trilha_antes),
              "a trilha de auditoria chegou inteira")
        igual([x["ato"] for x in trilha_depois], [x["ato"] for x in trilha_antes],
              "com os mesmos atos, na mesma ordem")

        ok(not (nova / EMPRESA_A / "indice" / "documentos.db").exists(),
           "e o índice NÃO veio")
        igual(len(acv.abrir(nova, EMPRESA_A).listar()), 3,
              "mas os três documentos do acervo vieram")

        rel = idx.reconstruir(nova, EMPRESA_A)
        igual(rel.total, 3, "o índice se reconstrói do acervo restaurado")
        indice_depois = idx.abrir(nova, EMPRESA_A).listar()
        igual([l["id_documento"] for l in indice_depois],
              [l["id_documento"] for l in indice_antes],
              "com exatamente os mesmos documentos de antes do backup")
        igual([l["valor_total"] for l in indice_depois],
              [l["valor_total"] for l in indice_antes], "e os mesmos valores")
        igual(acv.abrir(nova, EMPRESA_A).verificar(), [],
              "e todos os hashes conferem depois da viagem")

        cp = RepositorioCheckpoint(nova).carregar(EMPRESA_A, "nfe_distribuicao",
                                                  "producao")
        igual(cp.ult_nsu, "000000000000003",
              "o checkpoint chegou intacto — reiniciar do zero rebaixaria tudo")

# ══════════════════════════════════════════════════════════════════════════
secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS_DE_REDE, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede não estava ativa — o teste acima não valia nada")
except AssertionError as exc:
    ok("conexão de rede" in str(exc), "e a trava estava mesmo ativa (conferido)")
finally:
    _TENTATIVAS_DE_REDE.clear()
    _socket.socket.connect = _connect_original

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("ING 3A: todos os testes passaram.")
