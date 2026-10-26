param(
    [ValidateSet('start', 'stop', 'status')][string]$Action = 'start',
    [switch]$Download,
    [ValidateRange(0, 65535)][int]$Port = 0
)
$ErrorActionPreference = 'Stop'
$fgRoot = Split-Path -Parent $PSScriptRoot
$fgRuntime = Join-Path $fgRoot 'artifacts/postgresql'
$fgData = Join-Path $fgRuntime 'data'
$fgBin = Join-Path $fgRuntime 'pgsql/bin'
$fgPortFile = Join-Path $fgRuntime 'port.local'
$fgPasswordFile = Join-Path $fgRuntime 'password.local'
$fgEnvFile = Join-Path $fgRoot '.env'
$fgUtf8 = New-Object System.Text.UTF8Encoding($false)
New-Item -ItemType Directory -Force -Path $fgRuntime | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $fgBin 'pg_ctl.exe'))) {
    if ($Action -ne 'start' -or -not $Download) {
        throw 'Local PostgreSQL is absent. Run with -Download, or configure your own instance in .env.'
    }
    $fgArchive = Join-Path $fgRuntime 'postgresql-18.6-1-windows-x64-binaries.zip'
    if (-not (Test-Path -LiteralPath $fgArchive)) {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://get.enterprisedb.com/postgresql/postgresql-18.6-1-windows-x64-binaries.zip' -OutFile $fgArchive
    }
    $fgExpectedHash = 'FBE23DA234EE31547BF8A36D29DFD81E82B849DF2D2B78D2EECB43D360252F8C'
    if ((Get-FileHash -LiteralPath $fgArchive -Algorithm SHA256).Hash -ne $fgExpectedHash) {
        throw 'PostgreSQL archive checksum mismatch. The existing archive has been preserved.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $fgZip = [IO.Compression.ZipFile]::OpenRead($fgArchive)
    try {
        foreach ($fgEntry in $fgZip.Entries) {
            if ($fgEntry.FullName -notmatch '^pgsql/(bin|lib|share)/') { continue }
            $fgTarget = [IO.Path]::GetFullPath((Join-Path $fgRuntime $fgEntry.FullName))
            if (-not $fgTarget.StartsWith(([IO.Path]::GetFullPath($fgRuntime) + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Unsafe archive entry.'
            }
            if ($fgEntry.FullName.EndsWith('/')) {
                New-Item -ItemType Directory -Force -Path $fgTarget | Out-Null
            } else {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $fgTarget) | Out-Null
                if (-not (Test-Path -LiteralPath $fgTarget)) {
                    [IO.Compression.ZipFileExtensions]::ExtractToFile($fgEntry, $fgTarget)
                }
            }
        }
    } finally { $fgZip.Dispose() }
}
if ($Action -eq 'status') {
    & (Join-Path $fgBin 'pg_ctl.exe') status -D $fgData
    exit $LASTEXITCODE
}
if ($Action -eq 'stop') {
    & (Join-Path $fgBin 'pg_ctl.exe') stop -D $fgData -m fast -w
    exit $LASTEXITCODE
}
if (Test-Path -LiteralPath $fgPortFile) {
    $fgSavedPort = [int]([IO.File]::ReadAllText($fgPortFile).Trim())
    if ($Port -ne 0 -and $Port -ne $fgSavedPort) { throw 'An existing cluster uses a different port. Keep its saved port.' }
    $Port = $fgSavedPort
} elseif ($Port -eq 0) {
    $fgListener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    $fgListener.Start()
    $Port = $fgListener.LocalEndpoint.Port
    $fgListener.Stop()
}
[IO.File]::WriteAllText($fgPortFile, [string]$Port, $fgUtf8)
if (-not (Test-Path -LiteralPath (Join-Path $fgData 'PG_VERSION'))) {
    if (-not (Test-Path -LiteralPath $fgPasswordFile)) {
        $fgBytes = New-Object byte[] 32
        $fgRng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $fgRng.GetBytes($fgBytes) } finally { $fgRng.Dispose() }
        $fgPassword = [BitConverter]::ToString($fgBytes).Replace('-', '').ToLowerInvariant()
        [IO.File]::WriteAllText($fgPasswordFile, $fgPassword, $fgUtf8)
    }
    & (Join-Path $fgBin 'initdb.exe') -D $fgData -U finguard -A scram-sha-256 "--pwfile=$fgPasswordFile" --encoding=UTF8 --locale=C
    if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
}
if (-not (Test-Path -LiteralPath $fgEnvFile)) {
    if (-not (Test-Path -LiteralPath $fgPasswordFile)) { throw 'Local password file is missing. Configure .env manually.' }
    $fgPassword = [IO.File]::ReadAllText($fgPasswordFile).Trim()
    [IO.File]::WriteAllText($fgEnvFile, "FINGUARD_DATABASE_URL=postgresql://finguard:$fgPassword@127.0.0.1:$Port/postgres`n", $fgUtf8)
}
& (Join-Path $fgBin 'pg_ctl.exe') status -D $fgData *> $null
if ($LASTEXITCODE -ne 0) {
    $fgLog = Join-Path $fgRuntime 'server.log'
    $fgArguments = @('start', '-D', ('"' + $fgData + '"'), '-l', ('"' + $fgLog + '"'), '-o', ('"-h 127.0.0.1 -p ' + $Port + '"'), '-w')
    $fgProcess = Start-Process -FilePath (Join-Path $fgBin 'pg_ctl.exe') -ArgumentList $fgArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $fgRuntime 'start.stdout.log') -RedirectStandardError (Join-Path $fgRuntime 'start.stderr.log')
    if (-not $fgProcess.WaitForExit(30000)) { throw 'PostgreSQL startup did not finish within 30 seconds.' }
    if ($fgProcess.ExitCode -ne 0) { throw 'PostgreSQL startup failed. Inspect artifacts/postgresql/start.stderr.log.' }
}
& (Join-Path $fgBin 'pg_isready.exe') -h 127.0.0.1 -p $Port -U finguard
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL is not ready.' }
Write-Output "Local PostgreSQL is ready on 127.0.0.1:$Port. Existing .env was preserved."
