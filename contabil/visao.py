# -*- coding: utf-8 -*-
"""visao.py — a Visão Geral do Contábil: o estado, sem fechamento.

O PERCENTUAL É SOBRE O QUE FOI IMPORTADO, E SÓ SOBRE ISSO
    "80% conciliado" quer dizer: dos movimentos que ESTÃO no sistema, 80%
    estão conciliados. Não quer dizer que o mês está 80% pronto — o extrato
    de uma conta que ninguém importou não entra no denominador, porque o
    sistema não sabe que ela existe. A tela diz isso ao lado do número.

    Sem movimento importado, o percentual é `None` (a tela mostra "—"), e
    não 0% nem 100%: nenhum dos dois seria verdade.

SITUAÇÃO DA COMPETÊNCIA
    SEM_MOVIMENTO     nada importado e nada lançado nela;
    EM_ABERTO         há movimento pendente, sugestão, divergência ou
                      lançamento pendente;
    CONCILIADA        todo movimento importado está conciliado e não há
                      lançamento pendente.
    Não há "FECHADA": fechamento não existe nesta fase.
"""
from __future__ import annotations

from . import modelo
from .base import Base

SEM_MOVIMENTO = "SEM_MOVIMENTO"
EM_ABERTO = "EM_ABERTO"
CONCILIADA = "CONCILIADA"


def resumo(b: Base, competencia: str = "") -> dict:
    comp = modelo.competencia(competencia) if competencia else ""
    filtro_mov = " AND substr(data,1,7)=?" if comp else ""
    filtro_lanc = " AND competencia=?" if comp else ""
    a = (comp,) if comp else ()

    def um(sql, args=a):
        return b.con.execute(sql, args).fetchone()[0]

    importados = um("SELECT COUNT(*) FROM movimento WHERE 1=1" + filtro_mov)
    com_valor = um("SELECT COUNT(*) FROM movimento WHERE valor IS NOT NULL"
                   + filtro_mov)
    por_estado = {e: 0 for e in modelo.ESTADOS_CONCILIACAO}
    valor_estado = {e: 0 for e in modelo.ESTADOS_CONCILIACAO}
    for r in b.con.execute(
            "SELECT estado_conciliacao e, COUNT(*) n, COALESCE(SUM(ABS(valor)),0) v "
            "FROM movimento WHERE 1=1" + filtro_mov + " GROUP BY 1", a):
        por_estado[r["e"]] = r["n"]
        valor_estado[r["e"]] = r["v"]
    nao_ident = um(
        "SELECT COUNT(*) FROM movimento WHERE COALESCE(NULLIF("
        "categoria_confirmada,''), categoria)='NAO_IDENTIFICADO'" + filtro_mov)
    com_problema = um("SELECT COUNT(*) FROM movimento WHERE problema<>''"
                      + filtro_mov)
    lanc = {s: 0 for s in modelo.STATUS}
    for r in b.con.execute("SELECT status, COUNT(*) n FROM lancamento WHERE 1=1"
                           + filtro_lanc + " GROUP BY 1", a):
        lanc[r["status"]] = r["n"]
    entradas = um("SELECT COALESCE(SUM(valor),0) FROM movimento WHERE valor>0"
                  + filtro_mov)
    saidas = um("SELECT COALESCE(SUM(valor),0) FROM movimento WHERE valor<0"
                + filtro_mov)

    conciliados = por_estado[modelo.CONCILIADO]
    percentual = (round(100.0 * conciliados / importados, 1)
                  if importados else None)
    total_valor = sum(valor_estado.values())
    percentual_valor = (round(100.0 * valor_estado[modelo.CONCILIADO]
                              / total_valor, 1) if total_valor else None)

    if not importados and not sum(lanc.values()):
        situacao = SEM_MOVIMENTO
    elif conciliados == importados and not lanc[modelo.PENDENTE]:
        situacao = CONCILIADA
    else:
        situacao = EM_ABERTO

    # As competências que a tela oferece: as que já têm dado contábil MAIS as
    # que o acervo fiscal conhece — senão um mês nunca sincronizado não
    # apareceria na lista, e não haveria como sincronizá-lo a primeira vez.
    competencias = [r[0] for r in b.con.execute(
        "SELECT DISTINCT c FROM (SELECT substr(data,1,7) c FROM movimento WHERE "
        "data<>'' UNION SELECT competencia FROM lancamento "
        "UNION SELECT competencia FROM fato) WHERE c<>'' ORDER BY c DESC")]
    from . import fatos as _fatos
    for c in _fatos.competencias_no_fiscal(b.dados, b.empresa):
        if c not in competencias:
            competencias.append(c)
    competencias.sort(reverse=True)
    return {
        "competencia": comp,
        "competencias": competencias,
        "lancamentos_pendentes": lanc[modelo.PENDENTE],
        "lancamentos": lanc,
        "movimentos_importados": importados,
        "movimentos_com_valor": com_valor,
        "movimentos_com_problema": com_problema,
        "nao_identificados": nao_ident,
        "por_estado": por_estado,
        "divergencias": por_estado[modelo.DIVERGENTE],
        "total_conciliado": valor_estado[modelo.CONCILIADO],
        "total_pendente": (valor_estado[modelo.PENDENTE_CONC]
                           + valor_estado[modelo.SUGESTAO]),
        "total_divergente": valor_estado[modelo.DIVERGENTE],
        "entradas": entradas,
        "saidas": saidas,
        "percentual_conciliado": percentual,
        "percentual_conciliado_valor": percentual_valor,
        "base_do_percentual": "movimentos efetivamente importados",
        "situacao": situacao,
        "importacoes": b.con.execute("SELECT COUNT(*) FROM importacao").fetchone()[0],
        "contas_bancarias": b.con.execute(
            "SELECT COUNT(*) FROM conta_bancaria").fetchone()[0],
        "contas_no_plano": b.con.execute("SELECT COUNT(*) FROM conta").fetchone()[0],
    }
