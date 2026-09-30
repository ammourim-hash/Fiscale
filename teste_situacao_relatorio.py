#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O dossiê: compila os originais, e não carrega marca nenhuma.

    python teste_situacao_relatorio.py

O QUE SE PROTEGE
    • que o PDF gerado NÃO tenha o nome do sistema em lugar nenhum — nem nos
      metadados, que é por onde ele escaparia sem ninguém ver;
    • que o motor ANEXE o original e nunca o redesenhe;
    • que documento com hash divergente NÃO seja anexado;
    • que um PDF ruim não derrube o dossiê inteiro.

NÃO TOCA REDE NEM A PASTA REAL.
"""
from __future__ import annotations

import datetime as dt
import io
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio as apoio                       # noqa: E402

from situacao import importacao as portao         # noqa: E402
from situacao import indice as idx                # noqa: E402
from situacao import modelo as mod                # noqa: E402
from situacao import relatorio as rel             # noqa: E402

_ok = _falhas = 0
_erros: list = []

HOJE = dt.date(2026, 9, 11)
ALFA = "11222333000181"
BETA = "11444777000161"

# AS MARCAS QUE NAO PODEM APARECER. Minusculas, porque a busca e insensivel.
MARCAS = (b"fiscale", b"pypdf", b"fpdf", b"pyfpdf")


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        _erros.append(desc)
        print("  FALHOU  " + desc)


def igual(a, b, desc):
    if a == b:
        ok(True, desc)
    else:
        ok(False, "%s  (obtive %.110r, esperava %.110r)" % (desc, a, b))


def pdf_de(texto: str, paginas: int = 1) -> bytes:
    """Um PDF minusculo e legitimo, com texto e o numero de paginas pedido."""
    from fpdf import FPDF
    p = FPDF()
    for i in range(paginas):
        p.add_page()
        p.set_font("Helvetica", size=12)
        p.cell(0, 10, "%s (pagina %d)" % (texto, i + 1))
    return bytes(p.output())


def paginas_de(bruto: bytes) -> int:
    import pypdf
    return len(pypdf.PdfReader(io.BytesIO(bruto)).pages)


def texto_de(bruto: bytes) -> str:
    import pypdf
    r = pypdf.PdfReader(io.BytesIO(bruto))
    return "\n".join((pg.extract_text() or "") for pg in r.pages)


def main():
    base = apoio.pasta_temp("fiscale_relat_")
    ix = idx.abrir(base)
    try:
        # ═══════════════════════════════════════════════════════════════
        secao("Preparando: dois documentos reais no envelope")
        a = portao.importar(base, ix, ALFA, mod.FEDERAL, mod.CERTIDAO,
                            pdf_de("CERTIDAO NEGATIVA FEDERAL", 2),
                            origem="ASSISTIDA",
                            leitura={"validade": "2027-02-10",
                                     "natureza": mod.NEGATIVA})
        b = portao.importar(base, ix, ALFA, mod.FEDERAL, mod.EXTRATO,
                            pdf_de("RELATORIO DE SITUACAO FISCAL", 1),
                            origem="SITFIS",
                            leitura={"apurado_em": "2026-09-09"})
        igual((a["desfecho"], b["desfecho"]), ("NOVO", "NOVO"),
              "os dois entraram pelo portão")

        # ═══════════════════════════════════════════════════════════════
        secao("O dossiê COMPILA os originais")
        d = rel.dossie(base, ix, ALFA, "ALFA COMERCIO DE MATERIAIS LTDA", HOJE)
        igual(d["anexados"], 2, "anexou os 2 documentos")
        igual(paginas_de(d["pdf"]), 1 + 2 + 1,
              "capa + 2 páginas do primeiro + 1 do segundo")
        texto = texto_de(d["pdf"])
        ok("CERTIDAO NEGATIVA FEDERAL" in texto,
           "o conteúdo do ORIGINAL está lá, inteiro")
        ok("RELATORIO DE SITUACAO FISCAL" in texto,
           "e o do segundo também")
        ok("pagina 2" in texto,
           "inclusive a segunda página do documento de 2 páginas")

        # ═══════════════════════════════════════════════════════════════
        secao("A REGRA: nenhuma marca do sistema, em lugar nenhum")
        # ISTO E O QUE TORNA A REGRA EXECUTAVEL. Varre os BYTES INTEIROS --
        # texto, metadados, streams, tudo. O `pypdf` grava `/Producer: pypdf`
        # sozinho ao escrever, e sem esta varredura a marca sairia no PDF
        # sem ninguem ver.
        bruto = d["pdf"].lower()
        for marca in MARCAS:
            ok(marca not in bruto,
               "a palavra %r não aparece em nenhum byte do PDF" % marca.decode())

        import pypdf
        meta = pypdf.PdfReader(io.BytesIO(d["pdf"])).metadata or {}
        print("     metadados: %s" % dict(meta))
        for chave in ("/Producer", "/Creator", "/Author"):
            ok(not (meta.get(chave) or "").strip(),
               "%s está vazio" % chave)
        igual(meta.get("/Title"), rel.TITULO,
              "e o título é genérico: %r" % rel.TITULO)

        ok("Relatório de Situação Fiscal" in texto,
           "a capa usa o título neutro")
        ok("ALFA COMERCIO DE MATERIAIS LTDA" in texto,
           "e traz o nome do CLIENTE, que é o que interessa")
        ok("11.222.333/0001-81" in texto, "com o documento formatado")

        # ═══════════════════════════════════════════════════════════════
        secao("A procedência viaja junto — é cadeia de custódia, não marca")
        ok("SHA-256" in texto, "o hash de cada anexo aparece no índice")
        doc = [l for l in d["linhas"] if l["documento"].get("id")][0]["documento"]
        ok(doc["sha256"] in texto, "e é o hash de verdade do documento")
        ok("sitfis" in texto.lower(), "a origem de cada um também")

        # ═══════════════════════════════════════════════════════════════
        secao("Documento adulterado NÃO é anexado")
        alvo = Path(a["caminho"])
        guardado = alvo.read_bytes()
        alvo.write_bytes(guardado + b"%mexido\n")
        d2 = rel.dossie(base, ix, ALFA, "ALFA", HOJE)
        igual(d2["anexados"], 1, "só o íntegro entrou")
        ok(rel.OMITIDO_HASH in d2["omitidos"],
           "e o motivo foi o hash divergente")
        ok("não anexado" in texto_de(d2["pdf"]),
           "a capa DIZ que faltou um — capa que promete anexo que não veio "
           "é pior que capa sem ele")
        alvo.write_bytes(guardado)          # desfaz

        # ═══════════════════════════════════════════════════════════════
        secao("PDF ilegível não derruba o dossiê")
        # Um arquivo com cabecalho de PDF mas conteudo quebrado: passa pelo
        # portao (que so olha os 5 primeiros bytes) e morre no leitor.
        portao.importar(base, ix, BETA, mod.MUNICIPAL, mod.CERTIDAO,
                        b"%PDF-1.4\nisto nao e um PDF de verdade\n")
        portao.importar(base, ix, BETA, mod.FEDERAL, mod.CERTIDAO,
                        pdf_de("CERTIDAO MUNICIPAL BETA"),
                        leitura={"validade": "2027-01-05",
                                 "natureza": mod.NEGATIVA})
        d3 = rel.dossie(base, ix, BETA, "BETA SERVICOS", HOJE)
        igual(d3["anexados"], 1, "o bom foi anexado")
        ok(rel.OMITIDO_ILEGIVEL in d3["omitidos"],
           "e o ruim virou omissão, não exceção")
        ok(paginas_de(d3["pdf"]) >= 2, "o dossiê saiu inteiro mesmo assim")

        # ═══════════════════════════════════════════════════════════════
        secao("Empresa sem documento nenhum ainda rende um dossiê")
        d4 = rel.dossie(base, ix, "66789006000106", "GAMA TRANSPORTES", HOJE)
        igual(d4["anexados"], 0, "nada para anexar")
        igual(paginas_de(d4["pdf"]), 1, "mas a capa existe")
        ok("Nenhum documento anexado" in texto_de(d4["pdf"]),
           "e ela diz isso com todas as letras")
        ok("Sem documento" in texto_de(d4["pdf"]),
           "com o quadro mostrando as lacunas")

        # ═══════════════════════════════════════════════════════════════
        secao("O lote: a carteira inteira num PDF")
        empresas = [{"identidade": ALFA, "nome": "ALFA COMERCIO"},
                    {"identidade": BETA, "nome": "BETA SERVICOS"},
                    {"identidade": "66789006000106", "nome": "GAMA TRANSPORTES"}]
        L = rel.lote(base, ix, empresas, HOJE)
        igual(len(L["empresas"]), 3, "as três aparecem no resumo")
        igual(L["anexados"], 3, "e os 3 documentos anexáveis entraram")
        tl = texto_de(L["pdf"])
        for nome in ("ALFA COMERCIO", "BETA SERVICOS", "GAMA TRANSPORTES"):
            ok(nome in tl, "%s está no quadro consolidado" % nome)
        ok("3 empresas" in tl, "com a contagem no topo")
        bruto_lote = L["pdf"].lower()
        for marca in MARCAS:
            ok(marca not in bruto_lote, "o lote também não leva %r"
               % marca.decode())

        # ═══════════════════════════════════════════════════════════════
        secao("Uma empresa com problema não derruba o lote")
        ruim = empresas + [{"identidade": "", "nome": "SEM IDENTIDADE"}]
        L2 = rel.lote(base, ix, ruim, HOJE)
        igual(len(L2["empresas"]), 4, "as quatro aparecem")
        ok(any(e.get("erro") for e in L2["empresas"]),
           "a problemática vem marcada com erro")
        ok(paginas_de(L2["pdf"]) >= 1, "e o PDF saiu assim mesmo")

        # ═══════════════════════════════════════════════════════════════
        secao("O motor NÃO redesenha documento de órgão")
        fonte = (RAIZ / "situacao" / "relatorio.py").read_text("utf-8")
        import ast
        arvore = ast.parse(fonte)
        for no in ast.walk(arvore):
            if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef)) \
                    and ast.get_docstring(no):
                no.body = no.body[1:]
        codigo = ast.unparse(arvore)
        for proibido in ("MINISTÉRIO", "MINISTERIO", "RECEITA FEDERAL",
                         "PROCURADORIA", "SECRETARIA DA FAZENDA"):
            ok(proibido not in codigo.upper(),
               "o código não desenha cabeçalho de órgão (%r)" % proibido)
        ok("escritor.append" in codigo,
           "ele ANEXA o original — é compilação, não redesenho")

    finally:
        ix.fechar()

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
