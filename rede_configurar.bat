@echo off
chcp 65001 >nul
title Fiscale - configurar o servidor do escritorio
cd /d "%~dp0"
rem ---------------------------------------------------------------------------
rem ALTERA configuracoes do Windows (firewall, perfil de rede, energia e uma
rem tarefa agendada). Mostra tudo o que pretende fazer e PERGUNTA antes.
rem
rem Precisa de administrador: clique com o botao direito neste arquivo e
rem escolha "Executar como administrador".
rem ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rede_configurar.ps1"
echo.
pause
