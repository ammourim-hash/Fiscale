"""
painel.py — Apuração de TODAS as empresas de uma vez.

A rotina do escritório é fechar o mês da carteira inteira, não de uma empresa.
Enquanto o sistema só respondia "uma empresa por consulta", o trabalho manual
continuava: abrir, escolher, esperar, anotar, repetir 10, 20, 50 vezes.

Aqui o mês inteiro sai numa chamada: DAS de cada empresa, o que está pendente e
o que dá para resolver sozinho — pronto para conferir de uma vez.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _cfg_da_empresa(cfgs: dict, cnpj: str) -> dict:
    c = cfgs.get(cnpj) or {}
    return {
        "folha12m": c.get("folha12m"),
        "fatorR": bool(c.get("fatorR")),
        "anexoServico": (c.get("anexoServico") or "").strip(),
        "issFixo": bool(c.get("issFixo")),
        "inicioAtividade": c.get("inicioAtividade") or None,
        "regime": (c.get("regime") or "simples"),
        "receitas_manuais": _manuais_do_texto(c.get("manuais")),
        "lancamentos": c.get("lancamentos") or [],
    }


def _manuais_do_texto(txt) -> dict:
    """'MM/AAAA valor' por linha -> {'AAAA-MM': float} (mesmo parser da tela)."""
    out = {}
    for linha in (txt or "").split("\n"):
        m = re.match(r"\s*(\d{2})[/-](\d{4})\s+([\d.,]+)", linha)
        if not m:
            continue
        mes, ano, val = m.groups()
        val = val.replace(".", "").replace(",", ".") if "," in val else val
        try:
            out[f"{ano}-{mes}"] = float(val)
        except ValueError:
            pass
    return out


def _uma_empresa(args) -> dict:
    cnpj, nome, pasta, cfg, pa = args
    import classificador as clsmod
    import prefeituras as prefmod
    from datetime import date
    import calendar

    ano, mes = int(pa[:4]), int(pa[5:7])
    ini, fim = f"{pa}-01", f"{pa}-{calendar.monthrange(ano, mes)[1]:02d}"
    linha = {"cnpj": cnpj, "nome": nome, "ok": False}
    try:
        r = clsmod.classificar(pasta, cnpj, cfg, ini, fim)
    except Exception as e:
        linha["erro"] = f"{e.__class__.__name__}: {e}"
        return linha

    res = r.get("resumo") or {}
    das = (r.get("das") or [{}])[0] if r.get("das") else {}
    pend = [a for a in res.get("por_anexo", []) if a["anexo"] in ("CONFIGURAR", "FATOR R")]
    faltantes = das.get("rbt12_sem_dados") or []

    # de onde os meses faltantes podem vir sozinhos
    try:
        p = prefmod.pendencias(pasta, cnpj, pa)
        cidade = (p.get("cidade") or {})
    except Exception:
        cidade = {}

    linha.update({
        "ok": True,
        "receita": res.get("total", 0.0),
        "notas": res.get("quantidade", 0),
        "rbt12": das.get("rbt12", 0.0),
        "das": das.get("das_estimado", 0.0),
        "meses_faltando": faltantes,
        "anexo_pendente": [{"anexo": a["anexo"], "valor": a["valor"]} for a in pend],
        "cidade": cidade.get("nome") or "",
        "cidade_ibge": cidade.get("ibge") or "",
        "download_disponivel": bool(cidade.get("pronto")),
        "alertas": res.get("alertas", [])[:2],
        "regime": r.get("regime") or "simples",
    })
    # o que trava a apuração desta empresa, em uma frase
    if linha["regime"] in ("presumido", "real"):
        # Fora do Simples não há DAS. Dizer isso é melhor que mostrar 0,00, que
        # se confunde com "apurado e deu zero".
        linha["pendencia"] = ("Lucro Presumido — apuração federal fora do Fiscale"
                              if linha["regime"] == "presumido"
                              else "Lucro Real — apuração federal fora do Fiscale")
    elif pend:
        linha["pendencia"] = f"Anexo por definir em R$ {sum(a['valor'] for a in pend):,.2f}"
    elif faltantes and linha["download_disponivel"]:
        linha["pendencia"] = f"{len(faltantes)} mês(es) do RBT12 — baixável em {linha['cidade']}"
    elif faltantes:
        linha["pendencia"] = f"{len(faltantes)} mês(es) do RBT12 sem fonte automática"
    else:
        linha["pendencia"] = ""
    return linha


def apurar_todas(dados_dir, registro: list, cfgs: dict, pa: str,
                 pasta_de=None, paralelo: int = 4) -> dict:
    """Apura o mês de TODAS as empresas cadastradas, em paralelo."""
    pasta_de = pasta_de or (lambda c: Path(dados_dir) / c)
    tarefas = []
    for c in registro:
        cnpj = re.sub(r"\D", "", c.get("cnpj") or "")
        if not cnpj:
            continue
        tarefas.append((cnpj, (c.get("apelido") or c.get("nome") or cnpj),
                        str(pasta_de(cnpj)), _cfg_da_empresa(cfgs, cnpj), pa))

    with ThreadPoolExecutor(max_workers=max(1, paralelo)) as ex:
        linhas = list(ex.map(_uma_empresa, tarefas))

    validas = [l for l in linhas if l.get("ok")]
    com_pendencia = [l for l in validas if l.get("pendencia")]
    baixaveis = [l for l in validas if l.get("meses_faltando") and l.get("download_disponivel")]
    linhas.sort(key=lambda l: (not l.get("pendencia"), -(l.get("das") or 0)))
    return {
        "competencia": pa,
        "empresas": linhas,
        "resumo": {
            "total_empresas": len(linhas),
            "apuradas": len(validas),
            "das_total": round(sum(l.get("das") or 0 for l in validas), 2),
            "receita_total": round(sum(l.get("receita") or 0 for l in validas), 2),
            "com_pendencia": len(com_pendencia),
            "baixaveis": len(baixaveis),
        },
    }
