# Web3 Outreach Hub

> [English version](README.en.md)

Web3 项目自动化触达工具。从多种数据源导入项目信息，自动爬取官网提取 Telegram / X 链接，解析群管理员，然后通过 TG / X DM 批量发送消息。

## 功能

- **数据导入**：从 CrunchBase、RootData、CryptoRank、ChainScope 等来源批量导入项目
- **网站爬取**：Playwright 自动爬取项目官网，提取 TG 群链接和 X 链接
- **TG 解析**：Telethon 解析群管理员、小群离群用户
- **DM 发送**：
  - **X (Twitter)**：Playwright 控制浏览器，自动搜索用户并发送 DM（macOS）
  - **Telegram**：Telethon API 或 OCR 坐标点击（Windows）

## 安装

### 1. 安装 Google Chrome

X DM 发送需要通过 Chrome 浏览器完成，请先安装：

[下载 Google Chrome](https://www.google.com/chrome/)

### 2. 一键安装

复制以下命令到终端运行：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lukezhao-tech/outreach-hub/main/scripts/install.sh)"
```

这个脚本会自动完成以下所有步骤：

| 步骤 | 说明 |
|------|------|
| 克隆代码 | 从 GitHub 拉取最新代码 |
| 检查 Chrome | 确认已安装 Google Chrome |
| 安装 uv | 如果没有 uv，自动下载安装 |
| 安装 Python | 通过 uv 下载 Python 3.11（不需要系统自带 Python） |
| 安装依赖 | `uv sync` 安装所有 pip 包 |
| 安装浏览器引擎 | `playwright install chromium` 用于网站爬取 |
| 环境验证 | 检查 tkinter、customtkinter、playwright 是否可用 |

整个过程大约 2-3 分钟，取决于网速。**只需运行一次。**

### 安装完成后

安装脚本结束时会自动进入 `outreach-hub` 目录。日常启动（每次新开终端后）：

```bash
cd outreach-hub    # 安装时 clone 的目录（通常在 home 目录下）
./scripts/start_chrome_cdp.sh
```

### 更新到最新版

每次发新版后，在终端跑：

```bash
cd outreach-hub
git pull
uv sync    # 若依赖有更新，会自动安装；没更新则秒退
```

## 配套：[outreach-hub-android](https://github.com/lukezhao-tech/outreach-hub-android)（Android 真机版）

桌面 Playwright 在 X 上反 bot 风控越来越狠时，可以把**关键人搜索 / X DM 发送**两件事搬到手机 X App 里做（uiautomator2 + ADB）。

- **共享数据库**：自动读 `outreach-hub/data/outreach.db`，两边 `x_links` / `x_contacts` / `send_log` / `message_templates` 全互通，冷却期也共享
- **clone 到同一父目录即可，无需任何配置**：
  ```bash
  cd ~   # 或你 clone outreach-hub 的父目录
  git clone https://github.com/lukezhao-tech/outreach-hub-android.git
  ```
- 详细安装和使用见 [outreach-hub-android/README](https://github.com/lukezhao-tech/outreach-hub-android#readme)

## 使用

### 桌面 GUI

```bash
./scripts/start_chrome_cdp.sh
```

这个脚本会：
1. 启动 Chrome CDP 模式（后台运行）
2. 启动桌面 GUI 应用

**X DM 发送流程**：
1. 在弹出的 Chrome 中登录 Twitter（`x.com`）
2. 回到应用界面，点击「开始发送」
3. 等待浏览器打开 DM 页面后，点击「已登录就绪」
4. 自动逐个发送 DM

#### 启动模式

| 命令 | 说明 |
|------|------|
| `./scripts/start_chrome_cdp.sh` | **默认**。隔离 profile（`data/chrome_cdp_session/`），全新登录 |
| `./scripts/start_chrome_cdp.sh --system` | **从日常 Chrome 拷贝 cookies / 登录态到隔离 profile**，X 不会判定为新设备。自动扫描所有 profile 挑含 X 登录态的那个 |
| `./scripts/start_chrome_cdp.sh --system --refresh` | 强制重新拷贝（日常 Chrome 改密码或换号后用一次） |
| `./scripts/start_chrome_cdp.sh --system --profile "Profile 1"` | 多个 profile 都登录了 X 时，显式指定从哪个拷 |
| `./scripts/start_chrome_cdp.sh --help` | 查看用法 |

#### 何时需要 `--system`？

X 把脚本启动的「干净 Chrome」视作新设备，首次登录时常常**输完密码又被打回登录页循环**（典型反 bot 软封）。这时改用 `--system`：

```bash
# 1. 完全退出日常 Chrome（⌘Q，不只是关窗口；脚本要读 cookies SQLite，被写锁会损坏）
# 2. 启动 outreach-hub
./scripts/start_chrome_cdp.sh --system
```

`--system` 启动时会**扫描你日常 Chrome 的所有 profile**（`Default`、`Profile 1`、`Profile 2` ...），通过 SQLite 直接查每个 profile 的 cookies 数据库，找出含 X `auth_token` 的那个，把它的 cookies / Local State 拷贝到隔离 profile。弹出的 Chrome 直接是登录态，X 把你视作老用户。

- **只有一个 profile 登录了 X**：自动选中，无需任何参数
- **多个 profile 都登录了 X**：脚本会列出所有候选并报错退出，让你用 `--profile NAME` 显式指定，例如 `--profile "Profile 1"`
- **没有任何 profile 登录 X**：脚本会要求你先打开日常 Chrome 登录 `https://x.com`，⌘Q 退出再重试

> **为什么不直接复用日常 profile？** Chrome 136+ 出于安全考虑，禁止在默认 profile 上开 `--remote-debugging-port`（防恶意软件偷登录态）。所以脚本必须用独立 profile，再把日常 Chrome 的认证文件搬过去。

⚠️ **使用注意**：
- 拷贝前必须**完全退出**日常 Chrome（⌘Q，不只是关窗口），否则 cookies SQLite 还被锁住
- 拷贝只在隔离 profile 首次为空时进行，之后启动会复用上次的状态；日常 Chrome 改密码或换号后跑 `--system --refresh` 重刷一次
- 拷贝完成后，日常 Chrome 可以随便开，不影响 outreach-hub 这边的隔离 profile

## 数据流

```
数据源导入 -> projects 表
    |
    v  爬取官网
tg_links 表 + x_links 表
    |
    v  解析群信息
tg_handles 表 + tg_left_users 表
    |
    v  发送 DM
send_log 表
```

每一步都写入本地 SQLite，重启后自动跳过已处理项。

## 目录结构

```
outreach-hub/
  main.py              # 桌面 GUI 入口
  config.py            # 配置（凭证、路径、API）
  db.py                # 数据层（SQLite）
  workers/             # 后台任务（爬取、解析、发送）
  gui/                 # 桌面 GUI 标签页
  scripts/
    install.sh             # 一键安装（给新同事用）
    install_browsers.sh    # 环境准备（install.sh 内部调用）
    start_chrome_cdp.sh    # 日常启动（Chrome + GUI），加 --system 复用日常 profile
  data/                # 数据库、session（自动创建，不入版本控制）
```
