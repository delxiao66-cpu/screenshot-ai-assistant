$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$launcher = Get-Command py -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $venvPython) {
    $pythonExe = $venvPython
    $pythonArgs = @()
} elseif ($pythonCommand) {
    $pythonExe = $pythonCommand.Source
    $pythonArgs = @()
} elseif ($launcher) {
    $pythonExe = $launcher.Source
    $pythonArgs = @('-3')
} else {
    throw '未找到 Python。请安装 Python 3.11 或更新版本，并勾选添加到 PATH。'
}
& $pythonExe @pythonArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.11 或更新版本。' }
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $pythonExe @pythonArgs -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败。' }
}
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw '更新 pip 失败，请检查网络后重试。' }
& $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络后重试。' }
$envPath = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination $envPath
}
foreach ($relative in @('server\data', 'server\data\tmp', 'server\data\logs')) {
    New-Item -ItemType Directory -Path (Join-Path $projectRoot $relative) -Force | Out-Null
}
Write-Host '安装完成。请编辑 .env 填写 DEEPSEEK_API_KEY 和 APP_TOKEN，再运行 .\start.ps1。'
