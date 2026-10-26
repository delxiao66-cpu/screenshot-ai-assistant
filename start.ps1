$ErrorActionPreference = 'Stop'
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '请先执行 setup.ps1。' }
Set-Location -LiteralPath $PSScriptRoot
& $pythonExe -m uvicorn server.main:app --host 127.0.0.1 --port 8765 --ws-max-size 8192 --no-access-log
exit $LASTEXITCODE
