#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fiscale — o que muda quando a requisição chega por um proxy HTTPS.

    import fiscale_proxy as fp
    fp.via_proxy(ip_do_soquete, cabecalhos)      # passou por um proxy?
    fp.origem(ip_do_soquete, cabecalhos)         # IP de quem pediu
    fp.via_https(ip_do_soquete, cabecalhos)      # o navegador está em HTTPS?

O PROBLEMA (portal, Fase 3 — 13/09/2026)
    O acesso por `https://…` passa por um proxy reverso NESTA máquina, que
    fala com o FISCALE em 127.0.0.1:8777. Para o servidor, então, TODA
    requisição de fora passaria a vir de 127.0.0.1 — e três coisas quebram
    em silêncio:

      1. `_eh_local()` diria "está no PC" para quem está em casa: criar o
         primeiro admin, restaurar backup e ENCERRAR o servidor ficariam ao
         alcance de qualquer um que passasse pelo proxy;
      2. o limite de tentativas por IP juntaria o escritório inteiro num
         contador só — 30 senhas erradas de uma pessoa bloqueariam todas;
      3. a auditoria registraria 127.0.0.1 para todo mundo.

A REGRA
    Cabeçalho de encaminhamento só vale quando o SOQUETE é da própria
    máquina. Quem está em outra máquina não alcança 127.0.0.1, então não
    consegue escolher o que estes cabeçalhos dizem — o princípio de "o IP vem
    do soquete" continua de pé: o soquete é que diz se o cabeçalho é crível.

    E o erro é para o lado seguro: requisição de loopback que TRAZ cabeçalho
    de encaminhamento nunca é "local", mesmo que o valor seja inválido. Quem
    usa o FISCALE no próprio PC não manda esses cabeçalhos; se eles vieram,
    alguém no meio do caminho os colocou.

DO `X-Forwarded-For`, SÓ O ÚLTIMO
    O último valor é o que o proxy desta máquina escreveu. Os anteriores
    vieram de quem mandou a requisição, e valem tanto quanto um palpite.
"""
from __future__ import annotations

import ipaddress

LOOPBACK = ("127.0.0.1", "::1", "localhost")

# Os cabeçalhos que denunciam passagem por proxy. `Forwarded` (RFC 7239) entra
# na lista mesmo sem ser lido: presença basta para negar o "local".
CABECALHOS_ENCAMINHAMENTO = ("X-Forwarded-For", "X-Forwarded-Proto",
                             "X-Forwarded-Host", "Forwarded", "X-Real-IP")


def _cab(cabecalhos, nome: str) -> str:
    try:
        return (cabecalhos.get(nome) or "").strip()
    except Exception:
        return ""


def eh_loopback(ip: str) -> bool:
    ip = (ip or "").strip()
    if ip in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def via_proxy(ip_soquete: str, cabecalhos) -> bool:
    """A requisição veio de um proxy nesta máquina?"""
    return eh_loopback(ip_soquete) and any(
        _cab(cabecalhos, n) for n in CABECALHOS_ENCAMINHAMENTO)


def eh_local(ip_soquete: str, cabecalhos) -> bool:
    """Está no próprio PC — de verdade, sem proxy no meio."""
    return eh_loopback(ip_soquete) and not via_proxy(ip_soquete, cabecalhos)


def origem(ip_soquete: str, cabecalhos) -> str:
    """O IP a registrar e a contar no limite de tentativas."""
    ip_soquete = ip_soquete or ""
    if not via_proxy(ip_soquete, cabecalhos):
        return ip_soquete
    ultimo = _cab(cabecalhos, "X-Forwarded-For").split(",")[-1].strip()
    try:
        return str(ipaddress.ip_address(ultimo))
    except ValueError:
        # Proxy mal configurado: não inventa IP, mas também não devolve
        # 127.0.0.1 — que faria a pessoa parecer estar no PC na auditoria.
        return "proxy-sem-ip"


def via_https(ip_soquete: str, cabecalhos) -> bool:
    """O navegador está em HTTPS (o proxy terminou o TLS)?"""
    return (via_proxy(ip_soquete, cabecalhos)
            and _cab(cabecalhos, "X-Forwarded-Proto").lower() == "https")


# HSTS começa curto DE PROPÓSITO. Ele manda o navegador recusar HTTP naquele
# nome por todo o prazo — um erro de configuração no primeiro dia ficaria
# preso por um ano. Um dia basta para validar; aumentar é mudar este número.
HSTS = "max-age=86400"
