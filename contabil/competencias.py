# -*- coding: utf-8 -*-
"""competencias.py — ABERTA → EM CONFERÊNCIA → FECHADA, e o que cada uma barra.

O QUE O FECHAMENTO É NESTA FASE
    Uma **tranca com nome e data**, não um processo contábil. Não apura, não
    transfere resultado, não gera balancete — nada disso existe ainda. O que
    ele faz é impedir que um mês conferido mude sem ninguém saber.

OS TRÊS ESTADOS
    ABERTA          o padrão. Competência sem linha na tabela é ABERTA — não
                    existe "estado desconhecido" nem migração para criar.
    EM_CONFERENCIA  sinaliza revisão em curso. **Não barra nada**: barrar aqui
                    só faria a pessoa fechar antes da hora para poder
                    trabalhar. É recado, e é honesto chamá-lo assim.
    FECHADA         barra a escrita: criar, confirmar, cancelar lançamento e
                    gerar lançamento do Fiscal naquela competência.

O QUE FECHADA NÃO BARRA, DE PROPÓSITO
    LEITURA e SINCRONIZAÇÃO DE FATOS. O acervo fiscal é a verdade e continua
    andando: uma nota cancelada depois do fechamento precisa aparecer — como
    DIVERGÊNCIA, à vista — em vez de ser escondida pela tranca. Fechar o mês
    não pode virar uma forma de não ficar sabendo.

CORREÇÃO DEPOIS DE FECHADO
    Não há alteração silenciosa: é preciso REABRIR, com motivo, e a reabertura
    fica no histórico com quem a fez. O caminho de correção continua sendo o
    lançamento de AJUSTE, que também exige o mês aberto.
"""
from __future__ import annotations

from . import modelo
from .base import Base, agora


class Fechada(modelo.Invalido):
    """A competência está fechada. É `Invalido` para a tela já tratar como
    recusa com mensagem, e tem tipo próprio para quem precisar distinguir."""


def estado(b: Base, competencia: str) -> str:
    comp = modelo.competencia(competencia)
    r = b.con.execute("SELECT estado FROM competencia WHERE competencia=?",
                      (comp,)).fetchone()
    return r["estado"] if r else modelo.ABERTA


def registro(b: Base, competencia: str) -> dict:
    comp = modelo.competencia(competencia)
    r = b.con.execute("SELECT * FROM competencia WHERE competencia=?",
                      (comp,)).fetchone()
    d = dict(r) if r else {"competencia": comp, "estado": modelo.ABERTA,
                           "usuario": "", "quando_utc": "", "motivo": ""}
    # Só as MUDANÇAS DE ESTADO. A sincronização também é um evento da
    # competência, mas ela acontece o tempo todo e afogaria o que este
    # histórico existe para mostrar: quem fechou, quem reabriu e por quê.
    d["historico"] = [h for h in b.eventos("COMPETENCIA", comp)
                      if "→" in h["acao"]]
    return d


def definir(b: Base, competencia: str, novo: str, usuario: str,
            motivo: str = "") -> dict:
    """Muda o estado. Reabrir uma competência FECHADA exige motivo."""
    if not (usuario or "").strip():
        raise modelo.Invalido("mudar o estado da competência exige saber quem muda")
    comp = modelo.competencia(competencia)
    alvo = modelo.conferir(novo, modelo.ESTADOS_COMPETENCIA, "estado")
    atual = estado(b, comp)
    if atual == alvo:
        return registro(b, comp)
    if atual == modelo.FECHADA and not (motivo or "").strip():
        raise modelo.Invalido(
            "reabrir uma competência fechada exige motivo — ele fica no "
            "histórico, e é o que evita a alteração silenciosa")
    b.con.execute(
        "INSERT INTO competencia (competencia, estado, usuario, quando_utc, "
        "motivo) VALUES (?,?,?,?,?) ON CONFLICT(competencia) DO UPDATE SET "
        "estado=excluded.estado, usuario=excluded.usuario, "
        "quando_utc=excluded.quando_utc, motivo=excluded.motivo",
        (comp, alvo, usuario, agora(), str(motivo or "").strip()[:300]))
    b.evento("COMPETENCIA", comp, "%s→%s" % (atual, alvo), usuario, motivo)
    return registro(b, comp)


def exigir_aberta(b: Base, competencia: str, o_que: str = "esta operação") -> None:
    """Barra a escrita em competência FECHADA. Chamado por quem escreve."""
    if not competencia:
        return
    try:
        comp = modelo.competencia(competencia)
    except modelo.Invalido:
        return
    if estado(b, comp) == modelo.FECHADA:
        raise Fechada(
            "a competência %s está FECHADA e não aceita %s. Reabra em "
            "Visão Geral, com o motivo, e a reabertura fica no histórico."
            % (comp, o_que))


def listar(b: Base) -> list:
    """Toda competência que TEM estado registrado. As demais são ABERTAS e
    não precisam de linha nenhuma para isso ser verdade."""
    return [dict(r) for r in b.con.execute(
        "SELECT * FROM competencia ORDER BY competencia DESC")]
