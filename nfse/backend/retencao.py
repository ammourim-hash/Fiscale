# -*- coding: utf-8 -*-
"""retencao.py — a retenção de contribuições sociais (CSRF) de uma NFS-e, numa regra só.

POR QUE ESTE MÓDULO EXISTE
    A mesma conta morava copiada em três lugares — `core.parse_nfse` (a tela e
    a apuração), `classificador._retencoes_federais` e `pdflocal` (o DANFSe) —
    e cada cópia somava de um jeito. Retenção lida de dois jeitos é retenção
    que aparece com dois valores diferentes para a mesma nota.

O CAMPO "CONTRIBUIÇÕES SOCIAIS - RETIDAS" (vRetCSLL) TEM DOIS SIGNIFICADOS
    Medido no acervo real em 13/09/2026 (7.346 NFS-e, 257 com retenção social):

      • 219 notas trazem só um dos lados — sem conflito;
      •  28 notas trazem vPis + vCofins destacados E vRetCSLL = **1%** da base:
         aí vRetCSLL é **só a CSLL**, e o total retido é a SOMA dos três;
      •  10 notas trazem vPis + vCofins destacados E vRetCSLL = **4,65%** da
         base: aí vRetCSLL **já é o total consolidado**, e somar PIS e COFINS
         por cima DUPLICA (8 delas saíam com 5,30% em vez de 4,65%).

    As duas leituras erradas já aconteceram neste projeto: até 04/08/2026 o
    código descartava PIS/COFINS (perdia R$ 989,95 em 19 notas); depois passou
    a somar sempre (duplicava nas 10 acima). Nenhuma regra fixa acerta os dois.

A REGRA — ESTRUTURAL, E NÃO PELA ALÍQUOTA
    Quando PIS E COFINS vêm destacados e vRetCSLL é MAIOR OU IGUAL à soma
    deles, ele já contém
    PIS e COFINS: vale **estritamente o vRetCSLL**. Pela lei, a CSLL sozinha
    (1%) é sempre menor que PIS + COFINS (3,65%); então um vRetCSLL que alcança
    a soma dos dois só pode ser o consolidado. Só quando ele é menor é que
    representa a CSLL isolada, e aí se soma.

    Isso não usa os 4,65% para decidir — uma nota com alíquota fora do padrão
    continua lida pelo que o documento diz, e é o ALERTA abaixo que aponta a
    divergência, em vez de a leitura esconder o número.

O ALERTA DE 4,65% (Lei 10.833/2003, art. 30 e 31)
    PIS 0,65% + COFINS 3,00% + CSLL 1,00% = 4,65% sobre o valor do serviço.
    Retenção social > 0 que não bate com isso gera alerta de conferência.
    A tolerância é de R$ 0,02: é arredondamento de centavo, não margem — o
    emitente arredonda PIS, COFINS e CSLL SEPARADAMENTE, e três arredondamentos
    somados chegam a dois centavos (medido: 429,73 → 19,97 retidos contra
    19,98 calculados). Sem ela, notas corretas acenderiam alerta.

    O alerta é de CONFERÊNCIA, não de erro: há casos legais fora dos 4,65%
    (órgão público federal retém 9,45% com IR — IN RFB 1.234/2012; entidades
    com isenção parcial). O sistema aponta; quem decide é o contador.

ESTE MÓDULO NÃO TOCA DISCO NEM REDE.
"""
from __future__ import annotations

CSRF_PIS, CSRF_COFINS, CSRF_CSLL = 0.0065, 0.0300, 0.0100
CSRF_TOTAL = CSRF_PIS + CSRF_COFINS + CSRF_CSLL        # 0,0465
TOLERANCIA_CENTAVOS = 0.02


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def compor(tp_ret, v_pis, v_cofins, v_ret_csll, valor_servico) -> dict:
    """A retenção social de uma nota, sem duplicar e sem descartar.

    Devolve `pis`, `cofins`, `csll`, `total` e três marcas:
      `consolidada_no_campo` — vRetCSLL já era o total (vale estritamente ele);
      `detalhada` — há composição por tributo para mostrar;
      `rateada`   — veio consolidada SEM PIS/COFINS e foi dividida pela lei.
    """
    tp = (str(tp_ret).strip() if tp_ret is not None else "")
    v_pis, v_cofins = _f(v_pis), _f(v_cofins)
    v_csll, serv = _f(v_ret_csll), _f(valor_servico)

    # ── 1 · vRetCSLL já é o consolidado: estritamente ele ────────────────
    #     Exige PIS E COFINS destacados. Com um só deles a comparação não
    #     separa os casos: CSLL isolada (1%) já é maior que PIS sozinho (0,65%).
    #     Esse formato não apareceu no acervo; ele segue somando, como antes.
    destacados = v_pis + v_cofins
    if v_csll > 0 and v_pis > 0 and v_cofins > 0 and v_csll + 0.005 >= destacados:
        total = round(v_csll, 2)
        # A composição usa o que o emitente destacou; a CSLL é o resto, para
        # o total ser EXATAMENTE o campo — nunca um centavo a mais.
        pis, cofins = round(v_pis, 2), round(v_cofins, 2)
        csll = round(max(0.0, total - pis - cofins), 2)
        return {"pis": pis, "cofins": cofins, "csll": csll, "total": total,
                "consolidada_no_campo": True, "detalhada": True, "rateada": False}

    # ── 2 · vRetCSLL é só a CSLL: soma, respeitando tpRetPisCofins ───────
    #     1 = PIS e COFINS retidos, 3 = só PIS, 4 = só COFINS.
    pis = v_pis if tp in ("1", "3") else 0.0
    cofins = v_cofins if tp in ("1", "4") else 0.0
    csll = v_csll

    # ── 3 · consolidada sem PIS/COFINS em 4,65%: a divisão é a da lei ────
    #     (IN RFB 459/2004, art. 2º). Destrava o abatimento por tributo.
    rateada = False
    if (csll > 0 and pis == 0 and cofins == 0 and serv > 0
            and abs(csll / serv - CSRF_TOTAL) < 0.0002):
        total = csll
        pis = round(serv * CSRF_PIS, 2)
        cofins = round(serv * CSRF_COFINS, 2)
        csll = round(total - pis - cofins, 2)
        rateada = True

    total = round(pis + cofins + csll, 2)
    return {"pis": round(pis, 2), "cofins": round(cofins, 2),
            "csll": round(csll, 2), "total": total,
            "consolidada_no_campo": False,
            "detalhada": bool(csll > 0 and (pis > 0 or cofins > 0)),
            "rateada": rateada}


def alerta_csrf(total_retido, valor_servico) -> dict:
    """Confere a retenção social contra os 4,65% da Lei 10.833/2003.

    Só avalia quando HÁ retenção social (> 0): nota sem retenção não é
    divergência — é outro assunto.
    """
    total, serv = round(_f(total_retido), 2), _f(valor_servico)
    if total <= 0 or serv <= 0:
        return {"alerta": False, "esperado": 0.0, "diferenca": 0.0,
                "aliquota_efetiva": 0.0, "mensagem": ""}
    esperado = round(serv * CSRF_TOTAL, 2)
    diferenca = round(total - esperado, 2)
    efetiva = round(total / serv * 100, 3)
    if abs(diferenca) <= TOLERANCIA_CENTAVOS:
        return {"alerta": False, "esperado": esperado, "diferenca": diferenca,
                "aliquota_efetiva": efetiva, "mensagem": ""}
    return {"alerta": True, "esperado": esperado, "diferenca": diferenca,
            "aliquota_efetiva": efetiva,
            "mensagem": ("Retenção de contribuições sociais de R$ %s (%s%%) não "
                         "corresponde a 4,65%% do serviço (esperado R$ %s; "
                         "diferença R$ %s). Conferir no comprovante."
                         % (_brl(total), _pct(efetiva), _brl(esperado),
                            _brl(diferenca)))}


def _brl(v: float) -> str:
    s = "%.2f" % v
    inteiro, dec = s.split(".")
    neg = inteiro.startswith("-")
    inteiro = inteiro.lstrip("-")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return ("-" if neg else "") + ".".join(grupos) + "," + dec


def _pct(v: float) -> str:
    return ("%.2f" % v).replace(".", ",")
