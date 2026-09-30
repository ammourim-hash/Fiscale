@echo off
chcp 65001 >nul
title Fiscale — enviar para o Google Drive
cd /d "%~dp0"

rem ===========================================================================
rem  O QUE ESTE ARQUIVO FAZ (e o que ele NAO faz)
rem
rem  Ele manda para o Drive duas coisas, e nada alem disso:
rem
rem    1. O CODIGO do Fiscale, para a outra maquina receber a versao nova
rem       pelo atualizar_do_drive.bat.
rem    2. Os arquivos .fbk que estiverem na Area de Trabalho — o backup
rem       oficial do Fiscale, criado em Home > Backup.
rem
rem  Ele NAO faz backup dos seus dados. Backup e o .fbk.
rem
rem  POR QUE MUDOU (PORT 3, 11/08/2026)
rem    Ate aqui este arquivo copiava a pasta "dados" do projeto junto com o
rem    codigo. Isso dava dois problemas:
rem
rem      - FALSA SENSACAO DE BACKUP. Desde a PORT 1 os dados de verdade moram
rem        em C:\Users\<voce>\Fiscale\dados, FORA desta pasta. O que subia para
rem        o Drive era uma copia velha de julho com meia duzia de arquivos —
rem        quem confiasse nisso como backup perderia tudo.
rem      - DADO PESSOAL NA NUVEM. Aquele state_plano.json antigo levava CPF de
rem        pessoas fisicas para o Google Drive, sem necessidade nenhuma.
rem
rem    Agora existe UM mecanismo de backup: o .fbk. Este arquivo so distribui
rem    codigo e transporta o .fbk.
rem
rem  /MIR faria espelho e APAGARIA no destino o que nao existe aqui. Nao usamos:
rem  o Drive tem arquivos antigos (zip, ESTADO DO PROJETO) que devem ficar.
rem ===========================================================================

rem  O destino pode ser trocado por variavel de ambiente. Existe para poder
rem  EXERCITAR este arquivo inteiro sem escrever no Drive de verdade - sem
rem  isso, a unica forma de testar o .bat seria mandando arquivo para a nuvem.
rem  O backup_drive.py le a mesma variavel.
if defined FISCALE_BACKUP_DESTINO (
  set "DESTINO=%FISCALE_BACKUP_DESTINO%"
) else (
  set "DESTINO=G:\Meu Drive\Amorim\FISCALE 2026"
)

rem  Ensaio: "backup_para_drive.bat ensaio" mostra o que seria enviado sem
rem  escrever nada no Drive. O codigo continua sendo copiado normalmente -
rem  o ensaio e sobre o .fbk, que e a parte que estava errando de pasta.
set "ENSAIO="
if /I "%~1"=="ensaio" set "ENSAIO=--ensaio"

if not defined FISCALE_BACKUP_DESTINO if not exist "G:\" (
  echo.
  echo  O Google Drive ^(unidade G:^) nao esta montado.
  echo  Abra o Google Drive para computador e tente de novo.
  echo.
  pause
  exit /b 1
)
if not exist "%DESTINO%" mkdir "%DESTINO%" 2>nul

echo.
echo  ================================================================
echo   1 de 2 — enviando o CODIGO do Fiscale
echo   Destino: %DESTINO%
echo  ================================================================
echo.

rem  /XD "dados"  -> NENHUM dado sobe por aqui. Backup e o .fbk.
rem  node_modules do Elo tem ~14 mil arquivos e recria com "npm install".
rem  .env, chaves e .pfx nunca sobem: chave privada em nuvem nao pode acontecer.
robocopy "%CD%" "%DESTINO%" /E /XO /R:2 /W:2 /NFL /NDL /NJH ^
  /XD ".venv" "build" "dist" "__pycache__" ".git" "dados" ^
      "node_modules" ".next" ".turbo" ".storage" "coverage" ^
      "backup_pre_elo_mvp11_20260809" "backup_pre_elo_mvp12_20260809" ^
      "backup_pre_meta_mvp181_20260811" "backup_pre_health1_20260811" ^
  /XF "*.pyc" "*.log" ".env" ".env.*" "*.key" "*.pem" "*.pfx" "*.p12" ^
      "elo_config.json" "elo_chave_ed25519.json" "elo_integracao_ed25519.json" ^
      "*.db" "*.db-shm" "*.db-wal" "*.pre-port1" "*.pre-port3" "*.fbk"

if %ERRORLEVEL% GEQ 8 (
  echo.
  echo  [ERRO] A copia do codigo nao terminou. Codigo: %ERRORLEVEL%
  echo.
  pause
  exit /b 1
)

echo.
echo  ================================================================
echo   2 de 2 - enviando o backup .fbk
echo  ================================================================
echo.

rem ---------------------------------------------------------------------------
rem  QUEM DECIDE ISTO E O backup_drive.py, NAO ESTE ARQUIVO.
rem
rem  A versao antiga procurava em "%USERPROFILE%\Desktop" e tentava adivinhar
rem  a pasta do OneDrive escrevendo "Area de Trabalho" SEM ACENTO. Nesta
rem  maquina a pasta real e "OneDrive\Area de Trabalho" COM acento, entao o
rem  palpite nunca casava. Pior: o arquivo dizia "Concluido" no fim mesmo
rem  quando nao tinha achado .fbk nenhum.
rem
rem  O cmd.exe nao sabe perguntar ao Windows onde fica a Area de Trabalho de
rem  verdade, nao calcula SHA-256 e se atrapalha com acento em caminho. O
rem  Python sabe fazer as tres coisas, e da para testar.
rem
rem  Codigos de saida: 0 ok - 3 nenhum .fbk valido na pasta canonica
rem                    4 destino - 5 origem configurada invalida
rem ---------------------------------------------------------------------------

set "PY=nfse\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem  A origem e a pasta canonica <FISCALE_DADOS>ackups, e so ela. Backups
rem  antigos noutras pastas aparecem em:
rem      python backup_drive.py --localizar-legados
rem  que apenas LISTA - nao copia e nao envia.
"%PY%" backup_drive.py %ENSAIO%
set "RESULTADO=%ERRORLEVEL%"

if "%RESULTADO%"=="0" goto fimok

echo.
echo  ================================================================
if "%RESULTADO%"=="3" echo   PAREI: nenhum backup .fbk foi encontrado.
if "%RESULTADO%"=="4" echo   PAREI: nao consegui escrever no Drive.
if "%RESULTADO%"=="5" echo   PAREI: a pasta configurada em FISCALE_BACKUP_ORIGEM nao serve.
echo.
echo   O CODIGO subiu normalmente. O BACKUP nao.
echo   Leia a mensagem acima: ela diz o que fazer.
echo  ================================================================
echo.
pause
exit /b %RESULTADO%

:fimok
echo.
echo  LEMBRE-SE: o .fbk guarda os certificados cifrados com a frase-senha
echo  que voce digitou ao criar o backup. Sem ela, os certificados nao
echo  abrem na outra maquina. Guarde a frase em lugar seguro - ela nao
echo  fica salva em canto nenhum.
echo.
echo  ================================================================
echo   Concluido: codigo e backup.
echo.
echo   O Google Drive vai enviar em segundo plano. Confira o icone dele
echo   ao lado do relogio: quando parar de girar, terminou de subir.
echo  ================================================================
echo.
pause
