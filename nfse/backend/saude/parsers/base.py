"""base.py — o contrato que todo parser de operadora cumpre.

Duas obrigações, e as duas existem por um motivo prático:

* `confianca(texto)` — o usuário não deveria ter de dizer qual é a operadora
  quando o próprio documento diz. Cada parser pontua o quanto reconhece o
  texto (0 a 1) e o detector escolhe. Empate ou pontuação baixa = pergunta,
  nunca chute silencioso.

* `conferir(extrato)` — a regra de fechamento é DO PARSER. Bradesco fecha
  "soma das vidas = total da subfatura; + IOF = valor cobrado". SulAmérica
  fecha "soma = total geral; + acertos + IOF = total do resumo prêmio".
  Uma fórmula única para as duas geraria divergência onde não há.
"""
from __future__ import annotations

from ..modelo import Conferencia, Divergencia, Extrato


class Parser:
    nome = ""                # nome da operadora, como aparece na tela
    versao = "0"             # sobe quando a leitura muda; fica gravado no extrato
    ans = ""                 # registro ANS da operadora

    def confianca(self, texto: str) -> float:
        """0.0 = não é meu; 1.0 = é meu, sem dúvida."""
        raise NotImplementedError

    def ler(self, texto: str, extrato: Extrato) -> None:
        """Preenche `extrato` (cabeçalho, famílias, totais declarados)."""
        raise NotImplementedError

    def conferir(self, extrato: Extrato) -> Conferencia:
        raise NotImplementedError

    # ── auxiliares comuns ────────────────────────────────────────────────
    @staticmethod
    def _div(o_que, declarado, identificado, contexto="") -> Divergencia:
        return Divergencia(o_que=o_que, declarado=declarado,
                           identificado=identificado, contexto=contexto)

    def _conferir_familias(self, extrato: Extrato) -> list:
        """Comum às duas: cada família tem de fechar com o total que o
        relatório declarou para ela (quando declara)."""
        fora = []
        for f in extrato.familias:
            if f.total_declarado is None:
                continue
            d = self._div("Total da família", f.total_declarado, f.total_calculado,
                          (f.titular.nome if f.titular else "(sem titular)"))
            if d.relevante:
                fora.append(d)
        return fora
