@echo off
chcp 65001 >nul
title Fiscale
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (
  where py >nul 2>nul
  if %errorlevel%==0 (set "PY=py") else (goto sempython)
)

rem ── Modulo NFS-e: prepara as dependencias uma unica vez ────────────────
if exist "nfse\runner.py" (
  if not exist "nfse\.venv" (
    echo Preparando o modulo NFS-e ^(so na primeira vez, 1-2 minutos^)...
    %PY% -m venv nfse\.venv
    nfse\.venv\Scripts\python.exe -m pip install --upgrade pip -q
    nfse\.venv\Scripts\pip.exe install -r nfse\requirements.txt -q
  )
)

%PY% fiscale_server.py
goto fim

:sempython
echo.
echo  ================================================================
echo   O Python nao foi encontrado neste computador.
echo   Instale o Python 3.10 ou superior em: https://www.python.org/downloads/
echo   (Na instalacao, marque a opcao "Add Python to PATH".)
echo  ================================================================
echo.
pause
:fim
