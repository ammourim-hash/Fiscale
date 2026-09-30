# -*- coding: utf-8 -*-
"""armazenamento.py — o arquivo de extrato, exatamente como o banco entregou.

A MESMA PROMESSA DA SITUAÇÃO FISCAL, E A MESMA IMPLEMENTAÇÃO
    Os bytes são gravados como vieram e jamais reescritos. O caminho é o hash
    (nunca o nome do arquivo nem a conta lida dele: caminho que depende de
    leitor muda de lugar quando o leitor evolui). A gravação é atômica e
    RELIDA do disco antes de valer.

    A gravação atômica e o nome pelo hash vêm de `situacao.armazenamento`, e
    não de uma cópia: são a mesma regra, e regra escrita em dois lugares
    envelhece em um só.

LAYOUT
    <dados>/<empresa>/contabil/extratos/<id[:2]>/<id>/
        original.<ofx|csv|pdf|txt>   os bytes exatos
        captura.json                 nome recebido, hash inteiro, quem, quando
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from situacao.armazenamento import (TAMANHO_ID, _escrever_atomico,
                                    identificar)

from . import modelo
from .base import agora, pasta

PASTA = "extratos"
CAPTURA = "captura.json"
EXTENSOES = ("ofx", "csv", "pdf", "txt")


class ErroArmazenamento(Exception):
    pass


def _base(dados, empresa) -> Path:
    return pasta(dados, empresa) / PASTA


def _dir(dados, empresa, arquivo_id: str) -> Path:
    if len(arquivo_id or "") != TAMANHO_ID or any(
            c not in "0123456789abcdef" for c in arquivo_id):
        raise modelo.Invalido("identificador de arquivo fora de formato")
    return _base(dados, empresa) / arquivo_id[:2] / arquivo_id


def guardar(dados, empresa, bruto: bytes, extensao: str, nome_recebido="",
            usuario="") -> dict:
    ext = (extensao or "").lower()
    if ext not in EXTENSOES:
        raise modelo.Invalido("extensão não aceita: %r" % extensao)
    if not bruto:
        raise modelo.Invalido("arquivo vazio")
    arquivo_id, sha = identificar(bruto)
    destino = _dir(dados, empresa, arquivo_id)
    alvo = destino / ("original." + ext)
    if alvo.exists():
        if hashlib.sha256(alvo.read_bytes()).hexdigest() == sha:
            return {"arquivo_id": arquivo_id, "sha256": sha, "novo": False,
                    "caminho": str(alvo)}
        raise ErroArmazenamento("colisão de hash em %s" % destino)
    destino.mkdir(parents=True, exist_ok=True)
    _escrever_atomico(alvo, bruto)
    if hashlib.sha256(alvo.read_bytes()).hexdigest() != sha:
        try:
            alvo.unlink()
        except OSError:
            pass
        raise ErroArmazenamento("o disco não devolveu o que foi gravado")
    _escrever_atomico(destino / CAPTURA, json.dumps({
        "empresa": modelo.empresa_valida(empresa), "sha256": sha,
        "bytes": len(bruto), "extensao": ext,
        "nome_recebido": str(nome_recebido or "")[:200],
        "usuario": usuario or "", "capturado_utc": agora(),
    }, ensure_ascii=False, indent=2).encode("utf-8"))
    return {"arquivo_id": arquivo_id, "sha256": sha, "novo": True,
            "caminho": str(alvo)}


def localizar(dados, empresa, arquivo_id: str) -> Path | None:
    d = _dir(dados, empresa, arquivo_id)
    for ext in EXTENSOES:
        p = d / ("original." + ext)
        if p.exists():
            return p
    return None


def ler(dados, empresa, arquivo_id: str) -> bytes:
    p = localizar(dados, empresa, arquivo_id)
    if not p:
        raise FileNotFoundError(arquivo_id)
    return p.read_bytes()


def conferir_integridade(dados, empresa, arquivo_id: str,
                         sha_esperado: str) -> bool:
    """Confere contra o hash INTEIRO registrado, não contra o nome da pasta."""
    try:
        return hashlib.sha256(ler(dados, empresa, arquivo_id)).hexdigest() \
            == sha_esperado
    except OSError:
        return False
