$ErrorActionPreference = 'Stop'
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $suffix = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($PSScriptRoot.ToLowerInvariant())))).Replace('-', '').Substring(0, 12) }
finally { $sha.Dispose() }
$taskName = "ScreenshotAIAssistant-$suffix"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Write-Host '本项目自启动任务已移除。'
