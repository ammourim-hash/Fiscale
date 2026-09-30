#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fiscale — registro de acessos e alterações de segurança (Fase 3).

    import fiscale_auditoria as aud
    livro = aud.abrir(pasta_dados)
    livro.registrar(aud.LOGIN_OK, aud.OK, ator_uid="maria",
                    ip="192.168.0.7", agente=cabecalho_user_agent)

PARA QUE ISTO EXISTE
    Até aqui o FISCALE não sabia dizer quem entrou. Numa instalação pessoal
    isso não fazia falta; num escritório com uma base só e várias pessoas,
    faz — e faz principalmente no dia em que algo dá errado, que é o dia em
    que ninguém lembra de nada.

    O que este livro responde: quem entrou, quando, de onde, e o que foi
    mexido em segurança. Nada além disso, nesta fase: quem abriu qual NF-e é
    auditoria OPERACIONAL, é outro assunto e tem outro custo.

SÓ ACRÉSCIMO
    Não existe função para editar nem para apagar um evento. A única remoção
    é a retenção, que apaga por IDADE e registra que apagou. Um livro que se
    pode corrigir não serve para o que um livro serve.

O QUE NUNCA ENTRA AQUI
    Senha, hash, sal. Cookie, token, cabeçalho de autorização. Corpo de
    requisição. Certificado, chave, segredo DPAPI. E-mail ou login digitado
    que NÃO existe — esse entra como a chave derivada da Fase 2.1, que é um
    HMAC e não volta atrás.

    Ao editar usuário grava-se QUAIS campos mudaram, nunca o de-para. Saber
    que o e-mail de alguém mudou é auditoria; guardar o e-mail velho e o novo
    é criar um histórico de dados pessoais que ninguém pediu.

POR QUE UM BANCO SEPARADO
    `sessoes.db` é efêmero e é apagado ao restaurar backup; `tentativas.db` é
    um contador de 15 minutos que se joga fora sem dó. A auditoria é a única
    coisa aqui que precisa DURAR, e misturá-la com os dois seria condená-la a
    ser tratada como descartável na primeira faxina.

DISPONIBILIDADE: AS DUAS REGRAS SÃO OPOSTAS DE PROPÓSITO
    Se a auditoria falhar, **entrar continua funcionando**. Trancar o
    escritório inteiro para fora porque um arquivo de log não abriu seria
    transformar um problema de registro num problema de operação.

    Mas **alterar usuário, papel ou senha passa a ser recusado**. Aí a conta
    é outra: uma mudança sensível sem registro é exatamente o que a auditoria
    existe para impedir, e adiar a mudança custa minutos.

    A falha fica visível em `saude()`, e a tela do administrador a mostra.
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

ARQUIVO = "auditoria.db"

# ── vocabulário fechado ─────────────────────────────────────────────────────
# Evento e resultado são listas fixas. Texto livre num registro de segurança
# vira, com o tempo, um lugar por onde vaza o que não devia — e uma coluna
# que ninguém consegue filtrar.

LOGIN_OK = "LOGIN_OK"
LOGIN_RECUSADO = "LOGIN_RECUSADO"
LOGIN_BLOQUEADO = "LOGIN_BLOQUEADO"
LOGOUT = "LOGOUT"
SESSAO_EXPIRADA = "SESSAO_EXPIRADA"
SESSAO_INVALIDADA = "SESSAO_INVALIDADA"
SENHA_TROCADA = "SENHA_TROCADA"
SENHA_REDEFINIDA = "SENHA_REDEFINIDA"
USUARIO_CRIADO = "USUARIO_CRIADO"
USUARIO_EDITADO = "USUARIO_EDITADO"
USUARIO_ATIVADO = "USUARIO_ATIVADO"
USUARIO_DESATIVADO = "USUARIO_DESATIVADO"
USUARIO_EXCLUIDO = "USUARIO_EXCLUIDO"
PAPEL_ALTERADO = "PAPEL_ALTERADO"
ACESSO_ADMIN_NEGADO = "ACESSO_ADMIN_NEGADO"
RETENCAO_EXECUTADA = "RETENCAO_EXECUTADA"
# Backup: quem fez, quando, quanto tempo levou e quantos arquivos entraram.
# Só contagem e duração — o `detalhe` de uma falha é a FASE, nunca o conteúdo
# do arquivo, nunca a frase-senha.
BACKUP_CRIADO = "BACKUP_CRIADO"
BACKUP_FALHOU = "BACKUP_FALHOU"
# Decisões administrativas sobre a ingestão NF-e. Guardam QUEM decidiu, sobre
# QUAL empresa (mascarada) e a justificativa — nunca certificado, senha,
# frase-senha ou conteúdo de documento fiscal.
NFE_REVISAO_ENCERRADA = "NFE_REVISAO_ENCERRADA"
NFE_REVISAO_REABERTA = "NFE_REVISAO_REABERTA"
NFE_COBERTURA_MARCADA = "NFE_COBERTURA_MARCADA"
# A decisão de NÃO decidir. Sem este evento, "analisado e mantido bloqueado"
# não deixaria rastro nenhum — e é exatamente a informação que faltava para
# distinguir uma pendência nova de uma já examinada.
NFE_BLOQUEIO_MANTIDO = "NFE_BLOQUEIO_MANTIDO"
NFE_DECISAO_RECUSADA = "NFE_DECISAO_RECUSADA"

EVENTOS = (
    LOGIN_OK, LOGIN_RECUSADO, LOGIN_BLOQUEADO, LOGOUT,
    SESSAO_EXPIRADA, SESSAO_INVALIDADA,
    SENHA_TROCADA, SENHA_REDEFINIDA,
    USUARIO_CRIADO, USUARIO_EDITADO, USUARIO_ATIVADO, USUARIO_DESATIVADO,
    USUARIO_EXCLUIDO, PAPEL_ALTERADO, ACESSO_ADMIN_NEGADO,
    RETENCAO_EXECUTADA,
    BACKUP_CRIADO, BACKUP_FALHOU,
    NFE_REVISAO_ENCERRADA, NFE_REVISAO_REABERTA, NFE_COBERTURA_MARCADA,
    NFE_BLOQUEIO_MANTIDO, NFE_DECISAO_RECUSADA,
)

# Como aparece na tela. Fica aqui para a interface e o filtro lerem a MESMA
# lista — um rótulo escrito à mão no HTML envelhece sozinho.
ROTULO_EVENTO = {
    LOGIN_OK: "Entrada",
    LOGIN_RECUSADO: "Entrada recusada",
    LOGIN_BLOQUEADO: "Entrada bloqueada",
    LOGOUT: "Saída",
    SESSAO_EXPIRADA: "Sessão expirada",
    SESSAO_INVALIDADA: "Sessão encerrada pelo sistema",
    SENHA_TROCADA: "Senha trocada pela própria pessoa",
    SENHA_REDEFINIDA: "Senha redefinida pelo administrador",
    USUARIO_CRIADO: "Usuário criado",
    USUARIO_EDITADO: "Usuário editado",
    USUARIO_ATIVADO: "Usuário ativado",
    USUARIO_DESATIVADO: "Usuário desativado",
    USUARIO_EXCLUIDO: "Usuário excluído",
    PAPEL_ALTERADO: "Papel alterado",
    ACESSO_ADMIN_NEGADO: "Acesso administrativo negado",
    RETENCAO_EXECUTADA: "Limpeza automática do registro",
    BACKUP_CRIADO: "Backup criado",
    BACKUP_FALHOU: "Backup não concluído",
    NFE_REVISAO_ENCERRADA: "NF-e: revisão de sequência encerrada",
    NFE_REVISAO_REABERTA: "NF-e: revisão de sequência reaberta",
    NFE_COBERTURA_MARCADA: "NF-e: início de cobertura marcado",
    NFE_BLOQUEIO_MANTIDO: "NF-e: mantida bloqueada por falta de evidência",
    NFE_DECISAO_RECUSADA: "NF-e: decisão administrativa recusada",
}

OK = "OK"
RECUSADO = "RECUSADO"
BLOQUEADO = "BLOQUEADO"
ERRO = "ERRO"
RESULTADOS = (OK, RECUSADO, BLOQUEADO, ERRO)

ROTULO_RESULTADO = {OK: "Sucesso", RECUSADO: "Recusado",
                    BLOQUEADO: "Bloqueado", ERRO: "Erro"}

# Códigos de detalhe. Fechados pelo mesmo motivo dos eventos, e nenhum deles
# carrega VALOR: o que se diz é a natureza da coisa, não a coisa.
D_CREDENCIAL = "CREDENCIAL_INVALIDA"
D_INEXISTENTE = "IDENTIFICADOR_INEXISTENTE"
D_INATIVO = "CONTA_INATIVA"
D_ENTRADA_GRANDE = "ENTRADA_ACIMA_DO_LIMITE"
D_LIMITE_CONTA = "LIMITE_POR_CONTA"
D_LIMITE_IP = "LIMITE_POR_IP"
D_PAPEL = "PAPEL_INSUFICIENTE"
D_SEM_SESSAO = "SEM_SESSAO"
D_AUDITORIA_INDISPONIVEL = "AUDITORIA_INDISPONIVEL"
D_POR_DESATIVACAO = "POR_DESATIVACAO"
D_POR_EXCLUSAO = "POR_EXCLUSAO"
D_VIROU_ADMIN = "VIROU_ADMIN"
D_VIROU_OPERADOR = "VIROU_OPERADOR"
D_NENHUM = ""

DETALHES = (
    D_CREDENCIAL, D_INEXISTENTE, D_INATIVO, D_ENTRADA_GRANDE,
    D_LIMITE_CONTA, D_LIMITE_IP, D_PAPEL, D_SEM_SESSAO,
    D_AUDITORIA_INDISPONIVEL, D_POR_DESATIVACAO, D_POR_EXCLUSAO,
    D_VIROU_ADMIN, D_VIROU_OPERADOR, D_NENHUM,
)

# Campos cuja ALTERAÇÃO pode ser registrada. Só o nome do campo viaja; o
# conteúdo, nunca. `campos=nome,email` diz o suficiente para uma auditoria e
# não constrói um histórico de dados pessoais.
CAMPOS_AUDITAVEIS = ("nome", "email", "senha", "ativo", "admin")

RETENCAO_PADRAO_DIAS = 180
LOTE_EXPURGO = 500

PAGINA_PADRAO = 50
PAGINA_MAXIMA = 200

AGENTE_MAX = 120


def agora_utc() -> tuple:
    """`(iso_utc, epoch)`.

    UTC sempre, com o `Z` explícito. A tela converte para a hora de Recife
    na hora de mostrar. Guardar hora local seria guardar um número que muda
    de significado no horário de verão e ao mudar o fuso da máquina.
    """
    t = time.time()
    iso = datetime.fromtimestamp(t, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.") + ("%03dZ" % (int(t * 1000) % 1000))
    return iso, t


_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
_ESPACOS = re.compile(r"\s+")


def limpar_agente(bruto) -> str:
    """O user-agent, cortado e sem nada que morda.

    Ele é escrito pelo cliente: entra aqui como texto arbitrário, e um
    arbitrário sem tratamento vira injeção de linha no relatório e caractere
    de controle no terminal de quem lê. Tira-se o controle, colapsa-se o
    espaço e corta-se curto — o suficiente para dizer "Chrome no Windows" e
    curto demais para carregar carga útil (o teto é `AGENTE_MAX`).
    """
    texto = _CONTROLE.sub(" ", str(bruto or ""))
    texto = _ESPACOS.sub(" ", texto).strip()
    return texto[:AGENTE_MAX]


def resumir_agente(agente) -> str:
    """"Chrome no Windows", para a coluna Dispositivo.

    É palpite honesto sobre texto que o cliente escolhe — serve para ler a
    lista de relance, não para afirmar nada. O texto completo continua
    guardado.
    """
    a = (agente or "").lower()
    if not a:
        return "—"
    if "edg/" in a:
        nav = "Edge"
    elif "chrome/" in a and "chromium" not in a:
        nav = "Chrome"
    elif "firefox/" in a:
        nav = "Firefox"
    elif "safari/" in a:
        nav = "Safari"
    elif "curl/" in a:
        nav = "curl"
    elif "python" in a:
        nav = "script"
    else:
        nav = "outro"
    if "windows" in a:
        onde = "Windows"
    elif "android" in a:
        onde = "Android"
    elif "iphone" in a or "ipad" in a:
        onde = "iOS"
    elif "mac os" in a or "macintosh" in a:
        onde = "Mac"
    elif "linux" in a:
        onde = "Linux"
    else:
        return nav
    return "%s no %s" % (nav, onde)


def correlacao() -> str:
    """Um identificador aleatório por requisição.

    Serve para amarrar os eventos de um mesmo pedido — o 403 do portão e o
    `ACESSO_ADMIN_NEGADO`, por exemplo — sem precisar de hora exata nem de
    adivinhação. É aleatório e não deriva de nada: não pode virar, ele
    próprio, um identificador de pessoa.
    """
    return secrets.token_hex(8)


class Indisponivel(Exception):
    """A auditoria não pôde registrar. Muda de significado conforme quem pergunta.

    Para o login, é para ser engolida: ninguém fica de fora do escritório por
    causa de um log. Para alteração de usuário, papel ou senha, é para barrar.
    """


class Livro:
    def __init__(self, caminho, retencao_dias=RETENCAO_PADRAO_DIAS, agora=None):
        self.caminho = str(caminho)
        self.retencao_dias = int(retencao_dias)
        self._trava = threading.RLock()
        self._agora = agora or time.time
        self.falha = ""          # última falha de escrita, em texto curto
        self.falhou_em = 0.0
        self.escritas_falhas = 0
        self._con = None
        try:
            self._abrir()
        except Exception as e:
            self._marcar_falha(e)

    # ── banco ───────────────────────────────────────────────────────────
    def _abrir(self):
        self._con = sqlite3.connect(self.caminho, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")   # log que se perde não é log
        self._criar()

    def _criar(self):
        with self._trava, self._con:
            self._con.execute("""
                CREATE TABLE IF NOT EXISTS evento (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    quando_utc  TEXT    NOT NULL,
                    quando_ts   REAL    NOT NULL,
                    evento      TEXT    NOT NULL,
                    resultado   TEXT    NOT NULL,
                    ator_uid    TEXT,
                    alvo_uid    TEXT,
                    derivado    TEXT,
                    ip          TEXT,
                    agente      TEXT,
                    detalhe     TEXT,
                    quantidade  INTEGER,
                    correlacao  TEXT    NOT NULL
                )
            """)
            for col in ("quando_ts", "evento", "resultado", "ator_uid"):
                self._con.execute(
                    "CREATE INDEX IF NOT EXISTS ix_ev_%s ON evento (%s)"
                    % (col, col))

    def _marcar_falha(self, e):
        self.falha = "%s: %s" % (e.__class__.__name__, str(e)[:160])
        self.falhou_em = self._agora()
        self.escritas_falhas += 1

    # ── escrita ─────────────────────────────────────────────────────────
    def registrar(self, evento, resultado=OK, *, ator_uid="", alvo_uid="",
                  derivado="", ip="", agente="", detalhe=D_NENHUM,
                  quantidade=None, correlacao_id="") -> bool:
        """Grava um evento. NUNCA levanta exceção. Devolve se conseguiu.

        Não levantar é deliberado: esta função é chamada no meio do login, e
        um erro aqui não pode virar um erro lá. Quem PRECISA saber que
        falhou chama `exigir_disponivel()` antes de agir.
        """
        if evento not in EVENTOS:
            # Evento fora do vocabulário é erro de programação, não de
            # operação: grava assim mesmo, marcado, para não sumir a pista.
            detalhe = detalhe or D_NENHUM
            evento = str(evento)[:40]
        if resultado not in RESULTADOS:
            resultado = ERRO
        iso, ts = agora_utc()
        linha = (iso, ts, evento, resultado,
                 str(ator_uid or "") or None,
                 str(alvo_uid or "") or None,
                 str(derivado or "") or None,
                 str(ip or "") or None,
                 limpar_agente(agente) or None,
                 str(detalhe or ""),
                 None if quantidade is None else int(quantidade),
                 correlacao_id or correlacao())
        try:
            with self._trava:
                if self._con is None:
                    self._abrir()
                with self._con:
                    self._con.execute(
                        "INSERT INTO evento (quando_utc, quando_ts, evento, "
                        "resultado, ator_uid, alvo_uid, derivado, ip, agente, "
                        "detalhe, quantidade, correlacao) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        linha)
            # Escrita boa cura a falha anterior: sem isto, uma pasta que
            # sumiu por dez segundos deixaria a auditoria "quebrada" para
            # sempre, e alterar usuário ficaria barrado sem motivo.
            self.falha = ""
            return True
        except Exception as e:
            self._marcar_falha(e)
            return False

    def exigir_disponivel(self):
        """Levanta `Indisponivel` se a auditoria não estiver escrevendo.

        Chamado ANTES de alterar usuário, papel ou senha. A ordem importa:
        recusar depois de gravar a mudança não protegeria nada.

        A prova é uma ESCRITA de verdade, não um `SELECT`: o banco abre para
        leitura e recusa escrita em vários casos que interessam — arquivo
        somente-leitura, pasta que sumiu, disco cheio — e é exatamente esse
        o caso a pegar.

        Escreve-se o `user_version` de volta com o mesmo valor. Toca a página
        de cabeçalho do arquivo, exercita o caminho de escrita inteiro e não
        deixa linha nenhuma para trás — um evento "de teste" no livro seria
        ruído permanente em algo que se lê justamente para achar o incomum.
        """
        try:
            with self._trava:
                if self._con is None:
                    self._abrir()
                (v,) = self._con.execute("PRAGMA user_version").fetchone()
                self._con.execute("PRAGMA user_version = %d" % int(v))
                self._con.commit()
            self.falha = ""
            return True
        except Exception as e:
            self._marcar_falha(e)
            raise Indisponivel(self.falha)

    def saude(self) -> dict:
        """O estado que a tela do administrador mostra."""
        try:
            self.exigir_disponivel()
            escrevendo = True
            motivo = ""
        except Indisponivel as e:
            escrevendo = False
            motivo = str(e)
        total = 0
        mais_antigo = ""
        try:
            with self._trava:
                total, mais_antigo = self._con.execute(
                    "SELECT COUNT(*), MIN(quando_utc) FROM evento").fetchone()
        except Exception:
            pass
        return {"ok": bool(escrevendo), "critico": not escrevendo,
                "motivo": motivo, "arquivo": self.caminho,
                "eventos": total or 0, "mais_antigo": mais_antigo or "",
                "escritas_falhas": self.escritas_falhas,
                "retencao_dias": self.retencao_dias}

    # ── leitura ─────────────────────────────────────────────────────────
    def consultar(self, *, de="", ate="", ator="", evento="", resultado="",
                  pagina=1, por_pagina=PAGINA_PADRAO) -> dict:
        """Página de eventos, do mais recente para o mais antigo.

        Tudo parametrizado. O filtro do usuário vira `?`, nunca texto colado
        na consulta — é a diferença entre um filtro e uma porta aberta.
        """
        por_pagina = max(1, min(int(por_pagina or PAGINA_PADRAO), PAGINA_MAXIMA))
        pagina = max(1, int(pagina or 1))

        onde, args = [], []
        if de:
            onde.append("quando_utc >= ?")
            args.append(str(de))
        if ate:
            onde.append("quando_utc <= ?")
            args.append(str(ate))
        if ator:
            onde.append("(ator_uid = ? OR alvo_uid = ?)")
            args += [str(ator), str(ator)]
        if evento:
            onde.append("evento = ?")
            args.append(str(evento))
        if resultado:
            onde.append("resultado = ?")
            args.append(str(resultado))
        filtro = (" WHERE " + " AND ".join(onde)) if onde else ""

        with self._trava:
            (total,) = self._con.execute(
                "SELECT COUNT(*) FROM evento" + filtro, args).fetchone()
            linhas = self._con.execute(
                "SELECT id, quando_utc, evento, resultado, ator_uid, alvo_uid,"
                " derivado, ip, agente, detalhe, quantidade, correlacao"
                " FROM evento"
                + filtro + " ORDER BY quando_ts DESC, id DESC LIMIT ? OFFSET ?",
                args + [por_pagina, (pagina - 1) * por_pagina]).fetchall()

        campos = ("id", "quando_utc", "evento", "resultado", "ator_uid",
                  "alvo_uid", "derivado", "ip", "agente", "detalhe",
                  "quantidade", "correlacao")
        itens = []
        for linha in linhas:
            d = dict(zip(campos, linha))
            d["evento_rotulo"] = ROTULO_EVENTO.get(d["evento"], d["evento"])
            d["resultado_rotulo"] = ROTULO_RESULTADO.get(d["resultado"],
                                                         d["resultado"])
            d["dispositivo"] = resumir_agente(d.get("agente"))
            itens.append(d)
        return {"itens": itens, "total": total, "pagina": pagina,
                "por_pagina": por_pagina,
                "paginas": max(1, (total + por_pagina - 1) // por_pagina)}

    def atores(self) -> list:
        """Quem aparece no livro, para montar o filtro da tela."""
        with self._trava:
            linhas = self._con.execute(
                "SELECT DISTINCT ator_uid FROM evento WHERE ator_uid IS NOT NULL"
                " ORDER BY ator_uid").fetchall()
        return [l[0] for l in linhas]

    # ── retenção ────────────────────────────────────────────────────────
    def expurgar(self, dias=None, lote=LOTE_EXPURGO) -> int:
        """Apaga o que passou da idade. Em lotes, e deixa registro de si.

        Em lotes porque um `DELETE` de meio milhão de linhas segura a trava
        do banco por segundos — e a trava é a mesma por onde passa o login.

        A remoção é por IDADE e só por idade: `quando_ts < corte`. Não há
        caminho para apagar um evento escolhido, e é isso que faz o livro
        valer alguma coisa.
        """
        dias = int(self.retencao_dias if dias is None else dias)
        corte = self._agora() - dias * 86400
        total = 0
        try:
            while True:
                with self._trava, self._con:
                    cur = self._con.execute(
                        "DELETE FROM evento WHERE id IN ("
                        "  SELECT id FROM evento WHERE quando_ts < ? LIMIT ?)",
                        (corte, int(lote)))
                    n = cur.rowcount or 0
                total += n
                if n < lote:
                    break
        except Exception as e:
            self._marcar_falha(e)
        # O registro técnico da própria limpeza: sem ele, um livro que
        # encurtou é indistinguível de um livro adulterado.
        self.registrar(RETENCAO_EXECUTADA, OK if not self.falha else ERRO,
                       detalhe=D_NENHUM, quantidade=total)
        return total

    def manutencao(self, intervalo_s=86400) -> int:
        """Roda a retenção no máximo uma vez por dia. Devolve quantas apagou.

        O intervalo existe por causa do próprio registro: o expurgo se
        anota, e o FISCALE reinicia várias vezes por dia. Sem o freio, o
        livro se encheria de "limpeza executada, 0 apagado" — ruído num
        lugar que se lê justamente para achar o que destoa.

        Devolve -1 quando não era hora.
        """
        try:
            with self._trava:
                linha = self._con.execute(
                    "SELECT MAX(quando_ts) FROM evento WHERE evento = ?",
                    (RETENCAO_EXECUTADA,)).fetchone()
            ultima = (linha and linha[0]) or 0.0
            if self._agora() - ultima < intervalo_s:
                return -1
        except Exception as e:
            self._marcar_falha(e)
            return -1
        return self.expurgar()

    def fechar(self):
        with self._trava:
            try:
                if self._con is not None:
                    self._con.close()
            except Exception:
                pass
            self._con = None


def abrir(pasta_dados, retencao_dias=RETENCAO_PADRAO_DIAS, agora=None) -> Livro:
    os.makedirs(str(pasta_dados), exist_ok=True)
    return Livro(os.path.join(str(pasta_dados), ARQUIVO),
                 retencao_dias=retencao_dias, agora=agora)
