"""
consulta.py — a leitura oficial do acervo indexado. Local, e só local.

O QUE ESTA CAMADA É
    A porta de leitura para quem quer perguntar "quais documentos desta empresa,
    neste período, com este filtro". Ela responde do índice SQLite da ingestão —
    e de mais lugar nenhum.

O QUE ELA NÃO PODE FAZER, E POR QUÊ ISSO É O PONTO
    Não abre socket. Não importa `requests`, `http`, `socket`, nem os conectores.
    Não conhece NSU, `distNSU`, `consNSU` nem `consChNFe`. Não lê pasta legada
    de XML solto. Não chama `nfe.carregar_nfe()`.

    A razão é a regra mais importante do módulo NF-e, e ela é fácil de violar
    sem perceber: **NSU não é filtro de data**. A tentação natural, quando
    alguém pede "notas de julho", é traduzir isso em consulta ao Ambiente
    Nacional. Não é. A captura é sequencial por NSU e governada pelo checkpoint;
    "julho" é uma pergunta sobre o que **já está** no acervo.

    Se um dia a interface conseguir transformar um seletor de mês numa chamada à
    SEFAZ, terá sido porque esta camada deixou. Por isso a suíte da NF-e 2 varre
    o texto deste arquivo cobrando a ausência desses nomes: a guarda é sobre o
    código, não sobre a boa intenção de quem o escreve.

O QUE ELA NÃO É
    Não é apuração. Não soma imposto, não decide anexo, não classifica receita e
    não diz o que é compra. Devolve documento com os campos que o índice guarda.
    Quem interpreta é a normalização, o `vendas.py` e — no futuro — a
    Inteligência Fiscal.

DATAS: QUATRO COISAS DIFERENTES
    O pedido da NF-e 2 é explícito em não tratá-las como equivalentes. O que o
    índice hoje sabe responder:

        EMISSAO      `dh_emissao`  — quando o emitente emitiu. **Disponível.**
        COMPETENCIA  `competencia` — primeiro dia do mês da emissão. **Disponível.**
        ENTRADA      `dh_saida_entrada` — o `dhSaiEnt` da NF-e. **Disponível
                     desde a NF-e 6.** O schema o marca como opcional, e neste
                     acervo ele vem em 99,5% das NF-e completas (7.498 de
                     7.534, medido em 23/08/2026). Filtrar por ele **exclui**
                     os 0,5% que não o informaram — o que é a resposta certa à
                     pergunta feita, e ainda assim precisa estar dito na tela.
                     RESUMO (`resNFe`) nunca o traz: o resumo não tem `ide`.
        CAPTURA      quando o documento chegou ao FISCALE. **NÃO EXISTE no
                     índice** — está no `captura.json` do acervo, que é arquivo,
                     não tabela.

    Pedir `CAPTURA` levanta `CampoDeDataIndisponivel` com o motivo. Devolver
    silenciosamente a emissão no lugar seria pior do que recusar: o usuário
    acharia que filtrou por captura.

    **O padrão continua sendo `EMISSAO`**, porque é o único campo que existe
    para todos os documentos, inclusive para o resumo `resNFe`.

PAPEL: CINCO NOMES, TRÊS RESPOSTAS
    O contrato declara os cinco papéis que o módulo precisa
    (`DESTINATARIO`, `EMITENTE`, `TRANSPORTADOR`, `AUTXML`, `OUTRO`), mas o
    índice de hoje só distingue três: o parser colapsa transportador e
    autorizado em `TERCEIRO` (`parsers.py:_papel`).

    Isso não é detalhe. O diagnóstico da NF-e 1 mostrou que, nas duas
    transportadoras do escritório, **93% dos XML completos são frete de
    terceiros** — nota em que a empresa é só a transportadora. Um filtro que
    respondesse "TRANSPORTADOR: nenhum" estaria mentindo sobre 10 mil documentos.

    Por isso filtrar por `TRANSPORTADOR` ou `AUTXML` levanta
    `PapelIndisponivel`, em vez de devolver lista vazia. **Pendência declarada
    da NF-e 4.**

    E a inferência proibida, escrita para não ser reinventada:
    **XML completo ≠ compra.** O transportador recebe o documento completo sem
    manifestar; o destinatário, não. Confundir os dois é o erro que essa regra
    existe para impedir.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import documento as dm
from .identidade import normalizar
from .indice import ARQUIVO, PASTA

# ── papéis ──────────────────────────────────────────────────────────────────
# Os cinco são respondíveis desde a NF-e 4A: o parser passou a ler `transporta`
# e `autXML`, que existiam no schema e não eram olhados. Antes disso,
# transportador e autorizado colapsavam em `TERCEIRO` — e nas transportadoras
# do escritório isso era a maioria do acervo.
DESTINATARIO = dm.DESTINATARIO
EMITENTE = dm.EMITENTE
TRANSPORTADOR = dm.TRANSPORTADOR
AUTXML = dm.AUTXML
OUTRO = dm.OUTRO
PAPEIS = dm.PAPEIS

# ── CT-e: os papéis que só o conhecimento de transporte tem ─────────────────
# `PAPEIS` continua sendo o da NF-e, porque quem pergunta "quais papéis de nota
# existem" não quer TOMADOR na resposta. `PAPEIS_TODOS` é a união, e é ela que
# a validação usa: um filtro por TOMADOR precisa ser aceito, e um por um nome
# inventado precisa continuar sendo recusado.
REMETENTE = dm.REMETENTE
EXPEDIDOR = dm.EXPEDIDOR
RECEBEDOR = dm.RECEBEDOR
TOMADOR = dm.TOMADOR
PAPEIS_CTE = dm.PAPEIS_CTE
PAPEIS_TODOS = tuple(dict.fromkeys(dm.PAPEIS + dm.PAPEIS_CTE))

PAPEIS_RESPONDIVEIS = dm.PAPEIS
PAPEIS_PENDENTES: tuple[str, ...] = ()

# Índice gravado antes da NF-e 4A usa `TERCEIRO` onde hoje se escreve `OUTRO`.
# A leitura aceita os dois; a escrita só produz o nome novo.
_PAPEL_LEGADO = {dm.TERCEIRO: OUTRO}

# ── campos de data ──────────────────────────────────────────────────────────
EMISSAO = "EMISSAO"
COMPETENCIA = "COMPETENCIA"
ENTRADA = "ENTRADA"
CAPTURA = "CAPTURA"
CAMPOS_DATA = (EMISSAO, COMPETENCIA, ENTRADA, CAPTURA)
CAMPOS_DATA_DISPONIVEIS = (EMISSAO, COMPETENCIA, ENTRADA)
CAMPO_DATA_PADRAO = EMISSAO

# Coluna do índice por campo de data. Fechado de propósito: é o que impede um
# nome vindo da URL de virar pedaço de SQL.
_COLUNA_DATA = {
    EMISSAO: "d.dh_emissao",
    COMPETENCIA: "d.competencia",
    ENTRADA: "d.dh_saida_entrada",
}

# O que filtrar por ENTRADA custa, dito como dado para a tela poder avisar.
ADVERTENCIA_ENTRADA = (
    "`dhSaiEnt` é campo opcional da NF-e: documentos que não o informaram "
    "ficam de fora deste filtro — inclusive TODOS os que estão no acervo "
    "apenas como resumo. Ausência aqui é ausência no documento, não no "
    "FISCALE.")

_MOTIVO_DATA = {
    CAPTURA: ("a data de captura mora no `captura.json` do acervo, que é arquivo "
              "e não tabela; indexá-la continua pendente"),
}

# ── conteúdo: resumo × completo ─────────────────────────────────────────────
RESUMO = "RESUMO"
COMPLETO = "COMPLETO"
# O parser marca o resumo da distribuição com este `versao_schema`
# (`parsers.py:_resumo`). Qualquer outro valor veio de um XML com `infNFe`.
_SCHEMA_RESUMO = "resNFe"

# ── situação: a do documento e a de HOJE ────────────────────────────────────
# `situacao` (coluna do índice) é o que o DOCUMENTO diz de si mesmo: veio do
# `protNFe` (cStat 100 → AUTORIZADO) ou do `cSitNFe` do resumo. É evidência
# histórica e não se mexe nela.
#
# `situacao_atual` é derivada: documento + eventos válidos. Uma nota autorizada
# com evento de cancelamento homologado está CANCELADA hoje, e era o que a
# NF-e 3 encontrou — 6 documentos AUTORIZADO no índice com cancelamento no
# acervo.
#
# POR QUE DERIVAR NA CONSULTA E NÃO PERSISTIR
#     Estado derivado persistido fica velho quando o evento chega depois do
#     documento — e chega, porque a distribuição entrega em ordem de NSU, não
#     de causalidade. Um `situacao_atual` gravado exigiria recalcular a cada
#     evento novo, e esquecer de recalcular é silencioso. Derivado na leitura é
#     sempre correto, ao custo de um EXISTS que o `ix_ev_chave` já cobre.
#
# O CRITÉRIO É O MESMO DO ACERVO
#     `documento.e_cancelamento` é a autoridade. O SQL abaixo é a tradução
#     dela, e `teste_nfe4` compara os dois caminhos documento a documento.
_TP_CANCELAMENTO = tuple(sorted(dm.CANCELAMENTOS))

# `protocolo` não nulo e não vazio: cancelamento sem protocolo é pedido, não
# homologação — mesma exigência de `e_cancelamento(..., exigir_protocolo=True)`.
_EXISTE_CANCELAMENTO = (
    "EXISTS (SELECT 1 FROM eventos ev WHERE ev.chave = d.chave"
    "        AND ev.tp_evento IN (%s)"
    "        AND ev.protocolo IS NOT NULL AND TRIM(ev.protocolo) <> '')"
    % ", ".join("?" * len(_TP_CANCELAMENTO)))

# A expressão que devolve a situação de hoje, para o SELECT.
_SQL_SITUACAO_ATUAL = f"CASE WHEN {_EXISTE_CANCELAMENTO} THEN '{dm.CANCELADO}' "                       f"ELSE COALESCE(d.situacao, '') END"

ORDENS = {
    "emissao": "dh_emissao",
    "competencia": "competencia",
    "valor": "CAST(COALESCE(valor_total, '0') AS REAL)",
    "numero": "CAST(COALESCE(numero, '0') AS INTEGER)",
    "chave": "chave",
    "emitente": "emitente_nome",
    # Ordenar por saída/entrada põe os NULL num extremo — é o comportamento do
    # SQLite e é o honesto: quem não informou não tem posição na fila.
    "entrada": "dh_saida_entrada",
}
ORDEM_PADRAO = "emissao"
TAMANHO_PADRAO = 50
TAMANHO_MAXIMO = 500


class ErroConsulta(Exception):
    """Base. Recusar com motivo é melhor que responder errado em silêncio."""


class PapelIndisponivel(ErroConsulta):
    pass


class CampoDeDataIndisponivel(ErroConsulta):
    pass


class OrdenacaoInvalida(ErroConsulta):
    pass


class IndiceIlegivel(ErroConsulta):
    """O banco existe mas não abre. Não é o mesmo que não existir."""


# ════════════════════════════════════════════════════════════════════════════
#  Filtro
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Filtro:
    """Tudo opcional. Filtro vazio é 'todos os documentos desta empresa'.

    `especies` traz só notas por padrão: evento **não é** documento de compra
    nem de venda, e listá-los junto foi identificado na NF-e 1 como o caminho
    mais curto para uma tela ilegível — 98% dos eventos do acervo são Registro
    de Passagem. Quem quer evento chama `eventos_da_chave()`.
    """
    # O padrão continua sendo NOTA. Quem quer CT-e pede CT-e: misturar as duas
    # espécies por omissão faria a tela de NF-e somar frete emitido junto com
    # compra, que é o erro que a NF-e 4 gastou uma fase inteira para desfazer.
    especies: tuple[str, ...] = (dm.NFE55, dm.NFCE65)

    # período — sempre LOCAL
    campo_data: str = CAMPO_DATA_PADRAO
    data_de: date | None = None
    data_ate: date | None = None

    papel: str = ""
    situacoes: tuple[str, ...] = ()
    conteudo: str = ""            # RESUMO | COMPLETO | "" (ambos)

    numero: str = ""
    serie: str = ""
    chave: str = ""
    emitente: str = ""            # CNPJ/CPF ou parte do nome
    destinatario: str = ""        # idem, sobre a contraparte
    documento: str = ""           # CNPJ/CPF em qualquer dos dois lados
    uf: str = ""

    # ── filtros que passam pelos ITENS ──────────────────────────────────
    cfop: str = ""
    ncm: str = ""
    cest: str = ""
    # CST e CSOSN são filtros SEPARADOS, porque são campos separados. Pedir
    # `cst="102"` não pode devolver quem tem CSOSN 102 — é outra coisa.
    icms_cst: str = ""
    icms_csosn: str = ""
    origem: str = ""
    pis_cst: str = ""
    cofins_cst: str = ""
    ipi_cst: str = ""
    # A Reforma chegou nos XML: dá para perguntar quem já traz IBS/CBS.
    com_reforma: bool | None = None

    # ── filtros que passam pelo CT-e ────────────────────────────────────
    # Ficam separados dos de item de propósito: um CT-e não tem item, e um
    # filtro de NCM aplicado a frete devolveria vazio sem dizer por quê.
    modal: str = ""               # 01 rodoviário, 02 aéreo, 03 aquaviário…
    uf_inicio: str = ""
    uf_fim: str = ""
    tomador_papel: str = ""       # REMETENTE, EXPEDIDOR, RECEBEDOR, DESTINATARIO
    cte_cst_icms: str = ""
    com_icms: bool | None = None  # o frete destacou ICMS?

    # A PONTE. `chave_nfe` responde "que frete levou esta nota"; `com_nfe`
    # separa o CT-e que declara carga conhecida do que não declara.
    chave_nfe: str = ""
    com_nfe: bool | None = None

    com_evento: bool | None = None
    cancelada: bool | None = None      # pela SITUAÇÃO ATUAL, não pela do documento
    situacoes_atuais: tuple[str, ...] = ()
    quarentena: bool | None = None
    texto: str = ""               # livre: número, chave, nome, código do item

    def com(self, **mudancas) -> "Filtro":
        from dataclasses import replace
        return replace(self, **mudancas)


@dataclass(frozen=True)
class Documento:
    """O que a consulta devolve. Espelha o índice, sem interpretar."""
    id_documento: str
    especie: str
    chave: str = ""
    numero: str = ""
    serie: str = ""
    papel: str = OUTRO            # o principal, por precedência
    papeis: tuple[str, ...] = ()  # TODOS — a empresa pode ter mais de um
    papel_no_indice: str = ""
    emitente: str = ""
    emitente_nome: str = ""
    contraparte: str = ""
    contraparte_nome: str = ""
    dh_emissao: str = ""
    dh_saida_entrada: str = ""   # `dhSaiEnt`; vazio = o documento não informou
    competencia: str = ""
    valor_total: Decimal | None = None
    # REFORMA 2 — o total declarado no `IBSCBSTot`, vindo do índice. `None` =
    # o documento não informou; índice antigo, sem as colunas, também devolve
    # `None` — em nenhum dos dois casos o número existe para ser mostrado.
    total_base_ibs_cbs: Decimal | None = None
    total_ibs: Decimal | None = None
    total_cbs: Decimal | None = None
    situacao: str = ""            # o que o DOCUMENTO diz de si mesmo
    situacao_atual: str = ""      # documento + eventos válidos
    cancelada: bool = False       # atalho de `situacao_atual == CANCELADO`
    conteudo: str = ""            # RESUMO | COMPLETO
    versao_schema: str = ""
    estado: str = ""
    quarentena: bool = False
    eventos: int = 0

    @property
    def tem_total_reforma(self) -> bool:
        """O documento declarou o total da Reforma?"""
        return (self.total_base_ibs_cbs is not None or self.total_ibs is not None
                or self.total_cbs is not None)

    def para_json(self) -> dict:
        d = self.__dict__.copy()
        for campo in ("valor_total", "total_base_ibs_cbs", "total_ibs",
                      "total_cbs"):
            v = getattr(self, campo)
            d[campo] = None if v is None else str(v)
        return d


@dataclass(frozen=True)
class Pagina:
    itens: tuple[Documento, ...]
    total: int
    pagina: int
    tamanho: int
    ordenar_por: str
    direcao: str
    filtro: Filtro | None = None

    @property
    def paginas(self) -> int:
        return 0 if not self.tamanho else -(-self.total // self.tamanho)

    @property
    def tem_proxima(self) -> bool:
        return self.pagina < self.paginas

    def resumo(self) -> dict:
        return {"total": self.total, "pagina": self.pagina, "tamanho": self.tamanho,
                "paginas": self.paginas, "nesta_pagina": len(self.itens),
                "ordenar_por": self.ordenar_por, "direcao": self.direcao}


# ════════════════════════════════════════════════════════════════════════════
#  Conexão — SOMENTE LEITURA, e nunca cria nada
# ════════════════════════════════════════════════════════════════════════════
def caminho_do_indice(dados_dir, identidade) -> Path:
    """Sempre derivado da identidade canônica.

    É o isolamento por empresa: não existe caminho aqui que não nasça de um
    CNPJ/CPF validado. Um parâmetro de URL adulterado não tem como escapar da
    pasta da empresa porque a pasta É o identificador normalizado.
    """
    ident = normalizar(identidade)
    if not ident.valido:
        raise ErroConsulta(f"identidade inválida: {ident.motivo}")
    return Path(dados_dir) / ident.valor / PASTA / ARQUIVO


@contextmanager
def _ler(dados_dir, identidade):
    """Abre o índice em modo leitura. Banco ausente devolve `None`.

    `mode=ro` de propósito: a consulta não pode criar índice nem escrever a
    linha de `meta` que `Indice.conectar()` grava. Uma tela que abrisse a
    empresa errada criaria um banco vazio no disco e ninguém veria.
    """
    caminho = caminho_do_indice(dados_dir, identidade)
    if not caminho.exists():
        yield None
        return
    con = None
    try:
        con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        yield con
    except sqlite3.Error as e:
        raise IndiceIlegivel(f"o índice existe mas não abre: {type(e).__name__}") from e
    finally:
        if con is not None:
            con.close()


# ════════════════════════════════════════════════════════════════════════════
#  Tradução do filtro em SQL parametrizado
# ════════════════════════════════════════════════════════════════════════════
_SO_DIGITOS = re.compile(r"\D")


def _digitos(v: str) -> str:
    return _SO_DIGITOS.sub("", str(v or ""))


def _iso(d) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d or "")


def _validar(filtro: Filtro, ordenar_por: str) -> None:
    if filtro.campo_data not in CAMPOS_DATA:
        raise CampoDeDataIndisponivel(f"campo de data desconhecido: {filtro.campo_data!r}")
    if filtro.campo_data not in CAMPOS_DATA_DISPONIVEIS:
        raise CampoDeDataIndisponivel(_MOTIVO_DATA[filtro.campo_data])
    if filtro.papel and filtro.papel not in PAPEIS_TODOS:
        raise PapelIndisponivel(
            f"papel desconhecido: {filtro.papel!r}; "
            f"use um de {list(PAPEIS_TODOS)}")
    if filtro.conteudo and filtro.conteudo not in (RESUMO, COMPLETO):
        raise ErroConsulta(f"conteúdo deve ser {RESUMO} ou {COMPLETO}")
    if ordenar_por not in ORDENS:
        raise OrdenacaoInvalida(
            f"ordenação {ordenar_por!r} não existe; use uma de {sorted(ORDENS)}")


def _colunas(con) -> set[str]:
    return {r[1] for r in con.execute("PRAGMA table_info(documentos)")}


def _exigir_coluna_de_data(con, filtro: Filtro, ordenar_por: str) -> None:
    """Índice velho não tem `dh_saida_entrada` — e não pode ganhar aqui.

    A consulta abre em `mode=ro` de propósito: ela não cria banco nem altera
    esquema. Um índice gravado antes da NF-e 6 simplesmente não tem a coluna,
    e o SQL falharia com `no such column` — que chega à tela como "consulta
    falhou", sem dizer o que fazer.

    Recusar com o motivo é melhor: quem lê sabe que falta reindexar, e não
    fica achando que a empresa não tem documento com saída informada.
    """
    if filtro.campo_data != ENTRADA and ordenar_por != "entrada":
        return
    if "dh_saida_entrada" in _colunas(con):
        return
    raise CampoDeDataIndisponivel(
        "este índice foi gravado antes da NF-e 6 e ainda não tem a coluna de "
        "saída/entrada. Reindexe a empresa "
        "(`python -m ingestao.reindexacao`) e o filtro passa a funcionar. "
        "Enquanto isso, use a data de emissão.")


def _onde(filtro: Filtro) -> tuple[str, list]:
    """Monta o WHERE. **Tudo parametrizado** — nenhum valor entra por f-string."""
    partes: list[str] = []
    args: list = []

    if filtro.especies:
        partes.append("d.especie IN (%s)" % ", ".join("?" * len(filtro.especies)))
        args.extend(filtro.especies)

    # ── período: SEMPRE local, sobre coluna do índice ───────────────────────
    coluna_data = _COLUNA_DATA[filtro.campo_data]
    if filtro.data_de:
        # `dh_emissao` é ISO com hora; comparar por prefixo de data mantém a
        # borda inclusiva sem depender de fuso.
        partes.append(f"substr({coluna_data}, 1, 10) >= ?")
        args.append(_iso(filtro.data_de))
    if filtro.data_ate:
        partes.append(f"substr({coluna_data}, 1, 10) <= ?")
        args.append(_iso(filtro.data_ate))

    if filtro.papel:
        # Casa em QUALQUER papel do documento, não só no principal: perguntar
        # "quais notas ela transportou" tem de achar também aquelas em que ela
        # é destinatária E transportadora. `papeis` é '|A|B|', então o LIKE
        # ancorado pelas barras não confunde `AUTXML` com nada.
        cond = ["d.papeis LIKE ?"]
        arg = [f"%|{filtro.papel}|%"]
        # Documento indexado antes da NF-e 4A não tem `papeis`; nele vale o
        # `papel` antigo, com `TERCEIRO` lido como `OUTRO`.
        antigos = [filtro.papel] + ([dm.TERCEIRO] if filtro.papel == OUTRO else [])
        cond.append("(d.papeis IS NULL OR d.papeis = '') AND d.papel IN (%s)"
                    % ", ".join("?" * len(antigos)))
        arg.extend(antigos)
        partes.append("((%s) OR (%s))" % (cond[0], cond[1]))
        args.extend(arg)

    if filtro.situacoes:
        partes.append("d.situacao IN (%s)" % ", ".join("?" * len(filtro.situacoes)))
        args.extend(filtro.situacoes)

    if filtro.conteudo == RESUMO:
        partes.append("d.versao_schema = ?")
        args.append(_SCHEMA_RESUMO)
    elif filtro.conteudo == COMPLETO:
        partes.append("(d.versao_schema IS NULL OR d.versao_schema <> ?)")
        args.append(_SCHEMA_RESUMO)

    if filtro.numero:
        partes.append("CAST(d.numero AS INTEGER) = ?")
        args.append(int(_digitos(filtro.numero) or -1))
    if filtro.serie:
        partes.append("CAST(d.serie AS INTEGER) = ?")
        args.append(int(_digitos(filtro.serie) or -1))
    if filtro.chave:
        partes.append("d.chave = ?")
        args.append(_digitos(filtro.chave))

    if filtro.emitente:
        alvo = _digitos(filtro.emitente)
        if alvo:
            partes.append("d.emitente = ?")
            args.append(alvo)
        else:
            partes.append("d.emitente_nome LIKE ? COLLATE NOCASE")
            args.append(f"%{filtro.emitente}%")

    if filtro.destinatario:
        alvo = _digitos(filtro.destinatario)
        if alvo:
            partes.append("d.contraparte = ?")
            args.append(alvo)
        else:
            partes.append("d.contraparte_nome LIKE ? COLLATE NOCASE")
            args.append(f"%{filtro.destinatario}%")

    if filtro.documento:
        alvo = _digitos(filtro.documento)
        partes.append("(d.emitente = ? OR d.contraparte = ?)")
        args.extend([alvo, alvo])

    if filtro.uf:
        # A UF não é coluna do índice. Ela está nos dois primeiros dígitos da
        # chave de acesso (cUF), que é dado do próprio documento — não é
        # palpite. Só vale para documento COM chave.
        partes.append("substr(d.chave, 1, 2) = ?")
        args.append(str(filtro.uf).strip())

    if filtro.quarentena is not None:
        partes.append("d.quarentena = ?")
        args.append(1 if filtro.quarentena else 0)

    # ── filtros que passam pelos itens ──────────────────────────────────────
    if filtro.cfop:
        # O CFOP existe nos DOIS mundos e mora em lugares diferentes: na NF-e
        # é do item, no CT-e é do documento. Perguntar por CFOP e receber
        # vazio porque o frete guarda o dele noutra tabela seria uma resposta
        # errada com cara de resposta certa.
        partes.append("(EXISTS (SELECT 1 FROM itens i WHERE i.id_documento = "
                      "d.id_documento AND i.cfop = ?)"
                      " OR EXISTS (SELECT 1 FROM cte c WHERE c.id_documento = "
                      "d.id_documento AND c.cfop = ?))")
        args.extend([_digitos(filtro.cfop)] * 2)
    if filtro.ncm:
        partes.append("EXISTS (SELECT 1 FROM itens i WHERE i.id_documento = "
                      "d.id_documento AND i.ncm = ?)")
        args.append(_digitos(filtro.ncm))

    # ── filtros que passam pelo CT-e ────────────────────────────────────────
    for campo, coluna in (("modal", "modal"), ("uf_inicio", "uf_inicio"),
                          ("uf_fim", "uf_fim"),
                          ("tomador_papel", "tomador_papel"),
                          ("cte_cst_icms", "cst_icms")):
        valor = getattr(filtro, campo, "")
        if valor:
            partes.append(f"EXISTS (SELECT 1 FROM cte c WHERE c.id_documento = "
                          f"d.id_documento AND c.{coluna} = ?)")
            args.append(str(valor).strip().upper()
                        if campo in ("uf_inicio", "uf_fim", "tomador_papel")
                        else str(valor).strip())

    if filtro.com_icms is not None:
        # "Tem ICMS" é `valor_icms` PRESENTE, não maior que zero: um frete que
        # destacou R$ 0,00 informou o imposto: quem não informou é `NULL`.
        existe = ("EXISTS (SELECT 1 FROM cte c WHERE c.id_documento = "
                  "d.id_documento AND c.valor_icms IS NOT NULL)")
        partes.append(existe if filtro.com_icms else f"NOT {existe}")

    if filtro.chave_nfe:
        partes.append("EXISTS (SELECT 1 FROM cte_nfe cn WHERE cn.id_documento "
                      "= d.id_documento AND cn.chave_nfe = ?)")
        args.append(_digitos(filtro.chave_nfe))

    if filtro.com_nfe is not None:
        existe = ("EXISTS (SELECT 1 FROM cte_nfe cn WHERE cn.id_documento = "
                  "d.id_documento)")
        partes.append(existe if filtro.com_nfe else f"NOT {existe}")

    # Um por coluna, todos com a mesma forma. `origem` não é dígito-only por
    # princípio (é código de um caractere), então vai como veio.
    for coluna, valor, so_digitos in (
            ("cest", filtro.cest, True),
            ("icms_cst", filtro.icms_cst, True),
            ("icms_csosn", filtro.icms_csosn, True),
            ("origem", filtro.origem, False),
            ("pis_cst", filtro.pis_cst, True),
            ("cofins_cst", filtro.cofins_cst, True),
            ("ipi_cst", filtro.ipi_cst, True)):
        if not valor:
            continue
        partes.append(f"EXISTS (SELECT 1 FROM itens i WHERE i.id_documento = "
                      f"d.id_documento AND i.{coluna} = ?)")
        args.append(_digitos(valor) if so_digitos else str(valor).strip())

    if filtro.com_reforma is not None:
        existe = ("EXISTS (SELECT 1 FROM itens i WHERE i.id_documento = "
                  "d.id_documento AND (i.reforma_cst IS NOT NULL "
                  "OR i.ibs_valor IS NOT NULL OR i.cbs_valor IS NOT NULL))")
        partes.append(existe if filtro.com_reforma else f"NOT {existe}")

    if filtro.cancelada is not None:
        partes.append(_EXISTE_CANCELAMENTO if filtro.cancelada
                      else f"NOT {_EXISTE_CANCELAMENTO}")
        args.extend(_TP_CANCELAMENTO)

    if filtro.situacoes_atuais:
        # Comparar contra a expressão derivada, não contra a coluna: é a
        # diferença entre "o documento nasceu cancelado" e "está cancelado".
        partes.append("(%s) IN (%s)" % (
            _SQL_SITUACAO_ATUAL, ", ".join("?" * len(filtro.situacoes_atuais))))
        args.extend(_TP_CANCELAMENTO)
        args.extend(filtro.situacoes_atuais)

    if filtro.com_evento is not None:
        existe = ("EXISTS (SELECT 1 FROM eventos e WHERE e.chave = d.chave)")
        partes.append(existe if filtro.com_evento else f"NOT {existe}")

    if filtro.texto:
        t = f"%{filtro.texto}%"
        partes.append(
            "(d.chave LIKE ? OR d.numero LIKE ? OR d.emitente_nome LIKE ? COLLATE NOCASE"
            " OR d.contraparte_nome LIKE ? COLLATE NOCASE"
            " OR d.emitente LIKE ? OR d.contraparte LIKE ?"
            " OR EXISTS (SELECT 1 FROM itens i WHERE i.id_documento = d.id_documento"
            "            AND (i.descricao LIKE ? COLLATE NOCASE OR i.codigo LIKE ?)))")
        args.extend([t] * 8)

    return (" AND ".join(partes) if partes else "1=1"), args


def _dec_ou_nada(r: sqlite3.Row, campos, coluna: str) -> Decimal | None:
    """`Decimal` da coluna, ou `None` se ela não veio — nem a coluna existir.

    Índice criado antes da REFORMA 2 não tem estas colunas. `_colunas_faltantes`
    as acrescenta na próxima conexão de ESCRITA, mas a consulta abre em
    somente-leitura e pode encontrar o banco ainda sem elas.
    """
    if coluna not in campos or r[coluna] in (None, ""):
        return None
    return Decimal(r[coluna])


def _linha(r: sqlite3.Row, eventos: int) -> Documento:  # noqa: C901
    esquema = r["versao_schema"] or ""
    papel_idx = r["papel"] or dm.TERCEIRO
    campos = r.keys()
    crus = (r["papeis"] or "") if "papeis" in campos else ""
    lista = tuple(x for x in crus.split("|") if x)
    chaves_da_linha = r.keys()
    atual = (r["situacao_atual"] if "situacao_atual" in chaves_da_linha
             else (r["situacao"] or ""))
    return Documento(
        id_documento=r["id_documento"], especie=r["especie"],
        chave=r["chave"] or "", numero=r["numero"] or "", serie=r["serie"] or "",
        papel=_PAPEL_LEGADO.get(papel_idx, papel_idx),
        papeis=lista or (_PAPEL_LEGADO.get(papel_idx, papel_idx),),
        papel_no_indice=papel_idx,
        emitente=r["emitente"] or "", emitente_nome=r["emitente_nome"] or "",
        contraparte=r["contraparte"] or "", contraparte_nome=r["contraparte_nome"] or "",
        dh_emissao=r["dh_emissao"] or "",
        dh_saida_entrada=((r["dh_saida_entrada"] or "")
                          if "dh_saida_entrada" in campos else ""),
        competencia=r["competencia"] or "",
        valor_total=None if r["valor_total"] in (None, "") else Decimal(r["valor_total"]),
        # `if ... in campos` porque um índice ainda não migrado não tem estas
        # colunas: ler `r["total_ibs"]` nele levantaria IndexError e derrubaria
        # a consulta inteira. Ausente sai `None`, como o resto.
        total_base_ibs_cbs=_dec_ou_nada(r, campos, "total_base_ibs_cbs"),
        total_ibs=_dec_ou_nada(r, campos, "total_ibs"),
        total_cbs=_dec_ou_nada(r, campos, "total_cbs"),
        situacao=r["situacao"] or "",
        situacao_atual=atual or "",
        cancelada=(atual == dm.CANCELADO),
        conteudo=RESUMO if esquema == _SCHEMA_RESUMO else COMPLETO,
        versao_schema=esquema, estado=r["estado"] or "",
        quarentena=bool(r["quarentena"]), eventos=eventos)


# ════════════════════════════════════════════════════════════════════════════
#  API
# ════════════════════════════════════════════════════════════════════════════
def contar(dados_dir, identidade, filtro: Filtro | None = None) -> int:
    """Quantos documentos casam. Contagem é do SQLite, não de laço em Python."""
    filtro = filtro or Filtro()
    _validar(filtro, ORDEM_PADRAO)
    with _ler(dados_dir, identidade) as con:
        if con is None:
            return 0
        _exigir_coluna_de_data(con, filtro, ORDEM_PADRAO)
        onde, args = _onde(filtro)
        try:
            return int(con.execute(
                f"SELECT COUNT(*) FROM documentos d WHERE {onde}", args).fetchone()[0])
        except sqlite3.DatabaseError as e:
            raise IndiceIlegivel(f"consulta falhou: {type(e).__name__}") from e


def contar_chaves(dados_dir, identidade,
                  filtro: Filtro | None = None) -> tuple[int, int]:
    """`(linhas_com_chave, chaves_distintas)` — numa consulta só.

    Existe para que "há duplicidade no índice?" seja respondível sem abrir um
    segundo caminho de SQL sobre `documentos`. A pergunta é de quem lê o acervo
    por identificador fiscal: `id_documento` já é chave primária, então duas
    LINHAS com a mesma `chave` significam dois ids para o mesmo documento —
    defeito a investigar, não número a esconder.

    Os dois vêm juntos de propósito: sozinha, a contagem de distintas não
    distingue "não há duplicata" de "não há chave" (o resumo `resNFe` pode vir
    sem ela), e essa diferença é justamente o que se quer ver.
    """
    filtro = filtro or Filtro()
    _validar(filtro, ORDEM_PADRAO)
    with _ler(dados_dir, identidade) as con:
        if con is None:
            return (0, 0)
        _exigir_coluna_de_data(con, filtro, ORDEM_PADRAO)
        onde, args = _onde(filtro)
        try:
            r = con.execute(
                f"SELECT COUNT(d.chave) n, COUNT(DISTINCT d.chave) q "
                f"FROM documentos d WHERE {onde}", args).fetchone()
        except sqlite3.DatabaseError as e:
            raise IndiceIlegivel(f"consulta falhou: {type(e).__name__}") from e
    return (int(r["n"] or 0), int(r["q"] or 0))


def somar(dados_dir, identidade, filtro: Filtro | None = None) -> dict:
    """Quantos documentos e quanto somam — numa consulta só.

    Existe porque os cards precisavam de quantidade E valor, e a primeira
    versão obtinha o valor paginando o conjunto inteiro em Python: 1,2 s para
    montar seis cards numa empresa de 6.513 documentos. `COUNT` e `SUM` são
    trabalho de banco.

    O `SUM` é sobre `CAST(... AS REAL)`, então o resultado é ponto flutuante e
    volta arredondado a centavo. Isto é um TOTAL DE TELA, não apuração — quem
    for somar para fins fiscais usa os documentos, em `Decimal`, pelo caminho
    da normalização.
    """
    filtro = filtro or Filtro()
    _validar(filtro, ORDEM_PADRAO)
    with _ler(dados_dir, identidade) as con:
        if con is None:
            return {"quantidade": 0, "valor": 0.0}
        _exigir_coluna_de_data(con, filtro, ORDEM_PADRAO)
        onde, args = _onde(filtro)
        try:
            r = con.execute(
                f"SELECT COUNT(*) n, "
                f"       SUM(CAST(COALESCE(valor_total,'0') AS REAL)) v "
                f"FROM documentos d WHERE {onde}", args).fetchone()
        except sqlite3.DatabaseError as e:
            raise IndiceIlegivel(f"consulta falhou: {type(e).__name__}") from e
    return {"quantidade": int(r["n"] or 0), "valor": round(float(r["v"] or 0.0), 2)}


def consultar(dados_dir, identidade, filtro: Filtro | None = None, *,
              pagina: int = 1, tamanho: int = TAMANHO_PADRAO,
              ordenar_por: str = ORDEM_PADRAO, direcao: str = "desc") -> Pagina:
    """Uma página de documentos.

    A filtragem, a contagem, a ordenação e o recorte são **do SQLite**. Trazer
    tudo para a memória e filtrar em Python funcionaria com os 2.103 documentos
    de hoje e cairia no dia em que os 23.450 legados forem migrados (NF-e 3).
    """
    filtro = filtro or Filtro()
    ordenar_por = (ordenar_por or ORDEM_PADRAO).lower()
    _validar(filtro, ordenar_por)
    direcao = "ASC" if str(direcao).lower().startswith("a") else "DESC"
    pagina = max(1, int(pagina))
    tamanho = max(1, min(int(tamanho), TAMANHO_MAXIMO))

    with _ler(dados_dir, identidade) as con:
        if con is None:
            return Pagina((), 0, pagina, tamanho, ordenar_por, direcao.lower(), filtro)
        _exigir_coluna_de_data(con, filtro, ordenar_por)
        onde, args = _onde(filtro)
        try:
            total = int(con.execute(
                f"SELECT COUNT(*) FROM documentos d WHERE {onde}", args).fetchone()[0])
            # `ordenar_por` e `direcao` são validados contra listas fechadas
            # acima; nada vindo do usuário entra cru na string.
            sql = (f"SELECT d.*, ({_SQL_SITUACAO_ATUAL}) AS situacao_atual "
                   f"FROM documentos d WHERE {onde} "
                   f"ORDER BY {ORDENS[ordenar_por]} {direcao}, d.id_documento {direcao} "
                   f"LIMIT ? OFFSET ?")
            # os parâmetros do SELECT vêm ANTES dos do WHERE, na ordem do SQL
            linhas = con.execute(
                sql, [*_TP_CANCELAMENTO, *args, tamanho,
                      (pagina - 1) * tamanho]).fetchall()
            chaves = [r["chave"] for r in linhas if r["chave"]]
            contagem = _eventos_por_chave(con, chaves)
        except sqlite3.DatabaseError as e:
            raise IndiceIlegivel(f"consulta falhou: {type(e).__name__}") from e

    itens = tuple(_linha(r, contagem.get(r["chave"] or "", 0)) for r in linhas)
    return Pagina(itens, total, pagina, tamanho, ordenar_por, direcao.lower(), filtro)


def _eventos_por_chave(con, chaves: list[str]) -> dict[str, int]:
    """Uma consulta para a página inteira, não uma por linha."""
    if not chaves:
        return {}
    unicas = sorted(set(chaves))
    saida: dict[str, int] = {}
    # SQLite tem teto de parâmetros; 400 por vez está bem abaixo dele e cobre
    # a maior página possível numa chamada só.
    for i in range(0, len(unicas), 400):
        lote = unicas[i:i + 400]
        marcas = ", ".join("?" * len(lote))
        for chave, n in con.execute(
                f"SELECT chave, COUNT(*) FROM eventos WHERE chave IN ({marcas}) "
                f"GROUP BY chave", lote):
            saida[chave] = int(n)
    return saida


def eventos_da_chave(dados_dir, identidade, chave: str) -> tuple[dict, ...]:
    """Os eventos de UMA nota. Evento não entra na listagem de documentos.

    Separado de propósito: 98% dos eventos do acervo são Registro de Passagem
    (NF-e 1, §6). Misturá-los à lista de notas transformaria uma tela de
    documentos fiscais numa tela de trânsito de carga.
    """
    alvo = _digitos(chave)
    with _ler(dados_dir, identidade) as con:
        if con is None or not alvo:
            return ()
        try:
            linhas = con.execute(
                "SELECT * FROM eventos WHERE chave = ? ORDER BY dh_evento, sequencia",
                (alvo,)).fetchall()
        except sqlite3.DatabaseError as e:
            raise IndiceIlegivel(f"consulta falhou: {type(e).__name__}") from e
    return tuple(dict(r) for r in linhas)


def resumo_por_competencia(dados_dir, identidade,
                           filtro: Filtro | None = None) -> tuple[dict, ...]:
    """Quantidade e soma por competência. Agregação no banco.

    O valor devolvido é a soma de `valor_total` **como está no documento** — não
    é apuração, não separa compra de frete de terceiro e não desconta nada.
    Quem transformar isto em número fiscal precisa passar pela classificação.
    """
    filtro = filtro or Filtro()
    _validar(filtro, ORDEM_PADRAO)
    with _ler(dados_dir, identidade) as con:
        if con is None:
            return ()
        _exigir_coluna_de_data(con, filtro, ORDEM_PADRAO)
        onde, args = _onde(filtro)
        try:
            linhas = con.execute(
                f"SELECT competencia, COUNT(*) n, "
                f"       SUM(CAST(COALESCE(valor_total,'0') AS REAL)) v "
                f"FROM documentos d WHERE {onde} "
                f"GROUP BY competencia ORDER BY competencia", args).fetchall()
        except sqlite3.DatabaseError as e:
            raise IndiceIlegivel(f"consulta falhou: {type(e).__name__}") from e
    return tuple({"competencia": r["competencia"], "quantidade": int(r["n"]),
                  "valor": Decimal(str(round(r["v"] or 0.0, 2)))} for r in linhas)


# Colunas de item que a tela mostra, agrupadas por bloco. A ordem é a da tela,
# e o agrupamento existe para o detalhe poder dizer "este bloco não veio no
# documento" em vez de mostrar oito traços seguidos.
_BLOCOS_DO_ITEM = {
    "icms": ("icms_cst", "icms_csosn", "origem", "icms_base", "icms_aliquota",
             "icms_valor", "icms_reducao", "icms_base_st", "icms_valor_st",
             "icms_fcp", "icms_diferido", "icms_desonerado"),
    "pis": ("pis_cst", "pis_base", "pis_aliquota", "pis_valor",
            "pis_quantidade", "pis_valor_unidade"),
    "cofins": ("cofins_cst", "cofins_base", "cofins_aliquota", "cofins_valor",
               "cofins_quantidade", "cofins_valor_unidade"),
    "ipi": ("ipi_cst", "ipi_base", "ipi_aliquota", "ipi_valor"),
    "reforma": ("reforma_cst", "reforma_classificacao", "reforma_base",
                "ibs_uf_aliquota", "ibs_uf_valor", "ibs_mun_aliquota",
                "ibs_mun_valor", "ibs_valor", "cbs_aliquota", "cbs_valor",
                "is_cst", "is_base", "is_aliquota", "is_valor"),
}
_CAMPOS_BASICOS_DO_ITEM = ("numero", "codigo", "descricao", "ncm", "cest",
                           "cfop", "unidade", "quantidade", "valor_unitario",
                           "desconto", "valor_produto")


def itens_do_documento(dados_dir, identidade, id_documento: str) -> tuple[dict, ...]:
    """Os itens de UM documento, com os tributos agrupados por bloco.

    Campo ausente sai como `None`, nunca `0` — a tela mostra `—`. Um bloco
    inteiro ausente (o documento não trouxe IPI, por exemplo) sai com
    `presente: False`, para a tela poder omiti-lo em vez de desenhar uma
    fileira de traços.
    """
    with _ler(dados_dir, identidade) as con:
        if con is None:
            return ()
        try:
            linhas = con.execute(
                "SELECT * FROM itens WHERE id_documento = ? ORDER BY numero",
                (str(id_documento),)).fetchall()
        except sqlite3.DatabaseError as e:
            raise IndiceIlegivel(f"consulta falhou: {type(e).__name__}") from e

    saida = []
    for r in linhas:
        campos = set(r.keys())
        item = {c: (r[c] if c in campos else None)
                for c in _CAMPOS_BASICOS_DO_ITEM}
        for bloco, colunas in _BLOCOS_DO_ITEM.items():
            valores = {c: (r[c] if c in campos else None) for c in colunas}
            item[bloco] = {"presente": any(v not in (None, "")
                                           for v in valores.values()),
                           **valores}
        saida.append(item)
    return tuple(saida)


def detalhe(dados_dir, identidade, chave: str = "",
            id_documento: str = "") -> dict | None:
    """Tudo o que a tela precisa de UM documento, numa consulta só.

    Aceita chave OU `id_documento`. **Não recebe caminho de arquivo** — a tela
    nunca diz onde o documento está; ela diz qual documento quer, e a camada
    resolve pela identidade. É o que impede um parâmetro de URL de virar
    passeio pelo disco.
    """
    # A NOTA primeiro. Evento e nota compartilham a chave de acesso, e pedir o
    # detalhe "desta chave" quer dizer a nota — o evento aparece dentro dela,
    # na linha do tempo. Buscar sem essa ordem devolvia o evento e um detalhe
    # sem itens.
    alvo = None
    for especies in ((dm.NFE55, dm.NFCE65), (dm.EVENTO_NFE,)):
        filtro = Filtro(chave=chave, especies=especies) if chave             else Filtro(especies=especies)
        pg = consultar(dados_dir, identidade, filtro, tamanho=TAMANHO_MAXIMO)
        for d in pg.itens:
            if (chave and d.chave == _digitos(chave)) or                (id_documento and d.id_documento == id_documento):
                alvo = d
                break
        if alvo is not None:
            break
    if alvo is None:
        return None

    eventos = eventos_da_chave(dados_dir, identidade, alvo.chave) if alvo.chave else ()
    itens = itens_do_documento(dados_dir, identidade, alvo.id_documento)
    return {
        "documento": alvo.para_json(),
        "itens": [_item_json(i) for i in itens],
        "eventos": [dict(e) for e in eventos],
        "tem_reforma": any(i["reforma"]["presente"] for i in itens),
    }


def _item_json(item: dict) -> dict:
    """`Decimal` vira texto; ausente continua ausente."""
    def limpo(v):
        return None if v in (None, "") else str(v)
    saida = {c: limpo(item.get(c)) for c in _CAMPOS_BASICOS_DO_ITEM}
    saida["numero"] = item.get("numero")
    for bloco in _BLOCOS_DO_ITEM:
        d = item.get(bloco) or {}
        saida[bloco] = {k: (v if k == "presente" else limpo(v))
                        for k, v in d.items()}
    return saida


def capacidades() -> dict:
    """O que esta camada sabe e o que ainda não sabe — como dado.

    Existe para a tela poder desabilitar um filtro em vez de oferecê-lo e
    devolver vazio. Filtro que não funciona e não avisa é pior que filtro que
    não existe.
    """
    return {
        "papeis": list(PAPEIS),
        "papeis_cte": list(PAPEIS_CTE),
        "papeis_respondiveis": list(PAPEIS_RESPONDIVEIS),
        "papeis_pendentes": {},
        "campos_data": list(CAMPOS_DATA),
        "campos_data_disponiveis": list(CAMPOS_DATA_DISPONIVEIS),
        "campos_data_pendentes": dict(_MOTIVO_DATA),
        "campo_data_padrao": CAMPO_DATA_PADRAO,
        "advertencia_entrada": ADVERTENCIA_ENTRADA,
        "ordenacoes": sorted(ORDENS),
        "tamanho_padrao": TAMANHO_PADRAO,
        "tamanho_maximo": TAMANHO_MAXIMO,
        "conteudo": [RESUMO, COMPLETO],
        "fonte": "índice SQLite da ingestão — nenhuma chamada de rede",
    }
