#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fiscale — o segredo local do login (Fase 2.1).

    import fiscale_pimenta
    p = fiscale_pimenta.abrir(pasta_dados)
    p.chave_do_desconhecido(" Maria@X.com ")   # -> "~a3f9..."  (sempre o mesmo)
    p.gastar_o_mesmo_tempo("senha digitada")   # -> False, depois de 200k iterações

PARA QUE ISTO EXISTE
    Até a Fase 2, quem digitava um e-mail que NÃO existe recebia o "inválido"
    na hora, e quem digitava um que existe esperava as 200 mil iterações do
    PBKDF2. A mensagem era a mesma; o relógio não era. Com um cronômetro dava
    para separar os dois casos e montar a lista de quem tem conta no
    escritório — que é exatamente o primeiro passo de quem vai tentar entrar.

    Este módulo guarda as duas peças que fecham essa fresta:

    1. A PIMENTA, para contar tentativas de identificadores que não existem
       sem escrever o que foi digitado.
    2. O SAL E O HASH FICTÍCIOS, para que o caminho do usuário inexistente
       custe o mesmo que o do usuário real.

POR QUE HMAC, E NÃO UM HASH SOLTO
    O contador precisa de uma chave estável por identificador: `maria@x` tem
    de cair sempre na mesma linha, senão dez tentativas viram dez contadores
    de um e nada bloqueia. Mas gravar `maria@x` no banco seria transformar o
    contador de tentativas num vazamento de e-mails — e um SHA-256 puro não
    resolve: o conjunto de e-mails plausíveis é pequeno, e quem lesse o
    arquivo montaria a tabela em minutos.

    Com HMAC-SHA256 sob um segredo que só existe nesta máquina, a mesma
    entrada sempre dá a mesma chave, e quem lê o banco sem o segredo não tem
    por onde começar.

ONDE O SEGREDO MORA
    Em `<dados>/.pimenta-login`, protegido pelo DPAPI do Windows — a mesma
    proteção da senha do certificado, atrelada à conta do Windows. Fora do
    Windows não há DPAPI: o arquivo fica em claro com permissão 0600, e isso
    está dito em voz alta em `_gravar`, porque o FISCALE é um sistema Windows
    e o outro caminho existe só para os testes rodarem em qualquer lugar.

    O DPAPI é feito aqui com `ctypes` em vez de importar
    `nfse/backend/seguranca.py` de propósito: aquele módulo roda no
    interpretador do NFS-e e cai em `cryptography` quando não é Windows;
    este roda no Python do sistema, que não tem essa biblioteca garantida. O
    login não pode depender de um pacote que talvez não esteja instalado.

QUANDO O SEGREDO NÃO ABRE
    O DPAPI é preso à conta do Windows. Restaurar um backup em outra máquina,
    ou noutro usuário, torna o arquivo ilegível. Isso NÃO pode derrubar o
    login: nesse caso a pimenta é trocada por uma nova e as linhas velhas —
    que já eram ilegíveis — são descartadas.

    Trocar a pimenta zera o contador dos identificadores inexistentes, e é
    justo perguntar se isso não é uma porta. Não é: só quem já tem o disco e
    a conta do Windows consegue provocar a troca, e quem chegou até aí não
    precisa adivinhar login nenhum. O contador por IP, esse, não depende da
    pimenta e não se perde.

O QUE NUNCA ENTRA AQUI
    Senha, hash de usuário, sal de usuário. A pimenta não tem relação com
    nenhuma conta, e o hash fictício é o PBKDF2 de uma senha aleatória que
    ninguém nunca soube e que é descartada na criação.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import secrets
import threading

ARQUIVO = ".pimenta-login"
VERSAO = 1

# Tem de ser o MESMO custo do `_hash_senha` do servidor. Se um dia lá mudar e
# aqui não, o caminho falso volta a ser mais barato que o verdadeiro e a
# fresta reabre — por isso existe teste que compara os dois números.
ITERACOES = 200_000
ALGORITMO = "sha256"

# Marca a chave derivada como "identificador que não existe". Serve para
# ninguém confundir, ao ler o banco, uma linha destas com um uid de verdade.
PREFIXO = "~"


def normalizar(identificador) -> str:
    """Espaço fora, tudo minúsculo.

    É o que faz `Maria@X.com `, ` maria@x.com` e `MARIA@X.COM` caírem no
    mesmo contador. Sem isso, trocar a caixa das letras daria tentativas de
    graça — e trocar a caixa é a primeira coisa que um script faz.
    """
    return str(identificador or "").strip().lower()


# ── DPAPI (Windows), em stdlib puro ─────────────────────────────────────────
def _dpapi(data: bytes, proteger: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    entrada = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    saida = BLOB()
    funcao = (ctypes.windll.crypt32.CryptProtectData if proteger
              else ctypes.windll.crypt32.CryptUnprotectData)
    if not funcao(ctypes.byref(entrada), None, None, None, None, 0,
                  ctypes.byref(saida)):
        raise OSError("DPAPI recusou o segredo do login")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(saida.pbData)


def _windows() -> bool:
    return platform.system() == "Windows"


class Pimenta:
    def __init__(self, caminho, trocou=None):
        self.caminho = str(caminho)
        self._trava = threading.RLock()
        # Avisa quem abriu que o segredo é novo — o servidor usa isso para
        # jogar fora as linhas que ficaram ilegíveis.
        self.trocada = False
        self._carregar()
        if self.trocada and trocou:
            trocou()

    # ── disco ───────────────────────────────────────────────────────────
    def _gravar(self, miolo: dict):
        cru = json.dumps(miolo, separators=(",", ":")).encode("utf-8")
        if _windows():
            texto = "dpapi:" + base64.b64encode(_dpapi(cru, True)).decode()
        else:
            # Sem DPAPI não há a quem prender o segredo. O arquivo fica em
            # claro e a permissão é a única defesa — dito assim, sem eufemismo,
            # porque isto existe para os testes, não para produção.
            texto = "claro:" + base64.b64encode(cru).decode()
        tmp = self.caminho + ".novo"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.caminho)
        try:
            os.chmod(self.caminho, 0o600)
        except OSError:
            pass

    def _ler(self) -> dict:
        with open(self.caminho, "r", encoding="utf-8") as f:
            texto = f.read().strip()
        if texto.startswith("dpapi:"):
            cru = _dpapi(base64.b64decode(texto[6:]), False)
        elif texto.startswith("claro:"):
            cru = base64.b64decode(texto[6:])
        else:
            raise ValueError("formato desconhecido")
        miolo = json.loads(cru.decode("utf-8"))
        for campo in ("pimenta", "sal_ficticio", "hash_ficticio"):
            if not miolo.get(campo):
                raise ValueError("segredo incompleto")
        return miolo

    def _nascer(self) -> dict:
        """Cria as três peças, uma única vez.

        O hash fictício é o PBKDF2 de uma senha aleatória que é descartada
        aqui mesmo: ninguém — nem quem escreveu isto — sabe qual senha bate
        com ele, e é isso que garante que a comparação falsa nunca acerte.
        """
        sal = secrets.token_hex(16)
        descartada = secrets.token_hex(32)
        h = hashlib.pbkdf2_hmac(ALGORITMO, descartada.encode(),
                                bytes.fromhex(sal), ITERACOES).hex()
        return {"v": VERSAO, "pimenta": secrets.token_hex(32),
                "sal_ficticio": sal, "hash_ficticio": h}

    def _carregar(self):
        with self._trava:
            try:
                miolo = self._ler()
            except FileNotFoundError:
                miolo = self._nascer()
                self._gravar(miolo)
            except Exception:
                # Ilegível: outra conta do Windows, outra máquina, ou arquivo
                # estragado. Trocar é a única saída que mantém o login de pé.
                miolo = self._nascer()
                self._gravar(miolo)
                self.trocada = True
            self._pimenta = bytes.fromhex(miolo["pimenta"])
            self.sal_ficticio = miolo["sal_ficticio"]
            self.hash_ficticio = miolo["hash_ficticio"]

    # ── uso ─────────────────────────────────────────────────────────────
    def chave_do_desconhecido(self, identificador) -> str:
        """A chave de contagem de um identificador que não existe.

        Não é reversível e não sai daqui: o que vai para o banco é
        `~<32 hex>`, e o que foi digitado morre nesta função.
        """
        norm = normalizar(identificador)
        if not norm:
            return ""
        mac = hmac.new(self._pimenta, norm.encode("utf-8"), hashlib.sha256)
        return PREFIXO + mac.hexdigest()[:32]

    def gastar_o_mesmo_tempo(self, senha) -> bool:
        """O PBKDF2 do usuário que não existe. Devolve sempre `False`.

        Mesmo algoritmo, mesmas 200 mil iterações, mesma comparação em tempo
        constante do caminho verdadeiro. O retorno é `False` por construção —
        não por sorte —, porque a senha que gerou `hash_ficticio` foi jogada
        fora assim que ele nasceu.
        """
        h = hashlib.pbkdf2_hmac(ALGORITMO, str(senha or "").encode(),
                                bytes.fromhex(self.sal_ficticio),
                                ITERACOES).hex()
        return secrets.compare_digest(h, self.hash_ficticio)


def abrir(pasta_dados, trocou=None) -> Pimenta:
    os.makedirs(str(pasta_dados), exist_ok=True)
    return Pimenta(os.path.join(str(pasta_dados), ARQUIVO), trocou=trocou)
