#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fiscale_segredos.py — segredo de tela nunca fica em texto claro no disco.

O PROBLEMA QUE ISTO RESOLVE
    As telas guardam o que precisam pela rota genérica `/api/state/<mod>`: o
    navegador manda um JSON, o servidor grava. Serve bem para preferência de
    tela — e serve mal para credencial de portal, que ficaria legível em
    `state_<mod>.json`, num arquivo que o backup para o Google Drive copiava.

ESTE MÓDULO NÃO PERTENCE A MÓDULO FUNCIONAL NENHUM
    O cofre é infraestrutura: existe, é testado e continua valendo mesmo que
    NENHUMA tela declare segredo. Quem tem credencial se INSCREVE, chamando
    `registrar(...)` — e some da inscrição quando deixa de existir, sem levar o
    cofre junto. A primeira versão trazia o plano de saúde embutido no `MAPA`,
    e remover aquele módulo teria arrastado a proteção de segredo inteira.

COMO FICA
    Ao GRAVAR, o servidor separa o que é segredo, protege com DPAPI e guarda
    num campo irmão:

        "acesso":           {"Código da empresa": "8PXXV", "Usuário": "MASTER"}
        "acesso_protegido": {"Senha": "dpapi:AQAAANCM..."}

    Ao LER, o segredo **não vai para o navegador**. Vai só a situação:

        "acesso_estado":    {"Senha": "ok"}        ou "aguardando"

    Assim a tela sabe mostrar "Aguardando senha do portal" sem nunca ter tido a
    senha em mãos — nem no HTML, nem no console, nem no cache do navegador.

O QUE ISSO **NÃO** É
    DPAPI é da máquina e da conta do Windows. Isto NÃO torna a senha portável:
    depois de restaurar um backup em outro computador, ela volta a estar
    "aguardando", e é isso mesmo que se quer. O `.fbk` não leva nem a senha nem
    nada que a reconstrua.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# `seguranca` (DPAPI) mora no backend do NFS-e; é o mesmo cofre já usado para a
# senha do certificado, e não faz sentido ter dois.
_BACKEND = Path(__file__).resolve().parent / "nfse" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
import seguranca  # noqa: E402

# Nomes de campo que carregam segredo. Bate com "Senha", "senha do portal",
# "password", "token", "chave", "secret" — em qualquer caixa e com acento.
_E_SEGREDO = re.compile(r"senha|password|token|secret|chave|api[_\- ]?key", re.I)

# Onde procurar, por módulo de tela: (lista, campo em claro, campo protegido).
#
# Nasce VAZIO de propósito. Uma tela com credencial se inscreve chamando
# `registrar()` no seu próprio módulo — assim a inscrição vive junto de quem a
# usa, e remover a tela remove a inscrição sem tocar no cofre.
MAPA: dict[str, list[tuple[str, str, str]]] = {}


def registrar(mod: str, lista: str, claro: str, protegido: str) -> None:
    """Declara onde o módulo `mod` guarda credencial.

    `lista`     nome da lista de itens dentro do `state_<mod>.json`
    `claro`     dicionário do item onde os campos chegam preenchidos
    `protegido` dicionário irmão onde o valor protegido é guardado

    Idempotente: registrar duas vezes o mesmo ponto não o duplica.
    """
    ponto = (str(lista), str(claro), str(protegido))
    pontos = MAPA.setdefault(str(mod), [])
    if ponto not in pontos:
        pontos.append(ponto)


def esquecer(mod: str) -> None:
    """Tira a inscrição de um módulo. Usado quando a tela deixa de existir."""
    MAPA.pop(str(mod), None)


def e_segredo(nome: str) -> bool:
    return bool(_E_SEGREDO.search(str(nome or "")))


def _blocos(mod: str, dados: dict):
    """Percorre os pontos onde um módulo guarda segredo."""
    if not isinstance(dados, dict):
        return
    for lista, claro, protegido in MAPA.get(mod, []):
        itens = dados.get(lista)
        if not isinstance(itens, list):
            continue
        for item in itens:
            if isinstance(item, dict):
                yield item, claro, protegido


# ── gravar ──────────────────────────────────────────────────────────────────
def proteger_para_gravar(mod: str, dados: dict, dados_dir) -> tuple[dict, dict]:
    """Devolve (dados_a_gravar, relatório).

    Regras que valem a pena declarar:

    • Campo secreto vindo PREENCHIDO é protegido e sai do claro.
    • Campo secreto vindo VAZIO **não apaga** o que já estava protegido: a tela
      manda o formulário inteiro a cada salvamento, e o navegador nunca recebe
      a senha de volta — sem esta regra, salvar qualquer outra coisa na tela
      apagaria a senha guardada.
    • Se a proteção falhar (DPAPI indisponível), o segredo é DESCARTADO, nunca
      gravado em claro. Perder a senha é recuperável; vazá-la, não.
    """
    rel = {"protegidos": 0, "mantidos": 0, "descartados": 0}
    if not isinstance(dados, dict):
        return dados, rel

    for item, claro, protegido in _blocos(mod, dados):
        acesso = item.get(claro)
        if not isinstance(acesso, dict):
            continue
        cofre = dict(item.get(protegido) or {})
        for chave in list(acesso):
            if not e_segredo(chave):
                continue
            valor = acesso.pop(chave)          # sai do claro em qualquer caso
            if not valor:
                rel["mantidos"] += 1           # vazio: preserva o que já havia
                continue
            try:
                cofre[chave] = seguranca.proteger(str(valor), dados_dir)
                rel["protegidos"] += 1
            except Exception:
                cofre.pop(chave, None)
                rel["descartados"] += 1
        if cofre:
            item[protegido] = cofre
        else:
            item.pop(protegido, None)
    return dados, rel


def preservar_protegidos(mod: str, novos: dict, anteriores: dict) -> dict:
    """Traz de volta o cofre que já estava gravado.

    O navegador nunca recebe o segredo, então nunca o devolve. Sem reencaixar o
    cofre anterior aqui, qualquer salvamento da tela apagaria a senha guardada —
    o usuário mexeria numa data e perderia o acesso ao portal."""
    if not isinstance(novos, dict) or not isinstance(anteriores, dict):
        return novos
    for lista, claro, protegido in MAPA.get(mod, []):
        antes = {str(i.get("id")): i for i in (anteriores.get(lista) or [])
                 if isinstance(i, dict)}
        for item in (novos.get(lista) or []):
            if not isinstance(item, dict):
                continue
            velho = antes.get(str(item.get("id")))
            if not velho:
                continue
            cofre_velho = velho.get(protegido)
            if not isinstance(cofre_velho, dict):
                continue
            cofre = dict(cofre_velho)
            cofre.update(item.get(protegido) or {})
            item[protegido] = cofre
    return novos


# ── ler ─────────────────────────────────────────────────────────────────────
def mascarar_para_enviar(mod: str, dados: dict, dados_dir) -> dict:
    """Tira o segredo antes de o JSON ir para o navegador, deixando a situação.

    `ok` = a senha está guardada e abre nesta máquina.
    `aguardando` = falta informar aqui (instalação nova, ou restaurada de outro
    computador, onde o DPAPI de origem não abre)."""
    if not isinstance(dados, dict):
        return dados
    for item, claro, protegido in _blocos(mod, dados):
        cofre = item.get(protegido)
        item.pop(protegido, None)
        if not isinstance(cofre, dict):
            continue
        estado = {}
        for chave, blob in cofre.items():
            try:
                seguranca.desproteger(blob, dados_dir)
                estado[chave] = "ok"
            except Exception:
                estado[chave] = "aguardando"
        if estado:
            item[claro + "_estado"] = estado
    return dados


def pendencias(mod: str, dados: dict, dados_dir) -> list[dict]:
    """Quem está aguardando senha. É o que a tela lista depois de restaurar."""
    fora = []
    if not isinstance(dados, dict):
        return fora
    for lista, claro, protegido in MAPA.get(mod, []):
        for item in (dados.get(lista) or []):
            if not isinstance(item, dict):
                continue
            cofre = item.get(protegido) or {}
            campos_esperados = [c for c in (item.get(claro + "_campos") or []) if e_segredo(c)]
            faltando = []
            for chave in (list(cofre) or campos_esperados or ["Senha"]):
                blob = cofre.get(chave)
                if not blob:
                    faltando.append(chave)
                    continue
                try:
                    seguranca.desproteger(blob, dados_dir)
                except Exception:
                    faltando.append(chave)
            if faltando and (cofre or item.get(claro)):
                # `rotulo` é livre: cada tela escolhe o que identifica o item
                # para quem vai reinformar a senha. Antes este campo se chamava
                # `operadora` e só fazia sentido para uma tela.
                fora.append({"id": item.get("id"), "nome": item.get("nome"),
                             "rotulo": item.get("rotulo") or item.get("operadora"),
                             "campos": faltando})
    return fora


def informar(mod: str, dados: dict, alvo_id, chave: str, valor: str, dados_dir) -> bool:
    """Grava a senha de UM item, sem tocar no resto da configuração."""
    if not valor:
        return False
    for item, claro, protegido in _blocos(mod, dados):
        if str(item.get("id")) != str(alvo_id):
            continue
        cofre = dict(item.get(protegido) or {})
        cofre[chave] = seguranca.proteger(str(valor), dados_dir)
        # Confere na hora, e confere o VALOR, não só se abre: senha guardada que
        # não abre é pior do que não ter, e senha que abre devolvendo outra coisa
        # é pior ainda — o sistema diria "ok" e o portal recusaria o login sem
        # ninguém entender por quê. Mesma regra da migração.
        if seguranca.desproteger(cofre[chave], dados_dir) != str(valor):
            raise ValueError("ida e volta não confere")
        item[protegido] = cofre
        acesso = item.get(claro)
        if isinstance(acesso, dict):
            acesso.pop(chave, None)
        return True
    return False


# ── migração do formato antigo (texto claro) ────────────────────────────────
def migrar_texto_claro(mod: str, dados: dict, dados_dir) -> dict:
    """Converte segredo em claro para protegido.

    **Nunca apaga o claro antes de confirmar** que o protegido abre e devolve
    exatamente o mesmo valor. Se a conferência falhar, o claro fica onde está e
    a migração reporta — é melhor um segredo mal guardado do que um segredo
    perdido sem aviso."""
    rel = {"migrados": 0, "falharam": 0, "ja_protegidos": 0}
    if not isinstance(dados, dict):
        return rel
    for item, claro, protegido in _blocos(mod, dados):
        acesso = item.get(claro)
        if not isinstance(acesso, dict):
            continue
        cofre = dict(item.get(protegido) or {})
        for chave in list(acesso):
            if not e_segredo(chave):
                continue
            valor = acesso.get(chave)
            if not valor:
                acesso.pop(chave, None)
                continue
            if chave in cofre:
                acesso.pop(chave, None)
                rel["ja_protegidos"] += 1
                continue
            try:
                blob = seguranca.proteger(str(valor), dados_dir)
                if seguranca.desproteger(blob, dados_dir) != str(valor):
                    raise ValueError("ida e volta não confere")
                cofre[chave] = blob
                acesso.pop(chave, None)          # só agora o claro some
                rel["migrados"] += 1
            except Exception:
                rel["falharam"] += 1             # o claro FICA, e o relatório avisa
        if cofre:
            item[protegido] = cofre
    return rel
