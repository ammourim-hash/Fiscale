# -*- coding: utf-8 -*-
"""apuracao_memoria.py — a MEMÓRIA DE CÁLCULO da apuração.

O QUE ESTA CAMADA É
    A resposta a uma pergunta só: **de onde veio este número?**

    Ela percorre o mesmo caminho que a apuração já percorre, pelos mesmos
    módulos, e devolve o rastro: quais documentos formaram a receita, quais
    ficaram de fora e por quê, e o que cada motor de tributo conseguiu (ou não)
    calcular com isso.

O QUE ELA DELIBERADAMENTE NÃO FAZ
    **Não calcula tributo.** Nenhuma alíquota, nenhuma base, nenhuma soma de
    imposto mora aqui. Quem calcula continua sendo `classificador.calcular_das`
    (Simples, com RBT12, Fator R e ISS fixo), `apuracao_federal.apurar`
    (PIS/COFINS) e `apuracao_federal.apurar_trimestre` (IRPJ/CSLL do
    Presumido). Um segundo lugar calculando produziria dois números para a
    mesma guia — e um deles estaria errado sem ninguém saber qual.

    **Não lê disco por conta própria** para NFS-e: recebe o resultado de
    `classificador.classificar()`, que já anotou nota por nota o `anexo` e o
    `base_anexo` (o MOTIVO). O rastro não é reconstruído: é o que o motor já
    sabia e ninguém estava mostrando.

    **Não inventa valor.** Quando um tributo não pode ser apurado, o estado
    diz o que falta — nunca sai zero no lugar do desconhecido.

A NF-e VEM PELO CAMINHO CANÔNICO, E ISSO É UMA CORREÇÃO
    A apuração lia NF-e de `<pasta_cnpj>/nfe/*-procNFe.xml` — pasta LEGADA,
    fora do acervo, com regra própria de "venda ou compra" por comparação de
    CNPJ. O caminho canônico do FISCALE é outro: acervo → índice →
    `conferencia.operacoes_nfe` → `ingestao.vendas`, que é onde vivem as regras
    já corrigidas (transferência não é receita; `tpNF` é do emitente, não da
    empresa; NFC-e pelo modelo da chave).

    Aqui a NF-e vem do caminho canônico. O bloco é SEPARADO e declarado: ele
    não é somado à base do Simples às escondidas — se um dia entrar, entra por
    decisão escrita, não por efeito colateral de uma tela nova.

TRÊS ESTADOS PARA CADA TRIBUTO, E O TERCEIRO É O HONESTO
    CALCULADO      o motor apurou, e os documentos estão listados
    NAO_APLICAVEL  o regime da empresa não comporta este tributo
    DEPENDE_DE     falta informação; `falta` diz o que, em português
"""
from __future__ import annotations

from decimal import Decimal

# ── estados de um tributo na memória ────────────────────────────────────────
CALCULADO = "CALCULADO"
NAO_APLICAVEL = "NAO_APLICAVEL"
DEPENDE_DE = "DEPENDE_DE"

# ── as fontes de documento que a apuração conhece hoje ──────────────────────
FONTE_NFSE = "NFS-e"
FONTE_NFE = "NF-e"
FONTES = (FONTE_NFSE, FONTE_NFE)

TRIBUTOS = ("SIMPLES", "PIS", "COFINS", "IRPJ", "CSLL")

# Por onde cada fonte é lida — texto para a tela, para ninguém ter de adivinhar
# se o número veio do acervo ou de uma pasta solta.
# O bloco de NF-e é de CONFERÊNCIA: existe uma frase só para dizer isso, e os
# dois caminhos (com e sem acervo) usam a mesma. Duas redações da mesma ressalva
# divergem, e é justamente a ressalva que não pode faltar.
OBSERVACAO_NFE = ("bloco de conferência: a receita de NF-e ainda NÃO é "
                  "somada à base do Simples — a base de hoje é a NFS-e")

COMO_LIDO = {
    FONTE_NFSE: "cache do Portal Nacional + histórico municipal, por "
                "`classificador.ler_nfse`",
    FONTE_NFE: "acervo indexado → `conferencia.operacoes_nfe` → "
               "`ingestao.vendas` (caminho canônico)",
}


def _f(v) -> float:
    """Decimal ou string viram float só na borda da resposta JSON."""
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _2(v) -> float:
    return round(_f(v), 2)


# ════════════════════════════════════════════════════════════════════════════
#  NFS-e — o rastro que o classificador já produzia
# ════════════════════════════════════════════════════════════════════════════
def linhas_nfse(resultado_classificador: dict, competencia: str = "") -> dict:
    """Nota a nota, com o motivo que o motor já havia escrito em `base_anexo`.

    `competencia` vazia = o período inteiro que a classificação recebeu.
    """
    notas = (resultado_classificador or {}).get("notas") or []
    base = (resultado_classificador or {}).get("base_data") or "competencia"

    dentro, fora = [], []
    for n in notas:
        comp = (n.get("competencia") or "")[:7]
        if competencia and comp != competencia:
            continue
        linha = {
            "fonte": FONTE_NFSE,
            "documento": n.get("numero") or n.get("chave") or "—",
            "data": n.get("data") or "",
            "competencia": comp,
            "contraparte": n.get("tomador") or "",
            "item_lc116": n.get("item_lc116") or "",
            "valor": _2(n.get("valor")),
            "anexo": n.get("anexo") or "",
            # O MOTIVO NÃO É ESCRITO AQUI. É o que `resolver_anexo` decidiu.
            "motivo": n.get("base_anexo") or "",
            "arquivo": n.get("arquivo") or "",
        }
        if n.get("cancelada"):
            linha["entra"] = False
            # Cancelada já chega com `base_anexo = "Cancelada — fora da
            # receita"`; se por algum caminho não chegar, o motivo é dito.
            linha["motivo"] = linha["motivo"] or "cancelada — fora da receita"
            fora.append(linha)
        else:
            linha["entra"] = True
            dentro.append(linha)

    return {
        "fonte": FONTE_NFSE,
        "como_lido": COMO_LIDO[FONTE_NFSE],
        "base_data": base,
        "documentos": len(dentro) + len(fora),
        "na_receita": len(dentro),
        "fora_da_receita": len(fora),
        "valor_na_receita": _2(sum(x["valor"] for x in dentro)),
        "valor_fora": _2(sum(x["valor"] for x in fora)),
        "linhas": dentro,
        "excluidos": fora,
    }


# ════════════════════════════════════════════════════════════════════════════
#  NF-e — pelo acervo, com a classificação canônica de `vendas.py`
# ════════════════════════════════════════════════════════════════════════════
def linhas_nfe(dados_dir, identidade: str, competencia: str = "") -> dict:
    """O que o acervo de NF-e diz, pela regra de `vendas.py`.

    Nada aqui reclassifica: `vendas.classificar` decide a natureza e o motivo,
    e este bloco só os apresenta. Falha de leitura do acervo não derruba a
    memória — vira `indisponivel` com o nome da exceção, porque a apuração de
    NFS-e continua válida sem isto.
    """
    vazio = {
        "fonte": FONTE_NFE, "como_lido": COMO_LIDO[FONTE_NFE],
        "documentos": 0, "na_receita": 0, "fora_da_receita": 0,
        "valor_na_receita": 0.0, "valor_fora": 0.0,
        "linhas": [], "excluidos": [], "por_natureza": {},
        "indisponivel": "", "entra_na_base": False,
        "observacao": OBSERVACAO_NFE,
    }
    try:
        from ingestao import conferencia as cf
        from ingestao import vendas as vd
    except Exception as e:                              # pragma: no cover
        vazio["indisponivel"] = "pacote de ingestão indisponível (%s)" % type(e).__name__
        return vazio

    try:
        operacoes = [op for op, _falha in cf.operacoes_nfe(dados_dir, identidade)
                     if op is not None]
        receita = vd.receita_de_vendas(operacoes, identidade)
    except Exception as e:
        vazio["indisponivel"] = "não consegui ler o acervo de NF-e (%s)" % type(e).__name__
        return vazio

    dentro, fora = [], []
    for o in receita.operacoes:
        comp = getattr(o, "competencia", "") or ""
        if competencia and comp[:7] != competencia:
            continue
        chave = getattr(o, "chave", "") or ""
        linha = {
            "fonte": FONTE_NFE,
            # A chave inteira não vai para a tela: os 8 últimos dígitos bastam
            # para casar com o documento, como no painel de importação.
            "documento": chave[-8:] if chave else (o.id_documento or "—"),
            "id_documento": o.id_documento,
            "competencia": comp,
            "natureza": o.natureza,
            "cfops": list(getattr(o, "cfops", ()) or ()),
            "valor": _2(getattr(o, "valor", None)),
            "motivo": getattr(o, "motivo", "") or "",
        }
        if getattr(o, "e_receita", False) and not getattr(o, "cancelada", False):
            linha["entra"] = True
            dentro.append(linha)
        else:
            linha["entra"] = False
            linha["motivo"] = linha["motivo"] or vd.MOTIVO_FORA_DA_RECEITA.get(
                o.natureza, o.natureza)
            fora.append(linha)

    z = receita.resumo()
    saida = dict(vazio)
    saida.update({
        "documentos": len(dentro) + len(fora),
        "na_receita": len(dentro),
        "fora_da_receita": len(fora),
        "valor_na_receita": _2(sum(x["valor"] for x in dentro)),
        "valor_fora": _2(sum(x["valor"] for x in fora)),
        "linhas": dentro,
        "excluidos": fora,
        "por_natureza": z.get("por_natureza") or {},
    })
    return saida


# ════════════════════════════════════════════════════════════════════════════
#  Tributos — o que cada motor fez, e com quais documentos
# ════════════════════════════════════════════════════════════════════════════
def _simples(resultado_classificador: dict, competencia: str,
             nfse: dict) -> dict:
    """O Simples, como `calcular_das` apurou. Nenhuma conta refeita aqui."""
    regime = (resultado_classificador or {}).get("regime") or ""
    if regime and regime not in ("simples", ""):
        return {"estado": NAO_APLICAVEL, "de_onde": "classificador.calcular_das",
                "motivo": "empresa em %s — o DAS não se aplica" % regime}

    das = (resultado_classificador or {}).get("das") or []
    do_mes = [d for d in das if not competencia
              or (d.get("competencia") or "")[:7] == competencia]
    if not do_mes:
        return {"estado": DEPENDE_DE, "de_onde": "classificador.calcular_das",
                "falta": "nenhuma competência apurada no período pedido"}

    resumo = (resultado_classificador or {}).get("resumo") or {}
    pendentes = [l for d in do_mes for l in (d.get("linhas") or [])
                 if l.get("das") is None]
    falta = ""
    if pendentes:
        # Não existe DAS parcial silencioso: se uma linha ficou sem valor, o
        # estado diz que falta, e o que falta vem do próprio motor.
        falta = "; ".join(sorted({(l.get("obs") or l.get("anexo") or "?")
                                  for l in pendentes}))[:300]
    total = sum(_f(d.get("das_estimado")) for d in do_mes)

    return {
        "estado": DEPENDE_DE if pendentes else CALCULADO,
        "de_onde": "classificador.calcular_das",
        "falta": falta if pendentes else "",
        "valor": _2(total),
        "rbt12": _2(resumo.get("rbt12")),
        "fator_r": resumo.get("fator_r"),
        "competencias": [
            {"competencia": d.get("competencia"),
             "receita_mes": _2(d.get("receita_mes")),
             "das_estimado": _2(d.get("das_estimado")),
             "linhas": [
                 {"anexo": l.get("anexo"), "receita": _2(l.get("receita")),
                  "faixa": l.get("faixa"), "efetiva": l.get("efetiva"),
                  "das": None if l.get("das") is None else _2(l.get("das")),
                  "motivo": l.get("obs") or l.get("motivo") or "",
                  "tributos": l.get("tributos") or {}}
                 for l in (d.get("linhas") or [])],
             } for d in do_mes],
        # O rastro: os documentos que formaram a receita desta base.
        "documentos": [x["documento"] for x in nfse["linhas"]],
        "base_vem_de": FONTE_NFSE,
    }


def _federal(notas_nfse, cnpj: str, competencia: str, regime: str,
             resultado_federal: dict | None) -> dict:
    """PIS/COFINS, com a MESMA lista de notas que `apurar()` usou."""
    import apuracao_federal as af

    if not regime or regime == "simples":
        return {"PIS": {"estado": NAO_APLICAVEL,
                        "motivo": "empresa no Simples — PIS e COFINS estão "
                                  "dentro do DAS",
                        "de_onde": "apuracao_federal.apurar"},
                "COFINS": {"estado": NAO_APLICAVEL,
                           "motivo": "empresa no Simples — PIS e COFINS estão "
                                     "dentro do DAS",
                           "de_onde": "apuracao_federal.apurar"}}

    r = resultado_federal or {}
    if not r.get("aplicavel"):
        m = r.get("motivo") or "o motor federal não se aplica a este regime"
        return {t: {"estado": NAO_APLICAVEL, "motivo": m,
                    "de_onde": "apuracao_federal.apurar"} for t in ("PIS", "COFINS")}

    # As MESMAS notas que entraram na base — pela função do próprio motor.
    doc = af.emitidas_do_mes(notas_nfse, cnpj, competencia)
    rastro = [{"fonte": FONTE_NFSE, "documento": n.get("numero") or "—",
               "data": n.get("data") or "", "valor": _2(n.get("valor_servico")),
               "retido_pis": _2(n.get("valor_pis")),
               "retido_cofins": _2(n.get("valor_cofins"))}
              for n in doc]

    def um(nome, devido, retido, a_pagar, aliquota):
        return {"estado": CALCULADO, "de_onde": "apuracao_federal.apurar",
                "base": _2(r.get("receita_bruta")), "aliquota": aliquota,
                "devido": _2(devido), "retido": _2(retido),
                "a_pagar": _2(a_pagar), "regime": r.get("base") or regime,
                "avisos": r.get("avisos") or [], "documentos": rastro,
                "base_vem_de": FONTE_NFSE}

    return {
        "PIS": um("PIS", r.get("pis_devido"), r.get("retido_pis"),
                  r.get("pis_a_pagar"), r.get("aliquota_pis")),
        "COFINS": um("COFINS", r.get("cofins_devido"), r.get("retido_cofins"),
                     r.get("cofins_a_pagar"), r.get("aliquota_cofins")),
    }


def _trimestrais(regime: str, resultado_trimestre: dict | None) -> dict:
    """IRPJ e CSLL. No Presumido são TRIMESTRAIS — e isso é dito, não escondido."""
    if not regime or regime == "simples":
        m = "empresa no Simples — IRPJ e CSLL estão dentro do DAS"
        return {t: {"estado": NAO_APLICAVEL, "motivo": m,
                    "de_onde": "apuracao_federal.apurar_trimestre"}
                for t in ("IRPJ", "CSLL")}
    if regime == "real":
        m = ("Lucro Real: IRPJ e CSLL partem do lucro contábil ajustado no "
             "LALUR, que não existe em XML nenhum")
        return {t: {"estado": DEPENDE_DE, "falta": m,
                    "de_onde": "apuracao_federal.apurar_trimestre"}
                for t in ("IRPJ", "CSLL")}

    r = resultado_trimestre or {}
    if not r:
        m = ("apuração trimestral não pedida para este período — IRPJ/CSLL do "
             "Presumido fecham por trimestre civil (Lei 9.430/1996, art. 1º)")
        return {t: {"estado": DEPENDE_DE, "falta": m,
                    "de_onde": "apuracao_federal.apurar_trimestre"}
                for t in ("IRPJ", "CSLL")}

    return {
        "IRPJ": {"estado": CALCULADO,
                 "de_onde": "apuracao_federal.apurar_trimestre",
                 "trimestre": r.get("trimestre"), "meses": r.get("meses") or [],
                 "base": _2(r.get("base_irpj")), "devido": _2(r.get("irpj_devido")),
                 "adicional": _2(r.get("irpj_adicional")),
                 "a_pagar": _2(r.get("irpj_a_pagar")),
                 "base_vem_de": FONTE_NFSE},
        "CSLL": {"estado": CALCULADO,
                 "de_onde": "apuracao_federal.apurar_trimestre",
                 "trimestre": r.get("trimestre"), "meses": r.get("meses") or [],
                 "base": _2(r.get("base_csll")), "devido": _2(r.get("csll_devido")),
                 "a_pagar": _2(r.get("csll_a_pagar")),
                 "base_vem_de": FONTE_NFSE},
    }


# ════════════════════════════════════════════════════════════════════════════
#  A memória completa
# ════════════════════════════════════════════════════════════════════════════
def memoria(resultado_classificador: dict, *, dados_dir=None, identidade: str = "",
            competencia: str = "", resultado_federal: dict | None = None,
            resultado_trimestre: dict | None = None) -> dict:
    """O fluxo inteiro: empresa → período → documentos → receita → tributos.

    Recebe os resultados dos motores JÁ CALCULADOS. Isto é de propósito: se
    esta função os chamasse, ela decidiria parâmetros de apuração — e passaria a
    ser um segundo lugar onde se decide como apurar.
    """
    r = resultado_classificador or {}
    regime = (r.get("regime") or "").lower()

    nfse = linhas_nfse(r, competencia)
    if dados_dir and identidade:
        nfe = linhas_nfe(dados_dir, identidade, competencia)
    else:
        # Sem acervo informado, o bloco de NF-e não é ZERO: é "não consultado".
        # Zero diria "a empresa não emitiu", que é outra afirmação.
        nfe = {"fonte": FONTE_NFE, "como_lido": COMO_LIDO[FONTE_NFE],
               "documentos": 0, "na_receita": 0, "fora_da_receita": 0,
               "valor_na_receita": 0.0, "valor_fora": 0.0,
               "linhas": [], "excluidos": [], "por_natureza": {},
               "entra_na_base": False,
               "indisponivel": "acervo não informado nesta chamada",
               "observacao": OBSERVACAO_NFE}

    tributos = {}
    tributos["SIMPLES"] = _simples(r, competencia, nfse)
    tributos.update(_federal(r.get("notas") or [], identidade, competencia,
                             regime, resultado_federal))
    tributos.update(_trimestrais(regime, resultado_trimestre))

    excluidos = nfse["excluidos"] + nfe["excluidos"]
    return {
        "periodo": {"competencia": competencia or "(todo o período filtrado)",
                    "base_data": nfse["base_data"]},
        "regime": regime or "(não informado — tratado como Simples)",
        "fontes": [
            {"fonte": nfse["fonte"], "como_lido": nfse["como_lido"],
             "documentos": nfse["documentos"], "entra_na_base": True},
            {"fonte": nfe["fonte"], "como_lido": nfe["como_lido"],
             "documentos": nfe["documentos"], "entra_na_base": False,
             "indisponivel": nfe.get("indisponivel") or "",
             "observacao": nfe.get("observacao") or ""},
        ],
        "receita": {
            "identificada": nfse["valor_na_receita"],
            "por_fonte": {FONTE_NFSE: nfse["valor_na_receita"],
                          FONTE_NFE: nfe["valor_na_receita"]},
            "documentos": nfse["na_receita"] + nfe["na_receita"],
        },
        "nfse": nfse,
        "nfe": nfe,
        "excluidos": excluidos,
        "excluidos_valor": _2(sum(x["valor"] for x in excluidos)),
        "tributos": tributos,
        "estados": {t: tributos.get(t, {}).get("estado", "") for t in TRIBUTOS},
        "nada_inventado": (
            "cada valor acima veio de um motor nomeado em `de_onde`; o que não "
            "pôde ser apurado aparece como DEPENDE_DE, nunca como zero"),
    }
