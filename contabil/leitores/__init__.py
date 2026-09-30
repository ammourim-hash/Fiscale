# -*- coding: utf-8 -*-
"""leitores — do arquivo do banco à lista de movimentos, sem perder linha.

O CONTRATO DE TODO LEITOR
    Devolve uma `Leitura` em que CADA linha do arquivo aparece exatamente uma
    vez: ou como `MovLido`, ou em `ignoradas` com o motivo. Quem importa
    confere a soma (`conferir_completude`) e RECUSA a leitura inteira se ela
    não fechar. Um leitor que descarta linha em silêncio é pior que um que
    quebra — o que quebra, pelo menos, avisa.

    Movimento com valor ou data ilegível NÃO é descartado: vira `MovLido` com
    `problema` preenchido e valor `None`. Fica visível na tela, fora das somas.

    Leitor nunca adivinha empresa. A conta bancária vem do próprio arquivo
    (OFX) ou de quem importa (CSV).
"""
from __future__ import annotations

from dataclasses import dataclass, field


class SemLeitor(ValueError):
    """O arquivo é de um formato que ainda não tem leitor. A mensagem diz o
    que foi reconhecido e o que falta — nunca "formato inválido" seco."""


@dataclass
class MovLido:
    ordem: int                    # posição no arquivo: 1, 2, 3...
    linha: int                    # linha física onde começa
    data: str = ""
    data_lancamento: str = ""
    descricao: str = ""
    documento: str = ""
    valor: int | None = None      # centavos; + entra, - sai
    saldo: int | None = None
    tipo_operacao: str = ""
    id_transacao: str = ""
    pagina: int | None = None
    problema: str = ""


@dataclass
class Leitura:
    formato: str
    linhas_total: int
    movimentos: list = field(default_factory=list)
    ignoradas: list = field(default_factory=list)   # (linha, conteudo, motivo)
    banco: str = ""
    agencia: str = ""
    conta: str = ""
    moeda: str = ""
    saldo_final: int | None = None
    saldo_final_data: str = ""
    # Para OFX: quantas vezes `<STMTTRN>` aparece no texto cru. É a
    # contagem que não depende do leitor ter entendido nada.
    blocos_brutos: int | None = None
    avisos: list = field(default_factory=list)


def conferir_completude(l: Leitura) -> None:
    """Recusa a leitura que perdeu linha. É a guarda de "nenhuma linha some"."""
    if l.blocos_brutos is not None:
        if l.blocos_brutos != len(l.movimentos):
            raise SemLeitor(
                "o arquivo tem %d transações e o leitor entendeu %d — nada "
                "foi importado" % (l.blocos_brutos, len(l.movimentos)))
        return
    contadas = len(l.movimentos) + len(l.ignoradas)
    if contadas != l.linhas_total:
        raise SemLeitor(
            "o arquivo tem %d linhas e a leitura prestou contas de %d — nada "
            "foi importado" % (l.linhas_total, contadas))
    vistas = [m.linha for m in l.movimentos] + [i[0] for i in l.ignoradas]
    if len(set(vistas)) != len(vistas):
        raise SemLeitor("a leitura contou a mesma linha duas vezes — nada "
                        "foi importado")


def formato_de(bruto: bytes, nome: str = "") -> str:
    """OFX, PDF ou CSV — pelo conteúdo primeiro, pelo nome depois."""
    cabeca = bruto[:2048].lstrip(b"\xef\xbb\xbf \r\n\t")
    if cabeca[:5] == b"%PDF-":
        return "PDF"
    alto = cabeca.upper()
    if b"OFXHEADER" in alto or b"<OFX>" in alto or b"<?OFX" in alto:
        return "OFX"
    n = (nome or "").lower()
    if n.endswith((".ofx", ".qfx")):
        return "OFX"
    if n.endswith(".pdf"):
        return "PDF"
    return "CSV"


def ler(bruto: bytes, nome: str = "") -> Leitura:
    fmt = formato_de(bruto, nome)
    if fmt == "OFX":
        from . import ofx
        return ofx.ler(bruto)
    if fmt == "PDF":
        from . import pdf
        return pdf.ler(bruto)
    from . import csv_extrato
    return csv_extrato.ler(bruto)


def decodificar(bruto: bytes, declarado: str = "") -> str:
    """UTF-8 quando é UTF-8; senão o 1252 que os bancos brasileiros usam."""
    if bruto.startswith(b"\xef\xbb\xbf"):
        bruto = bruto[3:]
    if declarado and declarado.upper() in ("1252", "WINDOWS-1252", "CP1252",
                                           "ISO-8859-1", "LATIN1", "LATIN-1"):
        try:
            return bruto.decode("utf-8")
        except UnicodeDecodeError:
            return bruto.decode("cp1252", errors="replace")
    try:
        return bruto.decode("utf-8")
    except UnicodeDecodeError:
        return bruto.decode("cp1252", errors="replace")
