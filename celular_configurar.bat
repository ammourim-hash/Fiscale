@echo off
chcp 65001 >nul
title Fiscale - acesso pelo celular fora do escritorio
cd /d "%~dp0"
rem ---------------------------------------------------------------------------
rem Instala/liga o Tailscale (rede privada) e libera a porta 8777 SO para ela.
rem Mostra o que vai fazer e PERGUNTA antes. Pede administrador sozinho.
rem Previa sem alterar nada (nao pede administrador):  celular_configurar.bat -Simular
rem ---------------------------------------------------------------------------
if /i not "%~1"=="-Simular" (
  net session >nul 2>&1 || (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
  )
)
rem Le o .ps1 como UTF-8 explicitamente: acentos certos com ou sem BOM.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Get-Content -Raw -Encoding UTF8 -LiteralPath '%~dp0celular_configurar.ps1'))) %*"
echo.
pause
