"""
prova_visibilidade.py — os documentos capturados aparecem mesmo na interface?

A PERGUNTA QUE ESTE MÓDULO RESPONDE
    A NF-e 2 mediu **300 documentos** que o motor novo tinha capturado e que a
    tela não mostrava: eles vivem no acervo, e o leitor antigo só varria as
    pastas de XML solto. A NF-e 5 trocou a fonte da tela — e "trocou" precisa
    ser fato verificado, não afirmação.

    Aqui a verificação é feita **pelo mesmo caminho que a tela usa**: a camada
    `consulta`, que é o que a API serve. Se um documento aparece aqui, aparece
    na tela; se não aparece, a troca não terminou.

O QUE ELE NÃO FAZ
    Não conserta nada, não indexa, não migra. Só compara os dois universos e
    devolve o número. Nenhuma chamada de rede, nenhum checkpoint tocado.
"""
from __future__ import annotations

from pathlib import Path

from . import consulta as cq
from . import equivalencia as eqv
from .identidade import normalizar


def invisiveis(dados_dir, identidade) -> dict:
    """Quantos documentos do acervo o LEITOR LEGADO não enxerga — e quantos
    deles a CONSULTA (a fonte da tela hoje) enxerga.

    O número que importa é `ainda_invisiveis`: documentos que nem o legado nem
    a consulta devolvem. Ele tem de ser zero, e é o que a NF-e 5 prometeu.
    """
    ident = normalizar(identidade)
    alvo = ident.valor if ident.valido else str(identidade)

    rel = eqv.comparar_empresa(dados_dir, alvo)
    so_no_acervo = [d.chave for d in rel.diferencas
                    if d.categoria == eqv.DOCUMENTO_APENAS_NO_ACERVO]

    # Cada um deles é procurado pela consulta — o caminho da tela.
    achados, perdidos = [], []
    for chave in so_no_acervo:
        pg = cq.consultar(dados_dir, alvo, cq.Filtro(chave=chave), tamanho=1)
        (achados if pg.total else perdidos).append(chave)

    return {
        "identidade": alvo[:8] + "***",
        "invisiveis_para_o_legado": len(so_no_acervo),
        "visiveis_pela_consulta": len(achados),
        "ainda_invisiveis": len(perdidos),
        "exemplos_ainda_invisiveis": perdidos[:5],
        "resolvido": not perdidos,
    }


def carteira(dados_dir, identidades=None) -> dict:
    raiz = Path(dados_dir)
    if identidades is None:
        identidades = sorted(p.parts[-3] for p in
                             raiz.glob("*/indice/documentos.db"))
    empresas = [invisiveis(raiz, i) for i in identidades]
    return {
        "consolidado": {
            "empresas": len(empresas),
            "invisiveis_para_o_legado":
                sum(e["invisiveis_para_o_legado"] for e in empresas),
            "visiveis_pela_consulta":
                sum(e["visiveis_pela_consulta"] for e in empresas),
            "ainda_invisiveis": sum(e["ainda_invisiveis"] for e in empresas),
            "resolvido": all(e["resolvido"] for e in empresas),
        },
        "empresas": empresas,
    }


if __name__ == "__main__":                                    # pragma: no cover
    import json
    import os
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    import fiscale_dados as fd

    raiz = os.environ.get("FISCALE_DADOS") or str(fd.raiz())
    print(json.dumps(carteira(raiz, sys.argv[1:] or None),
                     ensure_ascii=False, indent=1))
