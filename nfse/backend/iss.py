# -*- coding: utf-8 -*-
"""iss.py — o ISS que a empresa efetivamente recolhe em guia municipal no período.

DUAS PARCELAS
    próprio             ISS das notas de SAÍDA que NÃO tiveram retenção — a
                        empresa prestou o serviço e ela mesma recolhe;
    retido de terceiros ISS das notas de ENTRADA em que a EMPRESA, como
                        tomadora, reteve o imposto (tpRetISSQN = 2) — ela é a
                        responsável pelo recolhimento em nome do prestador.

O SIMPLES NACIONAL NÃO ENTRA NO PRÓPRIO — E ISSO FOI MEDIDO
    Nota de optante (opSimpNac 2 = MEI, 3 = ME/EPP) tem o ISS DENTRO do DAS.
    Muitas trazem vISSQN preenchido mesmo assim: no acervo real de 13/09/2026,
    uma única empresa do Simples somava R$ 196.448,30 de vISSQN em 80 notas
    de saída sem retenção. Somar isso como "ISS a recolher" em guia municipal
    cobraria o mesmo imposto duas vezes. Esse valor aparece à parte, como
    `proprio_no_das`, e NÃO entra no total.

    (Exceção legal que a nota não revela: empresa do Simples acima do sublimite
    estadual recolhe o ISS fora do DAS — LC 123, art. 13-A. Quando for o caso,
    a apuração é manual; o sistema avisa em vez de adivinhar.)

RETIDO POR INTERMEDIÁRIO (tpRetISSQN = 3) NÃO É DA EMPRESA
    Só entra o retido quando a empresa é a TOMADORA da nota. Retenção feita por
    intermediário é responsabilidade dele.

UMA GUIA POR MUNICÍPIO
    O ISS é devido ao município de incidência (cLocIncid). Somar municípios
    diferentes num número só esconde que são guias separadas — por isso há o
    detalhamento `por_municipio`.

ESTE MÓDULO NÃO TOCA DISCO NEM REDE. Recebe notas já lidas por core.parse_nfse.
"""
from __future__ import annotations

OPTANTE_SIMPLES = ("2", "3")        # opSimpNac: 1 = não optante, 2 = MEI, 3 = ME/EPP


def _dig(v) -> str:
    return "".join(c for c in str(v or "") if c.isdigit())


def _v(n, campo) -> float:
    try:
        return float(n.get(campo) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def iss_a_recolher(notas, cnpj_empresa: str) -> dict:
    """O ISS a recolher das notas do período. `notas` = saídas E entradas, já
    filtradas pelo período; canceladas são ignoradas aqui."""
    cnpj = _dig(cnpj_empresa)
    proprio = retido = no_das = 0.0
    qtd_proprio = qtd_retido = qtd_no_das = 0
    por_mun: dict = {}

    def mun(n):
        cod = str(n.get("municipio_incidencia") or "").strip() or "?"
        return por_mun.setdefault(cod, {"municipio": cod, "proprio": 0.0,
                                         "retido_terceiros": 0.0, "total": 0.0})

    for n in notas or []:
        if n.get("cancelada"):
            continue
        valor = _v(n, "valor_iss")
        if valor <= 0:
            continue
        tp = str(n.get("tp_ret_iss") or "").strip()
        emitente = _dig(n.get("emit_cnpj"))
        tomador = _dig(n.get("toma_doc"))

        if emitente == cnpj:
            # SAÍDA. Retida pelo tomador ou intermediário: quem recolhe é ele.
            if tp in ("2", "3"):
                continue
            if str(n.get("op_simp_nac") or "").strip() in OPTANTE_SIMPLES:
                no_das += valor
                qtd_no_das += 1
                continue
            proprio += valor
            qtd_proprio += 1
            m = mun(n)
            m["proprio"] += valor
            m["total"] += valor
        elif tomador == cnpj and tp == "2":
            # ENTRADA com retenção feita pela própria empresa, como tomadora.
            retido += valor
            qtd_retido += 1
            m = mun(n)
            m["retido_terceiros"] += valor
            m["total"] += valor

    municipios = sorted(
        ({k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()}
         for m in por_mun.values()),
        key=lambda m: -m["total"])
    avisos = []
    if no_das > 0:
        avisos.append(
            "R$ %s de ISS em %d nota%s de optante do Simples Nacional estão dentro "
            "do DAS e não entram na guia municipal. Se a empresa ultrapassou o "
            "sublimite estadual, o ISS sai do DAS e a apuração é manual."
            % (_brl(no_das), qtd_no_das, "" if qtd_no_das == 1 else "s"))
    if len(municipios) > 1:
        avisos.append("Há ISS em %d municípios: é uma guia para cada um."
                      % len(municipios))
    total = round(proprio + retido, 2)
    return {
        "total": total,
        "proprio": round(proprio, 2),
        "retido_terceiros": round(retido, 2),
        "proprio_no_das": round(no_das, 2),
        "qtd_notas_proprio": qtd_proprio,
        "qtd_notas_retido": qtd_retido,
        "qtd_notas_no_das": qtd_no_das,
        "por_municipio": municipios,
        "avisos": avisos,
    }


def _brl(v: float) -> str:
    inteiro, dec = ("%.2f" % v).split(".")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return ".".join(grupos) + "," + dec
