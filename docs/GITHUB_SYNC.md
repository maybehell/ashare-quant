# GitHub 私有仓库同步

本项目使用本机 Git 和 Windows 计划任务同步源码，不依赖 Codex 或其他 AI
持续在线。远端仓库必须保持为私有仓库。

## 安全模型

同步采用两层保护：

1. `.gitignore` 排除行情、输出、账户数据库、订单、券商快照、日志、密钥、
   凭据和本地工具缓存。
2. `scripts/git_sync_preflight.py` 对实际暂存文件再次检查路径、文件类型、
   大小和常见密钥内容。检查失败时不提交、不推送。

`scripts/sync_github.ps1` 只暂存以下源码白名单：

- `ashare_quant/`
- `configs/`
- `docs/`
- `scripts/`
- `tests/`
- 根目录开发文档、依赖清单和启动脚本

它不会使用 `git add -A`，不会强制推送，也不会自动合并远端改动。远端领先、
已有人工暂存内容、敏感扫描失败或 Git 命令失败时，任务会停止并记录原因。

## 手动同步

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\sync_github.ps1
```

日志写入 `Logger/github_sync.log`，该文件不会上传。

## 定时同步

本机默认使用不需要管理员权限的登录启动守护程序：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_github_sync_startup.ps1 -StartNow
```

它在当前用户登录后隐藏运行，每天 22:30 同步一次；电脑休眠错过时间后会在
当天恢复时补跑。守护程序使用单实例锁，状态写入
`Logger/github_sync_daemon_status.json`。

在允许注册 Windows 计划任务的环境，也可改用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_github_sync_task.ps1
```

任务名为 `AShareQuantGitHubSync`，采用单实例和 30 分钟超时。

## 故障处理

- `remote branch is ahead/diverged`：人工检查远端提交并完成合并或变基。
- `pre-existing staged changes`：先处理人工暂存内容，再重跑同步。
- `sensitive-file preflight failed`：检查列出的文件，不要通过关闭扫描来绕过。
- 身份认证失败：运行 `gh auth status`，必要时重新执行 `gh auth login`。

自动同步是源码备份与版本历史，不替代行情、账户状态和实盘证据的本地备份。
这些运行数据包含隐私和交易信息，禁止上传到源码仓库。
