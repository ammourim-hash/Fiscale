#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fase 3 — registro de acessos e auditoria de segurança.

    python teste_auditoria.py

O QUE SE PROVA
    Que os eventos de segurança são gravados, que NADA identificável em texto
    claro entra no banco, que só o administrador consulta, e que as duas
    regras opostas de disponibilidade valem: auditoria quebrada NÃO impede
    entrar, e IMPEDE alterar usuário, papel ou senha.

    Nenhuma senha, e-mail ou identificador real aparece aqui.
"""
from __future__ import annotations

import ast
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_auditoria as aud                       # noqa: E402
import fiscale_papeis as fp                           # noqa: E402
import fiscale_pimenta as pim                         # noqa: E402
import fiscale_sessoes as fs                          # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                              # noqa: E402


def _connect_proibido(self, endereco, *a, **kw):
    raise AssertionError("teste tentou abrir rede para " + str(endereco))


_socket.socket.connect = _connect_proibido


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        _erros.append(desc)
        print("  FALHOU  " + desc)


def igual(a, b, desc):
    ok(a == b, "%s  (obtive %r, esperava %r)" % (desc, a, b) if a != b else desc)


def _fonte(nome):
    return (RAIZ / nome).read_text(encoding="utf-8")


def _bloco(inicio, fim, fonte=None):
    """Um trecho do servidor SEM comentários — código, não prosa.

    Ler o arquivo cru já me traiu: a regra estava explicada num comentário e
    o teste dava por implementada.
    """
    f = fonte if fonte is not None else _fonte("fiscale_server.py")
    i = f.index(inicio)
    j = f.index(fim, i)
    return "\n".join(l for l in f[i:j].splitlines()
                     if not l.strip().startswith("#"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="fiscale_aud_"))
    servidor = _fonte("fiscale_server.py")

    # ── 1: sucesso, falha, bloqueio, logout ─────────────────────────────
    secao("1 · Sucesso, falha, bloqueio e saída")
    L = aud.abrir(tmp)
    ctx = {"ip": "192.168.0.7", "agente": "Mozilla/5.0 (Windows NT 10.0) Chrome/120"}
    ok(L.registrar(aud.LOGIN_OK, aud.OK, ator_uid="maria", **ctx), "grava entrada")
    ok(L.registrar(aud.LOGIN_RECUSADO, aud.RECUSADO, ator_uid="maria",
                   detalhe=aud.D_CREDENCIAL, **ctx), "grava entrada recusada")
    ok(L.registrar(aud.LOGIN_BLOQUEADO, aud.BLOQUEADO, ator_uid="maria",
                   detalhe=aud.D_LIMITE_CONTA, **ctx), "grava bloqueio")
    ok(L.registrar(aud.LOGOUT, aud.OK, ator_uid="maria", **ctx), "grava saída")
    r = L.consultar()
    igual(r["total"], 4, "os quatro eventos estão no livro")
    igual([i["evento"] for i in r["itens"]],
          [aud.LOGOUT, aud.LOGIN_BLOQUEADO, aud.LOGIN_RECUSADO, aud.LOGIN_OK],
          "e vêm do mais recente para o mais antigo")
    ok(all(i["correlacao"] and len(i["correlacao"]) == 16 for i in r["itens"]),
       "cada evento tem identificador de correlação aleatório")
    ok(len({i["correlacao"] for i in r["itens"]}) == 4,
       "e eles são distintos quando não vêm do mesmo pedido")

    # ── 2: inexistente sem texto identificável ──────────────────────────
    secao("2 · Login inexistente entra pela chave derivada, nunca em claro")
    p = pim.abrir(tmp)
    digitado = "carlos.pereira@exemplo.invalido"
    derivada = p.chave_do_desconhecido(digitado)
    L.registrar(aud.LOGIN_RECUSADO, aud.RECUSADO, derivado=derivada,
                detalhe=aud.D_INEXISTENTE, **ctx)
    linha = L.consultar()["itens"][0]
    ok(linha["derivado"] == derivada and not linha["ator_uid"],
       "grava a chave derivada e nenhum ator")
    ok(linha["derivado"].startswith("~"), "e ela é reconhecível como derivada")

    # O que a ROTA faz, não só o que o módulo aceita.
    bloco_login = _bloco('if rota == "/api/login":', 'if rota == "/api/logout":',
                         servidor)
    ok('derivado="" if alvo else chave' in bloco_login,
       "a rota só usa a chave derivada quando NÃO achou conta")
    ok("ator_uid=alvo" in bloco_login,
       "e usa o uid quando achou")
    ok("derivado=usuario" not in bloco_login
       and "ator_uid=usuario" not in bloco_login,
       "o texto digitado nunca vai para o livro")

    # ── 3: e-mail entra pelo uid ────────────────────────────────────────
    secao("3 · Entrada por e-mail é registrada pelo `uid`")
    ok("ator_uid=uid" in bloco_login,
       "o sucesso registra o uid devolvido por verificar_login")
    ok("resolver_usuario" in bloco_login and "alvo, _reg" in bloco_login,
       "o alvo vem da resolução (login OU e-mail), não do que foi digitado")

    # ── 4: sessão expirada e invalidada ─────────────────────────────────
    secao("4 · Sessão expirada e sessão invalidada")
    ses = fs.Sessoes(str(tmp / "s.db"), validade_s=1)
    tok = ses.criar("joana")
    igual(ses.get_com_motivo(tok)[1], "OK", "sessão viva responde OK")
    time.sleep(1.2)
    quem, motivo = ses.get_com_motivo(tok)
    igual(motivo, "EXPIRADA", "sessão vencida se identifica como EXPIRADA")
    igual(quem, "joana", "e ainda diz de quem era, para o registro")
    igual(ses.get_com_motivo(tok)[1], "DESCONHECIDA",
          "e sai UMA vez só — a linha já foi apagada")
    igual(ses.get_com_motivo("")[1], "AUSENTE",
          "sem cookie é AUSENTE, não expirada")

    # O DEFEITO QUE ISTO FIXA
    #   `get_com_motivo` devolve o dono da sessão EXPIRADA de propósito, para
    #   a auditoria poder dizer de quem ela era. Uma versão minha de `get()`
    #   repassou esse nome adiante, e sessão vencida continuava autenticando.
    #   A suíte de sessões pegou; este teste garante que não volta.
    ses2 = fs.Sessoes(str(tmp / "s2.db"), validade_s=1)
    tok2 = ses2.criar("carla")
    igual(ses2.get(tok2), "carla", "sessão viva autentica")
    time.sleep(1.2)
    ok(ses2.get(tok2) is None,
       "sessão EXPIRADA não autentica — `get()` devolve None")
    ses3 = fs.Sessoes(str(tmp / "s3.db"), validade_s=1)
    tok3 = ses3.criar("carla")
    time.sleep(1.2)
    igual(ses3.get_com_motivo(tok3), ("carla", "EXPIRADA"),
          "mas o motivo ainda diz de quem era, que é o que a auditoria usa")
    ses2.fechar(); ses3.fechar()

    ses.criar("joana"); ses.criar("joana")
    igual(ses.revogar_usuario("joana"), 2,
          "revogar devolve QUANTAS sessões caíram")
    ok("get_com_motivo" in servidor and "SESSAO_EXPIRADA" in servidor,
       "o servidor usa o motivo para registrar a expiração")
    ok("SESSAO_INVALIDADA" in servidor and "quantidade=caidas" in servidor,
       "e registra quantas sessões foram encerradas pelo sistema")
    ses.fechar()

    # ── 5 e 6: senha, usuário e papel ───────────────────────────────────
    secao("5-6 · Senha, criação, edição, desativação, exclusão e papel")
    do_post = servidor[servidor.index("def do_POST"):]
    b_user = _bloco('if rota == "/api/usuarios":',
                    'if rota == "/api/usuarios/excluir":', do_post)
    b_excl = _bloco('if rota == "/api/usuarios/excluir":',
                    'if rota == "/api/senha":', do_post)
    b_senha = _bloco('if rota == "/api/senha":', 'if self._rota_nfse', do_post)

    ok("SENHA_TROCADA" in b_senha, "trocar a própria senha é registrado")
    ok("SENHA_REDEFINIDA" in b_user,
       "redefinição pelo administrador tem evento próprio")
    ok("USUARIO_CRIADO" in b_user, "criação é registrada")
    ok("USUARIO_EDITADO" in b_user, "edição é registrada")
    ok("USUARIO_ATIVADO" in b_user and "USUARIO_DESATIVADO" in b_user,
       "ativação e desativação têm evento próprio")
    ok("PAPEL_ALTERADO" in b_user, "alteração de papel é registrada")
    ok("USUARIO_EXCLUIDO" in b_excl, "exclusão é registrada")

    # A regra que importa na edição: NOMES de campos, nunca conteúdo.
    ok("CAMPOS_AUDITAVEIS" in b_user and 'detalhe=",".join(sorted(mudou))' in b_user,
       "a edição grava só os NOMES dos campos alterados")
    for vazado in ('detalhe=email', 'antes.get("email"))', 'reg["email"])',
                   'alvo_uid=email', 'detalhe=reg'):
        ok(vazado not in b_user, "a edição não carrega %r para o livro" % vazado)

    # ── 7: nada de senha, hash, sal, cookie, token ──────────────────────
    secao("7 · Nem senha, nem hash, nem sal, nem cookie, nem token no banco")
    L.registrar(aud.SENHA_TROCADA, aud.OK, ator_uid="maria", alvo_uid="maria",
                **ctx)
    L.registrar(aud.USUARIO_EDITADO, aud.OK, ator_uid="admin",
                alvo_uid="maria", detalhe="email,nome", **ctx)
    con = sqlite3.connect(str(tmp / aud.ARQUIVO))
    colunas = [c[1] for c in con.execute("PRAGMA table_info(evento)")]
    con.close()
    for proibida in ("senha", "hash", "sal", "cookie", "token", "autorizacao",
                     "corpo", "certificado"):
        ok(proibida not in colunas,
           "não existe coluna %r na tabela de eventos" % proibida)
    igual(sorted(colunas),
          sorted(["id", "quando_utc", "quando_ts", "evento", "resultado",
                  "ator_uid", "alvo_uid", "derivado", "ip", "agente",
                  "detalhe", "quantidade", "correlacao"]),
          "o esquema é exatamente o declarado")

    L.fechar()
    bruto = (tmp / aud.ARQUIVO).read_bytes().lower()
    for pedaco in (digitado, b"carlos", b"pereira", b"exemplo.invalido"):
        alvo = pedaco.encode() if isinstance(pedaco, str) else pedaco
        ok(alvo not in bruto,
           "%r não aparece no arquivo" % alvo.decode()[:24])
    # E o servidor não pode mandar nada disso para cá.
    ok('"agente": self._agente()' in servidor
       and "limpar_agente" in _bloco("def _agente", "def _correlacao", servidor),
       "o servidor só manda o user-agent já limpo")
    for proibido in ('Cookie"', "_token_sessao()," , "corpo=corpo",
                     "Authorization"):
        ok("auditar(" not in servidor or proibido not in servidor.split("auditar(")[-1][:400],
           "nenhuma chamada de registro carrega %r" % proibido)
    L = aud.Livro(str(tmp / aud.ARQUIVO))

    # ── 8 e 9 e 18: portão de papéis ────────────────────────────────────
    secao("8-9-18 · Só o administrador consulta")
    for rota in ("/api/auditoria", "/api/auditoria/saude",
                 "/seguranca_acessos.html"):
        igual(fp.classificar("GET", rota), fp.ADMIN,
              "%s é classificada como exclusiva do administrador" % rota)
        ok(not fp.pode(fp.OPERADOR, "GET", rota),
           "o operador NÃO alcança %s" % rota)
        ok(fp.pode(fp.ADMIN, "GET", rota),
           "o administrador alcança %s" % rota)
    # Segunda tranca dentro da própria rota.
    b_aud = _bloco('if rota == "/api/auditoria":', 'if rota == "/api/diagnostico"',
                   servidor)
    ok("eh_admin(self._usuario_sessao())" in b_aud and "403" in b_aud,
       "a rota confere o papel por conta própria, além do portão")
    # 9: sem sessão é 401, e isso vem do fluxo geral.
    ok("_exigir_login(rota)" in servidor
       and '"erro": "não autenticado"' in servidor,
       "quem não tem sessão recebe 401 antes de qualquer rota")
    # Não existe exportação nem remoção pela interface.
    ok("/api/auditoria/exportar" not in servidor
       and "/api/auditoria/apagar" not in servidor,
       "não há rota de exportação nem de remoção")
    ok(not any(m == "DELETE" and "auditoria" in r
               for m, r in list(fp._ADMIN_EXATAS) + list(fp._ADMIN_PREFIXOS)),
       "e nenhuma rota DELETE de auditoria foi classificada")

    # ── 10: paginação e filtros ─────────────────────────────────────────
    secao("10 · Paginação e filtros")
    for i in range(120):
        L.registrar(aud.LOGIN_OK if i % 2 else aud.LOGIN_RECUSADO,
                    aud.OK if i % 2 else aud.RECUSADO,
                    ator_uid="ana" if i % 3 else "bruno", **ctx)
    r = L.consultar(por_pagina=50, pagina=1)
    igual(len(r["itens"]), 50, "primeira página traz 50")
    igual(r["por_pagina"], 50, "o padrão é 50")
    r2 = L.consultar(por_pagina=50, pagina=2)
    ok({i["id"] for i in r["itens"]}.isdisjoint({i["id"] for i in r2["itens"]}),
       "a segunda página não repete a primeira")
    igual(L.consultar(por_pagina=500)["por_pagina"], 200,
          "por_pagina é limitado a 200")
    # Zero cai no padrão, e não em 1: `?por_pagina=0` é campo vazio ou
    # parâmetro ausente, e devolver UMA linha por página seria uma tela
    # inútil onde se queria a tela normal.
    igual(L.consultar(por_pagina=0)["por_pagina"], aud.PAGINA_PADRAO,
          "por_pagina zero cai no padrão")
    igual(L.consultar(por_pagina=-10)["por_pagina"], 1,
          "e valor negativo é preso no mínimo")
    igual(L.consultar(pagina=-3)["pagina"], 1, "página negativa vira 1")

    so_ana = L.consultar(ator="ana", por_pagina=200)
    ok(all(i["ator_uid"] == "ana" or i["alvo_uid"] == "ana"
           for i in so_ana["itens"]), "o filtro por usuário funciona")
    so_rec = L.consultar(resultado=aud.RECUSADO, por_pagina=200)
    ok(all(i["resultado"] == aud.RECUSADO for i in so_rec["itens"]),
       "o filtro por resultado funciona")
    so_ev = L.consultar(evento=aud.LOGIN_BLOQUEADO, por_pagina=200)
    ok(all(i["evento"] == aud.LOGIN_BLOQUEADO for i in so_ev["itens"]),
       "o filtro por evento funciona")
    ok(L.consultar(de="2099-01-01T00:00:00.000Z")["total"] == 0,
       "o filtro por período funciona")
    # Injeção: o filtro é parâmetro, não texto colado.
    veneno = "' OR 1=1 --"
    ok(L.consultar(ator=veneno, por_pagina=200)["total"] == 0,
       "aspas no filtro não viram consulta — é `?`, não concatenação")
    ok(L.consultar(evento="x'; DROP TABLE evento; --")["total"] == 0
       and L.consultar()["total"] > 0,
       "e a tabela continua de pé depois da tentativa")

    # ── 11: UTC ─────────────────────────────────────────────────────────
    secao("11 · As datas são gravadas em UTC")
    iso, ts = aud.agora_utc()
    ok(iso.endswith("Z"), "o carimbo termina em Z")
    ok(len(iso) == 24 and iso[10] == "T", "e tem forma ISO-8601 com milissegundo")
    from datetime import datetime, timezone
    d = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    ok(abs(d.timestamp() - ts) < 2,
       "o carimbo UTC bate com o instante gravado")
    ok(abs(d.timestamp() - time.time()) < 5,
       "e corresponde ao agora, não ao fuso local somado")
    ok("timezone.utc" in _fonte("fiscale_auditoria.py")
       and "localtime" not in _fonte("fiscale_auditoria.py"),
       "o módulo nunca usa hora local para gravar")

    # ── 12: user-agent grande ou torto ──────────────────────────────────
    secao("12 · User-agent grande, torto ou com caractere de controle")
    gigante = "A" * 100_000
    igual(len(aud.limpar_agente(gigante)), aud.AGENTE_MAX,
          "user-agent gigante é cortado no teto")
    ok("\n" not in aud.limpar_agente("linha1\nlinha2\r\nlinha3"),
       "quebra de linha não passa — seria injeção de linha no relatório")
    ok("\x00" not in aud.limpar_agente("antes\x00depois"),
       "byte nulo não passa")
    igual(aud.limpar_agente("  muito   espaço  "), "muito espaço",
          "espaço repetido é colapsado")
    igual(aud.limpar_agente(None), "", "None vira vazio, não estoura")
    igual(aud.limpar_agente(12345), "12345", "número não estoura")
    L.registrar(aud.LOGIN_OK, aud.OK, ator_uid="ana", agente=gigante, ip="1.2.3.4")
    guardado = L.consultar(por_pagina=1)["itens"][0]["agente"]
    ok(len(guardado) <= aud.AGENTE_MAX, "e o banco recebe já cortado")
    igual(aud.resumir_agente("Mozilla/5.0 (Windows NT 10.0) Chrome/120"),
          "Chrome no Windows", "o resumo é legível")
    igual(aud.resumir_agente(""), "—", "e vazio não vira palpite")

    # ── 13: concorrência ────────────────────────────────────────────────
    secao("13 · Concorrência sem perder evento")
    antes = L.consultar()["total"]

    def martelar():
        for _ in range(25):
            L.registrar(aud.LOGIN_OK, aud.OK, ator_uid="paralelo", ip="1.1.1.1")

    fios = [threading.Thread(target=martelar) for _ in range(8)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    igual(L.consultar()["total"] - antes, 200,
          "200 gravações simultâneas, nenhuma perdida")
    ids = [i["id"] for i in L.consultar(ator="paralelo", por_pagina=200)["itens"]]
    igual(len(set(ids)), len(ids), "e nenhum id repetido")

    # ── 14: retenção ────────────────────────────────────────────────────
    secao("14 · A retenção apaga o vencido e só o vencido")
    tmp2 = Path(tempfile.mkdtemp(prefix="fiscale_ret_"))
    R = aud.Livro(str(tmp2 / "a.db"))
    R.registrar(aud.LOGIN_OK, aud.OK, ator_uid="recente")
    con = sqlite3.connect(str(tmp2 / "a.db"))
    velho = time.time() - 200 * 86400
    with con:
        for i in range(1200):
            con.execute(
                "INSERT INTO evento (quando_utc, quando_ts, evento, resultado,"
                " ator_uid, detalhe, correlacao) VALUES"
                " ('2026-01-01T00:00:00.000Z',?,?,?,?,'',?)",
                (velho, aud.LOGIN_OK, aud.OK, "antigo", "c%d" % i))
    con.close()
    igual(R.consultar(por_pagina=1)["total"], 1201, "1.201 eventos no livro")
    apagados = R.expurgar(dias=180, lote=500)
    igual(apagados, 1200, "apagou os 1.200 vencidos, em lotes de 500")
    restantes = R.consultar(por_pagina=200)
    ok(any(i["ator_uid"] == "recente" for i in restantes["itens"]),
       "o evento recente continua lá")
    ok(not any(i["ator_uid"] == "antigo" for i in restantes["itens"]),
       "e nenhum vencido sobrou")
    ok(any(i["evento"] == aud.RETENCAO_EXECUTADA for i in restantes["itens"]),
       "a própria limpeza deixou registro técnico")
    reg_limpeza = [i for i in restantes["itens"]
                   if i["evento"] == aud.RETENCAO_EXECUTADA][0]
    igual(reg_limpeza["quantidade"], 1200, "com a contagem do que apagou")
    igual(R.manutencao(), -1, "e não roda de novo no mesmo dia")
    igual(aud.RETENCAO_PADRAO_DIAS, 180, "a retenção inicial é de 180 dias")

    # Só existe remoção por IDADE.
    fonte_aud = _fonte("fiscale_auditoria.py")
    arvore = ast.parse(fonte_aud)
    metodos = [n.name for n in ast.walk(arvore) if isinstance(n, ast.FunctionDef)]
    for proibido in ("editar", "atualizar", "apagar", "remover", "corrigir"):
        ok(proibido not in metodos,
           "não existe método %r — o livro é só de acréscimo" % proibido)
    deletes = [t.value for t in ast.walk(arvore)
               if isinstance(t, ast.Constant) and isinstance(t.value, str)
               and "DELETE FROM evento" in t.value]
    igual(len(deletes), 1, "há exatamente UM DELETE no módulo inteiro")
    ok("quando_ts < ?" in deletes[0],
       "e ele apaga por IDADE, não por evento escolhido")
    ok(not any(isinstance(t, ast.Constant) and isinstance(t.value, str)
               and "UPDATE evento" in t.value for t in ast.walk(arvore)),
       "e não existe UPDATE em evento nenhum")
    R.fechar()

    # ── 15, 16, 17: falha do banco ──────────────────────────────────────
    secao("15-16-17 · O que acontece quando a auditoria quebra")
    tmp3 = Path(tempfile.mkdtemp(prefix="fiscale_quebra_"))
    Q = aud.Livro(str(tmp3 / "a.db"))
    ok(Q.saude()["ok"] and not Q.saude()["critico"], "saudável antes de quebrar")

    # Quebra de verdade: a conexão é fechada por baixo dos pés. Qualquer uso
    # levanta ProgrammingError, que é o que acontece na prática quando o
    # arquivo some ou a pasta fica inacessível.
    Q._con.close()

    saude = Q.saude()
    ok(saude["critico"] is True, "a falha aparece como estado CRÍTICO")
    ok(bool(saude["motivo"]), "com motivo técnico preenchido")
    ok(saude["ok"] is False, "e `ok` é False, que é o que a tela lê")
    ok(Q.registrar(aud.LOGIN_OK, aud.OK, ator_uid="x") is False,
       "registrar devolve False em vez de estourar")
    ok(Q.escritas_falhas > 0, "e a falha fica contada")
    try:
        Q.exigir_disponivel()
        ok(False, "exigir_disponivel deveria ter levantado")
    except aud.Indisponivel:
        ok(True, "exigir_disponivel levanta Indisponivel")

    # 16 · O LOGIN CONTINUA. A prova é estrutural: `registrar` não levanta
    # (acima), e a rota de login chama `auditar`, que é `registrar`. Não há
    # nenhuma chamada a `exigir_disponivel` no caminho de entrar.
    ok("auditar(" in bloco_login, "a rota de login registra")
    ok("exigir_disponivel" not in bloco_login
       and "_auditoria_ou_recusa" not in bloco_login,
       "e NÃO exige a auditoria para deixar entrar")
    arvore_reg = [n for n in ast.walk(ast.parse(fonte_aud))
                  if isinstance(n, ast.FunctionDef) and n.name == "registrar"][0]
    ok(not any(isinstance(n, ast.Raise) for n in ast.walk(arvore_reg)),
       "`registrar` não tem um único `raise` — não derruba quem a chama")
    corpo_auditar = _bloco("def auditar(", "AUDITORIA.manutencao()", servidor)
    ok("registrar" in corpo_auditar and "exigir_disponivel" not in corpo_auditar,
       "o atalho `auditar` também não exige nada")

    # 17 · ALTERAÇÃO ADMINISTRATIVA É RECUSADA sem registro.
    porta = _bloco("def _auditoria_ou_recusa", "def _token_sessao", servidor)
    ok("exigir_disponivel" in porta and "503" in porta,
       "a porta prova a escrita e responde 503 quando não dá")
    ok("Indisponivel" in porta, "tratando a exceção do módulo")
    for nome, bloco in (("/api/usuarios", b_user),
                        ("/api/usuarios/excluir", b_excl),
                        ("/api/senha", b_senha)):
        ok("_auditoria_ou_recusa()" in bloco,
           "%s é recusada quando a auditoria não grava" % nome)
        # A ordem é o ponto: recusar depois de gravar não protege nada.
        pos_porta = bloco.index("_auditoria_ou_recusa()")
        grava = min([bloco.index(m) for m in ("salvar_usuarios(", "del todos[")
                     if m in bloco] or [len(bloco)])
        ok(pos_porta < grava,
           "e a recusa vem ANTES de gravar em %s" % nome)

    Q.fechar()

    # ── 18: cobertura das rotas novas ───────────────────────────────────
    secao("18 · Nenhuma rota nova ficou sem classificação")
    novas = []
    for linha in servidor.splitlines():
        t = linha.strip()
        if t.startswith('if rota == "/api/auditoria'):
            novas.append(t.split('"')[1])
    ok(novas, "achei as rotas novas no servidor (%d)" % len(novas))
    for rota in novas:
        igual(fp.classificar("GET", rota), fp.ADMIN,
              "%s classificada como admin" % rota)
    ok((RAIZ / "web" / "seguranca_acessos.html").exists(),
       "a tela existe")
    ok("/seguranca_acessos.html" in _fonte("fiscale_papeis.py"),
       "e a página está na lista de páginas de administrador")
    home = _fonte("web/home_portal.html")
    ok("btnSeguranca" in home and "seguranca_acessos.html" in home,
       "a Home tem o caminho para a tela")
    i_btn = home.index("btnSeguranca")
    ok("display:none" in home[i_btn:i_btn + 400],
       "e o botão nasce escondido, revelado só para quem é admin")
    ok("q.admin" in home[:home.index("btnSeguranca", i_btn + 1)]
       or "if(q.admin)" in home, "a revelação depende de `q.admin`")

    # ── 19: nada de fora foi mexido ─────────────────────────────────────
    secao("19 · Sessões, tentativas, usuários e ELO inalterados")
    ok("VALIDADE_PADRAO_S = 12 * 3600" in _fonte("fiscale_sessoes.py"),
       "a validade da sessão continua 12 horas")
    ok("token_hash" in _fonte("fiscale_sessoes.py")
       and "INSERT INTO sessao (token_hash" in _fonte("fiscale_sessoes.py"),
       "a sessão continua guardando o HASH do token, nunca o token")
    tt_fonte = _fonte("fiscale_tentativas.py")
    for regra in ("BLOQUEIO_USUARIO = 8", "BLOQUEIO_S = 5 * 60",
                  "LIMITE_IP = 30", "BLOQUEIO_IP_S = 10 * 60",
                  "LIVRES_USUARIO = 3"):
        ok(regra in tt_fonte, "o limite %r não mudou" % regra)
    fu_fonte = _fonte("fiscale_usuarios.py")
    ok('CAMPOS_INTOCAVEIS = ("sal", "hash", "admin")' in fu_fonte,
       "os campos intocáveis do cadastro continuam os mesmos")
    # Primeiro argumento `uid`; a validade nomeada do "Manter conectado"
    # (portal 13/09/2026) pode vir depois sem mudar quem é a sessão.
    ok(__import__("re").search(r"SESSOES\.criar\(uid[,)]", bloco_login),
       "a sessão continua nascendo do uid — o `sub` do ELO não mudou")
    # A checagem é por IMPORT e por referência ao módulo, não pela palavra
    # "sub" — que aparece dentro de "subir" e faria o teste mentir.
    arvore_aud = ast.parse(fonte_aud)
    importados = {n.names[0].name for n in ast.walk(arvore_aud)
                  if isinstance(n, ast.Import)}
    importados |= {n.module or "" for n in ast.walk(arvore_aud)
                   if isinstance(n, ast.ImportFrom)}
    ok(not any("elo" in m for m in importados),
       "a auditoria não importa nada do ELO")
    ok("elo" not in fonte_aud.lower().replace("pelo", "").replace("aquele", ""),
       "e não menciona o ELO em lugar nenhum")
    ok("MINIMO = 8" in _fonte("fiscale_senhas.py"),
       "a política de 8 caracteres continua de pé")

    secao("Extra · O `.fbk` não leva a auditoria nesta fase")
    bk = _fonte("fiscale_backup.py")
    i_fora = bk.index("ARQUIVOS_FORA")
    trecho = bk[i_fora:i_fora + 1400]
    for arquivo in ('"auditoria.db"', '"tentativas.db"', '".pimenta-login"'):
        ok(arquivo in trecho, "%s está fora do pacote de backup" % arquivo)

    secao("Extra · A tela não monta HTML com texto de fora")
    tela = _fonte("web/seguranca_acessos.html")
    ok("textContent" in tela, "a tela escreve por textContent")
    corpo_js = tela[tela.index("function celula"):]
    ok("innerHTML" not in corpo_js.split("document.querySelector")[0],
       "e não monta linha por innerHTML — o user-agent vem de fora")

    secao("Nenhum teste desta suíte foi à rede")
    try:
        _socket.socket().connect(("127.0.0.1", 9))
        ok(False, "a trava de rede não estava ativa")
    except AssertionError:
        ok(True, "a trava estava mesmo ativa (conferido)")

    L.fechar()
    for pasta in (tmp, tmp2, tmp3):
        shutil.rmtree(pasta, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
