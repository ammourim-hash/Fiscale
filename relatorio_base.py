# -*- coding: utf-8 -*-
"""relatorio_base.py — a regra white-label, em UMA implementação.

POR QUE ESTE MÓDULO EXISTE
    A regra decidida em 11/09/2026 é que o nome do sistema não aparece no PDF
    gerado — nem na capa, nem no rodapé, nem no título, nem **nos metadados**,
    que é por onde ele escaparia sem ninguém ver.

    Ela nasceu dentro de `situacao/relatorio.py`, que compila dossiês. O
    relatório de Optantes precisa da MESMA regra, e é a única coisa que ele
    compartilha com o dossiê: ele não é documento de órgão, não entra no
    envelope e não conhece o índice da Situação Fiscal.

    Copiar `_seguro`, `METADADOS`, `_Capa` e `nome_de_arquivo` para lá seria
    duas implementações da mesma regra — ou seja, uma implementação e um bug
    esperando o dia em que alguém corrigir só um dos lados. Então elas moram
    aqui, e os dois relatórios importam daqui.

O QUE **NÃO** MORA AQUI
    Nada que saiba o que é esfera, certidão, extrato ou envelope. Este módulo
    desenha e sanea; quem sabe o que está desenhando é quem chama.
"""
from __future__ import annotations

import datetime as _dt

TRACO = "-"          # o "vazio" dos campos, em ASCII

# ── o saneador de texto ──────────────────────────────────────────────────
#     As fontes básicas do PDF só entendem latin-1. O travessão tipográfico
#     (—), as aspas curvas e as reticências não estão lá, e o `fpdf2` LEVANTA
#     em vez de degradar.
#
#     Isso é armadilha de verdade: o nome de uma empresa vem do cadastro, e
#     basta um caractere colado de um site para derrubar o relatório inteiro.
#     Um relatório que falha por causa de um apóstrofo é um relatório que
#     ninguém consegue emitir no dia em que precisa.
EQUIVALENTES = {
    "—": "-", "–": "-", "‒": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", " ": " ", "•": "*", "→": "->",
    "≥": ">=", "≤": "<=",
}

# O nome do arquivo também é superfície da regra white-label: é a primeira
# coisa que a pessoa vê, vai para o e-mail que ela encaminha e sobrevive ao
# PDF. E precisa ser ASCII, porque vai no `Content-Disposition`, que é latin-1
# no protocolo — acento ali sai como lixo no Windows.
SEM_ACENTO = {
    "á": "a", "à": "a", "â": "a", "ã": "a", "ä": "a",
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "í": "i", "ì": "i", "î": "i", "ï": "i",
    "ó": "o", "ò": "o", "ô": "o", "õ": "o", "ö": "o",
    "ú": "u", "ù": "u", "û": "u", "ü": "u",
    "ç": "c", "ñ": "n",
}


def seguro(texto) -> str:
    """Texto que a fonte básica aceita. Nunca levanta."""
    t = str(texto if texto is not None else "")
    for de, para in EQUIVALENTES.items():
        if de in t:
            t = t.replace(de, para)
    try:
        t.encode("latin-1")
        return t
    except UnicodeEncodeError:
        return t.encode("latin-1", "replace").decode("latin-1")


def br(iso: str) -> str:
    """'AAAA-MM-DD' -> 'DD/MM/AAAA'. Sem data, o traço."""
    if not iso:
        return TRACO
    p = str(iso)[:10].split("-")
    return "%s/%s/%s" % (p[2], p[1], p[0]) if len(p) == 3 else str(iso)


def doc(d: str) -> str:
    """CNPJ ou CPF pontuado. Nem todo cliente é empresa."""
    d = "".join(c for c in str(d or "") if c.isdigit())
    if len(d) == 14:
        return "%s.%s.%s/%s-%s" % (d[:2], d[2:5], d[5:8], d[8:12], d[12:])
    if len(d) == 11:
        return "%s.%s.%s-%s" % (d[:3], d[3:6], d[6:9], d[9:])
    return d or TRACO


def metadados(titulo: str) -> dict:
    """Metadados neutros do documento final.

    Zerar é explícito de propósito: o `pypdf` grava `/Producer: pypdf` por
    conta própria ao escrever, e o `/Title` é visível na aba do navegador.
    Só o título sobrevive, e ele descreve o CONTEÚDO — nunca quem gerou.
    """
    return {"/Producer": "", "/Creator": "", "/Author": "",
            "/Subject": "", "/Keywords": "", "/Title": titulo}


def nome_de_arquivo(base: str, hoje=None, prefixo: str = "situacao-fiscal") -> str:
    """Nome neutro e ASCII para o arquivo baixado. Nunca levanta.

    `<prefixo>-<base>-<aaaammdd>.<ext>` — descreve o CONTEÚDO, não quem gerou.
    Sem base utilizável, sai só com a data: melhor um nome genérico que um
    nome inventado. O `prefixo` pode trazer a extensão (`"...csv"`); sem ela,
    assume `.pdf`, que era o único caso quando isto nasceu.
    """
    hoje = hoje or _dt.date.today()
    prefixo, _, ext = str(prefixo or "").partition(".")
    ext = ext or "pdf"
    t = str(base or "").lower()
    for de, para in SEM_ACENTO.items():
        t = t.replace(de, para)
    limpo = []
    for c in t:
        if c.isalnum() and c.isascii():
            limpo.append(c)
        elif limpo and limpo[-1] != "-":
            limpo.append("-")
    miolo = "".join(limpo).strip("-")[:60].strip("-")
    return "%s-%s%s.%s" % (prefixo, (miolo + "-") if miolo else "",
                           hoje.strftime("%Y%m%d"), ext)


class Capa:
    """Desenha em A4. Nenhuma marca do sistema entra aqui."""

    MARGEM = 18
    LARGURA = 210 - 2 * MARGEM

    def __init__(self, titulo: str = ""):
        from fpdf import FPDF
        self.pdf = FPDF(unit="mm", format="A4")
        self.pdf.set_auto_page_break(True, margin=18)
        self.pdf.set_margins(self.MARGEM, self.MARGEM, self.MARGEM)
        # O `fpdf2` nao grava Producer, mas o titulo do documento e visivel
        # no navegador -- e ele tambem e generico.
        self.pdf.set_title(titulo)
        self.pdf.set_creator("")
        self.pdf.set_producer("")
        self.pdf.set_author("")

    # -- tipografia ------------------------------------------------------
    def titulo(self, texto, tamanho=16):
        self.pdf.set_font("Helvetica", "B", tamanho)
        self.pdf.set_text_color(20, 30, 40)
        self.pdf.multi_cell(self.LARGURA, tamanho * 0.45, seguro(texto))
        self.pdf.ln(2)

    def rotulo(self, texto):
        self.pdf.set_font("Helvetica", "B", 8)
        self.pdf.set_text_color(110, 120, 128)
        self.pdf.cell(self.LARGURA, 4, seguro(texto).upper(), new_x="LMARGIN",
                      new_y="NEXT")

    def texto(self, t, tamanho=10, negrito=False, cor=(30, 40, 50)):
        self.pdf.set_font("Helvetica", "B" if negrito else "", tamanho)
        self.pdf.set_text_color(*cor)
        self.pdf.multi_cell(self.LARGURA, tamanho * 0.5, seguro(t))

    def risco(self, folga=4):
        self.pdf.ln(folga)
        y = self.pdf.get_y()
        self.pdf.set_draw_color(205, 213, 218)
        self.pdf.line(self.MARGEM, y, 210 - self.MARGEM, y)
        self.pdf.ln(folga)

    def pagina(self):
        self.pdf.add_page()

    def bytes(self) -> bytes:
        return bytes(self.pdf.output())
