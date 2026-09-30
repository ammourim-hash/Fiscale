@echo off
chcp 65001 >nul
title Fiscale - INSTANCIA DE TESTE
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem FISCALE - subir uma instancia de TESTE, sem risco para o dia a dia.
rem
rem PARA QUE SERVE
rem   Clicar em tudo, importar XML, apurar, cadastrar gente, errar de proposito
rem   - sem encostar nos dados de verdade.
rem
rem AS DUAS COISAS QUE TORNAM ISTO SEGURO
rem   1. PORTA DIFERENTE: 8899, nao 8777. O Fiscale que voce usa continua no ar
rem      o tempo todo. Este arquivo nao fecha e nao reinicia o seu.
rem   2. PASTA DE DADOS DIFERENTE: %LOCALAPPDATA%\Fiscale-teste\dados, nunca
rem      a pasta real. Quem decide isso e a variavel FISCALE_DADOS, lida por
rem      fiscale_dados.raiz - o unico lugar do sistema que responde "onde ficam
rem      os dados". O modulo NFS-e usa a mesma funcao desde a PORT 1, entao os
rem      XML que voce importar no teste tambem vao para a pasta de teste.
rem
rem DOIS MODOS
rem   testar_fiscale.bat         comeca VAZIO, sem empresa e sem nota
rem   testar_fiscale.bat copia   COPIA os dados reais para a pasta de teste,
rem                              para testar com empresas e notas de verdade.
rem                              E copia: o original nao e tocado, e nada volta
rem                              do teste para ele.
rem
rem COMO FECHAR
rem   Dentro do Fiscale de teste: Home, botao "Encerrar". Esse caminho fecha
rem   tambem o modulo NFS-e. Fechar esta janela preta no X deixa o modulo
rem   rodando sozinho.
rem
rem NOTA DE MANUTENCAO
rem   Este arquivo evita blocos entre parenteses de proposito. O cmd.exe
rem   reparseia o bloco inteiro de uma vez, e linhas rem com parenteses ou
rem   com ^ de continuacao dentro dele fazem o arquivo se despedacar - foi
rem   exatamente o que aconteceu na primeira versao. Rotulo e goto sao chatos
rem   de ler e nao tem esse problema.
rem ---------------------------------------------------------------------------

set "PORTA=8899"
set "TESTE=%LOCALAPPDATA%\Fiscale-teste\dados"
set "REAL=%USERPROFILE%\Fiscale\dados"

echo.
echo  ================================================================
echo    FISCALE - INSTANCIA DE TESTE
echo  ================================================================
echo.
echo    Porta          : %PORTA%    ^(o seu Fiscale continua na 8777^)
echo    Dados do teste : %TESTE%
echo.

if /I not "%~1"=="copia" goto subir
if not exist "%REAL%" goto semreal

echo    Copiando os dados reais para a pasta de teste...
echo    Sao dezenas de milhares de arquivos pequenos; leva alguns minutos.
echo    Na segunda vez e rapido: so o que mudou e copiado.
echo.
rem /XO nao regrava o que ja esta igual.
rem Os .db ficam de fora: sao sessao e fila, estao ABERTOS pelo Fiscale que
rem esta rodando agora, e copiar banco aberto traz arquivo pela metade.
robocopy "%REAL%" "%TESTE%" /E /XO /R:1 /W:1 /NFL /NDL /NJH /XF "*.db" "*.db-shm" "*.db-wal" "*.log"
if ERRORLEVEL 8 goto erroCopia
echo.
echo    Copia concluida. O original nao foi alterado.
echo.
goto subir

:semreal
echo    [!] Nao achei a pasta de dados real em:
echo        %REAL%
echo        Vou subir vazio.
echo.
goto subir

:erroCopia
echo.
echo    [ERRO] A copia nao terminou.
echo    Se o Fiscale de verdade estiver aberto, alguns arquivos ficam presos.
echo    Feche-o e rode de novo, ou use sem o "copia" para testar vazio.
echo.
pause
exit /b 1

:subir
if not exist "%TESTE%" mkdir "%TESTE%" 2>nul

rem O Python da venv do modulo NFS-e e o que tem tudo instalado, e e assinado
rem - o Smart App Control deixa rodar.
set "PY=nfse\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem SEM_BANDEJA: dois icones iguais ao lado do relogio e pedir para alguem
rem encerrar o Fiscale errado.
set "FISCALE_DADOS=%TESTE%"
set "FISCALE_PORT=%PORTA%"
set "FISCALE_SEM_BANDEJA=1"

echo  ================================================================
echo    Abrindo. O navegador abre sozinho em http://127.0.0.1:%PORTA%
echo.
echo    Para FECHAR: dentro do Fiscale de teste, Home ^> "Encerrar".
echo    NAO feche esta janela no X - deixaria um processo solto.
echo  ================================================================
echo.

"%PY%" fiscale_server.py

echo.
echo  Instancia de teste encerrada.
echo  Os dados do teste continuam em: %TESTE%
echo  Pode apagar essa pasta a vontade: nada ali e de verdade.
echo.
pause
