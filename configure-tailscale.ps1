$ErrorActionPreference = 'Stop'
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) { throw '未找到 tailscale 命令，请检查 Tailscale 安装和 PATH。' }
& tailscale funnel --bg 8765
if ($LASTEXITCODE -ne 0) { throw 'Tailscale Funnel 配置失败，请按命令输出启用 HTTPS 和 Funnel 权限。' }
& tailscale funnel status
if ($LASTEXITCODE -ne 0) { throw '读取 Tailscale Funnel 状态失败。' }
