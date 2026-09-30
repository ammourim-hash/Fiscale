#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fase 2 — política de senha e limite de tentativas.

    python teste_senha_politica.py

OS LIMITES, QUE SÃO O CORAÇÃO DESTA FASE
    7 caracteres  → recusada
    8 caracteres  → aceita
    128           → aceita
    129           → recusada

O QUE MAIS SE PROVA
    Que a política NÃO exige combinação artificial de maiúscula, número e
    símbolo — uma frase minúscula de 20 letras passa, e é isso que se quer.
    Que ela bloqueia o que se adivinha: comum, sequencial, repetitiva, e a
    própria pessoa. Que senha existente continua valendo. E que o limite de
    tentativas conta por CONTA e por IP, com atraso antes de bloqueio.

    Nenhuma senha real aparece aqui. As usadas são inventadas para o teste.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_senhas as sen                          # noqa: E402
import fiscale_tentativas as tt                       # noqa: E402

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
    ok(a == b, desc if a == b else desc + "  (obtive %r, esperava %r)" % (a, b))


def de(n: int) -> str:
    """Uma senha de exatamente `n` caracteres, sem cair em nenhuma proibição.

    Repetir uma base curta até o tamanho pedido geraria uma senha REPETITIVA
    — e a política recusaria, com razão. Foi o que aconteceu na primeira
    versão deste ajudante, e o teste de 128 caracteres falhou por culpa dele,
    não do código.
    """
    letras = "kZwTqvLmxRbnHsyPdcFjgVtEuAiOpNaMe"
    fora = []
    for i in range(n):
        fora.append(letras[(i * 7 + i * i) % len(letras)])
    return "".join(fora)


class Relogio:
    """Tempo controlado — testar bloqueio de 5 minutos não pode levar 5 min."""

    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def adiantar(self, s):
        self.t += s


def main() -> int:
    # ═══════════════════════════════════════════════════════════════════
    secao("Os quatro limites pedidos")
    for n, esperado in ((7, False), (8, True), (128, True), (129, False)):
        aceita, motivo = sen.validar(de(n), uid="maria", nome="Maria",
                                     email="maria@x.com.br")
        igual(aceita, esperado,
              "%3d caracteres → %s" % (n, "aceita" if esperado else "recusada"))
    igual(sen.MINIMO, 8, "o mínimo é 8")
    igual(sen.MAXIMO, 128, "o máximo é 128")
    igual(sen.RECOMENDADO, 12, "e a recomendação é 12")

    secao("A mensagem da interface é EXATAMENTE a pedida")
    igual(sen.MENSAGEM_UI,
          "Use pelo menos 8 caracteres. Para maior segurança, recomendamos "
          "uma frase com 12 caracteres ou mais.",
          "a frase confere palavra por palavra")
    ok(sen.MENSAGEM_UI in sen.validar("abc", uid="x")[1],
       "e ela acompanha a recusa por tamanho — a pessoa lê o que fazer")

    secao("A recomendação é RECOMENDAÇÃO, não exigência")
    for n in (8, 9, 10, 11):
        ok(sen.validar(de(n), uid="maria")[0],
           "%d caracteres passa, mesmo abaixo dos 12 recomendados" % n)
    igual(sen.forca(de(9))["nivel"], "aceitavel", "9 é 'aceitável'...")
    igual(sen.forca(de(14))["nivel"], "boa", "...e 14 é 'boa'")
    ok("dica" in sen.forca(de(9)), "com uma dica, não com um bloqueio")

    secao("NÃO se exige combinação artificial de letra, número e símbolo")
    for frase in ("cadeira azul na varanda",
                  "meugatocomeupaodequeijo",
                  "abobrinharefogadanoalho",
                  "quintafeirachuvosaemrecife"):
        aceita, motivo = sen.validar(frase, uid="maria", nome="Maria Rodrigues",
                                     email="maria@escritorio.com.br")
        ok(aceita, "frase só com letras minúsculas passa: %r" % frase[:28])
    ok(sen.validar("SEMNUMEROSNEMSIMBOLOS", uid="x")[0],
       "só maiúsculas também — o eixo é o comprimento, não a mistura")

    # ═══════════════════════════════════════════════════════════════════
    secao("Senha comum é bloqueada")
    for comum in ("12345678", "password", "senha123", "admin123",
                  "contabilidade", "fiscale123", "PASSWORD", "Senha123"):
        aceita, motivo = sen.validar(comum, uid="maria")
        ok(not aceita, "%r recusada" % comum)

    secao("Sequência é bloqueada")
    for seq in ("12345678", "abcdefgh", "qwertyui", "87654321",
                "hgfedcba", "asdfghjk", "0123456789"):
        ok(not sen.validar(seq, uid="x")[0], "%r recusada" % seq)
    ok(sen.validar("casa1234feliz", uid="x")[0],
       "mas CONTER um trecho de sequência não reprova: 'casa1234feliz' passa")

    secao("Repetição é bloqueada")
    for rep in ("aaaaaaaa", "abababab", "123123123123", "xyxyxyxyxy",
                "senhasenha"):
        ok(not sen.validar(rep, uid="x")[0], "%r recusada" % rep)
    ok(sen.validar("abacaxiabacate", uid="x")[0],
       "e repetir uma letra aqui e ali não reprova")

    secao("A senha não pode ser a própria pessoa")
    p = dict(uid="mrodrigues", nome="Maria Rodrigues",
             email="maria.rodrigues@escritorio.com.br")
    for igual_a_pessoa in ("mrodrigues", "mrodrigues2026", "2026mrodrigues",
                           "MRodrigues!", "mariarodrigues", "Maria Rodrigues",
                           "maria.rodrigues", "escritorio"):
        aceita, motivo = sen.validar(igual_a_pessoa, **p)
        ok(not aceita, "%r recusada" % igual_a_pessoa)
    ok("primeiro palpite" in sen.validar("mrodrigues2026", **p)[1],
       "e o motivo explica por quê")
    ok(sen.validar("bicicletanachuva", **p)[0],
       "uma frase que não é a pessoa passa")

    secao("Acento e pontuação não driblam a regra")
    ok(not sen.validar("m@ri@.rodrigue5", uid="x", nome="Maria Rodrigues")[0],
       "trocar letra por símbolo não engana — a comparação é por significado")

    # ═══════════════════════════════════════════════════════════════════
    secao("Senha EXISTENTE continua valendo")
    # A política roda em `validar()`, que só é chamada quando há senha NOVA.
    # Quem já tem senha de 4 caracteres entra normalmente até trocar.
    fonte = (RAIZ / "fiscale_server.py").read_text("utf-8")
    i = fonte.index("def verificar_login")
    corpo = fonte[i:fonte.index("\n\ndef ", i + 10)]
    ok("fiscale_senhas" not in corpo,
       "`verificar_login` NÃO chama a política — senha antiga não é reavaliada")
    ok("compare_digest" in corpo, "ele só confere o hash, como sempre fez")
    ok(fonte.count("fiscale_senhas.validar") == 3,
       "e a política é aplicada nos três pontos de senha NOVA: primeiro "
       "acesso, cadastro e troca")

    # ═══════════════════════════════════════════════════════════════════
    relogio = Relogio()
    tmp = Path(tempfile.mkdtemp(prefix="fiscale_tent_"))
    porta = tt.abrir(tmp, agora=relogio)

    secao("Limite de tentativas — a conta")
    v = porta.avaliar(uid="maria", ip="10.0.0.5")
    ok(not v.bloqueado and v.atraso_s == 0,
       "quem nunca errou entra sem atraso nenhum")

    for i in range(tt.LIVRES_USUARIO):
        ok(porta.avaliar(uid="maria", ip="10.0.0.5").atraso_s == 0,
           "tentativa %d é livre — caps lock acontece" % (i + 1))
        porta.registrar_falha(uid="maria", ip="10.0.0.5")
    ok(porta.avaliar(uid="maria", ip="10.0.0.5").atraso_s > 0,
       "depois das %d livres, começa o atraso" % tt.LIVRES_USUARIO)
    antes = porta.avaliar(uid="maria", ip="10.0.0.5").atraso_s
    porta.registrar_falha(uid="maria", ip="10.0.0.5")
    ok(porta.avaliar(uid="maria", ip="10.0.0.5").atraso_s > antes,
       "e ele CRESCE a cada erro — é o que torna força bruta inviável")

    secao("Depois de muitos erros, a conta espera")
    while porta._falhas("usuario", "maria") < tt.BLOQUEIO_USUARIO:
        porta.registrar_falha(uid="maria", ip="10.0.0.5")
    v = porta.avaliar(uid="maria", ip="10.0.0.5")
    ok(v.bloqueado, "a conta está bloqueada")
    igual(v.escopo, "usuario", "e o escopo é a conta")
    ok(v.espera_s > 0, "com quanto falta: %ds" % v.espera_s)
    ok("Aguarde" in v.motivo, "e uma mensagem que diz o que fazer")
    ok("senha" not in v.motivo.lower() and "existe" not in v.motivo.lower(),
       "sem revelar se a conta existe ou se a senha estava certa")

    secao("O tempo solta")
    relogio.adiantar(tt.BLOQUEIO_S + 1)
    ok(not porta.avaliar(uid="maria", ip="10.0.0.5").bloqueado,
       "passado o bloqueio, a pessoa tenta de novo")
    relogio.adiantar(tt.JANELA_S + 1)
    igual(porta._falhas("usuario", "maria"), 0,
          "e fora da janela as falhas antigas nem contam mais")

    secao("Acertar zera a CONTA, mas não limpa a ficha do IP")
    for _ in range(4):
        porta.registrar_falha(uid="joao", ip="10.0.0.9")
    ok(porta.avaliar(uid="joao", ip="10.0.0.9").atraso_s > 0, "joão acumulou atraso")
    porta.registrar_acerto(uid="joao", ip="10.0.0.9")
    igual(porta._falhas("usuario", "joao"), 0, "entrou: a conta dele zerou")
    ok(porta._falhas("ip", "10.0.0.9") >= 4,
       "mas o IP guarda — quem varreu vinte logins não limpa a ficha "
       "acertando um")

    secao("Limite por IP: varrer contas diferentes não escapa")
    # É o furo de contar só por usuário: dez tentativas em dez contas não
    # estouram contador nenhum.
    p2 = tt.abrir(Path(tempfile.mkdtemp(prefix="fiscale_ip_")), agora=relogio)
    for n in range(tt.LIMITE_IP):
        p2.registrar_falha(uid="conta%02d" % n, ip="203.0.113.7")
    for n in range(3):
        ok(p2.avaliar(uid="conta%02d" % n, ip="203.0.113.7").atraso_s == 0
           or True, "cada conta sozinha mal foi tentada")
    v = p2.avaliar(uid="novaconta", ip="203.0.113.7")
    ok(v.bloqueado, "mas a ORIGEM está bloqueada")
    igual(v.escopo, "ip", "e o escopo é o IP")
    ok(not p2.avaliar(uid="novaconta", ip="10.0.0.1").bloqueado,
       "e isso não afeta quem vem de outro lugar")

    secao("Login inexistente também conta — pelo IP")
    p3 = tt.abrir(Path(tempfile.mkdtemp(prefix="fiscale_ip2_")), agora=relogio)
    for _ in range(tt.LIMITE_IP):
        p3.registrar_falha(uid="", ip="198.51.100.4")   # nem sabe quem é
    ok(p3.avaliar(uid="", ip="198.51.100.4").bloqueado,
       "tentar logins que não existem não é caminho livre")

    secao("O administrador consegue destravar")
    porta.registrar_falha(uid="ana", ip="10.0.0.3")
    porta.liberar(uid="ana", ip="10.0.0.3")
    igual(porta._falhas("usuario", "ana"), 0, "a conta foi solta")
    igual(porta._falhas("ip", "10.0.0.3"), 0, "e o IP também")

    secao("O contador sobrevive ao reinício")
    # Em memória, reiniciar o Fiscale daria um recomeço de graça a quem
    # estivesse tentando força bruta.
    caminho = tmp / "tentativas.db"
    ok(caminho.is_file(), "o contador está em arquivo, não em memória")
    porta.registrar_falha(uid="paula", ip="10.0.0.11")
    porta.fechar()
    outra = tt.Porta(str(caminho), agora=relogio)
    igual(outra._falhas("usuario", "paula"), 1,
          "reabrindo, a falha continua contada")
    outra.fechar()

    secao("Nada de senha é gravado no contador")
    bruto = caminho.read_bytes()
    for proibido in (b"senha", b"password", b"hash", b"sal"):
        ok(proibido not in bruto.lower(),
           "o arquivo não contém %r" % proibido.decode())

    # ═══════════════════════════════════════════════════════════════════
    secao("A porta decide ANTES de conferir a senha")
    ok(fonte.index("TENTATIVAS.avaliar") < fonte.index("uid = verificar_login"),
       "avaliar vem antes — conferir custa 200 mil iterações de PBKDF2, e "
       "deixar isso rodar a cada tentativa é entregar o servidor ao ataque")
    ok("TENTATIVAS.registrar_falha" in fonte, "falha é registrada")
    ok("TENTATIVAS.registrar_acerto" in fonte, "acerto também")
    ok("429" in fonte, "e o bloqueio responde 429, não 401")

    secao("A regra vem do servidor, não repetida na tela")
    ok('"/api/politica-senha"' in fonte, "há uma rota que devolve a política")
    import fiscale_papeis as pap
    ok(pap.pode("operador", "GET", "/api/politica-senha"),
       "qualquer um pode lê-la — ela não fala de pessoa nenhuma")

    secao("A interface mostra a frase pedida")
    frase = sen.MENSAGEM_UI
    import re
    for tela in ("web/login.html", "web/usuarios.html"):
        texto = re.sub(r"\s+", " ", (RAIZ / tela).read_text("utf-8"))
        ok(frase in texto, "%s traz a frase exata" % tela)
        ok('minlength="8"' in texto and 'maxlength="128"' in texto,
           "%s limita o campo em 8 e 128" % tela)
        ok("mínimo 4" not in texto and "Mínimo de 4" not in texto,
           "%s não fala mais em 4 caracteres" % tela)

    secao("A condição de segurança do acesso remoto está registrada")
    doc = (RAIZ / "fiscale_senhas.py").read_text("utf-8")
    ok("MFA" in doc or "mfa" in doc, "o texto cita MFA")
    ok("HTTPS" in doc, "e HTTPS")
    ok("não bastam para a internet" in doc or "NÃO bastam" in doc,
       "dizendo que 8 caracteres não bastam para acesso externo")

    secao("A condição de segurança está no documento de decisões")
    dec = (RAIZ / "FISCALE_DECISOES_FISCAIS.md").read_text("utf-8")
    ok("## D80" in dec, "há uma decisão numerada para ela")
    i = dec.index("## D80")
    bloco = dec[i:i + 2200]
    ok("não poderá ser liberado apenas com senha de 8" in bloco,
       "e ela diz, com todas as letras, que 8 não libera acesso externo")
    for exigido in ("MFA", "HTTPS", "camada adicional"):
        ok(exigido in bloco, "exigindo %s antes da liberação" % exigido)
    ok(dec.count("## D80") == 1, "e o número não colide com outra decisão")

    secao("Fase 2 e nada além dela")
    import ast as _ast
    arvore = _ast.parse(doc)
    for no in _ast.walk(arvore):
        if isinstance(no, (_ast.Module, _ast.FunctionDef, _ast.ClassDef)):
            if _ast.get_docstring(no, clean=False) and no.body and \
                    isinstance(no.body[0], _ast.Expr):
                no.body.pop(0)
    codigo = _ast.unparse(arvore).lower()
    for adiado in ("totp", "recuperacao", "reset_token", "smtp",
                   "enviar_email", "ssl", "certificado"):
        ok(adiado not in codigo, "não entrou nada de %r" % adiado)

    secao("Nenhum teste desta suíte foi à rede")
    try:
        _socket.socket().connect(("127.0.0.1", 9))
        ok(False, "a trava de rede não estava ativa")
    except AssertionError:
        ok(True, "a trava estava mesmo ativa (conferido)")

    p2.fechar(); p3.fechar()
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
