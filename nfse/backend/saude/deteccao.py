"""deteccao.py — descobre a operadora pelo conteúdo do relatório.

O usuário não deve ter de escolher a operadora quando o documento já se
identifica. Mas classificar errado em silêncio é pior do que perguntar: o
extrato inteiro sairia com os totais lidos do lugar errado. Então:

  • confiança alta e folga sobre o segundo colocado  → decide sozinho
  • empate técnico, ou nenhum parser reconhecendo    → devolve `precisa_confirmar`
    e a tela pergunta, oferecendo os candidatos ordenados
"""
from __future__ import annotations

from .parsers import PARSERS

# a partir daqui o parser reconheceu o bastante para assumir
LIMIAR_DECIDE = 0.6
# e precisa estar na frente do segundo por esta margem
MARGEM = 0.25


class Deteccao:
    def __init__(self, parser, confianca, candidatos, precisa_confirmar, motivo=""):
        self.parser = parser
        self.confianca = confianca
        self.candidatos = candidatos          # [(nome, confiança), ...] ordenado
        self.precisa_confirmar = precisa_confirmar
        self.motivo = motivo

    def dict(self):
        return {"operadora": self.parser.nome if self.parser else None,
                "confianca": round(self.confianca, 2),
                "precisa_confirmar": self.precisa_confirmar,
                "motivo": self.motivo,
                "candidatos": [{"operadora": n, "confianca": round(c, 2)}
                               for n, c in self.candidatos]}


def detectar(texto: str) -> Deteccao:
    notas = sorted(((p, p.confianca(texto or "")) for p in PARSERS),
                   key=lambda x: x[1], reverse=True)
    candidatos = [(p.nome, c) for p, c in notas]
    melhor, nota = notas[0]
    segundo = notas[1][1] if len(notas) > 1 else 0.0

    if nota < LIMIAR_DECIDE:
        return Deteccao(None, nota, candidatos, True,
                        "Não reconheci a operadora pelo conteúdo do arquivo. "
                        "Confirme qual é, ou verifique se o relatório é o correto.")
    if nota - segundo < MARGEM:
        return Deteccao(None, nota, candidatos, True,
                        f"O arquivo se parece com mais de uma operadora "
                        f"({candidatos[0][0]} e {candidatos[1][0]}). Confirme qual é.")
    return Deteccao(melhor, nota, candidatos, False)
