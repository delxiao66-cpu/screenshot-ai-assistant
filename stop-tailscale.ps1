$ErrorActionPreference = 'Stop'
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) { throw '未找到 tailscale 命令。' }
& tailscale funnel --https=443 off
if ($LASTEXITCODE -ne 0) { throw '停止 Tailscale Funnel 失败，请检查 tailscale funnel status。' }
