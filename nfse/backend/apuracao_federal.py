"""
apuracao_federal.py — PIS e COFINS mensais de Lucro Presumido e Lucro Real,
com as retenções sofridas abatidas, para alimentar o MIT (DCTFWeb).

O MIT não calcula nada: ele recebe os débitos apurados e os créditos. Este
módulo produz esses números a partir das notas que o Fiscale já captura.

ESCOPO — o que ESTE módulo faz e o que NÃO faz
----------------------------------------------
FAZ:
  - receita bruta do mês, das notas emitidas (serviços e mercadorias);
  - PIS e COFINS devidos, pela alíquota do regime;
  - retenções sofridas na fonte (PIS, COFINS, CSLL e IRRF), lidas do XML;
  - o líquido a recolher de PIS e COFINS.

NÃO FAZ (e não deve fingir que faz):
  - créditos de PIS/COFINS do Lucro Real que não vêm de nota fiscal
    (depreciação, energia, aluguel, arrendamento, mão de obra). No Real, o
    valor daqui é DÉBITO BRUTO — falta descontar crédito;
  - IRPJ e CSLL do Lucro Real: partem do lucro contábil ajustado no LALUR,
    que não existe em XML nenhum;
  - IRPJ/CSLL do Presumido, que são TRIMESTRAIS (ver apuracao_presumido.py
    quando existir);
  - receitas sem nota (financeiras, aluguéis) — entram por lançamento manual.

Alíquotas (Leis 9.715/1998 e 9.718/1998 no cumulativo; 10.637/2002 e
10.833/2003 no não cumulativo).
"""

from __future__ import annotations

import re
from pathlib import Path

# regime -> (PIS, COFINS, rótulo, cumulativo?)
ALIQUOTAS = {
    "presumido": (0.0065, 0.0300, "cumulativo", True),
    "real":      (0.0165, 0.0760, "não cumulativo", False),
}


def _so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def emitidas_do_mes(notas, cnpj: str, competencia: str) -> list[dict]:
    """Notas EMITIDAS pela empresa na competência. A retenção na fonte é sofrida
    por quem PRESTA o serviço — nota recebida traz retenção de terceiro e não
    pode entrar aqui.

    PÚBLICA desde a memória de cálculo. Ela precisa listar EXATAMENTE as notas
    que formaram a base de PIS/COFINS — e uma cópia do filtro em outro módulo
    seria uma segunda opinião sobre o que entra na base, que divergiria no dia
    em que uma das duas mudasse.
    """
    cd = _so_digitos(cnpj)
    return [n for n in notas
            if (n.get("competencia") or "")[:7] == competencia
            and not n.get("cancelada")
            and _so_digitos(n.get("emit_cnpj")) == cd]


# O nome antigo continua valendo: era usado aqui dentro e é barato manter.
_emitidas_do_mes = emitidas_do_mes


def retencoes_de_entrada(notas_recebidas) -> dict:
    """O quadro tributário das notas de ENTRADA (serviços tomados).

    NOTA DE ENTRADA NÃO TEM APURAÇÃO SOBRE FATURAMENTO.
        DAS, PIS/COFINS do Presumido/Real e IRPJ/CSLL incidem sobre a RECEITA
        da empresa — as notas que ela EMITE. Um serviço tomado é despesa: pôr
        o quadro de apuração em cima dele sugeria imposto a pagar sobre o que
        a empresa comprou. Aqui não há alíquota, regime nem devido.

    O QUE A ENTRADA PODE TER: RETENÇÃO NA FONTE.
        Quando a empresa é tomadora, ela pode ter retido do prestador IRRF,
        contribuições sociais (CSRF), INSS e ISS. É isso — e só isso — que o
        quadro mostra. Sem nenhuma retenção, `tem_retencao` é False e a tela
        não desenha nada.

    Recebe as notas JÁ filtradas (papel recebidas, período, válidas).
    """
    notas = list(notas_recebidas or [])

    def soma(campo, marca=None):
        return round(sum(n.get(campo, 0.0) or 0.0 for n in notas
                         if marca is None or n.get(marca)), 2)

    ret = {
        "csrf": soma("valor_retido"),
        "irrf": soma("valor_irrf"),
        "inss": soma("valor_inss", "inss_retido"),
        "iss": soma("valor_iss_retido", "iss_retido"),
    }
    total = round(sum(ret.values()), 2)
    com = sum(1 for n in notas
              if (n.get("valor_retido") or 0) > 0 or (n.get("valor_irrf") or 0) > 0
              or (n.get("inss_retido") and (n.get("valor_inss") or 0) > 0)
              or (n.get("iss_retido") and (n.get("valor_iss_retido") or 0) > 0))
    return {
        "papel": "recebidas",
        "apuracao": False,
        "tem_retencao": total > 0,
        "retencoes": ret,
        "total_retido": total,
        "qtd_notas_com_retencao": com,
    }


def apurar(notas, cnpj: str, competencia: str, regime: str,
           receita_extra: float = 0.0,
           credito_pis: float = 0.0, credito_cofins: float = 0.0) -> dict:
    """Apura PIS/COFINS de um mês. `notas` vem de core.carregar_notas()."""
    regime = (regime or "").lower()
    if regime not in ALIQUOTAS:
        return {"aplicavel": False, "competencia": competencia, "regime": regime,
                "motivo": "Apuração federal só se aplica a Lucro Presumido ou Lucro Real."}

    al_pis, al_cofins, rotulo, cumulativo = ALIQUOTAS[regime]
    doc = _emitidas_do_mes(notas, cnpj, competencia)

    receita = round(sum(n.get("valor_servico", 0.0) for n in doc) + (receita_extra or 0.0), 2)

    # Retenções sofridas, lidas nota a nota. valor_pis/valor_cofins/valor_csll
    # só vêm preenchidos quando o emitente discrimina; quando ele consolida em
    # vRetCSLL, o total consolidado fica em valor_csll — por isso o abatimento
    # de PIS e COFINS pode sair zerado mesmo havendo retenção. Isso é sinalizado
    # em `avisos` em vez de ser adivinhado.
    ret_pis = round(sum(n.get("valor_pis", 0.0) for n in doc), 2)
    ret_cofins = round(sum(n.get("valor_cofins", 0.0) for n in doc), 2)
    ret_csll = round(sum(n.get("valor_csll", 0.0) for n in doc), 2)
    ret_irrf = round(sum(n.get("valor_irrf", 0.0) for n in doc), 2)
    ret_total_social = round(sum(n.get("valor_retido", 0.0) for n in doc), 2)

    pis_dev = round(receita * al_pis, 2)
    cofins_dev = round(receita * al_cofins, 2)

    pis_apagar = round(max(0.0, pis_dev - credito_pis - ret_pis), 2)
    cofins_apagar = round(max(0.0, cofins_dev - credito_cofins - ret_cofins), 2)
    # Retenção maior que o devido não vira zero e some: é saldo a compensar nos
    # meses seguintes, e o contador precisa enxergar isso.
    pis_saldo = round(max(0.0, (credito_pis + ret_pis) - pis_dev), 2)
    cofins_saldo = round(max(0.0, (credito_cofins + ret_cofins) - cofins_dev), 2)

    avisos = []
    if not cumulativo:
        avisos.append(
            "Lucro Real: o valor acima é o <b>débito bruto</b>. Os créditos de "
            "PIS/COFINS (insumos, energia, aluguel, depreciação) NÃO estão "
            "descontados — só entram os que você informar. Confira no Auditor.")
    if ret_csll > 0 and ret_pis == 0 and ret_cofins == 0:
        avisos.append(
            f"Há R$ {ret_total_social:,.2f} de contribuições retidas sem PIS e "
            "COFINS discriminados: o emitente consolidou tudo em vRetCSLL. "
            "O abatimento por tributo precisa ser conferido no comprovante.")
    if receita == 0:
        avisos.append("Nenhuma nota emitida nesta competência.")

    return {
        "aplicavel": True,
        "competencia": competencia,
        "regime": regime,
        "base": rotulo,
        "notas": len(doc),
        "receita_bruta": receita,
        "aliquota_pis": al_pis,
        "aliquota_cofins": al_cofins,
        "pis_devido": pis_dev,
        "cofins_devido": cofins_dev,
        "credito_pis": round(credito_pis or 0.0, 2),
        "credito_cofins": round(credito_cofins or 0.0, 2),
        "retido_pis": ret_pis,
        "retido_cofins": ret_cofins,
        "retido_csll": ret_csll,
        "retido_irrf": ret_irrf,
        "retido_total_social": ret_total_social,
        "pis_a_pagar": pis_apagar,
        "cofins_a_pagar": cofins_apagar,
        "pis_saldo_credor": pis_saldo,
        "cofins_saldo_credor": cofins_saldo,
        "total_a_pagar": round(pis_apagar + cofins_apagar, 2),
        "avisos": avisos,
    }


# ── IRPJ e CSLL do Lucro Presumido — TRIMESTRAIS ────────────────────────────
# Erro comum: tratar como mensais. No Presumido a apuração é por trimestre
# civil (Lei 9.430/1996, art. 1º), e por isso o MIT só traz IRPJ/CSLL no mês de
# fechamento do trimestre.
IRPJ_ALIQ = 0.15
IRPJ_ADICIONAL = 0.10
IRPJ_LIMITE_MENSAL = 20000.00      # o adicional incide sobre o que passar disso
CSLL_ALIQ = 0.09

# Presunção por atividade (Lei 9.249/1995, arts. 15 e 20). Sem a atividade
# declarada não dá para escolher sozinho — o padrão é serviços, e a tela avisa.
PRESUNCAO_PADRAO_IRPJ = 32.0
PRESUNCAO_PADRAO_CSLL = 32.0

PRESUNCOES = [
    {"irpj": 1.6,  "csll": 12.0, "rotulo": "Revenda de combustíveis"},
    {"irpj": 8.0,  "csll": 12.0, "rotulo": "Comércio, indústria, transporte de cargas"},
    {"irpj": 16.0, "csll": 12.0, "rotulo": "Transporte (exceto cargas)"},
    {"irpj": 32.0, "csll": 32.0, "rotulo": "Serviços em geral, locação, intermediação"},
]


def meses_do_trimestre(ano: int, tri: int) -> list[str]:
    ini = (tri - 1) * 3 + 1
    return [f"{ano:04d}-{m:02d}" for m in range(ini, ini + 3)]


def apurar_trimestre(notas, cnpj: str, ano: int, tri: int,
                     presuncao_irpj: float | None = None,
                     presuncao_csll: float | None = None,
                     receita_extra: float = 0.0) -> dict:
    """IRPJ e CSLL de um trimestre do Lucro Presumido."""
    if tri not in (1, 2, 3, 4):
        return {"aplicavel": False, "motivo": "Trimestre inválido."}

    padrao = presuncao_irpj is None and presuncao_csll is None
    p_irpj = float(presuncao_irpj if presuncao_irpj is not None else PRESUNCAO_PADRAO_IRPJ)
    p_csll = float(presuncao_csll if presuncao_csll is not None else PRESUNCAO_PADRAO_CSLL)

    meses = meses_do_trimestre(ano, tri)
    doc = [n for m in meses for n in _emitidas_do_mes(notas, cnpj, m)]

    receita = round(sum(n.get("valor_servico", 0.0) for n in doc) + (receita_extra or 0.0), 2)
    por_mes = {m: round(sum(n.get("valor_servico", 0.0)
                            for n in _emitidas_do_mes(notas, cnpj, m)), 2) for m in meses}

    base_irpj = round(receita * p_irpj / 100.0, 2)
    base_csll = round(receita * p_csll / 100.0, 2)

    # O limite do adicional é R$ 20.000 POR MÊS do período — R$ 60.000 no
    # trimestre completo. Usar 20.000 aqui cobraria adicional a mais.
    limite = IRPJ_LIMITE_MENSAL * len(meses)
    excedente = round(max(0.0, base_irpj - limite), 2)

    irpj_normal = round(base_irpj * IRPJ_ALIQ, 2)
    irpj_adic = round(excedente * IRPJ_ADICIONAL, 2)
    irpj_dev = round(irpj_normal + irpj_adic, 2)
    csll_dev = round(base_csll * CSLL_ALIQ, 2)

    # Retenções do trimestre: IRRF abate IRPJ, CSLL retida abate CSLL.
    ret_irrf = round(sum(n.get("valor_irrf", 0.0) for n in doc), 2)
    ret_csll = round(sum(n.get("valor_csll", 0.0) for n in doc), 2)

    irpj_apagar = round(max(0.0, irpj_dev - ret_irrf), 2)
    csll_apagar = round(max(0.0, csll_dev - ret_csll), 2)
    irpj_saldo = round(max(0.0, ret_irrf - irpj_dev), 2)
    csll_saldo = round(max(0.0, ret_csll - csll_dev), 2)

    avisos = []
    if padrao:
        avisos.append(
            f"Usando a presunção padrão de <b>serviços ({p_irpj:.0f}% IRPJ / "
            f"{p_csll:.0f}% CSLL)</b>. Confirme a atividade da empresa: comércio, "
            "indústria e transporte de cargas usam 8% e 12%, e o valor muda muito.")
    if excedente > 0:
        avisos.append(
            f"Adicional de 10% sobre R$ {excedente:,.2f} — o que passou do limite "
            f"de R$ {limite:,.2f} no trimestre (R$ 20.000 por mês).")

    return {
        "aplicavel": True,
        "periodo": f"{ano}-T{tri}",
        "ano": ano, "trimestre": tri, "meses": meses, "receita_por_mes": por_mes,
        "notas": len(doc),
        "receita_bruta": receita,
        "presuncao_irpj": p_irpj, "presuncao_csll": p_csll,
        "presuncao_padrao": padrao,
        "base_irpj": base_irpj, "base_csll": base_csll,
        "irpj_normal": irpj_normal, "irpj_adicional": irpj_adic,
        "limite_adicional": limite, "excedente_adicional": excedente,
        "irpj_devido": irpj_dev, "csll_devido": csll_dev,
        "retido_irrf": ret_irrf, "retido_csll": ret_csll,
        "irpj_a_pagar": irpj_apagar, "csll_a_pagar": csll_apagar,
        "irpj_saldo_credor": irpj_saldo, "csll_saldo_credor": csll_saldo,
        "total_a_pagar": round(irpj_apagar + csll_apagar, 2),
        "avisos": avisos,
    }


def apurar_carteira_trimestre(registro: list, cfgs: dict, ano: int, tri: int,
                              pasta_de) -> dict:
    """IRPJ/CSLL do trimestre de todas as empresas do Lucro Presumido."""
    import core

    linhas, fora = [], 0
    for c in registro or []:
        if c.get("procurador"):
            continue
        cnpj = c.get("cnpj") or ""
        cid = c.get("id") or cnpj
        cfg = cfgs.get(cid) or cfgs.get(cnpj) or {}
        # Lucro Real fica de fora: IRPJ/CSLL vêm do LALUR, não da receita.
        if (cfg.get("regime") or "simples").lower() != "presumido":
            fora += 1
            continue
        nome = c.get("apelido") or c.get("nome") or cnpj
        try:
            notas = core.carregar_notas(pasta_de(cnpj))
        except Exception as e:
            linhas.append({"id": cid, "cnpj": cnpj, "nome": nome, "ok": False,
                           "erro": f"{e.__class__.__name__}: {e}"})
            continue
        r = apurar_trimestre(notas, cnpj, ano, tri,
                             cfg.get("presuncaoIRPJ"), cfg.get("presuncaoCSLL"))
        r.update({"id": cid, "cnpj": cnpj, "nome": nome, "ok": True})
        linhas.append(r)

    linhas.sort(key=lambda x: -(x.get("total_a_pagar") or 0))
    validas = [l for l in linhas if l.get("ok")]
    return {
        "periodo": f"{ano}-T{tri}", "ano": ano, "trimestre": tri,
        "meses": meses_do_trimestre(ano, tri),
        "empresas": linhas,
        "resumo": {
            "empresas": len(linhas), "ignoradas": fora,
            "irpj_total": round(sum(l.get("irpj_a_pagar") or 0 for l in validas), 2),
            "csll_total": round(sum(l.get("csll_a_pagar") or 0 for l in validas), 2),
            "total": round(sum(l.get("total_a_pagar") or 0 for l in validas), 2),
            "receita_total": round(sum(l.get("receita_bruta") or 0 for l in validas), 2),
        },
    }


def apurar_carteira(dados_dir, registro: list, cfgs: dict, competencia: str,
                    pasta_de) -> dict:
    """Apura o mês de todas as empresas fora do Simples. Devolve uma linha por
    empresa, pronta para a tela do MIT."""
    import core

    linhas, ignoradas = [], 0
    for c in registro or []:
        cnpj = c.get("cnpj") or ""
        cid = c.get("id") or cnpj
        if c.get("procurador"):
            continue                       # é o certificado do procurador, não uma empresa
        cfg = cfgs.get(cid) or cfgs.get(cnpj) or {}
        regime = (cfg.get("regime") or "simples").lower()
        if regime not in ALIQUOTAS:
            ignoradas += 1
            continue
        nome = c.get("apelido") or c.get("nome") or cnpj
        try:
            notas = core.carregar_notas(pasta_de(cnpj))
        except Exception as e:
            linhas.append({"id": cid, "cnpj": cnpj, "nome": nome, "regime": regime,
                           "ok": False, "erro": f"{e.__class__.__name__}: {e}"})
            continue
        r = apurar(notas, cnpj, competencia, regime)
        r.update({"id": cid, "cnpj": cnpj, "nome": nome, "ok": True})
        linhas.append(r)

    linhas.sort(key=lambda x: -(x.get("total_a_pagar") or 0))
    validas = [l for l in linhas if l.get("ok")]
    return {
        "competencia": competencia,
        "empresas": linhas,
        "resumo": {
            "empresas": len(linhas),
            "fora_do_simples": len(validas),
            "ignoradas_simples": ignoradas,
            "pis_total": round(sum(l.get("pis_a_pagar") or 0 for l in validas), 2),
            "cofins_total": round(sum(l.get("cofins_a_pagar") or 0 for l in validas), 2),
            "total": round(sum(l.get("total_a_pagar") or 0 for l in validas), 2),
            "receita_total": round(sum(l.get("receita_bruta") or 0 for l in validas), 2),
        },
    }
