#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fiscale — sessões que sobrevivem ao reinício.

O PROBLEMA
    Até aqui as sessões viviam num dicionário em memória (``SESSOES = {}``).
    Funciona, e é simples — mas todo reinício do servidor desloga todo
    mundo ao mesmo tempo. Com uma pessoa isso é um aborrecimento; com
    quinze é o escritório parando junto.

POR QUE SQLite E NÃO OUTRA COISA
    A decisão anterior sugeriu SQLite. Reavaliei olhando o código real e
    ela continua certa, por motivos que só ficam claros aqui dentro:

    - O ``sqlite3`` já vem na biblioteca padrão do Python. O Fiscale se
      orgulha de rodar sem dependência externa, e o executável do
      PyInstaller já o empacota. Zero instalação, zero serviço novo.
    - O servidor é ``ThreadingMixIn``: várias threads mexendo no mesmo
      arquivo. Um JSON gravado por duas threads perde escrita; o SQLite
      resolve isso com transação, que é o problema para o qual ele existe.
    - Postgres e Redis exigiriam subir um serviço na máquina do escritório
      só para guardar quinze linhas. Custo de operação sem ganho.

    Um arquivo, sem servidor, com escrita atômica. É o encaixe exato.

O QUE FICA GRAVADO
    O que a sessão precisa e nada além. Sem senha, sem hash de senha, sem
    dado do certificado.

    E não é o token que fica guardado, e sim o SHA-256 dele. O arquivo
    ``sessoes.db`` mora na pasta de dados, que vai para backup e para o
    Drive: se alguém lesse os tokens ali, entraria como qualquer usuário
    sem saber senha nenhuma. Com o hash, o que se lê não serve para
    entrar — o valor original só existe no cookie de quem está logado.
"""
import hashlib
import os
import secrets
import sqlite3
import threading
import time

# 12 horas: mais que um dia de trabalho, menos que um fim de semana
# esquecido com a tela aberta.
VALIDADE_PADRAO_S = 12 * 3600

# "Manter conectado" (portal, 13/09/2026): 7 dias, CORRIDOS a partir do login.
# Não é renovável por uso — pelo mesmo motivo do `get_com_motivo`: sessão que
# se estende a cada clique nunca expira de verdade. Continua revogável por
# token, por usuário e em bloco, como qualquer outra.
VALIDADE_MANTER_S = 7 * 24 * 3600

# Faxina no máximo a cada 10 min — é oportunista, não precisa de agendador.
_INTERVALO_FAXINA_S = 600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Sessoes:
    """Sessões em SQLite, com a mesma cara de um dicionário.

    O acesso continua sendo ``sessoes.get(token)`` e ``sessoes.criar(user)``,
    para que o servidor mude o mínimo possível.
    """

    def __init__(self, caminho, validade_s=VALIDADE_PADRAO_S):
        self.caminho = caminho
        self.validade_s = validade_s
        self._trava = threading.Lock()
        self._ultima_faxina = 0.0
        # check_same_thread=False porque o servidor é multi-thread; a
        # serialização fica por conta do lock acima, que é mais previsível
        # do que depender do modo de threading do módulo.
        self._con = sqlite3.connect(caminho, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")   # leitura não trava escrita
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._criar_tabela()

    def _criar_tabela(self):
        with self._trava:
            self._con.execute("""
                CREATE TABLE IF NOT EXISTS sessao (
                    token_hash TEXT PRIMARY KEY,
                    usuario    TEXT    NOT NULL,
                    criada_em  INTEGER NOT NULL,
                    expira_em  INTEGER NOT NULL,
                    vista_em   INTEGER NOT NULL
                )
            """)
            self._con.execute(
                "CREATE INDEX IF NOT EXISTS ix_sessao_usuario ON sessao (usuario)")
            self._con.execute(
                "CREATE INDEX IF NOT EXISTS ix_sessao_expira ON sessao (expira_em)")
            self._con.commit()

    # ── uso ────────────────────────────────────────────────────────────
    def criar(self, usuario, validade_s=None):
        """Abre sessão e devolve o token. O token só existe aqui e no cookie.

        ``validade_s`` só existe para o "Manter conectado"; sem ele vale a
        validade da instância, como sempre valeu.
        """
        token = secrets.token_urlsafe(32)
        agora = int(time.time())
        validade = int(validade_s) if validade_s else self.validade_s
        with self._trava:
            self._con.execute(
                "INSERT INTO sessao (token_hash, usuario, criada_em, expira_em, vista_em)"
                " VALUES (?,?,?,?,?)",
                (_hash(token), usuario, agora, agora + validade, agora))
            self._con.commit()
        self._faxina_oportunista()
        return token

    def get(self, token):
        """Usuário da sessão, ou None. Mesma assinatura do dicionário antigo.

        Só devolve alguém quando o motivo é OK. O detalhe não é decorativo:
        `get_com_motivo` devolve o dono da sessão EXPIRADA de propósito, para
        a auditoria poder dizer de quem ela era — e uma versão minha deste
        método repassou esse nome adiante, deixando sessão vencida continuar
        autenticando. Quem pergunta "quem é" tem de receber None em tudo que
        não for OK.
        """
        usuario, motivo = self.get_com_motivo(token)
        return usuario if motivo == "OK" else None

    def get_com_motivo(self, token):
        """`(usuario, motivo)` — `motivo` em AUSENTE / DESCONHECIDA / EXPIRADA / OK.

        A auditoria (Fase 3) precisa separar "não mandou cookie" de "a sessão
        venceu": a primeira é a tela de login normal, a segunda é um evento
        que o administrador tem interesse em ver. Do lado de fora `get()`
        continua idêntico — quem só quer saber quem é não precisa escolher.

        O motivo EXPIRADA só sai UMA vez por sessão, porque a linha é apagada
        aqui mesmo. É o que impede o registro de virar uma enxurrada quando
        uma aba esquecida fica recarregando sozinha.
        """
        if not token:
            return None, "AUSENTE"
        agora = int(time.time())
        with self._trava:
            linha = self._con.execute(
                "SELECT usuario, expira_em FROM sessao WHERE token_hash = ?",
                (_hash(token),)).fetchone()
            if not linha:
                return None, "DESCONHECIDA"
            usuario, expira_em = linha
            if expira_em <= agora:
                # Expirada é o mesmo que inexistente — e já sai da frente.
                self._con.execute("DELETE FROM sessao WHERE token_hash = ?",
                                  (_hash(token),))
                self._con.commit()
                return usuario, "EXPIRADA"
            # Marca atividade sem estender a validade: sessão renovável para
            # sempre nunca expira de verdade.
            self._con.execute("UPDATE sessao SET vista_em = ? WHERE token_hash = ?",
                              (agora, _hash(token)))
            self._con.commit()
        return usuario, "OK"

    def revogar(self, token):
        """Logout. Apaga do servidor — não adianta só limpar o cookie."""
        if not token:
            return
        with self._trava:
            self._con.execute("DELETE FROM sessao WHERE token_hash = ?", (_hash(token),))
            self._con.commit()

    def revogar_usuario(self, usuario):
        """Derruba todas as sessões de alguém. Devolve QUANTAS caíram.

        A contagem existe para a auditoria: "desativei fulano" e "desativei
        fulano e derrubei as três sessões abertas dele" são fatos diferentes
        para quem lê o registro depois.
        """
        with self._trava:
            cur = self._con.execute(
                "DELETE FROM sessao WHERE usuario = ?", (usuario,))
            self._con.commit()
            return cur.rowcount or 0

    def fechar(self):
        """Solta o arquivo do banco.

        Existe por causa do Windows: enquanto o `sessoes.db` está aberto, a
        pasta de dados não pode ser movida nem renomeada — e a restauração em
        modo "Substituir" faz exatamente isso. Fechar antes é o que permite
        restaurar com o Fiscale rodando."""
        with self._trava:
            try:
                self._con.close()
            except Exception:
                pass

    def reabrir(self):
        """Volta a abrir o banco depois de uma restauração.

        O arquivo pode não existir mais (a restauração não traz sessões, de
        propósito): o SQLite cria de novo, vazio, e todo mundo entra outra vez.
        Que é exatamente o comportamento certo depois de trocar o cadastro de
        usuários."""
        with self._trava:
            self._con = sqlite3.connect(self.caminho, check_same_thread=False)
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA synchronous=NORMAL")
        self._criar_tabela()

    def revogar_todas(self):
        """Derruba todo mundo. Serve para depois de restaurar um backup: os
        usuários passaram a ser outros, e seguir logado com a sessão de antes
        seria estar autenticado por um cadastro que não existe mais."""
        with self._trava:
            self._con.execute("DELETE FROM sessao")
            self._con.commit()

    def contar(self):
        with self._trava:
            return self._con.execute(
                "SELECT count(*) FROM sessao WHERE expira_em > ?",
                (int(time.time()),)).fetchone()[0]

    # ── manutenção ─────────────────────────────────────────────────────
    def _faxina_oportunista(self):
        agora = time.time()
        if agora - self._ultima_faxina < _INTERVALO_FAXINA_S:
            return
        self._ultima_faxina = agora
        try:
            with self._trava:
                self._con.execute("DELETE FROM sessao WHERE expira_em <= ?",
                                  (int(agora),))
                self._con.commit()
        except Exception:
            pass   # faxina falhar não pode atrapalhar quem está entrando


def abrir(pasta_dados, validade_s=VALIDADE_PADRAO_S):
    """Abre (ou cria) o banco de sessões dentro da pasta de dados."""
    return Sessoes(os.path.join(pasta_dados, "sessoes.db"), validade_s)
