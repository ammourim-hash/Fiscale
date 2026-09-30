"""armazenamento.py — onde os extratos e os arquivos originais ficam.

Segue o padrão que já funciona no Fiscale: JSON por empresa dentro de
`DADOS/<cnpj>/`, sem banco. O volume esperado é da ordem de dezenas de vidas
por competência por empresa; SQLite entra quando doer, não antes.

    <DADOS>/<cnpj>/saude/
        extratos.json                 índice de tudo que foi importado
        originais/<hash8>-<nome>.pdf  o arquivo como veio (nunca alterado)

O ARQUIVO ORIGINAL é guardado sempre, antes de qualquer parse. É ele que
permite reprocessar quando um parser for corrigido — e é ele, não o nosso
parse, que vale como prova diante da operadora.

Reprocessar NÃO apaga o resultado anterior: o extrato antigo vai para
`historico[]` com o parserVersao que o produziu.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

ARQ_INDICE = "extratos.json"


def pasta_saude(pasta_cnpj) -> Path:
    p = Path(pasta_cnpj) / "saude"
    p.mkdir(parents=True, exist_ok=True)
    return p


def hash_arquivo(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _nome_seguro(nome: str) -> str:
    n = re.sub(r"[^0-9A-Za-zÀ-ÿ._\- ]", "_", nome or "arquivo")
    return n[:120] or "arquivo"


def guardar_original(pasta_cnpj, nome: str, dados: bytes) -> tuple[str, str]:
    """Grava o arquivo como veio. Devolve (hash, caminho).

    Mesmo conteúdo já guardado = mesmo caminho, sem reescrever: o hash é a
    identidade do arquivo e reescrever só arriscaria corromper o original."""
    destino = pasta_saude(pasta_cnpj) / "originais"
    destino.mkdir(parents=True, exist_ok=True)
    h = hash_arquivo(dados)
    alvo = destino / f"{h[:8]}-{_nome_seguro(nome)}"
    if not alvo.exists():
        alvo.write_bytes(dados)
    return h, str(alvo)


def _caminho_indice(pasta_cnpj) -> Path:
    return pasta_saude(pasta_cnpj) / ARQ_INDICE


def carregar(pasta_cnpj) -> dict:
    p = _caminho_indice(pasta_cnpj)
    if not p.exists():
        return {"versao": 1, "extratos": []}
    try:
        d = json.loads(p.read_text("utf-8"))
    except Exception:
        # índice corrompido não pode derrubar o módulo; preserva e recomeça
        try:
            p.replace(p.with_suffix(".json.corrompido"))
        except Exception:
            pass
        return {"versao": 1, "extratos": []}
    d.setdefault("extratos", [])
    return d


def salvar(pasta_cnpj, base: dict) -> None:
    p = _caminho_indice(pasta_cnpj)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)                      # troca atômica: nunca um índice pela metade


def achar(base: dict, extrato_id: str) -> dict | None:
    return next((e for e in base["extratos"] if e.get("id") == extrato_id), None)


def duplicado(base: dict, extrato_id: str) -> bool:
    return achar(base, extrato_id) is not None


def gravar_extrato(pasta_cnpj, dados: dict, substituir=False) -> str:
    """Grava um extrato. Devolve 'novo' | 'duplicado' | 'reprocessado'.

    Sem `substituir`, reimportar o mesmo arquivo no mesmo contexto é ignorado —
    é isso que impede lançamento em dobro quando o usuário arrasta a mesma
    pasta duas vezes."""
    base = carregar(pasta_cnpj)
    ja = achar(base, dados["id"])
    if ja is None:
        dados["importado_em"] = dados.get("importado_em") or agora()
        base["extratos"].append(dados)
        salvar(pasta_cnpj, base)
        return "novo"
    if not substituir:
        return "duplicado"
    historico = ja.pop("historico", [])
    historico.append({k: ja.get(k) for k in
                      ("parser", "parser_versao", "importado_em", "status",
                       "total_individualizado", "conferencia")})
    dados["historico"] = historico
    dados["importado_em"] = agora()
    dados["importado_primeiro_em"] = ja.get("importado_primeiro_em") or ja.get("importado_em")
    base["extratos"] = [dados if e.get("id") == dados["id"] else e
                        for e in base["extratos"]]
    salvar(pasta_cnpj, base)
    return "reprocessado"


def agora() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")
