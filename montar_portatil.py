#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
montar_portatil.py — monta o FISCALE-Portable: roda sem Python instalado.

    python montar_portatil.py                     monta em dist_portatil\\
    python montar_portatil.py D:\\saida            monta onde você mandar
    python montar_portatil.py --zip               e ainda gera o .zip
    python montar_portatil.py --cache C:\\baixados usa downloads já feitos

O QUE ELE MONTA

    FISCALE-Portable\\
        Fiscale.bat        duplo clique — é só isto que o usuário abre
        runtime\\           Python 3.12 oficial, assinado pela PSF
        libs\\              os 11 pacotes, instalados PELO pip deste runtime
        app\\               o código do Fiscale
        dados\\             vazia, com a marca .fiscale-portatil
        LEIA-ME.txt

POR QUE *EMBEDDABLE* E NÃO PyInstaller
    O Smart App Control desta máquina bloqueia .exe sem assinatura, e um
    PyInstaller nosso não é assinado. O `python.exe` do pacote oficial da
    python.org é assinado pela Python Software Foundation — passa. E atualizar
    o Fiscale volta a ser copiar arquivos .py, sem rebuild.

AS TRÊS ARMADILHAS DO EMBEDDABLE, E COMO SÃO TRATADAS
    1. Vem sem pip.  → baixamos o get-pip e rodamos com o próprio runtime.
    2. O `._pth` limita o sys.path e desliga o `site`.  → reescrevemos o
       arquivo habilitando `import site` e apontando `libs` e `app`.
    3. Pacote com código nativo (cryptography, Pillow) precisa casar com a
       versão e a arquitetura exatas.  → instalamos com o pip DESTE runtime,
       nunca copiando de outro lugar.

NÃO COPIA a .venv atual: ela é um atalho para o Python desta máquina.
NÃO COPIA dados, .pfx, .db, logs, build, dist, __pycache__ nem o ELO.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

PY_VERSAO = "3.12.10"
PY_ZIP = f"python-{PY_VERSAO}-embed-amd64.zip"
URL_PY = f"https://www.python.org/ftp/python/{PY_VERSAO}/{PY_ZIP}"
URL_GETPIP = "https://bootstrap.pypa.io/get-pip.py"

# Os 11 pacotes que o código realmente importa (ver inventario_port4.py).
# Versões fixas: distribuição que resolve dependência sozinha na hora do build
# muda de conteúdo sem ninguém pedir.
PACOTES = [
    "fastapi==0.115.6", "uvicorn==0.34.0", "pydantic==2.13.4",
    "requests==2.32.3", "requests-pkcs12==1.25", "cryptography==44.0.0",
    "fpdf2==2.8.7", "qrcode==8.2", "pillow==12.3.0", "pystray==0.19.5",
]

# O que do projeto vai para dentro do pacote.
COPIAR_ARQUIVOS = [
    "fiscale_server.py", "fiscale_dados.py", "fiscale_migracao.py",
    "fiscale_segredos.py", "fiscale_inscricoes.py",
    "fiscale_backup.py", "fiscale_sessoes.py",
    "fiscale_papeis.py",
    # Portal, Fase 3: o que muda quando a requisição chega por um proxy
    # HTTPS. O `fiscale_server` importa no topo — sem ele o portátil não
    # abre — e é ele que decide "está no próprio PC" e "está em HTTPS".
    # Essas duas respostas guardam criar o primeiro admin, restaurar
    # backup e encerrar o servidor.
    "fiscale_proxy.py",
    # Onde ficam, de verdade, as pastas do usuário (OneDrive move a Área de
    # Trabalho). O `fiscale_server` importa na inicialização: sem ele o
    # portátil nem abre.
    "pastas_windows.py",
    # Cria o atalho na Área de Trabalho no primeiro uso. Sem ele, quem
    # extraiu o ZIP fica sem caminho de volta amanhã.
    "atalho_desktop.py",
    # Gravação atômica compartilhada e o cadastro de usuários (Fase 1 do
    # login). O `fiscale_server` importa os dois na inicialização.
    "fiscale_arquivo.py",
    "fiscale_usuarios.py",
    "migrar_login_email.py",
    # Fase 2 do login: política de senha e limite de tentativas.
    "fiscale_senhas.py",
    "backup_drive.py",
    "fiscale_auditoria.py",
    "fiscale_cadastro.py",
    "fiscale_pimenta.py",
    "fiscale_tentativas.py",
    "fiscale_elo.py", "fiscale_elo_sync.py",
    # Decisões administrativas da ingestão NF-e (18/09/2026). O servidor
    # importa nas rotas `/api/nfe/admin/*`; ficou fora desta lista quando
    # entrou, e `teste_atalho_desktop` acusou a ausência (19/09/2026).
    "fiscale_decisoes_nfe.py",
    "cnpj_publico.py", "ncm_publico.py",
    # A varredura de Optantes e a regra white-label que ela compartilha com o
    # dossiê da Situação Fiscal. `relatorio_base` é importado pelos DOIS: sem
    # ele no pacote, `situacao/relatorio.py` quebra junto.
    "optantes.py", "relatorio_base.py",
    "diagnostico_instalacao.py", "inventario_port4.py",
    "exportar_backup.py", "restaurar_backup.py",
    "fiscale.ico",
    # Configura o acesso ao Integra Contador. Vai no pacote porque a
    # credencial e POR MAQUINA (DPAPI): quem levar o portatil para outro
    # computador precisa configurar la, e sem este arquivo teria de faze-lo
    # pela linha de comando -- que e exatamente o que ele existe para evitar.
    #
    # NOTA: `conferir_lista()` le imports de .py e nao enxerga .bat. Este
    # arquivo passou despercebido por ela na primeira montagem, e so apareceu
    # na conferencia manual do pacote.
    "configurar_serpro.bat",
    # Configuração de rede do escritório. Só valem quando MAIS de uma pessoa
    # vai usar; quem roda sozinho não precisa de nenhum deles.
    "rede_diagnostico.ps1", "rede_diagnostico.bat",
    "rede_configurar.ps1", "rede_configurar.bat",
]
# PACOTES DE DIRETORIO ENTRAM AQUI, e nao na lista de arquivos soltos.
#     `conferir_lista()` compara a lista de ARQUIVOS: um pacote novo
#     passa despercebido por ela e simplesmente nao viaja no portatil.
#     Foi o que quase aconteceu com `situacao/` — que so seria notado no
#     dia em que a tela dele abrisse quebrada numa maquina sem projeto.
COPIAR_PASTAS = ["web", "situacao",
                 # Fase Contábil 1: o servidor importa `contabil.rotas`.
                 "contabil"]
COPIAR_NFSE = ["runner.py", "requirements.txt"]     # dentro de nfse/

# `testar_fiscale.bat` NÃO entra: ele chama `nfse\.venv\Scripts\python.exe`,
# que só existe na máquina de desenvolvimento. No pacote portátil o Python é
# o `runtime\`, e o arquivo daria erro logo na primeira linha.

# Nunca entram.
FORA_PASTAS = {"__pycache__", ".venv", "build", "dist", "node_modules",
               ".next", ".git", "dados", "elo", "amostras"}
FORA_SUFIXOS = {".pyc", ".pyo", ".log", ".db", ".pfx", ".p12", ".fbk", ".bak",
                ".bak", ".tmp"}


def conferir_lista(raiz=None) -> list[str]:
    """Todo módulo que o pacote importa está na lista de cópia?

    POR QUE ISTO EXISTE
        `COPIAR_ARQUIVOS` é lista de permissão, e lista de permissão que
        ninguém atualiza é bomba-relógio: em 22/08/2026 o `fiscale_papeis.py`
        entrou no projeto — passou a ser importado pelo `fiscale_server.py` na
        primeira linha do controle de acesso — e ninguém o acrescentou aqui.

        O pacote teria sido montado sem reclamar, o ZIP teria ido para a mão de
        outra pessoa, e o Fiscale morreria no arranque com `ModuleNotFoundError`
        numa máquina onde ninguém consegue depurar.

        A conferência roda ANTES de baixar qualquer coisa: falha em dois
        segundos, e não depois de dez minutos de download.

    COMO
        Lê por AST os `import` de cada arquivo que vai para o pacote e cobra
        que todo módulo local (um `.py` irmão, no projeto) esteja na lista.
        Não segue import de biblioteca: esses são resolvidos por `PACOTES`.
    """
    import ast
    raiz = Path(raiz) if raiz else RAIZ
    na_lista = {n[:-3] for n in COPIAR_ARQUIVOS if n.endswith(".py")}
    faltando: dict[str, set[str]] = {}

    for nome in COPIAR_ARQUIVOS:
        if not nome.endswith(".py"):
            continue
        arq = raiz / nome
        if not arq.exists():
            continue
        try:
            arvore = ast.parse(arq.read_text("utf-8", "ignore"))
        except SyntaxError:
            continue
        for no in ast.walk(arvore):
            alvos = []
            if isinstance(no, ast.Import):
                alvos = [a.name.split(".")[0] for a in no.names]
            elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
                alvos = [no.module.split(".")[0]]
            for mod in alvos:
                # Local = existe um .py irmão com esse nome no projeto.
                if mod in na_lista or not (raiz / f"{mod}.py").exists():
                    continue
                faltando.setdefault(mod, set()).add(nome)

    return [f"{mod}.py — importado por {', '.join(sorted(quem))}"
            for mod, quem in sorted(faltando.items())]


def _log(msg):
    print(f"  {msg}", flush=True)


def _mb(n):
    return f"{n / 1048576:.1f} MB"


def _tamanho(p: Path) -> int:
    return sum(x.stat().st_size for x in p.rglob("*") if x.is_file())


def _baixar(url: str, destino: Path, cache: Path | None) -> Path:
    if cache:
        pronto = cache / destino.name
        if pronto.exists():
            _log(f"usando do cache: {pronto.name} ({_mb(pronto.stat().st_size)})")
            shutil.copy2(pronto, destino)
            return destino
    _log(f"baixando {url}")
    with urllib.request.urlopen(url, timeout=180) as r, open(destino, "wb") as f:
        shutil.copyfileobj(r, f)
    _log(f"  {destino.name}: {_mb(destino.stat().st_size)}")
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destino, cache / destino.name)
    return destino


def _copiar_arvore(origem: Path, destino: Path) -> int:
    n = 0
    for p in origem.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(origem)
        if any(x in FORA_PASTAS for x in rel.parts[:-1]) or p.suffix in FORA_SUFIXOS:
            continue
        alvo = destino / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, alvo)
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════════════
def montar(saida: Path, cache: Path | None = None, fazer_zip: bool = False) -> dict:
    # Antes de qualquer download: o pacote leva tudo o que ele importa?
    # Falhar aqui custa dois segundos. Falhar na máquina do cliente custa
    # um Fiscale que não abre e ninguém sabe por quê.
    if (faltam := conferir_lista()):
        raise SystemExit(
            "O pacote ficaria sem módulo que ele mesmo importa:\n  "
            + "\n  ".join(faltam)
            + "\n\nAcrescente em COPIAR_ARQUIVOS, em montar_portatil.py.")

    pacote = saida / "FISCALE-Portable"
    if pacote.exists():
        _log(f"limpando {pacote}")
        shutil.rmtree(pacote)
    pacote.mkdir(parents=True)
    runtime = pacote / "runtime"
    libs = pacote / "libs"
    app = pacote / "app"
    temp = saida / "_baixados"
    temp.mkdir(parents=True, exist_ok=True)

    # ── 1. runtime ────────────────────────────────────────────────────────
    print("\n[1/6] Python embutido")
    zip_py = _baixar(URL_PY, temp / PY_ZIP, cache)
    with zipfile.ZipFile(zip_py) as z:
        z.extractall(runtime)
    py = runtime / "python.exe"
    if not py.exists():
        raise RuntimeError("O pacote do Python não trouxe python.exe.")
    _log(f"extraído em runtime\\ ({_mb(_tamanho(runtime))})")

    # ── 2. destravar o sys.path ───────────────────────────────────────────
    # Sem isto o runtime não enxerga NADA fora do próprio zip: nem os pacotes,
    # nem o código do Fiscale. É a armadilha nº 2 do embeddable.
    print("\n[2/6] liberando o caminho de importação")
    pth = next(runtime.glob("python*._pth"), None)
    if not pth:
        raise RuntimeError("Não achei o arquivo ._pth do runtime.")
    pth.write_text(
        f"{pth.stem}.zip\n"
        ".\n"
        "..\\libs\n"
        "..\\app\n"
        "..\\app\\nfse\n"
        "..\\app\\nfse\\backend\n"
        "\n"
        "# O Fiscale precisa do site para o pip e para os pacotes instalados.\n"
        "import site\n", encoding="utf-8")
    _log(f"{pth.name} reescrito com libs, app e 'import site'")

    # ── 3. pip do PRÓPRIO runtime ─────────────────────────────────────────
    print("\n[3/6] instalando o pip neste runtime")
    getpip = _baixar(URL_GETPIP, temp / "get-pip.py", cache)
    r = subprocess.run([str(py), str(getpip), "--no-warn-script-location"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"get-pip falhou:\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
    _log("pip instalado")

    # ── 4. pacotes ────────────────────────────────────────────────────────
    print(f"\n[4/6] instalando os {len(PACOTES)} pacotes em libs\\")
    _log("(com o pip DESTE runtime — é o que faz o código nativo casar)")
    libs.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(py), "-m", "pip", "install", "--no-warn-script-location",
                        "--no-compile", "--target", str(libs), *PACOTES],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"pip install falhou:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    for lixo in libs.glob("*.dist-info"):
        for x in ("RECORD", "INSTALLER", "REQUESTED"):
            (lixo / x).unlink(missing_ok=True)
    _log(f"libs\\ = {_mb(_tamanho(libs))}")

    # ── 5. o Fiscale ──────────────────────────────────────────────────────
    print("\n[5/6] copiando o Fiscale")
    app.mkdir(parents=True, exist_ok=True)
    n = 0
    for nome in COPIAR_ARQUIVOS:
        o = RAIZ / nome
        if o.exists():
            shutil.copy2(o, app / nome)
            n += 1
    for nome in COPIAR_PASTAS:
        o = RAIZ / nome
        if o.is_dir():
            n += _copiar_arvore(o, app / nome)
    (app / "nfse").mkdir(parents=True, exist_ok=True)
    for nome in COPIAR_NFSE:
        o = RAIZ / "nfse" / nome
        if o.exists():
            shutil.copy2(o, app / "nfse" / nome)
            n += 1
    n += _copiar_arvore(RAIZ / "nfse" / "backend", app / "nfse" / "backend")
    # A INTERFACE DO MÓDULO NFS-e. Ela não é a mesma coisa que `web/`: o
    # módulo tem tela própria, servida pelo `main.py` dele em
    # `nfse/frontend/index.html`. Ficou de fora do pacote até 20/08/2026, e o
    # sintoma era um "Internal Server Error" ao abrir NFS-e — `FileNotFoundError`
    # no `read_text` do index, num traceback de 40 linhas de FastAPI que não
    # dizia que faltava uma PASTA. Sem ela o módulo sobe e não serve nada.
    n += _copiar_arvore(RAIZ / "nfse" / "frontend", app / "nfse" / "frontend")
    # A documentação interna NÃO vai no pacote. Ela é o diário de engenharia
    # do projeto — decisões, diagnósticos, relatórios de fase — e cita CNPJ de
    # clientes reais. Uma varredura desta fase encontrou 9 ocorrências em 8
    # arquivos `.md`. Não é conteúdo de usuário e não tem por que viajar num
    # ZIP que vai para a mão de outra pessoa.
    #
    # Já aconteceu antes de um `.md` levar segredo para fora (ver
    # FISCALE_SEGREDOS.md): a lição é não distribuir o que descreve a operação.
    # O que o usuário precisa ler é o LEIA-ME.txt, gerado logo abaixo.
    _log(f"{n} arquivos ({_mb(_tamanho(app))})")

    # ── 6. o que o usuário vê ─────────────────────────────────────────────
    print("\n[6/6] iniciador e marca de portátil")
    dados = pacote / "dados"
    dados.mkdir(parents=True, exist_ok=True)
    # É esta marca que faz o Fiscale guardar os dados AO LADO do programa, em
    # vez de em C:\Users\<voce>\Fiscale. Sem ela, o pendrive não seria portátil.
    (dados / ".fiscale-portatil").write_text(
        "Esta marca faz o Fiscale guardar os dados nesta pasta,\n"
        "ao lado do programa. Não apague.\n", encoding="utf-8")

    (pacote / "Fiscale.bat").write_text(_INICIADOR, encoding="utf-8")
    (pacote / "Diagnostico.bat").write_text(_DIAGNOSTICO, encoding="utf-8")
    (pacote / "LEIA-ME.txt").write_text(_LEIAME, encoding="utf-8")
    _log("Fiscale.bat, Diagnostico.bat, LEIA-ME.txt, dados\\.fiscale-portatil")

    total = _tamanho(pacote)
    resultado = {
        "pasta": str(pacote),
        "bytes": total,
        "runtime": _tamanho(runtime),
        "libs": _tamanho(libs),
        "app": _tamanho(app),
        "arquivos": sum(1 for x in pacote.rglob("*") if x.is_file()),
    }

    if fazer_zip:
        print("\n[extra] compactando")
        alvo = saida / "FISCALE-Portable.zip"
        if alvo.exists():
            alvo.unlink()
        with zipfile.ZipFile(alvo, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in pacote.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(pacote.parent).as_posix())
        resultado["zip"] = str(alvo)
        resultado["zip_bytes"] = alvo.stat().st_size
        _log(f"{alvo.name}: {_mb(alvo.stat().st_size)}")

    return resultado


_INICIADOR = r"""@echo off
chcp 65001 >nul
title Fiscale
cd /d "%~dp0"

rem ===========================================================================
rem  Abre o Fiscale. Nao precisa de Python instalado: ele vem na pasta runtime.
rem
rem  FISCALE_DADOS nao e definido aqui de proposito. Quem decide e o proprio
rem  Fiscale: existindo a marca dados\.fiscale-portatil, ele guarda tudo AO
rem  LADO do programa — e ai a pasta inteira cabe num pendrive.
rem ===========================================================================

if not exist "runtime\python.exe" (
  echo.
  echo  A pasta "runtime" nao esta aqui.
  echo  Copie a pasta FISCALE-Portable INTEIRA, nao so alguns arquivos.
  echo.
  pause
  exit /b 1
)

start "" "runtime\pythonw.exe" "app\fiscale_server.py"
"""

_DIAGNOSTICO = r"""@echo off
chcp 65001 >nul
title Fiscale — diagnostico da instalacao
cd /d "%~dp0"
if not exist "runtime\python.exe" (
  echo  A pasta "runtime" nao esta aqui. Copie a pasta INTEIRA.
  pause
  exit /b 1
)
"runtime\python.exe" "app\diagnostico_instalacao.py"
echo.
pause
"""

_LEIAME = """====================================================================
  FISCALE — versao portatil
====================================================================

COMO ABRIR
  De dois cliques em:  Fiscale.bat
  O navegador abre sozinho. Na primeira vez voce cria a senha do admin.

  Nao precisa instalar Python, nem nada. Esta tudo nesta pasta.

ONDE FICAM OS SEUS DADOS
  Na pasta "dados", aqui dentro. Levar esta pasta para outro computador
  leva o sistema E os dados junto — inclusive num pendrive.

TRAZER UMA INSTALACAO ANTERIOR
  1. Na maquina antiga: Fiscale > Home > Backup > Criar backup completo.
     Guarde o arquivo .fbk e a frase-senha que voce digitou.
  2. Aqui: abra o Fiscale, crie a senha do admin, va em Home > Backup >
     "Restaurar instalacao anterior", aponte o .fbk e informe a frase.
  3. As empresas aparecem em Clientes como "Aguardando senha". Informe a
     senha so dos certificados que for usar.
     NENHUMA empresa precisa ser recadastrada.

  A senha de cada certificado NAO viaja no backup: o Windows a guarda de
  um jeito que so funciona no computador onde ela foi digitada. E protecao
  dele, nao defeito do Fiscale.

SE ALGO NAO ABRIR
  De dois cliques em:  Diagnostico.bat
  Ele diz, em portugues, o que esta faltando e o que fazer.

O QUE NAO COPIAR PELA METADE
  Copie a pasta FISCALE-Portable INTEIRA. As pastas runtime e libs sao o
  motor: sem elas nada abre.
====================================================================
"""


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    saida = Path(args[0]) if args else (RAIZ / "dist_portatil")
    cache = None
    if "--cache" in argv:
        i = argv.index("--cache")
        if i + 1 < len(argv):
            cache = Path(argv[i + 1])
    saida.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print("  FISCALE — montando a distribuição portátil")
    print("=" * 68)
    print(f"  saída: {saida}")
    try:
        r = montar(saida, cache, "--zip" in argv)
    except Exception as e:
        print(f"\n  ERRO: {e}")
        return 2
    print("\n" + "=" * 68)
    print("  PRONTO")
    print(f"    {r['pasta']}")
    print(f"    {r['arquivos']} arquivos · {_mb(r['bytes'])}")
    print(f"      runtime {_mb(r['runtime'])} · libs {_mb(r['libs'])} · app {_mb(r['app'])}")
    if r.get("zip"):
        print(f"    {r['zip']} · {_mb(r['zip_bytes'])}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))
