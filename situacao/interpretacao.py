# -*- coding: utf-8 -*-
"""interpretacao.py — o que o FISCALE conclui de um documento, separado dele.

POR QUE EXISTE UMA TERCEIRA CAMADA
    O documento é do órgão. A tentativa é o fato de ter ido buscar. Falta a
    CONCLUSÃO: "este extrato mostra 3 débitos, R$ 1.240,00". Para o SITFIS o
    leitor conclui parte disso sozinho; para o extrato do Recife, que nenhum
    leitor lê (ver FISCALE_EXTRATOS_REAIS.md, seção 7.4), só uma pessoa que
    abriu o PDF pode concluir.

    Essa conclusão não pode morar no documento (ele é imutável) nem no índice
    sozinha (ele é cache, e se reconstrói do disco). Então ela é um ARQUIVO,
    ao lado do original, e o índice é só o espelho:

        <pasta do documento>/
            original.pdf                     os bytes do órgão — intocados
            captura.json                     proveniência
            interpretacoes/
                20260912T183412123Z-3f9a.json    uma conclusão, de alguém

A CONCLUSÃO NUNCA É EDITADA, SÓ SUCEDIDA
    Reclassificar grava um arquivo NOVO; o antigo fica. "Em agosto a Aline
    marcou regular; em setembro o Pedro corrigiu para pendência" é histórico
    que uma fiscalização pode pedir, e sobrescrever apagaria justamente a
    pista de que houve correção.

ELA SÓ EXISTE PARA DOCUMENTO QUE EXISTE E CONFERE
    Classificar um documento cujo arquivo não bate com o hash seria afirmar
    algo sobre um PDF que não é mais o que o órgão entregou.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from . import armazenamento, modelo, regularidade

PASTA = "interpretacoes"
LIMITE_PENDENCIAS = 200
LIMITE_TEXTO = 500


class Recusada(ValueError):
    """A classificação não entra. A mensagem é para a tela."""


def _agora():
    d = datetime.now(timezone.utc)
    return (d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            d.strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z")


def _numero(v, inteiro=False):
    """Número ou `None`. Aceita "1.240,50" do jeito que se digita no Brasil."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = v
    else:
        s = str(v).strip().replace("R$", "").replace(" ", "")
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        try:
            n = float(s)
        except ValueError:
            raise Recusada("valor não numérico: %r" % (v,))
    if n < 0:
        raise Recusada("valor negativo não faz sentido aqui: %r" % (v,))
    return int(n) if inteiro else round(float(n), 2)


def _pendencias(lista) -> list:
    """Cada pendência com os campos que o extrato costuma trazer. Texto livre
    fica curto; nada aqui é interpretado como regra."""
    saida = []
    for p in list(lista or [])[:LIMITE_PENDENCIAS]:
        if not isinstance(p, dict):
            continue
        item = {}
        for campo in ("tributo", "competencia", "vencimento", "situacao",
                      "cda", "parcelamento", "descricao"):
            if p.get(campo):
                item[campo] = str(p[campo])[:120]
        for campo in ("principal", "encargos", "total"):
            if p.get(campo) not in (None, ""):
                item[campo] = _numero(p[campo])
        if item:
            saida.append(item)
    return saida


def registrar(raiz, indice, identidade: str, esfera: str, tipo: str,
              documento_id: str, status: str, usuario: str,
              quantidade_pendencias=None, valor_total=None, validade: str = "",
              pendencias=None, observacao: str = "") -> dict:
    """Grava a conclusão no disco, espelha no índice, e devolve o registro."""
    try:
        e, t = modelo.conferir(esfera, tipo)
    except modelo.Invalido as erro:
        raise Recusada(str(erro))
    if status not in regularidade.CLASSIFICAVEIS:
        raise Recusada("classificação desconhecida: %r (esperado %s)"
                       % (status, "/".join(regularidade.CLASSIFICAVEIS)))
    if not str(usuario or "").strip():
        # Conclusão sem autor é palpite anônimo — e é exatamente o que uma
        # auditoria vai perguntar primeiro.
        raise Recusada("a classificação precisa de um usuário identificado")

    armazem = armazenamento.abrir(raiz, identidade)
    try:
        pasta = armazem.pasta(e, t, documento_id)
    except modelo.Invalido as erro:
        raise Recusada(str(erro))
    if not armazem.existe(e, t, documento_id):
        raise Recusada("documento não encontrado")
    if not armazem.conferir_integridade(e, t, documento_id):
        raise Recusada("o arquivo no disco não confere com o hash registrado; "
                       "nada foi classificado")

    qtd = _numero(quantidade_pendencias, inteiro=True)
    valor = _numero(valor_total)
    pend = _pendencias(pendencias)
    if status == regularidade.REGULAR and ((qtd or 0) > 0 or pend):
        raise Recusada("regular com pendências listadas é contraditório — "
                       "confira a classificação")
    if validade:
        try:
            validade = datetime.strptime(str(validade)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise Recusada("validade fora do formato AAAA-MM-DD: %r" % validade)

    quando, carimbo = _agora()
    reg = {
        "id": "%s-%s" % (carimbo, secrets.token_hex(3)),
        "identidade": armazem.identidade, "esfera": e, "tipo": t,
        "documento_id": documento_id,
        "sha256_documento": armazem.captura(e, t, documento_id).get("sha256", ""),
        "status": status,
        "quantidade_pendencias": qtd if qtd is not None else (len(pend) or None),
        "valor_total": valor,
        "validade": validade or "",
        "pendencias": pend,
        "observacao": str(observacao or "")[:LIMITE_TEXTO],
        "usuario": str(usuario)[:80],
        "quando_utc": quando,
    }
    destino = pasta / PASTA
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino / (reg["id"] + ".json")
    # "x": nunca sobrescreve. O id tem carimbo em milissegundos e sufixo
    # aleatório; se mesmo assim colidir, é para falhar alto.
    with open(arq, "x", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    indice.registrar_interpretacao(reg)
    return reg


def reconstruir(raiz, indice, identidades) -> int:
    """Repõe no índice as conclusões que estão no disco. Não apaga nada."""
    n = 0
    for ident in identidades:
        base = Path(raiz) / "".join(c for c in str(ident) if c.isdigit()) / \
            armazenamento.PASTA
        if not base.exists():
            continue
        for arq in sorted(base.rglob(PASTA + "/*.json")):
            try:
                reg = json.loads(arq.read_text(encoding="utf-8"))
                indice.registrar_interpretacao(reg)
                n += 1
            except (OSError, ValueError, KeyError):
                continue
    return n
