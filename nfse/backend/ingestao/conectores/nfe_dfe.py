"""
nfe_dfe.py — NF-e Distribuição de DF-e de Interesse (Ambiente Nacional).

Implementa `Fonte` e `FontePorChave` da ING 2. É a única peça do FISCALE que
conhece SOAP, `docZip`, gzip, base64 e `cStat` da NF-e.

────────────────────────────────────────────────────────────────────────────
O QUE É SEPARADO DE QUÊ, E POR QUÊ
────────────────────────────────────────────────────────────────────────────
    montar_envelope()        função pura: (CNPJ, NSU) -> bytes do SOAP
    interpretar_resposta()   função pura: bytes -> Resposta
    Transporte               protocolo: quem de fato fala TCP
    FonteNFeDistribuicaoDFe  junta os três

    Essa divisão existe para que **a suíte inteira rode sem rede**. O que pode
    dar errado num integrador fiscal quase nunca é o socket — é interpretar a
    resposta. Com `interpretar_resposta()` pura, todo caso difícil (docZip
    corrompido, cStat inesperado, SOAP truncado) vira teste barato e
    determinístico.

────────────────────────────────────────────────────────────────────────────
DOCUMENTO NUNCA SE PERDE POR NÃO SER ENTENDIDO
────────────────────────────────────────────────────────────────────────────
    O código anterior (`nfe.py`) fazia isto:

        try:    xml = gzip.decompress(base64.b64decode(dz.text))
        except: continue          # ← o documento sumia, e o NSU avançava

    Um `docZip` corrompido era descartado em silêncio e o `ultNSU` passava por
    cima dele. Como a Distribuição DF-e **não deixa voltar** (o serviço retém
    ~3 meses e o `ultNSU` só anda para frente), esse documento estava perdido
    para sempre — sem ninguém saber.

    Aqui, `docZip` que não descompacta é preservado do mesmo jeito, com os bytes
    que chegaram e o motivo registrado. O acervo o guarda como DESCONHECIDO
    (ING 3A) e um reprocessamento futuro pode reinterpretá-lo.

────────────────────────────────────────────────────────────────────────────
PROCEDÊNCIA DOS DADOS OFICIAIS
────────────────────────────────────────────────────────────────────────────
    Ver `FISCALE_ING3B.md` §2. Resumo do que está aqui:

    • endpoint de produção, `distNSU`/`consNSU`/`consChNFe`, lote de 50,
      retenção de ~3 meses e `docZip` em gzip+base64 vêm de `contratos.py`,
      conferido no pacote oficial `PL_NFeDistDFe_104` em 13/08/2026;
    • `cStat` 137, 138 e 656 confirmados na NT 2014.002;
    • endpoint de homologação (`hom1.nfe.fazenda.gov.br`) confirmado em portais
      oficiais estaduais.

    **O que NÃO foi confirmado em fonte oficial está marcado
    `confirmado=False` na tabela `CSTAT`** — e código não documentado nenhum
    recebe significado inventado: cai em `CATEGORIA_NAO_DOCUMENTADA`, que é
    tratada de forma segura (não avança checkpoint) e reporta o `xMotivo`
    literal do serviço.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import re
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from ..ambiente import HOMOLOGACAO, PRODUCAO, Ambiente, resolver as resolver_ambiente
from ..autor_consulta import AutorConsulta, resolver as resolver_autor
from ..checkpoint import NFE_DISTRIBUICAO, normalizar_nsu, nsu_int
from ..distribuicao import (
    CAP_CONS_CHAVE, CAP_CONS_NSU, CAP_DIST_NSU,
    DocumentoBruto, ErroDefinitivo, ErroFonte, ErroTransitorio, Lote,
    RespostaInvalida, higienizar,
)
from ..identidade import normalizar
from ..identificacao import chave_valida, digitos_da_chave

SERVICO = NFE_DISTRIBUICAO

NS_NFE = "http://www.portalfiscal.inf.br/nfe"
NS_WSDL = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
NS_SOAP12 = "http://www.w3.org/2003/05/soap-envelope"

VERSAO_DIST = "1.01"          # distDFeInt — PL_NFeDistDFe_104

ENDPOINT = {
    PRODUCAO.nome: "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    HOMOLOGACAO.nome: "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
}

# tpAmb do XML: 1 = produção, 2 = homologação. NÃO confundir com o `ambiente`
# do FISCALE, que também governa a chave do checkpoint (D29).
TP_AMB = {PRODUCAO.nome: "1", HOMOLOGACAO.nome: "2"}

CONTENT_TYPE = "application/soap+xml; charset=utf-8"

# Dois timeouts, e são coisas diferentes: conectar deve ser rápido ou o
# endereço está fora; ler pode demorar porque o serviço monta um lote de 50
# documentos do outro lado.
TIMEOUT_CONEXAO = 15
TIMEOUT_LEITURA = 90

MAX_DOCS_POR_LOTE = 50        # PL_NFeDistDFe_104

# `cUFAutor` NÃO mora mais aqui. A UF do autor da consulta é uma propriedade do
# ESCRITÓRIO, não deste conector nem da empresa consultada — ver
# `autor_consulta.py`. Deixar a tabela aqui foi o que permitiu, na primeira
# versão, derivar o valor de `empresa.uf`.


# ════════════════════════════════════════════════════════════════════════════
# cStat — categorias conceituais
# ════════════════════════════════════════════════════════════════════════════
CAT_DOCUMENTOS = "DOCUMENTOS_ENCONTRADOS"
CAT_SEM_DOCUMENTOS = "NENHUM_DOCUMENTO"
CAT_INDISPONIVEL = "SERVICO_INDISPONIVEL"
CAT_CONSUMO_INDEVIDO = "CONSUMO_INDEVIDO"
CAT_REJEICAO = "REJEICAO"
CAT_AUTORIZACAO = "IDENTIFICACAO_OU_AUTORIZACAO"
CAT_TECNICO = "ERRO_TECNICO"
CATEGORIA_NAO_DOCUMENTADA = "NAO_DOCUMENTADO"


@dataclass(frozen=True)
class Status:
    codigo: str
    categoria: str
    descricao: str
    confirmado: bool               # a descrição veio de fonte OFICIAL?
    fonte: str = ""


def _s(codigo, categoria, descricao, confirmado, fonte=""):
    return Status(codigo, categoria, descricao, confirmado, fonte)


_NT = "NT 2014.002 (conferido 13/08/2026)"
_PENDENTE = ("descrição NÃO confirmada nesta pesquisa — conferir na NT 2014.002; "
             "o tratamento é o seguro (não avança checkpoint)")

CSTAT: dict[str, Status] = {
    # ── confirmados em fonte oficial ────────────────────────────────────
    "138": _s("138", CAT_DOCUMENTOS, "Documento localizado", True, _NT),
    "137": _s("137", CAT_SEM_DOCUMENTOS, "Nenhum documento localizado", True, _NT),
    "656": _s("656", CAT_CONSUMO_INDEVIDO, "Rejeição: Consumo Indevido", True, _NT),

    # ── NÃO confirmados nesta pesquisa ──────────────────────────────────
    # Ficam aqui porque a CATEGORIA (e portanto o tratamento) é o ponto: um
    # serviço fora do ar precisa ser transitório, não definitivo. A descrição
    # exata é o que falta conferir, e está marcada.
    "108": _s("108", CAT_INDISPONIVEL, "Serviço paralisado momentaneamente",
              False, _PENDENTE),
    "109": _s("109", CAT_INDISPONIVEL, "Serviço paralisado sem previsão",
              False, _PENDENTE),
    "111": _s("111", CAT_INDISPONIVEL, "Consumo indevido — em processamento",
              False, _PENDENTE),
    "215": _s("215", CAT_REJEICAO, "Rejeição: falha no schema XML",
              False, _PENDENTE),
    "236": _s("236", CAT_REJEICAO, "Rejeição: chave de acesso inválida",
              False, _PENDENTE),
    "242": _s("242", CAT_REJEICAO, "Rejeição: elemento inválido na área de dados",
              False, _PENDENTE),
    "252": _s("252", CAT_REJEICAO, "Rejeição: ambiente informado difere do webservice",
              False, _PENDENTE),
    "280": _s("280", CAT_AUTORIZACAO, "Rejeição: certificado transmissor inválido",
              False, _PENDENTE),
    "281": _s("281", CAT_AUTORIZACAO, "Rejeição: certificado transmissor — data de validade",
              False, _PENDENTE),
    "283": _s("283", CAT_AUTORIZACAO, "Rejeição: certificado transmissor — sem CNPJ",
              False, _PENDENTE),
    "286": _s("286", CAT_AUTORIZACAO, "Rejeição: certificado transmissor revogado",
              False, _PENDENTE),
    "404": _s("404", CAT_REJEICAO, "Rejeição: uso de prefixo de namespace não permitido",
              False, _PENDENTE),
    "578": _s("578", CAT_AUTORIZACAO, "Rejeição: interessado não autorizado",
              False, _PENDENTE),
    "589": _s("589", CAT_REJEICAO, "Rejeição: número de NSU informado superior ao maxNSU",
              False, _PENDENTE),
    "632": _s("632", CAT_REJEICAO, "Rejeição: solicitação fora de padrão", False, _PENDENTE),
}

DESCONHECIDO = _s("", CATEGORIA_NAO_DOCUMENTADA,
                  "código não consta da tabela conferida", False,
                  "nenhuma — não inventar significado")

# Categorias que o motor pode tentar de novo mais tarde.
_TRANSITORIAS = {CAT_INDISPONIVEL, CAT_CONSUMO_INDEVIDO}


def status_de(cstat: str) -> Status:
    """O `cStat` como dado. Código fora da tabela vira `NAO_DOCUMENTADO`.

    Deliberadamente **não** há palpite por faixa numérica ("2xx é rejeição"):
    inventar significado para código não documentado é como se aprende errado o
    comportamento de um serviço fiscal."""
    c = str(cstat or "").strip()
    encontrado = CSTAT.get(c)
    if encontrado:
        return encontrado
    return Status(c, CATEGORIA_NAO_DOCUMENTADA, DESCONHECIDO.descricao, False,
                  DESCONHECIDO.fonte)


class ConsumoIndevido(ErroTransitorio):
    """cStat 656. O CNPJ fica bloqueado por cerca de 1 hora.

    É `ErroTransitorio` porque repetir MAIS TARDE resolve — mas quem chamar
    precisa respeitar a espera. O motor já para a varredura desta empresa ao
    receber isto; não existe retry automático nesta fase."""


class CertificadoRecusado(ErroFonte):
    """Handshake TLS falhou, ou o serviço recusou o certificado.

    **Não** herda de `ErroTransitorio`/`ErroDefinitivo` de propósito: o
    `DistribuicaoRunner` classifica pelo nome da classe e transforma isto em
    `CREDENCIAL_INVALIDA`, que é o status que separa "certificado vencido desta
    empresa" de "serviço fora do ar". Ver `distribuicao.py:328-337`."""


# ════════════════════════════════════════════════════════════════════════════
# 1. Montagem do pedido — função pura
# ════════════════════════════════════════════════════════════════════════════
def _validar_cuf(cuf: str) -> str:
    """Confere que o `cUFAutor` recebido é um código de UF do IBGE.

    Quem RESOLVE de onde ele vem é `autor_consulta`; aqui só se recusa lixo
    antes de montar XML."""
    c = re.sub(r"\D", "", str(cuf or ""))
    if not c:
        raise ValueError("cUFAutor não informado — resolva pelo autor_consulta")
    c = c.zfill(2)
    from ..autor_consulta import UF_POR_CUF
    if c not in UF_POR_CUF:
        raise ValueError(f"cUFAutor inválido: {c!r}")
    return c


def _corpo(consulta: str, identidade: str, tp_amb: str, cuf: str) -> bytes:
    ident = normalizar(identidade)
    if not ident.valido:
        raise ValueError(f"identidade fiscal inválida: {ident.motivo}")
    marca = "CNPJ" if ident.tipo == "CNPJ" else "CPF"
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap12:Envelope xmlns:soap12="{NS_SOAP12}"><soap12:Body>'
        f'<nfeDistDFeInteresse xmlns="{NS_WSDL}"><nfeDadosMsg>'
        f'<distDFeInt xmlns="{NS_NFE}" versao="{VERSAO_DIST}">'
        f'<tpAmb>{tp_amb}</tpAmb><cUFAutor>{cuf}</cUFAutor>'
        f'<{marca}>{ident.valor}</{marca}>'
        f'{consulta}'
        '</distDFeInt></nfeDadosMsg></nfeDistDFeInteresse>'
        '</soap12:Body></soap12:Envelope>'
    ).encode("utf-8")


def montar_envelope_dist_nsu(identidade: str, ult_nsu: str, ambiente: Ambiente,
                             cuf: str) -> bytes:
    """Varredura sequencial a partir de um NSU. É o modo normal de operação.

    `cuf` é **obrigatório e já resolvido** por `autor_consulta`. Não há default e
    não há derivação a partir da empresa: era exatamente assim que o valor
    errado entrava."""
    return _corpo(f"<distNSU><ultNSU>{normalizar_nsu(ult_nsu)}</ultNSU></distNSU>",
                  identidade, TP_AMB[ambiente.nome], _validar_cuf(cuf))


def montar_envelope_cons_nsu(identidade: str, nsu: str, ambiente: Ambiente,
                             cuf: str) -> bytes:
    """Busca UM NSU específico — para fechar lacuna, não para varrer."""
    return _corpo(f"<consNSU><NSU>{normalizar_nsu(nsu)}</NSU></consNSU>",
                  identidade, TP_AMB[ambiente.nome], _validar_cuf(cuf))


def montar_envelope_cons_chave(identidade: str, chave: str, ambiente: Ambiente,
                               cuf: str) -> bytes:
    """Busca por chave de acesso. **Só a NF-e tem isto** — o CT-e não.

    A chave é validada aqui: mandar chave com DV errado gasta uma consulta da
    cota horária para receber rejeição."""
    ch = digitos_da_chave(chave)
    if not chave_valida(ch):
        raise ValueError("chave de acesso inválida (44 dígitos com DV)")
    return _corpo(f"<consChNFe><chNFe>{ch}</chNFe></consChNFe>",
                  identidade, TP_AMB[ambiente.nome], _validar_cuf(cuf))


# ════════════════════════════════════════════════════════════════════════════
# 2. Interpretação da resposta — função pura, sem rede
# ════════════════════════════════════════════════════════════════════════════
MOTIVO_BASE64 = "docZip não é base64 válido"
MOTIVO_GZIP = "docZip não descompacta como gzip"
MOTIVO_VAZIO = "docZip veio vazio"


# ── subtipos de 656 ─────────────────────────────────────────────────────────
# NEM TODO 656 É A MESMA COISA, e tratar como um estado só apaga a diferença
# que decide o que fazer. Observado em produção em 17/08/2026, no MESMO ciclo,
# com dois `xMotivo` distintos:
#
#   SEQUENCIA  "Deve ser utilizado o ultNSU nas solicitacoes subsequentes"
#              A rejeição fala da POSIÇÃO enviada. Se a nossa sequência está
#              íntegra e ainda assim veio isto, há divergência entre o que o
#              Ambiente Nacional espera e o que sabemos — e voltar a consultar
#              depois do cooldown não resolve nada, só repete o consumo.
#
#   JANELA     "Deve ser aguardado 1 hora para efetuar nova solicitacao caso
#              nao existam mais documentos a serem pesquisados"
#              A rejeição fala de FREQUÊNCIA. Esperar resolve.
#
# LIMITE fica declarado porque é a hipótese que o caso real sugere (contador
# acumulado por CNPJ), **sem fonte oficial** — só é atribuído se o texto disser.
SEQUENCIA_656 = "CONSUMO_INDEVIDO_SEQUENCIA"
JANELA_656 = "CONSUMO_INDEVIDO_JANELA"
LIMITE_656 = "CONSUMO_INDEVIDO_LIMITE"
OUTRO_656 = "CONSUMO_INDEVIDO_OUTRO"

# Pistas textuais, em minúsculas e sem acento — o serviço não acentua.
_PISTAS_656 = (
    (SEQUENCIA_656, ("utilizado o ultnsu", "ultnsu nas solicitacoes",
                     "informado superior", "nsu informado")),
    (JANELA_656, ("aguardado 1 hora", "aguardar 1 hora", "apos 1 hora",
                  "nao existam mais documentos")),
    (LIMITE_656, ("limite", "quantidade de consultas", "excedeu",
                  "consultas por hora")),
)


def _sem_acento(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t or "")
                   if unicodedata.category(c) != "Mn").lower()


def classificar_consumo_indevido(xmotivo: str) -> str:
    """Subtipo do `656` a partir do `xMotivo`. Nunca levanta.

    A ordem importa: a mensagem da TRX contém **as duas** pistas ("utilizado o
    ultNSU" e "Tente apos 1 hora"). Sequência vence, porque é o diagnóstico
    mais grave — esperar não conserta posição errada, e tratar sequência como
    janela faria a empresa voltar sozinha para a automação."""
    t = _sem_acento(xmotivo)
    if not t:
        return OUTRO_656
    for subtipo, pistas in _PISTAS_656:
        if any(p in t for p in pistas):
            return subtipo
    return OUTRO_656


@dataclass(frozen=True)
class Resposta:
    """O `retDistDFeInt` já lido, sem nenhuma interpretação fiscal."""
    cstat: str = ""
    motivo: str = ""                 # xMotivo, como o serviço mandou
    status: Status = DESCONHECIDO
    ult_nsu: str = ""
    max_nsu: str = ""
    dh_resposta: str = ""
    tp_amb: str = ""
    documentos: tuple[DocumentoBruto, ...] = ()
    avarias: tuple[dict, ...] = ()   # docZip que não decodificou — preservados
    total_doczip: int = 0
    # Os bytes EXATOS que o transporte devolveu. Não entram em log, não entram
    # em `para_log`, e servem a uma coisa só: `resposta_bruta.preservar()`.
    # Sem eles, um erro de leitura como o do NSU (CTE 4) fica indetectável.
    bruto: bytes = b""

    @property
    def categoria(self) -> str:
        return self.status.categoria

    @property
    def subtipo_consumo(self) -> str:
        """Só faz sentido quando a categoria é consumo indevido."""
        if self.categoria != CAT_CONSUMO_INDEVIDO:
            return ""
        return classificar_consumo_indevido(self.motivo)

    def para_log(self) -> dict:
        """Nunca o XML, nunca o conteúdo. Só contagens e identificadores."""
        d = {"cStat": self.cstat, "categoria": self.categoria,
             "xMotivo": higienizar(self.motivo)[:120],
             "ultNSU": self.ult_nsu or None, "maxNSU": self.max_nsu or None,
             "docZip": self.total_doczip, "avarias": len(self.avarias)}
        if self.subtipo_consumo:
            d["subtipo"] = self.subtipo_consumo
        return d


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _host(url: str) -> str:
    """O endereço lógico do serviço — host, sem caminho nem parâmetro.

    Vai para a trilha. Identifica o ambiente sem virar URL completa dentro de
    linha de auditoria."""
    try:
        return urlparse(str(url or "")).netloc or str(url or "")
    except ValueError:
        return str(url or "")


def _local(tag: str) -> str:
    return re.sub(r"^\{[^}]*\}", "", tag or "")


def _achar_profundo(raiz, nome: str):
    for el in raiz.iter():
        if _local(el.tag) == nome:
            return el
    return None


def _texto(raiz, nome: str) -> str:
    el = _achar_profundo(raiz, nome)
    return (el.text or "").strip() if el is not None else ""


def _decodificar(texto: str) -> tuple[bytes, str]:
    """`docZip` → XML. Devolve `(conteudo, motivo_da_avaria)`.

    Quando não dá para decodificar, devolve o que foi possível recuperar em vez
    de nada — o documento precisa chegar ao acervo de qualquer jeito."""
    bruto = (texto or "").strip()
    if not bruto:
        return b"", MOTIVO_VAZIO
    try:
        comprimido = base64.b64decode(bruto, validate=True)
    except (binascii.Error, ValueError):
        # Preserva o próprio base64 como bytes: é tudo o que temos, e um
        # reprocessamento futuro pode achar sentido nele.
        return bruto.encode("utf-8", "replace"), MOTIVO_BASE64
    try:
        return gzip.decompress(comprimido), ""
    except (OSError, EOFError, zlib.error):
        return comprimido, MOTIVO_GZIP


def interpretar_resposta(corpo: bytes) -> Resposta:
    """Lê o SOAP e devolve `Resposta`. **Não toca a rede.**

    Levanta `RespostaInvalida` quando não dá para confiar no que chegou — e
    essa é a distinção que importa: resposta ilegível não é a mesma coisa que
    resposta legível dizendo "rejeitado"."""
    if not corpo:
        raise RespostaInvalida("o serviço devolveu corpo vazio")
    try:
        raiz = ET.fromstring(corpo)
    except ET.ParseError as exc:
        raise RespostaInvalida(
            f"resposta não é XML bem-formado: {type(exc).__name__}") from None

    falha = _achar_profundo(raiz, "Fault")
    if falha is not None:
        raise RespostaInvalida(
            f"SOAP Fault: {higienizar(_texto(falha, 'Text') or _texto(falha, 'faultstring'))}")

    ret = _achar_profundo(raiz, "retDistDFeInt")
    if ret is None:
        raise RespostaInvalida("resposta sem retDistDFeInt")

    cstat = _texto(ret, "cStat")
    if not cstat:
        raise RespostaInvalida("resposta sem cStat")

    documentos: list[DocumentoBruto] = []
    avarias: list[dict] = []
    total = 0
    lote = _achar_profundo(ret, "loteDistDFeInt")
    if lote is not None:
        for dz in lote:
            if _local(dz.tag) != "docZip":
                continue
            total += 1
            nsu = normalizar_nsu(dz.get("NSU") or "0")
            schema = (dz.get("schema") or "").strip()
            conteudo, avaria = _decodificar(dz.text or "")
            if avaria:
                # NÃO descarta. Preserva o que veio e registra a avaria: o NSU
                # não volta, e documento perdido não tem conserto.
                avarias.append({"nsu": nsu, "schema": schema, "motivo": avaria,
                                "bytes": len(conteudo)})
            documentos.append(DocumentoBruto(nsu=nsu, conteudo=conteudo,
                                             schema=schema, tipo=_tipo_do_schema(schema)))

    return Resposta(
        bruto=corpo,
        cstat=cstat, motivo=_texto(ret, "xMotivo"), status=status_de(cstat),
        ult_nsu=normalizar_nsu(_texto(ret, "ultNSU")) if _texto(ret, "ultNSU") else "",
        max_nsu=normalizar_nsu(_texto(ret, "maxNSU")) if _texto(ret, "maxNSU") else "",
        dh_resposta=_texto(ret, "dhResp"), tp_amb=_texto(ret, "tpAmb"),
        documentos=tuple(documentos), avarias=tuple(avarias), total_doczip=total)


def _tipo_do_schema(schema: str) -> str:
    """`procNFe_v4.00.xsd` → `procNFe`. Só um rótulo — quem decide a espécie é
    `identificacao.py`, olhando o documento, não o que o serviço disse dele."""
    return (schema or "").split("_")[0].strip()


def resposta_para_lote(r: Resposta) -> Lote:
    """`Resposta` → `Lote` da ING 2, aplicando a categoria do `cStat`.

    É aqui que a taxonomia vira comportamento do motor."""
    cat = r.categoria
    if cat == CAT_CONSUMO_INDEVIDO:
        raise ConsumoIndevido(
            f"cStat {r.cstat}: {r.motivo or r.status.descricao}. "
            "O serviço bloqueia novas consultas deste CNPJ por cerca de 1 hora.")
    if cat == CAT_INDISPONIVEL:
        raise ErroTransitorio(f"cStat {r.cstat}: {r.motivo or r.status.descricao}")
    if cat == CAT_AUTORIZACAO:
        raise CertificadoRecusado(f"cStat {r.cstat}: {r.motivo or r.status.descricao}")
    if cat == CAT_REJEICAO:
        raise ErroDefinitivo(f"cStat {r.cstat}: {r.motivo or r.status.descricao}")
    if cat == CATEGORIA_NAO_DOCUMENTADA:
        # Não inventamos significado. Tratado como definitivo porque é o seguro:
        # não avança checkpoint, não repete sozinho, e mostra o texto literal do
        # serviço para quem for investigar.
        raise ErroDefinitivo(
            f"cStat {r.cstat} não consta da tabela conferida. "
            f"O serviço disse: {higienizar(r.motivo)!r}")

    if cat == CAT_SEM_DOCUMENTOS:
        return Lote(ult_nsu=r.ult_nsu, max_nsu=r.max_nsu, sem_documentos=True,
                    motivo=r.motivo or r.status.descricao)
    return Lote(documentos=r.documentos, ult_nsu=r.ult_nsu, max_nsu=r.max_nsu,
                sem_documentos=False, motivo=r.motivo)


# ════════════════════════════════════════════════════════════════════════════
# 3. Transporte — a única parte que fala TCP
# ════════════════════════════════════════════════════════════════════════════
class Transporte(Protocol):
    """Manda bytes, devolve bytes. Trocável por dublê nos testes."""
    def enviar(self, url: str, corpo: bytes) -> bytes: ...


@dataclass
class TransporteHTTPS:
    """`requests.Session` já autenticada pela ING 1 (`sessao.criar_sessao`).

    Este objeto **não** abre `.pfx` e **não** vê senha: recebe a sessão pronta.
    A senha existe só dentro de `sessao.criar_sessao`, e é apagada lá mesmo."""
    sessao: object
    timeout_conexao: int = TIMEOUT_CONEXAO
    timeout_leitura: int = TIMEOUT_LEITURA

    def enviar(self, url: str, corpo: bytes) -> bytes:
        # Importado aqui, e não no topo: quem só interpreta resposta (os testes)
        # não deve precisar de `requests` instalado.
        import requests
        from requests import exceptions as rexc

        try:
            r = self.sessao.post(
                url, data=corpo, headers={"Content-Type": CONTENT_TYPE},
                timeout=(self.timeout_conexao, self.timeout_leitura))
        except rexc.SSLError as exc:
            # Handshake TLS: certificado vencido, revogado, cadeia incompleta.
            # NÃO é "serviço fora do ar" — repetir não resolve nada.
            raise CertificadoRecusado(
                f"TLS recusado pelo servidor: {higienizar(exc)}") from None
        except rexc.ConnectTimeout as exc:
            raise ErroTransitorio(
                f"tempo esgotado ao CONECTAR em {self.timeout_conexao}s: "
                f"{higienizar(exc)}") from None
        except rexc.ReadTimeout as exc:
            raise ErroTransitorio(
                f"tempo esgotado ao LER a resposta em {self.timeout_leitura}s: "
                f"{higienizar(exc)}") from None
        except rexc.ProxyError as exc:
            raise ErroTransitorio(f"proxy recusou: {higienizar(exc)}") from None
        except rexc.ConnectionError as exc:
            # Cobre DNS que não resolve e conexão recusada. `requests` não os
            # separa em classes distintas; o texto original preserva a causa.
            raise ErroTransitorio(
                f"não consegui conectar (DNS ou conexão recusada): "
                f"{higienizar(exc)}") from None
        except rexc.RequestException as exc:
            raise ErroTransitorio(f"falha de rede: {higienizar(exc)}") from None

        return self._conferir(r)

    @staticmethod
    def _conferir(r) -> bytes:
        """Status HTTP e tipo de conteúdo. Cada erro com o seu nome."""
        codigo = int(getattr(r, "status_code", 0) or 0)
        tipo = str((getattr(r, "headers", {}) or {}).get("Content-Type", "")).lower()

        if codigo == 429 or 500 <= codigo <= 599:
            raise ErroTransitorio(f"HTTP {codigo} do serviço — vale tentar mais tarde")
        if codigo in (401, 403, 496, 495):
            # 495/496 são os códigos de certificado cliente ausente/inválido.
            raise CertificadoRecusado(
                f"HTTP {codigo}: o serviço não aceitou a autenticação por certificado")
        if codigo and codigo != 200:
            raise ErroDefinitivo(f"HTTP {codigo} inesperado do serviço")

        corpo = getattr(r, "content", b"") or b""
        if tipo and not any(m in tipo for m in ("xml", "soap")):
            # HTML de portal de erro, JSON de gateway, página de captive portal.
            raise RespostaInvalida(
                f"o serviço respondeu {tipo!r} em vez de XML "
                f"({len(corpo)} bytes)")
        return corpo


# ════════════════════════════════════════════════════════════════════════════
# 4. A Fonte
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class FonteNFeDistribuicaoDFe:
    """`Fonte` + `FontePorChave` para a NF-e modelo 55.

    Um objeto por (empresa, ambiente). O `ambiente` entra na chave do
    checkpoint e no `tpAmb` do XML — misturar os dois faria uma varredura de
    homologação mover o ponteiro de produção (D29)."""

    identidade: str
    transporte: Transporte
    ambiente: Ambiente = PRODUCAO
    # `autor` é QUEM PERGUNTA (o escritório). NÃO existe parâmetro `uf` aqui, e
    # a ausência é proposital: era por ele que a UF da empresa consultada virava
    # `cUFAutor`. Ver `autor_consulta.py`.
    autor: AutorConsulta | None = None
    cuf: str = ""                       # override explícito (--cuf), diagnóstico
    url: str = ""
    dados_dir: object = None            # de onde ler o autor, quando não vier
    servico: str = field(default=SERVICO, init=False)
    capacidades: frozenset = field(
        default=frozenset({CAP_DIST_NSU, CAP_CONS_NSU, CAP_CONS_CHAVE}), init=False)
    ultima_resposta: Resposta | None = field(default=None, init=False)
    avisos: list = field(default_factory=list, init=False)
    # Uma entrada por chamada, com ou sem documento. DADO PURO: nada aqui toca
    # disco. O `pipeline` é quem grava na trilha de auditoria.
    tentativas: list = field(default_factory=list, init=False)

    def __post_init__(self):
        ident = normalizar(self.identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida: {ident.motivo}")
        self.identidade = ident.valor
        self.ambiente = resolver_ambiente(self.ambiente)
        self.url = self.url or ENDPOINT[self.ambiente.nome]
        # Resolve o AUTOR aqui, na construção: se não houver de onde tirar o
        # cUFAutor, a falha acontece antes de qualquer rede — e não no meio de
        # uma varredura, com metade das empresas já consultadas.
        self.autor = resolver_autor(dados_dir=self.dados_dir,
                                    cuf_override=self.cuf, autor=self.autor)
        self.cuf = self.autor.cuf

    # ── contrato Fonte ────────────────────────────────────────────────────
    def consultar(self, desde_nsu: str) -> Lote:
        """Varredura sequencial. Chamado em laço pelo `DistribuicaoRunner`.

        Note que NÃO há atalho "já está em dia": a decisão da ING 2 é que
        `ultNSU == maxNSU` no checkpoint antigo é uma FOTOGRAFIA e não autoriza
        pular a consulta — senão a empresa para de buscar para sempre."""
        return self._executar(
            montar_envelope_dist_nsu(self.identidade, desde_nsu, self.ambiente,
                                     self.cuf),
            tipo="distNSU", nsu_enviado=desde_nsu)

    # ── capacidades declaradas ────────────────────────────────────────────
    def consultar_nsu(self, nsu: str) -> Lote:
        """Fecha uma lacuna pontual. Não serve para varrer."""
        return self._executar(
            montar_envelope_cons_nsu(self.identidade, nsu, self.ambiente, self.cuf),
            tipo="consNSU", nsu_enviado=nsu)

    def consultar_por_chave(self, chave: str) -> Lote:
        """Recupera um documento por chave. Existe na NF-e; **não** no CT-e."""
        return self._executar(
            montar_envelope_cons_chave(self.identidade, chave, self.ambiente, self.cuf),
            tipo="consChNFe", nsu_enviado="0")

    @staticmethod
    def _fechar(tentativa: dict, t0: float) -> None:
        tentativa["fim"] = _agora()
        tentativa["duracao_s"] = round(time.monotonic() - t0, 3)

    # ── miolo ─────────────────────────────────────────────────────────────
    def _executar(self, envelope: bytes, tipo: str, nsu_enviado: str) -> Lote:
        """Uma chamada. Registra a tentativa como DADO, aconteça o que acontecer.

        A tentativa é anexada a `self.tentativas` mesmo quando a consulta falha
        no transporte — é justamente o caso em que a trilha mais importa. Quem
        grava em disco é o `pipeline`; este módulo não conhece auditoria."""
        inicio = _agora()
        t0 = time.monotonic()
        tentativa = {
            "inicio": inicio, "servico": self.servico,
            "ambiente": self.ambiente.nome, "tipo_consulta": tipo,
            "nsu_enviado": normalizar_nsu(nsu_enviado),
            "endpoint": _host(self.url), "transporte_ok": False,
            "resultado": "", "doczip": 0,
        }
        self.tentativas.append(tentativa)

        try:
            corpo = self.transporte.enviar(self.url, envelope)
            tentativa["transporte_ok"] = True
        except Exception as exc:
            tentativa.update(resultado="FALHA_TRANSPORTE",
                             erro_classe=type(exc).__name__,
                             erro_tecnico=higienizar(exc))
            self._fechar(tentativa, t0)
            raise

        try:
            resposta = interpretar_resposta(corpo)
        except Exception as exc:
            tentativa.update(resultado="RESPOSTA_INVALIDA",
                             erro_classe=type(exc).__name__,
                             erro_tecnico=higienizar(exc))
            self._fechar(tentativa, t0)
            raise

        tentativa.update(
            cstat=resposta.cstat, xmotivo=higienizar(resposta.motivo)[:200],
            ult_nsu=resposta.ult_nsu or None, max_nsu=resposta.max_nsu or None,
            doczip=resposta.total_doczip, resultado=resposta.categoria,
            avarias=len(resposta.avarias) or None)
        # O subtipo do `656` entra na tentativa, não só no relatório: é dele
        # que o controlador decide entre "espere" e "alguém precisa olhar", e
        # essa decisão tem de estar na trilha, não na memória de quem leu.
        if resposta.subtipo_consumo:
            tentativa["subtipo_consumo"] = resposta.subtipo_consumo
            tentativa["rejeitado_em"] = tentativa["inicio"]

        # ── divergência externa (ING 3B-R2) ────────────────────────────────
        # O `ultNSU` de uma REJEIÇÃO é notícia sobre a posição do Ambiente
        # Nacional — não é prova de que temos aqueles documentos. Aqui só se
        # detecta e se reporta; quem decide o que fazer com isso é o checkpoint,
        # e ele nunca avança por causa disto.
        if (resposta.ult_nsu
                and nsu_int(resposta.ult_nsu) > nsu_int(nsu_enviado)
                and resposta.categoria not in (CAT_DOCUMENTOS,)):
            tentativa["divergencia_externa"] = True
            tentativa["nsu_observado_sefaz"] = resposta.ult_nsu

        self._fechar(tentativa, t0)
        self.ultima_resposta = resposta
        # Lote maior que o contrato oficial não é erro e não pode virar recusa:
        # os documentos vieram e precisam ser preservados. Mas é sinal de que o
        # contrato mudou, e isso tem de aparecer em vez de passar batido.
        self.avisos = (
            [f"o serviço devolveu {len(resposta.documentos)} documentos, acima dos "
             f"{MAX_DOCS_POR_LOTE} do contrato conferido — preservados assim mesmo"]
            if len(resposta.documentos) > MAX_DOCS_POR_LOTE else [])
        return resposta_para_lote(resposta)

    # ── diagnóstico, sem segredo ──────────────────────────────────────────
    def descrever(self) -> dict:
        ident = normalizar(self.identidade)
        return {"empresa": ident.mascarado(), "servico": self.servico,
                "ambiente": self.ambiente.nome, "url": self.url,
                "cUFAutor": self.cuf,
                "uf_do_autor": self.autor.uf,
                "origem_do_cUFAutor": self.autor.origem,
                "tpAmb": TP_AMB[self.ambiente.nome], "versao": VERSAO_DIST}


def criar(cad, identificador, dados_dir=None, ambiente=PRODUCAO,
          cuf: str = "", timeout_leitura: int = TIMEOUT_LEITURA):
    """Monta a Fonte a partir do cadastro da ING 1 — o caminho de produção.

    Confere ANTES de qualquer consulta que existe credencial utilizável para
    ESTA empresa: gastar uma consulta da cota horária para descobrir que o
    certificado venceu é desperdício evitável."""
    from .. import credencial_estado as ce
    from .. import sessao as sess

    amb = resolver_ambiente(ambiente)
    ident = normalizar(identificador)
    if not ident.valido:
        raise ValueError(f"identidade fiscal inválida: {ident.motivo}")

    empresa = cad.obter_empresa(ident.valor)
    if empresa is None:
        raise ValueError(f"empresa {ident.mascarado()} não está no cadastro")

    cred = cad.obter_certificado_da_empresa(ident.valor)
    if cred is None:
        raise ValueError(f"empresa {ident.mascarado()} não tem certificado associado")

    raiz = dados_dir if dados_dir is not None else getattr(cad, "dados_dir", None)
    estado = ce.avaliar(cred, raiz)
    if not estado.utilizavel:
        raise ValueError(
            f"credencial de {ident.mascarado()} não está utilizável: "
            f"{estado.acao_necessaria}")

    sessao = sess.criar_sessao(cred, raiz, amb, timeout=timeout_leitura)
    # `empresa.uf` NÃO entra aqui. O cUFAutor vem do autor configurado, ou do
    # override explícito — nunca do endereço de quem está sendo consultado.
    return FonteNFeDistribuicaoDFe(
        identidade=ident.valor,
        transporte=TransporteHTTPS(sessao=sessao, timeout_leitura=timeout_leitura),
        ambiente=amb, cuf=cuf, dados_dir=raiz)
