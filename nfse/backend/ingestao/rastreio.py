"""
rastreio.py — quais documentos formaram cada valor apurado.

A PERGUNTA QUE ESTE MÓDULO RESPONDE
    "DAS de R$ 8.972,55 — de onde saiu isso?" Hoje o motor devolve o número e
    as linhas por Anexo, mas não diz QUAIS notas entraram em cada linha. Sem
    isso, conferir contra o PGDAS-D é abrir o XML na mão, e foi assim que dois
    erros reais passaram meses sem ser vistos.

    É o cumprimento da **D44**: nenhum valor apurado sem caminho de volta ao
    documento que o originou.

COMO ELE FAZ, E O QUE ELE NÃO FAZ
    **Não recalcula nada.** Chama o mesmo `calcular_das()` e a mesma
    `apuracao_federal` de sempre, e depois LÊ as marcas que o motor deixou.

    O `calcular_das` grava em cada nota o `anexo` e a `base_anexo` que decidiu.
    O rastreio agrupa por essas marcas — não por um critério próprio. Se ele
    inventasse a própria regra de agrupamento, existiriam duas verdades: a do
    motor e a do relatório, e um dia elas divergiriam sem ninguém notar.

    Para o federal, o filtro de quais notas entram vem de
    `apuracao_federal._emitidas_do_mes` — a função do próprio motor. Mesma
    razão.

O QUE É PROVA, E O QUE É INDÍCIO
    A composição **fecha**: a soma dos documentos rastreados tem de bater com
    a base que o motor usou, ao centavo. Quando não bate, `conferir()` diz —
    em vez de o relatório mostrar uma lista bonita que não soma.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Tolerância de fechamento: um centavo. Os valores vêm em `float` do motor
# atual, e somar float em ordem diferente muda a última casa. Um centavo é o
# limite do que o dinheiro enxerga; acima disso é divergência de verdade.
TOLERANCIA = 0.01


@dataclass
class Composicao:
    """Um valor apurado e os documentos que o formaram."""
    rotulo: str
    valor: float = 0.0                 # o que o MOTOR apurou
    documentos: list = field(default_factory=list)   # id_documento
    soma_documentos: float = 0.0       # a soma dos documentos rastreados
    detalhe: list = field(default_factory=list)      # por documento
    observacao: str = ""

    @property
    def quantidade(self) -> int:
        return len(self.documentos)

    @property
    def fecha(self) -> bool:
        """A soma dos documentos bate com o valor do motor?"""
        return abs(self.soma_documentos - self.valor) <= TOLERANCIA

    @property
    def diferenca(self) -> float:
        return round(self.soma_documentos - self.valor, 2)

    def conferir(self) -> dict:
        return {"rotulo": self.rotulo, "valor": round(self.valor, 2),
                "documentos": self.quantidade,
                "soma_documentos": round(self.soma_documentos, 2),
                "fecha": self.fecha, "diferenca": self.diferenca,
                "observacao": self.observacao or None}

    def para_json(self, limite: int = 0) -> dict:
        ids = self.documentos if not limite else self.documentos[:limite]
        return {**self.conferir(), "id_documento": ids}


def _somar(notas, campo: str = "valor") -> float:
    return round(sum(float(n.get(campo) or 0.0) for n in notas), 2)


def _comp(rotulo, valor, notas, campo="valor", observacao="") -> Composicao:
    """Monta a composição a partir das notas que o MOTOR usou."""
    return Composicao(
        rotulo=rotulo, valor=float(valor or 0.0),
        documentos=[n.get("id_documento", "") for n in notas],
        soma_documentos=_somar(notas, campo),
        detalhe=[{"id_documento": n.get("id_documento", ""),
                  "chave_final": (n.get("chave") or "")[-6:] or None,
                  "arquivo": n.get("arquivo") or None,
                  "valor": round(float(n.get(campo) or 0.0), 2)} for n in notas],
        observacao=observacao)


# ════════════════════════════════════════════════════════════════════════════
# Simples Nacional — DAS
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class RastreioDAS:
    competencia: str = ""
    das_estimado: float = 0.0
    rbt12: float = 0.0
    receita: Composicao | None = None
    por_anexo: dict = field(default_factory=dict)   # rótulo -> Composicao
    excluidos: Composicao | None = None
    iss_retido: Composicao | None = None

    def conferir(self) -> dict:
        linhas = [c.conferir() for c in self.por_anexo.values()]
        return {"competencia": self.competencia,
                "das_estimado": round(self.das_estimado, 2),
                "rbt12": round(self.rbt12, 2),
                "receita": self.receita.conferir() if self.receita else None,
                "linhas": linhas,
                "excluidos": self.excluidos.conferir() if self.excluidos else None,
                "iss_retido": self.iss_retido.conferir() if self.iss_retido else None,
                "tudo_fecha": self.tudo_fecha}

    @property
    def tudo_fecha(self) -> bool:
        alvos = [self.receita, self.excluidos, self.iss_retido]
        alvos += list(self.por_anexo.values())
        return all(c.fecha for c in alvos if c is not None)


def rastrear_das(notas, competencia: str, cfg, fator_r) -> RastreioDAS | None:
    """Chama `calcular_das()` DE VERDADE e monta a composição do resultado.

    As notas vêm da ponte (`ponte_motor.para_classificador`), então cada uma
    já carrega `id_documento`. O motor as marca com `anexo`; agrupamos por essa
    marca — a dele, não a nossa.
    """
    from classificador import calcular_das

    do_mes = [n for n in notas if (n.get("competencia") or "")[:7] == competencia]
    if not do_mes:
        return None
    # ATENÇÃO: `calcular_das` MUTA as notas do mês (grava `anexo`,
    # `base_anexo`, e pode marcar `iss_retido` quando a empresa tem ISS fixo).
    # É justamente dessa mutação que sai o agrupamento correto.
    resultado = calcular_das(notas, competencia, cfg, fator_r)
    if resultado is None:
        return None

    validas = [n for n in do_mes if not n.get("cancelada")]
    excluidas = [n for n in do_mes if n.get("cancelada")]

    r = RastreioDAS(competencia=competencia,
                    das_estimado=resultado.get("das_estimado") or 0.0,
                    rbt12=resultado.get("rbt12") or 0.0)
    r.receita = _comp("receita da competência", resultado.get("receita_mes"),
                      validas)
    # Os excluídos NÃO são uma base a fechar contra o motor: são o que ficou de
    # FORA. O valor deles é informação — "R$ X saíram da receita, nestes N
    # documentos" —, então o total é a própria soma. Compará-lo com zero, como
    # a primeira versão fazia, acusava divergência em toda competência que
    # tivesse uma nota cancelada: o número não fechava porque a pergunta estava
    # errada, não porque o rastreio falhasse.
    r.excluidos = _comp("excluídos por cancelamento/substituição",
                        _somar(excluidas), excluidas,
                        observacao="valor FORA da receita, rastreável documento a documento")
    # `iss_retido_no_mes` soma `valor_iss` das notas com ISS retido — a mesma
    # conta do motor, sobre as mesmas notas.
    com_iss = [n for n in validas if n.get("iss_retido")]
    r.iss_retido = _comp("ISS retido no mês", resultado.get("iss_retido_no_mes"),
                         com_iss, campo="valor_iss")

    for linha in resultado.get("linhas") or []:
        anexo = linha.get("anexo")
        retido = bool(linha.get("iss_retido"))
        motivo = linha.get("motivo") or ""
        # O mesmo balde que o motor usou: (anexo, iss_retido, motivo).
        do_balde = [n for n in validas
                    if n.get("anexo") == anexo
                    and bool(n.get("iss_retido")) == retido
                    and (n.get("motivo_sem_iss") or
                         ("ISS retido na fonte" if n.get("iss_retido") else "")) == motivo]
        rot = f"Anexo {anexo}" + (f" · {motivo}" if motivo else "")
        c = _comp(rot, linha.get("receita"), do_balde)
        c.observacao = (f"DAS da linha: {linha.get('das')}"
                        if linha.get("das") is not None else
                        (linha.get("obs") or ""))
        r.por_anexo[rot] = c
    return r


# ════════════════════════════════════════════════════════════════════════════
# PIS, COFINS — mensal
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class RastreioFederal:
    competencia: str = ""
    regime: str = ""
    receita: Composicao | None = None
    pis_devido: float = 0.0
    cofins_devido: float = 0.0
    retencoes: dict = field(default_factory=dict)   # tributo -> Composicao

    def conferir(self) -> dict:
        return {"competencia": self.competencia, "regime": self.regime,
                "pis_devido": round(self.pis_devido, 2),
                "cofins_devido": round(self.cofins_devido, 2),
                "receita": self.receita.conferir() if self.receita else None,
                "retencoes": {k: v.conferir() for k, v in self.retencoes.items()},
                "tudo_fecha": self.tudo_fecha}

    @property
    def tudo_fecha(self) -> bool:
        alvos = [self.receita] + list(self.retencoes.values())
        return all(c.fecha for c in alvos if c is not None)


_RETENCOES = (("PIS", "valor_pis", "retido_pis"),
              ("COFINS", "valor_cofins", "retido_cofins"),
              ("CSLL", "valor_csll", "retido_csll"),
              ("IRRF", "valor_irrf", "retido_irrf"))


def rastrear_federal(notas, cnpj: str, competencia: str,
                     regime: str, **kw) -> RastreioFederal | None:
    """Chama `apuracao_federal.apurar()` e compõe a base e as retenções.

    Quem decide o conjunto é `_emitidas_do_mes` — do próprio motor."""
    import apuracao_federal as af

    resultado = af.apurar(notas, cnpj, competencia, regime, **kw)
    if not resultado.get("aplicavel"):
        return None
    usadas = af._emitidas_do_mes(notas, cnpj, competencia)

    r = RastreioFederal(competencia=competencia, regime=regime,
                        pis_devido=resultado.get("pis_devido") or 0.0,
                        cofins_devido=resultado.get("cofins_devido") or 0.0)
    r.receita = _comp("receita usada na base", resultado.get("receita_bruta"),
                      usadas, campo="valor_servico")
    for rotulo, campo, chave in _RETENCOES:
        com = [n for n in usadas if float(n.get(campo) or 0.0) > 0]
        r.retencoes[rotulo] = _comp(f"{rotulo} retido",
                                    resultado.get(chave), com, campo=campo)
    return r


# ════════════════════════════════════════════════════════════════════════════
# IRPJ e CSLL — trimestral
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class RastreioTrimestre:
    periodo: str = ""
    irpj_devido: float = 0.0
    csll_devido: float = 0.0
    receita: Composicao | None = None
    por_mes: dict = field(default_factory=dict)
    retencoes: dict = field(default_factory=dict)

    def conferir(self) -> dict:
        return {"periodo": self.periodo,
                "irpj_devido": round(self.irpj_devido, 2),
                "csll_devido": round(self.csll_devido, 2),
                "receita": self.receita.conferir() if self.receita else None,
                "por_mes": {k: v.conferir() for k, v in self.por_mes.items()},
                "retencoes": {k: v.conferir() for k, v in self.retencoes.items()},
                "tudo_fecha": self.tudo_fecha}

    @property
    def tudo_fecha(self) -> bool:
        alvos = [self.receita] + list(self.por_mes.values()) \
            + list(self.retencoes.values())
        return all(c.fecha for c in alvos if c is not None)


def rastrear_trimestre(notas, cnpj: str, ano: int, tri: int,
                       **kw) -> RastreioTrimestre | None:
    """Chama `apurar_trimestre()` e compõe a receita do trimestre, mês a mês."""
    import apuracao_federal as af

    resultado = af.apurar_trimestre(notas, cnpj, ano, tri, **kw)
    if not resultado.get("aplicavel"):
        return None
    meses = af.meses_do_trimestre(ano, tri)
    usadas = [n for m in meses for n in af._emitidas_do_mes(notas, cnpj, m)]

    r = RastreioTrimestre(periodo=resultado.get("periodo") or f"{ano}-T{tri}",
                          irpj_devido=resultado.get("irpj_devido") or 0.0,
                          csll_devido=resultado.get("csll_devido") or 0.0)
    r.receita = _comp("receita do trimestre", resultado.get("receita_bruta"),
                      usadas, campo="valor_servico")
    for m in meses:
        do_mes = af._emitidas_do_mes(notas, cnpj, m)
        r.por_mes[m] = _comp(f"receita de {m}",
                             (resultado.get("receita_por_mes") or {}).get(m),
                             do_mes, campo="valor_servico")
    for rotulo, campo, chave in (("IRRF", "valor_irrf", "retido_irrf"),
                                 ("CSLL", "valor_csll", "retido_csll")):
        com = [n for n in usadas if float(n.get(campo) or 0.0) > 0]
        r.retencoes[rotulo] = _comp(f"{rotulo} retido no trimestre",
                                    resultado.get(chave), com, campo=campo)
    return r


# ════════════════════════════════════════════════════════════════════════════
def caminho_do_documento(operacoes, id_documento: str) -> dict | None:
    """Do `id_documento` de volta ao XML de origem.

    É o último elo: o relatório mostra o identificador, e daqui se chega ao
    arquivo em disco — sem precisar procurar."""
    for op in operacoes:
        if op.id_documento == id_documento:
            return {"id_documento": op.id_documento,
                    "especie": op.especie,
                    "arquivo": op.origem.arquivo or None,
                    "caminho": op.origem.caminho or None,
                    "fonte_leitor": op.origem.fonte_leitor,
                    "chave_final": (op.origem.chave or "")[-6:] or None,
                    "situacao": op.situacao,
                    "entra_na_receita": op.entra_na_receita}
    return None
