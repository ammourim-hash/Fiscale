#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes das sessões persistentes do Fiscale e da emissão do token do Elo.

Roda sem framework, sem rede e sem tocar em nada do escritório: usa uma
pasta temporária própria.

    python teste_fiscale_sessoes.py
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Pastas temporárias saem de `teste_apoio.pasta_temp()`, que as apaga no fim
# do processo. Antes eram `tempfile.mkdtemp()` soltos, e sobravam em %TEMP%.
import teste_apoio as _ta  # noqa: E402

import fiscale_sessoes
import fiscale_elo

falhas = []


def checar(condicao, descricao):
    if condicao:
        print(f"  ok   {descricao}")
    else:
        print(f"  FALHA {descricao}")
        falhas.append(descricao)


def teste_sessoes(pasta):
    print("\nSessões (SQLite)")

    s = fiscale_sessoes.abrir(pasta)
    tok = s.criar("aline")

    checar(s.get(tok) == "aline", "token recém-criado resolve para o usuário")
    checar(s.get("token-inventado") is None, "token inventado não resolve")
    checar(s.get(None) is None, "token vazio não resolve")

    # O ponto de todo o exercício: reabrir o banco é o que acontece quando
    # o servidor reinicia.
    s2 = fiscale_sessoes.abrir(pasta)
    checar(s2.get(tok) == "aline", "a sessão SOBREVIVE ao reinício")

    s2.revogar(tok)
    checar(s2.get(tok) is None, "logout apaga do servidor, não só o cookie")
    checar(fiscale_sessoes.abrir(pasta).get(tok) is None,
           "sessão revogada continua morta depois de reiniciar")

    # Nada de token puro no arquivo — ele vai para backup e para o Drive.
    t3 = s2.criar("bruno")
    import sqlite3
    con = sqlite3.connect(os.path.join(pasta, "sessoes.db"))
    guardado = [r[0] for r in con.execute("SELECT token_hash FROM sessao")]
    con.close()
    checar(t3 not in guardado, "o banco guarda o HASH, nunca o token")
    checar(len(guardado) > 0 and all(len(h) == 64 for h in guardado),
           "o que está gravado tem cara de SHA-256")

    a = s2.criar("carla")
    b = s2.criar("carla")
    s2.revogar_usuario("carla")
    checar(s2.get(a) is None and s2.get(b) is None,
           "excluir usuário derruba TODAS as sessões dele")
    checar(s2.get(t3) == "bruno", "e não derruba a de mais ninguém")

    # Expiração: validade de 1 segundo.
    curta = fiscale_sessoes.Sessoes(os.path.join(pasta, "curta.db"), validade_s=1)
    tc = curta.criar("efemero")
    checar(curta.get(tc) == "efemero", "sessão curta vale enquanto dura")
    time.sleep(1.2)
    checar(curta.get(tc) is None, "sessão expirada não vale mais")

    checar(len(set(s2.criar("x") for _ in range(50))) == 50,
           "50 tokens seguidos são todos diferentes")


def teste_token_elo(pasta):
    print("\nToken de troca (Ed25519)")

    if not fiscale_elo.TEM_ED25519:
        print("  PULADO: cryptography indisponível nesta instalação")
        return

    kid, _, publica = fiscale_elo.carregar_ou_criar_chave(pasta)
    checar(bool(kid) and bool(publica), "chave criada no primeiro uso")

    kid2, _, publica2 = fiscale_elo.carregar_ou_criar_chave(pasta)
    checar((kid, publica) == (kid2, publica2), "a chave é reaproveitada, não recriada")

    tenant = "07331487-2e97-4106-a16f-d937dd29fceb"
    token = fiscale_elo.emitir_token(pasta, "admin", tenant)
    checar(token.count(".") == 2, "o token tem as três partes")

    import base64
    import json

    def parte(i):
        seg = token.split(".")[i]
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))

    cab, corpo = parte(0), parte(1)
    checar(cab["alg"] == "EdDSA", "assina com EdDSA")
    checar(cab["kid"] == kid, "o cabeçalho traz o kid — é o que permite rotação")
    checar(corpo["iss"] == "fiscale.local", "issuer correto")
    checar(corpo["aud"] == "elo", "audience correta")
    checar(corpo["sub"] == "admin", "sub é o login do Fiscale")
    checar(corpo["tid"] == tenant, "o tenant vai ASSINADO, não pelo navegador")
    checar(corpo["exp"] - corpo["iat"] == 90, "vale 90 segundos")
    checar(len(corpo["jti"]) >= 8, "tem jti para o Elo queimar")

    outro = fiscale_elo.emitir_token(pasta, "admin", tenant)
    checar(parte(1)["jti"] != json.loads(base64.urlsafe_b64decode(
        outro.split(".")[1] + "=" * (-len(outro.split(".")[1]) % 4)))["jti"],
        "cada emissão tem jti próprio")

    # A privada nunca aparece no que sai daqui.
    checar("PRIVATE KEY" not in token, "o token não carrega chave privada")

    try:
        fiscale_elo.emitir_token(pasta, "", tenant)
        checar(False, "sem usuário deveria recusar")
    except fiscale_elo.EloIndisponivel:
        checar(True, "sem usuário autenticado, recusa")

    try:
        fiscale_elo.emitir_token(pasta, "admin", "")
        checar(False, "sem tenant deveria recusar")
    except fiscale_elo.EloIndisponivel:
        checar(True, "sem tenant configurado, recusa")


def teste_config(pasta):
    print("\nConfiguração do Elo")

    checar(fiscale_elo.configuracao(pasta)["tenant_id"] == "",
           "sem arquivo, devolve padrão sem tenant")

    caminho = os.path.join(pasta, "elo_config.json")
    # Com BOM de propósito: é como o Bloco de Notas e o PowerShell gravam.
    with open(caminho, "w", encoding="utf-8-sig") as f:
        f.write('{"url":"http://elo.local:3000/","tenant_id":"abc"}')

    cfg = fiscale_elo.configuracao(pasta)
    checar(cfg["tenant_id"] == "abc", "lê arquivo salvo com BOM")
    checar(cfg["url"] == "http://elo.local:3000", "tira a barra final da URL")

    with open(caminho, "w", encoding="utf-8") as f:
        f.write("isto nao e json")
    checar(fiscale_elo.configuracao(pasta)["tenant_id"] == "",
           "arquivo corrompido cai no padrão em vez de derrubar o Fiscale")


def main():
    pasta = _ta.pasta_temp(prefix="fiscale-teste-")
    print(f"pasta de teste: {pasta}")
    try:
        teste_sessoes(pasta)
        teste_token_elo(pasta)
        teste_config(pasta)
    finally:
        shutil.rmtree(pasta, ignore_errors=True)

    print()
    if falhas:
        print(f"REPROVADO — {len(falhas)} falha(s):")
        for f in falhas:
            print(f"  - {f}")
        sys.exit(1)
    print("Todos os testes passaram.")


if __name__ == "__main__":
    main()
