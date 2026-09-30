#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O PDF dá para ler? — a medição que vem ANTES de escrever leitor.

    python teste_situacao_diagnostico.py

POR QUE ESTE TESTE EXISTE
    As Fases E e G (leitores do Recife e da SEFAZ-PE) dependem de amostra real,
    e o plano proíbe escrever leitor antes dela. Ao procurar a primeira amostra
    apareceu algo que muda o que essas fases precisam ser.

    O único PDF do portal do Recife encontrado nesta máquina em 12/09/2026
    (`/Title: "Recife em Dia"`) tem:

        2 páginas · 0 caracteres de texto · 0 fontes · 1 imagem por página
        /Producer: "Microsoft: Print To PDF"

    Foi **impresso** para PDF, e a impressão rasterizou a página. Nenhum leitor
    por extração de texto lê esse arquivo — não é layout desconhecido, é que
    não existe texto. Ler exigiria OCR, que num documento fiscal troca "não
    sei" por "li errado": dígito trocado em CNPJ ou data é pior que campo vazio.

    Então a primeira pergunta sobre qualquer amostra não é "qual o layout" e sim
    **"existe camada de texto?"**. Este módulo responde isso sem conhecer
    layout nenhum, e é o que impede alguém gastar um dia escrevendo parser para
    um arquivo que é fotografia.

O QUE ESTE TESTE **NÃO** FAZ
    Não vai à rede e não depende das amostras: os PDFs são construídos aqui —
    um com texto, um só com imagem — para que a distinção fique travada.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from situacao.leitores import diagnostico as diag  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []


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
    ok(a == b, desc if a == b else "%s  (obtive %r, esperava %r)" % (desc, a, b))


# ── os dois PDFs que fazem a distinção ──────────────────────────────────
TEXTO_EXTRATO = [
    "PREFEITURA DO RECIFE - SECRETARIA DE FINANCAS",
    "EXTRATO DE DEBITOS - INSCRICAO MERCANTIL",
    "Inscricao Municipal: 473.900-0",
    "CNPJ: 04.103.256/0001-85",
    "Razao Social: MONTE ASSESSORIA E CONSULTORIA LTDA",
    "Emitido em: 12/09/2026",
    "Competencia: 08/2026",
    "Protocolo 12345678901234",          # 14 algarismos, NAO e CNPJ
    "Situacao: NADA CONSTA",
]


def pdf_com_texto() -> bytes:
    from fpdf import FPDF
    pdf = FPDF(unit="mm", format="A4")
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    for linha in TEXTO_EXTRATO:
        pdf.cell(0, 6, linha, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def pdf_so_imagem() -> bytes:
    """Uma página que é só imagem — o que o 'print to PDF' produz."""
    import io as _io
    from fpdf import FPDF
    from PIL import Image
    img = Image.new("RGB", (600, 850), (250, 250, 250))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    pdf = FPDF(unit="mm", format="A4")
    pdf.add_page()
    pdf.image(buf, x=0, y=0, w=210, h=297)
    pdf.set_producer("Microsoft: Print To PDF")   # como o real veio
    return bytes(pdf.output())


def main():
    secao("PDF com texto: legível, e os identificadores saem")
    d = diag.analisar(pdf_com_texto())
    ok(d["abriu"], "abriu")
    ok(d["tem_texto"], "tem camada de texto")
    ok(not d["provavel_imagem"], "e não é imagem")
    ok(d["fontes"] > 0, "declara fonte (%d)" % d["fontes"])
    igual(d["cnpjs"], ["04103256000185"], "o CNPJ sai, já sem pontuação")
    igual(d["cims"], ["473.900-0"], "o CIM do Recife sai no formato dele")
    ok("12/09/2026" in d["datas"], "a data de emissão está entre as datas")
    ok("08/2026" in d["competencias"], "e a competência entre as competências")
    ok("LEGÍVEL" in diag.veredito(d), "o veredito diz que dá para ler")

    secao("O dígito verificador é a peneira, não o comprimento")
    # "Protocolo 12345678901234" tem 14 algarismos. Sem conferir o DV, ele
    # entraria na lista de CNPJ e um leitor escolheria a empresa errada.
    ok("12345678901234" not in d["cnpjs"],
       "protocolo de 14 algarismos NÃO é reportado como CNPJ")
    ok(diag.cnpj_valido("04.103.256/0001-85"), "CNPJ real passa")
    ok(not diag.cnpj_valido("04.103.256/0001-86"), "e um dígito trocado não")
    ok(not diag.cnpj_valido("11111111111111"), "nem repetição")
    ok(diag.cpf_valido("123.456.789-09"), "CPF real passa")
    ok(not diag.cpf_valido("123.456.694-72"), "e um dígito trocado não")

    secao("PDF que é só imagem: o veredito manda parar")
    im = diag.analisar(pdf_so_imagem())
    ok(im["abriu"], "abriu")
    igual(im["caracteres"], 0, "zero caracteres de texto")
    igual(im["fontes"], 0, "zero fontes")
    ok(im["imagens"] >= 1, "e ao menos uma imagem de página")
    ok(not im["tem_texto"], "então NÃO tem camada de texto")
    ok(im["provavel_imagem"], "é classificado como imagem")
    v = diag.veredito(im)
    ok("SEM CAMADA DE TEXTO" in v, "o veredito diz isso: %r" % v[:46])
    ok("OCR" in v, "e nomeia o que seria necessário")
    ok("print to pdf" in im["rasterizador"],
       "o /Producer denuncia como o arquivo foi salvo")

    secao("Um leitor por extração de texto não serviria — e isso é dito antes")
    # A CONSEQUÊNCIA PRÁTICA. Sem esta distinção, a Fase E seria escrita, os
    # testes passariam com fixtura de texto, e em produção o leitor devolveria
    # {} para todo arquivo salvo por impressão — sem ninguém saber por quê.
    ok(not im["cnpjs"] and not im["cims"] and not im["datas"],
       "de imagem não sai identificador nenhum, por definição")

    secao("Nunca levanta — nem com lixo, nem com PDF cortado")
    for rotulo, bruto in (("bytes que não são PDF", b"isto nao e um pdf"),
                          ("vazio", b""),
                          ("PDF truncado", pdf_com_texto()[:120])):
        r = diag.analisar(bruto)
        ok(isinstance(r, dict), "%s devolve dicionário" % rotulo)
        ok(not r["abriu"] or r["caracteres"] == 0,
           "  e não finge ter lido (%s)" % (r["erro"] or "abriu vazio"))

    secao("O diagnóstico NÃO é um leitor")
    # Ele mede; não afirma validade. Entrar no registro faria o índice acreditar
    # que existe leitor municipal quando não existe.
    from situacao import leitores, modelo
    reg = leitores.registro()
    ok((modelo.MUNICIPAL, modelo.EXTRATO) not in reg,
       "o municipal continua SEM leitor — as Fases E/G seguem abertas")
    ok((modelo.ESTADUAL, modelo.EXTRATO) not in reg,
       "e o estadual também")
    ok(diag.analisar not in reg.values(),
       "e o diagnóstico não se disfarça de leitor")
    igual(leitores.ler(modelo.MUNICIPAL, modelo.EXTRATO, pdf_com_texto()), {},
          "sem leitor, a leitura devolve {} — 'está aqui, não sei o que diz'")

    print("\n" + "=" * 62)
    print(f"{_ok} ok · {_falhas} falha(s)")
    for e in _erros:
        print("  - " + e)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
