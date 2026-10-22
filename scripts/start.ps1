# Avvia Signal Desk con doppio clic su Windows.
# Crea .venv e installa requirements.txt solo quando necessario.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$BasePort = 8000
$PortAttempts = 20
$Venv = Join-Path $Root '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
$Requirements = Join-Path $Root 'requirements.txt'
$Marker = Join-Path $Venv '.requirements-installed'

# La porta 8000 è affollata (Django, python -m http.server, vari tool), quindi
# non basta che "qualcuno" risponda: verifichiamo che sia davvero Signal Desk,
# altrimenti apriremmo il browser sull'applicazione di qualcun altro.
function Find-RunningUrl {
  for ($p = $BasePort; $p -lt $BasePort + $PortAttempts; $p++) {
    $candidate = "http://127.0.0.1:$p"
    try {
      $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "$candidate/api/status"
      if (($r.Content | ConvertFrom-Json).app -eq 'signal-desk') { return $candidate }
    } catch {}
  }
  return $null
}

$Running = Find-RunningUrl
if ($null -ne $Running) {
  Start-Process $Running
  exit 0
}

if (-not (Test-Path $Python)) {
  $SystemPython = Get-Command py -ErrorAction SilentlyContinue
  if ($null -eq $SystemPython) { $SystemPython = Get-Command python -ErrorAction SilentlyContinue }
  if ($null -eq $SystemPython) {
    Write-Host 'Python 3 non è installato. Scaricalo da https://www.python.org/downloads/windows/' -ForegroundColor Yellow
    Read-Host 'Premi Invio per chiudere'
    exit 1
  }
  Write-Host 'Preparo Signal Desk per il primo avvio…'
  if ($SystemPython.Name -eq 'py.exe') { & py -3 -m venv $Venv } else { & $SystemPython.Source -m venv $Venv }
}

if (-not (Test-Path $Marker) -or ((Get-Item $Requirements).LastWriteTime -gt (Get-Item $Marker).LastWriteTime)) {
  Write-Host 'Installo o aggiorno i componenti necessari…'
  & $Python -m pip install --disable-pip-version-check -r $Requirements
  New-Item -ItemType File -Path $Marker -Force | Out-Null
}

# Il server sceglie da solo la prima porta libera, quindi la cerchiamo invece
# di darla per scontata.
Start-Job -ScriptBlock {
  param($Base, $Attempts)
  for ($i = 0; $i -lt 40; $i++) {
    for ($p = $Base; $p -lt $Base + $Attempts; $p++) {
      $candidate = "http://127.0.0.1:$p"
      try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "$candidate/api/status"
        if (($r.Content | ConvertFrom-Json).app -eq 'signal-desk') { Start-Process $candidate; return }
      } catch {}
    }
    Start-Sleep -Milliseconds 500
  }
} -ArgumentList $BasePort, $PortAttempts | Out-Null

Write-Host 'Avvio Signal Desk… (Ctrl+C per fermare)'
& $Python -m src.main serve
