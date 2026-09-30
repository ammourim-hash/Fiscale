#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fase 1 do login — entrar pelo e-mail sem quebrar nada.

    python teste_login_email.py

OS ONZE CASOS PEDIDOS
    1. login antigo                    7. usuário inativo
    2. login por e-mail                8. sessão guarda o uid antigo
    3. e-mail com maiúsculas/espaços   9. token do ELO mantém o sub antigo
    4. e-mail duplicado               10. migração não toca hash nem sal
    5. formato inválido               11. duas gravações concorrentes
    6. usuário antigo sem e-mail

A INVARIANTE QUE ESTA SUÍTE PROTEGE
    A chave do dicionário é a identidade interna e **não muda nunca**. O
    e-mail é um caminho de entrada. Sessão, permissão, auditoria e o `sub` do
    Elo apontam para o `uid` — sempre.

    Se um dia alguém "simplificar" isso usando o e-mail como identidade,
    trocar o e-mail de uma pessoa quebraria as sessões dela, o histórico e o
    vínculo com o Elo de uma vez só. Estes testes existem para essa mudança
    não passar despercebida.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_usuarios as us                        # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                             # noqa: E402


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
    ok(a == b, desc if a == b else desc + "  (obtive %r, esperava %r)" % (a, b))


# ── um cadastro de mentira, no formato ANTIGO ───────────────────────────────
# Sem `uid`, sem `nome`, sem `email`, sem `ativo` — exatamente como está hoje
# na máquina do escritório. Os "hashes" são fictícios: a suíte nunca precisa
# de senha de verdade para provar o que precisa provar.
CADASTRO_ANTIGO = {
    "admin":  {"sal": "a" * 32, "hash": "h-do-admin",  "admin": True},
    "maria":  {"sal": "b" * 32, "hash": "h-da-maria",  "admin": False},
    "joao":   {"sal": "c" * 32, "hash": "h-do-joao"},
}


def escrever(caminho, dados):
    Path(caminho).write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fiscale_login_"))
    arq = tmp / "usuarios.json"
    escrever(arq, CADASTRO_ANTIGO)

    # ═══════════════════════════════════════════════════════════════════
    secao("O cadastro antigo é lido sem exigir migração")
    atuais = us.carregar(arq)
    igual(sorted(atuais), ["admin", "joao", "maria"], "os três usuários vieram")
    for uid in atuais:
        igual(atuais[uid]["uid"], uid,
              "%r recebeu `uid` igual à chave" % uid)
        igual(atuais[uid]["ativo"], True, "%r nasce ativo" % uid)
        igual(atuais[uid]["email"], "", "%r nasce sem e-mail" % uid)
    ok(us.esta_ativo({}), "registro sem a marca `ativo` conta como ATIVO — "
                          "o padrão nunca pode trancar alguém para fora")

    # ── 1 ──────────────────────────────────────────────────────────────
    secao("1. Login antigo continua entrando")
    uid, reg = us.resolver(atuais, "maria")
    igual(uid, "maria", "achou pelo login antigo")
    ok(reg is atuais["maria"], "e devolveu o registro dela")
    igual(us.resolver(atuais, "MARIA")[0], "maria",
          "digitado com maiúscula também acha")

    # ── 2 e 3 ──────────────────────────────────────────────────────────
    secao("2 e 3. Login por e-mail, com maiúsculas e espaços")
    atuais["maria"]["email"] = "maria@escritorio.com.br"
    for digitado in ("maria@escritorio.com.br",
                     "MARIA@ESCRITORIO.COM.BR",
                     "  Maria@Escritorio.Com.Br  ",
                     "\tmaria@escritorio.com.br\n"):
        igual(us.resolver(atuais, digitado)[0], "maria",
              "entra com %r" % digitado)
    igual(us.normalizar_email("  Maria@X.COM  "), "maria@x.com",
          "a normalização tira espaço e baixa a caixa")

    secao("O login antigo VENCE o e-mail")
    # Se alguém cadastrar como e-mail de uma pessoa algo que é o login de
    # outra, a dona do login continua entrando na própria conta.
    confuso = dict(atuais)
    confuso["joao"] = dict(confuso["joao"], email="maria")
    igual(us.resolver(confuso, "maria")[0], "maria",
          "a dona do login não perde a conta para um e-mail parecido")

    # ── 4 ──────────────────────────────────────────────────────────────
    secao("4. E-mail duplicado é recusado")
    igual(us.email_em_uso(atuais, "maria@escritorio.com.br"), "maria",
          "o e-mail já tem dono")
    igual(us.email_em_uso(atuais, "MARIA@ESCRITORIO.COM.BR "), "maria",
          "e a comparação é normalizada — não dá para duplicar mudando a caixa")
    igual(us.email_em_uso(atuais, "maria@escritorio.com.br",
                          exceto_uid="maria"), "",
          "mas ela pode salvar a própria tela sem colidir consigo mesma")
    igual(us.email_em_uso(atuais, "outro@x.com"), "", "e-mail livre é livre")
    igual(us.email_em_uso(atuais, ""), "", "e-mail vazio não colide com nada")

    # ── 5 ──────────────────────────────────────────────────────────────
    secao("5. Formato inválido")
    for bom in ("a@b.co", "maria@escritorio.com.br", "m.r+fiscal@x.io",
                "MARIA@X.COM", " maria@x.com "):
        ok(us.email_valido(bom), "%r é aceito" % bom)
    for ruim in ("maria", "maria@", "@x.com", "maria@x", "a b@x.com",
                 "a@@x.com", "a@x .com", "a@x.com b"):
        ok(not us.email_valido(ruim), "%r é recusado" % ruim)
    ok(not us.email_valido(""), "vazio não é válido...")
    ok(us.normalizar_email(None) == "",
       "...mas também não é erro: é AUSENTE, e ausente é permitido")
    ok(not us.email_valido("a" * 250 + "@x.com"), "endereço absurdo é recusado")

    # ── 6 ──────────────────────────────────────────────────────────────
    secao("6. Usuário antigo sem e-mail continua entrando")
    igual(atuais["joao"]["email"], "", "joão não tem e-mail")
    igual(us.resolver(atuais, "joao")[0], "joao", "e entra pelo login de sempre")
    igual(us.resolver(atuais, "")[0], None, "identificador vazio não acha ninguém")
    igual(us.resolver(atuais, "ninguem@x.com")[0], None,
          "e-mail desconhecido não acha ninguém")

    # ── 7 ──────────────────────────────────────────────────────────────
    secao("7. Usuário inativo")
    atuais["joao"]["ativo"] = False
    ok(not us.esta_ativo(atuais["joao"]), "joão está inativo")
    uid, reg = us.resolver(atuais, "joao")
    igual(uid, "joao", "o registro ainda é ENCONTRÁVEL (para o admin reativar)")
    ok(not us.esta_ativo(reg), "mas ele não está ativo — quem barra é o login")
    ok(us.esta_ativo(atuais["maria"]), "e isso não afeta mais ninguém")

    # ── 8 ──────────────────────────────────────────────────────────────
    secao("8. O que a sessão guarda é o uid, não o e-mail")
    fonte = (RAIZ / "fiscale_server.py").read_text("utf-8")
    ok("uid = verificar_login(usuario, senha)" in fonte,
       "o login resolve para um uid")
    # Primeiro argumento `uid`; a validade nomeada ("Manter conectado",
    # portal 13/09/2026) pode vir depois sem mudar quem é a sessão.
    ok(__import__("re").search(r"SESSOES\.criar\(uid[,)]", fonte),
       "e a sessão é criada com o UID, nunca com o que foi digitado")
    ok("SESSOES.criar(usuario)" not in fonte,
       "a forma antiga não sobrou em lugar nenhum")
    ok("def verificar_login" in fonte and "return uid" in fonte,
       "`verificar_login` devolve QUEM entrou, não apenas que alguém entrou")

    secao("Mensagem genérica: não conta se o e-mail existe")
    # A propriedade é "existe UMA mensagem", não "o texto aparece uma vez".
    # Contar ocorrências do literal me deu falso alarme quando a Fase 2.1
    # acrescentou a saída de entrada grande demais — que usa exatamente a
    # mesma mensagem. Agora há uma constante, e o que se afirma é que ela é
    # a única definição.
    ok(fonte.count('ERRO_CREDENCIAL = "Usuário ou senha inválidos."') == 1,
       "há UMA mensagem para senha errada, usuário inexistente e conta inativa")
    # Só o BLOCO da rota de login, e só o que ela RESPONDE. Varrer o arquivo
    # inteiro acusaria o comentário que EXPLICA a regra, e o `/api/usuarios/
    # excluir` — que é do admin e pode dizer "usuário não existe" à vontade.
    i = fonte.index('rota == "/api/login"')
    bloco_login = fonte[i:fonte.index('rota == "/api/logout"', i)]
    respostas = [l for l in bloco_login.splitlines()
                 if "_json(" in l or ("erro" in l and "#" not in l.split("erro")[0])]
    respondido = " ".join(respostas).lower()
    for vazamento in ("desativad", "inativ", "não existe", "nao existe",
                      "inexistente", "e-mail"):
        ok(vazamento not in respondido,
           "a RESPOSTA do login não diz %r" % vazamento)
    # Três saídas desde a Fase 2: entrou · bloqueado por tentativas (429) ·
    # a mensagem única (401). O bloqueio é uma resposta diferente de propósito
    # — "espere" não é "senha errada", e confundir os dois faria a pessoa
    # tentar de novo justamente quando não deve.
    # Quatro saídas desde a Fase 2.1: entrou · bloqueado (429) · entrada
    # grande demais (401) · credencial recusada (401). As duas últimas
    # respondem o MESMO texto de propósito: um erro específico para "grande
    # demais" seria mais um sinal a observar de fora.
    igual(bloco_login.count("_json("), 4,
          "quatro saídas: entrou, bloqueado (429) e dois 401 de mesma cara")
    ok("429" in bloco_login, "e o bloqueio tem código próprio")
    ok(bloco_login.count('"Usuário ou senha inválidos."') == 0
       and bloco_login.count("ERRO_CREDENCIAL") == 2,
       "os dois 401 saem pela MESMA constante, sem literal solto")

    # ── 9 ──────────────────────────────────────────────────────────────
    secao("9. O `sub` do token do ELO continua sendo o uid")
    elo = (RAIZ / "fiscale_elo.py").read_text("utf-8")
    ok('"sub": usuario' in elo, "o `sub` vem do usuário passado pelo servidor")
    ok("email" not in elo.lower(),
       "`fiscale_elo` não conhece e-mail — não há por onde ele entrar no token")
    i = fonte.index('rota == "/api/elo/abrir"')
    trecho = fonte[i:i + 1600]
    ok("quem = self._usuario_sessao()" in trecho,
       "o servidor tira o `quem` da SESSÃO")
    ok("emitir_token(DADOS, quem," in trecho,
       "e é esse `quem` que vira o `sub`")
    ok("email" not in trecho.lower(),
       "nenhum e-mail atravessa para o Elo")

    # ── 10 ─────────────────────────────────────────────────────────────
    secao("10. A migração não toca hash nem sal")
    tmp2 = Path(tempfile.mkdtemp(prefix="fiscale_migra_"))
    arq2 = tmp2 / "usuarios.json"
    escrever(arq2, CADASTRO_ANTIGO)
    antes_bytes = arq2.read_bytes()
    antes = json.loads(antes_bytes)

    r = us.migrar(arq2)
    ok(r["migrado"], "migrou")
    igual(r["usuarios"], 3, "os três usuários")
    ok(r["backup"] and Path(r["backup"]).is_file(), "e criou o backup")
    igual(Path(r["backup"]).read_bytes(), antes_bytes,
          "o backup é o arquivo original byte a byte, não uma reserialização")

    depois = json.loads(arq2.read_text("utf-8"))
    for uid in antes:
        igual(depois[uid]["sal"], antes[uid]["sal"], "%r: sal intacto" % uid)
        igual(depois[uid]["hash"], antes[uid]["hash"], "%r: hash intacto" % uid)
        igual(depois[uid].get("admin", uid == "admin"),
              antes[uid].get("admin", uid == "admin"),
              "%r: admin intacto" % uid)
        igual(depois[uid]["uid"], uid, "%r: uid = a chave antiga" % uid)
        igual(depois[uid]["ativo"], True, "%r: nasce ativo" % uid)
        igual(depois[uid]["email"], "", "%r: nasce sem e-mail" % uid)

    secao("A migração é idempotente")
    r2 = us.migrar(arq2)
    ok(r2["ja_estava"], "rodar de novo não faz nada")
    ok(not r2["backup"], "e não cria um segundo backup à toa")

    secao("Arquivo já migrado, restaurado de um backup ANTIGO, ainda funciona")
    escrever(arq2, CADASTRO_ANTIGO)          # como se restaurassem o .fbk velho
    voltou = us.carregar(arq2)
    igual(us.resolver(voltou, "maria")[0], "maria",
          "todo mundo continua entrando pelo login")
    ok(all(us.esta_ativo(v) for v in voltou.values()),
       "e ninguém fica trancado para fora")

    # ── 11 ─────────────────────────────────────────────────────────────
    secao("11. Duas gravações concorrentes, sem perda")
    # O servidor é `ThreadingMixIn`: dois administradores salvando a tela de
    # usuários caem aqui ao mesmo tempo. Antes da Fase 1 isto era
    # `open(..., "w")` direto — o perdedor levava o cadastro do vencedor, e um
    # processo morto no meio deixava o arquivo truncado, que é ninguém entrar.
    tmp3 = Path(tempfile.mkdtemp(prefix="fiscale_conc_"))
    arq3 = tmp3 / "usuarios.json"
    escrever(arq3, CADASTRO_ANTIGO)

    ESCRITORES, RODADAS = 8, 25
    problemas: list[str] = []

    def escritor(n):
        for i in range(RODADAS):
            dados = {("u%02d_%03d" % (n, i)): {"sal": "s", "hash": "h",
                                               "uid": "u", "ativo": True}}
            try:
                us.salvar(arq3, dados)
            except Exception as e:
                problemas.append("escrita %d/%d: %s" % (n, i, e))

    def leitor():
        for _ in range(RODADAS * ESCRITORES):
            d = us.carregar(arq3)
            # O que NUNCA pode acontecer: ler um arquivo pela metade. Com
            # escrita não atômica, o leitor pega o truncado e vê {} —
            # e {} é "o sistema não tem usuários".
            if d == {} and arq3.exists() and arq3.stat().st_size > 2:
                problemas.append("leu vazio com arquivo cheio")

    fios = ([threading.Thread(target=escritor, args=(n,))
             for n in range(ESCRITORES)]
            + [threading.Thread(target=leitor) for _ in range(2)])
    for f in fios:
        f.start()
    for f in fios:
        f.join()

    igual(problemas, [], "nenhuma escrita falhou e nenhuma leitura viu lixo")
    final = us.carregar(arq3)
    igual(len(final), 1, "o arquivo final é de UM escritor inteiro")
    ok(all(v.get("sal") for v in final.values()),
       "e o registro está completo — nunca meio gravado")
    sobra = [p.name for p in tmp3.iterdir() if p.name.startswith(".state-")]
    igual(sobra, [], "nenhum temporário ficou para trás")

    secao("A gravação atômica é a MESMA do resto do sistema")
    ok("from fiscale_arquivo import" in
       (RAIZ / "fiscale_usuarios.py").read_text("utf-8"),
       "`fiscale_usuarios` importa a gravação, não copia")
    ok("from fiscale_arquivo import" in fonte,
       "e `fiscale_server` também — uma implementação só")

    # ═══════════════════════════════════════════════════════════════════
    secao("A tela nunca recebe sal nem hash")
    linhas = us.para_tela(us.carregar(arq))
    ok(linhas, "a listagem tem conteúdo")
    for l in linhas:
        ok("sal" not in l and "hash" not in l,
           "%r vai sem material de senha" % l["usuario"])
    igual(sorted(linhas[0]),
          ["admin", "ativo", "email", "nome", "uid", "usuario"],
          "e os campos são exatamente os que a tela precisa")

    secao("Fase 1 e nada além dela")
    # A guarda é sobre o que o módulo FAZ, não sobre o que ele explica: a
    # docstring dele diz, com todas as letras, que 2FA e bloqueio por
    # tentativas ficam para depois. Punir a explicação seria ensinar a apagá-la.
    import ast as _ast
    bruto = (RAIZ / "fiscale_usuarios.py").read_text("utf-8")
    arvore = _ast.parse(bruto)
    for no in _ast.walk(arvore):
        if isinstance(no, (_ast.Module, _ast.FunctionDef, _ast.ClassDef)):
            if _ast.get_docstring(no, clean=False) and no.body and                     isinstance(no.body[0], _ast.Expr):
                no.body.pop(0)
    codigo = _ast.unparse(arvore).lower()
    for adiado in ("2fa", "totp", "recuperacao", "reset_token", "bloqueio",
                   "tentativa", "rate_limit", "https", "smtp", "enviar_email"):
        ok(adiado not in codigo,
           "o CÓDIGO não traz nada de %r — cada uma é decisão própria" % adiado)
    ok("2FA" in bruto and "bloqueio" in bruto,
       "mas a documentação diz, sim, que elas ficaram para depois")

    secao("O último administrador ativo não pode desaparecer")
    # Três portas para o mesmo precipício: excluir, desativar, tirar o papel.
    base = {"admin": {"sal": "s", "hash": "h", "admin": True, "ativo": True},
            "aline": {"sal": "s", "hash": "h", "admin": False, "ativo": True}}
    igual(us.admins_ativos(base), ["admin"], "há UM administrador ativo")
    ok(us.ficaria_sem_admin(base, "admin", excluir=True),
       "excluí-lo deixaria o sistema sem administrador")
    ok(us.ficaria_sem_admin(base, "admin", novo_ativo=False),
       "desativá-lo, também")
    ok(us.ficaria_sem_admin(base, "admin", novo_admin=False),
       "e tirar o papel dele, também")
    ok(not us.ficaria_sem_admin(base, "aline", excluir=True),
       "mas excluir a operadora é livre")

    secao("Com dois administradores ativos, mexer em um é permitido")
    dois = dict(base)
    dois["aline"] = dict(dois["aline"], admin=True)
    igual(us.admins_ativos(dois), ["admin", "aline"], "agora são dois")
    for como in ({"excluir": True}, {"novo_ativo": False}, {"novo_admin": False}):
        ok(not us.ficaria_sem_admin(dois, "admin", **como),
           "mexer em um deles é permitido (%s)" % list(como)[0])

    secao("Administrador DESATIVADO não conta como administrador")
    # Era o buraco da regra antiga: ela contava a marca `admin`, e não quem
    # consegue entrar. Com um admin desativado na lista, excluir o único que
    # ainda entra passaria.
    com_morto = {"admin": {"admin": True, "ativo": False},
                 "aline": {"admin": True, "ativo": True}}
    igual(us.admins_ativos(com_morto), ["aline"],
          "só a que está ativa conta")
    ok(us.ficaria_sem_admin(com_morto, "aline", excluir=True),
       "e excluí-la é recusado, mesmo havendo outro `admin: true` no arquivo")

    secao("A guarda está nas DUAS rotas")
    ok(fonte.count("ficaria_sem_admin") == 2,
       "cadastro e exclusão chamam a mesma regra")
    ok("único administrador ativo" in fonte,
       "e a mensagem diz que o critério é ATIVO")

    secao("Nenhum teste desta suíte foi à rede")
    try:
        _socket.socket().connect(("127.0.0.1", 9))
        ok(False, "a trava de rede não estava ativa")
    except AssertionError:
        ok(True, "a trava estava mesmo ativa (conferido)")

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
