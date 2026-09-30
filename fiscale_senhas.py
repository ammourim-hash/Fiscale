#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fiscale — a política de senha (Fase 2).

    import fiscale_senhas as sen
    ok, motivo = sen.validar(nova, uid="maria", nome="Maria R.",
                             email="maria@x.com.br")

A REGRA, EM UMA LINHA
    Pelo menos 8 caracteres, no máximo 128, e nada que seja adivinhável.

O QUE ESTA POLÍTICA **NÃO** EXIGE
    Combinação artificial de maiúscula, número e símbolo. Essa exigência tem
    trinta anos e um efeito conhecido: ela não produz senha forte, produz
    `Fiscal@2026` — que um atacante tenta antes de qualquer outra coisa. Uma
    frase longa e comum de lembrar bate qualquer `P@ssw0rd`.

    Por isso o comprimento é o eixo, e a recomendação (12+) é RECOMENDAÇÃO:
    aparece na tela, não barra ninguém.

O QUE ELA BLOQUEIA
    O que se adivinha: senha comum, sequência de teclado ou de alfabeto,
    repetição, e o que a própria pessoa é — uid, nome, e-mail. Bloquear isso
    vale mais que exigir um `!` no fim.

SENHA EXISTENTE CONTINUA VALENDO
    A política vale para senha NOVA. Ninguém é obrigado a trocar, e ninguém
    fica trancado para fora por uma regra que nasceu depois dele. Quem trocar,
    trocará dentro da regra nova.

O LIMITE DE 128 NÃO É CAPRICHO
    O PBKDF2 processa o que vier, e o custo é linear no tamanho. Sem teto, um
    campo de senha vira um jeito barato de ocupar o servidor: um POST com
    alguns megabytes de "senha" custa segundos de CPU por tentativa.

CONDIÇÃO DE SEGURANÇA REGISTRADA (não implementada aqui)
    **Oito caracteres bastam para a rede do escritório e NÃO bastam para a
    internet.** Antes de qualquer liberação de acesso externo é preciso, além
    de HTTPS, uma camada adicional de autenticação — rede privada, ou MFA.
    Senha de 8 exposta à internet é questão de tempo, não de sorte.
"""
from __future__ import annotations

import re
import unicodedata

MINIMO = 8
MAXIMO = 128
RECOMENDADO = 12

# O texto exato que a tela mostra. Fica aqui para a interface e o teste
# lerem a MESMA frase — mensagem duplicada é mensagem que envelhece em um
# lugar só.
MENSAGEM_UI = ("Use pelo menos 8 caracteres. Para maior segurança, "
               "recomendamos uma frase com 12 caracteres ou mais.")

# ── o que se adivinha ───────────────────────────────────────────────────────
# Lista curta e deliberada: as que aparecem no topo de qualquer vazamento, mais
# as que um escritório de contabilidade brasileiro usaria. Não é para ser
# exaustiva — dicionário grande é trabalho de quem faz força bruta, e para
# isso existe o limite de tentativas.
COMUNS = {
    "12345678", "123456789", "1234567890", "password", "senha123",
    "12345678910", "87654321", "qwertyui", "asdfghjk", "abcd1234",
    "1q2w3e4r", "senhasenha", "contabilidade", "escritorio", "fiscale",
    "fiscale123", "administrador", "adminadmin", "admin123", "mudar123",
    "trocar123", "primeiroacesso", "bemvindo", "brasil123", "novasenha",
    "minhasenha", "senha1234", "iloveyou", "sunshine", "princess",
    "letmein1", "welcome1", "monkey12", "football", "baseball", "dragon12",
}

_SEQUENCIAS = (
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789",
    "qwertyuiop", "asdfghjkl", "zxcvbnm",
    "1qaz2wsx", "!@#$%^&*()",
)


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto or "")
                   if not unicodedata.combining(c))


# Trocas de "leetspeak". Sem elas, `m@ri@.rodrigue5` passa por uma regra que
# recusa `maria.rodrigues` — e é exatamente essa a troca que alguém faz quando
# a tela diz "não pode ser o seu nome".
_LEET = str.maketrans({"@": "a", "4": "a", "3": "e", "1": "i", "!": "i",
                       "0": "o", "5": "s", "$": "s", "7": "t", "+": "t"})


def _cru(texto: str) -> str:
    """Minúsculo, sem acento, só letra/número — e SEM dobrar leetspeak.

    Serve para a pergunta "isto é uma sequência de teclado?", que é sobre os
    CARACTERES digitados. Dobrar `0` em `o` aqui faria `0123456789` virar
    `oi2eas6t89` e escapar da regra — foi o que o teste pegou.
    """
    return re.sub(r"[^a-z0-9]", "", _sem_acento(str(texto or "")).lower())


def _achatar(texto: str) -> str:
    """Minúsculo, sem acento, sem leet e só letra/número.

    Compara SIGNIFICADO, não grafia: `M@ria.2026` e `maria2026` são a mesma
    ideia para quem tenta adivinhar, e tratá-las como diferentes seria
    bloquear uma e deixar a outra passar.
    """
    limpo = _sem_acento(str(texto or "")).lower().translate(_LEET)
    return re.sub(r"[^a-z0-9]", "", limpo)


def repetitiva(senha: str) -> bool:
    """`aaaaaaaa`, `abababab`, `123123123`.

    Uma senha que é um pedacinho repetido tem a força do pedacinho, não a do
    comprimento — e o comprimento é justamente o que estamos usando como eixo.
    """
    s = senha or ""
    if len(set(s)) <= 2:
        return True
    for tamanho in range(1, len(s) // 2 + 1):
        if len(s) % tamanho == 0 and s[:tamanho] * (len(s) // tamanho) == s:
            return True
    return False


def sequencial(senha: str) -> bool:
    """`12345678`, `abcdefgh`, `qwertyui` — e os mesmos de trás para frente."""
    # `_cru`, não `_achatar`: aqui a pergunta é sobre os caracteres, e a
    # dobra de leet é sobre significado. São coisas diferentes.
    s = _cru(senha)
    if len(s) < 4:
        return False
    for base in _SEQUENCIAS:
        for fonte in (base, base[::-1]):
            # A senha INTEIRA ser um trecho de uma sequência é o caso a
            # bloquear. Conter um trecho não é: `casa1234feliz` é boa.
            if s in fonte:
                return True
    return False


def parece_com_a_pessoa(senha: str, *, uid="", nome="", email="") -> str:
    """A senha é a própria pessoa? Devolve o que ela repete, ou "".

    Quem invade um escritório tenta o nome e o e-mail antes de tudo. E
    `maria2026` é `maria` com um ano colado.
    """
    alvo = _achatar(senha)
    if not alvo:
        return ""
    candidatos = []
    if uid:
        candidatos.append(("o nome de usuário", _achatar(uid)))
    if nome:
        candidatos.append(("o seu nome", _achatar(nome)))
        for parte in str(nome).split():
            if len(parte) >= 4:
                candidatos.append(("o seu nome", _achatar(parte)))
    if email:
        local = str(email).split("@")[0]
        candidatos.append(("o seu e-mail", _achatar(local)))
        dominio = str(email).split("@")[-1].split(".")[0]
        if len(dominio) >= 4:
            candidatos.append(("o domínio do seu e-mail", _achatar(dominio)))

    for rotulo, pedaco in candidatos:
        if len(pedaco) < 3:
            continue
        # Igual, ou o miolo dela: `maria`, `maria2026`, `2026maria`.
        if alvo == pedaco or (pedaco in alvo and
                              len(alvo) - len(pedaco) <= 4):
            return rotulo
    return ""


def validar(senha, *, uid="", nome="", email="") -> tuple:
    """`(ok, motivo)`. Motivo vazio quando passa.

    O motivo é escrito para quem está trocando a senha, e diz o que fazer —
    "senha fraca" sozinho faz a pessoa tentar `Senha@123` e voltar.
    """
    s = senha or ""
    if len(s) < MINIMO:
        return False, ("A senha precisa de pelo menos %d caracteres. %s"
                       % (MINIMO, MENSAGEM_UI))
    if len(s) > MAXIMO:
        return False, ("A senha pode ter no máximo %d caracteres." % MAXIMO)
    if s.strip() != s and not s.strip():
        return False, "A senha não pode ser só espaços."

    achatada = _achatar(s)
    if achatada in COMUNS or s.lower() in COMUNS:
        return False, ("Esta senha é uma das mais usadas do mundo e está na "
                       "primeira tentativa de quem invade. Escolha outra. %s"
                       % MENSAGEM_UI)
    if repetitiva(s):
        return False, ("Senha feita de um trecho repetido tem a força do "
                       "trecho, não do tamanho. %s" % MENSAGEM_UI)
    if sequencial(s):
        return False, ("Sequência do teclado ou do alfabeto é adivinhada em "
                       "segundos. %s" % MENSAGEM_UI)

    parecido = parece_com_a_pessoa(s, uid=uid, nome=nome, email=email)
    if parecido:
        return False, ("A senha não pode ser %s. É o primeiro palpite de "
                       "quem tenta entrar. %s" % (parecido, MENSAGEM_UI))
    return True, ""


def forca(senha) -> dict:
    """Uma leitura amigável para a tela. Nunca barra nada — só informa."""
    n = len(senha or "")
    if n < MINIMO:
        return {"nivel": "curta", "rotulo": "Curta demais", "n": n}
    if n < RECOMENDADO:
        return {"nivel": "aceitavel", "rotulo": "Aceitável", "n": n,
                "dica": "Uma frase com %d caracteres ou mais protege muito "
                        "mais." % RECOMENDADO}
    return {"nivel": "boa", "rotulo": "Boa", "n": n}
