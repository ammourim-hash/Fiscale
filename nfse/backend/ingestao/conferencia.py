"""
conferencia.py — o caminho novo lê o mesmo que o atual? Prova, não promessa.

O QUE ESTE MÓDULO FAZ
    Lê os MESMOS documentos pelos dois caminhos — os leitores de hoje e a
    normalização nova — e compara documento a documento e por competência.
    Só isso. Não troca fonte, não calcula imposto, não corrige nada.

POR QUE ELE EXISTE ANTES DA TROCA
    Trocar o leitor da apuração é mexer em dinheiro. A única forma honesta de
    fazer isso é provar antes, sobre o acervo real, que os dois caminhos dão o
    mesmo número — e ter escrito, preto no branco, onde eles NÃO dão.

DIVERGÊNCIA REAL × DIFERENÇA DE REPRESENTAÇÃO
    Não é a mesma coisa, e tratar como se fosse esconde o que importa:

    REPRESENTACAO  `None` de um lado e `0` do outro. O valor é o mesmo — zero —,
                   muda só como cada leitor diz "não houve". Não altera conta.

    REAL           valores diferentes, competências diferentes, situações
                   diferentes. Isso muda apuração, e nenhuma delas é corrigida
                   aqui: são listadas para decisão humana.

    A separação é feita campo a campo, não no atacado: uma operação pode ter
    uma diferença de representação e uma divergência real ao mesmo tempo.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import normalizacao as nz
from .identidade import normalizar

REAL = "REAL"
REPRESENTACAO = "REPRESENTACAO"

# Campos monetários: é neles que `None` × `0` aparece.
_MONETARIOS = ("valor_bruto", "retencoes.pis", "retencoes.cofins",
               "retencoes.csll", "retencoes.irrf", "retencoes.iss")


def _valor(op: nz.Operacao, campo: str):
    if campo.startswith("retencoes."):
        return getattr(op.retencoes, campo.split(".", 1)[1])
    return getattr(op, campo)


def _classificar(campo: str, a, b) -> str:
    """`None` × `0` é representação; qualquer outra diferença é real."""
    if campo in _MONETARIOS:
        za = a is None or a == Decimal(0)
        zb = b is None or b == Decimal(0)
        if za and zb:
            return REPRESENTACAO
    return REAL


@dataclass
class Divergencia:
    id_documento: str
    chave: str
    campo: str
    valor_a: object
    valor_b: object
    tipo: str

    def para_json(self) -> dict:
        return {"id_documento": self.id_documento,
                "chave_final": self.chave[-6:] if self.chave else None,
                "campo": self.campo, "tipo": self.tipo,
                "a": None if self.valor_a is None else str(self.valor_a),
                "b": None if self.valor_b is None else str(self.valor_b)}


@dataclass
class TotaisCompetencia:
    documentos: int = 0
    na_receita: int = 0
    canceladas: int = 0
    substituidas: int = 0
    substitutas: int = 0
    valor_bruto: Decimal = Decimal("0")
    valor_na_receita: Decimal = Decimal("0")
    pis: Decimal = Decimal("0")
    cofins: Decimal = Decimal("0")
    csll: Decimal = Decimal("0")
    irrf: Decimal = Decimal("0")
    iss: Decimal = Decimal("0")
    com_item_servico: int = 0

    def somar(self, op: nz.Operacao) -> None:
        self.documentos += 1
        v = op.valor_bruto or Decimal(0)
        self.valor_bruto += v
        if op.situacao == nz.CANCELADA:
            self.canceladas += 1
        elif op.situacao == nz.SUBSTITUIDA:
            self.substituidas += 1
        elif op.situacao == nz.SUBSTITUTA:
            self.substitutas += 1
        if op.entra_na_receita:
            self.na_receita += 1
            self.valor_na_receita += v
        for campo in ("pis", "cofins", "csll", "irrf", "iss"):
            setattr(self, campo,
                    getattr(self, campo) + (getattr(op.retencoes, campo) or Decimal(0)))
        if op.item_lc116 or op.codigo_servico:
            self.com_item_servico += 1

    def para_json(self) -> dict:
        return {"documentos": self.documentos, "na_receita": self.na_receita,
                "canceladas": self.canceladas, "substituidas": self.substituidas,
                "substitutas": self.substitutas,
                "valor_bruto": str(self.valor_bruto),
                "valor_na_receita": str(self.valor_na_receita),
                "pis": str(self.pis), "cofins": str(self.cofins),
                "csll": str(self.csll), "irrf": str(self.irrf),
                "iss": str(self.iss),
                "com_item_servico": self.com_item_servico}


@dataclass
class RelatorioNFSe:
    identidade: str = ""
    documentos_core: int = 0
    documentos_classificador: int = 0
    comparados: int = 0
    so_no_core: list = field(default_factory=list)
    so_no_classificador: list = field(default_factory=list)
    divergencias: list = field(default_factory=list)
    totais_core: dict = field(default_factory=dict)
    totais_cls: dict = field(default_factory=dict)

    @property
    def reais(self) -> list:
        return [d for d in self.divergencias if d.tipo == REAL]

    @property
    def representacao(self) -> list:
        return [d for d in self.divergencias if d.tipo == REPRESENTACAO]

    def resumo(self) -> dict:
        por_campo = defaultdict(int)
        for d in self.reais:
            por_campo[d.campo] += 1
        por_campo_rep = defaultdict(int)
        for d in self.representacao:
            por_campo_rep[d.campo] += 1
        return {
            "empresa": normalizar(self.identidade).mascarado(),
            "documentos_core": self.documentos_core,
            "documentos_classificador": self.documentos_classificador,
            "comparados": self.comparados,
            "so_no_core": len(self.so_no_core),
            "so_no_classificador": len(self.so_no_classificador),
            "divergencias_reais": len(self.reais),
            "diferencas_de_representacao": len(self.representacao),
            "reais_por_campo": dict(por_campo),
            "representacao_por_campo": dict(por_campo_rep),
        }


def conferir_nfse(dados_dir, identidade, *, ler_core=None,
                  ler_classificador=None,
                  somente_saidas: bool = False) -> RelatorioNFSe:
    """Compara os dois leitores de NFS-e sobre a MESMA pasta. Somente leitura.

    `somente_saidas=True` restringe aos documentos EMITIDOS pela empresa — a
    única comparação maçã-com-maçã de RECEITA que existe aqui. Sem isso o core
    traz também as notas recebidas, que o classificador nem devolve: os totais
    divergiriam por conjunto diferente, não por leitura diferente, e o número
    não provaria nada.

    Os leitores são injetáveis para o teste não depender do backend inteiro;
    por padrão usa os de produção."""
    if ler_core is None or ler_classificador is None:
        import classificador as _cls
        import core as _core
        ler_core = ler_core or (lambda p: _core.carregar_notas(p))
        ler_classificador = ler_classificador or (
            lambda p, c: _cls.ler_nfse(p, c))

    ident = normalizar(identidade)
    alvo = ident.valor if ident.valido else str(identidade)
    pasta = Path(dados_dir) / alvo
    rel = RelatorioNFSe(identidade=alvo)

    ops_core = {}
    for n in ler_core(pasta):
        o = nz.de_nota_core(n, alvo)
        if somente_saidas and o.sentido != nz.SAIDA:
            continue
        if o.id_documento:
            ops_core[o.id_documento] = o
    ops_cls = {}
    for n in ler_classificador(pasta, alvo):
        o = nz.de_nota_classificador(n, alvo)
        if o.id_documento:
            ops_cls[o.id_documento] = o

    rel.documentos_core = len(ops_core)
    rel.documentos_classificador = len(ops_cls)
    # O core devolve TODAS as notas do cache; o classificador só as emitidas
    # pela empresa. A diferença de conjunto é esperada e fica registrada —
    # comparar só a interseção evitaria mostrar isso.
    rel.so_no_core = sorted(set(ops_core) - set(ops_cls))
    rel.so_no_classificador = sorted(set(ops_cls) - set(ops_core))

    for id_doc in sorted(set(ops_core) & set(ops_cls)):
        a, b = ops_core[id_doc], ops_cls[id_doc]
        rel.comparados += 1
        for campo in nz.CAMPOS_COMPARAVEIS + tuple(
                f"retencoes.{c}" for c in ("pis", "cofins", "csll", "irrf", "iss")):
            va, vb = _valor(a, campo), _valor(b, campo)
            if va != vb:
                rel.divergencias.append(
                    Divergencia(id_doc, a.origem.chave, campo, va, vb,
                                _classificar(campo, va, vb)))

    rel.totais_core = totais_por_competencia(ops_core.values())
    rel.totais_cls = totais_por_competencia(ops_cls.values())
    return rel


def totais_por_competencia(operacoes) -> dict:
    """`{'AAAA-MM': TotaisCompetencia}`. Sem competência vai para `'(sem)'`."""
    out: dict[str, TotaisCompetencia] = defaultdict(TotaisCompetencia)
    for op in operacoes:
        chave = op.competencia.strftime("%Y-%m") if op.competencia else "(sem)"
        out[chave].somar(op)
    return dict(out)


def comparar_totais(a: dict, b: dict) -> list:
    """Diferenças de total por competência, campo a campo."""
    difs = []
    for comp in sorted(set(a) | set(b)):
        ta = a.get(comp) or TotaisCompetencia()
        tb = b.get(comp) or TotaisCompetencia()
        for campo in ("documentos", "na_receita", "canceladas", "substituidas",
                      "substitutas", "valor_bruto", "valor_na_receita",
                      "pis", "cofins", "csll", "irrf", "iss"):
            va, vb = getattr(ta, campo), getattr(tb, campo)
            if va != vb:
                difs.append({"competencia": comp, "campo": campo,
                             "a": str(va), "b": str(vb)})
    return difs


# ════════════════════════════════════════════════════════════════════════════
# NF-e — só leitura normalizada do acervo novo. NADA de tributo.
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class RelatorioNFe:
    identidade: str = ""
    documentos: int = 0
    entradas: int = 0
    saidas: int = 0
    sentido_indefinido: int = 0
    mercadorias: int = 0
    itens: int = 0
    com_cfop: int = 0
    com_ncm: int = 0
    com_cst_icms: int = 0
    sem_itens: int = 0
    sem_valor: int = 0
    indeterminados: dict = field(default_factory=lambda: defaultdict(int))
    cfops: dict = field(default_factory=lambda: defaultdict(int))
    # As operações normalizadas, guardadas para quem precisa classificar em vez
    # de só contar. `resumo()` não as expõe: relatório não carrega documento.
    operacoes: list = field(default_factory=list)

    def resumo(self) -> dict:
        return {"empresa": normalizar(self.identidade).mascarado(),
                "documentos": self.documentos, "entradas": self.entradas,
                "saidas": self.saidas,
                "sentido_indefinido": self.sentido_indefinido,
                "mercadorias": self.mercadorias, "itens": self.itens,
                "itens_com_cfop": self.com_cfop, "itens_com_ncm": self.com_ncm,
                "itens_com_cst_icms": self.com_cst_icms,
                "documentos_sem_itens": self.sem_itens,
                "documentos_sem_valor": self.sem_valor,
                "indeterminados": dict(self.indeterminados),
                "cfops_distintos": len(self.cfops)}


def operacoes_nfe(dados_dir, identidade, *, limite: int = 0, ids=None):
    """Percorre o acervo de NF-e e devolve `(Operacao, falha)` uma a uma.

    Existe separado de `conferir_nfe()` porque mais de um leitor precisa das
    MESMAS operações — a conferência conta, `vendas.py` classifica — e duplicar
    a travessia do acervo seria pedir para os dois divergirem em silêncio.

    `falha` vem preenchido quando o documento não pôde ser interpretado; nesse
    caso `Operacao` é `None`. O documento continua no acervo: quem chama decide
    o que fazer com o motivo, mas ninguém o perde sem saber.

    `ids` restringe a travessia a um conjunto de `id_documento` — é o que
    permite ao Contábil pedir SÓ os documentos de uma competência (a lista sai
    do índice, que já a conhece) em vez de abrir e interpretar o acervo
    inteiro. Sem ele, nada muda: quem não passa `ids` continua percorrendo
    tudo. A alternativa seria o chamador refazer esta travessia por fora, e aí
    haveria duas — que um dia divergiriam.
    """
    from . import acervo as acv
    from . import identificacao as idf
    from . import parsers as prs

    ident = normalizar(identidade)
    alvo = ident.valor if ident.valido else str(identidade)
    ac = acv.abrir(dados_dir, alvo)
    itens = [x for x in ac.listar() if x[0] in idf.ESPECIES_NOTA]
    if ids is not None:
        alvos = set(ids)
        itens = [x for x in itens if x[1] in alvos]
    if limite:
        itens = itens[:limite]

    for especie, id_doc in itens:
        caminho = ac.caminho_canonico(especie, id_doc)
        try:
            bruto = caminho.read_bytes()
        except OSError:
            continue
        i = idf.identificar(bruto)
        parser = prs.para(i)
        if parser is None:
            continue
        try:
            doc = parser.interpretar(bruto, i, alvo)
        except Exception:
            yield None, "falha_parser"
            continue
        yield nz.de_documento_nfe(
            doc, caminho=str(caminho.relative_to(Path(dados_dir))),
            hash_conteudo=i.hash_conteudo), ""


def conferir_nfe(dados_dir, identidade, *, limite: int = 0) -> RelatorioNFe:
    """Lê o acervo novo de NF-e e normaliza. **Não calcula tributo nenhum.**"""
    ident = normalizar(identidade)
    alvo = ident.valor if ident.valido else str(identidade)
    rel = RelatorioNFe(identidade=alvo)

    for op, falha in operacoes_nfe(dados_dir, alvo, limite=limite):
        if op is None:
            rel.indeterminados[falha] += 1
            continue
        rel.operacoes.append(op)

        rel.documentos += 1
        rel.entradas += 1 if op.sentido == nz.ENTRADA else 0
        rel.saidas += 1 if op.sentido == nz.SAIDA else 0
        rel.sentido_indefinido += 1 if op.sentido == nz.SENTIDO_INDEFINIDO else 0
        rel.mercadorias += 1 if op.natureza == nz.MERCADORIA else 0
        rel.itens += len(op.itens)
        rel.sem_itens += 1 if not op.itens else 0
        rel.sem_valor += 1 if op.valor_bruto is None else 0
        for campo in op.indeterminados:
            rel.indeterminados[campo] += 1
        for it in op.itens:
            rel.com_cfop += 1 if it.cfop else 0
            rel.com_ncm += 1 if it.ncm else 0
            rel.com_cst_icms += 1 if it.cst_icms else 0
            if it.cfop:
                rel.cfops[it.cfop] += 1
    return rel
