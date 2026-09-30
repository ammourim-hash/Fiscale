# -*- coding: utf-8 -*-
"""diagnostico.py — o PDF dá para ler? E o que há dentro dele?

POR QUE ISTO VEM ANTES DOS LEITORES
    O plano é claro: **antes da amostra não se escreve uma linha do leitor.**
    Só que "ter a amostra" não basta — é preciso saber o que ela é. E a
    primeira coisa a descobrir não é o layout: é se existe **camada de texto**.

    Medido em 12/09/2026 no único PDF do portal do Recife encontrado nesta
    máquina (`/Title: "Recife em Dia"`):

        2 páginas · 0 caracteres de texto · 0 fontes · 1 imagem por página
        /Producer: "Microsoft: Print To PDF"

    Ele foi **impresso** para PDF, e a impressão rasterizou a página. Nenhum
    leitor por extração de texto jamais lerá esse arquivo — não é questão de
    layout, é que não há texto nenhum. Ler exigiria OCR, que é dependência
    nova e, num documento fiscal, troca "não sei" por "li errado": um dígito
    trocado num CNPJ ou numa data é pior do que campo vazio.

    Por isso este módulo existe separado dos leitores. Ele responde, para
    qualquer PDF, três perguntas que não dependem de layout nenhum:

      1. há camada de texto, ou é imagem?
      2. que identificadores plausíveis aparecem (CNPJ, CPF, CIM, datas)?
      3. de onde o arquivo veio (o `/Producer`, que denuncia o "print to PDF")?

    Com isso, mapear uma amostra nova é rodar uma linha em vez de abrir o PDF e
    conferir no olho — e a resposta "este arquivo é imagem" chega ANTES de
    alguém gastar um dia escrevendo parser para ele.

O QUE ELE NÃO FAZ
    Não decide empresa, não devolve validade e **não é um leitor**: não entra
    no `registro()`. É instrumento de medição. E, como os leitores, **nunca
    levanta** — PDF corrompido devolve o que deu para saber.
"""
from __future__ import annotations

import io
import re

# Um "print to PDF" não é o único jeito de perder o texto, mas é o que
# apareceu aqui. O campo é informativo: a decisão vem da contagem de texto.
RASTERIZADORES = ("print to pdf", "microsoft: print to pdf", "image printer",
                  "pdfcreator", "scanner", "canon", "epson", "hp scan")

# Abaixo disto a página é, na prática, sem texto: um cabeçalho solto de 20
# caracteres não sustenta leitor nenhum.
MINIMO_TEXTO_POR_PAGINA = 40

_CNPJ = re.compile(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b")
_CPF = re.compile(r"\b(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b")
_DATA = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_COMPET = re.compile(r"\b(\d{2}/\d{4})\b")
# O CIM do Recife aparece pontuado (473.900-0) e sem pontuação.
_CIM = re.compile(r"\b(\d{3}\.\d{3}-\d)\b")


def _dig(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def cnpj_valido(c: str) -> bool:
    """Dígito verificador. Sem isto, qualquer sequência de 14 algarismos do
    documento — número de protocolo, código de barras — passaria por CNPJ."""
    c = _dig(c)
    if len(c) != 14 or c == c[0] * 14:
        return False
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(d) * p for d, p in zip(c[:tam], pesos))
        resto = soma % 11
        if int(c[tam]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def cpf_valido(c: str) -> bool:
    c = _dig(c)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(c[i]) * ((tam + 1) - i) for i in range(tam))
        resto = (soma * 10) % 11
        if int(c[tam]) != (0 if resto == 10 else resto):
            return False
    return True


def analisar(bruto: bytes) -> dict:
    """O que se sabe deste PDF sem conhecer o layout dele. Nunca levanta."""
    r = {
        "abriu": False, "paginas": 0, "caracteres": 0, "fontes": 0,
        "imagens": 0, "produtor": "", "titulo": "", "tem_texto": False,
        "provavel_imagem": False, "rasterizador": "",
        "cnpjs": [], "cpfs": [], "cims": [], "datas": [], "competencias": [],
        "texto": "", "erro": "",
    }
    try:
        import pypdf
    except Exception as e:
        r["erro"] = "pypdf indisponível (%s)" % type(e).__name__
        return r
    try:
        doc = pypdf.PdfReader(io.BytesIO(bruto))
        meta = doc.metadata or {}
        r["abriu"] = True
        r["paginas"] = len(doc.pages)
        r["produtor"] = str(meta.get("/Producer") or "")
        r["titulo"] = str(meta.get("/Title") or "")
    except Exception as e:
        r["erro"] = "não abriu: %s" % type(e).__name__
        return r

    partes = []
    for p in doc.pages:
        try:
            partes.append(p.extract_text() or "")
        except Exception:
            partes.append("")
        try:
            fontes = ((p.get("/Resources") or {}).get("/Font") or {})
            r["fontes"] += len(fontes)
        except Exception:
            pass
        try:
            r["imagens"] += len(p.images)
        except Exception:
            pass

    texto = "\n".join(partes)
    r["texto"] = texto
    r["caracteres"] = len(texto.strip())
    # O critério é por PÁGINA: um documento de 10 páginas com 50 caracteres no
    # total é imagem com uma legenda, não documento legível.
    media = r["caracteres"] / max(1, r["paginas"])
    r["tem_texto"] = media >= MINIMO_TEXTO_POR_PAGINA
    r["provavel_imagem"] = (not r["tem_texto"]) and r["imagens"] > 0
    baixo = r["produtor"].lower()
    r["rasterizador"] = next((m for m in RASTERIZADORES if m in baixo), "")

    # Só o que passa no dígito verificador. Protocolo e código de barras têm
    # 14 algarismos e entrariam na lista sem esta peneira.
    r["cnpjs"] = sorted({_dig(m) for m in _CNPJ.findall(texto)
                         if cnpj_valido(m)})
    r["cpfs"] = sorted({_dig(m) for m in _CPF.findall(texto) if cpf_valido(m)})
    r["cims"] = sorted(set(_CIM.findall(texto)))
    r["datas"] = sorted(set(_DATA.findall(texto)))
    r["competencias"] = sorted(set(_COMPET.findall(texto)))
    return r


def veredito(d: dict) -> str:
    """Uma frase que diz se dá para escrever leitor para este arquivo."""
    if not d.get("abriu"):
        return "NÃO ABRIU — %s" % (d.get("erro") or "motivo desconhecido")
    if d.get("provavel_imagem"):
        extra = (" (salvo por %r)" % d["rasterizador"]) if d.get("rasterizador") else ""
        return ("SEM CAMADA DE TEXTO%s — são imagens de página. Leitor por "
                "extração de texto não serve; exigiria OCR." % extra)
    if not d.get("tem_texto"):
        return ("TEXTO INSUFICIENTE — %d caracteres em %d página(s). Não dá "
                "para mapear layout com isto." % (d["caracteres"], d["paginas"]))
    achados = []
    for rot, chave in (("CNPJ", "cnpjs"), ("CPF", "cpfs"), ("CIM", "cims"),
                       ("data", "datas"), ("competência", "competencias")):
        if d.get(chave):
            achados.append("%s: %s" % (rot, ", ".join(d[chave][:4])))
    return ("LEGÍVEL — %d caracteres em %d página(s). %s"
            % (d["caracteres"], d["paginas"],
               " · ".join(achados) or "nenhum identificador reconhecido"))


def _principal(argv) -> int:
    """`python -m situacao.leitores.diagnostico <arquivo.pdf> ...`"""
    from pathlib import Path
    alvos = [a for a in argv if not a.startswith("-")]
    se_texto = "--texto" in argv
    if not alvos:
        print(__doc__.strip().splitlines()[0])
        print("\nuso: python -m situacao.leitores.diagnostico <pdf> [...] [--texto]")
        return 2
    for caminho in alvos:
        p = Path(caminho)
        print("=" * 74)
        print(p.name)
        if not p.is_file():
            print("  arquivo não encontrado")
            continue
        d = analisar(p.read_bytes())
        print("  %s" % veredito(d))
        print("  páginas %d · caracteres %d · fontes %d · imagens %d"
              % (d["paginas"], d["caracteres"], d["fontes"], d["imagens"]))
        if d["produtor"] or d["titulo"]:
            print("  produtor %r · título %r" % (d["produtor"], d["titulo"]))
        if se_texto and d["texto"].strip():
            print("  ---- texto ----")
            for linha in d["texto"].splitlines():
                if linha.strip():
                    print("  | " + linha.rstrip()[:110])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_principal(sys.argv[1:]))
