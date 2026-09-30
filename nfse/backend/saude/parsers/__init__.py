"""Registro dos parsers por operadora.

Só entra parser construído contra AMOSTRA REAL. Hapvida, Unimed, Amil e
companhia ficam de fora até aparecer um relatório de verdade: parser escrito
"por dedução" a partir do que a operadora provavelmente faz produz números
errados com cara de certos, que é o pior resultado possível numa conferência
de fatura.

Para acrescentar uma operadora: crie `<nome>.py` com uma subclasse de `Parser`
e some a instância em `PARSERS`. O detector e a tela se ajustam sozinhos.
"""
from .base import Parser
from .bradesco import ParserBradesco
from .sulamerica import ParserSulAmerica

PARSERS = [ParserSulAmerica(), ParserBradesco()]

# Operadoras que a tela mostra como "ainda sem parser" — para o usuário saber
# que o Fiscale conhece a operadora e o que falta é a amostra do relatório.
AGUARDANDO_AMOSTRA = ["Hapvida", "Unimed", "Amil", "NotreDame Intermédica"]


def por_nome(nome: str) -> Parser | None:
    alvo = (nome or "").strip().lower()
    for p in PARSERS:
        if p.nome.lower() == alvo:
            return p
    return None


__all__ = ["Parser", "PARSERS", "AGUARDANDO_AMOSTRA", "por_nome",
           "ParserBradesco", "ParserSulAmerica"]
