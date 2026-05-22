#!/usr/bin/env bash
# 一键启动：Chrome CDP + Python 应用
#
# Chrome 136+ 安全限制：当 --user-data-dir 指向默认 profile（用户的日常
# Chrome）时，--remote-debugging-port 被静默禁用。所以本脚本始终用项目
# 本地的隔离 profile（data/chrome_cdp_session/），CDP 才能开。
#
# 模式：
#   默认           -- 全新隔离 profile，需在弹出的 Chrome 里手动登录 X
#   --system / -s  -- 首次启动时从日常 Chrome 拷贝 cookies/Local State，
#                     保留隔离 profile（CDP 可用）+ X 登录态（不被风控）
#   --refresh      -- 配合 --system 使用，强制重新拷贝（覆盖隔离 profile
#                     里的 cookies）。日常 Chrome 改密码后需要刷一次
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=9222
PROJECT_ROOT="$(pwd)"
USER_DATA="$PROJECT_ROOT/data/chrome_cdp_session"
SYSTEM_PROFILE="$HOME/Library/Application Support/Google/Chrome"

# -- 0. 解析参数 --------------------------------------------------------------
USE_SYSTEM=0
REFRESH=0
EXPLICIT_PROFILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --system|-s) USE_SYSTEM=1; shift ;;
        --refresh) REFRESH=1; shift ;;
        --profile)
            EXPLICIT_PROFILE="$2"; shift 2 ;;
        --help|-h)
            cat <<EOF
用法：$0 [--system|-s] [--refresh] [--profile NAME]

  默认             项目本地隔离 profile，全新登录（首次会被 X 风控的话改用 --system）
  --system         从日常 Chrome 拷贝完整 profile 数据（cookies、LocalStorage、IndexedDB、
                   History 等共 18+ 项），保留隔离 profile + X 登录态
                   自动扫描所有 Chrome profile，挑含 X auth_token 的那个
  --refresh        强制重新拷贝（即使隔离 profile 里已有 cookies）
  --profile NAME   显式指定源 profile（如 "Default" / "Profile 1"），跳过自动扫描

  Chrome 必须完全退出（⌘Q），SQLite 才能读到一致状态
EOF
            exit 0 ;;
        *) echo "未知参数：$1（用 --help 查看）"; exit 1 ;;
    esac
done

# -- 1. 清理旧 CDP 进程 -------------------------------------------------------
PIDS=$(lsof -ti :$PORT 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    echo "端口 $PORT 已被占用（PID: $(echo $PIDS | tr '\n' ' ')），正在关闭..."
    echo "$PIDS" | xargs kill 2>/dev/null || true
    sleep 1
    if lsof -ti :$PORT &>/dev/null; then
        echo "端口仍被占用，强制关闭..."
        lsof -ti :$PORT | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
fi

# -- 2. --system 模式：从日常 Chrome 拷贝认证文件 ----------------------------
if [ $USE_SYSTEM -eq 1 ]; then
    if [ ! -d "$SYSTEM_PROFILE" ]; then
        echo "❌  未找到日常 Chrome profile 目录：$SYSTEM_PROFILE"
        echo "    --system 模式不可用，请先用 Chrome 登录一次 X，再重试"
        exit 1
    fi

    # 日常 Chrome 在跑会持有 cookies SQLite 的写锁，拷贝出来是损坏的
    if pgrep -x "Google Chrome" > /dev/null 2>&1; then
        echo "❌  检测到日常 Chrome 正在运行。"
        echo "    --system 拷贝认证文件前必须完全关闭日常 Chrome（⌘Q，不只是关窗口）。"
        exit 1
    fi

    # 判断是否需要拷贝：首次启动 / 用户主动 --refresh
    NEED_COPY=0
    if [ $REFRESH -eq 1 ]; then
        NEED_COPY=1
        echo "[--refresh] 强制重新拷贝 profile 数据..."
    elif [ ! -d "$USER_DATA/Default/Local Storage" ]; then
        NEED_COPY=1
        echo "[--system] 首次启动，从日常 Chrome 拷贝完整 profile 数据..."
    else
        echo "[--system] 隔离 profile 已有 Local Storage 数据，跳过拷贝（用 --refresh 强制刷新）"
    fi

    if [ $NEED_COPY -eq 1 ]; then
        # 选择源 profile：--profile 指定 / 自动扫描 auth_token
        SELECTED_PROFILE=""
        if [ -n "$EXPLICIT_PROFILE" ]; then
            if [ ! -d "$SYSTEM_PROFILE/$EXPLICIT_PROFILE" ]; then
                echo "❌  指定的 profile 不存在：$SYSTEM_PROFILE/$EXPLICIT_PROFILE"
                exit 1
            fi
            SELECTED_PROFILE="$EXPLICIT_PROFILE"
            echo "  使用指定 profile：$SELECTED_PROFILE"
        else
            echo "  扫描所有 Chrome profile，查找含 X auth_token 的那个..."
            FOUND_PROFILES=()
            # 遍历 Default + Profile N
            for profile_dir in "$SYSTEM_PROFILE/Default" "$SYSTEM_PROFILE"/Profile\ *; do
                [ -d "$profile_dir" ] || continue
                pname=$(basename "$profile_dir")
                # cookies DB 优先 Network/Cookies（Chrome 96+），fallback 老位置
                cookie_db=""
                if [ -f "$profile_dir/Network/Cookies" ]; then
                    cookie_db="$profile_dir/Network/Cookies"
                elif [ -f "$profile_dir/Cookies" ]; then
                    cookie_db="$profile_dir/Cookies"
                fi
                [ -n "$cookie_db" ] || continue
                # 拷一份 DB 再查（避免临时锁影响）
                tmp_db=$(mktemp)
                cp -f "$cookie_db" "$tmp_db" 2>/dev/null || { rm -f "$tmp_db"; continue; }
                has_auth=$(sqlite3 "$tmp_db" \
                    "SELECT COUNT(*) FROM cookies WHERE name='auth_token' AND host_key LIKE '%.x.com' OR host_key LIKE '%.twitter.com';" \
                    2>/dev/null || echo 0)
                rm -f "$tmp_db"
                if [ "${has_auth:-0}" != "0" ]; then
                    FOUND_PROFILES+=("$pname")
                    echo "    ✓ $pname (含 X auth_token)"
                fi
            done
            if [ ${#FOUND_PROFILES[@]} -eq 0 ]; then
                echo "❌  在所有 Chrome profile 中都没找到 X auth_token。"
                echo "    请先打开日常 Chrome 登录 https://x.com，⌘Q 退出后重试。"
                exit 1
            elif [ ${#FOUND_PROFILES[@]} -gt 1 ]; then
                echo "❌  多个 profile 含 X 登录态：${FOUND_PROFILES[*]}"
                echo "    请用 --profile NAME 显式指定，例如："
                echo "      $0 --system --refresh --profile \"${FOUND_PROFILES[0]}\""
                exit 1
            fi
            SELECTED_PROFILE="${FOUND_PROFILES[0]}"
            echo "  自动选中：$SELECTED_PROFILE"
        fi

        # Chrome 用 OSCrypt 加密 cookies，Local State 存了加密元数据，
        # 主密钥在 macOS Keychain 里（per-app，不需要拷贝）。
        # 源 profile 文件名可能含空格（"Profile 1"），所以用变量包裹路径。
        # 目标永远是隔离 profile 的 Default/，让 Chrome 当默认 profile 用。
        SRC="$SYSTEM_PROFILE/$SELECTED_PROFILE"
        DST="$USER_DATA/Default"
        mkdir -p "$DST/Network" "$DST/Local Storage/leveldb" "$DST/Session Storage" "$DST/IndexedDB"
        copied=0
        skipped=0

        # --- 全局文件（在 profile 父目录）---
        for f in "Local State" "First Run"; do
            if [ -f "$SYSTEM_PROFILE/$f" ]; then
                cp -f "$SYSTEM_PROFILE/$f" "$USER_DATA/$f"
                copied=$((copied + 1))
            fi
        done

        # --- 文件：认证、指纹、历史 ---
        for src_rel in \
            "Cookies" \
            "Cookies-journal" \
            "Network/Cookies" \
            "Network/Cookies-journal" \
            "Preferences" \
            "Secure Preferences" \
            "Login Data" \
            "Login Data-journal" \
            "Web Data" \
            "Web Data-journal" \
            "History" \
            "History-journal" \
            "Favicons" \
            "Favicons-journal" \
            "Top Sites" \
            "Top Sites-journal" \
            "Bookmarks" \
            "Visited Links"
        do
            src_path="$SRC/$src_rel"
            dst_path="$DST/$src_rel"
            if [ -f "$src_path" ]; then
                cp -f "$src_path" "$dst_path"
                copied=$((copied + 1))
            else
                skipped=$((skipped + 1))
            fi
        done

        # --- 目录：LocalStorage / SessionStorage / IndexedDB ---
        for dir_rel in \
            "Local Storage" \
            "Session Storage" \
            "IndexedDB"
        do
            src_dir="$SRC/$dir_rel"
            dst_dir="$DST/$dir_rel"
            if [ -d "$src_dir" ]; then
                # 用 rsync 增量拷贝，避免每次全量覆盖（保留隔离 profile 自己产生的数据）
                rsync -a --delete "$src_dir/" "$dst_dir/" 2>/dev/null
                copied=$((copied + 1))
            fi
        done

        echo "  ✓ 已从「$SELECTED_PROFILE」拷贝到 $DST/：$copied 项${skipped:+（$skipped 项不存在已跳过）}"
    fi

    # 清理残留 SingletonLock
    if [ -e "$USER_DATA/SingletonLock" ]; then
        rm -f "$USER_DATA/SingletonLock" "$USER_DATA/SingletonCookie" "$USER_DATA/SingletonSocket"
    fi
fi

# -- 3. 启动 Chrome CDP（后台）------------------------------------------------
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ ! -f "$CHROME" ]; then
    echo "未找到 Chrome: $CHROME"
    echo "请安装 Google Chrome 或修改此脚本中的 CHROME 路径"
    exit 1
fi

echo "启动 Chrome CDP (端口 $PORT)..."
mkdir -p "$USER_DATA"
LOG_FILE="$PROJECT_ROOT/data/chrome_debug.log"
mkdir -p "$(dirname "$LOG_FILE")"
"$CHROME" \
    --remote-debugging-port=$PORT \
    --remote-debugging-address=127.0.0.1 \
    --user-data-dir="$USER_DATA" \
    --no-first-run \
    --no-default-browser-check \
    >"$LOG_FILE" 2>&1 \
    &
CHROME_PID=$!
echo "Chrome 进程已启动 (PID: $CHROME_PID)，等待 CDP 端口就绪..."

# 等待 CDP 端口真正可连（最多 15 秒）。Chrome 进程起来不代表 CDP 已 listen。
CDP_READY=0
for i in $(seq 1 30); do
    if nc -z 127.0.0.1 $PORT 2>/dev/null; then
        CDP_READY=1
        break
    fi
    # 顺便检测 Chrome 进程是否还活着——异常退出就立刻报错
    if ! kill -0 $CHROME_PID 2>/dev/null; then
        echo ""
        echo "❌  Chrome 已退出（启动失败）。最近日志："
        echo "─────────────────────────────"
        tail -20 "$LOG_FILE" 2>/dev/null || echo "(无日志)"
        echo "─────────────────────────────"
        exit 1
    fi
    sleep 0.5
done

if [ $CDP_READY -ne 1 ]; then
    echo ""
    echo "❌  Chrome 进程跑着，但 15 秒内 CDP 端口 $PORT 仍未就绪。"
    if grep -q "non-default data directory" "$LOG_FILE" 2>/dev/null; then
        echo "    日志显示 Chrome 拒绝在默认 profile 上开 CDP（Chrome 136+ 安全限制）。"
        echo "    本脚本不该走到这条分支 — 请把脚本和日志贴给开发者。"
    else
        echo "    可能 Chrome 卡在某个对话框（如「恢复上次会话？」），请手动点掉重试。"
    fi
    exit 1
fi

echo "✓ CDP 端口已就绪"
echo ""

if [ $USE_SYSTEM -eq 1 ]; then
    echo "下一步："
    echo "  ✓ 已从日常 Chrome 拷贝登录态到隔离 profile"
    echo "  1. 在弹出的 Chrome 中确认 https://x.com 已登录（应该已经是登录态）"
    echo "  2. 回到 outreach-hub，点击「▶ 开始发送」或「▶ 开始搜索」，再点「已登录就绪」"
    echo ""
    echo "  💡 日常 Chrome 改密码或登录新账号后，运行 --system --refresh 重新同步"
else
    echo "下一步："
    echo "  1. 在弹出的 Chrome 中打开 https://x.com 并登录"
    echo "  2. 登录成功后，回到 outreach-hub 程序"
    echo "  3. 点击「▶ 开始发送」或「▶ 开始搜索」，再点「已登录就绪」"
    echo "  ✓ 本次登录会保存在 data/chrome_cdp_session/，下次无需重新登录"
    echo ""
    echo "  💡 若 X 登录卡循环回首页，改用 --system 从日常 Chrome 拷贝登录态"
fi
echo ""

# -- 4. 启动 Python 应用（前台）-----------------------------------------------
echo ""
echo "启动 Python 应用..."
uv run python main.py

# main.py 退出后清理 Chrome
echo "应用已退出, 关闭 Chrome CDP..."
kill $CHROME_PID 2>/dev/null || true
