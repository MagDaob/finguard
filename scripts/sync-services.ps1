$ErrorActionPreference = 'Stop'
$fgRoot = Split-Path -Parent $PSScriptRoot
$fgPreviousEnvironment = $env:UV_PROJECT_ENVIRONMENT
try {
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $fgRoot 'runtime/backend/.venv'
    & uv sync --project $fgRoot --locked --no-dev --package finguard
    if ($LASTEXITCODE -ne 0) { throw 'Backend environment setup failed.' }
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $fgRoot 'services/bank-simulator/.venv'
    & uv sync --project $fgRoot --locked --no-dev --package finguard-bank-simulator
    if ($LASTEXITCODE -ne 0) { throw 'Simulator environment setup failed.' }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $fgPreviousEnvironment
}
