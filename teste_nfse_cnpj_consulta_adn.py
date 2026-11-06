#!/usr/bin/env python3
"""Testes puros do parâmetro cnpjConsulta do ADN. Não acessa rede nem dados."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "nfse" / "backend"))
import core  # noqa: E402


class CnpjConsultaAdnTests(unittest.TestCase):
    def test_url_direta_preserva_endpoint_antigo(self):
        self.assertEqual(
            core.montar_url_distribuicao("https://adn.nfse.gov.br", 123),
            "https://adn.nfse.gov.br/contribuintes/DFe/123",
        )

    def test_url_representada_inclui_cnpj_consulta(self):
        self.assertEqual(
            core.montar_url_distribuicao(
                "https://adn.nfse.gov.br", 123, "27.824.794/0001-64"
            ),
            "https://adn.nfse.gov.br/contribuintes/DFe/123?cnpjConsulta=27824794000164",
        )

    def test_rejeita_cnpj_invalido_antes_da_rede(self):
        with self.assertRaises(ValueError):
            core.montar_url_distribuicao(
                "https://adn.nfse.gov.br", 123, "11.111.111/1111-11"
            )

    def test_nao_altera_cnpj_do_certificado(self):
        # O parâmetro é a empresa consultada; a identidade mTLS é independente.
        url = core.montar_url_distribuicao(
            "https://adn.nfse.gov.br", 0, "27824794000164"
        )
        self.assertIn("cnpjConsulta=27824794000164", url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
