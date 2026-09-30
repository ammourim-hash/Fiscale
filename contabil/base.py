# -*- coding: utf-8 -*-
"""base.py — o banco contábil de UMA empresa.

UM ARQUIVO POR EMPRESA, E NÃO UMA COLUNA `empresa`
    `<FISCALE_DADOS>/<documento>/contabil/contabil.db`

    A Situação Fiscal usa um banco do escritório porque a pergunta dela cruza
    empresas ("quem vence nos próximos 30 dias?"). Aqui é o contrário: não
    existe pergunta contábil legítima que misture o razão de duas empresas.
    Com uma coluna `empresa`, bastaria UM `WHERE` esquecido para o extrato da
    empresa B aparecer na conciliação da A. Com um arquivo por empresa, o
    esquecimento não tem onde acontecer — a conexão aberta só enxerga uma.

ESTE BANCO É DADO PRIMÁRIO, NÃO CACHE
    Plano de contas, lançamentos e conciliações são decisões de gente, e não
    se reconstroem de lugar nenhum. Por isso ele fica em `<documento>/contabil/`,
    que o backup `.fbk` leva (a pasta não está em `PASTAS_FORA`), e por isso o
    journal é o padrão (DELETE), não WAL: o backup deixa `.db-wal` de fora, e
    com WAL a última gravação poderia ficar só no arquivo que não viaja.

MIGRAÇÃO SÓ ADITIVA
    Mesma regra da Situação Fiscal: coluna nova entra por `_ACRESCIMOS`, nada
    muda de sentido, nenhuma linha é reescrita na abertura.
"""
from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import modelo

PASTA = "contabil"
ARQUIVO = "contabil.db"
VERSAO_ESQUEMA = 1

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- ── Plano de contas ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conta (
    id            TEXT PRIMARY KEY,
    codigo        TEXT NOT NULL UNIQUE,
    descricao     TEXT NOT NULL,
    tipo          TEXT NOT NULL,              -- SINTETICA | ANALITICA
    natureza      TEXT NOT NULL,              -- DEVEDORA | CREDORA
    grupo         TEXT NOT NULL,
    codigo_pai    TEXT NOT NULL DEFAULT '',
    ativa         INTEGER NOT NULL DEFAULT 1,
    -- Mapeamento FUTURO para ECD/ECF. Guardado, nunca usado nesta fase.
    ref_ecd       TEXT NOT NULL DEFAULT '',
    ref_ecf       TEXT NOT NULL DEFAULT '',
    criado_utc    TEXT NOT NULL,
    criado_por    TEXT NOT NULL DEFAULT '',
    alterado_utc  TEXT NOT NULL DEFAULT '',
    alterado_por  TEXT NOT NULL DEFAULT ''
);

-- ── Lançamentos ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lancamento (
    id             TEXT PRIMARY KEY,
    numero         INTEGER NOT NULL UNIQUE,
    data           TEXT NOT NULL,
    competencia    TEXT NOT NULL,
    historico      TEXT NOT NULL,
    documento      TEXT NOT NULL DEFAULT '',
    origem         TEXT NOT NULL,
    lote           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,
    ajusta_id      TEXT NOT NULL DEFAULT '',
    criado_utc     TEXT NOT NULL,
    criado_por     TEXT NOT NULL DEFAULT '',
    confirmado_utc TEXT NOT NULL DEFAULT '',
    confirmado_por TEXT NOT NULL DEFAULT '',
    cancelado_utc  TEXT NOT NULL DEFAULT '',
    cancelado_por  TEXT NOT NULL DEFAULT '',
    motivo         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_lanc_comp ON lancamento (competencia, status);

CREATE TABLE IF NOT EXISTS partida (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lancamento_id  TEXT NOT NULL REFERENCES lancamento(id),
    conta_id       TEXT NOT NULL REFERENCES conta(id),
    tipo           TEXT NOT NULL,             -- D | C
    valor          INTEGER NOT NULL           -- centavos, sempre > 0
);
CREATE INDEX IF NOT EXISTS ix_part_lanc ON partida (lancamento_id);
CREATE INDEX IF NOT EXISTS ix_part_conta ON partida (conta_id);

-- A trilha: quem criou, confirmou, cancelou, ajustou. Só cresce.
CREATE TABLE IF NOT EXISTS evento (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    objeto      TEXT NOT NULL,                -- LANCAMENTO | MOVIMENTO | ...
    objeto_id   TEXT NOT NULL,
    acao        TEXT NOT NULL,
    usuario     TEXT NOT NULL DEFAULT '',
    quando_utc  TEXT NOT NULL,
    detalhe     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_evento_obj ON evento (objeto, objeto_id);

-- ── Bancos ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conta_bancaria (
    id                TEXT PRIMARY KEY,
    banco             TEXT NOT NULL DEFAULT '',
    agencia           TEXT NOT NULL DEFAULT '',
    numero            TEXT NOT NULL DEFAULT '',
    descricao         TEXT NOT NULL DEFAULT '',
    conta_contabil_id TEXT NOT NULL DEFAULT '',
    criado_utc        TEXT NOT NULL,
    UNIQUE (banco, agencia, numero)
);

-- Um ARQUIVO de extrato. A chave natural é o hash dos bytes.
CREATE TABLE IF NOT EXISTS importacao (
    id                 TEXT PRIMARY KEY,
    conta_bancaria_id  TEXT NOT NULL,
    sha256             TEXT NOT NULL UNIQUE,
    arquivo_id         TEXT NOT NULL,         -- pasta do original
    nome_arquivo       TEXT NOT NULL DEFAULT '',
    formato            TEXT NOT NULL,
    bytes              INTEGER NOT NULL,
    quando_utc         TEXT NOT NULL,
    usuario            TEXT NOT NULL DEFAULT '',
    linhas_total       INTEGER NOT NULL DEFAULT 0,
    movimentos_lidos   INTEGER NOT NULL DEFAULT 0,
    movimentos_novos   INTEGER NOT NULL DEFAULT 0,
    movimentos_existentes INTEGER NOT NULL DEFAULT 0,
    linhas_ignoradas   INTEGER NOT NULL DEFAULT 0,
    periodo_ini        TEXT NOT NULL DEFAULT '',
    periodo_fim        TEXT NOT NULL DEFAULT '',
    saldo_final        INTEGER,
    saldo_final_data   TEXT NOT NULL DEFAULT ''
);

-- O que NÃO virou movimento, com o motivo. É o que prova que nenhuma linha
-- sumiu: total de linhas = movimentos + ignoradas, sempre.
CREATE TABLE IF NOT EXISTS linha_ignorada (
    importacao_id  TEXT NOT NULL,
    linha          INTEGER NOT NULL,
    conteudo       TEXT NOT NULL,
    motivo         TEXT NOT NULL,
    PRIMARY KEY (importacao_id, linha)
);

CREATE TABLE IF NOT EXISTS movimento (
    id                 TEXT PRIMARY KEY,
    conta_bancaria_id  TEXT NOT NULL,
    identificador      TEXT NOT NULL,
    data               TEXT NOT NULL DEFAULT '',
    data_lancamento    TEXT NOT NULL DEFAULT '',
    descricao          TEXT NOT NULL DEFAULT '',
    documento          TEXT NOT NULL DEFAULT '',
    valor              INTEGER,               -- centavos; + entra, - sai
    sentido            TEXT NOT NULL DEFAULT '',
    saldo              INTEGER,
    tipo_operacao      TEXT NOT NULL DEFAULT '',
    id_transacao       TEXT NOT NULL DEFAULT '',   -- FITID do OFX
    banco              TEXT NOT NULL DEFAULT '',
    agencia            TEXT NOT NULL DEFAULT '',
    conta              TEXT NOT NULL DEFAULT '',
    importacao_id      TEXT NOT NULL,         -- o PRIMEIRO arquivo que o trouxe
    linha              INTEGER NOT NULL DEFAULT 0,
    pagina             INTEGER,
    problema           TEXT NOT NULL DEFAULT '',
    -- SUGESTÃO da classificação. Nunca é lançamento.
    categoria          TEXT NOT NULL DEFAULT 'NAO_IDENTIFICADO',
    sugestao           TEXT NOT NULL DEFAULT '',
    confianca          TEXT NOT NULL DEFAULT '',
    regra              TEXT NOT NULL DEFAULT '',
    contraparte_doc    TEXT NOT NULL DEFAULT '',
    contraparte_nome   TEXT NOT NULL DEFAULT '',
    par_id             TEXT NOT NULL DEFAULT '',   -- a outra ponta da transferência
    -- O que uma PESSOA disse. Vazio até alguém dizer.
    categoria_confirmada TEXT NOT NULL DEFAULT '',
    confirmada_por     TEXT NOT NULL DEFAULT '',
    estado_conciliacao TEXT NOT NULL DEFAULT 'PENDENTE',
    UNIQUE (conta_bancaria_id, identificador)
);
CREATE INDEX IF NOT EXISTS ix_mov_data ON movimento (data);
CREATE INDEX IF NOT EXISTS ix_mov_valor ON movimento (valor);
CREATE INDEX IF NOT EXISTS ix_mov_estado ON movimento (estado_conciliacao);

-- CADA linha de CADA arquivo aponta para um movimento. Um movimento que veio
-- em dois extratos sobrepostos tem duas linhas aqui e uma só na tabela acima.
-- `ordem` é a posição do movimento no arquivo (1, 2, 3...). A chave não é a
-- linha porque há OFX inteiro numa linha só: todas as transações teriam
-- linha 1, e a segunda colidiria com a primeira.
CREATE TABLE IF NOT EXISTS movimento_origem (
    movimento_id   TEXT NOT NULL,
    importacao_id  TEXT NOT NULL,
    ordem          INTEGER NOT NULL,
    linha          INTEGER NOT NULL,
    pagina         INTEGER,
    PRIMARY KEY (importacao_id, ordem)
);
CREATE INDEX IF NOT EXISTS ix_movorig_mov ON movimento_origem (movimento_id);

-- ── Conciliação ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conciliacao (
    id             TEXT PRIMARY KEY,
    movimento_id   TEXT NOT NULL,
    alvo_tipo      TEXT NOT NULL,             -- LANCAMENTO | DOCUMENTO_FISCAL
    alvo_id        TEXT NOT NULL,
    alvo_resumo    TEXT NOT NULL DEFAULT '{}',
    estado         TEXT NOT NULL,             -- CONCILIADO | DIVERGENTE | SUGESTAO
    valor_alvo     INTEGER,
    diferenca      INTEGER NOT NULL DEFAULT 0,
    motivo         TEXT NOT NULL DEFAULT '',
    usuario        TEXT NOT NULL DEFAULT '',
    quando_utc     TEXT NOT NULL,
    desfeita_utc   TEXT NOT NULL DEFAULT '',
    desfeita_por   TEXT NOT NULL DEFAULT '',
    desfeita_motivo TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_conc_mov ON conciliacao (movimento_id);
CREATE INDEX IF NOT EXISTS ix_conc_alvo ON conciliacao (alvo_tipo, alvo_id);

-- ── Fatos contábeis vindos do Fiscal (Contábil 2) ───────────────────────
-- UM fato por DOCUMENTO. A chave natural é o `id_documento` do acervo — o
-- mesmo que a apuração e o rastreio usam. É por ele que sincronizar duas
-- vezes não cria dois fatos, e é por ele que se responde "qual lançamento
-- nasceu desta nota?".
--
-- Nada aqui é cópia do XML: o fato guarda o CAMINHO do original e o hash. O
-- documento continua sendo do acervo fiscal, que o Contábil só lê.
CREATE TABLE IF NOT EXISTS fato (
    id                TEXT PRIMARY KEY,
    id_documento      TEXT NOT NULL UNIQUE,
    competencia       TEXT NOT NULL,
    origem            TEXT NOT NULL,          -- FISCAL
    especie           TEXT NOT NULL,          -- NFE55 | NFCE65 | NFSE | CTE57
    tipo              TEXT NOT NULL,          -- o que é, para a contabilidade
    natureza_fiscal   TEXT NOT NULL DEFAULT '',  -- VENDA/TRANSFERENCIA/... (vendas.py)
    sentido           TEXT NOT NULL DEFAULT '',
    situacao          TEXT NOT NULL DEFAULT '',
    entra_na_receita  INTEGER,
    valor             INTEGER,                -- centavos
    retencoes         INTEGER NOT NULL DEFAULT 0,
    retencoes_detalhe TEXT NOT NULL DEFAULT '{}',
    numero            TEXT NOT NULL DEFAULT '',
    serie             TEXT NOT NULL DEFAULT '',
    chave             TEXT NOT NULL DEFAULT '',
    data_emissao      TEXT NOT NULL DEFAULT '',
    contraparte_doc   TEXT NOT NULL DEFAULT '',
    contraparte_nome  TEXT NOT NULL DEFAULT '',
    cfops             TEXT NOT NULL DEFAULT '',
    arquivo           TEXT NOT NULL DEFAULT '',
    caminho           TEXT NOT NULL DEFAULT '',   -- até o XML original
    hash_conteudo     TEXT NOT NULL DEFAULT '',
    fonte_leitor      TEXT NOT NULL DEFAULT '',
    papel_debito      TEXT NOT NULL DEFAULT '',   -- sugestão, em PAPEL
    papel_credito     TEXT NOT NULL DEFAULT '',
    motivo            TEXT NOT NULL DEFAULT '',
    estado            TEXT NOT NULL DEFAULT 'SEM_LANCAMENTO',
    lancamento_id     TEXT NOT NULL DEFAULT '',
    sincronizado_utc  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_fato_comp ON fato (competencia, tipo);
CREATE INDEX IF NOT EXISTS ix_fato_lanc ON fato (lancamento_id);

-- Papel → conta do plano DESTA empresa. É o que permite sugerir lançamento
-- sem adivinhar conta nenhuma.
CREATE TABLE IF NOT EXISTS mapa_conta (
    papel        TEXT PRIMARY KEY,
    conta_id     TEXT NOT NULL,
    definido_utc TEXT NOT NULL DEFAULT '',
    definido_por TEXT NOT NULL DEFAULT ''
);

-- Fechamento por competência. Linha só existe depois que alguém mexeu no
-- estado; competência sem linha é ABERTA.
CREATE TABLE IF NOT EXISTS competencia (
    competencia TEXT PRIMARY KEY,
    estado      TEXT NOT NULL,
    usuario     TEXT NOT NULL DEFAULT '',
    quando_utc  TEXT NOT NULL DEFAULT '',
    motivo      TEXT NOT NULL DEFAULT ''
);

-- Candidatos achados pela busca automática. São SUGESTÃO e só isso.
CREATE TABLE IF NOT EXISTS candidato (
    movimento_id   TEXT NOT NULL,
    alvo_tipo      TEXT NOT NULL,
    alvo_id        TEXT NOT NULL,
    alvo_resumo    TEXT NOT NULL DEFAULT '{}',
    pontuacao      INTEGER NOT NULL DEFAULT 0,
    motivo         TEXT NOT NULL DEFAULT '',
    quando_utc     TEXT NOT NULL,
    PRIMARY KEY (movimento_id, alvo_tipo, alvo_id)
);
"""

_ACRESCIMOS: tuple = ()


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def novo_id() -> str:
    return secrets.token_hex(12)


def pasta(dados, empresa: str) -> Path:
    return Path(dados) / modelo.empresa_valida(empresa) / PASTA


def caminho(dados, empresa: str) -> Path:
    return pasta(dados, empresa) / ARQUIVO


def existe(dados, empresa: str) -> bool:
    return caminho(dados, empresa).exists()


class Base:
    """Uma conexão aberta no banco de UMA empresa."""

    def __init__(self, con: sqlite3.Connection, empresa: str, dados: Path):
        self.con = con
        self.empresa = empresa
        self.dados = Path(dados)

    def evento(self, objeto, objeto_id, acao, usuario="", detalhe=""):
        self.con.execute(
            "INSERT INTO evento (objeto, objeto_id, acao, usuario, quando_utc,"
            " detalhe) VALUES (?,?,?,?,?,?)",
            (objeto, objeto_id, acao, usuario or "", agora(),
             str(detalhe or "")[:500]))

    def eventos(self, objeto, objeto_id) -> list:
        return [dict(r) for r in self.con.execute(
            "SELECT acao, usuario, quando_utc, detalhe FROM evento WHERE "
            "objeto=? AND objeto_id=? ORDER BY id", (objeto, objeto_id))]


@contextmanager
def abrir(dados, empresa: str, criar: bool = True):
    """Abre o banco da empresa. Tudo o que acontece dentro do `with` é UMA
    transação: ou grava inteiro, ou não grava nada."""
    ident = modelo.empresa_valida(empresa)
    arq = caminho(dados, ident)
    if not criar and not arq.exists():
        raise FileNotFoundError(str(arq))
    arq.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(arq), timeout=15)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(_ESQUEMA)
        for tabela, coluna, tipo in _ACRESCIMOS:
            existentes = {r[1] for r in con.execute(
                "PRAGMA table_info(%s)" % tabela)}
            if coluna not in existentes:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s"
                            % (tabela, coluna, tipo))
        con.execute("INSERT OR IGNORE INTO meta (chave, valor) VALUES (?,?)",
                    ("empresa", ident))
        con.execute("INSERT OR REPLACE INTO meta (chave, valor) VALUES (?,?)",
                    ("versao_esquema", str(VERSAO_ESQUEMA)))
        dono = con.execute("SELECT valor FROM meta WHERE chave='empresa'"
                           ).fetchone()[0]
        if dono != ident:
            # Um banco copiado para a pasta de outra empresa. Abrir assim
            # misturaria as duas — e é exatamente o que este arquivo evita.
            raise modelo.Invalido(
                "o banco contábil desta pasta pertence a outra empresa")
        con.commit()
        b = Base(con, ident, Path(dados))
        yield b
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
