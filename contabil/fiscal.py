# -*- coding: utf-8 -*-
"""fiscal.py — o acervo fiscal que JÁ EXISTE, visto pelo Contábil. Só leitura.

NÃO HÁ SEGUNDO ACERVO, E NÃO HÁ SEGUNDO PARSER
    Duas fontes, as mesmas de sempre:

      NF-e / NFC-e / CT-e   o índice da ingestão, `<empresa>/indice/documentos.db`,
                            cujo caminho é decidido por `ingestao.indice` —
                            o Contábil pergunta a ele onde o arquivo mora;
      NFS-e                 os XML do módulo NFS-e, lidos por
                            `core.carregar_notas()` — a mesma função que a
                            apuração usa, com o mesmo tratamento de
                            cancelada/substituída.

POR QUE O ÍNDICE É ABERTO EM `mode=ro`
    `Indice.conectar()` garante o esquema e GRAVA a versão no `meta` — é o
    certo para quem indexa, e errado para quem só consulta. O Contábil abre o
    mesmo arquivo em somente-leitura: não cria pasta, não cria banco, não
    altera linha. Empresa sem índice simplesmente não tem documento.

O QUE VOLTA
    Uma lista de dicionários com o mínimo para conciliar e rastrear:
    `alvo_id` ("NFSE:<chave>" ou "ACERVO:<id_documento>"), espécie, número,
    data, valores possíveis em centavos, contraparte, papel, situação e o
    caminho do arquivo. Nada disso é recalculado — é lido.
"""
from __future__ import annotations

import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import modelo

_BACKEND = str(Path(__file__).resolve().parent.parent / "nfse" / "backend")

# O papel da empresa no documento → o sentido do dinheiro que se espera.
_ENTRA = ("EMITENTE", "PRESTADOR")               # ela vendeu/prestou
_SAI = ("DESTINATARIO", "TOMADOR", "REMETENTE")  # ela comprou/tomou


def _modulo_backend(nome: str):
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    return __import__(nome, fromlist=["*"])


def _centavos_dec(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        return modelo.centavos(Decimal(str(v)))
    except (InvalidOperation, modelo.Invalido):
        return None


def caminho_indice(dados, empresa: str) -> Path | None:
    """Onde o índice da ingestão mora — perguntado ao próprio `ingestao`."""
    try:
        ind = _modulo_backend("ingestao.indice")
        return ind.abrir(dados, empresa).caminho(criar=False)
    except Exception:
        return None


def documentos_acervo(dados, empresa: str) -> list:
    arq = caminho_indice(dados, empresa)
    if not arq or not arq.exists():
        return []
    saida = []
    con = sqlite3.connect(arq.as_uri() + "?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        for r in con.execute(
                "SELECT id_documento, especie, chave, numero, serie, papel, "
                "emitente, emitente_nome, contraparte, contraparte_nome, "
                "dh_emissao, competencia, valor_total, situacao "
                "FROM documentos WHERE estado='INDEXADO' AND especie IN "
                "('NFE55','NFCE65','CTE57')"):
            papel = (r["papel"] or "").upper()
            if papel not in _ENTRA + _SAI:
                continue     # transportador, autXML, terceiro: não é caixa dela
            valor = _centavos_dec(r["valor_total"])
            saida.append({
                "alvo_id": "ACERVO:" + r["id_documento"],
                "fonte": "ACERVO",
                "especie": r["especie"],
                "numero": r["numero"] or "",
                "serie": r["serie"] or "",
                "chave": r["chave"] or "",
                "data": (r["dh_emissao"] or "")[:10],
                "competencia": (r["competencia"] or "")[:7],
                "valores": [v for v in (valor,) if v is not None],
                "valor": valor,
                "sentido": modelo.ENTRADA if papel in _ENTRA else modelo.SAIDA,
                "papel": papel,
                "contraparte_doc": modelo.so_digitos(r["contraparte"]),
                "contraparte_nome": r["contraparte_nome"] or "",
                "situacao": r["situacao"] or "",
                "operacao": "%s · %s" % (
                    "VENDA/SAÍDA" if papel in _ENTRA else "COMPRA/ENTRADA",
                    r["situacao"] or "situação não lida"),
            })
    finally:
        con.close()
    return saida


def documentos_nfse(dados, empresa: str) -> list:
    pasta = Path(dados) / modelo.empresa_valida(empresa)
    if not (pasta / "xmls").exists():
        return []            # sem criar pasta: quem cria é o módulo NFS-e
    try:
        core = _modulo_backend("core")
        notas = core.carregar_notas(pasta)
    except Exception:
        return []
    ident = modelo.empresa_valida(empresa)
    saida = []
    for n in notas:
        emitida = modelo.so_digitos(n.get("emit_cnpj")) == ident
        liquido = _centavos_dec(n.get("valor_liquido"))
        servico = _centavos_dec(n.get("valor_servico"))
        valores = []
        for v in (liquido, servico):
            if v is not None and v not in valores:
                valores.append(v)
        situacao = n.get("situacao") or ""
        saida.append({
            "alvo_id": "NFSE:" + (n.get("chave") or ""),
            "fonte": "NFSE",
            "especie": "NFSE",
            "numero": str(n.get("numero") or ""),
            "serie": "",
            "chave": n.get("chave") or "",
            "data": str(n.get("data_emissao") or "")[:10],
            "competencia": str(n.get("data_competencia") or "")[:7],
            "valores": valores,
            "valor": liquido if liquido is not None else servico,
            "sentido": modelo.ENTRADA if emitida else modelo.SAIDA,
            "papel": "PRESTADOR" if emitida else "TOMADOR",
            "contraparte_doc": modelo.so_digitos(
                n.get("toma_doc") if emitida else n.get("emit_cnpj")),
            "contraparte_nome": (n.get("toma_nome") if emitida
                                 else n.get("emit_nome")) or "",
            "situacao": situacao,
            "cancelada": bool(n.get("cancelada")),
            "operacao": "%s · %s" % ("SERVIÇO PRESTADO" if emitida
                                     else "SERVIÇO TOMADO", situacao or "—"),
            "arquivo": n.get("arquivo") or "",
        })
    return saida


def documentos(dados, empresa: str) -> list:
    """Todos os documentos fiscais da empresa, das duas fontes."""
    return documentos_acervo(dados, empresa) + documentos_nfse(dados, empresa)


def obter(dados, empresa: str, alvo_id: str) -> dict | None:
    for d in documentos(dados, empresa):
        if d["alvo_id"] == alvo_id:
            return d
    return None


def contexto_classificacao(docs: list, empresa: str) -> dict:
    """Quem é cliente e quem é fornecedor, segundo o próprio acervo."""
    clientes, fornecedores = {}, {}
    for d in docs:
        if d.get("cancelada") or "CANCEL" in (d.get("situacao") or "").upper():
            continue
        doc = d.get("contraparte_doc") or ""
        if len(doc) not in (11, 14):
            continue
        alvo = clientes if d["sentido"] == modelo.ENTRADA else fornecedores
        alvo.setdefault(doc, d.get("contraparte_nome") or "")
    return {"empresa": empresa, "clientes": clientes,
            "fornecedores": fornecedores}
