# Web3 Outreach Hub

> [English version](README.en.md)

Web3 Outreach Hub 是一个本地桌面 GUI 工具，用来把 Web3 项目发现、官网爬取、Telegram / X 联系人整理、文案管理和 DM 发送串成一条自动化流程。所有数据存储在本机 SQLite，主要入口是桌面 GUI。

## 功能概览

- **数据导入**：从 CrunchBase、RootData、CryptoRank、ChainScope、活动项目 API、Excel / CSV 等来源导入项目
- **官网爬取**：用 Playwright 扫描项目官网，提取 Telegram 群、X 账号和邮箱
- **Telegram 解析**：用 Telethon 解析群管理员，扫描小群离群用户
- **X 关键人搜索**：通过 X People Search 找 CEO / CMO / Growth / Founder 等关键人
- **文案管理**：在 GUI 中管理 Telegram / X / Email 文案模板和激活状态
- **批量发送**：
  - X：连接真实 Chrome CDP，会复用本机 Chrome 登录态
  - Telegram：macOS 使用 Telegram Web，Windows 使用 OCR / 坐标点击
  - Email：读取本地配置的 Gmail App Password 发送

## 安装

### 1. 安装 Google Chrome

X 发送和部分爬虫依赖 Chrome：

[下载 Google Chrome](https://www.google.com/chrome/)

### 2. 一键安装

复制以下命令到终端运行：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lukezhao-tech/outreach-hub/main/scripts/install.sh)"
```

安装脚本会完成：

| 步骤 | 说明 |
|------|------|
| 克隆代码 | 拉取 `lukezhao-tech/outreach-hub` |
| 检查 Chrome | 确认本机已安装 Google Chrome |
| 安装 uv | 如果没有 uv，会自动安装 |
| 安装 Python | 通过 uv 安装 Python 3.11 |
| 安装依赖 | `uv sync` |
| 安装浏览器引擎 | `playwright install chromium` |
| 验证环境 | 检查 tkinter、customtkinter、playwright |

### 3. 日常启动

```bash
cd ~/outreach-hub
./scripts/start_chrome_cdp.sh
```

这个命令会先启动 Chrome CDP，再启动桌面 GUI。

## 推荐启动方式

X 对新设备和自动化环境很敏感。最稳的方式是从你日常 Chrome profile 完整同步登录态到项目隔离 profile：

```bash
cd ~/outreach-hub
./scripts/start_chrome_cdp.sh --system --refresh --profile "lukezhao@taskon.xyz"
```

也可以用 Chrome profile 目录名：

```bash
./scripts/start_chrome_cdp.sh --system --refresh --profile "Profile 1"
./scripts/start_chrome_cdp.sh --system --refresh --profile "Default"
```

`--profile` 支持三类写法：

| 写法 | 示例 |
|------|------|
| Chrome profile 目录名 | `Default`、`Profile 1` |
| Chrome 显示名 | `Luke Zhao`、`taskon.xyz` |
| 登录邮箱 | `lukezhao@taskon.xyz` |

启动前必须完全退出日常 Chrome：

```text
Chrome 菜单 -> Quit Google Chrome
或按 Command + Q
```

只关闭窗口不够，因为 Chrome 仍可能锁住 cookies / profile 数据库。

## X 风控处理

新版做了几件事来降低 X 反机器人触发概率：

- CDP 模式默认使用真实 Chrome 指纹，不再注入固定 UA / Client Hints
- `--system --refresh` 会完整同步所选 Chrome profile，避免旧账号状态残留混入隔离 profile
- X DM 发送改成更慢的节奏，默认每条后随机等待 `30-90s`
- 文案输入改成逐字输入，不再瞬间填充
- 如果 X DM 页面出现 `connecting / disconnected` 或“连接不上网络 / try again”，程序会停止本轮，避免继续撞风控

如果打开 `https://x.com/i/chat` 一直 `connecting / disconnected`：

1. 完全退出日常 Chrome
2. 重新同步 profile：

   ```bash
   cd ~/outreach-hub
   ./scripts/start_chrome_cdp.sh --system --refresh --profile "你的 Chrome 邮箱或 Profile 名"
   ```

3. 弹出的 Chrome 里手动打开：

   ```text
   https://x.com/i/chat
   ```

4. 等 DM 页面稳定连接后，再回 GUI 点「已登录就绪」

如果日常 Chrome 里同一个账号也打不开 DM，那通常是 X 账号或网络侧限制，需要先手动恢复账号状态。

## GUI 使用流程

### 1. 设置

在「设置」页配置：

- Telegram API ID / API Hash
- Gmail 地址和 App Password
- DeepSeek API Key（官网爬取需要 LLM 提取时使用）
- Telegram / X 坐标和 OCR 区域（主要给 Windows 模式使用）
- DM 冷却时间

### 2. 爬虫

在「爬虫」页选择数据源：

- 官网 -> TG + X
- Crunchbase -> 官网
- RootData -> 官网 + TG + X
- ChainScope
- TokenFinder
- Campaign Twitter / KOL
- CryptoRank
- X 关键人搜索
- Excel / CSV 文件导入

每批导入建议填写独立 `source tag`，后续发送时可以按数据源筛选。

### 3. 解析

在「解析」页可以运行：

- TG 管理员解析
- 离群用户扫描

### 4. 文案

在「文案」页分别维护：

- Telegram 文案
- X 文案
- Email 文案

发送前需要激活对应渠道的文案。

### 5. 发送

在「发送」页选择渠道和数据源：

- Telegram 发送
- X 项目官号发送
- X 关键人发送
- Email 发送

X 发送建议先小批量测试，确认 DM 页面稳定后再放大数量。

## 更新

```bash
cd ~/outreach-hub
git pull
uv sync
```

如果 X 登录态、账号或 Chrome profile 变了，更新后建议重新同步：

```bash
./scripts/start_chrome_cdp.sh --system --refresh --profile "你的 Chrome 邮箱或 Profile 名"
```

## 数据流

```text
数据源导入 -> projects
    |
    v
官网爬取 -> tg_links / x_links / emails
    |
    v
TG 解析 -> tg_contacts / tg_left_users
    |
    v
X 关键人搜索 -> x_contacts
    |
    v
发送 -> send_log
```

所有步骤都写入本地 `data/outreach.db`，重启后会跳过已经处理过的数据。

## 目录结构

```text
outreach-hub/
  main.py                  # 桌面 GUI 入口
  config.py                # 路径和默认配置
  db.py                    # SQLite 数据层
  gui/                     # CustomTkinter GUI 标签页
  workers/                 # 爬取、解析、发送等后台任务
  scripts/
    install.sh             # 一键安装
    install_browsers.sh    # 安装依赖和浏览器引擎
    start_chrome_cdp.sh    # 启动 Chrome CDP + GUI
  data/                    # 数据库、浏览器 session、TG session，本地生成
```

## Android 配套项目

当桌面 Playwright 在 X 上触发更强风控时，可以把 X 关键人搜索 / X DM 发送搬到 Android 真机版：

[outreach-hub-android](https://github.com/lukezhao-tech/outreach-hub-android)

Android 版和桌面版共享 `data/outreach.db`，`x_links`、`x_contacts`、`send_log`、`message_templates` 和冷却期互通。
