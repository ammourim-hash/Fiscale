"""
auditor_nfe.py — Auditor dos XMLs de NF-e (entradas e saídas) da empresa.

Cruza as notas já importadas/distribuídas (pastas 'nfe' e 'nfe_importadas') e,
item a item (<det>), avalia:

  • NCM — formato válido (8 dígitos), não-zerado e CONSISTÊNCIA (o mesmo produto
    aparecendo com NCMs diferentes entre notas é o erro mais comum);
  • Créditos de ICMS, PIS e COFINS destacados nas COMPRAS, classificados pela CST;
  • A quem o crédito APROVEITA, conforme o regime da empresa:
        - ICMS  → só regime NORMAL (Lucro Presumido/Real); Simples não credita;
        - PIS/COFINS → só LUCRO REAL (não-cumulativo); Presumido (cumulativo) e
          Simples não creditam;
        - Monofásico/ST/alíquota zero (CST 04–09) NÃO gera crédito na entrada.

IMPORTANTE (limites honestos, para não induzir a erro):
  - É um INDICATIVO para conferência, não apuração oficial. Casos como ST,
    monofásico, substituição, devolução, uso/consumo e ativo imobilizado têm
    regras próprias que o auditor sinaliza mas não decide sozinho.
  - O crédito de PIS/COFINS no Lucro Real é apurado sobre o valor da aquisição às
    alíquotas não-cumulativas (1,65% e 7,6%) — NÃO é o valor destacado na nota do
    fornecedor (que está à alíquota dele). Por isso mostramos os dois números.
"""
from __future__ import annotations

import re
import ssl
import urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

import nfe as _nfe   # reaproveita namespace, _num, _dig, _classificar_papel

NFE = _nfe.NFE

# ── Alíquotas não-cumulativas (Lucro Real) ──────────────────────────────────
PIS_NAO_CUMUL = 0.0165
COFINS_NAO_CUMUL = 0.076

# ── CSTs que geram crédito de ICMS ao destinatário (quando há destaque) ─────
# 00 tributada integral · 10 c/ ST (parte própria) · 20 redução BC · 51 diferimento
# 70 red+ST · 90 outras.  40/41/50 (isenta/não trib/suspensão) e 60 (ICMS ST já
# retido) NÃO dão crédito próprio ao destinatário.
_ICMS_CST_CREDITO = {"00", "10", "20", "51", "70", "90"}
# Fornecedor do Simples que TRANSMITE crédito de ICMS (campo vCredICMSSN):
_ICMS_CSOSN_CREDITO = {"101", "201"}

# ── PIS/COFINS: situação pela CST da nota de saída do fornecedor ─────────────
_PC_TRIBUTADO = {"01", "02"}                          # gera crédito no Lucro Real
_PC_SEM_CREDITO = {"04", "05", "06", "07", "08", "09"}  # monofásico/ST/zero/isenta

# ── CFOP: naturezas que, em regra, NÃO geram crédito ordinário na aquisição ──
# (usa os 3 últimos dígitos — a 1ª casa é só a região). O auditor NÃO credita
# esses itens e os lista em separado para o contador revisar caso a caso.
_CFOP_REVISAR = {
    "405": "ST — mercadoria já com ICMS retido",
    "551": "compra de ativo imobilizado (crédito via CIAP, não direto)",
    "552": "ativo/uso e consumo", "553": "ativo/uso e consumo",
    "554": "ativo/uso e consumo", "555": "ativo/uso e consumo",
    "556": "material de uso e consumo", "557": "ativo/uso e consumo",
    "910": "bonificação/doação/brinde", "911": "amostra grátis",
    "912": "remessa p/ demonstração", "913": "retorno de demonstração",
    "914": "remessa p/ exposição/feira", "915": "remessa p/ conserto",
    "916": "retorno de conserto", "917": "remessa em consignação",
    "918": "devolução de consignação", "919": "devolução de consignação",
    "920": "remessa p/ venda fora do estab.", "921": "retorno venda fora do estab.",
    "922": "faturamento de venda futura", "923": "remessa venda futura",
    "924": "remessa p/ industrialização", "925": "retorno de industrialização",
    "901": "remessa p/ industrialização", "902": "retorno de industrialização",
    "903": "retorno mercadoria não industrializada", "904": "remessa p/ venda fora do estab.",
    "908": "remessa p/ conserto/reparo", "909": "retorno p/ conserto/reparo",
    "201": "devolução de venda", "202": "devolução de venda",
    "208": "devolução de bonificação", "209": "devolução de mercadoria",
    "410": "devolução (ST)", "411": "devolução (ST)", "412": "devolução",
    "413": "devolução", "414": "devolução", "415": "devolução",
}


def _cfop_revisar(cfop):
    """Devolve o motivo se o CFOP normalmente não gera crédito ordinário, senão ''."""
    d = re.sub(r"\D", "", cfop or "")
    if len(d) == 4:
        return _CFOP_REVISAR.get(d[1:], "")
    return ""


# ── Legalidade: coerência entre CFOP e CST (o que dá para checar com certeza) ─
# Só entram regras VERIFICÁVEIS no próprio XML. Casos com regra estadual/regime
# específico ficam de fora — o auditor aponta o que é objetivamente incoerente,
# não "adivinha" tributação.
_CFOP_ST = {"403", "404", "405"}          # operações com ICMS-ST
_CFOP_DEVOL = {"201", "202", "208", "209", "410", "411", "412", "413", "414", "415"}
# CFOPs onde CST 60/61 (ICMS cobrado antes / monofásico de combustível) é NORMAL:
# combustíveis e lubrificantes (65x/66x) e registro de cupom/ECF (929). Sem isto o
# auditor acusaria falso positivo em massa — confirmado nos XMLs reais da TRX
# (5929+CST61 = 149 itens, 5656+CST60 = 23 itens, todos legítimos).
_CFOP_ST_OK = _CFOP_ST | {
    "651", "652", "653", "654", "655", "656", "657", "658", "659",
    "661", "662", "663", "664", "665", "666", "667", "929",
}
# CSTs de ICMS já cobrado anteriormente (sem crédito próprio ao destinatário)
_ICMS_COBRADO_ANTES = {"60", "61"}


def _checar_legalidade_item(it) -> list[dict]:
    """Divergências CFOP×CST×valores de UM item. Devolve lista de {tipo, detalhe}."""
    p = []
    cfop = re.sub(r"\D", "", it.get("cfop") or "")
    suf = cfop[1:] if len(cfop) == 4 else ""
    cst = (it.get("cst_icms") or "").strip()
    vicms = it.get("vicms") or 0.0
    cst_pis = (it.get("cst_pis") or "").strip()
    vpis = it.get("vpis") or 0.0

    if not cfop:
        p.append({"tipo": "cfop_ausente", "detalhe": "Item sem CFOP"})
    elif len(cfop) != 4:
        p.append({"tipo": "cfop_formato", "detalhe": f"CFOP '{it.get('cfop')}' inválido (deve ter 4 dígitos)"})

    # CFOP de ST × CST de ST (60/61/10/70 ou CSOSN 500/201/202/203)
    if suf in _CFOP_ST and cst and cst not in ("60", "61", "10", "70", "500", "201", "202", "203", "900"):
        p.append({"tipo": "cfop_st_sem_cst_st",
                  "detalhe": f"CFOP {cfop} é de substituição tributária, mas o CST/CSOSN é {cst}"})
    if cst in _ICMS_COBRADO_ANTES and suf and suf not in _CFOP_ST_OK and suf not in _CFOP_DEVOL:
        p.append({"tipo": "cst_st_sem_cfop_st",
                  "detalhe": f"CST {cst} (ICMS já cobrado antes/monofásico) com CFOP {cfop}, "
                             f"que não é de ST nem de combustível"})
    # tributada integral sem destaque / isenta com destaque
    if cst == "00" and vicms <= 0:
        p.append({"tipo": "cst00_sem_icms",
                  "detalhe": f"CST 00 (tributada integralmente) sem valor de ICMS destacado (CFOP {cfop})"})
    if cst in ("40", "41", "50") and vicms > 0:
        p.append({"tipo": "isenta_com_icms",
                  "detalhe": f"CST {cst} (isenta/não tributada/suspensão) com ICMS destacado de {vicms:.2f}"})
    # PIS/COFINS
    if cst_pis in _PC_TRIBUTADO and vpis <= 0:
        p.append({"tipo": "pis_trib_sem_valor",
                  "detalhe": f"CST PIS {cst_pis} (tributado) sem valor de PIS destacado"})
    if cst_pis in ("04", "06", "07", "08", "09") and vpis > 0:
        p.append({"tipo": "pis_sem_trib_com_valor",
                  "detalhe": f"CST PIS {cst_pis} (monofásico/zero/isenta) com PIS destacado de {vpis:.2f}"})
    return p


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _primeiro_filho(node):
    """<ICMS>, <PIS>, <COFINS> têm 1 filho que diz o grupo (ICMS00, PISAliq…)."""
    if node is None:
        return None
    filhos = list(node)
    return filhos[0] if filhos else None


def _texto(node, tag):
    return node.findtext(f"{NFE}{tag}") if node is not None else None


# ── Classificação de crédito por item ───────────────────────────────────────
def _credito_icms(cst, vicms, vcred_sn):
    """Valor de ICMS creditável ao destinatário (regime normal)."""
    if cst in _ICMS_CSOSN_CREDITO and vcred_sn > 0:
        return vcred_sn, "Fornecedor do Simples transmitiu crédito (CSOSN %s)" % cst
    if cst in _ICMS_CST_CREDITO and vicms > 0:
        return vicms, "ICMS destacado (CST %s)" % cst
    if cst == "60":
        return 0.0, "ICMS-ST já retido (CST 60) — sem crédito próprio"
    if cst == "61":
        return 0.0, "Monofásico de combustível (CST 61) — ICMS cobrado antes, sem crédito"
    if cst == "500":
        return 0.0, "Simples/ST cobrada anteriormente (CSOSN 500) — sem crédito"
    if cst in ("102", "103", "300", "400"):
        return 0.0, f"Fornecedor do Simples sem permissão de crédito (CSOSN {cst})"
    if cst in ("40", "41", "50"):
        return 0.0, "Isenta/não tributada/suspensão (CST %s) — sem destaque" % cst
    return 0.0, ""


def _situacao_piscofins(cst):
    if cst in _PC_TRIBUTADO:
        return "tributado"
    if cst in _PC_SEM_CREDITO:
        return "sem_credito"
    return "outros"


# ── Conferidor EFD × XML: o que a CST da nota do fornecedor autoriza ─────────
# O crédito de PIS/COFINS só tem respaldo quando o fornecedor pagou a
# contribuição na etapa anterior (Lei 10.637/2002 e 10.833/2003, art. 3º, §2º, II).
# CST 04 (monofásico revendido a alíquota zero), 06 (alíquota zero), 07/08/09 e
# 49/99 indicam que NÃO houve pagamento — crédito sobre isso é glosável.
_CST_PIS_TEXTO = {
    "01": ("com respaldo", "Tributada à alíquota básica — o fornecedor pagou"),
    "02": ("com respaldo", "Tributada à alíquota diferenciada — o fornecedor pagou"),
    "03": ("revisar", "Alíquota por unidade de medida — conferir o cálculo"),
    "04": ("sem respaldo", "MONOFÁSICO revendido a alíquota ZERO — o fornecedor não pagou"),
    "05": ("sem respaldo", "Substituição tributária"),
    "06": ("sem respaldo", "Alíquota ZERO"),
    "07": ("sem respaldo", "Operação isenta"),
    "08": ("sem respaldo", "Sem incidência da contribuição"),
    "09": ("sem respaldo", "Operação com suspensão"),
    "49": ("sem respaldo", "Outras operações de saída — não indica tributação"),
    "99": ("sem respaldo", "Outras operações — não indica tributação"),
}


def _categoria_produto(ncm, descricao):
    """Agrupa o item pelo NCM para o conferidor (combustível é o caso crítico)."""
    d = re.sub(r"\D", "", ncm or "")
    if d.startswith("2710"):
        return "Combustível / lubrificante"
    if d.startswith("3820"):
        return "ARLA 32"
    if d.startswith(("02", "04", "07", "16", "19", "20", "21", "22")):
        return "Alimentos / carga avariada"
    if d.startswith(("40", "73", "82", "83", "84", "85", "87", "90", "32", "39", "68", "70")):
        return "Peças / manutenção"
    return "Outros"


# ── Coleta dos itens de todas as notas completas ────────────────────────────
def _itens_da_nota(inf, cnpj, arquivo, origem):
    n = _nfe._nota_de_infnfe(inf, cnpj, arquivo, origem)
    n["papel"] = _nfe._classificar_papel(n, cnpj)
    itens = []
    for det in inf.findall(f"{NFE}det"):
        prod = det.find(f"{NFE}prod")
        imp = det.find(f"{NFE}imposto")
        if prod is None:
            continue
        icms_grp = _primeiro_filho(imp.find(f"{NFE}ICMS")) if imp is not None else None
        pis_grp = _primeiro_filho(imp.find(f"{NFE}PIS")) if imp is not None else None
        cof_grp = _primeiro_filho(imp.find(f"{NFE}COFINS")) if imp is not None else None

        cst_icms = _texto(icms_grp, "CST") or _texto(icms_grp, "CSOSN") or ""
        vicms = _nfe._num(_texto(icms_grp, "vICMS"))
        vcred_sn = _nfe._num(_texto(icms_grp, "vCredICMSSN"))
        cst_pis = _texto(pis_grp, "CST") or ""
        vpis = _nfe._num(_texto(pis_grp, "vPIS"))
        cst_cof = _texto(cof_grp, "CST") or ""
        vcof = _nfe._num(_texto(cof_grp, "vCOFINS"))

        # Combustível: a NF-e traz o grupo <comb> com o código ANP. É um sinal
        # muito mais confiável do que listar NCM, porque vale para qualquer
        # combustível e não envelhece com mudanças de tabela.
        comb = prod.find(f"{NFE}comb")
        # ICMS monofásico de combustível (CST 61 e grupos correlatos): o valor
        # já foi recolhido antes; guardamos para conferência, não creditamos.
        vicms_mono = (_nfe._num(_texto(icms_grp, "vICMSMono"))
                      or _nfe._num(_texto(icms_grp, "vICMSMonoRet")))

        itens.append({
            "nItem": det.get("nItem"),
            "cProd": _texto(prod, "cProd"),
            "xProd": _texto(prod, "xProd"),
            "ncm": (_texto(prod, "NCM") or "").strip(),
            "cfop": (_texto(prod, "CFOP") or "").strip(),
            "vProd": _nfe._num(_texto(prod, "vProd")),
            "qCom": _nfe._num(_texto(prod, "qCom")),
            "uCom": (_texto(prod, "uCom") or "").strip(),
            "cst_icms": cst_icms, "vicms": vicms, "vcred_sn": vcred_sn,
            "cst_pis": cst_pis, "vpis": vpis,
            "cst_cof": cst_cof, "vcof": vcof,
            "combustivel": comb is not None,
            "anp_cod": (_texto(comb, "cProdANP") or "").strip() if comb is not None else "",
            "anp_desc": (_texto(comb, "descANP") or "").strip() if comb is not None else "",
            "vicms_mono": vicms_mono,
        })
    n["itens"] = itens
    return n


def _coletar(pasta_cnpj, cnpj):
    """Todas as notas COMPLETAS (com itens) das duas pastas."""
    pasta = Path(pasta_cnpj) / "nfe"
    importadas = Path(pasta_cnpj) / "nfe_importadas"
    vistas: dict[str, dict] = {}
    canceladas = set()
    for base, origem, padrao in ((pasta, "distribuida", "*-procNFe.xml"),
                                 (importadas, "importada", "*.xml")):
        if not base.exists():
            continue
        canceladas |= _nfe._eventos_cancelamento(base)
        for f in base.glob(padrao):
            try:
                inf = ET.fromstring(f.read_text("utf-8", "ignore")).find(f".//{NFE}infNFe")
            except Exception:
                continue
            if inf is None:
                continue
            n = _itens_da_nota(inf, cnpj, f.name, origem)
            vistas.setdefault(n["chave"], n)   # distribuída tem prioridade (vem 1º)
    for ch, n in vistas.items():
        n["cancelada"] = ch in canceladas
    return [n for n in vistas.values() if not n["cancelada"]]


# ── NCM: validação de formato, zerado e consistência ────────────────────────
def _auditar_ncm(notas):
    problemas = []
    por_produto: dict[str, set] = {}       # chave do produto -> NCMs vistos
    for n in notas:
        for it in n["itens"]:
            ncm = re.sub(r"\D", "", it["ncm"])
            ref = f"nota {n.get('numero') or n['chave'][-6:]} · item {it['nItem']} · {it['xProd'] or ''}".strip()
            if not ncm:
                problemas.append({"tipo": "ncm_ausente", "ref": ref, "detalhe": "NCM em branco"})
            elif len(ncm) != 8:
                problemas.append({"tipo": "ncm_formato", "ref": ref,
                                  "detalhe": f"NCM '{it['ncm']}' não tem 8 dígitos"})
            elif ncm == "00000000":
                problemas.append({"tipo": "ncm_zerado", "ref": ref, "detalhe": "NCM zerado (00000000)"})
            # consistência: mesmo produto (código+descrição) com NCMs diferentes
            chave = _norm(it["cProd"]) + "|" + _norm(it["xProd"])
            if chave.strip("|") and ncm:
                por_produto.setdefault(chave, set()).add(ncm)
    for chave, ncms in por_produto.items():
        if len(ncms) > 1:
            _, desc = chave.split("|", 1)
            problemas.append({"tipo": "ncm_divergente", "ref": desc or chave,
                              "detalhe": "Mesmo produto com NCMs diferentes: " + ", ".join(sorted(ncms))})
    return problemas


# ── Auditoria de créditos (só ENTRADAS/compras) ─────────────────────────────
def _auditar_creditos(notas):
    icms_total = 0.0
    base_pc = 0.0            # base creditável de PIS/COFINS (valor das aquisições tributadas)
    vpis_destacado = 0.0
    vcof_destacado = 0.0
    notas_com_credito = []
    revisar_cfop: dict[str, dict] = {}   # motivo -> {qtd, valor}
    fornec: dict[str, dict] = {}         # cnpj -> {nome, notas, valor, icms, base}
    produtos: dict[str, dict] = {}       # ncm|descrição -> agregado do item
    legal: list[dict] = []               # divergências CFOP×CST
    cfop_uso: dict[str, dict] = {}       # cfop -> {qtd, valor, credita}
    for n in notas:
        if n["papel"] != "compra":
            continue
        icms_nota = 0.0
        base_nota = 0.0
        tem_mono = False
        cfops_nota: list[str] = []
        for it in n["itens"]:
            cfop = (it.get("cfop") or "").strip()
            if cfop and cfop not in cfops_nota:
                cfops_nota.append(cfop)
            # legalidade (item a item) — guarda as primeiras 300 p/ não estourar a tela
            for d in _checar_legalidade_item(it):
                if len(legal) < 300:
                    legal.append({**d, "ref": f"nota {n.get('numero') or n['chave'][-6:]} · "
                                                f"item {it.get('nItem')} · {(it.get('xProd') or '')[:40]}",
                                  "cfop": cfop, "fornecedor": n.get("emit_nome") or ""})
            motivo_cfop = _cfop_revisar(it["cfop"])
            # uso de cada CFOP (quantos itens, quanto valor, se creditou)
            cu = cfop_uso.setdefault(cfop or "(sem CFOP)",
                                     {"cfop": cfop or "(sem CFOP)", "qtd": 0, "valor": 0.0,
                                      "icms": 0.0, "motivo": motivo_cfop})
            cu["qtd"] += 1
            cu["valor"] += it["vProd"]
            # agregação por PRODUTO (quais produtos geram crédito)
            chave_prod = (re.sub(r"\D", "", it.get("ncm") or "") + "|" + _norm(it.get("xProd")))[:160]
            pr = produtos.setdefault(chave_prod, {
                "ncm": it.get("ncm") or "", "descricao": (it.get("xProd") or "")[:60],
                "qtd": 0, "valor": 0.0, "icms_credito": 0.0, "base_piscofins": 0.0,
                "cfops": [], "situacao": "", "motivo": ""})
            pr["qtd"] += 1
            pr["valor"] += it["vProd"]
            if cfop and cfop not in pr["cfops"]:
                pr["cfops"].append(cfop)
            if motivo_cfop:
                # CFOP de remessa/devolução/bonificação/ativo/ST: não credita aqui
                r = revisar_cfop.setdefault(motivo_cfop, {"qtd": 0, "valor": 0.0})
                r["qtd"] += 1
                r["valor"] += it["vProd"]
                if not pr["motivo"]:
                    pr["motivo"] = motivo_cfop
                continue
            cred, motivo_icms = _credito_icms(it["cst_icms"], it["vicms"], it["vcred_sn"])
            icms_nota += cred
            pr["icms_credito"] += cred
            cu["icms"] += cred
            if not cred and motivo_icms and not pr["motivo"]:
                pr["motivo"] = motivo_icms
            sit = _situacao_piscofins(it["cst_pis"])
            if sit == "tributado":
                base_nota += it["vProd"]
                pr["base_piscofins"] += it["vProd"]
                vpis_destacado += it["vpis"]
                vcof_destacado += it["vcof"]
            elif sit == "sem_credito":
                tem_mono = True
                if not pr["motivo"]:
                    pr["motivo"] = f"PIS/COFINS CST {it.get('cst_pis')} (monofásico/ST/alíq. zero)"
        icms_total += icms_nota
        base_pc += base_nota
        # custos por fornecedor (Klaus/Analytics faz isso — sai dos mesmos dados)
        fcnpj = _nfe._dig(n.get("emit_cnpj")) or "?"
        fr = fornec.setdefault(fcnpj, {"cnpj": fcnpj, "nome": n.get("emit_nome") or "",
                                       "notas": 0, "valor": 0.0, "icms": 0.0, "base": 0.0})
        fr["notas"] += 1
        fr["valor"] += (n.get("valor") or 0.0)
        fr["icms"] += icms_nota
        fr["base"] += base_nota
        if icms_nota > 0 or base_nota > 0:
            notas_com_credito.append({
                "chave": n["chave"], "numero": n.get("numero"),
                "fornecedor": n.get("emit_nome"), "data": n.get("data"),
                "valor": n.get("valor"),
                "icms_credito": round(icms_nota, 2),
                "base_piscofins": round(base_nota, 2),
                "pis_real": round(base_nota * PIS_NAO_CUMUL, 2),
                "cofins_real": round(base_nota * COFINS_NAO_CUMUL, 2),
                "tem_monofasico": tem_mono,
                "cfops": cfops_nota[:6],
            })
    notas_com_credito.sort(key=lambda x: (x["icms_credito"] + x["base_piscofins"]), reverse=True)
    revisar = [{"motivo": m, "qtd": v["qtd"], "valor": round(v["valor"], 2)}
               for m, v in sorted(revisar_cfop.items(), key=lambda kv: kv[1]["valor"], reverse=True)]
    fornecedores = sorted(
        ({"cnpj": f["cnpj"], "nome": f["nome"], "notas": f["notas"],
          "valor": round(f["valor"], 2), "icms_credito": round(f["icms"], 2),
          "base_piscofins": round(f["base"], 2)} for f in fornec.values()),
        key=lambda x: x["valor"], reverse=True)[:50]
    # produtos: marca quem gera crédito e por quê (os maiores primeiro)
    lista_prod = []
    for p in produtos.values():
        gera = p["icms_credito"] > 0 or p["base_piscofins"] > 0
        p["situacao"] = "gera" if gera else "nao_gera"
        if gera and not p["motivo"]:
            p["motivo"] = "ICMS destacado / aquisição tributada"
        if not gera and not p["motivo"]:
            p["motivo"] = "sem destaque creditável"
        lista_prod.append({**p, "valor": round(p["valor"], 2),
                           "icms_credito": round(p["icms_credito"], 2),
                           "base_piscofins": round(p["base_piscofins"], 2),
                           "pis_real": round(p["base_piscofins"] * PIS_NAO_CUMUL, 2),
                           "cofins_real": round(p["base_piscofins"] * COFINS_NAO_CUMUL, 2)})
    lista_prod.sort(key=lambda x: x["valor"], reverse=True)

    cfops = sorted(({"cfop": c["cfop"], "qtd": c["qtd"], "valor": round(c["valor"], 2),
                     "icms_credito": round(c["icms"], 2),
                     "credita": not c["motivo"], "motivo": c["motivo"]}
                    for c in cfop_uso.values()),
                   key=lambda x: x["valor"], reverse=True)

    return {
        "icms_credito": round(icms_total, 2),
        "base_piscofins": round(base_pc, 2),
        "pis_real": round(base_pc * PIS_NAO_CUMUL, 2),
        "cofins_real": round(base_pc * COFINS_NAO_CUMUL, 2),
        "pis_destacado_fornecedor": round(vpis_destacado, 2),
        "cofins_destacado_fornecedor": round(vcof_destacado, 2),
        "notas": notas_com_credito,
        "revisar_cfop": revisar,
        "fornecedores": fornecedores,
        "produtos": lista_prod[:300],
        "produtos_total": len(lista_prod),
        "cfops": cfops,
        "legalidade": legal,
    }


# ── Combustível como INSUMO ──────────────────────────────────────────────────
# O combustível vem do fornecedor com CST monofásico (04/05/06) e, por isso, o
# auditor comum o classifica como "sem crédito" — o que é correto para REVENDA.
# Quando ele é CONSUMIDO como insumo da atividade (transporte, indústria, rural),
# a vedação não se aplica: a proibição alcança a aquisição para revenda, e o
# crédito é calculado às alíquotas não-cumulativas sobre o valor da aquisição,
# não sobre o valor destacado na nota do fornecedor.
#
# LIMITE DESTE CÓDIGO: quem sabe se o combustível foi consumido na atividade é o
# usuário. Por isso nada entra no crédito sem que ele marque "consumido como
# insumo" — o sistema apenas separa, mede e mostra a conta.

def _auditar_combustivel(notas):
    """Compras de combustível, nota a nota, com a base creditável SE for insumo."""
    itens_por_nota: dict[str, dict] = {}
    por_produto: dict[str, dict] = {}
    total_valor = 0.0
    total_litros = 0.0
    icms_mono = 0.0

    for n in notas:
        if n["papel"] != "compra":
            continue
        for it in n["itens"]:
            if not it.get("combustivel"):
                continue
            # CFOP de remessa/devolução/ativo segue sem crédito, como no resto
            # do auditor — insumo não muda a natureza da operação.
            if _cfop_revisar(it.get("cfop")):
                continue
            valor = it["vProd"]
            litros = it.get("qCom") or 0.0
            total_valor += valor
            total_litros += litros
            icms_mono += it.get("vicms_mono") or 0.0

            desc = (it.get("anp_desc") or it.get("xProd") or "").strip()
            p = por_produto.setdefault(desc[:60] or "(sem descrição)", {
                "descricao": desc[:60] or "(sem descrição)",
                "anp": it.get("anp_cod") or "", "qtd": 0,
                "litros": 0.0, "valor": 0.0, "diesel": bool(re.search(r"diesel", desc, re.I)),
            })
            p["qtd"] += 1
            p["litros"] += litros
            p["valor"] += valor

            r = itens_por_nota.setdefault(n["chave"], {
                "chave": n["chave"], "numero": n.get("numero"),
                "data": n.get("data"), "fornecedor": n.get("emit_nome"),
                "cnpj_fornecedor": _nfe._dig(n.get("emit_cnpj")),
                "litros": 0.0, "valor": 0.0, "icms_mono": 0.0,
                "produtos": [], "cfops": [], "cst_pis": set(),
            })
            r["litros"] += litros
            r["valor"] += valor
            r["icms_mono"] += it.get("vicms_mono") or 0.0
            if desc and desc[:40] not in r["produtos"]:
                r["produtos"].append(desc[:40])
            if it.get("cfop") and it["cfop"] not in r["cfops"]:
                r["cfops"].append(it["cfop"])
            if it.get("cst_pis"):
                r["cst_pis"].add(it["cst_pis"])

    linhas = []
    for r in itens_por_nota.values():
        base = round(r["valor"], 2)
        linhas.append({
            "chave": r["chave"], "numero": r["numero"], "data": r["data"],
            "fornecedor": r["fornecedor"], "cnpj_fornecedor": r["cnpj_fornecedor"],
            "produtos": ", ".join(r["produtos"][:3]),
            "cfops": r["cfops"][:4],
            "cst_pis": ", ".join(sorted(r["cst_pis"])),
            "litros": round(r["litros"], 4),
            "base": base,
            "pis_insumo": round(base * PIS_NAO_CUMUL, 2),
            "cofins_insumo": round(base * COFINS_NAO_CUMUL, 2),
            "icms_mono": round(r["icms_mono"], 2),
        })
    linhas.sort(key=lambda x: x["base"], reverse=True)

    produtos = sorted(por_produto.values(), key=lambda p: p["valor"], reverse=True)
    for p in produtos:
        p["litros"] = round(p["litros"], 4)
        p["valor"] = round(p["valor"], 2)

    base_total = round(total_valor, 2)
    return {
        "notas": linhas,
        "produtos": produtos,
        "quantidade_notas": len(linhas),
        "litros": round(total_litros, 4),
        "base": base_total,
        "pis_insumo": round(base_total * PIS_NAO_CUMUL, 2),
        "cofins_insumo": round(base_total * COFINS_NAO_CUMUL, 2),
        "icms_monofasico": round(icms_mono, 2),
    }


def _aproveitamento(regime, cred):
    """Quanto de cada crédito a empresa PODE aproveitar, dado o regime."""
    regime = (regime or "real").lower()
    if regime == "simples":
        return {
            "icms": 0.0, "pis": 0.0, "cofins": 0.0,
            "nota": "Simples Nacional: recolhe tudo no DAS — NÃO se credita de ICMS, "
                    "PIS nem COFINS das compras. (Pode, isso sim, TRANSMITIR crédito de "
                    "ICMS ao cliente do regime normal via CSOSN 101/201.)",
        }
    if regime == "presumido":
        return {
            "icms": cred["icms_credito"], "pis": 0.0, "cofins": 0.0,
            "nota": "Lucro Presumido: ICMS é não-cumulativo (aproveita o destacado); "
                    "PIS/COFINS são CUMULATIVOS — NÃO há crédito sobre as compras.",
        }
    return {  # real
        "icms": cred["icms_credito"], "pis": cred["pis_real"], "cofins": cred["cofins_real"],
        "nota": "Lucro Real: aproveita ICMS destacado e créditos não-cumulativos de "
                "PIS/COFINS (1,65% e 7,6% sobre as aquisições tributadas). Confira "
                "monofásico/ST e uso/consumo, que têm tratamento próprio.",
    }


# ── NCM: existência na tabela oficial (online, opcional) ────────────────────
# Em vez de consultar código a código (lento e limitado), baixamos a tabela NCM
# INTEIRA uma única vez e validamos todos os códigos localmente contra ela.
_NCM_TABELA_URL = "https://brasilapi.com.br/api/ncm/v1"
_NCM_SET: set[str] | None = None       # cache do processo: {codigos de 8 díg. válidos}


def _carregar_tabela_ncm():
    """Baixa a tabela NCM completa (1 requisição) e devolve o conjunto de códigos
    de 8 dígitos válidos. Cacheia no processo. None se a rede falhar."""
    global _NCM_SET
    if _NCM_SET is not None:
        return _NCM_SET
    try:
        import json
        req = urllib.request.Request(_NCM_TABELA_URL, headers={"User-Agent": "Fiscale/1.0"})
        with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
            lista = json.loads(r.read().decode("utf-8"))
        codigos = set()
        for x in lista:
            d = re.sub(r"\D", "", (x.get("codigo") or ""))
            if len(d) == 8:
                codigos.add(d)
        if codigos:
            _NCM_SET = codigos
        return _NCM_SET
    except Exception:
        return None


def _validar_ncm_online(notas):
    """Confere TODOS os NCMs (8 díg.) distintos contra a tabela oficial.
    Best-effort: se a rede falhar, devolve status 'offline' sem travar."""
    distintos: dict[str, str] = {}   # ncm -> um exemplo de produto
    for n in notas:
        for it in n["itens"]:
            d = re.sub(r"\D", "", it["ncm"])
            if len(d) == 8 and d != "00000000" and d not in distintos:
                distintos[d] = it.get("xProd") or ""
    tabela = _carregar_tabela_ncm()
    if not tabela:
        return {"problemas": [], "distintos": len(distintos), "checados": 0,
                "nao_checados": len(distintos), "offline": True}
    problemas = []
    for ncm8, exemplo in distintos.items():
        if ncm8 not in tabela:
            problemas.append({"tipo": "ncm_inexistente", "ref": exemplo,
                              "detalhe": f"NCM {ncm8[:4]}.{ncm8[4:6]}.{ncm8[6:8]} não consta na tabela oficial (extinto/errado)"})
    return {
        "problemas": problemas,
        "distintos": len(distintos),
        "checados": len(distintos),
        "nao_checados": 0,
        "offline": False,
    }


def conferir_efd(notas, base_declarada=0.0) -> dict:
    """CONFERIDOR EFD × XML — compara a base de crédito DECLARADA na
    EFD-Contribuições com a que os XMLs efetivamente sustentam.

    A separação é feita pelo CST de PIS que o FORNECEDOR destacou: só há respaldo
    direto quando ele pagou a contribuição na etapa anterior (CST 01/02). CST 04
    (monofásico revendido a alíquota zero), 06, 07/08/09 e 49/99 indicam que não
    houve pagamento — é a hipótese do art. 3º, §2º, II das Leis 10.637/2002 e
    10.833/2003, e é o que a fiscalização costuma glosar.

    O conferidor NÃO decide a tese (combustível consumido como insumo tem
    discussão própria — ver _auditar_combustivel): ele mede cada parte e mostra
    o valor em risco para o contador decidir com número na mão."""
    por_cst: dict[str, dict] = {}
    por_cat: dict[str, dict] = {}
    total = 0.0
    for n in notas:
        if n["papel"] != "compra":
            continue
        for it in n["itens"]:
            cst = ((it.get("cst_pis") or "").strip() or "??")
            v = it.get("vProd") or 0.0
            total += v
            situacao, texto = _CST_PIS_TEXTO.get(cst, ("revisar", "CST não previsto — conferir"))
            d = por_cst.setdefault(cst, {"cst": cst, "situacao": situacao, "texto": texto,
                                         "itens": 0, "valor": 0.0})
            d["itens"] += 1
            d["valor"] += v
            cat = _categoria_produto(it.get("ncm"), it.get("xProd"))
            c = por_cat.setdefault(cat, {"categoria": cat, "com": 0.0, "sem": 0.0, "revisar": 0.0})
            chave = "com" if situacao == "com respaldo" else ("sem" if situacao == "sem respaldo" else "revisar")
            c[chave] += v

    com = sum(d["valor"] for d in por_cst.values() if d["situacao"] == "com respaldo")
    sem = sum(d["valor"] for d in por_cst.values() if d["situacao"] == "sem respaldo")
    rev = total - com - sem
    base_declarada = float(base_declarada or 0.0)
    # o que foi declarado além do que tem respaldo direto no CST
    risco_base = max(base_declarada - com, 0.0) if base_declarada else sem
    return {
        "total_compras": round(total, 2),
        "com_respaldo": round(com, 2),
        "sem_respaldo": round(sem, 2),
        "a_revisar": round(rev, 2),
        "base_declarada": round(base_declarada, 2),
        "diferenca": round(base_declarada - com, 2) if base_declarada else None,
        "credito_com_respaldo": {"pis": round(com * PIS_NAO_CUMUL, 2),
                                 "cofins": round(com * COFINS_NAO_CUMUL, 2)},
        "credito_declarado": ({"pis": round(base_declarada * PIS_NAO_CUMUL, 2),
                               "cofins": round(base_declarada * COFINS_NAO_CUMUL, 2)}
                              if base_declarada else None),
        "risco": {"base": round(risco_base, 2),
                  "pis": round(risco_base * PIS_NAO_CUMUL, 2),
                  "cofins": round(risco_base * COFINS_NAO_CUMUL, 2),
                  "total": round(risco_base * (PIS_NAO_CUMUL + COFINS_NAO_CUMUL), 2)},
        "por_cst": sorted(por_cst.values(), key=lambda x: -x["valor"]),
        "por_categoria": sorted(
            [{**c, "com": round(c["com"], 2), "sem": round(c["sem"], 2),
              "revisar": round(c["revisar"], 2)} for c in por_cat.values()],
            key=lambda x: -(x["com"] + x["sem"] + x["revisar"])),
    }


def auditar(pasta_cnpj, cnpj, data_ini=None, data_fim=None, regime="real",
            validar_ncm=False, combustivel_insumo=False, base_efd=0.0) -> dict:
    notas = _coletar(pasta_cnpj, cnpj)

    def _no_periodo(n):
        d = n.get("data") or ""
        if data_ini and d and d < data_ini:
            return False
        if data_fim and d and d > data_fim:
            return False
        return True

    notas = [n for n in notas if _no_periodo(n)]
    compras = [n for n in notas if n["papel"] == "compra"]
    vendas = [n for n in notas if n["papel"] in ("venda", "nfce")]

    cred = _auditar_creditos(notas)
    comb = _auditar_combustivel(notas)
    aprov = _aproveitamento(regime, cred)

    # O crédito de combustível como insumo só entra se o usuário afirmar o uso
    # E o regime for Lucro Real (no cumulativo não há crédito de PIS/COFINS).
    comb["considerado"] = bool(combustivel_insumo) and (regime or "real").lower() == "real"
    if comb["considerado"] and comb["base"] > 0:
        aprov["pis"] = round(aprov["pis"] + comb["pis_insumo"], 2)
        aprov["cofins"] = round(aprov["cofins"] + comb["cofins_insumo"], 2)
        aprov["combustivel_insumo"] = {
            "pis": comb["pis_insumo"], "cofins": comb["cofins_insumo"], "base": comb["base"],
        }
        aprov["nota"] += (
            f' Somado o combustível consumido como insumo: base de {comb["base"]:.2f} '
            f'({comb["litros"]:.0f} un.), que o fornecedor destacou como monofásico e por '
            'isso não entra na base comum.'
        )
    elif bool(combustivel_insumo) and (regime or "real").lower() != "real":
        comb["aviso_regime"] = ('Crédito de PIS/COFINS sobre insumo existe apenas no Lucro Real '
                                '(não-cumulativo). Neste regime a base fica só para conferência.')

    ncm_prob = _auditar_ncm(notas)

    ncm_online = None
    if validar_ncm:
        ncm_online = _validar_ncm_online(notas)
        ncm_prob = ncm_prob + ncm_online["problemas"]

    return {
        "ok": True,
        "regime": (regime or "real").lower(),
        "periodo": {"ini": data_ini, "fim": data_fim},
        "totais": {
            "notas": len(notas),
            "compras": len(compras),
            "vendas": len(vendas),
            "itens": sum(len(n["itens"]) for n in notas),
        },
        "creditos": cred,
        "combustivel": comb,
        "aproveitavel": aprov,
        "conferidor": conferir_efd(notas, base_efd),
        "ncm_problemas": ncm_prob,
        "ncm_ok": len(ncm_prob) == 0,
        "ncm_online": ncm_online,   # None se não foi pedida a validação online
    }
