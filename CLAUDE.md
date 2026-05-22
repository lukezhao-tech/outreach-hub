# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Web3 Outreach Hub — Python 桌面应用（CustomTkinter GUI），用于 Web3 项目的自动化触达：从多种数据源导入项目 → 爬取官网提取 Telegram/X 链接 → 解析群管理员/离群用户 → OCR/坐标自动发送 DM。数据存储在本地 SQLite。

**运行环境**：Windows/macOS 桌面环境，Python 3.11+。

## 常用命令

```bash
# 安装依赖
uv sync
uv run playwright install chromium

# 启动桌面 GUI
./scripts/start_chrome_cdp.sh

# 打包为 exe（PyInstaller）
# 使用 build.ps1

# 重置某张表数据（示例）
uv run python -c "import sqlite3; c=sqlite3.connect('data/outreach.db'); c.execute('DELETE FROM tg_handles'); c.commit(); c.close(); print('done')"
```

## 架构概览

### 数据流（全链路）

```
数据源导入 → projects 表（company_name, website）
    ↓ ScraperWorker（Playwright headless）
tg_links 表 + x_links 表
    ↓ ParserWorker（Telethon）
tg_handles 表（管理员） + tg_left_users 表（离群用户）
    ↓ SenderWorker（OCR/坐标点击）
send_log 表
```

每步写 SQLite，重启后自动跳过已处理项（断点续传）。

### 核心模块

| 文件 | 职责 |
|------|------|
| `config.py` | **唯一配置文件**：所有常量、路径、凭证、API 地址 |
| `db.py` | **唯一数据层**：全部 SQLite 读写 |
| `main.py` | 入口：`init_db()` → `App().mainloop()` |

### Worker 模式

所有后台任务继承 `workers/base_worker.py` 的 `BaseWorker`：
- `__init__(log_callback, progress_callback)` — GUI 日志和进度条
- `run()` — 在 `threading.Thread(daemon=True)` 中执行
- `stop()` — GUI 停止按钮设置 `self._stop = True`
- `safe_log(msg)` — GBK 编码安全的日志输出

GUI 启动 Worker 的标准写法：
```python
self.worker = SomeWorker(self._log, self._update_progress, ...)
threading.Thread(target=self.worker.run, daemon=True).start()
```

### GUI 模式

CustomTkinter 桌面应用，`gui/app.py` 懒加载 7 个标签页：
📊 仪表盘、🔍 爬虫、🔬 解析、✉️ 文案、📤 发送、📋 记录、⚙️ 设置

每个 Tab 类统一结构：`_build()` → `_start()` → `_run_worker()` → `_stop()`

### Workers 一览

| Worker | 数据源 | 输出表 | 关键技术 |
|--------|--------|--------|----------|
| `crunchbase_worker` | Excel | projects | undetected-chromedriver |
| `crunchbase_discover_worker` | CB Discover 页 | projects | 有头浏览器 |
| `scraper_worker` | 官网 URL | tg_links + x_links | Playwright headless |
| `x_profile_search_worker` | x_links handles | x_contacts | Playwright People search |
| `parser_worker` | TG 群链接 | tg_handles | Telethon |
| `tgleft_worker` | TG 小群 | tg_left_users | Telethon |
| `tg_sender_worker` | TG handles | send_log | WinRT OCR + PyAutoGUI |
| `x_sender_worker` | X handles | send_log | Playwright 浏览器自动化 |
| `rootdata_worker` | RootData API | projects+tg_links+x_links | Playwright |
| `chainscope_worker` | ChainScope API | projects+x_links | 纯 HTTP |
| `token_finder_worker` | TokenFinder API | x_links | 纯 HTTP |
| `campaign_worker` | 活动项目 API | x_links | 纯 HTTP |
| `cryptorank_worker` | CryptoRank | projects+x_links | Playwright |
| `email_sender_worker` | email handles | send_log | SMTP |

### SQLite 表（10 张）

`projects` / `tg_links` / `x_links` / `x_contacts` / `tg_handles` / `tg_left_users` / `send_log` / `message_templates` / `settings` / 外加若干扩展表。关键去重：`UNIQUE` 约束 + `INSERT OR IGNORE`。

**x_contacts 表**：通过 `x_profile_search_worker` 写入，存储 Web3 项目关键人（CEO/CMO/Growth/Founder）的 X handle、bio、role、所属项目 handle。与 `x_links` 通过 `x_link_id` 关联。发送时在 `tab_sender.py` X 面板切换「发送目标」为「关键人 (x_contacts)」。

### 发送冷却机制

`send_log.channel` 区分 TG/X，各自独立冷却。冷却 SQL 在 `db._cooldown_clause()` 中生成。

### x_links 的 source 标签

`cb_excel` / `cb_discover` / `rootdata` / `chainscope` / `tokenfinder` / `campaign` / `cryptorank`，用于发送时按来源筛选。

## 新增功能模式

### 新增数据源
1. 新建 Worker 继承 `BaseWorker`
2. 在 `gui/tab_scraper.py` 的 TabView 中注册新子面板
3. Worker 中调用 `db.insert_x_link()` / `db.upsert_project_return_id()` 等写入

### 新增发送渠道
1. `send_log.channel` 是自由文本，无需改表
2. 新建 Worker
3. 在 `gui/tab_sender.py` 扩展

### 新增数据库表
在 `db.py` 的 `init_db()` 的 `executescript` SQL 末尾追加，重启即生效。

### 新增 x_links source 标签
在 `db.insert_x_link()` 调用处传新 source 值，同步更新 `tab_sender.py` 的下拉菜单 values。

## 注意事项

- Windows 桌面应用，macOS 上只能做代码修改，无法运行 GUI/OCR/坐标操作
- `db.py` 是唯一数据层入口，GUI 和 Worker 都通过它操作数据库，不直接写 SQL
- `config.py` 是唯一配置入口，改配置只改这个文件
- Telethon session 文件在 `data/tg_session_{purpose}.session`，三个功能独立 session
- 坐标校准数据保存在 `data/*.txt` 和 `settings` 表中
- PyInstaller 打包时 `sys._MEIPASS` 处理资源路径
