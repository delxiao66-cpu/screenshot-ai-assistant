# 截图 AI 助手

## macOS arm64 客户端

新增原生 macOS 客户端源码，使用 ScreenCaptureKit 截取所选显示器全屏，显式不排除任何窗口；界面与浏览器扩展保持统一。支持账号连接、单张分析、多图收集、远程截图及全局快捷键。最低 macOS 14.2，使用说明见 [macOS 客户端文档](macos/README.md)。本次不执行客户端打包或运行测试。

## 安装和连接

1. Windows 安装 Python 3.11 或更新版本（加入 PATH）、Chrome 120 或更新版本。Windows 主机安装并登录 Tailscale；手机、平板等访问设备无需安装 Tailscale。
2. 在此目录打开 PowerShell，执行 `./setup.ps1`。如本机执行策略阻止脚本，可对当前窗口运行 `Set-ExecutionPolicy -Scope Process Bypass` 后重试。
3. 编辑 `.env`：填写 `DEEPSEEK_API_KEY` 和自行生成的长随机 `APP_TOKEN`。可以运行 `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"` 生成访问令牌。保留 `HOST=127.0.0.1`、`PORT=8765`。
4. 执行 `./start.ps1`，浏览器访问 `http://127.0.0.1:8765` 并输入 APP_TOKEN。
5. 执行 `./configure-tailscale.ps1`。脚本执行 `tailscale funnel --bg 8765`；首次使用时按命令提示在 Tailscale 网页启用 HTTPS 和 Funnel。用 `tailscale funnel status` 获取 `https://hostname.tailnet-name.ts.net` 地址，手机/平板可直接通过互联网打开并输入 APP_TOKEN。
6. Chrome 打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择 `screenshot-ai-assistant\extension` 文件夹。
7. 扩展弹窗填写服务器地址（Server URL）和 APP_TOKEN，点击“保存并连接”。在主机本地也可填写 `http://127.0.0.1:8765`。DeepSeek API Key 只填后端 `.env`。

## 账户管理

管理员用户名固定为 `admin`。首次安装后，在项目目录执行：

```powershell
.\.venv\Scripts\python.exe -m server.set_admin_password
```

按提示在本机输入管理员密码；密码只保存为加盐 scrypt 哈希，不写入 `.env` 或日志。登录接口为 `POST /api/auth/login`。管理员登录后可调用 `GET /api/admin/users`、`POST /api/admin/users`、`PATCH /api/admin/users/{id}` 和 `DELETE /api/admin/users/{id}` 管理普通账户；停用、删除或重置密码会立即使该账户原有会话失效。删除账户不会删除其历史记录。

当前 Chrome Agent 仍使用 APP_TOKEN 连接后端；账户会话用于控制台和管理员 API。完成管理员密码设置后请重启后端。

`.env` 的 `DEEPSEEK_MODEL` 完全控制模型名称，`DEEPSEEK_THINKING=auto` 默认对单图关闭思考、对多图开启思考；也可设置为 `enabled` 或 `disabled`。`MAX_OUTPUT_TOKENS` 只做宽松兜底，默认 16384（允许范围 1024～131072），正常题目不会触及；若服务商不支持该数值会返回请求错误，按账号支持的模型上限调低即可。最终仍只返回答案，思考内容不会显示在控制台。示例保留需求指定的模型名称；DeepSeek 官方目前说明该旧名称会路由到当前 Flash 模型，需要时可自行更换为账号支持的视觉模型。

## 使用

- **单张**：在扩展或 Web 控制台点击“单张截图并分析”，或按 `Ctrl+Shift+S`。Chrome 必须保持打开、Agent 在线，当前活动标签页为普通网页。结果在 Web 控制台查看。
- **多图**：控制台点击“新建多图任务”，然后“添加截图”。手动滚动网页，再次添加；也可以在扩展中反复点击“添加截图到当前多图任务”。两个入口共享当前收集中的多图任务。最后点击“开始分析”。
- **本地图片**：新建多图任务后点击“上传本地图片”，一次选择 1～20 张。按浏览器 FileList 返回顺序追加，以缩略图序号为准（操作系统文件选择器不保证鼠标点选顺序）；需要精确顺序时可逐张添加。开始前可删除、清空、继续添加，删除后序号连续。
- 只返回题号和答案，不输出题干、选项、解析和推理过程；选择题给选项，判断题给正确或错误，填空题给各空答案，其余题型给简洁但完整的最终答案，统一用字符串数组。答案触及 `MAX_OUTPUT_TOKENS` 上限会提示调高该值，不显示被截断的内容。
- 新建任务立即取消并替代未完成的旧任务（superseded），丢弃旧结果和迟到截图。扩展继续向同一多图任务添加截图不会替代该任务；再次点击同一任务“开始分析”不会重复请求。进行中的分析也可以取消。
- 分析阶段没有本地时限，一直等待服务商返回结果，只有完成、失败或手动取消才结束。新任务无需排队等待旧分析；取消会中止本地请求，但无法保证服务商立即停止远端计算或免除已发生的费用。
- 原始图片支持 PNG/JPEG/WebP，每次上传原始总大小不超过配置值；处理为 JPEG（质量 92，最长边最多 3072 像素），使用 auto 图像细节。减小文件可减少上传量，但视觉 token 是否下降取决于模型自身的图片计费规则。单任务处理后不超过 `MAX_TASK_IMAGE_BYTES`，并额外限制为 35 MiB，以满足 base64 编码后的 48 MiB 接口请求体上限。小字请分多张采集。
- 任务结束或取消后删除截图，历史只保留最近 200 条结果和图片元数据。取消记为 failed，原因注明已取消。收集中的图片超过 24 小时未操作会清理；重启时中断任务标为失败并清理。网页不会缓存预览；已打开的独立预览标签页请自行关闭。
- “清空历史记录”仅删除已完成、失败或已被替代的任务，不影响正在收集或分析的任务。APP_TOKEN 存在此浏览器 localStorage，可用“清除访问令牌”移除；扩展配置保存在 chrome.storage.local。

## Windows 自启动

依赖安装、手动启动验证成功后运行 `./install-autostart.ps1`。它设置当前用户登录 Windows 后自动启动（不是未登录时的系统服务），使用隐藏控制台的 Python，失败后可重启三次。重复运行会更新此目录对应的任务。`./remove-autostart.ps1` 停止并删除该任务。不要同时启动两份后端，也不要设置多个 Uvicorn worker。

停止公网转发：`./stop-tailscale.ps1`。根据后续确认，本项目使用 Funnel 提供公网 HTTPS 入口，业务接口仍要求 APP_TOKEN，后端仍只监听 127.0.0.1:8765。Windows 主机和后端必须保持运行。配置/停止转发会影响本机对应的 443 端口配置；已有其他转发时先查看 `tailscale funnel status`。

## 常见问题

- **Agent 离线**：保持 Chrome 打开；核对服务器地址、APP_TOKEN、Tailscale 网络；点击“重新连接”。错误令牌会停止自动重连，修正后重新保存。普通断线以 1、2、4、8、16、30 秒间隔重连。
- **无法截图**：切到普通 HTTP/HTTPS 网页。chrome://、扩展页等不支持；扩展只截当前活动标签页当前可见区域。快捷键冲突可在 `chrome://extensions/shortcuts` 检查。
- **截图超时**：后端 45 秒后释放截图请求，可以重新添加；迟到上传不会加入后续请求。
- **令牌错误/Key 未配置**：控制台重新输入 APP_TOKEN；DeepSeek Key 和模型填在 `.env`，修改后重启后端。缺少 Key 时已上传图片仍保留在收集任务里，配置完成后可点“开始分析”。
- **429、5xx、网络超时**：不自动重试，避免重复耗时和计费；请求失败即结束任务，不长时间挂起。JSON 格式异常时保留原始文本供查看。
- **图片过多/过大/格式错误**：减少图片、分次上传或调整分辨率；一次上传失败不会部分追加。
- **数据库或清理错误**：检查磁盘空间、文件占用和 `server/data` 写权限；清理失败会提示并定期重试。主要日志是 `server/data/logs/app.log`，自启动进程日志是同目录的 `server.log`，均自动轮转。
- **端口被占用**：检查是否同时运行了手动启动和计划任务，仅保留一个进程。

## 本地检查

针对本次改动的简短验证：`.\.venv\Scripts\python.exe -m unittest tests.test_latest_task tests.test_deepseek -v`。测试使用临时数据库与替代模型响应，不访问 DeepSeek，不写正常历史数据。更新文件后重启后端，在 `chrome://extensions` 重新加载扩展，再刷新控制台。

源码依据：当前工作目录实际提供的是 `chrome-extension-1.2.1` 解压目录，未提供指定 ZIP。本项目沿用其 MV3、captureVisibleTab → multipart 上传、20 秒心跳、deviceId、Badge 和快捷键处理方式，重新接入私人后端。原目录保持不变。

扩展需要 `<all_urls>` 以便远程控制端在没有临时 activeTab 授权的情况下截图；`tabs` 用于识别当前窗口及受限制页面；`storage` 保存设置；`alarms` 在 service worker 休眠后恢复连接。不含 content_scripts。
