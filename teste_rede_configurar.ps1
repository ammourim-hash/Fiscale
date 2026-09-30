<#
  teste_rede_configurar.ps1 — prova a política do rede_configurar.ps1 SEM tocar no Windows.

  COMO ISTO GARANTE QUE NADA É ALTERADO
    1. Recusa rodar como administrador: sem elevação, o Windows negaria
       qualquer mudança de firewall/perfil/tarefa mesmo que algo escapasse.
    2. Todo comando que altera algo (New/Set/Enable/Disable/Remove-NetFirewallRule,
       Set-NetConnectionProfile, Register/Unregister-ScheduledTask, netsh,
       powercfg) é substituído por uma FUNÇÃO de mesmo nome, que só registra a
       chamada num firewall de mentira. Em PowerShell, função vence cmdlet e
       executável na resolução de nomes.
    3. A leitura do Windows (Get-EstadoReal) é substituída por um estado
       fictício. O script é carregado só com as funções, sem executar o fluxo.
    4. Antes de tudo, a árvore sintática confere que os comandos que alteram
       algo só aparecem nos três trechos de aplicação (backup, aplicar, desfazer).

  Rodado pelo teste_rede_configurar.py, que é o que o rodar_testes.py enxerga.
#>
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$AQUI = Split-Path -Parent $MyInvocation.MyCommand.Path
$ALVO = Join-Path $AQUI 'rede_configurar.ps1'

$script:Ok = 0
$script:Falhas = 0
function Ok([bool]$cond, [string]$desc) {
    if ($cond) { $script:Ok++; [Console]::WriteLine("  ok   $desc") }
    else { $script:Falhas++; [Console]::WriteLine("  FALHOU  $desc") }
}
function Secao([string]$t) { [Console]::WriteLine(""); [Console]::WriteLine("-- $t") }
function Fim { [Console]::WriteLine(""); [Console]::WriteLine("$($script:Ok) ok · $($script:Falhas) falha(s)"); if ($script:Falhas) { exit 1 } else { exit 0 } }

$eu = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($eu.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Ok $false "este teste recusa rodar como administrador (é o que impede qualquer escape de chegar ao Windows)"
    Fim
}


# ── 0. Estrutura: quem pode alterar o quê ───────────────────────────────────
Secao "0. Estrutura do script (árvore sintática, sem executar)"
$tokens = $null; $erros = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ALVO, [ref]$tokens, [ref]$erros)
Ok (@($erros).Count -eq 0) "o script não tem erro de sintaxe ($(@($erros).Count))"

$MUTANTES = @('New-NetFirewallRule', 'Set-NetFirewallRule', 'Enable-NetFirewallRule', 'Disable-NetFirewallRule',
              'Remove-NetFirewallRule', 'Set-NetFirewallProfile', 'Copy-NetFirewallRule', 'Rename-NetFirewallRule',
              'Set-NetConnectionProfile', 'Register-ScheduledTask', 'Unregister-ScheduledTask', 'Set-ScheduledTask',
              'Set-Content', 'Add-Content', 'Out-File', 'New-Item', 'Remove-Item', 'Move-Item', 'Copy-Item',
              'Set-ItemProperty', 'New-ItemProperty', 'Remove-ItemProperty', 'Stop-Process', 'Start-Process',
              'Invoke-Expression', 'iex', 'netsh', 'powercfg', 'schtasks', 'reg', 'sc', 'wscript', 'cmd')
$PERMITIDAS = @('Save-BackupRede', 'Invoke-PlanoRede', 'Invoke-DesfazerRede')
function Get-FuncaoDona($no) {
    $p = $no.Parent
    while ($p) {
        if ($p -is [System.Management.Automation.Language.FunctionDefinitionAst]) { return $p.Name }
        $p = $p.Parent
    }
    return '(topo do script)'
}
$violacoes = @(); $dinamicos = @(); $netshFora = @()
foreach ($c in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)) {
    $nome = $c.GetCommandName()
    $dono = Get-FuncaoDona $c
    if (-not $nome) { $dinamicos += "$dono : $($c.Extent.Text)"; continue }
    $nomeBase = [IO.Path]::GetFileNameWithoutExtension($nome)
    if ($MUTANTES -notcontains $nomeBase) { continue }
    if ($nomeBase -eq 'netsh' -and $dono -eq 'Get-RedesConectadas') {
        $el = @($c.CommandElements | ForEach-Object { $_.Extent.Text })
        if ($el.Count -ge 3 -and $el[1] -eq 'wlan' -and $el[2] -eq 'show') { continue }
        $netshFora += $c.Extent.Text; continue
    }
    if ($PERMITIDAS -notcontains $dono) { $violacoes += "$dono : $nome" }
}
Ok ($violacoes.Count -eq 0) "comandos que alteram algo só existem em backup/aplicar/desfazer $(if ($violacoes) { '-> ' + ($violacoes -join '; ') })"
Ok ($dinamicos.Count -eq 0) "nenhuma invocação dinâmica (& `$variavel) $(if ($dinamicos) { '-> ' + ($dinamicos -join '; ') })"
Ok ($netshFora.Count -eq 0) "fora do backup, netsh só é usado como 'netsh wlan show' (leitura)"
$removes = @($ast.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] -and $args[0].GetCommandName() -eq 'Remove-NetFirewallRule' }, $true) |
             ForEach-Object { Get-FuncaoDona $_ } | Sort-Object -Unique)
Ok (($removes -join ',') -eq 'Invoke-DesfazerRede') "Remove-NetFirewallRule só existe no desfazer (apaga apenas a regra que o próprio script criou)"
$tokensDeCodigo = @($tokens | Where-Object { $_.Kind -ne 'Comment' })
Ok (@($tokensDeCodigo | Where-Object { $_.Text -match 'powercfg|standby|hibernat' }).Count -eq 0) "nenhum trecho de código (fora de comentário) fala de powercfg, suspensão ou hibernação"
$paramSimular = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'Simular' })
Ok ($paramSimular.Count -eq 1 -and @($paramSimular[0].Attributes | Where-Object { $_.TypeName.Name -eq 'switch' }).Count -eq 1) "o parâmetro -Simular existe e é um switch"


# ── Mundo de mentira ────────────────────────────────────────────────────────
$script:Chamadas = New-Object System.Collections.ArrayList
function Registrar([string]$cmd, $params) {
    $h = @{}; foreach ($k in @($params.Keys)) { $h[$k] = $params[$k] }
    [void]$script:Chamadas.Add([pscustomobject]@{ Cmd = $cmd; P = $h })
}
function New-NetFirewallRule {
    [CmdletBinding()] param([string]$Name, [string]$DisplayName, [string]$Description, [string]$Direction,
        [string]$Protocol, [string[]]$LocalPort, [Alias('Profile')][string[]]$Perfis, [string[]]$RemoteAddress, [string]$Action)
    Registrar 'New-NetFirewallRule' $PSBoundParameters
    if ($script:Fw.Contains($Name)) { throw "regra já existe: $Name" }
    $script:Fw[$Name] = New-RegraModelo -Nome $Name -Exibicao $DisplayName -Perfis @(Get-PerfisDaRegra ($Perfis -join ', ')) `
        -Protocolo $Protocol -PortaLocal $LocalPort -Origem $RemoteAddress
    return [pscustomobject]@{ Name = $Name }
}
function Set-NetFirewallRule {
    [CmdletBinding()] param([string]$Name, [Alias('Profile')][string[]]$Perfis, [string[]]$RemoteAddress)
    Registrar 'Set-NetFirewallRule' $PSBoundParameters
    if (-not $script:Fw.Contains($Name)) { throw "regra inexistente: $Name" }
    if ($PSBoundParameters.ContainsKey('Perfis')) { $script:Fw[$Name].Perfis = @(Get-PerfisDaRegra ($Perfis -join ', ')) }
    if ($PSBoundParameters.ContainsKey('RemoteAddress')) { $script:Fw[$Name].Origem = @($RemoteAddress) }
}
function Enable-NetFirewallRule { [CmdletBinding()] param([string]$Name) Registrar 'Enable-NetFirewallRule' $PSBoundParameters; $script:Fw[$Name].Ativa = $true }
function Disable-NetFirewallRule { [CmdletBinding()] param([string]$Name) Registrar 'Disable-NetFirewallRule' $PSBoundParameters; $script:Fw[$Name].Ativa = $false }
function Remove-NetFirewallRule { [CmdletBinding()] param([string]$Name) Registrar 'Remove-NetFirewallRule' $PSBoundParameters; $script:Fw.Remove($Name) }
function Get-NetFirewallRule { [CmdletBinding()] param([string]$Name, [string]$Direction, [string]$Action)
    if ($Name -and $script:Fw.Contains($Name)) { return [pscustomobject]@{ Name = $Name } } }
function Set-NetConnectionProfile {
    [CmdletBinding()] param([int]$InterfaceIndex = -1, [string]$InterfaceAlias, [string]$NetworkCategory)
    Registrar 'Set-NetConnectionProfile' $PSBoundParameters
    foreach ($r in $script:Redes) { if ($r.InterfaceIndex -eq $InterfaceIndex -or $r.Interface -eq $InterfaceAlias) { $r.Categoria = $NetworkCategory } }
}
function Get-NetConnectionProfile { [CmdletBinding()] param([string]$InterfaceAlias)
    foreach ($r in $script:Redes) { if ($r.Interface -eq $InterfaceAlias) { return [pscustomobject]@{ Name = $r.Nome; InterfaceAlias = $r.Interface } } } }
function Register-ScheduledTask {
    [CmdletBinding()] param([string]$TaskName, $Action, $Trigger, $Settings, [string]$RunLevel, [string]$Description)
    Registrar 'Register-ScheduledTask' $PSBoundParameters; $script:Tarefas[$TaskName] = $true
}
function Unregister-ScheduledTask { [CmdletBinding(SupportsShouldProcess = $true)] param([string]$TaskName)
    Registrar 'Unregister-ScheduledTask' $PSBoundParameters; $script:Tarefas.Remove($TaskName) }
function Get-ScheduledTask { [CmdletBinding()] param([string]$TaskName)
    if ($TaskName -and $script:Tarefas.ContainsKey($TaskName)) { return [pscustomobject]@{ TaskName = $TaskName } } }
function New-ScheduledTaskAction { return 'acao-simulada' }
function New-ScheduledTaskTrigger { return 'gatilho-simulado' }
function New-ScheduledTaskSettingsSet { return 'opcoes-simuladas' }
function netsh {
    Registrar 'netsh' @{ args = ($args -join ' ') }
    if ($script:NetshFalha) { $global:LASTEXITCODE = 1; return 'erro simulado' }
    if ($args[0] -eq 'advfirewall' -and $args[1] -eq 'export') {
        Set-Content -LiteralPath $args[2] -Value 'exportacao simulada'; $global:LASTEXITCODE = 0; return 'Ok.'
    }
    $global:LASTEXITCODE = 0
}
function powercfg { Registrar 'powercfg' @{ args = ($args -join ' ') } }

# Carrega SÓ as funções do script.
$env:FISCALE_REDE_SO_CARREGAR = '1'
. $ALVO
$env:FISCALE_REDE_SO_CARREGAR = $null

# Pontos de contato do script, trocados depois de carregado.
function Test-Administrador { return $script:Admin }
function Read-Resposta([string]$pergunta) {
    [void]$script:Perguntas.Add($pergunta)
    if ($script:Respostas.Count -eq 0) { throw "pergunta inesperada: $pergunta" }
    return $script:Respostas.Dequeue()
}
function Get-EstadoReal([int]$Porta, [string]$Lancador, [string]$Raiz) {
    return [pscustomobject]@{
        Computador = 'TESTE'
        Redes = @($script:Redes | ForEach-Object { $_.PSObject.Copy() })
        ExesServidor = @($script:Exes)
        Regras = @($script:Fw.Values | ForEach-Object { Copy-Regra $_ })
        Inicializacao = @($script:Inicio)
        TarefaExiste = $script:Tarefas.ContainsKey($NOME_TAREFA)
        LancadorExiste = $script:LancadorExiste
        Lancador = 'C:\teste\Iniciar Fiscale.vbs'
    }
}

$script:PastaTeste = Join-Path $env:TEMP ('fiscale_teste_rede_' + [guid]::NewGuid().ToString('N'))
function Add-Regra($r) { $script:Fw[$r.Nome] = $r }
function Reset-Mundo {
    $script:Chamadas.Clear()
    $script:Perguntas = New-Object System.Collections.ArrayList
    $script:Respostas = New-Object System.Collections.Queue
    $script:Fw = [ordered]@{}
    $script:Tarefas = @{}
    $script:NetshFalha = $false
    $script:Admin = $true
    $script:LancadorExiste = $true
    if (Test-Path -LiteralPath $script:PastaTeste) { Remove-Item -LiteralPath $script:PastaTeste -Recurse -Force }
    $script:Exes = @(
        [pscustomobject]@{ Caminho = 'C:\Py312\python.exe'; Provas = @('Python base da venv do FISCALE') },
        [pscustomobject]@{ Caminho = 'C:\Py312\pythonw.exe'; Provas = @('Python base da venv do FISCALE', 'processo que escuta na porta 8777 agora (PID 1)') })
    $script:Redes = @([pscustomobject]@{ InterfaceIndex = 7; Nome = 'Rede Teste 3'; Interface = 'Wi-Fi'; Ssid = 'Rede Teste'
                                          Categoria = 'Public'; IPv4 = '192.168.1.25'; Prefixo = 24; Gateway = '192.168.1.1'; Faixa = '192.168.1.0/24' })
    $script:Inicio = @([pscustomobject]@{ Tipo = 'Pasta Inicializar'; Onde = 'C:\Startup\Iniciar Fiscale.vbs' })
    Add-Regra (New-RegraModelo -Nome 'py-tcp' -Exibicao 'pythonw.exe' -Programa 'C:\Py312\pythonw.exe' -Perfis Private, Public -ExeExiste $true)
    Add-Regra (New-RegraModelo -Nome 'py-udp' -Exibicao 'pythonw.exe' -Programa 'C:\Py312\pythonw.exe' -Protocolo UDP -Perfis Private, Public -ExeExiste $true)
    Add-Regra (New-RegraModelo -Nome 'py-pub' -Exibicao 'pythonw.exe' -Programa 'C:\Py312\pythonw.exe' -Perfis Public -ExeExiste $true)
    Add-Regra (New-RegraModelo -Nome 'py-exe' -Exibicao 'python.exe' -Programa 'C:\Py312\python.exe' -Perfis Private, Public -ExeExiste $true)
    Add-Regra (New-RegraModelo -Nome 'py314' -Exibicao 'python 3.14' -Programa 'C:\Python314\python.exe' -Perfis Private, Public -ExeExiste $true)
    Add-Regra (New-RegraModelo -Nome 'orfa-py' -Exibicao 'python portátil' -Programa 'C:\Temp\sumiu\runtime\python.exe' -Perfis Public -ExeExiste $false)
    Add-Regra (New-RegraModelo -Nome 'orfa-fiscale' -Exibicao 'fiscale.exe antigo' -Programa 'C:\Antigo\dist\fiscale.exe' -Perfis Private, Public -ExeExiste $false)
    Add-Regra (New-RegraModelo -Nome 'fiscale-exe' -Exibicao 'fiscale.exe' -Programa 'C:\Programs\Fiscale\fiscale.exe' -Perfis Private, Public -ExeExiste $true)
    Add-Regra (New-RegraModelo -Nome 'node' -Exibicao 'Node.js' -Programa 'C:\Program Files\nodejs\node.exe' -Perfis Private, Public -ExeExiste $true)
    Add-Regra (New-RegraModelo -Nome 'anydesk' -Exibicao 'AnyDesk' -Programa 'C:\Program Files (x86)\AnyDesk\AnyDesk.exe' -Perfis Public -ExeExiste $true)
    Add-Regra (New-RegraModelo -Nome 'appx' -Exibicao 'App da Store' -Programa 'Any' -Protocolo Any -Perfis Domain, Private, Public -Restrita $true)
    Add-Regra (New-RegraModelo -Nome 'alterdata' -Exibicao 'Alterdata 8768' -Programa 'Any' -PortaLocal 8768 -Origem LocalSubnet -Perfis Domain, Private, Public)
    Add-Regra (New-RegraModelo -Nome 'py-desligada' -Exibicao 'pythonw.exe (desligada)' -Programa 'C:\Py312\pythonw.exe' -Perfis Public -Ativa $false -ExeExiste $true)
}
function Get-Foto {
    return (@($script:Fw.Values | ForEach-Object { "$($_.Nome)|$($_.Ativa)|$(@($_.Perfis) -join ',')|$(@($_.Origem) -join ',')" } | Sort-Object) -join "`n")
}
function Get-CategoriaDaRede { return $script:Redes[0].Categoria }
function Rodar([hashtable]$Extra = @{}) {
    $p = @{ Porta = 8777; Lancador = 'C:\teste\Iniciar Fiscale.vbs'; PastaBackup = $script:PastaTeste
            Raiz = 'C:\teste'; Script = 'C:\teste\rede_configurar.ps1' }
    foreach ($k in $Extra.Keys) { $p[$k] = $Extra[$k] }
    try {
        $saida = @(Invoke-RedeConfigurar @p 6>&1)
    } catch {
        return [pscustomobject]@{ Codigo = -1; Texto = "EXCEÇÃO: $($_.Exception.Message)" }
    }
    $texto = (@($saida | Where-Object { $_ -is [System.Management.Automation.InformationRecord] } | ForEach-Object { "$($_.MessageData)" })) -join "`n"
    $retorno = @($saida | Where-Object { $_ -isnot [System.Management.Automation.InformationRecord] })
    $codigo = if ($retorno.Count) { [int]$retorno[-1] } else { -2 }
    return [pscustomobject]@{ Codigo = $codigo; Texto = $texto }
}
function Get-Mutacoes([string]$cmd = '') {
    return @($script:Chamadas | Where-Object { -not $cmd -or $_.Cmd -eq $cmd })
}
function Get-ArquivosBackup {
    if (-not (Test-Path -LiteralPath $script:PastaTeste)) { return @() }
    return @(Get-ChildItem -LiteralPath $script:PastaTeste -File)
}
$FAIXA = '192.168.1.0/24'
$FISCALE = @('py-tcp', 'py-udp', 'py-exe', 'fiscale-exe')


# ── 1. Tarefa duplicada ─────────────────────────────────────────────────────
Secao "1. Não cria inicialização duplicada"
Reset-Mundo
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA -Porta 8777
Ok ($null -eq $plano.Tarefa) "com o 'Iniciar Fiscale.vbs' na pasta Inicializar, o plano não tem tarefa"
$script:Respostas.Enqueue('S')
$r = Rodar @{ RedeEscritorio = $FAIXA }
Ok ($r.Codigo -eq 0) "a aplicação terminou (código $($r.Codigo))"
Ok (@(Get-Mutacoes 'Register-ScheduledTask').Count -eq 0) "nenhum Register-ScheduledTask foi chamado"
Ok ($r.Texto -match 'JÁ sobe sozinho' -and $r.Texto -match 'Iniciar Fiscale\.vbs') "a saída mostra o mecanismo existente"
Ok ($r.Texto -match 'mecanismo atual não é alterado') "a saída diz que o mecanismo atual não é alterado"

Reset-Mundo
$script:Inicio = @([pscustomobject]@{ Tipo = 'Tarefa agendada'; Onde = 'Sobe o fiscale_server (Ready)' })
Ok ($null -eq (New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA).Tarefa) "tarefa agendada que já roda o fiscale_server também impede criar outra"

Reset-Mundo
$script:Inicio = @([pscustomobject]@{ Tipo = 'Registro Run'; Onde = 'HKCU\...\Run\Fiscale' })
Ok ($null -eq (New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA).Tarefa) "chave Run que sobe o FISCALE também impede criar tarefa"

Reset-Mundo
$script:Inicio = @()
$script:LancadorExiste = $false
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA
Ok ($null -eq $plano.Tarefa -and ($plano.Avisos -join ' ') -match 'lançador não existe') "sem inicialização e sem lançador: não cria e avisa"

Reset-Mundo
$script:Inicio = @()
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA
Ok ($plano.Tarefa -and $plano.Tarefa.Nome -eq $NOME_TAREFA) "sem nenhuma inicialização existente, o plano cria UMA tarefa"
$script:Respostas.Enqueue('S')
$null = Rodar @{ RedeEscritorio = $FAIXA }
Ok (@(Get-Mutacoes 'Register-ScheduledTask').Count -eq 1) "e aplicar registra exatamente uma"
$script:Chamadas.Clear()
$r2 = Rodar @{ RedeEscritorio = $FAIXA }
Ok ($r2.Codigo -eq 0 -and @(Get-Mutacoes 'Register-ScheduledTask').Count -eq 0) "rodar de novo não cria a segunda"


# ── 2. Python fora do Público ───────────────────────────────────────────────
Secao "2. Não deixa o Python do FISCALE aberto no perfil Público"
Reset-Mundo
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA
Ok (@($plano.AlcanceAntes | Where-Object { $_.Perfil -eq 'Public' }).Count -gt 0) "(controle) antes, a 8777 é alcançável no Público"
$script:Respostas.Enqueue('S')
$null = Rodar @{ RedeEscritorio = $FAIXA }
foreach ($n in $FISCALE) {
    Ok ((@($script:Fw[$n].Perfis) -join ',') -eq 'Private') "regra '$n' ficou só no Privado (era Private,Public)"
}
Ok ($script:Fw['py-pub'].Ativa -eq $false -and (@($script:Fw['py-pub'].Perfis) -join ',') -eq 'Public') "regra que só valia no Público foi DESATIVADA, não passada para o Privado"
$exes = @($script:Exes | ForEach-Object { $_.Caminho })
$alcanceReal = @(Get-Alcance @($script:Fw.Values) $exes 8777)
Ok (@($alcanceReal | Where-Object { $_.Perfil -ne 'Private' }).Count -eq 0) "no firewall resultante, ninguém alcança a 8777 fora do Privado"
$ampliou = @($plano.Restringir | Where-Object { $de = @($_.PerfisDe); @($_.PerfisPara | Where-Object { $de -notcontains $_ }).Count -gt 0 })
Ok ($ampliou.Count -eq 0) "nenhuma restrição acrescenta perfil que a regra não tinha"

Reset-Mundo
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa ''
Ok (@($plano.AlcanceDepois | Where-Object { $_.Perfil -ne 'Private' }).Count -eq 0) "mesmo sem rede confirmada, o plano tira o Python do Público"
Ok (@($plano.Restringir | Where-Object { (@($_.OrigemPara) -join ',') -ne 'LocalSubnet' }).Count -eq 0) "e sem rede confirmada a origem provisória é LocalSubnet, nunca 'qualquer'"


# ── 3. A regra da 8777 restringe origem ─────────────────────────────────────
Secao "3. A regra 'FISCALE 8777' restringe a origem"
Reset-Mundo
$script:Respostas.Enqueue('S')
$null = Rodar @{ RedeEscritorio = $FAIXA }
$nova = @(Get-Mutacoes 'New-NetFirewallRule')
Ok ($nova.Count -eq 1) "uma regra criada"
if ($nova.Count -eq 1) {
    $p = $nova[0].P
    Ok ($p.Name -eq 'FISCALE-8777' -and $p.DisplayName -eq 'FISCALE 8777') "nome fixo FISCALE-8777 (é o que o desfazer procura)"
    Ok ($p.Protocol -eq 'TCP' -and (@($p.LocalPort) -join ',') -eq '8777') "TCP 8777"
    Ok ($p.Direction -eq 'Inbound' -and $p.Action -eq 'Allow') "entrada, permitir"
    Ok ((@($p.Perfis) -join ',') -eq 'Private') "só perfil Privado"
    Ok ((@($p.RemoteAddress) -join ',') -eq $FAIXA) "origem = só a faixa confirmada ($FAIXA)"
}
Ok ((Test-OrigemDentro '192.168.1.0/255.255.255.0' $FAIXA)) "origem no formato do Windows (máscara) dentro da faixa é reconhecida"
Ok ((Test-OrigemDentro '192.168.1.40' $FAIXA)) "IP solto dentro da faixa é aceito"
Ok (-not (Test-OrigemDentro '192.168.2.40' $FAIXA)) "IP de outra faixa é recusado"
Ok (-not (Test-OrigemDentro 'Any' $FAIXA)) "'Any' é recusado"
Ok (-not (Test-OrigemDentro 'LocalSubnet' $FAIXA)) "'LocalSubnet' é recusado quando há faixa (ela incluiria a rede do WSL)"
Ok (-not (Test-OrigemDentro '192.168.0.0/16' $FAIXA)) "faixa maior que a autorizada é recusada"
Ok ((Test-OrigemDentro '192.168.1.10-192.168.1.20' $FAIXA)) "intervalo inteiro dentro da faixa é aceito"
Ok (-not (Test-OrigemDentro '192.168.1.10-192.168.2.20' $FAIXA)) "intervalo que escapa da faixa é recusado"

foreach ($caso in @(@('10.0.0.0/8', 'larga demais'), @('8.8.8.0/24', 'não é privada'), @('192.168.1.25/24', 'não é o início'),
                    @('192.168.5.0/24', 'não é de rede conectada'), @('abc', 'formato inválido'))) {
    Reset-Mundo
    $r = Rodar @{ RedeEscritorio = $caso[0] }
    Ok ($r.Codigo -eq 2 -and @(Get-Mutacoes).Count -eq 0) "faixa '$($caso[0])' ($($caso[1])) para com código 2 e nada alterado"
}


# ── 4. Regras amplas não anulam a restrição ─────────────────────────────────
Secao "4. Regras amplas existentes não anulam a restrição"
Reset-Mundo
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA
Ok (@($plano.AlcanceAntes | Where-Object { $_.Origem -eq 'Any' }).Count -gt 0) "(controle) antes, regras do Python liberam a 8777 de QUALQUER origem"
Ok ($plano.Status -eq 'PROTEGE') "o plano restringe essas regras e fica PROTEGE"
Ok (@($plano.AlcanceDepois | Where-Object { -not (Test-OrigemDentro $_.Origem $FAIXA) -or $_.Perfil -ne 'Private' }).Count -eq 0) "depois, TODA regra que alcança a 8777 está no Privado e dentro da faixa"
Ok (@($plano.AlcanceDepois | Where-Object { $_.Regra.Nome -eq 'appx' }).Count -eq 0) "regra de app da Store com 'Programa: Any' não é contada como acesso ao Python"

Reset-Mundo
Add-Regra (New-RegraModelo -Nome 'ampla' -Exibicao 'Ferramenta ampla' -Programa 'Any' -Protocolo Any -Perfis Private -Origem Any)
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA
Ok ($plano.Status -eq 'NAO_PROTEGE' -and ($plano.Problemas -join ' ') -match 'Ferramenta ampla') "regra de terceiro com qualquer programa/porta/origem vira NAO_PROTEGE e é nomeada"
$script:Respostas.Enqueue('S')
$r = Rodar @{ RedeEscritorio = $FAIXA }
Ok ($r.Codigo -eq 3) "aplicar com o plano NAO_PROTEGE para com código 3"
Ok (@(Get-Mutacoes).Count -eq 0 -and $script:Perguntas.Count -eq 0) "nada foi alterado e nem chegou a perguntar"
Ok (@(Get-ArquivosBackup).Count -eq 0) "nem backup foi gravado"

Reset-Mundo
Add-Regra (New-RegraModelo -Nome 'faixa-portas' -Exibicao 'Portas 8000-9000' -Programa 'Any' -PortaLocal '8000-9000' -Perfis Private -Origem Any)
Ok ((New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA).Status -eq 'NAO_PROTEGE') "intervalo de portas que contém 8777 com origem 'Any' também é detectado"

Reset-Mundo
Add-Regra (New-RegraModelo -Nome 'FISCALE-8777' -Exibicao 'FISCALE 8777' -Programa 'Any' -PortaLocal '8777' -Perfis Private -Origem LocalSubnet)
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA
Ok (@($plano.Criar).Count -eq 0 -and @($plano.Restringir | Where-Object { $_.Regra.Nome -eq 'FISCALE-8777' -and (@($_.OrigemPara) -join ',') -eq $FAIXA }).Count -eq 1) "a regra antiga 'FISCALE 8777' (LocalSubnet) é restringida à faixa, não duplicada"

Reset-Mundo
$script:Fw['py-tcp'].Perfis = @('Private'); $script:Fw['py-tcp'].Origem = @('192.168.1.40')
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA
Ok (@($plano.Restringir | Where-Object { $_.Regra.Nome -eq 'py-tcp' }).Count -eq 0) "regra já mais estreita que a política (um IP da faixa) não é ampliada para a faixa inteira"


# ── 5. Rede não confirmada ──────────────────────────────────────────────────
Secao "5. Rede não confirmada não vira Privada"
Reset-Mundo
$script:Respostas.Enqueue(''); $script:Respostas.Enqueue('S')
$r = Rodar
Ok ($r.Codigo -eq 0 -and @(Get-Mutacoes).Count -eq 0) "ENTER na faixa + 'S': cancela (sem rede confirmada, 'S' não basta)"
Ok ($r.Texto -match 'NENHUMA rede confirmada') "a saída diz que nenhuma rede foi confirmada"

Reset-Mundo
$script:Respostas.Enqueue(''); $script:Respostas.Enqueue('SEM REDE')
$r = Rodar
Ok ($r.Codigo -eq 0) "com 'SEM REDE' aplica a parte que não depende de rede"
Ok (@(Get-Mutacoes 'Set-NetConnectionProfile').Count -eq 0 -and (Get-CategoriaDaRede) -eq 'Public') "o perfil da rede NÃO foi alterado (continua Público)"
Ok (@(Get-Mutacoes 'New-NetFirewallRule').Count -eq 0) "a regra 'FISCALE 8777' não foi criada"

Reset-Mundo
$r = Rodar @{ Simular = $true }
Ok ($r.Codigo -eq 0 -and $script:Perguntas.Count -eq 0 -and $r.Texto -match 'NENHUMA rede confirmada') "-Simular sem -RedeEscritorio não pergunta e não confirma rede"
Ok ($null -eq (New-PlanoRede -Estado (Get-EstadoReal) -Faixa '').AlterarPerfil) "plano sem faixa não altera perfil"
Ok ($r.Texto -match 'SSID\s+:\s+Rede Teste' -and $r.Texto -match 'Gateway\s+:\s+192\.168\.1\.1' -and $r.Texto -match 'Faixa\s+:\s+192\.168\.1\.0/24' -and
    $r.Texto -match 'Categoria\s+:\s+Public' -and $r.Texto -match 'Interface\s+:\s+Wi-Fi' -and $r.Texto -match 'IPv4\s+:\s+192\.168\.1\.25/24') "a validação mostra SSID, interface, categoria, IPv4, gateway e faixa"

Reset-Mundo
$script:Respostas.Enqueue('192.168.9.0/24')
$r = Rodar
Ok ($r.Codigo -eq 2 -and @(Get-Mutacoes).Count -eq 0) "faixa digitada que não é da rede conectada: para com código 2"

Reset-Mundo
$script:Redes[0].Categoria = 'Private'
$script:Respostas.Enqueue('S')
$null = Rodar @{ RedeEscritorio = $FAIXA }
Ok (@(Get-Mutacoes 'Set-NetConnectionProfile').Count -eq 0) "rede confirmada que já é Privada: perfil não é mexido"

Reset-Mundo
$script:Respostas.Enqueue($FAIXA); $script:Respostas.Enqueue('S')
$null = Rodar
Ok (@(Get-Mutacoes 'Set-NetConnectionProfile').Count -eq 1 -and (Get-CategoriaDaRede) -eq 'Private') "(controle) só com a faixa digitada e confirmada o perfil vira Privado"


# ── 6. Energia ──────────────────────────────────────────────────────────────
Secao "6. Energia não é alterada"
Reset-Mundo
$script:Inicio = @()
$script:Respostas.Enqueue('S')
$r = Rodar @{ RedeEscritorio = $FAIXA }
Ok ($r.Codigo -eq 0 -and @(Get-Mutacoes 'powercfg').Count -eq 0) "aplicação completa (com tarefa) não chama powercfg"
Ok ($r.Texto -match 'Não alterada\. Este script não mexe em suspensão') "a saída declara que a energia não é alterada"


# ── 7. Órfãs ────────────────────────────────────────────────────────────────
Secao "7. Regras órfãs são desativadas, nunca apagadas"
Reset-Mundo
$antes = @($script:Fw.Keys)
$plano = New-PlanoRede -Estado (Get-EstadoReal) -Faixa $FAIXA
$desat = @($plano.Desativar | Where-Object { $_.Categoria -eq 'ORFA' } | ForEach-Object { $_.Regra.Nome } | Sort-Object)
Ok (($desat -join ',') -eq 'orfa-fiscale,orfa-py') "as duas órfãs (python e fiscale.exe inexistentes) são classificadas"
$script:Respostas.Enqueue('S')
$null = Rodar @{ RedeEscritorio = $FAIXA }
Ok (@(Get-Mutacoes 'Remove-NetFirewallRule').Count -eq 0) "nenhum Remove-NetFirewallRule na aplicação"
Ok (@($antes | Where-Object { -not $script:Fw.Contains($_) }).Count -eq 0) "toda regra que existia continua existindo"
Ok ($script:Fw['orfa-py'].Ativa -eq $false -and $script:Fw['orfa-fiscale'].Ativa -eq $false) "as órfãs ficaram desativadas"
foreach ($n in 'py314', 'node', 'anydesk') {
    Ok ((@($script:Fw[$n].Perfis) -join ',') -eq (@(Get-PerfisDaRegra ($(if ($n -eq 'anydesk') { 'Public' } else { 'Private, Public' }))) -join ',') -and $script:Fw[$n].Ativa) "'$n' (identificada) não foi alterada"
}
Ok (@($plano.Identificadas | ForEach-Object { $_.Categoria } | Sort-Object -Unique) -join ',' -eq 'ANYDESK,NODE,PYTHON_OUTRO') "python de outro sistema, node e AnyDesk aparecem em grupos separados"


# ── 8. Rollback ─────────────────────────────────────────────────────────────
Secao "8. Rollback sem depender de memória"
Reset-Mundo
$script:Inicio = @()
$foto0 = Get-Foto
$script:Respostas.Enqueue('S')
$r = Rodar @{ RedeEscritorio = $FAIXA }
$arqs = @(Get-ArquivosBackup)
$json = @($arqs | Where-Object { $_.Name -like 'rede_*.json' }) | Select-Object -First 1
$wfw = @($arqs | Where-Object { $_.Name -like 'firewall_*.wfw' })
$txt = @($arqs | Where-Object { $_.Name -like 'COMO_DESFAZER_*.txt' }) | Select-Object -First 1
Ok ($r.Codigo -eq 0 -and $json -and $wfw.Count -eq 1 -and $txt) "aplicar gravou exportação completa, registro JSON e instruções"
$ordem = @($script:Chamadas | ForEach-Object { $_.Cmd })
Ok ($ordem.Count -gt 1 -and $ordem[0] -eq 'netsh') "o backup (netsh export) veio ANTES da primeira alteração"
if ($txt) {
    $conteudo = Get-Content -LiteralPath $txt.FullName -Raw -Encoding UTF8
    Ok ($conteudo -match '-Desfazer' -and $conteudo -match 'netsh advfirewall import' -and $conteudo -match 'Unregister-ScheduledTask') "as instruções trazem o comando de desfazer, o import completo e a tarefa"
}
Ok ((Get-Foto) -ne $foto0 -and (Get-CategoriaDaRede) -eq 'Private' -and $script:Tarefas.Count -eq 1) "(controle) o firewall, o perfil e a tarefa mudaram"
if ($json) {
    $script:Chamadas.Clear()
    $script:Admin = $false
    $rs = Rodar @{ Desfazer = $json.FullName; Simular = $true }
    Ok ($rs.Codigo -eq 0 -and @(Get-Mutacoes).Count -eq 0 -and $rs.Texto -match 'SERIA desfeito') "-Desfazer -Simular mostra e não altera (nem exige administrador)"
    $script:Admin = $true
    $script:Respostas.Enqueue('S')
    $rd = Rodar @{ Desfazer = $json.FullName }
    Ok ($rd.Codigo -eq 0) "-Desfazer terminou (código $($rd.Codigo))"
    Ok ((Get-Foto) -eq $foto0) "todas as regras voltaram exatamente ao estado anterior (ativa, perfis, origem)"
    Ok (-not $script:Fw.Contains('FISCALE-8777')) "a regra criada pelo script foi removida"
    Ok ((Get-CategoriaDaRede) -eq 'Public') "a rede voltou a Pública"
    Ok ($script:Tarefas.Count -eq 0) "a tarefa criada foi removida"
}

Reset-Mundo
$foto0 = Get-Foto
$script:NetshFalha = $true
$script:Respostas.Enqueue('S')
$r = Rodar @{ RedeEscritorio = $FAIXA }
Ok ($r.Codigo -eq 4) "se a exportação do firewall falha, para com código 4"
Ok (@(Get-Mutacoes | Where-Object { $_.Cmd -ne 'netsh' }).Count -eq 0 -and (Get-Foto) -eq $foto0 -and (Get-CategoriaDaRede) -eq 'Public') "e nada foi alterado"

Reset-Mundo
$script:Admin = $false
$r = Rodar @{ RedeEscritorio = $FAIXA }
Ok ($r.Codigo -eq 1 -and @(Get-Mutacoes).Count -eq 0) "sem administrador (e sem -Simular) para com código 1 antes de ler qualquer coisa"


# ── 9. -Simular não altera nada ─────────────────────────────────────────────
Secao "9. -Simular não altera nada e não esconde nada"
foreach ($faixaCaso in @($FAIXA, '')) {
    Reset-Mundo
    $script:Inicio = @()
    $script:Admin = $false
    $foto0 = Get-Foto
    $extra = @{ Simular = $true }
    if ($faixaCaso) { $extra['RedeEscritorio'] = $faixaCaso }
    $r = Rodar $extra
    $rotulo = if ($faixaCaso) { 'com rede confirmada' } else { 'sem rede confirmada' }
    Ok ($r.Codigo -eq 0) "$rotulo : termina com código 0 sem administrador"
    Ok (@(Get-Mutacoes).Count -eq 0) "$rotulo : nenhum comando de alteração foi chamado"
    Ok ((Get-Foto) -eq $foto0 -and (Get-CategoriaDaRede) -eq 'Public' -and $script:Tarefas.Count -eq 0) "$rotulo : firewall, perfil e tarefas intactos"
    Ok ($script:Perguntas.Count -eq 0) "$rotulo : não perguntou nada"
    Ok (@(Get-ArquivosBackup).Count -eq 0) "$rotulo : não gravou arquivo"
    Ok ($r.Texto -match 'Simulação: nada foi alterado') "$rotulo : diz que nada foi alterado"
}

Reset-Mundo
$script:Inicio = @()
$sim = Rodar @{ Simular = $true; RedeEscritorio = $FAIXA }
foreach ($secaoEsperada in @('Regras que SERIA CRIADAS', 'Regras que SERIA RESTRINGIDAS', 'Regras que SERIA DESATIVADAS',
                             "Perfil que SERIA alterado: 'Rede Teste 3'", 'Tarefa que SERIA criada', 'Portas afetadas',
                             'Origem permitida pela política: 192\.168\.1\.0/24', 'IDENTIFICADAS e NÃO alteradas', 'Energia', 'DEPOIS:')) {
    Ok ($sim.Texto -match $secaoEsperada) "a simulação mostra: $($secaoEsperada -replace '\\', '')"
}
# Tudo o que a aplicação real muda precisa ter aparecido na simulação.
$script:Respostas.Enqueue('S')
$null = Rodar @{ RedeEscritorio = $FAIXA }
$mudadas = @(Get-Mutacoes | Where-Object { $_.Cmd -match 'NetFirewallRule$' } | ForEach-Object { $_.P.Name } | Sort-Object -Unique)
$ocultas = @($mudadas | Where-Object { $sim.Texto -notmatch [regex]::Escape("id       : $_") })
Ok ($mudadas.Count -gt 0 -and $ocultas.Count -eq 0) "cada uma das $($mudadas.Count) regras alteradas de verdade aparece pelo id na simulação $(if ($ocultas) { '-> faltou: ' + ($ocultas -join ', ') })"
Ok (@(Get-Mutacoes 'Set-NetConnectionProfile').Count -eq 1 -and @(Get-Mutacoes 'Register-ScheduledTask').Count -eq 1) "(controle) perfil e tarefa que a simulação anunciou são os que a aplicação fez"

Reset-Mundo
Add-Regra (New-RegraModelo -Nome 'ampla' -Exibicao 'Ferramenta ampla' -Programa 'Any' -Protocolo Any -Perfis Private -Origem Any)
$script:Admin = $false
$r = Rodar @{ Simular = $true; RedeEscritorio = $FAIXA }
Ok ($r.Codigo -eq 0 -and @(Get-Mutacoes).Count -eq 0 -and $r.Texto -match 'NÃO PROTEGE') "simulação de plano que não protege: mostra NÃO PROTEGE e não altera"


if (Test-Path -LiteralPath $script:PastaTeste) { Remove-Item -LiteralPath $script:PastaTeste -Recurse -Force }
Fim
