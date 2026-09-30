#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fiscale — quem é quem no login (Fase 1: entrar pelo e-mail).

    import fiscale_usuarios as us
    uid, reg = us.resolver(us.carregar(arq), "maria@escritorio.com.br")

A IDEIA EM UMA FRASE
    A pessoa passa a entrar pelo e-mail profissional, mas **por dentro ela
    continua sendo o login antigo**.

POR QUE A CHAVE NÃO MUDA
    A chave do dicionário (`"admin"`, `"maria"`) é a identidade interna, e ela
    é IMUTÁVEL. Sessão, permissão, auditoria e — o que mais importa — o `sub`
    do token do ELO apontam para ela.

    Se o e-mail virasse a identidade, trocar o e-mail de alguém quebraria três
    coisas de uma vez: as sessões abertas, o histórico já gravado, e o vínculo
    com o Elo, que casa a pessoa por `providerUid = claims.sub`. O e-mail é um
    APELIDO de entrada — um jeito de achar o registro, nunca o registro.

    É por isso que `resolver()` devolve sempre `(uid, registro)`: quem chama
    nunca precisa saber por qual caminho a pessoa entrou.

O QUE ESTA FASE NÃO FAZ
    Sem política de senha nova, sem bloqueio por tentativas, sem HTTPS, sem
    2FA e sem recuperação de senha. Cada uma dessas é uma decisão própria e
    entra depois; misturar tudo numa mudança só é como se perde a capacidade
    de dizer o que quebrou.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime

# A gravação atômica com trava já existe e já tem teste de concorrência
# (`teste_estado_concorrente.py`). Importar é melhor que copiar: a repetição
# do `os.replace` no Windows é sutil, e duas cópias divergem.
from fiscale_arquivo import gravar_json_atomico, trava_de_escrita

# ── campos ──────────────────────────────────────────────────────────────────
# `sal`, `hash` e `admin` são os de sempre e NÃO se mexe neles.
# `uid`, `nome`, `email` e `ativo` são o que a Fase 1 acrescenta.
CAMPOS_INTOCAVEIS = ("sal", "hash", "admin")
CAMPOS_NOVOS = ("uid", "nome", "email", "ativo")

# Formato de e-mail: deliberadamente permissivo. Não há restrição de domínio —
# cada escritório usa o seu — e validar e-mail com rigor é um problema sem
# fim (a RFC 5322 aceita coisas que nenhum provedor emite). O que se recusa
# aqui é o que claramente não é endereço: sem @, sem domínio, com espaço no
# meio, com duas arrobas.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalizar_email(valor) -> str:
    """Espaço fora, tudo minúsculo.

    E-mail não diferencia maiúscula de minúscula na prática, e quem digita
    numa tela cola espaço no fim sem perceber. Guardar `" Maria@X.COM "` e
    `"maria@x.com"` como coisas diferentes seria criar dois usuários que a
    pessoa jura serem um só.
    """
    return str(valor or "").strip().lower()


def email_valido(valor) -> bool:
    """Parece um endereço de e-mail? Vazio NÃO é inválido — é ausente."""
    e = normalizar_email(valor)
    return bool(e) and bool(_EMAIL.match(e)) and len(e) <= 254


def esta_ativo(registro) -> bool:
    """Ausência de marca significa ATIVO.

    Usuário criado antes desta fase não tem o campo, e ele precisa continuar
    entrando exatamente como entrava ontem. O padrão nunca pode ser o que
    tranca alguém para fora.
    """
    if not isinstance(registro, dict):
        return False
    return bool(registro.get("ativo", True))


def normalizar_registro(chave: str, registro) -> dict:
    """O registro com os campos da Fase 1 preenchidos, sem tocar nos antigos.

    Feito em MEMÓRIA, na leitura. É isto que faz um `usuarios.json` ainda não
    migrado funcionar sem diferença nenhuma — e é a garantia de que restaurar
    um backup velho não tranca ninguém para fora.
    """
    reg = dict(registro or {})
    reg["uid"] = reg.get("uid") or chave          # a identidade interna
    reg["nome"] = reg.get("nome") or ""
    reg["email"] = normalizar_email(reg.get("email"))
    reg["ativo"] = bool(reg.get("ativo", True))
    return reg


def carregar(caminho) -> dict:
    """Lê e normaliza os registros.

    DICIONÁRIO VAZIO SÓ QUANDO O ARQUIVO NÃO EXISTE
        `{}` aqui não quer dizer "não consegui ler": quer dizer **"este
        Fiscale não tem usuários"** — e é isso que abre a tela de criar o
        primeiro administrador. Devolver `{}` porque a leitura falhou seria
        oferecer a criação de um admin novo num sistema que já tem gente.

        Por isso falha de leitura SOBE. Falhar dizendo é melhor que fingir
        que o cadastro está vazio.

    A REPETIÇÃO É DO WINDOWS, E FOI MEDIDA
        Enquanto o `os.replace` da gravação acontece, abrir o arquivo para
        ler dá `PermissionError`. Com 8 escritores e 2 leitores em laço, o
        `teste_login_email.py` pegou exatamente isso — e a primeira versão
        deste código respondia "sem usuários" no meio de uma gravação normal.
    """
    if not os.path.exists(caminho):
        return {}

    ultimo = None
    limite = time.time() + 2.0
    while True:
        try:
            with open(caminho, encoding="utf-8-sig") as f:
                bruto = json.load(f)
            break
        except OSError as e:
            ultimo = e            # alguém está trocando o arquivo; já já solta
            if time.time() >= limite:
                raise ultimo
            time.sleep(0.01)

    if not isinstance(bruto, dict):
        # Arquivo corrompido não é sistema sem usuários. Quem chama precisa
        # ver o erro, não uma tela de primeiro acesso.
        raise ValueError("usuarios.json não contém um objeto JSON")
    return {k: normalizar_registro(k, v) for k, v in bruto.items()
            if isinstance(v, dict)}


def salvar(caminho, usuarios: dict) -> None:
    """Grava com escrita atômica e trava por arquivo.

    A trava é a mesma família da de `state_*.json`: o servidor é
    `ThreadingMixIn`, e dois administradores salvando a tela de usuários ao
    mesmo tempo escreveriam por cima um do outro.
    """
    gravar_json_atomico(caminho, usuarios)


def caminho_backup(caminho) -> str:
    """`usuarios.json` → `usuarios.json.pre-email-<AAAAMMDD-HHMMSS>`.

    Nome com carimbo, e não `.bak`: migração feita duas vezes não apaga a
    prova da primeira.
    """
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    return "%s.pre-email-%s" % (caminho, marca)


def migrar(caminho) -> dict:
    """Grava os campos novos no arquivo, DEPOIS de fazer o backup.

    Idempotente: rodar de novo não muda nada além do que já está lá.

    O backup vem antes de qualquer escrita, e é uma cópia byte a byte do
    arquivo original — não uma reserialização. Se algo der errado no meio, o
    que se restaura é exatamente o que existia.
    """
    r = {"migrado": False, "backup": "", "usuarios": 0, "ja_estava": False}
    if not os.path.exists(caminho):
        r["motivo"] = "arquivo inexistente"
        return r

    with open(caminho, encoding="utf-8-sig") as f:
        bruto = json.load(f) or {}
    if not isinstance(bruto, dict) or not bruto:
        r["motivo"] = "sem usuários"
        return r

    faltando = [k for k, v in bruto.items()
                if isinstance(v, dict)
                and not all(c in v for c in CAMPOS_NOVOS)]
    if not faltando:
        r.update(ja_estava=True, usuarios=len(bruto))
        return r

    destino = caminho_backup(caminho)
    with trava_de_escrita(caminho):
        shutil.copy2(caminho, destino)      # cópia fiel, antes de escrever

    novos = {k: normalizar_registro(k, v) for k, v in bruto.items()
             if isinstance(v, dict)}
    salvar(caminho, novos)
    r.update(migrado=True, backup=destino, usuarios=len(novos))
    return r


def eh_admin(uid: str, registro) -> bool:
    """O registro é de administrador?

    O login literal `admin` é administrador mesmo sem a marca — regra que já
    valia antes desta fase e não pode mudar sob os pés de quem usa o sistema.
    """
    return bool((registro or {}).get("admin", uid == "admin"))


def admins_ativos(usuarios: dict) -> list:
    """Quem pode, AGORA, administrar o sistema.

    Ativo **e** administrador. As duas condições juntas: um administrador
    desativado não administra nada, e contar só a marca `admin` deixaria o
    escritório trancado com um administrador que não consegue entrar.
    """
    return sorted(uid for uid, reg in (usuarios or {}).items()
                  if eh_admin(uid, reg) and esta_ativo(reg))


def ficaria_sem_admin(usuarios: dict, uid: str, *,
                      excluir: bool = False,
                      novo_admin=None, novo_ativo=None) -> bool:
    """A mudança pretendida deixaria o sistema SEM administrador ativo?

    Três portas levam ao mesmo lugar, e todas precisam da mesma guarda:
    excluir o último administrador, desativá-lo, ou tirar o papel dele. Fechar
    só a primeira — que era o que existia — deixava as outras duas abertas.

    Sem administrador ativo, ninguém cadastra usuário, ninguém mexe em
    certificado e ninguém reativa ninguém. E o conserto não é pela tela: é
    editando `usuarios.json` na mão, no servidor.
    """
    usuarios = usuarios or {}
    if uid not in usuarios:
        return False
    restantes = set(admins_ativos(usuarios))
    if uid not in restantes:
        return False          # ele já não conta; mexer nele não tira ninguém

    if excluir:
        continua = False
    else:
        reg = usuarios[uid]
        sera_admin = eh_admin(uid, reg) if novo_admin is None else bool(novo_admin)
        sera_ativo = esta_ativo(reg) if novo_ativo is None else bool(novo_ativo)
        continua = sera_admin and sera_ativo
    if continua:
        return False
    return len(restantes - {uid}) == 0


def indice_email(usuarios: dict) -> dict:
    """e-mail normalizado → uid. Só de quem tem e-mail."""
    saida = {}
    for uid, reg in (usuarios or {}).items():
        e = normalizar_email((reg or {}).get("email"))
        if e:
            saida[e] = uid
    return saida


def email_em_uso(usuarios: dict, email, exceto_uid=None) -> str:
    """De quem é este e-mail? Vazio se de ninguém.

    `exceto_uid` deixa o usuário salvar a própria tela sem colidir consigo
    mesmo — sem isso, editar o nome de alguém sem mexer no e-mail daria
    "e-mail já cadastrado".
    """
    e = normalizar_email(email)
    if not e:
        return ""
    dono = indice_email(usuarios).get(e, "")
    return "" if dono == exceto_uid else dono


def resolver(usuarios: dict, identificador) -> tuple:
    """Acha o usuário por login antigo OU por e-mail. Devolve `(uid, registro)`.

    A ordem importa: o LOGIN ANTIGO vence. Se alguém cadastrasse como e-mail
    de uma pessoa algo que é o login de outra, a dona do login continuaria
    entrando na própria conta.

    Devolve `(None, None)` quando não acha — e quem chama nunca deve dizer
    qual dos dois caminhos falhou.
    """
    ident = str(identificador or "").strip()
    if not ident:
        return None, None
    usuarios = usuarios or {}

    reg = usuarios.get(ident)
    if isinstance(reg, dict):
        return ident, reg

    # O login antigo é gravado em minúsculas pelas rotas de cadastro; aceitar
    # a digitação com maiúscula é conveniência sem risco.
    baixo = ident.lower()
    reg = usuarios.get(baixo)
    if isinstance(reg, dict):
        return baixo, reg

    uid = indice_email(usuarios).get(normalizar_email(ident), "")
    if uid:
        return uid, usuarios[uid]
    return None, None


def para_tela(usuarios: dict) -> list:
    """A lista que a tela de administração mostra. **Sem `sal` e sem `hash`.**

    O navegador nunca precisa do material da senha, e o que não é enviado não
    vaza por tela aberta, por captura de rede nem por log de proxy.
    """
    saida = []
    for uid, reg in sorted((usuarios or {}).items()):
        saida.append({
            "usuario": uid,
            "uid": reg.get("uid") or uid,
            "nome": reg.get("nome") or "",
            "email": reg.get("email") or "",
            "ativo": esta_ativo(reg),
            "admin": bool(reg.get("admin", uid == "admin")),
        })
    return saida
