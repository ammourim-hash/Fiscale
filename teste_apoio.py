#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teste_apoio.py — raiz temporária e trava contra a pasta de dados REAL.

O RISCO CONCRETO
    As suítes já usam `tempfile`, mas isso depende de cada teste lembrar de
    fazer certo. Um esquecimento grava em `~/Fiscale/dados` — 30 mil arquivos
    fiscais de 21 empresas — e o estrago só aparece dias depois, quando alguém
    procura uma nota que sumiu. Disciplina não é proteção; trava é.

COMO FUNCIONA
    `raiz_temporaria()` cria uma pasta em `tempfile`, aponta `FISCALE_DADOS`
    para ela, limpa o cache do `fiscale_dados` e **liga a trava**
    `FISCALE_TESTE_PROIBIR_RAIZ_REAL`. Com a trava ligada, qualquer código que
    tente resolver a raiz para `~/Fiscale/dados` ou `~/SistemaNFSe/dados`
    levanta `RuntimeError` na hora, em vez de escrever.

    Ao sair, apaga a pasta e devolve as variáveis de ambiente ao que eram.

    A trava é **opt-in**: sem a variável, `fiscale_dados` se comporta
    exatamente como sempre. A instalação normal do usuário não muda.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fiscale_dados as fd  # noqa: E402

# Lido pelo runner por AST, sem importar: declara que este arquivo é
# APOIO, não suíte. Rodá-lo não executa asserção nenhuma, e contá-lo
# como suíte verde seria contar um arquivo que nunca testou nada.
TESTE_APOIO = True

# ── temporários que se apagam sozinhos ──────────────────────────────────────
# As suítes usavam `tempfile.mkdtemp()` direto e nunca apagavam: sobraram 106
# pastas em %TEMP%, várias com segredos FICTÍCIOS plantados pelos testes. Lixo
# com cara de segredo é ruim mesmo quando não é segredo — alguém encontra e
# perde tempo, ou pior, conclui que vazou de verdade.
#
# `pasta_temp()` registra a pasta para ser apagada no fim do processo.
#
# Deliberadamente NÃO escreve arquivo marcador dentro: várias dessas pastas são
# usadas como RAIZ DE DADOS pelos testes de backup e portabilidade, que contam
# e comparam arquivo por arquivo. Um marcador ali dentro entraria no `.fbk` e
# quebraria justamente as suítes que verificam integridade. Sobras antigas são
# identificadas pelo prefixo do projeto (ver `limpar_temporarios.py`).
PREFIXOS_DO_PROJETO = ("port1_", "port2_", "port3_", "port4_",
                       "ing1_", "ing2_", "fiscale_teste_", "fiscale-teste-",
                       "fiscale-sync-teste-")
_CRIADAS: list[Path] = []


def pasta_temp(prefixo: str = "fiscale_teste_", prefix: str | None = None) -> Path:
    """Pasta temporária que se apaga no fim do processo. Use no lugar de
    `tempfile.mkdtemp()` em qualquer teste.

    Aceita `prefix=` além de `prefixo=` para ser substituição direta de
    `tempfile.mkdtemp()` nas suítes que já existiam."""
    p = Path(tempfile.mkdtemp(prefix=prefix if prefix is not None else prefixo))
    _CRIADAS.append(p)
    return p


def registrar_para_apagar(p) -> Path:
    """Marca uma pasta já existente para ser apagada no fim do processo."""
    p = Path(p)
    if p not in _CRIADAS:
        _CRIADAS.append(p)
    return p


@atexit.register
def _apagar_temporarios() -> None:
    """Apaga o que os testes criaram — inclusive o que o próprio Fiscale criou
    ao lado.

    Dois detalhes descobertos medindo o %TEMP% antes e depois de uma rodada:

    1. Restaurar backup em modo "Substituir" move a pasta anterior para uma
       IRMÃ `<raiz>-substituido-<carimbo>` (por desenho: a restauração nunca
       apaga nada). Essa irmã nasce fora do nosso registro, então é varrida
       aqui a partir do nome da pasta que criamos.
    2. O `elo_sync.db` continua aberto quando o teste termina, e no Windows
       arquivo aberto não é apagado. Um `gc.collect()` fecha a conexão órfã e a
       segunda tentativa funciona.
    """
    import gc

    # `fiscale_elo_sync.fila()` guarda a Fila num dicionário de módulo, então a
    # conexão SQLite fica viva enquanto o processo existir — e no Windows um
    # .db aberto simplesmente não é apagado. Soltar o cache é suficiente; não
    # mexemos no ELO por causa de limpeza de teste.
    try:
        import fiscale_elo_sync as _sync
        with _sync._trava_filas:
            _sync._filas.clear()
    except Exception:
        pass

    # E TODA CONEXAO SQLITE VIVA, e nao so a do ELO.
    #     Medido em 07/09/2026, rodando a suite inteira e comparando %TEMP%
    #     antes e depois: seis suites deixavam pasta para tras, e em TODAS o
    #     unico arquivo sobrevivente era um `.db` -- `tentativas.db`,
    #     `auditoria.db`, `sessoes.db`, `documentos.db`. No Windows arquivo
    #     aberto nao e apagado, e o `rmtree(ignore_errors=True)` falha calado.
    #
    #     Tratar modulo por modulo nao escala: cada cache novo de conexao
    #     traria a mesma sobra meses depois, e ninguem ligaria uma coisa a
    #     outra. Aqui o processo ja esta ACABANDO -- fechar tudo que for
    #     `sqlite3.Connection` nao pode quebrar nada, e cobre inclusive o
    #     modulo que ainda nem existe.
    try:
        import sqlite3
        gc.collect()
        for obj in gc.get_objects():
            if isinstance(obj, sqlite3.Connection):
                try:
                    obj.close()
                except Exception:
                    pass
        gc.collect()
    except Exception:
        pass

    alvos: list[Path] = []
    for p in _CRIADAS:
        alvos.append(p)
        try:
            alvos.extend(sorted(p.parent.glob(p.name + "-substituido-*")))
            alvos.extend(sorted(p.parent.glob(p.name + "-migrado-para-fiscale*")))
        except OSError:
            pass

    restantes = [a for a in alvos if a.exists()]
    for tentativa in range(3):
        if not restantes:
            break
        if tentativa:
            gc.collect()          # solta handles de SQLite ainda abertos
            time.sleep(0.05)      # o antivirus tambem segura, por um instante
        for a in list(restantes):
            shutil.rmtree(a, ignore_errors=True)
            # AINDA ASSIM PODE SOBRAR O ESQUELETO: o `rmtree` leva os arquivos
            # e falha no diretorio. Uma pasta vazia nao guarda dado, mas
            # acumula -- foram 261 delas em %TEMP% antes desta linha existir.
            if a.exists():
                try:
                    for d in sorted(a.rglob("*"), reverse=True):
                        if d.is_dir():
                            d.rmdir()
                    a.rmdir()
                except OSError:
                    pass
            if not a.exists():
                restantes.remove(a)
    _CRIADAS.clear()


def raizes_reais() -> set[Path]:
    """As pastas de produção desta máquina — as que nenhum teste pode tocar."""
    return fd._raizes_reais()


def e_raiz_real(p) -> bool:
    try:
        alvo = Path(p).expanduser().resolve()
    except OSError:
        return False
    return any(alvo == r.resolve() if r.exists() else alvo == r for r in raizes_reais())


def exigir_raiz_temporaria(p) -> Path:
    """Barra na entrada. Use no começo de qualquer teste que escreva em disco."""
    alvo = Path(p).expanduser().resolve()
    if e_raiz_real(alvo):
        raise RuntimeError(f"teste tentou usar a pasta de dados REAL: {alvo}")
    return alvo


@contextmanager
def raiz_temporaria(prefixo: str = "fiscale_teste_"):
    """Raiz de dados isolada, com a trava ligada e limpeza garantida.

        with raiz_temporaria("ing1_") as raiz:
            ...   # raiz é uma pasta descartável; a real está protegida
    """
    antes_dados = os.environ.get("FISCALE_DADOS")
    antes_trava = os.environ.get(fd.VAR_PROIBIR_RAIZ_REAL)
    antes_cache = fd._raiz_cache

    raiz = Path(tempfile.mkdtemp(prefix=prefixo))
    # REGISTRA TAMBEM PARA A VARREDURA FINAL.
    #     O `finally` abaixo apaga na saida do bloco -- e nesse instante a
    #     conexao SQLite que o teste abriu ainda esta viva, entao o `.db` nao
    #     e apagado e o `rmtree` falha calado. Medido em 07/09/2026: o
    #     `teste_cte1` deixava 13 pastas por execucao, cada uma com um
    #     `documentos.db` dentro.
    #
    #     Registrando aqui, a limpeza de saida do processo pega o que sobrou
    #     -- e la as conexoes ja foram fechadas.
    registrar_para_apagar(raiz)
    exigir_raiz_temporaria(raiz)
    os.environ["FISCALE_DADOS"] = str(raiz)
    os.environ[fd.VAR_PROIBIR_RAIZ_REAL] = "1"
    fd._raiz_cache = None
    try:
        fd.raiz(raiz)
        yield raiz
    finally:
        fd._raiz_cache = antes_cache
        for var, valor in ((fd.VAR_PROIBIR_RAIZ_REAL, antes_trava),
                           ("FISCALE_DADOS", antes_dados)):
            if valor is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = valor
        shutil.rmtree(raiz, ignore_errors=True)


# ── certificado de teste, gerado na hora ────────────────────────────────────
def gerar_pfx(destino: Path, senha: str, cn: str = "EMPRESA TESTE LTDA",
              dias_validade: int = 365) -> Path:
    """Cria um `.pfx` autoassinado só para o teste.

    Nenhum teste usa o certificado real do usuário: além de ser dado sensível,
    ele expira e faria a suíte quebrar sozinha um dia. Validade negativa gera
    um certificado já vencido, que é como se testa a recusa.
    """
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    agora = datetime.now(timezone.utc)
    fim = agora + timedelta(days=dias_validade)
    # Com validade negativa (certificado já vencido) o início precisa recuar
    # junto — senão o próprio construtor recusa antes de o teste chegar ao ponto.
    inicio = min(agora - timedelta(days=1), fim - timedelta(days=1))
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(inicio)
        .not_valid_after(fim)
        .sign(chave, hashes.SHA256())
    )
    dados = pkcs12.serialize_key_and_certificates(
        name=cn.encode("utf-8"), key=chave, cert=cert, cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(senha.encode("utf-8")),
    )
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)
    return destino


# ════════════════════════════════════════════════════════════════════════════
#  Encerrar um servidor de teste — a ÁRVORE, e só ela
# ════════════════════════════════════════════════════════════════════════════
# O QUE ACONTECIA
#     Uma suíte que sobe um Fiscale de teste chamava `proc.terminate()` no
#     final. Isso mata o processo lançado — e mais nada.
#
#     Só que o `fiscale_server.py` lança o `nfse/runner.py` como processo
#     próprio, e no Windows um neto NÃO morre junto com o avô. Cada suíte que
#     subia um servidor deixava dois `runner.py` vivos, cada um segurando uma
#     porta interna. Depois de uma rodada completa eram catorze processos
#     órfãos ocupando 8791, 8792, 8793…
#
#     Medido em 30/08/2026: `teste_importar_http` deixava 2,
#     `teste_clientes_modelo` deixava 2, e assim por diante a cada execução.
#
# POR QUE NÃO MATAR POR NOME OU POR PORTA
#     Matar "todo python que rode fiscale_server" derrubaria o FISCALE do
#     usuário, que roda o dia inteiro na 8777. Matar "quem estiver na porta X"
#     tem a mesma armadilha no dia em que a porta coincidir. A única
#     identificação segura é a genealogia: o PID que ESTA suíte lançou, e os
#     descendentes dele.
def descendentes(pid: int) -> list[int]:
    """PIDs abaixo de `pid`, do mais fundo para o mais raso.

    A ordem importa: matar o avô primeiro faz o neto perder o pai e virar
    órfão de verdade, sem ninguém para colhê-lo.
    """
    filhos_de: dict[int, list[int]] = {}
    if sys.platform == "win32":
        try:
            saida = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | "
                 "ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
                capture_output=True, text=True, timeout=60).stdout
        except Exception:
            return []
        for linha in saida.splitlines():
            partes = linha.split()
            if len(partes) == 2 and partes[0].isdigit() and partes[1].isdigit():
                filhos_de.setdefault(int(partes[1]), []).append(int(partes[0]))
    else:                                                    # pragma: no cover
        try:
            saida = subprocess.run(["ps", "-eo", "pid=,ppid="],
                                   capture_output=True, text=True,
                                   timeout=60).stdout
        except Exception:
            return []
        for linha in saida.splitlines():
            partes = linha.split()
            if len(partes) == 2:
                filhos_de.setdefault(int(partes[1]), []).append(int(partes[0]))

    fundo: list[int] = []

    def desce(p, profundidade=0):
        if profundidade > 12:          # ciclo de PID reaproveitado
            return
        for f in filhos_de.get(p, []):
            desce(f, profundidade + 1)
            fundo.append(f)

    desce(pid)
    return fundo


def encerrar_arvore(proc, espera: float = 15.0) -> list[int]:
    """Encerra `proc` E os descendentes dele. Devolve os PIDs encerrados.

    Chamável quantas vezes for preciso e em qualquer estado — sucesso, falha,
    asserção interrompida, exceção ou timeout. Nunca levanta: o lugar dela é
    um `finally`, e um `finally` que estoura esconde o erro de verdade.
    """
    if proc is None:
        return []
    pid = getattr(proc, "pid", None)
    if not pid:
        return []

    alvos = descendentes(pid) + [pid]        # netos primeiro, o lançado por último
    encerrados = []
    for alvo in alvos:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(alvo), "/T", "/F"],
                               capture_output=True, timeout=30)
            else:                                            # pragma: no cover
                os.kill(alvo, 9)
            encerrados.append(alvo)
        except Exception:
            pass
    try:
        proc.wait(timeout=espera)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return encerrados
