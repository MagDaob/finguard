param([ValidateSet('start', 'stop', 'status')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
$fgRoot = Split-Path -Parent $PSScriptRoot
$fgServices = @(
    @{ Name = 'bank-simulator'; Module = 'finguard_simulator.api:create_app'; Port = 8001; Python = (Join-Path $fgRoot 'services/bank-simulator/.venv/Scripts/python.exe'); Directory = (Join-Path $fgRoot 'services/bank-simulator'); Runtime = (Join-Path $fgRoot 'services/bank-simulator/artifacts') },
    @{ Name = 'backend'; Module = 'finguard.backend.api:create_app'; Port = 8000; Python = (Join-Path $fgRoot 'runtime/backend/.venv/Scripts/python.exe'); Directory = $fgRoot; Runtime = (Join-Path $fgRoot 'artifacts') }
)
if ($Action -eq 'stop') { [array]::Reverse($fgServices) }
foreach ($fgService in $fgServices) {
    $fgOwner = Get-NetTCPConnection -LocalPort $fgService.Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess
    if ($fgOwner) {
        $fgProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$fgOwner"
        if ($fgProcess.CommandLine -notlike "*$($fgService.Module)*" -or $fgProcess.CommandLine -notlike '*uvicorn*' -or $fgProcess.CommandLine -notlike "*$fgRoot*") {
            throw "Port $($fgService.Port) belongs to a different application."
        }
    }
    if ($Action -eq 'status') {
        Write-Output "$($fgService.Name): $(if ($fgOwner) {'running'} else {'stopped'}) on 127.0.0.1:$($fgService.Port)"
        continue
    }
    if ($Action -eq 'stop') {
        if ($fgOwner) { Stop-Process -Id $fgOwner -ErrorAction Stop }
        continue
    }
    if (-not $fgOwner) {
        if (-not (Test-Path -LiteralPath $fgService.Python)) { throw 'Run scripts/sync-services.ps1 first.' }
        New-Item -ItemType Directory -Force -Path $fgService.Runtime | Out-Null
        $fgArguments = @('-m', 'uvicorn', $fgService.Module, '--factory', '--host', '127.0.0.1', '--port', $fgService.Port)
        $fgStarted = Start-Process -FilePath $fgService.Python -ArgumentList $fgArguments -WorkingDirectory $fgService.Directory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $fgService.Runtime 'service.stdout.log') -RedirectStandardError (Join-Path $fgService.Runtime 'service.stderr.log')
        [IO.File]::WriteAllText((Join-Path $fgService.Runtime 'service.pid.local'), [string]$fgStarted.Id)
    }
    $fgReady = $false
    for ($fgAttempt = 0; $fgAttempt -lt 40; $fgAttempt++) {
        try {
            $fgHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$($fgService.Port)/health/ready" -TimeoutSec 2
            if ($fgHealth.status -eq 'ready') { $fgReady = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    if (-not $fgReady) { throw "$($fgService.Name) did not become ready. Inspect its artifacts/service.stderr.log." }
    Write-Output "$($fgService.Name): ready on 127.0.0.1:$($fgService.Port)"
}
