"""
vendas.py — as NF-e de saída formam receita de venda? Quais, e quanto.

O QUE ESTE MÓDULO FAZ
    Olha as NF-e já normalizadas do acervo e separa, pelo CFOP, o que é
    **venda**, **transferência**, **devolução** e **operação que não é
    receita**. Soma por empresa e competência.

O QUE ELE NÃO FAZ
    Não calcula imposto. Não decide Anexo. Não segrega monofásico nem ST. Não
    reduz base por devolução. Não alimenta `calcular_das()`. É leitura
    classificada, e para por aí.

POR QUE UMA TABELA PRÓPRIA, E NÃO A REGRA QUE JÁ EXISTE
    `classificador.tipo_por_cfop()` resolve o que precisa resolver hoje —
    distinguir comércio de indústria — mas **não tem a categoria
    transferência**: ela classifica o grupo `5.1xx` inteiro como venda, e
    `5.151`/`5.152` são transferência entre estabelecimentos da mesma empresa.

    Isso não é defeito dela: transferência nunca precisou existir ali, porque
    a venda por NF-e nunca entrou na apuração. Passa a importar agora, e no
    acervo real **os dois CFOP mais numerosos são justamente `6152` e `5152`**.
    Somar transferência como receita inflaria a base de forma grosseira.

    Então: tabela própria aqui, `tipo_por_cfop()` intocado lá, e
    `comparar_com_regra_atual()` mostra onde as duas discordam. Quem decide
    unificar é o contador, com a divergência na mesa — não este módulo.

NA DÚVIDA, INDETERMINADA
    A tabela lista só CFOP de significado estabelecido. Tudo que não está nela
    sai como `INDETERMINADA`, com o código à vista. Chutar uma categoria para
    "não deixar buraco no relatório" é como se erra caro: o buraco aparece,
    o chute não.

NOTA COM ITENS DE NATUREZAS DIFERENTES
    Existe, e é legítima. Quando os itens não concordam, a nota inteira fica
    `INDETERMINADA` e os CFOP divergentes ficam registrados. Ratear valor por
    natureza exigiria decidir o que fazer com frete, desconto e ICMS-ST — que
    é regra fiscal, e não é desta fase.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from . import normalizacao as nz
from .identidade import normalizar

VENDA = "VENDA"
TRANSFERENCIA = "TRANSFERENCIA"
DEVOLUCAO = "DEVOLUCAO"
NAO_RECEITA = "NAO_RECEITA"
INDETERMINADA = "INDETERMINADA"

# Só o que TEM significado estabelecido na tabela de CFOP. Guardamos os três
# dígitos depois do primeiro — o primeiro só diz se a operação é dentro do
# estado, fora dele ou com o exterior, e isso não muda a natureza.
_VENDA = {
    "101", "102", "103", "104", "105", "106", "109", "110", "111", "112",
    "113", "114", "115", "116", "117", "118", "119", "120", "122", "123",
    "124", "125",
    "401", "402", "403", "404", "405",          # venda com ICMS-ST
    "551", "552",                                # ativo imobilizado / uso e consumo
}
_TRANSFERENCIA = {"151", "152", "153", "155", "156", "408", "409", "557"}
_DEVOLUCAO = {"201", "202", "208", "209", "210", "410", "411", "412", "413",
              "414", "415", "553"}
# Remessas, retornos, consignações, industrialização por conta de terceiro,
# bonificação, brinde, amostra, e o lançamento do ECF (5.929). Nenhuma é
# receita nova de venda — várias têm valor, mas não são faturamento próprio.
_NAO_RECEITA = {
    "901", "902", "903", "904", "905", "906", "907", "908", "909", "910",
    "911", "912", "913", "914", "915", "916", "917", "918", "919", "920",
    "921", "922", "923", "924", "925", "926", "929", "931", "932", "933",
    "934", "949",
}


# Por que a nota não forma receita — na palavra da situação, não na suposição.
MOTIVO_FORA_DA_RECEITA = {
    nz.CANCELADA: "documento cancelado",
    nz.SUBSTITUIDA: "documento substituído",
    nz.DENEGADA: "documento denegado — nunca produziu efeito fiscal",
    nz.NAO_AUTORIZADA: "sem protocolo de autorização conhecido",
}


def classificar_cfop(cfop: str) -> str:
    """CFOP → natureza da operação. `INDETERMINADA` quando não se sabe."""
    c = "".join(ch for ch in str(cfop or "") if ch.isdigit())
    if len(c) != 4:
        return INDETERMINADA
    if c[0] in "123":
        # Entrada: não é saída da empresa, logo não é receita de venda dela.
        return NAO_RECEITA
    if c[0] not in "567":
        return INDETERMINADA
    g = c[1:]
    if g in _VENDA:
        return VENDA
    if g in _TRANSFERENCIA:
        return TRANSFERENCIA
    if g in _DEVOLUCAO:
        return DEVOLUCAO
    if g in _NAO_RECEITA:
        return NAO_RECEITA
    return INDETERMINADA


@dataclass
class OperacaoVenda:
    """Uma NF-e de saída, classificada. Sem imposto, sem anexo."""
    id_documento: str
    competencia: str = ""
    natureza: str = INDETERMINADA
    valor: Decimal | None = None
    cfops: tuple = field(default_factory=tuple)
    itens: int = 0
    caminho: str = ""
    chave: str = ""
    cancelada: bool = False
    motivo: str = ""

    @property
    def e_receita(self) -> bool:
        """Só venda não cancelada forma receita."""
        return self.natureza == VENDA and not self.cancelada

    def para_json(self) -> dict:
        return {"id_documento": self.id_documento,
                "competencia": self.competencia or None,
                "natureza": self.natureza,
                "e_receita": self.e_receita,
                "valor": str(self.valor) if self.valor is not None else None,
                "cfops": list(self.cfops), "itens": self.itens,
                "cancelada": self.cancelada,
                "chave_final": self.chave[-6:] if self.chave else None,
                "caminho": self.caminho or None,
                "motivo": self.motivo or None}


def classificar(op: nz.Operacao, identidade_empresa: str = "") -> OperacaoVenda | None:
    """`Operacao` de NF-e → `OperacaoVenda`. `None` se não for saída da empresa.

    A saída é conferida por DOIS caminhos: o sentido que a normalização leu do
    `tpNF`/papel, e o emitente ser a própria empresa. Exigir os dois evita
    contar como venda uma nota que chegou ao acervo por ser de interesse.
    """
    # NF-e e NFC-e convergem aqui: as duas são venda de mercadoria, e o CFOP
    # classifica igual. A espécie continua registrada na operação.
    if op.especie not in nz.ESPECIES_MERCADORIA:
        return None
    empresa = normalizar(identidade_empresa or op.identidade_empresa)
    emit = normalizar(op.emitente)
    if op.sentido != nz.SAIDA:
        return None
    if empresa.valido and emit.valido and emit.valor != empresa.valor:
        return None

    v = OperacaoVenda(
        id_documento=op.id_documento,
        competencia=op.competencia.strftime("%Y-%m") if op.competencia else "",
        valor=op.valor_bruto,
        itens=len(op.itens),
        caminho=op.origem.caminho,
        chave=op.origem.chave,
        cancelada=not op.entra_na_receita)

    cfops = sorted({i.cfop for i in op.itens if i.cfop})
    v.cfops = tuple(cfops)
    if not cfops:
        v.natureza = INDETERMINADA
        v.motivo = "documento sem CFOP — resumo, não o XML completo"
        return v

    naturezas = {classificar_cfop(c) for c in cfops}
    if len(naturezas) == 1:
        v.natureza = naturezas.pop()
        if v.natureza == INDETERMINADA:
            v.motivo = f"CFOP fora da tabela conhecida: {', '.join(cfops)}"
    else:
        v.natureza = INDETERMINADA
        v.motivo = ("itens de naturezas diferentes na mesma nota: "
                    + ", ".join(f"{c}={classificar_cfop(c)}" for c in cfops))
    if v.cancelada:
        # `cancelada` é o nome do campo desde a APURAÇÃO 6 e significa "fora da
        # receita". Cancelamento é só o caso mais comum: desde a 6C existem
        # também DENEGADA e NAO_AUTORIZADA. Dizer "cancelado" para uma nota sem
        # protocolo mandaria o contador procurar um evento que não existe.
        v.motivo = ((v.motivo + " · " if v.motivo else "")
                    + MOTIVO_FORA_DA_RECEITA.get(op.situacao,
                                                 "documento fora da receita"))
    return v


@dataclass
class ReceitaVendas:
    identidade: str = ""
    operacoes: list = field(default_factory=list)
    por_competencia: dict = field(default_factory=dict)

    def resumo(self) -> dict:
        por_nat: dict = defaultdict(lambda: {"documentos": 0,
                                             "valor": Decimal("0")})
        for o in self.operacoes:
            d = por_nat[o.natureza]
            d["documentos"] += 1
            if o.valor is not None:
                d["valor"] += o.valor
        return {"empresa": normalizar(self.identidade).mascarado(),
                "documentos": len(self.operacoes),
                "receita_venda": str(self.total_venda),
                "por_natureza": {k: {"documentos": v["documentos"],
                                     "valor": str(v["valor"])}
                                 for k, v in sorted(por_nat.items())},
                "competencias": len(self.por_competencia)}

    @property
    def total_venda(self) -> Decimal:
        return sum((o.valor or Decimal("0")) for o in self.operacoes
                   if o.e_receita) or Decimal("0")

    def documentos_de(self, competencia: str) -> list:
        """Os `id_documento` que formaram a receita de venda do mês (D44)."""
        return sorted(o.id_documento for o in self.operacoes
                      if o.e_receita and o.competencia == competencia)


def receita_de_vendas(operacoes, identidade_empresa: str) -> ReceitaVendas:
    """Classifica todas e soma a receita de VENDA por competência.

    Só `VENDA` não cancelada entra no total. Transferência, devolução,
    não-receita e indeterminada ficam separadas e contadas — visíveis, não
    descartadas."""
    r = ReceitaVendas(identidade=normalizar(identidade_empresa).valor
                      or str(identidade_empresa))
    for op in operacoes:
        v = classificar(op, identidade_empresa)
        if v is not None:
            r.operacoes.append(v)

    por_comp: dict = defaultdict(
        lambda: {"venda": Decimal("0"), "documentos_venda": [],
                 "transferencia": Decimal("0"), "devolucao": Decimal("0"),
                 "nao_receita": Decimal("0"), "indeterminada": Decimal("0"),
                 "documentos_indeterminados": [], "cancelada": Decimal("0")})
    for v in r.operacoes:
        alvo = por_comp[v.competencia or "(sem)"]
        valor = v.valor or Decimal("0")
        if v.cancelada:
            alvo["cancelada"] += valor
        elif v.natureza == VENDA:
            alvo["venda"] += valor
            alvo["documentos_venda"].append(v.id_documento)
        elif v.natureza == TRANSFERENCIA:
            alvo["transferencia"] += valor
        elif v.natureza == DEVOLUCAO:
            alvo["devolucao"] += valor
        elif v.natureza == NAO_RECEITA:
            alvo["nao_receita"] += valor
        else:
            alvo["indeterminada"] += valor
            alvo["documentos_indeterminados"].append(v.id_documento)
    r.por_competencia = dict(por_comp)
    return r


def comparar_com_regra_atual(operacoes_venda) -> dict:
    """Onde esta tabela e `classificador.tipo_por_cfop()` discordam.

    Não corrige nenhuma das duas: mostra. A regra atual não tem a categoria
    transferência, então a divergência esperada é justamente ali."""
    from classificador import tipo_por_cfop

    equivalente = {VENDA: {"Comércio", "Indústria"},
                   DEVOLUCAO: {"Devolução/Entrada"},
                   NAO_RECEITA: {"Não é receita", "Devolução/Entrada",
                                 "Serviço"},
                   INDETERMINADA: {"Indefinido"}}
    difs: dict = defaultdict(lambda: {"documentos": 0, "exemplo": ""})
    for v in operacoes_venda:
        for c in v.cfops:
            aqui = classificar_cfop(c)
            la = tipo_por_cfop(c)
            if la in equivalente.get(aqui, set()):
                continue
            chave = f"CFOP {c}: aqui={aqui} · regra atual={la}"
            difs[chave]["documentos"] += 1
            difs[chave]["exemplo"] = v.id_documento
    return dict(difs)


def comparar_com_manuais(receita: ReceitaVendas, lancamentos) -> list:
    """Receita de venda apurada aqui × lançamentos manuais de comércio/indústria.

    Os lançamentos manuais são a forma como a venda entra na apuração HOJE.
    Se o acervo já traz a mesma receita, o lançamento vira redundante — mas
    quem decide desativá-lo é o contador, olhando esta comparação."""
    manuais: dict = defaultdict(Decimal)
    for l in (lancamentos or []):
        tipo = str(l.get("tipo") or "")
        anexo = str(l.get("anexo") or "").upper()
        if tipo not in ("comercio", "industria") and anexo not in ("I", "II"):
            continue
        comp = str(l.get("competencia") or "")[:7]
        try:
            manuais[comp] += Decimal(str(l.get("valor") or 0))
        except Exception:
            continue

    saida = []
    comps = sorted(set(manuais) | set(receita.por_competencia))
    for c in comps:
        do_acervo = receita.por_competencia.get(c, {}).get("venda", Decimal("0"))
        manual = manuais.get(c, Decimal("0"))
        if do_acervo == 0 and manual == 0:
            continue
        saida.append({"competencia": c,
                      "venda_no_acervo": str(do_acervo),
                      "lancamento_manual": str(manual),
                      "diferenca": str(do_acervo - manual),
                      "iguais": do_acervo == manual})
    return saida
