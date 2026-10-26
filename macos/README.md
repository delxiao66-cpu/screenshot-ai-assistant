# ScreenPilot macOS 客户端

原生 SwiftUI 客户端，面向 Apple Silicon（arm64），最低 macOS 14.2。界面沿用扩展的白底、黑色主按钮、圆角输入框和中文文案，无第三方 Swift 依赖。

## 功能

- 账号密码登录；令牌保存至系统钥匙串，密码不落盘。
- Agent WebSocket 连接、20 秒心跳、断线后 1/2/4/8/16/30 秒重连；认证失效停止重连。
- 单张截图后自动分析、多图连续收集和开始分析、任务状态刷新；结果和历史在 Web 控制台查看。
- 支持 Web 控制台发起的远程截图和取消请求，取消时停止上传并丢弃迟到结果。
- 全局 `Control+Shift+1/2/3` 分别执行单张截图、添加截图、分析。占用时显示提示，可继续使用按钮。与扩展同时使用时请调整扩展快捷键。
- 菜单栏常驻，关闭主窗口后继续接收远程截图；在菜单栏选择“退出”结束连接。

## 截图范围和权限

使用 `ScreenCaptureKit` 的 `SCScreenshotManager.captureImage`，缓存显示器内容，默认主显示器，可点击“刷新”选择其他显示器。每次截取所选显示器的完整画面，多显示器不会拼接，所选显示器断开时报告错误。

源码显式声明 `SCContentFilter(display: display, excludingWindows: [])`，**不排除任何窗口，也不排除客户端自身窗口**；共享内容枚举设置 `excludingDesktopWindows: false`，包含菜单栏并显示鼠标指针。不自动隐藏或最小化客户端。该设置不绕过 macOS 对受保护内容的限制。

第一次枚举显示器或截图需要系统屏幕录制授权。客户端不会在每次截图时重复弹出授权请求；拒绝或授权状态变化后，请从客户端打开系统设置，勾选当前正在运行的 ScreenPilot，并完全退出后重新打开。macOS 将授权绑定到应用的 Bundle ID、签名身份和当前应用副本；如果重新打包后仍提示未授权，请删除系统设置中的旧 ScreenPilot 条目，删除旧 `.app` 副本，将最终应用固定放到 `/Applications/ScreenPilot.app` 后重新授权。截图 JPEG 仅在内存中生成并以 multipart 上传；客户端将最长边限制为 3072 像素，质量约 88%。客户端按钮会直接截图上传，远程 Web 控制台请求仍通过 Agent WebSocket，不保存在本地。

## 开发与后续手动构建

可以使用 Xcode 打开 `Package.swift` 阅读和编辑源码。需要安装支持 macOS 14 SDK 的 Xcode 或 Command Line Tools。

按本次需求，没有执行构建、打包、启动客户端或真实截图测试。待允许打包时才手动运行：

```sh
cd macos
bash build-app.sh
```

脚本固定 `--arch arm64`，产物为 `dist/ScreenPilot.app`，默认本机 ad-hoc 签名（适合临时本机验证，重新编译后可能需要重新授权）。分发或持续更新时应通过 `SCREENPILOT_SIGN_IDENTITY` 指定固定的 Developer ID 签名，并另行完成公证；该脚本不自动发布或公证。建议将最终应用放到 `/Applications/ScreenPilot.app` 后再授权屏幕录制。开发机需要清除旧授权时，可先退出客户端，再执行 `tccutil reset ScreenCapture com.screenpilot.macos`，随后重新打开固定位置的应用并授权。

连接现有后端使用账号密码，服务器地址与扩展规则一致：`http://127.0.0.1:8765`、`http://localhost:8765` 或 Tailscale HTTPS 根地址。Web 控制台使用自己的浏览器会话，需要单独登录。

本次客户端还缓存了 ScreenCaptureKit 内容和过滤器、只枚举当前屏幕窗口、将 JPEG 编码放到后台任务，并把本地按钮改为直接截图上传。远程请求仍使用 WebSocket。本次后端为截图接口增加可选 `device_id` 查询参数。客户端本地按钮指定自身设备；没有指定设备的 Web 控制台仍按原逻辑选择最后连接的 Agent。需要控制台截取 Mac 时，将 Mac 重新连接为最后在线设备。先更新并重启后端，再使用新客户端。

## 验证边界

已通过 arm64 / macOS 14.2 目标的 Swift SDK 静态类型检查、Info.plist 校验、构建脚本语法检查和修改的 Python 文件语法检查；没有生成二进制或应用包。Swift 检查命令：

```sh
swiftc -typecheck -parse-as-library -target arm64-apple-macos14.2 -module-cache-path /tmp/screenpilot-swift-module-cache Sources/ScreenPilot/*.swift
```

后端设备选择和定向截图回归测试位于 `tests/test_agent_selection.py`。当前提供的 `.venv` 为 Windows 环境，本机 Python 缺少 FastAPI，因此未运行；配置好后端依赖后可从项目根目录运行 `python -m unittest tests.test_agent_selection -v`。

屏幕录制授权、Retina/多显示器捕获、快捷键冲突、睡眠唤醒、网络重连和端到端分析仍需要后续获准的运行验证。
