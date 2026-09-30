# -*- mode: python ; coding: utf-8 -*-
# Build do Fiscale como executável único (.exe):
#   nfse\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm fiscale.spec
# Resultado: dist\Fiscale.exe  (não precisa de Python instalado)
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# O módulo NFS-e é importado dinamicamente (em thread) — por isso listamos seus
# módulos e dependências como imports ocultos, senão o PyInstaller não os inclui.
hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("pydantic")
    + collect_submodules("fpdf")
    + collect_submodules("qrcode")
    + ["main", "core", "nfe", "auditor_nfe", "inscricoes", "painel", "prefeituras", "recife", "classificador", "seguranca", "pdflocal",
       "cnpj_publico", "ncm_publico",
       "requests", "requests_pkcs12", "cryptography", "PIL.Image",
       "anyio", "click", "h11",
       # ícone ao lado do relógio (sem console, é o jeito de abrir/encerrar)
       "pystray", "pystray._win32"]
)

datas = [
    ("web", "web"),                                   # todas as telas do Fiscale
    ("nfse/frontend/index.html", "frontend"),         # interface do módulo NFS-e
    ("nfse/frontend/nfse.webp", "frontend"),
    ("nfse/frontend/danfse_logo.png", "frontend"),
    ("nfse/backend/municipios.json", "."),            # tabela IBGE (PDF resumo)
] + collect_data_files("fpdf")

a = Analysis(
    ["fiscale_server.py"],
    pathex=["nfse/backend"],   # torna main/core/seguranca/pdflocal importáveis
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Fiscale",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # sem janela de CMD: usa o ícone ao lado do relógio
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="fiscale.ico",
)
