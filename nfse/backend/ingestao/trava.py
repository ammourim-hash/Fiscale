"""
trava.py — impede duas execuções avançarem o mesmo NSU ao mesmo tempo.

O RISCO CONCRETO
    O FISCALE roda como um servidor local com várias rotas. Nada impede o
    usuário clicar "Buscar documentos" duas vezes, ou uma varredura agendada
    começar enquanto a manual ainda roda. Se as duas lerem o checkpoint em
    `ultNSU=100`, ambas buscam o mesmo lote, e a última a gravar decide o valor
    final — podendo *recuar* o ponteiro que a outra já tinha avançado, ou
    avançá-lo por cima de documentos que a outra ainda não salvou.

    Para o CT-e isso é especialmente ruim: sem `consChCTe`, um NSU pulado não
    tem como ser recuperado por chave.

A SOLUÇÃO, DO TAMANHO DO PROBLEMA
    Um arquivo de trava por `(identidade, serviço, ambiente)`, criado com
    `O_CREAT | O_EXCL` — operação atômica no sistema de arquivos, tanto no
    Windows quanto no POSIX. Quem cria, entra; quem encontra o arquivo, desiste
    com `TravaOcupada`.

    Isto é um monólito local: não há Redis, não há banco, não há coordenação
    distribuída — e não deve haver. A trava vale para este computador, que é
    exatamente o escopo em que o problema existe.

TRAVA ÓRFÃ
    Se o processo morre sem liberar, o arquivo fica. Por isso a trava guarda PID
    e horário: uma trava cujo processo não existe mais, ou mais velha que
    `IDADE_MAXIMA`, é considerada órfã e pode ser tomada — com o fato
    registrado, nunca em silêncio.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ambiente import resolver as resolver_ambiente
from .checkpoint import PASTA, validar_servico
from .identidade import normalizar

# Uma varredura longa (23 mil NF-e) leva minutos, não horas. Duas horas é folga
# generosa antes de considerar que o dono da trava morreu.
IDADE_MAXIMA = timedelta(hours=2)


class TravaOcupada(Exception):
    """Já existe execução em andamento para esta chave."""
    def __init__(self, mensagem: str, dono: dict | None = None):
        super().__init__(mensagem)
        self.dono = dono or {}


def _processo_vivo(pid: int) -> bool:
    """O PID ainda existe? Sem matar nada, sem depender de biblioteca externa."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            código = ctypes.c_ulong()
            # 259 = STILL_ACTIVE
            if ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(código)):
                return código.value == 259
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # existe, mas é de outro usuário
    return True


def caminho_trava(dados_dir, identidade, servico: str, ambiente) -> Path:
    ident = normalizar(identidade)
    if not ident.valido:
        raise ValueError(f"identidade fiscal inválida: {identidade!r}")
    srv = validar_servico(servico)
    amb = resolver_ambiente(ambiente)
    return Path(dados_dir) / ident.valor / PASTA / f"{srv}.{amb.nome}.lock"


def _ler_dono(p: Path) -> dict:
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def _orfa(dono: dict) -> tuple[bool, str]:
    """A trava encontrada é de um processo que já morreu ou é velha demais?"""
    pid = int(dono.get("pid") or 0)
    if pid and not _processo_vivo(pid):
        return True, f"o processo {pid} não existe mais"
    try:
        desde = datetime.fromisoformat(str(dono.get("desde")))
        if desde.tzinfo is None:
            desde = desde.replace(tzinfo=timezone.utc)
        idade = datetime.now(timezone.utc) - desde
        if idade > IDADE_MAXIMA:
            return True, f"trava com {int(idade.total_seconds() // 60)} min (limite: {int(IDADE_MAXIMA.total_seconds() // 60)})"
    except Exception:
        return True, "trava sem horário legível"
    return False, ""


@contextmanager
def travar(dados_dir, identidade, servico: str, ambiente, tomar_orfa: bool = True):
    """Segura a chave enquanto o bloco roda.

        with travar(raiz, cnpj, CTE_DISTRIBUICAO, PRODUCAO) as t:
            ...    # ninguém mais avança este NSU

    Levanta `TravaOcupada` se outra execução estiver ativa. Libera sempre — no
    caminho normal e na exceção.
    """
    alvo = caminho_trava(dados_dir, identidade, servico, ambiente)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    info = {"pid": os.getpid(), "desde": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "servico": validar_servico(servico), "ambiente": resolver_ambiente(ambiente).nome}
    notas: list[str] = []

    def _criar():
        # O_EXCL é a garantia: a criação falha se o arquivo já existe, e essa
        # verificação é atômica no sistema de arquivos.
        fd = os.open(str(alvo), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(info, fh)

    try:
        _criar()
    except FileExistsError:
        dono = _ler_dono(alvo)
        orfa, motivo = _orfa(dono)
        if not (orfa and tomar_orfa):
            raise TravaOcupada(
                f"já existe execução para {info['servico']}/{info['ambiente']} "
                f"(pid {dono.get('pid', '?')}, desde {dono.get('desde', '?')})", dono) from None
        notas.append(f"trava órfã assumida: {motivo}")
        try:
            os.unlink(alvo)
            _criar()
        except FileExistsError:
            # Alguém pegou entre o unlink e o create. Perder a corrida aqui é o
            # comportamento certo: um dos dois roda, o outro desiste.
            raise TravaOcupada("outra execução assumiu a trava primeiro", dono) from None

    try:
        yield notas
    finally:
        try:
            if alvo.is_file() and _ler_dono(alvo).get("pid") == os.getpid():
                os.unlink(alvo)
        except OSError:
            pass          # não deixar falha de limpeza mascarar o erro real
