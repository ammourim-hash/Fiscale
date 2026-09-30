"""texto.py — tira o texto do arquivo importado, seja qual for o formato.

Os relatórios que temos hoje são PDF com camada de texto (não são digitalizações),
então `pypdf` resolve. CSV/TXT entram direto. Se o PDF não tiver camada de texto
(scan), devolvemos vazio e o importador marca ERRO com uma explicação — melhor
do que fingir que leu.
"""
from __future__ import annotations

import re

try:
    from pypdf import PdfReader
    TEM_PYPDF = True
except Exception:                       # pragma: no cover - ambiente sem a lib
    PdfReader = None
    TEM_PYPDF = False

SEM_PYPDF = ("Para ler PDF é preciso a biblioteca 'pypdf'. "
             "Instale com: pip install pypdf")


def eh_pdf(dados: bytes) -> bool:
    return dados[:5] == b"%PDF-"


def texto_de_pdf(dados: bytes) -> str:
    if not TEM_PYPDF:
        raise RuntimeError(SEM_PYPDF)
    import io
    r = PdfReader(io.BytesIO(dados))
    paginas = []
    for pg in r.pages:
        try:
            paginas.append(pg.extract_text() or "")
        except Exception:
            paginas.append("")
    return "\n".join(paginas)


def texto_de(dados: bytes, nome: str = "") -> str:
    """Texto do arquivo. Levanta RuntimeError com mensagem em português quando
    não dá para ler — quem chama transforma isso no status ERRO do extrato."""
    if eh_pdf(dados):
        t = texto_de_pdf(dados)
        if not re.search(r"[A-Za-z]{4}", t):
            raise RuntimeError(
                "O PDF não tem camada de texto (parece ser digitalização/imagem). "
                "Baixe o relatório original do portal da operadora, não o escaneado.")
        return t
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return dados.decode(enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Não reconheci o formato do arquivo '{nome}'.")


def linhas(texto: str) -> list:
    """Linhas úteis: sem vazias, com espaço colapsado."""
    return [re.sub(r"[ \t]+", " ", l).strip()
            for l in (texto or "").split("\n") if l.strip()]
