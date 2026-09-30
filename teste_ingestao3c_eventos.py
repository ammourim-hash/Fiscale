#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes do parser de eventos — `procEventoNFe` e `resEvento`.

    python teste_ingestao3c_eventos.py

O QUE ORIGINOU ESTA SUÍTE
    Na primeira consulta real da ING 3C (15/08/2026), 49 dos 50 documentos
    vieram como `resEvento` — o RESUMO de evento — e o parser recusou todos com
    "sem bloco infEvento". Nenhuma fixture cobria esse formato, então a suíte
    passava enquanto o mundo real não passava.

    A diferença é estrutural: no `resEvento` os campos ficam **direto sob a
    raiz**, sem `infEvento` e sem `detEvento`.

O QUE ESTA SUÍTE GARANTE
    Que os DOIS formatos são lidos, que nenhum é tratado como o outro, e que o
    que não existe num deles fica `None` em vez de virar string vazia.

FIXTURES
    Fictícias, de `teste_fixturas_nfe`. Nenhum documento real foi copiado — os
    49 serviram só para entender a forma, localmente.

REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import acervo as acv                 # noqa: E402
from ingestao import auditoria as aud              # noqa: E402
from ingestao import documento as dm               # noqa: E402
from ingestao import identificacao as idf          # noqa: E402
from ingestao import indice as idx                 # noqa: E402
from ingestao import parsers as prs                # noqa: E402
from ingestao import pipeline as pipe              # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                           # noqa: E402
_TENTATIVAS: list[str] = []
_connect_original = _socket.socket.connect


def _proibido(self, endereco, *a, **kw):
    _TENTATIVAS.append(str(endereco))
    raise AssertionError(f"teste tentou abrir conexão de rede para {endereco}")


_socket.socket.connect = _proibido


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


P = prs.ParserEventoNFe()


def ler(conteudo: bytes, empresa=T.EMPRESA_A):
    return P.interpretar(conteudo, idf.identificar(conteudo),
                         identidade_empresa=empresa)


# ══════════════════════════════════════════════════════════════════════════
secao("procEventoNFe — o formato completo continua sendo lido")
doc = ler(T.xml_evento(T.CHAVE_1, tp="110111", seq="1", just="Erro de digitacao"))
igual(doc.especie, dm.EVENTO_NFE, "é evento")
igual(doc.chave, T.CHAVE_1, "com a chave da nota")
e = doc.extensao
igual(e.forma, "procEventoNFe", "declara a forma completa")
igual(e.tp_evento, "110111", "tpEvento")
igual(e.sequencia, "1", "nSeqEvento")
igual(e.orgao, "26", "cOrgao")
igual(e.protocolo, "126000000000009", "protocolo do retEvento")
igual(e.justificativa, "Erro de digitacao", "justificativa do detEvento")
ok(e.e_cancelamento, "e é reconhecido como cancelamento")
igual(doc.competencia, date(2026, 8, 1), "competência derivada do dhEvento")
igual(doc.versao_parser, prs.VERSAO_EVENTO, "com a versão do parser")

secao("resEvento — o formato resumido passou a ser lido")
res = T.xml_res_evento(T.CHAVE_1, tp="110111", seq="1",
                       descricao="Cancelamento registrado")
doc = ler(res)
igual(doc.especie, dm.EVENTO_NFE, "é evento")
igual(doc.chave, T.CHAVE_1, "com a chave, lida do <chNFe> sob a raiz")
e = doc.extensao
igual(e.forma, "resEvento", "declara a forma resumida")
igual(e.tp_evento, "110111", "tpEvento")
igual(e.sequencia, "1", "nSeqEvento")
igual(e.orgao, "26", "cOrgao")
igual(e.protocolo, "126000000000009", "nProt, que aqui fica sob a raiz")
igual(e.descricao, "Cancelamento registrado",
      "descrição vem do xEvento do PRÓPRIO documento")
ok(e.dh_recebimento is not None, "dhRecbto é lido")
igual(doc.emitente.identificador, T.FORNECEDOR, "o autor do evento")
igual(doc.versao_schema, "1.01", "a versão declarada na raiz")
igual(doc.competencia, date(2026, 8, 1), "competência derivada do dhEvento")

secao("O que NÃO existe no resumo fica None, não string vazia")
ok(e.justificativa is None, "justificativa é None — não existe no resEvento")
ok(e.correcao is None, "correção é None — idem")
ok(any("detEvento" in a for a in doc.avisos), "e o documento avisa disso")

completo = ler(T.xml_evento(T.CHAVE_1))
ok(completo.extensao.justificativa is not None,
   "enquanto no formato completo a justificativa existe de fato")

secao("Um formato NÃO é tratado como o outro")
ok(ler(res).extensao.forma != ler(T.xml_evento(T.CHAVE_1)).extensao.forma,
   "as duas formas são distinguíveis no resultado")
igual(idf.identificar(res).id_documento,
      idf.identificar(T.xml_evento(T.CHAVE_1)).id_documento,
      "mas o MESMO evento tem o mesmo id nas duas formas (chave+tp+seq)")

secao("A descrição do serviço prevalece sobre a tabela local")
d = ler(T.xml_res_evento(T.CHAVE_1, tp="110111",
                         descricao="Texto que o servico mandou"))
igual(d.extensao.descricao, "Texto que o servico mandou",
      "usa o xEvento do documento, não a tabela interna")

secao("Tipo de evento desconhecido é lido assim mesmo")
d = ler(T.xml_res_evento(T.CHAVE_1, tp="610614", descricao="Evento nao catalogado"))
igual(d.extensao.tp_evento, "610614", "o tpEvento desconhecido é preservado")
igual(d.extensao.descricao, "Evento nao catalogado",
      "e a descrição vem do documento — nada é inventado")
ok(not d.extensao.e_cancelamento, "e ele não é confundido com cancelamento")

d2 = ler(T.xml_res_evento(T.CHAVE_1, tp="999999", descricao=""))
igual(d2.extensao.descricao, "",
      "sem xEvento e sem tabela, a descrição fica vazia — não inventada")

secao("Campos opcionais ausentes")
for campo in ("nProt", "dhRecbto", "cOrgao", "CNPJ"):
    d = ler(T.xml_res_evento(T.CHAVE_1, omitir=(campo,)))
    ok(d.chave == T.CHAVE_1, f"sem '{campo}' o evento ainda é lido")
d = ler(T.xml_res_evento(T.CHAVE_1, omitir=("nProt",)))
igual(d.extensao.protocolo, "", "protocolo ausente fica vazio")
d = ler(T.xml_res_evento(T.CHAVE_1, omitir=("dhRecbto",)))
ok(d.extensao.dh_recebimento is None, "dhRecbto ausente fica None")

secao("Evento sem chNFe — identidade fraca, e o parser não estoura")
sem_chave = T.xml_res_evento(T.CHAVE_1, omitir=("chNFe",))
i = idf.identificar(sem_chave)
ok(not i.identidade_forte, "a identificação marca identidade fraca")
igual(i.especie, idf.EVENTO_NFE, "mas a espécie continua sendo evento")
d = ler(sem_chave)
igual(d.chave, "", "o documento é lido com chave vazia")
igual(d.extensao.tp_evento, "110111", "e o resto dos campos vem normalmente")

secao("XML de evento malformado")
try:
    ler(b"<resEvento><chNFe>123")
    ok(False, "XML malformado deveria levantar ErroParser")
except prs.ErroParser as exc:
    ok("malformado" in str(exc), "XML malformado levanta ErroParser")

try:
    P.interpretar(b'<evento xmlns="http://www.portalfiscal.inf.br/nfe"/>',
                  idf.identificar(b'<evento xmlns="http://www.portalfiscal.inf.br/nfe"/>'))
    ok(False, "evento sem infEvento deveria levantar")
except prs.ErroParser as exc:
    ok("infEvento" in str(exc),
       "no formato COMPLETO, a ausência de infEvento continua sendo erro")

secao("Namespace — presente, ausente e inesperado")
sem_ns = T.xml_res_evento(T.CHAVE_1).replace(
    b' xmlns="http://www.portalfiscal.inf.br/nfe"', b"")
i = idf.identificar(sem_ns)
igual(i.especie, idf.EVENTO_NFE, "sem namespace ainda é reconhecido como evento")
igual(ler(sem_ns).extensao.tp_evento, "110111", "e é lido normalmente")

outro_ns = T.xml_res_evento(T.CHAVE_1).replace(
    b"http://www.portalfiscal.inf.br/nfe", b"http://exemplo.invalido/outro")
igual(idf.identificar(outro_ns).especie, idf.EVENTO_NFE,
      "namespace inesperado não impede o reconhecimento pela tag raiz")
igual(ler(outro_ns).extensao.tp_evento, "110111", "nem a leitura")

# ══════════════════════════════════════════════════════════════════════════
secao("Cadeia completa: resEvento no acervo, no índice e na trilha")
with apoio.raiz_temporaria("i3c_ev_") as raiz:
    ac = acv.abrir(raiz, T.EMPRESA_A, servico="nfe_distribuicao",
                   ambiente="producao")
    for n, tp in enumerate(("110111", "610614", "610600"), start=1):
        ac.preservar(T.bruto(T.xml_res_evento(T.CHAVE_1, tp=tp, seq=str(n)),
                             f"{n:015d}", schema="resEvento_v1.01.xsd"))
    ac.preservar(T.bruto(T.xml_resumo(T.CHAVE_2), "000000000000004",
                         schema="resNFe_v1.01.xsd"))
    rel = pipe.indexar_pendentes(raiz, T.EMPRESA_A)
    igual(rel.indexados, 4, "os quatro documentos são indexados")
    igual(rel.falha_parser, 0, "nenhuma falha de parser")

    ind = idx.abrir(raiz, T.EMPRESA_A)
    igual(len(ind.listar(especie=dm.EVENTO_NFE)), 3, "três eventos no índice")
    evs = ind.eventos_da_chave(T.CHAVE_1)
    igual(len(evs), 3, "os três apontam para a chave da nota")
    igual(sorted(e["tp_evento"] for e in evs), ["110111", "610600", "610614"],
          "com os tipos preservados, inclusive os não catalogados")

secao("O evento NÃO consolida a situação da NF-e (isso é da camada futura)")
with apoio.raiz_temporaria("i3c_ev_") as raiz:
    ac = acv.abrir(raiz, T.EMPRESA_A, servico="nfe_distribuicao",
                   ambiente="producao")
    ac.preservar(T.bruto(T.xml_nfe(T.CHAVE_1), "000000000000001"))
    ac.preservar(T.bruto(T.xml_res_evento(T.CHAVE_1, tp="110111"),
                         "000000000000002", schema="resEvento_v1.01.xsd"))
    pipe.indexar_pendentes(raiz, T.EMPRESA_A)
    ind = idx.abrir(raiz, T.EMPRESA_A)
    nota = ind.listar(especie=dm.NFE55)[0]
    igual(nota["situacao"], dm.AUTORIZADO,
          "a nota continua AUTORIZADO mesmo com cancelamento registrado")
    ok(ind.listar(especie=dm.EVENTO_NFE)[0]["id_documento"] != nota["id_documento"],
       "e o evento tem id próprio, sem sobrescrever a nota")

secao("Reprocessamento recupera FALHA_PARSER sem tocar no acervo")
with apoio.raiz_temporaria("i3c_ev_") as raiz:
    ac = acv.abrir(raiz, T.EMPRESA_A, servico="nfe_distribuicao",
                   ambiente="producao")
    p = ac.preservar(T.bruto(T.xml_res_evento(T.CHAVE_1), "000000000000001",
                             schema="resEvento_v1.01.xsd"))
    bytes_antes = ac.ler_original(idf.EVENTO_NFE, p.id_documento)

    # simula a versão ANTIGA do parser, que não sabia ler resEvento
    original = prs.ParserEventoNFe._resumo

    def recusa(self, *a, **kw):
        raise prs.ErroParser("sem bloco infEvento")

    prs.ParserEventoNFe._resumo = recusa
    try:
        rel = pipe.indexar_pendentes(raiz, T.EMPRESA_A)
    finally:
        prs.ParserEventoNFe._resumo = original
    igual(rel.falha_parser, 1, "com o parser antigo, falha")
    igual(idx.abrir(raiz, T.EMPRESA_A).obter(p.id_documento)["estado"],
          idx.FALHA_PARSER, "e o índice registra FALHA_PARSER")

    rel = idx.reprocessar(raiz, T.EMPRESA_A, somente_desatualizados=False)
    igual(rel.indexados, 1, "com o parser novo, reprocessar do disco recupera")
    igual(idx.abrir(raiz, T.EMPRESA_A).obter(p.id_documento)["estado"],
          idx.INDEXADO, "e o estado passa a INDEXADO")
    igual(ac.ler_original(idf.EVENTO_NFE, p.id_documento), bytes_antes,
          "o original.xml continua byte a byte idêntico")
    igual(ac.verificar(), [], "e o hash confere")

    trilha = aud.abrir(raiz, T.EMPRESA_A)
    igual(len(trilha.consultas()), 0, "nenhum ato CONSULTA foi criado")
    igual(trilha.contar().get(aud.CAPTURA), 1,
          "e nenhuma CAPTURA nova — só a original")
    ok(trilha.contar().get(aud.REPROCESSAMENTO), "o reprocessamento é registrado")

# ══════════════════════════════════════════════════════════════════════════
secao("A fixture é fictícia")
fonte = (RAIZ / "teste_fixturas_nfe.py").read_text("utf-8")
ok("64567004000139" not in fonte, "nenhum CNPJ real do escritório na fixture")
ok("TRX" not in fonte and "TRANSPORTES LOGISTICAS" not in fonte,
   "nenhuma razão social real")
res_txt = T.xml_res_evento(T.CHAVE_1).decode("utf-8")
ok(T.FORNECEDOR in res_txt and T.cnpj_ficticio("333333330001") == T.FORNECEDOR,
   "o CNPJ da fixture é o fictício com DV calculado")

secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede não estava ativa")
except AssertionError as exc:
    ok("conexão de rede" in str(exc), "e a trava estava mesmo ativa (conferido)")
finally:
    _TENTATIVAS.clear()
    _socket.socket.connect = _connect_original

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("ING 3C (eventos): todos os testes passaram.")
