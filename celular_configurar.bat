@echo off
chcp 65001 >nul
title Fiscale - acesso pelo celular fora do escritorio
cd /d "%~dp0"
rem ---------------------------------------------------------------------------
rem Instala/liga o Tailscale (rede privada) e libera a porta 8777 SO para ela.
rem Mostra o que vai fazer e PERGUNTA antes. Pede administrador sozinho.
rem Previa sem alterar nada:  powershell -File celular_configurar.ps1 -Simular
rem ---------------------------------------------------------------------------
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0celular_configurar.ps1" %*
echo.
pause
