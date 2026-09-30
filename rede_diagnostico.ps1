<#
================================================================================
  FISCALE — diagnóstico da rede do escritório
================================================================================

  O QUE ESTE ARQUIVO FAZ
      Nada. Ele só OLHA e conta o que encontrou.

      Nenhuma linha aqui altera firewall, perfil de rede, energia ou tarefa
      agendada. Pode rodar quantas vezes quiser, a qualquer hora, com o
      Fiscale aberto. Quem altera é o `rede_configurar.ps1`, e só ele.

  PARA QUE SERVE
      O FISCALE do escritório é UM servidor: uma máquina roda o sistema, e
      todas as outras abrem pelo navegador. Não há dado duplicado, não há
      "sincronizar". Para isso funcionar, meia dúzia de coisas do Windows
      precisam estar de um jeito — e é isso que este arquivo confere.

  COMO RODAR
      Clique com o botão direito em `rede_diagnostico.bat` > Abrir.
      Ou, no PowerShell:  .\rede_diagnostico.ps1

      Não precisa de administrador.
================================================================================
#>

$ErrorActionPreference = 'Continue'
$PORTA = 8777

function Titulo($t) {
    Write-Host ""
    Write-Host ("== " + $t + " " + ("=" * [Math]::Max(0, 60 - $t.Length))) -ForegroundColor Cyan
}

function Item($ok, $rotulo, $detalhe, $comoResolver) {
    if ($ok) {
        Write-Host "  [ok]  $rotulo" -ForegroundColor Green
        if ($detalhe) { Write-Host "        $detalhe" -ForegroundColor DarkGray }
    } else {
        Write-Host "  [!]   $rotulo" -ForegroundColor Yellow
        if ($detalhe)      { Write-Host "        $detalhe" -ForegroundColor DarkGray }
        if ($comoResolver) { Write-Host "        -> $comoResolver" -ForegroundColor DarkYellow }
    }
}

Write-Host ""
Write-Host "  FISCALE - diagnostico da rede" -ForegroundColor White
Write-Host "  $(Get-Date -Format 'dd/MM/yyyy HH:mm')  ·  $env:COMPUTERNAME" -ForegroundColor DarkGray


# ── 1. Identidade da máquina ────────────────────────────────────────────────
Titulo "Esta máquina"
$cs = Get-CimInstance Win32_ComputerSystem
$so = (Get-CimInstance Win32_OperatingSystem).Caption
Write-Host "  Nome     : $($cs.Name)"
Write-Host "  Sistema  : $so"
Write-Host "  Domínio  : $(if ($cs.PartOfDomain) { $cs.Domain } else { "$($cs.Workgroup)  (sem domínio)" })"

# Sem domínio não há política de grupo: cada máquina é configurada uma a uma.
# Não é problema — é só o que explica por que este arquivo existe.
if (-not $cs.PartOfDomain) {
    Write-Host "  (sem AD: cada máquina se configura sozinha; é o esperado num escritório)" -ForegroundColor DarkGray
}


# ── 2. Perfil de rede ───────────────────────────────────────────────────────
#
# É o item que mais derruba o acesso pela rede, e o menos óbvio. No perfil
# Público o Windows bloqueia descoberta e compartilhamento — o nome da máquina
# deixa de resolver e as outras estações não acham o servidor.
Titulo "Perfil de rede"
$perfis = Get-NetConnectionProfile
foreach ($p in $perfis) {
    Item ($p.NetworkCategory -ne 'Public') `
         "$($p.Name) [$($p.InterfaceAlias)] : $($p.NetworkCategory)" `
         $(if ($p.InterfaceAlias -like '*Wi-Fi*') { "conexão sem fio" } else { "" }) `
         "Configurações > Rede e Internet > propriedades da rede > Rede privada"
}
if (-not $perfis) { Item $false "nenhuma rede conectada" "" "" }


# ── 3. Firewall ─────────────────────────────────────────────────────────────
Titulo "Firewall"

$regraNomeada = Get-NetFirewallRule -DisplayName 'FISCALE *' -ErrorAction SilentlyContinue
Item ([bool]$regraNomeada) `
     "regra nomeada para a porta $PORTA" `
     $(if ($regraNomeada) { ($regraNomeada | ForEach-Object { "$($_.DisplayName) · perfil=$($_.Profile) · ativa=$($_.Enabled)" }) -join '; ' } else { "não existe" }) `
     "rode o rede_configurar.ps1 como administrador"

# As regras por aplicativo que o Windows cria sozinho no "Permitir acesso".
# Elas funcionam, mas se acumulam e várias acabam no perfil Público — que é
# toda rede de aeroporto, café e hotel em que a máquina se conectar.
$porApp = @()
foreach ($f in (Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue)) {
    if ($f.Program -match 'python') {
        $r = $f | Get-NetFirewallRule -ErrorAction SilentlyContinue
        if ($r -and $r.Direction -eq 'Inbound' -and $r.Action -eq 'Allow') { $porApp += $r }
    }
}
$publicas = @($porApp | Where-Object { $_.Profile -match 'Public' })
Item ($publicas.Count -eq 0) `
     "regras de entrada por aplicativo (python) no perfil Público" `
     "$($publicas.Count) no Público, de $($porApp.Count) no total" `
     "o rede_configurar.ps1 remove as do Público e deixa uma regra de porta só"


# ── 4. O servidor ───────────────────────────────────────────────────────────
Titulo "Servidor do Fiscale"
$escuta = Get-NetTCPConnection -LocalPort $PORTA -State Listen -ErrorAction SilentlyContinue
Item ([bool]$escuta) "alguém escutando na porta $PORTA" `
     $(if ($escuta) { "PID $($escuta.OwningProcess -join ', ')" } else { "o Fiscale não está rodando agora" }) `
     "abra o Fiscale nesta máquina"

# Procurar pelo NOME da tarefa não serve: existe uma "FISCALE - Piloto NF-e",
# que baixa documentos de hora em hora e NÃO sobe o servidor. Casar por nome
# dava [ok] com ela — inclusive desativada. O que identifica a tarefa certa é
# a ação que ela executa.
$todas = Get-ScheduledTask -ErrorAction SilentlyContinue
$sobeServidor = @($todas | Where-Object {
    $_.Actions | Where-Object {
        "$($_.Execute) $($_.Arguments)" -match 'fiscale_server|Iniciar Fiscale'
    }
})
Item ($sobeServidor.Count -gt 0) "tarefa agendada que sobe o servidor" `
     $(if ($sobeServidor.Count) { ($sobeServidor | ForEach-Object { "$($_.TaskName) · $($_.State)" }) -join '; ' } else { "nenhuma" }) `
     "sem ela, o Fiscale só sobe se alguém abrir o atalho à mão nesta máquina"

# O piloto é outra coisa, e aparece só para não ser confundido com a de cima.
$piloto = @($todas | Where-Object { $_.TaskName -like '*Piloto*' })
if ($piloto.Count) {
    Write-Host "        (à parte: $(($piloto | ForEach-Object { "$($_.TaskName) · $($_.State)" }) -join '; ') — baixa documentos, não sobe o servidor)" -ForegroundColor DarkGray
}


# ── 5. Disponibilidade ──────────────────────────────────────────────────────
#
# Com um servidor só, esta máquina dormindo = ninguém do escritório trabalha.
Titulo "Disponibilidade (a máquina não pode dormir)"
# O `powercfg` fala a língua do Windows: aqui o rótulo é "Índice de
# Configurações de Correntes Alternadas Atuais", em inglês é "Current AC Power
# Setting Index". Casar pelo texto quebra na próxima máquina que estiver noutro
# idioma — e num produto vendido a escritórios isso acontece.
#
# A ORDEM dos valores, essa não muda: mínimo, máximo, incremento, CA, CC.
# É por posição, portanto, e com conferência de que vieram os cinco.
$plano = [regex]::Match((powercfg /getactivescheme | Out-String),
                        '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})').Value
$valores = @()
if ($plano) {
    $valores = @(powercfg /query $plano SUB_SLEEP STANDBYIDLE 2>$null |
                 Select-String -Pattern '0x([0-9a-f]{8})' |
                 ForEach-Object { [Convert]::ToInt64($_.Matches[0].Groups[1].Value, 16) })
}
if ($valores.Count -ge 4) {
    $seg = $valores[3]                      # 4º valor = na tomada (CA)
    Item ($seg -eq 0) "suspensão automática (na tomada)" `
         $(if ($seg -eq 0) { "desligada" } else { "$([int]($seg/60)) minutos" }) `
         "powercfg /change standby-timeout-ac 0   (como administrador)"
} else {
    Item $false "suspensão automática" `
         "não consegui ler o plano de energia (li $($valores.Count) valores, esperava 5)" `
         "confira à mão em Configurações > Sistema > Energia"
}


# ── 6. Acesso de fora ───────────────────────────────────────────────────────
Titulo "Acesso de fora do escritório"
$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $ts) { $ts = Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe" }
Item ([bool]$ts) "Tailscale instalado" `
     $(if ($ts) { "presente" } else { "não encontrado" }) `
     "instale em tailscale.com/download — é o que dá HTTPS e acesso de fora sem abrir porta no roteador"


# ── 7. Endereço para as estações ────────────────────────────────────────────
Titulo "Como as outras máquinas chegam aqui"
Write-Host "  Pelo NOME (use este):  http://$env:COMPUTERNAME`:$PORTA" -ForegroundColor White
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }
foreach ($ip in $ips) {
    Write-Host "  (pelo IP, só para teste: http://$($ip.IPAddress):$PORTA — o IP muda, o nome não)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host ("=" * 64) -ForegroundColor Cyan
Write-Host "  Onde aparecer [!], o rede_configurar.ps1 resolve — menos instalar" -ForegroundColor Gray
Write-Host "  o Tailscale, que é download e você faz." -ForegroundColor Gray
Write-Host ""
