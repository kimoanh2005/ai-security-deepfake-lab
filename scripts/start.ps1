<#
.SYNOPSIS
  Start the backend: alembic upgrade -> seed -> uvicorn (T-10).
.DESCRIPTION
  Run from anywhere:  .\scripts\start.ps1
  Needs backend\.venv and backend\.env (see README). Safe to re-run: migration and seed are idempotent.
  Binds to 127.0.0.1 by default (local only).
.PARAMETER Port
  Port for uvicorn (default 8000). When given, also sets SANDBOX_BASE_URL to http://127.0.0.1:<Port> for this run.
.PARAMETER BindHost
  Address to bind (default 127.0.0.1). Do not expose 0.0.0.0 unless you mean to.
.PARAMETER NoSeed
  Skip "python -m app.seed".
.PARAMETER Reload
  Enable uvicorn auto-reload (development only).
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [switch]$NoSeed,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$backend = Join-Path $PSScriptRoot "..\backend" | Resolve-Path | Select-Object -ExpandProperty Path
$python  = Join-Path $backend ".venv\Scripts\python.exe"
$envFile = Join-Path $backend ".env"

function Fail([string]$msg) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

function Invoke-Step([string]$title, [string[]]$pyArgs) {
    Write-Host "==> $title" -ForegroundColor Cyan
    & $python @pyArgs
    if ($LASTEXITCODE -ne 0) { Fail "'$title' failed (exit code $LASTEXITCODE)." }
}

if (-not (Test-Path $python)) {
    Fail "Missing virtualenv: $python`n    Create it first: py -3.12 -m venv backend\.venv ; backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt"
}
if (-not (Test-Path $envFile)) {
    Fail "Missing $envFile`n    Copy backend\.env.example to backend\.env and set JWT_SECRET."
}
$jwt = Select-String -Path $envFile -Pattern '^\s*JWT_SECRET\s*=\s*(\S+)' -ErrorAction SilentlyContinue
if (-not $jwt) { Fail "JWT_SECRET in backend\.env is empty. Generate one: backend\.venv\Scripts\python.exe -c `"import secrets; print(secrets.token_urlsafe(48))`"" }

Push-Location $backend
try {
    Invoke-Step "alembic upgrade head" @("-m", "alembic", "upgrade", "head")
    if (-not $NoSeed) { Invoke-Step "seed (idempotent)" @("-m", "app.seed") }

    # Lab API Security goi /sandbox/echo tren chinh server nay: giu SANDBOX_BASE_URL khop voi cong dang dung.
    if ($PSBoundParameters.ContainsKey("Port")) { $env:SANDBOX_BASE_URL = "http://127.0.0.1:$Port" }

    $uv = @("-m", "uvicorn", "app.main:app", "--host", $BindHost, "--port", "$Port")
    if ($Reload) { $uv += "--reload" }
    Write-Host "==> uvicorn on http://${BindHost}:$Port  (docs: /docs, health: /api/v1/health)" -ForegroundColor Green
    & $python @uv
    if ($LASTEXITCODE -ne 0) { Fail "uvicorn stopped with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
