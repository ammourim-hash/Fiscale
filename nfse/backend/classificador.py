"""
classificador.py — Lê os XMLs JÁ BAIXADOS (NFS-e Nacional e NF-e) de uma empresa
e classifica: tipo de receita, item da LC 116 e Anexo sugerido do Simples Nacional.

Diferenças em relação ao classificador de planilha (Apps Script):
  • A fonte é local e já separada por CNPJ (nada de subir XML em pasta).
  • O item da LC 116 sai do cTribNac (6 dígitos = item+subitem+desdobramento).
    Ex.: 090201 -> "9.02"; 171901 -> "17.19". Sem isso, o Anexo fixado em lei
    (art. 18 §5º-B e §5º-C) nunca era aplicado às NFS-e Nacionais.
  • O RBT12 é calculado das próprias notas (12 meses), não digitado.
  • O regime vem do XML (opSimpNac) quando disponível.
  • Cancelamento vem dos eventos já baixados, não de texto do cStat.

O Anexo é SUGESTÃO para conferência — a decisão final é do contador.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "{http://www.sped.fazenda.gov.br/nfse}"
NFE = "{http://www.portalfiscal.inf.br/nfe}"

# Listas do escritório (mesmas do classificador de planilha). São o conhecimento
# fiscal de vocês — mantidas como fonte da verdade, não inventadas aqui.
# LC 123, art. 18, §5º-C -> Anexo IV (CPP fora do DAS)
LC116_ANEXO_IV = {"7.02", "7.04", "7.05", "7.11", "7.15", "7.16", "7.17",
                  "7.19", "7.10", "11.02", "17.14"}
# LC 123, art. 18, §5º-B -> Anexo III direto (sem Fator R)
# "4.08" (fisioterapia) acrescentado: o PGDAS-D real da MARIA DAS VITORIAS
# (PA 06/2026) declara essas notas como Anexo III "não sujeitas ao fator r".
LC116_ANEXO_III = {"4.08", "8.02", "9.02", "12.01", "12.02", "12.03", "12.04", "12.05",
                   "12.06", "12.07", "12.08", "12.09", "12.10", "12.11", "12.12",
                   "12.13", "12.14", "12.15", "12.16", "12.17", "16.01", "16.02",
                   "17.08", "17.19", "21.01"}

# ── Sujeição ao FATOR R, pela atividade ─────────────────────────────────────
# A pergunta "esta empresa é Fator R?" NÃO é uma preferência a marcar numa
# tela: quem responde é a LEI, pelo item da LC 116 do serviço prestado. Antes
# desta lista o sistema só sabia dizer "informe a Folha ou escolha o Anexo" —
# devolvia a pergunta para quem a tinha feito.
#
# LC 123/2006, art. 18:
#   §5º-D  atividades tributadas pelo Anexo V;
#   §5º-I  atividades que eram do extinto Anexo VI (LC 155/2016);
#   §5º-J  as dos §§5º-D e 5º-I vão para o **Anexo III** quando o fator r
#          (folha 12m ÷ receita bruta 12m) for **≥ 28%**; abaixo disso, Anexo V.
#
# A ORDEM IMPORTA e está no `resolver_anexo`: §5º-C (Anexo IV) e §5º-B
# (Anexo III fixo) vêm ANTES. Um item que a lei já fixou não entra em Fator R —
# é por isso que contabilidade (17.19) e fisioterapia (4.08) seguem no Anexo III
# mesmo com folha baixa, e advocacia (17.14) segue no IV.
#
# Cada entrada abaixo cita o inciso que a sustenta. Item que não estiver em
# NENHUMA das três listas continua perguntando — inventar enquadramento seria
# pior do que admitir a dúvida.
LC116_FATOR_R = {
    # §5º-D, IV/V/VI — software, licenciamento e páginas eletrônicas
    "1.04": "§5º-D, IV — elaboração de programas de computador",
    "1.05": "§5º-D, V — licenciamento/cessão de uso de programas",
    "1.08": "§5º-D, VI — confecção e manutenção de páginas eletrônicas",
    # §5º-I, VI — suporte e análises técnicas e tecnológicas
    "1.07": "§5º-I, VI — suporte técnico em informática",
    # §5º-I, I a IV — saúde (o extinto Anexo VI)
    "4.01": "§5º-I, I — medicina",
    "4.02": "§5º-D, XII — laboratórios de análises clínicas/patologia",
    "4.03": "§5º-I, I — hospitais, clínicas e pronto-socorro",
    "4.06": "§5º-I, I — enfermagem",
    "4.11": "§5º-I, I — obstetrícia",
    "4.12": "§5º-I, III — odontologia",
    "4.13": "§5º-I, XII — ortóptica (profissão regulamentada da saúde)",
    "4.14": "§5º-D, XIV — próteses sob encomenda",
    "4.15": "§5º-I, IV — psicanálise",
    "4.16": "§5º-I, IV — psicologia",
    "4.17": "§5º-I, IV — casas de repouso e de recuperação",
    "4.19": "§5º-I, XII — instrumentação cirúrgica",
    "4.20": "§5º-D, XIII — acupuntura",
    "4.21": "§5º-I, XII — terapia ocupacional, fisiatria e fonoaudiologia",
    "5.01": "§5º-I, II — medicina veterinária",
    # §5º-I, VI — engenharia, arquitetura e afins (a OBRA é §5º-C, Anexo IV)
    "7.01": "§5º-I, VI — engenharia, agronomia, arquitetura, urbanismo",
    "7.03": "§5º-I, VI — estudos, projetos e planos diretores",
    "7.20": "§5º-I, VI — aerofotogrametria, topografia, geologia, geodésia",
    # §5º-I, VII e XI — representação, intermediação e agenciamento
    "10.09": "§5º-I, VII — representação de qualquer natureza",
    # §5º-I, VIII a X — perícia, consultoria, publicidade
    "17.01": "§5º-I, IX — assessoria, consultoria, análise e organização",
    "17.03": "§5º-I, IX — planejamento, coordenação e controle de programas",
    "17.09": "§5º-I, VIII — perícia, laudo, exame técnico e análise",
    "17.10": "§5º-I, VIII — avaliação de bens e serviços",
    "17.12": "§5º-I, IX — administração de bens e negócios de terceiros",
    "17.16": "§5º-I, VIII — leilão e congêneres",
    "17.18": "§5º-I, IX — atuária e cálculos técnicos",
    "17.20": "§5º-I, IX — auditoria",
    "17.06": "§5º-I, X — propaganda e publicidade",
    "17.24": "§5º-I, X — inserção de textos e material de publicidade",
}

# ── ISS em valor FIXO (fora do DAS) ─────────────────────────────────────────
# LC 123/2006, art. 18, §5º-B, XIV c/c §22-A: o ESCRITÓRIO DE SERVIÇOS CONTÁBEIS
# é tributado pelo Anexo III, mas recolhe o ISS em VALOR FIXO à prefeitura, "na
# forma da legislação municipal" — ou seja, a fatia do ISS SAI do DAS. O mesmo
# vale para a sociedade uniprofissional que recolhe ISS fixo por profissional.
#
# CONFERIDO contra o DAS real da MONTE ASSESSORIA (PA 06/2026):
#   R$ 100.458,50 × 13,232% × (1 − 32,50% de ISS) = R$ 8.972,55 — idêntico à guia.
# Sem esta regra o sistema apurava R$ 12.970,93 (R$ 3.998,38 a mais).
#
# NÃO é automático por lei: depende da legislação municipal e do enquadramento.
# Por isso é opção do cadastro — o sistema apenas SUGERE quando vê o item 17.19.
ITEM_CONTABIL = "17.19"
MOTIVO_ISS_FIXO = "ISS fixo — escritório de serviços contábeis (LC 123/2006, art. 18, §22-A)"


# ── Tabelas do Simples Nacional (LC 123 / Res. CGSN 140/2018) ───────────────
# Cada faixa: (limite superior do RBT12, alíquota nominal, parcela a deduzir)
TABELAS = {
    "I": [(180000, .04, 0), (360000, .073, 5940), (720000, .095, 13860),
          (1800000, .107, 22500), (3600000, .143, 87300), (4800000, .19, 378000)],
    "II": [(180000, .045, 0), (360000, .078, 5940), (720000, .10, 13860),
           (1800000, .112, 22500), (3600000, .147, 85500), (4800000, .30, 720000)],
    "III": [(180000, .06, 0), (360000, .112, 9360), (720000, .135, 17640),
            (1800000, .16, 35640), (3600000, .21, 125640), (4800000, .33, 648000)],
    "IV": [(180000, .045, 0), (360000, .09, 8100), (720000, .102, 12420),
           (1800000, .14, 39780), (3600000, .22, 183780), (4800000, .33, 828000)],
    "V": [(180000, .155, 0), (360000, .18, 4500), (720000, .195, 9900),
          (1800000, .205, 17100), (3600000, .23, 62100), (4800000, .305, 540000)],
}
LIMITE_SIMPLES = 4800000.0
SUBLIMITE_ISS_ICMS = 3600000.0

# Repartição dos tributos DENTRO da alíquota efetiva, por Anexo e faixa.
# O Anexo III (faixa 2) foi CONFERIDO contra um PGDAS-D real:
#   IRPJ 4,00% · CSLL 3,50% · COFINS 14,05% · PIS 3,05% · CPP 43,40% · ISS 32,00%
REPARTICAO = {
    "I": [  # Comércio (ICMS)
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1274, "PIS": .0276, "CPP": .4150, "ICMS": .3400},
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1274, "PIS": .0276, "CPP": .4150, "ICMS": .3400},
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1274, "PIS": .0276, "CPP": .4200, "ICMS": .3350},
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1274, "PIS": .0276, "CPP": .4200, "ICMS": .3350},
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1274, "PIS": .0276, "CPP": .4200, "ICMS": .3350},
        {"IRPJ": .135, "CSLL": .100, "COFINS": .2827, "PIS": .0613, "CPP": .4210},
    ],
    "II": [  # Indústria (IPI + ICMS)
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1151, "PIS": .0249, "CPP": .3750, "IPI": .0750, "ICMS": .3200},
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1151, "PIS": .0249, "CPP": .3750, "IPI": .0750, "ICMS": .3200},
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1151, "PIS": .0249, "CPP": .3750, "IPI": .0750, "ICMS": .3200},
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1151, "PIS": .0249, "CPP": .3750, "IPI": .0750, "ICMS": .3200},
        {"IRPJ": .055, "CSLL": .035, "COFINS": .1151, "PIS": .0249, "CPP": .3750, "IPI": .0750, "ICMS": .3200},
        {"IRPJ": .085, "CSLL": .075, "COFINS": .2096, "PIS": .0454, "CPP": .2350, "IPI": .3500},
    ],
    "III": [  # Serviços (ISS)
        {"IRPJ": .040, "CSLL": .035, "COFINS": .1282, "PIS": .0278, "CPP": .4340, "ISS": .3350},
        {"IRPJ": .040, "CSLL": .035, "COFINS": .1405, "PIS": .0305, "CPP": .4340, "ISS": .3200},
        {"IRPJ": .040, "CSLL": .035, "COFINS": .1364, "PIS": .0296, "CPP": .4340, "ISS": .3250},
        {"IRPJ": .040, "CSLL": .035, "COFINS": .1364, "PIS": .0296, "CPP": .4340, "ISS": .3250},
        {"IRPJ": .040, "CSLL": .035, "COFINS": .1282, "PIS": .0278, "CPP": .4340, "ISS": .3350},
        {"IRPJ": .350, "CSLL": .150, "COFINS": .1603, "PIS": .0347, "CPP": .3050},
    ],
    "IV": [  # Serviços — CPP recolhida FORA do DAS
        {"IRPJ": .188, "CSLL": .152, "COFINS": .1767, "PIS": .0383, "ISS": .4450},
        {"IRPJ": .198, "CSLL": .152, "COFINS": .2055, "PIS": .0445, "ISS": .4000},
        {"IRPJ": .208, "CSLL": .152, "COFINS": .1973, "PIS": .0427, "ISS": .4000},
        {"IRPJ": .178, "CSLL": .192, "COFINS": .1890, "PIS": .0410, "ISS": .4000},
        {"IRPJ": .188, "CSLL": .192, "COFINS": .1808, "PIS": .0392, "ISS": .4000},
        {"IRPJ": .535, "CSLL": .215, "COFINS": .2055, "PIS": .0445},
    ],
    "V": [  # Serviços
        {"IRPJ": .250, "CSLL": .150, "COFINS": .1410, "PIS": .0305, "CPP": .2885, "ISS": .1400},
        {"IRPJ": .230, "CSLL": .150, "COFINS": .1410, "PIS": .0305, "CPP": .2785, "ISS": .1700},
        {"IRPJ": .240, "CSLL": .150, "COFINS": .1492, "PIS": .0323, "CPP": .2385, "ISS": .1900},
        {"IRPJ": .210, "CSLL": .150, "COFINS": .1574, "PIS": .0341, "CPP": .2385, "ISS": .2100},
        {"IRPJ": .230, "CSLL": .125, "COFINS": .1410, "PIS": .0305, "CPP": .2385, "ISS": .2350},
        {"IRPJ": .350, "CSLL": .155, "COFINS": .1644, "PIS": .0356, "CPP": .2950},
    ],
}


def _brl(v: float) -> str:
    """Número no formato pt-BR. O ',.2f' do Python sai americano (37,996.88)."""
    return f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def reparticao(anexo: str, faixa: int) -> dict:
    tab = REPARTICAO.get(anexo)
    if not tab or not faixa or faixa > len(tab):
        return {}
    return tab[faixa - 1]


def aliquota_efetiva(anexo: str, rbt12: float):
    """Alíquota efetiva = [(RBT12 × Alíq) − PD] / RBT12  (Res. CGSN 140/2018).
    Devolve (efetiva, faixa, nominal, pd) ou None se o anexo não tem tabela."""
    tab = TABELAS.get(anexo)
    if not tab or rbt12 <= 0:
        return None
    for i, (limite, aliq, pd) in enumerate(tab, start=1):
        if rbt12 <= limite:
            ef = ((rbt12 * aliq) - pd) / rbt12
            return {"efetiva": round(max(ef, 0), 6), "faixa": i,
                    "nominal": aliq, "pd": pd}
    # acima de 4,8 mi: fora do Simples
    return {"efetiva": None, "faixa": 0, "nominal": None, "pd": None,
            "erro": "RBT12 acima do limite do Simples (R$ 4.800.000)"}


# ── Receita sem nota fiscal (lançamento manual) ──────────────────────────────
# Nem toda receita tem NFS-e: aluguel, por exemplo, não gera nota de serviço.
# Sem isso, uma empresa só de aluguel não tinha DAS nenhum (a apuração do mês
# só olhava as notas). Aqui o lançamento vira uma "nota sintética" e percorre
# exatamente o mesmo cálculo já conferido contra o PGDAS-D.
#
# `sem_iss=True` marca a receita cuja fatia de ISS sai do DAS. O caso clássico é
# a LOCAÇÃO DE BENS MÓVEIS: o STF decidiu que locação não é serviço e não incide
# ISS (Súmula Vinculante 31) — reaproveitamos o mesmo mecanismo do ISS retido.
TIPOS_RECEITA = [
    {"id": "aluguel_movel", "rotulo": "Aluguel / locação de bens móveis",
     "anexo": "III", "sem_iss": True,
     "nota": "Locação de bem móvel não é serviço: não incide ISS (Súmula Vinculante 31 do STF)."},
    {"id": "aluguel_imovel", "rotulo": "Aluguel de imóvel próprio",
     "anexo": "III", "sem_iss": True,
     "nota": "Confira o enquadramento e o CNAE da empresa antes de usar."},
    {"id": "servico_iii", "rotulo": "Serviço sem nota — Anexo III", "anexo": "III", "sem_iss": False},
    {"id": "servico_iv", "rotulo": "Serviço sem nota — Anexo IV", "anexo": "IV", "sem_iss": False},
    {"id": "servico_v", "rotulo": "Serviço sem nota — Anexo V", "anexo": "V", "sem_iss": False},
    {"id": "comercio", "rotulo": "Venda de mercadoria (comércio)", "anexo": "I", "sem_iss": True,
     "nota": "Anexo I não tem ISS."},
    {"id": "industria", "rotulo": "Indústria", "anexo": "II", "sem_iss": True,
     "nota": "Anexo II não tem ISS."},
]
_TIPOS = {t["id"]: t for t in TIPOS_RECEITA}


def lancamentos_para_notas(lancs) -> list[dict]:
    """Converte os lançamentos manuais em 'notas' para entrarem na apuração.
    Cada lançamento: {competencia, valor, tipo|anexo, descricao, iss_retido}."""
    out = []
    for i, l in enumerate(lancs or []):
        try:
            valor = float(l.get("valor") or 0)
        except (TypeError, ValueError):
            continue
        comp = str(l.get("competencia") or "")[:7]
        if not comp or valor <= 0:
            continue
        tipo = _TIPOS.get(l.get("tipo") or "")
        anexo = (l.get("anexo") or (tipo or {}).get("anexo") or "").upper()
        if not anexo:
            continue
        # ISS fora do DAS: por natureza do tipo (locação/comércio) ou por retenção
        sem_iss = bool(l.get("iss_retido")) or bool((tipo or {}).get("sem_iss"))
        rotulo = (tipo or {}).get("rotulo") or "Lançamento manual"
        out.append({
            "origem": "Manual",
            "arquivo": f"manual-{i}",
            "chave": "", "numero": "", "cstat": "100",
            "data": f"{comp}-01",
            "competencia": comp,
            "tomador": l.get("descricao") or "",
            "ctribnac": "", "item_lc116": "",
            "descricao": l.get("descricao") or rotulo,
            "valor": round(valor, 2),
            "valor_liquido": round(valor, 2),
            "op_simp_nac": "", "iss_retido": sem_iss, "valor_iss": 0.0,
            "municipio": "", "tipo": "Sem nota", "cancelada": False,
            "anexo_manual": anexo,
            "base_manual": rotulo + (" — sem ISS" if sem_iss else ""),
            # o ISS sai do DAS pelo mesmo mecanismo da retenção, mas o MOTIVO é
            # outro — dizer "retido na fonte" num aluguel confundiria o contador
            "motivo_sem_iss": ((tipo or {}).get("nota") or "Receita sem incidência de ISS")
                              if (tipo or {}).get("sem_iss") else
                              ("ISS retido na fonte" if l.get("iss_retido") else ""),
        })
    return out


def _meses_entre(de: str, ate: str) -> int:
    """Quantos meses de 'AAAA-MM' até 'AAAA-MM'. Negativo se 'ate' for anterior."""
    a1, m1 = int(de[:4]), int(de[5:7])
    a2, m2 = int(ate[:4]), int(ate[5:7])
    return (a2 * 12 + m2) - (a1 * 12 + m1)


def _mes_menos(comp: str, n: int) -> str:
    """'2026-06' - n meses -> 'AAAA-MM'."""
    a, m = int(comp[:4]), int(comp[5:7])
    total = a * 12 + (m - 1) - n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


# ── Base do período: EMISSÃO ou COMPETÊNCIA ─────────────────────────────────
# Uma nota emitida em 03/09 com competência 08/2026 cai em agosto por
# competência e em setembro por emissão. Os dois panoramas são legítimos e dão
# números diferentes — o que não pode acontecer é a TELA listar por um critério
# e o DAS somar pelo outro, que era o comportamento anterior (lista por emissão,
# DAS por competência). Daqui para frente a base é UMA SÓ, escolhida na tela, e
# atravessa período, RBT12, Fator R e a apuração de cada mês.
BASES = ("competencia", "emissao")


def data_base(n, base: str = "competencia") -> str:
    """A data 'AAAA-MM-DD' que manda nesta nota, segundo a base escolhida."""
    if base == "emissao":
        return (n.get("data") or "")[:10]
    # Competência: o dCompet completo quando a fonte tem (NFS-e nacional);
    # senão o mês da competência da própria nota, dia 1.
    #
    # DE PROPÓSITO não olha `competencia_xml`. O histórico municipal guarda ali
    # a competência real, mas o campo `competencia` dele SEMPRE foi o mês da
    # emissão — e é sobre esse campo que a regra anti-duplicidade e o RBT12
    # foram conferidos contra uma guia de DAS real. Trocar a base do histórico
    # aqui mudaria, em silêncio, números que já foram validados.
    comp = n.get("data_competencia")
    if not comp and n.get("competencia"):
        comp = f'{str(n["competencia"])[:7]}-01'
    return (comp or n.get("data") or "")[:10]


def mes_base(n, base: str = "competencia") -> str:
    return data_base(n, base)[:7]


def receita_do_mes(notas, comp: str, base: str = "competencia") -> float:
    return round(sum(n.get("valor", 0.0) for n in notas
                     if mes_base(n, base) == comp and not n.get("cancelada")), 2)


def primeiro_mes_com_nota(notas, base: str = "competencia") -> str | None:
    """Mês da nota mais antiga. Antes dele o sistema NÃO TEM dados;
    a partir dele, mês zerado é mês sem faturamento de verdade.

    Sem essa distinção, todo mês parado virava "informe esse mês" e disparava o
    alerta vermelho de RBT12 furado — assustando à toa quem simplesmente não
    faturou naquele mês."""
    comps = [mes_base(n, base) for n in notas if not n.get("cancelada")]
    comps = [c for c in comps if c]
    return min(comps) if comps else None


def rbt12_de(notas, pa: str, manuais: dict | None = None,
             inicio: str | None = None, base: str = "competencia") -> dict:
    """RBT12 do PA ('AAAA-MM') = receita dos 12 meses ANTERIORES ao mês de apuração.

    `manuais` ({'AAAA-MM': valor}) tem PRECEDÊNCIA: serve para os meses que não
    existem na API (ex.: notas emitidas no sistema municipal antes da migração
    para a NFS-e Nacional).

    `inicio` ('AAAA-MM') = início de atividade. Nos 12 primeiros meses a empresa
    não tem 12 meses de histórico, e a lei manda PROPORCIONALIZAR em vez de somar
    o que houver (Resolução CGSN 140/2018, art. 21, §2º):
      I  - no primeiro mês de atividade: RBT12 = receita do próprio mês x 12;
      II - nos 11 meses seguintes: média dos meses anteriores x 12.
    Somar direto deixaria o RBT12 baixo, jogaria a empresa na 1ª faixa e o DAS
    sairia MENOR que o devido — o erro perigoso.
    """
    manuais = manuais or {}
    primeiro = primeiro_mes_com_nota(notas, base)
    # Só é "sem dados" o mês anterior ao primeiro que conhecemos (ou anterior ao
    # início de atividade). Depois disso, zero é zero.
    corte = inicio or primeiro

    ini, fim = _mes_menos(pa, 12), _mes_menos(pa, 1)
    meses, total, faltando = [], 0.0, []
    m = ini
    while m <= fim:
        if m in manuais:
            v, origem = float(manuais[m]), "informado"
        elif inicio and m < inicio:
            v, origem = 0.0, "antes da abertura"
        else:
            v, origem = receita_do_mes(notas, m, base), "xml"
            if v == 0:
                if corte and m >= corte:
                    origem = "sem faturamento"
                else:
                    faltando.append(m)
        total += v
        meses.append({"comp": m, "valor": round(v, 2), "origem": origem})
        m = _mes_menos(m, -1)

    # ── Início de atividade: proporcionalizar ───────────────────────────────
    proporcional = None
    if inicio and inicio <= pa and _meses_entre(inicio, pa) < 12:
        ativos = [x for x in meses if x["comp"] >= inicio]
        if ativos:
            media = sum(x["valor"] for x in ativos) / len(ativos)
        else:
            # Primeiro mês de atividade: não há mês anterior — usa o próprio PA.
            media = receita_do_mes(notas, pa)
        total = round(media * 12, 2)
        proporcional = {"meses_considerados": len(ativos) or 1,
                        "media_mensal": round(media, 2),
                        "regra": "CGSN 140/2018, art. 21, §2º — RBT12 proporcional"}
        faltando = []          # não há o que informar: a empresa ainda não existia

    return {"total": round(total, 2), "meses": meses, "sem_dados": faltando,
            "proporcional": proporcional}


def calcular_das(notas, pa: str, cfg, fator_r, base: str = "competencia"):
    """DAS estimado de um mês (PA). Segrega a receita por Anexo E por retenção
    de ISS — quando o ISS é retido na fonte, a fatia do ISS sai do DAS (é o que
    o PGDAS-D faz ao separar 'com retenção/substituição tributária de ISS')."""
    do_mes = [n for n in notas
              if mes_base(n, base) == pa and not n.get("cancelada")]
    if not do_mes:
        return None
    inicio = str(cfg.get("inicioAtividade") or "")[:7] or None
    r12 = rbt12_de(notas, pa, cfg.get("receitas_manuais"), inicio, base)
    rbt = r12["total"]

    # (anexo, iss_retido, motivo) -> receita
    # O motivo separa "ISS retido na fonte" de "não incide ISS" (aluguel, comércio).
    # A conta é a mesma, mas no PGDAS-D são atividades distintas: juntar numa
    # linha só obrigaria a desmembrar na mão na hora de declarar.
    # ISS fixo (escritório contábil / sociedade de profissionais): a fatia do ISS
    # sai do DAS de TODA a receita de serviço — é característica da EMPRESA, não
    # da nota. Ver MOTIVO_ISS_FIXO.
    iss_fixo = bool(cfg.get("issFixo"))
    buckets = defaultdict(float)
    for n in do_mes:
        anexo, justificativa = resolver_anexo(n, cfg, fator_r)
        n["anexo"], n["base_anexo"] = anexo, justificativa
        if iss_fixo and not n.get("iss_retido"):
            n["iss_retido"] = True
            n["motivo_sem_iss"] = MOTIVO_ISS_FIXO
        retido = bool(n.get("iss_retido"))
        motivo = (n.get("motivo_sem_iss") or "ISS retido na fonte") if retido else ""
        buckets[(anexo, retido, motivo)] += n.get("valor", 0.0)

    linhas, total_das, pendencias = [], 0.0, []
    tributos_tot = defaultdict(float)
    for (anexo, retido, motivo), receita in sorted(buckets.items()):
        base_linha = {"anexo": anexo, "receita": round(receita, 2),
                      "iss_retido": retido, "motivo": motivo}
        if anexo not in TABELAS:
            pendencias.append(f"{anexo}: R$ {_brl(receita)} sem Anexo definido")
            linhas.append({**base_linha, "efetiva": None, "das": None, "faixa": None})
            continue
        af = aliquota_efetiva(anexo, rbt) if rbt > 0 else None
        if not af or af.get("efetiva") is None:
            linhas.append({**base_linha, "efetiva": None, "das": None, "faixa": None,
                           "obs": (af or {}).get("erro") or "Sem RBT12 (início de atividade) — usar regra proporcional"})
            pendencias.append(f"{anexo}: sem RBT12 para calcular")
            continue

        rep = reparticao(anexo, af["faixa"])
        iss_pct = rep.get("ISS", 0.0) if retido else 0.0   # só sai se foi retido
        bruto = receita * af["efetiva"]
        das = bruto * (1 - iss_pct)
        total_das += das
        # detalhamento por tributo (ISS zerado quando retido na fonte)
        det = {}
        for trib, pct in rep.items():
            v = 0.0 if (trib == "ISS" and retido) else bruto * pct
            det[trib] = round(v, 2)
            tributos_tot[trib] += v
        linhas.append({**base_linha, "faixa": af["faixa"], "nominal": af["nominal"],
                       "pd": af["pd"], "efetiva": af["efetiva"],
                       "das": round(das, 2), "tributos": det,
                       "obs": (f"{motivo} — fatia do ISS "
                               f"({rep.get('ISS', 0)*100:.2f}%) fora do DAS") if retido else ""})

    iss_retido_fonte = round(sum(n.get("valor_iss", 0.0) for n in do_mes if n.get("iss_retido")), 2)
    return {
        "competencia": pa,
        # Qual data agrupou este mês. A tela precisa dizer isso em cima do
        # quadro: o mesmo período dá DAS diferente por emissão e por competência.
        "base_data": base,
        "rbt12": rbt,
        "rbt12_meses": r12["meses"],
        "rbt12_sem_dados": r12["sem_dados"],
        "rbt12_proporcional": r12.get("proporcional"),
        "receita_mes": round(sum(n.get("valor", 0.0) for n in do_mes), 2),
        "linhas": linhas,
        "tributos": {k: round(v, 2) for k, v in tributos_tot.items()},
        "das_estimado": round(total_das, 2),
        "iss_retido_no_mes": iss_retido_fonte,
        "pendencias": pendencias,
        "acima_sublimite": rbt > SUBLIMITE_ISS_ICMS,
    }


def item_lc116(ctribnac: str) -> str:
    """cTribNac (IISSDD) -> item da LC 116 'II.SS'. Ex.: 090201 -> 9.02."""
    d = re.sub(r"\D", "", ctribnac or "")
    if len(d) >= 4:
        return f"{int(d[:2])}.{d[2:4]}"
    return ""


def tipo_por_cfop(cfop: str) -> str:
    """CFOP -> tipo de receita, por FAIXA (não por lista fechada)."""
    c = re.sub(r"\D", "", cfop or "")
    if len(c) != 4:
        return "Indefinido"
    if c[0] in "123":
        return "Devolução/Entrada"          # entrada nunca é receita de saída
    if c[0] not in "567":
        return "Indefinido"
    g = c[1:]                                # 3 dígitos do grupo
    ultimo = int(c[-1])
    if g.startswith("1"):                    # 5.1xx/6.1xx = vendas
        # ímpar final = produção própria (indústria); par = revenda de terceiros
        return "Indústria" if ultimo % 2 == 1 else "Comércio"
    if g.startswith("4"):                    # 5.4xx = ST
        return "Indústria" if ultimo % 2 == 1 else "Comércio"
    if g.startswith("3") or g in ("933", "932"):
        return "Serviço"                     # 5.3xx = comunicação/transporte; 5.933 = serviço
    if g.startswith(("2", "9", "5")):        # transferências, remessas, ajustes
        return "Não é receita"
    return "Indefinido"


def anexo_por_lc116(item: str):
    """Anexo quando a LEI fixa. Senão devolve ('', motivo) e cai no Fator R."""
    if not item:
        return "", ""
    if item in LC116_ANEXO_IV:
        return "IV", f"LC116 {item} — Anexo IV por lei (art. 18 §5º-C)"
    if item in LC116_ANEXO_III:
        return "III", f"LC116 {item} — Anexo III por lei (art. 18 §5º-B)"
    return "", f"LC116 {item} — depende do Fator R / anexo da empresa"


def _txt(el, tag):
    if el is None:
        return ""
    n = el.find(f".//{NS}{tag}")
    return (n.text or "").strip() if n is not None and n.text else ""


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _canceladas(xml_dir: Path) -> set:
    """Chaves canceladas/substituídas — dos eventos já baixados."""
    fora = set()
    for f in xml_dir.glob("evento-*.xml"):
        try:
            d = ET.fromstring(f.read_text("utf-8"))
        except Exception:
            continue
        ch = d.find(f".//{NS}chNFSe")
        desc = (d.findtext(f".//{NS}xDesc") or "").lower()
        if ch is not None and ch.text and ("cancel" in desc or "substitu" in desc):
            fora.add(ch.text)
    return fora


def _retencoes_federais(inf) -> dict:
    """PIS, COFINS, CSLL e IRRF retidos na fonte, lidos da nota.

    A composição é a de `retencao.compor` — a MESMA de core.parse_nfse. Antes
    esta função tinha cópia própria da regra, e as duas somavam vPis/vCofins
    por cima de um vRetCSLL que às vezes já era o total consolidado.

    `consolidado` = há CSLL sem PIS/COFINS discriminados que não deu para
    dividir com respaldo legal."""
    import retencao as _ret
    comp = _ret.compor(_txt(inf, "tpRetPisCofins"), _num(_txt(inf, "vPis")),
                       _num(_txt(inf, "vCofins")), _num(_txt(inf, "vRetCSLL")),
                       _num(_txt(inf, "vServ")) or _num(_txt(inf, "vLiq")))
    irrf = _num(_txt(inf, "vRetIRRF"))
    alerta = _ret.alerta_csrf(comp["total"],
                              _num(_txt(inf, "vServ")) or _num(_txt(inf, "vLiq")))
    return {
        "ret_pis": comp["pis"], "ret_cofins": comp["cofins"],
        "ret_csll": comp["csll"], "ret_irrf": round(irrf, 2),
        "ret_total": comp["total"],
        "ret_rateado": comp["rateada"],
        "ret_consolidado": bool(comp["csll"] > 0 and comp["pis"] == 0
                                and comp["cofins"] == 0),
        "alerta_csrf": alerta["alerta"],
        "alerta_csrf_msg": alerta["mensagem"],
    }


def ler_nfse(pasta_cnpj, cnpj) -> list[dict]:
    """NFS-e EMITIDAS pela empresa, com item LC116 e valores."""
    xml_dir = Path(pasta_cnpj) / "xmls"
    if not xml_dir.exists():
        return []
    fora = _canceladas(xml_dir)
    out = []
    for f in xml_dir.glob("nfse-*.xml"):
        try:
            root = ET.fromstring(f.read_text("utf-8"))
        except Exception:
            continue
        inf = root.find(f"{NS}infNFSe")
        if inf is None:
            continue
        emit_cnpj = _txt(inf.find(f"{NS}emit"), "CNPJ") or _txt(inf.find(f"{NS}emit"), "CPF")
        if emit_cnpj != cnpj:
            continue                                  # só as EMITIDAS (receita)
        chave = (inf.get("Id") or "").replace("NFS", "", 1)
        ctrib = _txt(inf, "cTribNac")
        item = item_lc116(ctrib)
        dcomp = _txt(inf, "dCompet")[:10]
        dh = _txt(inf, "dhProc")[:10]
        toma = inf.find(f".//{NS}toma")
        out.append({
            "origem": "NFS-e", "arquivo": f.name, "chave": chave,
            "numero": _txt(inf, "nNFSe"), "cstat": _txt(inf, "cStat"),
            "data": dh or dcomp, "competencia": (dcomp or dh)[:7],
            "data_competencia": dcomp or dh,
            "tomador": _txt(toma, "xNome") if toma is not None else "",
            "ctribnac": ctrib, "item_lc116": item,
            "descricao": _txt(inf, "xTribNac") or _txt(inf, "xDescServ"),
            "valor": _num(_txt(inf, "vServ")) or _num(_txt(inf, "vLiq")),
            "valor_liquido": _num(_txt(inf, "vLiq")),
            "op_simp_nac": _txt(inf, "opSimpNac"),
            "iss_retido": _txt(inf, "tpRetISSQN") in ("2", "3"),
            "valor_iss": _num(_txt(inf, "vISSQN")),
            # Retenções federais discriminadas. tpRetPisCofins manda em PIS e
            # COFINS (1=os dois, 3=só PIS, 4=só COFINS); a CSLL tem campo
            # próprio. Quando o emitente CONSOLIDA os três em vRetCSLL, vPis e
            # vCofins vêm zerados — a tela marca "consolidado" em vez de exibir
            # zeros, que se leriam como "não houve retenção".
            **_retencoes_federais(inf),
            "municipio": _txt(inf, "cLocIncid"),
            "tipo": "Serviço",
            "cancelada": chave in fora,
        })
    out.sort(key=lambda x: x.get("data") or "", reverse=True)
    return out


def ler_nfe_itens(pasta_cnpj, cnpj) -> list[dict]:
    """Itens das NF-e (CFOP/NCM). Hoje o Fiscale baixa as RECEBIDAS (compras)."""
    d = Path(pasta_cnpj) / "nfe"
    if not d.exists():
        return []
    out = []
    for f in d.glob("*-procNFe.xml"):
        try:
            root = ET.fromstring(f.read_text("utf-8"))
        except Exception:
            continue
        inf = root.find(f".//{NFE}infNFe")
        if inf is None:
            continue
        chave = (inf.get("Id") or "").replace("NFe", "")
        ide = inf.find(f"{NFE}ide")
        emit = inf.find(f"{NFE}emit")
        dest = inf.find(f"{NFE}dest")
        emit_cnpj = emit.findtext(f"{NFE}CNPJ") if emit is not None else None
        dest_cnpj = dest.findtext(f"{NFE}CNPJ") if dest is not None else None
        data = (ide.findtext(f"{NFE}dhEmi") or "")[:10] if ide is not None else ""
        papel = "venda" if emit_cnpj == cnpj else ("compra" if dest_cnpj == cnpj else "terceiro")
        for det in inf.findall(f"{NFE}det"):
            p = det.find(f"{NFE}prod")
            if p is None:
                continue
            cfop = p.findtext(f"{NFE}CFOP") or ""
            out.append({
                "origem": "NF-e", "arquivo": f.name, "chave": chave, "data": data,
                "competencia": data[:7], "papel": papel,
                "emit_nome": emit.findtext(f"{NFE}xNome") if emit is not None else "",
                "descricao": p.findtext(f"{NFE}xProd") or "",
                "ncm": p.findtext(f"{NFE}NCM") or "", "cfop": cfop,
                "valor": _num(p.findtext(f"{NFE}vProd")),
                "tipo": tipo_por_cfop(cfop),
            })
    return out


def rbt12(notas, ate: str | None = None, base: str = "competencia") -> float:
    """Receita bruta dos últimos 12 meses (das notas válidas emitidas)."""
    if not notas:
        return 0.0
    ref = ate or max((data_base(n, base) for n in notas if data_base(n, base)), default="")
    if not ref:
        return 0.0
    ano, mes = int(ref[:4]), int(ref[5:7])
    ini_ano, ini_mes = (ano - 1, mes + 1) if mes < 12 else (ano, 1)
    ini = f"{ini_ano:04d}-{ini_mes:02d}"
    fim = ref[:7]
    total = 0.0
    for n in notas:
        if n.get("cancelada"):
            continue
        c = mes_base(n, base)
        if c and ini <= c <= fim:
            total += n.get("valor", 0.0)
    return round(total, 2)


def sujeito_a_fator_r(item: str) -> str:
    """A LEI submete este item ao Fator R? Devolve o inciso, ou "" se não.

    Só responde depois que os anexos fixos já foram descartados: §5º-C
    (Anexo IV) e §5º-B (Anexo III) vêm primeiro e não admitem Fator R.
    """
    return LC116_FATOR_R.get(item or "", "")


def resolver_anexo(nota, cfg, fator_r):
    """Anexo sugerido de uma nota de serviço.

    A ordem é a da lei, e é ela que decide — não uma opção de tela:
      1) §5º-C fixa o Anexo IV?
      2) §5º-B fixa o Anexo III?
      3) §5º-D / §5º-I submetem ao **Fator R**? Então É Fator R, marcado ou não.
      4) nada disso: aí sim vale o Anexo fixo do cadastro, e por último a
         pergunta — porque enquadrar no escuro seria pior que admitir a dúvida.
    """
    # Lançamento manual: o anexo foi informado por quem lançou — respeita.
    if nota.get("anexo_manual"):
        return nota["anexo_manual"], nota.get("base_manual") or "Lançamento manual"
    item = nota.get("item_lc116") or ""
    fix, base = anexo_por_lc116(item)
    if fix:
        return fix, base

    # A atividade é de Fator R por LEI. Antes disto o sistema só entrava aqui se
    # alguém marcasse a caixa "Fator R" ou digitasse a folha; sem isso devolvia
    # "CONFIGURAR" — ou seja, perguntava ao contador algo que estava escrito na
    # LC 123. Agora quem responde é o item da nota.
    inciso = sujeito_a_fator_r(item)
    marcado = bool(cfg.get("fatorR")) or fator_r is not None
    if inciso or marcado:
        porque = (f"LC116 {item} — Fator R por lei ({inciso})" if inciso
                  else "Fator R marcado no cadastro")
        if fator_r is not None:
            # Com a folha na mão o Fator R VENCE o anexo fixo do cadastro: o
            # anexo fixo é um atalho para enquanto não se sabe o fator, não uma
            # forma de escapar do §5º-J.
            anexo = "III" if fator_r >= 0.28 else "V"
            return anexo, (f"{porque}: {fator_r*100:.1f}% "
                           f"({'≥' if fator_r >= 0.28 else '<'} 28%) → Anexo {anexo}")
        fixo = cfg.get("anexoServico") if inciso else ""
        if fixo:
            # Sem a folha, mas com um anexo escolhido à mão: MANTÉM a escolha e
            # avisa. Passar a "pendente" aqui apagaria um DAS que hoje é
            # calculado — foi o caso da IMUNOCARE, com Anexo V no cadastro e
            # notas de medicina. A escolha do contador não é sobrescrita em
            # silêncio; ela só cede quando existir um número que a contradiga.
            #
            # `if inciso` acima é deliberado: esta ponte só vale quando a
            # sujeição vem da LEI. Quem apenas MARCOU a caixa "Fator R" e não
            # informou a folha continua vendo o que já via — pendente. Sem essa
            # restrição a EMPRESA J (caixa marcada, Anexo III fixo, item 13.05
            # que não é de Fator R) saltava de R$ 0 para R$ 43 mil de DAS no ano
            # como efeito colateral, e ninguém tinha pedido isso.
            return fixo, (f"Anexo {fixo} fixo no cadastro. Atenção: {porque} — "
                          "informe a Folha 12m para confirmar (o fator pode "
                          "levar ao outro anexo).")
        # Nem folha nem anexo fixo: a sujeição já está resolvida pela lei, e o
        # que falta é um número, não uma decisão.
        return "FATOR R", f"{porque}. Informe a Folha 12m para calcular."

    if cfg.get("anexoServico"):
        return cfg["anexoServico"], "Anexo fixo informado no cadastro"
    return "CONFIGURAR", (f"LC116 {item or '—'} não está nas listas do art. 18 "
                          "(§§5º-B, 5º-C, 5º-D e 5º-I): escolha o Anexo fixo "
                          "ou marque Fator R.")


def classificar(pasta_cnpj, cnpj, cfg=None, data_ini=None, data_fim=None,
                base_data: str = "competencia") -> dict:
    cfg = cfg or {}
    base = base_data if base_data in BASES else "competencia"
    todas = ler_nfse(pasta_cnpj, cnpj)
    # Histórico municipal (Recife) — notas anteriores à migração para a NFS-e
    # Nacional; sem elas o RBT12 fica subestimado.
    # ANTI-DUPLICIDADE: no mês da virada as duas fontes têm as mesmas notas (com
    # numeração diferente, então não dá para casar nota a nota). Regra: o
    # municipal só entra nos meses em que a NACIONAL não tem nenhuma nota.
    # Conferido contra PGDAS real: 12/2025 existe nas duas e só pode contar 1x.
    # Falha aqui NÃO pode passar em silêncio: sem o histórico o RBT12 despenca e
    # o DAS sai menor, sem nenhum sinal na tela. Guarda o erro e mostra o aviso.
    erro_hist = None
    try:
        import recife as recifemod
        municipais = recifemod.ler(pasta_cnpj)
        # histórico das demais prefeituras (Paulista etc.) — mesma regra
        try:
            import prefeituras as prefmod
            municipais = (municipais or []) + prefmod.ler(pasta_cnpj)
        except Exception:
            pass
        if municipais:
            meses_nacionais = {mes_base(n, base) for n in todas}
            todas += [n for n in municipais
                      if mes_base(n, base) not in meses_nacionais]
            todas.sort(key=lambda x: x.get("data") or "", reverse=True)
    except Exception as e:
        erro_hist = (f"O histórico municipal não pôde ser lido ({e.__class__.__name__}: {e}). "
                     "O RBT12 pode estar menor que o real e o DAS, subestimado.")

    # Receita sem nota fiscal (aluguel etc.): entra na apuração como as notas.
    manuais_notas = lancamentos_para_notas(cfg.get("lancamentos"))
    if manuais_notas:
        todas += manuais_notas
        todas.sort(key=lambda x: x.get("data") or "", reverse=True)

    itens_nfe = ler_nfe_itens(pasta_cnpj, cnpj)

    # RBT12 e Fator R usam TODO o histórico (não só o período filtrado)
    receita12 = rbt12(todas, base=base)
    folha = _num(cfg.get("folha12m"))
    fator = round(folha / receita12, 4) if (folha and receita12) else None

    def no_periodo(x):
        d = data_base(x, base)
        if data_ini and (not d or d < data_ini):
            return False
        if data_fim and (not d or d > data_fim):
            return False
        return True

    notas = [n for n in todas if no_periodo(n)]
    itens = [i for i in itens_nfe if no_periodo(i)]

    por_anexo = defaultdict(lambda: {"qtd": 0, "valor": 0.0})
    por_item = defaultdict(lambda: {"qtd": 0, "valor": 0.0, "desc": ""})
    alertas = []
    if erro_hist:
        alertas.append(erro_hist)
    for n in notas:
        if n.get("cancelada"):
            n["anexo"], n["base_anexo"] = "—", "Cancelada — fora da receita"
            continue
        n["anexo"], n["base_anexo"] = resolver_anexo(n, cfg, fator)
        por_anexo[n["anexo"]]["qtd"] += 1
        por_anexo[n["anexo"]]["valor"] += n["valor"]
        k = n.get("item_lc116") or "(sem código)"
        por_item[k]["qtd"] += 1
        por_item[k]["valor"] += n["valor"]
        por_item[k]["desc"] = n.get("descricao") or por_item[k]["desc"]

    # A empresa É de Fator R? Quem responde é a lei, pelo item das notas dela.
    # Isto não depende de ninguém marcar nada na tela — e a mensagem diz QUAL
    # atividade obriga, para dar para conferir em vez de acreditar.
    incisos = {}
    for n in notas:
        if n.get("cancelada"):
            continue
        inciso = sujeito_a_fator_r(n.get("item_lc116") or "")
        if inciso:
            incisos.setdefault(n["item_lc116"], inciso)
    sujeita = bool(incisos)
    if sujeita and folha and receita12:
        alertas.append(
            "Esta empresa <b>é de Fator R</b> por " + ", ".join(
                f"<b>{i}</b> ({d})" for i, d in sorted(incisos.items()))
            + f". Com folha de R$ {_brl(folha)} sobre receita de R$ {_brl(receita12)}, "
              f"o fator é <b>{(fator or 0)*100:.1f}%</b> → Anexo "
              f"<b>{'III' if (fator or 0) >= 0.28 else 'V'}</b>.")
    elif sujeita:
        alertas.append(
            "Esta empresa <b>é de Fator R</b> por " + ", ".join(
                f"<b>{i}</b> ({d})" for i, d in sorted(incisos.items()))
            + ". Isso a lei já resolve — o que falta é só a <b>Folha 12 meses</b> "
              "(salários + pró-labore + encargos) para saber se o fator alcança "
              "28% e a empresa cai no Anexo III em vez do V. Sem ela o DAS "
              "destas notas não é calculado.")
    elif any(n.get("anexo") == "FATOR R" for n in notas):
        alertas.append("Há notas dependendo do Fator R — informe a Folha 12m no cadastro.")
    pend = [n for n in notas if n.get("anexo") == "CONFIGURAR"]
    if pend:
        # Diz QUAL item está pendente e O QUE fazer — "configure o Anexo" sozinho
        # não informa nada a quem está na tela.
        itens_pend = sorted({(n.get("item_lc116") or "—") for n in pend})
        desc = next((n.get("descricao") for n in pend if n.get("descricao")), "")
        valor_pend = sum(n.get("valor", 0.0) for n in pend)
        alertas.append(
            f"<b>Item {', '.join(itens_pend)}</b> ({desc[:60]}) — R$ {_brl(valor_pend)} em {len(pend)} "
            "nota(s) sem Anexo. A lei não fixa o Anexo desse serviço: ele sai do <b>Fator R</b>. "
            "Informe a <b>Folha 12 meses</b> (salários + pró-labore + encargos) que o sistema decide "
            "sozinho — Fator R ≥ 28% → Anexo III; abaixo disso → Anexo V. "
            "Se preferir, defina o <b>Anexo fixo de serviço</b>.")
    # Escritório contábil sem a opção de ISS fixo marcada: o DAS sai MAIOR que o
    # real (o ISS não deveria estar dentro). Foi exatamente o caso da Monte.
    sugerir_iss_fixo = (not cfg.get("issFixo")) and any(
        (n.get("item_lc116") or "") == ITEM_CONTABIL and not n.get("cancelada") for n in notas)
    if sugerir_iss_fixo:
        alertas.append(
            "Há serviços de contabilidade (item 17.19). Se o município cobra o ISS em "
            "VALOR FIXO (escritório contábil / sociedade de profissionais — LC 123, art. 18, "
            "§22-A), marque «ISS fixo» no cadastro: sem isso o DAS sai maior que o real, "
            "porque a fatia do ISS fica dentro da guia.")
    op = {n.get("op_simp_nac") for n in notas if n.get("op_simp_nac")}
    regime_xml = {"1": "Não optante", "2": "MEI", "3": "Simples (ME/EPP)"}
    regimes = sorted({regime_xml.get(o, o) for o in op})

    # ── Regime tributário ────────────────────────────────────────────────────
    # Até aqui o sistema calculava DAS para QUALQUER empresa. Numa empresa do
    # Lucro Presumido ou Real isso produz um número que não existe — e que
    # alguém pode acabar pagando. Sem regime marcado, mantém o comportamento
    # antigo (Simples), que é o caso da carteira toda hoje.
    regime = (cfg.get("regime") or "simples").lower()
    if regime not in ("simples", "mei", "presumido", "real"):
        regime = "simples"

    if regime in ("presumido", "real"):
        rotulo = "Lucro Presumido" if regime == "presumido" else "Lucro Real"
        alertas.append(
            f"Empresa marcada como <b>{rotulo}</b>. A prévia do DAS não se aplica e "
            "por isso não é calculada — o Simples Nacional é outro regime. "
            "A apuração federal (PIS, COFINS, IRPJ e CSLL) desta empresa ainda "
            "não é feita pelo Fiscale; as notas e as retenções abaixo continuam "
            "valendo para conferência.")
        validas = [n for n in notas if not n.get("cancelada")]
        return {
            "notas": notas,
            "itens_nfe": itens,
            "das": [],
            "regime": regime,
            "base_data": base,
            "resumo": {
                "quantidade": len(validas),
                "canceladas": len(notas) - len(validas),
                "total": round(sum(n["valor"] for n in validas), 2),
                "rbt12": receita12,
                "folha12m": folha or None,
                "fator_r": fator,
                "regime_no_xml": None,
                "por_anexo": [{"anexo": k, **v, "valor": round(v["valor"], 2)}
                              for k, v in sorted(por_anexo.items(), key=lambda x: -x[1]["valor"])],
                "por_item": [{"item": k, **v, "valor": round(v["valor"], 2)}
                             for k, v in sorted(por_item.items(), key=lambda x: -x[1]["valor"])],
                "alertas": alertas,
            },
        }

    # DAS estimado por competência (mês) presente no período filtrado
    comps = sorted({mes_base(n, base) for n in notas
                    if mes_base(n, base) and not n.get("cancelada")}, reverse=True)
    das = [d for d in (calcular_das(todas, c, cfg, fator, base) for c in comps) if d]

    # Nota emitida num mês com competência de OUTRO mês (substituta emitida no
    # mês seguinte, serviço de dezembro faturado em janeiro) puxa a competência
    # dela inteira para o quadro. O cálculo do mês está certo, mas somar isso no
    # "total do período" faz parecer que o DAS está somando o mês anterior.
    # Marca quem está fora da janela pesquisada para a tela separar do total.
    m_ini = (data_ini or "")[:7]
    m_fim = (data_fim or "")[:7]
    for d in das:
        fora = bool((m_ini and d["competencia"] < m_ini)
                    or (m_fim and d["competencia"] > m_fim))
        d["fora_do_periodo"] = fora
    # avisa quando faltam meses no RBT12 (a API não tem o histórico anterior à
    # migração para a NFS-e Nacional — esses meses devem ser informados à mão)
    for d in das:
        if d.get("rbt12_sem_dados"):
            d["aviso_hist"] = (
                f"RBT12 de {d['competencia']} incompleto: sem receita para "
                f"{', '.join(d['rbt12_sem_dados'])}. Informe esses meses para o valor bater.")

    validas = [n for n in notas if not n.get("cancelada")]
    return {
        "notas": notas,
        "itens_nfe": itens,
        "das": das,
        "regime": regime,
        "base_data": base,
        "resumo": {
            "quantidade": len(validas),
            "canceladas": len(notas) - len(validas),
            "total": round(sum(n["valor"] for n in validas), 2),
            "rbt12": receita12,
            "folha12m": folha or None,
            "fator_r": fator,
            # A lei submete esta empresa ao Fator R? (e por qual inciso)
            "sujeita_fator_r": sujeita,
            "fator_r_incisos": incisos,
            "regime_no_xml": ", ".join(regimes) if regimes else None,
            "por_anexo": [{"anexo": k, **v, "valor": round(v["valor"], 2)}
                          for k, v in sorted(por_anexo.items(), key=lambda x: -x[1]["valor"])],
            "por_item": [{"item": k, **v, "valor": round(v["valor"], 2)}
                         for k, v in sorted(por_item.items(), key=lambda x: -x[1]["valor"])],
            "alertas": alertas,
        },
    }
