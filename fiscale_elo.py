#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fiscale — emissão do token de troca para o Elo.

O QUE ISTO É
    Um bilhete de entrada, não um crachá. Vale 90 segundos, serve uma vez
    só, e a única coisa que ele faz é dizer ao Elo: "esta pessoa acabou de
    se autenticar aqui, e é quem eu digo que é". O Elo confere e cria a
    sessão dele. Cookie não é compartilhado entre os dois sistemas.

POR QUE Ed25519 E NÃO SEGREDO COMPARTILHADO
    A chave PRIVADA fica só nesta máquina, dentro do escritório. O Elo
    recebe apenas a PÚBLICA. Amanhã o Elo roda num VPS, que é a ponta mais
    exposta: se aquele servidor for invadido, o atacante consegue conferir
    tokens, mas não consegue emitir nenhum. Com segredo compartilhado,
    invadir o VPS seria ganhar o poder de forjar login de qualquer pessoa
    do escritório.

A CHAVE
    Gerada sozinha no primeiro uso e guardada na pasta de dados, fora do
    repositório. Nunca é enviada a lugar nenhum. A pública é impressa para
    você colar no .env do Elo.

FALHA SEGURA
    Se o `cryptography` não estiver disponível ou a chave não puder ser
    lida, a emissão falha e ponto. O Fiscale continua funcionando
    normalmente — quem quiser abrir o Elo vê um aviso, e nada mais.
"""
import base64
import json
import os
import time
import uuid

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    TEM_ED25519 = True
except Exception:
    TEM_ED25519 = False

# Precisam bater com ELO_EXCHANGE_ISSUER / ELO_EXCHANGE_AUDIENCE no Elo.
ISSUER = "fiscale.local"
AUDIENCE = "elo"

# 90 segundos. Tempo de sair daqui e chegar lá, e nada além disso.
VALIDADE_S = 90

ARQUIVO_CHAVE = "elo_chave_ed25519.json"


class EloIndisponivel(Exception):
    """Não dá para emitir token agora. Não é erro do Fiscale."""


def _b64url(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def _b64url_json(obj) -> str:
    bruto = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _b64url(bruto)


def caminho_chave(pasta_dados: str) -> str:
    return os.path.join(pasta_dados, ARQUIVO_CHAVE)


def carregar_ou_criar_chave(pasta_dados: str):
    """Devolve (kid, chave_privada, publica_base64), criando na primeira vez.

    O `kid` vai no cabeçalho do token. É o que permite trocar a chave sem
    parar o sistema: durante a virada o Elo fica com as duas públicas
    configuradas e cada token é conferido com a sua.
    """
    if not TEM_ED25519:
        raise EloIndisponivel(
            "a biblioteca cryptography não está disponível nesta instalação")

    caminho = caminho_chave(pasta_dados)

    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as f:
                dados = json.load(f)
            privada = serialization.load_pem_private_key(
                dados["privada_pem"].encode("utf-8"), password=None)
            return dados["kid"], privada, dados["publica_b64"]
        except Exception as e:
            raise EloIndisponivel(f"a chave do Elo não pôde ser lida ({e.__class__.__name__})")

    privada = Ed25519PrivateKey.generate()
    kid = "fiscale-" + uuid.uuid4().hex[:8]

    privada_pem = privada.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    # SPKI DER em base64 — exatamente o formato que o Elo espera no
    # ELO_EXCHANGE_PUBLIC_KEYS.
    publica_b64 = base64.b64encode(privada.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )).decode("ascii")

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"kid": kid, "privada_pem": privada_pem,
                   "publica_b64": publica_b64}, f, indent=2)
    try:
        os.chmod(caminho, 0o600)   # sem efeito prático no Windows, mas correto
    except Exception:
        pass

    print("=" * 62)
    print("  Chave de troca Fiscale -> Elo criada.")
    print("  Cole a linha abaixo no .env do Elo:")
    print(f'  ELO_EXCHANGE_PUBLIC_KEYS={{"{kid}":"{publica_b64}"}}')
    print("=" * 62)

    return kid, privada, publica_b64


def emitir_token(pasta_dados: str, usuario: str, tenant_id: str) -> str:
    """Monta e assina o token de troca.

    `usuario` é o login do Fiscale — vira `sub`, e é por ele que o Elo
    encontra a pessoa. `tenant_id` é o escritório; vai assinado justamente
    para o Elo não ter de aceitar essa informação do navegador.
    """
    if not usuario:
        raise EloIndisponivel("sem usuário autenticado")
    if not tenant_id:
        raise EloIndisponivel("o tenant do Elo ainda não foi configurado")

    kid, privada, _ = carregar_ou_criar_chave(pasta_dados)

    agora = int(time.time())
    cabecalho = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    corpo = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": usuario,
        "tid": tenant_id,
        "iat": agora,
        "exp": agora + VALIDADE_S,
        "jti": str(uuid.uuid4()),     # o Elo queima este valor no primeiro uso
    }

    assinado = f"{_b64url_json(cabecalho)}.{_b64url_json(corpo)}"
    assinatura = privada.sign(assinado.encode("ascii"))
    return f"{assinado}.{_b64url(assinatura)}"


def configuracao(pasta_dados: str) -> dict:
    """Para onde mandar o usuário e qual tenant usar.

    Fica em ``elo_config.json`` na pasta de dados: cada escritório aponta
    para a sua instalação do Elo, e nada disso é código.
    """
    caminho = os.path.join(pasta_dados, "elo_config.json")
    padrao = {"url": "http://127.0.0.1:3000", "tenant_id": ""}
    if not os.path.exists(caminho):
        return padrao
    try:
        # utf-8-sig e nao utf-8: o Bloco de Notas e o PowerShell gravam
        # UTF-8 com BOM, e o leitor estrito engasgaria num arquivo que a
        # pessoa acabou de editar do jeito mais natural possivel.
        with open(caminho, encoding="utf-8-sig") as f:
            dados = json.load(f)
        return {"url": (dados.get("url") or padrao["url"]).rstrip("/"),
                "tenant_id": dados.get("tenant_id") or ""}
    except Exception:
        return padrao
