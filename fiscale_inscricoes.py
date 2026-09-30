#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fiscale_inscricoes.py — quais telas guardam credencial, e onde.

POR QUE ESTE ARQUIVO EXISTE SEPARADO DO COFRE
    `fiscale_segredos` é infraestrutura: protege, mascara, migra e relata
    segredo — e não conhece tela nenhuma. Se ele trouxesse a lista embutida,
    remover uma tela arrastaria junto a proteção de segredo do sistema
    inteiro. Foi exatamente o que quase aconteceu com o Plano de Saúde: o
    `MAPA` tinha uma única entrada, e era dele.

    Aqui ficam as INSCRIÇÕES. Importar este módulo é o que as ativa. Uma tela
    que deixa de existir some daqui numa linha, e o cofre não sente.

QUEM PRECISA IMPORTAR
    Todo ponto de entrada que grava, lê ou migra `state_<mod>.json`:
    `fiscale_server` (a rota `/api/state/`), `fiscale_migracao`
    (a proteção no arranque, chamada também pelo NFS-e e pela restauração) e
    `diagnostico_instalacao`.

    Importar é idempotente e barato: `registrar()` não duplica ponto.

ESTAR VAZIO É UM ESTADO VÁLIDO
    Sem nenhuma inscrição o cofre continua íntegro e testado — só não tem o
    que proteger. `teste_seguranca` prova isso de propósito.
"""
from __future__ import annotations

import fiscale_segredos as _seg


def aplicar() -> None:
    """Inscreve as telas vigentes. Chamado na importação, e seguro repetir."""
    # Nenhuma tela guarda credencial no momento. O cofre continua de pé:
    # `teste_seguranca` prova o comportamento com este registro vazio, e uma
    # tela nova entra aqui numa linha.
    return


aplicar()
