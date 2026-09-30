<#
================================================================================
  FISCALE — acesso pelo celular FORA do escritório (rede privada Tailscale)
================================================================================

  O QUE ELE FAZ (D72/D97: acesso externo só por rede privada)
      1. Instala o Tailscale nesta máquina, se ainda não houver (winget).
      2. Liga o Tailscale. Na primeira vez abre o navegador para você ENTRAR
         na conta; é a única coisa que ninguém pode fazer por você.
      3. Cria a regra de firewall "FISCALE 8777 (Tailscale)": TCP 8777,
         liberada SÓ para a faixa da rede privada do Tailscale (100.64.0.0/10).
         Nenhuma porta é aberta no roteador e nada é publicado na internet.
      4. Confere se o FISCALE responde na 8777 e mostra o endereço que vai no
         app do celular. Também grava esse endereço num arquivo na Área de
         Trabalho.

  O QUE ELE NÃO FAZ
      - Não mexe em nenhuma outra regra de firewall nem no perfil da rede.
        Isso é da Infra 1A (rede_configurar.ps1), que segue em revisão.
      - Não abre porta no roteador, não publica nada, não instala nada no
        celular.

  COMO USAR
      Prévia, sem alterar nada:     celular_configurar.bat -Simular
      Configurar:                   celular_configurar.bat   (pede administrador)
      Desfazer a regra criada:      PowerShell como administrador, nesta pasta:
          powershell -ExecutionPolicy Bypass -File celular_configurar.ps1 -Desfazer

  NO CELULAR (depois)
      1. Play Store → instalar "Tailscale" → entrar com a MESMA conta.
      2. App FISCALE → segurar o ícone → "Alterar servidor" → digitar o
         endereço que este script mostrar.
================================================================================
#>
param(
    [switch]$Simular,
    [switch]$Desfazer
)

$ErrorActionPreference = "Stop"
$REGRA   = "FISCALE 8777 (Tailscale)"
$PORTA   = 8777
$FAIXA   = "100.64.0.0/10"   # CGNAT: faixa que o Tailscale usa para os aparelhos
$TS_EXE  = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"

function Titulo($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Ok($t)     { Write-Host "  [ok]   $t" -ForegroundColor Green }
function Aviso($t)  { Write-Host "  [!]    $t" -ForegroundColor Yellow }
function Erro($t)   { Write-Host "  [ERRO] $t" -ForegroundColor Red }
function Faria($t)  { Write-Host "  [faria] $t" -ForegroundColor Magenta }

function EhAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Tailscale { & $TS_EXE @args 2>$null }

function EstadoTailscale {
    if (-not (Test-Path $TS_EXE)) { return $null }
    try { return (Tailscale status --json | Out-String | ConvertFrom-Json) } catch { return $null }
}

function FiscaleResponde {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$PORTA/login.html" -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch { return $false }
}

Write-Host "FISCALE — acesso pelo celular fora do escritório" -ForegroundColor White
if ($Simular) { Write-Host "(PRÉVIA: nada será alterado)" -ForegroundColor Magenta }

# ── Desfazer ──────────────────────────────────────────────────────────────────
if ($Desfazer) {
    Titulo "Desfazer"
    if (-not (EhAdmin)) { Erro "Rode como administrador (clique direito → Executar como administrador)."; exit 1 }
    $r = Get-NetFirewallRule -DisplayName $REGRA -ErrorAction SilentlyContinue
    if ($r) { $r | Remove-NetFirewallRule; Ok "Regra '$REGRA' removida." }
    else    { Ok "A regra '$REGRA' não existia. Nada a desfazer." }
    Write-Host "  O Tailscale continua instalado; para removê-lo use 'Adicionar ou remover programas'."
    exit 0
}

if (-not $Simular -and -not (EhAdmin)) {
    Erro "Precisa de administrador. Use o celular_configurar.bat, que já pede a permissão."
    exit 1
}

if (-not $Simular) {
    Write-Host ""
    Write-Host "  Vai: instalar/ligar o Tailscale e criar a regra '$REGRA'"
    Write-Host "  (TCP $PORTA, só a partir de $FAIXA). Nada mais é alterado."
    $resp = Read-Host "  Continuar? (S/N)"
    if ($resp -notmatch '^[sS]') { Write-Host "  Nada foi alterado."; exit 0 }
}

# ── 1. Tailscale instalado ────────────────────────────────────────────────────
Titulo "1. Tailscale nesta máquina"
if (Test-Path $TS_EXE) {
    Ok "Tailscale já instalado."
} elseif ($Simular) {
    Faria "instalar o Tailscale com: winget install -e --id Tailscale.Tailscale"
} else {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Erro "O winget não está disponível neste Windows."
        Write-Host "  Instale manualmente em https://tailscale.com/download/windows e rode este script de novo."
        Start-Process "https://tailscale.com/download/windows"
        exit 1
    }
    Write-Host "  Instalando o Tailscale (pode levar um minuto)..."
    winget install -e --id Tailscale.Tailscale --silent --accept-source-agreements --accept-package-agreements | Out-Host
    for ($i = 0; $i -lt 30 -and -not (Test-Path $TS_EXE); $i++) { Start-Sleep -Seconds 2 }
    if (-not (Test-Path $TS_EXE)) { Erro "A instalação não terminou. Instale em https://tailscale.com/download/windows e rode de novo."; exit 1 }
    Ok "Tailscale instalado."
}

# ── 2. Tailscale conectado ────────────────────────────────────────────────────
Titulo "2. Conta do Tailscale"
$st = EstadoTailscale
if ($st -and $st.BackendState -eq "Running") {
    $conta = $st.User."$($st.Self.UserID)".LoginName
    Ok "Conectado como $conta."
} elseif ($Simular) {
    Faria "rodar 'tailscale up' e abrir o navegador para você entrar na conta"
} else {
    Write-Host "  Vai abrir o navegador: ENTRE com a conta que você também vai usar no celular"
    Write-Host "  (pode ser a conta Google). Depois volte aqui."
    Tailscale up --unattended | Out-Host
    for ($i = 0; $i -lt 90; $i++) {
        $st = EstadoTailscale
        if ($st -and $st.BackendState -eq "Running") { break }
        Start-Sleep -Seconds 2
    }
    if (-not ($st -and $st.BackendState -eq "Running")) {
        Erro "O Tailscale ainda não está conectado. Termine o login no navegador e rode este script de novo."
        exit 1
    }
    Ok "Conectado."
}
# --unattended: o Tailscale continua ativo mesmo sem ninguém logado no Windows,
# como o próprio FISCALE (que sobe no logon, mas o servidor não deve depender disso).

# ── 3. Firewall: 8777 só para a rede privada ──────────────────────────────────
Titulo "3. Firewall: porta $PORTA só para a rede privada ($FAIXA)"
$existente = Get-NetFirewallRule -DisplayName $REGRA -ErrorAction SilentlyContinue
if ($existente) {
    Ok "Regra '$REGRA' já existe."
} elseif ($Simular) {
    Faria "criar a regra '$REGRA': entrada, TCP $PORTA, origem $FAIXA, todos os perfis"
} else {
    # Todos os perfis de propósito: o adaptador do Tailscale costuma aparecer como
    # rede "Pública" no Windows. Quem restringe é a ORIGEM, que só existe dentro
    # da rede privada.
    New-NetFirewallRule -DisplayName $REGRA -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $PORTA -RemoteAddress $FAIXA -Profile Any `
        -Description "FISCALE: acesso pelo celular via Tailscale (D72/D97). Criada por celular_configurar.ps1." | Out-Null
    Ok "Regra criada."
}

# ── 4. Conferência ────────────────────────────────────────────────────────────
Titulo "4. Conferência"
if (FiscaleResponde) { Ok "O FISCALE responde em http://127.0.0.1:$PORTA." }
else { Aviso "O FISCALE não respondeu na $PORTA agora. Ligue o servidor (iniciar.bat) antes de usar o celular." }

$st = EstadoTailscale
$ip = $null; $nome = $null
if ($st -and $st.BackendState -eq "Running") {
    $ip = ($st.Self.TailscaleIPs | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1)
    $nome = ([string]$st.Self.DNSName).TrimEnd('.')
}

Titulo "Pronto — no celular"
if ($ip) {
    $url = "http://${ip}:$PORTA"
    Write-Host ""
    Write-Host "  Endereço para o app FISCALE:" -ForegroundColor White
    Write-Host "      $ip" -ForegroundColor Green
    Write-Host "  (o app completa para $url)"
    if ($nome) { Write-Host "  Com MagicDNS ligado, também funciona: $nome" }
    Write-Host ""
    Write-Host "  1. No celular, instale 'Tailscale' pela Play Store e entre com a MESMA conta."
    Write-Host "  2. Deixe o Tailscale conectado."
    Write-Host "  3. App FISCALE → segure o ícone → 'Alterar servidor' → digite $ip → Testar → Salvar."

    if (-not $Simular) {
        $txt = Join-Path ([Environment]::GetFolderPath("Desktop")) "FISCALE_celular.txt"
        @(
            "FISCALE no celular (fora do escritório)",
            "",
            "Endereço para o app: $ip   (completo: $url)",
            $(if ($nome) { "Nome (MagicDNS):     $nome" } else { "" }),
            "",
            "1. Play Store: instalar Tailscale e entrar com a MESMA conta deste servidor.",
            "2. Deixar o Tailscale conectado.",
            "3. App FISCALE: segurar o ícone > Alterar servidor > digitar o endereço > Testar > Salvar.",
            "",
            "Para desfazer a regra de firewall: celular_configurar.ps1 -Desfazer (como administrador)"
        ) | Set-Content -Path $txt -Encoding UTF8
        Ok "Endereço salvo em $txt"
    }
} elseif ($Simular) {
    Faria "mostrar o IP 100.x.x.x desta máquina na rede privada"
} else {
    Aviso "Não consegui ler o IP do Tailscale. Veja no ícone do Tailscale, perto do relógio."
}
Write-Host ""
