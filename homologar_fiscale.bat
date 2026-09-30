@echo off
chcp 65001 >nul
title FISCALE - HOMOLOGACAO
cd /d "%~dp0"
setlocal EnableExtensions

rem ---------------------------------------------------------------------------
rem FISCALE - HOMOLOGACAO (D-AMB-1)
rem
rem   Producao provisoria  : porta 8777, dados em %USERPROFILE%\Fiscale\dados
rem   Homologacao          : porta 8898, dados em C:\Fiscale\homologacao\dados
rem
rem ESTE ARQUIVO NAO TOCA A PRODUCAO. Ele nao fecha, nao reinicia e nao le a
rem pasta de dados real - e RECUSA subir se alguem apontar a homologacao para
rem ela. A trava FISCALE_TESTE_PROIBIR_RAIZ_REAL fica ligada: se qualquer parte
rem do sistema tentar resolver a raiz para a pasta real, estoura na hora em vez
rem de gravar escondido.
rem
rem COMO A HOMOLOGACAO RECEBE DADOS
rem   Somente por RESTAURACAO de um .fbk, pela tela Home > Backup da propria
rem   homologacao. Nunca por copia crua da pasta real: numa copia crua as
rem   senhas de certificado (DPAPI da mesma conta do Windows) continuariam
rem   abrindo, e a homologacao poderia assinar de verdade. Restaurando do .fbk,
rem   cada empresa fica "aguardando senha", que e o que se quer num ambiente
rem   de teste. Dado ficticio: CNPJ ficticio, certificado ficticio, senha
rem   ficticia - nunca certificado real.
rem
rem MODOS
rem   homologar_fiscale.bat            confere e SOBE a homologacao
rem   homologar_fiscale.bat conferir   so confere e sai (nao sobe nada)
rem
rem GANCHOS DE TESTE (usados por teste_homologacao.py; nao use no dia a dia)
rem   FISCALE_HOMOLOGACAO_DADOS   troca a pasta conferida
rem   FISCALE_HOMOLOGACAO_PORTA   troca a porta conferida
rem   segundo argumento _teste_sem_dados  simula pasta nao definida
rem
rem NOTA DE MANUTENCAO: sem blocos entre parenteses, como no testar_fiscale.bat.
rem O cmd.exe reparseia o bloco inteiro e um rem com parentese dentro dele
rem despedaca o arquivo. Rotulo e goto sao chatos de ler e nao tem esse problema.
rem ---------------------------------------------------------------------------

set "AMBIENTE=HOMOLOGACAO"
set "PORTA=8898"
set "DADOS=C:\Fiscale\homologacao\dados"
set "PORTA_PRODUCAO=8777"
set "REAL=%USERPROFILE%\Fiscale\dados"
set "REAL_LEGADO=%USERPROFILE%\SistemaNFSe\dados"
set "MODO=%~1"

if defined FISCALE_HOMOLOGACAO_DADOS set "DADOS=%FISCALE_HOMOLOGACAO_DADOS%"
if defined FISCALE_HOMOLOGACAO_PORTA set "PORTA=%FISCALE_HOMOLOGACAO_PORTA%"
if /I "%~2"=="_teste_sem_dados" set "DADOS="

echo.
echo  ================================================================
echo    FISCALE - AMBIENTE: %AMBIENTE%
echo  ================================================================
echo.
echo    Porta          : %PORTA%
echo    Dados          : %DADOS%
echo    Producao       : porta %PORTA_PRODUCAO% em %REAL%  -- INTOCADA
echo.

rem ---- 1. a pasta de dados tem de estar explicita -----------------------
if not defined DADOS goto :semdados
if not defined PORTA goto :semporta

rem ---- 2. nunca a raiz real, nem nada dentro dela -----------------------
set "D=%DADOS%"
if "%D:~-1%"=="\" set "D=%D:~0,-1%"
echo "%D%" | findstr /I /C:"%REAL%" >nul
if not errorlevel 1 goto :raizreal
echo "%D%" | findstr /I /C:"%REAL_LEGADO%" >nul
if not errorlevel 1 goto :raizreal

rem ---- 3. nunca a porta da producao ------------------------------------
if "%PORTA%"=="%PORTA_PRODUCAO%" goto :portaproducao

rem ---- 4. a porta da homologacao tem de estar livre --------------------
netstat -ano -p tcp | findstr /R /C:":%PORTA% .*LISTENING" >nul
if not errorlevel 1 goto :portaocupada

rem ---- 5. o Python do projeto ------------------------------------------
set "PY=nfse\.venv\Scripts\python.exe"
if not exist "%PY%" goto :sempython

rem ---- 6. a pasta de dados, criada so se for preciso -------------------
if exist "%DADOS%\" goto :pastaok
echo    Pasta de homologacao ainda nao existe; criando.
mkdir "%DADOS%" 2>nul
if not exist "%DADOS%\" goto :errodir
:pastaok

rem ---- 7. ambiente do processo -----------------------------------------
set "FISCALE_DADOS=%DADOS%"
set "FISCALE_PORT=%PORTA%"
set "FISCALE_SEM_BANDEJA=1"
set "FISCALE_TESTE_PROIBIR_RAIZ_REAL=1"

if not exist "%DADOS%\certificados.json" echo    [i] Homologacao vazia: restaure um .fbk em Home ^> Backup depois de entrar.
echo    Trava contra a pasta real: LIGADA
echo    Icone na bandeja: desligado
echo.

if /I "%MODO%"=="conferir" goto :conferido

echo  ================================================================
echo    Subindo a HOMOLOGACAO. O navegador abre em http://127.0.0.1:%PORTA%
echo.
echo    Para FECHAR: dentro da homologacao, Home ^> "Encerrar".
echo    NAO feche esta janela no X - deixaria o modulo NFS-e solto.
echo  ================================================================
echo.

"%PY%" fiscale_server.py

echo.
echo  Homologacao encerrada. Os dados dela continuam em: %DADOS%
echo  A producao na porta %PORTA_PRODUCAO% nao foi tocada.
echo.
if "%FISCALE_HOMOLOGACAO_SEM_PAUSA%"=="1" goto :fim
pause
goto :fim

:conferido
echo  CONFERIDO: tudo pronto e NADA foi iniciado.
echo  Para subir de verdade: homologar_fiscale.bat
exit /b 0

:semdados
echo  [ERRO] A pasta de dados da homologacao nao esta definida.
echo         Recuso subir: sem ela o FISCALE cairia na pasta de dados REAL.
exit /b 2

:semporta
echo  [ERRO] A porta da homologacao nao esta definida.
exit /b 8

:raizreal
echo  [ERRO] A pasta indicada e a pasta de dados REAL da producao, ou esta dentro dela:
echo         %DADOS%
echo         Homologacao NUNCA usa a producao. Use C:\Fiscale\homologacao\dados
echo         e traga os dados por restauracao de um .fbk.
exit /b 3

:portaproducao
echo  [ERRO] A porta %PORTA% e a da PRODUCAO. A homologacao usa 8898.
exit /b 5

:portaocupada
echo  [ERRO] A porta %PORTA% ja esta em uso nesta maquina.
echo         Feche a homologacao que ja esta aberta - ou veja quem escuta com:
echo         netstat -ano -p tcp ^| findstr :%PORTA%
exit /b 4

:sempython
echo  [ERRO] Nao achei o Python do projeto em:
echo         %CD%\%PY%
echo         A homologacao usa o MESMO codigo e o MESMO Python da producao.
exit /b 6

:errodir
echo  [ERRO] Nao consegui criar a pasta:
echo         %DADOS%
exit /b 7

:fim
endlocal
