"""
equivalencia.py — o leitor legado e a consulta indexada dizem a mesma coisa?

POR QUE ISTO EXISTE ANTES DA TROCA
    A tela do módulo NF-e lê hoje o leitor legado (`nfe.carregar_nfe`, que varre
    pastas de XML). A NF-e 2 construiu a consulta indexada (`consulta.py`) para
    substituí-lo. Trocar o leitor sem provar equivalência é mexer no número que
    o contador vê, com base em confiança.

    Este módulo é o mesmo instrumento que a APURAÇÃO 3 usou (`conferencia.py`):
    lê os MESMOS documentos pelos dois caminhos e escreve, preto no branco, onde
    eles divergem.

O QUE ELE NÃO FAZ
    **Não corrige nada.** Não move documento, não reindexa, não migra, não toca
    no acervo nem no checkpoint. Só lê e compara.

    E não força coincidência. Uma diferença explicada é resultado; uma diferença
    escondida é defeito adiado.

A ASSIMETRIA ESPERADA — e por que ela NÃO é erro
    O legado varre `<cnpj>/nfe/` e `<cnpj>/nfe_importadas/`: 23.450 arquivos.
    O índice cobre o acervo da ING 3A em diante: 2.103 documentos. **O legado
    tem mais**, e isso é o esperado — a migração dos XML legados é a NF-e 3,
    que ainda não aconteceu.

    Por isso a métrica que importa aqui **não** é "os totais batem". É:

        Para os documentos que existem NOS DOIS lados, os campos coincidem?

    Se essa resposta for sim, o índice é confiável e o que falta é volume, que a
    NF-e 3 resolve. Se for não, há defeito estrutural — e aí se para.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import consulta as cq
from . import documento as dm
from .identidade import normalizar

# ── categorias de diferença ─────────────────────────────────────────────────
LEGADO_FORA_DO_ACERVO = "LEGADO_FORA_DO_ACERVO"
DOCUMENTO_APENAS_NO_ACERVO = "DOCUMENTO_APENAS_NO_ACERVO"
EVENTO_TRATADO_COMO_DOCUMENTO = "EVENTO_TRATADO_COMO_DOCUMENTO"
RESUMO_VERSUS_COMPLETO = "RESUMO_VERSUS_COMPLETO"
DEDUPLICACAO = "DEDUPLICACAO"
PAPEL_DIFERENTE = "PAPEL_DIFERENTE"
CANCELAMENTO = "CANCELAMENTO"
SUBSTITUICAO = "SUBSTITUICAO"
PARSER_DIFERENTE = "PARSER_DIFERENTE"
DATA_DIFERENTE = "DATA_DIFERENTE"
VALOR_DIFERENTE = "VALOR_DIFERENTE"
DOCUMENTO_CORROMPIDO = "DOCUMENTO_CORROMPIDO"
DESCONHECIDO = "DESCONHECIDO"

CATEGORIAS = (
    LEGADO_FORA_DO_ACERVO, DOCUMENTO_APENAS_NO_ACERVO,
    EVENTO_TRATADO_COMO_DOCUMENTO, RESUMO_VERSUS_COMPLETO, DEDUPLICACAO,
    PAPEL_DIFERENTE, CANCELAMENTO, SUBSTITUICAO, PARSER_DIFERENTE,
    DATA_DIFERENTE, VALOR_DIFERENTE, DOCUMENTO_CORROMPIDO, DESCONHECIDO,
)

# Categorias que NÃO indicam defeito: são consequência conhecida de a NF-e 3
# ainda não ter rodado, ou de os dois lados representarem o mesmo fato de
# formas diferentes.
BENIGNAS = frozenset({LEGADO_FORA_DO_ACERVO, RESUMO_VERSUS_COMPLETO,
                      DEDUPLICACAO})

# Tolerância de centavo. Mesma da casa (D9): R$ 0,02 e só.
TOLERANCIA = Decimal("0.02")


@dataclass(frozen=True)
class Diferenca:
    chave: str
    categoria: str
    detalhe: str = ""
    legado: str = ""
    indice: str = ""

    def para_json(self) -> dict:
        return {"chave": self.chave, "categoria": self.categoria,
                "detalhe": self.detalhe, "legado": self.legado,
                "indice": self.indice}


@dataclass
class RelatorioEmpresa:
    identidade: str
    quantidade_legado: int = 0
    quantidade_indice: int = 0
    em_ambos: int = 0
    apenas_legado: int = 0
    apenas_indice: int = 0
    valor_legado: Decimal = Decimal("0")
    valor_indice: Decimal = Decimal("0")
    valor_em_ambos_legado: Decimal = Decimal("0")
    valor_em_ambos_indice: Decimal = Decimal("0")
    diferencas: list[Diferenca] = field(default_factory=list)
    competencias: dict = field(default_factory=dict)
    erro: str = ""

    # ── métricas ──────────────────────────────────────────────────────────
    @property
    def diferenca_absoluta(self) -> Decimal:
        return abs(self.valor_legado - self.valor_indice)

    @property
    def divergentes(self) -> int:
        """Documentos presentes nos dois lados que NÃO conferem.

        Ausência de um lado não conta aqui: isso é cobertura, não divergência.
        """
        so_ambos = {d.chave for d in self.diferencas
                    if d.categoria not in (LEGADO_FORA_DO_ACERVO,
                                           DOCUMENTO_APENAS_NO_ACERVO)}
        return len(so_ambos)

    @property
    def equivalencia(self) -> float:
        """% dos documentos EM AMBOS os lados que conferem campo a campo.

        Deliberadamente **não** é `em_ambos / total_legado`: essa conta mediria
        a migração pendente (NF-e 3), não a fidelidade do índice. São perguntas
        diferentes e misturá-las esconderia a que importa agora.
        """
        if not self.em_ambos:
            return 100.0 if not self.quantidade_legado else 0.0
        return round(100.0 * (self.em_ambos - self.divergentes) / self.em_ambos, 2)

    @property
    def cobertura(self) -> float:
        """% dos documentos do legado que já estão no índice. Mede a NF-e 3."""
        if not self.quantidade_legado:
            return 100.0
        return round(100.0 * self.em_ambos / self.quantidade_legado, 2)

    @property
    def por_categoria(self) -> dict:
        saida: dict[str, int] = {}
        for d in self.diferencas:
            saida[d.categoria] = saida.get(d.categoria, 0) + 1
        return dict(sorted(saida.items(), key=lambda kv: -kv[1]))

    @property
    def so_benignas(self) -> bool:
        return all(d.categoria in BENIGNAS for d in self.diferencas)

    def resumo(self) -> dict:
        return {
            "identidade": self.identidade[:8] + "***",
            "quantidade_legado": self.quantidade_legado,
            "quantidade_indice": self.quantidade_indice,
            "em_ambos": self.em_ambos,
            "apenas_legado": self.apenas_legado,
            "apenas_indice": self.apenas_indice,
            "valor_legado": str(self.valor_legado),
            "valor_indice": str(self.valor_indice),
            "diferenca_absoluta": str(self.diferenca_absoluta),
            "valor_em_ambos_legado": str(self.valor_em_ambos_legado),
            "valor_em_ambos_indice": str(self.valor_em_ambos_indice),
            "documentos_divergentes": self.divergentes,
            "equivalencia_pct": self.equivalencia,
            "cobertura_pct": self.cobertura,
            "por_categoria": self.por_categoria,
            "so_diferencas_benignas": self.so_benignas,
            "erro": self.erro,
        }


# ════════════════════════════════════════════════════════════════════════════
#  Leitura dos dois lados
# ════════════════════════════════════════════════════════════════════════════
def _do_legado(dados_dir, identidade) -> dict[str, dict]:
    """O leitor de hoje, chamado como a tela o chama.

    O import é local de propósito: este módulo não pode fazer do leitor legado
    uma dependência de importação do pacote de ingestão. Ele é o objeto sob
    comparação, não uma peça da arquitetura nova.
    """
    import nfe as legado

    ident = normalizar(identidade)
    pasta = Path(dados_dir) / ident.valor
    if not pasta.exists():
        return {}
    saida: dict[str, dict] = {}
    for n in legado.carregar_nfe(pasta, ident.valor):
        chave = str(n.get("chave") or "")
        if chave:
            saida[chave] = n
    return saida


def _do_indice(dados_dir, identidade) -> dict[str, cq.Documento]:
    """A consulta nova, sem filtro: tudo que é NOTA (evento fica de fora)."""
    saida: dict[str, cq.Documento] = {}
    pagina, tamanho = 1, cq.TAMANHO_MAXIMO
    while True:
        pg = cq.consultar(dados_dir, identidade, pagina=pagina, tamanho=tamanho,
                          ordenar_por="chave", direcao="asc")
        for d in pg.itens:
            if d.chave:
                saida[d.chave] = d
        if not pg.tem_proxima:
            return saida
        pagina += 1


# ════════════════════════════════════════════════════════════════════════════
#  Comparação
# ════════════════════════════════════════════════════════════════════════════
def _valor(v) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _classificar(chave: str, leg: dict, idx: cq.Documento) -> list[Diferenca]:
    """As duas leituras do MESMO documento. O que difere, e por quê."""
    fora: list[Diferenca] = []

    # ── valor ────────────────────────────────────────────────────────────
    v_leg, v_idx = _valor(leg.get("valor")), _valor(idx.valor_total)
    if abs(v_leg - v_idx) > TOLERANCIA:
        # Resumo não traz total detalhado; a diferença é de representação, não
        # de conta — e precisa ser dita como tal.
        cat = RESUMO_VERSUS_COMPLETO if idx.conteudo == cq.RESUMO else VALOR_DIFERENTE
        fora.append(Diferenca(chave, cat, "valor total",
                              str(v_leg), str(v_idx)))

    # ── data de emissão ──────────────────────────────────────────────────
    d_leg = str(leg.get("data") or "")[:10]
    d_idx = str(idx.dh_emissao or "")[:10]
    if d_leg and d_idx and d_leg != d_idx:
        fora.append(Diferenca(chave, DATA_DIFERENTE, "data de emissão",
                              d_leg, d_idx))

    # ── situação ─────────────────────────────────────────────────────────
    # O legado diz "cancelada: sim/não", derivando dos eventos que ele varre.
    # A comparação certa é contra a SITUAÇÃO ATUAL do índice — que também sai
    # de documento + eventos (NF-e 4A). Comparar contra `situacao` (a do
    # documento) acusaria divergência em toda nota autorizada que foi cancelada
    # depois, e isso não é divergência: são duas perguntas diferentes.
    cancelada_leg = bool(leg.get("cancelada"))
    cancelada_idx = bool(getattr(idx, "cancelada", False)) or         idx.situacao_atual == dm.CANCELADO
    if cancelada_leg != cancelada_idx:
        fora.append(Diferenca(chave, CANCELAMENTO, "cancelamento",
                              "cancelada" if cancelada_leg else "ativa",
                              idx.situacao_atual or idx.situacao or "(vazio)"))

    # ── papel ────────────────────────────────────────────────────────────
    # O legado classifica em compra/venda/frete/outra; o índice em
    # EMITENTE/DESTINATARIO/TERCEIRO. Só se compara o que os dois sabem dizer.
    # O legado classifica em compra/venda/frete/outra; o índice, desde a
    # NF-e 4A, nos cinco papéis oficiais. A comparação é feita contra TODOS os
    # papéis do documento, não só o principal: uma nota em que a empresa é
    # destinatária E transportadora é "compra" para o legado e
    # `DESTINATARIO+TRANSPORTADOR` aqui — e os dois estão certos.
    papel_leg = str(leg.get("papel") or "")
    equivalente = {"compra": cq.DESTINATARIO, "venda": cq.EMITENTE,
                   "frete": cq.TRANSPORTADOR}
    esperado = equivalente.get(papel_leg)
    achados = set(getattr(idx, "papeis", ()) or (idx.papel,))
    if esperado and esperado not in achados:
        fora.append(Diferenca(chave, PAPEL_DIFERENTE, "papel da empresa",
                              papel_leg, "+".join(sorted(achados)) or idx.papel))

    # ── resumo × completo ────────────────────────────────────────────────
    completa_leg = bool(leg.get("completa"))
    completa_idx = idx.conteudo == cq.COMPLETO
    if completa_leg != completa_idx and not any(
            d.categoria == RESUMO_VERSUS_COMPLETO for d in fora):
        fora.append(Diferenca(chave, RESUMO_VERSUS_COMPLETO, "forma do documento",
                              "completa" if completa_leg else "resumo",
                              idx.conteudo))

    return fora


def comparar_empresa(dados_dir, identidade) -> RelatorioEmpresa:
    """Uma empresa, os dois leitores, documento a documento por chave."""
    ident = normalizar(identidade)
    rel = RelatorioEmpresa(identidade=ident.valor if ident.valido else str(identidade))
    if not ident.valido:
        rel.erro = f"identidade inválida: {ident.motivo}"
        return rel

    try:
        legado = _do_legado(dados_dir, ident.valor)
    except Exception as e:                                   # pragma: no cover
        rel.erro = f"leitor legado falhou: {type(e).__name__}: {e}"
        return rel
    try:
        indice = _do_indice(dados_dir, ident.valor)
    except cq.ErroConsulta as e:
        rel.erro = f"consulta indexada falhou: {e}"
        return rel

    rel.quantidade_legado = len(legado)
    rel.quantidade_indice = len(indice)
    rel.valor_legado = sum((_valor(n.get("valor")) for n in legado.values()),
                           Decimal("0"))
    rel.valor_indice = sum((_valor(d.valor_total) for d in indice.values()),
                           Decimal("0"))

    so_legado = sorted(set(legado) - set(indice))
    so_indice = sorted(set(indice) - set(legado))
    ambos = sorted(set(legado) & set(indice))

    rel.apenas_legado = len(so_legado)
    rel.apenas_indice = len(so_indice)
    rel.em_ambos = len(ambos)

    for chave in so_legado:
        rel.diferencas.append(Diferenca(
            chave, LEGADO_FORA_DO_ACERVO,
            "está na pasta legada e ainda não foi migrado para o acervo (NF-e 3)",
            legado=str(legado[chave].get("arquivo") or "")))
    for chave in so_indice:
        rel.diferencas.append(Diferenca(
            chave, DOCUMENTO_APENAS_NO_ACERVO,
            "está no acervo e o leitor legado não o enxerga",
            indice=indice[chave].id_documento))

    for chave in ambos:
        rel.valor_em_ambos_legado += _valor(legado[chave].get("valor"))
        rel.valor_em_ambos_indice += _valor(indice[chave].valor_total)
        rel.diferencas.extend(_classificar(chave, legado[chave], indice[chave]))

    # ── por competência ──────────────────────────────────────────────────
    for chave in ambos:
        comp = (indice[chave].competencia or "")[:7]
        c = rel.competencias.setdefault(
            comp, {"em_ambos": 0, "divergentes": 0,
                   "valor_legado": Decimal("0"), "valor_indice": Decimal("0")})
        c["em_ambos"] += 1
        c["valor_legado"] += _valor(legado[chave].get("valor"))
        c["valor_indice"] += _valor(indice[chave].valor_total)
    divergentes_por_chave = {d.chave for d in rel.diferencas
                             if d.categoria not in (LEGADO_FORA_DO_ACERVO,
                                                    DOCUMENTO_APENAS_NO_ACERVO)}
    for chave in divergentes_por_chave:
        comp = (indice[chave].competencia or "")[:7] if chave in indice else ""
        if comp in rel.competencias:
            rel.competencias[comp]["divergentes"] += 1

    return rel


def comparar_carteira(dados_dir, identidades=None) -> dict:
    """Todas as empresas que têm índice. Visão consolidada + por empresa."""
    raiz = Path(dados_dir)
    if identidades is None:
        identidades = sorted(p.parts[-3] for p in raiz.glob("*/indice/documentos.db"))

    relatorios = [comparar_empresa(raiz, i) for i in identidades]
    validos = [r for r in relatorios if not r.erro]

    total_ambos = sum(r.em_ambos for r in validos)
    total_divergentes = sum(r.divergentes for r in validos)
    consolidado = {
        "empresas": len(relatorios),
        "empresas_com_erro": sum(1 for r in relatorios if r.erro),
        "quantidade_legado": sum(r.quantidade_legado for r in validos),
        "quantidade_indice": sum(r.quantidade_indice for r in validos),
        "em_ambos": total_ambos,
        "apenas_legado": sum(r.apenas_legado for r in validos),
        "apenas_indice": sum(r.apenas_indice for r in validos),
        "documentos_divergentes": total_divergentes,
        "equivalencia_pct": (100.0 if not total_ambos else
                             round(100.0 * (total_ambos - total_divergentes)
                                   / total_ambos, 2)),
        "valor_em_ambos_legado": str(sum((r.valor_em_ambos_legado for r in validos),
                                         Decimal("0"))),
        "valor_em_ambos_indice": str(sum((r.valor_em_ambos_indice for r in validos),
                                         Decimal("0"))),
        "por_categoria": {},
        "so_diferencas_benignas": all(r.so_benignas for r in validos),
    }
    for r in validos:
        for cat, n in r.por_categoria.items():
            consolidado["por_categoria"][cat] = \
                consolidado["por_categoria"].get(cat, 0) + n
    consolidado["por_categoria"] = dict(
        sorted(consolidado["por_categoria"].items(), key=lambda kv: -kv[1]))

    return {"consolidado": consolidado,
            "empresas": [r.resumo() for r in relatorios],
            "relatorios": relatorios}


# ── linha de comando ────────────────────────────────────────────────────────
# Existe para o relatório ser REPRODUZÍVEL por quem vier depois, sem precisar
# montar o `sys.path` na mão. É só leitura: nada aqui escreve no acervo, no
# índice ou no checkpoint.
#
#     python -m ingestao.equivalencia                    (toda a carteira)
#     python -m ingestao.equivalencia 64567004000139     (uma empresa)
if __name__ == "__main__":                                    # pragma: no cover
    import json
    import os
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    import fiscale_dados as fd

    raiz = os.environ.get("FISCALE_DADOS") or str(fd.raiz())
    alvos = sys.argv[1:] or None
    r = comparar_carteira(raiz, alvos)
    print(json.dumps({"consolidado": r["consolidado"], "empresas": r["empresas"]},
                     ensure_ascii=False, indent=1))
