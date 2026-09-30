<#
================================================================================
  FISCALE — configurar esta máquina como o servidor do escritório
================================================================================

  ANTES DE RODAR, LEIA ISTO
      Este arquivo ALTERA o firewall do Windows e, se você confirmar a rede,
      o perfil dela. Ele é o único do par que altera — o
      `rede_diagnostico.ps1` só olha.

      Primeiro rode a PRÉVIA, que não altera nada e não exige administrador:
          .\rede_configurar.ps1 -Simular
          .\rede_configurar.ps1 -Simular -RedeEscritorio 192.168.1.0/24

      Na execução de verdade ele mostra a mesma prévia, PERGUNTA, grava um
      backup e só então muda. Nada acontece antes da sua resposta.

  A POLÍTICA QUE ELE IMPLANTA (fase de transição, D72/D97)
      A porta 8777 do FISCALE só é alcançável:
        - no perfil PRIVADO do Windows, e
        - a partir da faixa da REDE DO ESCRITÓRIO confirmada por você.
      Em nenhuma rede Pública, e de nenhuma origem "qualquer".

      Para isso não basta criar uma regra para a 8777: o Windows libera a
      conexão se QUALQUER regra liberar. Por isso o script também restringe
      as regras amplas do próprio Python do FISCALE, e confere, antes de
      aplicar, quem ainda alcançaria a porta depois da mudança. Se sobrar
      alguma regra que abra a 8777 fora da política, ele RECUSA aplicar.

      Depois da implantação do proxy (Caddy HTTPS -> 127.0.0.1:8777), a 8777
      deixa de ser acessível pelas estações. Isso é outra fase, e não é
      feito aqui.

  O QUE ELE MUDA
      1. Perfil da rede do escritório: Público -> Privado
         SÓ se você confirmar a faixa da rede (ex.: 192.168.1.0/24). Rede não
         confirmada não muda de perfil. O nome da rede não é prova de nada.

      2. Regra "FISCALE 8777": TCP 8777, só Privado, só a faixa confirmada.
         Sem rede confirmada, a regra não é criada.

      3. Regras do Python QUE RODA O FISCALE (e do fiscale.exe)
         Identificado por prova: o pyvenv.cfg da venv, o runtime do portátil
         ou o processo que escuta na 8777. Regra que vale no Privado é
         RESTRINGIDA (só Privado + só a faixa); regra que só valia no Público
         é DESATIVADA. Nunca amplia uma regra.

      4. Regras ÓRFÃS (executável que não existe mais) de python, fiscale,
         node e AnyDesk: DESATIVADAS. Nunca apagadas.

      5. Tarefa agendada que sobe o FISCALE no logon: SÓ se não existir
         nenhum mecanismo de inicialização (pasta Inicializar, tarefa ou
         chave Run). Havendo um, nada é criado e o atual não é tocado.

  O QUE ELE **NÃO** FAZ
      - Não mexe em energia (suspensão/hibernação).
      - Não altera Python de outros sistemas, node.exe nem AnyDesk que
        existem: só os IDENTIFICA e mostra a exposição de cada regra.
      - Não apaga regra nenhuma que já existia.
      - Não instala nada, não abre porta em roteador, não toca em dados.

  BACKUP E COMO DESFAZER
      Antes de qualquer mudança grava, em %USERPROFILE%\Fiscale\rede-backup:
        firewall_<data>.wfw      exportação COMPLETA do firewall (netsh)
        rede_<data>.json         o estado anterior de tudo que será mudado
        COMO_DESFAZER_<data>.txt os comandos exatos para voltar
      Se o backup falhar, nada é alterado.

      Para voltar:  .\rede_configurar.ps1 -Desfazer "<...>\rede_<data>.json"
      (com -Simular junto, só mostra o que seria desfeito)

  COMO RODAR
      Botão direito no `rede_configurar.bat` > "Executar como administrador".
      Sem administrador só a prévia (-Simular) funciona.
================================================================================
#>

param(
    [int]$Porta = 8777,
    # Caminho do lançador usado SÓ se for preciso criar a tarefa agendada.
    [string]$Lancador = "$env:USERPROFILE\Fiscale\Iniciar Fiscale.vbs",
    # Faixa da rede do escritório, confirmada por quem roda. Ex.: 192.168.1.0/24
    # Sem ela (e sem digitar na pergunta), a rede NÃO é tratada como do escritório.
    [string]$RedeEscritorio = '',
    [string]$PastaBackup = "$env:USERPROFILE\Fiscale\rede-backup",
    # Caminho de um rede_<data>.json gravado por uma aplicação anterior.
    [string]$Desfazer = '',
    # Mostra tudo o que SERIA feito e sai. Não altera nada.
    [switch]$Simular
)

$NOME_TAREFA = 'FISCALE - Servidor do escritorio'
# Executáveis cujas regras interessam a esta política (pelo NOME do arquivo).
$FOLHAS_DE_INTERESSE = '^(pythonw?[\d\.]*\.exe|fiscale\.exe|node\.exe|anydesk.*\.exe)$'
# O que identifica um mecanismo que já sobe o servidor do FISCALE.
$PADRAO_INICIALIZACAO = 'fiscale_server|Iniciar Fiscale'


# ── Saída ───────────────────────────────────────────────────────────────────
function Titulo($t) {
    Write-Host ""
    Write-Host ("== " + $t + " " + ("=" * [Math]::Max(0, 66 - $t.Length))) -ForegroundColor Cyan
}
function Linha($t)   { Write-Host "  $t" }
function Detalhe($t) { Write-Host "        $t" -ForegroundColor DarkGray }
function Feito($t)   { Write-Host "  [feito] $t" -ForegroundColor Green }
function Alerta($t)  { Write-Host "  [!]     $t" -ForegroundColor Yellow }
function Nada($t)    { Write-Host "  (nenhuma) $t" -ForegroundColor DarkGray }

# Pontos de contato com quem opera. Separados para que os testes possam
# substituí-los sem que nada chegue ao Windows.
function Test-Administrador {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Read-Resposta([string]$pergunta) { return (Read-Host $pergunta) }


# ── Endereços e faixas (funções puras) ──────────────────────────────────────
function ConvertTo-Numero([string]$ip) {
    $b = ([Net.IPAddress]::Parse($ip)).GetAddressBytes()
    if ($b.Count -ne 4) { throw "não é IPv4: $ip" }
    return ([uint64]$b[0] * 16777216) + ([uint64]$b[1] * 65536) + ([uint64]$b[2] * 256) + [uint64]$b[3]
}
function ConvertTo-Ip([uint64]$n) {
    return '{0}.{1}.{2}.{3}' -f (($n -shr 24) -band 255), (($n -shr 16) -band 255), (($n -shr 8) -band 255), ($n -band 255)
}
function Get-Mascara([int]$prefixo) {
    if ($prefixo -le 0) { return [uint64]0 }
    return [uint64](4294967296 - [Math]::Pow(2, 32 - $prefixo))
}
function Get-FaixaDaRede([string]$ip, [int]$prefixo) {
    return "$(ConvertTo-Ip ((ConvertTo-Numero $ip) -band (Get-Mascara $prefixo)))/$prefixo"
}
function Read-Faixa([string]$faixa) {
    if ("$faixa" -notmatch '^(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})$') { return $null }
    $ip = $Matches[1]; $p = [int]$Matches[2]
    if ($p -gt 32) { return $null }
    try { $n = ConvertTo-Numero $ip } catch { return $null }
    return [pscustomobject]@{ Ip = $ip; Numero = $n; Prefixo = $p; Rede = ($n -band (Get-Mascara $p)) }
}
function Test-FaixaDentro([string]$interna, [string]$externa) {
    $i = Read-Faixa $interna; $e = Read-Faixa $externa
    if (-not $i -or -not $e) { return $false }
    if ($i.Prefixo -lt $e.Prefixo) { return $false }
    return (($i.Rede -band (Get-Mascara $e.Prefixo)) -eq $e.Rede)
}
# Devolve '' quando a faixa serve como rede do escritório, ou o motivo de não servir.
function Test-FaixaAceitavel([string]$faixa) {
    $f = Read-Faixa $faixa
    if (-not $f) { return "faixa inválida: '$faixa' (formato esperado: 192.168.1.0/24)" }
    if ($f.Prefixo -lt 16 -or $f.Prefixo -gt 30) { return "a faixa $faixa é larga ou estreita demais (aceito de /16 a /30)" }
    if ($f.Numero -ne $f.Rede) { return "$faixa não começa no início da faixa; o correto seria $(ConvertTo-Ip $f.Rede)/$($f.Prefixo)" }
    foreach ($privada in '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10') {
        if (Test-FaixaDentro $faixa $privada) { return '' }
    }
    return "$faixa não é faixa de rede privada (10/8, 172.16/12, 192.168/16, 100.64/10)"
}
# O firewall devolve origens como "192.168.1.0/255.255.255.0", IP solto,
# intervalo "a-b" ou palavra-chave (Any, LocalSubnet, Internet...).
function ConvertTo-FaixaNormal([string]$origem) {
    $o = "$origem".Trim()
    if ($o -match '^(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,3}(?:\.\d{1,3}){3})$') {
        $ip = $Matches[1]; $m = ConvertTo-Numero $Matches[2]; $p = 0
        for ($b = 31; $b -ge 0; $b--) { if ((($m -shr $b) -band 1) -eq 1) { $p++ } else { break } }
        return "$ip/$p"
    }
    if ($o -match '^\d{1,3}(?:\.\d{1,3}){3}$') { return "$o/32" }
    return $o
}
# A origem está inteira dentro do que foi autorizado?
function Test-OrigemDentro([string]$origem, [string]$autorizada) {
    if ($autorizada -eq 'LocalSubnet') { return ("$origem" -eq 'LocalSubnet') }
    $n = ConvertTo-FaixaNormal $origem
    if ($n -match '^(\d{1,3}(?:\.\d{1,3}){3})-(\d{1,3}(?:\.\d{1,3}){3})$') {
        $a = $Matches[1]; $z = $Matches[2]
        return ((Test-FaixaDentro "$a/32" $autorizada) -and (Test-FaixaDentro "$z/32" $autorizada))
    }
    return (Test-FaixaDentro $n $autorizada)
}


# ── Modelo de regra (independente do Windows) ───────────────────────────────
function Get-PerfisDaRegra($perfil) {
    $t = "$perfil"
    if ($t -eq '' -or $t -eq 'Any' -or $t -eq 'All') { return @('Domain', 'Private', 'Public') }
    return @($t -split '\s*,\s*' | Where-Object { $_ })
}
function ConvertTo-Lista($v) {
    if ($null -eq $v) { return @() }
    return @($v | ForEach-Object { "$_" } | Where-Object { $_ -ne '' })
}
function New-RegraModelo {
    param(
        [string]$Nome, [string]$Exibicao = '', [bool]$Ativa = $true,
        [string]$Direcao = 'Inbound', [string]$Acao = 'Allow',
        [string[]]$Perfis = @('Private', 'Public'), [string]$Programa = 'Any',
        [string]$Protocolo = 'TCP', [string[]]$PortaLocal = @('Any'),
        [string[]]$Origem = @('Any'), $ExeExiste = $null,
        # Regra de app da Store, de serviço do Windows ou de pacote: não vale
        # para um python.exe comum, mesmo com "Programa: Any".
        [bool]$Restrita = $false,
        [bool]$LeituraIncompleta = $false
    )
    if (-not $Exibicao) { $Exibicao = $Nome }
    return [pscustomobject]@{
        Nome = $Nome; Exibicao = $Exibicao; Ativa = $Ativa; Direcao = $Direcao; Acao = $Acao
        Perfis = @($Perfis); Programa = $Programa; Protocolo = $Protocolo
        PortaLocal = @($PortaLocal); Origem = @($Origem); ExeExiste = $ExeExiste
        Restrita = $Restrita; LeituraIncompleta = $LeituraIncompleta
    }
}
function Copy-Regra($r) {
    return New-RegraModelo -Nome $r.Nome -Exibicao $r.Exibicao -Ativa $r.Ativa -Direcao $r.Direcao `
        -Acao $r.Acao -Perfis @($r.Perfis) -Programa $r.Programa -Protocolo $r.Protocolo `
        -PortaLocal @($r.PortaLocal) -Origem @($r.Origem) -ExeExiste $r.ExeExiste `
        -Restrita $r.Restrita -LeituraIncompleta $r.LeituraIncompleta
}
function Test-CobrePorta($r, [int]$porta) {
    if (@('Any', 'TCP', '6') -notcontains "$($r.Protocolo)") { return $false }
    foreach ($p in @($r.PortaLocal)) {
        if ($p -eq 'Any') { return $true }
        if ($p -match '^\d+$' -and [int]$p -eq $porta) { return $true }
        if ($p -match '^(\d+)-(\d+)$') {
            $de = [int]$Matches[1]; $ate = [int]$Matches[2]
            if ($porta -ge $de -and $porta -le $ate) { return $true }
        }
    }
    return $false
}
function Test-CobrePrograma($r, $exes) {
    if ($r.Programa -eq 'Any') { return (-not $r.Restrita) }
    foreach ($e in @($exes)) { if ("$($r.Programa)" -ieq "$e") { return $true } }
    return $false
}
function Test-EhRegraDaPorta($r, [int]$porta) {
    return ($r.Programa -eq 'Any' -and -not $r.Restrita -and
            ($r.Nome -eq "FISCALE-$porta" -or $r.Exibicao -like 'FISCALE *') -and
            (Test-CobrePorta $r $porta))
}
function Get-Categoria($r, $exesServidor, [int]$porta) {
    if (Test-EhRegraDaPorta $r $porta) { return 'FISCALE_PORTA' }
    if ($r.Programa -eq 'Any') { return '' }
    if (Test-CobrePrograma $r $exesServidor) {
        if ($r.ExeExiste -eq $false) { return 'ORFA' }
        return 'FISCALE_SERVIDOR'
    }
    $folha = [IO.Path]::GetFileName("$($r.Programa)").ToLower()
    if ($folha -notmatch $FOLHAS_DE_INTERESSE) { return '' }
    if ($r.ExeExiste -eq $false) { return 'ORFA' }
    if ($folha -eq 'fiscale.exe') { return 'FISCALE_EXE' }
    if ($folha -eq 'node.exe') { return 'NODE' }
    if ($folha -like 'anydesk*') { return 'ANYDESK' }
    return 'PYTHON_OUTRO'
}
# Quem alcança a porta: uma linha por (perfil, origem, regra) que libera.
function Get-Alcance($regras, $exes, [int]$porta) {
    $itens = @()
    foreach ($r in @($regras)) {
        if (-not $r.Ativa -or $r.Direcao -ne 'Inbound' -or $r.Acao -ne 'Allow') { continue }
        if (-not (Test-CobrePorta $r $porta)) { continue }
        if (-not (Test-CobrePrograma $r $exes)) { continue }
        foreach ($perfil in @($r.Perfis)) {
            foreach ($o in @($r.Origem)) {
                $itens += [pscustomobject]@{ Perfil = $perfil; Origem = $o; Regra = $r }
            }
        }
    }
    return $itens
}


# ── Plano (função pura: recebe o estado, devolve o que seria feito) ─────────
function New-PlanoRede {
    param($Estado, [string]$Faixa = '', [int]$Porta = 8777)

    $alvo = if ($Faixa) { $Faixa } else { 'LocalSubnet' }
    $exes = @($Estado.ExesServidor | ForEach-Object { $_.Caminho })
    $plano = [pscustomobject]@{
        Porta = $Porta; Faixa = $Faixa; OrigemAlvo = $alvo; RedeAlvo = $null
        AlterarPerfil = $null; Criar = @(); Restringir = @(); Desativar = @()
        Identificadas = @(); Tarefa = $null; Inicializacao = @($Estado.Inicializacao)
        Exes = @($Estado.ExesServidor); AlcanceAntes = @(); AlcanceDepois = @()
        Avisos = @(); Problemas = @(); Status = ''
    }

    # 1. Rede
    if ($Faixa) {
        $rede = @($Estado.Redes | Where-Object { $_.Faixa -eq $Faixa }) | Select-Object -First 1
        if (-not $rede) {
            $plano.Problemas += "a faixa $Faixa não é de nenhuma rede conectada agora"
        } else {
            $plano.RedeAlvo = $rede
            if ($rede.Categoria -eq 'Public') {
                $plano.AlterarPerfil = [pscustomobject]@{
                    InterfaceIndex = $rede.InterfaceIndex; Nome = $rede.Nome
                    Interface = $rede.Interface; De = 'Public'; Para = 'Private'
                }
            }
        }
    } else {
        foreach ($r in @($Estado.Redes | Where-Object { $_.Categoria -eq 'Public' })) {
            $plano.Avisos += "rede '$($r.Nome)' ($($r.Faixa)) NÃO confirmada: o perfil continua Público e, com a política aplicada, as estações nela deixam de alcançar a porta $Porta"
        }
    }

    if (-not $exes.Count) {
        $plano.Problemas += "não identifiquei o Python que roda o FISCALE (sem venv, sem runtime do portátil e nada escutando na $Porta)"
    }
    foreach ($e in @($Estado.ExesServidor)) {
        if (@($e.Provas | Where-Object { $_ -notlike 'processo que escuta*' }).Count -eq 0) {
            $plano.Avisos += "o processo na porta $Porta ($($e.Caminho)) não é da instalação detectada; tratado como servidor do FISCALE mesmo assim"
        }
    }

    # 2. Regras existentes
    $temRegraDaPorta = $false
    foreach ($r in @($Estado.Regras)) {
        if ($r.Direcao -ne 'Inbound' -or $r.Acao -ne 'Allow') { continue }
        if ($r.LeituraIncompleta) {
            $plano.Avisos += "não consegui ler todos os filtros da regra '$($r.Exibicao)'; ela foi tratada como aberta (qualquer porta e origem)"
        }
        $cat = Get-Categoria $r $exes $Porta
        if ($cat -eq 'FISCALE_PORTA') { $temRegraDaPorta = $true }
        if (-not $r.Ativa) {
            if ($cat -eq 'FISCALE_PORTA') {
                $plano.Avisos += "a regra '$($r.Exibicao)' existe e está DESATIVADA; não é reativada nem recriada"
            }
            continue
        }
        switch ($cat) {
            { $_ -in 'FISCALE_SERVIDOR', 'FISCALE_EXE', 'FISCALE_PORTA' } {
                if (@($r.Perfis) -notcontains 'Private') {
                    # Só valia fora do Privado: passar para o Privado seria AMPLIAR.
                    $plano.Desativar += [pscustomobject]@{ Regra = $r; Categoria = $cat; Motivo = "só valia no perfil $(@($r.Perfis) -join ', ')" }
                    break
                }
                $dentro = @($r.Origem | Where-Object { Test-OrigemDentro $_ $alvo })
                $origemPara = if ($dentro.Count) { $dentro } else { @($alvo) }
                $mudaPerfil = (@($r.Perfis).Count -ne 1)
                $mudaOrigem = ($dentro.Count -ne @($r.Origem).Count)
                if ($mudaPerfil -or $mudaOrigem) {
                    $plano.Restringir += [pscustomobject]@{
                        Regra = $r; Categoria = $cat
                        PerfisDe = @($r.Perfis); PerfisPara = @('Private')
                        OrigemDe = @($r.Origem); OrigemPara = @($origemPara)
                    }
                }
            }
            'ORFA' {
                $plano.Desativar += [pscustomobject]@{ Regra = $r; Categoria = 'ORFA'; Motivo = 'o executável não existe mais' }
            }
            { $_ -in 'PYTHON_OUTRO', 'NODE', 'ANYDESK' } {
                $plano.Identificadas += [pscustomobject]@{ Regra = $r; Categoria = $cat }
            }
        }
    }

    # 3. Regra da porta
    if ($Faixa -and -not $temRegraDaPorta) {
        $plano.Criar += [pscustomobject]@{
            Nome = "FISCALE-$Porta"; Exibicao = "FISCALE $Porta"; Protocolo = 'TCP'
            PortaLocal = @("$Porta"); Perfis = @('Private'); Origem = @($Faixa)
        }
    } elseif (-not $Faixa) {
        $plano.Avisos += "sem rede confirmada, a regra 'FISCALE $Porta' não é criada e a origem das regras restringidas fica LocalSubnet (provisório)"
    }

    # 4. Inicialização
    if (@($Estado.Inicializacao).Count -gt 0) {
        # Já sobe sozinho: uma segunda inicialização disputaria a mesma porta.
    } elseif ($Estado.TarefaExiste) {
        # A tarefa com este nome já existe; não é recriada.
    } elseif (-not $Estado.LancadorExiste) {
        $plano.Avisos += "nenhuma inicialização automática encontrada e o lançador não existe em $($Estado.Lancador); tarefa não criada"
    } else {
        $plano.Tarefa = [pscustomobject]@{ Nome = $NOME_TAREFA; Lancador = $Estado.Lancador }
    }

    # 5. Quem alcança a porta, antes e depois — é isto que prova a política.
    $plano.AlcanceAntes = @(Get-Alcance $Estado.Regras $exes $Porta)
    $depois = @(Invoke-PlanoSobreRegras $Estado.Regras $plano)
    $plano.AlcanceDepois = @(Get-Alcance $depois $exes $Porta)
    foreach ($a in $plano.AlcanceDepois) {
        if ($a.Perfil -ne 'Private') {
            $plano.Problemas += "depois da mudança a porta $Porta ainda seria alcançável no perfil $($a.Perfil) pela regra '$($a.Regra.Exibicao)' [$($a.Regra.Nome)]"
        } elseif (-not (Test-OrigemDentro $a.Origem $alvo)) {
            $plano.Problemas += "depois da mudança a porta $Porta ainda aceitaria a origem '$($a.Origem)' pela regra '$($a.Regra.Exibicao)' [$($a.Regra.Nome)]"
        }
    }

    $plano.Status = if ($plano.Problemas.Count) { 'NAO_PROTEGE' } elseif ($Faixa) { 'PROTEGE' } else { 'PROVISORIO' }
    return $plano
}

# Aplica o plano sobre CÓPIAS das regras. Não toca no Windows.
function Invoke-PlanoSobreRegras($regras, $plano) {
    $restr = @{}; foreach ($i in @($plano.Restringir)) { $restr[$i.Regra.Nome] = $i }
    $desat = @{}; foreach ($i in @($plano.Desativar)) { $desat[$i.Regra.Nome] = $true }
    $saida = @()
    foreach ($r in @($regras)) {
        $c = Copy-Regra $r
        if ($restr.ContainsKey($c.Nome)) { $c.Perfis = @($restr[$c.Nome].PerfisPara); $c.Origem = @($restr[$c.Nome].OrigemPara) }
        if ($desat.ContainsKey($c.Nome)) { $c.Ativa = $false }
        $saida += $c
    }
    foreach ($n in @($plano.Criar)) {
        $saida += New-RegraModelo -Nome $n.Nome -Exibicao $n.Exibicao -Perfis $n.Perfis `
            -Protocolo $n.Protocolo -PortaLocal $n.PortaLocal -Origem $n.Origem -ExeExiste $null
    }
    return $saida
}

function Test-PlanoVazio($p) {
    return (-not $p.AlterarPerfil -and -not @($p.Criar).Count -and -not @($p.Restringir).Count -and
            -not @($p.Desativar).Count -and -not $p.Tarefa)
}


# ── Mostrar ─────────────────────────────────────────────────────────────────
function Format-Portas($r) {
    $portas = if (@($r.PortaLocal) -contains 'Any') { 'qualquer porta' } else { 'porta ' + (@($r.PortaLocal) -join ',') }
    $proto = if ("$($r.Protocolo)" -eq 'Any') { 'qualquer protocolo' } else { "$($r.Protocolo)" }
    return "$proto $portas"
}
function Format-Origem($lista) {
    return ((@($lista) | ForEach-Object { if ($_ -eq 'Any') { 'QUALQUER origem' } else { $_ } }) -join ', ')
}
function Show-Regra($r) {
    Linha "- $($r.Exibicao)"
    Detalhe "id       : $($r.Nome)"
    $sufixo = if ($r.ExeExiste -eq $false) { '   [executável NÃO existe]' } else { '' }
    Detalhe "programa : $($r.Programa)$sufixo"
    Detalhe "portas   : $(Format-Portas $r)"
}
function Show-Redes($redes) {
    Titulo "Redes conectadas (nenhuma é tratada como do escritório sem confirmação)"
    if (-not @($redes).Count) { Nada "rede conectada" }
    foreach ($r in @($redes)) {
        Linha "- Rede do Windows : $($r.Nome)"
        Detalhe "SSID        : $(if ($r.Ssid) { $r.Ssid } else { '(não é Wi-Fi ou não disponível)' })"
        Detalhe "Interface   : $($r.Interface) (índice $($r.InterfaceIndex))"
        Detalhe "Categoria   : $($r.Categoria)"
        Detalhe "IPv4        : $($r.IPv4)/$($r.Prefixo)"
        Detalhe "Gateway     : $($r.Gateway)"
        Detalhe "Faixa       : $($r.Faixa)"
        $motivo = if ($r.Faixa) { Test-FaixaAceitavel $r.Faixa } else { 'sem IPv4' }
        if ($motivo) { Detalhe "não pode ser confirmada: $motivo" }
    }
}
function Show-Plano($p, [bool]$simulacao) {
    $verbo = if ($simulacao) { 'SERIA' } else { 'SERÁ' }

    Titulo "1. Rede do escritório e perfil"
    if ($p.Faixa) {
        Linha "Faixa confirmada: $($p.Faixa)"
        if ($p.RedeAlvo) { Detalhe "rede '$($p.RedeAlvo.Nome)' · $($p.RedeAlvo.Interface) · $($p.RedeAlvo.Categoria)" }
    } else {
        Alerta "NENHUMA rede confirmada como do escritório"
    }
    if ($p.AlterarPerfil) {
        Linha "Perfil que $verbo alterado: '$($p.AlterarPerfil.Nome)' ($($p.AlterarPerfil.Interface)): Público -> Privado"
    } else { Nada "alteração de perfil de rede" }

    Titulo "2. Regras que $verbo CRIADAS"
    if (-not @($p.Criar).Count) { Nada "regra criada" }
    foreach ($c in @($p.Criar)) {
        Linha "- $($c.Exibicao)"
        Detalhe "id       : $($c.Nome)"
        Detalhe "portas   : $($c.Protocolo) porta $(@($c.PortaLocal) -join ',') (entrada, permitir)"
        Detalhe "perfis   : $(@($c.Perfis) -join ', ')"
        Detalhe "origem   : $(Format-Origem $c.Origem)"
    }

    Titulo "3. Regras que $verbo RESTRINGIDAS (FISCALE)"
    if (-not @($p.Restringir).Count) { Nada "regra restringida" }
    foreach ($i in @($p.Restringir)) {
        Show-Regra $i.Regra
        Detalhe "motivo   : $($i.Categoria)"
        Detalhe "perfis   : $(@($i.PerfisDe) -join ', ')  ->  $(@($i.PerfisPara) -join ', ')"
        Detalhe "origem   : $(Format-Origem $i.OrigemDe)  ->  $(Format-Origem $i.OrigemPara)"
    }

    Titulo "4. Regras que $verbo DESATIVADAS (nenhuma é apagada)"
    if (-not @($p.Desativar).Count) { Nada "regra desativada" }
    foreach ($i in @($p.Desativar)) {
        Show-Regra $i.Regra
        Detalhe "motivo   : $($i.Categoria) — $($i.Motivo)"
        Detalhe "hoje     : ativa, perfis $(@($i.Regra.Perfis) -join ', '), origem $(Format-Origem $i.Regra.Origem)"
    }

    Titulo "5. Regras IDENTIFICADAS e NÃO alteradas"
    if (-not @($p.Identificadas).Count) { Nada "regra identificada" }
    foreach ($grupo in @('PYTHON_OUTRO', 'NODE', 'ANYDESK')) {
        $doGrupo = @($p.Identificadas | Where-Object { $_.Categoria -eq $grupo })
        if (-not $doGrupo.Count) { continue }
        $rotulo = switch ($grupo) { 'PYTHON_OUTRO' { 'Python que NÃO é o do FISCALE' } 'NODE' { 'node.exe' } 'ANYDESK' { 'AnyDesk' } }
        Linha "$rotulo :"
        foreach ($i in $doGrupo) {
            Show-Regra $i.Regra
            $pub = if (@($i.Regra.Perfis) -contains 'Public') { '   [ABERTA NO PÚBLICO]' } else { '' }
            Detalhe "exposição: perfis $(@($i.Regra.Perfis) -join ', '), origem $(Format-Origem $i.Regra.Origem)$pub"
        }
    }

    Titulo "6. Inicialização automática e tarefas agendadas"
    if (@($p.Inicializacao).Count) {
        Linha "O FISCALE JÁ sobe sozinho por:"
        foreach ($m in @($p.Inicializacao)) { Detalhe "$($m.Tipo): $($m.Onde)" }
        Linha "Nenhuma tarefa agendada $verbo criada, e o mecanismo atual não é alterado."
    } elseif ($p.Tarefa) {
        Linha "Tarefa que $verbo criada: '$($p.Tarefa.Nome)' (ao entrar no Windows, como $env:USERNAME)"
        Detalhe "executa: wscript.exe `"$($p.Tarefa.Lancador)`""
    } else { Nada "tarefa agendada criada" }

    Titulo "7. Energia"
    Linha "Não alterada. Este script não mexe em suspensão, hibernação nem plano de energia."

    Titulo "8. Executáveis reconhecidos como servidor do FISCALE"
    if (-not @($p.Exes).Count) { Nada "executável identificado" }
    foreach ($e in @($p.Exes)) {
        Linha "- $($e.Caminho)"
        foreach ($prova in @($e.Provas)) { Detalhe "prova: $prova" }
    }

    Titulo "9. Portas afetadas"
    $afetadas = @(@($p.Restringir | ForEach-Object { $_.Regra }) + @($p.Desativar | ForEach-Object { $_.Regra }) |
                  ForEach-Object { Format-Portas $_ } | Sort-Object -Unique)
    foreach ($c in @($p.Criar)) { $afetadas += "$($c.Protocolo) porta $(@($c.PortaLocal) -join ',') (regra nova)" }
    if (-not $afetadas.Count) { Nada "porta afetada" }
    foreach ($a in $afetadas) { Linha "- $a" }
    Detalhe "(regra por aplicativo com 'qualquer porta' vale para toda porta que aquele programa abrir)"

    Titulo "10. Quem alcança a porta $($p.Porta) (servidor do FISCALE)"
    foreach ($fase in @(@('ANTES', $p.AlcanceAntes), @('DEPOIS', $p.AlcanceDepois))) {
        Linha "$($fase[0]):"
        $lista = @($fase[1])
        if (-not $lista.Count) { Detalhe "ninguém de fora desta máquina" }
        foreach ($g in @($lista | Group-Object { "$($_.Perfil)|$($_.Origem)" })) {
            $perfil, $origem = $g.Name -split '\|', 2
            $nomes = @($g.Group | ForEach-Object { $_.Regra.Exibicao } | Sort-Object -Unique) -join '; '
            Detalhe ("perfil {0,-8} origem {1,-22} por: {2}" -f $perfil, (Format-Origem @($origem)), $nomes)
        }
    }
    Linha "Origem permitida pela política: $(Format-Origem @($p.OrigemAlvo)), só no perfil Privado"

    Titulo "11. Resultado da conferência"
    foreach ($a in @($p.Avisos)) { Alerta $a }
    foreach ($x in @($p.Problemas)) { Write-Host "  [ERRO]  $x" -ForegroundColor Red }
    switch ($p.Status) {
        'PROTEGE'     { Write-Host "  PROTEGE: depois da mudança a $($p.Porta) só aceita a faixa $($p.Faixa), no perfil Privado." -ForegroundColor Green }
        'PROVISORIO'  { Write-Host "  PROVISÓRIO: sem rede confirmada a $($p.Porta) sai do Público, mas nenhuma estação fica autorizada." -ForegroundColor Yellow }
        'NAO_PROTEGE' { Write-Host "  NÃO PROTEGE: o plano não seria aplicado (veja os erros acima)." -ForegroundColor Red }
    }
}


# ── Leitura do Windows (só leitura) ─────────────────────────────────────────
function Get-RedesConectadas {
    $ssids = @{}
    try {
        $atual = ''
        foreach ($l in @(netsh wlan show interfaces 2>$null)) {
            if ($l -match '^\s*(Nome|Name)\s*:\s*(.+?)\s*$') { $atual = $Matches[2] }
            elseif ($l -match '^\s*SSID\s*:\s*(.+?)\s*$' -and $atual) { $ssids[$atual] = $Matches[1] }
        }
    } catch { }
    $redes = @()
    foreach ($p in @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)) {
        $ip = @(Get-NetIPAddress -InterfaceIndex $p.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.IPAddress -notlike '169.254.*' }) | Select-Object -First 1
        $gw = @((Get-NetIPConfiguration -InterfaceIndex $p.InterfaceIndex -ErrorAction SilentlyContinue).IPv4DefaultGateway) | Select-Object -First 1
        $redes += [pscustomobject]@{
            InterfaceIndex = $p.InterfaceIndex; Nome = $p.Name; Interface = $p.InterfaceAlias
            Ssid = $(if ($ssids.ContainsKey($p.InterfaceAlias)) { $ssids[$p.InterfaceAlias] } else { '' })
            Categoria = "$($p.NetworkCategory)"
            IPv4 = $(if ($ip) { $ip.IPAddress } else { '' })
            Prefixo = $(if ($ip) { [int]$ip.PrefixLength } else { 0 })
            Gateway = $(if ($gw) { $gw.NextHop } else { '' })
            Faixa = $(if ($ip) { Get-FaixaDaRede $ip.IPAddress $ip.PrefixLength } else { '' })
        }
    }
    return $redes
}

function Get-ExecutaveisDoServidor([string]$raiz, [int]$porta) {
    $cands = @()
    $cfg = Join-Path $raiz 'nfse\.venv\pyvenv.cfg'
    if (Test-Path -LiteralPath $cfg) {
        $base = ''
        foreach ($l in @(Get-Content -LiteralPath $cfg)) { if ($l -match '^\s*home\s*=\s*(.+?)\s*$') { $base = $Matches[1] } }
        foreach ($n in 'python.exe', 'pythonw.exe') {
            if ($base) { $cands += [pscustomobject]@{ Caminho = (Join-Path $base $n); Prova = "Python base da venv do FISCALE ($cfg)" } }
            $cands += [pscustomobject]@{ Caminho = (Join-Path $raiz "nfse\.venv\Scripts\$n"); Prova = 'lançador da venv do FISCALE' }
        }
    }
    $runtime = Join-Path (Split-Path -Parent $raiz) 'runtime'
    foreach ($n in 'python.exe', 'pythonw.exe') {
        $cands += [pscustomobject]@{ Caminho = (Join-Path $runtime $n); Prova = 'runtime do FISCALE-Portable' }
    }
    foreach ($c in @(Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue)) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
        if ($proc -and $proc.ExecutablePath) {
            $cands += [pscustomobject]@{ Caminho = $proc.ExecutablePath; Prova = "processo que escuta na porta $porta agora (PID $($c.OwningProcess))" }
        }
    }
    $vistos = [ordered]@{}
    foreach ($c in $cands) {
        if (-not (Test-Path -LiteralPath $c.Caminho -PathType Leaf)) { continue }
        $k = $c.Caminho.ToLower()
        if ($vistos.Contains($k)) {
            if (@($vistos[$k].Provas) -notcontains $c.Prova) { $vistos[$k].Provas += $c.Prova }
        } else {
            $vistos[$k] = [pscustomobject]@{ Caminho = $c.Caminho; Provas = @($c.Prova) }
        }
    }
    return @($vistos.Values)
}

function Get-RegrasDeEntrada($exes) {
    # Leitura em bloco. Sem administrador ela pode vir incompleta; o que faltar
    # é lido regra a regra, e o que ainda assim faltar é tratado como ABERTO.
    $idx = @{}
    foreach ($tipo in 'Application', 'Port', 'Address', 'Service') {
        $idx[$tipo] = @{}
        $itens = switch ($tipo) {
            'Application' { Get-NetFirewallApplicationFilter -All -ErrorAction SilentlyContinue }
            'Port'        { Get-NetFirewallPortFilter -All -ErrorAction SilentlyContinue }
            'Address'     { Get-NetFirewallAddressFilter -All -ErrorAction SilentlyContinue }
            'Service'     { Get-NetFirewallServiceFilter -All -ErrorAction SilentlyContinue }
        }
        foreach ($f in @($itens)) { if ($f) { $idx[$tipo][$f.InstanceID] = $f } }
    }
    $folhasServidor = @($exes | ForEach-Object { [IO.Path]::GetFileName("$_").ToLower() })
    $saida = @()
    foreach ($r in @(Get-NetFirewallRule -Direction Inbound -Action Allow -ErrorAction SilentlyContinue)) {
        $app = $idx['Application'][$r.Name]
        if (-not $app) { $app = $r | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue }
        $svc = $idx['Service'][$r.Name]
        if (-not $svc) { $svc = $r | Get-NetFirewallServiceFilter -ErrorAction SilentlyContinue }
        $programa = if ($app -and $app.Program -and $app.Program -ne 'Any') { [Environment]::ExpandEnvironmentVariables($app.Program) } else { 'Any' }
        $folha = if ($programa -ne 'Any') { [IO.Path]::GetFileName($programa).ToLower() } else { '' }
        $restrita = ($app -and "$($app.Package)") -or ($svc -and $svc.Service -and $svc.Service -ne 'Any') -or
                    ($programa -eq 'Any' -and "$($r.Owner)")
        $relevante = ($programa -eq 'Any' -and -not $restrita) -or ($folha -match $FOLHAS_DE_INTERESSE) -or
                     ($folhasServidor -contains $folha) -or ("$($r.DisplayName)" -like 'FISCALE *')
        if (-not $relevante) { continue }

        $por = $idx['Port'][$r.Name]
        if (-not $por) { $por = $r | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue }
        $end = $idx['Address'][$r.Name]
        if (-not $end) { $end = $r | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue }
        $incompleta = (-not $por) -or (-not $end)

        $existe = $null
        if ($programa -ne 'Any' -and $programa -notmatch '\\WindowsApps\\' -and $programa -notmatch '%') {
            try { $existe = [bool](Test-Path -LiteralPath $programa -PathType Leaf) } catch { $existe = $null }
        }
        $saida += New-RegraModelo -Nome $r.Name -Exibicao "$($r.DisplayName)" -Ativa ("$($r.Enabled)" -eq 'True') `
            -Direcao 'Inbound' -Acao 'Allow' -Perfis @(Get-PerfisDaRegra $r.Profile) -Programa $programa `
            -Protocolo $(if ($por) { "$($por.Protocol)" } else { 'Any' }) `
            -PortaLocal $(if ($por) { @(ConvertTo-Lista $por.LocalPort) } else { @('Any') }) `
            -Origem $(if ($end) { @(ConvertTo-Lista $end.RemoteAddress) } else { @('Any') }) `
            -ExeExiste $existe -Restrita ([bool]$restrita) -LeituraIncompleta $incompleta
    }
    return $saida
}

function Get-InicializacaoDoFiscale {
    $achados = @()
    foreach ($pasta in @([Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('CommonStartup'))) {
        if (-not $pasta -or -not (Test-Path -LiteralPath $pasta)) { continue }
        foreach ($f in @(Get-ChildItem -LiteralPath $pasta -File -ErrorAction SilentlyContinue)) {
            $conteudo = ''
            if ($f.Extension -ieq '.lnk') {
                # CreateShortcut sobre um .lnk existente só LÊ; nada é salvo.
                try { $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($f.FullName); $conteudo = "$($lnk.TargetPath) $($lnk.Arguments)" } catch { }
            } elseif ($f.Extension -match '^\.(vbs|bat|cmd|ps1)$') {
                try { $conteudo = Get-Content -LiteralPath $f.FullName -Raw -ErrorAction Stop } catch { }
            }
            if ($f.Name -match $PADRAO_INICIALIZACAO -or $conteudo -match $PADRAO_INICIALIZACAO) {
                $achados += [pscustomobject]@{ Tipo = 'Pasta Inicializar'; Onde = $f.FullName }
            }
        }
    }
    foreach ($t in @(Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        $acoes = (@($t.Actions) | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' ; '
        if ($acoes -match $PADRAO_INICIALIZACAO) {
            $achados += [pscustomobject]@{ Tipo = 'Tarefa agendada'; Onde = "$($t.TaskName) ($($t.State))" }
        }
    }
    foreach ($k in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run') {
        $v = Get-ItemProperty -Path $k -ErrorAction SilentlyContinue
        if (-not $v) { continue }
        foreach ($prop in $v.PSObject.Properties) {
            if ($prop.Name -notlike 'PS*' -and "$($prop.Value)" -match $PADRAO_INICIALIZACAO) {
                $achados += [pscustomobject]@{ Tipo = 'Registro Run'; Onde = "$k\$($prop.Name)" }
            }
        }
    }
    return $achados
}

function Get-EstadoReal([int]$Porta, [string]$Lancador, [string]$Raiz) {
    $exes = @(Get-ExecutaveisDoServidor $Raiz $Porta)
    return [pscustomobject]@{
        Computador = $env:COMPUTERNAME
        Redes = @(Get-RedesConectadas)
        ExesServidor = $exes
        Regras = @(Get-RegrasDeEntrada @($exes | ForEach-Object { $_.Caminho }))
        Inicializacao = @(Get-InicializacaoDoFiscale)
        TarefaExiste = [bool](Get-ScheduledTask -TaskName $NOME_TAREFA -ErrorAction SilentlyContinue)
        LancadorExiste = [bool](Test-Path -LiteralPath $Lancador)
        Lancador = $Lancador
    }
}


# ── Backup, aplicação e desfazer (os ÚNICOS trechos que alteram algo) ───────
function Save-BackupRede($Estado, $Plano, [string]$Pasta, [string]$Script) {
    $carimbo = Get-Date -Format 'yyyyMMdd_HHmmss'
    New-Item -ItemType Directory -Force -Path $Pasta | Out-Null
    $wfw = Join-Path $Pasta "firewall_$carimbo.wfw"
    $json = Join-Path $Pasta "rede_$carimbo.json"
    $txt = Join-Path $Pasta "COMO_DESFAZER_$carimbo.txt"

    $saidaNetsh = & netsh advfirewall export "$wfw" 2>&1
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $wfw)) {
        throw "a exportação completa do firewall falhou: $saidaNetsh"
    }

    $regras = @()
    foreach ($i in @($Plano.Restringir)) {
        $regras += [ordered]@{ nome = $i.Regra.Nome; exibicao = $i.Regra.Exibicao; mudanca = 'restringir'
                               ativa = [bool]$i.Regra.Ativa; perfis = @($i.Regra.Perfis); origem = @($i.Regra.Origem) }
    }
    foreach ($i in @($Plano.Desativar)) {
        $regras += [ordered]@{ nome = $i.Regra.Nome; exibicao = $i.Regra.Exibicao; mudanca = 'desativar'
                               ativa = [bool]$i.Regra.Ativa; perfis = @($i.Regra.Perfis); origem = @($i.Regra.Origem) }
    }
    $registro = [ordered]@{
        versao = 1
        criado = (Get-Date).ToString('s')
        computador = $Estado.Computador
        porta = $Plano.Porta
        faixa = $Plano.Faixa
        exportacaoCompleta = $wfw
        regras = $regras
        regrasCriadas = @($Plano.Criar | ForEach-Object { $_.Nome })
        perfilRede = $(if ($Plano.AlterarPerfil) { [ordered]@{ nome = $Plano.AlterarPerfil.Nome; interface = $Plano.AlterarPerfil.Interface; de = $Plano.AlterarPerfil.De } } else { $null })
        tarefaCriada = $(if ($Plano.Tarefa) { $Plano.Tarefa.Nome } else { $null })
    }
    ConvertTo-Json -InputObject $registro -Depth 6 | Set-Content -LiteralPath $json -Encoding UTF8

    $relido = Get-Content -LiteralPath $json -Raw -Encoding UTF8 | ConvertFrom-Json
    if (@($relido.regras).Count -ne $regras.Count) { throw "o registro $json não confere depois de gravado" }

    @(
        "FISCALE — como desfazer a configuração de rede de $($registro.criado)"
        ""
        "1) Caminho normal (PowerShell como administrador, na pasta do FISCALE):"
        "   powershell -NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Desfazer `"$json`""
        "   (acrescente -Simular para só ver o que seria desfeito)"
        ""
        "2) Último recurso — restaura o firewall INTEIRO como estava:"
        "   netsh advfirewall import `"$wfw`""
        $(if ($Plano.AlterarPerfil) { "   e o perfil da rede: Set-NetConnectionProfile -InterfaceAlias '$($Plano.AlterarPerfil.Interface)' -NetworkCategory Public" })
        $(if ($Plano.Tarefa) { "   e a tarefa: Unregister-ScheduledTask -TaskName '$($Plano.Tarefa.Nome)' -Confirm:`$false" })
    ) | Set-Content -LiteralPath $txt -Encoding UTF8

    return [pscustomobject]@{ Json = $json; Wfw = $wfw; Txt = $txt }
}

function Invoke-PlanoRede($Plano, $Backup, [string]$Script) {
    $passo = ''
    try {
        if ($Plano.AlterarPerfil) {
            $passo = "perfil da rede '$($Plano.AlterarPerfil.Nome)'"
            Set-NetConnectionProfile -InterfaceIndex $Plano.AlterarPerfil.InterfaceIndex -NetworkCategory Private -ErrorAction Stop
            Feito "rede '$($Plano.AlterarPerfil.Nome)' agora é Privada"
        }
        foreach ($c in @($Plano.Criar)) {
            $passo = "criar a regra '$($c.Exibicao)'"
            New-NetFirewallRule -Name $c.Nome -DisplayName $c.Exibicao `
                -Description 'FISCALE: acesso das estacoes do escritorio (transicao; sai quando o proxy HTTPS entrar).' `
                -Direction Inbound -Protocol TCP -LocalPort $c.PortaLocal `
                -Profile Private -RemoteAddress $c.Origem -Action Allow -ErrorAction Stop | Out-Null
            Feito "regra criada: $($c.Exibicao) (TCP $(@($c.PortaLocal) -join ','), Privado, $(@($c.Origem) -join ', '))"
        }
        foreach ($i in @($Plano.Restringir)) {
            $passo = "restringir '$($i.Regra.Exibicao)'"
            Set-NetFirewallRule -Name $i.Regra.Nome -Profile $i.PerfisPara -RemoteAddress $i.OrigemPara -ErrorAction Stop
            Feito "restringida: $($i.Regra.Exibicao) -> Privado, $(@($i.OrigemPara) -join ', ')"
        }
        foreach ($i in @($Plano.Desativar)) {
            $passo = "desativar '$($i.Regra.Exibicao)'"
            Disable-NetFirewallRule -Name $i.Regra.Nome -ErrorAction Stop
            Feito "desativada: $($i.Regra.Exibicao) ($($i.Motivo))"
        }
        if ($Plano.Tarefa) {
            $passo = "criar a tarefa '$($Plano.Tarefa.Nome)'"
            $acao = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$($Plano.Tarefa.Lancador)`""
            $gatilho = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
            $opcoes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                          -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
            # Como VOCÊ, nunca como SYSTEM: sem o DPAPI da sua conta nenhuma
            # senha de certificado abriria.
            Register-ScheduledTask -TaskName $Plano.Tarefa.Nome -Action $acao -Trigger $gatilho `
                -Settings $opcoes -RunLevel Limited -Description 'Sobe o servidor do FISCALE.' -ErrorAction Stop | Out-Null
            Feito "tarefa criada: $($Plano.Tarefa.Nome)"
        }
        return $true
    } catch {
        Write-Host ""
        Write-Host "  PAROU em: $passo" -ForegroundColor Red
        Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  O que já foi feito pode ser desfeito com:" -ForegroundColor Yellow
        Write-Host "      .\rede_configurar.ps1 -Desfazer `"$($Backup.Json)`"" -ForegroundColor Yellow
        return $false
    }
}

function Invoke-DesfazerRede([string]$Arquivo, [bool]$SoMostrar) {
    if (-not (Test-Path -LiteralPath $Arquivo)) { Alerta "não achei o registro: $Arquivo"; return 1 }
    $reg = Get-Content -LiteralPath $Arquivo -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($reg.versao -ne 1) { Alerta "registro em versão desconhecida: $($reg.versao)"; return 1 }

    Titulo $(if ($SoMostrar) { "O que SERIA desfeito (simulação)" } else { "O que será desfeito" })
    Linha "registro de $($reg.criado) · $($reg.computador)"
    foreach ($r in @($reg.regras)) {
        Linha "- regra '$($r.exibicao)' volta a: ativa=$($r.ativa) · perfis $(@($r.perfis) -join ', ') · origem $(Format-Origem $r.origem)"
        Detalhe "id: $($r.nome)"
    }
    foreach ($n in @($reg.regrasCriadas)) { Linha "- remover a regra criada pelo script: $n" }
    if ($reg.perfilRede) { Linha "- rede '$($reg.perfilRede.nome)' ($($reg.perfilRede.interface)) volta a $($reg.perfilRede.de)" }
    if ($reg.tarefaCriada) { Linha "- remover a tarefa criada pelo script: $($reg.tarefaCriada)" }
    Detalhe "último recurso: netsh advfirewall import `"$($reg.exportacaoCompleta)`""
    if ($SoMostrar) { Write-Host ""; Write-Host "  Simulação: nada foi alterado." -ForegroundColor Yellow; return 0 }

    if ((Read-Resposta "  Desfazer? (S/N)") -notmatch '^[Ss]$') { Linha "Cancelado. Nada foi alterado."; return 0 }

    $falhas = 0
    foreach ($r in @($reg.regras)) {
        try {
            if (-not (Get-NetFirewallRule -Name $r.nome -ErrorAction SilentlyContinue)) { Alerta "regra não existe mais: $($r.nome)"; $falhas++; continue }
            Set-NetFirewallRule -Name $r.nome -Profile @($r.perfis) -RemoteAddress @($r.origem) -ErrorAction Stop
            if ($r.ativa) { Enable-NetFirewallRule -Name $r.nome -ErrorAction Stop } else { Disable-NetFirewallRule -Name $r.nome -ErrorAction Stop }
            Feito "restaurada: $($r.exibicao)"
        } catch { Alerta "falhou em $($r.nome): $($_.Exception.Message)"; $falhas++ }
    }
    foreach ($n in @($reg.regrasCriadas)) {
        try {
            if (Get-NetFirewallRule -Name $n -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name $n -ErrorAction Stop; Feito "removida a regra criada: $n" }
        } catch { Alerta "falhou ao remover $($n): $($_.Exception.Message)"; $falhas++ }
    }
    if ($reg.perfilRede) {
        try {
            $atual = Get-NetConnectionProfile -InterfaceAlias $reg.perfilRede.interface -ErrorAction SilentlyContinue
            if ($atual -and $atual.Name -eq $reg.perfilRede.nome) {
                Set-NetConnectionProfile -InterfaceAlias $reg.perfilRede.interface -NetworkCategory $reg.perfilRede.de -ErrorAction Stop
                Feito "rede '$($reg.perfilRede.nome)' voltou a $($reg.perfilRede.de)"
            } else { Alerta "a rede '$($reg.perfilRede.nome)' não está conectada; o perfil dela não foi mexido"; $falhas++ }
        } catch { Alerta "falhou no perfil: $($_.Exception.Message)"; $falhas++ }
    }
    if ($reg.tarefaCriada) {
        try {
            if (Get-ScheduledTask -TaskName $reg.tarefaCriada -ErrorAction SilentlyContinue) {
                Unregister-ScheduledTask -TaskName $reg.tarefaCriada -Confirm:$false -ErrorAction Stop
                Feito "tarefa removida: $($reg.tarefaCriada)"
            }
        } catch { Alerta "falhou ao remover a tarefa: $($_.Exception.Message)"; $falhas++ }
    }
    if ($falhas) { Alerta "$falhas item(ns) não voltaram; use o último recurso acima"; return 1 }
    return 0
}


# ── Fluxo principal ─────────────────────────────────────────────────────────
# Códigos de saída: 0 ok/cancelado/simulação · 1 sem administrador ou falha ao
# desfazer · 2 rede inválida · 3 plano não protege · 4 backup falhou · 5 falha ao aplicar
function Invoke-RedeConfigurar {
    param([int]$Porta = 8777, [string]$Lancador = '', [string]$RedeEscritorio = '',
          [string]$PastaBackup = '', [string]$Desfazer = '', [bool]$Simular = $false,
          [string]$Raiz = '', [string]$Script = '')
    $ErrorActionPreference = 'Stop'

    Write-Host ""
    Write-Host "  FISCALE - configurar a rede do servidor do escritorio" -ForegroundColor White
    Write-Host "  $env:COMPUTERNAME  ·  $(Get-Date -Format 'dd/MM/yyyy HH:mm')$(if ($Simular) { '  ·  SIMULAÇÃO' })" -ForegroundColor DarkGray

    if (-not $Simular -and -not (Test-Administrador)) {
        Write-Host ""
        Write-Host "  Este arquivo precisa de administrador para alterar algo." -ForegroundColor Red
        Write-Host "  Para só ver o que seria feito:  .\rede_configurar.ps1 -Simular" -ForegroundColor Gray
        Write-Host "  Para valer: botão direito no rede_configurar.bat > 'Executar como administrador'." -ForegroundColor Gray
        return 1
    }

    if ($Desfazer) { return (Invoke-DesfazerRede $Desfazer $Simular) }

    $estado = Get-EstadoReal $Porta $Lancador $Raiz
    Show-Redes $estado.Redes

    $faixa = ''
    $informada = $RedeEscritorio
    if (-not $informada -and -not $Simular) {
        Write-Host ""
        Linha "Esta máquina está na REDE DO ESCRITÓRIO? Se sim, digite a Faixa exatamente"
        Linha "como aparece acima (ex.: 192.168.1.0/24). ENTER = não confirmar."
        $informada = "$(Read-Resposta '  Faixa da rede do escritório')".Trim()
    }
    if ($informada) {
        $motivo = Test-FaixaAceitavel $informada
        if (-not $motivo -and -not @($estado.Redes | Where-Object { $_.Faixa -eq $informada }).Count) {
            $motivo = "a faixa $informada não é de nenhuma rede conectada agora"
        }
        if ($motivo) { Write-Host ""; Write-Host "  PARADO: $motivo. Nada foi alterado." -ForegroundColor Red; return 2 }
        $faixa = $informada
    }

    $plano = New-PlanoRede -Estado $estado -Faixa $faixa -Porta $Porta
    Show-Plano $plano $Simular

    Write-Host ""
    Linha "Backup antes de aplicar: $PastaBackup (exportação completa + registro para -Desfazer)"

    if ($plano.Status -eq 'NAO_PROTEGE') {
        Write-Host ""
        Write-Host "  O plano NÃO implanta a política; ele não é aplicado. Nada foi alterado." -ForegroundColor Red
        if ($Simular) { return 0 } else { return 3 }
    }
    if ($Simular) {
        Write-Host ""
        Write-Host "  Simulação: nada foi alterado." -ForegroundColor Yellow
        if (-not $faixa) { Detalhe "para ver o plano com a rede confirmada: .\rede_configurar.ps1 -Simular -RedeEscritorio <faixa>" }
        return 0
    }
    if (Test-PlanoVazio $plano) { Write-Host ""; Write-Host "  Nada a fazer: a política já está aplicada." -ForegroundColor Green; return 0 }

    Write-Host ""
    Linha "Nada foi alterado ainda."
    if ($faixa) {
        $ok = ("$(Read-Resposta '  Aplicar exatamente o que está acima? (S/N)')" -match '^[Ss]$')
    } else {
        Alerta "Sem rede confirmada, NENHUMA estação fica autorizada a abrir a porta $Porta."
        $ok = ("$(Read-Resposta '  Para aplicar assim mesmo digite SEM REDE (qualquer outra coisa cancela)')" -ceq 'SEM REDE')
    }
    if (-not $ok) { Write-Host ""; Write-Host "  Cancelado. Nada foi alterado." -ForegroundColor Yellow; return 0 }

    Titulo "Backup"
    try { $backup = Save-BackupRede $estado $plano $PastaBackup $Script }
    catch { Write-Host "  PARADO: $($_.Exception.Message). Nada foi alterado." -ForegroundColor Red; return 4 }
    Feito "exportação completa: $($backup.Wfw)"
    Feito "registro para desfazer: $($backup.Json)"
    Feito "instruções: $($backup.Txt)"

    Titulo "Aplicando"
    if (-not (Invoke-PlanoRede $plano $backup $Script)) { return 5 }

    Titulo "Pronto"
    Linha "Para desfazer:  .\rede_configurar.ps1 -Desfazer `"$($backup.Json)`""
    Linha "Confira com o rede_diagnostico.bat e abrindo http://$env:COMPUTERNAME`:$Porta de outra estação."
    return 0
}

# Os testes carregam só as funções, sem executar nada.
if ($env:FISCALE_REDE_SO_CARREGAR -eq '1') { return }

$codigo = Invoke-RedeConfigurar -Porta $Porta -Lancador $Lancador -RedeEscritorio $RedeEscritorio `
    -PastaBackup $PastaBackup -Desfazer $Desfazer -Simular ([bool]$Simular) `
    -Raiz $PSScriptRoot -Script $PSCommandPath
exit ([int](@($codigo)[-1]))
