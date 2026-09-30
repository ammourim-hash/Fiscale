@echo off
chcp 65001 >nul
title Fiscale — atualizar a partir do Google Drive
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem RECEBE a versao nova do Fiscale que veio do Drive.
rem Rode este na maquina do ESCRITORIO, depois de ter rodado o
rem backup_para_drive.bat na maquina onde voce faz as alteracoes.
rem
rem NUNCA toca em "dados": as senhas dos certificados sao cifradas com DPAPI e
rem so funcionam na maquina que as gravou (ver nfse\backend\seguranca.py). Alem
rem disso, os dados reais moram so aqui — sobrescrever apagaria trabalho.
rem
rem Tambem nao traz .venv (tem caminho da outra maquina), build e dist.
rem ---------------------------------------------------------------------------

set "ORIGEM=G:\Meu Drive\Amorim\FISCALE 2026"

if not exist "G:\" (
  echo.
  echo  O Google Drive ^(unidade G:^) nao esta montado.
  echo  Abra o Google Drive para computador e tente de novo.
  echo.
  pause
  exit /b 1
)

if not exist "%ORIGEM%\fiscale_server.py" (
  echo.
  echo  Nao encontrei o Fiscale em:
  echo  %ORIGEM%
  echo.
  echo  Confira se o Drive terminou de baixar os arquivos.
  echo.
  pause
  exit /b 1
)

echo.
echo  ================================================================
echo   ANTES DE CONTINUAR: feche o Fiscale.
echo   Com ele aberto, os arquivos em uso nao podem ser substituidos.
echo  ================================================================
echo.
choice /C SN /M "O Fiscale esta fechado? Continuar"
if errorlevel 2 exit /b 0

echo.
echo  Atualizando a partir do Drive...
echo.

robocopy "%ORIGEM%" "%CD%" /E /R:2 /W:2 /NFL /NDL /NJH ^
  /XD "dados" ".venv" "build" "dist" "__pycache__" ".git" "fonte-2026-08-04" ^
      "node_modules" ".next" ".turbo" ".storage" "coverage" ^
  /XF "*.pyc" "*.log" "*.zip" ".env" ".env.*" "*.key" "*.pem" "*.pfx" ^
      "elo_config.json" "*.db" "*.db-shm" "*.db-wal"

if %ERRORLEVEL% GEQ 8 (
  echo.
  echo  [ERRO] A atualizacao nao terminou. Codigo: %ERRORLEVEL%
  echo  O Fiscale pode estar aberto. Feche e rode de novo.
  echo.
  pause
  exit /b 1
)

rem ── Dependencias do SERVIDOR ────────────────────────────────────────────
rem Sem isto, o Fiscale abre mas funcoes novas ficam mudas. Foi o que
rem aconteceu na primeira tentativa em outra maquina: os .py viajaram no
rem backup, a biblioteca nao.
if exist "requirements-servidor.txt" (
  echo.
  echo  Conferindo as dependencias do servidor...
  set "PY="
  where python >nul 2>nul && set "PY=python"
  if not defined PY ( where py >nul 2>nul && set "PY=py" )
  if defined PY (
    %PY% -m pip install -q -r requirements-servidor.txt
    if errorlevel 1 (
      echo  [!] Nao consegui instalar as dependencias do servidor.
      echo      Rode manualmente:
      echo      python -m pip install -r requirements-servidor.txt
    ) else (
      echo  [ok] Dependencias do servidor conferidas.
    )
  ) else (
    echo  [!] Python nao encontrado no PATH — pule esta etapa e instale depois.
  )
)

echo.
echo  ================================================================
echo   Atualizado. Pode abrir o Fiscale.
echo.
echo   Seus dados nao foram tocados: notas, certificados e as
echo   configuracoes das empresas continuam como estavam.
echo  ================================================================
echo.
pause
