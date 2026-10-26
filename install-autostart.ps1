$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '请先执行 setup.ps1。' }
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $suffix = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())))).Replace('-', '').Substring(0, 12) }
finally { $sha.Dispose() }
$taskName = "ScreenshotAIAssistant-$suffix"
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
# pythonw has no console window. run.py configures rotating logs before Uvicorn starts.
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument ('"' + (Join-Path $projectRoot 'server\run.py') + '"') -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description '登录 Windows 后启动私人截图分析服务，仅监听本机。' -Force | Out-Null
Write-Host "已设置当前用户登录时后台启动：$taskName。请勿同时重复运行 start.ps1。"
