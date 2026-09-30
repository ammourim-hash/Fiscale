#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da ING 3B — conector NF-e Distribuição de DF-e, sem rede.

    python teste_ingestao3b.py

O QUE ESTA SUÍTE PROVA
    Que a cadeia `SEFAZ → Fonte → DocumentoBruto → ING 3A → acervo → índice`
    funciona ponta a ponta com respostas SOAP realistas, e que **cada modo de
    falha tem tratamento próprio**: timeout de conexão não é timeout de leitura,
    TLS recusado não é serviço fora do ar, rejeição de negócio não é resposta
    ilegível, e `docZip` corrompido não some.

REDE BLOQUEADA
    `socket.socket.connect` levanta durante toda a suíte, e isso é conferido no
    fim. Nenhum teste fala com a SEFAZ.

FIXTURES SANITIZADAS
    CNPJ e chaves fictícios com DV calculado. Nenhum dado de cliente, nenhum
    certificado, nenhuma senha — nem fictícia dentro de campo de senha.
"""
from __future__ import annotations

import base64
import gzip
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402  (só dado, não suíte)
from ingestao import acervo as acv                 # noqa: E402
from ingestao import auditoria as aud              # noqa: E402
from ingestao import documento as dm               # noqa: E402
from ingestao import identificacao as idf          # noqa: E402
from ingestao import indice as idx                 # noqa: E402
from ingestao import pipeline as pipe              # noqa: E402
from ingestao.ambiente import HOMOLOGACAO, PRODUCAO  # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402
from ingestao.conectores import nfe_dfe as N       # noqa: E402
from ingestao.distribuicao import (                # noqa: E402
    DistribuicaoRunner, ErroDefinitivo, ErroTransitorio, RespostaInvalida,
)

_ok = _falhas = 0
_erros: list[str] = []

# ── trava de rede ───────────────────────────────────────────────────────────
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


def levanta(tipo, funcao, desc, trecho=""):
    try:
        funcao()
    except tipo as exc:
        if trecho and trecho.lower() not in str(exc).lower():
            ok(False, desc + f"  (mensagem sem {trecho!r}: {exc})")
        else:
            ok(True, desc)
        return
    except Exception as exc:
        ok(False, desc + f"  (levantou {type(exc).__name__} em vez de {tipo.__name__})")
        return
    ok(False, desc + "  (não levantou nada)")


# ══════════════════════════════════════════════════════════════════════════
# Fixtures de resposta SOAP
# ══════════════════════════════════════════════════════════════════════════
def zipar(xml: bytes) -> str:
    return base64.b64encode(gzip.compress(xml)).decode("ascii")


def doczip(nsu: str, schema: str, xml: bytes = None, conteudo_bruto: str = None) -> str:
    dados = conteudo_bruto if conteudo_bruto is not None else zipar(xml)
    return f'<docZip NSU="{nsu}" schema="{schema}">{dados}</docZip>'


def resposta(cstat="138", motivo="Documento localizado", ult="000000000000002",
             maximo="000000000000010", docs=(), tp_amb="1") -> bytes:
    lote = f"<loteDistDFeInt>{''.join(docs)}</loteDistDFeInt>" if docs else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<nfeDistDFeInteresseResponse xmlns="{N.NS_WSDL}"><nfeDistDFeInteresseResult>
<retDistDFeInt xmlns="{N.NS_NFE}" versao="1.01">
  <tpAmb>{tp_amb}</tpAmb><verAplic>AN_1.0.0</verAplic>
  <cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo>
  <dhResp>2026-08-13T14:22:31-03:00</dhResp>
  <ultNSU>{ult}</ultNSU><maxNSU>{maximo}</maxNSU>
  {lote}
</retDistDFeInt>
</nfeDistDFeInteresseResult></nfeDistDFeInteresseResponse>
</soap:Body></soap:Envelope>""".encode("utf-8")


SOAP_FAULT = b"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<soap:Fault><soap:Code><soap:Value>soap:Receiver</soap:Value></soap:Code>
<soap:Reason><soap:Text>System.Web.Services.Protocols.SoapException</soap:Text></soap:Reason>
</soap:Fault></soap:Body></soap:Envelope>"""

SOAP_TRUNCADO = b"""<?xml version="1.0"?><soap:Envelope><soap:Body><retDistDFeInt>"""

HTML_DE_PORTAL = b"<html><body><h1>503 Service Unavailable</h1></body></html>"


class TransporteFalso:
    """Dublê do transporte. Devolve respostas de uma fila; nunca abre socket."""

    def __init__(self, respostas):
        self._fila = list(respostas)
        self.enviados: list[bytes] = []
        self.urls: list[str] = []

    def enviar(self, url: str, corpo: bytes) -> bytes:
        self.enviados.append(corpo)
        self.urls.append(url)
        if not self._fila:
            return resposta(cstat="137", motivo="Nenhum documento localizado",
                            ult="000000000000010", maximo="000000000000010")
        item = self._fila.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RespostaHTTP:
    """Dublê de `requests.Response`, só o que `_conferir` usa."""

    def __init__(self, status_code=200, content=b"<xml/>", content_type="text/xml"):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}


class SessaoFalsa:
    def __init__(self, resultado):
        self._resultado = resultado
        self.chamadas = 0

    def post(self, url, data=None, headers=None, timeout=None):
        self.chamadas += 1
        self.ultimo_timeout = timeout
        if isinstance(self._resultado, Exception):
            raise self._resultado
        return self._resultado


def fonte(transporte, ambiente=PRODUCAO, cuf="26"):
    # `cuf` explícito: nos testes o autor não vem de arquivo. Ver
    # `autor_consulta.py` — cUFAutor é do ESCRITÓRIO, nunca da empresa.
    return N.FonteNFeDistribuicaoDFe(identidade=T.EMPRESA_A, transporte=transporte,
                                     ambiente=ambiente, cuf=cuf)


# ══════════════════════════════════════════════════════════════════════════
secao("Envelope — montagem do pedido")
env = N.montar_envelope_dist_nsu(T.EMPRESA_A, "5", PRODUCAO, cuf="26")
texto = env.decode("utf-8")
ok(f'versao="{N.VERSAO_DIST}"' in texto, "declara a versão do distDFeInt")
ok("<tpAmb>1</tpAmb>" in texto, "produção usa tpAmb 1")
ok("<cUFAutor>26</cUFAutor>" in texto, "cUFAutor vem do AUTOR (PE = 26)")
ok(f"<CNPJ>{T.EMPRESA_A}</CNPJ>" in texto, "CNPJ do interessado")
ok("<ultNSU>000000000000005</ultNSU>" in texto, "NSU normalizado para 15 dígitos")
ok(N.NS_WSDL in texto and N.NS_NFE in texto, "os dois namespaces oficiais")

env_hom = N.montar_envelope_dist_nsu(T.EMPRESA_A, "0", HOMOLOGACAO, cuf="26")
ok("<tpAmb>2</tpAmb>" in env_hom.decode(), "homologação usa tpAmb 2")

env_nsu = N.montar_envelope_cons_nsu(T.EMPRESA_A, "42", PRODUCAO, cuf="26")
ok("<consNSU><NSU>000000000000042</NSU></consNSU>" in env_nsu.decode(),
   "consNSU busca um NSU específico")

env_ch = N.montar_envelope_cons_chave(T.EMPRESA_A, T.CHAVE_1, PRODUCAO, cuf="26")
ok(f"<consChNFe><chNFe>{T.CHAVE_1}</chNFe></consChNFe>" in env_ch.decode(),
   "consChNFe busca por chave")
levanta(ValueError, lambda: N.montar_envelope_cons_chave(
    T.EMPRESA_A, T.CHAVE_1[:-1] + "0", PRODUCAO, cuf="26"),
    "chave com DV errado é recusada ANTES de gastar consulta da cota")
levanta(ValueError, lambda: N.montar_envelope_dist_nsu("123", "0", PRODUCAO, cuf="26"),
        "identidade inválida é recusada na montagem")
levanta(ValueError, lambda: N.montar_envelope_dist_nsu(T.EMPRESA_A, "0", PRODUCAO,
                                                       cuf="99"),
        "cUFAutor fora da tabela do IBGE é recusado")
levanta(ValueError, lambda: N.montar_envelope_dist_nsu(T.EMPRESA_A, "0", PRODUCAO,
                                                       cuf=""),
        "cUFAutor vazio é recusado — não há default")

pf = N.montar_envelope_dist_nsu("12345678909", "0", PRODUCAO, cuf="26").decode()
ok("<CPF>12345678909</CPF>" in pf, "pessoa física vai como CPF, não CNPJ")

# ══════════════════════════════════════════════════════════════════════════
secao("Endpoints e ambiente")
igual(N.ENDPOINT[PRODUCAO.nome],
      "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
      "endpoint de produção")
igual(N.ENDPOINT[HOMOLOGACAO.nome],
      "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
      "endpoint de homologação")
f_prod = fonte(TransporteFalso([]))
f_hom = fonte(TransporteFalso([]), ambiente=HOMOLOGACAO)
ok(f_prod.url != f_hom.url, "produção e homologação NÃO compartilham endereço")
igual(f_prod.servico, "NFE_DISTRIBUICAO", "o serviço casa com a chave de checkpoint")
ok(N.CAP_CONS_CHAVE in f_prod.capacidades, "declara consulta por chave (NF-e tem)")
d = f_prod.descrever()
ok(T.EMPRESA_A not in str(d), "descrever() mascara o CNPJ")
igual(d["tpAmb"], "1", "e mostra o tpAmb")

# ══════════════════════════════════════════════════════════════════════════
secao("Interpretação da resposta — docZip")
r = N.interpretar_resposta(resposta(docs=[
    doczip("000000000000001", "resNFe_v1.01.xsd", T.xml_resumo(T.CHAVE_1)),
    doczip("000000000000002", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_2)),
]))
igual(r.cstat, "138", "lê o cStat")
igual(r.categoria, N.CAT_DOCUMENTOS, "e a categoria")
igual(r.ult_nsu, "000000000000002", "lê o ultNSU")
igual(r.max_nsu, "000000000000010", "lê o maxNSU")
igual(len(r.documentos), 2, "descompacta os dois docZip")
igual(r.avarias, (), "sem avarias")
igual(r.documentos[0].nsu, "000000000000001", "o NSU vem do atributo do docZip")
igual(r.documentos[0].schema, "resNFe_v1.01.xsd", "o schema informado é preservado")
igual(r.documentos[0].tipo, "resNFe", "com o rótulo do tipo extraído")
ok(b"resNFe" in r.documentos[0].conteudo, "e o conteúdo descompactado é o XML")
ok(b"nfeProc" in r.documentos[1].conteudo, "idem para o procNFe")

um = N.interpretar_resposta(resposta(docs=[
    doczip("000000000000001", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1))]))
igual(len(um.documentos), 1, "lote com um documento só")

evento = N.interpretar_resposta(resposta(docs=[
    doczip("000000000000003", "procEventoNFe_v1.00.xsd", T.xml_evento(T.CHAVE_1))]))
ok(b"procEventoNFe" in evento.documentos[0].conteudo, "evento chega inteiro")

# ══════════════════════════════════════════════════════════════════════════
secao("docZip com defeito — preservado, nunca descartado")
r = N.interpretar_resposta(resposta(docs=[
    doczip("000000000000001", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1)),
    doczip("000000000000002", "procNFe_v4.00.xsd", conteudo_bruto="isto-nao-e-base64!!"),
    doczip("000000000000003", "procNFe_v4.00.xsd",
           conteudo_bruto=base64.b64encode(b"nao sou gzip").decode()),
    doczip("000000000000004", "procNFe_v4.00.xsd", conteudo_bruto=""),
]))
igual(r.total_doczip, 4, "os quatro docZip foram vistos")
igual(len(r.documentos), 4, "e os QUATRO viraram DocumentoBruto — nenhum sumiu")
igual(len(r.avarias), 3, "três avarias registradas")
motivos = sorted(a["motivo"] for a in r.avarias)
igual(motivos, sorted([N.MOTIVO_BASE64, N.MOTIVO_GZIP, N.MOTIVO_VAZIO]),
      "base64 inválido, gzip inválido e vazio são distinguidos")
nsus = sorted(a["nsu"] for a in r.avarias)
igual(nsus, ["000000000000002", "000000000000003", "000000000000004"],
      "com o NSU de cada um, para poder pedir de novo por consNSU")
ok(r.documentos[1].conteudo, "o docZip não-base64 preservou os bytes que vieram")
igual(r.documentos[2].conteudo, b"nao sou gzip",
      "o que era base64 válido mas não gzip preservou o binário decodificado")

xml_bom = N.interpretar_resposta(resposta(docs=[
    doczip("000000000000001", "procNFe_v4.00.xsd", T.XML_MALFORMADO)]))
igual(xml_bom.avarias, (), "XML malformado DENTRO do gzip não é avaria de transporte")
igual(xml_bom.documentos[0].conteudo, T.XML_MALFORMADO,
      "ele chega como veio, e quem julga é o acervo")

dup = N.interpretar_resposta(resposta(docs=[
    doczip("000000000000001", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1)),
    doczip("000000000000001", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1)),
]))
igual(len(dup.documentos), 2,
      "docZip repetido na mesma resposta NÃO é filtrado aqui (é papel do acervo)")

# ══════════════════════════════════════════════════════════════════════════
secao("Respostas que não dá para confiar")
levanta(RespostaInvalida, lambda: N.interpretar_resposta(b""),
        "corpo vazio", "vazio")
levanta(RespostaInvalida, lambda: N.interpretar_resposta(SOAP_TRUNCADO),
        "SOAP truncado é resposta inválida, não erro de negócio", "bem-formado")
levanta(RespostaInvalida, lambda: N.interpretar_resposta(SOAP_FAULT),
        "SOAP Fault é reportado como tal", "fault")
levanta(RespostaInvalida,
        lambda: N.interpretar_resposta(b"<a><b>sem retDistDFeInt</b></a>"),
        "resposta sem retDistDFeInt", "retDistDFeInt")
levanta(RespostaInvalida, lambda: N.interpretar_resposta(
    resposta(cstat="").replace(b"<cStat></cStat>", b"")),
    "resposta sem cStat", "cStat")

# ══════════════════════════════════════════════════════════════════════════
secao("cStat — categorias explícitas, sem inventar significado")
igual(N.status_de("138").categoria, N.CAT_DOCUMENTOS, "138 = documentos encontrados")
igual(N.status_de("137").categoria, N.CAT_SEM_DOCUMENTOS, "137 = nenhum documento")
igual(N.status_de("656").categoria, N.CAT_CONSUMO_INDEVIDO, "656 = consumo indevido")
igual(N.status_de("108").categoria, N.CAT_INDISPONIVEL, "108 = serviço indisponível")
igual(N.status_de("578").categoria, N.CAT_AUTORIZACAO, "578 = autorização")
igual(N.status_de("999").categoria, N.CATEGORIA_NAO_DOCUMENTADA,
      "código fora da tabela NÃO ganha significado inventado")

for codigo in ("138", "137", "656"):
    ok(N.CSTAT[codigo].confirmado and N.CSTAT[codigo].fonte,
       f"cStat {codigo} está marcado como confirmado, com a fonte")
nao_confirmados = [c for c, s in N.CSTAT.items() if not s.confirmado]
ok(nao_confirmados, "os não confirmados estão marcados como tal, não escondidos")
ok(all(N.CSTAT[c].fonte for c in nao_confirmados),
   "e cada um diz por que não foi confirmado")

secao("cStat vira comportamento do motor")
f = fonte(TransporteFalso([resposta(cstat="137", motivo="Nenhum documento localizado",
                                    ult="000000000000010")]))
lote = f.consultar("000000000000010")
ok(lote.sem_documentos, "137 devolve lote 'sem documentos', não erro")
igual(lote.max_nsu, "000000000000010", "com o maxNSU preenchido")

levanta(N.ConsumoIndevido,
        lambda: fonte(TransporteFalso([resposta(cstat="656",
                                                motivo="Rejeicao: Consumo Indevido")])
                      ).consultar("0"),
        "656 levanta ConsumoIndevido", "1 hora")
ok(issubclass(N.ConsumoIndevido, ErroTransitorio),
   "e ConsumoIndevido é transitório (mais tarde resolve)")

levanta(ErroTransitorio,
        lambda: fonte(TransporteFalso([resposta(cstat="108", motivo="Servico paralisado")])
                      ).consultar("0"),
        "serviço paralisado é transitório")
levanta(N.CertificadoRecusado,
        lambda: fonte(TransporteFalso([resposta(cstat="578",
                                                motivo="Interessado nao autorizado")])
                      ).consultar("0"),
        "problema de autorização tem classe própria")
levanta(ErroDefinitivo,
        lambda: fonte(TransporteFalso([resposta(cstat="215", motivo="Falha no schema")])
                      ).consultar("0"),
        "rejeição de schema é definitiva")
levanta(ErroDefinitivo,
        lambda: fonte(TransporteFalso([resposta(cstat="991", motivo="Coisa nova")])
                      ).consultar("0"),
        "código não documentado é tratado de forma segura e mostra o xMotivo",
        "não consta")

# ══════════════════════════════════════════════════════════════════════════
secao("HTTP e TLS — cada falha com o seu nome")
import requests
from requests import exceptions as rexc

def transporte_com(excecao_ou_resposta):
    return N.TransporteHTTPS(sessao=SessaoFalsa(excecao_ou_resposta))

levanta(N.CertificadoRecusado,
        lambda: transporte_com(rexc.SSLError("handshake failure")).enviar("u", b"x"),
        "erro de TLS vira CertificadoRecusado, não 'serviço fora do ar'")
levanta(ErroTransitorio,
        lambda: transporte_com(rexc.ConnectTimeout("timed out")).enviar("u", b"x"),
        "timeout de CONEXÃO é transitório", "conectar")
levanta(ErroTransitorio,
        lambda: transporte_com(rexc.ReadTimeout("timed out")).enviar("u", b"x"),
        "timeout de LEITURA é distinguido do de conexão", "ler a resposta")
levanta(ErroTransitorio,
        lambda: transporte_com(rexc.ConnectionError("Name or service not known")
                               ).enviar("u", b"x"),
        "DNS/conexão recusada é transitório", "dns")
levanta(ErroTransitorio,
        lambda: transporte_com(rexc.ProxyError("proxy")).enviar("u", b"x"),
        "proxy recusando é transitório")

levanta(ErroTransitorio,
        lambda: transporte_com(RespostaHTTP(503, b"x")).enviar("u", b"x"),
        "HTTP 5xx é transitório")
levanta(ErroTransitorio,
        lambda: transporte_com(RespostaHTTP(429, b"x")).enviar("u", b"x"),
        "HTTP 429 é transitório")
levanta(N.CertificadoRecusado,
        lambda: transporte_com(RespostaHTTP(403, b"x")).enviar("u", b"x"),
        "HTTP 403 é problema de autenticação por certificado")
levanta(N.CertificadoRecusado,
        lambda: transporte_com(RespostaHTTP(496, b"x")).enviar("u", b"x"),
        "HTTP 496 (certificado cliente exigido) idem")
levanta(ErroDefinitivo,
        lambda: transporte_com(RespostaHTTP(404, b"x")).enviar("u", b"x"),
        "HTTP 4xx inesperado é definitivo")
levanta(RespostaInvalida,
        lambda: transporte_com(RespostaHTTP(200, HTML_DE_PORTAL, "text/html")
                               ).enviar("u", b"x"),
        "corpo HTML em vez de XML é resposta inválida", "text/html")

sessao = SessaoFalsa(RespostaHTTP(200, resposta(), "application/soap+xml"))
corpo = N.TransporteHTTPS(sessao=sessao).enviar("u", b"x")
ok(b"retDistDFeInt" in corpo, "resposta boa passa")
igual(sessao.ultimo_timeout, (N.TIMEOUT_CONEXAO, N.TIMEOUT_LEITURA),
      "os DOIS timeouts são passados explicitamente")

# ══════════════════════════════════════════════════════════════════════════
secao("Cadeia completa: SOAP -> DocumentoBruto -> acervo -> índice")
with apoio.raiz_temporaria("ing3b_") as raiz:
    trans = TransporteFalso([
        resposta(ult="000000000000002", maximo="000000000000004", docs=[
            doczip("000000000000001", "resNFe_v1.01.xsd", T.xml_resumo(T.CHAVE_1)),
            doczip("000000000000002", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_2)),
        ]),
        resposta(ult="000000000000004", maximo="000000000000004", docs=[
            doczip("000000000000003", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1)),
            doczip("000000000000004", "procEventoNFe_v1.00.xsd",
                   T.xml_evento(T.CHAVE_2)),
        ]),
    ])
    res = pipe.ingerir(raiz, T.EMPRESA_A, fonte(trans), ambiente="producao",
                       usar_trava=False)
    ok(res.sucesso, "a ingestão termina com sucesso")
    igual(res.execucao.documentos, 4, "quatro documentos trafegaram")
    igual(res.indexacao.indexados, 3, "e três documentos distintos indexados")

    ind = idx.abrir(raiz, T.EMPRESA_A)
    igual(len(ind.listar(especie=dm.NFE55)), 2, "duas NF-e")
    igual(len(ind.listar(especie=dm.EVENTO_NFE)), 1, "um evento")
    cp = RepositorioCheckpoint(raiz).carregar(T.EMPRESA_A, "nfe_distribuicao",
                                              "producao")
    igual(cp.ult_nsu, "000000000000004", "o checkpoint chegou ao fim do lote")
    igual(cp.max_nsu, "000000000000004", "e conhece o maxNSU")

    # a chave da NF-e completa promoveu o resumo, como na ING 3A
    ac = acv.abrir(raiz, T.EMPRESA_A)
    cap = ac.ler_captura(idf.NFE55, idf.id_de_nfe(T.CHAVE_1))
    igual(cap["prioridade"], idf.PRIO_AUTORIZADO,
          "o procNFe do 2º lote promoveu o resumo do 1º")
    igual(len(cap["copias"]), 2, "e as duas cópias estão guardadas")

secao("Checkpoint NÃO avança quando a persistência falha")
with apoio.raiz_temporaria("ing3b_") as raiz:
    class AcervoQuebrado(acv.AcervoArquivos):
        def preservar(self, doc):
            raise acv.ErroAcervo("disco cheio (simulado)")

    trans = TransporteFalso([resposta(ult="000000000000002", maximo="000000000000009",
                                      docs=[doczip("000000000000001",
                                                   "procNFe_v4.00.xsd",
                                                   T.xml_nfe(T.CHAVE_1))])])
    repo = RepositorioCheckpoint(raiz)
    ac = AcervoQuebrado(dados_dir=raiz, identidade=T.EMPRESA_A,
                        servico="nfe_distribuicao", ambiente="producao")
    r = DistribuicaoRunner(repo).executar(T.EMPRESA_A, fonte(trans), ac,
                                          usar_trava=False)
    ok(not r.sucesso, "a execução reporta falha")
    igual(repo.carregar(T.EMPRESA_A, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000000", "e o NSU continua onde estava")
    igual(len(trans.enviados), 1, "uma única consulta foi feita")

secao("Falha parcial de lote — o 37º documento derruba o lote inteiro, sem perder NSU")
with apoio.raiz_temporaria("ing3b_") as raiz:
    class AcervoQueFalhaNoTrigesimoSetimo(acv.AcervoArquivos):
        gravados = 0

        def preservar(self, doc):
            AcervoQueFalhaNoTrigesimoSetimo.gravados += 1
            if AcervoQueFalhaNoTrigesimoSetimo.gravados == 37:
                raise acv.ErroAcervo("falha no 37º (simulada)")
            return super().preservar(doc)

    docs = [doczip(f"{i:015d}", "procNFe_v4.00.xsd",
                   T.xml_nfe(T.chave_ficticia(T.FORNECEDOR, i)))
            for i in range(1, 51)]
    trans = TransporteFalso([resposta(ult="000000000000050",
                                      maximo="000000000000100", docs=docs)])
    repo = RepositorioCheckpoint(raiz)
    ac = AcervoQueFalhaNoTrigesimoSetimo(dados_dir=raiz, identidade=T.EMPRESA_A,
                                         servico="nfe_distribuicao",
                                         ambiente="producao")
    r = DistribuicaoRunner(repo).executar(T.EMPRESA_A, fonte(trans), ac,
                                          usar_trava=False)
    ok(not r.sucesso, "o lote NÃO é dado como concluído")
    igual(repo.carregar(T.EMPRESA_A, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000000",
          "o ultNSU fica em zero — os 50 virão de novo, e 36 serão duplicata")
    igual(len(acv.abrir(raiz, T.EMPRESA_A).listar()), 36,
          "os 36 que gravaram continuam no acervo (duplicata é barata)")
    ok(any("reprocessad" in n for n in r.notas),
       "e o relatório avisa que haverá reprocessamento")

secao("Schema desconhecido pelo serviço chega e é preservado")
with apoio.raiz_temporaria("ing3b_") as raiz:
    trans = TransporteFalso([resposta(ult="000000000000002", maximo="000000000000002",
                                      docs=[
        doczip("000000000000001", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1)),
        doczip("000000000000002", "procCTeOS_v4.00.xsd", T.XML_DESCONHECIDO),
    ])])
    res = pipe.ingerir(raiz, T.EMPRESA_A, fonte(trans), ambiente="producao",
                       usar_trava=False)
    igual(res.indexacao.indexados, 1, "a NF-e é indexada")
    igual(res.indexacao.schema_desconhecido, 1, "e o desconhecido é preservado")
    igual(len(acv.abrir(raiz, T.EMPRESA_A).listar()), 2, "os dois estão no acervo")
    cp = RepositorioCheckpoint(raiz).carregar(T.EMPRESA_A, "nfe_distribuicao",
                                              "producao")
    igual(cp.ult_nsu, "000000000000002",
          "e o checkpoint avança — o byte está salvo, que é o que importa")

secao("docZip corrompido também chega ao acervo")
with apoio.raiz_temporaria("ing3b_") as raiz:
    trans = TransporteFalso([resposta(ult="000000000000002", maximo="000000000000002",
                                      docs=[
        doczip("000000000000001", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1)),
        doczip("000000000000002", "procNFe_v4.00.xsd",
               conteudo_bruto="@@@nao-e-base64@@@"),
    ])])
    res = pipe.ingerir(raiz, T.EMPRESA_A, fonte(trans), ambiente="producao",
                       usar_trava=False)
    igual(len(acv.abrir(raiz, T.EMPRESA_A).listar()), 2,
          "o documento avariado NÃO some — está no acervo")
    igual(res.indexacao.schema_desconhecido, 1, "e é listado como não interpretado")
    cp = RepositorioCheckpoint(raiz).carregar(T.EMPRESA_A, "nfe_distribuicao",
                                              "producao")
    igual(cp.ult_nsu, "000000000000002", "o NSU avança porque nada foi perdido")

secao("'Em dia' só depois de perguntar (decisão da ING 2 preservada)")
with apoio.raiz_temporaria("ing3b_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    trans = TransporteFalso([resposta(ult="000000000000005", maximo="000000000000005",
                                      docs=[doczip("000000000000005",
                                                   "procNFe_v4.00.xsd",
                                                   T.xml_nfe(T.CHAVE_1))])])
    ac = acv.abrir(raiz, T.EMPRESA_A, servico="nfe_distribuicao", ambiente="producao")
    DistribuicaoRunner(repo).executar(T.EMPRESA_A, fonte(trans), ac, usar_trava=False)
    cp = repo.carregar(T.EMPRESA_A, "nfe_distribuicao", "producao")
    ok(cp.em_dia, "o checkpoint diz 'em dia'")

    trans2 = TransporteFalso([resposta(ult="000000000000006",
                                       maximo="000000000000006",
                                       docs=[doczip("000000000000006",
                                                    "procNFe_v4.00.xsd",
                                                    T.xml_nfe(T.CHAVE_2))])])
    DistribuicaoRunner(repo).executar(T.EMPRESA_A, fonte(trans2), ac, usar_trava=False)
    igual(len(trans2.enviados), 1,
          "mesmo 'em dia', a próxima execução CONSULTA — não pula")
    igual(repo.carregar(T.EMPRESA_A, "nfe_distribuicao", "producao").ult_nsu,
          "000000000000006", "e acha o documento novo que tinha aparecido")

# ══════════════════════════════════════════════════════════════════════════
secao("Observabilidade sem segredo")
with apoio.raiz_temporaria("ing3b_") as raiz:
    trans = TransporteFalso([resposta(ult="000000000000001", maximo="000000000000001",
                                      docs=[doczip("000000000000001",
                                                   "procNFe_v4.00.xsd",
                                                   T.xml_nfe(T.CHAVE_1))])])
    f = fonte(trans)
    res = pipe.ingerir(raiz, T.EMPRESA_A, f, ambiente="producao", usar_trava=False)

    log = f.ultima_resposta.para_log()
    igual(log["cStat"], "138", "o log traz o cStat")
    igual(log["ultNSU"], "000000000000001", "o ultNSU")
    igual(log["maxNSU"], "000000000000001", "o maxNSU")
    igual(log["docZip"], 1, "a quantidade de docZip")
    texto_log = str(log)
    ok("nfeProc" not in texto_log and "PRODUTO" not in texto_log,
       "e NENHUM pedaço de XML fiscal")

    linha = res.execucao.linha()
    ok(T.EMPRESA_A not in linha, "a linha de execução não traz o CNPJ inteiro")
    trilha = aud.abrir(raiz, T.EMPRESA_A).ler()
    texto_trilha = str(trilha)
    ok(T.EMPRESA_A not in texto_trilha, "nem a trilha")
    ok("PRODUTO 1" not in texto_trilha, "nem o conteúdo do documento")

secao("Fronteira do conector — conferida no CÓDIGO, não na prosa")
# A primeira versão destes testes fazia `grep` no arquivo inteiro e falhava por
# causa de palavras nos comentários ("a senha existe só dentro de
# sessao.criar_sessao"). Comentário não é dependência. O que importa é o que o
# módulo IMPORTA e o que ele NOMEIA — e isso se lê na árvore sintática.
import ast as _ast

_ARQ = RAIZ / "nfse" / "backend" / "ingestao" / "conectores" / "nfe_dfe.py"
_ARVORE = _ast.parse(_ARQ.read_text("utf-8"))


def modulos_importados(arvore) -> set[str]:
    achados = set()
    for no in _ast.walk(arvore):
        if isinstance(no, _ast.Import):
            achados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, _ast.ImportFrom):
            alvo = (no.module or "").split(".")[-1] if no.module else ""
            if alvo:
                achados.add(alvo)
            achados.update(a.name for a in no.names)
    return achados


def nomes_atribuidos(arvore) -> set[str]:
    achados = set()
    for no in _ast.walk(arvore):
        if isinstance(no, _ast.Name) and isinstance(no.ctx, _ast.Store):
            achados.add(no.id)
        elif isinstance(no, _ast.arg):
            achados.add(no.arg)
        elif isinstance(no, _ast.keyword) and no.arg:
            achados.add(no.arg)
    return achados


importados = modulos_importados(_ARVORE)
nomeados = {n.lower() for n in nomes_atribuidos(_ARVORE)}

# O que o conector NÃO pode conhecer: as camadas de cima do pipeline.
for proibido in ("acervo", "indice", "parsers", "documento", "pipeline",
                 "classificador", "auditoria"):
    ok(proibido not in importados,
       f"o conector não importa '{proibido}' — a seta só aponta para baixo")

# Nem certificado, nem senha: isso é da ING 1.
for proibido in ("Pkcs12Adapter", "pkcs12_filename", "pkcs12_password"):
    ok(proibido not in importados and proibido.lower() not in nomeados,
       f"o conector não manipula '{proibido}' (é da ING 1)")
ok(not any("senha" in n or "password" in n for n in nomeados),
   "nenhuma variável ou parâmetro de senha existe no conector")

# O que ele PODE e DEVE conhecer.
for esperado in ("distribuicao", "checkpoint", "ambiente", "identidade"):
    ok(esperado in importados, f"e depende de '{esperado}', como previsto")

secao("O conector não interpreta regra tributária")
_TRIBUTARIO = ("CFOP", "NCM", "CST", "CSOSN", "ICMS", "aliquota", "tributo")
texto_codigo = "\n".join(
    _ast.unparse(no) for no in _ARVORE.body
    if not isinstance(no, _ast.Expr) or not isinstance(no.value, _ast.Constant))
import re as _re
for termo in _TRIBUTARIO:
    # Palavra inteira: "CST" é substring de "cStat"/"CSTAT", e casar por
    # substring transformaria a tabela de status do serviço em falso positivo.
    ok(not _re.search(rf"\b{_re.escape(termo)}\b", texto_codigo, _re.I),
       f"'{termo}' não aparece no código do conector")

# ══════════════════════════════════════════════════════════════════════════
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
print("ING 3B: todos os testes passaram.")
