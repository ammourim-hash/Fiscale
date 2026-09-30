# -*- coding: utf-8 -*-
"""lancamentos.py — partida dobrada, e nada nasce definitivo.

AS DUAS REGRAS QUE NÃO SE NEGOCIAM
    1. Débito = crédito, ao centavo, em inteiro. Um lançamento que não fecha é
       recusado ANTES de tocar o banco — não existe "grava e avisa".
    2. Todo lançamento nasce PENDENTE. `criar()` não aceita status: não há
       parâmetro que permita a um lançamento automático (banco, fiscal,
       importação) entrar como definitivo. Quem torna definitivo é
       `confirmar()`, e ele exige o nome de quem confirma.

O CICLO DE VIDA
    PENDENTE ──confirmar──▶ CONFIRMADO ──(ajuste confirmado)──▶ AJUSTADO
        │
        └──cancelar──▶ CANCELADO

    Lançamento CONFIRMADO não é editado nem cancelado: é corrigido por um
    lançamento de AJUSTE (origem AJUSTE, `ajusta_id` apontando para ele), que
    também nasce pendente. Quando o ajuste é confirmado, o original passa a
    AJUSTADO — continua no razão, com o ajuste ao lado. Apagar não existe.
"""
from __future__ import annotations

from . import competencias, modelo, plano
from .base import Base, agora, novo_id


def _partidas(b: Base, partidas) -> list:
    """Valida e normaliza as partidas. Recusa antes de gravar qualquer coisa."""
    if not isinstance(partidas, (list, tuple)) or len(partidas) < 2:
        raise modelo.Invalido("o lançamento precisa de ao menos duas partidas")
    saida = []
    for p in partidas:
        if not isinstance(p, dict):
            raise modelo.Invalido("partida mal formada")
        tipo = str(p.get("tipo") or "").strip().upper()[:1]
        if tipo not in (modelo.DEBITO, modelo.CREDITO):
            raise modelo.Invalido("cada partida é D (débito) ou C (crédito)")
        conta_id = p.get("conta_id") or ""
        if not conta_id and p.get("codigo"):
            c = plano.por_codigo(b, p["codigo"])
            conta_id = c["id"] if c else ""
        c = plano.lancavel(b, conta_id)
        valor = modelo.centavos(p.get("valor"))
        if valor <= 0:
            raise modelo.Invalido(
                "o valor de cada partida é positivo; o lado é dito por D/C")
        saida.append({"conta_id": c["id"], "codigo": c["codigo"],
                      "tipo": tipo, "valor": valor})
    deb = sum(p["valor"] for p in saida if p["tipo"] == modelo.DEBITO)
    cred = sum(p["valor"] for p in saida if p["tipo"] == modelo.CREDITO)
    if not deb or not cred:
        raise modelo.Invalido("partida dobrada exige débito e crédito")
    if deb != cred:
        raise modelo.Invalido(
            "lançamento desequilibrado: débitos R$ %s, créditos R$ %s "
            "(diferença R$ %s)" % (modelo.reais(deb), modelo.reais(cred),
                                   modelo.reais(deb - cred)))
    return saida


def criar(b: Base, data, competencia, historico, partidas, origem,
          documento="", lote="", usuario="", ajusta_id="") -> dict:
    """Cria um lançamento PENDENTE. Não existe outra forma de nascer."""
    d = modelo.data_iso(data)
    comp = modelo.competencia(competencia or d)
    hist = str(historico or "").strip()
    if not hist:
        raise modelo.Invalido("o lançamento precisa de histórico")
    orig = modelo.conferir(origem, modelo.ORIGENS, "origem")
    # Competência fechada não recebe lançamento novo — nem de ajuste. Para
    # corrigir um mês fechado, reabre-se com motivo, e a reabertura fica no
    # histórico (Contábil 2).
    competencias.exigir_aberta(b, comp, "lançamento novo")
    if ajusta_id:
        alvo = obter(b, ajusta_id)
        if not alvo:
            raise modelo.Invalido("o lançamento a ajustar não existe")
        if alvo["status"] != modelo.CONFIRMADO:
            raise modelo.Invalido("só lançamento CONFIRMADO recebe ajuste")
        orig = modelo.AJUSTE
    elif orig == modelo.AJUSTE:
        raise modelo.Invalido("ajuste precisa dizer qual lançamento ajusta")
    ps = _partidas(b, partidas)

    lid = novo_id()
    numero = (b.con.execute("SELECT COALESCE(MAX(numero),0)+1 FROM lancamento"
                            ).fetchone()[0])
    b.con.execute(
        "INSERT INTO lancamento (id, numero, data, competencia, historico, "
        "documento, origem, lote, status, ajusta_id, criado_utc, criado_por) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (lid, numero, d, comp, hist[:500], str(documento or "")[:120], orig,
         str(lote or "")[:80], modelo.PENDENTE, ajusta_id or "", agora(),
         usuario or ""))
    b.con.executemany(
        "INSERT INTO partida (lancamento_id, conta_id, tipo, valor) "
        "VALUES (?,?,?,?)",
        [(lid, p["conta_id"], p["tipo"], p["valor"]) for p in ps])
    b.evento("LANCAMENTO", lid, "CRIADO", usuario, orig)
    return obter(b, lid)


def obter(b: Base, lancamento_id: str) -> dict | None:
    r = b.con.execute("SELECT * FROM lancamento WHERE id=?",
                      (lancamento_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["partidas"] = [dict(p) for p in b.con.execute(
        "SELECT p.tipo, p.valor, p.conta_id, c.codigo, c.descricao, c.grupo "
        "FROM partida p JOIN conta c ON c.id = p.conta_id "
        "WHERE p.lancamento_id=? ORDER BY p.tipo DESC, p.id", (lancamento_id,))]
    d["total"] = sum(p["valor"] for p in d["partidas"]
                     if p["tipo"] == modelo.DEBITO)
    return d


def confirmar(b: Base, lancamento_id: str, usuario: str) -> dict:
    if not (usuario or "").strip():
        raise modelo.Invalido("confirmar exige saber quem confirma")
    lc = obter(b, lancamento_id)
    if not lc:
        raise modelo.Invalido("lançamento não encontrado")
    if lc["status"] != modelo.PENDENTE:
        raise modelo.Invalido("só lançamento PENDENTE é confirmado (este está "
                              "%s)" % lc["status"])
    competencias.exigir_aberta(b, lc["competencia"], "confirmação de lançamento")
    # Confere de novo: a conta pode ter sido desativada depois da criação.
    _partidas(b, [{"conta_id": p["conta_id"], "tipo": p["tipo"],
                   "valor": p["valor"]} for p in lc["partidas"]])
    b.con.execute("UPDATE lancamento SET status=?, confirmado_utc=?, "
                  "confirmado_por=? WHERE id=?",
                  (modelo.CONFIRMADO, agora(), usuario, lancamento_id))
    b.evento("LANCAMENTO", lancamento_id, "CONFIRMADO", usuario)
    if lc["ajusta_id"]:
        b.con.execute("UPDATE lancamento SET status=? WHERE id=? AND status=?",
                      (modelo.AJUSTADO, lc["ajusta_id"], modelo.CONFIRMADO))
        b.evento("LANCAMENTO", lc["ajusta_id"], "AJUSTADO", usuario,
                 "ajuste nº %s" % lc["numero"])
    # A conciliação que esperava por este lançamento passa a valer.
    from . import conciliacao
    conciliacao.lancamento_confirmado(b, lancamento_id, usuario)
    return obter(b, lancamento_id)


def cancelar(b: Base, lancamento_id: str, usuario: str, motivo: str) -> dict:
    if not (motivo or "").strip():
        raise modelo.Invalido("cancelar exige motivo")
    lc = obter(b, lancamento_id)
    if not lc:
        raise modelo.Invalido("lançamento não encontrado")
    if lc["status"] != modelo.PENDENTE:
        raise modelo.Invalido(
            "só lançamento PENDENTE é cancelado; o confirmado é corrigido "
            "por um lançamento de ajuste")
    competencias.exigir_aberta(b, lc["competencia"], "cancelamento de lançamento")
    b.con.execute("UPDATE lancamento SET status=?, cancelado_utc=?, "
                  "cancelado_por=?, motivo=? WHERE id=?",
                  (modelo.CANCELADO, agora(), usuario or "", motivo.strip()[:300],
                   lancamento_id))
    b.evento("LANCAMENTO", lancamento_id, "CANCELADO", usuario, motivo)
    from . import conciliacao, fatos
    conciliacao.lancamento_cancelado(b, lancamento_id, usuario)
    # O fato fiscal volta a esperar lançamento: deixá-lo dizendo LANCADO
    # apontando para um lançamento cancelado seria cobertura fantasma.
    fatos.lancamento_cancelado(b, lancamento_id)
    return obter(b, lancamento_id)


def listar(b: Base, competencia="", status="", origem="", texto="",
           conta_id="", limite: int = 500) -> list:
    sql = ["SELECT l.* FROM lancamento l WHERE 1=1"]
    args = []
    if competencia:
        sql.append("AND l.competencia=?"); args.append(modelo.competencia(competencia))
    if status:
        sql.append("AND l.status=?")
        args.append(modelo.conferir(status, modelo.STATUS, "status"))
    if origem:
        sql.append("AND l.origem=?")
        args.append(modelo.conferir(origem, modelo.ORIGENS, "origem"))
    if texto:
        sql.append("AND (l.historico LIKE ? OR l.documento LIKE ? OR l.lote LIKE ?)")
        args += ["%" + texto + "%"] * 3
    if conta_id:
        sql.append("AND EXISTS (SELECT 1 FROM partida p WHERE "
                   "p.lancamento_id=l.id AND p.conta_id=?)")
        args.append(conta_id)
    sql.append("ORDER BY l.data DESC, l.numero DESC LIMIT ?")
    args.append(int(limite))
    saida = []
    for r in b.con.execute(" ".join(sql), args):
        d = dict(r)
        d["total"] = b.con.execute(
            "SELECT COALESCE(SUM(valor),0) FROM partida WHERE lancamento_id=? "
            "AND tipo='D'", (d["id"],)).fetchone()[0]
        saida.append(d)
    return saida


def valor_na_conta(b: Base, lancamento_id: str, conta_id: str) -> int | None:
    """Quanto o lançamento movimenta NESTA conta: débito positivo, crédito
    negativo. None se ele não toca a conta."""
    linhas = b.con.execute(
        "SELECT tipo, valor FROM partida WHERE lancamento_id=? AND conta_id=?",
        (lancamento_id, conta_id)).fetchall()
    if not linhas:
        return None
    return sum(v if t == modelo.DEBITO else -v for t, v in linhas)
