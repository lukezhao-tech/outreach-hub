# Outreach Hub Windows 一键安装脚本
#
# 使用方式（PowerShell，右键「以管理员身份运行」或普通终端均可）：
#
# 方式一 — 远程执行（新同事直接复制粘贴到 PowerShell）：
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   Invoke-Expression (Invoke-RestMethod https://raw.githubusercontent.com/lukezhgo-tech/outreach-hub/main/scripts/install.ps1)
#
# 方式二 — clone 后运行：
#   git clone https://github.com/lukezhao-tech/outreach-hub.git
#   cd outreach-hub
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1

$ErrorActionPreference = "Stop"
$REPO_URL = "https://github.com/lukezhao-tech/outreach-hub.git"
$REPO_NAME = "outreach-hub"

Write-Host ""
Write-Host "Outreach Hub 安装 (Windows)" -ForegroundColor Cyan
Write-Host "============================"

# ── 0. 确保 git 可用 ──────────────────────────────────────────────────────────
$gitExe = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitExe) {
    Write-Host ""
    Write-Host "[需要] 未找到 Git，正在下载安装..." -ForegroundColor Yellow

    # 下载 Git for Windows 安装包
    $gitInstaller = "$env:TEMP\GitSetup.exe"
    Write-Host "  下载 Git for Windows..."
    Invoke-WebRequest -Uri "https://github.com/git-for-windows/git/releases/latest/download/Git-2.47.1-64-bit.exe" -OutFile $gitInstaller -UseBasicParsing

    Write-Host "  启动安装向导（请一路 Next 完成安装）..."
    Start-Process -FilePath $gitInstaller -Wait

    # 刷新 PATH（Git 安装后会写入 Program Files）
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

    $gitExe = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitExe) {
        Write-Host "[错误] Git 安装后仍未找到。请重启终端后重新运行此脚本。" -ForegroundColor Red
        exit 1
    }
}
Write-Host "[OK] Git $(git --version)" -ForegroundColor Green

# ── 1. 确认代码目录 ──────────────────────────────────────────────────────────
if ((Test-Path "pyproject.toml") -and ((Get-Content "pyproject.toml" -Raw) -match "outreach-hub")) {
    $REPO_DIR = (Get-Location).Path
    Write-Host "[OK] 已在项目目录: $REPO_DIR" -ForegroundColor Green
} else {
    if (Test-Path $REPO_NAME) {
        Write-Host "[OK] 目录 $REPO_NAME 已存在，拉取最新..." -ForegroundColor Green
        Set-Location $REPO_NAME
        git remote set-url origin $REPO_URL
        git pull
    } else {
        Write-Host "克隆仓库..."
        git clone $REPO_URL
        Set-Location $REPO_NAME
    }
    $REPO_DIR = (Get-Location).Path
}

# ── 2. 检查 Chrome ────────────────────────────────────────────────────────────
$chromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:LOCALAPPDATA}\Google\Chrome\Application\chrome.exe"
)
$chromeFound = $false
foreach ($p in $chromePaths) {
    if (Test-Path $p) {
        $chromeFound = $true
        break
    }
}
if (-not $chromeFound) {
    Write-Host ""
    Write-Host "[需要] 未找到 Google Chrome，正在下载安装..." -ForegroundColor Yellow

    $chromeInstaller = "$env:TEMP\ChromeSetup.exe"
    Write-Host "  下载 Chrome 安装程序..."
    Invoke-WebRequest -Uri "https://dl.google.com/chrome/win64/1.0.0.0/chrome_installer.exe" -OutFile $chromeInstaller -UseBasicParsing

    Write-Host "  启动安装（请等待自动完成）..."
    Start-Process -FilePath $chromeInstaller -Wait

    # 再次检查
    $chromeFound = $false
    foreach ($p in $chromePaths) {
        if (Test-Path $p) {
            $chromeFound = $true
            break
        }
    }
    if (-not $chromeFound) {
        Write-Host "[需要] Chrome 安装未完成。请手动安装后重试: https://www.google.com/chrome/" -ForegroundColor Red
        exit 1
    }
}
Write-Host "[OK] Google Chrome" -ForegroundColor Green

# ── 3. 安装 uv ────────────────────────────────────────────────────────────────
$uvExe = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvExe) {
    Write-Host ""
    Write-Host "安装 uv 包管理器..."
    $uvInstaller = "$env:TEMP\uv_install.ps1"
    Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $uvInstaller -UseBasicParsing
    powershell -ExecutionPolicy Bypass -File $uvInstaller

    # 刷新 PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

    $uvExe = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvExe) {
        # uv 默认装到 %USERPROFILE%\.local\bin 或 %LOCALAPPDATA%\uv
        $uvCandidates = @(
            "$env:USERPROFILE\.local\bin\uv.exe",
            "$env:LOCALAPPDATA\uv\uv.exe"
        )
        foreach ($c in $uvCandidates) {
            if (Test-Path $c) {
                $env:Path = "$($c | Split-Path);$($env:Path)"
                break
            }
        }
        $uvExe = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $uvExe) {
            Write-Host "[需要] 请重启终端后重新运行此脚本（uv 需要加入 PATH）" -ForegroundColor Red
            exit 1
        }
    }
}
Write-Host "[OK] uv $(uv --version)" -ForegroundColor Green

# ── 4. 安装 Python + 依赖 + Playwright ────────────────────────────────────────
Write-Host ""
Write-Host "── 1/3 安装 Python 依赖 ────────────────────────────"
uv sync

Write-Host ""
Write-Host "── 2/3 安装 Playwright 浏览器 ──────────────────────"
uv run playwright install chromium

Write-Host ""
Write-Host "── 3/3 验证环境 ────────────────────────────────────"
uv run python -c "import tkinter; print('[OK] tkinter')"
uv run python -c "import customtkinter; print('[OK] customtkinter')"
uv run python -c "import playwright; print('[OK] playwright')"

# ── 5. 创建启动快捷脚本 ──────────────────────────────────────────────────────
$launcher = Join-Path $REPO_DIR "start.bat"
$launcherContent = @"
@echo off
cd /d "$REPO_DIR"
uv run python main.py
pause
"@
Set-Content -Path $launcher -Value $launcherContent -Encoding ASCII

$webLauncher = Join-Path $REPO_DIR "start_web.bat"
$webLauncherContent = @"
@echo off
cd /d "$REPO_DIR"
uv run python web_server.py
echo.
echo 浏览器打开 http://localhost:5000
pause
"@
Set-Content -Path $webLauncher -Value $webLauncherContent -Encoding ASCII

# ── 完成 ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================" -ForegroundColor Cyan
Write-Host "安装完成！" -ForegroundColor Green
Write-Host ""
Write-Host "日常使用：" -ForegroundColor White
Write-Host "  方式一 — 双击 start.bat 启动桌面 GUI" -ForegroundColor White
Write-Host "  方式二 — 双击 start_web.bat 启动 Web UI，浏览器打开 http://localhost:5000" -ForegroundColor White
Write-Host ""
Write-Host "首次使用桌面 GUI 需要先登录 Chrome：" -ForegroundColor Yellow
Write-Host "  1. 双击 start.bat 启动程序" -ForegroundColor White
Write-Host "  2. 在设置页配置 Telegram 账号（如需 TG 功能）" -ForegroundColor White
Write-Host "  3. 在爬虫页点击「启动 Chrome CDP」，弹出的 Chrome 中登录 X/TG" -ForegroundColor White
Write-Host ""
