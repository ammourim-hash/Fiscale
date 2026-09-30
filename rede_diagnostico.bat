@echo off
chcp 65001 >nul
title Fiscale - diagnostico da rede
cd /d "%~dp0"
rem ---------------------------------------------------------------------------
rem So OLHA e conta o que encontrou. Nao altera nada, nao precisa de
rem administrador, e pode rodar com o Fiscale aberto.
rem ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rede_diagnostico.ps1"
echo.
pause
