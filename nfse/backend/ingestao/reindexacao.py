"""
reindexacao.py — reler o acervo com o parser novo, e PROVAR que só o que devia
mudar mudou.

O QUE ESTA CAMADA É
    Uma prova, não um motor. Quem relê é `indice.reprocessar()`, que já existe
    desde a ING 3A e faz exatamente isto: abre cada documento do acervo, roda
    os parsers atuais e regrava o índice. Aqui em volta há o que faltava —
    **um instantâneo antes, um depois, e a comparação entre os dois**.

POR QUE A PROVA É O PONTO
    A NF-e 4 acrescenta campos lidos de XML que já estavam no disco: papéis,
    CEST, origem, CST separado de CSOSN, tributos por item. Nada disso deveria
    mexer em valor, chave, emitente, destinatário ou data — mas "não deveria"
    não é verificação.

    Um parser que ganha campos novos e, sem querer, muda o `vNF` de uma nota é
    o tipo de defeito que ninguém percebe até a apuração do mês seguinte. Por
    isso as invariantes são conferidas documento a documento, e qualquer
    mudança nelas **falha a reindexação** em vez de passar batido.

O QUE ELA NUNCA FAZ
    Não vai à rede, não move checkpoint, não toca no acervo. O `original.xml`
    de cada documento é lido e nunca reescrito — quem muda é só a projeção.

IDEMPOTÊNCIA
    Reindexar duas vezes dá o mesmo índice: `reprocessar` regrava por
    `id_documento`, que é estável. A segunda passada não encontra
    desatualizado nenhum e não faz nada.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import indice as idx
from . import parsers as prs
from .identidade import normalizar

# Os campos que o enriquecimento NÃO pode mudar. Mudar qualquer um deles
# significa que o parser passou a ler diferente o que já lia — e isso é
# regressão, não enriquecimento.
INVARIANTES = ("chave", "numero", "serie", "emitente", "contraparte",
               "dh_emissao", "competencia", "valor_total", "situacao",
               "especie")


@dataclass
class Instantaneo:
    """Fotografia do índice de uma empresa, comparável campo a campo."""
    identidade: str
    documentos: dict = field(default_factory=dict)   # id → {campo: valor}
    itens: int = 0
    eventos: int = 0
    erro: str = ""

    @property
    def total(self) -> int:
        return len(self.documentos)

    @property
    def valor(self) -> Decimal:
        soma = Decimal("0")
        for d in self.documentos.values():
            v = d.get("valor_total")
            if v not in (None, ""):
                soma += Decimal(str(v))
        return soma

    def resumo(self) -> dict:
        return {"identidade": self.identidade[:8] + "***",
                "documentos": self.total, "itens": self.itens,
                "eventos": self.eventos, "valor": str(self.valor),
                "erro": self.erro}


def instantaneo(dados_dir, identidade) -> Instantaneo:
    """Lê o índice como está agora. Só leitura, e não cria banco."""
    ident = normalizar(identidade)
    inst = Instantaneo(identidade=ident.valor if ident.valido else str(identidade))
    if not ident.valido:
        inst.erro = f"identidade inválida: {ident.motivo}"
        return inst

    caminho = Path(dados_dir) / ident.valor / idx.PASTA / idx.ARQUIVO
    if not caminho.exists():
        return inst
    try:
        con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error as e:                                # pragma: no cover
        inst.erro = f"índice não abre: {type(e).__name__}"
        return inst
    try:
        colunas = ", ".join(INVARIANTES)
        for r in con.execute(f"SELECT id_documento, {colunas} FROM documentos"):
            inst.documentos[r["id_documento"]] = {c: r[c] for c in INVARIANTES}
        inst.itens = int(con.execute("SELECT COUNT(*) FROM itens").fetchone()[0])
        inst.eventos = int(con.execute("SELECT COUNT(*) FROM eventos").fetchone()[0])
    finally:
        con.close()
    return inst


@dataclass
class Comparacao:
    identidade: str
    antes: Instantaneo
    depois: Instantaneo
    alterados: list = field(default_factory=list)     # (id, campo, antes, depois)
    sumidos: list = field(default_factory=list)
    novos: list = field(default_factory=list)

    @property
    def integro(self) -> bool:
        """Nenhuma invariante mudou e nenhum documento sumiu.

        Documento NOVO é aceitável — o índice pode alcançar algo do acervo que
        ainda não tinha sido indexado. Documento que SOME não é: significa que
        o reprocessamento perdeu uma projeção que existia.
        """
        return not self.alterados and not self.sumidos

    def resumo(self) -> dict:
        return {
            "identidade": self.identidade[:8] + "***",
            "documentos_antes": self.antes.total,
            "documentos_depois": self.depois.total,
            "itens_antes": self.antes.itens,
            "itens_depois": self.depois.itens,
            "eventos_antes": self.antes.eventos,
            "eventos_depois": self.depois.eventos,
            "valor_antes": str(self.antes.valor),
            "valor_depois": str(self.depois.valor),
            "diferenca_de_valor": str(abs(self.antes.valor - self.depois.valor)),
            "invariantes_alteradas": len(self.alterados),
            "documentos_sumidos": len(self.sumidos),
            "documentos_novos": len(self.novos),
            "integro": self.integro,
            "exemplos_alterados": [
                {"id": i[:12], "campo": c, "antes": str(a)[:40],
                 "depois": str(d)[:40]} for i, c, a, d in self.alterados[:10]],
        }


def comparar(antes: Instantaneo, depois: Instantaneo) -> Comparacao:
    c = Comparacao(identidade=antes.identidade, antes=antes, depois=depois)
    c.sumidos = sorted(set(antes.documentos) - set(depois.documentos))
    c.novos = sorted(set(depois.documentos) - set(antes.documentos))
    for id_doc in sorted(set(antes.documentos) & set(depois.documentos)):
        a, d = antes.documentos[id_doc], depois.documentos[id_doc]
        for campo in INVARIANTES:
            if a.get(campo) != d.get(campo):
                c.alterados.append((id_doc, campo, a.get(campo), d.get(campo)))
    return c


def reindexar_empresa(dados_dir, identidade, *, ensaio: bool = False,
                      tudo: bool = False) -> dict:
    """Instantâneo → reprocessa → instantâneo → compara.

    `ensaio=True` só fotografa e diz quantos documentos estão desatualizados,
    sem reprocessar nada.

    `tudo=True` relê o acervo inteiro em vez de só o que a versão do parser
    marcou como desatualizado. Serve para conferência: o resultado tem de ser
    o mesmo, e diferente significa que a marca de versão está mentindo.
    """
    ident = normalizar(identidade)
    if not ident.valido:
        return {"identidade": str(identidade), "erro": f"identidade inválida: {ident.motivo}"}

    antes = instantaneo(dados_dir, ident.valor)
    pendentes = idx.abrir(dados_dir, ident.valor).desatualizados()

    if ensaio:
        return {"identidade": ident.valor[:8] + "***", "ensaio": True,
                "documentos": antes.total, "desatualizados": len(pendentes),
                "versoes_parser": prs.versoes()}

    rel = idx.reprocessar(dados_dir, ident.valor, somente_desatualizados=not tudo)
    depois = instantaneo(dados_dir, ident.valor)
    c = comparar(antes, depois)

    saida = c.resumo()
    saida.update({"ensaio": False, "desatualizados_antes": len(pendentes),
                  "reprocessados": rel.total, "indexados": rel.indexados,
                  "versoes_parser": prs.versoes()})
    return saida


def reindexar_carteira(dados_dir, identidades=None, *, ensaio: bool = False,
                       tudo: bool = False) -> dict:
    raiz = Path(dados_dir)
    if identidades is None:
        identidades = sorted(p.parts[-3] for p in
                             raiz.glob(f"*/{idx.PASTA}/{idx.ARQUIVO}"))
    empresas = [reindexar_empresa(raiz, i, ensaio=ensaio, tudo=tudo)
                for i in identidades]
    consolidado = {
        "ensaio": ensaio,
        "empresas": len(empresas),
        "documentos": sum(e.get("documentos_depois", e.get("documentos", 0))
                          for e in empresas),
        "reprocessados": sum(e.get("reprocessados", 0) for e in empresas),
        "invariantes_alteradas": sum(e.get("invariantes_alteradas", 0)
                                     for e in empresas),
        "documentos_sumidos": sum(e.get("documentos_sumidos", 0) for e in empresas),
        "integro": all(e.get("integro", True) for e in empresas),
    }
    return {"consolidado": consolidado, "empresas": empresas}


# ── linha de comando ────────────────────────────────────────────────────────
#     python -m ingestao.reindexacao --ensaio
#     python -m ingestao.reindexacao [cnpj...]
if __name__ == "__main__":                                    # pragma: no cover
    import json
    import os
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    import fiscale_dados as fd

    raiz = os.environ.get("FISCALE_DADOS") or str(fd.raiz())
    modos = {a for a in sys.argv[1:] if a.startswith("--")}
    alvos = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    print(json.dumps(reindexar_carteira(raiz, alvos,
                                        ensaio="--ensaio" in modos,
                                        tudo="--tudo" in modos),
                     ensure_ascii=False, indent=1))
