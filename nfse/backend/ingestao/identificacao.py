"""
identificacao.py — quem é ESTE documento, antes de qualquer parser.

O QUE ESTE MÓDULO RESPONDE
    Dado um `DocumentoBruto` recém-chegado: que espécie é, qual a chave de
    acesso, e qual o identificador interno estável. Nada além disso.

POR QUE É SEPARADO DO PARSER
    O acervo precisa saber ONDE gravar antes de saber o que o documento diz. Se
    a gravação dependesse do parser, um schema desconhecido não teria onde ser
    guardado — e a regra da casa é que schema desconhecido **se guarda assim
    mesmo**, porque o NSU não volta e o CT-e retém só 3 meses.

    Por isso aqui só se olha a "casca": a tag raiz, o namespace e a chave. É
    barato, é estável, e funciona mesmo para documento que ainda não sabemos ler.

DOCUMENTO NÃO É EVENTO
    Um cancelamento e a NF-e cancelada compartilham a chave de acesso, e são
    coisas diferentes. Se os dois gerassem o mesmo identificador, o evento
    sobrescreveria a nota — ou pior, seria descartado como "duplicata".

    Por isso o identificador do evento carrega também `tpEvento`, `nSeqEvento`
    e **`cOrgao`**:

        NF-e         →  sha256("NFE55|<chave>")
        evento       →  sha256("EVENTO_NFE|<chave>|<tpEvento>|<nSeq>|<cOrgao>")

POR QUE `cOrgao` ENTROU NA IDENTIDADE (16/08/2026)
    Caso real: a mesma NF-e recebeu dois `tpEvento 610514` (Registro de Passagem
    propagado por MDF-e/CT-e), **ambos com `nSeqEvento 1`**, registrados por
    órgãos diferentes, em datas e protocolos diferentes. São dois fatos fiscais
    distintos — a carga passou por duas UFs —, e a fórmula antiga dava o mesmo
    identificador aos dois. O segundo virou `COLISAO` e ficou fora do índice.

    A sequência do evento é contada **por órgão**, não globalmente: `nSeqEvento`
    só é único dentro de `(chave, tpEvento, cOrgao)`. Sem `cOrgao`, a chave
    natural está incompleta.

    O `Id` oficial **não resolve isto**: ele é `ID + tpEvento(6) + chave(44) +
    nSeq(2)`, 54 caracteres, e não contém o órgão — os dois eventos reais têm o
    mesmo `Id` oficial. Confirmado nos 5 `procEventoNFe` do acervo.

POR QUE `nProt` E `dhEvento` **NÃO** ENTRAM
    `nProt` não é um valor por evento lógico: num `procEventoNFe` de
    cancelamento existem **dois** — o protocolo da NF-e cancelada, dentro de
    `<detEvento>`, e o protocolo do próprio evento, em `<retEvento>`. Observado
    no acervo real. Usá-lo faria o mesmo evento receber identidades diferentes
    conforme o formato, quebrando a promoção resumo → completo.

    `dhEvento` é carimbo de tempo: entra no conteúdo, não na identidade.

IDENTIDADE FORTE × FRACA
    Chave de 44 dígitos com DV conferido é identidade **forte**: pode
    deduplicar. Quando não há chave confiável, a identidade é **fraca** — cai
    no hash do conteúdo e **nunca** deduplica contra outro documento, porque
    dois documentos diferentes com bytes iguais são raros, mas duas notas
    distintas tratadas como uma apagam receita em silêncio.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

# ── espécies que a ING 3A reconhece ─────────────────────────────────────────
NFE55 = "NFE55"                     # a nota em si (procNFe, NFe ou resNFe)
NFCE65 = "NFCE65"                   # cupom eletrônico — MESMAS tags, outro modelo
EVENTO_NFE = "EVENTO_NFE"           # cancelamento, CC-e, manifestação, ciência
CTE57 = "CTE57"                     # conhecimento de transporte (cteProc/CTe/resCTe)
EVENTO_CTE = "EVENTO_CTE"           # cancelamento, CC-e e demais eventos do CT-e
# CT-e OS (67), CT-e Simplificado e GTV-e: a distribuição os entrega, e eles
# são guardados com espécie própria. Não é "não sei o que é" — é "sei o que é
# e ainda não sei ler". A distinção importa: o documento preservado com chave
# deduplica; um DESCONHECIDO sem chave, não.
CTE_NAO_SUPORTADO = "CTE_NAO_SUPORTADO"
DESCONHECIDO = "DESCONHECIDO"       # chegou, será guardado, ainda não sabemos ler

ESPECIES = (NFE55, NFCE65, EVENTO_NFE, CTE57, EVENTO_CTE,
            CTE_NAO_SUPORTADO, DESCONHECIDO)

# As duas espécies que são "uma nota de mercadoria": mesma estrutura, mesmo
# parser, modelos diferentes. Quem precisa das duas pergunta por esta tupla em
# vez de listar `NFE55` e esquecer a NFC-e seis meses depois.
ESPECIES_NOTA = (NFE55, NFCE65)

# O modelo, como aparece nas posições 21-22 da chave de acesso.
MODELO_DA_ESPECIE = {NFE55: "55", NFCE65: "65", CTE57: "57"}
ESPECIE_DO_MODELO = {"55": NFE55, "65": NFCE65}

# ── prioridade da cópia: quanto maior, mais completa ────────────────────────
# Motivo de existir: a Distribuição DF-e entrega primeiro o RESUMO (`resNFe`) e,
# depois da manifestação, o XML completo com protocolo. São dois eventos
# legítimos do mesmo documento — não uma duplicata, e não uma divergência.
PRIO_RESUMO = 1
PRIO_COMPLETO = 2
PRIO_AUTORIZADO = 3                 # completo E com protocolo de autorização

_ROTULO_PRIO = {PRIO_RESUMO: "resumo", PRIO_COMPLETO: "completo",
                PRIO_AUTORIZADO: "autorizado"}

# ── tags raiz conhecidas → (espécie, prioridade) ────────────────────────────
_RAIZ = {
    "nfeProc":        (NFE55, PRIO_AUTORIZADO),
    "NFe":            (NFE55, PRIO_COMPLETO),
    "resNFe":         (NFE55, PRIO_RESUMO),
    "procEventoNFe":  (EVENTO_NFE, PRIO_AUTORIZADO),
    "evento":         (EVENTO_NFE, PRIO_COMPLETO),
    "resEvento":      (EVENTO_NFE, PRIO_RESUMO),
}

# ── CT-e: as mesmas posições, outro leiaute ─────────────────────────────────
_RAIZ_CTE = {
    "cteProc":        (CTE57, PRIO_AUTORIZADO),
    "CTe":            (CTE57, PRIO_COMPLETO),
    "resCTe":         (CTE57, PRIO_RESUMO),
    "procEventoCTe":  (EVENTO_CTE, PRIO_AUTORIZADO),
    "eventoCTe":      (EVENTO_CTE, PRIO_COMPLETO),
    "evento":         (EVENTO_CTE, PRIO_COMPLETO),
    "resEvento":      (EVENTO_CTE, PRIO_RESUMO),
    # ── o que a distribuição também entrega, e que ainda não sabemos ler ──
    # CT-e OS (67), CT-e Simplificado e GTV-e vêm pelo MESMO serviço. Eles são
    # identificados como espécie própria e PRESERVADOS byte a byte; o que falta
    # é interpretação, não guarda.
    #
    # Cair em DESCONHECIDO seria pior de um jeito específico: o documento
    # entraria sem chave, com identidade fraca, e deixaria de deduplicar contra
    # ele mesmo na próxima consulta.
    "cteOSProc":      (CTE_NAO_SUPORTADO, PRIO_AUTORIZADO),
    "CTeOS":          (CTE_NAO_SUPORTADO, PRIO_COMPLETO),
    "cteSimpProc":    (CTE_NAO_SUPORTADO, PRIO_AUTORIZADO),
    "CTeSimp":        (CTE_NAO_SUPORTADO, PRIO_COMPLETO),
    "GTVeProc":       (CTE_NAO_SUPORTADO, PRIO_AUTORIZADO),
    "GTVe":           (CTE_NAO_SUPORTADO, PRIO_COMPLETO),
}

# O NAMESPACE É O DESEMPATE, E ELE NÃO É OPCIONAL.
#     `resEvento` e `evento` são tags dos DOIS documentos. Um resumo de evento
#     de CT-e tem exatamente a mesma tag raiz que um de NF-e; o que os separa é
#     o namespace do portal — `/cte` contra `/nfe`.
#
#     Sem isso, todo evento de CT-e seria identificado como `EVENTO_NFE`, iria
#     para a fila da NF-e e ficaria pendurado numa chave que aquele acervo não
#     conhece. Não seria erro visível: seria um evento a menos, em silêncio.
NS_CTE = "http://www.portalfiscal.inf.br/cte"

_SO_DIGITOS = re.compile(r"\D")
_NS = re.compile(r"^\{[^}]*\}")


def _local(tag: str) -> str:
    """Nome da tag sem o namespace. `{http://…}NFe` → `NFe`."""
    return _NS.sub("", tag or "")


def _namespace(tag: str) -> str:
    """`{http://…/cte}resEvento` → `http://…/cte`. `""` quando não há."""
    t = tag or ""
    return t[1:t.index("}")] if t.startswith("{") and "}" in t else ""


# ── chave de acesso ─────────────────────────────────────────────────────────
def digitos_da_chave(v) -> str:
    """Só os dígitos. Aceita `NFe3521…` (formato do atributo `Id`)."""
    return _SO_DIGITOS.sub("", str(v or ""))


def dv_chave(chave44: str) -> int:
    """Dígito verificador da chave de acesso — módulo 11, pesos 2..9 cíclicos.

    Mesma regra para NF-e, NFC-e e CT-e: é a chave de acesso do modelo nacional,
    não algo específico da NF-e."""
    corpo = chave44[:43]
    peso, soma = 2, 0
    for d in reversed(corpo):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return 0 if resto in (0, 1) else 11 - resto


def chave_valida(v) -> bool:
    """44 dígitos e DV fechando.

    Isto não prova que a nota existe — prova que o número não foi corrompido no
    transporte. É o suficiente para usá-lo como identidade."""
    c = digitos_da_chave(v)
    if len(c) != 44 or not c.isdigit() or c == c[0] * 44:
        return False
    return int(c[43]) == dv_chave(c)


# ── o resultado ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Identificacao:
    """O que se sabe do documento sem abrir o parser."""
    especie: str = DESCONHECIDO
    chave: str = ""
    id_documento: str = ""
    hash_conteudo: str = ""              # "sha256:<hex>"
    prioridade: int = PRIO_RESUMO
    identidade_forte: bool = False
    raiz: str = ""                       # tag raiz encontrada, para diagnóstico
    tp_evento: str = ""
    seq_evento: str = ""
    orgao: str = ""                      # cOrgao — parte da identidade do evento
    motivo: str = ""                     # por que não deu, quando não deu
    avisos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def e_evento(self) -> bool:
        return self.especie == EVENTO_NFE

    @property
    def reconhecido(self) -> bool:
        return self.especie != DESCONHECIDO

    @property
    def rotulo_prioridade(self) -> str:
        return _ROTULO_PRIO.get(self.prioridade, str(self.prioridade))

    def para_log(self) -> dict:
        """Sem conteúdo de documento — só identificadores."""
        return {"especie": self.especie, "chave": self.chave or None,
                "id_documento": self.id_documento, "hash": self.hash_conteudo,
                "prioridade": self.rotulo_prioridade,
                "identidade_forte": self.identidade_forte,
                "raiz": self.raiz or None, "motivo": self.motivo or None,
                "orgao": self.orgao or None}


def hash_conteudo(conteudo: bytes) -> str:
    """SHA-256 dos bytes exatos, com prefixo do algoritmo.

    O prefixo não é enfeite: quando um dia trocarmos de algoritmo, os hashes
    antigos precisam continuar identificáveis em vez de virar string opaca."""
    return "sha256:" + hashlib.sha256(conteudo or b"").hexdigest()


def _id(*partes: str) -> str:
    """Identificador interno: 32 hex de SHA-256 sobre as partes.

    Estável entre máquinas e entre execuções — é o que faz API, importação
    manual e pasta monitorada convergirem para o mesmo documento sem
    combinarem nada entre si."""
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()[:32]


def modelo_da_chave(chave: str) -> str:
    """Posições 21-22 da chave: o modelo do documento. `""` se a chave não serve.

    É daqui que sai a espécie de uma nota de mercadoria — **não** da tag raiz.
    NF-e e NFC-e usam exatamente as mesmas tags (`nfeProc`, `NFe`, `resNFe`);
    quem olha só a raiz chama cupom de NF-e e não percebe."""
    c = digitos_da_chave(chave)
    return c[20:22] if len(c) == 44 else ""


def especie_da_nota(chave: str) -> str:
    """`NFE55` ou `NFCE65`, pelo modelo na chave. Sem chave válida, `NFE55`.

    O padrão em `NFE55` não é chute: sem chave utilizável a identidade já é
    fraca e o documento não deduplica contra ninguém — o rótulo serve só para
    ele não ficar órfão de espécie."""
    return ESPECIE_DO_MODELO.get(modelo_da_chave(chave), NFE55)


def id_de_nfe(chave: str) -> str:
    """Identidade de uma nota de mercadoria, com a ESPÉCIE certa no prefixo.

    O prefixo faz parte do hash. Se a NFC-e entrasse como `NFE55|<chave>`, o
    id existiria e até seria único — a chave difere —, mas mentiria sobre o que
    é o documento, e todo filtro por espécie misturaria os dois."""
    return _id(especie_da_nota(chave), digitos_da_chave(chave))


def id_de_nfce(chave: str) -> str:
    """Explícito, para quem quer afirmar que é cupom. Mesmo resultado."""
    return _id(NFCE65, digitos_da_chave(chave))


def id_de_evento(chave: str, tp_evento: str, seq: str, orgao: str = "") -> str:
    """Identidade canônica do evento: `(chave, tpEvento, nSeq, cOrgao)`.

    `orgao` vazio mantém o formato antigo de partes — não por
    retrocompatibilidade cega, mas porque **sem `cOrgao` não há como afirmar
    que dois eventos são o mesmo**. Nesse caso a identidade fica igual à
    legada, e o evento com órgão declarado nasce separado. Separar demais é
    reversível; juntar errado apaga um fato fiscal."""
    partes = [EVENTO_NFE, digitos_da_chave(chave),
              str(tp_evento or "").strip(), str(seq or "1").strip()]
    org = str(orgao or "").strip()
    if org:
        partes.append(org)
    return _id(*partes)


def id_de_evento_legado(chave: str, tp_evento: str, seq: str) -> str:
    """A fórmula anterior a 16/08/2026, sem `cOrgao`.

    Existe **só** para a migração saber de onde cada documento veio. Nenhum
    caminho de gravação deve chamá-la."""
    return _id(EVENTO_NFE, digitos_da_chave(chave),
               str(tp_evento or "").strip(), str(seq or "1").strip())


# ── o trabalho ──────────────────────────────────────────────────────────────
def _achar(raiz: ET.Element, *nomes: str) -> str:
    """Primeiro texto encontrado para qualquer um dos nomes de tag, em qualquer
    profundidade. Ignora namespace — a NF-e usa um, o evento usa outro, e os
    dois mudam entre versões de schema."""
    alvo = set(nomes)
    for el in raiz.iter():
        if _local(el.tag) in alvo and (el.text or "").strip():
            return el.text.strip()
    return ""


def _chave_do_id(raiz: ET.Element) -> str:
    """A chave que mora no atributo `Id` (`<infNFe Id="NFe3521...">`).

    **`infEvento` fica de fora de propósito.** O `Id` do evento é
    `ID + tpEvento(6) + chave(44) + nSeqEvento(2)` — a chave está no MEIO, não
    no fim. Pegar os últimos 44 dígitos dali devolve um número que parece chave,
    passa por qualquer verificação de comprimento e está errado. Para evento, a
    chave vem de `<chNFe>`, que é inequívoco."""
    for el in raiz.iter():
        if _local(el.tag) in ("infNFe", "infCte"):
            bruto = el.get("Id") or ""
            if bruto:
                d = digitos_da_chave(bruto)
                if len(d) >= 44:
                    return d[-44:]
    return ""


def orgao_do_evento(raiz: ET.Element) -> str:
    """O `cOrgao` que identifica o evento, escolhido de forma DETERMINÍSTICA.

    Num `procEventoNFe` há dois: o do `<evento>` (o que o autor declarou) e o
    do `<retEvento>` (o que respondeu). Nos 5 do acervo real eles coincidem,
    mas "coincidem hoje" não é regra — a identidade não pode depender de qual
    apareceu primeiro no documento. Fica valendo o do `<evento>`, que é o fato
    declarado; `retEvento` é a resposta a ele.

    Num `resEvento` só existe um, filho direto da raiz.
    """
    for bloco in raiz.iter():
        if _local(bloco.tag) != "evento":
            continue
        for el in bloco.iter():
            if _local(el.tag) == "cOrgao" and (el.text or "").strip():
                return el.text.strip()
    return _achar(raiz, "cOrgao")


def identificar(conteudo: bytes, chave_informada: str = "",
                schema: str = "") -> Identificacao:
    """Identifica o documento pela casca. **NUNCA levanta.**

    Documento que não dá para identificar volta como `DESCONHECIDO` com o
    motivo preenchido — e é guardado assim mesmo. Levantar aqui faria o acervo
    recusar justamente o caso que ele mais precisa preservar.
    """
    h = hash_conteudo(conteudo)
    avisos: list[str] = []

    if not conteudo:
        return Identificacao(hash_conteudo=h, id_documento=_id(DESCONHECIDO, h),
                             motivo="conteúdo vazio")

    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError as exc:
        # XML malformado é DESCONHECIDO, não erro. Ele será preservado, e um
        # parser futuro (ou uma correção da fonte) pode reprocessá-lo.
        return Identificacao(hash_conteudo=h, id_documento=_id(DESCONHECIDO, h),
                             motivo=f"XML malformado: {type(exc).__name__}")

    nome_raiz = _local(raiz.tag)
    # O namespace decide qual tabela consultar. Documento sem namespace cai na
    # da NF-e, que é o comportamento de sempre.
    tabela = _RAIZ_CTE if _namespace(raiz.tag) == NS_CTE else _RAIZ
    especie, prioridade = tabela.get(nome_raiz, (DESCONHECIDO, PRIO_RESUMO))

    if especie == DESCONHECIDO:
        return Identificacao(hash_conteudo=h, id_documento=_id(DESCONHECIDO, h),
                             raiz=nome_raiz,
                             motivo=f"tag raiz não reconhecida: '{nome_raiz}'")

    # `nfeProc` só é AUTORIZADO se realmente trouxer o protocolo. Sem `protNFe`
    # ele é um envelope completo sem prova de autorização.
    if nome_raiz == "nfeProc" and not any(
            _local(e.tag) == "protNFe" for e in raiz.iter()):
        prioridade = PRIO_COMPLETO
        avisos.append("nfeProc sem protNFe: tratado como completo, não autorizado")

    chave = _chave_do_id(raiz) or digitos_da_chave(_achar(raiz, "chNFe", "chCTe"))
    if not chave and chave_informada:
        chave = digitos_da_chave(chave_informada)
        avisos.append("chave veio do serviço, não do documento")

    if not chave_valida(chave):
        motivo = ("chave ausente no documento" if not chave else
                  f"chave inválida ({len(chave)} dígitos ou DV não confere)")
        # Espécie reconhecida mas sem chave utilizável: identidade FRACA.
        # Guarda-se pelo hash, e não deduplica contra ninguém.
        return Identificacao(especie=especie, hash_conteudo=h, prioridade=prioridade,
                             id_documento=_id(especie, "sem-chave", h),
                             raiz=nome_raiz, identidade_forte=False,
                             motivo=motivo, avisos=tuple(avisos))

    if especie in (EVENTO_NFE, EVENTO_CTE):
        tp = _achar(raiz, "tpEvento")
        seq = _achar(raiz, "nSeqEvento") or "1"
        org = orgao_do_evento(raiz)
        if not org:
            avisos.append("evento sem cOrgao: identidade não distingue órgãos")
        return Identificacao(especie=especie, chave=chave, prioridade=prioridade,
                             id_documento=id_de_evento(chave, tp, seq, org),
                             hash_conteudo=h, identidade_forte=True,
                             raiz=nome_raiz, tp_evento=tp, seq_evento=seq,
                             orgao=org, avisos=tuple(avisos))

    # A tag raiz disse "é uma nota"; a CHAVE diz qual modelo. `nfeProc` serve
    # NF-e e NFC-e sem distinção, e só aqui dá para separá-las.
    if especie == NFE55:
        especie = especie_da_nota(chave)
    elif especie == CTE_NAO_SUPORTADO:
        avisos.append(f"espécie `{nome_raiz}` preservada sem interpretação "
                      f"(modelo {chave[20:22]}): leiaute não implementado")
    elif especie == CTE57 and chave[20:22] not in ("57", "67"):
        # A tag disse CT-e e a chave discorda. Não se escolhe um dos dois: o
        # documento fica com a espécie da tag e o aviso registra a divergência,
        # para quem investigar ver as duas versões.
        avisos.append(f"modelo {chave[20:22]!r} na chave não é de CT-e (57/67)")
    return Identificacao(especie=especie, chave=chave, prioridade=prioridade,
                         id_documento=_id(especie, digitos_da_chave(chave)),
                         hash_conteudo=h,
                         identidade_forte=True, raiz=nome_raiz,
                         avisos=tuple(avisos))
