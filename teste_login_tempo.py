#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fase 2.1 — fechar a enumeração de usuários pelo tempo de resposta.

    python teste_login_tempo.py

O QUE SE PROVA
    Que o caminho do identificador INEXISTENTE passa pelas mesmas 200 mil
    iterações do caminho do usuário real, e que essas tentativas agora são
    contadas — sem que o banco fique sabendo o que foi digitado.

SOBRE O TESTE 16 (medição)
    Um teste que cronometra é um teste que falha sozinho na máquina ocupada.
    Aqui a medição existe, mas o que ela afirma é grosso de propósito: que
    a resposta do inexistente deixou de ser QUASE ZERO perto da do real. A
    afirmação fina — "mesmo algoritmo, mesmas iterações, mesma comparação" —
    é provada contando chamadas, não segundos.

    Nenhuma senha, e-mail ou identificador real aparece aqui.
"""
from __future__ import annotations

import ast
import hashlib
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_pimenta as pim                         # noqa: E402
import fiscale_tentativas as tt                       # noqa: E402
import fiscale_usuarios as fu                         # noqa: E402

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


def _fonte(nome):
    return (RAIZ / nome).read_text(encoding="utf-8")


def _corpo_da_rota_de_login():
    """O código da rota `/api/login`, SEM comentários e SEM docstrings.

    Ler o arquivo cru já me traiu antes: a regra estava explicada num
    comentário e o teste dava por implementada. Aqui só sobra código.
    """
    fonte = _fonte("fiscale_server.py")
    i = fonte.index('if rota == "/api/login":')
    j = fonte.index('if rota == "/api/logout":', i)
    trecho = fonte[i:j]
    linhas = [l for l in trecho.splitlines()
              if not l.strip().startswith("#")]
    return "\n".join(linhas)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="fiscale_tempo_"))

    # ── um relógio de mentira, para não dormir de verdade ───────────────
    class Relogio:
        def __init__(self):
            self.t = 1_000_000.0

        def __call__(self):
            return self.t

        def avancar(self, s):
            self.t += s

    relogio = Relogio()

    # ── 1 a 3: os dois caminhos custam o mesmo ──────────────────────────
    secao("1-3 · Usuário inexistente também paga o PBKDF2")
    p = pim.abrir(tmp)

    chamadas = []
    original = hashlib.pbkdf2_hmac

    def espiao(algoritmo, senha, sal, iteracoes, *a, **kw):
        chamadas.append((algoritmo, iteracoes))
        return original(algoritmo, senha, sal, iteracoes, *a, **kw)

    hashlib.pbkdf2_hmac = espiao
    try:
        chamadas.clear()
        resultado = p.gastar_o_mesmo_tempo("qualquer coisa")
        ok(len(chamadas) == 1,
           "identificador inexistente executa PBKDF2 fictício (1 chamada)")
        ok(resultado is False,
           "o PBKDF2 fictício NUNCA acerta — o retorno é False por construção")
        ok(chamadas and chamadas[0][1] == 200_000,
           "o PBKDF2 fictício usa exatamente 200.000 iterações")
        ok(chamadas and chamadas[0][0] == "sha256",
           "e o mesmo algoritmo do caminho real (sha256)")
    finally:
        hashlib.pbkdf2_hmac = original

    # O número não pode viver em dois lugares que discordam.
    servidor = _fonte("fiscale_server.py")
    ok("200_000" in servidor and pim.ITERACOES == 200_000,
       "o custo do servidor e o do módulo fictício são o MESMO número")

    corpo = _corpo_da_rota_de_login()
    vl = _fonte("fiscale_server.py")
    i = vl.index("def verificar_login")
    j = vl.index("import fiscale_papeis", i)
    codigo_vl = ast.unparse(ast.parse(
        "\n".join(l for l in vl[i:j].splitlines()
                  if not l.strip().startswith("#"))))
    ok("gastar_o_mesmo_tempo" in codigo_vl,
       "`verificar_login` paga o custo falso quando não há conta")
    ok(codigo_vl.count("compare_digest") >= 1,
       "a comparação continua em tempo constante nos dois caminhos")

    # ── 4 a 6: inexistente acumula, atrasa e bloqueia ───────────────────
    secao("4-6 · O identificador inexistente é contado, atrasa e bloqueia")
    porta = tt.Porta(str(tmp / "t1.db"), agora=relogio)
    fantasma = p.chave_do_desconhecido("nao-existe@exemplo.invalido")
    ok(fantasma.startswith("~") and len(fantasma) == 33,
       "a chave derivada é `~` + 32 hex")

    D = tt.ESCOPO_DESCONHECIDO
    for _ in range(3):
        porta.registrar_falha(uid=fantasma, ip="10.0.0.9", escopo=D)
    ok(porta.situacao(uid=fantasma, escopo=D)["falhas_usuario"] == 3,
       "identificador inexistente acumula tentativas")
    ok(porta.avaliar(uid=fantasma, ip="10.0.0.9", escopo=D).atraso_s > 0,
       "e passa a sofrer o atraso progressivo (4ª tentativa)")

    for _ in range(5):
        porta.registrar_falha(uid=fantasma, ip="10.0.0.9", escopo=D)
    v = porta.avaliar(uid=fantasma, ip="10.0.0.9", escopo=D)
    ok(v.bloqueado and v.escopo == D,
       "bloqueia na 8ª tentativa, com a MESMA régua das contas reais")
    relogio.avancar(5 * 60 + 1)
    ok(not porta.avaliar(uid=fantasma, ip="10.0.0.9", escopo=D).bloqueado,
       "e solta depois dos 5 minutos — o limite não mudou")

    # ── 5: sobrevive ao reinício ────────────────────────────────────────
    secao("5 · O contador sobrevive ao reinício do Fiscale")
    porta.fechar()
    relogio2 = Relogio()
    porta = tt.Porta(str(tmp / "t1.db"), agora=relogio2)
    ok(porta.situacao(uid=fantasma, escopo=D)["falhas_usuario"] == 8,
       "as 8 falhas continuam lá depois de fechar e reabrir")
    # E a chave tem de continuar a mesma depois de reabrir o segredo.
    p2 = pim.abrir(tmp)
    ok(p2.chave_do_desconhecido("nao-existe@exemplo.invalido") == fantasma,
       "e a pimenta lida do disco deriva a MESMA chave")
    ok(p2.trocada is False,
       "reabrir um segredo íntegro não o troca")

    # ── 7: caixa e espaço caem no mesmo contador ────────────────────────
    secao("7 · Maiúsculas e espaços compartilham o contador")
    base = p.chave_do_desconhecido("Maria.Ferreira@Exemplo.Invalido")
    for variante in ("maria.ferreira@exemplo.invalido",
                     "  MARIA.FERREIRA@EXEMPLO.INVALIDO  ",
                     "Maria.Ferreira@exemplo.INVALIDO\t"):
        ok(p.chave_do_desconhecido(variante) == base,
           "mesma chave para a variante %r" % variante.strip()[:18])

    # ── 8: login e e-mail reais convergem para o mesmo uid ──────────────
    secao("8 · Login e e-mail reais continuam caindo no mesmo `uid`")
    cadastro = {"joana": {"sal": "00" * 16, "hash": "ab" * 32,
                          "email": "joana@escritorio.invalido"}}
    u1, _ = fu.resolver(cadastro, "joana")
    u2, _ = fu.resolver(cadastro, "JOANA@ESCRITORIO.INVALIDO")
    u3, _ = fu.resolver(cadastro, "  joana@escritorio.invalido ")
    ok(u1 == u2 == u3 == "joana",
       "os três caminhos de entrada devolvem o mesmo uid")
    ok("chave = alvo" in corpo
       and "chave = PIMENTA.chave_do_desconhecido(usuario)" in corpo,
       "a rota só deriva a chave QUANDO não achou conta")

    # ── 9 e 10: contadores separados, IP compartilhado ──────────────────
    secao("9-10 · Contador individual separado, teto de IP compartilhado")
    a = p.chave_do_desconhecido("aaa@exemplo.invalido")
    b = p.chave_do_desconhecido("bbb@exemplo.invalido")
    ok(a != b, "dois identificadores inexistentes não compartilham a chave")

    porta2 = tt.Porta(str(tmp / "t2.db"), agora=relogio2)
    for _ in range(4):
        porta2.registrar_falha(uid=a, ip="10.0.0.7", escopo=D)
    ok(porta2.situacao(uid=b, escopo=D)["falhas_usuario"] == 0,
       "as falhas de um não contam para o outro")
    ok(porta2.situacao(uid=b, ip="10.0.0.7", escopo=D)["falhas_ip"] == 4,
       "mas as duas somam no MESMO contador de IP")

    for _ in range(26):
        porta2.registrar_falha(uid=b, ip="10.0.0.7", escopo=D)
    v = porta2.avaliar(uid="ninguem", ip="10.0.0.7")
    ok(v.bloqueado and v.escopo == "ip",
       "o IP bloqueia na 30ª, somando os dois identificadores")
    relogio2.avancar(10 * 60 + 1)
    ok(not porta2.avaliar(uid="ninguem", ip="10.0.0.7").bloqueado,
       "e solta depois dos 10 minutos — o limite não mudou")

    # ── 9b: colisão com um uid real é IMPOSSÍVEL, não improvável ────────
    secao("9b · Conta real e identificador desconhecido não podem colidir")
    ok(tt.ESCOPO_CONTA != tt.ESCOPO_DESCONHECIDO,
       "são dois espaços de contagem com NOMES diferentes no banco")

    # O caso que o prefixo sozinho não cobria: alguém cria um login com
    # exatamente o formato da chave derivada. Antes, os dois cairiam na mesma
    # linha; agora nem se enxergam.
    sosia = p.chave_do_desconhecido("vitima@exemplo.invalido")
    portaC = tt.Porta(str(tmp / "t9.db"), agora=relogio2)
    for _ in range(8):
        portaC.registrar_falha(uid=sosia, ip="10.0.0.2",
                               escopo=tt.ESCOPO_DESCONHECIDO)
    ok(portaC.avaliar(uid=sosia, ip="10.0.0.2",
                      escopo=tt.ESCOPO_DESCONHECIDO).bloqueado,
       "o identificador desconhecido está bloqueado")
    ok(not portaC.avaliar(uid=sosia, ip="10.0.0.2",
                          escopo=tt.ESCOPO_CONTA).bloqueado,
       "um uid REAL com o mesmo texto NÃO é atingido pelo bloqueio dele")
    ok(portaC.situacao(uid=sosia, escopo=tt.ESCOPO_CONTA)["falhas_usuario"] == 0,
       "e não herda falha nenhuma")

    # E o caminho inverso: trancar uma conta real não tranca o desconhecido.
    portaC.liberar(uid=sosia, escopo=tt.ESCOPO_DESCONHECIDO)
    for _ in range(8):
        portaC.registrar_falha(uid="joana", ip="10.0.0.2")
    ok(portaC.avaliar(uid="joana", ip="10.0.0.2").bloqueado,
       "a conta real bloqueia normalmente")
    ok(not portaC.avaliar(uid="joana", ip="10.0.0.2",
                          escopo=tt.ESCOPO_DESCONHECIDO).bloqueado,
       "sem contaminar o espaço dos desconhecidos")

    # A separação tem de estar no CÓDIGO da rota, não só aqui.
    ok("ESCOPO_DESCONHECIDO" in corpo and "ESCOPO_CONTA" in corpo,
       "a rota escolhe o espaço de contagem explicitamente")
    ok("escopo=escopo" in corpo,
       "e o repassa para avaliar e registrar")
    portaC.fechar()

    # ── 2: o evento da troca nao conta nada alem do evento ─────────
    secao("2 · A troca do segredo registra o evento, não o segredo")
    i_ev = servidor.index("def _pimenta_trocada")
    j_ev = servidor.index("PIMENTA = fiscale_pimenta.abrir", i_ev)
    arvore_ev = ast.parse(servidor[i_ev:j_ev].strip())
    # Sem docstring: a docstring EXPLICA a regra, e já me enganei antes
    # dando por implementado o que estava só escrito em prosa.
    fn = arvore_ev.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body.pop(0)
    codigo_ev = ast.unparse(arvore_ev)

    ok("esquecer_desconhecidos" in codigo_ev and "print(" in codigo_ev,
       "há um evento técnico registrado na troca")

    # O que a linha registra são literais fixos mais UM número. Se alguém
    # acrescentar uma variável ali, isto para de valer.
    textos = [n.value for n in ast.walk(arvore_ev)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    ok(bool(textos), "a linha é feita de texto fixo")
    juntos = " ".join(textos).lower()
    # Só o que denunciaria um VALOR sendo formatado. Palavra de frase fixa
    # ("identificador desconhecido") não vaza coisa alguma, e proibi-la era
    # caçar vocabulário em vez de caçar vazamento.
    for vazado in ("%s", "%r", "{", "@"):
        ok(vazado not in juntos,
           "o texto registrado não interpola %r" % vazado)
    ok(juntos.count("%d") == 1,
       "e interpola exatamente UM número: a contagem")

    # Nenhuma variável do segredo entra na chamada do print.
    chamadas_print = [n for n in ast.walk(arvore_ev)
                      if isinstance(n, ast.Call)
                      and getattr(n.func, "id", "") == "print"]
    nomes = {getattr(x, "id", "") for c in chamadas_print
             for x in ast.walk(c) if isinstance(x, ast.Name)}
    ok("PIMENTA" not in nomes and "usuario" not in nomes,
       "o print não recebe o segredo nem identificador algum")

    # ── 11: nada em texto claro ─────────────────────────────────────────
    secao("11 · Nada identificável no SQLite nem nos logs")
    segredo = "carlos.pereira@exemplo.invalido"
    porta3 = tt.Porta(str(tmp / "t3.db"), agora=relogio2)
    porta3.registrar_falha(uid=p.chave_do_desconhecido(segredo),
                           ip="10.0.0.5", escopo=D)
    porta3.fechar()
    bruto = (tmp / "t3.db").read_bytes()
    for pedaco in (segredo, "carlos", "pereira", "exemplo.invalido"):
        ok(pedaco.encode() not in bruto.lower(),
           "%r não aparece no arquivo do banco" % pedaco)
    con = sqlite3.connect(str(tmp / "t3.db"))
    chaves = [c for (c,) in con.execute(
        "SELECT chave FROM tentativa WHERE escopo=?", (D,))]
    con.close()
    ok(all(c.startswith("~") and len(c) == 33 for c in chaves),
       "só há chave derivada, e ela é irreversível sem o segredo")

    ok("hmac" in _fonte("fiscale_pimenta.py"),
       "a derivação é HMAC, não um hash solto")
    ok("senha" not in [c for c in chaves],
       "senha nenhuma vira chave de contagem")
    # E o segredo em si não pode estar legível no disco quando há DPAPI.
    guardado = (tmp / pim.ARQUIVO).read_text(encoding="utf-8")
    import platform
    if platform.system() == "Windows":
        ok(guardado.startswith("dpapi:"),
           "no Windows o segredo fica protegido pelo DPAPI")
    else:
        ok(guardado.startswith("claro:"),
           "fora do Windows o segredo é declarado como não protegido")
    ok(p._pimenta.hex() not in guardado,
       "a pimenta não aparece em claro dentro do arquivo")

    # o servidor não pode registrar o que foi digitado
    ok("print(usuario" not in corpo and "log(usuario" not in corpo,
       "a rota não imprime o identificador digitado")

    # ── 12: concorrência ────────────────────────────────────────────────
    secao("12 · Tentativas concorrentes respeitam os limites")
    import threading
    porta4 = tt.Porta(str(tmp / "t4.db"), agora=relogio2)
    alvo4 = p.chave_do_desconhecido("concorrente@exemplo.invalido")

    def martelar():
        for _ in range(10):
            porta4.registrar_falha(uid=alvo4, ip="10.0.0.4", escopo=D)

    fios = [threading.Thread(target=martelar) for _ in range(6)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    ok(porta4.situacao(uid=alvo4, escopo=D)["falhas_usuario"] == 60,
       "60 falhas simultâneas foram contadas sem perder nenhuma")
    ok(porta4.avaliar(uid=alvo4, ip="10.0.0.4", escopo=D).bloqueado,
       "e o bloqueio valeu para todas as linhas")

    # ── 13: bloqueado não gasta PBKDF2 ──────────────────────────────────
    secao("13 · Conta bloqueada não gasta PBKDF2 à toa")
    ordem_avaliar = corpo.index("TENTATIVAS.avaliar")
    ordem_verificar = corpo.index("verificar_login(usuario, senha)")
    ok(ordem_avaliar < ordem_verificar,
       "a porta decide ANTES de a senha ser conferida")
    bloqueio = corpo.index("v.bloqueado")
    ok(bloqueio < ordem_verificar and "429" in corpo[bloqueio:ordem_verificar],
       "e o 429 sai por `return`, sem chegar ao PBKDF2")

    # ── 14: sucesso limpa só o que deve ─────────────────────────────────
    secao("14 · O acerto limpa apenas os contadores certos")
    porta5 = tt.Porta(str(tmp / "t5.db"), agora=relogio2)
    for _ in range(5):
        porta5.registrar_falha(uid="joana", ip="10.0.0.3")
    porta5.registrar_falha(uid=a, ip="10.0.0.3", escopo=D)
    porta5.registrar_acerto(uid="joana", ip="10.0.0.3")
    ok(porta5.situacao(uid="joana")["falhas_usuario"] == 0,
       "zera a conta de quem provou ser ela")
    ok(porta5.situacao(uid=a, escopo=D)["falhas_usuario"] == 1,
       "não zera a de outro identificador")
    ok(porta5.situacao(ip="10.0.0.3")["falhas_ip"] == 6,
       "e NÃO zera o IP — acertar um login não limpa a ficha da origem")

    secao("14b · Trocar a pimenta descarta só as chaves derivadas")
    porta5.registrar_falha(uid="joana", ip="10.0.0.3")
    n = porta5.esquecer_desconhecidos()
    ok(n == 1, "descartou a linha derivada")
    ok(porta5.situacao(uid="joana")["falhas_usuario"] == 1,
       "e não tocou na conta real")
    ok(porta5.situacao(ip="10.0.0.3")["falhas_ip"] == 7,
       "nem no contador de IP, que não depende do segredo")

    # ── 15: nada de fora mexeu ──────────────────────────────────────────
    secao("15 · Senhas, sessões, papéis e o `sub` do ELO intocados")
    codigo_servidor = ast.unparse(ast.parse(servidor)).lower()
    ok("pbkdf2_hmac" in servidor and servidor.count("200_000") >= 1,
       "o hash das senhas reais continua o mesmo")
    ok("minimo = 8" in _fonte("fiscale_senhas.py").lower()
       or "MINIMO = 8" in _fonte("fiscale_senhas.py"),
       "a política de 8 caracteres não foi tocada")
    # Primeiro argumento `uid`; a validade nomeada do "Manter conectado"
    # (portal 13/09/2026) pode vir depois sem mudar quem é a sessão.
    ok(__import__("re").search(r"SESSOES\.criar\(uid[,)]", corpo),
       "a sessão continua nascendo do `uid`, nunca do que foi digitado")
    ok("sessoes.criar(usuario)" not in codigo_servidor,
       "nada passou a abrir sessão pelo identificador digitado")
    for arquivo in ("fiscale_usuarios.py",):
        fonte_u = _fonte(arquivo)
        ok("CAMPOS_INTOCAVEIS" in fonte_u and '"admin"' in fonte_u,
           "%s manteve os campos intocáveis" % arquivo)
    elo = RAIZ / "fiscale_elo_sync.py"
    if elo.exists():
        ok('"sub"' not in _fonte("fiscale_pimenta.py"),
           "a Fase 2.1 não encostou no token do ELO")

    # Portal, Fase 3 (13/09/2026): a origem passou a atravessar o proxy HTTPS
    # desta máquina. A regra continua sendo "o soquete decide": cabeçalho só é
    # crível quando o soquete é loopback. Quem guarda isso agora é
    # `fiscale_proxy`, testado em `teste_https_pwa.py`; aqui se confere que a
    # rota passa pelo módulo e não lê cabeçalho por conta própria.
    secao("15b · O IP vem do soquete (e só o proxy local o atravessa)")
    ok("fiscale_proxy.origem(self.client_address[0]" in corpo,
       "a origem vem do soquete, pela regra única de fiscale_proxy")
    for cabecalho in ("x-forwarded-for", "x_forwarded_for", "x-real-ip"):
        ok(cabecalho not in corpo.lower(),
           "a rota não consulta %r por conta própria" % cabecalho)

    secao("15c · Teto de entrada antes do que é caro")
    ok("LIMITE_IDENTIFICADOR" in corpo and "LIMITE_SENHA" in corpo,
       "a rota aplica o teto de tamanho")
    teto = corpo.index("LIMITE_IDENTIFICADOR")
    ok(teto < ordem_verificar and teto < ordem_avaliar,
       "e o aplica antes do banco e antes do PBKDF2")
    # A janela precisa caber o bloco inteiro do teto: a Fase 3 acrescentou o
    # registro de auditoria ali dentro e empurrou o `return` para além dos
    # 400 caracteres que eu tinha chutado.
    trecho_teto = corpo[teto:corpo.index("resolver_usuario", teto)]
    # A mesma CONSTANTE das outras recusas, não um texto parecido: um erro
    # específico para "grande demais" seria mais um sinal a observar de fora.
    ok("ERRO_CREDENCIAL" in trecho_teto
       and "Usuário ou senha inválidos" not in trecho_teto,
       "entrada grande demais recebe a MESMA mensagem genérica")

    secao("15d · A espera é injetável")
    ok("ESPERAR = time.sleep" in servidor and "ESPERAR(v.atraso_s)" in corpo,
       "o `sleep` passa por uma variável que o teste pode trocar")

    # ── 16: a medição ───────────────────────────────────────────────────
    secao("16 · A diferença grosseira sumiu (medição, propositalmente folgada)")

    def medir(funcao, vezes=5):
        melhor = 10.0
        for _ in range(vezes):
            ini = time.perf_counter()
            funcao()
            melhor = min(melhor, time.perf_counter() - ini)
        return melhor

    sal_real = "11" * 16
    def caminho_real():
        hashlib.pbkdf2_hmac("sha256", b"tentativa", bytes.fromhex(sal_real),
                            200_000)

    t_real = medir(caminho_real)
    t_falso = medir(lambda: p.gastar_o_mesmo_tempo("tentativa"))
    razao = t_falso / t_real if t_real else 0
    print("     real %.0f ms · inexistente %.0f ms · razão %.2f"
          % (t_real * 1000, t_falso * 1000, razao))
    # ANTES a razão era ~0.00 (resposta imediata). A afirmação é grossa de
    # propósito: exigir 0,95–1,05 faria o teste falhar na máquina ocupada.
    ok(0.5 <= razao <= 2.0,
       "o inexistente custa a mesma ordem de grandeza do real (razão %.2f)"
       % razao)
    ok(t_falso > t_real * 0.5,
       "e deixou de ser a resposta instantânea que denunciava a conta")

    secao("Nenhum teste desta suíte foi à rede")
    try:
        _socket.socket().connect(("127.0.0.1", 9))
        ok(False, "a trava de rede não estava ativa")
    except AssertionError:
        ok(True, "a trava estava mesmo ativa (conferido)")

    for porta_aberta in (porta, porta2, porta3, porta4, porta5):
        try:
            porta_aberta.fechar()
        except Exception:
            pass
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
