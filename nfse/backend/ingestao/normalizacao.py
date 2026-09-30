"""
normalizacao.py — documento capturado → **operação apurável**.

O QUE ESTA CAMADA É
    O ponto onde "um XML que chegou" vira "um fato que pode entrar numa
    apuração". Ela não calcula imposto, não decide anexo, não segrega receita
    e não conhece Simples, monofásico, ST nem devolução. Ela só coloca NF-e e
    NFS-e no MESMO contrato, sem perder o que cada uma tem de próprio.

POR QUE ELA EXISTE SEPARADA
    Hoje o motor fiscal lê pasta de XML direto, e cada leitor tem o seu
    vocabulário: `valor_servico` num, `valor` no outro; `valor_pis` num,
    `ret_pis` no outro. Enquanto for assim, ligar o acervo novo ao motor
    significaria espalhar mais um dialeto. Aqui existe um só.

    E existe um motivo mais duro: **rastreabilidade** (D44). Nenhum valor
    apurado pode ficar sem caminho de volta ao documento que o originou. Por
    isso `id_documento` e a referência ao XML original são obrigatórios e
    atravessam a normalização inteira — não são metadado opcional.

O QUE ELA NÃO FAZ, E É DE PROPÓSITO
    - Não chama `classificador.py` nem `apuracao_federal.py`.
    - Não substitui os leitores atuais: ela LÊ o que eles devolvem.
    - Não conserta a divergência `rbt12()` × `rbt12_de()` nem a dos leitores.
      As diferenças achadas na APURAÇÃO 1 são **registradas**, não apagadas:
      cada operação diz de qual leitor veio (`fonte_leitor`), para que uma
      conferência futura possa comparar as duas sem adivinhação.

DINHEIRO É `Decimal`, E AUSENTE É `None`
    Mesma regra do resto do pacote: `dinheiro()` recusa `float`. Os leitores
    legados devolvem `float` — a conversão passa por `str()`, explicitamente,
    em `_de_legado()`, para que a decisão fique visível num lugar só.

    `None` é "o documento não informou". Zero é "informou zero". Confundir os
    dois faz uma retenção sumir sem deixar rastro.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import documento as dm
from .documento import dinheiro
from .identidade import normalizar

# ── espécie da operação ─────────────────────────────────────────────────────
NFE = "NFE55"
NFCE = "NFCE65"
NFSE = "NFSE"

# NF-e e NFC-e são o mesmo FATO econômico — venda de mercadoria — em dois
# modelos. Convergem em `vendas.py`, mas cada uma guarda a sua espécie: perder
# o modelo apaga a diferença entre uma venda a consumidor e uma venda a CNPJ,
# que têm tratamento fiscal próprio mais adiante.
ESPECIES_MERCADORIA = (NFE, NFCE)

# ── sentido ─────────────────────────────────────────────────────────────────
ENTRADA = "ENTRADA"
SAIDA = "SAIDA"
SENTIDO_INDEFINIDO = "INDEFINIDO"

# ── natureza ────────────────────────────────────────────────────────────────
# É a natureza DO DOCUMENTO, não a classificação fiscal da receita. NFS-e é
# documento de serviço; NF-e modelo 55 é documento de mercadoria. Dizer isso
# não é aplicar regra tributária — é ler a espécie. A segregação fiscal
# (comércio × indústria × serviço, monofásico, ST) fica para a APURAÇÃO 5.
MERCADORIA = "MERCADORIA"
SERVICO = "SERVICO"
NATUREZA_INDEFINIDA = "INDEFINIDA"

# ── situação ────────────────────────────────────────────────────────────────
NORMAL = "NORMAL"
CANCELADA = "CANCELADA"
SUBSTITUIDA = "SUBSTITUIDA"
SUBSTITUTA = "SUBSTITUTA"
DENEGADA = "DENEGADA"              # cStat 110/301/302/303: nunca valeu
NAO_AUTORIZADA = "NAO_AUTORIZADA"  # sem protocolo de autorização conhecido
SITUACAO_INDEFINIDA = "INDEFINIDA"

# Situações que tiram o documento da receita. **O documento continua existindo**
# — some da base de cálculo, não do acervo, e o motivo fica escrito.
#
# `DENEGADA` e `NAO_AUTORIZADA` entraram na APURAÇÃO 6C. Antes delas, tudo que
# não fosse cancelado virava `NORMAL`, e uma NF-e sem protocolo — ou denegada —
# formava receita. O portão de importação já a barrava (`SEM_PROTOCOLO`), mas
# `vendas.py` lia a operação normalizada e a somava assim mesmo: a barreira
# estava num lugar e a soma no outro. A situação é do DOCUMENTO, então é aqui
# que ela precisa ser dita.
FORA_DA_RECEITA = frozenset({CANCELADA, SUBSTITUIDA, DENEGADA, NAO_AUTORIZADA})

# `doc.situacao` (vocabulário do parser) → situação da operação. O parser já
# distinguia autorizado, denegado e indefinido; a normalização é que jogava
# tudo em `NORMAL`.
_SITUACAO_NFE = {
    dm.AUTORIZADO: NORMAL,
    dm.CANCELADO: CANCELADA,
    dm.DENEGADO: DENEGADA,
}

# ── de qual leitor a operação veio ──────────────────────────────────────────
FONTE_ACERVO = "acervo"                    # o motor ING, documento parseado
FONTE_CORE = "core.carregar_notas"
FONTE_CLASSIFICADOR = "classificador.ler_nfse"


def _de_legado(v, campo: str = "") -> Decimal | None:
    """`float` dos leitores antigos → `Decimal`, passando por `str`.

    `dinheiro()` recusa `float` de propósito, e está certo: `Decimal(0.1)` não
    é 0,1. Mas os leitores atuais devolvem `float`, e negar isso só empurraria
    a conversão para quem chama — espalhada, sem comentário, cada um do seu
    jeito. Aqui ela acontece uma vez, explicitamente, e `str(0.1)` devolve
    exatamente `'0.1'`.

    `None` continua `None`. Zero continua zero.
    """
    if v is None:
        return None
    if isinstance(v, float):
        return dinheiro(str(v), campo)
    return dinheiro(v, campo)


@dataclass(frozen=True)
class Retencoes:
    """Tributos retidos na fonte. `None` = o documento não informou."""
    pis: Decimal | None = None
    cofins: Decimal | None = None
    csll: Decimal | None = None
    irrf: Decimal | None = None
    iss: Decimal | None = None
    # A NFS-e às vezes traz PIS+COFINS+CSLL somados num campo só. Quando o
    # total bate 4,65% da receita, o leitor decompõe pela IN RFB 459/2004 — e
    # isso PRECISA ficar visível, porque um número decomposto não tem o mesmo
    # peso de conferência que um número declarado pelo emitente.
    consolidada: bool = False
    rateada: bool = False

    @property
    def informada(self) -> bool:
        return any(v is not None for v in
                   (self.pis, self.cofins, self.csll, self.irrf, self.iss))


@dataclass(frozen=True)
class ItemOperacao:
    """Um item da NF-e. Estrutura pronta; classificação fiscal NÃO entra aqui."""
    numero: int = 0
    codigo: str = ""
    descricao: str = ""
    ncm: str = ""
    cest: str = ""
    cfop: str = ""
    unidade: str = ""
    quantidade: Decimal | None = None
    valor_unitario: Decimal | None = None
    valor_produto: Decimal | None = None
    cst_icms: str = ""
    cst_pis: str = ""
    cst_cofins: str = ""

    # ── Reforma Tributária (REFORMA 3) ─────────────────────────────────────
    # O GRUPO INTEIRO, não campos soltos. `dm.TributoReforma` já é o tipo do
    # projeto para isto — é o que o parser produz e o que o índice guarda — e
    # reaproveitá-lo evita inventar um terceiro vocabulário para os mesmos 14
    # valores. Os vizinhos (`cst_icms`, `cst_pis`, `cst_cofins`) são planos
    # porque ali só o CST interessava; aqui interessa o grupo todo, incluindo
    # `cClassTrib`, que é o que distingue imunidade de alíquota zero.
    #
    # TRANSPORTE, NÃO APURAÇÃO: o que estava no documento chega aqui igual.
    # Ausente continua `None`; item de CST 410 chega com CST e `cClassTrib` e
    # nenhum valor, que é exatamente como o emitente o mandou.
    reforma: dm.TributoReforma = field(default_factory=dm.TributoReforma)

    @property
    def tem_reforma(self) -> bool:
        return self.reforma.presente


@dataclass(frozen=True)
class OrigemDocumento:
    """Como voltar do número ao papel. É o que sustenta a rastreabilidade."""
    fonte_leitor: str = ""             # acervo | core | classificador
    arquivo: str = ""                  # nome do XML, como está em disco
    caminho: str = ""                  # caminho relativo à raiz de dados
    chave: str = ""
    hash_conteudo: str = ""

    def para_json(self) -> dict:
        return {"fonte_leitor": self.fonte_leitor, "arquivo": self.arquivo or None,
                "caminho": self.caminho or None, "chave": self.chave or None,
                "hash": self.hash_conteudo or None}


@dataclass(frozen=True)
class Operacao:
    """UM documento fiscal pronto para ser somado — ou recusado com motivo.

    Contrato mínimo e obrigatório: `id_documento`, empresa, espécie,
    competência, sentido, natureza, valor bruto, situação e origem. O resto é
    "quando existir", e quando não existe fica `None` ou vazio, nunca zero.
    """
    id_documento: str
    identidade_empresa: str
    especie: str
    origem: OrigemDocumento

    competencia: date | None = None
    data_emissao: date | None = None
    sentido: str = SENTIDO_INDEFINIDO
    natureza: str = NATUREZA_INDEFINIDA
    valor_bruto: Decimal | None = None

    situacao: str = SITUACAO_INDEFINIDA
    chave_substituta: str = ""

    numero: str = ""
    serie: str = ""
    emitente: str = ""                 # só dígitos
    contraparte: str = ""

    # NF-e
    itens: tuple[ItemOperacao, ...] = field(default_factory=tuple)
    natureza_operacao: str = ""        # o texto do XML (natOp), sem interpretar

    # NFS-e
    codigo_servico: str = ""           # cTribNac, como veio
    item_lc116: str = ""               # já derivado pelo leitor, quando houver
    municipio_incidencia: str = ""

    retencoes: Retencoes = field(default_factory=Retencoes)
    avisos: tuple[str, ...] = field(default_factory=tuple)
    # Campos que a normalização NÃO conseguiu determinar. Existir aqui é melhor
    # do que sair um valor inventado: quem consumir sabe o que precisa perguntar.
    indeterminados: tuple[str, ...] = field(default_factory=tuple)

    # ── leitura ────────────────────────────────────────────────────────────
    @property
    def entra_na_receita(self) -> bool:
        """Sai da base quem foi cancelado ou substituído. Nada é apagado."""
        return self.situacao not in FORA_DA_RECEITA

    @property
    def e_servico(self) -> bool:
        return self.natureza == SERVICO

    def para_json(self) -> dict:
        """Sem CNPJ inteiro e sem conteúdo de documento — igual ao resto."""
        return {
            "id_documento": self.id_documento,
            "empresa": normalizar(self.identidade_empresa).mascarado(),
            "especie": self.especie,
            "competencia": self.competencia.isoformat() if self.competencia else None,
            "data_emissao": self.data_emissao.isoformat() if self.data_emissao else None,
            "sentido": self.sentido,
            "natureza": self.natureza,
            "valor_bruto": str(self.valor_bruto) if self.valor_bruto is not None else None,
            "situacao": self.situacao,
            "entra_na_receita": self.entra_na_receita,
            "chave_substituta": self.chave_substituta or None,
            "itens": len(self.itens),
            "codigo_servico": self.codigo_servico or None,
            "item_lc116": self.item_lc116 or None,
            "retencoes": {
                "pis": str(self.retencoes.pis) if self.retencoes.pis is not None else None,
                "cofins": str(self.retencoes.cofins) if self.retencoes.cofins is not None else None,
                "csll": str(self.retencoes.csll) if self.retencoes.csll is not None else None,
                "irrf": str(self.retencoes.irrf) if self.retencoes.irrf is not None else None,
                "iss": str(self.retencoes.iss) if self.retencoes.iss is not None else None,
                "consolidada": self.retencoes.consolidada,
                "rateada": self.retencoes.rateada,
            },
            "origem": self.origem.para_json(),
            "avisos": list(self.avisos),
            "indeterminados": list(self.indeterminados),
        }


# ════════════════════════════════════════════════════════════════════════════
# NF-e — do acervo novo
# ════════════════════════════════════════════════════════════════════════════
def _data(dt) -> date | None:
    if isinstance(dt, datetime):
        return dt.date()
    return dt if isinstance(dt, date) else None


def _sentido_da_nfe(doc: dm.Documento, ext) -> tuple[str, tuple]:
    """Entrada ou saída **na perspectiva DA EMPRESA**, sem regra fiscal.

    ERRO QUE ESTA FUNÇÃO JÁ COMETEU
        A primeira versão lia `tpNF` primeiro. `tpNF` é campo do documento —
        0 entrada, 1 saída — mas na perspectiva de **quem emitiu**. Como a
        Distribuição DF-e entrega justamente os documentos que a empresa NÃO
        gerou, quase toda nota do acervo chega com `tpNF=1`: é saída do
        FORNECEDOR, e entrada nossa. O resultado foi 301 compras contadas
        como saídas, e um relatório meu afirmando "300 saídas" onde havia
        zero. Ver decisão D2: o `NFeDistribuicaoDFe` entrega o que a empresa
        NÃO gerou — nunca as NF-e de emissão própria.

    A ORDEM CORRETA
        Quem manda é o PAPEL da empresa no documento. `tpNF` só é consultado
        quando a própria empresa emitiu — aí as duas perspectivas coincidem, e
        ele ainda distingue uma devolução de saída.

        Sem papel determinado (o resumo `resNFe` não traz destinatário), o
        sentido fica INDEFINIDO e é declarado. Um documento de terceiro sem
        papel conhecido não é saída da empresa: no máximo não se sabe.
    """
    emit = normalizar(doc.emitente.identificador)
    empresa = normalizar(doc.identidade_empresa)
    tp = getattr(ext, "tipo_operacao", "") if ext else ""

    if doc.papel == dm.EMITENTE:
        # A empresa emitiu. Agora sim `tpNF` fala por ela.
        if tp == "0":
            return ENTRADA, ()
        return SAIDA, ()
    if doc.papel == dm.DESTINATARIO:
        return ENTRADA, ()
    # Papel indeterminado. O emitente ainda pode resolver.
    if emit.valido and empresa.valido:
        if emit.valor == empresa.valor:
            return (ENTRADA if tp == "0" else SAIDA), ()
        return ENTRADA, ()      # emitida por terceiro: não é saída nossa
    return SENTIDO_INDEFINIDO, ("sentido",)


def de_documento_nfe(doc: dm.Documento, *, caminho: str = "",
                     hash_conteudo: str = "",
                     situacao: str = "") -> Operacao:
    """`Documento` do acervo (NF-e 55) → `Operacao`.

    `situacao` permite ao chamador informar o que só se sabe olhando os
    EVENTOS daquela chave (cancelamento). A normalização não vai atrás deles
    sozinha: quem tem o índice é quem sabe, e inventar aqui seria duplicar a
    regra de cancelamento num segundo lugar.
    """
    ext = doc.extensao if isinstance(doc.extensao, dm.ExtensaoNFe) else None
    sentido, indef = _sentido_da_nfe(doc, ext)

    itens = tuple(
        ItemOperacao(
            numero=i.numero, codigo=i.codigo, descricao=i.descricao,
            ncm=i.ncm, cest=i.cest, cfop=i.cfop, unidade=i.unidade,
            quantidade=i.quantidade, valor_unitario=i.valor_unitario,
            valor_produto=i.valor_produto,
            cst_icms=i.icms.cst, cst_pis=i.pis.cst, cst_cofins=i.cofins.cst,
            # REFORMA 3 — o grupo vem do parser inteiro, sem tocar em nada.
            # Documento sem Reforma traz um `TributoReforma()` vazio, que é o
            # mesmo que o item já tinha: nenhum valor inventado.
            reforma=i.reforma,
        ) for i in (ext.itens if ext else ()))

    avisos = list(doc.avisos)
    indeterminados = list(indef)
    if not itens:
        # `resNFe` (resumo) não traz itens. Não é NF-e sem produto: é NF-e cujo
        # detalhe ainda não chegou. Dizer isso evita apuração de item vazia.
        indeterminados.append("itens")
        avisos.append("documento sem itens — resumo, não o XML completo")
    if doc.valor_total is None:
        indeterminados.append("valor_bruto")

    # A NF-e 55 é documento de MERCADORIA. Isso é a espécie do documento, não
    # a classificação fiscal da receita — que depende de CFOP e vem depois.
    return Operacao(
        id_documento=doc.id_documento,
        identidade_empresa=doc.identidade_empresa,
        especie=doc.especie if doc.especie in ESPECIES_MERCADORIA else NFE,
        origem=OrigemDocumento(fonte_leitor=FONTE_ACERVO,
                               arquivo=Path(caminho).name if caminho else "",
                               caminho=caminho, chave=doc.chave,
                               hash_conteudo=hash_conteudo),
        competencia=doc.competencia,
        data_emissao=_data(doc.dh_emissao),
        sentido=sentido,
        natureza=MERCADORIA,
        valor_bruto=doc.valor_total,
        situacao=situacao or _SITUACAO_NFE.get(doc.situacao, NAO_AUTORIZADA),
        numero=doc.numero, serie=doc.serie,
        emitente=doc.emitente.identificador,
        contraparte=doc.contraparte.identificador,
        itens=itens,
        natureza_operacao=(ext.natureza_operacao if ext else ""),
        retencoes=Retencoes(),      # NF-e 55 não traz retenção de serviço
        avisos=tuple(avisos),
        indeterminados=tuple(indeterminados),
    )


# ── do ÍNDICE, sem reabrir o XML (REFORMA 3) ───────────────────────────────
# `de_documento_nfe` recebe um `dm.Documento`, e hoje quem o produz reabre o
# XML do acervo e reinterpreta (ver `conferencia.operacoes_nfe`). Para o item,
# isso deixou de ser necessário: desde a REFORMA 2 o índice guarda o grupo da
# Reforma inteiro, e as funções abaixo o transportam direto de lá.
#
# POR QUE SÓ O ITEM, E NÃO A `Operacao` COMPLETA
#     `Operacao` exige `sentido`, e o sentido sai de `_sentido_da_nfe`, que
#     precisa do `tpNF` quando a própria empresa emitiu. **O índice não guarda
#     `tpNF`** (conferido: nenhuma coluna). Montar a operação só com o que está
#     indexado obrigaria a adivinhar o sentido — exatamente o erro que custou
#     301 compras contadas como saídas, documentado em `_sentido_da_nfe`.
#     Então: o item vem do índice; a operação continua vindo do documento.
FONTE_INDICE = "indice"


def _valor_do_indice(bloco: dict, campo: str) -> Decimal | None:
    """TEXT do SQLite → `Decimal`. Coluna ausente ou vazia continua `None`."""
    return _de_legado((bloco.get(campo) or None), campo)


def reforma_do_indice(bloco: dict) -> dm.TributoReforma:
    """O bloco `reforma` do índice → `TributoReforma`.

    Espera o formato que `consulta.itens_do_documento()` devolve. Índice
    antigo, sem as colunas, entrega o bloco vazio — e aí sai um
    `TributoReforma()` sem nada, que é o correto: o dado não existe, não é
    zero.
    """
    if not bloco:
        return dm.TributoReforma()
    return dm.TributoReforma(
        cst=bloco.get("reforma_cst") or "",
        classificacao_tributaria=bloco.get("reforma_classificacao") or "",
        base=_valor_do_indice(bloco, "reforma_base"),
        aliquota_ibs_uf=_valor_do_indice(bloco, "ibs_uf_aliquota"),
        valor_ibs_uf=_valor_do_indice(bloco, "ibs_uf_valor"),
        aliquota_ibs_mun=_valor_do_indice(bloco, "ibs_mun_aliquota"),
        valor_ibs_mun=_valor_do_indice(bloco, "ibs_mun_valor"),
        valor_ibs=_valor_do_indice(bloco, "ibs_valor"),
        aliquota_cbs=_valor_do_indice(bloco, "cbs_aliquota"),
        valor_cbs=_valor_do_indice(bloco, "cbs_valor"),
        cst_is=bloco.get("is_cst") or "",
        base_is=_valor_do_indice(bloco, "is_base"),
        aliquota_is=_valor_do_indice(bloco, "is_aliquota"),
        valor_is=_valor_do_indice(bloco, "is_valor"),
    )


def de_item_indice(linha: dict) -> ItemOperacao:
    """Uma linha de item do índice → `ItemOperacao`. Não toca em disco.

    Transporte puro: nada é calculado, nada é completado e nada vira zero.
    """
    linha = linha or {}
    try:
        numero = int(linha.get("numero") or 0)
    except (TypeError, ValueError):
        numero = 0
    return ItemOperacao(
        numero=numero,
        codigo=linha.get("codigo") or "",
        descricao=linha.get("descricao") or "",
        ncm=linha.get("ncm") or "",
        cest=linha.get("cest") or "",
        cfop=linha.get("cfop") or "",
        unidade=linha.get("unidade") or "",
        quantidade=_valor_do_indice(linha, "quantidade"),
        valor_unitario=_valor_do_indice(linha, "valor_unitario"),
        valor_produto=_valor_do_indice(linha, "valor_produto"),
        cst_icms=(linha.get("icms") or {}).get("icms_cst") or "",
        cst_pis=(linha.get("pis") or {}).get("pis_cst") or "",
        cst_cofins=(linha.get("cofins") or {}).get("cofins_cst") or "",
        reforma=reforma_do_indice(linha.get("reforma") or {}),
    )


def itens_do_indice(linhas) -> tuple[ItemOperacao, ...]:
    """Os itens de UM documento, na ordem em que o índice os devolveu.

    A correspondência item a item é a do próprio índice, que ordena por
    `numero` — não se reordena nada aqui.
    """
    return tuple(de_item_indice(linha) for linha in (linhas or ()))


def com_itens_do_indice(op: Operacao, linhas) -> Operacao:
    """A MESMA operação, com os itens vindos do índice.

    Existe para o caso em que a operação já foi montada (com o sentido que só
    o documento sabe dar) e os itens podem vir do índice, sem reabrir o XML.
    `id_documento`, origem, chave e hash atravessam intactos: é `replace`,
    não uma operação nova.
    """
    return replace(op, itens=itens_do_indice(linhas))


# ════════════════════════════════════════════════════════════════════════════
# NFS-e — dos leitores atuais, sem substituí-los
# ════════════════════════════════════════════════════════════════════════════
_SITUACAO_CORE = {"Cancelada": CANCELADA, "Substituída": SUBSTITUIDA,
                  "Substituta": SUBSTITUTA, "Normal": NORMAL}


def _competencia_de(texto) -> date | None:
    t = str(texto or "")[:7]
    if len(t) != 7 or t[4] != "-":
        return None
    try:
        return date(int(t[:4]), int(t[5:7]), 1)
    except ValueError:
        return None


def _data_de(texto) -> date | None:
    t = str(texto or "")[:10]
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


def de_nota_core(n: dict, identidade_empresa: str, *,
                 caminho: str = "") -> Operacao:
    """Nota de `core.carregar_notas()` → `Operacao`.

    Este leitor conhece as TRÊS situações (cancelada, substituída, substituta)
    e o rateio da CSRF. É o mais rico em situação — e é o que a apuração
    federal usa hoje."""
    empresa = normalizar(identidade_empresa)
    emit = str(n.get("emit_cnpj") or "")
    sentido = SAIDA if (empresa.valido and emit == empresa.valor) else ENTRADA

    situacao = _SITUACAO_CORE.get(str(n.get("situacao") or ""), "")
    if not situacao:
        situacao = CANCELADA if n.get("cancelada") else SITUACAO_INDEFINIDA

    indeterminados = []
    if n.get("competencia") in (None, ""):
        indeterminados.append("competencia")
    if n.get("valor_servico") in (None, ""):
        indeterminados.append("valor_bruto")

    ret = Retencoes(
        pis=_de_legado(n.get("valor_pis"), "valor_pis"),
        cofins=_de_legado(n.get("valor_cofins"), "valor_cofins"),
        csll=_de_legado(n.get("valor_csll"), "valor_csll"),
        irrf=_de_legado(n.get("valor_irrf"), "valor_irrf"),
        iss=_de_legado(n.get("valor_iss_retido"), "valor_iss_retido"),
        consolidada=bool(n.get("valor_csll") and not n.get("valor_pis")
                         and not n.get("retencao_rateada")),
        rateada=bool(n.get("retencao_rateada")),
    )
    return Operacao(
        id_documento=_id_nfse(n.get("chave") or ""),
        identidade_empresa=empresa.valor if empresa.valido else str(identidade_empresa),
        especie=NFSE,
        origem=OrigemDocumento(fonte_leitor=FONTE_CORE,
                               arquivo=str(n.get("arquivo") or ""),
                               caminho=caminho, chave=str(n.get("chave") or "")),
        competencia=_competencia_de(n.get("competencia")),
        data_emissao=_data_de(n.get("data_emissao")),
        sentido=sentido,
        natureza=SERVICO,
        valor_bruto=_de_legado(n.get("valor_servico"), "valor_servico"),
        situacao=situacao,
        numero=str(n.get("numero") or ""),
        emitente=emit,
        contraparte=str(n.get("toma_doc") or ""),
        retencoes=ret,
        indeterminados=tuple(indeterminados),
    )


def de_nota_classificador(n: dict, identidade_empresa: str, *,
                          caminho: str = "") -> Operacao:
    """Nota de `classificador.ler_nfse()` → `Operacao`.

    Este leitor já devolve o item da LC 116 e o município de incidência, que o
    core não traz — e conhece a retenção pelo nome `ret_*`. Em compensação, só
    tem o booleano `cancelada`: ele NÃO distingue substituição de cancelamento
    (ver APURAÇÃO 1). A operação registra isso como indeterminado em vez de
    escolher um dos dois."""
    empresa = normalizar(identidade_empresa)
    indeterminados = []
    if n.get("cancelada"):
        # Sai da receita, sim — mas não dá para dizer POR QUE só com este leitor.
        situacao = CANCELADA
        indeterminados.append("motivo_da_exclusao")
    else:
        situacao = NORMAL

    ret = Retencoes(
        pis=_de_legado(n.get("ret_pis"), "ret_pis"),
        cofins=_de_legado(n.get("ret_cofins"), "ret_cofins"),
        csll=_de_legado(n.get("ret_csll"), "ret_csll"),
        irrf=_de_legado(n.get("ret_irrf"), "ret_irrf"),
        iss=_de_legado(n.get("valor_iss"), "valor_iss") if n.get("iss_retido") else None,
        consolidada=bool(n.get("ret_consolidado")),
        rateada=bool(n.get("ret_rateado")),
    )
    return Operacao(
        id_documento=_id_nfse(n.get("chave") or ""),
        identidade_empresa=empresa.valor if empresa.valido else str(identidade_empresa),
        especie=NFSE,
        origem=OrigemDocumento(fonte_leitor=FONTE_CLASSIFICADOR,
                               arquivo=str(n.get("arquivo") or ""),
                               caminho=caminho, chave=str(n.get("chave") or "")),
        competencia=_competencia_de(n.get("competencia")),
        data_emissao=_data_de(n.get("data")),
        sentido=SAIDA,      # `ler_nfse` já devolve só as EMITIDAS pela empresa
        natureza=SERVICO,
        valor_bruto=_de_legado(n.get("valor"), "valor"),
        situacao=situacao,
        numero=str(n.get("numero") or ""),
        emitente=empresa.valor if empresa.valido else "",
        contraparte="",     # o classificador traz o NOME do tomador, não o doc
        codigo_servico=str(n.get("ctribnac") or ""),
        item_lc116=str(n.get("item_lc116") or ""),
        municipio_incidencia=str(n.get("municipio") or ""),
        retencoes=ret,
        indeterminados=tuple(indeterminados),
    )


def _id_nfse(chave: str) -> str:
    """Identidade da NFS-e para a operação.

    A NFS-e ainda não passa pelo acervo endereçado por conteúdo, então não há
    `id_documento` calculado para ela. Usar a CHAVE como identidade é o mais
    honesto disponível hoje: é estável, vem do documento e não é inventada.
    Quando a NFS-e entrar no acervo, esta função passa a devolver o
    `id_documento` de lá — e o contrato de fora não muda.
    """
    import hashlib
    ch = "".join(c for c in str(chave or "") if c.isdigit())
    if not ch:
        return ""
    return hashlib.sha256(f"{NFSE}|{ch}".encode("utf-8")).hexdigest()[:32]


# ════════════════════════════════════════════════════════════════════════════
# Conferência entre os dois leitores — sem unificar nenhum deles
# ════════════════════════════════════════════════════════════════════════════
CAMPOS_COMPARAVEIS = ("competencia", "valor_bruto", "situacao", "sentido",
                      "numero")


def comparar(a: Operacao, b: Operacao) -> dict:
    """Onde duas operações do MESMO documento divergem.

    Existe para a APURAÇÃO 3 poder provar igualdade antes de trocar de leitor.
    Não corrige nada, não escolhe um lado: devolve a lista de diferenças."""
    if a.id_documento != b.id_documento:
        return {"mesmo_documento": False, "diferencas": ["id_documento"]}
    difs = []
    for campo in CAMPOS_COMPARAVEIS:
        va, vb = getattr(a, campo), getattr(b, campo)
        if va != vb:
            difs.append(campo)
    for campo in ("pis", "cofins", "csll", "irrf", "iss"):
        if getattr(a.retencoes, campo) != getattr(b.retencoes, campo):
            difs.append(f"retencoes.{campo}")
    return {"mesmo_documento": True, "diferencas": difs,
            "fontes": [a.origem.fonte_leitor, b.origem.fonte_leitor]}


def com_situacao(op: Operacao, situacao: str, chave_substituta: str = "") -> Operacao:
    """Devolve uma cópia com a situação resolvida por quem conhece os eventos.

    `frozen=True` não é capricho: a operação normalizada é um fato, e fato não
    se edita no meio do caminho. Quem precisa mudar, cria outra e diz por quê."""
    return replace(op, situacao=situacao,
                   chave_substituta=chave_substituta or op.chave_substituta)
