"""
ncm_publico.py — Consulta a descrição de um NCM (Nomenclatura Comum do Mercosul).

Fonte: dados públicos da NCM via BrasilAPI (mesma casa que já usamos para CNPJ).
Serve à Consulta por NCM do módulo CClassTrib: o usuário digita um código ou uma
descrição do produto e recebe o(s) NCM(s) com a descrição oficial.

IMPORTANTE: o NCM NÃO define sozinho o cClassTrib (isso depende da OPERAÇÃO). Por
isso a tela só mostra a descrição do NCM e orienta a escolher o cClassTrib na aba
própria — é o mesmo limite que os sistemas do mercado enfrentam.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

_BASE = "https://brasilapi.com.br/api/ncm/v1"
_CACHE: dict[str, tuple[float, dict]] = {}
_VALIDADE = 24 * 3600  # a tabela NCM muda muito raramente


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Fiscale/1.0"})
    with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def consultar(termo: str) -> dict:
    """Busca NCM por código (ex.: 3004.90.99) ou por texto (ex.: 'medicamento').
    Devolve {'ok': True, 'itens': [{codigo, descricao}]} — nunca levanta exceção."""
    termo = (termo or "").strip()
    if len(termo) < 2:
        return {"ok": False, "erro": "Digite ao menos 2 caracteres (um código NCM ou uma descrição)."}

    cache = _CACHE.get(termo.lower())
    if cache and (time.time() - cache[0]) < _VALIDADE:
        return {**cache[1], "do_cache": True}

    parece_codigo = re.fullmatch(r"[\d.]{2,10}", termo) is not None
    itens: list[dict] = []
    try:
        if parece_codigo:
            # tenta o código exato primeiro (aceita "30049099" ou "3004.90.99")
            for cand in {termo, _formatar(termo)}:
                try:
                    d = _get(f"{_BASE}/{urllib.parse.quote(cand)}")
                    if isinstance(d, dict) and d.get("codigo"):
                        itens = [{"codigo": d["codigo"], "descricao": d.get("descricao", "")}]
                        break
                except urllib.error.HTTPError:
                    continue
        if not itens:
            # A busca da BrasilAPI casa SUBSTRING de UMA palavra — frases inteiras
            # ("cadeira de rodas") não batem. Tenta a frase e, se vazio, as maiores
            # palavras (ignorando conectivos), pegando o primeiro termo que retorna.
            tentativas = [termo]
            palavras = sorted(
                [p for p in re.split(r"\s+", termo)
                 if len(p) >= 4 and p.lower() not in ("para", "pelo", "pela", "dos", "das", "com", "sem")],
                key=len, reverse=True)
            tentativas += palavras
            for t in tentativas:
                lista = _get(f"{_BASE}?search={urllib.parse.quote(t)}")
                if lista:
                    itens = [{"codigo": x.get("codigo"), "descricao": x.get("descricao", "")}
                             for x in lista if x.get("codigo")][:80]
                    break
    except Exception as e:
        return {"ok": False, "erro": f"Não foi possível consultar o NCM agora ({e.__class__.__name__})."}

    out = {"ok": True, "itens": itens}
    _CACHE[termo.lower()] = (time.time(), out)
    return out


def _formatar(v: str) -> str:
    """'30049099' -> '3004.90.99' (8 dígitos no formato NCM)."""
    d = re.sub(r"\D", "", v)
    if len(d) == 8:
        return f"{d[:4]}.{d[4:6]}.{d[6:8]}"
    return v
