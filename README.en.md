# Web3 Outreach Hub

Automated outreach tool for Web3 projects. Import project info from multiple data sources, automatically scrape official websites for Telegram / X links, parse group admins, then send bulk DMs via TG / X.

## Features

- **Data Import**: Batch import projects from CrunchBase, RootData, CryptoRank, ChainScope, and more
- **Website Scraping**: Auto-scrape project websites using Playwright to extract TG group links and X links
- **TG Parsing**: Parse group admins and left users from small groups via Telethon
- **DM Sending**:
  - **X (Twitter)**: Playwright-driven browser automation to search users and send DMs (macOS)
  - **Telegram**: Telethon API or OCR coordinate-based clicking (Windows)

## Installation

### 1. Install Google Chrome

X DM sending requires Google Chrome. Install it first:

[Download Google Chrome](https://www.google.com/chrome/)

### 2. One-Click Install

Copy and run the following command in your terminal:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lukezhao-tech/outreach-hub/main/scripts/install.sh)"
```

This script automatically completes all of the following steps:

| Step | Description |
|------|-------------|
| Clone repo | Pull the latest code from GitHub |
| Check Chrome | Verify Google Chrome is installed |
| Install uv | Auto-download and install uv if not present |
| Install Python | Download Python 3.11 via uv (no system Python required) |
| Install dependencies | `uv sync` to install all pip packages |
| Install browser engine | `playwright install chromium` for website scraping |
| Environment verification | Check tkinter, customtkinter, playwright availability |

The whole process takes about 2-3 minutes depending on network speed. **Only needs to be run once.**

### After Installation

The install script automatically enters the `outreach-hub` directory. For daily startup (each time you open a new terminal):

```bash
cd outreach-hub    # the cloned directory (usually in your home directory)
./scripts/start_chrome_cdp.sh
```

## Usage

### Desktop GUI

```bash
./scripts/start_chrome_cdp.sh
```

This script will:
1. Start Chrome in CDP mode (runs in background)
2. Launch the desktop GUI application

**X DM Sending Flow**:
1. Log in to Twitter (`x.com`) in the Chrome window that pops up
2. Return to the app and click "Start Sending"
3. Wait for the browser to open the DM page, then click "Logged In / Ready"
4. DMs will be sent automatically one by one

## Data Flow

```
Data source import → projects table
    |
    v  Scrape websites
tg_links table + x_links table
    |
    v  Parse group info
tg_handles table + tg_left_users table
    |
    v  Send DMs
send_log table
```

Every step writes to local SQLite. Already-processed items are automatically skipped on restart.

## Directory Structure

```
outreach-hub/
  main.py              # Desktop GUI entry point
  config.py            # Configuration (credentials, paths, APIs)
  db.py                # Data layer (SQLite)
  workers/             # Background tasks (scraping, parsing, sending)
  gui/                 # Desktop GUI tabs
  scripts/
    install.sh             # One-click install (for new team members)
    install_browsers.sh    # Environment setup (called internally by install.sh)
    start_chrome_cdp.sh    # Daily startup (Chrome + GUI)
  data/                # Database, sessions (auto-created, not version-controlled)
```
