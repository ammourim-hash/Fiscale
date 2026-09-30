#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Onde ficam, de verdade, as pastas conhecidas do usuário.

    from pastas_windows import area_de_trabalho, conhecidas

    area_de_trabalho()   # Path — a Área de Trabalho que a pessoa VÊ
    conhecidas()         # [(rótulo, Path), …] para um seletor de pastas

POR QUE ISTO EXISTE
    `Path.home() / "Desktop"` MENTE quando o OneDrive assume as pastas
    conhecidas — e ele assume por padrão no Windows 11.

    Numa máquina do escritório a Área de Trabalho de verdade estava em
    `C:\\Users\\<user>\\OneDrive\\Área de Trabalho`, enquanto
    `C:\\Users\\<user>\\Desktop` continuava existindo. E o pior: com dois
    arquivos dentro. Então "a pasta existe" nunca foi prova de "é a certa" —
    o palpite acertava o caminho e errava o lugar.

    Isso apareceu duas vezes, de formas diferentes:

      1. O seletor de pastas oferecia "Área de Trabalho" e levava a pessoa
         para um lugar que não era o que ela estava vendo na tela.
      2. Os backups `.fbk` eram gravados lá. Dois backups de 75 e 80 MB
         ficaram meses invisíveis para a dona deles — e backup que não se
         acha é backup que não existe.

    Quem sabe a verdade é o registro do Windows (`User Shell Folders`), onde
    ele anota para onde cada pasta conhecida foi movida. O nome em português
    não serve de palpite: a chave é `Desktop` e `Personal` mesmo num Windows
    em português, e Downloads é identificado por GUID.

    Sem registro (outro sistema, chave ausente), cai no palpite antigo — que
    continua certo para quem não usa OneDrive.
"""
from __future__ import annotations

import os
from pathlib import Path

# (rótulo para a tela, chave no registro, palpite dentro de ~)
PASTAS = (
    ("Downloads", "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
    ("Área de Trabalho", "Desktop", "Desktop"),
    ("Documentos", "Personal", "Documents"),
)

CHAVE_SHELL = (r"Software\Microsoft\Windows\CurrentVersion"
               r"\Explorer\User Shell Folders")


def _do_registro() -> dict:
    """O que o Windows anotou. Dicionário vazio fora do Windows."""
    achado: dict = {}
    if os.name != "nt":
        return achado
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_SHELL) as k:
            for _rotulo, chave, _queda in PASTAS:
                try:
                    valor, _tipo = winreg.QueryValueEx(k, chave)
                    achado[chave] = os.path.expandvars(valor)
                except OSError:
                    pass
    except Exception:
        pass
    return achado


def _resolver(chave: str, queda: str) -> Path:
    anotado = _do_registro().get(chave, "")
    alvo = Path(anotado) if anotado else Path.home() / queda
    return alvo if alvo.is_dir() else Path.home() / queda


def area_de_trabalho() -> Path:
    """A Área de Trabalho que a pessoa vê — não a pasta homônima esquecida."""
    return _resolver("Desktop", "Desktop")


def documentos() -> Path:
    return _resolver("Personal", "Documents")


def downloads() -> Path:
    return _resolver(PASTAS[0][1], "Downloads")


def conhecidas() -> list:
    """Os pontos de partida de um seletor de pastas, na ordem em que aparecem.

    Uma consulta ao registro só, e não uma por pasta.
    """
    anotado = _do_registro()
    saida = []
    for rotulo, chave, queda in PASTAS:
        bruto = anotado.get(chave, "")
        alvo = Path(bruto) if bruto else Path.home() / queda
        if not alvo.is_dir():
            alvo = Path.home() / queda
        saida.append((rotulo, alvo))
    saida.append(("Pasta do usuário", Path.home()))
    return saida
