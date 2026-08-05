[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "uv not found. Please install uv first." -ForegroundColor Red
  exit 1
}

if (-not (Test-Path (Join-Path $PSScriptRoot ".venv"))) {
  Write-Host ".venv not found. Running uv sync..." -ForegroundColor Yellow
  uv sync
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

$serviceConfigJson = uv run python -c "import json; from app.core.config import get_settings; s = get_settings(); print(json.dumps({'host': s.service_host, 'port': s.service_port}))"
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
$serviceConfig = $serviceConfigJson | ConvertFrom-Json
$serviceHost = [string]$serviceConfig.host
$servicePort = [int]$serviceConfig.port

Write-Host "Starting recommendation service on http://127.0.0.1:$servicePort ..." -ForegroundColor Cyan
uv run uvicorn app.main:app --host $serviceHost --port $servicePort --reload
exit $LASTEXITCODE
