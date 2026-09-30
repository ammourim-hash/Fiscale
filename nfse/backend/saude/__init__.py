"""Fiscale — Plano de Saúde (HEALTH 1: importação e normalização).

Transforma o relatório mensal da operadora (PDF) em dados individualizados por
TITULAR e DEPENDENTE, com conferência financeira contra os totais que o próprio
relatório declara.

NÃO gera EFD-Reinf. A saída normalizada é o insumo de uma camada REINF futura,
que fica deliberadamente desacoplada daqui.
"""
from .modelo import (
    Beneficiario, Cobranca, Familia, Extrato, Conferencia, Divergencia,
    STATUS, dec, PARENTESCOS,
)
from .servico import importar_arquivos, listar_competencias, competencia, reprocessar

__all__ = [
    "Beneficiario", "Cobranca", "Familia", "Extrato", "Conferencia", "Divergencia",
    "STATUS", "dec", "PARENTESCOS",
    "importar_arquivos", "listar_competencias", "competencia", "reprocessar",
]
