#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Utilitários compartilhados das suítes de NF-e 6 — SEM asserção nenhuma.

    Este arquivo NÃO é suíte. Ele é importado, nunca executado.

POR QUE ELE EXISTE
    `teste_nfe6b.py` e `teste_nfe6b_ux.py` precisavam de `montar()`,
    `carregar_main()`, `CONTA` e `Req`, e os importavam de `teste_nfe6.py`.

    Só que `teste_nfe6.py` tem as asserções no nível do módulo: importar é
    executar. Cada uma dessas duas suítes rodava as **312 asserções** do
    `teste_nfe6` de novo, antes das próprias. Numa rodada completa elas
    executavam três vezes — e o `teste_nfe6` é das mais lentas, porque gera
    DANFE em PDF.

    Pior que o tempo: a contagem. A saída passava a ter DUAS linhas-resumo, e
    o total da suíte virava questão de qual delas o runner lia — 627, 936 ou
    1.251 asserções para os mesmos três arquivos. Foi daí que saíram os
    números que não fechavam.

    Aqui não há asserção nem `__main__`: importar este módulo não executa
    teste nenhum.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

# Lido pelo runner por AST, sem importar: declara que este arquivo é apoio.
TESTE_APOIO = True

import teste_apoio as apoio                          # noqa: E402
import ingestao as ing                               # noqa: E402
from ingestao import acervo as acv                   # noqa: E402
from ingestao import pipeline as pipe                # noqa: E402
from teste_fixturas_nfe import (                     # noqa: E402
    cnpj_ficticio, chave_ficticia, xml_nfe, xml_resumo, xml_evento, bruto,
    EMPRESA_A, FORNECEDOR,
)


OUTRA = cnpj_ficticio("888888880001")
CONTA = "conta-teste-1"
SAIDA = "2026-08-03T14:30:00-03:00"


class Req:
    """Dublê de `Request`: as rotas só leem `query_params`."""

    def __init__(self, **q):
        self.query_params = {k: str(v) for k, v in q.items() if v not in (None, "")}


# ══════════════════════════════════════════════════════════════════════════
def montar(raiz, empresa=EMPRESA_A):
    """Acervo variado: recebida, emitida, transporte, cancelada, resumo.

    A `recebida` traz `dhSaiEnt`; a `emitida` NÃO traz — é o par que separa
    "ausente" de "igual à emissão".
    """
    import json

    (Path(raiz) / "certs").mkdir(parents=True, exist_ok=True)
    (Path(raiz) / "certs" / "E.pfx").write_bytes(b"PFX-FALSO")
    (Path(raiz) / "certificados.json").write_text(json.dumps([{
        "id": CONTA, "cnpj": empresa, "nome": "EMPRESA / TESTE & CIA LTDA.",
        "caminho": "certs/E.pfx"}]), encoding="utf-8")

    ac = acv.abrir(raiz, empresa, servico="nfe_distribuicao",
                   ambiente=ing.PRODUCAO)
    ch, n = {}, 0

    def guarda(conteudo, schema="procNFe_v4.00.xsd"):
        nonlocal n
        n += 1
        ac.preservar(bruto(conteudo, nsu=str(1000 + n), schema=schema))

    c1 = chave_ficticia(FORNECEDOR, 1); ch["recebida"] = c1
    guarda(xml_nfe(c1, valor="1000.00", numero="1", saida_entrada=SAIDA))

    c2 = chave_ficticia(EMPRESA_A, 2); ch["emitida"] = c2
    guarda(xml_nfe(c2, valor="2000.00", numero="2",
                   emitente=EMPRESA_A, destinatario=FORNECEDOR))

    c3 = chave_ficticia(FORNECEDOR, 3); ch["transporte"] = c3
    base = xml_nfe(c3, valor="9000.00", numero="3",
                   destinatario=OUTRA, saida_entrada=SAIDA).decode("utf-8")
    base = base.replace("</infNFe>", f"<transp><transporta><CNPJ>{EMPRESA_A}"
                                     f"</CNPJ></transporta></transp></infNFe>")
    guarda(base.encode("utf-8"))

    c4 = chave_ficticia(FORNECEDOR, 4); ch["cancelada"] = c4
    guarda(xml_nfe(c4, valor="400.00", numero="4"))
    guarda(xml_evento(c4, tp="110111"), "procEventoNFe_v1.00.xsd")

    c5 = chave_ficticia(FORNECEDOR, 5); ch["resumo"] = c5
    guarda(xml_resumo(c5, valor="500.00"), "resNFe_v1.01.xsd")

    pipe.indexar_pendentes(raiz, empresa)
    return ch


def carregar_main(raiz):
    os.environ["FISCALE_DADOS"] = str(raiz)
    # A exportação padrão nunca pode cair na raiz do disco do usuário durante
    # um teste. A pasta é temporária e é apagada com as demais.
    os.environ["FISCALE_EXPORTACOES"] = str(
        apoio.registrar_para_apagar(apoio.pasta_temp("nfe6_exp_")))
    import fiscale_dados as fd
    fd._raiz_cache = None
    fd.raiz(raiz)
    sys.modules.pop("main", None)
    import main
    return main


def abrir_zip(resposta) -> zipfile.ZipFile:
    """A rota devolve `FileResponse`; aqui se lê o arquivo que ela aponta."""
    return zipfile.ZipFile(resposta.path)
