#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fiscale — limite de tentativas de login (Fase 2).

    import fiscale_tentativas as tt
    porta = tt.abrir(pasta_dados)
    v = porta.avaliar(uid="maria", ip="192.168.0.7")
    if v.bloqueado: ...          # nem confere a senha
    porta.registrar_falha(uid="maria", ip="192.168.0.7")
    porta.registrar_acerto(uid="maria", ip="192.168.0.7")

POR QUE DUAS CHAVES, E NÃO UMA
    Contar só por USUÁRIO deixa passar quem varre logins diferentes da mesma
    origem — dez tentativas em dez contas não estouram contador nenhum.
    Contar só por IP tranca o escritório inteiro quando uma pessoa erra a
    senha, porque todo mundo sai pelo mesmo endereço na rede local.

    As duas juntas: a conta protege a pessoa, o IP protege o sistema. E o
    limite do IP é bem mais alto, justamente porque ele é compartilhado.

POR QUE ATRASO ANTES DE BLOQUEIO
    Errar a senha é normal — caps lock, teclado trocado, senha do outro
    sistema. Bloquear na terceira tentativa transforma o cotidiano em chamado
    para o administrador. O atraso progressivo torna a força bruta inviável
    (que é o objetivo) sem punir quem só errou (que não é).

POR QUE SQLite E NÃO MEMÓRIA
    Em memória, reiniciar o Fiscale zera os contadores — e o Fiscale reinicia
    com frequência. Quem estivesse tentando força bruta ganharia um "recomeço"
    de graça, e o bloqueio que mais importa seria o que menos dura.

    O mesmo arquivo, a mesma família do `sessoes.db`: um arquivo, sem
    serviço, com escrita transacional.

O QUE **NÃO** FICA GRAVADO
    Nem senha, nem hash, nem o que foi digitado. Só a chave (uid ou ip),
    quando foi, e se acertou. O IP é dado de acesso, não segredo — mas
    também não vai para log nenhum por conta deste módulo.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

# ── a política ──────────────────────────────────────────────────────────────
# Números escolhidos para a REDE DO ESCRITÓRIO, onde o risco é alguém errando
# a senha, não um robô. Para acesso externo eles teriam de ser revistos — e,
# antes disso, o acesso externo exige HTTPS e uma segunda camada (ver
# `fiscale_senhas`, condição de segurança registrada).
# Os três espaços de contagem. São NOMES DIFERENTES no banco, não prefixos
# dentro do mesmo nome: a conta real e o identificador que não existe nunca
# podem cair na mesma linha, e uma convenção de prefixo garantiria isso
# apenas enquanto ninguém criasse um login com aquele formato. Namespace
# separado tira a pergunta da mesa.
ESCOPO_CONTA = "usuario"
ESCOPO_DESCONHECIDO = "desconhecido"
ESCOPO_IP = "ip"

JANELA_S = 15 * 60           # o que aconteceu nos últimos 15 minutos conta
LIVRES_USUARIO = 3           # erros sem qualquer atraso
ATRASOS_S = (0, 0, 0, 1, 2, 5, 10, 20, 30)   # da 1ª à 9ª falha
BLOQUEIO_USUARIO = 8         # a partir daqui, a conta espera
BLOQUEIO_S = 5 * 60          # e espera 5 minutos
LIMITE_IP = 30               # a origem inteira, com folga para o escritório
BLOQUEIO_IP_S = 10 * 60

_LIMPEZA_S = 300


class Veredito:
    """O que a porta respondeu, e por quê."""

    def __init__(self, bloqueado=False, atraso_s=0.0, espera_s=0,
                 motivo="", escopo=""):
        self.bloqueado = bloqueado
        self.atraso_s = atraso_s
        self.espera_s = espera_s
        self.motivo = motivo
        self.escopo = escopo       # "usuario" ou "ip"

    def dict(self):
        return {"bloqueado": self.bloqueado, "espera_s": self.espera_s,
                "motivo": self.motivo, "escopo": self.escopo}


class Porta:
    def __init__(self, caminho, agora=None):
        self.caminho = str(caminho)
        self._trava = threading.RLock()
        self._agora = agora or time.time
        self._ultima_limpeza = 0.0
        self._con = sqlite3.connect(self.caminho, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._criar()

    def _criar(self):
        with self._trava:
            self._con.execute("""
                CREATE TABLE IF NOT EXISTS tentativa (
                    escopo  TEXT    NOT NULL,
                    chave   TEXT    NOT NULL,
                    quando  REAL    NOT NULL,
                    ok      INTEGER NOT NULL
                )
            """)
            self._con.execute(
                "CREATE INDEX IF NOT EXISTS ix_tent ON tentativa "
                "(escopo, chave, quando)")
            self._con.commit()

    # ── contagem ────────────────────────────────────────────────────────
    def _falhas(self, escopo, chave) -> int:
        if not chave:
            return 0
        corte = self._agora() - JANELA_S
        with self._trava:
            (n,) = self._con.execute(
                "SELECT COUNT(*) FROM tentativa WHERE escopo=? AND chave=? "
                "AND ok=0 AND quando>=?", (escopo, str(chave), corte)).fetchone()
        return n

    def _ultima_falha(self, escopo, chave) -> float:
        with self._trava:
            r = self._con.execute(
                "SELECT MAX(quando) FROM tentativa WHERE escopo=? AND chave=? "
                "AND ok=0", (escopo, str(chave))).fetchone()
        return (r and r[0]) or 0.0

    # ── decisão ─────────────────────────────────────────────────────────
    def avaliar(self, *, uid="", ip="", escopo=ESCOPO_CONTA) -> Veredito:
        """Pode tentar agora? Chamado ANTES de conferir a senha.

        Barrar antes é o ponto: conferir a senha custa 200 mil iterações de
        PBKDF2, e deixar isso acontecer a cada tentativa é entregar o próprio
        servidor como ferramenta do ataque.
        """
        self._limpar()
        agora = self._agora()

        n_ip = self._falhas(ESCOPO_IP, ip)
        if ip and n_ip >= LIMITE_IP:
            resta = BLOQUEIO_IP_S - (agora - self._ultima_falha(ESCOPO_IP, ip))
            if resta > 0:
                return Veredito(
                    bloqueado=True, espera_s=int(resta) + 1, escopo=ESCOPO_IP,
                    motivo="Muitas tentativas vindas deste computador. "
                           "Aguarde alguns minutos.")

        # O MESMO limite vale para a conta real e para o identificador que
        # não existe: muda o espaço de contagem, não a régua.
        n_uid = self._falhas(escopo, uid)
        if uid and n_uid >= BLOQUEIO_USUARIO:
            resta = BLOQUEIO_S - (agora - self._ultima_falha(escopo, uid))
            if resta > 0:
                return Veredito(
                    bloqueado=True, espera_s=int(resta) + 1, escopo=escopo,
                    motivo="Muitas tentativas seguidas. Aguarde alguns "
                           "minutos e tente de novo.")

        # Atraso progressivo: cresce com o erro, some com o acerto.
        #
        # As primeiras `LIVRES_USUARIO` tentativas não custam nada. Errar a
        # senha é normal — caps lock, teclado trocado, a senha do outro
        # sistema — e cobrar já na segunda transforma o cotidiano em chamado
        # para o administrador.
        if n_uid < LIVRES_USUARIO:
            return Veredito(atraso_s=0.0)
        atraso = (ATRASOS_S[n_uid] if n_uid < len(ATRASOS_S)
                  else ATRASOS_S[-1])
        return Veredito(atraso_s=float(atraso))

    # ── registro ────────────────────────────────────────────────────────
    def _gravar(self, escopo, chave, ok):
        if not chave:
            return
        with self._trava:
            self._con.execute(
                "INSERT INTO tentativa (escopo, chave, quando, ok) "
                "VALUES (?,?,?,?)", (escopo, str(chave), self._agora(),
                                     1 if ok else 0))
            self._con.commit()

    def registrar_falha(self, *, uid="", ip="", escopo=ESCOPO_CONTA):
        """Falhou. Conta nos dois escopos.

        `uid` pode vir vazio quando nem se sabe quem é (login inexistente) —
        aí só o IP conta, e é exatamente o caso que o contador de IP existe
        para pegar.
        """
        self._gravar(escopo, uid, False)
        self._gravar(ESCOPO_IP, ip, False)

    def registrar_acerto(self, *, uid="", ip=""):
        """Entrou. Zera o contador DA CONTA, mas não o do IP.

        Só existe para conta REAL: quem acerta a senha tem conta, e por isso
        aqui o escopo é sempre `ESCOPO_CONTA`. Um identificador inexistente
        nunca chega a acertar coisa nenhuma.

        A conta zera porque a pessoa provou ser ela. O IP não: uma origem que
        varreu vinte logins e acertou um não pode limpar a ficha acertando.
        """
        with self._trava:
            if uid:
                self._con.execute(
                    "DELETE FROM tentativa WHERE escopo=? AND chave=?",
                    (ESCOPO_CONTA, str(uid)))
            self._con.execute(
                "INSERT INTO tentativa (escopo, chave, quando, ok) "
                "VALUES (?,?,?,1)", (ESCOPO_CONTA, str(uid), self._agora()))
            self._con.commit()

    def liberar(self, *, uid="", ip="", escopo=ESCOPO_CONTA):
        """Solta manualmente — é o que o administrador usa para destravar."""
        with self._trava:
            if uid:
                self._con.execute(
                    "DELETE FROM tentativa WHERE escopo=? AND chave=?",
                    (escopo, str(uid)))
            if ip:
                self._con.execute(
                    "DELETE FROM tentativa WHERE escopo=? AND chave=?",
                    (ESCOPO_IP, str(ip)))
            self._con.commit()

    def esquecer_desconhecidos(self) -> int:
        """Apaga as linhas de identificador que não existe. Devolve quantas.

        Essas linhas são chaveadas por um HMAC sob o segredo local (ver
        `fiscale_pimenta`). Quando o segredo tem de ser trocado — backup
        restaurado noutra máquina, outra conta do Windows —, elas deixam de
        corresponder a coisa alguma: ninguém mais cai nelas, e elas nunca
        mais bloqueiam ninguém. Guardar seria acumular lixo que só cresce.

        O contador das contas REAIS e o do IP não são tocados: ele não depende do segredo, e é
        justamente o que continua de pé quando a pimenta se perde.
        """
        with self._trava:
            # O escopo INTEIRO some. Não é um `LIKE` sobre nomes que se
            # parecem: é um espaço separado, e nenhuma conta real mora nele.
            cur = self._con.execute(
                "DELETE FROM tentativa WHERE escopo=?", (ESCOPO_DESCONHECIDO,))
            self._con.commit()
            return cur.rowcount or 0

    def situacao(self, *, uid="", ip="", escopo=ESCOPO_CONTA) -> dict:
        return {"falhas_usuario": self._falhas(escopo, uid),
                "falhas_ip": self._falhas(ESCOPO_IP, ip),
                "veredito": self.avaliar(uid=uid, ip=ip, escopo=escopo).dict()}

    # ── faxina ──────────────────────────────────────────────────────────
    def _limpar(self):
        agora = self._agora()
        if agora - self._ultima_limpeza < _LIMPEZA_S:
            return
        self._ultima_limpeza = agora
        # Guarda o dobro da janela: o suficiente para a janela deslizante e
        # nada além. Registro de tentativa não é histórico, é contador.
        with self._trava:
            self._con.execute("DELETE FROM tentativa WHERE quando < ?",
                              (agora - JANELA_S * 2,))
            self._con.commit()

    def fechar(self):
        with self._trava:
            try:
                self._con.close()
            except Exception:
                pass


def abrir(pasta_dados, agora=None) -> Porta:
    os.makedirs(str(pasta_dados), exist_ok=True)
    return Porta(os.path.join(str(pasta_dados), "tentativas.db"), agora=agora)
