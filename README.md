# Grok 批量注册工具

Web 控制台 + same-session 注册引擎，支持临时邮箱、Turnstile、Castle 同页 mint，以及 HTTP 导入 sub2api。

## 功能

- 同页 same-session 注册（Castle mint + 页内 fetch，默认 CLEAN 主路径）
- 临时邮箱自动建邮 / 收码（cloudflare_temp_email）
- 本地 Camoufox Turnstile Solver（也可接 YesCaptcha）
- 多线程并发、实时日志与进度
- 注册成功后由页面「导入」手动入库 sub2api（HTTP Admin API / sso-to-oauth；`AUTO_IMPORT=1` 可开自动）
- 按**分组名称**自动解析 sub2api `group_id`（ID 只读缓存）

## 目录结构

```
.
├── app.py                 # Web 控制台（Flask）
├── grok.py                # 注册引擎 + CLI
├── solver_manager.py      # Turnstile Solver 进程管理
├── api_solver.py          # 本地 Turnstile Solver
├── setup_solver.py        # 安装 Solver / camoufox 依赖
├── TurnstileSolver.bat    # Windows 一键启动 Solver
├── import_batch_once.py   # 从 keys 文本批量导入 sub2api
├── browser_configs.py
├── db_results.py
├── templates/index.html   # 控制台前端
├── g/                     # 邮箱 / Turnstile / Castle / 同会话注册 / 导入
├── .env.example
└── requirements.txt
```

本地运行产生、**不要提交**的内容：

- `.env`（真实密钥）
- `keys/`（SSO 输出）
- `logs/`（运行日志）

## 环境要求

- Python 3.10+
- Windows / Linux 均可（Camoufox Solver 在 Windows 上更常用）
- 已部署的 [cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email) Worker（或兼容 freemail）
- 可选：自建 [sub2api](https://github.com/) 用于账号入库

## 安装

```bash
pip install -r requirements.txt
# 首次使用本地 Turnstile Solver
python setup_solver.py
```

## 配置

```bash
cp .env.example .env
```

编辑 `.env`（**切勿提交**）：

| 配置项 | 说明 | 默认 |
|--------|------|------|
| `WORKER_DOMAIN` | cloudflare_temp_email 的 Worker 域名（不要 `https://`） | — |
| `FREEMAIL_TOKEN` | 站点密码 / JWT（变量名兼容旧 freemail） | — |
| `FREEMAIL_DOMAIN` | 邮箱后缀；`auto` 用服务端默认 | `auto` |
| `FREEMAIL_API_STYLE` | `auto` / `cf_temp` / `freemail` | `auto` |
| `YESCAPTCHA_KEY` | 有则走 YesCaptcha；空则本地 Solver | 空 |
| `SOLVER_URL` | 本地 Solver 地址 | `http://127.0.0.1:5072` |
| `SOLVER_BROWSER` | `camoufox` / `chromium` | `camoufox` |
| `SOLVER_THREADS` | Solver 浏览器线程 | `4` |
| `UI_HOST` / `UI_PORT` | Web 监听 | `127.0.0.1` / `3333` |
| `GROK_PROXY` | 注册代理；空=直连。支持 `host:port` / URL / `user:pass@host:port` / `host:port:user:pass` | 空 |
| `SUB2API_URL` | sub2api 根地址 | `http://127.0.0.1:9898` |
| `SUB2API_GROK_GROUP_NAME` | 导入目标分组**名称**（按名称解析 ID） | `grok` |
| `SUB2API_GROK_GROUP_ID` | 可选缓存；运行时会按名称回写 | 空 |
| `UPSTREAM_ADMIN_EMAIL` | sub2api 管理员邮箱 | — |
| `UPSTREAM_ADMIN_PASSWORD` | sub2api 管理员密码 | — |

说明：

- 页面「配置」里只填**分组名称**；分组 ID 只读显示，保存/测试/导入时自动拉取。
- 名称匹配的是 sub2api **已有**分组（可跨 platform）；新分组请先在 sub2api 后台创建。
- 导入主路径：`POST /api/v1/admin/grok/sso-to-oauth`（服务端换票）。
- 默认**不**自动入库；需要自动时在 `.env` 设 `AUTO_IMPORT=1`。

## 使用

### 1. 启动 Solver（本地模式）

```bash
python solver_manager.py start
python solver_manager.py status
```

或双击 `TurnstileSolver.bat`。Web 控制台也可一键启动。

### 2. Web 控制台（推荐）

```bash
python app.py
```

打开：`http://127.0.0.1:3333`

- **配置**：邮箱 / Solver / 注册代理 / sub2api / 分组名称
- **运行**：选择 `same_session`、并发、数量后开始
- **Keys**：下载 SSO 文件，一键导入 sub2api

same_session 建议并发先 **1～2**（注册浏览器 + Solver 双开）。

### 3. 命令行

```bash
python grok.py
```

成功账号写入 `keys/`。

### 4. 批量导入已有 SSO 文件

```bash
python import_batch_once.py keys/your_sso.txt
```

## 注册路径

| 模式 | 说明 |
|------|------|
| `same_session`（默认） | 同页 Castle mint + 页内发码/验码/signup，CLEAN 主路径 |
| `protocol` / `legacy` | 兼容旧路径，易被 Castle deny，不推荐 |

## 注意事项

- 仅供学习与自用自动化，请遵守目标站点与服务条款。
- 仓库不包含真实 `.env`、代理账号、邮箱密码、keys/logs。
- 推送前请确认工作区无个人域名、代理凭证与内网地址。
