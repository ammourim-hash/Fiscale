"""
indice.py — o índice consultável. É CACHE, e é descartável de propósito.

A REGRA QUE ORGANIZA TUDO
    **O XML é a verdade; o índice é conveniência.** Se os dois divergirem, o XML
    ganha e o índice se reconstrói. Por isso ele:

    • fica fora do `.fbk` (backup carrega documento, não cache);
    • pode ser apagado a qualquer momento sem perda;
    • é reconstruído inteiro a partir do acervo, sem tocar na rede.

    A consequência prática é que erro de parser nunca é permanente: sobe a
    versão, reprocessa, o índice se refaz. Foi para isso que o acervo guardou o
    original.

POR QUE SQLITE E NÃO JSON
    A consulta do escritório é sempre por período e por empresa, sobre milhares
    de documentos. JSON exigiria carregar tudo em memória a cada tela. SQLite
    resolve isso com índice de verdade, já é usado no projeto (`sessoes.db`), e
    não acrescenta dependência.

DINHEIRO É TEXTO, NUNCA REAL
    Coluna `REAL` no SQLite é ponto flutuante — guardar `Decimal` nela traria o
    float pela porta dos fundos, e a precisão sumiria entre gravar e ler. Os
    valores são gravados como TEXT e voltam por `Decimal(...)`.

UM BANCO POR EMPRESA
    `<raiz>/<identidade>/indice/documentos.db`. Não é decisão de performance: é
    isolamento. Não existe consulta capaz de misturar duas empresas porque não
    existe banco onde as duas coexistam.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import acervo as acv
from . import auditoria as aud
from . import documento as dm
from . import identificacao as idf
from . import parsers as prs
from .distribuicao import higienizar
from .identidade import normalizar

PASTA = "indice"
ARQUIVO = "documentos.db"
VERSAO_ESQUEMA = 1

# estado do documento no índice
INDEXADO = "INDEXADO"
SCHEMA_DESCONHECIDO = "SCHEMA_DESCONHECIDO"
FALHA_PARSER = "FALHA_PARSER"
QUARENTENA = "QUARENTENA"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT
);
CREATE TABLE IF NOT EXISTS documentos (
    id_documento      TEXT PRIMARY KEY,
    especie           TEXT NOT NULL,
    chave             TEXT,
    numero            TEXT,
    serie             TEXT,
    identidade_empresa TEXT NOT NULL,
    papel             TEXT,
    papeis            TEXT,      -- '|A|B|' — todos; `papel` é o principal
    emitente          TEXT,
    emitente_nome     TEXT,
    contraparte       TEXT,
    contraparte_nome  TEXT,
    dh_emissao        TEXT,
    dh_saida_entrada  TEXT,      -- `dhSaiEnt`: opcional no schema, presente
                                 -- em 99,5% do acervo (7.498 de 7.534)
    competencia       TEXT,
    valor_total       TEXT,           -- Decimal como TEXTO. Nunca REAL.
    -- Reforma Tributária: o total DECLARADO pelo emitente, de
    -- `<total><IBSCBSTot>`. Ficam AQUI, ao lado do `valor_total`, porque são
    -- totais DO DOCUMENTO — o que é por item já mora em `itens`. TEXT pela
    -- mesma razão de sempre: REAL traria o float de volta.
    -- NULL = o documento não informou. Nunca zero.
    total_base_ibs_cbs TEXT,          -- `vBCIBSCBS`
    total_ibs         TEXT,           -- `gIBS/vIBS`
    total_cbs         TEXT,           -- `gCBS/vCBS`
    situacao          TEXT,
    versao_schema     TEXT,
    parser            TEXT,
    versao_parser     INTEGER,
    estado            TEXT NOT NULL,
    quarentena        INTEGER NOT NULL DEFAULT 0,
    hash_conteudo     TEXT,
    motivo            TEXT
);
CREATE INDEX IF NOT EXISTS ix_doc_comp  ON documentos(competencia, especie);
CREATE INDEX IF NOT EXISTS ix_doc_emis  ON documentos(dh_emissao);
CREATE INDEX IF NOT EXISTS ix_doc_chave ON documentos(chave);
CREATE INDEX IF NOT EXISTS ix_doc_est   ON documentos(estado);

CREATE TABLE IF NOT EXISTS eventos (
    id_documento  TEXT PRIMARY KEY,
    chave         TEXT NOT NULL,
    tp_evento     TEXT,
    descricao     TEXT,
    sequencia     TEXT,
    dh_evento     TEXT,
    protocolo     TEXT,
    justificativa TEXT,
    orgao         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ev_chave ON eventos(chave);
-- A situação ATUAL (NF-e 4A) pergunta "existe cancelamento desta chave?" uma
-- vez por documento. Com `ix_ev_chave` sozinho o SQLite acha os eventos da
-- chave e depois confere o tipo de cada um; nas transportadoras isso significa
-- percorrer milhares de registros de passagem para achar zero cancelamentos.
--   medido em 23/08/2026, 6.513 documentos e 15.731 eventos:
--       filtro `cancelada`   219 ms  ->  109 ms
CREATE INDEX IF NOT EXISTS ix_ev_tipo_chave ON eventos(tp_evento, chave);

CREATE TABLE IF NOT EXISTS cte (
    id_documento TEXT PRIMARY KEY,
    -- A ExtensaoCTe tinha 24 campos e nenhum destino: o parser os produzia e
    -- o índice os descartava, porque só havia ramo para evento e para item de
    -- NF-e. Estes são os mesmos campos, com os mesmos nomes do modelo.
    modelo       TEXT,
    cfop         TEXT,
    natureza_operacao TEXT,
    tipo_cte     TEXT,
    tipo_servico TEXT,
    modal        TEXT,
    tomador_codigo TEXT,
    tomador_papel  TEXT,
    uf_inicio    TEXT,
    uf_fim       TEXT,
    municipio_inicio TEXT,
    municipio_fim    TEXT,
    -- Valores em TEXT, como no resto do índice: `Decimal` vira texto e volta
    -- texto. Gravar `REAL` aqui reintroduziria o ponto flutuante que o projeto
    -- inteiro evita em dinheiro.
    valor_prestacao TEXT,
    valor_receber   TEXT,
    base_icms       TEXT,
    aliquota_icms   TEXT,
    valor_icms      TEXT,
    cst_icms        TEXT,
    protocolo       TEXT,
    dh_recebimento  TEXT,
    codigo_status   TEXT,
    motivo_status   TEXT
);

CREATE INDEX IF NOT EXISTS ix_cte_cfop ON cte(cfop);
CREATE INDEX IF NOT EXISTS ix_cte_rota ON cte(uf_inicio, uf_fim);
CREATE INDEX IF NOT EXISTS ix_cte_modal ON cte(modal);

-- A PONTE. Cada linha diz "este CT-e carregou aquela NF-e".
--
-- É a única coisa no acervo que liga frete a mercadoria. Sem ela, o custo do
-- transporte e a nota transportada ficam em universos separados e a
-- conciliação vira trabalho manual. Um CT-e carrega várias NF-e, e uma NF-e
-- pode ser transportada por mais de um CT-e — daí a chave composta.
--
-- Guarda a CHAVE da NF-e, não o `id_documento` dela: a nota transportada
-- costuma ser de terceiro e pode nunca entrar no nosso acervo. Uma chave
-- estrangeira exigiria que ela existisse, e ela não existe.
CREATE TABLE IF NOT EXISTS cte_nfe (
    id_documento TEXT NOT NULL,
    chave_nfe    TEXT NOT NULL,
    PRIMARY KEY (id_documento, chave_nfe)
);

CREATE INDEX IF NOT EXISTS ix_cte_nfe_chave ON cte_nfe(chave_nfe);

CREATE TABLE IF NOT EXISTS itens (
    id_documento TEXT NOT NULL,
    numero       INTEGER NOT NULL,
    codigo       TEXT,
    descricao    TEXT,
    ncm          TEXT,
    cest         TEXT,          -- separado do NCM; nunca inferido a partir dele
    cfop         TEXT,
    unidade      TEXT,
    quantidade   TEXT,
    valor_unitario TEXT,
    valor_produto  TEXT,
    desconto     TEXT,
    origem       TEXT,          -- `orig` do ICMS: vale para o item inteiro
    -- ICMS. `icms_cst` e `icms_csosn` são COLUNAS DIFERENTES de propósito:
    -- `102` como CST e `102` como CSOSN significam coisas distintas, e o
    -- auditor precisa saber qual dos dois o documento preencheu.
    icms_cst     TEXT,
    icms_csosn   TEXT,
    icms_base    TEXT,
    icms_aliquota TEXT,
    icms_valor   TEXT,
    icms_reducao TEXT,
    icms_base_st TEXT,
    icms_valor_st TEXT,
    icms_fcp     TEXT,
    icms_diferido TEXT,
    icms_desonerado TEXT,
    -- IPI, PIS e COFINS
    ipi_cst      TEXT,
    ipi_base     TEXT,
    ipi_aliquota TEXT,
    ipi_valor    TEXT,
    pis_cst      TEXT,
    pis_base     TEXT,
    pis_aliquota TEXT,
    pis_valor    TEXT,
    pis_quantidade TEXT,        -- modalidade por quantidade (`PISQtde`)
    pis_valor_unidade TEXT,
    cofins_cst   TEXT,
    cofins_base  TEXT,
    cofins_aliquota TEXT,
    cofins_valor TEXT,
    cofins_quantidade TEXT,
    cofins_valor_unidade TEXT,
    -- Reforma Tributária. NÃO é preparação: `gIBSCBS` já aparece em 78% dos
    -- XML completos do acervo (medido em 23/08/2026). Ausente fica NULL —
    -- nunca zero, que faria parecer apuração feita com resultado nulo.
    reforma_cst  TEXT,
    reforma_classificacao TEXT, -- `cClassTrib`
    reforma_base TEXT,
    ibs_uf_aliquota TEXT,
    ibs_uf_valor TEXT,
    ibs_mun_aliquota TEXT,
    ibs_mun_valor TEXT,
    ibs_valor    TEXT,
    cbs_aliquota TEXT,
    cbs_valor    TEXT,
    is_cst       TEXT,
    is_base      TEXT,
    is_aliquota  TEXT,
    is_valor     TEXT,
    PRIMARY KEY (id_documento, numero)
);
CREATE INDEX IF NOT EXISTS ix_item_ncm  ON itens(ncm);
CREATE INDEX IF NOT EXISTS ix_item_cfop ON itens(cfop);

-- Compostos, acrescentados na NF-e 3 com número medido na mão.
--
-- A consulta por CFOP é `EXISTS (SELECT 1 FROM itens WHERE id_documento = ?
-- AND cfop = ?)`, avaliada uma vez por documento. Com `ix_item_cfop(cfop)`
-- sozinho, o SQLite acha as linhas do CFOP mas ainda precisa conferir o
-- `id_documento` de cada uma — e CFOP é pouco seletivo: `5102` sozinho responde
-- por milhares de itens.
--
-- Medido em 23/08/2026, empresa com 6.513 documentos e 37.733 itens, DEPOIS da
-- migração do legado:
--     filtro por CFOP    22.041 ms  →  com este índice, 8 ms
--     filtro por NCM         27 ms  (rápido só porque aquele NCM era raro)
--
-- O NCM ganha o mesmo tratamento por antecipação medida, não por simetria: um
-- NCM comum cairia no mesmo buraco que o CFOP caiu.
CREATE INDEX IF NOT EXISTS ix_item_cfop_doc ON itens(cfop, id_documento);
CREATE INDEX IF NOT EXISTS ix_item_ncm_doc  ON itens(ncm, id_documento);
"""


# Os campos da tabela `itens`, na ordem em que `_linha_do_item` os produz.
# Lista e montador andam juntos de propósito: separá-los seria a forma mais
# fácil de gravar o CSOSN na coluna do CST sem ninguém notar.
_CAMPOS_ITEM = (
    "id_documento", "numero", "codigo", "descricao", "ncm", "cest", "cfop",
    "unidade", "quantidade", "valor_unitario", "valor_produto", "desconto",
    "origem",
    "icms_cst", "icms_csosn", "icms_base", "icms_aliquota", "icms_valor",
    "icms_reducao", "icms_base_st", "icms_valor_st", "icms_fcp",
    "icms_diferido", "icms_desonerado",
    "ipi_cst", "ipi_base", "ipi_aliquota", "ipi_valor",
    "pis_cst", "pis_base", "pis_aliquota", "pis_valor", "pis_quantidade",
    "pis_valor_unidade",
    "cofins_cst", "cofins_base", "cofins_aliquota", "cofins_valor",
    "cofins_quantidade", "cofins_valor_unidade",
    "reforma_cst", "reforma_classificacao", "reforma_base",
    "ibs_uf_aliquota", "ibs_uf_valor", "ibs_mun_aliquota", "ibs_mun_valor",
    "ibs_valor", "cbs_aliquota", "cbs_valor",
    "is_cst", "is_base", "is_aliquota", "is_valor",
)


# Os campos da tabela `cte`, na ordem em que `_linha_do_cte` os produz. Mesma
# disciplina de `_CAMPOS_ITEM`: lista e montador andam juntos, porque separá-los
# é a forma mais fácil de gravar a UF de destino na coluna da origem.
_CAMPOS_CTE = (
    "id_documento", "modelo", "cfop", "natureza_operacao", "tipo_cte",
    "tipo_servico", "modal", "tomador_codigo", "tomador_papel",
    "uf_inicio", "uf_fim", "municipio_inicio", "municipio_fim",
    "valor_prestacao", "valor_receber",
    "base_icms", "aliquota_icms", "valor_icms", "cst_icms",
    "protocolo", "dh_recebimento", "codigo_status", "motivo_status",
)


def _linha_do_cte(id_documento: str, e) -> tuple:
    """A `ExtensaoCTe` vira uma linha do índice.

    Ausente vira `NULL`, nunca `0` nem `""` — a mesma regra dos itens. Um
    `valor_icms` zerado diz que o documento informou zero; ausente diz que ele
    não informou, e as duas coisas levam a conclusões fiscais diferentes.
    """
    return (
        id_documento, _txt(e.modelo), _txt(e.cfop), _txt(e.natureza_operacao),
        _txt(e.tipo_cte), _txt(e.tipo_servico), _txt(e.modal),
        _txt(e.tomador_codigo), _txt(e.tomador_papel),
        _txt(e.uf_inicio), _txt(e.uf_fim),
        _txt(e.municipio_inicio), _txt(e.municipio_fim),
        _txt(e.valor_prestacao), _txt(e.valor_receber),
        _txt(e.base_icms), _txt(e.aliquota_icms), _txt(e.valor_icms),
        _txt(e.cst_icms), _txt(e.protocolo),
        e.dh_recebimento.isoformat() if e.dh_recebimento else None,
        _txt(e.codigo_status), _txt(e.motivo_status),
    )


def _linha_do_item(id_documento: str, it) -> tuple:
    """Um item do documento normalizado vira uma linha do índice.

    Campo ausente vira `NULL`, nunca `0`: `_txt(None)` devolve `None` e a
    distinção sobrevive até a consulta. Um `0` gravado no lugar de ausente
    diria que o documento informou zero — que é outra afirmação."""
    i, p, c, r = it.icms, it.pis, it.cofins, it.reforma
    return (
        id_documento, it.numero, it.codigo, it.descricao, _txt(it.ncm),
        _txt(it.cest), _txt(it.cfop), it.unidade, _txt(it.quantidade),
        _txt(it.valor_unitario),
        _txt(it.valor_produto), _txt(it.desconto), _txt(i.origem),
        _txt(i.cst), _txt(i.csosn), _txt(i.base), _txt(i.aliquota), _txt(i.valor),
        _txt(i.reducao_base), _txt(i.base_st), _txt(i.valor_st),
        _txt(i.valor_fcp), _txt(i.valor_diferido), _txt(i.valor_desonerado),
        _txt(it.ipi.cst), _txt(it.ipi.base), _txt(it.ipi.aliquota),
        _txt(it.ipi.valor),
        _txt(p.cst), _txt(p.base), _txt(p.aliquota), _txt(p.valor),
        _txt(p.quantidade_base), _txt(p.aliquota_por_unidade),
        _txt(c.cst), _txt(c.base), _txt(c.aliquota), _txt(c.valor),
        _txt(c.quantidade_base), _txt(c.aliquota_por_unidade),
        _txt(r.cst), _txt(r.classificacao_tributaria), _txt(r.base),
        _txt(r.aliquota_ibs_uf), _txt(r.valor_ibs_uf),
        _txt(r.aliquota_ibs_mun), _txt(r.valor_ibs_mun), _txt(r.valor_ibs),
        _txt(r.aliquota_cbs), _txt(r.valor_cbs),
        _txt(r.cst_is), _txt(r.base_is), _txt(r.aliquota_is), _txt(r.valor_is),
    )


def _dec(v):
    """TEXTO → `Decimal`. `None` continua `None` — ausente não vira zero."""
    return None if v is None or v == "" else Decimal(v)


def _txt(v):
    """`None` continua `None`; o resto vira texto.

    String VAZIA também vira `None`: o parser devolve `""` para código que o
    documento não trouxe (um CST que não existe naquele grupo), e gravar `''`
    faria `IS NOT NULL` responder "tem" para um campo que não tem. Foi assim
    que o filtro `com_reforma` achou documento sem Reforma nenhuma."""
    if v is None:
        return None
    texto = str(v)
    return texto if texto.strip() else None


# Colunas acrescentadas depois que já havia banco em disco. `CREATE TABLE IF
# NOT EXISTS` não altera tabela existente — sem isto, um índice antigo ficaria
# sem a coluna e o INSERT quebraria em produção, não no teste.
_ACRESCIMOS = (
    ("eventos", "orgao", "TEXT"),
    # NF-e 6: a data de saída/entrada. Banco que já existe não ganha coluna com
    # `CREATE TABLE IF NOT EXISTS` — sem esta linha o INSERT quebraria em
    # produção, não no teste.
    ("documentos", "dh_saida_entrada", "TEXT"),
    # NF-e 4A: os papéis reais da empresa no documento. Coluna nova sobre banco
    # que já existe — sem isto o INSERT quebraria em produção, não no teste.
    ("documentos", "papeis", "TEXT"),
    # NF-e 4B: o item enriquecido. `CREATE TABLE IF NOT EXISTS` não altera
    # tabela que já existe, então cada coluna precisa entrar por aqui.
    ("itens", "cest", "TEXT"),
    ("itens", "desconto", "TEXT"),
    ("itens", "origem", "TEXT"),
    ("itens", "icms_csosn", "TEXT"),
    ("itens", "icms_aliquota", "TEXT"),
    ("itens", "icms_reducao", "TEXT"),
    ("itens", "icms_base_st", "TEXT"),
    ("itens", "icms_valor_st", "TEXT"),
    ("itens", "icms_fcp", "TEXT"),
    ("itens", "icms_diferido", "TEXT"),
    ("itens", "icms_desonerado", "TEXT"),
    ("itens", "ipi_cst", "TEXT"),
    ("itens", "ipi_base", "TEXT"),
    ("itens", "ipi_aliquota", "TEXT"),
    ("itens", "ipi_valor", "TEXT"),
    ("itens", "pis_cst", "TEXT"),
    ("itens", "pis_base", "TEXT"),
    ("itens", "pis_aliquota", "TEXT"),
    ("itens", "pis_valor", "TEXT"),
    ("itens", "pis_quantidade", "TEXT"),
    ("itens", "pis_valor_unidade", "TEXT"),
    ("itens", "cofins_cst", "TEXT"),
    ("itens", "cofins_base", "TEXT"),
    ("itens", "cofins_aliquota", "TEXT"),
    ("itens", "cofins_valor", "TEXT"),
    ("itens", "cofins_quantidade", "TEXT"),
    ("itens", "cofins_valor_unidade", "TEXT"),
    ("itens", "reforma_cst", "TEXT"),
    ("itens", "reforma_classificacao", "TEXT"),
    ("itens", "reforma_base", "TEXT"),
    ("itens", "ibs_uf_aliquota", "TEXT"),
    ("itens", "ibs_uf_valor", "TEXT"),
    ("itens", "ibs_mun_aliquota", "TEXT"),
    ("itens", "ibs_mun_valor", "TEXT"),
    ("itens", "ibs_valor", "TEXT"),
    ("itens", "cbs_aliquota", "TEXT"),
    ("itens", "cbs_valor", "TEXT"),
    ("itens", "is_cst", "TEXT"),
    ("itens", "is_base", "TEXT"),
    ("itens", "is_aliquota", "TEXT"),
    ("itens", "is_valor", "TEXT"),
    # REFORMA 2: o total declarado do documento (`IBSCBSTot`). Os índices do
    # escritório já existem e não nasceriam de novo por `CREATE TABLE IF NOT
    # EXISTS` — sem estas três linhas o INSERT quebraria na máquina de quem
    # já usa o sistema, e não aqui.
    ("documentos", "total_base_ibs_cbs", "TEXT"),
    ("documentos", "total_ibs", "TEXT"),
    ("documentos", "total_cbs", "TEXT"),
)


# Índices sobre COLUNAS ACRESCENTADAS. Não podem morar no `_ESQUEMA`: ele roda
# antes de `_colunas_faltantes`, e criar índice sobre coluna que ainda não
# existe derruba a conexão inteira — foi o que aconteceu ao acrescentar `cest`.
_INDICES_TARDIOS = (
    "CREATE INDEX IF NOT EXISTS ix_item_cest ON itens(cest);",
    "CREATE INDEX IF NOT EXISTS ix_item_cest_doc ON itens(cest, id_documento);",
    # CST e CSOSN entram na mesma forma dos de CFOP/NCM: o filtro é um EXISTS
    # correlacionado, e sem `id_documento` na segunda posição o SQLite percorre
    # todas as linhas do código antes de casar o documento.
    #   medido em 23/08/2026, 6.513 documentos e 37.733 itens:
    #       filtro por CSOSN     205 ms  ->  132 ms
    #       filtro por CST        31 ms  ->   17 ms
    #   O ganho do CSOSN é menor do que o do CFOP porque quase nenhum item tem
    #   CSOSN preenchido: o índice fica quase todo em NULL e ajuda pouco. Fica
    #   assim mesmo — 132 ms é aceitável, e a alternativa seria um índice
    #   parcial, que é complexidade sem retorno neste volume.
    "CREATE INDEX IF NOT EXISTS ix_item_cst_doc ON itens(icms_cst, id_documento);",
    "CREATE INDEX IF NOT EXISTS ix_item_csosn_doc ON itens(icms_csosn, id_documento);",
    # Filtrar o período pela saída/entrada percorreria a tabela inteira sem
    # isto. A coluna é preenchida em 99,5% dos documentos, então o índice é
    # quase do tamanho do de emissão — e serve pela mesma razão que aquele.
    "CREATE INDEX IF NOT EXISTS ix_doc_saient ON documentos(dh_saida_entrada);",
)


def _colunas_faltantes(con) -> None:
    for tabela, coluna, tipo in _ACRESCIMOS:
        existentes = {r[1] for r in con.execute(f"PRAGMA table_info({tabela})")}
        if coluna not in existentes:
            con.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")


@dataclass
class Indice:
    """O índice de UMA empresa."""
    dados_dir: Path
    identidade: str

    def __post_init__(self):
        self.dados_dir = Path(self.dados_dir)
        ident = normalizar(self.identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida para índice: {ident.motivo}")
        self.identidade = ident.valor

    # ── acesso ────────────────────────────────────────────────────────────
    def caminho(self, criar: bool = False) -> Path:
        p = self.dados_dir / self.identidade / PASTA
        if criar:
            p.mkdir(parents=True, exist_ok=True)
        return p / ARQUIVO

    @contextmanager
    def conectar(self):
        """Conexão com o esquema garantido.

        `closing` fecha sempre: no Windows um SQLite aberto não é apagado, e o
        teste de reconstrução precisa poder apagar o arquivo."""
        caminho = self.caminho(criar=True)
        con = sqlite3.connect(str(caminho))
        try:
            con.row_factory = sqlite3.Row
            con.executescript(_ESQUEMA)
            _colunas_faltantes(con)
            for sql in _INDICES_TARDIOS:
                con.execute(sql)
            con.execute("INSERT OR REPLACE INTO meta(chave, valor) VALUES(?,?)",
                        ("versao_esquema", str(VERSAO_ESQUEMA)))
            yield con
            con.commit()
        finally:
            con.close()

    # ── escrita ───────────────────────────────────────────────────────────
    def registrar(self, con, doc: dm.Documento, estado: str = INDEXADO,
                  quarentena: bool = False, hash_conteudo: str = "",
                  motivo: str = "") -> None:
        """Grava (ou regrava) um documento no índice. Idempotente por id."""
        d = doc.para_indice()
        d.update({"estado": estado, "quarentena": 1 if quarentena else 0,
                  "hash_conteudo": hash_conteudo or None, "motivo": motivo or None})
        colunas = ", ".join(d)
        marcas = ", ".join("?" * len(d))
        con.execute(f"INSERT OR REPLACE INTO documentos ({colunas}) VALUES ({marcas})",
                    tuple(d.values()))

        if doc.e_evento and isinstance(doc.extensao, dm.ExtensaoEvento):
            e = doc.extensao
            con.execute(
                "INSERT OR REPLACE INTO eventos (id_documento, chave, tp_evento, "
                "descricao, sequencia, dh_evento, protocolo, justificativa, orgao) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (doc.id_documento, doc.chave, e.tp_evento, e.descricao, e.sequencia,
                 doc.dh_emissao.isoformat() if doc.dh_emissao else None,
                 e.protocolo, e.justificativa, e.orgao))
            return

        if isinstance(doc.extensao, dm.ExtensaoCTe):
            con.execute("DELETE FROM cte WHERE id_documento = ?",
                        (doc.id_documento,))
            con.execute(
                f"INSERT OR REPLACE INTO cte ({', '.join(_CAMPOS_CTE)}) "
                f"VALUES ({', '.join('?' * len(_CAMPOS_CTE))})",
                _linha_do_cte(doc.id_documento, doc.extensao))

            # A ponte é regravada inteira: se o parser mudar de ideia sobre
            # quais chaves o documento carrega, o que sobrou da leitura antiga
            # não pode ficar para trás dizendo o contrário.
            con.execute("DELETE FROM cte_nfe WHERE id_documento = ?",
                        (doc.id_documento,))
            chaves = {c for c in (doc.extensao.chaves_nfe or ()) if c}
            if chaves:
                con.executemany(
                    "INSERT OR REPLACE INTO cte_nfe (id_documento, chave_nfe) "
                    "VALUES (?,?)",
                    [(doc.id_documento, c) for c in sorted(chaves)])
            return

        if isinstance(doc.extensao, dm.ExtensaoNFe) and doc.extensao.itens:
            con.execute("DELETE FROM itens WHERE id_documento = ?", (doc.id_documento,))
            con.executemany(
                f"INSERT OR REPLACE INTO itens ({', '.join(_CAMPOS_ITEM)}) "
                f"VALUES ({', '.join('?' * len(_CAMPOS_ITEM))})",
                [_linha_do_item(doc.id_documento, it) for it in doc.extensao.itens])

    def registrar_bruto(self, con, id_documento: str, especie: str, estado: str,
                        hash_conteudo: str = "", motivo: str = "",
                        quarentena: bool = False, chave: str = "") -> None:
        """Registra o que NÃO deu para interpretar.

        Schema desconhecido e falha de parser entram no índice assim mesmo — é
        assim que a tela consegue listar "o que ainda não sei ler" em vez de o
        documento sumir da vista por não ter linha."""
        con.execute(
            "INSERT OR REPLACE INTO documentos (id_documento, especie, chave, "
            "identidade_empresa, estado, quarentena, hash_conteudo, motivo) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (id_documento, especie, chave or None, self.identidade, estado,
             1 if quarentena else 0, hash_conteudo or None, motivo or None))

    # ── consulta ──────────────────────────────────────────────────────────
    def contar(self) -> dict[str, int]:
        with self.conectar() as con:
            linhas = con.execute(
                "SELECT estado, COUNT(*) n FROM documentos GROUP BY estado").fetchall()
            return {r["estado"]: r["n"] for r in linhas}

    def obter(self, id_documento: str) -> dict | None:
        with self.conectar() as con:
            r = con.execute("SELECT * FROM documentos WHERE id_documento = ?",
                            (id_documento,)).fetchone()
            return dict(r) if r else None

    def listar(self, especie: str = "", competencia_de: date | None = None,
               competencia_ate: date | None = None, estado: str = "",
               apenas_quarentena: bool = False) -> list[dict]:
        sql = ["SELECT * FROM documentos WHERE 1=1"]
        args: list = []
        if especie:
            sql.append("AND especie = ?"); args.append(especie)
        if estado:
            sql.append("AND estado = ?"); args.append(estado)
        if apenas_quarentena:
            sql.append("AND quarentena = 1")
        if competencia_de:
            sql.append("AND competencia >= ?"); args.append(competencia_de.isoformat())
        if competencia_ate:
            sql.append("AND competencia <= ?"); args.append(competencia_ate.isoformat())
        sql.append("ORDER BY competencia, dh_emissao, id_documento")
        with self.conectar() as con:
            return [dict(r) for r in con.execute(" ".join(sql), args).fetchall()]

    def total_por_competencia(self, competencia: date, especie: str = dm.NFE55):
        """Soma em `Decimal`. **Não usa `SUM()` do SQLite** — ele somaria como
        float, que é exatamente o que este módulo evita."""
        linhas = self.listar(especie=especie, competencia_de=competencia,
                             competencia_ate=competencia, estado=INDEXADO)
        valores = [_dec(l.get("valor_total")) for l in linhas]
        return dm.soma(*valores)

    def eventos_da_chave(self, chave: str) -> list[dict]:
        with self.conectar() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM eventos WHERE chave = ? ORDER BY sequencia",
                (chave,)).fetchall()]

    def ausentes(self, ac: acv.AcervoArquivos, especie: str = "") -> list[tuple]:
        """`(especie, id_documento)` que existem no ACERVO e faltam no índice.

        POR QUE ISTO PRECISOU DE NOME PRÓPRIO
            `desatualizados()` responde outra pergunta — quem foi lido por
            parser velho — e responde bem. Mas na migração M1 o acervo ficou
            com 510 documentos, o índice com 493, e `desatualizados()` devolveu
            **0**: ele varre a tabela do índice, e quem nunca entrou nela é
            invisível para ele. O índice não denunciava a própria defasagem.

            A lógica de achar o que falta existia — enterrada dentro de
            `pipeline.indexar_pendentes`, sem nome e sem como ser consultada
            por quem só quisesse **saber** se estava em dia. Aqui ela tem nome,
            e por isso pode ser testada e perguntada.
        """
        with self.conectar() as con:
            existentes = {r["id_documento"] for r in con.execute(
                "SELECT id_documento FROM documentos").fetchall()}
        return [(e, i) for e, i in ac.listar(especie) if i not in existentes]

    def promovidos(self, ac: acv.AcervoArquivos, especie: str = "") -> list[tuple]:
        """Documentos cujo CANÔNICO mudou no acervo depois de indexados.

        A TERCEIRA FORMA DE O ÍNDICE FICAR PARA TRÁS
            `ausentes()` acha quem nunca entrou. `desatualizados()` acha quem
            foi lido por parser velho. Nenhum dos dois acha o caso do meio:
            o documento **já está** no índice, foi lido pelo parser **atual**,
            e mesmo assim está errado — porque o acervo recebeu depois uma
            cópia mais completa e promoveu o canônico.

            É o fluxo normal da distribuição: o resumo `resNFe` chega primeiro
            e o `procNFe` vem depois. Sem esta função, o índice continuaria
            mostrando o resumo — sem itens, sem tributos e sem papel — de uma
            nota cujo documento completo já está preservado no disco.

            Descoberto por um teste da NF-e 4 que promovia um resumo e cobrava
            que a consulta passasse a devolver `COMPLETO`.

        COMO
            O índice grava o hash do conteúdo que INDEXOU. O captura sabe qual
            arquivo é o canônico de hoje e o hash de cada cópia. Se o hash do
            canônico atual não é o que o índice guardou, o canônico mudou.

            A comparação é feita pelo registro de cópias, sem abrir os XML: ler
            24 mil arquivos só para descobrir que estão em dia seria caro à toa.

            Atenção ao detalhe que custou uma investigação: promover **não**
            altera `cap["hash_conteudo"]` — esse campo continua sendo o hash da
            primeira cópia recebida. Quem muda é `cap["canonico"]`. Comparar o
            campo errado faz esta função devolver sempre vazio.
        """
        with self.conectar() as con:
            no_indice = {r["id_documento"]: r["hash_conteudo"] for r in
                         con.execute("SELECT id_documento, hash_conteudo "
                                     "FROM documentos").fetchall()}
        fora = []
        for e, i in ac.listar(especie):
            gravado = no_indice.get(i)
            if not gravado:
                continue                       # é `ausentes()`, não daqui
            cap = ac.ler_captura(e, i)
            canonico = cap.get("canonico") or acv.ORIGINAL
            atual = cap.get("hash_conteudo")
            for c in (cap.get("copias") or []):
                if c.get("arquivo") == canonico:
                    atual = c.get("hash") or atual
                    break
            if atual and atual != gravado:
                fora.append((e, i))
        return fora

    def em_dia_com(self, ac: acv.AcervoArquivos) -> bool:
        """Índice lógico == acervo lógico? Pergunta direta, resposta direta."""
        return not self.ausentes(ac)

    def desatualizados(self) -> list[str]:
        """Ids lidos por versão de parser mais antiga que a atual.

        Não confundir com `ausentes()`: aqui o documento **está** no índice, só
        foi lido por um parser velho. Quem nunca entrou no índice não aparece
        nesta lista — e é justamente por isso que `ausentes()` existe."""
        atuais = prs.versoes()
        fora: list[str] = []
        with self.conectar() as con:
            for r in con.execute(
                    "SELECT id_documento, parser, versao_parser FROM documentos "
                    "WHERE estado IN (?,?)", (INDEXADO, FALHA_PARSER)).fetchall():
                atual = atuais.get(r["parser"] or "")
                if atual is None or (r["versao_parser"] or 0) < atual:
                    fora.append(r["id_documento"])
        return fora


# ── indexação e reconstrução ────────────────────────────────────────────────
@dataclass
class RelatorioIndexacao:
    indexados: int = 0
    schema_desconhecido: int = 0
    falha_parser: int = 0
    quarentena: int = 0
    total: int = 0
    erros: list[str] = None

    def __post_init__(self):
        if self.erros is None:
            self.erros = []

    def resumo(self) -> dict:
        return {"total": self.total, "indexados": self.indexados,
                "schema_desconhecido": self.schema_desconhecido,
                "falha_parser": self.falha_parser, "quarentena": self.quarentena,
                "erros": len(self.erros)}


def indexar_documento(ind: Indice, con, ac: acv.AcervoArquivos, especie: str,
                      id_documento: str, trilha: aud.Trilha | None = None,
                      rel: RelatorioIndexacao | None = None) -> str:
    """Lê UM documento do acervo e grava no índice. Devolve o estado.

    **Falha aqui nunca derruba o lote**: o documento entra no índice com o
    estado do problema, e a varredura segue. Um XML que este parser não entende
    não pode esconder os outros novecentos que ele entende."""
    rel = rel or RelatorioIndexacao()
    cap = ac.ler_captura(especie, id_documento)
    quarentena = bool(cap.get("quarentena"))
    try:
        conteudo = ac.caminho_canonico(especie, id_documento).read_bytes()
    except OSError as exc:
        rel.erros.append(f"{id_documento}: {higienizar(exc)}")
        ind.registrar_bruto(con, id_documento, especie, FALHA_PARSER,
                            motivo=higienizar(exc), quarentena=quarentena)
        return FALHA_PARSER

    ident = idf.identificar(conteudo, chave_informada=cap.get("chave") or "")
    parser = prs.para(ident)

    if parser is None:
        ind.registrar_bruto(con, id_documento, especie, SCHEMA_DESCONHECIDO,
                            hash_conteudo=ident.hash_conteudo,
                            motivo=ident.motivo or "sem parser para esta espécie",
                            quarentena=quarentena, chave=ident.chave)
        rel.schema_desconhecido += 1
        if trilha:
            trilha.registrar(aud.INDEXACAO, id_documento=id_documento,
                             estado=SCHEMA_DESCONHECIDO, motivo=ident.motivo)
        return SCHEMA_DESCONHECIDO

    try:
        doc = parser.interpretar(conteudo, ident, identidade_empresa=ind.identidade)
    except Exception as exc:
        ind.registrar_bruto(con, id_documento, especie, FALHA_PARSER,
                            hash_conteudo=ident.hash_conteudo,
                            motivo=higienizar(exc), quarentena=quarentena,
                            chave=ident.chave)
        rel.falha_parser += 1
        rel.erros.append(f"{id_documento}: {higienizar(exc)}")
        if trilha:
            trilha.registrar(aud.PARSE, id_documento=id_documento, sucesso=False,
                             parser=parser.nome, versao_parser=parser.VERSAO,
                             motivo=higienizar(exc))
        return FALHA_PARSER

    estado = QUARENTENA if quarentena else INDEXADO
    ind.registrar(con, doc, estado=estado, quarentena=quarentena,
                  hash_conteudo=ident.hash_conteudo,
                  motivo=cap.get("motivo_quarentena") or "")
    if quarentena:
        rel.quarentena += 1
    else:
        rel.indexados += 1
    if trilha:
        trilha.registrar(aud.PARSE, id_documento=id_documento, sucesso=True,
                         parser=parser.nome, versao_parser=parser.VERSAO,
                         especie=doc.especie, chave=doc.chave or None)
    return estado


def reconstruir(dados_dir, identidade, apagar_antes: bool = True) -> RelatorioIndexacao:
    """Refaz o índice inteiro a partir do acervo. **Não toca na rede.**

    É o teste vivo da promessa deste desenho: se isto funciona, o índice é
    mesmo descartável e o acervo é mesmo suficiente."""
    ind = Indice(dados_dir=Path(dados_dir), identidade=str(identidade))
    ac = acv.abrir(dados_dir, identidade)
    trilha = aud.abrir(dados_dir, identidade)
    rel = RelatorioIndexacao()

    if apagar_antes:
        alvo = ind.caminho()
        if alvo.exists():
            with closing(sqlite3.connect(str(alvo))) as con:
                con.executescript(_ESQUEMA)
                con.execute("DELETE FROM documentos")
                con.execute("DELETE FROM eventos")
                con.execute("DELETE FROM itens")
                con.commit()

    itens = ac.listar()
    rel.total = len(itens)
    with ind.conectar() as con:
        for especie, id_doc in itens:
            indexar_documento(ind, con, ac, especie, id_doc, trilha=trilha, rel=rel)

    trilha.registrar(aud.INDEXACAO, resultado="reconstrucao", **rel.resumo())
    return rel


def reprocessar(dados_dir, identidade, somente_desatualizados: bool = True
                ) -> RelatorioIndexacao:
    """Relê documentos do disco com os parsers ATUAIS.

    O caso de uso é: subiu a versão do parser, e os documentos antigos precisam
    ser relidos. Nada é baixado de novo, nenhum checkpoint se move, e o acervo
    não é tocado — só o índice muda."""
    ind = Indice(dados_dir=Path(dados_dir), identidade=str(identidade))
    ac = acv.abrir(dados_dir, identidade)
    trilha = aud.abrir(dados_dir, identidade)
    rel = RelatorioIndexacao()

    if somente_desatualizados:
        alvos = set(ind.desatualizados())
        itens = [(e, i) for e, i in ac.listar() if i in alvos]
    else:
        itens = ac.listar()

    rel.total = len(itens)
    with ind.conectar() as con:
        for especie, id_doc in itens:
            indexar_documento(ind, con, ac, especie, id_doc, trilha=trilha, rel=rel)

    trilha.registrar(aud.REPROCESSAMENTO, versoes_parser=prs.versoes(), **rel.resumo())
    return rel


def abrir(dados_dir, identidade) -> Indice:
    return Indice(dados_dir=Path(dados_dir), identidade=str(identidade))


def indexar_ausentes(dados_dir, identidade,
                     trilha: aud.Trilha | None = None) -> RelatorioIndexacao:
    """Indexa o que está no acervo e falta no índice. **Idempotente.**

    Rodar de novo com tudo em dia não reindexa nada: `total` sai 0 e nenhuma
    linha é reescrita. Reindexar por precaução custaria I/O e, pior, apagaria a
    diferença entre "estava em dia" e "foi consertado agora".

    Não conhece checkpoint nem rede — de propósito. É por isso que o portão de
    importação pode chamar esta função sem herdar o mundo da distribuição."""
    ind = abrir(dados_dir, identidade)
    ac = acv.abrir(dados_dir, identidade)
    trilha = trilha or aud.abrir(dados_dir, identidade)
    rel = RelatorioIndexacao()
    # Ausentes E promovidos. Os dois casos terminam no mesmo lugar — reler o
    # canônico de hoje —, e separá-los em duas passadas só faria a reconciliação
    # ficar pela metade quando alguém chamasse uma e esquecesse a outra.
    pendentes = ind.ausentes(ac) + ind.promovidos(ac)
    rel.total = len(pendentes)
    if not pendentes:
        return rel
    with ind.conectar() as con:
        for especie, id_doc in pendentes:
            indexar_documento(ind, con, ac, especie, id_doc, trilha=trilha, rel=rel)
    return rel
