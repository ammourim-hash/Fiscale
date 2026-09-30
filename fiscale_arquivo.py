#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fiscale — gravar JSON sem deixar arquivo pela metade.

    from fiscale_arquivo import gravar_json_atomico, trava_de_escrita

POR QUE ISTO SAIU DO `fiscale_server`
    A regra nasceu lá, para os `state_*.json` das telas. Depois o cadastro de
    usuários passou a precisar da MESMA coisa — e copiar não servia: a
    repetição do `os.replace` abaixo é sutil, e duas cópias divergem no dia em
    que uma delas for ajustada.

    `fiscale_server` continua expondo os dois nomes, então nada que já chamava
    de lá precisou mudar.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time

_TRAVAS: dict[str, threading.Lock] = {}
_GUARDA = threading.Lock()


def trava_de_escrita(caminho):
    """Uma trava por arquivo, para os escritores não brigarem entre si.

    O servidor é `ThreadingMixIn`: cada requisição é uma thread, e duas
    pessoas salvando a mesma tela caem aqui ao mesmo tempo. Sem isto, os
    `os.replace` concorrem entre si além de concorrerem com os leitores — e no
    Windows cada um deles pode ser negado.

    Trava de processo, não de máquina, e é o suficiente **por causa da
    arquitetura escolhida**: o escritório inteiro usa um servidor só. Se um dia
    voltar a existir mais de um processo sobre a mesma pasta, isto deixa de
    bastar — e é por isso que está escrito aqui.
    """
    chave = os.path.normcase(os.path.abspath(caminho))
    with _GUARDA:
        trava = _TRAVAS.get(chave)
        if trava is None:
            trava = _TRAVAS[chave] = threading.Lock()
    return trava


def gravar_json_atomico(destino, dados):
    """Grava JSON sem deixar arquivo pela metade.

    Com o escritório inteiro no mesmo servidor, gravações do mesmo arquivo se
    cruzam com leituras o tempo todo. Escrever direto no destino deixa uma
    janela em que ele está truncado — e quem lesse ali levaria o cadastro
    vazio. Escreve ao lado e troca.

    Mesmo desenho de `ingestao/checkpoint.py:salvar()`: `tempfile` na MESMA
    pasta (entre volumes o `os.replace` deixa de ser atômico), `flush` +
    `fsync`, e só então a troca.

    A REPETIÇÃO NO REPLACE É DO WINDOWS, E NÃO É ENFEITE
        Lá o `os.replace` precisa de acesso de exclusão ao destino, e **falha
        com WinError 32 enquanto qualquer um estiver lendo o arquivo**. Não é
        hipótese: com 8 gravações simultâneas e um leitor em laço, o teste
        `teste_estado_concorrente.py` derrubou a primeira versão disto na hora.

        Meio segundo de tentativas cobre a leitura de um arquivo pequeno, que
        é de milissegundos. Passou disso, o erro sobe: falhar dizendo é melhor
        que fingir que salvou.
    """
    destino = str(destino)
    pasta = os.path.dirname(destino) or "."
    with trava_de_escrita(destino):
        fd, tmp = tempfile.mkstemp(dir=pasta, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            ultimo = None
            limite = time.time() + 2.0
            while True:
                try:
                    os.replace(tmp, destino)
                    tmp = None      # trocou: o finally não deve apagar nada
                    return
                except PermissionError as e:
                    ultimo = e      # alguém está lendo; já já solta
                    if time.time() >= limite:
                        raise ultimo
                    time.sleep(0.01)
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)  # nunca deixa lixo se falhou
                except OSError:
                    pass
