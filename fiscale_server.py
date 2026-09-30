#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fiscale — servidor local (somente biblioteca padrão do Python).
Serve a HOME e os módulos, guarda os dados em JSON e recebe certificado/PDF.
Rode com:  python3 fiscale_server.py   (ou dois cliques em iniciar.bat / iniciar.sh)
"""
import http.server, socketserver, json, os, sys, webbrowser, threading, hashlib, hmac, secrets
import atexit, subprocess, urllib.request, urllib.error, time
import base64, re, shutil
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from http import cookies as http_cookies

# ESTE SERVIDOR IMPORTA O CÓDIGO QUE ESTÁ AO LADO DELE — sempre.
#     Sem esta linha, quem decide de onde vem `situacao`, `fiscale_papeis` e os
#     demais módulos é o `sys.path` do interpretador. O runtime instalado em
#     `C:\ProgramData\Fiscale\runtime` é um Python embeddable com `._pth`, e o
#     `._pth` coloca a pasta `app` da INSTALAÇÃO à frente de tudo e ignora o
#     diretório atual e o PYTHONPATH.
#
#     O efeito é silencioso e pernicioso: rodar `python fiscale_server.py` de
#     dentro do repositório sobe ESTE arquivo, mas com os módulos de apoio da
#     instalação — duas versões do sistema no mesmo processo. Um teste passa ou
#     falha por causa de código que ninguém está editando.
#
#     `nfse/backend/main.py` já fazia isso (linhas 33 e 54); o servidor
#     principal não, e a assimetria só apareceu depois da migração para o
#     ProgramData. Nunca é a pasta do .exe: quando congelado, `BASE` resolve
#     sozinho logo abaixo.
if not getattr(sys, "frozen", False):
    _aqui = os.path.dirname(os.path.abspath(__file__))
    if _aqui not in sys.path[:1]:
        sys.path.insert(0, _aqui)

# Dados públicos do CNPJ: optante do Simples + município (escolhe a prefeitura)
import cnpj_publico
import ncm_publico

# cryptography é opcional: se existir, lê o CNPJ/nome do certificado .pfx
try:
    from cryptography.hazmat.primitives.serialization import pkcs12
    TEM_CRYPTO = True
except Exception:
    TEM_CRYPTO = False

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    # .exe / pacote: os recursos vêm de dentro; os dados, não.
    BASE = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")

# RAIZ ÚNICA DE DADOS. Quem decide é `fiscale_dados.raiz()` — o mesmo módulo
# que o backend do NFS-e usa. Antes da PORT 1 cada um escolhia por conta e o
# sistema ficava partido em duas metades que não se enxergavam.
import atalho_desktop
import pastas_windows
import fiscale_dados as fdados
import fiscale_migracao as fmigra
import fiscale_segredos
import fiscale_inscricoes  # noqa: F401  — ativa as inscrições de tela no cofre

DADOS = str(fdados.raiz())
_migracao = fmigra.garantir(DADOS)      # idempotente
UPLOADS = os.path.join(DADOS, "uploads")
CERTS = os.path.join(DADOS, "certs")
for d in (DADOS, UPLOADS, CERTS):
    os.makedirs(d, exist_ok=True)

# Sem janela de console (exe "windowed"), sys.stdout/stderr são None e qualquer
# print() estoura AttributeError. Manda tudo para um log — que também é a única
# forma de diagnosticar um problema quando não há console para ler.
LOG = os.path.join(DADOS, "fiscale.log")
if sys.stdout is None or sys.stderr is None:
    try:
        _saida = open(LOG, "a", encoding="utf-8", buffering=1, errors="replace")
    except Exception:
        _saida = open(os.devnull, "w")
    sys.stdout = sys.stderr = _saida

# Porta do servidor (padrão 8777; configurável p/ testes ou conflito de porta)
PORT = int(os.environ.get("FISCALE_PORT") or os.environ.get("PORT") or 8777)

# Interface em que o servidor escuta.
#
# HOJE o padrão é `0.0.0.0`: o Fiscale atende as outras máquinas do
# escritório direto, e é o login que protege. Amanhã, quando o Caddy
# estiver na frente falando HTTPS pela rede privada, o padrão vira
# `127.0.0.1` — e aí só o proxy alcança a porta 8777.
#
# A variável existe ANTES dessa virada de propósito: dá para ensaiar o
# arranjo novo (`FISCALE_BIND=127.0.0.1`) sem editar código, e dá para
# voltar atrás sem editar código. Trocar o padrão é uma linha, e é uma
# decisão — não um efeito colateral de outra mudança.
BIND_PADRAO = "0.0.0.0"


def endereco_de_escuta(ambiente=None) -> str:
    """Em qual interface escutar. Vazio ou ausente = o padrão de hoje."""
    fonte = os.environ if ambiente is None else ambiente
    return (fonte.get("FISCALE_BIND") or "").strip() or BIND_PADRAO


def escuta_so_local(endereco: str) -> bool:
    """O endereço atende SÓ esta máquina?

    Decide o que a tela de inicialização pode prometer: anunciar
    "Na rede local" quando ninguém da rede alcança seria pior do que não
    anunciar nada.
    """
    return endereco in ("127.0.0.1", "::1", "localhost")


# ══════════════════════════════════════════════════════════════════════════
# CICLO DE VIDA — quando o servidor subiu, quando caiu, e por quê
# ══════════════════════════════════════════════════════════════════════════
# O QUE ESTAVA FALTANDO
#     O `fiscale.log` guardava vinte banners de inicialização e NENHUMA data.
#     Descobrir quando a produção reiniciou exigiu ir ao Visualizador de
#     Eventos do Windows correlacionar logon, `explorer.exe` e horário de
#     criação de processo — para responder uma pergunta que o próprio sistema
#     deveria saber responder sobre si.
#
# O QUE ISTO É, E O QUE NÃO É
#     São linhas de registro, e nada mais. Não há supervisor, não há reinício
#     automático, o startup continua sendo a pasta Inicializar e o Job Object
#     não mudou — aqui ele só é DESCRITO quando vincula ou falha.
#
# SOBRE O MOTIVO DO ENCERRAMENTO, E POR QUE ELE ÀS VEZES CHEGA ATRASADO
#     "Encerrar" pela tela e pela bandeja passam por aqui e dizem o motivo na
#     hora. Fim de sessão do Windows e terminação externa NÃO passam: o
#     processo é morto, `atexit` não roda e não há o que escrever naquele
#     instante. Para esses, o marcador de execução em disco deixa a resposta
#     para a PRÓXIMA subida: se o marcador da execução anterior ficou para
#     trás, ela terminou sem registro, e isso é dito com esse nome —
#     `FIM_DA_SESSAO_OU_TERMINACAO_EXTERNA`. Quando nem isso dá para afirmar,
#     o registro diz `MOTIVO_NAO_DETERMINADO`. Nenhuma linha inventa causa.
MOTIVO_TELA = "ENCERRAR_TELA"
MOTIVO_BANDEJA = "ENCERRAR_BANDEJA"
MOTIVO_TECLADO = "INTERRUPCAO_DE_TECLADO"
MOTIVO_EXTERNO = "FIM_DA_SESSAO_OU_TERMINACAO_EXTERNA"
MOTIVO_DESCONHECIDO = "MOTIVO_NAO_DETERMINADO"
MOTIVOS = (MOTIVO_TELA, MOTIVO_BANDEJA, MOTIVO_TECLADO, MOTIVO_EXTERNO,
           MOTIVO_DESCONHECIDO)

# Marcador da execução em curso. Só número e horário — nenhum segredo, nenhum
# dado fiscal. É apagado no encerramento limpo; sobreviver a ele É a evidência
# de que a execução anterior morreu sem poder se despedir.
MARCADOR_EXECUCAO = os.path.join(DADOS, ".execucao-atual.json")
_ENCERRAMENTO_REGISTRADO = []


def _ciclo(evento, **campos):
    """Uma linha de ciclo de vida, com data e hora local.

    Formato fixo para poder ser lido por gente e por `grep`:
        [ciclo] 18/09/2026 22:15:03 INICIO porta=8777 pid=17656 ...
    """
    partes = " ".join("%s=%s" % (c, v) for c, v in campos.items()
                      if v not in (None, ""))
    try:
        print("[ciclo] %s %s%s"
              % (datetime.now().strftime("%d/%m/%Y %H:%M:%S"), evento,
                 (" " + partes) if partes else ""))
    except Exception:
        pass        # registro nunca derruba o servidor


def _marcar_execucao():
    """Grava o marcador da execução atual. Falhar aqui não impede nada."""
    try:
        with open(MARCADOR_EXECUCAO, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "porta": PORT,
                       "inicio": datetime.now().strftime("%d/%m/%Y %H:%M:%S")},
                      f, ensure_ascii=False)
    except Exception:
        pass


def _limpar_marcador():
    try:
        os.remove(MARCADOR_EXECUCAO)
    except OSError:
        pass


def _relatar_execucao_anterior():
    """A execução anterior terminou sem registrar? Então diga isso agora.

    Não afirma QUAL foi a causa — fim de sessão do Windows e terminação
    externa são indistinguíveis daqui, e o nome do motivo carrega as duas.
    """
    try:
        with open(MARCADOR_EXECUCAO, encoding="utf-8") as f:
            anterior = json.load(f)
    except Exception:
        return
    pid = anterior.get("pid")
    vivo = False
    if isinstance(pid, int) and os.name == "nt":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                vivo = True
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            vivo = False
    _ciclo("ENCERRAMENTO_ANTERIOR",
           pid=pid, porta=anterior.get("porta"),
           inicio=anterior.get("inicio"),
           motivo=(MOTIVO_DESCONHECIDO if vivo else MOTIVO_EXTERNO),
           nota=("o processo anterior ainda existe" if vivo
                 else "sem registro de encerramento na execução anterior"))


def registrar_encerramento(motivo, detalhe=""):
    """Registra o fim com motivo explícito. Idempotente: só a primeira vale."""
    if _ENCERRAMENTO_REGISTRADO:
        return
    _ENCERRAMENTO_REGISTRADO.append(motivo)
    if motivo not in MOTIVOS:
        motivo = MOTIVO_DESCONHECIDO
    _ciclo("ENCERRAMENTO", pid=os.getpid(), porta=PORT, motivo=motivo,
           detalhe=detalhe or None)
    _limpar_marcador()

# ─── Módulo NFS-e (backend próprio, FastAPI) ────────────────────────────────
# O backend real do Sistema NFS-e roda como processo-filho numa porta interna
# livre, e este servidor faz proxy das rotas dele (atrás do login).
# Os dados são os mesmos do SistemaNFSe standalone (C:\Users\<u>\SistemaNFSe).
def _porta_livre(inicio=8790, fim=8830):
    """Primeira porta em que ninguém está escutando AGORA.

    Cuidado: isto é um palpite, não uma reserva. Quem vai BINDAR é o processo
    filho, depois — e entre o palpite e o bind outro programa pode tomar a
    porta. Foi exatamente o que aconteceu quando duas instalações do Fiscale
    (a do projeto e a portátil) subiram na mesma máquina: as duas miraram a
    8790, uma bindou, a outra morreu em silêncio.

    Por isso `iniciar_modulo_nfse()` **confirma** que o filho ficou escutando,
    e tenta a próxima porta quando não ficou.
    """
    import socket
    for p in range(inicio, fim + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as t:
            if t.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return inicio


def _escutando(porta, espera=12.0):
    """O filho chegou a abrir a porta? Espera até `espera` segundos."""
    import socket
    fim = time.time() + espera
    while time.time() < fim:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", porta)) == 0:
                return True
        time.sleep(0.3)
    return False


NFSE_PORT = _porta_livre()
NFSE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nfse")
NFSE_PROC = None
NFSE_ATIVO = False
NFSE_JOB = None      # Job Object (Windows) que prende o módulo a este processo
# Rotas do módulo NFS-e repassadas ao processo-filho (prefixos).
NFSE_PREFIXOS = (
    "/api/certificados", "/api/sincronizar", "/api/competencias", "/api/notas",
    "/api/danfse", "/api/exportar", "/api/abrir-pasta", "/api/nfe", "/api/nfse",
    "/api/classificador", "/api/vencimentos", "/api/apuracao-federal", "/api/apuracao-trimestral", "/api/federal-empresa",
    "/api/iss-recolher",    # ISS a recolher: próprio + retido de terceiros
    "/api/recife", "/api/clientes", "/api/prefeituras",
    "/api/cte",             # CT-e (CTE 1) — leitura do acervo
    "/api/pastas",          # seletor de pastas da tela (substituiu o tkinter)
    "/api/fiscal",          # Central de Atualizações Fiscais
    "/logo", "/favicon.ico",
)


# Rotas que COMEÇAM com um prefixo repassado, mas são atendidas AQUI. Sem
# esta lista elas viram 404 silencioso: o proxy responde antes, e o módulo
# NFS-e não as conhece.
NFSE_EXCECOES = frozenset({
    "/api/clientes/modelo",
    "/api/clientes/importar/previa",
    "/api/clientes/importar/aplicar",
    # A ficha da empresa é atendida AQUI porque o cadastro é daqui: quem sabe
    # ler `state_clientes.json`, os certificados e o índice da situação fiscal
    # é este processo. Repassá-la ao módulo faria o módulo ler o cadastro — o
    # caminho oposto ao que esta etapa está declarando.
    "/api/clientes/ficha",
    # Decisões administrativas da ingestão NF-e. Começam com `/api/nfe`, que é
    # repassado ao módulo, mas são atendidas AQUI — é aqui que existem sessão,
    # papel, CSRF e trilha de acessos. O módulo NFS-e não autentica nada.
    "/api/nfe/admin/pendencias",
    "/api/nfe/admin/revisao/encerrar",
    "/api/nfe/admin/revisao/reabrir",
    "/api/nfe/admin/cobertura/marco",
    "/api/nfe/admin/bloqueio/manter",
})


def _python_do_modulo_nfse():
    """Python com as dependências do módulo (venv própria) ou o atual."""
    for cand in (os.path.join(NFSE_DIR, ".venv", "Scripts", "python.exe"),
                 os.path.join(NFSE_DIR, ".venv", "bin", "python")):
        if os.path.exists(cand):
            return cand
    return sys.executable


def _iniciar_nfse_thread():
    """No .exe, o backend do NFS-e roda NA MESMA APLICAÇÃO, numa thread —
    dispensando um segundo Python. O proxy continua falando com ele por HTTP."""
    global NFSE_ATIVO
    try:
        import threading as _th
        import uvicorn
        # No pacote, os módulos do NFS-e (main/core/seguranca/pdflocal) são
        # importáveis diretamente; sys.frozen faz o main.py usar a pasta de
        # dados persistente (C:\Users\<u>\SistemaNFSe), como no app standalone.
        import main as _nfse
        cfg = uvicorn.Config(_nfse.app, host="127.0.0.1", port=NFSE_PORT,
                             log_level="warning", log_config=None)
        server = uvicorn.Server(cfg)
        server.install_signal_handlers = lambda: None  # thread secundária
        _th.Thread(target=server.run, daemon=True).start()
        NFSE_ATIVO = True
    except Exception as e:
        print(f"  [NFS-e] módulo não pôde iniciar: {e}")


def _prender_ao_servidor(proc):
    """Põe o processo do módulo NFS-e num Job Object que MORRE COM ESTE PROCESSO.

    POR QUE
        `parar_modulo_nfse()` cobre o encerramento normal (Home > Encerrar,
        bandeja, `atexit`). Mas se este servidor cair — exceção fatal,
        Gerenciador de Tarefas, logoff —, nada disso roda e o módulo ficava
        órfão na porta interna, segurando a pasta de dados. Com o Job Object
        em `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, o Windows encerra o filho
        quando o último handle do job fecha — e o handle é deste processo.
        O neto (o Python base que o lançador da venv cria) morre junto: o
        lançador o mantém no job dele, que também fecha ao morrer.

    Nada de nome de processo: o alvo é o handle do filho que ESTE servidor
    criou. Falhar aqui não impede o módulo de subir; só volta ao
    comportamento antigo.
    """
    global NFSE_JOB
    if os.name != "nt" or proc is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _Basico(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _Estendido(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basico), ("IoInfo", _Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if NFSE_JOB is None:
            job = k32.CreateJobObjectW(None, None)
            if not job:
                return False
            info = _Estendido()
            info.BasicLimitInformation.LimitFlags = 0x2000   # KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info),
                                               ctypes.sizeof(info)):
                k32.CloseHandle(ctypes.c_void_p(job))
                return False
            NFSE_JOB = job
        return bool(k32.AssignProcessToJobObject(NFSE_JOB, int(proc._handle)))
    except Exception:
        return False


def iniciar_modulo_nfse():
    """Sobe o backend do NFS-e. No .exe (frozen) roda em thread interna; em
    desenvolvimento roda como processo-filho usando a venv do módulo. Se algo
    falhar, o resto do Fiscale segue e a rota do NFS-e devolve 503."""
    global NFSE_PROC, NFSE_ATIVO
    if FROZEN:
        return _iniciar_nfse_thread()
    global NFSE_PORT
    runner = os.path.join(NFSE_DIR, "runner.py")
    if not os.path.exists(runner):
        return
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # Até três portas. Não é paranoia: `Popen` devolver um processo NÃO quer
    # dizer que ele subiu — se a porta foi tomada no meio, o filho morre ao
    # bindar, e antes desta correção o Fiscale marcava o módulo como ATIVO
    # mesmo assim. O usuário só descobria ao clicar em NFS-e e receber "o
    # módulo não respondeu", sem nenhuma pista do motivo.
    # O FILHO PRECISA DE SAÍDA PRÓPRIA, E ISTO NÃO É DETALHE.
    # O Fiscale é aberto por `pythonw.exe`, que roda sem console: `sys.stdout` e
    # `sys.stderr` do pai não são handles válidos. Sem redirecionar, o filho os
    # HERDA — e morre na primeira linha que tentar escrever, sem deixar rastro.
    # Era esta a causa de "o módulo NFS-e não respondeu": não faltava porta,
    # faltava para onde escrever. Mandando para um arquivo, o filho vive e
    # ainda deixa o motivo registrado quando falhar por outro motivo.
    try:
        _log_nfse = open(os.path.join(DADOS, "nfse-modulo.log"), "ab",
                         buffering=0)
    except Exception:
        _log_nfse = subprocess.DEVNULL

    for tentativa in range(3):
        try:
            proc = subprocess.Popen(
                [_python_do_modulo_nfse(), runner, str(NFSE_PORT)],
                cwd=NFSE_DIR, creationflags=flags,
                stdin=subprocess.DEVNULL,
                stdout=_log_nfse, stderr=subprocess.STDOUT)
        except Exception as e:
            print(f"  [NFS-e] módulo não pôde iniciar: {e}")
            return
        preso = _prender_ao_servidor(proc)
        # Só o ESTADO do vínculo, nunca o handle: o número do handle não ajuda
        # ninguém a entender nada e é ruído num arquivo que se lê às pressas.
        _ciclo("NFSE_JOB", resultado=("VINCULADO" if preso else "NAO_VINCULADO"),
               pid_lancador=proc.pid)
        if _escutando(NFSE_PORT):
            NFSE_PROC = proc
            NFSE_ATIVO = True
            _ciclo("NFSE_ATIVO", porta=NFSE_PORT, pid_lancador=proc.pid,
                   resultado="OK")
            atexit.register(parar_modulo_nfse)
            return
        # Não subiu. Encerra este filho antes de tentar outra porta, senão
        # sobra processo órfão segurando recurso.
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        anterior = NFSE_PORT
        NFSE_PORT = _porta_livre(NFSE_PORT + 1)
        print(f"  [NFS-e] porta {anterior} não subiu "
              f"(outra instalação do Fiscale pode estar usando); "
              f"tentando {NFSE_PORT}")
        _ciclo("NFSE_PORTA_OCUPADA", porta=anterior, proxima=NFSE_PORT)
    print("  [NFS-e] módulo não subiu em nenhuma porta livre. "
          "Feche outras instâncias do Fiscale e abra de novo.")
    _ciclo("NFSE_INDISPONIVEL", resultado="NAO_SUBIU")


def parar_modulo_nfse():
    global NFSE_PROC, NFSE_JOB
    pid_lancador = getattr(NFSE_PROC, "pid", None)
    resultado = "JA_PARADO"
    if NFSE_PROC and NFSE_PROC.poll() is None:
        NFSE_PROC.terminate()
        resultado = "ENCERRADO"
        try:
            NFSE_PROC.wait(timeout=5)
        except Exception:
            NFSE_PROC.kill()
            resultado = "ENCERRADO_A_FORCA"
    if pid_lancador:
        _ciclo("NFSE_PARADO", pid_lancador=pid_lancador, porta=NFSE_PORT,
               resultado=resultado)
    NFSE_PROC = None
    # Fechar o job encerra qualquer processo que ainda esteja nele — é a
    # garantia de que nada do módulo sobrevive ao Encerrar.
    if NFSE_JOB is not None and os.name == "nt":
        fechado = False
        try:
            import ctypes
            fechado = bool(ctypes.windll.kernel32.CloseHandle(
                ctypes.c_void_p(NFSE_JOB)))
        except Exception:
            fechado = False
        NFSE_JOB = None
        _ciclo("NFSE_JOB", resultado=("FECHADO" if fechado else "FALHA_AO_FECHAR"))

USUARIOS_ARQ = os.path.join(DADOS, "usuarios.json")

# Sessões em SQLite na pasta de dados. Antes eram um dicionário em memória,
# e todo reinício deslogava o escritório inteiro. A interface continua a
# mesma (get/criar/revogar), então o resto deste arquivo quase não mudou.
import fiscale_sessoes
import fiscale_proxy
SESSOES = fiscale_sessoes.abrir(DADOS)

# Emissão do token de troca para o Elo. Import isolado: se algo faltar
# nesta instalação, só o botão "Abrir ELO" para de funcionar — o Fiscale
# segue normal.
try:
    import fiscale_elo
    TEM_ELO = True
except Exception:
    TEM_ELO = False

# Sincronização da projeção de clientes para o Elo. Mesmo isolamento: se
# faltar algo, o cadastro continua funcionando sem sincronizar.
try:
    import fiscale_elo_sync
    TEM_ELO_SYNC = True
except Exception:
    TEM_ELO_SYNC = False

def _hash_senha(senha, sal=None):
    sal = sal or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), 200_000).hex()
    return sal, h

import backup_drive
import fiscale_auditoria
import fiscale_cadastro
import situacao.armazenamento          # noqa: F401  (usado nas rotas)

# UMA CONSULTA OFICIAL POR VEZ, NO SERVIDOR INTEIRO.
#     O lote da tela já é sequencial, mas duas abas (ou duas pessoas) somariam
#     dois laços contra o mesmo serviço do governo. A trava é do processo: a
#     segunda consulta simultânea recebe 429 e a tela diz para esperar.
_CONSULTA_SITUACAO = threading.Lock()
import fiscale_pimenta
import fiscale_senhas
import fiscale_tentativas
import fiscale_usuarios

# A porta de tentativas é do processo inteiro, como as sessões: contador
# por requisição não conta nada.
TENTATIVAS = fiscale_tentativas.abrir(DADOS)

# O segredo local do login (Fase 2.1). Se ele tiver de ser trocado — backup
# restaurado noutra máquina, outra conta do Windows —, as linhas antigas de
# identificador desconhecido viram lixo ilegível e são descartadas na hora.
def _pimenta_trocada():
    """Registra o EVENTO, nunca o segredo e nunca quem tentou entrar.

    A linha diz que o segredo foi trocado e quantas linhas caíram. Não diz o
    segredo — obviamente — e também não diz identificador nenhum, porque as
    linhas descartadas não guardavam identificador: guardavam um HMAC que,
    sem o segredo velho, não corresponde mais a nada.
    """
    n = TENTATIVAS.esquecer_desconhecidos()
    print("[fiscale] segredo do login regenerado (DPAPI de outra conta ou "
          "arquivo ilegível); %d contador(es) de identificador desconhecido "
          "descartado(s)" % n, flush=True)


PIMENTA = fiscale_pimenta.abrir(DADOS, trocou=_pimenta_trocada)

# Teto de entrada, aplicado ANTES de qualquer coisa cara. Sem ele, um POST
# com alguns megabytes no campo da senha custa segundos de CPU por tentativa,
# e o próprio login vira a ferramenta de quem quer derrubar o servidor.
#
# 254 é o maior e-mail que a RFC 5321 admite. 4096 na senha é folga
# deliberada: a política nova para em 128, mas senha ANTIGA não foi criada
# sob a política, e um teto apertado aqui trancaria alguém para fora por uma
# regra que nasceu depois dela.
# A ÚNICA mensagem de credencial recusada. Fica numa constante porque a
# propriedade que importa é "existe uma só": senha errada, e-mail que não
# existe, conta desativada e entrada grande demais têm de sair pelo MESMO
# texto. Enquanto era um literal repetido, bastava alguém melhorar a redação
# de um deles para reabrir a fresta sem perceber.
ERRO_CREDENCIAL = "Usuário ou senha inválidos."

LIMITE_IDENTIFICADOR = 254
LIMITE_SENHA = 4096

# A espera fica numa variável para o teste poder trocá-la. Um teste que dorme
# 30 segundos de verdade é um teste que ninguém roda.
ESPERAR = time.sleep

# O livro de acessos (Fase 3). Banco próprio, separado do de sessões e do de
# tentativas: aqueles dois são descartáveis por natureza, este precisa durar.
AUDITORIA = fiscale_auditoria.abrir(DADOS)


class _Previas:
    """Os bilhetes das prévias de importação.

    POR QUE EXISTE
        `/aplicar` grava o cadastro do escritório. Sem amarra, ele aceitaria
        qualquer upload — e a prévia, que existe para alguém VER o que vai
        mudar, viraria um passo opcional que ninguém é obrigado a dar.

        O bilhete amarra três coisas: QUEM viu a prévia, QUAL arquivo foi
        conferido, e QUANDO. Trocar o arquivo entre a prévia e a aplicação
        invalida o bilhete — que é justamente o caso em que o que a pessoa viu
        não é o que seria gravado.

    NÃO GUARDA A PLANILHA
        Só o SHA-256 dela. O conteúdo morre no fim do pedido; guardar entre os
        dois seria manter uma segunda cópia do cadastro em memória, à espera
        de uma confirmação que pode nunca vir.
    """

    VALIDADE_S = 30 * 60

    def __init__(self):
        self._trava = threading.Lock()
        self._bilhetes = {}

    def _limpar(self, agora):
        vencidos = [t for t, (_, _, q) in self._bilhetes.items()
                    if agora - q > self.VALIDADE_S]
        for t in vencidos:
            self._bilhetes.pop(t, None)

    def emitir(self, uid, bruto: bytes) -> str:
        token = secrets.token_urlsafe(24)
        with self._trava:
            agora = time.time()
            self._limpar(agora)
            self._bilhetes[token] = (uid or "", hashlib.sha256(bruto).hexdigest(),
                                     agora)
        return token

    def conferir(self, token, uid, bruto: bytes) -> tuple:
        """`(ok, motivo)`. O motivo é escrito para quem está na tela."""
        if not token:
            return False, ("Confira a planilha antes de gravar: a prévia é "
                           "obrigatória.")
        with self._trava:
            dado = self._bilhetes.get(str(token))
        if not dado:
            return False, ("A prévia expirou ou não foi encontrada. Confira a "
                           "planilha de novo.")
        dono, impressao, quando = dado
        if time.time() - quando > self.VALIDADE_S:
            return False, "A prévia expirou. Confira a planilha de novo."
        if dono != (uid or ""):
            return False, "Esta prévia é de outra sessão. Confira de novo."
        if impressao != hashlib.sha256(bruto).hexdigest():
            return False, ("O arquivo mudou depois da prévia. Confira de novo "
                           "antes de gravar.")
        return True, ""

    def queimar(self, token):
        """Um bilhete serve UMA vez: aplicar duas vezes é importar duas vezes."""
        with self._trava:
            self._bilhetes.pop(str(token or ""), None)


PREVIAS = _Previas()


# ══════════════════════════════════════════════════════════════════════════
# CSRF — só para as rotas de DECISÃO administrativa
# ══════════════════════════════════════════════════════════════════════════
# POR QUE AQUI, E SÓ AQUI
#     O cookie de sessão é `SameSite=Lax`, o que já barra POST vindo de outro
#     site, e isso continua valendo para o sistema inteiro. Mas as rotas que
#     tiram uma empresa da revisão de sequência mudam, por decisão humana, o
#     estado que determina se um documento fiscal será buscado ou pulado —
#     para elas, "o navegador provavelmente não manda o cookie" não basta:
#     exige-se prova ATIVA de que quem postou leu a nossa própria tela.
#
# COMO É O TOKEN
#     HMAC-SHA256 de uma chave sorteada NESTE processo sobre o token da
#     sessão. Não é gravado em lugar nenhum, muda a cada reinício (a tela pede
#     um novo antes de agir) e não conta nada sobre a sessão a quem o vê. A
#     comparação é `compare_digest`, para não vazar o valor pelo tempo.
_CSRF_CHAVE = secrets.token_bytes(32)
CSRF_CABECALHO = "X-Fiscale-Csrf"


def csrf_para(token_sessao: str) -> str:
    if not token_sessao:
        return ""
    return hmac.new(_CSRF_CHAVE, token_sessao.encode("utf-8"),
                    hashlib.sha256).hexdigest()


# ══════════════════════════════════════════════════════════════════════════
# BACKUP — andamento visível e telemetria
# ══════════════════════════════════════════════════════════════════════════
# POR QUE ISTO EXISTE
#     O backup da instalação real lê 91 mil arquivos. Ele demora minutos, e
#     até aqui a tela mostrava uma barra indeterminada e o log não registrava
#     nada: nem início, nem fim, nem duração, nem motivo de falha. "O backup
#     demora horas" era uma impressão que ninguém conseguia confirmar ou
#     desmentir, porque não havia número nenhum guardado.
#
# O QUE NUNCA ENTRA AQUI
#     A frase-senha, o conteúdo de qualquer arquivo e o nome de certificado.
#     Este dicionário é servido por HTTP e escrito no log: o que ele guarda é
#     contagem, byte, fase, duração e, no caso de falha, o NOME do arquivo que
#     mudou durante o backup.
_BACKUP_TRAVA = threading.Lock()
BACKUP_ANDAMENTO = {"ativo": False, "fase": "", "feitos": 0, "total": 0,
                    "bytes": 0, "bytes_total": 0, "iniciado_em": "",
                    "duracao_s": 0.0, "resultado": "", "erro": "",
                    "arquivo": ""}


def _backup_anotar(**campos):
    """Atualiza o andamento. Nunca levanta: telemetria não derruba backup."""
    try:
        with _BACKUP_TRAVA:
            BACKUP_ANDAMENTO.update(campos)
    except Exception:
        pass


def _backup_instantaneo():
    with _BACKUP_TRAVA:
        return dict(BACKUP_ANDAMENTO)


def _backup_log(texto):
    """Uma linha no fiscale.log. `print` já vai para lá (ver o topo)."""
    print("[backup] %s %s" % (datetime.now().strftime("%d/%m/%Y %H:%M:%S"), texto))


def auditar(evento, resultado=fiscale_auditoria.OK, **campos):
    """Atalho para registrar. Nunca levanta — ver `fiscale_auditoria`."""
    return AUDITORIA.registrar(evento, resultado, **campos)


# A retenção é oportunista, como a faxina das sessões: roda ao subir e no
# máximo uma vez por dia. Um agendador só para isto seria mais uma coisa que
# pode não estar rodando — e ninguém descobre que um agendador parou.
AUDITORIA.manutencao()


def carregar_usuarios():
    """O cadastro, já com os campos da Fase 1 preenchidos em memória.

    A normalização acontece na LEITURA, não no disco. É isso que faz um
    `usuarios.json` ainda não migrado — ou restaurado de um backup antigo —
    funcionar sem diferença nenhuma.
    """
    return fiscale_usuarios.carregar(USUARIOS_ARQ)


def salvar_usuarios(u):
    """Escrita atômica com trava por arquivo.

    Antes isto era `open(..., "w")` direto: dois administradores salvando a
    tela de usuários ao mesmo tempo escreviam um por cima do outro, e um
    processo morto no meio deixava o cadastro truncado — que é o cadastro
    vazio, ou seja, ninguém entra mais.
    """
    fiscale_usuarios.salvar(USUARIOS_ARQ, u)

def sem_usuarios():
    """Primeiro uso: ninguém cadastrado ainda."""
    return not carregar_usuarios()


def criar_admin(senha):
    """Cria o admin no primeiro uso. Devolve (ok, erro)."""
    if not sem_usuarios():
        return False, "O sistema já tem usuários."
    # A política vale desde o primeiro acesso: o admin do escritório é
    # justamente a conta que não pode ter senha fraca.
    ok, motivo = fiscale_senhas.validar(senha, uid="admin", nome="administrador")
    if not ok:
        return False, motivo
    sal, h = _hash_senha(senha)
    salvar_usuarios({"admin": {"sal": sal, "hash": h, "admin": True}})
    return True, None

def resolver_usuario(identificador):
    """`(uid, registro)` a partir do login antigo OU do e-mail.

    O `uid` é a identidade INTERNA e imutável — é ele que vai para a sessão,
    para a permissão, para a auditoria e para o `sub` do token do Elo. O
    e-mail é só um caminho de entrada.
    """
    return fiscale_usuarios.resolver(carregar_usuarios(), identificador)


def verificar_login(usuario, senha):
    """A senha confere E a pessoa pode entrar? Devolve o `uid`, ou None.

    Devolve o UID em vez de `True` de propósito: quem chama precisa saber
    QUEM entrou, e não apenas que alguém entrou. Entrar por
    `maria@escritorio.com.br` tem de abrir sessão para `maria`.

    Usuário inativo não entra, e a resposta é a mesma de senha errada — dizer
    "esta conta está desativada" confirma que o endereço existe.

    TODO caminho gasta EXATAMENTE UM PBKDF2 (Fase 2.1). Antes, o usuário
    inexistente saía daqui na primeira linha e o real esperava as 200 mil
    iterações: a mensagem era igual, o relógio não era, e com um cronômetro
    dava para descobrir quais e-mails existem no escritório. Quando não há
    conta, o custo é pago contra um sal e um hash fictícios — mesmo
    algoritmo, mesmas iterações, mesma comparação em tempo constante.

    A comparação continua sendo `compare_digest` também no caso falso: sair
    por um `return False` mais curto ali reabriria, em miniatura, a mesma
    fresta.
    """
    uid, u = resolver_usuario(usuario)
    if not u or not u.get("sal") or not u.get("hash"):
        PIMENTA.gastar_o_mesmo_tempo(senha)
        return None
    _, h = _hash_senha(senha, u["sal"])
    if not secrets.compare_digest(h, u["hash"]):
        return None
    if not fiscale_usuarios.esta_ativo(u):
        return None
    return uid


import fiscale_papeis


def papel_de(usuario):
    """O papel deste usuário: `admin` ou `operador`.

    Uma fonte só para a pergunta. `eh_admin()` continua existindo porque meia
    dúzia de rotas já a chamam, mas agora ela deriva daqui — dois lugares
    decidindo o mesmo é como uma regra de acesso se contradiz.
    """
    if not usuario:
        return fiscale_papeis.OPERADOR
    return fiscale_papeis.papel_do_registro(carregar_usuarios().get(usuario), usuario)


def eh_admin(usuario):
    """O usuário tem papel de administrador? (o usuário 'admin' sempre é)"""
    if not usuario:
        return False
    return papel_de(usuario) == fiscale_papeis.ADMIN

# `/api/politica-senha` é livre porque a tela de PRIMEIRO ACESSO precisa dela
# antes de existir qualquer usuário. Ela devolve a regra — mínimo, máximo e a
# frase — e nada sobre pessoa alguma.
ROTAS_LIVRES = ("/login.html", "/api/login", "/icone.webp", "/manifest.webmanifest", "/sw.js",
                "/api/primeiro-acesso", "/api/politica-senha",
                # PWA (portal, Fase 3): o navegador busca manifest e ícones
                # SEM o cookie da sessão, e a tela "sem conexão" precisa abrir
                # justamente quando não há servidor para conferir sessão.
                # Nenhum deles fala de pessoa, empresa ou documento.
                "/offline.html", "/icones/fiscale-192.png", "/icones/fiscale-512.png",
                "/icones/fiscale-maskable-512.png", "/icones/fiscale-180.png")


def caminho_estado(mod):
    seguro = "".join(c for c in mod if c.isalnum() or c in "-_")
    return os.path.join(DADOS, f"state_{seguro}.json")


def ler_estado(mod) -> dict:
    """O estado de uma tela, ou `{}` quando ainda não existe.

    Existe porque a importação de cadastro precisa LER antes de conciliar, e
    repetir o `open`+`json.load` em cada rota é como surgem duas leituras que
    discordam sobre codificação — o arquivo de Clientes tem BOM.
    """
    caminho = caminho_estado(mod)
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def ler_certificados() -> list:
    """O cadastro de certificados (`certificados.json`), ou `[]`.

    O servidor nunca precisou dele: `/api/certificados` é repassado ao módulo
    NFS-e, que é o dono do arquivo. O relatório de Optantes precisa, porque a
    coluna "certificado no cofre" é sobre ESTE cadastro — e porque há empresa
    que existe só aqui, sem registro em Clientes (a TRX ficou meses assim).

    Leitura pura e tolerante: nada aqui escreve, e cadastro ilegível não pode
    derrubar um relatório — vira lista vazia e a coluna sai "-".
    """
    caminho = os.path.join(DADOS, "certificados.json")
    if not os.path.exists(caminho):
        return []
    try:
        with open(caminho, encoding="utf-8-sig") as f:
            itens = json.load(f)
        return itens if isinstance(itens, list) else []
    except (OSError, ValueError):
        return []


def versao_estado(mod):
    """Identifica a versão do estado no disco. Arquivo ausente é a versão "0".

    É o `mtime_ns`, não um contador: não exige guardar nada a mais e muda
    sozinho a cada gravação, inclusive numa feita por fora do Fiscale.
    """
    try:
        return str(os.stat(caminho_estado(mod)).st_mtime_ns)
    except OSError:
        return "0"


# A gravação atômica com trava mora em `fiscale_arquivo`, porque o cadastro
# de usuários passou a precisar da MESMA coisa (Fase 1 do login). Os dois
# nomes seguem existindo aqui: `teste_estado_concorrente.py` e o resto do
# arquivo chamam por eles, e mudar isso não faria diferença para ninguém.
from fiscale_arquivo import gravar_json_atomico, trava_de_escrita

_trava_de_escrita = trava_de_escrita


# ── O que um certificado ICP-Brasil realmente carrega ───────────────────────
# Medido nos certificados do escritório, não suposto. No SubjectAltName vão os
# campos brasileiros, cada um num otherName com OID próprio:
#
#   2.16.76.1.3.1  e-CPF : nascimento(8) + CPF(11) + NIS(11) + RG(15)
#   2.16.76.1.3.2  e-CNPJ: NOME do responsável pela empresa
#   2.16.76.1.3.3  e-CNPJ: CNPJ da empresa
#   2.16.76.1.3.4  e-CNPJ: nascimento(8) + CPF(11) + NIS(11) + RG(15) do RESPONSÁVEL
#   2.16.76.1.3.7  e-CNPJ: CEI da empresa
#
# ATENÇÃO: o `2.16.76.1.3.4` NÃO é a abertura da empresa — é o nascimento da
# PESSOA responsável. Na MONTE ele diz 01/01/1990 (o mesmo valor do e-CPF do
# Fulano) contra abertura real em 19/10/2000. A data de início de atividade
# vem da base pública (`cnpj_publico`), nunca daqui.
#
# Fora do SAN ainda dão para aproveitar: e-mail (rfc822Name), município (L) e
# UF (ST) do subject.
OID_RESPONSAVEL_NOME = "2.16.76.1.3.2"
OID_CNPJ = "2.16.76.1.3.3"
OID_DADOS_PF = ("2.16.76.1.3.1", "2.16.76.1.3.4")


def _texto_do_othername(valor: bytes) -> str:
    """Conteúdo de um otherName, sem o cabeçalho DER.

    O valor vem como TAG + TAMANHO + conteúdo. Ficar só com os bytes
    imprimíveis não basta: quando o nome tem 33 caracteres, o byte de tamanho
    é 0x21 — que é "!" — e o responsável da TRANSPORTADORA TRX virava
    "!ETIENE DA CONCEICAO...". Aqui o cabeçalho é descartado de propósito.
    """
    b = bytes(valor)
    # Tags vistas nos certificados do escritório: 0x04 OCTET STRING (o mais
    # comum aqui), 0x0C UTF8String, 0x13 PrintableString, 0x16 IA5String.
    # A confirmação é o byte de tamanho bater com o que sobra — assim um texto
    # que por acaso comece com um desses bytes não é decapitado.
    if len(b) >= 2 and b[0] in (0x04, 0x0C, 0x13, 0x16, 0x1E) and b[1] == len(b) - 2:
        b = b[2:]
    try:
        return b.decode("utf-8").strip()
    except UnicodeDecodeError:
        return b.decode("latin-1", "ignore").strip()


def ler_cnpj_certificado(conteudo, senha):
    """Lê tudo o que dá para aproveitar de um .pfx ICP-Brasil.

    Devolve (titular, cnpj, validade, extras). Os três primeiros continuam
    iguais para quem já chamava assim; `extras` é um dict com o resto:
    e-mail, município, UF, nome e CPF do responsável, emissão do certificado e
    o tipo (e-CNPJ / e-CPF). A senha é usada só aqui e nunca é guardada.

    NÃO devolve a data de nascimento do responsável: não serve para nada no
    cadastro fiscal e é o campo que já foi confundido com a abertura da empresa.
    """
    vazio = ("", "", "", {})
    if not TEM_CRYPTO:
        return vazio
    try:
        _, cert, _ = pkcs12.load_key_and_certificates(conteudo, (senha or "").encode())
    except Exception:
        return vazio
    try:
        titular, cnpj, municipio, uf = "", "", "", ""
        for attr in cert.subject:
            nome = getattr(attr.oid, "_name", "")
            if nome in ("commonName", "2.5.4.3"):
                val = attr.value
                titular = val
                if ":" in val:  # e-CNPJ traz "NOME:CNPJ" no CN
                    titular, cnpj = val.rsplit(":", 1)
            elif nome == "localityName":
                municipio = attr.value
            elif nome == "stateOrProvinceName":
                uf = attr.value

        email, responsavel, resp_cpf, cnpj_san = "", "", "", ""
        try:
            from cryptography import x509
            san = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value
            for gn in san:
                if isinstance(gn, x509.RFC822Name):
                    email = email or gn.value
                elif isinstance(gn, x509.OtherName):
                    oid = gn.type_id.dotted_string
                    txt = _texto_do_othername(gn.value)
                    if oid == OID_RESPONSAVEL_NOME:
                        responsavel = txt
                    elif oid == OID_CNPJ:
                        cnpj_san = re.sub(r"\D", "", txt)
                    elif oid in OID_DADOS_PF and len(re.sub(r"\D", "", txt)) >= 19:
                        # nascimento(8) + CPF(11): fica só com o CPF
                        resp_cpf = re.sub(r"\D", "", txt)[8:19]
        except Exception:
            pass

        exp = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
        ini = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
        doc = re.sub(r"\D", "", cnpj or cnpj_san)
        extras = {
            "email": email,
            "municipio": municipio,
            "uf": (uf or "").upper()[:2],
            "responsavel": responsavel,
            "responsavel_cpf": resp_cpf,
            "emissao": ini.strftime("%Y-%m-%d") if ini else "",
            "tipo": "e-CPF" if len(doc) == 11 else ("e-CNPJ" if len(doc) == 14 else ""),
            "emissor": next((a.value for a in cert.issuer
                             if getattr(a.oid, "_name", "") == "commonName"), ""),
        }
        return (titular, cnpj or cnpj_san,
                exp.strftime("%Y-%m-%d") if exp else "", extras)
    except Exception:
        return vazio


def parse_multipart(corpo, boundary):
    """Parser simples de multipart/form-data. Retorna dict campo -> (nome_arquivo, bytes)."""
    resultado = {}
    delim = b"--" + boundary
    for parte in corpo.split(delim):
        if not parte or parte in (b"--\r\n", b"--", b"\r\n"):
            continue
        if parte.startswith(b"\r\n"):
            parte = parte[2:]
        if b"\r\n\r\n" not in parte:
            continue
        cabecalho, dados = parte.split(b"\r\n\r\n", 1)
        if dados.endswith(b"\r\n"):
            dados = dados[:-2]
        cab = cabecalho.decode("utf-8", "ignore")
        nome_campo, nome_arquivo = None, None
        for linha in cab.split("\r\n"):
            if linha.lower().startswith("content-disposition"):
                for item in linha.split(";"):
                    item = item.strip()
                    if item.startswith("name="):
                        nome_campo = item.split("=", 1)[1].strip('"')
                    elif item.startswith("filename="):
                        nome_arquivo = item.split("=", 1)[1].strip('"')
        if nome_campo is not None:
            resultado[nome_campo] = (nome_arquivo, dados)
    return resultado


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        # HTML e JS sempre frescos: evita o navegador ficar preso numa versão
        # antiga depois de atualizar o sistema. Inclui o sw.js, para o navegador
        # trocar o service worker antigo pelo atual (que se autodesativa).
        if urlparse(self.path).path.endswith((".html", ".js", "/")):
            self.send_header("Cache-Control", "no-store, max-age=0")
        # HSTS só quando a página chegou MESMO por HTTPS (proxy). Mandá-lo em
        # HTTP não teria efeito — e na 8777 da rede do escritório não deve ter.
        if self._via_https():
            self.send_header("Strict-Transport-Security", fiscale_proxy.HSTS)
        super().end_headers()

    def _json(self, obj, code=200, set_cookie=None, etag=None):
        corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        if etag is not None:
            self.send_header("ETag", f'"{etag}"')
        self.end_headers()
        self.wfile.write(corpo)

    def _origem(self):
        """O IP de quem pediu. Do soquete, sempre.

        `X-Forwarded-For` é escrito por quem manda a requisição. Confiar nele
        deixaria o próprio visitante escolher o que a auditoria registra — e
        um registro que a pessoa registrada escolhe não é registro.
        """
        try:
            # Com uma exceção: o proxy HTTPS desta máquina. Só ele fala com o
            # FISCALE por 127.0.0.1 trazendo `X-Forwarded-For`, e quem está em
            # outra máquina não alcança esse soquete — ver fiscale_proxy.
            return fiscale_proxy.origem(self.client_address[0] or "", self.headers)
        except Exception:
            return ""

    def _agente(self):
        return fiscale_auditoria.limpar_agente(
            self.headers.get("User-Agent", ""))

    def _correlacao(self):
        """Um id por requisição, para amarrar os eventos de um mesmo pedido."""
        c = getattr(self, "_corr", "")
        if not c:
            c = fiscale_auditoria.correlacao()
            self._corr = c
        return c

    def _ctx(self):
        """Os campos de contexto que todo evento carrega."""
        return {"ip": self._origem(), "agente": self._agente(),
                "correlacao_id": self._correlacao()}

    def _planilha_enviada(self, corpo):
        """`(nome, bytes, extras)` da planilha que veio no pedido.

        DOIS FORMATOS, PORQUE HÁ DOIS CLIENTES
            `multipart/form-data` é como um navegador manda arquivo, e é o que
            um `<form>` ou um `FormData` produz. JSON com base64 é o que a tela
            usava antes e o que um script simples escreve em uma linha.

            Aceitar os dois custa esta função; recusar um deles custaria um
            "não funciona" sem explicação, do lado que ninguém testou.

        O TETO É APLICADO AQUI, ANTES DE QUALQUER LEITURA
            O corpo já foi lido pelo `do_POST`, mas decodificar base64 de um
            corpo enorme dobraria a memória antes de alguém dizer "grande
            demais".
        """
        tipo = (self.headers.get("Content-Type") or "").lower()
        limite = fiscale_cadastro.TAMANHO_MAX

        if tipo.startswith("multipart/form-data"):
            import email
            cabecalho = ("Content-Type: %s\r\nMIME-Version: 1.0\r\n\r\n"
                         % self.headers.get("Content-Type"))
            msg = email.message_from_bytes(cabecalho.encode("utf-8") + corpo)
            if not msg.is_multipart():
                # Um corpo só com a fronteira final não tem partes, e o
                # `email` o vê como não-multipart. Para quem está na tela,
                # o que aconteceu é um só: nenhum arquivo veio.
                raise ValueError(
                    "Nenhum arquivo veio no envio.")
            nome, dados, extras = "", None, {}
            for parte in msg.get_payload():
                campo = parte.get_param("name", header="content-disposition")
                arquivo = parte.get_filename()
                if arquivo:
                    nome = arquivo
                    dados = parte.get_payload(decode=True) or b""
                elif campo:
                    extras[campo] = (parte.get_payload(decode=True)
                                     or b"").decode("utf-8", "replace").strip()
            if dados is None:
                raise ValueError(
                    "Nenhum arquivo veio no envio. O campo do arquivo deve "
                    "ter um nome de arquivo (filename).")
            if len(dados) > limite:
                raise ValueError("O arquivo tem %.1f MB e o limite é %.0f MB."
                                 % (len(dados) / 1e6, limite / 1e6))
            return nome, dados, extras

        # JSON com base64
        try:
            d = json.loads(corpo or b"{}")
        except Exception:
            raise ValueError("Não consegui ler o pedido.")
        b64 = (d.get("b64") or "").split(",")[-1]
        if len(b64) > limite * 2:
            raise ValueError("O arquivo enviado é grande demais.")
        try:
            dados = base64.b64decode(b64)
        except Exception:
            raise ValueError("Não consegui ler o arquivo enviado.")
        if len(dados) > limite:
            raise ValueError("O arquivo tem %.1f MB e o limite é %.0f MB."
                             % (len(dados) / 1e6, limite / 1e6))
        extras = {k: v for k, v in d.items() if k not in ("b64", "nome")}
        return str(d.get("nome") or ""), dados, extras

    def _auditoria_ou_recusa(self):
        """True se JÁ respondeu 503 porque a auditoria não escreve.

        A regra é o oposto da do login, e de propósito. Login com auditoria
        quebrada continua entrando: trancar o escritório para fora por causa
        de um log seria trocar um problema de registro por um de operação.

        Alteração de usuário, papel ou senha, não: mudança sensível sem
        registro é exatamente o que este livro existe para impedir, e esperar
        custa minutos. Chamado ANTES de gravar — recusar depois não protege.
        """
        try:
            AUDITORIA.exigir_disponivel()
            return False
        except fiscale_auditoria.Indisponivel:
            self._json({"erro": "O registro de acessos não está gravando, e "
                                "alterações de usuário, papel ou senha não "
                                "são feitas sem registro. Veja Segurança → "
                                "Registro de acessos."}, 503)
            return True

    def _token_sessao(self):
        c = http_cookies.SimpleCookie(self.headers.get("Cookie", ""))
        tok = c.get("fiscale_sessao")
        return tok.value if tok else None

    def _decisao_nfe(self, rota, corpo):
        """As três decisões administrativas da ingestão NF-e.

        O CORPO É FECHADO. Só `id`, `justificativa` e, na cobertura,
        `origem_informacao`. Qualquer outro campo é recusado com 400 — e isso
        inclui `ult_nsu`, `max_nsu`, `checkpoint`, caminho de arquivo e
        estado: posição de fila é do domínio, não de formulário. Foi a
        tentação de "só corrigir o NSU pela tela" que este 400 existe para
        impedir.

        Sessão e papel já foram conferidos pelos portões do `do_POST`; aqui se
        exige, além deles, o CSRF — prova ativa de que o POST veio da nossa
        própria tela.
        """
        import fiscale_decisoes_nfe as _dec

        if not self._csrf_ok():
            return self._json({"erro": "CSRF ausente ou inválido. Recarregue "
                                        "a tela e tente de novo."}, 403)
        try:
            d = json.loads(corpo or b"{}")
        except Exception:
            d = {}
        if not isinstance(d, dict):
            return self._json({"erro": "corpo inválido"}, 400)

        permitidos = {"id", "justificativa"}
        if rota == "/api/nfe/admin/cobertura/marco":
            permitidos.add("origem_informacao")
        intrusos = sorted(set(d) - permitidos)
        if intrusos:
            return self._json(
                {"erro": "campo não aceito nesta rota: %s. Posição de fila "
                         "(NSU), checkpoint e caminho de arquivo são do "
                         "domínio e não vêm de formulário." % ", ".join(intrusos),
                 "campos_recusados": intrusos}, 400)

        acoes = {
            "/api/nfe/admin/revisao/encerrar":
                (_dec.encerrar_revisao, fiscale_auditoria.NFE_REVISAO_ENCERRADA),
            "/api/nfe/admin/revisao/reabrir":
                (_dec.reabrir_revisao, fiscale_auditoria.NFE_REVISAO_REABERTA),
            "/api/nfe/admin/cobertura/marco":
                (_dec.marcar_cobertura, fiscale_auditoria.NFE_COBERTURA_MARCADA),
            # A decisão de NÃO decidir: registra que a empresa foi analisada e
            # continua fora da automação. Não mexe em estado fiscal nenhum.
            "/api/nfe/admin/bloqueio/manter":
                (_dec.manter_bloqueio, fiscale_auditoria.NFE_BLOQUEIO_MANTIDO),
        }
        funcao, evento = acoes[rota]
        quem = self._usuario_sessao() or ""
        extra = {}
        if rota == "/api/nfe/admin/cobertura/marco":
            extra["origem_informacao"] = d.get("origem_informacao") or ""
        try:
            r = funcao(DADOS, str(d.get("id") or "").strip(), quem=quem,
                       justificativa=d.get("justificativa") or "", **extra)
        except _dec.DecisaoRecusada as e:
            # Recusa TAMBÉM é registrada: tentar decidir sobre empresa que não
            # está no estado certo é informação, não ruído.
            auditar(fiscale_auditoria.NFE_DECISAO_RECUSADA,
                    fiscale_auditoria.RECUSADO, ator_uid=quem,
                    derivado=e.motivo[:40], **self._ctx())
            return self._json({"erro": e.detalhe or e.motivo,
                               "motivo": e.motivo}, 409)
        except Exception as e:
            return self._json({"erro": "%s" % e.__class__.__name__}, 500)

        # A trilha de acessos guarda QUEM, QUANDO, SOBRE QUEM e POR QUÊ — com
        # a empresa MASCARADA e sem nada de fiscal no meio.
        auditar(evento, fiscale_auditoria.OK, ator_uid=quem,
                derivado=r["empresa"], detalhe=r["justificativa"][:200],
                **self._ctx())
        return self._json(r)

    def _csrf_ok(self):
        """O cabeçalho traz o token derivado DESTA sessão?

        Sem sessão não há token válido — e a rota já teria sido barrada pelo
        `_exigir_login`. A comparação é em tempo constante.
        """
        esperado = csrf_para(self._token_sessao())
        recebido = self.headers.get(CSRF_CABECALHO) or ""
        return bool(esperado) and hmac.compare_digest(esperado, recebido)

    def _usuario_sessao(self):
        """Quem está nesta sessão, ou None.

        Guarda o resultado por requisição: sem isso o mesmo pedido consulta o
        banco de sessões três ou quatro vezes (login, papel, rota), e o evento
        de sessão expirada seria registrado uma vez por consulta.
        """
        if hasattr(self, "_sessao_cache"):
            return self._sessao_cache
        usuario, motivo = SESSOES.get_com_motivo(self._token_sessao())
        if motivo == "EXPIRADA":
            # Sai UMA vez: a linha da sessão já foi apagada por quem respondeu.
            auditar(fiscale_auditoria.SESSAO_EXPIRADA,
                    fiscale_auditoria.OK, ator_uid=usuario or "", **self._ctx())
            usuario = None
        elif motivo != "OK":
            usuario = None
        self._sessao_cache = usuario
        return usuario

    def _cookie_sessao(self, token, validade_s=None):
        """Cookie da sessão.

        HttpOnly desde sempre (JavaScript não lê) e SameSite=Lax (o cookie
        não acompanha requisição disparada por outro site). O que NÃO entra
        é o `Secure`: o Fiscale serve HTTP na rede do escritório, e com
        Secure o navegador simplesmente descartaria o cookie — ninguém
        conseguiria entrar. Quando houver HTTPS, é uma linha.

        Max-Age acompanha a validade da sessão no servidor, para o cookie
        não ficar no navegador depois de a sessão já ter morrido — por isso
        recebe a MESMA validade que foi dada à sessão ("Manter conectado").
        """
        validade = int(validade_s or fiscale_sessoes.VALIDADE_PADRAO_S)
        # A "uma linha" de quando houvesse HTTPS: `Secure` só quando o pedido
        # veio por HTTPS. Na 8777 em HTTP ele continua de fora, senão ninguém
        # da rede do escritório conseguiria entrar.
        seguro = "; Secure" if self._via_https() else ""
        return (f"fiscale_sessao={token}; Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age={validade}{seguro}")

    def _exigir_login(self, rota):
        """True se a rota exige login e o usuário não está autenticado."""
        if rota in ROTAS_LIVRES:
            return False
        return self._usuario_sessao() is None

    def _barrar_por_papel(self, rota):
        """Responde 403 e devolve True se este usuário não alcança esta rota.

        Fica logo depois do login, nos três `do_*`, e ANTES do `_proxy_nfse`.
        Tem de ser antes: o módulo NFS-e escuta em 127.0.0.1 e não autentica
        nada — ele confia em quem repassa. Se o portão ficasse depois, o
        proxy seria o buraco.

        Quem escreve rota nova não precisa lembrar de nada aqui: rota não
        declarada em `fiscale_papeis` nasce restrita ao admin, e
        `teste_papeis.py` acusa a omissão pelo nome.
        """
        quem = self._usuario_sessao()
        if fiscale_papeis.pode(papel_de(quem), self.command, rota):
            return False
        # A rota NÃO entra no registro: ela é o caminho pedido pelo navegador
        # e pode carregar consulta com dado de cliente. O que interessa é
        # que houve tentativa, de quem, e de onde.
        auditar(fiscale_auditoria.ACESSO_ADMIN_NEGADO,
                fiscale_auditoria.RECUSADO, ator_uid=quem or "",
                detalhe=fiscale_auditoria.D_PAPEL, **self._ctx())
        if rota.startswith("/api/"):
            self._json({"erro": "Seu usuário não tem acesso a esta função. "
                                "Fale com o administrador do Fiscale."}, 403)
        else:
            # Página de administração pedida por quem não é admin: mandar para
            # a Home é melhor que um 403 seco — a tela abriria vazia e cheia
            # de erro de qualquer jeito.
            self.send_response(302)
            self.send_header("Location", "/home_portal.html")
            self.end_headers()
        return True

    def _eh_local(self):
        """Requisição veio da própria máquina? O servidor escuta em 0.0.0.0, então
        a criação do admin (que roda sem login) fica restrita a quem está no PC.

        Quem chega pelo proxy HTTPS também vem de 127.0.0.1 — e NÃO está no
        PC. Por isso loopback com cabeçalho de encaminhamento não é local
        (fiscale_proxy.eh_local): criar admin, restaurar backup e encerrar o
        servidor continuam só para quem está sentado na máquina."""
        return fiscale_proxy.eh_local(self.client_address[0] or "", self.headers)

    def _via_https(self):
        """O navegador está em HTTPS, atrás do proxy desta máquina?"""
        try:
            return fiscale_proxy.via_https(self.client_address[0] or "", self.headers)
        except Exception:
            return False

    def _rota_nfse(self, rota):
        """Esta rota é do módulo NFS-e, e deve ser repassada a ele?

        A LISTA DE EXCEÇÕES NÃO É DETALHE.
            `/api/clientes` está entre os prefixos repassados, então TUDO que
            começa com ele vai para o módulo NFS-e — inclusive rotas que nasceram
            aqui depois. Foi assim que `/api/clientes/modelo` e as duas rotas de
            importação de cadastro sumiram: o proxy respondia primeiro, o módulo
            não conhecia o caminho, e o navegador recebia 404 sem nenhuma pista
            de que a rota existia do lado de cá.

            Quem acrescentar rota sob um prefixo repassado precisa declará-la
            aqui. E `teste_papeis.py` falha se alguém esquecer — a lista deixou
            de depender de memória.
        """
        if rota in NFSE_EXCECOES:
            return False
        return rota == "/nfse.html" or rota.startswith(NFSE_PREFIXOS)

    def _proxy_nfse(self, corpo=None):
        """Repassa a requisição ao backend do módulo NFS-e (processo-filho)."""
        destino = "/" if urlparse(self.path).path == "/nfse.html" else self.path
        url = f"http://127.0.0.1:{NFSE_PORT}{destino}"
        req = urllib.request.Request(url, data=corpo, method=self.command)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)
        # O módulo NFS-e leva ~2 s para ficar de pé (uvicorn + FastAPI + os
        # módulos fiscais). Quem abre o navegador rápido chegava aqui antes e
        # levava um 503 dizendo para reiniciar o Fiscale — conselho errado para
        # uma espera de dois segundos. Agora a primeira requisição espera.
        dados = code = tipo = None
        limite = time.time() + 25
        while True:
            try:
                try:
                    with urllib.request.urlopen(req, timeout=600) as r:
                        dados, code, tipo = r.read(), r.status, r.headers.get("Content-Type")
                except urllib.error.HTTPError as e:
                    dados, code, tipo = e.read(), e.code, e.headers.get("Content-Type")
                break
            except Exception:
                if NFSE_ATIVO and time.time() < limite:
                    time.sleep(0.4)
                    continue
                return self._json({"detail": "O módulo NFS-e não respondeu. Feche o "
                                   "Fiscale e abra de novo; se continuar, rode o "
                                   "Diagnostico para ver o que falta."}, 503)
        self.send_response(code)
        self.send_header("Content-Type", tipo or "application/octet-stream")
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(dados)

    def _contabil(self, metodo, rota, corpo=b""):
        """As rotas do módulo Contábil (FASE CONTÁBIL 1).

        Chega aqui DEPOIS do login e do papel. O que o servidor acrescenta é
        o que só ele sabe fazer: o CSRF de toda escrita, o arquivo do
        multipart, e a lista de empresas lida do cadastro de Clientes — o
        universo do FISCALE, e não as pastas em disco. Todo o resto mora em
        `contabil/rotas.py`, que não toca regra fiscal nenhuma.
        """
        import contabil.rotas as _cont
        if metodo == "POST" and not self._csrf_ok():
            return self._json({"erro": "Pedido sem o token desta sessão. "
                                       "Recarregue a tela."}, 403)
        q = parse_qs(urlparse(self.path).query)
        dados_corpo, arquivo = {}, None
        if metodo == "POST":
            tipo = (self.headers.get("Content-Type") or "").lower()
            if tipo.startswith("multipart/form-data"):
                try:
                    nome_arq, bruto, extras = self._planilha_enviada(corpo)
                except ValueError as e:
                    return self._json({"erro": str(e)}, 400)
                arquivo = (nome_arq, bruto, extras)
                dados_corpo = dict(extras)
            else:
                try:
                    dados_corpo = json.loads(corpo or b"{}")
                except ValueError:
                    return self._json({"erro": "Não consegui ler o pedido."}, 400)
                if not isinstance(dados_corpo, dict):
                    return self._json({"erro": "Não consegui ler o pedido."}, 400)
        empresas = []
        for c in (ler_estado("clientes").get("clientes") or []):
            doc = fiscale_cadastro.documento_de(c)
            if doc:
                empresas.append({"identidade": doc, "nome": (
                    c.get("nome") or c.get("razao_social")
                    or c.get("nome_fantasia") or doc)})
        empresas.sort(key=lambda e: e["nome"].casefold())
        try:
            codigo, r = _cont.atender(metodo, rota, q, dados_corpo, arquivo,
                                      self._usuario_sessao() or "", DADOS,
                                      empresas)
        except Exception as e:
            sys.stderr.write("contabil falhou: %s: %s\n"
                             % (e.__class__.__name__, e))
            return self._json({"erro": "O Contábil não conseguiu atender "
                                       "(%s)." % e.__class__.__name__}, 500)
        if isinstance(r, _cont.Arquivo):
            nome = "".join(ch if ch.isalnum() or ch in "._-" else "_"
                           for ch in (r.nome or "extrato"))[:120]
            self.send_response(codigo)
            self.send_header("Content-Type", r.tipo)
            self.send_header("Content-Length", str(len(r.bruto)))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % nome)
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.end_headers()
            self.wfile.write(r.bruto)
            return
        return self._json(r, codigo)

    def do_GET(self):
        rota = urlparse(self.path).path
        if rota == "/api/politica-senha":
            # A tela lê a regra do servidor em vez de repetir o texto. Regra
            # escrita em dois lugares envelhece em um só.
            return self._json({
                "minimo": fiscale_senhas.MINIMO,
                "maximo": fiscale_senhas.MAXIMO,
                "recomendado": fiscale_senhas.RECOMENDADO,
                "mensagem": fiscale_senhas.MENSAGEM_UI,
            })

        if rota == "/api/primeiro-acesso":
            # a tela de login usa isto para decidir entre "entrar" e "criar senha"
            return self._json({"primeiro": sem_usuarios(), "local": self._eh_local()})
        if self._exigir_login(rota):
            if rota.startswith("/api/"):
                return self._json({"erro": "não autenticado"}, 401)
            self.send_response(302)
            self.send_header("Location", "/login.html")
            self.end_headers()
            return
        if self._barrar_por_papel(rota):
            return
        if rota == "/":
            # A porta de entrada é a Central de Aplicações (portal, Fase 2);
            # o FISCALE propriamente dito continua em /home_portal.html.
            self.send_response(302)
            self.send_header("Location", "/central.html")
            self.end_headers()
            return
        if self._rota_nfse(rota):
            return self._proxy_nfse()
        if rota == "/api/quem":
            u = self._usuario_sessao()
            # 'local' diz à Home se pode oferecer o botão Encerrar (só no próprio PC)
            # 'papel' é o que a navegação usa para esconder o que o operador
            # não alcança. Esconder não protege nada — quem protege é o
            # `_barrar_por_papel` — mas oferecer um botão que sempre dá 403 é
            # defeito de tela.
            resposta = {"usuario": u, "admin": eh_admin(u),
                        "papel": papel_de(u), "local": self._eh_local()}
            # `?completo=1` é da Central de Aplicações: o nome para a saudação
            # e o login ANTERIOR ao atual. Fica fora da resposta comum porque
            # a barra de navegação chama /api/quem em toda tela.
            if "completo=1" in (urlparse(self.path).query or ""):
                _uid, reg = resolver_usuario(u or "")
                resposta["nome"] = ((reg or {}).get("nome") or "").strip()
                resposta["ultimo_acesso"] = ""
                try:
                    logins = AUDITORIA.consultar(
                        ator=u or "", evento=fiscale_auditoria.LOGIN_OK,
                        por_pagina=2)["itens"]
                    # [0] é o login desta sessão; o "último acesso" é o de antes.
                    if len(logins) > 1:
                        resposta["ultimo_acesso"] = logins[1]["quando_utc"]
                except Exception:
                    pass
            return self._json(resposta)

        if rota == "/api/elo/sync/estado":
            # Diagnóstico: quantos pendentes, qual foi o último erro.
            # Sem painel: uma rota que responde JSON basta nesta fase.
            if not TEM_ELO_SYNC:
                return self._json({"ok": False, "erro": "módulo indisponível"}, 503)
            cfg = fiscale_elo.configuracao(DADOS) if TEM_ELO else {}
            return self._json({
                "ok": True,
                "configurado": bool(cfg.get("url") and cfg.get("tenant_id")),
                "clientes": len(fiscale_elo_sync.ler_clientes(DADOS)),
                "fila": fiscale_elo_sync.fila(DADOS).estado(),
            })

        if rota == "/api/escritorio":
            # "Como as outras pessoas do escritório entram aqui?" — a pergunta
            # que todo escritório faz na primeira semana e que nenhuma tela
            # respondia. Só nome e porta: é barato, e a Home carrega isto a
            # cada abertura. As conferências de rede (perfil, firewall) são
            # caras e ficam no Diagnóstico, que roda sob demanda.
            import diagnostico_instalacao as _diag
            try:
                return self._json(_diag.endereco_do_escritorio())
            except Exception as e:
                return self._json({"erro": f"{e.__class__.__name__}: {e}"}, 500)

        if rota == "/api/optante":
            cnpj = (parse_qs(urlparse(self.path).query).get("cnpj") or [""])[0]
            return self._json(cnpj_publico.consultar(cnpj))

        if rota == "/api/optantes/universo":
            # A MESMA LISTA QUE O RELATÓRIO USA.
            #     A tela montava a carteira sozinha, juntando Clientes e
            #     certificados no JavaScript. Manter as duas montagens faria a
            #     coluna "certificado" da tela poder discordar da coluna do
            #     PDF — e ninguém confere relatório contra tela até o dia em
            #     que um cliente aponta a diferença.
            import optantes as _opt

            try:
                return self._json({"empresas": _opt.universo(
                    (ler_estado("clientes").get("clientes") or []),
                    ler_certificados(), fdados.resolver_cert)})
            except Exception as e:
                sys.stderr.write("optantes/universo: %s\n" % e.__class__.__name__)
                return self._json(
                    {"erro": "Não consegui montar a lista (%s)."
                             % e.__class__.__name__}, 500)

        if rota in ("/api/optantes/relatorio", "/api/optantes/exportar"):
            # A VARREDURA DA CARTEIRA, EM PDF OU CSV.
            #     Não é documento de órgão: é tabulação nossa sobre base
            #     pública. Por isso NÃO passa pelo envelope da Situação Fiscal
            #     e nem aparece no painel dela — mas obedece à mesma regra
            #     white-label, que mora em `relatorio_base`.
            import optantes as _opt

            try:
                clientes = (ler_estado("clientes").get("clientes") or [])
                universo = _opt.universo(clientes, ler_certificados(),
                                         fdados.resolver_cert)
                # A REFERÊNCIA É CONSULTADA UMA VEZ POR RELATÓRIO.
                #     Uma por empresa seriam 24 chamadas para obter o mesmo
                #     valor, e a data podia até divergir no meio da varredura.
                referencia = cnpj_publico.referencia_da_base()
                resultado = _opt.varrer(universo)
            except Exception as e:
                # O nome da exceção, nunca a mensagem: ela carrega caminho de
                # disco e às vezes trecho de resposta de terceiro.
                sys.stderr.write("optantes falhou: %s\n" % e.__class__.__name__)
                return self._json(
                    {"erro": "Não consegui montar o relatório (%s)."
                             % e.__class__.__name__}, 500)

            if rota == "/api/optantes/exportar":
                corpo_exp = _opt.csv_bytes(resultado, referencia)
                tipo, nome_arq = "text/csv; charset=utf-8", _opt.nome_csv()
            else:
                corpo_exp = _opt.pdf_bytes(resultado, referencia)
                tipo, nome_arq = "application/pdf", _opt.nome_pdf()

            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo_exp)))
            # O QUE A TELA PRECISA DECIDIR, NO CABEÇALHO.
            #     Ela avisa quando a base não informou a referência e quando
            #     alguma linha veio da retaguarda — e não consegue descobrir
            #     isso de dentro de um PDF já montado.
            self.send_header("X-Referencia", referencia.get("referencia") or "")
            self.send_header("X-Empresas", str(resultado["total"]))
            self.send_header("X-Consultadas", str(resultado["consultadas"]))
            self.send_header("X-Retaguarda", str(resultado["retaguarda"]))
            self.send_header("X-Falhas", str(resultado["falhas"]))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % nome_arq)
            self.end_headers()
            self.wfile.write(corpo_exp)
            return

        if rota == "/api/ncm":
            termo = (parse_qs(urlparse(self.path).query).get("q") or [""])[0]
            return self._json(ncm_publico.consultar(termo))

        if rota == "/api/usuarios":
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            # `para_tela` monta a lista SEM `sal` e sem `hash`: o navegador
            # nunca precisa do material da senha, e o que não é enviado não
            # vaza por tela aberta nem por log de proxy.
            return self._json(fiscale_usuarios.para_tela(carregar_usuarios()))

        # ── Backup e Migração ────────────────────────────────────────────
        # Fica AQUI, no servidor de fora, e não no módulo NFS-e: backup tem de
        # funcionar mesmo quando o módulo não sobe — é justamente aí que ele
        # mais importa. E não depende do ELO de forma alguma.
        if rota == "/api/csrf":
            # Token para as rotas de decisão. Só para quem já tem sessão — o
            # `_exigir_login` acima já garantiu isso.
            return self._json({"csrf": csrf_para(self._token_sessao()),
                               "cabecalho": CSRF_CABECALHO})

        if rota == "/api/nfe/admin/pendencias":
            # Só leitura: quem está em revisão de sequência ou em divergência
            # externa, com o diagnóstico rotulado como HIPÓTESE.
            import fiscale_decisoes_nfe as _dec
            try:
                return self._json(_dec.listar_pendentes(DADOS))
            except Exception as e:
                return self._json({"erro": "%s" % e.__class__.__name__}, 500)

        if rota == "/api/backup/progresso":
            # Só leitura, e só número: a tela pergunta de tempos em tempos
            # enquanto o POST de criar ainda está aberto. Sem isto, o usuário
            # olha para uma barra indeterminada por minutos e conclui que
            # travou — foi exatamente o que aconteceu.
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            return self._json(_backup_instantaneo())

        if rota == "/api/backup/situacao":
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            import fiscale_backup as _bk
            certs = os.path.join(DADOS, "certs")
            pfx = [f for f in os.listdir(certs)] if os.path.isdir(certs) else []
            pfx = [f for f in pfx if f.lower().endswith((".pfx", ".p12"))]
            est = _bk.estado_do_destino(DADOS)
            return self._json({
                "raiz": DADOS,
                "arquivos": est["arquivos"],
                "empresas": est["empresas"],
                "certificados": len(pfx),
                # SEMPRE. No formato 2 o pacote inteiro é cifrado, e não só
                # o cofre dos certificados: não existe mais backup sem frase.
                "precisa_frase": True,
                "cifrado_por_inteiro": True,
                "nome_sugerido": _bk.nome_sugerido(),
                # O DESTINO PADRÃO é a pasta canônica, `<dados>/backups`.
                #
                # Antes era a Área de Trabalho, e o efeito apareceu na prática:
                # o backup de 28/08 saiu com 157,7 MB para a Área de Trabalho,
                # e o envio ao Drive — que só olha a pasta canônica — não o
                # enxergava. Backup guardado em lugar que o resto do sistema
                # não procura é backup que ninguém acha no dia em que precisa.
                #
                # Continua sendo só o PADRÃO: o campo é editável e qualquer
                # outra pasta é aceita.
                "pasta_padrao": str(backup_drive.canonica()),
                # Mantida para a tela poder oferecer a Área de Trabalho como
                # alternativa, e porque `~/Desktop` mente quando o OneDrive
                # redireciona a pasta.
                "area_de_trabalho": str(pastas_windows.area_de_trabalho()),
            })

        # ── Contábil (FASE CONTÁBIL 1) ────────────────────────────────────
        if rota.startswith("/api/contabil/"):
            return self._contabil("GET", rota)

        # ── Situação fiscal: certidões e extratos ─────────────────────────
        if rota.startswith("/api/situacao/"):
            import situacao.indice as _sit_indice
            import situacao.modelo as _sit_modelo

            # UM ÍNDICE POR PEDIDO, e não um global.
            #     O servidor é multi-thread e o `sqlite3` recusa uso da mesma
            #     conexão em thread diferente. Abrir por pedido custa
            #     microssegundos num arquivo local e elimina a classe inteira
            #     de erro — que apareceria como "objeto criado em outra
            #     thread", intermitente, e só sob uso simultâneo.
            q = parse_qs(urlparse(self.path).query)
            with _sit_indice.abrir(DADOS) as ix:
                if rota == "/api/situacao/panorama":
                    clientes = (ler_estado("clientes").get("clientes") or [])
                    empresas = []
                    for c in clientes:
                        doc = fiscale_cadastro.documento_de(c)
                        if not doc:
                            continue
                        empresas.append({
                            "identidade": doc,
                            # O CADASTRO VIVO GUARDA O NOME EM `nome`.
                            #     `razao_social` e o nome da coluna na
                            #     PLANILHA de importacao; o registro em disco
                            #     usa `nome`. Ler so o primeiro fazia a tela
                            #     cair no CNPJ para as 19 empresas reais --
                            #     defeito que nenhum dado ficticio revelaria,
                            #     porque o ficticio foi escrito ja no formato
                            #     da planilha.
                            "nome": (c.get("nome") or c.get("razao_social")
                                     or c.get("nome_fantasia") or doc),
                            "tipo_documento": fiscale_cadastro.tipo_documento(doc),
                        })
                    empresas.sort(key=lambda e: e["nome"].casefold())
                    linhas = ix.panorama([e["identidade"] for e in empresas])
                    por_empresa = {}
                    for l in linhas:
                        por_empresa.setdefault(l["identidade"], []).append(l)
                    return self._json({
                        "empresas": empresas,
                        "esferas": list(_sit_modelo.ESFERAS),
                        "tipos": list(_sit_modelo.TIPOS),
                        "situacao": por_empresa,
                        "a_vencer": ix.a_vencer(),
                        "hoje": datetime.now().strftime("%Y-%m-%d"),
                    })

                if rota in ("/api/situacao/carteira", "/api/situacao/empresa"):
                    import situacao.carteira as _sit_cart

                    # O UNIVERSO É O CADASTRO, igual ao panorama.
                    clientes = (ler_estado("clientes").get("clientes") or [])
                    empresas = []
                    for c in clientes:
                        doc = fiscale_cadastro.documento_de(c)
                        if not doc:
                            continue
                        empresas.append({
                            "identidade": doc,
                            "nome": (c.get("nome") or c.get("razao_social")
                                     or c.get("nome_fantasia") or doc),
                        })
                    empresas.sort(key=lambda e: e["nome"].casefold())

                    # O CANAL FEDERAL SÓ OLHA PRESENÇA DE CHAVE, sem decifrar.
                    try:
                        import situacao.ciclo as _sit_ciclo
                        canal = _sit_cart.canal_federal(
                            _sit_ciclo.cofre_do_projeto(DADOS))
                    except Exception:
                        canal = _sit_cart.canal_federal(None)

                    if rota == "/api/situacao/carteira":
                        r = _sit_cart.montar(ix, empresas, canal)
                        r["hoje"] = datetime.now().strftime("%Y-%m-%d")
                        return self._json(r)

                    ident = fiscale_cadastro.normalizar_documento(
                        (q.get("identidade") or [""])[0])
                    emp = next((e for e in empresas
                                if e["identidade"] == ident), None)
                    if not emp:
                        return self._json(
                            {"erro": "empresa não está no cadastro"}, 404)
                    linha = _sit_cart.avaliar_empresa(ix, emp, canal)
                    linha["historico"] = _sit_cart.historico(ix, ident)
                    # A FEDERAL é a que decide o consolidado (12/09/2026).
                    linha["documentos"] = [_sit_cart._doc_resumo(d)
                                           for d in ix.documentos(ident, "FEDERAL")]
                    # O MUNICIPAL vem AO LADO, nunca dentro do consolidado: é
                    # canal assistido, sem consulta automática, e a resposta
                    # dele diz isso. Sem esta linha, um PDF do Recife guardado
                    # ficava invisível na tela — existia no índice e em lugar
                    # nenhum mais.
                    linha["municipal"] = _sit_cart.municipal(ix, ident)
                    linha["rotulos"] = _sit_cart.reg.ROTULO
                    return self._json(linha)

                if rota == "/api/situacao/tentativas":
                    ident = (q.get("identidade") or [""])[0]
                    return self._json(
                        {"tentativas": ix.tentativas(ident, limite=100)})

                if rota == "/api/situacao/documento":
                    ident = (q.get("identidade") or [""])[0]
                    esfera = (q.get("esfera") or [""])[0]
                    tipo = (q.get("tipo") or [""])[0]
                    ident_doc = (q.get("id") or [""])[0]
                    try:
                        armazem = situacao.armazenamento.abrir(DADOS, ident)
                        bruto = armazem.ler(esfera, tipo, ident_doc)
                    except Exception:
                        return self._json({"erro": "documento não encontrado"}, 404)
                    # CONFERE ANTES DE ENTREGAR. O documento vale numa
                    # fiscalização; entregar um arquivo adulterado no disco
                    # sem avisar seria pior que recusar.
                    if not armazem.conferir_integridade(esfera, tipo, ident_doc):
                        return self._json(
                            {"erro": "o arquivo no disco não confere com o "
                                     "hash registrado"}, 409)
                    # PÁGINAS NO CABEÇALHO: o visualizador da tela precisa
                    # saber até onde vai o "próxima página", e o navegador não
                    # conta páginas de PDF para o JavaScript.
                    try:
                        import io as _io
                        import pypdf as _pypdf
                        paginas = len(_pypdf.PdfReader(_io.BytesIO(bruto)).pages)
                    except Exception:
                        paginas = 0
                    try:
                        sha = armazem.captura(esfera, tipo, ident_doc).get(
                            "sha256", "")
                    except Exception:
                        sha = ""
                    baixar = (q.get("baixar") or [""])[0] == "1"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Length", str(len(bruto)))
                    self.send_header(
                        "Content-Disposition",
                        '%s; filename="%s-%s-%s-%s.pdf"'
                        % ("attachment" if baixar else "inline", ident,
                           esfera.lower(), tipo.lower(), ident_doc[:8]))
                    self.send_header("X-Paginas", str(paginas))
                    self.send_header("X-Sha256", sha)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(bruto)
                    return

                if rota in ("/api/situacao/dossie", "/api/situacao/lote"):
                    import situacao.relatorio as _sit_rel

                    clientes = (ler_estado("clientes").get("clientes") or [])

                    def _nome_de(doc):
                        for c in clientes:
                            if fiscale_cadastro.documento_de(c) == doc:
                                return (c.get("nome") or c.get("razao_social")
                                        or c.get("nome_fantasia") or doc)
                        return doc

                    try:
                        if rota == "/api/situacao/dossie":
                            ident = fiscale_cadastro.normalizar_documento(
                                (q.get("identidade") or [""])[0])
                            if not ident:
                                return self._json(
                                    {"erro": "informe a empresa"}, 400)
                            base_nome = _nome_de(ident)
                            r = _sit_rel.dossie(DADOS, ix, ident, base_nome)
                            quantas = 1
                        else:
                            empresas = []
                            for c in clientes:
                                doc = fiscale_cadastro.documento_de(c)
                                if doc:
                                    empresas.append({"identidade": doc,
                                                     "nome": _nome_de(doc)})
                            empresas.sort(key=lambda e: e["nome"].casefold())
                            r = _sit_rel.lote(DADOS, ix, empresas)
                            base_nome = "escritorio"
                            quantas = len(empresas)
                    except Exception as e:
                        # O NOME DA EXCEÇÃO, E NÃO A MENSAGEM DELA.
                        #     A mensagem de um erro de disco carrega caminho
                        #     completo, e isto vai para a tela do navegador.
                        sys.stderr.write("situacao/relatorio falhou: "
                                         "%s\n" % e.__class__.__name__)
                        return self._json(
                            {"erro": "Não consegui montar o relatório (%s)."
                                     % e.__class__.__name__}, 500)

                    corpo_pdf = r["pdf"]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Length", str(len(corpo_pdf)))
                    # QUANTOS ANEXOS ENTRARAM, NO CABEÇALHO.
                    #     A tela precisa saber para avisar "saiu sem anexo" --
                    #     e ela não consegue contar páginas de um PDF. Sem
                    #     isto, um dossiê de capa sozinha baixaria calado e a
                    #     pessoa só descobriria ao abrir.
                    self.send_header("X-Anexados", str(r.get("anexados", 0)))
                    self.send_header("X-Omitidos",
                                     str(len(r.get("omitidos") or [])))
                    self.send_header("X-Empresas", str(quantas))
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="%s"'
                        % _sit_rel.nome_de_arquivo(base_nome))
                    self.end_headers()
                    self.wfile.write(corpo_pdf)
                    return

            return self._json({"erro": "rota desconhecida"}, 404)

        if rota == "/api/clientes/modelo":
            # O modelo da planilha. Existe para que ninguém tenha de adivinhar
            # os nomes das colunas — e para que a primeira importação de
            # alguém não seja uma sequência de erros de cabeçalho.
            corpo_csv = fiscale_cadastro.modelo_csv()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo_csv)))
            self.send_header("Content-Disposition",
                             'attachment; filename="modelo-empresas-fiscale.csv"')
            self.end_headers()
            self.wfile.write(corpo_csv)
            return

        if rota == "/api/auditoria":
            # O portão de papéis já barrou quem não é admin; a conferência
            # aqui é a segunda tranca, porque esta rota devolve IP e hábito
            # de entrada de pessoas.
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            q = parse_qs(urlparse(self.path).query)
            def _um(nome, padrao=""):
                return (q.get(nome) or [padrao])[0]
            try:
                pagina = int(_um("pagina", "1") or 1)
            except ValueError:
                pagina = 1
            try:
                por = int(_um("por_pagina", "") or fiscale_auditoria.PAGINA_PADRAO)
            except ValueError:
                por = fiscale_auditoria.PAGINA_PADRAO
            try:
                r = AUDITORIA.consultar(
                    de=_um("de"), ate=_um("ate"), ator=_um("ator"),
                    evento=_um("evento"), resultado=_um("resultado"),
                    pagina=pagina, por_pagina=por)
            except Exception as e:
                return self._json(
                    {"erro": "Não consegui ler o registro de acessos (%s)."
                             % e.__class__.__name__,
                     "itens": [], "total": 0, "pagina": 1, "paginas": 1}, 200)
            r["eventos"] = [{"chave": k, "rotulo": fiscale_auditoria.ROTULO_EVENTO[k]}
                            for k in fiscale_auditoria.EVENTOS]
            r["resultados"] = [{"chave": k, "rotulo": fiscale_auditoria.ROTULO_RESULTADO[k]}
                               for k in fiscale_auditoria.RESULTADOS]
            r["atores"] = AUDITORIA.atores()
            r["saude"] = AUDITORIA.saude()
            return self._json(r)

        if rota == "/api/clientes/ficha":
            # A FICHA: o que o sistema sabe da empresa, e de onde. Só leitura.
            # A consolidação é de `fiscale_cadastro.ficha`, função pura — este
            # bloco só junta o que cada fonte já sabe e entrega a ela.
            ident = fiscale_cadastro.normalizar_documento(
                (q.get("identidade") or [""])[0])
            clientes = (ler_estado("clientes").get("clientes") or [])
            reg = next((c for c in clientes
                        if fiscale_cadastro.documento_de(c) == ident), None)
            if not ident or reg is None:
                return self._json({"erro": "empresa não está no cadastro"}, 404)

            # Certificado: a ORIGEM é `certificados.json`. O caminho do .pfx
            # NÃO sai daqui — a ficha diz que existe e até quando vale.
            cert = {}
            try:
                with open(os.path.join(DADOS, "certificados.json"),
                          encoding="utf-8-sig") as _f:
                    bruto = json.load(_f)
                for c in (bruto or []):
                    if fiscale_cadastro.so_digitos(c.get("cnpj")) == ident:
                        cert = {"arquivo": bool(c.get("caminho")),
                                "validade": str(c.get("validade") or "")[:10]}
                        break
            except Exception:
                cert = {}

            # Regularidade: do pacote `situacao/`, e só o status.
            regularidade = {}
            try:
                import situacao.carteira as _cart
                import situacao.indice as _sind
                with _sind.abrir(DADOS) as _ix:
                    linha = _cart.avaliar_empresa(
                        _ix, {"identidade": ident,
                              "nome": reg.get("nome") or ""},
                        _cart.canal_federal(None))
                regularidade = {"status": (linha.get("geral") or {}).get("status"),
                                "rotulo": (linha.get("geral") or {}).get("rotulo")}
            except Exception:
                regularidade = {}

            # Acervo: contagem do índice, sem abrir documento.
            acervo = {}
            try:
                from ingestao import consulta as _cq
                acervo = {"documentos": _cq.contar(DADOS, ident, _cq.Filtro())}
            except Exception:
                acervo = {}

            apuracao = None
            try:
                with open(os.path.join(DADOS, "state_classificador.json"),
                          encoding="utf-8-sig") as _f:
                    _est = json.load(_f) or {}
                for chave, v in (_est.get("cfgs") or {}).items():
                    if (fiscale_cadastro.so_digitos(chave) == ident
                            or fiscale_cadastro.so_digitos(
                                (v or {}).get("cnpj")) == ident):
                        apuracao = v or {}
                        break
            except Exception:
                apuracao = None

            # IE/IM: a ORIGEM é o XML, e quem o lê é o módulo NFS-e
            # (`/api/clientes/inscricoes`). NÃO passo aqui o valor do próprio
            # cadastro como se fosse a origem: comparar um campo consigo mesmo
            # nunca acusaria divergência e daria a impressão de conferência.
            # A tela pede as inscrições ao módulo e mostra as duas colunas.
            return self._json(fiscale_cadastro.ficha(
                reg, certificado=cert, inscricoes=None,
                regularidade=regularidade, acervo=acervo, apuracao=apuracao))

        if rota == "/api/centro":
            # A FILA DE TRABALHO do Centro de Operações. Só leitura, e de fontes
            # que já existem: cadastro, índice da ingestão, índice da situação
            # fiscal e estado da apuração. Nenhum módulo fiscal foi alterado
            # para alimentar esta rota.
            #
            # É do OPERADOR, não do administrador: a fila é o trabalho do dia.
            # O que exige administrador (backup, diagnóstico) continua nas rotas
            # próprias, e a Home já sabe mostrar "visível ao administrador".
            import fiscale_centro
            clientes = (ler_estado("clientes").get("clientes") or [])
            # A sonda do módulo NFS-e continua sendo da tela: ela é uma chamada
            # HTTP, e fazê-la aqui atrasaria a fila por causa de um processo
            # que a própria tela já verifica.
            try:
                return self._json(fiscale_centro.fila(DADOS, clientes))
            except Exception as e:
                sys.stderr.write("centro/fila falhou: %s\n" % e.__class__.__name__)
                return self._json(
                    {"erro": "Não consegui montar a fila (%s)."
                             % e.__class__.__name__,
                     "blocos": {}, "ordem": [], "total_pendencias": None}, 200)

        if rota == "/api/auditoria/saude":
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            return self._json(AUDITORIA.saude())

        if rota == "/api/diagnostico":
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            try:
                import diagnostico_instalacao as _diag
                return self._json(_diag.executar(DADOS))
            except Exception as e:
                # Diagnóstico que estoura é pior do que não ter diagnóstico:
                # assusta e não informa. Devolve a falha já em português.
                return self._json({"erro": "Não consegui rodar o diagnóstico "
                                            f"({e.__class__.__name__}).",
                                   "geral": "ATENÇÃO", "itens": [],
                                   "contagem": {"OK": 0, "ATENÇÃO": 0, "ERRO": 0}}, 200)

        if rota == "/api/backup/baixar":
            # Só o admin, e só de quem está NO computador: o .fbk carrega os
            # dados do escritório inteiro; não sai por navegador da rede.
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            if not self._eh_local():
                return self._json({"erro": "O backup só pode ser baixado no "
                                            "computador onde o Fiscale roda."}, 403)
            q = parse_qs(urlparse(self.path).query)
            caminho = (q.get("arquivo") or [""])[0]
            if not caminho or not os.path.isfile(caminho):
                return self._json({"erro": "arquivo não encontrado"}, 404)
            tam = os.path.getsize(caminho)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(tam))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{os.path.basename(caminho)}"')
            self.end_headers()
            with open(caminho, "rb") as f:
                shutil_copy = f.read(1 << 20)
                while shutil_copy:
                    self.wfile.write(shutil_copy)
                    shutil_copy = f.read(1 << 20)
            return

        if rota.startswith("/api/state/"):
            mod = rota.split("/api/state/", 1)[1]
            p = caminho_estado(mod)
            # A versão do arquivo vai no ETag, e a tela a devolve no If-Match
            # ao salvar. É o que permite recusar a gravação de quem carregou a
            # tela antes de outra pessoa salvar. Vai como cabeçalho, e não
            # dentro do JSON, para não misturar controle com o dado da tela.
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    dados = json.load(f)
                # ORDEM CENTRAL, no servidor. A tela de Clientes ordenava
                # do seu jeito, o seletor de NF-e do dele, e a mesma empresa
                # aparecia em posições diferentes conforme onde se olhasse.
                # Ordenar aqui faz com que TODA tela que lê este estado receba
                # a lista já na ordem — inclusive as que ainda nem existem.
                if mod == "clientes" and isinstance(dados.get("clientes"), list):
                    dados["clientes"] = fiscale_cadastro.ordenar(dados["clientes"])
                # O segredo NUNCA sai daqui: no lugar dele vai só a situação
                # ("ok" / "aguardando"), para a tela poder avisar.
                return self._json(fiscale_segredos.mascarar_para_enviar(mod, dados, DADOS),
                                  etag=versao_estado(mod))
            return self._json({}, etag=versao_estado(mod))
        if rota.startswith("/dados/uploads/"):
            arq = os.path.join(UPLOADS, os.path.basename(rota))
            if os.path.exists(arq):
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.end_headers()
                with open(arq, "rb") as f:
                    self.wfile.write(f.read())
                return
            self.send_error(404)
            return
        return super().do_GET()

    def do_DELETE(self):
        """DELETE só existe nas rotas do módulo NFS-e (ex.: remover certificado)."""
        rota = urlparse(self.path).path
        if self._exigir_login(rota):
            return self._json({"erro": "não autenticado"}, 401)
        if self._barrar_por_papel(rota):
            return
        if self._rota_nfse(rota):
            return self._proxy_nfse()
        return self._json({"erro": "rota desconhecida"}, 404)

    def do_POST(self):
        rota = urlparse(self.path).path
        tam = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(tam) if tam else b""

        if rota == "/api/primeiro-acesso":
            # Só existe enquanto não há nenhum usuário, e só para quem está no PC.
            if not sem_usuarios():
                return self._json({"ok": False, "erro": "O sistema já tem usuários."}, 409)
            if not self._eh_local():
                return self._json({"ok": False, "erro": "A senha do admin só pode ser criada "
                                   "no computador onde o Fiscale está instalado."}, 403)
            try:
                dados = json.loads(corpo or b"{}")
            except Exception:
                dados = {}
            ok, erro = criar_admin(dados.get("senha") or "")
            if not ok:
                return self._json({"ok": False, "erro": erro}, 400)
            tok = SESSOES.criar("admin")
            return self._json({"ok": True}, set_cookie=self._cookie_sessao(tok))

        if rota == "/api/login":
            try:
                dados = json.loads(corpo or b"{}")
            except Exception:
                dados = {}
            # O campo aceita o login antigo OU o e-mail profissional. O que
            # entra aqui é só o CAMINHO de entrada — quem manda é o `uid`.
            usuario = (dados.get("usuario") or "").strip()
            senha = dados.get("senha") or ""
            # O IP vem do soquete. Cabeçalho de encaminhamento é escrito por
            # quem manda a requisição: aceitá-lo de qualquer um seria deixar o
            # atacante escolher em qual contador ele cai. A ÚNICA exceção é o
            # proxy HTTPS desta máquina (soquete de loopback) — sem ela, todo
            # o escritório cairia no contador de 127.0.0.1. Ver fiscale_proxy.
            origem = fiscale_proxy.origem(self.client_address[0] or "", self.headers)

            # Entrada absurda morre aqui, antes do PBKDF2 e antes do banco.
            # A resposta é a MESMA das outras falhas — um erro específico
            # para "muito grande" seria mais um sinal a observar de fora.
            if (len(usuario) > LIMITE_IDENTIFICADOR
                    or len(str(senha)) > LIMITE_SENHA):
                TENTATIVAS.registrar_falha(ip=origem)
                auditar(fiscale_auditoria.LOGIN_RECUSADO,
                        fiscale_auditoria.RECUSADO,
                        detalhe=fiscale_auditoria.D_ENTRADA_GRANDE,
                        **self._ctx())
                return self._json(
                    {"ok": False, "erro": ERRO_CREDENCIAL}, 401)

            # Quem a política vai contar. Resolver ANTES de conferir a senha
            # é o que permite contar por conta, e não pelo texto digitado —
            # senão entrar por e-mail e por login seriam dois contadores.
            #
            # Quando não existe conta, contamos assim mesmo (Fase 2.1), num
            # ESPAÇO SEPARADO do das contas reais — não num prefixo dentro
            # do mesmo espaço. Prefixo é convenção: bastaria alguém criar um
            # login com aquele formato para as duas coisas colidirem. A
            # chave é um HMAC do que foi digitado sob o segredo local, então
            # `maria@x` cai sempre na mesma linha e o banco nunca fica
            # sabendo que alguém tentou `maria@x`. Sem isso, varrer NOMES de
            # usuário não encontrava freio nenhum antes do teto de IP.
            alvo, _reg = resolver_usuario(usuario)
            if alvo:
                chave = alvo
                escopo = fiscale_tentativas.ESCOPO_CONTA
            else:
                chave = PIMENTA.chave_do_desconhecido(usuario)
                escopo = fiscale_tentativas.ESCOPO_DESCONHECIDO

            # A porta decide antes de a senha ser conferida. Conferir custa
            # 200 mil iterações de PBKDF2: deixar isso rodar a cada tentativa
            # é entregar o servidor como ferramenta do ataque.
            v = TENTATIVAS.avaliar(uid=chave, ip=origem, escopo=escopo)
            if v.bloqueado:
                # Conta real vai pelo uid; identificador que não existe vai
                # pela chave derivada. Em nenhum caso o texto digitado entra.
                auditar(fiscale_auditoria.LOGIN_BLOQUEADO,
                        fiscale_auditoria.BLOQUEADO,
                        ator_uid=alvo or "",
                        derivado="" if alvo else chave,
                        detalhe=(fiscale_auditoria.D_LIMITE_IP
                                 if v.escopo == fiscale_tentativas.ESCOPO_IP
                                 else fiscale_auditoria.D_LIMITE_CONTA),
                        **self._ctx())
                return self._json({"ok": False, "erro": v.motivo,
                                   "espera_s": v.espera_s}, 429)
            if v.atraso_s:
                ESPERAR(v.atraso_s)

            uid = verificar_login(usuario, senha)
            if not uid:
                TENTATIVAS.registrar_falha(uid=chave, ip=origem,
                                           escopo=escopo)
                # O motivo separa credencial errada de conta desativada. A
                # RESPOSTA continua sendo uma só — quem lê o registro é o
                # administrador, quem lê a resposta é quem está tentando.
                if not alvo:
                    porque = fiscale_auditoria.D_INEXISTENTE
                elif not fiscale_usuarios.esta_ativo(_reg or {}):
                    porque = fiscale_auditoria.D_INATIVO
                else:
                    porque = fiscale_auditoria.D_CREDENCIAL
                auditar(fiscale_auditoria.LOGIN_RECUSADO,
                        fiscale_auditoria.RECUSADO,
                        ator_uid=alvo or "",
                        derivado="" if alvo else chave,
                        detalhe=porque, **self._ctx())
            if uid:
                # Zera pelo `uid`, que é a chave real desta conta. A chave
                # derivada só existe enquanto a conta não existe.
                TENTATIVAS.registrar_acerto(uid=uid, ip=origem)
                # A sessão guarda o UID, nunca o e-mail digitado. É o que faz
                # trocar o e-mail de alguém amanhã não derrubar a sessão dela
                # hoje, nem quebrar o vínculo com o Elo.
                #
                # "Manter conectado" só troca a VALIDADE (12 h → 7 dias). Sem
                # marcar, o comportamento é exatamente o de antes do portal.
                # Só `True` de verdade liga: "false" em texto não é marcação.
                validade = (fiscale_sessoes.VALIDADE_MANTER_S
                            if dados.get("manter") is True else None)
                tok = SESSOES.criar(uid, validade_s=validade)
                auditar(fiscale_auditoria.LOGIN_OK, fiscale_auditoria.OK,
                        ator_uid=uid, **self._ctx())
                return self._json({"ok": True, "destino": "/central.html"},
                                  set_cookie=self._cookie_sessao(tok, validade))
            # Mensagem única para senha errada, usuário inexistente e conta
            # desativada. Distinguir os casos diria a quem tenta que aquele
            # e-mail existe no escritório.
            return self._json({"ok": False, "erro": ERRO_CREDENCIAL}, 401)

        if rota == "/api/logout":
            # Apaga do servidor, não só do navegador: cookie limpo com
            # sessão viva continuaria valendo para quem tivesse copiado.
            quem_saiu = self._usuario_sessao()
            SESSOES.revogar(self._token_sessao())
            auditar(fiscale_auditoria.LOGOUT, fiscale_auditoria.OK,
                    ator_uid=quem_saiu or "", **self._ctx())
            return self._json({"ok": True},
                              set_cookie="fiscale_sessao=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")

        if self._exigir_login(rota):
            return self._json({"erro": "não autenticado"}, 401)
        if self._barrar_por_papel(rota):
            return

        # ── Contábil (FASE CONTÁBIL 1) — toda escrita exige CSRF ─────────
        if rota.startswith("/api/contabil/"):
            return self._contabil("POST", rota, corpo)

        # ── Decisões administrativas da ingestão NF-e ────────────────────
        #
        # O QUE ESTAS ROTAS NÃO FAZEM: falar com a SEFAZ. Encerrar a revisão
        # devolve a empresa à fila de elegibilidade e para por aí — a consulta
        # seguinte continua sendo outra decisão, com cooldown, teto e trava.
        #
        # AS TRÊS SÃO DECLARADAS UMA A UMA, e não por prefixo, porque é assim
        # que `teste_papeis.py` enxerga rota local — e foi ele quem pegou o
        # prefixo aqui, dizendo que a exceção do proxy apontava para rota
        # inexistente. A guarda estava certa.
        if rota == "/api/nfe/admin/revisao/encerrar":
            return self._decisao_nfe(rota, corpo)
        if rota == "/api/nfe/admin/revisao/reabrir":
            return self._decisao_nfe(rota, corpo)
        if rota == "/api/nfe/admin/cobertura/marco":
            return self._decisao_nfe(rota, corpo)
        if rota == "/api/nfe/admin/bloqueio/manter":
            return self._decisao_nfe(rota, corpo)

        # ── Backup e Migração (escrita) ──────────────────────────────────
        #
        # DEPOIS DOS PORTÕES, e isso é o ponto (T5). Este bloco vivia ANTES de
        # `_exigir_login` e `_barrar_por_papel`, e se defendia sozinho com um
        # `eh_admin`. Duas consequências, ambas medidas:
        #
        #   • sem sessão a resposta era 403 ("somente o administrador") em vez
        #     de 401 — quem recusava era a autodefesa, não o portão;
        #   • `fiscale_papeis` NÃO governava estas rotas, então rota nova sob
        #     `/api/backup/` nascia fora da lista de permissão.
        #
        # A autodefesa de PAPEL saiu: `_ADMIN_PREFIXOS` já declara
        # ("POST", "/api/backup/"), e duas autoridades sobre a mesma decisão é
        # como uma delas fica velha sem ninguém perceber. A conferência de
        # máquina local FICA: ela responde outra pergunta — "é neste PC?" —,
        # que papel nenhum responde.
        if rota.startswith("/api/backup/"):
            import fiscale_backup as _bk
            if not self._eh_local():
                return self._json({"erro": "Backup e restauração só no computador "
                                            "onde o Fiscale está instalado."}, 403)
            try:
                d = json.loads(corpo or b"{}")
            except Exception:
                d = {}

            if rota == "/api/backup/criar":
                # Sem pasta escolhida, vai para a canônica — que é onde o
                # resto do sistema procura backup.
                pasta = ((d.get("pasta") or "").strip()
                         or str(backup_drive.canonica()))
                quem = self._usuario_sessao() or ""
                comeco = time.monotonic()

                # O callback recebe (feitos, total, info) e só guarda número.
                # A frase-senha não passa por aqui, não é registrada e não
                # aparece em nenhuma linha de log desta rota.
                def _andou(feitos, total, info=None):
                    info = info or {}
                    _backup_anotar(ativo=True, feitos=int(feitos),
                                   total=int(total),
                                   fase=str(info.get("fase") or ""),
                                   bytes=int(info.get("bytes") or 0),
                                   bytes_total=int(info.get("bytes_total") or 0),
                                   duracao_s=round(time.monotonic() - comeco, 1))

                _backup_anotar(ativo=True, fase=_bk.FASE_DESCOBERTA, feitos=0,
                               total=0, bytes=0, bytes_total=0,
                               iniciado_em=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                               duracao_s=0.0, resultado="", erro="", arquivo="")
                _backup_log("início · pasta=%s · por=%s" % (pasta, quem or "?"))
                try:
                    r = _bk.exportar(pasta, d.get("frase") or None, raiz=DADOS,
                                     progresso=_andou)
                except ValueError as e:
                    _backup_anotar(ativo=False, resultado="erro",
                                   erro=e.__class__.__name__,
                                   duracao_s=round(time.monotonic() - comeco, 1))
                    _backup_log("recusado · %s" % e.__class__.__name__)
                    return self._json({"erro": str(e)}, 400)
                except Exception as e:
                    fase = getattr(e, "fase", "") or "?"
                    arq = getattr(e, "arquivo", "") or ""
                    feitos = int(getattr(e, "arquivos_processados", 0) or 0)
                    gasto = round(time.monotonic() - comeco, 1)
                    _backup_anotar(ativo=False, resultado="erro", fase=fase,
                                   erro=e.__class__.__name__, arquivo=arq,
                                   feitos=feitos, duracao_s=gasto)
                    _backup_log("FALHOU · fase=%s · arquivo=%s · %.1fs · "
                                "%d arquivo(s) processado(s) · %s"
                                % (fase, arq or "-", gasto, feitos,
                                   e.__class__.__name__))
                    auditar(fiscale_auditoria.BACKUP_FALHOU,
                            fiscale_auditoria.ERRO, ator_uid=quem,
                            detalhe=("fase=%s" % fase)[:40], quantidade=feitos,
                            **self._ctx())
                    return self._json({"erro": f"{e.__class__.__name__}: {e}",
                                       "fase": fase, "arquivo": arq,
                                       "duracao_s": gasto,
                                       "arquivos_processados": feitos}, 500)
                gasto = round(time.monotonic() - comeco, 1)
                _backup_anotar(ativo=False, resultado="ok", fase=_bk.FASE_CONCLUSAO,
                               duracao_s=gasto, erro="", arquivo="")
                _backup_log("fim · %.1fs · %s arquivo(s) · %s byte(s) no pacote "
                            "· fases=%s"
                            % (gasto, r.get("arquivos"), r.get("bytes"),
                               r.get("fases")))
                auditar(fiscale_auditoria.BACKUP_CRIADO, fiscale_auditoria.OK,
                        ator_uid=quem, quantidade=int(r.get("arquivos") or 0),
                        detalhe=("%.1fs" % gasto)[:40], **self._ctx())
                return self._json(r)

            if rota == "/api/backup/inspecionar":
                # A FRASE É NECESSÁRIA AQUI, e não era.
                #
                # No v1 o manifesto ficava em claro: dava para conferir o
                # pacote inteiro sem senha nenhuma. No v2 não há nada legível
                # sem a chave — e esta rota continuou chamando `conferir()`
                # sem frase. O resultado não foi um erro: foi `FraseIncorreta`
                # escapando do tratador, a conexão morrendo sem resposta, e a
                # tela presa em "Conferindo…" para sempre.
                frase = d.get("frase") or None
                try:
                    m = _bk.inspecionar((d.get("arquivo") or "").strip())
                    if m.get("_precisa_frase") and not frase:
                        # Sem a frase dá para dizer o que é, e nada além.
                        return self._json({
                            "manifesto": m, "conferencia": None,
                            "precisa_frase": True,
                            "aviso": "Este backup é cifrado por inteiro. "
                                     "Informe a frase-senha para conferir o "
                                     "conteúdo."})
                    c = _bk.conferir(m["_arquivo"], frase)
                except _bk.FraseIncorreta as e:
                    return self._json({"erro": str(e),
                                       "frase_incorreta": True}, 400)
                except _bk.BackupInvalido as e:
                    return self._json({"erro": str(e)}, 400)
                except Exception as e:
                    return self._json(
                        {"erro": "Não consegui conferir o arquivo (%s)."
                                 % e.__class__.__name__}, 500)
                # O MANIFESTO QUE VAI PARA A TELA É O DE DENTRO.
                #
                # No v2, `inspecionar()` sem frase devolve só o cabeçalho
                # técnico — de propósito: ele não conta nada sobre o
                # escritório. Mas quando a frase veio e a conferência abriu o
                # pacote, o manifesto INTEIRO está ali, com data, origem e
                # resumo. Mandar o cabeçalho nessa hora fazia a tela exibir
                # "0 arquivos, 0 empresas" logo abaixo de "79.328 conferidos".
                manifesto = dict(c.get("manifesto") or m)
                manifesto.setdefault("_arquivo", m.get("_arquivo"))
                manifesto.setdefault("_bytes", m.get("_bytes"))
                manifesto["_precisa_frase"] = m.get("_precisa_frase", False)
                return self._json({"manifesto": manifesto, "conferencia": {
                    "ok": c["ok"], "conferidos": c["conferidos"],
                    "problemas": c["problemas"]},
                    "destino": _bk.estado_do_destino(DADOS)})

            if rota == "/api/backup/restaurar":
                # O banco de sessões vive DENTRO da pasta de dados, e no Windows
                # um arquivo aberto impede mover a pasta — que é o que o modo
                # "Substituir" faz. Sem fechar aqui, restaurar com o Fiscale
                # aberto falhava com "arquivo em uso", e a mensagem não dizia
                # o porquê. Fechar antes é seguro: a restauração troca o
                # cadastro de usuários, então as sessões morrem de qualquer jeito.
                fechou = False
                try:
                    SESSOES.fechar()
                    fechou = True
                except Exception:
                    pass
                try:
                    r = _bk.restaurar((d.get("arquivo") or "").strip(),
                                      d.get("frase") or None, raiz=DADOS,
                                      modo=d.get("modo") or None)
                except _bk.FraseIncorreta as e:
                    return self._json({"erro": str(e), "frase_incorreta": True}, 400)
                except _bk.BackupInvalido as e:
                    return self._json({"erro": str(e)}, 400)
                except Exception as e:
                    return self._json({"erro": f"{e.__class__.__name__}: {e}"}, 500)
                finally:
                    if fechou:
                        try:
                            SESSOES.reabrir()
                        except Exception:
                            pass
                if r.get("ok"):
                    # todo mundo sai: seguir logado com o cadastro anterior
                    # seria estar autenticado por um usuário que não existe mais.
                    try:
                        SESSOES.revogar_todas()
                    except Exception:
                        pass
                return self._json(r)

            return self._json({"erro": "rota desconhecida"}, 404)

        # ── Importar cadastro de empresas (Cadastro 1) ───────────────────
        #
        # ESTAS ROTAS FICAM NO TOPO DO do_POST, E ISSO NÃO É ARRUMAÇÃO.
        #     Elas estavam aninhadas DENTRO de `if rota.startswith("/api/backup/")`.
        #     Nenhum caminho de cadastro começa com `/api/backup/`, então a
        #     requisição atravessava o `do_POST` inteiro sem casar com nada e
        #     terminava no "rota desconhecida", 404 — com as rotas escritas,
        #     classificadas e testadas. Os testes provavam que elas EXISTIAM;
        #     nenhum provava que eram ALCANÇÁVEIS.
        # ── Central de regularidade: classificar e consultar ─────────────
        if rota in ("/api/situacao/classificar", "/api/situacao/consultar"):
            import situacao.indice as _sit_indice

            try:
                dados = json.loads(corpo or b"{}")
            except ValueError:
                return self._json({"erro": "pedido inválido"}, 400)
            if not isinstance(dados, dict):
                return self._json({"erro": "pedido inválido"}, 400)
            # Conclusão sobre regularidade e consulta a órgão sem registro é
            # exatamente o que a trilha existe para impedir.
            if self._auditoria_ou_recusa():
                return
            usuario = self._usuario_sessao() or ""

            if rota == "/api/situacao/classificar":
                import situacao.interpretacao as _sit_interp
                try:
                    with _sit_indice.abrir(DADOS) as ix:
                        reg = _sit_interp.registrar(
                            DADOS, ix, str(dados.get("identidade") or ""),
                            str(dados.get("esfera") or ""),
                            str(dados.get("tipo") or ""),
                            str(dados.get("documento_id") or ""),
                            str(dados.get("status") or ""), usuario,
                            quantidade_pendencias=dados.get("quantidade_pendencias"),
                            valor_total=dados.get("valor_total"),
                            validade=str(dados.get("validade") or ""),
                            pendencias=dados.get("pendencias") or [],
                            observacao=str(dados.get("observacao") or ""))
                except _sit_interp.Recusada as e:
                    return self._json({"erro": str(e)}, 400)
                except Exception as e:
                    sys.stderr.write("situacao/classificar falhou: %s\n"
                                     % e.__class__.__name__)
                    return self._json(
                        {"erro": "Não consegui registrar (%s)."
                                 % e.__class__.__name__}, 500)
                auditar(fiscale_auditoria.USUARIO_EDITADO, fiscale_auditoria.OK,
                        ator_uid=usuario, alvo_uid="situacao fiscal",
                        detalhe="classificacao %s/%s %s" % (
                            reg["esfera"], reg["tipo"], reg["status"]),
                        quantidade=1, **self._ctx())
                return self._json({"ok": True, "interpretacao": reg})

            # /api/situacao/consultar — SÓ A FEDERAL (SITFIS).
            #     O escopo municipal foi retirado em 12/09/2026: o Recife em
            #     Dia não tem API e exige CAPTCHA resolvido por pessoa.
            esfera = str(dados.get("esfera") or "FEDERAL").upper()
            if esfera != "FEDERAL":
                return self._json(
                    {"erro": "A Situação Fiscal opera somente na esfera "
                             "federal (SITFIS via Integra Contador)."}, 400)
            ident = fiscale_cadastro.normalizar_documento(
                dados.get("identidade") or "")
            no_cadastro = {fiscale_cadastro.documento_de(c) for c in
                           (ler_estado("clientes").get("clientes") or [])}
            if not ident or ident not in no_cadastro:
                return self._json({"erro": "empresa não está no cadastro"}, 404)
            if not _CONSULTA_SITUACAO.acquire(blocking=False):
                return self._json(
                    {"erro": "Já há uma consulta oficial em andamento. "
                             "Aguarde ela terminar."}, 429)
            try:
                import situacao.ciclo as _sit_ciclo
                r = _sit_ciclo.uma_empresa(DADOS, ident,
                                           forcar=bool(dados.get("forcar")),
                                           usuario=usuario)
            except Exception as e:
                sys.stderr.write("situacao/consultar falhou: %s\n"
                                 % e.__class__.__name__)
                return self._json(
                    {"erro": "A consulta falhou (%s)." % e.__class__.__name__},
                    500)
            finally:
                _CONSULTA_SITUACAO.release()
            auditar(fiscale_auditoria.USUARIO_EDITADO, fiscale_auditoria.OK,
                    ator_uid=usuario, alvo_uid="situacao fiscal",
                    detalhe="consulta federal %s" % r.get("desfecho"),
                    quantidade=1, **self._ctx())
            return self._json({"ok": True, "resultado": r})

        if rota == "/api/situacao/importar":
            import situacao.importacao as _sit_portao
            import situacao.indice as _sit_indice

            try:
                nome_arq, bruto, extras = self._planilha_enviada(corpo)
            except ValueError as e:
                return self._json({"erro": str(e)}, 400)

            identidade = str(extras.get("identidade") or "")
            esfera = str(extras.get("esfera") or "")
            tipo = str(extras.get("tipo") or "")
            if self._auditoria_ou_recusa():
                return
            try:
                with _sit_indice.abrir(DADOS) as ix:
                    # A ORIGEM DO CANAL, quando a pessoa disse de onde veio.
                    # Fora da lista, fica o apelido de sempre ("entregue à
                    # mão"): ninguém inventa origem por um campo de formulário.
                    # `ASSISTIDA_RECIFE` entra aqui porque é canal assistido de
                    # verdade: a pessoa resolve o CAPTCHA no portal, baixa o PDF
                    # e o entrega. Não há consulta automática municipal — e o
                    # `/api/situacao/consultar` continua recusando essa esfera.
                    origem = str(extras.get("origem") or "").upper()
                    if origem not in ("ASSISTIDA_ECAC", "ASSISTIDA_RECIFE",
                                      "MANUAL"):
                        origem = "ASSISTIDA"
                    r = _sit_portao.importar(
                        DADOS, ix, identidade, esfera, tipo, bruto,
                        origem=origem,
                        extra={"arquivo": nome_arq[:80]},
                        usuario=self._usuario_sessao() or "",
                        metodo="UPLOAD")
                    estado = ix.estado(r["identidade"], r["esfera"], r["tipo"])
            except _sit_portao.Recusado as e:
                return self._json({"erro": str(e)}, 400)
            except Exception as e:
                return self._json(
                    {"erro": "Não consegui guardar o documento (%s)."
                             % e.__class__.__name__}, 500)

            auditar(fiscale_auditoria.USUARIO_EDITADO, fiscale_auditoria.OK,
                    ator_uid=self._usuario_sessao() or "",
                    alvo_uid="situacao fiscal",
                    detalhe="%s/%s" % (r["esfera"], r["tipo"]),
                    quantidade=1, **self._ctx())
            return self._json({"ok": True, "documento": r, "estado": estado})

        if rota.startswith("/api/clientes/importar/"):
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            try:
                nome_arq, bruto, extras = self._planilha_enviada(corpo)
            except ValueError as e:
                return self._json({"erro": str(e)}, 400)

            if rota == "/api/clientes/importar/previa":
                # PRÉVIA: lê, concilia e devolve o que ACONTECERIA. Não grava
                # nada — nem a planilha, que morre nesta função.
                try:
                    linhas, avisos = fiscale_cadastro.ler_planilha(bruto, nome_arq)
                except fiscale_cadastro.PlanilhaInvalida as e:
                    return self._json({"erro": str(e)}, 400)
                except Exception as e:
                    return self._json(
                        {"erro": "Não consegui ler a planilha (%s)."
                                 % e.__class__.__name__}, 400)
                atuais = (ler_estado("clientes").get("clientes") or [])
                previa = fiscale_cadastro.conciliar(atuais, linhas)
                previa["avisos"] = avisos
                previa["arquivo"] = nome_arq[:80]
                previa["cadastro_atual"] = len(atuais)
                # O BILHETE DA PRÉVIA. Amarra três coisas: quem viu, QUAL
                # arquivo, e quando. Sem ele, `/aplicar` seria uma rota que
                # grava a partir de um upload qualquer — e a prévia viraria
                # decoração, não etapa.
                previa["token"] = PREVIAS.emitir(self._usuario_sessao(), bruto)
                return self._json(previa)

            if rota == "/api/clientes/importar/aplicar":
                if self._auditoria_ou_recusa():
                    return
                # A prévia é OBRIGATÓRIA, e tem de ser desta pessoa e deste
                # arquivo. Aceitar sem bilhete deixaria gravar sem ninguém ter
                # visto o que ia mudar.
                ok_bilhete, porque = PREVIAS.conferir(
                    extras.get("token"), self._usuario_sessao(), bruto)
                if not ok_bilhete:
                    return self._json({"erro": porque,
                                       "precisa_previa": True}, 409)
                try:
                    linhas, _ = fiscale_cadastro.ler_planilha(bruto, nome_arq)
                except fiscale_cadastro.PlanilhaInvalida as e:
                    return self._json({"erro": str(e)}, 400)
                except Exception as e:
                    return self._json(
                        {"erro": "Não consegui ler a planilha (%s)."
                                 % e.__class__.__name__}, 400)

                estado = ler_estado("clientes")
                atuais = estado.get("clientes") or []
                previa = fiscale_cadastro.conciliar(atuais, linhas)
                aceitar = str(extras.get("aceitar_conflitos") or "").lower() \
                    in ("1", "true", "sim", "on")

                # BACKUP ANTES DE ESCREVER. O cadastro é a memória do
                # escritório; uma importação errada sem cópia anterior seria
                # irreversível, e "reverter" não pode depender de lembrar o
                # que estava lá.
                copia = ""
                try:
                    origem = caminho_estado("clientes")
                    if os.path.exists(origem):
                        copia = origem + ".antes-da-importacao-%s" % (
                            datetime.now().strftime("%Y%m%d-%H%M%S"))
                        shutil.copy2(origem, copia)
                except Exception as e:
                    return self._json(
                        {"erro": "Não consegui guardar a cópia de segurança "
                                 "do cadastro (%s). Nada foi alterado."
                                 % e.__class__.__name__}, 500)

                novos, aplicado = fiscale_cadastro.aplicar(
                    atuais, previa, aceitar_conflitos=aceitar)
                estado["clientes"] = novos
                gravar_json_atomico(caminho_estado("clientes"), estado)
                PREVIAS.queimar(extras.get("token"))

                # A auditoria registra O QUE mudou, nunca a planilha. Guardar
                # o conteúdo dela seria guardar uma segunda cópia do cadastro,
                # dentro do registro de segurança.
                auditar(fiscale_auditoria.USUARIO_EDITADO,
                        fiscale_auditoria.OK,
                        ator_uid=self._usuario_sessao() or "",
                        alvo_uid="cadastro de clientes",
                        detalhe="importacao",
                        quantidade=aplicado["criados"] + aplicado["atualizados"],
                        **self._ctx())
                return self._json({"ok": True, "aplicado": aplicado,
                                   "resumo": previa["resumo"],
                                   "backup": os.path.basename(copia),
                                   "total": len(novos)})

            return self._json({"erro": "rota desconhecida"}, 404)


        if rota == "/api/encerrar":
            # Sem console, o ícone da bandeja é fácil de não achar (o Windows o
            # esconde atrás da setinha "^"). Este botão é o caminho visível.
            # Qualquer usuário pode encerrar, MAS só do próprio PC: pela rede
            # derrubaria o servidor de todo mundo do escritório.
            if not self._eh_local():
                return self._json({"ok": False, "erro": "O Fiscale só pode ser encerrado no "
                                   "computador onde ele está rodando."}, 403)
            self._json({"ok": True})
            # `os._exit()` mata SÓ este processo. No Windows o filho do módulo
            # NFS-e não é levado junto (não há grupo de processos herdado como
            # no POSIX), e `atexit` também não roda — então ele ficaria escutando
            # a porta interna e segurando a pasta de dados. Derruba o filho ANTES.
            def _encerrar():
                registrar_encerramento(MOTIVO_TELA, "Home > Encerrar")
                parar_modulo_nfse()
                os._exit(0)
            threading.Timer(0.5, _encerrar).start()
            return

        if rota == "/api/elo/abrir":
            # Emite o bilhete de entrada do Elo. Vale 90 s e serve uma vez.
            #
            # O token volta no CORPO da resposta, e a tela o envia adiante
            # por um formulário POST — nunca na URL. Query string fica no
            # histórico do navegador, no cabeçalho Referer e no log de
            # acesso de qualquer proxy no caminho.
            #
            # Se o Elo estiver fora do ar ou mal configurado, isto falha
            # sozinho: nada aqui bloqueia login ou uso fiscal.
            if not TEM_ELO:
                return self._json({"ok": False, "erro":
                                   "Este Fiscale não tem o módulo de integração com o Elo."}, 503)
            # `quem` é o UID — a sessão nunca guardou o e-mail digitado. É
            # isso que mantém o `sub` do token estável: o Elo casa a pessoa
            # por `providerUid = claims.sub`, e trocar o e-mail dela aqui não
            # pode desfazer o vínculo lá.
            quem = self._usuario_sessao()
            cfg = fiscale_elo.configuracao(DADOS)
            try:
                token = fiscale_elo.emitir_token(DADOS, quem, cfg["tenant_id"])
            except fiscale_elo.EloIndisponivel as e:
                return self._json({"ok": False, "erro": f"Não foi possível abrir o Elo: {e}"}, 503)
            except Exception:
                return self._json({"ok": False, "erro":
                                   "Não foi possível abrir o Elo agora."}, 503)
            # `base` existe para a tela embutir o ELO sem saber o endereco
            # dele: a origem continua vindo de um lugar so, o elo_config.json.
            return self._json({"ok": True, "token": token,
                               "base": cfg["url"],
                               "destino": cfg["url"] + "/api/auth/exchange"})

        if rota == "/api/elo/sync":
            # Sincronização completa, sob demanda. Enfileira e tenta
            # enviar na hora; se o Elo estiver fora, fica pendente e a
            # thread de fundo tenta de novo depois.
            if not TEM_ELO_SYNC:
                return self._json({"ok": False, "erro":
                                   "Este Fiscale não tem o módulo de sincronização."}, 503)
            cfg = fiscale_elo.configuracao(DADOS) if TEM_ELO else {}
            fiscale_elo_sync.enfileirar_full(DADOS)
            resumo = fiscale_elo_sync.processar(DADOS, cfg)
            return self._json({"ok": "erro" not in resumo, **resumo})

        if rota == "/api/elo/sync/tentar":
            # Retry manual: zera a espera do que estiver pendente.
            if not TEM_ELO_SYNC:
                return self._json({"ok": False, "erro": "módulo indisponível"}, 503)
            cfg = fiscale_elo.configuracao(DADOS) if TEM_ELO else {}
            liberados = fiscale_elo_sync.fila(DADOS).liberar_todos()
            resumo = fiscale_elo_sync.processar(DADOS, cfg)
            return self._json({"ok": "erro" not in resumo, "liberados": liberados, **resumo})

        if rota == "/api/optantes":
            # Consulta várias empresas de uma vez (em paralelo — são chamadas de
            # rede independentes; em série o escritório esperaria minutos).
            try:
                dados = json.loads(corpo or b"{}")
            except Exception:
                dados = {}
            lista = [str(c) for c in (dados.get("cnpjs") or [])][:200]
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as ex:
                return self._json(list(ex.map(cnpj_publico.consultar, lista)))

        if rota == "/api/xlsx-codigos":
            # Lê uma planilha .xlsx (que é um .zip de XMLs) usando só a biblioteca
            # padrão do Python — sem depender de openpyxl/pandas no executável.
            # Extrai os "tokens" de 4 a 6 dígitos das células; a tela filtra os que
            # batem com a tabela cClassTrib (mesma lógica do importar CSV).
            import base64 as _b64, io as _io, zipfile as _zip, re as _re
            try:
                d = json.loads(corpo or b"{}")
                bruto = _b64.b64decode((d.get("b64") or "").split(",")[-1])
                codigos: list[str] = []
                with _zip.ZipFile(_io.BytesIO(bruto)) as z:
                    alvos = [n for n in z.namelist()
                             if n.startswith("xl/") and n.endswith(".xml")
                             and ("sharedStrings" in n or "/worksheets/" in n)]
                    for nome in alvos:
                        txt = z.read(nome).decode("utf-8", "ignore")
                        # tira as tags XML, sobra o conteúdo das células
                        texto = _re.sub(r"<[^>]+>", " ", txt)
                        codigos += _re.findall(r"\b\d{4,6}\b", texto)
                # remove duplicados preservando a ordem
                vistos, unicos = set(), []
                for c in codigos:
                    if c not in vistos:
                        vistos.add(c); unicos.append(c)
                return self._json({"ok": True, "codigos": unicos[:2000]})
            except Exception as e:
                return self._json({"ok": False,
                                   "erro": f"Não consegui ler a planilha ({e.__class__.__name__}). "
                                           "Confirme que é um arquivo .xlsx do Excel."}, 400)

        # ── Gestão de usuários (somente admin) ─────────────────────────────
        if rota == "/api/usuarios":
            if not eh_admin(self._usuario_sessao()):
                return self._json({"erro": "somente o administrador"}, 403)
            try:
                d = json.loads(corpo or b"{}")
            except Exception:
                d = {}
            if self._auditoria_ou_recusa():
                return
            quem = self._usuario_sessao()
            nome = (d.get("usuario") or "").strip().lower()
            senha = d.get("senha") or ""
            if not nome or not nome.replace("_", "").replace(".", "").isalnum():
                return self._json({"erro": "Nome de usuário inválido (use letras/números)."}, 400)

            todos = carregar_usuarios()
            novo = nome not in todos

            email = fiscale_usuarios.normalizar_email(d.get("email"))

            # Senha só é exigida na CRIAÇÃO. Editar nome ou e-mail de quem já
            # existe não pode obrigar a redefinir a senha da pessoa — seria
            # convidar o administrador a inventar uma e contar por aí.
            #
            # Quando há senha, ela passa pela política — e a política precisa
            # do nome e do e-mail NOVOS, que são justamente o que a pessoa
            # tende a repetir na senha.
            if novo and not senha:
                return self._json({"erro": "Informe a senha do novo usuário. "
                                            + fiscale_senhas.MENSAGEM_UI}, 400)
            if senha:
                ok_pol, motivo = fiscale_senhas.validar(
                    senha, uid=nome, nome=(d.get("nome") or ""), email=email)
                if not ok_pol:
                    return self._json({"erro": motivo}, 400)
            if email and not fiscale_usuarios.email_valido(email):
                return self._json({"erro": "E-mail inválido. Use algo como "
                                            "nome@empresa.com.br."}, 400)
            dono = fiscale_usuarios.email_em_uso(todos, email, exceto_uid=nome)
            if dono:
                # Diz que está em uso, sem dizer de quem: a tela é do
                # administrador, mas o princípio é o mesmo.
                return self._json({"erro": "Este e-mail já está cadastrado "
                                            "para outro usuário."}, 409)

            antes = dict(todos.get(nome) or {})
            reg = dict(antes)
            reg["uid"] = reg.get("uid") or nome        # imutável desde o nascimento
            reg["nome"] = (d.get("nome") or reg.get("nome") or "").strip()
            reg["email"] = email
            reg["ativo"] = bool(d.get("ativo", reg.get("ativo", True)))
            reg["admin"] = bool(d.get("admin", reg.get("admin", nome == "admin")))
            if senha:
                # `sal` e `hash` só mudam quando há senha nova. Sem isto,
                # editar o e-mail de alguém apagaria a senha dela.
                sal, h = _hash_senha(senha)
                reg["sal"], reg["hash"] = sal, h
            if nome == "admin":
                reg["admin"] = True

            # A guarda do ÚLTIMO ADMINISTRADOR ATIVO. Três portas levam ao
            # mesmo lugar — excluir, desativar, tirar o papel — e todas
            # precisam dela. Sem administrador ativo ninguém cadastra usuário,
            # ninguém mexe em certificado e ninguém reativa ninguém: o
            # conserto passaria a ser editar `usuarios.json` na mão, no
            # servidor.
            if not novo and fiscale_usuarios.ficaria_sem_admin(
                    todos, nome, novo_admin=reg["admin"],
                    novo_ativo=reg["ativo"]):
                return self._json(
                    {"erro": "Este é o único administrador ativo. Promova ou "
                             "reative outro antes de desativá-lo ou de tirar o "
                             "papel de administrador."}, 400)
            todos[nome] = reg
            salvar_usuarios(todos)

            # ── o que entra no livro ────────────────────────────────────
            # Só os NOMES dos campos que mudaram. Guardar o de-para seria
            # construir um histórico de dados pessoais que ninguém pediu — e
            # que passaria a ser mais uma coisa a proteger.
            ctx = self._ctx()
            if novo:
                auditar(fiscale_auditoria.USUARIO_CRIADO,
                        fiscale_auditoria.OK, ator_uid=quem or "",
                        alvo_uid=nome, **ctx)
            else:
                mudou = [c for c in fiscale_auditoria.CAMPOS_AUDITAVEIS
                         if c != "senha" and antes.get(c) != reg.get(c)]
                if senha:
                    mudou.append("senha")
                if mudou:
                    auditar(fiscale_auditoria.USUARIO_EDITADO,
                            fiscale_auditoria.OK, ator_uid=quem or "",
                            alvo_uid=nome, detalhe=",".join(sorted(mudou)),
                            **ctx)
            # Ativação, desativação e papel ganham evento próprio: são as três
            # perguntas que alguém faz ao abrir este registro, e procurá-las
            # dentro de "editado" seria transformar consulta em garimpo.
            era_ativo = fiscale_usuarios.esta_ativo(antes) if not novo else None
            if not novo and era_ativo is not None and era_ativo != reg["ativo"]:
                auditar(fiscale_auditoria.USUARIO_ATIVADO if reg["ativo"]
                        else fiscale_auditoria.USUARIO_DESATIVADO,
                        fiscale_auditoria.OK, ator_uid=quem or "",
                        alvo_uid=nome, **ctx)
            era_admin = bool(antes.get("admin", nome == "admin"))
            if not novo and era_admin != reg["admin"]:
                auditar(fiscale_auditoria.PAPEL_ALTERADO,
                        fiscale_auditoria.OK, ator_uid=quem or "",
                        alvo_uid=nome,
                        detalhe=(fiscale_auditoria.D_VIROU_ADMIN if reg["admin"]
                                 else fiscale_auditoria.D_VIROU_OPERADOR),
                        **ctx)
            if senha and not novo and nome != (quem or ""):
                auditar(fiscale_auditoria.SENHA_REDEFINIDA,
                        fiscale_auditoria.OK, ator_uid=quem or "",
                        alvo_uid=nome, **ctx)

            # Conta desativada agora não pode continuar valendo com a sessão
            # que já estava aberta.
            if not reg["ativo"]:
                caidas = SESSOES.revogar_usuario(nome)
                if caidas:
                    auditar(fiscale_auditoria.SESSAO_INVALIDADA,
                            fiscale_auditoria.OK, ator_uid=quem or "",
                            alvo_uid=nome, quantidade=caidas,
                            detalhe=fiscale_auditoria.D_POR_DESATIVACAO, **ctx)
            return self._json({"ok": True, "criado": novo,
                               "usuario": fiscale_usuarios.para_tela({nome: reg})[0]})

        if rota == "/api/usuarios/excluir":
            quem = self._usuario_sessao()
            if not eh_admin(quem):
                return self._json({"erro": "somente o administrador"}, 403)
            try:
                d = json.loads(corpo or b"{}")
            except Exception:
                d = {}
            if self._auditoria_ou_recusa():
                return
            nome = (d.get("usuario") or "").strip().lower()
            todos = carregar_usuarios()
            if nome not in todos:
                return self._json({"erro": "Usuário não existe."}, 404)
            if nome == quem:
                return self._json({"erro": "Você não pode excluir o seu próprio usuário."}, 400)
            # Antes isto contava a marca `admin`, e não quem PODE administrar.
            # Com `ativo` existindo, um administrador desativado entrava na
            # conta e deixava excluir o único que ainda entrava.
            if fiscale_usuarios.ficaria_sem_admin(todos, nome, excluir=True):
                return self._json(
                    {"erro": "Não é possível excluir o único administrador "
                             "ativo."}, 400)
            del todos[nome]
            salvar_usuarios(todos)
            ctx = self._ctx()
            auditar(fiscale_auditoria.USUARIO_EXCLUIDO, fiscale_auditoria.OK,
                    ator_uid=quem or "", alvo_uid=nome, **ctx)
            # derruba sessões abertas do usuário excluído
            caidas = SESSOES.revogar_usuario(nome)
            if caidas:
                auditar(fiscale_auditoria.SESSAO_INVALIDADA,
                        fiscale_auditoria.OK, ator_uid=quem or "",
                        alvo_uid=nome, quantidade=caidas,
                        detalhe=fiscale_auditoria.D_POR_EXCLUSAO, **ctx)
            return self._json({"ok": True})

        if rota == "/api/senha":
            if self._auditoria_ou_recusa():
                return
            quem = self._usuario_sessao()
            try:
                d = json.loads(corpo or b"{}")
            except Exception:
                d = {}
            atual, nova = d.get("atual") or "", d.get("nova") or ""
            if not verificar_login(quem, atual):
                return self._json({"erro": "Senha atual incorreta."}, 400)
            todos = carregar_usuarios()
            reg = todos.get(quem) or {}
            ok_pol, motivo = fiscale_senhas.validar(
                nova, uid=quem, nome=reg.get("nome"), email=reg.get("email"))
            if not ok_pol:
                return self._json({"erro": motivo}, 400)
            sal, h = _hash_senha(nova)
            todos[quem].update({"sal": sal, "hash": h})
            salvar_usuarios(todos)
            auditar(fiscale_auditoria.SENHA_TROCADA, fiscale_auditoria.OK,
                    ator_uid=quem, alvo_uid=quem, **self._ctx())
            return self._json({"ok": True})

        if self._rota_nfse(rota):
            return self._proxy_nfse(corpo)

        if rota.startswith("/api/state/"):
            mod = rota.split("/api/state/", 1)[1]
            destino = caminho_estado(mod)
            dados = json.loads(corpo or b"{}")

            # Alguém salvou esta mesma tela depois de você abri-la?
            #
            # A tela manda no `If-Match` o ETag que recebeu no GET. Se o
            # arquivo já não está naquela versão, a gravação é RECUSADA — antes
            # disto a última a salvar apagava o trabalho da primeira, sem aviso
            # nenhum para nenhuma das duas.
            #
            # Recusar só quando o cabeçalho vem: uma tela antiga (ou o
            # `restaurar_backup.py`, que grava direto) continua funcionando
            # como antes. Detectar e avisar, não bloquear.
            enviado = (self.headers.get("If-Match") or "").strip().strip('"')
            atual = versao_estado(mod)
            if enviado and enviado != atual:
                return self._json(
                    {"erro": "Outra pessoa salvou esta tela enquanto você a "
                             "editava. Recarregue para ver o que mudou antes "
                             "de salvar de novo.",
                     "conflito": True}, 409, etag=atual)

            # Segredo de tela (o que uma tela declarou como credencial em
            # `fiscale_inscricoes`) NÃO vai para o disco em claro. Vai por DPAPI,
            # num campo irmão. O que já estava guardado e veio vazio é mantido:
            # o navegador nunca recebe a senha de volta, então salvar qualquer
            # outra coisa na tela apagaria a senha se não fosse por isto.
            try:
                anterior = {}
                if os.path.exists(destino):
                    with open(destino, encoding="utf-8-sig") as f:
                        anterior = json.load(f) or {}
                dados = fiscale_segredos.preservar_protegidos(mod, dados, anterior)
                dados, _rel_seg = fiscale_segredos.proteger_para_gravar(mod, dados, DADOS)
            except Exception as e:
                print(f"  [segredos] não consegui proteger o estado de '{mod}': {e}")

            # Estado ANTERIOR do cadastro de clientes, para descobrir quem
            # de fato mudou. Lido antes de sobrescrever, e só para o módulo
            # que sincroniza.
            antes = None
            if mod == "clientes" and TEM_ELO_SYNC and os.path.exists(destino):
                try:
                    with open(destino, encoding="utf-8-sig") as f:
                        antes = (json.load(f) or {}).get("clientes") or []
                except Exception:
                    antes = None

            gravar_json_atomico(destino, dados)

            # Enfileirar é gravação local, rápida, e acontece DEPOIS de o
            # cadastro estar salvo. Se falhar, o cliente já está salvo — a
            # sincronização é que fica para trás, nunca o dado fiscal.
            if mod == "clientes" and TEM_ELO_SYNC:
                try:
                    fiscale_elo_sync.enfileirar_diferencas(
                        DADOS, antes, (dados or {}).get("clientes") or [])
                except Exception as e:
                    print(f"  [Elo] não consegui enfileirar a sincronização: {e}")

            # O ETag novo volta junto: a tela segue salvando sem precisar
            # recarregar, e continua protegida no salvamento seguinte.
            return self._json({"ok": True}, etag=versao_estado(mod))

        if rota == "/api/upload":
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype or "boundary=" not in ctype:
                return self._json({"erro": "envio inválido"}, 400)
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"').encode()
            campos = parse_multipart(corpo, boundary)
            if "file" not in campos or not campos["file"][0]:
                return self._json({"erro": "sem arquivo"}, 400)
            nome_arquivo, conteudo = campos["file"]
            nome_arquivo = os.path.basename(nome_arquivo)
            kind = (campos.get("kind", (None, b"pdf"))[1] or b"pdf").decode("utf-8", "ignore")

            if kind == "cert":
                senha = (campos.get("senha", (None, b""))[1] or b"").decode("utf-8", "ignore")
                caminho = os.path.join(CERTS, nome_arquivo)
                with open(caminho, "wb") as f:
                    f.write(conteudo)
                titular, cnpj, validade, extras = ler_cnpj_certificado(conteudo, senha)
                # `caminho` permite ao Clientes registrar o MESMO cert como conta
                # dos módulos NFS-e/NF-e (unificação), sem reenviar o arquivo.
                return self._json({"ok": True, "arquivo": nome_arquivo, "caminho": caminho,
                                   "titular": titular, "cnpj": cnpj, "validade": validade,
                                   "leu": bool(titular or cnpj), "temCrypto": TEM_CRYPTO,
                                   **extras})
            else:
                with open(os.path.join(UPLOADS, nome_arquivo), "wb") as f:
                    f.write(conteudo)
                return self._json({"ok": True, "arquivo": nome_arquivo, "url": f"/dados/uploads/{nome_arquivo}"})

        return self._json({"erro": "rota desconhecida"}, 404)


class Servidor(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def abrir_navegador():
    webbrowser.open(f"http://127.0.0.1:{PORT}/home_portal.html")


_TRAVA = []


def instancia_unica() -> bool:
    """False se já existe um Fiscale rodando nesta porta.

    Sem isto, cada clique no atalho sobe OUTRO servidor: no Windows o
    SO_REUSEADDR deixa vários processos ligarem na mesma porta, então eles se
    acumulam sem aparecer, e o "Encerrar" fecha só um — o usuário fica achando
    que o sistema não fecha. A trava é por porta, para as instâncias de teste
    (FISCALE_PORT diferente) continuarem podendo subir junto."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        ERROR_ALREADY_EXISTS = 183
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = wintypes.HANDLE
        h = k32.CreateMutexW(None, False, f"FiscaleServidor-{PORT}")
        if not h:
            return True                      # não deu para travar: deixa subir
        if k32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _TRAVA.append(h)                     # segura o handle enquanto o app viver
        return True
    except Exception:
        return True


# ─── Ícone ao lado do relógio ───────────────────────────────────────────────
# Sem janela de console, este ícone é o único jeito de reabrir e de encerrar
# o Fiscale. Se o pystray falhar, o servidor continua rodando normalmente.
def _icone_bandeja():
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None
    img = None
    for nome in ("fiscale.ico", os.path.join("web", "icone.webp")):
        caminho = os.path.join(BASE, nome)
        try:
            img = Image.open(caminho).convert("RGBA")
            break
        except Exception:
            continue
    if img is None:
        img = Image.new("RGBA", (64, 64), (16, 68, 78, 255))

    def _abrir(icone=None, item=None):
        abrir_navegador()

    def _pasta(icone=None, item=None):
        try:
            os.startfile(DADOS)
        except Exception:
            pass

    def _sair(icone, item=None):
        icone.visible = False
        icone.stop()
        registrar_encerramento(MOTIVO_BANDEJA, "ícone ao lado do relógio")
        # O módulo NFS-e é processo-filho e NÃO morre junto: no Windows não há
        # grupo de processos herdado, e `os._exit()` ainda pula o `atexit`.
        # Sem esta chamada ele fica órfão, escutando a porta interna.
        parar_modulo_nfse()
        os._exit(0)      # derruba o servidor sem esperar threads

    menu = pystray.Menu(
        pystray.MenuItem("Abrir o Fiscale", _abrir, default=True),
        pystray.MenuItem("Pasta de dados", _pasta),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Encerrar", _sair),
    )
    return pystray.Icon("fiscale", img, f"Fiscale — http://127.0.0.1:{PORT}", menu)


def _ip_da_rede():
    """IP desta máquina na rede local (para acesso de outros computadores)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main():
    # Já tem um Fiscale nesta porta? Então só reabre a tela dele e sai — em vez
    # de empilhar mais um servidor invisível.
    if not instancia_unica():
        print(f"Já existe um Fiscale rodando na porta {PORT}; reabrindo a tela dele.")
        _ciclo("SUBIDA_RECUSADA", porta=PORT, motivo="JA_HA_INSTANCIA_NESTA_PORTA")
        abrir_navegador()
        return
    # Antes de qualquer coisa: a execução anterior conseguiu se despedir?
    _relatar_execucao_anterior()
    os.chdir(WEB)

    # Primeiro uso nesta máquina: deixa o atalho na Área de Trabalho. Quem
    # recebeu o portátil extraiu um ZIP e ficou com uma pasta — sem atalho, o
    # caminho de volta amanhã é lembrar onde ela está. Falha aqui não impede
    # o Fiscale de abrir: é conveniência, não requisito.
    _atalho = atalho_desktop.garantir(DADOS)
    if _atalho.get("criado"):
        print(f"  Atalho criado :  {_atalho['caminho']}")

    iniciar_modulo_nfse()   # o admin do 1º uso é criado pela tela de login

    # Thread que drena a fila de sincronização com o Elo. Daemon e
    # silenciosa: Elo fora do ar não aparece na tela de quem trabalha.
    if TEM_ELO_SYNC and TEM_ELO:
        fiscale_elo_sync.iniciar_worker(DADOS, lambda: fiscale_elo.configuracao(DADOS))
    # Padrão 0.0.0.0: aceita acesso de outras máquinas da rede (o login
    # protege tudo). Com FISCALE_BIND=127.0.0.1, só esta máquina — que é o
    # arranjo do dia em que o Caddy estiver na frente.
    bind = endereco_de_escuta()
    httpd = Servidor((bind, PORT), Handler)
    ip = None if escuta_so_local(bind) else _ip_da_rede()
    _ciclo("INICIO", pid=os.getpid(), porta=PORT, bind=bind, dados=DADOS,
           nfse=("ATIVO" if NFSE_ATIVO else "INDISPONIVEL"),
           porta_nfse=(NFSE_PORT if NFSE_ATIVO else None),
           bandeja=("nao" if os.environ.get("FISCALE_SEM_BANDEJA") == "1" else "sim"))
    _marcar_execucao()
    # Fecha o registro quando o processo termina NORMALMENTE. Morte externa
    # não passa por aqui — e é por isso que existe o marcador acima.
    atexit.register(lambda: registrar_encerramento(MOTIVO_DESCONHECIDO,
                                                   "saída sem motivo declarado"))
    print("=" * 54)
    print("  FISCALE  —  sistema integrado do setor fiscal")
    print("=" * 54)
    print(f"  Nesta máquina :  http://127.0.0.1:{PORT}")
    if ip:
        print(f"  Na rede local :  http://{ip}:{PORT}   (outras máquinas)")
    else:
        print(f"  Na rede local :  fechado (FISCALE_BIND={bind})")
    print(f"  Dados em      :  {DADOS}")
    print(f"  NFS-e         :  módulo {'ATIVO' if NFSE_ATIVO else 'indisponível'}"
          f" (porta interna {NFSE_PORT})")
    if sem_usuarios():
        print("  PRIMEIRO USO  :  crie a senha do admin na tela que abriu no navegador.")
    print("  Para encerrar: ícone do Fiscale ao lado do relógio > Encerrar.")
    print("=" * 54)
    if os.environ.get("FISCALE_NO_BROWSER") != "1":
        threading.Timer(1.0, abrir_navegador).start()

    icone = None if os.environ.get("FISCALE_SEM_BANDEJA") == "1" else _icone_bandeja()
    if icone is None:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            registrar_encerramento(MOTIVO_TECLADO, "Ctrl+C")
            print("\nEncerrado.")
        return
    # No Windows o ícone da bandeja exige a thread principal; o servidor vai p/ outra.
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        icone.run()
    except KeyboardInterrupt:
        registrar_encerramento(MOTIVO_TECLADO, "Ctrl+C")
    print("Encerrado.")


if __name__ == "__main__":
    main()
