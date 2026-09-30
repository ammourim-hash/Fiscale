# -*- coding: utf-8 -*-
"""pdf.py — extrato em PDF: a arquitetura está aqui, o leitor de layout não.

O QUE ESTE ARQUIVO FAZ HOJE
    Abre o PDF, diz se ele tem TEXTO (não precisa de OCR) ou se é imagem, e
    recusa a importação dizendo exatamente isso. Não inventa movimento.

POR QUE NÃO HÁ LEITOR AINDA
    Extrato em PDF não tem padrão: cada banco desenha a tabela de um jeito, e
    a regra deste projeto é que leitor de layout NASCE DE AMOSTRA REAL — foi
    ela que evitou, no SITFIS, um leitor que confundia dois campos. Nenhuma
    amostra de extrato em PDF foi entregue até esta fase.

COMO UM LEITOR NOVO ENTRA
    Um módulo com duas funções, registrado em `LAYOUTS`:

        reconhece(paginas: list[str]) -> bool
        ler(paginas: list[str]) -> Leitura   # com `pagina` e `linha` em
                                             # cada MovLido, e as linhas que
                                             # não são movimento em `ignoradas`

    `paginas` é o texto extraído por `pypdf`, uma string por página. OCR não
    entra por padrão: PDF com texto é lido como texto.
"""
from __future__ import annotations

import io

from . import Leitura, SemLeitor

LAYOUTS: list = []


def texto_das_paginas(bruto: bytes) -> list:
    try:
        import pypdf
    except Exception:
        raise SemLeitor("não há leitor de PDF instalado nesta máquina")
    try:
        doc = pypdf.PdfReader(io.BytesIO(bruto))
        return [(p.extract_text() or "") for p in doc.pages]
    except Exception as e:
        raise SemLeitor("não consegui abrir o PDF (%s)" % type(e).__name__)


def diagnosticar(bruto: bytes) -> dict:
    paginas = texto_das_paginas(bruto)
    linhas = sum(len([l for l in p.splitlines() if l.strip()]) for p in paginas)
    return {"paginas": len(paginas), "linhas_de_texto": linhas,
            "tem_texto": linhas > 0}


def ler(bruto: bytes) -> Leitura:
    paginas = texto_das_paginas(bruto)
    for layout in LAYOUTS:
        if layout.reconhece(paginas):
            return layout.ler(paginas)
    d = diagnosticar(bruto)
    if d["tem_texto"]:
        raise SemLeitor(
            "PDF de extrato com texto (%d página(s), %d linhas) — não precisa "
            "de OCR, mas ainda não há leitor para o layout deste banco. O "
            "leitor nasce de uma amostra real; até lá, exporte o extrato em "
            "OFX ou CSV no internet banking." % (d["paginas"],
                                                 d["linhas_de_texto"]))
    raise SemLeitor(
        "PDF sem texto (%d página(s)): é imagem digitalizada e exigiria OCR, "
        "que não é usado por padrão. Exporte o extrato em OFX ou CSV."
        % d["paginas"])
