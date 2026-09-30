#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fiscale_backup.py — exportar e restaurar a instalação inteira num arquivo só.

O ARQUIVO
    FISCALE-backup-AAAA-MM-DD-HHMM.fbk  — um ZIP, com esta estrutura:

        manifesto.json      EM CLARO. Versão, data, contagens e o SHA-256 de
                            cada arquivo. Fica em claro de propósito: dá para
                            inspecionar o que tem dentro ANTES de restaurar, e
                            sem precisar da frase-senha.
        dados/**            os dados, sem segredo nenhum
        cofre.fbkc          os .pfx, cifrados com AES-256-GCM

O COFRE
    Chave derivada da frase-senha por scrypt (n=2^15, r=8, p=1, 32 bytes),
    sal aleatório de 16 bytes por backup. Cifra autenticada: se a frase estiver
    errada, ou se um byte do arquivo for adulterado, a abertura falha — não
    devolve lixo silenciosamente.

    A frase-senha NÃO é gravada em lugar nenhum, nem seu hash. Perdeu a frase,
    perdeu os certificados do backup — mas não as empresas, que estão em claro
    e continuam cadastradas.

O QUE NÃO ENTRA (e por quê)
    senha_protegida (DPAPI)   é da máquina de origem; não abriria no destino
    credencial de tela        de qualquer `state_<mod>.json` inscrito no cofre
    sessoes.db / elo_sync.db  efêmeros; sessão nova é o certo depois de migrar
    logs, caches, .venv, build, dist, __pycache__, node_modules, .next
    chaves privadas do ELO    chave privada não sai da máquina que a gerou

    O ELO não participa disto de forma alguma: o backup funciona com ele
    ligado, desligado ou inexistente.

RESTAURAR
    1. lê o manifesto e confere versão, contagens e hashes;
    2. se houver cofre, pede a frase e confirma abrindo (a própria cifra valida);
    3. se o destino já tem dados, NÃO decide sozinho — devolve o resumo e espera
       `cancelar`, `mesclar` ou `substituir`;
    4. extrai, recoloca os .pfx e deixa cada empresa "aguardando senha".
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

import fiscale_dados as fd
import fiscale_migracao as fmigra

FORMATO = 1                       # versão do formato do .fbk
EXTENSAO = ".fbk"
ARQ_MANIFESTO = "manifesto.json"
ARQ_COFRE = "cofre.fbkc"
PASTA_DADOS = "dados/"

# scrypt: 2^15 leva ~0,1 s aqui e torna força bruta cara. Não sobe mais que
# isso porque a restauração roda em máquina fraca também.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
TAM_SAL, TAM_NONCE, TAM_CHAVE = 16, 12, 32

# Nunca entram no pacote.
PASTAS_FORA = {"uploads", "__pycache__", ".venv", "build", "dist",
               "node_modules", ".next", ".turbo", "entrada",
               # ING 3: `<cnpj>/indice/` é CACHE. O `documentos.db` é
               # reconstruído inteiro a partir de `<cnpj>/acervo/` por
               # `ingestao.indice.reconstruir()`, sem tocar na rede. Levá-lo
               # no pacote inflaria o backup com dado derivado — e, pior,
               # poderia restaurar um índice velho por cima de um acervo novo.
               # O acervo e a trilha de auditoria VÃO no pacote: são a verdade.
               "indice",
               # NF-e 6: `<cnpj>/derivados/` é o cache de DANFE. PDF é leitura
               # do XML em papel — pesa mais que o XML que o originou e se
               # refaz num clique. Mesma razão do `indice`, com o agravante do
               # tamanho: 7.500 DANFE inflariam o pacote sem acrescentar
               # nenhuma evidência.
               "derivados",
               # A PASTA CANÔNICA DOS BACKUPS mora DENTRO da raiz de dados
               # (`<FISCALE_DADOS>/backups`). Sem esta linha, cada backup novo
               # engoliria todos os anteriores: o segundo levaria o primeiro, o
               # terceiro levaria os dois, e o tamanho dobraria a cada vez. Um
               # teste pegou isso na primeira execução depois de a pasta
               # canônica passar a existir.
               "backups"}
ARQUIVOS_FORA = {
    "sessoes.db", "elo_sync.db",              # efêmeros
    # Contador de tentativas: janela de 15 minutos, não é histórico.
    "tentativas.db",
    # `.pimenta-login`: segredo do login, preso à conta do Windows pelo DPAPI.
    # Restaurado noutra máquina não abriria, e o sistema o regenera sozinho.
    ".pimenta-login",
    # AUDITORIA (Fase 3): fica FORA por decisão explícita nesta fase. Ela
    # carrega IP e hábito de entrada de pessoas identificadas, e o `.fbk` vai
    # para o Google Drive — mandá-la junto sem ninguém ter decidido isso
    # seria publicar dado pessoal por efeito colateral de uma linha de código.
    "auditoria.db",
    "fiscale.log", "migracao.log",            # logs
    # Marcador da execução em curso (ciclo de vida). Guarda PID e horário
    # DESTA máquina: restaurado noutra, descreveria um processo que nunca
    # existiu ali. É efêmero como as sessões, e sai pelo mesmo motivo.
    ".execucao-atual.json",
    ".chave",                                 # chave do Fernet (fora do Windows)
    # ── ELO: nada dele viaja ────────────────────────────────────────────
    # As duas chaves são privadas e não saem da máquina que as gerou. O
    # elo_config.json parece inofensivo (tenant, URL) mas carrega `app_secret`
    # e `whatsapp_token`; um teste de segurança pegou isso vazando no pacote.
    # Na máquina nova o Elo é reconfigurado — é rápido e é o correto.
    "elo_chave_ed25519.json", "elo_integracao_ed25519.json", "elo_config.json",
}
# `.lock` (ING 2): trava de execução do motor de ingestão. Guarda um PID desta
# máquina; restaurada em outra, seria uma trava de um processo que nunca existiu,
# bloqueando a primeira varredura por até duas horas sem motivo.
# `.fbk` também por SUFIXO, e não só pela pasta: um backup que alguém deixou
# solto na raiz dos dados não pode entrar noutro backup por ter escapado da
# pasta certa. Backup dentro de backup nunca é o que se quis.
SUFIXOS_FORA = {".db-shm", ".db-wal", ".pyc", ".tmp", ".log", ".lock",
                ".fbk", ".parcial"}

# Cópias de segurança que a migração deixa antes de mudar um formato. Ficam só
# nesta máquina: por definição elas guardam o formato ANTIGO — inclusive a senha
# do portal em texto claro, que é justamente o que a migração veio tirar.
#
# `corrompido-<carimbo>` (ING 2) entra na mesma regra: é o checkpoint ilegível
# que o motor isolou como EVIDÊNCIA para o usuário decidir o que fazer. É um
# fato local desta instalação; levá-lo para outra máquina só confundiria, e o
# checkpoint bom vai no pacote normalmente.
_COPIAS_DE_MIGRACAO = re.compile(
    r"\.(pre-port\d+|corrompido(-\d+)?|bak|antigo)$", re.I)


class BackupInvalido(Exception):
    """O arquivo não é um backup do Fiscale, ou está corrompido."""


class FraseIncorreta(Exception):
    """A frase-senha não abre o cofre deste backup."""


# ── utilidades ──────────────────────────────────────────────────────────────
def _sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _sha256_arquivo(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def nome_sugerido(agora: datetime | None = None) -> str:
    return f"FISCALE-backup-{(agora or datetime.now()):%Y-%m-%d-%H%M}{EXTENSAO}"


def _entra_no_backup(p: Path, raiz: Path) -> bool:
    rel = p.relative_to(raiz)
    if any(parte in PASTAS_FORA for parte in rel.parts[:-1]):
        return False
    if p.name in ARQUIVOS_FORA or p.suffix in SUFIXOS_FORA:
        return False
    if _COPIAS_DE_MIGRACAO.search(p.name):
        return False
    # os .pfx vão pelo cofre, não em claro
    if p.suffix.lower() in (".pfx", ".p12"):
        return False
    return True


# ── segredos que precisam ser limpos antes de sair da máquina ──────────────
def _limpar_certificados(bruto: bytes) -> tuple[bytes, int]:
    """Tira `senha_protegida` do cadastro. É DPAPI: não abriria no destino, e
    carregar segredo inútil por aí é só risco."""
    try:
        itens = json.loads(bruto)
    except Exception:
        return bruto, 0
    if not isinstance(itens, list):
        return bruto, 0
    n = 0
    for c in itens:
        if c.pop("senha_protegida", None) is not None:
            n += 1
        c.pop("caminho_origem", None)      # caminho da máquina antiga
    return json.dumps(itens, ensure_ascii=False, indent=2).encode("utf-8"), n


def _limpar_state(mod: str, bruto: bytes) -> tuple[bytes, int]:
    """Tira credencial de tela de um `state_<mod>.json`, seja ele qual for.

    Duas formas precisam sair, e por motivos diferentes:

    • o campo em claro (formato antigo) — nunca pode viajar;
    • o campo protegido por DPAPI (PORT 3) — também não viaja: é da máquina de
      origem, não abriria no destino, e carregar segredo inútil por aí é risco
      sem contrapartida.

    O resto da configuração fica: não é segredo e é trabalhoso de redigitar. O
    manifesto registra quantas senhas saíram, para o usuário saber o que
    reinformar.

    ONDE PROCURAR VEM DA INSCRIÇÃO, NÃO DE UM NOME DE ARQUIVO
        Antes esta função conhecia `state_plano.json` e a lista
        `empresasPlano` pelo nome — o backup ficava amarrado a UMA tela, e uma
        tela nova com credencial viajaria com o segredo dentro sem que nada
        acusasse. Agora a fonte é a mesma inscrição que o cofre usa
        (`fiscale_segredos.MAPA`): inscrever-se no cofre já protege o backup
        pelo mesmo gesto.

        A varredura de `*_protegido` no fim é rede de segurança: blob de DPAPI
        não viaja NUNCA, esteja ou não a tela inscrita. Ela só toca nos campos
        irmãos que o próprio cofre cria — de propósito: heurística que saísse
        apagando qualquer campo chamado "chave" apagaria a chave de acesso das
        notas.
    """
    import fiscale_segredos as fseg
    import fiscale_inscricoes  # noqa: F401  — ativa as inscrições vigentes

    try:
        d = json.loads(bruto)
    except Exception:
        return bruto, 0
    if not isinstance(d, dict):
        return bruto, 0

    n = 0
    for lista, claro, protegido in fseg.MAPA.get(mod, []):
        for item in (d.get(lista) or []):
            if not isinstance(item, dict):
                continue
            cofre = item.pop(protegido, None)
            if isinstance(cofre, dict):
                n += len(cofre)
            item.pop(claro + "_estado", None)
            acesso = item.get(claro)
            if not isinstance(acesso, dict):
                continue
            for chave in list(acesso):
                if fseg.e_segredo(chave) and acesso[chave]:
                    acesso[chave] = ""
                    n += 1

    n += _varrer_protegidos(d)
    if not n:
        # Nada saiu: devolve os bytes ORIGINAIS. Reserializar um arquivo que
        # não mudou trocaria indentação e ordem sem motivo, e o pacote passaria
        # a "mexer" em telas que não têm segredo nenhum — ruído que esconde a
        # mudança real quando alguém for comparar dois backups.
        return bruto, 0
    return json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8"), n


def _varrer_protegidos(no) -> int:
    """Rede de segurança: remove qualquer campo `*_protegido` que tenha sobrado.

    Vale mesmo para tela não inscrita — blob de DPAPI da máquina de origem não
    tem uso no destino, e o que não viaja não vaza."""
    n = 0
    if isinstance(no, dict):
        for chave in list(no):
            if chave.endswith("_protegido"):
                valor = no.pop(chave)
                n += len(valor) if isinstance(valor, dict) else 1
            elif chave.endswith("_estado"):
                no.pop(chave)
        for valor in list(no.values()):
            n += _varrer_protegidos(valor)
    elif isinstance(no, list):
        for valor in no:
            n += _varrer_protegidos(valor)
    return n


def _tratar(rel: str, bruto: bytes, rel_info: dict) -> bytes:
    nome = rel.rsplit("/", 1)[-1]
    if nome == "certificados.json":
        bruto, n = _limpar_certificados(bruto)
        rel_info["senhas_dpapi_removidas"] = n
    elif nome.startswith("state_") and nome.endswith(".json"):
        # Vale para QUALQUER tela: a que guarda credencial hoje e a que passar
        # a guardar amanha sem ninguem lembrar de voltar aqui.
        bruto, n = _limpar_state(nome[len("state_"):-len(".json")], bruto)
        if n:
            rel_info["senhas_portal_removidas"] = (
                rel_info.get("senhas_portal_removidas", 0) + n)
    return bruto


# ── cofre ───────────────────────────────────────────────────────────────────
def _derivar(frase: str, sal: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=sal, length=TAM_CHAVE, n=SCRYPT_N, r=SCRYPT_R,
                  p=SCRYPT_P).derive(frase.encode("utf-8"))


def _fechar_cofre(arquivos: list[tuple[str, bytes]], frase: str) -> bytes:
    """sal(16) || nonce(12) || AES-256-GCM(zip interno com os .pfx)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    interno = io.BytesIO()
    with zipfile.ZipFile(interno, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, dados in arquivos:
            z.writestr(nome, dados)
    sal, nonce = os.urandom(TAM_SAL), os.urandom(TAM_NONCE)
    cifra = AESGCM(_derivar(frase, sal))
    return sal + nonce + cifra.encrypt(nonce, interno.getvalue(), b"fiscale-cofre-v1")


def _abrir_cofre(blob: bytes, frase: str) -> dict[str, bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < TAM_SAL + TAM_NONCE + 16:
        raise BackupInvalido("O cofre do backup está truncado.")
    sal, nonce, corpo = (blob[:TAM_SAL],
                         blob[TAM_SAL:TAM_SAL + TAM_NONCE],
                         blob[TAM_SAL + TAM_NONCE:])
    try:
        claro = AESGCM(_derivar(frase, sal)).decrypt(nonce, corpo, b"fiscale-cofre-v1")
    except Exception:
        # A cifra é autenticada: aqui não dá para distinguir "frase errada" de
        # "arquivo adulterado", e as duas exigem a mesma reação do usuário.
        raise FraseIncorreta(
            "A frase-senha não abre o cofre deste backup — ou o arquivo foi "
            "alterado depois de criado.")
    with zipfile.ZipFile(io.BytesIO(claro)) as z:
        return {n: z.read(n) for n in z.namelist()}


# ══════════════════════════════════════════════════════════════════════════
# EXPORTAR
# ══════════════════════════════════════════════════════════════════════════
def _exportar_v1(destino: str | Path, frase: str | None = None,
             raiz: str | Path | None = None, progresso=None) -> dict:
    """v1 — NÃO É MAIS USADO PARA GERAR. Fica só para os testes de leitura.

    Mantido porque a suíte precisa fabricar pacotes v1 para provar que o
    envio ao Drive os recusa, e que a restauração local ainda os abre.
    Nenhum caminho de produção chama esta função.
    """
    raiz = Path(raiz) if raiz else fd.raiz()
    destino = Path(destino)
    # Sem a extensão .fbk, o que veio é PASTA — mesmo que ainda não exista.
    # Testar só `is_dir()` fazia o backup ser gravado COM O NOME DA PASTA quando
    # o usuário digitava um caminho novo na tela; o arquivo saía sem extensão e
    # sem nome de data.
    if destino.suffix.lower() != EXTENSAO:
        destino.mkdir(parents=True, exist_ok=True)
        destino = destino / nome_sugerido()
    destino.parent.mkdir(parents=True, exist_ok=True)

    arquivos = sorted(p for p in raiz.rglob("*")
                      if p.is_file() and _entra_no_backup(p, raiz))
    pfx = sorted(p for p in (raiz / "certs").rglob("*")
                 if p.is_file() and p.suffix.lower() in (".pfx", ".p12")) \
        if (raiz / "certs").is_dir() else []

    if pfx and not frase:
        raise ValueError(
            f"Há {len(pfx)} certificado(s) para guardar. Informe a frase-senha "
            "de exportação — sem ela os certificados não entram no backup.")

    rel_info: dict = {}
    manifesto = {
        "produto": "Fiscale",
        "formato": FORMATO,
        "criado_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "origem": {"maquina": os.environ.get("COMPUTERNAME") or "",
                   "raiz": str(raiz),
                   "dados_versao": fmigra.versao(raiz)},
        "cofre": {"presente": bool(pfx), "algoritmo": "AES-256-GCM",
                  "kdf": f"scrypt n={SCRYPT_N} r={SCRYPT_R} p={SCRYPT_P}",
                  "arquivos": len(pfx)},
        "arquivos": {},
        "resumo": {},
    }

    temporario = destino.with_suffix(destino.suffix + ".parcial")
    with zipfile.ZipFile(temporario, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for i, p in enumerate(arquivos):
            rel = p.relative_to(raiz).as_posix()
            bruto = _tratar(rel, p.read_bytes(), rel_info)
            z.writestr(PASTA_DADOS + rel, bruto)
            manifesto["arquivos"][rel] = {"sha256": _sha256(bruto), "bytes": len(bruto)}
            if progresso and i % 500 == 0:
                progresso(i, len(arquivos))
        if pfx:
            z.writestr(ARQ_COFRE, _fechar_cofre(
                [(p.name, p.read_bytes()) for p in pfx], frase))
            manifesto["cofre"]["nomes"] = [p.name for p in pfx]

        manifesto["resumo"] = _resumir(raiz, arquivos, pfx, rel_info)
        z.writestr(ARQ_MANIFESTO, json.dumps(manifesto, ensure_ascii=False, indent=1))

    temporario.replace(destino)         # só vira .fbk quando terminou inteiro
    return {"ok": True, "arquivo": str(destino),
            "bytes": destino.stat().st_size, **manifesto["resumo"]}


def exportar(destino, frase=None, raiz=None, progresso=None,
             trabalhadores=None, adiante=None) -> dict:
    """Gera o backup. Hoje isso quer dizer v2 — cifrado por inteiro.

    A tela Home > Backup chama isto e nada mais. Trocar aqui foi o que fez o
    formato novo valer para quem usa o sistema, e não só para quem lê o
    código.
    """
    return exportar_v2(destino, frase, raiz=raiz, progresso=progresso,
                       trabalhadores=trabalhadores, adiante=adiante)


def versao_do_arquivo(fbk) -> int:
    """2, 1, ou 0 quando não é um backup do Fiscale.

    O v2 se identifica pelos 8 primeiros bytes; o v1 é um ZIP com manifesto.
    Perguntar antes de agir é o que permite ler o antigo sem passar a
    produzi-lo.
    """
    if eh_v2(fbk):
        return FORMATO_V2
    try:
        with zipfile.ZipFile(fbk) as z:
            if ARQ_MANIFESTO in z.namelist():
                return FORMATO
    except Exception:
        pass
    return 0


def _resumir(raiz: Path, arquivos: list[Path], pfx: list[Path], rel_info: dict,
             instantes: dict | None = None) -> dict:
    """O resumo que vai no manifesto.

    `instantes` é o retrato `{caminho: (bytes, mtime_ns)}` que a varredura já
    montou. Quando ele vem, os tamanhos saem DALI: a versão anterior fazia uma
    TERCEIRA passada de `stat()` sobre os mesmos arquivos só para somar bytes
    — 91 mil chamadas ao sistema no fim do backup, para um número que já
    estava na memória. Sem ele (caminho v1), o comportamento antigo continua.
    """
    empresas = sorted({p.relative_to(raiz).parts[0] for p in arquivos
                       if len(p.relative_to(raiz).parts) > 1
                       and re.fullmatch(r"\d{11,14}", p.relative_to(raiz).parts[0])})
    estados = {}
    for p in arquivos:
        if p.name != "estado.json":
            continue
        try:
            d = json.loads(p.read_text("utf-8"))
        except Exception:
            continue
        rel = p.relative_to(raiz).as_posix()
        estados[rel] = {"ultNSU": d.get("ultNSU"), "ultimoNSU": d.get("ultimoNSU")}
    cadastradas = 0
    reg = raiz / "certificados.json"
    if reg.exists():
        try:
            cadastradas = len(json.loads(reg.read_text("utf-8")))
        except Exception:
            pass
    return {
        "arquivos": len(arquivos),
        "bytes_originais": (sum(instantes[p][0] for p in arquivos)
                            if instantes is not None
                            else sum(p.stat().st_size for p in arquivos)),
        "empresas_com_documentos": len(empresas),
        "empresas_cadastradas": cadastradas,
        "certificados_no_cofre": len(pfx),
        "estado_sincronismo": estados,
        "estado_sincronismo_qtd": len(estados),
        "senhas_dpapi_removidas": rel_info.get("senhas_dpapi_removidas", 0),
        "senhas_portal_removidas": rel_info.get("senhas_portal_removidas", 0),
    }


# ══════════════════════════════════════════════════════════════════════════
# INSPECIONAR — sem frase-senha, sem extrair nada
# ══════════════════════════════════════════════════════════════════════════
def inspecionar(fbk: str | Path) -> dict:
    """O que dá para saber SEM a frase.

    No v1 isso era o manifesto inteiro — ele ficava em claro. No v2 é só o
    cabeçalho técnico, e é essa a diferença que interessa: o v2 não conta
    nada sobre o escritório para quem não tem a frase.
    """
    fbk = Path(fbk)
    if not fbk.exists():
        raise BackupInvalido(f"Arquivo não encontrado: {fbk}")
    if eh_v2(fbk):
        cabeca = cabecalho_v2(fbk)
        return {"produto": "Fiscale", "formato": FORMATO_V2,
                "cifra": cabeca["cifra"], "kdf": cabeca["kdf"],
                "_arquivo": str(fbk), "_bytes": fbk.stat().st_size,
                "_precisa_frase": True, "_cifrado_por_inteiro": True}
    try:
        with zipfile.ZipFile(fbk) as z:
            ruim = z.testzip()
            if ruim:
                raise BackupInvalido(f"O arquivo está corrompido (em '{ruim}').")
            if ARQ_MANIFESTO not in z.namelist():
                raise BackupInvalido(
                    "Isto não parece um backup do Fiscale: falta o manifesto.")
            man = json.loads(z.read(ARQ_MANIFESTO))
            tem_cofre = ARQ_COFRE in z.namelist()
    except zipfile.BadZipFile:
        raise BackupInvalido("O arquivo não é um backup válido (não é um pacote).")
    if man.get("produto") != "Fiscale":
        raise BackupInvalido("Este pacote não é um backup do Fiscale.")
    if int(man.get("formato", 0)) > FORMATO:
        raise BackupInvalido(
            f"Backup no formato {man.get('formato')}, e esta versão do Fiscale "
            f"lê até o {FORMATO}. Atualize o Fiscale antes de restaurar.")
    man["_arquivo"] = str(fbk)
    man["_bytes"] = fbk.stat().st_size
    man["_precisa_frase"] = bool(tem_cofre)
    return man


def conferir(fbk: str | Path, frase: str | None = None) -> dict:
    """Confere hash a hash. É o que separa 'o zip abre' de 'o backup presta'."""
    if eh_v2(fbk):
        return conferir_v2(fbk, frase)
    man = inspecionar(fbk)
    problemas, conferidos = [], 0
    with zipfile.ZipFile(fbk) as z:
        nomes = set(z.namelist())
        for rel, info in (man.get("arquivos") or {}).items():
            alvo = PASTA_DADOS + rel
            if alvo not in nomes:
                problemas.append(f"falta no pacote: {rel}")
                continue
            if _sha256(z.read(alvo)) != info["sha256"]:
                problemas.append(f"conteúdo diferente do manifesto: {rel}")
            conferidos += 1
        sobrando = [n for n in nomes
                    if n.startswith(PASTA_DADOS)
                    and n[len(PASTA_DADOS):] not in (man.get("arquivos") or {})
                    and not n.endswith("/")]
        if sobrando:
            problemas.append(f"{len(sobrando)} arquivo(s) fora do manifesto")
        if man["_precisa_frase"] and frase:
            _abrir_cofre(z.read(ARQ_COFRE), frase)      # levanta se não abrir
    return {"ok": not problemas, "conferidos": conferidos,
            "problemas": problemas[:30], "problemas_qtd": len(problemas),
            "manifesto": man}


# ══════════════════════════════════════════════════════════════════════════
# FORMATO v2 — O PACOTE INTEIRO CIFRADO
# ══════════════════════════════════════════════════════════════════════════
#
# O QUE MUDOU, E POR QUE
#     No v1 só o cofre dos certificados era cifrado. O resto — manifesto,
#     `usuarios.json` com sal e hash, `certificados.json`, CNPJ, XML, acervo
#     inteiro — abria em qualquer descompactador, sem senha nenhuma. Isso
#     bastava enquanto o `.fbk` ficava na Área de Trabalho; deixou de bastar
#     no instante em que ele passou a subir para o Google Drive.
#
#     Compactar não é cifrar. O v2 cifra o pacote inteiro.
#
# O QUE O ARQUIVO REVELA POR FORA
#     Só o indispensável para conseguir abri-lo: que é um backup do Fiscale,
#     a versão do formato, a função de derivação e seus parâmetros, o sal e o
#     nonce. Nem data, nem nome de máquina, nem contagem de empresas — tudo
#     isso é informação sobre o escritório, e mora dentro.
#
#     O cabeçalho vai como AAD: mexer nele — baixar o custo do scrypt, trocar
#     o nonce — faz a abertura falhar, em vez de produzir lixo.
#
# SEM ZIP EM CLARO NO DISCO
#     A compactação escreve DENTRO do cifrador, que escreve no arquivo final.
#     Em nenhum momento existe um .zip legível em disco — nem temporário e
#     apagado depois, porque "apagado depois" tem estado no disco enquanto
#     dura, e num disco cheio pode nem chegar a ser apagado.
#
#     O `zipfile` aceita fluxo não-buscável desde o Python 3.7: grava
#     descritores de dados em vez de voltar para corrigir cabeçalhos.
#
# O QUE ESTE FORMATO NÃO RESOLVE
#     Quem tem o arquivo vê o TAMANHO dele. Um backup de 80 MB conta que o
#     escritório tem movimento; um de 2 MB, que não tem. Cifra não esconde
#     volume, e fingir que esconde seria pior do que dizer isto aqui.

MAGIC_V2 = b"FSCLBK2\x00"          # 8 bytes, fixos
FORMATO_V2 = 2
TAG_BYTES = 16
_BLOCO = 1 << 20


class _FluxoCifrado(io.RawIOBase):
    """Arquivo de escrita que cifra tudo que passa por ele.

    É isto que deixa o `zipfile` compactar direto para dentro da cifra. Sem
    `seek`: o `zipfile` detecta e usa descritores de dados.
    """

    def __init__(self, saida, cifrador):
        self._saida, self._cifrador, self._n = saida, cifrador, 0

    def writable(self):
        return True

    def seekable(self):
        return False

    def tell(self):
        return self._n

    def write(self, b):
        b = bytes(b)
        if b:
            self._saida.write(self._cifrador.update(b))
            self._n += len(b)
        return len(b)


def _gcm(chave, nonce, tag=None):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    modo = modes.GCM(nonce, tag) if tag else modes.GCM(nonce)
    c = Cipher(algorithms.AES(chave), modo)
    return c.decryptor() if tag else c.encryptor()


def _montar_cabecalho(sal: bytes, nonce: bytes) -> bytes:
    """MAGIC + tamanho + JSON. Só parâmetro técnico — nada sobre o escritório."""
    cabeca = {
        "produto": "Fiscale", "formato": FORMATO_V2,
        "cifra": "AES-256-GCM",
        "kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "dklen": 32,
        "sal": sal.hex(), "nonce": nonce.hex(),
    }
    bruto = json.dumps(cabeca, ensure_ascii=False,
                       sort_keys=True, separators=(",", ":")).encode("utf-8")
    return MAGIC_V2 + len(bruto).to_bytes(4, "big") + bruto


def eh_v2(fbk) -> bool:
    try:
        with open(fbk, "rb") as f:
            return f.read(len(MAGIC_V2)) == MAGIC_V2
    except OSError:
        return False


def cabecalho_v2(fbk) -> dict:
    """O cabeçalho em claro, sem precisar da frase. Traz também os bytes do AAD."""
    with open(fbk, "rb") as f:
        magica = f.read(len(MAGIC_V2))
        if magica != MAGIC_V2:
            raise BackupInvalido("Isto não é um backup do Fiscale no formato 2.")
        tam_bruto = f.read(4)
        if len(tam_bruto) != 4:
            raise BackupInvalido("O backup está truncado no cabeçalho.")
        tam = int.from_bytes(tam_bruto, "big")
        if not 1 <= tam <= 4096:
            raise BackupInvalido("O cabeçalho do backup é inválido.")
        bruto = f.read(tam)
        if len(bruto) != tam:
            raise BackupInvalido("O backup está truncado no cabeçalho.")
    try:
        cabeca = json.loads(bruto)
    except ValueError:
        raise BackupInvalido("O cabeçalho do backup está ilegível.")
    if cabeca.get("produto") != "Fiscale":
        raise BackupInvalido("Este pacote não é um backup do Fiscale.")
    if int(cabeca.get("formato", 0)) != FORMATO_V2:
        raise BackupInvalido(
            "Backup no formato %s, e este código lê o formato %d."
            % (cabeca.get("formato"), FORMATO_V2))
    for campo in ("sal", "nonce", "kdf", "cifra"):
        if not cabeca.get(campo):
            raise BackupInvalido("O cabeçalho do backup está incompleto.")
    # Sal e nonce PRECISAM ser hexadecimal do tamanho certo, e isso se confere
    # aqui. Sem esta checagem, um byte trocado no cabeçalho fazia o
    # `bytes.fromhex` estourar com `ValueError` cru lá adiante — mensagem de
    # programador, no meio de um caminho que existe para recusar arquivo
    # adulterado com clareza.
    for campo, tamanho in (("sal", 32), ("nonce", 24)):
        valor = cabeca[campo]
        if not isinstance(valor, str) or len(valor) != tamanho:
            raise BackupInvalido("O cabeçalho do backup está corrompido.")
        try:
            bytes.fromhex(valor)
        except ValueError:
            raise BackupInvalido("O cabeçalho do backup está corrompido.")
    for campo in ("n", "r", "p", "dklen"):
        if not isinstance(cabeca.get(campo), int) or cabeca[campo] <= 0:
            raise BackupInvalido("O cabeçalho do backup está corrompido.")
    cabeca["_aad"] = magica + tam_bruto + bruto
    cabeca["_inicio_cifra"] = len(magica) + 4 + tam
    return cabeca


# ── leitura adiantada, com fila limitada ───────────────────────────────────
#
# POR QUE ISTO EXISTE
#     Medido nos dados reais: 79.304 arquivos, mediana de 905 bytes, e o
#     backup inteiro é dominado por ABRIR arquivo. Cifrar 362 MB custa 0,6 s;
#     comprimir, 13 s; ler, mais de meia hora. Cada abertura leva ~24 ms num
#     NVMe — sinal de varredura por abertura, e não de disco lento.
#
#     Contra latência por arquivo, o remédio é ter várias aberturas em voo ao
#     mesmo tempo. Não é paralelizar o trabalho: é parar de esperar em fila.
#
# O QUE **NÃO** MUDA
#     A ordem. O manifesto e o ZIP continuam saindo na ordem alfabética de
#     sempre, porque quem escreve é uma thread só, consumindo o resultado na
#     mesma sequência em que pediu. Dois backups dos mesmos dados continuam
#     tendo o mesmo manifesto — se a ordem dependesse de quem terminasse
#     primeiro, o pacote deixaria de ser reproduzível e ninguém perceberia.
#
# A FILA É LIMITADA DE PROPÓSITO
#     Sem teto, o adiantamento leria os 79 mil arquivos para a memória muito
#     antes de o compressor dar conta deles. O teto é `ADIANTE` arquivos
#     pequenos em voo — memória previsível, e nada de encher a fila do disco.
#
# ARQUIVO GRANDE FICA DE FORA
#     Acima de `LIMITE_PARALELO` o arquivo é lido na vez dele, sozinho. Um
#     `.jsonl` de 45 MB não ganha nada com adiantamento — o custo dele é
#     transferência, não latência — e vários deles em voo estourariam a
#     memória, que é justamente o que o teto existe para evitar.

# Medido em fatias FRIAS e INTERCALADAS da mesma região: fatias de regiões
# diferentes têm misturas de tamanho diferentes e não se comparam, e reler a
# mesma fatia mede o cache do Windows, não o disco.
#
#     1 trabalhador   43,93 ms/arquivo   (referência)
#     2               18,51 ms   2,4x
#     4                9,97 ms   4,4x
#     8                6,97 ms   6,3x   <- adotado
#
# Oito porque foi o mais rápido entre os quatro medidos, e porque subir de 4
# para 8 não custa nada: a memória é presa pela FILA, não pelo número de
# trabalhadores, e oito leituras em voo num NVMe não disputam disco. Medi 16
# também e ele ainda melhora — ficou de fora porque não foi pedido, e porque
# mais threads é mais varredura simultânea do antivírus, que é justamente o
# que estamos contornando.
TRABALHADORES = 8
ADIANTE = 16
LIMITE_PARALELO = 1 << 20        # 1 MB


# ══════════════════════════════════════════════════════════════════════════
# FASES — o backup deixa de ser uma caixa preta de N minutos
# ══════════════════════════════════════════════════════════════════════════
# POR QUE NOMEAR FASES
#     Até aqui, "o backup demorou" era tudo que se sabia depois de esperar.
#     Sem fase não há diagnóstico: um backup de 9 minutos gasto em leitura
#     (91 mil arquivos pequenos, antivírus em cada abertura) e um de 9 minutos
#     gasto em conferência pedem decisões OPOSTAS, e eram indistinguíveis.
#
# O QUE CADA UMA É, SEM MENTIR SOBRE O DESENHO
#     A cifra e a compressão correm EM FLUXO dentro do empacotamento — é isso
#     que mantém a memória presa à fila e não ao tamanho do pacote. Então
#     `cifra` e `escrita` medem o FECHAMENTO (finalize + tag; flush + fsync),
#     não o total gasto cifrando. Medir de outro jeito exigiria desmontar o
#     fluxo, que é justamente a parte que funciona.
FASE_DESCOBERTA = "descoberta"
FASE_EMPACOTAMENTO = "empacotamento"
FASE_CIFRA = "cifra"
FASE_ESCRITA = "escrita"
FASE_CONFERENCIA = "conferencia"
FASE_CONCLUSAO = "conclusao"
FASE_FALHA = "falha"
FASES = (FASE_DESCOBERTA, FASE_EMPACOTAMENTO, FASE_CIFRA, FASE_ESCRITA,
         FASE_CONFERENCIA, FASE_CONCLUSAO)

# De quantos em quantos arquivos avisar, e de quanto em quanto tempo. Os dois
# juntos: só a contagem deixaria uma pasta pequena sem nenhum aviso, e só o
# tempo faria 91 mil arquivos avisarem de menos.
PASSO_PROGRESSO = 500
INTERVALO_PROGRESSO = 0.5


def _avisador(progresso):
    """Adapta o callback de progresso, SEM quebrar quem já usava dois argumentos.

    A assinatura antiga é `progresso(feitos, total)` e continua valendo. Quem
    aceitar um terceiro parâmetro recebe também um dicionário com a fase, os
    bytes lidos e os totais.

    A arity é lida uma vez, com `inspect`, e não descoberta por `TypeError`:
    engolir TypeError aqui esconderia um erro de dentro do próprio callback.

    NADA de segredo passa por aqui: só contagens, bytes, nomes de fase e, no
    caso de falha, o NOME do arquivo — nunca conteúdo, caminho de certificado
    ou frase-senha.
    """
    if progresso is None:
        return lambda feitos, total, info=None: None
    try:
        params = [p for p in inspect.signature(progresso).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        aceita_info = len(params) >= 3 or any(
            p.kind == p.VAR_POSITIONAL
            for p in inspect.signature(progresso).parameters.values())
    except (TypeError, ValueError):
        aceita_info = False

    def avisar(feitos, total, info=None):
        if aceita_info:
            progresso(feitos, total, dict(info or {}))
        else:
            progresso(feitos, total)

    return avisar


def _ler_uma_vez(caminho, esperado):
    """Lê o arquivo com UMA abertura e confere que ele não mudou.

    O `fstat` sai do MESMO descritor já aberto: perguntar de novo pelo caminho
    seria uma segunda abertura — e abriria uma janela entre o que se leu e o
    que se conferiu.

    Se o arquivo sumiu, encolheu, cresceu ou foi regravado enquanto o backup
    corria, isto levanta. O pacote inteiro é abandonado logo acima: um backup
    que contém metade de um arquivo é pior que um backup que não existe,
    porque parece existir.
    """
    tamanho, mtime = esperado
    with open(caminho, "rb") as f:
        dados = f.read()
        st = os.fstat(f.fileno())
    if len(dados) != tamanho or st.st_size != tamanho or st.st_mtime_ns != mtime:
        # O NOME vai também em campo próprio, e não só no texto: quem registra
        # o erro (servidor, log, auditoria) não deveria ter de extrair o
        # arquivo de uma frase com expressão regular. Nome, nunca conteúdo.
        erro = BackupInvalido(
            "O arquivo mudou enquanto o backup era gerado: %s" % caminho.name)
        erro.arquivo = caminho.name
        raise erro
    return dados


def _fluxo_de_leitura(arquivos, instantes, trabalhadores=None, adiante=None):
    """Entrega `(caminho, bytes)` NA ORDEM recebida, lendo adiantado.

    Um gerador, e não uma lista: quem consome escreve no ZIP à medida que os
    resultados chegam, e a memória fica presa ao teto da fila em vez de ao
    tamanho do backup.
    """
    trabalhadores = TRABALHADORES if trabalhadores is None else trabalhadores
    adiante = ADIANTE if adiante is None else adiante

    if trabalhadores <= 1:
        for p in arquivos:
            yield p, _ler_uma_vez(p, instantes[p])
        return

    from collections import deque
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=trabalhadores,
                            thread_name_prefix="fiscale-backup") as ex:
        fila = deque()
        restantes = iter(arquivos)

        def adiantar():
            p = next(restantes, None)
            if p is None:
                return False
            # Grande demais: entra na fila SEM tarefa, e é lido na vez dele.
            if instantes[p][0] > LIMITE_PARALELO:
                fila.append((p, None))
            else:
                fila.append((p, ex.submit(_ler_uma_vez, p, instantes[p])))
            return True

        for _ in range(max(1, adiante)):
            if not adiantar():
                break
        try:
            while fila:
                p, tarefa = fila.popleft()
                dados = tarefa.result() if tarefa is not None \
                    else _ler_uma_vez(p, instantes[p])
                adiantar()
                yield p, dados
        except BaseException:
            # Um arquivo falhou. Cancela o que ainda não começou para não
            # ficar segurando o processo lendo o que ninguém vai usar.
            for _, tarefa in fila:
                if tarefa is not None:
                    tarefa.cancel()
            raise


def exportar_v2(destino, frase, raiz=None, progresso=None,
                trabalhadores=None, adiante=None) -> dict:
    """Escreve um `.fbk` v2. A frase é obrigatória: aqui TUDO é cifrado.

    Grava como `.parcial`, confere o pacote fechado abrindo-o de novo, e só
    então renomeia. Um `.fbk` que existe é um `.fbk` que já foi lido inteiro.

    PROGRESSO E FASES
        `progresso` recebe `(feitos, total)` — ou `(feitos, total, info)`, se
        aceitar três argumentos. `info` traz `fase`, `arquivos_total`,
        `bytes`, `bytes_total` e, quando a fase é `falha`, `arquivo` e `erro`.
        O resultado traz `duracao_s` e `fases`, com o tempo de cada etapa.

        Em caso de erro, a exceção sai com `fase`, `arquivo`, `duracao_s` e
        `arquivos_processados` — para quem registra não precisar adivinhar.
    """
    if not frase:
        raise ValueError(
            "Informe a frase-senha. No formato 2 o backup inteiro é cifrado — "
            "sem ela não há como gerar, e sem ela não há como abrir depois.")
    raiz = Path(raiz) if raiz else fd.raiz()
    destino = Path(destino)
    if destino.suffix.lower() != EXTENSAO:
        destino.mkdir(parents=True, exist_ok=True)
        destino = destino / nome_sugerido()
    destino.parent.mkdir(parents=True, exist_ok=True)

    avisar = _avisador(progresso)
    relogio = time.perf_counter
    t_zero = relogio()
    fases: dict = {}
    fase = FASE_DESCOBERTA
    feitos = 0

    arquivos = sorted(p for p in raiz.rglob("*")
                      if p.is_file() and _entra_no_backup(p, raiz))
    pfx = sorted(p for p in (raiz / "certs").rglob("*")
                 if p.is_file() and p.suffix.lower() in (".pfx", ".p12")) \
        if (raiz / "certs").is_dir() else []

    # O retrato de cada arquivo no momento da varredura. É contra ele que
    # a leitura confere: sem isso, um XML regravado no meio do backup
    # entraria pela metade e o pacote sairia "válido".
    instantes = {}
    for _p in arquivos + pfx:
        _st = _p.stat()
        instantes[_p] = (_st.st_size, _st.st_mtime_ns)

    total = len(arquivos)
    bytes_total = sum(v[0] for v in instantes.values())
    fases[FASE_DESCOBERTA] = relogio() - t_zero
    avisar(0, total, {"fase": FASE_DESCOBERTA, "arquivos_total": total,
                      "bytes": 0, "bytes_total": bytes_total})

    sal, nonce = os.urandom(16), os.urandom(12)
    cabecalho = _montar_cabecalho(sal, nonce)
    chave = _derivar(frase, sal)

    rel_info: dict = {}
    manifesto = {
        "produto": "Fiscale", "formato": FORMATO_V2,
        "criado_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "origem": {"maquina": os.environ.get("COMPUTERNAME") or "",
                   "raiz": str(raiz), "dados_versao": fmigra.versao(raiz)},
        "certificados": {"quantidade": len(pfx),
                         "nomes": [p.name for p in pfx]},
        "arquivos": {}, "resumo": {},
    }

    parcial = destino.with_suffix(destino.suffix + ".parcial")
    cifrador = _gcm(chave, nonce)
    cifrador.authenticate_additional_data(cabecalho)
    lidos = 0
    try:
        fase = FASE_EMPACOTAMENTO
        t_fase = relogio()
        ultimo_aviso = 0.0
        with open(parcial, "wb") as saida:
            saida.write(cabecalho)
            fluxo = _FluxoCifrado(saida, cifrador)
            with zipfile.ZipFile(fluxo, "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as z:
                # A leitura vem adiantada; a ESCRITA continua sendo uma
                # só, na ordem em que os arquivos foram pedidos.
                for i, (p, bruto) in enumerate(
                        _fluxo_de_leitura(arquivos, instantes,
                                          trabalhadores, adiante)):
                    rel = p.relative_to(raiz).as_posix()
                    bruto = _tratar(rel, bruto, rel_info)
                    z.writestr(PASTA_DADOS + rel, bruto)
                    manifesto["arquivos"][rel] = {"sha256": _sha256(bruto),
                                                  "bytes": len(bruto)}
                    feitos = i + 1
                    lidos += len(bruto)
                    agora = relogio()
                    if (i % PASSO_PROGRESSO == 0
                            or agora - ultimo_aviso >= INTERVALO_PROGRESSO):
                        ultimo_aviso = agora
                        avisar(feitos, total,
                               {"fase": FASE_EMPACOTAMENTO,
                                "arquivos_total": total, "bytes": lidos,
                                "bytes_total": bytes_total})
                # Os certificados entram como qualquer outro arquivo. Não há
                # mais cofre separado porque não há mais nada em claro do lado
                # de fora para o cofre proteger.
                for p in pfx:
                    rel = "certs/" + p.name
                    bruto = _ler_uma_vez(p, instantes[p])
                    z.writestr(PASTA_DADOS + rel, bruto)
                    manifesto["arquivos"][rel] = {"sha256": _sha256(bruto),
                                                  "bytes": len(bruto)}
                manifesto["resumo"] = _resumir(raiz, arquivos, pfx, rel_info,
                                               instantes)
                z.writestr(ARQ_MANIFESTO,
                           json.dumps(manifesto, ensure_ascii=False, indent=1))
            avisar(feitos, total, {"fase": FASE_EMPACOTAMENTO,
                                   "arquivos_total": total, "bytes": lidos,
                                   "bytes_total": bytes_total})
            fases[FASE_EMPACOTAMENTO] = relogio() - t_fase

            # Fechamento da cifra: o GCM só produz a tag depois do último byte.
            fase = FASE_CIFRA
            t_fase = relogio()
            avisar(feitos, total, {"fase": FASE_CIFRA, "arquivos_total": total,
                                   "bytes": lidos, "bytes_total": bytes_total})
            saida.write(cifrador.finalize())
            saida.write(cifrador.tag)
            fases[FASE_CIFRA] = relogio() - t_fase

            fase = FASE_ESCRITA
            t_fase = relogio()
            avisar(feitos, total, {"fase": FASE_ESCRITA, "arquivos_total": total,
                                   "bytes": lidos, "bytes_total": bytes_total})
            saida.flush()
            os.fsync(saida.fileno())
            fases[FASE_ESCRITA] = relogio() - t_fase

        # Conferir ANTES de existir com nome de backup: abre, autentica e
        # recalcula os hashes internos. Se algo estiver errado, o que sobra
        # chama-se `.parcial`, e ninguém confia num `.parcial`.
        fase = FASE_CONFERENCIA
        t_fase = relogio()
        avisar(feitos, total, {"fase": FASE_CONFERENCIA,
                               "arquivos_total": total, "bytes": lidos,
                               "bytes_total": bytes_total})
        conf = conferir_v2(parcial, frase)
        if not conf["ok"]:
            raise BackupInvalido(
                "O pacote recém-criado não passou na própria conferência: "
                + "; ".join(conf["problemas"][:3]))
        fases[FASE_CONFERENCIA] = relogio() - t_fase
    except BaseException as e:
        # O diagnóstico vai em CAMPOS da exceção: fase, arquivo (só o nome),
        # duração até aqui e quantos arquivos já tinham entrado. Nada de
        # conteúdo e nada de frase-senha.
        try:
            e.fase = fase
            e.duracao_s = round(relogio() - t_zero, 3)
            e.arquivos_processados = feitos
            if not getattr(e, "arquivo", ""):
                e.arquivo = ""
        except Exception:
            pass
        try:
            avisar(feitos, total, {"fase": FASE_FALHA, "arquivos_total": total,
                                   "bytes": lidos, "bytes_total": bytes_total,
                                   "arquivo": getattr(e, "arquivo", ""),
                                   "erro": e.__class__.__name__})
        except Exception:
            pass
        try:
            parcial.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    fase = FASE_CONCLUSAO
    t_fase = relogio()
    parcial.replace(destino)
    fases[FASE_CONCLUSAO] = relogio() - t_fase
    duracao = round(relogio() - t_zero, 3)
    avisar(total, total, {"fase": FASE_CONCLUSAO, "arquivos_total": total,
                          "bytes": lidos, "bytes_total": bytes_total})
    return {"ok": True, "arquivo": str(destino), "formato": FORMATO_V2,
            "bytes": destino.stat().st_size,
            "duracao_s": duracao,
            "bytes_lidos": lidos,
            "fases": {k: round(v, 3) for k, v in fases.items()},
            **manifesto["resumo"]}


@contextlib.contextmanager
def pacote_aberto(fbk, frase=None):
    """O ZIP de dentro, decifrado quando preciso. Nome público de `_pacote`.

    Existe porque, com o v2, "abrir o backup" deixou de ser
    `zipfile.ZipFile(arquivo)`. Sem um caminho único e nomeado, cada teste e
    cada ferramenta inventaria o seu — e um deles inventaria errado.
    """
    with _pacote(fbk, frase) as caminho:
        yield caminho


@contextlib.contextmanager
def _pacote(fbk, frase):
    """O ZIP de dentro, seja qual for o formato.

    v2: decifra para uma pasta temporária isolada e entrega o caminho de lá —
    que é o "extrair primeiro para pasta isolada, conferir, só depois aplicar".
    v1: o próprio arquivo já é o ZIP.
    """
    if eh_v2(fbk):
        with _aberto_v2(fbk, frase) as interno:
            yield interno
    else:
        yield Path(fbk)


@contextlib.contextmanager
def _aberto_v2(fbk, frase):
    """Decifra para uma pasta temporária isolada e entrega o ZIP de dentro.

    Vai para disco, e não para a memória, de propósito: um backup de 80 MB
    virando 80 MB de bytes na RAM, mais o que o `zipfile` copia por cima, é um
    pico que a máquina do escritório não precisa levar.

    A tag do GCM só é conferida no `finalize()`, que acontece DEPOIS de todo o
    texto ter sido escrito. Por isso nada é entregue antes disso: se a
    autenticação falhar, a pasta temporária inteira é apagada e ninguém vê um
    byte. O que existiu no disco no meio do caminho é um `.zip` dentro de uma
    pasta temporária que só este processo conhece — nunca conteúdo extraído.
    """
    if not frase:
        raise FraseIncorreta(
            "Este backup é cifrado por inteiro. Informe a frase-senha.")
    cabeca = cabecalho_v2(fbk)
    chave = _derivar(frase, bytes.fromhex(cabeca["sal"]))
    tamanho = Path(fbk).stat().st_size
    fim_cifra = tamanho - TAG_BYTES
    if fim_cifra <= cabeca["_inicio_cifra"]:
        raise BackupInvalido("O backup está truncado: falta o conteúdo.")

    with open(fbk, "rb") as f:
        f.seek(fim_cifra)
        tag = f.read(TAG_BYTES)
        if len(tag) != TAG_BYTES:
            raise BackupInvalido("O backup está truncado: falta a assinatura.")

    temp = Path(tempfile.mkdtemp(prefix="fiscale_v2_"))
    interno = temp / "pacote.zip"
    try:
        decifrador = _gcm(chave, bytes.fromhex(cabeca["nonce"]), tag)
        decifrador.authenticate_additional_data(cabeca["_aad"])
        with open(fbk, "rb") as f, open(interno, "wb") as saida:
            f.seek(cabeca["_inicio_cifra"])
            restante = fim_cifra - cabeca["_inicio_cifra"]
            while restante > 0:
                pedaco = f.read(min(_BLOCO, restante))
                if not pedaco:
                    raise BackupInvalido("O backup está truncado.")
                restante -= len(pedaco)
                saida.write(decifrador.update(pedaco))
            try:
                saida.write(decifrador.finalize())
            except Exception:
                # A MESMA resposta para frase errada e para arquivo mexido:
                # separar as duas contaria a quem tenta qual das duas ele
                # acertou.
                raise FraseIncorreta(
                    "A frase-senha não abre este backup — ou o arquivo foi "
                    "alterado depois de criado. Nada foi extraído.")
        yield interno
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def conferir_v2(fbk, frase=None) -> dict:
    """Autentica, abre e recalcula o SHA-256 de cada arquivo interno."""
    with _aberto_v2(fbk, frase) as interno:
        with zipfile.ZipFile(interno) as z:
            ruim = z.testzip()
            if ruim:
                raise BackupInvalido("O conteúdo está corrompido (em '%s')." % ruim)
            if ARQ_MANIFESTO not in z.namelist():
                raise BackupInvalido("Falta o manifesto dentro do pacote.")
            man = json.loads(z.read(ARQ_MANIFESTO))
            nomes = set(z.namelist())
            problemas, conferidos = [], 0
            for rel, info in (man.get("arquivos") or {}).items():
                alvo = PASTA_DADOS + rel
                if alvo not in nomes:
                    problemas.append("falta no pacote: %s" % rel)
                    continue
                if _sha256(z.read(alvo)) != info["sha256"]:
                    problemas.append("conteúdo diferente do manifesto: %s" % rel)
                conferidos += 1
            sobrando = [n for n in nomes
                        if n.startswith(PASTA_DADOS)
                        and n[len(PASTA_DADOS):] not in (man.get("arquivos") or {})
                        and not n.endswith("/")]
            if sobrando:
                problemas.append("%d arquivo(s) fora do manifesto" % len(sobrando))
        man["_arquivo"] = str(fbk)
        man["_bytes"] = Path(fbk).stat().st_size
        man["_precisa_frase"] = True
    return {"ok": not problemas, "conferidos": conferidos,
            "problemas": problemas[:30], "problemas_qtd": len(problemas),
            "manifesto": man}



# ══════════════════════════════════════════════════════════════════════════
# RESTAURAR
# ══════════════════════════════════════════════════════════════════════════
# Arquivos que uma instalação recém-criada tem sem ninguém ter trabalhado nela.
# Não contam como "dados a perder": numa máquina nova o usuário cria a senha do
# admin ANTES de restaurar, e sem esta distinção o sistema pediria decisão de
# mesclar/substituir justamente no caminho principal, quando não há nada em risco.
_SO_DO_SISTEMA = {"dados_versao.json", "usuarios.json", "migracao.log", "fiscale.log"}


def estado_do_destino(raiz: str | Path | None = None) -> dict:
    """O que já existe no destino — para o usuário decidir com número na mão.

    `vazio` significa "não há trabalho a perder aqui", e não "a pasta não tem
    nenhum byte"."""
    raiz = Path(raiz) if raiz else fd.raiz()
    if not raiz.exists():
        return {"vazio": True, "arquivos": 0, "empresas": 0, "raiz": str(raiz)}
    arquivos = [p for p in raiz.rglob("*") if p.is_file() and _entra_no_backup(p, raiz)]
    empresas = 0
    reg = raiz / "certificados.json"
    if reg.exists():
        try:
            empresas = len(json.loads(reg.read_text("utf-8")))
        except Exception:
            pass
    trabalho = [p for p in arquivos if p.name not in _SO_DO_SISTEMA]
    return {"vazio": not trabalho and not empresas,
            "arquivos": len(arquivos), "arquivos_de_trabalho": len(trabalho),
            "empresas": empresas, "raiz": str(raiz)}


def _mesclar_certificados(existente: bytes, do_backup: bytes) -> bytes:
    """Une os dois cadastros por CNPJ.

    Quem já está na máquina VENCE — inclusive a `senha_protegida`, que é o
    trabalho de quem já digitou as senhas aqui. Do backup entram só as empresas
    que ainda não existem."""
    try:
        atuais = json.loads(existente)
        novos = json.loads(do_backup)
    except Exception:
        return existente
    if not isinstance(atuais, list) or not isinstance(novos, list):
        return existente
    por_id = {c.get("id") or c.get("cnpj"): c for c in atuais}
    for c in novos:
        chave = c.get("id") or c.get("cnpj")
        if chave not in por_id:
            por_id[chave] = c
    return json.dumps(list(por_id.values()), ensure_ascii=False, indent=2).encode("utf-8")


def _guardar_anterior(raiz: Path, guardado: Path) -> dict:
    """Tira a instalação atual do caminho, sem apagar nada.

    Mover a pasta inteira é o ideal — instantâneo e atômico. Mas no Windows um
    único arquivo aberto (o `sessoes.db`, quando o Fiscale está rodando) trava
    a pasta toda. Então: tenta mover; não dando, move ITEM A ITEM, e o que
    estiver preso fica onde está e é sobrescrito pelo backup.

    O que fica para trás é sempre efêmero por natureza — banco de sessão, log.
    Se algo de valor não puder ser movido, o relatório diz qual."""
    guardado.mkdir(parents=True, exist_ok=True)
    try:
        guardado.rmdir()                      # só some se estiver vazio
        shutil.move(str(raiz), str(guardado))
        raiz.mkdir(parents=True, exist_ok=True)
        return {"anterior_movido_para": str(guardado)}
    except Exception:
        pass

    guardado.mkdir(parents=True, exist_ok=True)
    presos = []
    for item in list(raiz.iterdir()):
        try:
            os.replace(item, guardado / item.name)
        except Exception:
            presos.append(item.name)
    rel = {"anterior_movido_para": str(guardado)}
    if presos:
        rel["nao_puderam_ser_movidos"] = presos
    return rel


def restaurar(fbk: str | Path, frase: str | None = None,
              raiz: str | Path | None = None, modo: str | None = None,
              progresso=None) -> dict:
    """Restaura. `modo` só é exigido quando o destino já tem dados.

    Devolve {"precisa_decisao": True, ...} em vez de escolher sozinho — apagar
    o trabalho de alguém não é decisão de programa."""
    raiz = Path(raiz) if raiz else fd.raiz()
    conf = conferir(fbk, frase if frase else None)
    if not conf["ok"]:
        return {"ok": False, "erro": "O backup não passou na conferência.",
                "problemas": conf["problemas"], "manifesto": conf["manifesto"]}
    man = conf["manifesto"]
    if man["_precisa_frase"] and not frase:
        return {"ok": False, "precisa_frase": True,
                "erro": "Este backup tem certificados. Informe a frase-senha usada na exportação.",
                "manifesto": man}

    destino = estado_do_destino(raiz)
    if not destino["vazio"] and modo not in ("mesclar", "substituir"):
        return {"ok": False, "precisa_decisao": True, "destino": destino,
                "manifesto": man,
                "erro": (f"Já existem dados aqui ({destino['arquivos']} arquivos, "
                         f"{destino['empresas']} empresas). Escolha o que fazer."),
                "opcoes": {
                    "cancelar": "Não mexer em nada.",
                    "mesclar": ("Manter o que já está e acrescentar o que só existe "
                                "no backup. Empresa que existe dos dois lados fica "
                                "como está aqui, inclusive a senha já informada."),
                    "substituir": ("Usar o backup como verdade. O que está aqui é "
                                   "MOVIDO para uma pasta ao lado — nada é apagado."),
                }}

    raiz.mkdir(parents=True, exist_ok=True)
    rel = {"modo": modo or "novo", "escritos": 0, "mantidos": 0,
           "certificados": 0, "aguardando_senha": 0}

    # A marca `.fiscale-portatil` é a IDENTIDADE da instalação, não dado dela:
    # é ela que faz o Fiscale guardar tudo ao lado do programa. O modo
    # "Substituir" levava a marca junto com o resto, e no reinício seguinte o
    # pacote portátil passava a apontar para C:\Users\<voce>\Fiscale — deixava
    # de ser portátil, em silêncio, logo depois de uma restauração.
    era_portatil = (raiz / fd.MARCA_PORTATIL).exists()

    if modo == "substituir" and not destino["vazio"]:
        guardado = raiz.parent / f"{raiz.name}-substituido-{datetime.now():%Y%m%d-%H%M%S}"
        rel.update(_guardar_anterior(raiz, guardado))

    # No v2 isto decifra para uma pasta temporária isolada e trabalha de lá; a
    # autenticação já falhou lá em cima se a frase estivesse errada, e nada
    # chega a ser escrito na raiz de dados antes disso.
    with _pacote(fbk, frase) as _dentro, zipfile.ZipFile(_dentro) as z:
        nomes = [n for n in z.namelist() if n.startswith(PASTA_DADOS) and not n.endswith("/")]
        for i, nome in enumerate(nomes):
            sub = nome[len(PASTA_DADOS):]
            alvo = raiz / sub
            bruto = z.read(nome)
            if alvo.exists() and modo == "mesclar":
                if sub == "certificados.json":
                    alvo.write_bytes(_mesclar_certificados(alvo.read_bytes(), bruto))
                    rel["escritos"] += 1
                else:
                    rel["mantidos"] += 1
                continue
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(bruto)
            rel["escritos"] += 1
            if progresso and i % 500 == 0:
                progresso(i, len(nomes))

        # Só o v1 tem cofre separado. No v2 os .pfx já vieram no laço acima,
        # como qualquer outro arquivo — não há mais cofre porque não há mais
        # nada em claro do lado de fora.
        if ARQ_COFRE in z.namelist():
            certs = fd.pasta_certs() if raiz == fd.raiz() else (raiz / "certs")
            certs.mkdir(parents=True, exist_ok=True)
            for nome, dados in _abrir_cofre(z.read(ARQ_COFRE), frase).items():
                destino_pfx = certs / nome
                if destino_pfx.exists() and modo == "mesclar":
                    continue
                destino_pfx.write_bytes(dados)
                rel["certificados"] += 1
        else:
            rel["certificados"] = sum(
                1 for n in nomes
                if n[len(PASTA_DADOS):].lower().startswith("certs/"))

    if era_portatil and not (raiz / fd.MARCA_PORTATIL).exists():
        (raiz / fd.MARCA_PORTATIL).write_text(
            "Esta marca faz o Fiscale guardar os dados nesta pasta,\n"
            "ao lado do programa. Não apague.\n", encoding="utf-8")
        rel["marca_portatil_reposta"] = True

    rel["aguardando_senha"] = _marcar_aguardando(raiz)
    fmigra.garantir(raiz)          # deixa a raiz no formato atual
    rel["ok"] = True
    rel["manifesto"] = man
    return rel


def _marcar_aguardando(raiz: Path) -> int:
    """Quantas empresas ficam sem senha nesta máquina.

    Não precisa gravar marca nenhuma: `senha_protegida` já saiu no backup, e o
    `estado_senha()` do módulo NFS-e responde "aguardando" para quem não tem.
    Aqui só se conta, para o relatório da restauração dizer o tamanho da tarefa."""
    reg = raiz / "certificados.json"
    if not reg.exists():
        return 0
    try:
        return sum(1 for c in json.loads(reg.read_text("utf-8"))
                   if not c.get("senha_protegida"))
    except Exception:
        return 0
