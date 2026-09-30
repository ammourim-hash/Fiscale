# -*- coding: utf-8 -*-
"""conciliacao.py — banco → documento fiscal / lançamento.

OS QUATRO ESTADOS DE UM MOVIMENTO
    CONCILIADO   ligado por uma pessoa a um documento fiscal, ou a um
                 lançamento CONFIRMADO, e os valores batem ao centavo;
    DIVERGENTE   ligado, mas o valor (ou o sentido) não bate — e quem ligou
                 escreveu o motivo (retenção, pagamento parcial, juros...);
    SUGESTÃO     há candidato achado pela busca, ou ligação com lançamento
                 ainda PENDENTE. Nada aqui é definitivo;
    PENDENTE     nada ligado e nada achado.

    O estado do movimento é DERIVADO das ligações ativas (`recalcular`), e
    nunca escrito à mão: estado guardado à parte do que o justifica diverge.

A BUSCA ACHA, A PESSOA LIGA
    `procurar()` grava candidatos na tabela `candidato`. Ela não cria
    ligação: um candidato com mesmo valor e mesma data ainda pode ser outra
    nota. `conciliar()` é o ato humano, e exige usuário.

DESFAZER NÃO APAGA
    A ligação desfeita fica, com quem desfez, quando e por quê.
"""
from __future__ import annotations

import json
from datetime import date

from . import fiscal, lancamentos, modelo
from .base import Base, agora, novo_id

JANELA_LANCAMENTO_DIAS = 7
# Recebe-se depois de emitir; às vezes adiantado. A janela é larga porque o
# prazo de pagamento de nota é largo — e o valor exato continua obrigatório.
DOC_ANTES_DIAS = 5
DOC_DEPOIS_DIAS = 120
MAX_CANDIDATOS = 5


def _dias(a: str, b: str) -> int | None:
    try:
        return (date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days
    except (TypeError, ValueError):
        return None


def link_ativo(b: Base, mov_id: str, alvo_tipo: str) -> dict | None:
    r = b.con.execute(
        "SELECT * FROM conciliacao WHERE movimento_id=? AND alvo_tipo=? AND "
        "desfeita_utc='' ORDER BY quando_utc DESC LIMIT 1",
        (mov_id, alvo_tipo)).fetchone()
    return dict(r) if r else None


def _alvo_ocupado(b: Base, alvo_tipo: str, alvo_id: str, mov_id: str) -> bool:
    """O documento já está ligado a OUTRO movimento? (Um lançamento de
    transferência pode estar ligado às duas pontas — por isso a exceção.)"""
    r = b.con.execute(
        "SELECT movimento_id FROM conciliacao WHERE alvo_tipo=? AND alvo_id=? "
        "AND desfeita_utc='' AND movimento_id<>?", (alvo_tipo, alvo_id, mov_id)
    ).fetchall()
    if not r:
        return False
    if alvo_tipo == modelo.ALVO_LANCAMENTO:
        par = b.con.execute("SELECT par_id FROM movimento WHERE id=?",
                            (mov_id,)).fetchone()
        return any(x["movimento_id"] != (par["par_id"] if par else "") for x in r)
    return True


def recalcular(b: Base, mov_id: str) -> str:
    ativos = [dict(r) for r in b.con.execute(
        "SELECT estado FROM conciliacao WHERE movimento_id=? AND desfeita_utc=''",
        (mov_id,))]
    estados = {a["estado"] for a in ativos}
    if modelo.DIVERGENTE in estados:
        e = modelo.DIVERGENTE
    elif modelo.CONCILIADO in estados:
        e = modelo.CONCILIADO
    elif modelo.SUGESTAO in estados:
        e = modelo.SUGESTAO
    elif candidatos(b, mov_id):
        e = modelo.SUGESTAO
    else:
        e = modelo.PENDENTE_CONC
    b.con.execute("UPDATE movimento SET estado_conciliacao=? WHERE id=?",
                  (e, mov_id))
    return e


# ── Valor do alvo ─────────────────────────────────────────────────────────
def _valor_do_lancamento(b: Base, mov: dict, lanc: dict) -> int:
    """O valor que o lançamento põe NO BANCO deste movimento, com sinal."""
    cb = b.con.execute("SELECT conta_contabil_id FROM conta_bancaria WHERE id=?",
                       (mov["conta_bancaria_id"],)).fetchone()
    if cb and cb["conta_contabil_id"]:
        v = lancamentos.valor_na_conta(b, lanc["id"], cb["conta_contabil_id"])
        if v is not None:
            return v
    total = lanc["total"]
    return total if (mov["valor"] or 0) > 0 else -total


def _valor_do_documento(mov: dict, doc: dict) -> int | None:
    alvo = abs(mov["valor"] or 0)
    vals = doc.get("valores") or []
    if not vals:
        return None
    melhor = min(vals, key=lambda v: abs(abs(v) - alvo))
    return abs(melhor) if doc["sentido"] == modelo.ENTRADA else -abs(melhor)


def _resumo_doc(doc: dict) -> dict:
    return {k: doc.get(k) for k in (
        "fonte", "especie", "numero", "serie", "data", "competencia", "valor",
        "valores", "sentido", "papel", "contraparte_doc", "contraparte_nome",
        "situacao", "operacao", "chave", "arquivo")}


def _resumo_lanc(lc: dict) -> dict:
    return {"numero": lc["numero"], "data": lc["data"], "status": lc["status"],
            "historico": lc["historico"], "total": lc["total"],
            "origem": lc["origem"]}


# ── Ligar ─────────────────────────────────────────────────────────────────
def ligar(b: Base, mov_id: str, alvo_tipo: str, alvo_id: str, usuario: str,
          motivo: str = "", docs=None) -> dict:
    mov = b.con.execute("SELECT * FROM movimento WHERE id=?", (mov_id,)).fetchone()
    if not mov:
        raise modelo.Invalido("movimento não encontrado")
    mov = dict(mov)
    if mov["valor"] is None:
        raise modelo.Invalido("movimento sem valor não é conciliado")
    alvo_tipo = modelo.conferir(alvo_tipo, modelo.ALVOS, "alvo")
    if link_ativo(b, mov_id, alvo_tipo):
        raise modelo.Invalido("este movimento já tem %s ligado; desfaça antes"
                              % ("lançamento" if alvo_tipo == modelo.ALVO_LANCAMENTO
                                 else "documento fiscal"))
    if _alvo_ocupado(b, alvo_tipo, alvo_id, mov_id):
        raise modelo.Invalido("este %s já está ligado a outro movimento"
                              % ("lançamento" if alvo_tipo == modelo.ALVO_LANCAMENTO
                                 else "documento"))
    motivo = (motivo or "").strip()

    if alvo_tipo == modelo.ALVO_LANCAMENTO:
        lc = lancamentos.obter(b, alvo_id)
        if not lc:
            raise modelo.Invalido("lançamento não encontrado nesta empresa")
        if lc["status"] == modelo.CANCELADO:
            raise modelo.Invalido("lançamento cancelado não concilia")
        valor_alvo = _valor_do_lancamento(b, mov, lc)
        resumo = _resumo_lanc(lc)
        diferenca = mov["valor"] - valor_alvo
        if diferenca:
            estado = modelo.DIVERGENTE
        elif lc["status"] == modelo.PENDENTE:
            estado = modelo.SUGESTAO        # vira CONCILIADO na confirmação
        else:
            estado = modelo.CONCILIADO
    else:
        doc = None
        for d in (docs if docs is not None else fiscal.documentos(b.dados, b.empresa)):
            if d["alvo_id"] == alvo_id:
                doc = d
                break
        if not doc:
            raise modelo.Invalido("documento fiscal não encontrado no acervo "
                                  "desta empresa")
        valor_alvo = _valor_do_documento(mov, doc)
        if valor_alvo is None:
            raise modelo.Invalido("o documento não tem valor lido")
        resumo = _resumo_doc(doc)
        diferenca = mov["valor"] - valor_alvo
        estado = modelo.DIVERGENTE if diferenca else modelo.CONCILIADO

    if estado == modelo.DIVERGENTE and not motivo:
        raise modelo.Invalido(
            "os valores não batem (movimento R$ %s, %s R$ %s): diga o motivo "
            "para registrar como DIVERGENTE" % (
                modelo.reais(mov["valor"]),
                "lançamento" if alvo_tipo == modelo.ALVO_LANCAMENTO else "documento",
                modelo.reais(valor_alvo)))
    cid = novo_id()
    b.con.execute(
        "INSERT INTO conciliacao (id, movimento_id, alvo_tipo, alvo_id, "
        "alvo_resumo, estado, valor_alvo, diferenca, motivo, usuario, quando_utc)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, mov_id, alvo_tipo, alvo_id, json.dumps(resumo, ensure_ascii=False),
         estado, valor_alvo, diferenca, motivo[:300], usuario or "", agora()))
    b.evento("MOVIMENTO", mov_id, "LIGADO", usuario,
             "%s %s → %s" % (alvo_tipo, alvo_id[:40], estado))
    recalcular(b, mov_id)
    _recalcular_quem_esperava(b, alvo_tipo, alvo_id)
    return obter(b, cid)


def _recalcular_quem_esperava(b: Base, alvo_tipo: str, alvo_id: str) -> None:
    """Outros movimentos que tinham este alvo como candidato. Ligado aqui, ele
    deixa de valer para eles; desligado, volta a valer."""
    for r in b.con.execute("SELECT movimento_id FROM candidato WHERE "
                           "alvo_tipo=? AND alvo_id=?",
                           (alvo_tipo, alvo_id)).fetchall():
        recalcular(b, r[0])


def conciliar(b: Base, mov_id: str, alvo_tipo: str, alvo_id: str, usuario: str,
              motivo: str = "", docs=None) -> dict:
    """O ato humano de conciliar. Sem usuário, não há ato."""
    if not (usuario or "").strip():
        raise modelo.Invalido("conciliar exige saber quem concilia")
    return ligar(b, mov_id, alvo_tipo, alvo_id, usuario, motivo, docs)


def obter(b: Base, conc_id: str) -> dict | None:
    r = b.con.execute("SELECT * FROM conciliacao WHERE id=?", (conc_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["alvo_resumo"] = json.loads(d["alvo_resumo"] or "{}")
    return d


def desfazer(b: Base, conc_id: str, usuario: str, motivo: str) -> dict:
    if not (motivo or "").strip():
        raise modelo.Invalido("desfazer exige motivo")
    c = obter(b, conc_id)
    if not c:
        raise modelo.Invalido("conciliação não encontrada")
    if c["desfeita_utc"]:
        raise modelo.Invalido("esta conciliação já foi desfeita")
    b.con.execute("UPDATE conciliacao SET desfeita_utc=?, desfeita_por=?, "
                  "desfeita_motivo=? WHERE id=?",
                  (agora(), usuario or "", motivo.strip()[:300], conc_id))
    b.evento("MOVIMENTO", c["movimento_id"], "DESLIGADO", usuario, motivo)
    recalcular(b, c["movimento_id"])
    _recalcular_quem_esperava(b, c["alvo_tipo"], c["alvo_id"])
    return obter(b, conc_id)


def lancamento_confirmado(b: Base, lanc_id: str, usuario: str) -> None:
    lc = lancamentos.obter(b, lanc_id)
    for r in b.con.execute(
            "SELECT id, movimento_id FROM conciliacao WHERE alvo_tipo=? AND "
            "alvo_id=? AND desfeita_utc='' AND estado=?",
            (modelo.ALVO_LANCAMENTO, lanc_id, modelo.SUGESTAO)).fetchall():
        b.con.execute("UPDATE conciliacao SET estado=?, alvo_resumo=? WHERE id=?",
                      (modelo.CONCILIADO,
                       json.dumps(_resumo_lanc(lc), ensure_ascii=False), r["id"]))
        recalcular(b, r["movimento_id"])


def lancamento_cancelado(b: Base, lanc_id: str, usuario: str) -> None:
    for r in b.con.execute(
            "SELECT id, movimento_id FROM conciliacao WHERE alvo_tipo=? AND "
            "alvo_id=? AND desfeita_utc=''",
            (modelo.ALVO_LANCAMENTO, lanc_id)).fetchall():
        b.con.execute("UPDATE conciliacao SET desfeita_utc=?, desfeita_por=?, "
                      "desfeita_motivo=? WHERE id=?",
                      (agora(), usuario or "", "lançamento cancelado", r["id"]))
        recalcular(b, r["movimento_id"])


def ligacoes(b: Base, mov_id: str, incluir_desfeitas=True) -> list:
    sql = "SELECT id FROM conciliacao WHERE movimento_id=?"
    if not incluir_desfeitas:
        sql += " AND desfeita_utc=''"
    return [obter(b, r["id"]) for r in b.con.execute(sql + " ORDER BY quando_utc",
                                                     (mov_id,))]


# ── Procurar ──────────────────────────────────────────────────────────────
def procurar(b: Base, mov_id: str, docs=None) -> list:
    """Acha candidatos e os grava como SUGESTÃO. Não liga nada."""
    mov = b.con.execute("SELECT * FROM movimento WHERE id=?", (mov_id,)).fetchone()
    if not mov:
        raise modelo.Invalido("movimento não encontrado")
    mov = dict(mov)
    b.con.execute("DELETE FROM candidato WHERE movimento_id=?", (mov_id,))
    if mov["valor"] in (None, 0) or not mov["data"]:
        recalcular(b, mov_id)
        return []
    achados = []

    # Lançamentos que movimentam a conta contábil deste banco pelo mesmo valor.
    cb = b.con.execute("SELECT conta_contabil_id FROM conta_bancaria WHERE id=?",
                       (mov["conta_bancaria_id"],)).fetchone()
    if cb and cb["conta_contabil_id"]:
        for r in b.con.execute(
                "SELECT DISTINCT l.id FROM lancamento l JOIN partida p ON "
                "p.lancamento_id=l.id WHERE p.conta_id=? AND l.status<>?",
                (cb["conta_contabil_id"], modelo.CANCELADO)):
            lc = lancamentos.obter(b, r["id"])
            if lancamentos.valor_na_conta(b, lc["id"], cb["conta_contabil_id"]) \
                    != mov["valor"]:
                continue
            dd = _dias(mov["data"], lc["data"])
            if dd is None or abs(dd) > JANELA_LANCAMENTO_DIAS:
                continue
            if _alvo_ocupado(b, modelo.ALVO_LANCAMENTO, lc["id"], mov_id):
                continue
            achados.append((modelo.ALVO_LANCAMENTO, lc["id"], _resumo_lanc(lc),
                            80 - abs(dd), "valor exato na conta do banco; "
                            "%d dia(s) de distância" % abs(dd)))

    # Documentos fiscais do acervo desta empresa.
    if docs is None:
        docs = fiscal.documentos(b.dados, b.empresa)
    sentido = modelo.ENTRADA if mov["valor"] > 0 else modelo.SAIDA
    alvo_valor = abs(mov["valor"])
    for d in docs:
        if d["sentido"] != sentido or alvo_valor not in [abs(v) for v in d["valores"]]:
            continue
        if d.get("cancelada") or "CANCEL" in (d.get("situacao") or "").upper():
            continue
        dd = _dias(mov["data"], d["data"])
        if dd is None or dd < -DOC_ANTES_DIAS or dd > DOC_DEPOIS_DIAS:
            continue
        if _alvo_ocupado(b, modelo.ALVO_DOCUMENTO, d["alvo_id"], mov_id):
            continue
        pontos, porque = 50, ["valor exato"]
        if mov["contraparte_doc"] and mov["contraparte_doc"] == d["contraparte_doc"]:
            pontos += 40
            porque.append("mesmo CNPJ/CPF")
        elif len(modelo.normalizar_texto(mov["contraparte_nome"]).split()) >= 2 \
                and modelo.normalizar_texto(d["contraparte_nome"]).split()[:2] == \
                modelo.normalizar_texto(mov["contraparte_nome"]).split()[:2]:
            pontos += 20
            porque.append("mesmo nome")
        pontos += max(0, 10 - abs(dd) // 7)
        porque.append("%d dia(s) após a emissão" % dd if dd >= 0
                      else "%d dia(s) antes da emissão" % -dd)
        achados.append((modelo.ALVO_DOCUMENTO, d["alvo_id"], _resumo_doc(d),
                        pontos, "; ".join(porque)))

    achados.sort(key=lambda x: -x[3])
    for tipo, alvo, resumo, pontos, porque in achados[:MAX_CANDIDATOS]:
        b.con.execute(
            "INSERT OR REPLACE INTO candidato (movimento_id, alvo_tipo, alvo_id, "
            "alvo_resumo, pontuacao, motivo, quando_utc) VALUES (?,?,?,?,?,?,?)",
            (mov_id, tipo, alvo, json.dumps(resumo, ensure_ascii=False), pontos,
             porque, agora()))
    recalcular(b, mov_id)
    return candidatos(b, mov_id)


def candidatos(b: Base, mov_id: str) -> list:
    """Os candidatos AINDA VÁLIDOS: some o do tipo que o movimento já ligou,
    e some o documento que outro movimento ligou depois da busca. Mostrar um
    botão "conciliar" que só pode dar erro é pior que não mostrar."""
    ja = {r[0] for r in b.con.execute(
        "SELECT alvo_tipo FROM conciliacao WHERE movimento_id=? AND "
        "desfeita_utc=''", (mov_id,))}
    saida = []
    for r in b.con.execute("SELECT * FROM candidato WHERE movimento_id=? ORDER "
                           "BY pontuacao DESC", (mov_id,)):
        d = dict(r)
        if d["alvo_tipo"] in ja or _alvo_ocupado(b, d["alvo_tipo"], d["alvo_id"],
                                                  mov_id):
            continue
        d["alvo_resumo"] = json.loads(d["alvo_resumo"] or "{}")
        saida.append(d)
    return saida


# ── Rastreio ──────────────────────────────────────────────────────────────
def rastreio(b: Base, mov_id: str) -> dict:
    """Banco → Movimento → Documento Fiscal → Operação → Lançamento, e de
    volta ao arquivo e à linha de onde o movimento saiu."""
    mov = b.con.execute("SELECT * FROM movimento WHERE id=?", (mov_id,)).fetchone()
    if not mov:
        raise modelo.Invalido("movimento não encontrado")
    mov = dict(mov)
    cb = b.con.execute("SELECT * FROM conta_bancaria WHERE id=?",
                       (mov["conta_bancaria_id"],)).fetchone()
    origens = [dict(r) for r in b.con.execute(
        "SELECT o.importacao_id, o.ordem, o.linha, o.pagina, i.nome_arquivo, "
        "i.sha256, i.formato, i.quando_utc FROM movimento_origem o JOIN "
        "importacao i ON i.id=o.importacao_id WHERE o.movimento_id=? "
        "ORDER BY i.quando_utc", (mov_id,))]
    docs, lancs = [], []
    for c in ligacoes(b, mov_id, incluir_desfeitas=False):
        if c["alvo_tipo"] == modelo.ALVO_DOCUMENTO:
            docs.append({"conciliacao": c["id"], "estado": c["estado"],
                         "alvo_id": c["alvo_id"], **c["alvo_resumo"],
                         "operacao": c["alvo_resumo"].get("operacao", "")})
        else:
            lc = lancamentos.obter(b, c["alvo_id"])
            if lc:
                lancs.append({"conciliacao": c["id"], "estado": c["estado"],
                              **{k: lc[k] for k in ("id", "numero", "data",
                                                    "competencia", "historico",
                                                    "status", "origem",
                                                    "documento", "lote")},
                              "partidas": lc["partidas"]})
    return {"banco": dict(cb) if cb else None, "movimento": mov,
            "arquivos": origens, "documentos_fiscais": docs,
            "lancamentos": lancs}
