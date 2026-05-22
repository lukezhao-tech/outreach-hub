# Web3 Outreach Hub

Web3 Outreach Hub is a local desktop GUI app for Web3 outreach workflows: importing projects, scraping websites, collecting Telegram / X contacts, managing templates, and sending DMs. Data is stored locally in SQLite.

## Features

- Import projects from CrunchBase, RootData, CryptoRank, ChainScope, campaign APIs, Excel, and CSV
- Scrape official websites for Telegram groups, X handles, and emails
- Parse Telegram admins and left users with Telethon
- Search X people profiles for CEO / CMO / Growth / Founder contacts
- Manage Telegram / X / Email templates in the GUI
- Send Telegram, X, and Email outreach from the desktop app

## Install

Install Google Chrome first:

[Download Google Chrome](https://www.google.com/chrome/)

Then run:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lukezhao-tech/outreach-hub/main/scripts/install.sh)"
```

Daily startup:

```bash
cd ~/outreach-hub
./scripts/start_chrome_cdp.sh
```

## Recommended X Startup

X is sensitive to new devices and automation contexts. The most reliable startup mode is to sync your regular Chrome profile into the app's isolated CDP profile:

```bash
cd ~/outreach-hub
./scripts/start_chrome_cdp.sh --system --refresh --profile "you@example.com"
```

`--profile` accepts:

| Type | Example |
|------|---------|
| Chrome profile directory | `Default`, `Profile 1` |
| Chrome display name | `Luke Zhao`, `taskon.xyz` |
| Login email | `lukezhao@taskon.xyz` |

Before running this command, fully quit your regular Chrome with `Command + Q`. Closing windows is not enough because Chrome may keep profile databases locked.

## X Anti-Bot Notes

The current version reduces X friction by:

- Using the real Chrome fingerprint in CDP mode instead of injecting a fixed UA / Client Hints profile
- Fully syncing the selected Chrome profile on `--system --refresh`, preventing stale mixed-account browser state
- Slowing down X DM sending with a default random delay of `30-90s` between messages
- Typing DM text progressively instead of instant-filling it
- Stopping the current run if X DM shows `connecting / disconnected`, network errors, or retry pages

If `https://x.com/i/chat` loops between `connecting` and `disconnected`:

1. Quit regular Chrome with `Command + Q`
2. Run:

   ```bash
   cd ~/outreach-hub
   ./scripts/start_chrome_cdp.sh --system --refresh --profile "your Chrome email or profile name"
   ```

3. In the launched Chrome, manually open:

   ```text
   https://x.com/i/chat
   ```

4. Start sending only after the DM page connects normally

If the same account cannot open DM in regular Chrome either, the issue is likely account-side or network-side.

## GUI Workflow

1. **Settings**: configure Telegram API credentials, Gmail App Password, DeepSeek keys, OCR / coordinate settings, and DM cooldown.
2. **Scraper**: import or scrape projects from the supported sources. Use a unique `source tag` for each batch.
3. **Parser**: parse Telegram admins or left users.
4. **Messages**: create and activate outreach templates.
5. **Sender**: send Telegram, X project-handle, X key-person, or Email messages.

## Update

```bash
cd ~/outreach-hub
git pull
uv sync
```

After changing X accounts or Chrome profiles, refresh the synced profile:

```bash
./scripts/start_chrome_cdp.sh --system --refresh --profile "your Chrome email or profile name"
```

## Data Flow

```text
Project import -> projects
    |
    v
Website scraping -> tg_links / x_links / emails
    |
    v
Telegram parsing -> tg_contacts / tg_left_users
    |
    v
X people search -> x_contacts
    |
    v
Sending -> send_log
```

Everything is stored in `data/outreach.db`.

## Project Structure

```text
outreach-hub/
  main.py                  # Desktop GUI entry
  config.py                # Paths and defaults
  db.py                    # SQLite data layer
  gui/                     # CustomTkinter tabs
  workers/                 # Scraping, parsing, sending workers
  scripts/
    install.sh             # One-click install
    install_browsers.sh    # Dependency and browser setup
    start_chrome_cdp.sh    # Start Chrome CDP + GUI
  data/                    # Local database and sessions
```

## Android Companion

For stronger X anti-bot cases, X people search and X DM sending can be moved to the Android real-device version:

[outreach-hub-android](https://github.com/lukezhao-tech/outreach-hub-android)

The desktop and Android versions share `data/outreach.db`.
