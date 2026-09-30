# -*- coding: utf-8 -*-
"""indice.py — o consultável da situação fiscal. É CACHE, e é descartável.

A REGRA QUE ORGANIZA TUDO
    **O documento é a verdade; o índice é conveniência.** Se os dois
    divergirem, o disco ganha e o índice se reconstrói. Por isso ele fica fora
    do backup, pode ser apagado a qualquer momento, e é refeito inteiro a
    partir das pastas — sem tocar a rede.

UM BANCO DO ESCRITÓRIO, NÃO UM POR EMPRESA
    A pergunta que o escritório faz não é "como está a MONTE?", é **"quem vence
    nos próximos 30 dias?"**. Essa consulta cruza todas as empresas. Um banco
    por empresa transformaria isso em 19 aberturas de arquivo e uma junção na
    memória, para uma pergunta que o SQLite responde num índice.

DUAS COISAS, DUAS CONTAS — e é aqui que isso vive
    CERTIDÃO responde pela VALIDADE que o órgão carimbou:
        vencida < hoje ≤ vence_em_breve ≤ válida
    EXTRATO responde pela IDADE do retrato:
        apurado ≤ frescor < desatualizado

    A mesma coluna não serve para as duas: um extrato não tem "até quando
    vale", e uma certidão não fica "desatualizada" enquanto o carimbo dela
    valer. Por isso são colunas diferentes (`validade` e `apurado_em`) e a
    derivação escolhe pela `tipo`.

TENTATIVA É OUTRA TABELA
    "Não emitida" chega com sucesso e sem documento: há pendência, o órgão
    recusa emitir. Isso é desfecho de negócio. Se morasse na tabela de
    documento, toda recusa criaria uma linha afirmando a existência de algo que
    não existe em pasta nenhuma.

DE ONDE VÊM VALIDADE E NATUREZA
    Do `extra` da captura, quando a FONTE as entregou estruturadas — é o caso
    da API, que devolve `DataValidade` e `TipoCertidao` em JSON, e isso é
    proveniência, não leitura nossa. Quando não vieram, de um `leitor`
    injetado, que abre o PDF. Sem nenhum dos dois, ficam vazias — e o estado
    derivado é `SEM_VALIDADE`, que é honesto.

O ÍNDICE TAMBÉM É O GUARDA DA BILHETAGEM
    A API Consulta CND é cobrada por chamada. `precisa_consultar()` responde se
    vale gastar: já existe documento válido em mãos? Sem isso, um laço de
    atualização consultaria 19 empresas por dia para reconfirmar o que já
    estava no disco.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

from . import modelo

ARQUIVO = "situacao.db"
VERSAO_ESQUEMA = 2

# A MIGRAÇÃO É SÓ ADITIVA, E É FEITA NA ABERTURA.
#     v1 -> v2 (central de regularidade, 12/09/2026): colunas novas e uma
#     tabela nova. Nenhuma coluna muda de sentido e nenhuma linha é reescrita:
#     um índice v1 aberto por código v2 ganha as colunas vazias, e o código v1
#     continua lendo um índice v2 (ele simplesmente não pede as colunas novas).
#     É o que permite voltar ao checkpoint sem restaurar banco nenhum.
_COLUNAS_V2 = (
    # NULL = o leitor não disse; 1 = a frase "não foram detectadas pendências"
    # foi lida. Não existe 0: ausência de frase não é presença de pendência.
    ("documento", "sem_pendencias", "INTEGER"),
    # QUEM e COMO. A auditoria da consulta mora na própria tentativa, porque
    # é ela que viaja no backup junto com o documento — o auditoria.db, de
    # propósito, não viaja (carrega IP e hábito de entrada).
    ("tentativa", "usuario", "TEXT NOT NULL DEFAULT ''"),
    ("tentativa", "metodo", "TEXT NOT NULL DEFAULT ''"),
    ("tentativa", "sha256", "TEXT NOT NULL DEFAULT ''"),
)

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- Um documento que EXISTE em pasta. A chave é o hash: o mesmo documento nunca
-- entra duas vezes, e não há id sequencial que dependa da ordem de chegada.
CREATE TABLE IF NOT EXISTS documento (
    id             TEXT NOT NULL,
    identidade     TEXT NOT NULL,
    esfera         TEXT NOT NULL,
    tipo           TEXT NOT NULL,
    jurisdicao     TEXT NOT NULL DEFAULT '',
    sha256         TEXT NOT NULL,
    bytes          INTEGER NOT NULL DEFAULT 0,
    caminho        TEXT NOT NULL DEFAULT '',
    origem         TEXT NOT NULL DEFAULT '',
    capturado_utc  TEXT NOT NULL DEFAULT '',
    -- derivados: da fonte (proveniência) ou de um leitor. Podem faltar.
    natureza       TEXT NOT NULL DEFAULT '',
    emissao        TEXT NOT NULL DEFAULT '',
    validade       TEXT NOT NULL DEFAULT '',      -- só CERTIDAO
    apurado_em     TEXT NOT NULL DEFAULT '',      -- só EXTRATO
    codigo         TEXT NOT NULL DEFAULT '',
    valor_total    REAL,                          -- NULL = não lido, ≠ zero
    leitura_versao INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (identidade, esfera, tipo, id)
);
CREATE INDEX IF NOT EXISTS ix_doc_empresa ON documento (identidade, esfera, tipo);
CREATE INDEX IF NOT EXISTS ix_doc_validade ON documento (validade);
CREATE INDEX IF NOT EXISTS ix_doc_apurado ON documento (apurado_em);

-- Um EVENTO. Nasce sempre, inclusive quando não veio documento.
CREATE TABLE IF NOT EXISTS tentativa (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    identidade   TEXT NOT NULL,
    esfera       TEXT NOT NULL,
    tipo         TEXT NOT NULL,
    quando_utc   TEXT NOT NULL,
    desfecho     TEXT NOT NULL,
    detalhe      TEXT NOT NULL DEFAULT '',
    origem       TEXT NOT NULL DEFAULT '',
    documento_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_tent_empresa ON tentativa (identidade, esfera, quando_utc);

-- O que o FISCALE CONCLUI de um documento, quando o leitor não basta e uma
-- pessoa abriu o PDF. É ESPELHO de `interpretacoes/*.json` no disco, ao lado
-- do original — reconstruível como o resto. Nunca altera o documento.
CREATE TABLE IF NOT EXISTS interpretacao (
    id                    TEXT PRIMARY KEY,
    identidade            TEXT NOT NULL,
    esfera                TEXT NOT NULL,
    tipo                  TEXT NOT NULL,
    documento_id          TEXT NOT NULL,
    status                TEXT NOT NULL,
    quantidade_pendencias INTEGER,
    valor_total           REAL,
    validade              TEXT NOT NULL DEFAULT '',
    pendencias            TEXT NOT NULL DEFAULT '[]',
    observacao            TEXT NOT NULL DEFAULT '',
    usuario               TEXT NOT NULL DEFAULT '',
    quando_utc            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_interp_doc ON interpretacao (identidade, esfera, documento_id);
"""


def _hoje(v=None) -> _dt.date:
    if v is None:
        return _dt.date.today()
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    return _dt.date.fromisoformat(str(v)[:10])


def _agora() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Indice:
    """O banco consultável. Fecha com `fechar()` ou como gerenciador."""

    def __init__(self, caminho):
        self.caminho = str(caminho)
        self.con = sqlite3.connect(self.caminho)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(_ESQUEMA)
        self._migrar()
        self.con.execute(
            "INSERT INTO meta (chave, valor) VALUES ('versao', ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (str(VERSAO_ESQUEMA),))
        self.con.commit()

    def _migrar(self):
        for tabela, coluna, tipo in _COLUNAS_V2:
            existentes = {r[1] for r in
                          self.con.execute("PRAGMA table_info(%s)" % tabela)}
            if coluna not in existentes:
                self.con.execute("ALTER TABLE %s ADD COLUMN %s %s"
                                 % (tabela, coluna, tipo))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.fechar()

    def fechar(self):
        try:
            self.con.close()
        except Exception:
            pass

    # ── escrita ──────────────────────────────────────────────────────────
    def registrar(self, captura: dict, leitura: dict = None) -> None:
        """Põe (ou atualiza) um documento no índice.

        `captura` é o que o armazenamento gravou. `leitura` são os campos
        derivados — da fonte ou de um leitor. Nada aqui abre PDF.
        """
        e, t = modelo.conferir(captura["esfera"], captura["tipo"])
        d = dict(leitura or {})
        # A FONTE JÁ PODE TER DITO. `extra` é o que a fonte entregou junto com
        # os bytes; é proveniência, não leitura nossa, e por isso vale.
        for campo in ("natureza", "emissao", "validade", "apurado_em",
                      "codigo", "valor_total", "sem_pendencias"):
            if campo not in d and campo in (captura.get("extra") or {}):
                d[campo] = captura["extra"][campo]

        self.con.execute(
            "INSERT INTO documento (id, identidade, esfera, tipo, jurisdicao,"
            " sha256, bytes, caminho, origem, capturado_utc, natureza,"
            " emissao, validade, apurado_em, codigo, valor_total,"
            " leitura_versao, sem_pendencias)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(identidade, esfera, tipo, id) DO UPDATE SET"
            "   caminho=excluded.caminho, natureza=excluded.natureza,"
            "   emissao=excluded.emissao, validade=excluded.validade,"
            "   apurado_em=excluded.apurado_em, codigo=excluded.codigo,"
            "   valor_total=excluded.valor_total,"
            "   leitura_versao=excluded.leitura_versao,"
            "   sem_pendencias=excluded.sem_pendencias",
            (captura["id"], captura["identidade"], e, t,
             str(d.get("jurisdicao") or captura.get("jurisdicao") or ""),
             captura["sha256"], int(captura.get("bytes") or 0),
             str(captura.get("caminho") or ""),
             str(captura.get("origem") or ""),
             str(captura.get("capturado_utc") or ""),
             str(d.get("natureza") or ""), str(d.get("emissao") or ""),
             str(d.get("validade") or ""), str(d.get("apurado_em") or ""),
             str(d.get("codigo") or ""),
             d.get("valor_total"),
             int(d.get("leitura_versao") or 0),
             1 if d.get("sem_pendencias") in (True, 1, "1") else None))
        self.con.commit()

    def registrar_tentativa(self, identidade: str, esfera: str, tipo: str,
                            desfecho: str, detalhe: str = "", origem: str = "",
                            documento_id: str = "", usuario: str = "",
                            metodo: str = "", sha256: str = "") -> None:
        e, t = modelo.conferir(esfera, tipo)
        if desfecho not in modelo.DESFECHOS:
            raise modelo.Invalido("desfecho desconhecido: %r" % (desfecho,))
        self.con.execute(
            "INSERT INTO tentativa (identidade, esfera, tipo, quando_utc,"
            " desfecho, detalhe, origem, documento_id, usuario, metodo, sha256)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (identidade, e, t, _agora(), desfecho, detalhe[:500], origem,
             documento_id, str(usuario or "")[:80], str(metodo or "")[:40],
             str(sha256 or "")))
        self.con.commit()

    # ── reconstrução ─────────────────────────────────────────────────────
    def reconstruir(self, armazens, leitor=None) -> int:
        """Refaz a tabela de documentos a partir do DISCO.

        `armazens` é um iterável de `armazenamento.Armazem`. `leitor(bytes)`
        é opcional e devolve os campos derivados; sem ele, o que a fonte não
        tiver dito fica vazio — e vazio é honesto.

        A TABELA DE TENTATIVAS NÃO É TOCADA. Ela é histórico de eventos, não
        reflexo do disco: reconstruir apagaria o registro de que houve uma
        recusa, que é exatamente o que ninguém consegue recuperar depois.
        """
        self.con.execute("DELETE FROM documento")
        n = 0
        for armazem in armazens:
            for captura in armazem.percorrer():
                leitura = None
                if leitor is not None:
                    try:
                        leitura = leitor(Path(captura["caminho"]).read_bytes())
                    except Exception:
                        leitura = None      # leitor que falha não perde o doc
                self.registrar(captura, leitura)
                n += 1
        self.con.commit()
        return n

    # ── consulta ─────────────────────────────────────────────────────────
    def documentos(self, identidade: str = "", esfera: str = "",
                   tipo: str = "") -> list:
        onde, args = [], []
        for coluna, valor in (("identidade", identidade), ("esfera", esfera),
                              ("tipo", tipo)):
            if valor:
                onde.append("%s = ?" % coluna)
                args.append(valor.upper() if coluna != "identidade" else valor)
        sql = "SELECT * FROM documento"
        if onde:
            sql += " WHERE " + " AND ".join(onde)
        # O MAIS RECENTE PRIMEIRO, e o critério é a captura — não a validade.
        # Duas certidões da mesma empresa podem ter a mesma validade; a que
        # vale é a que chegou depois.
        sql += " ORDER BY capturado_utc DESC, id DESC"
        return [dict(r) for r in self.con.execute(sql, args)]

    def vigente(self, identidade: str, esfera: str, tipo: str) -> dict:
        """O documento que responde por esta empresa nesta esfera, ou `{}`."""
        docs = self.documentos(identidade, esfera, tipo)
        return docs[0] if docs else {}

    def estado(self, identidade: str, esfera: str, tipo: str,
               hoje=None, dias_aviso: int = modelo.DIAS_AVISO,
               dias_frescor: int = modelo.DIAS_FRESCOR) -> dict:
        """O estado derivado — pela VALIDADE se certidão, pela IDADE se extrato.

        É aqui que a distinção vive. Uma coluna só, ou um `if` escondido no
        SQL, faria a tela dizer "desatualizada" de uma certidão que ainda vale,
        ou "válida" de um extrato de seis meses atrás.
        """
        e, t = modelo.conferir(esfera, tipo)
        doc = self.vigente(identidade, e, t)
        h = _hoje(hoje)
        if not doc:
            return {"identidade": identidade, "esfera": e, "tipo": t,
                    "estado": modelo.SEM_DOCUMENTO, "regular": None,
                    "documento": {}}

        if t == modelo.CERTIDAO:
            est = modelo.estado_certidao(doc.get("validade") or None, h,
                                         dias_aviso)
        else:
            est = modelo.estado_extrato(doc.get("apurado_em") or None, h,
                                        dias_frescor)

        natureza = doc.get("natureza") or modelo.NAO_LIDA
        return {"identidade": identidade, "esfera": e, "tipo": t,
                "estado": est,
                # `None` é "não sei", e não se confunde com `False`.
                "regular": modelo.regular(natureza) if natureza != modelo.NAO_LIDA
                           else None,
                "natureza": natureza,
                "documento": doc}

    def panorama(self, identidades, hoje=None, **kw) -> list:
        """Uma linha por empresa × esfera × tipo. É o que a tela desenha."""
        saida = []
        for ident in identidades:
            for esfera in modelo.ESFERAS:
                for tipo in modelo.TIPOS:
                    saida.append(self.estado(ident, esfera, tipo, hoje, **kw))
        return saida

    def a_vencer(self, dias: int = modelo.DIAS_AVISO, hoje=None) -> list:
        """Certidões que vencem em até `dias` — inclusive as já vencidas.

        SÓ CERTIDÃO. Extrato não vence, e incluí-lo aqui obrigaria a inventar
        uma data de vencimento que ninguém carimbou.
        """
        h = _hoje(hoje)
        limite = (h + _dt.timedelta(days=dias)).isoformat()
        linhas = self.con.execute(
            "SELECT * FROM documento WHERE tipo = ? AND validade != ''"
            " AND validade <= ? ORDER BY validade ASC",
            (modelo.CERTIDAO, limite))
        return [dict(r) for r in linhas]

    def precisa_consultar(self, identidade: str, esfera: str, tipo: str,
                          hoje=None, **kw) -> tuple:
        """`(precisa, motivo)` — o guarda da bilhetagem.

        A Consulta CND é cobrada por chamada. Sem este freio, uma rotina de
        atualização gastaria 19 chamadas por dia para reconfirmar o que já está
        no disco. `VALIDA` e `APURADO` são as duas únicas respostas que
        dispensam a consulta.
        """
        st = self.estado(identidade, esfera, tipo, hoje, **kw)
        e = st["estado"]
        if e == modelo.VALIDA:
            return False, "há certidão válida até %s" % (
                st["documento"].get("validade") or "?")
        if e == modelo.APURADO:
            return False, "há extrato apurado em %s" % (
                st["documento"].get("apurado_em") or "?")
        motivos = {
            modelo.SEM_DOCUMENTO: "não há documento",
            modelo.VENCIDA: "a certidão está vencida",
            modelo.VENCE_EM_BREVE: "a certidão vence em breve",
            modelo.DESATUALIZADO: "o extrato está desatualizado",
            modelo.SEM_VALIDADE: "o documento existe, mas não sabemos até quando vale",
        }
        return True, motivos.get(e, e)

    # ── interpretação ────────────────────────────────────────────────────
    def registrar_interpretacao(self, reg: dict) -> None:
        """Espelha um `interpretacoes/*.json` do disco. Idempotente pelo id."""
        import json as _json
        self.con.execute(
            "INSERT OR REPLACE INTO interpretacao (id, identidade, esfera, tipo,"
            " documento_id, status, quantidade_pendencias, valor_total,"
            " validade, pendencias, observacao, usuario, quando_utc)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (reg["id"], reg["identidade"], reg["esfera"], reg["tipo"],
             reg["documento_id"], reg["status"],
             reg.get("quantidade_pendencias"), reg.get("valor_total"),
             str(reg.get("validade") or ""),
             _json.dumps(reg.get("pendencias") or [], ensure_ascii=False),
             str(reg.get("observacao") or ""), str(reg.get("usuario") or ""),
             reg["quando_utc"]))
        self.con.commit()

    def interpretacoes(self, identidade: str = "", esfera: str = "") -> list:
        import json as _json
        sql, args, onde = "SELECT * FROM interpretacao", [], []
        if identidade:
            onde.append("identidade = ?")
            args.append(identidade)
        if esfera:
            onde.append("esfera = ?")
            args.append(esfera.upper())
        if onde:
            sql += " WHERE " + " AND ".join(onde)
        sql += " ORDER BY quando_utc DESC"
        saida = []
        for r in self.con.execute(sql, args):
            d = dict(r)
            try:
                d["pendencias"] = _json.loads(d.get("pendencias") or "[]")
            except ValueError:
                d["pendencias"] = []
            saida.append(d)
        return saida

    def tentativas_da_esfera(self, identidade: str, esfera: str,
                             limite: int = 200) -> list:
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM tentativa WHERE identidade = ? AND esfera = ?"
            " ORDER BY id DESC LIMIT ?",
            (identidade, esfera.upper(), int(limite)))]

    def tentativas(self, identidade: str = "", limite: int = 50) -> list:
        sql = "SELECT * FROM tentativa"
        args = []
        if identidade:
            sql += " WHERE identidade = ?"
            args.append(identidade)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limite))
        return [dict(r) for r in self.con.execute(sql, args)]


def abrir(raiz) -> Indice:
    """Abre (criando se preciso) o índice do escritório em `<raiz>/situacao.db`."""
    raiz = Path(raiz)
    raiz.mkdir(parents=True, exist_ok=True)
    return Indice(raiz / ARQUIVO)
